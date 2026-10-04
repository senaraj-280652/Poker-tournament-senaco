# -*- coding: utf-8 -*-
""""Pré-inscrit"/"Inscrit"/"Actif" — complément du 2026-09-29 au cahier
des charges du Lot 2, uniquement pertinent quand « Gestion de l'accueil »
est activée pour ce tournoi (voir tests/test_accueil_management_toggle.py).
Distinction "Inscrit"/"Actif" selon le démarrage ajoutée le 2026-10-03
("vocabulaire plus parlant avant le démarrage, aligné sur celui déjà
affiché côté téléphone" — voir remote_control.py, `.inscrit-label`) :

- Colonne "Statut" de l'onglet Joueurs : un joueur ACTIF ("technique",
  `player["status"] == "active"`) s'affiche "Pré-inscrit" tant que son
  arrivée n'a pas été confirmée (`arrived_at` NULL) ; une fois confirmée,
  "Inscrit" tant que le tournoi n'a pas démarré, "Actif" une fois
  démarré — AUCUN nouvel état technique créé, uniquement un libellé
  d'affichage (App._accueil_status_label), voir cahier des charges
  section 4. La transition "Inscrit" -> "Actif" au démarrage est
  PUREMENT visuelle : `arrived_at` ne change pas, aucune nouvelle
  confirmation, aucun effet sur table/siège/rééquilibrage.
- Mise à jour IMMÉDIATE (sans reconstruire tout le tableau) après une
  confirmation/annulation, depuis la case PC/Mac (_on_players_tree_
  click/_check_all_players/_uncheck_all_players), le bouton "Inscrire"
  (_reinstate_selected) ou depuis le téléphone (_sync_checkbox_for_
  arrival) — y compris après le démarrage du tournoi, pour un
  retardataire, SANS recocher sa case (App._apply_accueil_status_display).
- Un joueur non actif (forfait/éliminé) garde son libellé habituel,
  jamais "Pré-inscrit" simplement parce qu'il n'a pas de confirmation
  d'arrivée.
- Un joueur signalé absent (Abs/Dés, encore `active`, jamais accueilli)
  reste "Pré-inscrit" — le clignotement de sa ligne signale déjà
  visuellement ce cas.
- Réinscrire (reinstate_player) et Abs/Dés (withdraw_player) ne touchent
  jamais `arrived_at` : le libellé qui en découle reste cohérent avant/
  après ces mécanismes historiques, inchangés par ce complément.
- Réglage désactivé (comportement par défaut) : libellés inchangés
  ("Actif" pour tout joueur actif, jamais "Inscrit"), quelle que soit la
  valeur d'`arrived_at` (y compris une valeur ancienne jamais effacée,
  voir cahier des charges section 4 du complément précédent)."""
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

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name="A"):
    return database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


# =====================================================================
# 1. App._accueil_status_label — unité pure, sans Tk
# =====================================================================
class AccueilStatusLabelTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_status_label_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.win = types.SimpleNamespace(db=self.db)
        self.win._accueil_status_label = types.MethodType(main.App._accueil_status_label, self.win)

    def test_actif_pas_encore_accueilli_est_pre_inscrit_quand_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Pré-inscrit")

    def test_actif_accueilli_est_inscrit_avant_demarrage_quand_active(self):
        """Modification du 2026-10-03 : "Inscrit", pas "Actif", tant que
        le tournoi n'a pas démarré (voir module docstring)."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Inscrit")

    def test_transition_complete_pre_inscrit_inscrit_demarrage_actif(self):
        """Transition complète explicitement demandée (2026-10-03) :
        Pré-inscrit -> Inscrit -> démarrage du tournoi -> Actif.
        PUREMENT visuelle au moment du démarrage : aucune nouvelle
        confirmation, `arrived_at` inchangé, aucun effet sur table/
        siège/rééquilibrage (vérifié ici au niveau du libellé ; voir
        ClockResumeTransitionsInscritToActifTest plus bas pour la
        vérification du reste de l'état, via App._clock_resume)."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Pré-inscrit")

        self.db.confirm_arrival(pid)
        arrived_at_apres_confirmation = self.db.get_player(pid)["arrived_at"]
        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Inscrit")

        _start_tournament(self.db)  # démarrage : AUCUN appel à confirm_arrival

        p_apres_demarrage = self.db.get_player(pid)
        self.assertEqual(self.win._accueil_status_label(p_apres_demarrage), "Actif")
        self.assertEqual(p_apres_demarrage["arrived_at"], arrived_at_apres_confirmation)

    def test_actif_pas_accueilli_reste_actif_quand_desactive(self):
        """Comportement par défaut (réglage désactivé) : jamais
        "Pré-inscrit", quelle que soit la valeur d'arrived_at — cahier
        des charges, section 4 : "conserver intégralement les statuts et
        comportements habituels"."""
        self.db.set_settings({"accueil_management_enabled": 0})
        pid = self.db.add_player("Alice")
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Actif")

    def test_arrived_at_ancien_ignore_quand_desactive_apres_coup(self):
        """Le réglage a pu être activé puis désactivé après coup : les
        données `arrived_at` déjà écrites ne sont jamais effacées (voir
        tests/test_accueil_management_toggle.py), mais le libellé
        redevient "Actif" pur dès que le réglage est désactivé — jamais
        "Pré-inscrit" pour un joueur qui EST arrivé, jamais "Actif
        parce qu'accueilli" pour un tournoi qui n'utilise plus l'accueil."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        self.db.set_settings({"accueil_management_enabled": 0})
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Actif")

    def test_joueur_desactive_forfait_jamais_pre_inscrit(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.withdraw_player(pid)
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Forfait")

    def test_joueur_elimine_jamais_pre_inscrit(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        pid2 = self.db.add_player("Bob")
        self.db.eliminate_player(pid, eliminated_by_id=pid2)
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Éliminé")

    def test_signale_absent_affiche_absent(self):
        """Harmonisation Mac/iPhone du 2026-09-30 : un joueur signalé
        absent (Abs/Dés) affiche désormais explicitement "Absent" —
        avant cette modification, il restait affiché "Pré-inscrit", le
        clignotement de sa ligne étant seul à signaler l'absence. Voir
        tests/test_status_absent_harmonization.py pour la couverture
        dédiée de cette modification."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(pid)
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Absent")

    def test_confirmation_apres_le_demarrage_devient_actif(self):
        """Retardataire confirmé pendant le délai autorisé (après le
        démarrage) : passe bien à "Actif"."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.confirm_arrival(pid)
        p = self.db.get_player(pid)

        self.assertEqual(self.win._accueil_status_label(p), "Actif")

    def test_reste_pre_inscrit_apres_expiration_du_delai(self):
        """Après expiration : confirm_arrival refuse déjà (voir Database.
        confirm_arrival/absence_deadline_passed) — un joueur jamais
        accueilli reste donc mécaniquement "Pré-inscrit", jamais
        désactivé automatiquement (cahier des charges : "seul le DIRTO
        décide")."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=65, duration_minutes=60)

        self.assertFalse(self.db.confirm_arrival(pid))
        p = self.db.get_player(pid)
        self.assertEqual(self.win._accueil_status_label(p), "Pré-inscrit")
        self.assertEqual(p["status"], "active")

    def test_annulation_avant_demarrage_redevient_pre_inscrit(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        self.assertEqual(
            self.win._accueil_status_label(self.db.get_player(pid)), "Inscrit"
        )

        self.db.cancel_arrival(pid)

        self.assertEqual(
            self.win._accueil_status_label(self.db.get_player(pid)), "Pré-inscrit"
        )

    def test_reinstate_player_preserve_arrived_at_donc_le_libelle(self):
        """Réinscrire (reinstate_player) ne touche jamais arrived_at
        (mécanisme historique inchangé) : un joueur réinscrit qui avait
        déjà été accueilli avant son forfait (ici, avant le démarrage)
        redevient "Inscrit", pas "Pré-inscrit"."""
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        self.db.withdraw_player(pid)
        self.assertEqual(
            self.win._accueil_status_label(self.db.get_player(pid)), "Forfait"
        )

        self.db.reinstate_player(pid)

        self.assertEqual(
            self.win._accueil_status_label(self.db.get_player(pid)), "Inscrit"
        )

    def test_reinstate_player_jamais_accueilli_redevient_pre_inscrit(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.withdraw_player(pid)

        self.db.reinstate_player(pid)

        self.assertEqual(
            self.win._accueil_status_label(self.db.get_player(pid)), "Pré-inscrit"
        )


# =====================================================================
# 2. App._refresh_players_tab — colonne "Statut" complète
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
    "_players_absent_blink_tick", "_update_sort_headings", "_accueil_status_label",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RefreshPlayersTabStatusColumnTest(unittest.TestCase):
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

        self._tmp = tempfile.TemporaryDirectory(prefix="refresh_players_status_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

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
        self.win._correct_arrival_btn_tooltip_normal_text = ""
        self.win.correct_arrival_btn_tooltip = main.Tooltip(self.win.correct_arrival_btn, "")
        self.win.player_photo_images = {}

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

    def test_colonne_statut_quand_accueil_active(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid_pre = self.db.add_player("Alice")
        pid_actif = self.db.add_player("Bob")
        self.db.confirm_arrival(pid_actif)
        pid_forfait = self.db.add_player("Charlie")
        self.db.withdraw_player(pid_forfait)

        self.win._refresh_players_tab()

        self.assertEqual(self.win.players_tree.set(str(pid_pre), "status"), "Pré-inscrit")
        self.assertEqual(self.win.players_tree.set(str(pid_actif), "status"), "Inscrit")
        self.assertEqual(self.win.players_tree.set(str(pid_forfait), "status"), "Forfait")

    def test_colonne_statut_quand_accueil_desactive(self):
        self.db.set_settings({"accueil_management_enabled": 0})
        pid = self.db.add_player("Alice")

        self.win._refresh_players_tab()

        self.assertEqual(self.win.players_tree.set(str(pid), "status"), "Actif")

    def test_apres_demarrage_pre_inscrit_reste_pre_inscrit(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)

        self.win._refresh_players_tab()

        self.assertEqual(self.win.players_tree.set(str(pid), "status"), "Pré-inscrit")

    def test_apres_demarrage_deja_accueilli_reste_actif(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        _start_tournament(self.db)

        self.win._refresh_players_tab()

        self.assertEqual(self.win.players_tree.set(str(pid), "status"), "Actif")


# =====================================================================
# 3. Mise à jour immédiate, sans reconstruire tout le tableau
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ImmediateStatusUpdateOnMacClickTest(unittest.TestCase):
    """_on_players_tree_click : la colonne "Statut" bascule IMMÉDIATEMENT
    (pas seulement la case à cocher), avant même le démarrage."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="immediate_status_click_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.db.set_settings({"accueil_management_enabled": 1})
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        self.tree.insert("", "end", iid=str(self.pid), values=("", "Alice", "Pré-inscrit"))

        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids=set(),
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
        )
        for meth in (
            "_on_players_tree_click", "_apply_checkbox_display",
            "_update_checked_count_label", "_players_checkbox_drives_accueil",
            "_update_players_stats_label", "_accueil_status_label",
            "_apply_accueil_status_display",
        ):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_checkin_cache = lambda: None

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def _click_checkbox(self, row_iid):
        event = types.SimpleNamespace(x=0, y=0)
        with patch.object(self.tree, "identify", return_value="cell"), \
             patch.object(self.tree, "identify_column", return_value="#1"), \
             patch.object(self.tree, "identify_row", return_value=row_iid):
            self.win._on_players_tree_click(event)

    def test_cocher_bascule_immediatement_vers_inscrit(self):
        self._click_checkbox(str(self.pid))

        self.assertEqual(self.tree.set(str(self.pid), "status"), "Inscrit")

    def test_decocher_rebascule_immediatement_vers_pre_inscrit(self):
        self._click_checkbox(str(self.pid))  # coche -> Inscrit
        self._click_checkbox(str(self.pid))  # décoche -> Pré-inscrit

        self.assertEqual(self.tree.set(str(self.pid), "status"), "Pré-inscrit")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ImmediateStatusUpdateCheckAllTest(unittest.TestCase):
    """_check_all_players/_uncheck_all_players : même mise à jour
    immédiate de la colonne "Statut" pour TOUS les joueurs concernés."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="immediate_status_check_all_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.db.set_settings({"accueil_management_enabled": 1})
        self.addCleanup(self.db.conn.close)
        self.id_a = self.db.add_player("Alice")
        self.id_b = self.db.add_player("Bob")

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        for pid, name in ((self.id_a, "Alice"), (self.id_b, "Bob")):
            self.tree.insert("", "end", iid=str(pid), values=("", name, "Pré-inscrit"))

        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids=set(),
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
        )
        for meth in (
            "_check_all_players", "_uncheck_all_players", "_apply_checkbox_display",
            "_update_checked_count_label", "_players_checkbox_drives_accueil",
            "_update_players_stats_label", "_accueil_status_label",
            "_apply_accueil_status_display",
        ):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_checkin_cache = lambda: None

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def test_tout_cocher_bascule_tous_les_statuts_vers_inscrit(self):
        self.win._check_all_players()

        self.assertEqual(self.tree.set(str(self.id_a), "status"), "Inscrit")
        self.assertEqual(self.tree.set(str(self.id_b), "status"), "Inscrit")

    def test_tout_decocher_rebascule_tous_les_statuts_vers_pre_inscrit(self):
        self.win._check_all_players()

        self.win._uncheck_all_players()

        self.assertEqual(self.tree.set(str(self.id_a), "status"), "Pré-inscrit")
        self.assertEqual(self.tree.set(str(self.id_b), "status"), "Pré-inscrit")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ImmediateStatusUpdateFromPhoneTest(unittest.TestCase):
    """_sync_checkbox_for_arrival (Lot 1, déclenché depuis le téléphone) :
    la colonne "Statut" bascule elle aussi, y compris APRÈS le démarrage
    pour un retardataire — SANS jamais recocher sa case à cocher."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="immediate_status_phone_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.db.set_settings({"accueil_management_enabled": 1})
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        self.tree.insert(
            "", "end", iid=str(self.pid),
            values=(main.App.CHECKBOX_UNCHECKED, "Alice", "Pré-inscrit"),
        )

        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids=set(),
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
        )
        for meth in (
            "_sync_checkbox_for_arrival", "_apply_checkbox_display",
            "_update_checked_count_label", "_players_checkbox_drives_accueil",
            "_update_players_stats_label", "_accueil_status_label",
            "_apply_accueil_status_display",
            # Correctif du 2026-09-29 ("retour d'un joueur absent") :
            # _sync_checkbox_for_arrival en a désormais besoin.
            "_clear_absent_blink_tag", "_cancel_players_absent_blink",
        ):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def test_confirmation_avant_demarrage_bascule_vers_inscrit(self):
        self.db.confirm_arrival(self.pid)

        self.win._sync_checkbox_for_arrival(self.pid, True)

        self.assertEqual(self.tree.set(str(self.pid), "status"), "Inscrit")
        self.assertIn(self.pid, self.win.checked_player_ids)

    def test_retardataire_apres_demarrage_bascule_vers_actif_sans_recocher(self):
        _start_tournament(self.db)
        self.db.confirm_arrival(self.pid)

        self.win._sync_checkbox_for_arrival(self.pid, True)

        self.assertEqual(self.tree.set(str(self.pid), "status"), "Actif")
        # La case à cocher ne redevient PAS un indicateur d'accueil après
        # le démarrage (voir _players_checkbox_drives_accueil) : jamais
        # recochée automatiquement pour ce retardataire.
        self.assertNotIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.pid), "sel"), main.App.CHECKBOX_UNCHECKED)


if __name__ == "__main__":
    unittest.main()
