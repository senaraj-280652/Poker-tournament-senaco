# -*- coding: utf-8 -*-
""""Gestion de l'accueil" optionnelle (modification du 2026-09-29) :
toute la fonctionnalité Accueil des joueurs/Gestion des absences (Lots 1
et 2) est désormais gouvernée par un réglage propre à chaque tournoi,
`accueil_management_enabled` — DÉCOCHÉ PAR DÉFAUT, y compris pour un
ancien fichier .tournoi qui ne connaît pas encore ce réglage (simple clé
de la table settings, aucune migration de colonne nécessaire, voir
Database.accueil_management_enabled).

Couvre :
1. Database.accueil_management_enabled — défaut désactivé, propre à
   chaque tournoi, aucune migration requise.
2. Toutes les opérations d'accueil/absence refusent CÔTÉ BASE lorsque le
   réglage est désactivé (confirm_arrival, cancel_arrival, declare_
   absent, cancel_absence, absence_deadline_passed) — jamais seulement
   un masquage de bouton — et les données déjà écrites (arrived_at/
   absent_declared_at) ne sont jamais effacées par la désactivation.
3. App._players_checkbox_drives_accueil : jamais piloté par l'accueil
   quand le réglage est désactivé, quel que soit clock_started.
4. App._on_abs_des_clicked : redevient l'ancien "Désactiver (forfait)"
   (désactivation directe, sans jamais passer par declare_absent) quand
   le réglage est désactivé.
5. App._update_tournament_started_buttons : "Abs/Dés" toujours actif et
   étiqueté "Désactiver (forfait)" quand le réglage est désactivé —
   jamais grisé avant le démarrage, contrairement au comportement Lot 2.
6. Contrôle à distance : bouton "Accueil" masqué sur la page principale
   pour TOUT rôle (y compris ADMIN, qui échappe normalement à
   _strip_permission_sections) quand désactivé ; routes GET /checkin,
   /checkin.html, /checkin_players et POST /confirm_arrival, /cancel_
   arrival, /declare_absence, /cancel_absence, /withdraw_absent_player
   refusées CÔTÉ SERVEUR (pas seulement un bouton masqué) ; bouton
   "Abs/Dés" absent de la page Éliminations quand désactivé."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
import player_photos  # noqa: E402
import remote_control  # noqa: E402
import roster  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name="A"):
    return database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))


def _new_db_cross_thread(tmp_dir, filename):
    """Même principe que tests/test_remote_confirm_arrival.py : une
    connexion SQLite utilisable depuis le thread HTTP du serveur de
    contrôle à distance (check_same_thread=False), nécessaire pour tout
    test qui interroge la base depuis une requête HTTP réelle."""
    import sqlite3

    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    db.conn.close()
    db.conn = sqlite3.connect(path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


# =====================================================================
# 1. Database.accueil_management_enabled
# =====================================================================
class AccueilManagementEnabledDefaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_default_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

    def test_desactive_par_defaut_pour_un_tournoi_flambant_neuf(self):
        self.assertFalse(self.db.accueil_management_enabled())

    def test_active_une_fois_le_reglage_pose(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        self.assertTrue(self.db.accueil_management_enabled())

    def test_peut_etre_desactive_apres_avoir_ete_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        self.db.set_settings({"accueil_management_enabled": 0})
        self.assertFalse(self.db.accueil_management_enabled())

    def test_propre_a_chaque_tournoi(self):
        """Un tournoi Open peut l'activer sans affecter un autre tournoi
        du même dossier (cahier des charges, section 4)."""
        db_open = _new_db(self._tmp.name, name="Open")
        self.addCleanup(db_open.conn.close)
        db_open.set_settings({"accueil_management_enabled": 1})

        self.assertTrue(db_open.accueil_management_enabled())
        self.assertFalse(self.db.accueil_management_enabled())

    def test_ancien_fichier_sans_ce_reglage_reste_desactive(self):
        """Aucune migration de colonne nécessaire (clé de la table
        settings, pas une colonne de players) : reprendre un fichier créé
        AVANT l'existence de cette fonctionnalité (donc qui n'a jamais vu
        cette clé) doit se comporter comme "désactivé", jamais planter ni
        se comporter comme "activé" par erreur."""
        path = self.db.path
        self.db.conn.close()

        reopened = database.Database(path)
        self.addCleanup(reopened.conn.close)

        self.assertFalse(reopened.accueil_management_enabled())


# =====================================================================
# 2. Interdiction côté serveur/base des opérations d'accueil/absence
# =====================================================================
class AccueilDisabledRefusesDatabaseOperationsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_db_ops_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        # Réglage EXPLICITEMENT désactivé (comportement par défaut, posé
        # ici pour la clarté du test).
        self.db.set_settings({"accueil_management_enabled": 0})

    def test_confirm_arrival_refuse(self):
        self.assertFalse(self.db.confirm_arrival(self.pid))
        self.assertIsNone(self.db.get_player(self.pid)["arrived_at"])

    def test_cancel_arrival_refuse(self):
        self.assertFalse(self.db.cancel_arrival(self.pid))

    def test_declare_absent_refuse_meme_tournoi_demarre(self):
        _start_tournament(self.db)
        self.assertFalse(self.db.declare_absent(self.pid))
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_cancel_absence_refuse(self):
        self.assertFalse(self.db.cancel_absence(self.pid))

    def test_absence_deadline_passed_toujours_faux(self):
        """Même délai largement dépassé (65 min pour 60 configurées) : la
        clôture de l'accueil et l'éligibilité à la désactivation n'ont
        aucun sens si l'accueil lui-même n'est jamais utilisé."""
        _start_tournament(self.db, minutes_ago=65, duration_minutes=60)
        self.assertFalse(self.db.absence_deadline_passed())

    def test_activer_le_reglage_retablit_le_fonctionnement_normal(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        _start_tournament(self.db)

        self.assertTrue(self.db.confirm_arrival(self.pid))

    def test_desactiver_apres_coup_ne_touche_jamais_les_donnees_existantes(self):
        """Cahier des charges, section 4 : "ne pas effacer les données
        d'accueil ou d'absence existantes lorsqu'on désactive l'option"."""
        self.db.set_settings({"accueil_management_enabled": 1})
        _start_tournament(self.db)
        self.db.confirm_arrival(self.pid)
        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])

        self.db.set_settings({"accueil_management_enabled": 0})

        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])


# =====================================================================
# 3. App._players_checkbox_drives_accueil
# =====================================================================
class PlayersCheckboxDrivesAccueilToggleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_checkbox_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.win = types.SimpleNamespace(db=self.db)
        self.win._players_checkbox_drives_accueil = types.MethodType(
            main.App._players_checkbox_drives_accueil, self.win
        )

    def test_jamais_pilote_quand_desactive_meme_avant_demarrage(self):
        self.db.set_settings({"accueil_management_enabled": 0, "clock_started": 0})
        self.assertFalse(self.win._players_checkbox_drives_accueil())

    def test_pilote_avant_demarrage_quand_active(self):
        self.db.set_settings({"accueil_management_enabled": 1, "clock_started": 0})
        self.assertTrue(self.win._players_checkbox_drives_accueil())

    def test_ne_pilote_plus_apres_demarrage_meme_active(self):
        self.db.set_settings({"accueil_management_enabled": 1, "clock_started": 1})
        self.assertFalse(self.win._players_checkbox_drives_accueil())


# =====================================================================
# 4. App._on_abs_des_clicked : redevient "Désactiver (forfait)"
# =====================================================================
class _FakeAppForAbsDes:
    """Même doublure que tests/test_abs_des_and_reinstate.py (reprise à
    l'identique, convention déjà établie dans cette suite)."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)
        self.clear_checked_calls = 0
        self.refresh_all_calls = 0
        self.refresh_remote_players_calls = 0
        self.refresh_remote_checkin_calls = 0
        self._withdraw_selected = types.MethodType(main.App._withdraw_selected, self)

    def _checked_or_selected_ids(self):
        return list(self._ids)

    def _clear_checked(self):
        self.clear_checked_calls += 1

    def _refresh_all(self):
        self.refresh_all_calls += 1

    def _refresh_remote_players_cache(self):
        self.refresh_remote_players_calls += 1

    def _refresh_remote_checkin_cache(self):
        self.refresh_remote_checkin_calls += 1


class OnAbsDesClickedDisabledTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_abs_des_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.set_settings({"accueil_management_enabled": 0})
        # Réglage désactivé : même un tournoi jamais démarré doit
        # pouvoir désactiver directement (comportement historique
        # "Désactiver (forfait)", jamais gouverné par le démarrage).

    def test_desactive_directement_sans_jamais_signaler_absent(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_confirm:
            main.App._on_abs_des_clicked(app)

        mock_confirm.assert_called_once()
        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_refus_de_la_confirmation_ne_desactive_pas(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=False):
            main.App._on_abs_des_clicked(app)

        self.assertEqual(self.db.get_player(self.pid)["status"], "active")

    def test_fonctionne_avant_meme_le_demarrage_du_tournoi(self):
        """Contrairement au Lot 2 (Abs/Dés gouverné par le démarrage),
        l'ancien "Désactiver (forfait)" a toujours été disponible dès
        avant le démarrage — jamais un concept post-démarrage."""
        self.assertEqual(self.db.get_setting_int("clock_started", 0), 0)
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._on_abs_des_clicked(app)

        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")


# =====================================================================
# 5. App._update_tournament_started_buttons
# =====================================================================
PLAYERS_COLUMNS = (
    "sel", "id", "name", "club", "table", "seat", "chips", "buyin", "rebuy",
    "addon", "bounty", "status", "rang", "elim_time", "elim_round", "eliminated_by",
)

_GRAFTED_METHODS = (
    "_refresh_players_tab", "_update_tournament_started_buttons",
    "_pending_old_seat_by_name", "_update_checked_count_label",
    "_update_undo_elimination_button_state", "_players_checkbox_drives_accueil",
    "_update_players_stats_label", "_cancel_players_absent_blink",
    "_players_absent_blink_tick", "_update_sort_headings",
    "_accueil_status_label",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class UpdateTournamentStartedButtonsToggleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        roster_patcher_list = patch.object(roster, "list_clubs", return_value=[])
        self.addCleanup(roster_patcher_list.stop)
        roster_patcher_list.start()
        photo_patcher = patch.object(player_photos, "get_photo_path", return_value=None)
        self.addCleanup(photo_patcher.stop)
        photo_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_buttons_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = set()
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in _GRAFTED_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

        parent = ttk.Frame(self.root)
        self.addCleanup(parent.destroy)
        self.win.players_tree = ttk.Treeview(parent, columns=PLAYERS_COLUMNS, show="tree headings")
        for c in PLAYERS_COLUMNS:
            self.win.players_tree.heading(c, text="")
        self.win.new_player_club_combo = ttk.Combobox(parent)
        self.win.stats_lbl = ttk.Label(parent)
        self.win.checked_count_lbl = ttk.Label(parent)
        self.win.delete_player_btn = ttk.Button(parent)
        self.win.delete_player_btn_tooltip = main.Tooltip(self.win.delete_player_btn, "")
        self.win.eliminate_player_btn = ttk.Button(parent)
        self.win.eliminate_player_btn_tooltip = main.Tooltip(self.win.eliminate_player_btn, "")
        self.win._eliminate_btn_tooltip_normal_text = ""
        self.win.undo_elimination_btn = ttk.Button(parent)
        self.win.abs_des_btn = ttk.Button(parent)
        self.win._abs_des_btn_tooltip_normal_text = "TEXTE NORMAL ABS/DES"
        self.win.abs_des_btn_tooltip = main.Tooltip(self.win.abs_des_btn, self.win._abs_des_btn_tooltip_normal_text)
        self.win.correct_arrival_btn = ttk.Button(parent)
        self.win._correct_arrival_btn_tooltip_normal_text = "TEXTE NORMAL CORRIGER"
        self.win.correct_arrival_btn_tooltip = main.Tooltip(self.win.correct_arrival_btn, self.win._correct_arrival_btn_tooltip_normal_text)
        self.win.player_photo_images = {}

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

    def test_toujours_actif_avant_demarrage_quand_desactive(self):
        self.db.set_settings({"accueil_management_enabled": 0})

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "normal")
        self.assertEqual(str(self.win.abs_des_btn.cget("text")), "Désactiver (forfait)")

    def test_toujours_actif_meme_apres_demarrage_quand_desactive(self):
        self.db.set_settings({"accueil_management_enabled": 0})
        _start_tournament(self.db)

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "normal")
        self.assertEqual(str(self.win.abs_des_btn.cget("text")), "Désactiver (forfait)")

    def test_grise_avant_demarrage_quand_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "disabled")
        self.assertEqual(str(self.win.abs_des_btn.cget("text")), "🚩 Abs/Dés")

    def test_actif_apres_demarrage_quand_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        _start_tournament(self.db)

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "normal")
        self.assertEqual(self.win.abs_des_btn_tooltip.text, "TEXTE NORMAL ABS/DES")


# =====================================================================
# 6. Contrôle à distance : boutons masqués + routes refusées côté serveur
# =====================================================================
class RemoteAccueilToggleHttpTest(_RemoteControlHttpTestCase):
    """Hérite de _RemoteControlHttpTestCase (rôle ADMIN inconditionnel,
    voir tests/test_remote_control_reliability.py) : c'est précisément
    le cas critique à couvrir, puisque _strip_permission_sections est
    normalement SAUTÉE pour ADMIN — le masquage du bouton "Accueil" pour
    ce rôle doit donc passer par un chemin dédié (voir do_GET, page
    "/")."""

    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="accueil_toggle_http_test_")
        self.addCleanup(self._tmp2.cleanup)
        self.db = _new_db_cross_thread(self._tmp2.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self._accueil_enabled = False

        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_players=lambda: [
                {"id": p["id"], "name": p["name"], "absent": bool(p["absent_declared_at"])}
                for p in self.db.list_players(status="active")
            ],
            get_checkin_players=lambda: [
                {"id": p["id"], "name": p["name"], "arrived_at": p["arrived_at"]}
                for p in self.db.list_players(status="active")
            ],
            on_confirm_arrival=lambda pid: {"ok": self.db.confirm_arrival(pid), "message": ""},
            on_cancel_arrival=lambda pid: {"ok": self.db.cancel_arrival(pid), "message": ""},
            on_declare_absence=lambda pid: {"ok": self.db.declare_absent(pid), "message": ""},
            on_cancel_absence=lambda pid: {"ok": self.db.cancel_absence(pid), "message": ""},
            on_withdraw_absent_player=lambda pid: {"ok": True, "message": ""},
            get_absence_deadline_passed=lambda: self.db.absence_deadline_passed(),
            get_accueil_management_enabled=lambda: self._accueil_enabled,
            port=self.port,
        ))

    def _jar(self):
        return authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

    def test_bouton_accueil_absent_de_la_page_principale_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, body, _ = http_request(self.base, "GET", "/", jar)

        self.assertEqual(status, 200)
        self.assertNotIn(b'id="btn-checkin"', body)

    def test_bouton_accueil_present_sur_la_page_principale_quand_active(self):
        self._accueil_enabled = True
        jar = self._jar()

        status, body, _ = http_request(self.base, "GET", "/", jar)

        self.assertEqual(status, 200)
        self.assertIn(b"btn-checkin", body)

    def test_page_checkin_refusee_quand_desactivee(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, _, _ = http_request(self.base, "GET", "/checkin", jar)

        self.assertEqual(status, 403)

    def test_checkin_players_refuse_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, _, _ = http_request(self.base, "GET", "/checkin_players", jar)

        self.assertEqual(status, 403)

    def test_page_checkin_accessible_quand_activee(self):
        self._accueil_enabled = True
        jar = self._jar()

        status, _, _ = http_request(self.base, "GET", "/checkin", jar)

        self.assertEqual(status, 200)

    def test_confirm_arrival_refuse_cote_serveur_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, body, _ = http_request(
            self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 403)
        self.assertIsNone(self.db.get_player(self.pid)["arrived_at"])

    def test_cancel_arrival_refuse_cote_serveur_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, _, _ = http_request(
            self.base, "POST", "/cancel_arrival", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 403)

    def test_declare_absence_refuse_cote_serveur_quand_desactive(self):
        self._accueil_enabled = False
        _start_tournament(self.db)
        jar = self._jar()

        status, body, _ = http_request(
            self.base, "POST", "/declare_absence", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 403)
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_cancel_absence_refuse_cote_serveur_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, _, _ = http_request(
            self.base, "POST", "/cancel_absence", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 403)

    def test_withdraw_absent_player_refuse_cote_serveur_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, _, _ = http_request(
            self.base, "POST", "/withdraw_absent_player", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 403)

    def test_confirm_arrival_fonctionne_quand_activee(self):
        self._accueil_enabled = True
        # get_accueil_management_enabled (serveur) gouverne le refus de
        # LA ROUTE ; Database.confirm_arrival vérifie en plus, elle,
        # accueil_management_enabled() lu EN BASE (défense en profondeur,
        # voir sa docstring) — les deux doivent donc être alignés ici.
        self.db.set_settings({"accueil_management_enabled": 1})
        jar = self._jar()

        status, body, _ = http_request(
            self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 200)
        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])

    def test_bouton_abs_des_absent_de_la_page_eliminations_quand_desactive(self):
        self._accueil_enabled = False
        jar = self._jar()

        status, body, _ = http_request(self.base, "GET", "/eliminate", jar)

        self.assertEqual(status, 200)
        self.assertNotIn(b'id="btn-abs-des"', body)

    def test_bouton_abs_des_present_sur_la_page_eliminations_quand_active(self):
        self._accueil_enabled = True
        jar = self._jar()

        status, body, _ = http_request(self.base, "GET", "/eliminate", jar)

        self.assertEqual(status, 200)
        self.assertIn(b'id="btn-abs-des"', body)


# =====================================================================
# 6bis. Même masquage pour un rôle DIRTO (pas seulement ADMIN)
# =====================================================================
class RemoteAccueilToggleDirtoHttpTest(unittest.TestCase):
    """Un DIRTO explicitement autorisé "checkin" doit lui aussi perdre le
    bouton "Accueil" quand le réglage est désactivé — pas seulement
    ADMIN (dont le chemin de nettoyage est distinct, voir do_GET)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_dirto_http_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(roster, "get_group", return_value=roster.ROSTER_GROUP_DIRTO),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        self._accueil_enabled = False
        # port=0 (demande du 2026-10-20) : voir tests/test_remote_control_
        # reliability.py:_RemoteControlHttpTestCase.setUp pour le détail
        # du correctif (ConnectionRefusedError intermittent, self.base
        # jamais resynchronisé avec le port réel après un repli éventuel
        # de start()).
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_dirto_permissions=lambda name: frozenset({remote_control._PERM_CHECKIN}),
            get_accueil_management_enabled=lambda: self._accueil_enabled,
            port=0,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"
        self.code = open_windows.remote_session_code()

    def test_bouton_accueil_absent_pour_dirto_quand_desactive(self):
        self._accueil_enabled = False
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Dirto")

        status, body, _ = http_request(self.base, "GET", "/", jar)

        self.assertEqual(status, 200)
        self.assertNotIn(b'id="btn-checkin"', body)

    def test_bouton_accueil_present_pour_dirto_quand_active_et_autorise(self):
        self._accueil_enabled = True
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Dirto")

        status, body, _ = http_request(self.base, "GET", "/", jar)

        self.assertEqual(status, 200)
        self.assertIn(b"btn-checkin", body)


if __name__ == "__main__":
    unittest.main()
