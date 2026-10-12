# -*- coding: utf-8 -*-
"""Bouton "Movt Précédent" de l'onglet Mouvements (demande du 2026-10-06)
— sécurité contre un "Terminé" cliqué trop tôt.

BDD (database.py) :
- nouvelle table `previous_seat_moves`, même structure utile que
  `seat_moves`, DANS le fichier .tournoi (donnée de CE tournoi, pas une
  préférence d'interface — isolation automatique entre tournois et
  persistance via le mécanisme SQLite déjà existant).
- `archive_seat_moves_as_previous()` : un seul emplacement (jamais un
  historique), ne fait RIEN si seat_moves est vide au moment de l'appel
  (choix explicitement validé : un "Terminé" sans mouvement en attente ne
  doit jamais écraser la vraie dernière série mémorisée).
- `get_previous_seat_moves()` : lecture consultative.
- AUCUNE des fonctions existantes (count_seat_moves/get_seat_moves/
  confirm_seat_move/rebalance_tables/has_pending_moves) ne référence
  cette nouvelle table — vérifié par construction (fichiers séparés).

UI (main.py) :
- bouton "Movt Précédent" (App._toggle_previous_moves_display) à côté de
  "Terminé" — interrupteur d'affichage pur.
- App._finish_movement_alert archive AVANT de vider seat_moves, puis
  masque systématiquement l'affichage.
- App._refresh_moves_tab ajoute les lignes de la dernière série terminée
  APRÈS les mouvements réellement en attente, taguées "previous_move"
  (police italique : Helvetica 11 italic en affichage normal, Helvetica
  24 bold italic en mode joueurs — _apply_moves_tab_display_mode),
  Raison forcée à l'affichage à "Non Validé" (libellé ajusté le
  2026-10-06 ; était "Mouvement précédent") SANS modifier la donnée
  archivée elle-même."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk
import tkinter.font as tkfont

import database  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


# =========================================================================
# A — Couche base de données, sans aucune fenêtre Tk
# =========================================================================
class PreviousSeatMovesDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="previous_seat_moves_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat,
                      reason="auto_balance"):
        self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-10-06 20:00:00", reason),
        )
        self.db.conn.commit()

    def test_vide_au_depart(self):
        self.assertEqual(self.db.get_previous_seat_moves(), [])

    def test_archive_puis_clear_la_rend_disponible(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        previous = self.db.get_previous_seat_moves()
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0]["player_name"], "Alice")

    def test_conserve_la_vraie_raison_dorigine_sans_la_modifier(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5, reason="manual")
        self.db.archive_seat_moves_as_previous()
        self.assertEqual(self.db.get_previous_seat_moves()[0]["reason"], "manual")

    def test_une_nouvelle_serie_remplace_lancienne_jamais_daccumulation(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()

        self._insert_move("Bob", "Table 3", 1, "Table 4", 2)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()

        previous = self.db.get_previous_seat_moves()
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0]["player_name"], "Bob")  # jamais Alice

    def test_archiver_avec_seat_moves_vide_ne_touche_a_rien(self):
        """Coeur du choix validé : "Terminé" sans mouvement en attente ne
        doit jamais effacer/remplacer la vraie dernière série."""
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()

        self.assertEqual(self.db.count_seat_moves(), 0)
        self.db.archive_seat_moves_as_previous()  # rien à archiver

        previous = self.db.get_previous_seat_moves()
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0]["player_name"], "Alice")

    def test_jamais_comptee_par_count_seat_moves(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        # archive_seat_moves_as_previous ne vide PAS seat_moves elle-même
        # (c'est clear_seat_moves, appelé séparément, qui le fait) : on
        # vérifie ici que la présence de previous_seat_moves ne modifie
        # jamais ce que count_seat_moves rapporte.
        self.assertEqual(self.db.count_seat_moves(), 1)
        self.db.clear_seat_moves()
        self.assertEqual(self.db.count_seat_moves(), 0)  # previous_seat_moves non vide, sans effet

    def test_jamais_retournee_par_get_seat_moves(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self._insert_move("Bob", "Table 3", 1, "Table 4", 2)
        names = [m["player_name"] for m in self.db.get_seat_moves()]
        self.assertEqual(names, ["Bob"])  # jamais Alice (archivée)

    def test_isolation_stricte_entre_deux_fichiers_tournoi(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()

        other_tmp = tempfile.TemporaryDirectory(prefix="previous_seat_moves_other_")
        self.addCleanup(other_tmp.cleanup)
        other_db = database.Database(os.path.join(other_tmp.name, "B.tournoi"))
        self.addCleanup(other_db.conn.close)

        self.assertEqual(other_db.get_previous_seat_moves(), [])  # jamais la série de A
        self.assertEqual(len(self.db.get_previous_seat_moves()), 1)

    def test_persiste_apres_fermeture_et_reouverture_du_fichier(self):
        """Fermer puis rouvrir le même fichier .tournoi (équivalent d'un
        redémarrage de Senaco ou d'une réouverture manuelle) doit
        retrouver exactement la même série."""
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        db_path = os.path.join(self._tmp.name, "A.tournoi")

        reopened = database.Database(db_path)
        self.addCleanup(reopened.conn.close)
        previous = reopened.get_previous_seat_moves()
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0]["player_name"], "Alice")


# =========================================================================
# B — UI : App._refresh_moves_tab / _toggle_previous_moves_display /
#     _finish_movement_alert / _apply_moves_tab_display_mode
# =========================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovesTabPreviousSeriesUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        main.App._apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="moves_tab_previous_series_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win._moves_tab_player_mode = False
        self.win._moves_tab_display_mode_applied = None
        self.win._moves_tab_normal_col_widths = None
        self.win._moves_tab_show_previous_moves = False
        self.win.voice_awaiting_resume = False
        self.win.clock_window = None

        self.win.moves_tab = ttk.Frame(self.root)
        self.addCleanup(self.win.moves_tab.destroy)
        self.win._print_movement_slips_filled = lambda: None
        self.win._print_movement_slips = lambda: None
        self.win._clock_resume = lambda: None
        self.win._refresh_clock_tab = lambda: None
        self.win._resume_rebalance_if_needed = lambda: None
        self.win.clock_tab = MagicMock()

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
            "_finish_movement_alert",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))

        self.win._build_moves_tab()

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat):
        self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-10-06 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()

    def _rows_by_tag(self, tag):
        return [
            iid for iid in self.win.moves_tree.get_children()
            if tag in self.win.moves_tree.item(iid, "tags")
        ]

    def test_masque_par_defaut_meme_si_une_serie_est_archivee(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._refresh_moves_tab()
        self.assertEqual(self._rows_by_tag("previous_move"), [])

    def test_premier_clic_affiche_en_italique_avec_raison_forcee(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._refresh_moves_tab()

        self.win._toggle_previous_moves_display()

        rows = self._rows_by_tag("previous_move")
        self.assertEqual(len(rows), 1)
        values = self.win.moves_tree.item(rows[0], "values")
        cols = self.win.moves_tree["columns"]
        row = dict(zip(cols, values))
        self.assertEqual(row["player"], "Alice")
        self.assertEqual(row["reason"], "Non Validé")

    def test_donnee_archivee_non_modifiee_par_laffichage(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._toggle_previous_moves_display()  # affiche -> force le libellé à l'écran
        # La VRAIE donnée archivée doit rester "auto_balance", jamais
        # réécrite par le simple fait de l'avoir affichée.
        self.assertEqual(self.db.get_previous_seat_moves()[0]["reason"], "auto_balance")

    def test_deuxieme_clic_masque(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._toggle_previous_moves_display()
        self.win._toggle_previous_moves_display()
        self.assertEqual(self._rows_by_tag("previous_move"), [])

    def test_police_italique_mode_normal(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._toggle_previous_moves_display()
        font = self.win.moves_tree.tag_configure("previous_move", "font")
        actual = tkfont.Font(font=font).actual()
        self.assertEqual(actual["size"], 11)
        self.assertEqual(actual["slant"], "italic")
        self.assertEqual(actual["weight"], "normal")

    def test_police_italique_mode_joueurs(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._moves_tab_player_mode = True
        self.win._toggle_previous_moves_display()
        font = self.win.moves_tree.tag_configure("previous_move", "font")
        actual = tkfont.Font(font=font).actual()
        self.assertEqual(actual["size"], 24)
        self.assertEqual(actual["slant"], "italic")
        self.assertEqual(actual["weight"], "bold")

    def test_visibles_dans_les_7_colonnes_normales(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._toggle_previous_moves_display()
        self.assertEqual(
            tuple(self.win.moves_tree["displaycolumns"]), main.App.MOVES_TAB_ALL_COLUMNS,
        )
        self.assertEqual(len(self._rows_by_tag("previous_move")), 1)

    def test_visibles_dans_les_5_colonnes_du_mode_joueurs_raison_masquee(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._moves_tab_player_mode = True
        self.win._toggle_previous_moves_display()
        self.assertEqual(
            tuple(self.win.moves_tree["displaycolumns"]), main.App.MOVES_TAB_PLAYER_COLUMNS,
        )
        self.assertNotIn("reason", self.win.moves_tree["displaycolumns"])
        self.assertEqual(len(self._rows_by_tag("previous_move")), 1)

    def test_mouvements_actifs_et_precedents_simultanement_sans_interaction(self):
        """Le test explicitement demandé : mouvements ACTIFS + affichage
        "Movt Précédent" en même temps = actifs normaux + précédents
        italiques à la suite, sans confusion ni interaction entre eux."""
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()

        # Nouveaux mouvements RÉELLEMENT en attente, pendant que la série
        # précédente (Alice) reste disponible.
        self._insert_move("Carla", "Table 5", 2, "Table 6", 4)
        self._insert_move("Dan", "Table 6", 1, "Table 5", 3)

        self.win._toggle_previous_moves_display()

        active_rows = [
            iid for iid in self.win.moves_tree.get_children()
            if "previous_move" not in self.win.moves_tree.item(iid, "tags")
        ]
        previous_rows = self._rows_by_tag("previous_move")

        self.assertEqual(len(active_rows), 2)
        self.assertEqual(len(previous_rows), 1)

        cols = self.win.moves_tree["columns"]
        active_names = sorted(
            dict(zip(cols, self.win.moves_tree.item(iid, "values")))["player"]
            for iid in active_rows
        )
        self.assertEqual(active_names, ["Carla", "Dan"])
        previous_row = dict(zip(cols, self.win.moves_tree.item(previous_rows[0], "values")))
        self.assertEqual(previous_row["player"], "Alice")
        self.assertEqual(previous_row["reason"], "Non Validé")

        # Les mouvements actifs n'ont PAS été rebaptisés "Non Validé" ni
        # rendus italiques par la présence de la série précédente à côté
        # d'eux.
        for iid in active_rows:
            row = dict(zip(cols, self.win.moves_tree.item(iid, "values")))
            self.assertNotEqual(row["reason"], "Non Validé")

        # previous_seat_moves/seat_moves restent deux choses distinctes :
        # la présence simultanée à l'écran ne les a jamais mélangées en base.
        self.assertEqual(self.db.count_seat_moves(), 2)
        self.assertEqual(len(self.db.get_previous_seat_moves()), 1)

    def test_terminer_archive_la_serie_et_masque_laffichage(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.win._finish_movement_alert()
        self.assertEqual(len(self.db.get_previous_seat_moves()), 1)
        self.assertEqual(self.db.get_previous_seat_moves()[0]["player_name"], "Alice")
        self.assertEqual(self.db.count_seat_moves(), 0)
        self.assertFalse(self.win._moves_tab_show_previous_moves)

    def test_terminer_sans_mouvement_en_attente_ne_touche_pas_a_la_serie_memorisee(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.win._finish_movement_alert()  # archive Alice

        self.win._finish_movement_alert()  # re-clic, rien en attente cette fois

        previous = self.db.get_previous_seat_moves()
        self.assertEqual(len(previous), 1)
        self.assertEqual(previous[0]["player_name"], "Alice")

    def test_terminer_masque_laffichage_meme_sil_etait_actif(self):
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._toggle_previous_moves_display()
        self.assertTrue(self.win._moves_tab_show_previous_moves)

        self._insert_move("Bob", "Table 3", 1, "Table 4", 2)
        self.win._finish_movement_alert()

        self.assertFalse(self.win._moves_tab_show_previous_moves)
        self.assertEqual(self._rows_by_tag("previous_move"), [])
        # Et la série désormais mémorisée est B, pas A.
        self.assertEqual(self.db.get_previous_seat_moves()[0]["player_name"], "Bob")

    def test_toggle_repete_pendant_refresh_periodique_reste_coherent(self):
        """Le défilement/rafraîchissement périodique (_tick) ne doit pas
        faire disparaître les lignes précédentes tant que le bouton reste
        activé."""
        self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.win._toggle_previous_moves_display()
        for _ in range(5):
            self.win._refresh_moves_tab()
        self.assertEqual(len(self._rows_by_tag("previous_move")), 1)


if __name__ == "__main__":
    unittest.main()
