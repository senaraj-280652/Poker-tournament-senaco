# -*- coding: utf-8 -*-
""""Accueil : correction des erreurs réservée au Mac" (2026-09-30) —
deuxième volet du verrouillage des arrivées confirmées côté iPhone :

1. iPhone : plus AUCUN bouton "Annuler" une fois un joueur "Actif", ni
   côté page (voir tests/test_status_absent_harmonization.py) ni côté
   serveur — App._remote_cancel_arrival refuse désormais INCONDITION-
   NELLEMENT (voir tests/test_remote_confirm_arrival.py, test_annuler_
   une_arrivee_deja_confirmee_est_refuse).
2. Mac : nouveau bouton "Corriger l'arrivée" (onglet Joueurs, juste
   après "Inscrire"), geste DIRTO qui remet un joueur "Actif" à "Pré-
   inscrit" — SANS jamais toucher jetons/table/siège, sans rééquili-
   brage, et qui reste possible MÊME après expiration du délai des
   retardataires (Database.correct_arrival_confirmation, voir sa
   docstring).
3. POINT DE SÉCURITÉ explicitement demandé : une fois une arrivée
   corrigée après expiration du délai, ni "Inscrire" (Mac) ni l'iPhone
   ("Confirmer") ne doivent jamais pouvoir reconfirmer ce joueur — testé
   ici bout en bout (Database.confirm_arrival refuse déjà, séparément,
   une fois le délai dépassé ; ce fichier le VÉRIFIE explicitement dans
   ce scénario précis plutôt que de le supposer).

Structure, même principe que tests/test_reinstate_preinscrit_extension.py
(App._correct_arrival_selected est le pendant DIRTO-Mac-post-démarrage de
App._reinstate_selected) :
1. Database.correct_arrival_confirmation — niveau DB pur.
2. App._correct_arrival_selected — gating, confirmation explicite,
   sélection mixte, non-régression jetons/table/siège.
3. Scénario de sécurité bout en bout : correction après expiration du
   délai, puis tentative de reconfirmation Mac ET iPhone.
4. _update_tournament_started_buttons — état/info-bulle du nouveau
   bouton "Corriger l'arrivée".
5. Bout en bout HTTP réel : preuve que /cancel_arrival refuse bien une
   requête directe (sans passer par un bouton) visant un joueur déjà
   "Actif"."""
import json
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
import player_photos  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from test_remote_confirm_arrival import _FakeRemoteCheckinApp, _new_db_cross_thread  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402,F401

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name="A", accueil_enabled=True):
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1 if accueil_enabled else 0})
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


# =====================================================================
# 1. Database.correct_arrival_confirmation — niveau DB pur
# =====================================================================
class CorrectArrivalConfirmationDatabaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="correct_arrival_db_test_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, accueil_enabled=True):
        db = _new_db(self._tmp.name, accueil_enabled=accueil_enabled)
        self.addCleanup(db.conn.close)
        return db

    def test_remet_un_joueur_actif_a_pre_inscrit(self):
        db = self._db()
        pid = db.add_player("Alice")
        db.confirm_arrival(pid)

        ok = db.correct_arrival_confirmation(pid)

        self.assertTrue(ok)
        after = db.get_player(pid)
        self.assertIsNone(after["arrived_at"])
        self.assertEqual(after["status"], "active")

    def test_fonctionne_meme_apres_expiration_du_delai(self):
        """Scénario explicitement demandé : DISTINCT de cancel_arrival,
        cette méthode doit rester possible MÊME une fois le délai des
        retardataires dépassé."""
        db = self._db()
        pid = db.add_player("Alice")
        _start_tournament(db, minutes_ago=5, duration_minutes=60)
        db.confirm_arrival(pid)
        # Le délai est désormais dépassé.
        db.set_settings({"tournament_start_epoch": int(time.time()) - 120 * 60})
        self.assertTrue(db.absence_deadline_passed())

        ok = db.correct_arrival_confirmation(pid)

        self.assertTrue(ok)
        self.assertIsNone(db.get_player(pid)["arrived_at"])

    def test_refuse_si_accueil_desactive(self):
        db = self._db(accueil_enabled=False)
        pid = db.add_player("Alice")
        db.set_settings({"accueil_management_enabled": 1})
        db.confirm_arrival(pid)
        db.set_settings({"accueil_management_enabled": 0})

        ok = db.correct_arrival_confirmation(pid)

        self.assertFalse(ok)
        self.assertIsNotNone(db.get_player(pid)["arrived_at"])

    def test_refuse_si_joueur_non_actif(self):
        db = self._db()
        pid = db.add_player("Alice")
        db.confirm_arrival(pid)
        db.withdraw_player(pid)

        ok = db.correct_arrival_confirmation(pid)

        self.assertFalse(ok)

    def test_refuse_si_rien_a_corriger_pre_inscrit_jamais_arrive(self):
        db = self._db()
        pid = db.add_player("Alice")

        ok = db.correct_arrival_confirmation(pid)

        self.assertFalse(ok)

    def test_refuse_si_joueur_signale_absent_jamais_arrive(self):
        db = self._db()
        pid = db.add_player("Alice")
        _start_tournament(db)
        db.declare_absent(pid)

        ok = db.correct_arrival_confirmation(pid)

        self.assertFalse(ok)

    def test_ne_touche_ni_jetons_ni_table_ni_siege_ni_rebalance(self):
        db = self._db()
        pid = db.add_player("Alice")
        db.set_chips(pid, 15975)
        db.confirm_arrival(pid)
        before = db.get_player(pid)
        seat_moves_before = db.count_seat_moves()

        db.correct_arrival_confirmation(pid)

        after = db.get_player(pid)
        self.assertEqual(after["chips"], 15975)
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])
        self.assertEqual(db.count_seat_moves(), seat_moves_before)

    def test_idempotent_deja_pre_inscrit_apres_une_premiere_correction(self):
        db = self._db()
        pid = db.add_player("Alice")
        db.confirm_arrival(pid)
        self.assertTrue(db.correct_arrival_confirmation(pid))

        # Rien à corriger une seconde fois : refusé proprement.
        ok = db.correct_arrival_confirmation(pid)

        self.assertFalse(ok)
        self.assertIsNone(db.get_player(pid)["arrived_at"])


# =====================================================================
# 2. App._correct_arrival_selected — gating, confirmation, sélection
# =====================================================================
class _FakeAppForCorrectArrival:
    """Correctif du 2026-10-03 ("Inscrire ignore le joueur sélectionné
    quand un autre est déjà accueilli") : _reinstate_selected lit
    désormais players_tree.selection() directement quand l'accueil
    pilote la case — exposé ici (mêmes _ids que _checked_or_selected_ids,
    ce conflit n'étant pas l'objet de ces tests, voir tests/test_
    inscrire_ignore_deja_accueillis.py pour celui-ci) avec la méthode
    réelle greffée pour refléter le vrai réglage accueil/démarrage."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)
        self.clear_checked_calls = 0
        self.refresh_all_calls = 0
        self.refresh_remote_players_calls = 0
        self.refresh_remote_checkin_calls = 0
        self.players_tree = types.SimpleNamespace(
            selection=lambda: [str(pid) for pid in self._ids]
        )

    def _players_checkbox_drives_accueil(self):
        return main.App._players_checkbox_drives_accueil(self)

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


class CorrectArrivalSelectedGatingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="correct_arrival_selected_test_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, accueil_enabled=True, started=True, minutes_ago=5, duration_minutes=60):
        db = _new_db(self._tmp.name, accueil_enabled=accueil_enabled)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        if started:
            _start_tournament(db, minutes_ago=minutes_ago, duration_minutes=duration_minutes)
        return db, pid

    def test_corrige_l_arrivee_quand_toutes_conditions_reunies(self):
        db, pid = self._db()
        db.confirm_arrival(pid)
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._correct_arrival_selected(app)

        after = db.get_player(pid)
        self.assertIsNone(after["arrived_at"])
        self.assertEqual(app.clear_checked_calls, 1)
        self.assertEqual(app.refresh_all_calls, 1)
        self.assertEqual(app.refresh_remote_players_calls, 1)
        self.assertEqual(app.refresh_remote_checkin_calls, 1)

    def test_demande_confirmation_explicite_avec_le_nom_du_joueur(self):
        db, pid = self._db()
        db.confirm_arrival(pid)
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_ask:
            main.App._correct_arrival_selected(app)

        mock_ask.assert_called_once()
        title, question = mock_ask.call_args[0]
        self.assertEqual(title, "Corriger l'arrivée")
        self.assertIn("Alice", question)
        self.assertIn("Pré-inscrit", question)

    def test_refuse_si_confirmation_declinee(self):
        db, pid = self._db()
        db.confirm_arrival(pid)
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno", return_value=False):
            main.App._correct_arrival_selected(app)

        after = db.get_player(pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertEqual(app.refresh_all_calls, 0)

    def test_avant_demarrage_aucun_effet_ni_dialogue(self):
        db, pid = self._db(started=False)
        # Un joueur ne peut pas être "Actif" avant le démarrage via le
        # parcours normal, mais on force l'état pour isoler ce gate.
        db.set_settings({"clock_started": 1})
        db.confirm_arrival(pid)
        db.set_settings({"clock_started": 0})
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno") as mock_ask:
            main.App._correct_arrival_selected(app)

        mock_ask.assert_not_called()
        self.assertIsNotNone(db.get_player(pid)["arrived_at"])

    def test_accueil_desactive_aucun_effet_ni_dialogue(self):
        db, pid = self._db()
        db.confirm_arrival(pid)
        db.set_settings({"accueil_management_enabled": 0})
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno") as mock_ask:
            main.App._correct_arrival_selected(app)

        mock_ask.assert_not_called()
        self.assertIsNotNone(db.get_player(pid)["arrived_at"])

    def test_ignore_silencieusement_un_pre_inscrit(self):
        db, pid = self._db()
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno") as mock_ask:
            main.App._correct_arrival_selected(app)

        mock_ask.assert_not_called()
        self.assertEqual(app.refresh_all_calls, 0)

    def test_ignore_silencieusement_un_signale_absent_jamais_arrive(self):
        db, pid = self._db()
        db.declare_absent(pid)
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno") as mock_ask:
            main.App._correct_arrival_selected(app)

        mock_ask.assert_not_called()
        after = db.get_player(pid)
        self.assertIsNotNone(after["absent_declared_at"])

    def test_ignore_un_joueur_reellement_desactive(self):
        db, pid = self._db()
        db.confirm_arrival(pid)
        db.withdraw_player(pid)
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno") as mock_ask:
            main.App._correct_arrival_selected(app)

        mock_ask.assert_not_called()

    def test_fonctionne_meme_apres_expiration_du_delai(self):
        db, pid = self._db(minutes_ago=5, duration_minutes=60)
        db.confirm_arrival(pid)
        db.set_settings({"tournament_start_epoch": int(time.time()) - 120 * 60})
        self.assertTrue(db.absence_deadline_passed())
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._correct_arrival_selected(app)

        self.assertIsNone(db.get_player(pid)["arrived_at"])

    def test_ne_touche_ni_jetons_ni_table_ni_siege(self):
        db, pid = self._db()
        db.set_chips(pid, 15975)
        db.confirm_arrival(pid)
        before = db.get_player(pid)
        seat_moves_before = db.count_seat_moves()
        app = _FakeAppForCorrectArrival(db, [pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._correct_arrival_selected(app)

        after = db.get_player(pid)
        self.assertEqual(after["chips"], 15975)
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])
        self.assertEqual(db.count_seat_moves(), seat_moves_before)


class MixedSelectionCorrectArrivalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="correct_arrival_mixed_selection_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)
        self.pid_preinscrit = self.db.add_player("Alice")
        self.pid_actif = self.db.add_player("Bob")
        self.db.confirm_arrival(self.pid_actif)
        self.pid_absent = self.db.add_player("Charlie")
        self.db.declare_absent(self.pid_absent)
        self.pid_withdrawn = self.db.add_player("Diana")
        self.db.withdraw_player(self.pid_withdrawn)
        self.pid_actif_2 = self.db.add_player("Eve")
        self.db.confirm_arrival(self.pid_actif_2)

    def test_ne_corrige_que_les_joueurs_reellement_actifs(self):
        app = _FakeAppForCorrectArrival(self.db, [
            self.pid_preinscrit, self.pid_actif, self.pid_absent,
            self.pid_withdrawn, self.pid_actif_2,
        ])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._correct_arrival_selected(app)

        self.assertIsNone(self.db.get_player(self.pid_actif)["arrived_at"])
        self.assertIsNone(self.db.get_player(self.pid_actif_2)["arrived_at"])
        # Inchangés : jamais "actifs et arrivés" au départ.
        self.assertIsNone(self.db.get_player(self.pid_preinscrit)["arrived_at"])
        absent_after = self.db.get_player(self.pid_absent)
        self.assertIsNone(absent_after["arrived_at"])
        self.assertIsNotNone(absent_after["absent_declared_at"])
        self.assertEqual(self.db.get_player(self.pid_withdrawn)["status"], "withdrawn")

    def test_question_au_pluriel_pour_plusieurs_joueurs(self):
        app = _FakeAppForCorrectArrival(self.db, [self.pid_actif, self.pid_actif_2])

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_ask:
            main.App._correct_arrival_selected(app)

        _, question = mock_ask.call_args[0]
        self.assertIn("2 joueurs", question)


# =====================================================================
# 3. Scénario de sécurité : correction après le délai, puis tentative
#    de reconfirmation Mac ET iPhone — POINT explicitement demandé.
# =====================================================================
class CorrectArrivalSecurityAfterDeadlineTest(unittest.TestCase):
    """Un joueur confirmé "Actif" PUIS corrigé "Corriger l'arrivée" après
    expiration du délai des retardataires doit rester "Pré-inscrit" de
    façon DÉFINITIVE : ni "Inscrire" (Mac, App._reinstate_selected) ni
    l'iPhone (Database.confirm_arrival via la route /confirm_arrival) ne
    doivent pouvoir le reconfirmer par la suite."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="correct_arrival_security_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_ni_inscrire_ni_iphone_ne_peuvent_reconfirmer_apres_correction_hors_delai(self):
        db = _new_db_cross_thread(self._tmp.name, "t.tournoi")
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        _start_tournament(db, minutes_ago=5, duration_minutes=60)

        # 1) Confirmation normale (dans les délais).
        self.assertTrue(db.confirm_arrival(pid))
        self.assertIsNotNone(db.get_player(pid)["arrived_at"])

        # 2) Le délai des retardataires expire.
        db.set_settings({"tournament_start_epoch": int(time.time()) - 120 * 60})
        self.assertTrue(db.absence_deadline_passed())

        # 3) Le DIRTO corrige l'arrivée depuis le Mac, MÊME hors délai.
        mac_app = _FakeAppForCorrectArrival(db, [pid])
        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._correct_arrival_selected(mac_app)
        self.assertIsNone(db.get_player(pid)["arrived_at"])

        # 4) POINT DE SÉCURITÉ — tentative de reconfirmation via
        #    "Inscrire" sur Mac : doit rester SANS EFFET (délai dépassé).
        #    _reinstate_selected affiche un messagebox.showinfo natif dans
        #    ce cas ("Confirmation impossible") — mocké ici comme dans
        #    tests/test_reinstate_preinscrit_extension.py, sans quoi un
        #    VRAI dialogue macOS bloquerait ce test indéfiniment.
        reinstate_app = _FakeAppForCorrectArrival(db, [pid])
        with patch.object(main.messagebox, "showinfo"):
            main.App._reinstate_selected(reinstate_app)
        self.assertIsNone(
            db.get_player(pid)["arrived_at"],
            "« Inscrire » a reconfirmé un joueur corrigé hors délai — faille de sécurité.",
        )

        # 5) POINT DE SÉCURITÉ — tentative de reconfirmation depuis
        #    l'iPhone (voie réelle : file d'attente + thread consommateur,
        #    identique au chemin HTTP réel) : doit aussi échouer.
        phone_app = _FakeRemoteCheckinApp(db)
        self.addCleanup(phone_app.stop)
        result = phone_app._remote_confirm_arrival_request(pid)
        self.assertFalse(
            result["ok"],
            "L'iPhone a reconfirmé un joueur corrigé hors délai — faille de sécurité.",
        )
        self.assertIsNone(db.get_player(pid)["arrived_at"])

        # 6) Le joueur reste "Pré-inscrit" de façon définitive ; seul
        #    "Abs/Dés" (déclarer absent) reste disponible pour le
        #    désactiver formellement — non testé ici (hors périmètre),
        #    simplement vérifié comme n'étant pas bloqué par cet état.
        self.assertTrue(db.declare_absent(pid))


# =====================================================================
# 4. _update_tournament_started_buttons — état/info-bulle du bouton
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
class UpdateTournamentStartedButtonsCorrectArrivalGatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        roster_patcher = patch.object(roster, "list_clubs", return_value=[])
        self.addCleanup(roster_patcher.stop)
        roster_patcher.start()
        photo_patcher = patch.object(player_photos, "get_photo_path", return_value=None)
        self.addCleanup(photo_patcher.stop)
        photo_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="correct_arrival_btn_gating_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.add_player("Alice")

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
        self.win._abs_des_btn_tooltip_normal_text = ""
        self.win.abs_des_btn_tooltip = main.Tooltip(self.win.abs_des_btn, "")
        self.win.correct_arrival_btn = ttk.Button(parent)
        self.win._correct_arrival_btn_tooltip_normal_text = "TEXTE NORMAL CORRIGER"
        self.win.correct_arrival_btn_tooltip = main.Tooltip(
            self.win.correct_arrival_btn, self.win._correct_arrival_btn_tooltip_normal_text
        )
        self.win.player_photo_images = {}

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

    def test_desactive_quand_gestion_accueil_desactivee(self):
        self.db.set_settings({"accueil_management_enabled": 0})

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.correct_arrival_btn.cget("state")), "disabled")
        self.assertIn("Gestion de l'accueil", self.win.correct_arrival_btn_tooltip.text)

    def test_desactive_avant_demarrage_quand_accueil_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.correct_arrival_btn.cget("state")), "disabled")
        self.assertIn("n'a pas encore démarré", self.win.correct_arrival_btn_tooltip.text)

    def test_actif_apres_demarrage_quand_accueil_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        _start_tournament(self.db)

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.correct_arrival_btn.cget("state")), "normal")
        self.assertEqual(self.win.correct_arrival_btn_tooltip.text, "TEXTE NORMAL CORRIGER")


# =====================================================================
# 5. Bout en bout HTTP réel : /cancel_arrival refuse une requête
#    directe visant un joueur déjà "Actif" — preuve que la protection
#    est bien côté serveur, jamais seulement l'absence du bouton.
# =====================================================================
class DirectCancelArrivalHttpRequestRefusedTest(_RemoteControlHttpTestCase):
    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="correct_arrival_http_test_")
        self.addCleanup(self._tmp2.cleanup)
        self.db = _new_db_cross_thread(self._tmp2.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.id_a = self.db.add_player("Alice")
        self.fake = _FakeRemoteCheckinApp(self.db)
        self.addCleanup(self.fake.stop)

        import remote_control

        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_checkin_players=lambda: [],
            on_confirm_arrival=self.fake._remote_confirm_arrival_request,
            on_cancel_arrival=self.fake._remote_cancel_arrival_request,
            get_accueil_management_enabled=lambda: True,
            port=self.port,
        ))

    def test_requete_directe_vers_cancel_arrival_refusee_pour_un_joueur_actif(self):
        """Simule un client qui n'affiche plus de bouton "Annuler" mais
        enverrait quand même la requête HTTP directement (client modifié,
        rejeu réseau, etc.) : /cancel_arrival doit répondre ok=false et
        ne rien modifier en base, exactement comme si le bouton avait
        encore existé — la vraie protection est le serveur, pas le
        masquage du bouton (voir App._remote_cancel_arrival)."""
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        confirm_status, confirm_body, _ = http_request(
            self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.id_a},
        )
        self.assertEqual(confirm_status, 200)
        self.assertTrue(json.loads(confirm_body)["ok"])
        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])

        status, body, _ = http_request(
            self.base, "POST", "/cancel_arrival", jar, body={"player_id": self.id_a},
        )

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertFalse(data["ok"])
        self.assertTrue(data.get("message"))
        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])


if __name__ == "__main__":
    unittest.main()
