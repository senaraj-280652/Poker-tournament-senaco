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
widget) : les explications détaillées des 4 formules vivent dans le
manuel utilisateur, ET, depuis le 2026-09-14, dans un Tooltip ordinaire
sur `ranking_lbl` (voir RankingLabelTooltipTest ci-dessous) — jamais un
nouveau bouton/popup séparé, jamais de retour au bouton "ⓘ" retiré. Les
tests qui portaient uniquement sur ce bouton/son popup ont été retirés
en même temps.

Ne modifie pas la logique de calcul (RANKING_FORMULA_* eux-mêmes,
ranking_points/get_ranking_bonuses) — non touchée par cette demande,
donc non retestée ici (déjà couverte par tests/test_ranking_formula.py)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

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
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : _build_ranking_formula_widget crée un cycle
        # StringVar/trace_add + Tooltip/bind sur cls.root, réclamé ici.
        cleanup_tk(cls, "root")

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

    def test_selection_tournois_cpc_texte_court_correct(self):
        self._select("Tournois CPC")
        self.assertEqual(self._short_text(), "Barème total N × 1000 pts (plus grands restes)")
        self.assertEqual(self.win.settings_vars["ranking_formula"].get(), "tournois_cpc")

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
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : _build_ranking_formula_widget crée un cycle
        # StringVar/trace_add + Tooltip/bind sur cls.root, réclamé ici.
        cleanup_tk(cls, "root")

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


# =======================================================================
# Tooltip sur le libellé "Système de points distribués" (demande du
# 2026-09-14, étendu le 2026-09-20 pour "Tournois CPC") — contenu
# détaillé des 5 formules au survol, JAMAIS un nouveau bouton/popup (le
# bouton "ⓘ" reste définitivement retiré).
# =======================================================================
_EXPECTED_TOOLTIP_LINES = [
    "Aucun — aucun point n'est attribué en fonction du",
    "classement final.",
    "Classique — formule 100 × √N / P",
    "(N = nombre de joueurs du tournoi, P = place finale du",
    "joueur). Favorise davantage les premières places.",
    "Progressive — formule 100 × √N / √P",
    "joueur). Réduit l'écart entre les premières places et",
    "récompense davantage la régularité.",
    "Tournois CPC — formule P(r,N) = 50 + 950×N ×",
    "0,12×0,88^(r-1) / (1 − 0,88^N) (N = nombre de joueurs",
    "du tournoi, r = place finale du joueur). Chaque joueur",
    "apporte 1000 points au total distribué ; répartition",
    "décroissante, appliquée identiquement quel que soit N.",
    "Arrondi par la méthode des plus grands restes (jamais",
    "indépendant par place) pour que la somme distribuée",
    "reste toujours exactement N × 1000.",
    "Sit & Go CPC — formule 1000 + 100(N+1) − 200×P",
    "(N = nombre de joueurs du Sit & Go, P = place finale du",
    "joueur). Chaque joueur apporte 1000 points au total",
    "distribué ; l'écart entre deux places successives est de",
    "200 points.",
]


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RankingLabelTooltipTest(unittest.TestCase):
    """Un seul tk.Tk() par CLASSE de test (même précaution que les
    classes ci-dessus)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : _build_ranking_formula_widget crée un cycle
        # StringVar/trace_add + Tooltip/bind sur cls.root, réclamé ici.
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_label_tooltip_test_")
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

        # Capture CHAQUE Tooltip réellement construit (classe réelle,
        # jamais une doublure) pendant _build_ranking_formula_widget,
        # pour retrouver ensuite celui posé sur ranking_lbl précisément
        # — sans dépendre du délai réel de Tooltip.__init__ (500 ms) ni
        # d'une boucle d'événements Tk (voir _show ci-dessous, appelée
        # directement).
        self._created_tooltips = []
        real_tooltip_cls = main.Tooltip

        def _capturing_tooltip(*args, **kwargs):
            tt = real_tooltip_cls(*args, **kwargs)
            self._created_tooltips.append(tt)
            return tt

        patcher = patch.object(main, "Tooltip", side_effect=_capturing_tooltip)
        patcher.start()
        self.addCleanup(patcher.stop)

        (
            self.ranking_lbl, self.ranking_row, self.ranking_combo,
            self.ranking_short_lbl, self.ranking_legacy_note,
        ) = self.win._build_ranking_formula_widget(parent, 0)

        label_tooltips = [tt for tt in self._created_tooltips if tt.widget is self.ranking_lbl]
        self.assertEqual(len(label_tooltips), 1, "un seul Tooltip attendu sur ranking_lbl")
        self.tooltip = label_tooltips[0]
        self.addCleanup(self.tooltip._hide)
        # cleanup_tk (voir tests/_tk_cleanup.py) : self.tooltip forme un
        # cycle avec ranking_lbl (Tooltip.widget <-> bind() sur ce même
        # widget) INDÉPENDANT de cls.root — self.tooltip/_created_tooltips
        # sont des attributs de CE TestCase (self), pas de cls.root,
        # jamais nettoyés par tearDownClass. Enregistré ICI (donc avant
        # self.addCleanup(self.tooltip._hide) juste au-dessus dans l'ordre
        # d'exécution LIFO : _hide tourne avant ce nettoyage complet).
        # "win" est VOLONTAIREMENT absent de cette liste : self.win est
        # ICI le même objet que cls.root (racine PARTAGÉE par toute la
        # classe, voir setUp) — le détruire à la fin de CE test casserait
        # les tests suivants de la même classe. Seuls les widgets/objets
        # RECRÉÉS À CHAQUE test (ranking_lbl et consorts, tooltip) sont
        # listés ; cls.root n'est nettoyée qu'une fois, dans
        # tearDownClass.
        self.addCleanup(lambda: cleanup_tk(
            self, "tooltip", "_created_tooltips",
            "ranking_lbl", "ranking_row", "ranking_combo",
            "ranking_short_lbl", "ranking_legacy_note",
        ))

    def test_un_seul_tooltip_pose_uniquement_sur_le_libelle(self):
        # Contrainte explicite : tooltip UNIQUEMENT sur le libellé, pas
        # sur le Combobox ni le texte court (qui ont chacun leur propre
        # rôle, jamais celui-ci).
        self.assertNotIn(self.ranking_combo, [tt.widget for tt in self._created_tooltips])
        self.assertNotIn(self.ranking_short_lbl, [tt.widget for tt in self._created_tooltips])

    def test_contenu_exact_du_tooltip(self):
        for line in _EXPECTED_TOOLTIP_LINES:
            self.assertIn(line, self.tooltip.text)

    def test_affichage_reel_du_tooltip_contient_le_texte_attendu(self):
        """Appelle directement Tooltip._show() (le vrai code exécuté une
        fois le délai de survol écoulé, voir Tooltip._schedule) plutôt
        que d'attendre 500 ms ou de piloter une vraie boucle d'événements
        Tk — vérifie que le Label réellement affiché porte le texte
        exact, pas seulement l'attribut `.text` stocké."""
        self.tooltip._show()
        self.addCleanup(self.tooltip._hide)
        self.assertIsNotNone(self.tooltip._tip)
        shown_label = self.tooltip._tip.winfo_children()[0]
        self.assertEqual(shown_label.cget("text"), self.tooltip.text)
        for line in _EXPECTED_TOOLTIP_LINES:
            self.assertIn(line, shown_label.cget("text"))

    def test_tooltip_fonctionne_meme_libelle_grise(self):
        """Contrainte explicite de la demande : le tooltip doit rester
        disponible même si la section Primes est désactivée (ranking_lbl
        fait partie de self._primes_section_widgets, grisé par
        _update_primes_section_state — voir sa docstring). L'état ttk
        "disabled" bloque l'interaction (clic, saisie), pas les
        événements <Enter>/<Leave> sur lesquels Tooltip.__init__ se
        contente de bind() : la liaison doit donc rester active, et
        _show() doit continuer à fonctionner, une fois le libellé
        grisé."""
        self.ranking_lbl.configure(state="disabled")
        self.assertEqual(str(self.ranking_lbl.cget("state")), "disabled")
        # La liaison posée par Tooltip.__init__ (bind "<Enter>") n'est
        # pas retirée par un changement d'état ttk — vérifié directement
        # plutôt que supposé.
        self.assertTrue(self.ranking_lbl.bind("<Enter>"))

        self.tooltip._show()
        self.addCleanup(self.tooltip._hide)
        self.assertIsNotNone(self.tooltip._tip)
        shown_label = self.tooltip._tip.winfo_children()[0]
        self.assertIn(_EXPECTED_TOOLTIP_LINES[0], shown_label.cget("text"))

    def test_aucun_bouton_ou_libelle_i_reintroduit(self):
        """Non-régression explicite : ce Tooltip ne doit s'accompagner
        d'aucun nouveau widget cliquable (bouton "ⓘ" ou équivalent) —
        seul ranking_lbl (déjà existant) porte l'explication, au survol."""
        children_texts = []
        for w in self.ranking_row.winfo_children():
            try:
                children_texts.append(str(w.cget("text")))
            except tk.TclError:
                pass
        self.assertNotIn("ⓘ", children_texts)


if __name__ == "__main__":
    unittest.main()
