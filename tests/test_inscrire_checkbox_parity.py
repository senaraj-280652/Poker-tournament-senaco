# -*- coding: utf-8 -*-
"""Bouton « Inscrire » équivalent à la case à cocher, AVANT le démarrage
du tournoi (demande du 2026-10-03, "BOUTON INSCRIRE POUR UN JOUEUR
PRÉINSCRIT") — complément à tests/test_reinstate_preinscrit_extension.py
(couverture générale du gate retiré) : ce fichier vérifie spécifiquement
la PARITÉ entre les deux mécanismes, exigée explicitement par la
validation : « même résultat ; même mise à jour du compteur ; même état
de la case ; table et siège strictement inchangés ; aucun autre joueur
déplacé ; aucun rééquilibrage. »

Avant ce correctif, `App._reinstate_selected` ("Inscrire") refusait
d'agir sur un Pré-inscrit tant que `clock_started != 1` — seule la case
à cocher de l'onglet Joueurs (`_on_players_tree_click`) fonctionnait
avant le démarrage. Le gate a été retiré : les deux mécanismes partagent
désormais exactement le même appel à Database.confirm_arrival, qui
assure à lui seul toutes les garanties de sécurité (délai, accueil
activé, aucune écriture table/siège, aucun rééquilibrage — voir sa
docstring, database.py).

Couvre :
1. Avant le démarrage, confirmer via la case OU via "Inscrire" produit
   un résultat strictement identique pour deux joueurs équivalents :
   même `arrived_at` posé, même libellé "Inscrit" (pas "Actif" — modifi-
   cation du 2026-10-03, vocabulaire aligné sur le téléphone avant le
   démarrage) affiché dans la colonne Statut, même case cochée, même
   incrément du compteur "accueilli(s)".
2. Table/siège inchangés, aucun autre joueur déplacé, aucun
   rééquilibrage — pour chacun des deux mécanismes.
3. "Inscrire" fonctionne aussi bien avant qu'après le démarrage (non-
   régression explicite du comportement déjà existant après démarrage)."""
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
    "_on_players_tree_click", "_apply_checkbox_display", "_apply_accueil_status_display",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class InscrireCheckboxParityTest(unittest.TestCase):
    """Même fixture (grafted methods sur un vrai tk.Tk, même principe que
    tests/test_reinstate_preinscrit_extension.py::ReinstatePreInscritCounter
    AndRefreshTest, reprise à l'identique plutôt qu'importée pour garder
    ce fichier indépendant), SANS appeler _start_tournament par défaut —
    exactement le scénario qui échouait avant ce correctif."""

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

        self._tmp = tempfile.TemporaryDirectory(prefix="inscrire_checkbox_parity_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid_checkbox = self.db.add_player("Alice")  # confirmée via la case
        self.pid_inscrire = self.db.add_player("Bob")    # confirmée via "Inscrire"
        self.pid_bystander = self.db.add_player("Charlie")  # ne doit jamais bouger

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
        self.remote_checkin_refreshes = 0
        self.win._refresh_remote_players_cache = lambda: None
        self.win._refresh_remote_checkin_cache = self._count_checkin_refresh
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

        # Un premier rafraîchissement complet pour peupler le tableau
        # (même point de départ pour les deux mécanismes comparés).
        self.win._refresh_players_tab()

    def _count_checkin_refresh(self):
        self.remote_checkin_refreshes += 1

    def _click_checkbox(self, pid):
        event = types.SimpleNamespace(x=0, y=0)
        with patch.object(self.win.players_tree, "identify", return_value="cell"), \
             patch.object(self.win.players_tree, "identify_column", return_value="#1"), \
             patch.object(self.win.players_tree, "identify_row", return_value=str(pid)):
            self.win._on_players_tree_click(event)

    def _click_inscrire(self, pid):
        """Sélectionne réellement la ligne (comme un clic physique sur sa
        ligne, JAMAIS en cochant sa case — voir le correctif du
        2026-10-03 : avant le démarrage, _reinstate_selected lit
        directement players_tree.selection(), jamais checked_player_ids,
        précisément pour ne pas confondre "sélectionné" et "déjà
        accueilli (coché)")."""
        self.win.players_tree.selection_set(str(pid))
        self.win._reinstate_selected()

    # -- 1. Parité du résultat (avant démarrage) -------------------------

    def test_les_deux_mecanismes_confirment_larrivee_avant_demarrage(self):
        self._click_checkbox(self.pid_checkbox)
        self._click_inscrire(self.pid_inscrire)

        via_case = self.db.get_player(self.pid_checkbox)
        via_inscrire = self.db.get_player(self.pid_inscrire)
        self.assertIsNotNone(via_case["arrived_at"])
        self.assertIsNotNone(via_inscrire["arrived_at"])
        self.assertEqual(via_case["status"], "active")
        self.assertEqual(via_inscrire["status"], "active")

    def test_meme_libelle_inscrit_affiche_dans_la_colonne_statut(self):
        """Modification du 2026-10-03 : "Inscrit", pas "Actif", avant le
        démarrage — pour les deux mécanismes, identiquement."""
        self._click_checkbox(self.pid_checkbox)
        self._click_inscrire(self.pid_inscrire)

        self.assertEqual(
            self.win.players_tree.set(str(self.pid_checkbox), "status"),
            self.win.players_tree.set(str(self.pid_inscrire), "status"),
        )
        self.assertEqual(self.win.players_tree.set(str(self.pid_inscrire), "status"), "Inscrit")

    def test_meme_etat_de_case_cochee_apres_les_deux_mecanismes(self):
        self._click_checkbox(self.pid_checkbox)
        self._click_inscrire(self.pid_inscrire)
        # "Inscrire" passe par _refresh_all (-> _refresh_players_tab), qui
        # reconstruit checked_player_ids depuis arrived_at tant que la
        # case pilote l'accueil (voir _refresh_players_tab, main.py) : un
        # second rafraîchissement explicite ici confirme que l'état de la
        # case cochée via la case elle-même survit bien à ce même
        # rafraîchissement complet, exactement comme celui déclenché par
        # "Inscrire".
        self.win._refresh_players_tab()

        self.assertEqual(
            self.win.players_tree.set(str(self.pid_checkbox), "sel"),
            self.win.players_tree.set(str(self.pid_inscrire), "sel"),
        )
        self.assertEqual(
            self.win.players_tree.set(str(self.pid_inscrire), "sel"),
            main.App.CHECKBOX_CHECKED,
        )

    def test_meme_increment_du_compteur_accueilli(self):
        self.win._refresh_players_tab()
        self.assertIn("0 accueilli", self.win.stats_lbl.cget("text"))

        self._click_checkbox(self.pid_checkbox)
        self.assertIn("1 accueilli", self.win.stats_lbl.cget("text"))

        self._click_inscrire(self.pid_inscrire)
        self.assertIn("2 accueilli", self.win.stats_lbl.cget("text"))

    # -- 2. Places figées pour chacun des deux mécanismes -----------------

    def test_case_a_cocher_ne_deplace_personne_et_naucun_rebalance(self):
        before = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in (self.pid_checkbox, self.pid_inscrire, self.pid_bystander)
        }

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            self._click_checkbox(self.pid_checkbox)
            spy.assert_not_called()

        after = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in (self.pid_checkbox, self.pid_inscrire, self.pid_bystander)
        }
        self.assertEqual(after, before)

    def test_inscrire_ne_deplace_personne_et_naucun_rebalance(self):
        before = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in (self.pid_checkbox, self.pid_inscrire, self.pid_bystander)
        }

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            self._click_inscrire(self.pid_inscrire)
            spy.assert_not_called()

        after = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in (self.pid_checkbox, self.pid_inscrire, self.pid_bystander)
        }
        self.assertEqual(after, before)

    # -- 3. Non-régression : fonctionne aussi après démarrage -------------

    def test_inscrire_fonctionne_toujours_apres_demarrage(self):
        _start_tournament(self.db)

        self._click_inscrire(self.pid_inscrire)

        self.assertIsNotNone(self.db.get_player(self.pid_inscrire)["arrived_at"])

    def test_accueil_desactive_aucun_effet_ni_pour_la_case_ni_pour_inscrire(self):
        self.db.set_settings({"accueil_management_enabled": 0})

        self._click_checkbox(self.pid_checkbox)
        self._click_inscrire(self.pid_inscrire)

        self.assertIsNone(self.db.get_player(self.pid_checkbox)["arrived_at"])
        self.assertIsNone(self.db.get_player(self.pid_inscrire)["arrived_at"])


if __name__ == "__main__":
    unittest.main()
