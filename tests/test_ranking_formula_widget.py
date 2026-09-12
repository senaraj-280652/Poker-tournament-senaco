# -*- coding: utf-8 -*-
"""Tests UI ciblés du bloc "Système de points distribués" (onglet
Paramètres) — App._build_ranking_formula_widget (main.py), extrait de
_build_settings_tab pour rester testable isolément sans construire tout
l'onglet (voir la docstring de la méthode).

Vérifie, pour CHAQUE sélection dans la Combobox :
1. le texte court affiché sous la liste correspond EXACTEMENT à
   RANKING_FORMULA_SHORT_TEXTS ;
2. le réglage interne réellement enregistré (self.settings_vars[
   "ranking_formula"]) correspond à la valeur interne attendue.

Le bouton d'aide "ⓘ" (popup complet au clic) a été DÉFINITIVEMENT
retiré le 2026-09-12 (invisible dans l'interface réelle une fois
positionné avec place() — voir la docstring de _build_ranking_formula_
widget) : les explications détaillées des 4 formules vivent désormais
uniquement dans le manuel utilisateur. Les tests qui portaient
uniquement sur ce bouton/son popup ont été retirés en même temps.

Ne modifie pas la logique de calcul (RANKING_FORMULA_* eux-mêmes,
ranking_points/get_ranking_bonuses) — non touchée par cette demande,
donc non retestée ici (déjà couverte par tests/test_ranking_formula.py)."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RankingFormulaWidgetTest(unittest.TestCase):
    """Un seul tk.Tk() par CLASSE de test (voir tests/test_rebalance_
    phase2_max_seats_change.py pour la même précaution anti-instabilité
    Tcl/Tk)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_widget_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win.settings_vars = {}
        self.win._build_ranking_formula_widget = types.MethodType(
            main.App._build_ranking_formula_widget, self.win
        )
        parent = ttk.Frame(self.root)
        self.addCleanup(parent.destroy)
        (
            self.ranking_lbl, self.ranking_row, self.ranking_combo,
            self.ranking_short_lbl, self.ranking_legacy_note,
        ) = self.win._build_ranking_formula_widget(parent, 0)

    def _select(self, display_label):
        """Simule une sélection utilisateur dans la Combobox — passe par
        la même StringVar que le widget réel (déclenche la même trace
        que si l'utilisateur cliquait), pas un raccourci direct sur le
        réglage interne."""
        self.win.ranking_formula_display_var.set(display_label)

    def _short_text(self):
        return self.win.ranking_formula_short_lbl.cget("text")

    def test_selection_classique_texte_court_correct(self):
        self._select("Classique")
        self.assertEqual(self._short_text(), "100 × √N / P")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "current")

    def test_selection_progressive_texte_court_correct(self):
        self._select("Progressive")
        self.assertEqual(self._short_text(), "100 × √N / √P")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "progressive")

    def test_selection_sitngo_cpc_texte_court_correct(self):
        self._select("Sit & Go CPC")
        self.assertEqual(self._short_text(), "1000 + 100(N+1) - 200P")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "sitngo_cpc")

    def test_selection_aucun_texte_court_correct(self):
        self._select("Aucun")
        self.assertEqual(self._short_text(), "Aucun point attribué selon le classement.")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "none")

    def test_texte_court_change_immediatement_a_chaque_selection(self):
        self._select("Classique")
        self.assertEqual(self._short_text(), "100 × √N / P")
        self._select("Progressive")
        self.assertEqual(self._short_text(), "100 × √N / √P")
        self._select("Aucun")
        self.assertEqual(self._short_text(), "Aucun point attribué selon le classement.")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RankingFormulaWidgetValeurFixeHistoriqueTest(unittest.TestCase):
    """Ancien tournoi à valeur fixe (ranking_bonus_points > 0, aucun
    ranking_formula) : le placeholder est affiché et le texte court
    reste vide (voir RANKING_FORMULA_SHORT_TEXTS.get(..., ""))."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_widget_legacy_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"ranking_bonus_points": 37})
        self.db.conn.execute("DELETE FROM settings WHERE key='ranking_formula'")
        self.db.conn.commit()

        self.win = self.root
        self.win.db = self.db
        self.win.settings_vars = {}
        self.win._build_ranking_formula_widget = types.MethodType(
            main.App._build_ranking_formula_widget, self.win
        )
        parent = ttk.Frame(self.root)
        self.addCleanup(parent.destroy)
        (
            self.ranking_lbl, self.ranking_row, self.ranking_combo,
            self.ranking_short_lbl, self.ranking_legacy_note,
        ) = self.win._build_ranking_formula_widget(parent, 0)

    def test_texte_court_vide_tant_quaucun_choix_explicite(self):
        self.assertEqual(self.win.ranking_formula_short_lbl.cget("text"), "")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "")

    def test_note_valeur_fixe_historique_affichee(self):
        self.assertIsNotNone(self.ranking_legacy_note)
        self.assertIn("37", self.ranking_legacy_note.cget("text"))

    def test_choisir_une_formule_met_a_jour_le_texte_court(self):
        self.win.ranking_formula_display_var.set("Classique")
        self.assertEqual(self.win.ranking_formula_short_lbl.cget("text"), "100 × √N / P")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "current")


if __name__ == "__main__":
    unittest.main()
