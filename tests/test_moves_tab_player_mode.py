# -*- coding: utf-8 -*-
""""Mode joueurs" de l'onglet Mouvements (demande du 2026-10-05) :
lisibilité à distance quand "📋 Afficher Mouvements" est déclenché depuis
le téléphone — remplace le chantier précédent (abandonné, voir la
conversation) qui avait agrandi À TORT le tableau de mouvements du Chrono
Projo (clock_window.py, strictement remis dans son état d'origine) au
lieu du VRAI tableau visé par les joueurs : celui de l'onglet "Mouvements"
de la fenêtre principale (main.py: moves_tree), sélectionné par
App._on_voice_word("mouvements") (voir remote_control.py: showMoves()).

Le "mode joueurs" (App._moves_tab_player_mode, voir son commentaire dans
App.__init__) :
- est activé UNIQUEMENT par App._on_voice_word("mouvements") (jamais par
  un clic manuel de l'organisateur sur l'onglet, qui passe par
  <<NotebookTabChanged>>/_on_notebook_tab_changed sans jamais toucher ce
  drapeau) ;
- bascule moves_tree sur le style ttk dédié "MovesBig.Treeview" (défini
  dans App._apply_theme : Helvetica 24 bold, rowheight=48 — séparé du
  style "Treeview" par défaut, donc sans impact sur aucun autre tableau
  de Senaco) et masque les colonnes Heure/Raison via `displaycolumns`
  (JAMAIS en supprimant les données réelles) ;
- revient à la normale (style "Treeview", 7 colonnes) dans
  App._finish_movement_alert (bouton "Terminé"), exactement comme le
  reste du cycle de mouvements qu'il clôt déjà."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


class OnVoiceWordMouvementsPlayerModeTest(unittest.TestCase):
    """Même style de doublure que tests/test_niveau_precedent_remote.py :
    App._on_voice_word (fonction non liée) appelée directement, sans
    construire une vraie App — aucune fenêtre Tk nécessaire ici, seul le
    dispatch lui-même est en jeu."""

    def _make_fake(self):
        calls = []

        class _FakeDb:
            def get_setting_int(self, key, default=0):
                return 0

        class _FakeTab:
            pass

        class _FakeMovesTree:
            def yview_moveto(self, frac):
                calls.append(("yview_moveto", frac))

        class _Fake:
            db = _FakeDb()
            moves_tab = _FakeTab()
            tables_tab = _FakeTab()
            moves_tree = _FakeMovesTree()
            _moves_tab_player_mode = False
            _moves_player_scroll_paused = True

            class notebook:
                @staticmethod
                def select(tab):
                    calls.append(("select", tab))

            def _refresh_moves_tab(self):
                calls.append("refresh")

            def _refresh_tables_tab(self):
                calls.append("refresh_tables")

            def lift(self):
                calls.append("lift")

            def focus_force(self):
                calls.append("focus_force")

        return _Fake(), calls

    def test_commande_telephone_active_le_mode_joueurs(self):
        fake, calls = self._make_fake()
        main.App._on_voice_word(fake, "mouvements")
        self.assertTrue(fake._moves_tab_player_mode)

    def test_commande_telephone_conserve_le_comportement_existant(self):
        """Non-régression : la sélection de l'onglet, le rafraîchissement,
        lift() et focus_force() doivent continuer de se produire exactement
        comme avant l'ajout du mode joueurs (le nouveau retour en haut du
        défilement, voir le chantier suivant, s'insère entre le
        rafraîchissement et lift())."""
        fake, calls = self._make_fake()
        main.App._on_voice_word(fake, "mouvements")
        self.assertEqual(
            calls,
            [("select", fake.moves_tab), "refresh", ("yview_moveto", 0.0), "lift", "focus_force"],
        )

    def test_commande_telephone_reinitialise_la_pause_du_defilement(self):
        fake, calls = self._make_fake()
        main.App._on_voice_word(fake, "mouvements")
        self.assertFalse(fake._moves_player_scroll_paused)

    def test_autre_mot_cle_ne_touche_pas_au_mode_joueurs(self):
        """Non-régression : un mot-clé sans rapport (ex. "tables") ne doit
        jamais activer le mode joueurs de l'onglet Mouvements."""
        fake, calls = self._make_fake()
        main.App._on_voice_word(fake, "tables")
        self.assertFalse(fake._moves_tab_player_mode)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovesTabDisplayModeTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (voir la docstring de
    tests/test_tables_tab_movement_pending_banner.py, même principe)."""

    ALL_COLUMNS = ("time", "player", "old_table", "old_seat", "new_table", "new_seat", "reason")
    PLAYER_COLUMNS = ("player", "old_table", "old_seat", "new_table", "new_seat")

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        main.App._apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="moves_tab_player_mode_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win._moves_tab_player_mode = False
        self.win._moves_tab_display_mode_applied = None
        self.win._moves_tab_normal_col_widths = None
        self.win._moves_tab_show_previous_moves = False

        self.win.moves_tab = ttk.Frame(self.root)
        self.addCleanup(self.win.moves_tab.destroy)
        self.win._finish_movement_alert = lambda: None
        self.win._print_movement_slips_filled = lambda: None
        self.win._print_movement_slips = lambda: None

        # Notebook + after()/after_cancel() doublés : _build_moves_tab
        # démarre désormais aussi la boucle de défilement du mode joueurs
        # (voir tests/test_moves_tab_player_columns_and_scroll.py, chantier
        # suivant) — jamais de vrai timer Tk ni de vrai notebook ici, ce
        # fichier ne teste pas cette boucle elle-même.
        from unittest.mock import MagicMock
        self.win.notebook = MagicMock()
        self.win.notebook.select.return_value = "tabid"
        self.win.notebook.tab.return_value = "Mouvements"
        self.win.after = lambda ms, fn: "after-id"
        self.win.after_cancel = MagicMock()

        for const in (
            "MOVES_TAB_ALL_COLUMNS", "MOVES_TAB_PLAYER_COLUMNS",
            "MOVES_TAB_PLAYER_DEFAULT_WIDTHS", "MOVES_PLAYER_AUTOSCROLL_INTERVAL_MS",
            "MOVES_PLAYER_AUTOSCROLL_PAUSE_MS",
        ):
            setattr(self.win, const, getattr(main.App, const))

        for name in (
            "_build_moves_tab", "_apply_moves_tab_display_mode", "_refresh_moves_tab",
            "_on_moves_tree_button_release", "_save_moves_player_column_widths_if_changed",
            "_moves_player_autoscroll_tick", "_moves_player_reset_to_top",
            "_moves_player_resume_autoscroll", "_toggle_previous_moves_display",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        self.win._build_moves_tab()

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat):
        self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-10-05 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()

    def test_mode_normal_sept_colonnes_style_habituel(self):
        self.win._moves_tab_player_mode = False
        self.win._apply_moves_tab_display_mode()
        self.assertEqual(self.win.moves_tree.cget("style"), "Treeview")
        self.assertEqual(tuple(self.win.moves_tree["displaycolumns"]), self.ALL_COLUMNS)

    def test_mode_joueurs_cinq_colonnes_style_dedie(self):
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.assertEqual(self.win.moves_tree.cget("style"), "MovesBig.Treeview")
        self.assertEqual(tuple(self.win.moves_tree["displaycolumns"]), self.PLAYER_COLUMNS)

    def test_mode_joueurs_masque_heure_et_raison_sans_les_supprimer(self):
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        displayed = tuple(self.win.moves_tree["displaycolumns"])
        self.assertNotIn("time", displayed)
        self.assertNotIn("reason", displayed)
        # "Heure"/"Raison" restent des colonnes CONFIGURÉES (jamais
        # supprimées) : seul leur affichage est masqué.
        self.assertIn("time", self.win.moves_tree["columns"])
        self.assertIn("reason", self.win.moves_tree["columns"])

    def test_police_24_bold_et_rowheight_48(self):
        style = ttk.Style(self.win)
        font = style.lookup("MovesBig.Treeview", "font")
        self.assertIn("24", str(font))
        self.assertIn("bold", str(font).lower())
        self.assertEqual(int(style.lookup("MovesBig.Treeview", "rowheight")), 48)

    def test_en_tetes_du_mode_joueurs_egalement_agrandis(self):
        style = ttk.Style(self.win)
        heading_font = style.lookup("MovesBig.Treeview.Heading", "font")
        self.assertIn("24", str(heading_font))

    def test_style_treeview_global_inchange(self):
        """Non-régression : le style ttk "Treeview" par défaut (utilisé
        par TOUS les autres tableaux de Senaco : Joueurs, Paiements,
        Tables...) garde sa police/hauteur de ligne d'origine, quel que
        soit l'état du mode joueurs — "MovesBig.Treeview" est un style
        totalement séparé."""
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        style = ttk.Style(self.win)
        self.assertEqual(int(style.lookup("Treeview", "rowheight")), 27)

    def test_autre_treeview_non_affecte_par_le_mode_joueurs(self):
        """Un deuxième Treeview quelconque (style par défaut, comme
        payouts_tree/tables_tree/etc.) ne doit subir aucun changement
        visuel lorsque moves_tree bascule en mode joueurs."""
        other = ttk.Treeview(self.win.moves_tab, columns=("a",), show="headings")
        self.addCleanup(other.destroy)
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.assertEqual(other.cget("style"), "")  # jamais touché -> style par défaut

    def test_donnees_conservees_malgre_le_masquage_en_mode_joueurs(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.win._moves_tab_player_mode = True
        self.win._refresh_moves_tab()
        iid = self.win.moves_tree.get_children()[0]
        values = self.win.moves_tree.item(iid, "values")
        cols = self.win.moves_tree["columns"]
        row = dict(zip(cols, values))
        # L'heure et la raison restent bien présentes dans les DONNÉES de
        # la ligne (jamais perdues), même si displaycolumns ne les montre
        # pas à l'écran.
        self.assertTrue(row["time"])
        self.assertTrue(row["reason"])
        self.assertEqual(row["player"], "Alice")
        self.assertEqual(row["old_table"], "Table 1")
        self.assertEqual(row["new_seat"], "5")

    def test_terminer_revient_au_mode_normal(self):
        self._insert_move("Bob", "Table 3", 1, "Table 4", 2)
        self.win._moves_tab_player_mode = True
        self.win._refresh_moves_tab()
        self.assertEqual(self.win.moves_tree.cget("style"), "MovesBig.Treeview")

        # Dépendances de _finish_movement_alert hors du périmètre testé ici
        # (chrono, fenêtre projecteur séparée, rééquilibrage différé) :
        # simples doublures, comme dans tests/test_tables_tab_movement_
        # pending_banner.py — jamais la logique métier réimplémentée.
        self.win.voice_awaiting_resume = False
        self.win.clock_window = None
        self.win._clock_resume = lambda: None
        self.win._refresh_clock_tab = lambda: None
        self.win._resume_rebalance_if_needed = lambda: None
        from unittest.mock import MagicMock
        self.win.notebook = MagicMock()
        self.win.clock_tab = MagicMock()
        self.win._finish_movement_alert = types.MethodType(
            main.App._finish_movement_alert, self.win
        )

        self.win._finish_movement_alert()

        self.assertFalse(self.win._moves_tab_player_mode)
        self.assertEqual(self.win.moves_tree.cget("style"), "Treeview")
        self.assertEqual(tuple(self.win.moves_tree["displaycolumns"]), self.ALL_COLUMNS)


if __name__ == "__main__":
    unittest.main()
