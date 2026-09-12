# -*- coding: utf-8 -*-
"""Correctif du 2026-09-11 : le widget "Système de points distribués"
(Combobox + texte court) n'était jamais réellement grisé/réactivé par
"Calculer les primes", parce que _build_settings_tab ajoutait
`ranking_row` (un ttk.Frame, sans option "state") à self._primes_
section_widgets au lieu des widgets interactifs eux-mêmes — configure(
state=...) échouait silencieusement (except tk.TclError: pass) sur
toute la ligne. En conséquence, le Combobox restait figé sur son style
de construction "readonly", visuellement indiscernable de "disabled"
sous le thème Aqua, même case cochée.

(Le bouton d'aide "ⓘ" évoqué dans cette même section a été retiré
définitivement le 2026-09-12 — voir tests/test_ranking_formula_widget.py
— les tests le concernant ont été retirés en même temps ; ce fichier ne
couvre plus que le Combobox et le texte court, toujours d'actualité.)

Ce fichier vérifie :
1. primes activées, session non verrouillée : Combobox="readonly",
   texte court à l'état "normal" ;
2. primes désactivées : Combobox="disabled", texte court "disabled" ;
3. session verrouillée (même si "Calculer les primes" reste coché) :
   Combobox="disabled" ;
4. réactivation après désactivation : le Combobox revient bien à
   "readonly", JAMAIS "normal" (saisie libre interdite) ;
5. non-régression : les AUTRES champs de la section (Entry générique)
   continuent d'utiliser la logique générique "normal"/"disabled"
   inchangée.

Réutilise le style de doublure déjà établi dans tests/test_primes_
enabled_toggle.py (UpdatePrimesSectionStateTest) pour les widgets
génériques, et le harnais réel (racine Tk partagée) déjà établi dans
tests/test_ranking_formula_widget.py pour le widget ranking_formula
lui-même — nécessaire ici car isinstance(widget, ttk.Combobox) exige un
VRAI Combobox, pas une doublure générique."""
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
class PrimesSectionRankingComboboxStateTest(unittest.TestCase):
    """Un seul tk.Tk() par CLASSE de test (voir tests/test_ranking_
    formula_widget.py pour la même précaution anti-instabilité Tcl/Tk)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_ranking_combo_state_test_")
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
            ranking_lbl, ranking_row, ranking_combo,
            ranking_short_lbl, ranking_legacy_note,
        ) = self.win._build_ranking_formula_widget(parent, 0)
        self.ranking_combo = ranking_combo
        self.ranking_short_lbl = ranking_short_lbl

        # Un champ "générique" de la section (Entry), pour vérifier la
        # non-régression de la logique existante côte à côte — même
        # esprit que "bounty_entry"/"attendance_entry" dans le vrai
        # _build_settings_tab.
        self.generic_entry = ttk.Entry(parent)

        # Reproduit EXACTEMENT ce que fait maintenant _build_settings_tab
        # (voir le correctif) : les widgets INTERACTIFS eux-mêmes, jamais
        # ranking_row.
        self.win._primes_section_widgets = [
            ranking_lbl, ranking_combo, ranking_short_lbl,
            self.generic_entry,
        ]
        if ranking_legacy_note is not None:
            self.win._primes_section_widgets.append(ranking_legacy_note)

    def _update(self, enabled, locked):
        main.App._update_primes_section_state(self.win, enabled, locked=locked)

    def test_primes_activees_combobox_readonly_et_texte_actif(self):
        self._update(enabled=True, locked=False)
        self.assertEqual(str(self.ranking_combo.cget("state")), "readonly")
        self.assertEqual(str(self.ranking_short_lbl.cget("state")), "normal")

    def test_primes_desactivees_combobox_et_texte_disabled(self):
        self._update(enabled=False, locked=False)
        self.assertEqual(str(self.ranking_combo.cget("state")), "disabled")
        self.assertEqual(str(self.ranking_short_lbl.cget("state")), "disabled")

    def test_session_verrouillee_combobox_disabled_meme_si_coche(self):
        # "Calculer les primes" reste coché (enabled=True) MAIS la
        # session est verrouillée : tout doit rester grisé (point 2 de
        # la demande du 2026-09-09, déjà valable pour les autres
        # champs — vérifié ici pour le nouveau Combobox spécifiquement).
        self._update(enabled=True, locked=True)
        self.assertEqual(str(self.ranking_combo.cget("state")), "disabled")

    def test_reactivation_apres_desactivation_revient_a_readonly_jamais_normal(self):
        self._update(enabled=False, locked=False)
        self.assertEqual(str(self.ranking_combo.cget("state")), "disabled")
        self._update(enabled=True, locked=False)
        self.assertEqual(str(self.ranking_combo.cget("state")), "readonly")
        self.assertNotEqual(str(self.ranking_combo.cget("state")), "normal")

    def test_non_regression_champ_generique_suit_toujours_normal_disabled(self):
        self._update(enabled=True, locked=False)
        self.assertEqual(str(self.generic_entry.cget("state")), "normal")
        self._update(enabled=False, locked=False)
        self.assertEqual(str(self.generic_entry.cget("state")), "disabled")
        self._update(enabled=True, locked=True)
        self.assertEqual(str(self.generic_entry.cget("state")), "disabled")


if __name__ == "__main__":
    unittest.main()
