# -*- coding: utf-8 -*-
"""Correction du 2026-10-03 ("échec du test manuel Inscrire") — scénario
réel qui avait échappé à tous les tests précédents : Alice déjà
accueillie (donc cochée, la case EST l'indicateur d'accueil avant le
démarrage) + Bob Pré-inscrit réellement sélectionné dans le Treeview
(clic sur sa ligne) -> clic sur « Inscrire » confirmait à tort ALICE
(déjà accueillie, branche "ignoré, sans effet") au lieu de BOB, car
`_checked_or_selected_ids()` donne la priorité aux joueurs cochés sur la
sélection réelle — rôle historique volontaire pour Éliminer/Désactiver/
etc., mais incompatible avec l'usage de la case comme indicateur
d'accueil avant le démarrage (dès qu'un seul joueur est déjà accueilli,
checked_player_ids n'est plus jamais vide).

Correction LOCALE à App._reinstate_selected (voir sa docstring,
main.py) : quand `_players_checkbox_drives_accueil()` est vrai (accueil
activé, tournoi non démarré), "Inscrire" lit directement la sélection
RÉELLE du Treeview (`players_tree.selection()`), jamais
`checked_player_ids`. Dans tous les autres cas (après démarrage, ou
accueil désactivé), `_checked_or_selected_ids()` reste utilisée
EXACTEMENT comme avant — AUCUNE modification de cette fonction elle-
même, ni des autres boutons qui s'en servent (Éliminer, Désactiver,
Supprimer, Rebuy, Add-on, Corriger l'arrivée), ni du fonctionnement des
cases, ni du chemin iPhone.

Couvre :
1. Scénario exact signalé : Alice cochée (accueillie) + Bob sélectionné
   (Pré-inscrit) -> Inscrire confirme Bob, Alice n'est PAS reconfirmée
   (son `arrived_at` ne change pas).
2. Sélection multiple (Ctrl/Maj) : plusieurs joueurs déjà accueillis +
   plusieurs Pré-inscrits sélectionnés -> seuls les Pré-inscrits
   sélectionnés sont confirmés.
3. Table/siège inchangés, aucun `rebalance_tables`, pour l'ensemble des
   joueurs du tournoi (pas seulement les joueurs concernés).
4. Compteur "accueilli(s)" et case à cocher correctement actualisés
   après "Inscrire" (au rafraîchissement suivant).
5. Un joueur déjà accueilli accidentellement inclus dans la sélection
   réelle (en plus d'un Pré-inscrit) n'est pas reconfirmé.
6. Non-régression : après démarrage, "Inscrire" continue d'utiliser
   `_checked_or_selected_ids()` (cases cochées prioritaires sur la
   sélection) — comportement historique inchangé.
7. Non-régression : accueil désactivé -> `_checked_or_selected_ids()`
   utilisée comme avant.
8. Non-régression : réellement désactivé (forfait) sélectionné avant le
   démarrage -> reinstate_player, inchangé."""
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


def _snapshot_positions(db):
    return {
        p["id"]: (p["table_id"], p["seat"])
        for p in db.list_players(status="active")
    }


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
    "_reinstate_selected", "_checked_or_selected_ids", "_clear_checked",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class InscrireIgnoreDejaAccueillisTest(unittest.TestCase):
    """Même fixture (grafted methods sur un vrai tk.Tk) que tests/
    test_reinstate_preinscrit_extension.py::ReinstatePreInscritCounterAnd
    RefreshTest, reprise à l'identique pour garder ce fichier indépendant
    — complétée avec de vraies sélections Treeview (`selection_set`, API
    native équivalente à un clic/Ctrl-clic réel) plutôt qu'une
    manipulation directe de checked_player_ids."""

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

        self._tmp = tempfile.TemporaryDirectory(prefix="inscrire_ignore_deja_accueillis_test_")
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
        self.win._players_absent_blink_active = False
        self.win._players_absent_blink_phase = False
        self.win._players_absent_blink_after_id = None

        for meth in _GRAFTED_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_players_cache = lambda: None
        self.win._refresh_remote_checkin_cache = lambda: None
        self.win._refresh_all = self.win._refresh_players_tab

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

    def _select_rows(self, pids):
        """Sélection RÉELLE du Treeview — selection_set est l'API native
        qu'un clic (ou Ctrl/Maj-clic pour plusieurs) invoque en interne,
        voir ttk::treeview::Select ; équivalent fidèle sans avoir à
        simuler des combinaisons de touches."""
        self.win.players_tree.selection_set(tuple(str(p) for p in pids))

    # -- 1. Scénario exact signalé ----------------------------------------

    def test_alice_dejaaccueillie_bob_preinscrit_selectionne_inscrire_confirme_bob(self):
        pid_alice = self.db.add_player("Alice")
        pid_bob = self.db.add_player("Bob")
        self.db.confirm_arrival(pid_alice)
        alice_arrived_at_avant = self.db.get_player(pid_alice)["arrived_at"]
        self.win._refresh_players_tab()  # recharge checked_player_ids = {alice}
        self.assertIn(pid_alice, self.win.checked_player_ids)
        self.assertNotIn(pid_bob, self.win.checked_player_ids)

        self._select_rows([pid_bob])
        self.win._reinstate_selected()

        bob_after = self.db.get_player(pid_bob)
        alice_after = self.db.get_player(pid_alice)
        self.assertIsNotNone(bob_after["arrived_at"])
        self.assertEqual(bob_after["status"], "active")
        self.assertEqual(alice_after["arrived_at"], alice_arrived_at_avant)  # PAS reconfirmée

    # -- 2. Sélection multiple ---------------------------------------------

    def test_plusieurs_deja_accueillis_plusieurs_preinscrits_selectionnes(self):
        pid_alice = self.db.add_player("Alice")
        pid_carol = self.db.add_player("Carol")
        pid_bob = self.db.add_player("Bob")
        pid_dave = self.db.add_player("Dave")
        self.db.confirm_arrival(pid_alice)
        self.db.confirm_arrival(pid_carol)
        alice_avant = self.db.get_player(pid_alice)["arrived_at"]
        carol_avant = self.db.get_player(pid_carol)["arrived_at"]
        self.win._refresh_players_tab()

        self._select_rows([pid_bob, pid_dave])
        self.win._reinstate_selected()

        self.assertIsNotNone(self.db.get_player(pid_bob)["arrived_at"])
        self.assertIsNotNone(self.db.get_player(pid_dave)["arrived_at"])
        self.assertEqual(self.db.get_player(pid_alice)["arrived_at"], alice_avant)
        self.assertEqual(self.db.get_player(pid_carol)["arrived_at"], carol_avant)

    # -- 3. Places figées ---------------------------------------------------

    def test_table_et_siege_inchanges_aucun_rebalance(self):
        pid_alice = self.db.add_player("Alice")
        self.db.add_player("Carol")
        pid_bob = self.db.add_player("Bob")
        self.db.add_player("Dave")
        self.db.confirm_arrival(pid_alice)
        self.win._refresh_players_tab()
        before = _snapshot_positions(self.db)

        self._select_rows([pid_bob])
        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            self.win._reinstate_selected()
            spy.assert_not_called()

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)

    # -- 4. Compteur et case actualisés --------------------------------------

    def test_compteur_et_case_actualises_pour_le_bon_joueur(self):
        pid_alice = self.db.add_player("Alice")
        pid_bob = self.db.add_player("Bob")
        self.db.confirm_arrival(pid_alice)
        self.win._refresh_players_tab()
        self.assertIn("1 accueilli", self.win.stats_lbl.cget("text"))

        self._select_rows([pid_bob])
        self.win._reinstate_selected()

        self.assertIn("2 accueilli", self.win.stats_lbl.cget("text"))
        self.assertEqual(
            self.win.players_tree.set(str(pid_bob), "sel"), main.App.CHECKBOX_CHECKED
        )
        self.assertEqual(
            self.win.players_tree.set(str(pid_bob), "status"), "Inscrit"
        )

    # -- 5. Déjà-accueilli inclus par erreur dans la sélection ---------------

    def test_deja_accueilli_inclus_dans_la_selection_nest_pas_reconfirme(self):
        pid_alice = self.db.add_player("Alice")
        pid_bob = self.db.add_player("Bob")
        self.db.confirm_arrival(pid_alice)
        alice_avant = self.db.get_player(pid_alice)["arrived_at"]
        self.win._refresh_players_tab()

        self._select_rows([pid_alice, pid_bob])  # Alice ET Bob sélectionnés
        self.win._reinstate_selected()

        self.assertIsNotNone(self.db.get_player(pid_bob)["arrived_at"])
        self.assertEqual(self.db.get_player(pid_alice)["arrived_at"], alice_avant)

    # -- 6. Non-régression : après démarrage, comportement historique -------

    def test_apres_demarrage_cases_cochees_restent_prioritaires(self):
        """Non-régression explicite : après démarrage,
        _players_checkbox_drives_accueil() est faux -> _reinstate_selected
        doit continuer d'utiliser _checked_or_selected_ids() (cases
        cochées prioritaires sur la sélection), comme avant ce correctif."""
        pid_alice = self.db.add_player("Alice")
        pid_bob = self.db.add_player("Bob")
        _start_tournament(self.db)
        self.win._refresh_players_tab()
        self.assertFalse(self.win._players_checkbox_drives_accueil())

        # Bob est COCHÉ (présélection classique pour action groupée,
        # sens historique après démarrage), Alice est seulement
        # sélectionnée dans le Treeview (sans être cochée) : le
        # comportement historique doit agir sur Bob (coché), pas Alice.
        self.win.checked_player_ids = {pid_bob}
        self._select_rows([pid_alice])

        self.win._reinstate_selected()

        self.assertIsNotNone(self.db.get_player(pid_bob)["arrived_at"])
        self.assertIsNone(self.db.get_player(pid_alice)["arrived_at"])

    # -- 7. Non-régression : accueil désactivé -------------------------------

    def test_accueil_desactive_cases_cochees_restent_prioritaires(self):
        self.db.set_settings({"accueil_management_enabled": 0})
        pid_alice = self.db.add_player("Alice")
        pid_bob = self.db.add_player("Bob")
        self.win._refresh_players_tab()
        self.assertFalse(self.win._players_checkbox_drives_accueil())

        self.win.checked_player_ids = {pid_bob}
        self._select_rows([pid_alice])

        self.win._reinstate_selected()

        # Accueil désactivé : aucun effet pour quiconque (comportement
        # historique d'un tournoi club classique, inchangé).
        self.assertIsNone(self.db.get_player(pid_bob)["arrived_at"])
        self.assertIsNone(self.db.get_player(pid_alice)["arrived_at"])

    # -- 8. Non-régression : forfait sélectionné avant démarrage -------------

    def test_forfait_selectionne_avant_demarrage_utilise_reinstate_player(self):
        pid_withdrawn = self.db.add_player("Alice")
        self.db.withdraw_player(pid_withdrawn)
        self.win._refresh_players_tab()

        self._select_rows([pid_withdrawn])
        self.win._reinstate_selected()

        after = self.db.get_player(pid_withdrawn)
        self.assertEqual(after["status"], "active")
        self.assertIsNotNone(after["table_id"])  # reinstate_player réattribue un siège


if __name__ == "__main__":
    unittest.main()
