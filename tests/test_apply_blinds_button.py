# -*- coding: utf-8 -*-
"""Bouton "Appliquer" de l'onglet Blindes (demande du 2026-09-24) :
applique à ce tournoi les valeurs actuellement visibles dans le tableau,
sans confirmation, en réutilisant EXACTEMENT la chaîne existante
_collect_blinds_from_widgets -> _rounds_to_flat_structure ->
Database.set_blind_structure. Ne réinitialise jamais current_level_order
à 1 (contrairement à "Structure standard") ; ne le ramène au dernier
niveau valide QUE si la nouvelle structure est devenue trop courte
(règle reprise telle quelle de App._generate_custom_blind_structure).

Deux volets, comme tests/test_ca_log_tab_wiring.py :
1. ButtonWiringStructuralTest — analyse du CODE SOURCE de
   App._build_blinds_tab (AST) : le bouton "Appliquer" existe, packé
   dans top_btns, IMMÉDIATEMENT après "Structure standard" (aucune autre
   instruction entre les deux dans le corps de la méthode) — donc sur la
   même ligne, juste à sa droite.
2. ApplyBlindsBehaviorTest et consorts — doublure minimale de App (même
   principe que tests/test_niveau_precedent_remote.py : self.db RÉEL,
   _refresh_blinds_tab/_refresh_clock_tab de simples compteurs, jamais
   une vraie fenêtre Tk construite ici) portant les méthodes réelles
   _collect_blinds_from_widgets/_rounds_to_flat_structure/
   _apply_blinds_from_tab/_reset_blind_structure_from_tab/_go_to_level."""
import ast
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402


# =====================================================================
# 1. Câblage du bouton (analyse du code source, aucun Tk nécessaire)
# =====================================================================
class ButtonWiringStructuralTest(unittest.TestCase):
    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=main_py)

    def _find_method(self, class_name, method_name):
        cls = next(n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef) and n.name == class_name)
        return next(
            n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def test_bouton_appliquer_immediatement_apres_structure_standard(self):
        func = self._find_method("App", "_build_blinds_tab")
        body = func.body
        idx_standard = idx_appliquer = None
        for i, stmt in enumerate(body):
            seg = ast.get_source_segment(self.source, stmt) or ""
            if 'text="Structure standard"' in seg:
                idx_standard = i
            if 'text="Appliquer"' in seg:
                idx_appliquer = i
        self.assertIsNotNone(idx_standard, "bouton 'Structure standard' introuvable dans _build_blinds_tab")
        self.assertIsNotNone(idx_appliquer, "bouton 'Appliquer' introuvable dans _build_blinds_tab")
        self.assertEqual(
            idx_appliquer, idx_standard + 1,
            "'Appliquer' doit être l'instruction JUSTE APRÈS 'Structure standard' "
            "(donc sur la même ligne, immédiatement à sa droite)",
        )

    def test_bouton_appliquer_packe_dans_top_btns_a_gauche(self):
        func = self._find_method("App", "_build_blinds_tab")
        for stmt in func.body:
            seg = ast.get_source_segment(self.source, stmt) or ""
            if 'text="Appliquer"' in seg:
                self.assertTrue(seg.startswith("ttk.Button(top_btns,"), seg)
                self.assertIn('side="left"', seg)
                self.assertIn("command=self._apply_blinds_from_tab", seg)
                return
        self.fail("bouton 'Appliquer' introuvable")

    def test_apply_blinds_from_tab_ne_demande_aucune_confirmation(self):
        """Décision explicite du 2026-09-24 : contrairement à 'Structure
        standard' (messagebox.askyesno), 'Appliquer' n'affiche jamais de
        boîte de confirmation."""
        func = self._find_method("App", "_apply_blinds_from_tab")
        func_source = ast.get_source_segment(self.source, func) or ""
        self.assertNotIn("askyesno", func_source)

    def test_apply_blinds_from_tab_najamais_go_to_level(self):
        """Ne remet jamais le tournoi au niveau 1 (contrairement à
        _reset_blind_structure_from_tab, qui appelle _go_to_level(1)) —
        vérifié sur les vrais APPELS (AST), jamais une recherche de texte
        brut qui trouverait aussi la mention de _go_to_level dans la
        docstring explicative (comparaison avec Structure standard)."""
        func = self._find_method("App", "_apply_blinds_from_tab")
        called = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertNotIn("_go_to_level", called)

    def test_apply_blinds_from_tab_reutilise_la_chaine_existante(self):
        func = self._find_method("App", "_apply_blinds_from_tab")
        called = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        for expected in (
            "_collect_blinds_from_widgets", "_rounds_to_flat_structure",
            "_refresh_blinds_tab", "_refresh_clock_tab",
        ):
            self.assertIn(expected, called, f"{expected} devrait être appelée par _apply_blinds_from_tab")


# =====================================================================
# 2. Comportement réel (self.db réel, pas de vraie fenêtre Tk)
# =====================================================================
class _FakeVar:
    """Doublure d'une variable Tk (StringVar/IntVar) : .get() renvoie la
    valeur telle quelle — int(valeur) se comporte exactement comme pour
    une vraie variable Tk (ValueError sur une valeur non numérique)."""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


def _row_vars(duration, sb, bb, ante, pause=0):
    return {
        "duration": _FakeVar(duration), "sb": _FakeVar(sb), "bb": _FakeVar(bb),
        "ante": _FakeVar(ante), "pause": _FakeVar(pause),
    }


def _structure_3_rounds():
    return [
        {"small_blind": 25, "big_blind": 50, "ante": 0, "duration_minutes": 15, "is_break": False},
        {"small_blind": 50, "big_blind": 100, "ante": 0, "duration_minutes": 15, "is_break": False},
        {"small_blind": 100, "big_blind": 200, "ante": 25, "duration_minutes": 20, "is_break": False},
    ]


class _FakeApp:
    """Même principe que tests/test_niveau_precedent_remote.py::_FakeApp :
    self.db RÉEL, les méthodes de rafraîchissement Tk réduites à de
    simples compteurs, jamais une vraie fenêtre construite ici — les
    méthodes métier réelles sont attachées telles quelles (pas de
    réimplémentation)."""

    _collect_blinds_from_widgets = main.App._collect_blinds_from_widgets
    _rounds_to_flat_structure = main.App._rounds_to_flat_structure
    _apply_blinds_from_tab = main.App._apply_blinds_from_tab
    _reset_blind_structure_from_tab = main.App._reset_blind_structure_from_tab
    _go_to_level = main.App._go_to_level

    def __init__(self, db):
        self.db = db
        self._blind_row_vars = []
        self.refresh_blinds_calls = 0
        self.refresh_clock_calls = 0

    def _refresh_blinds_tab(self):
        self.refresh_blinds_calls += 1

    def _refresh_clock_tab(self):
        self.refresh_clock_calls += 1


class _ApplyBlindsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="apply_blinds_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_3_rounds())
        self.app = _FakeApp(self.db)
        # hasattr(self, "blinds_tree") suffit pour ces deux méthodes
        # (jamais un vrai Treeview manipulé) — reflète l'état normal en
        # production, où l'onglet Chronomètre est déjà construit dès que
        # l'onglet Blindes est utilisable.
        self.app.blinds_tree = "dummy"


class ModificationsSimplesTest(_ApplyBlindsTestCase):
    def test_sb_bb_modifiees_puis_appliquer(self):
        self.app._blind_row_vars = [
            _row_vars(15, 30, 60, 0),  # round 1 : SB/BB modifiées (25/50 -> 30/60)
            _row_vars(15, 50, 100, 0),
            _row_vars(20, 100, 200, 25),
        ]
        self.app._apply_blinds_from_tab()
        levels = self.db.get_blind_structure()
        self.assertEqual((levels[0]["small_blind"], levels[0]["big_blind"]), (30, 60))
        self.assertEqual(self.app.refresh_blinds_calls, 1)
        self.assertEqual(self.app.refresh_clock_calls, 1)

    def test_ante_modifiee_puis_appliquer(self):
        self.app._blind_row_vars = [
            _row_vars(15, 25, 50, 5),  # ante 0 -> 5
            _row_vars(15, 50, 100, 0),
            _row_vars(20, 100, 200, 25),
        ]
        self.app._apply_blinds_from_tab()
        levels = self.db.get_blind_structure()
        self.assertEqual(levels[0]["ante"], 5)

    def test_duree_modifiee_puis_appliquer(self):
        self.app._blind_row_vars = [
            _row_vars(15, 25, 50, 0),
            _row_vars(15, 50, 100, 0),
            _row_vars(45, 100, 200, 25),  # durée 20 -> 45
        ]
        self.app._apply_blinds_from_tab()
        levels = self.db.get_blind_structure()
        self.assertEqual(levels[2]["duration_minutes"], 45)

    def test_plusieurs_modifications_simultanees(self):
        self.app._blind_row_vars = [
            _row_vars(10, 20, 40, 0),
            _row_vars(25, 75, 150, 10),
            _row_vars(30, 150, 300, 50),
        ]
        self.app._apply_blinds_from_tab()
        levels = self.db.get_blind_structure()
        self.assertEqual(
            [(l["small_blind"], l["big_blind"], l["ante"], l["duration_minutes"]) for l in levels],
            [(20, 40, 0, 10), (75, 150, 10, 25), (150, 300, 50, 30)],
        )

    def test_aucune_confirmation_demandee(self):
        self.app._blind_row_vars = [_row_vars(15, 25, 50, 0)]
        with patch.object(main, "messagebox") as mock_mb:
            self.app._apply_blinds_from_tab()
            mock_mb.askyesno.assert_not_called()


class ValeurInvalideTest(_ApplyBlindsTestCase):
    def test_valeur_non_numerique_naltere_pas_la_base(self):
        before = self.db.get_blind_structure()
        self.app._blind_row_vars = [
            _row_vars("abc", 25, 50, 0),  # durée invalide
            _row_vars(15, 50, 100, 0),
            _row_vars(20, 100, 200, 25),
        ]
        with patch.object(main, "messagebox") as mock_mb:
            self.app._apply_blinds_from_tab()
            mock_mb.showerror.assert_called_once()
        after = self.db.get_blind_structure()
        self.assertEqual(before, after)
        self.assertEqual(self.app.refresh_blinds_calls, 0)
        self.assertEqual(self.app.refresh_clock_calls, 0)

    def test_duree_negative_naltere_pas_la_base_ni_le_niveau_courant(self):
        self.db.set_settings({"current_level_order": 2})
        before = self.db.get_blind_structure()
        self.app._blind_row_vars = [
            _row_vars(15, 25, 50, 0),
            _row_vars(-5, 50, 100, 0),  # durée négative
            _row_vars(20, 100, 200, 25),
        ]
        with patch.object(main, "messagebox") as mock_mb:
            self.app._apply_blinds_from_tab()
            mock_mb.showerror.assert_called_once()
        self.assertEqual(self.db.get_blind_structure(), before)
        self.assertEqual(self.db.get_setting_int("current_level_order"), 2)


class TournoiEnCoursTest(_ApplyBlindsTestCase):
    def test_niveau_courant_conserve_si_toujours_valide(self):
        self.db.set_settings({
            "current_level_order": 2, "level_start_epoch": 1000, "paused_accum_seconds": 42,
            "is_paused": 0, "clock_started": 1,
        })
        self.app._blind_row_vars = [
            _row_vars(15, 30, 60, 0),
            _row_vars(15, 50, 100, 0),
            _row_vars(20, 100, 200, 25),
        ]
        self.app._apply_blinds_from_tab()
        # current_level_order INCHANGÉ (toujours valide dans la nouvelle
        # structure, elle aussi à 3 rounds) — et rien d'autre touché.
        self.assertEqual(self.db.get_setting_int("current_level_order"), 2)
        self.assertEqual(self.db.get_setting_int("level_start_epoch"), 1000)
        self.assertEqual(self.db.get_setting_int("paused_accum_seconds"), 42)
        self.assertEqual(self.db.get_setting_int("is_paused"), 0)
        self.assertEqual(self.db.get_setting_int("clock_started"), 1)

    def test_structure_raccourcie_ramene_au_dernier_niveau_valide(self):
        self.db.set_settings({"current_level_order": 3, "level_start_epoch": 500, "paused_accum_seconds": 7})
        # Un seul round dans le tableau édité : la structure devient plus
        # courte que le niveau courant (3).
        self.app._blind_row_vars = [_row_vars(15, 25, 50, 0)]
        self.app._apply_blinds_from_tab()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 1)  # dernier niveau valide
        # Règle reprise de _generate_custom_blind_structure : SEUL
        # current_level_order est ramené, jamais level_start_epoch ni
        # paused_accum_seconds (contrairement à _go_to_level, jamais
        # appelée ici).
        self.assertEqual(self.db.get_setting_int("level_start_epoch"), 500)
        self.assertEqual(self.db.get_setting_int("paused_accum_seconds"), 7)

    def test_jamais_de_remise_au_niveau_1_quand_toujours_valide(self):
        self.db.set_settings({"current_level_order": 3})
        self.app._blind_row_vars = [
            _row_vars(15, 25, 50, 0),
            _row_vars(15, 50, 100, 0),
            _row_vars(20, 100, 200, 25),
            _row_vars(20, 150, 300, 50),  # structure agrandie (3 -> 4), niveau 3 reste valide
        ]
        self.app._apply_blinds_from_tab()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 3)

    def test_chronometre_rafraichi_immediatement(self):
        self.app._blind_row_vars = [_row_vars(15, 25, 50, 0)]
        self.app._apply_blinds_from_tab()
        self.assertEqual(self.app.refresh_clock_calls, 1)


class StructureStandardNonRegressionTest(_ApplyBlindsTestCase):
    def test_structure_standard_continue_de_reinitialiser_au_niveau_1(self):
        self.db.set_settings({"current_level_order": 2, "level_start_epoch": 999, "paused_accum_seconds": 30})
        self.app.blinds_tree = "dummy"  # hasattr(...) suffit, pas un vrai Treeview (voir _go_to_level)
        with patch.object(main, "messagebox") as mock_mb:
            mock_mb.askyesno.return_value = True
            self.app._reset_blind_structure_from_tab()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 1)
        # _go_to_level (inchangée) redémarre bien le décompte du niveau 1.
        self.assertEqual(self.db.get_setting_int("paused_accum_seconds"), 0)
        # _go_to_level appelle _refresh_clock_tab lui-même (comportement
        # historique, inchangé par ce chantier).
        self.assertEqual(self.app.refresh_clock_calls, 1)

    def test_structure_standard_demande_toujours_confirmation(self):
        self.app.blinds_tree = "dummy"
        with patch.object(main, "messagebox") as mock_mb:
            mock_mb.askyesno.return_value = False
            self.app._reset_blind_structure_from_tab()
            mock_mb.askyesno.assert_called_once()
        # Refusé : la structure d'origine (3 rounds) doit rester intacte.
        self.assertEqual(len(self.db.get_blind_structure()), 3)


if __name__ == "__main__":
    unittest.main()
