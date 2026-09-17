# -*- coding: utf-8 -*-
"""Tests du tri par en-tête de la colonne des cases à cocher de l'onglet
Joueurs (demande du 2026-09-14) — réservé au Mode Test.

Comportement attendu :
- 1er clic sur l'en-tête "sel" : joueurs COCHÉS en haut, triés par nom
  (insensible à la casse) ; joueurs décochés en dessous, triés par nom ;
- 2e clic : l'inverse (décochés en haut, cochés en dessous), toujours
  triés par nom à l'intérieur de chaque groupe ;
- clics suivants : alternance entre ces deux états ;
- AUCUN changement de self.checked_player_ids (uniquement l'ordre
  d'affichage — tri en mémoire, rien écrit en base pour ce tri, comme
  les tris existants Nom/Club/... de ce même onglet) ;
- indisponible hors Mode Test (le clic ne fait rien).

N'instancie PAS App(tk.Tk) au complet : seuls les widgets/attributs lus
par App._refresh_players_tab sont greffés sur une racine Tk réelle
partagée pour toute la classe (même précaution anti-flakiness Tcl/Tk que
tests/test_tables_tab_player_counts.py)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
import player_photos  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


PLAYERS_COLUMNS = (
    "sel", "id", "name", "club", "table", "seat", "chips", "buyin", "rebuy",
    "addon", "bounty", "status", "rang", "elim_time", "elim_round", "eliminated_by",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayersCheckboxHeaderSortTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (voir tests/test_tables_tab_
    player_counts.py pour la même précaution)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        # Jamais le vrai répertoire/index de photos de ce Mac (voir la
        # convention déjà établie ailleurs dans cette suite) : ce test ne
        # doit dépendre d'aucune donnée réelle de la machine qui l'exécute.
        roster_patcher = patch.object(roster, "list_clubs", return_value=[])
        self.addCleanup(roster_patcher.stop)
        roster_patcher.start()
        photo_patcher = patch.object(player_photos, "get_photo_path", return_value=None)
        self.addCleanup(photo_patcher.stop)
        photo_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="players_checkbox_sort_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        # 4 joueurs, volontairement dans un ordre non alphabétique et
        # avec une casse mélangée (pour vérifier le tri insensible à la
        # casse) : bob (coché), Alice (coché), Zoé (décoché), charlie
        # (décoché).
        self.id_bob = self.db.add_player("bob")
        self.id_alice = self.db.add_player("Alice")
        self.id_zoe = self.db.add_player("Zoé")
        self.id_charlie = self.db.add_player("charlie")

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = {self.id_bob, self.id_alice}
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        # CHECKBOX_CHECKED/UNCHECKED sont des attributs de CLASSE de App
        # (main.py) — self.win est un tk.Tk brut, pas une App, donc pas
        # d'héritage automatique : reportés ici à l'identique.
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in (
            "_refresh_players_tab", "_sort_players_by",
            "_on_players_checkbox_header_click", "_update_sort_headings",
            "_update_tournament_started_buttons", "_pending_old_seat_by_name",
            "_test_mode_enabled", "_update_checked_count_label",
            "_update_undo_elimination_button_state",
        ):
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
        # "Annule Eliminer" (demande du 2026-09-17) : _refresh_players_tab
        # appelle désormais aussi _update_undo_elimination_button_state,
        # qui a besoin de ce bouton — même principe que delete_player_btn/
        # eliminate_player_btn ci-dessus.
        self.win.undo_elimination_btn = ttk.Button(parent)
        self.win.player_photo_images = {}

    def _names_in_order(self):
        return [self.win.players_tree.set(iid, "name") for iid in self.win.players_tree.get_children()]

    def _checked_ids_snapshot(self):
        return set(self.win.checked_player_ids)

    # -- Hors Mode Test : le clic ne fait rien ------------------------

    def test_hors_mode_test_le_clic_ne_fait_rien(self):
        self.win.test_mode_var.set(False)
        before = self._checked_ids_snapshot()
        self.win._refresh_players_tab()
        order_before = self._names_in_order()

        self.win._on_players_checkbox_header_click()

        self.assertIsNone(self.win.players_sort["column"])
        self.assertEqual(self._names_in_order(), order_before)
        self.assertEqual(self._checked_ids_snapshot(), before)

    # -- Mode Test : 1er clic -> cochés d'abord -----------------------

    def test_premier_clic_coches_en_haut_tries_par_nom(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()

        self.win._on_players_checkbox_header_click()

        # Cochés (Alice, bob) triés par nom insensible à la casse, puis
        # décochés (charlie, Zoé) triés par nom insensible à la casse.
        self.assertEqual(self._names_in_order(), ["Alice", "bob", "charlie", "Zoé"])
        self.assertEqual(self.win.players_sort["column"], "sel")
        self.assertTrue(self.win.players_sort["ascending"])

    # -- 2e clic -> décochés d'abord -----------------------------------

    def test_deuxieme_clic_decoches_en_haut_tries_par_nom(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()
        self.win._on_players_checkbox_header_click()  # 1er clic

        self.win._on_players_checkbox_header_click()  # 2e clic

        self.assertEqual(self._names_in_order(), ["charlie", "Zoé", "Alice", "bob"])
        self.assertFalse(self.win.players_sort["ascending"])

    # -- Clics suivants : alternance -----------------------------------

    def test_clics_suivants_alternent(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()

        self.win._on_players_checkbox_header_click()  # 1
        self.assertEqual(self._names_in_order(), ["Alice", "bob", "charlie", "Zoé"])
        self.win._on_players_checkbox_header_click()  # 2
        self.assertEqual(self._names_in_order(), ["charlie", "Zoé", "Alice", "bob"])
        self.win._on_players_checkbox_header_click()  # 3 (retour à l'état du 1er clic)
        self.assertEqual(self._names_in_order(), ["Alice", "bob", "charlie", "Zoé"])
        self.win._on_players_checkbox_header_click()  # 4 (retour à l'état du 2e clic)
        self.assertEqual(self._names_in_order(), ["charlie", "Zoé", "Alice", "bob"])

    # -- Aucun changement de l'état coché/décoché ----------------------

    def test_aucun_changement_des_ids_coches(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()
        before = self._checked_ids_snapshot()

        self.win._on_players_checkbox_header_click()
        self.assertEqual(self._checked_ids_snapshot(), before)

        self.win._on_players_checkbox_header_click()
        self.assertEqual(self._checked_ids_snapshot(), before)

        # Les marques ☑/☐ affichées suivent toujours fidèlement
        # checked_player_ids, quel que soit l'ordre des lignes.
        for iid in self.win.players_tree.get_children():
            pid = int(iid)
            mark = self.win.players_tree.set(iid, "sel")
            expected = main.App.CHECKBOX_CHECKED if pid in before else main.App.CHECKBOX_UNCHECKED
            self.assertEqual(mark, expected)

    # -- Rien n'est écrit en base pour ce tri --------------------------

    def test_rien_ecrit_en_base_pour_ce_tri(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()
        before_rows = [dict(p) for p in self.db.list_players()]

        self.win._on_players_checkbox_header_click()

        after_rows = {p["id"]: p for p in self.db.list_players()}
        for row in before_rows:
            self.assertEqual(dict(after_rows[row["id"]]), row)

    # -- En-tête : glyphe ☑/☐ reflète l'état actif ----------------------

    def test_entete_affiche_le_bon_glyphe(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()

        self.win._on_players_checkbox_header_click()
        self.assertEqual(
            str(self.win.players_tree.heading("sel", "text")), main.App.CHECKBOX_CHECKED,
        )
        self.win._on_players_checkbox_header_click()
        self.assertEqual(
            str(self.win.players_tree.heading("sel", "text")), main.App.CHECKBOX_UNCHECKED,
        )

    def test_entete_vide_hors_tri_sel(self):
        self.win.test_mode_var.set(True)
        self.win._refresh_players_tab()
        self.win._on_players_checkbox_header_click()
        # Passe ensuite à un tri par nom classique : l'en-tête "sel"
        # redevient vide (comme avant toute utilisation de ce tri).
        self.win._sort_players_by("name")
        self.assertEqual(str(self.win.players_tree.heading("sel", "text")), "")


if __name__ == "__main__":
    unittest.main()
