# -*- coding: utf-8 -*-
"""Tests ciblés de l'héritage automatique du bloc Primes par un nouveau
tournoi (demande du 2026-09-18, "Senaco doit reprendre automatiquement
les paramètres de Primes déjà en place / utilisés précédemment") :

- attendance_bonus_points / assiduity_bonus_points /
  assiduity_consecutive_days rejoignent simplement le mécanisme
  générique déjà existant (tournament_prefs.PERSISTED_KEYS,
  load_last_settings/save_last_settings) — même principe que
  bounty_amount/pko_mode/pko_cash_percent, déjà couverts.
- ranking_formula rejoint ce même mécanisme, mais avec DEUX protections
  supplémentaires (voir tournament_prefs.py et main.py:
  _collect_and_save_all_settings / _choose_tournament_file) :
  1. la valeur "" (placeholder légataire, aucun choix explicite fait)
     n'est JAMAIS écrite dans last_settings.json ;
  2. à la création d'un nouveau tournoi, un ranking_formula hérité et
     valide est conservé ; à défaut, le comportement sûr actuel
     (stampage explicite "none") reste inchangé.

Database.resolve_ranking_formula() (interprétation des anciens fichiers
.tournoi) n'est PAS modifiée par ce chantier — vérifié explicitement
ci-dessous par des tests de non-régression.

Quatre niveaux de test, AUCUN ne pilote de vrai dialogue Tk modal :
- TournamentPrefsPersistedKeysUnitTest : tournament_prefs.py seul, sans
  Tk, sans Database.
- CollectAndSaveSettingsPrimesTest : App._collect_and_save_all_settings
  (écriture depuis l'onglet Paramètres), même harnais minimal (racine Tk
  nue, AUCUN Toplevel modal) que tests/test_ranking_formula_settings_
  save.py.
- NewTournamentRankingFormulaLogicStructuralTest : preuve structurelle
  (AST de main.py, voir sa docstring) de la règle exacte appliquée par
  App._choose_tournament_file à la création d'un tournoi neuf — une
  tentative antérieure de piloter ce dialogue réellement de bout en bout
  a provoqué un SIGSEGV Tcl/Tk réel (wait_window imbriqué depuis un
  callback after() programmé) ; abandonnée, jamais à reproduire.
- RankingFormulaLegacyCompatibilityNonRegressionTest / SettingsTemplates
  PrimesNonRegressionTest : non-régression (anciens fichiers .tournoi,
  "Paramètres sauvés").
"""
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import database  # noqa: E402
import main  # noqa: E402
import settings_templates  # noqa: E402
import tournament_prefs  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


RANKING_FORMULAS = [
    database.RANKING_FORMULA_NONE,
    database.RANKING_FORMULA_CURRENT,
    database.RANKING_FORMULA_PROGRESSIVE,
    database.RANKING_FORMULA_SITNGO_CPC,
]


class TournamentPrefsPersistedKeysUnitTest(unittest.TestCase):
    """tournament_prefs.py seul : aucun Tk, aucune Database."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="prefs_primes_test_")
        self.addCleanup(self._tmp.cleanup)
        prefs_path = os.path.join(self._tmp.name, "last_settings.json")
        patcher = patch.object(tournament_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_quatre_cles_primes_dans_persisted_keys(self):
        for key in (
            "attendance_bonus_points", "assiduity_bonus_points",
            "assiduity_consecutive_days", "ranking_formula",
        ):
            self.assertIn(key, tournament_prefs.PERSISTED_KEYS)

    def test_round_trip_presence_assiduite_jours_consecutifs(self):
        tournament_prefs.save_last_settings({
            "attendance_bonus_points": "10",
            "assiduity_bonus_points": "10",
            "assiduity_consecutive_days": "2",
        })
        loaded = tournament_prefs.load_last_settings()
        self.assertEqual(loaded["attendance_bonus_points"], "10")
        self.assertEqual(loaded["assiduity_bonus_points"], "10")
        self.assertEqual(loaded["assiduity_consecutive_days"], "2")

    def test_round_trip_ranking_formula_valeurs_valides(self):
        for formula in RANKING_FORMULAS:
            with self.subTest(formula=formula):
                tournament_prefs.save_last_settings({"ranking_formula": formula})
                self.assertEqual(tournament_prefs.load_last_settings()["ranking_formula"], formula)

    def test_bounty_pko_toujours_presents_non_regression(self):
        for key in ("bounty_amount", "pko_mode", "pko_cash_percent"):
            self.assertIn(key, tournament_prefs.PERSISTED_KEYS)
        tournament_prefs.save_last_settings({
            "bounty_amount": "50", "pko_mode": "1", "pko_cash_percent": "70",
        })
        loaded = tournament_prefs.load_last_settings()
        self.assertEqual(loaded["bounty_amount"], "50")
        self.assertEqual(loaded["pko_mode"], "1")
        self.assertEqual(loaded["pko_cash_percent"], "70")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class CollectAndSaveSettingsPrimesTest(unittest.TestCase):
    """App._collect_and_save_all_settings : ce qui atterrit réellement
    dans last_settings.json quand l'utilisateur clique "Appliquer"/
    "Enregistrer Paramètres sous..." dans l'onglet Paramètres. Même
    principe que tests/test_ranking_formula_settings_save.py, étendu
    aux 3 nouveaux champs Primes."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : 2 méthodes de main.App greffées sur cls.root
        # forment chacune un cycle, réclamé ici par gc.collect().
        cleanup_tk(cls, "root")

    def setUp(self):
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="collect_save_primes_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.prefs_path = os.path.join(self._tmp.name, "last_settings.json")
        patcher = patch.object(tournament_prefs, "_prefs_path", return_value=self.prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.win = self.root
        self.win.db = self.db
        self.win._collect_and_save_all_settings = types.MethodType(
            main.App._collect_and_save_all_settings, self.win
        )
        self.win._update_window_title = types.MethodType(main.App._update_window_title, self.win)
        self.win._trigger_movement_alert = MagicMock()
        self.win._check_pending_rebalance = MagicMock()
        self.win.settings_vars = {
            "max_seats_per_table": tk.StringVar(value="9"),
            "min_players_per_table": tk.StringVar(value="4"),
            "attendance_bonus_points": tk.StringVar(value="0"),
            "assiduity_bonus_points": tk.StringVar(value="0"),
            "assiduity_consecutive_days": tk.StringVar(value="2"),
            "ranking_formula": tk.StringVar(value="none"),
            "bounty_amount": tk.StringVar(value="0"),
            "pko_mode": tk.StringVar(value="0"),
        }

    def _save(self):
        with patch.object(main, "messagebox"):
            self.win._collect_and_save_all_settings()

    def _load_prefs_file(self):
        if not os.path.exists(self.prefs_path):
            return {}
        with open(self.prefs_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def test_presence_assiduite_jours_ecrits_dans_last_settings(self):
        self.win.settings_vars["attendance_bonus_points"].set("10")
        self.win.settings_vars["assiduity_bonus_points"].set("10")
        self.win.settings_vars["assiduity_consecutive_days"].set("2")
        self._save()
        prefs = self._load_prefs_file()
        self.assertEqual(prefs["attendance_bonus_points"], "10")
        self.assertEqual(prefs["assiduity_bonus_points"], "10")
        self.assertEqual(prefs["assiduity_consecutive_days"], "2")

    def test_ranking_formula_vide_jamais_ecrit_dans_last_settings(self):
        """Le placeholder légataire ("", voir RANKING_FORMULA_LEGACY_
        PLACEHOLDER) ne doit jamais devenir une préférence héritée —
        sous peine de faire retomber un futur tournoi neuf sur le
        fallback légataire (Classique) au lieu du "Aucun" sûr."""
        self.db.set_settings({"ranking_bonus_points": 77})
        self.win.settings_vars["ranking_formula"].set("")
        self._save()
        prefs = self._load_prefs_file()
        self.assertNotIn("ranking_formula", prefs)

    def test_ranking_formula_choix_reel_ecrit_dans_last_settings(self):
        for formula in RANKING_FORMULAS:
            with self.subTest(formula=formula):
                self.win.settings_vars["ranking_formula"].set(formula)
                self._save()
                prefs = self._load_prefs_file()
                self.assertEqual(prefs["ranking_formula"], formula)

    def test_bounty_pko_toujours_ecrits_non_regression(self):
        self.win.settings_vars["bounty_amount"].set("50")
        self.win.settings_vars["pko_mode"].set("1")
        self._save()
        prefs = self._load_prefs_file()
        self.assertEqual(prefs["bounty_amount"], "50")
        self.assertEqual(prefs["pko_mode"], "1")


class NewTournamentRankingFormulaLogicStructuralTest(unittest.TestCase):
    """Vérifie la règle exacte de App._choose_tournament_file pour
    ranking_formula à la création d'un tournoi neuf (point 5 de la
    demande du 2026-09-18) SANS jamais construire ni piloter la fenêtre
    modale "Bienvenue" : une tentative précédente pilotant ce dialogue
    réellement (bouton invoqué depuis un callback after() PENDANT que
    _choose_tournament_file était déjà bloqué dans son propre
    wait_window) a provoqué un SIGSEGV Tcl/Tk réel (wait_window imbriqué
    depuis un callback programmé, plutôt qu'un vrai événement souris) —
    abandonnée, jamais à reproduire.

    Plutôt qu'un refactoring du code de production pour en extraire une
    fonction pure testable isolément (jugé non nécessaire ici), ce test
    vérifie directement, par analyse de l'arbre syntaxique (AST) de
    main.py, que _choose_tournament_file contient bien EXACTEMENT la
    règle voulue — même principe déjà utilisé dans ce projet pour des
    preuves structurelles équivalentes (voir tests/test_single_
    tournament_at_a_time.py: NoBypassPathExistsTest). Combiné à :
    - TournamentPrefsPersistedKeysUnitTest / CollectAndSaveSettingsPrimesTest
      (le contenu réel de last_settings, avant/après filtrage "") ;
    - RankingFormulaLegacyCompatibilityNonRegressionTest (Database.
      resolve_ranking_formula, totalement inchangée) ;
    ce trio couvre le comportement complet SANS jamais risquer de
    reproduire le crash."""

    def _parse_main(self):
        import ast

        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=main_py)

    def _find_function(self, tree, name):
        import ast

        matches = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
        self.assertEqual(len(matches), 1, f"{name} introuvable ou nom ambigu dans main.py")
        return matches[0]

    def test_lit_bien_ranking_formula_depuis_last_settings(self):
        import ast

        func = self._find_function(self._parse_main(), "_choose_tournament_file")
        reads_it = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
            and isinstance(n.func.value, ast.Name) and n.func.value.id == "last_settings"
            and n.args and isinstance(n.args[0], ast.Constant) and n.args[0].value == "ranking_formula"
            for n in ast.walk(func)
        )
        self.assertTrue(
            reads_it, "_choose_tournament_file ne lit plus last_settings.get('ranking_formula')",
        )

    def test_garde_not_in_ranking_formula_labels_present_et_unique(self):
        import ast

        func = self._find_function(self._parse_main(), "_choose_tournament_file")
        guards = [
            n for n in ast.walk(func)
            if isinstance(n, ast.Compare)
            and len(n.ops) == 1 and isinstance(n.ops[0], ast.NotIn)
            and len(n.comparators) == 1
            and isinstance(n.comparators[0], ast.Name) and n.comparators[0].id == "RANKING_FORMULA_LABELS"
        ]
        self.assertEqual(
            len(guards), 1,
            "garde '... not in RANKING_FORMULA_LABELS' introuvable ou dupliquée",
        )

    def test_stampage_none_present_correctement_forme_et_unique(self):
        import ast

        func = self._find_function(self._parse_main(), "_choose_tournament_file")

        def is_stamp(node):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "set_settings"):
                return False
            if not node.args or not isinstance(node.args[0], ast.Dict):
                return False
            d = node.args[0]
            return any(
                isinstance(k, ast.Constant) and k.value == "ranking_formula"
                and isinstance(v, ast.Name) and v.id == "RANKING_FORMULA_NONE"
                for k, v in zip(d.keys, d.values)
            )

        stamps = [n for n in ast.walk(func) if is_stamp(n)]
        self.assertEqual(
            len(stamps), 1,
            "stampage set_settings({'ranking_formula': RANKING_FORMULA_NONE}) introuvable ou dupliqué",
        )

    def test_stampage_none_conditionne_par_la_garde_jamais_inconditionnel(self):
        """Le point exact demandé : un ranking_formula hérité valide
        n'est jamais écrasé (le stampage "none" ne doit s'exécuter que
        DANS le bloc if de la garde 'not in RANKING_FORMULA_LABELS'),
        et cette application se fait bien APRÈS que last_settings ait
        déjà été appliqué en bloc (set_settings(last_settings) plus
        haut, qui aurait déjà posé la valeur héritée le cas échéant)."""
        import ast

        func = self._find_function(self._parse_main(), "_choose_tournament_file")

        guard_if = None
        for n in ast.walk(func):
            if isinstance(n, ast.If) and isinstance(n.test, ast.Compare) \
               and len(n.test.ops) == 1 and isinstance(n.test.ops[0], ast.NotIn) \
               and len(n.test.comparators) == 1 \
               and isinstance(n.test.comparators[0], ast.Name) \
               and n.test.comparators[0].id == "RANKING_FORMULA_LABELS":
                guard_if = n
                break
        self.assertIsNotNone(guard_if, "le garde 'not in RANKING_FORMULA_LABELS' n'est pas un test de if")

        def is_stamp(node):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "set_settings"):
                return False
            if not node.args or not isinstance(node.args[0], ast.Dict):
                return False
            d = node.args[0]
            return any(
                isinstance(k, ast.Constant) and k.value == "ranking_formula"
                and isinstance(v, ast.Name) and v.id == "RANKING_FORMULA_NONE"
                for k, v in zip(d.keys, d.values)
            )

        stamp_in_guard = [n for n in ast.walk(guard_if) if n is not guard_if.test and is_stamp(n)]
        self.assertEqual(
            len(stamp_in_guard), 1,
            "le stampage 'none' doit être DANS le bloc if du garde, jamais ailleurs/inconditionnel",
        )

        # set_settings(last_settings) (application en bloc, y compris
        # ranking_formula s'il y figure) doit précéder ce garde dans le
        # texte de la fonction — sans quoi une valeur héritée valide
        # serait appliquée APRÈS le stampage "none" et donc jamais visible.
        def is_apply_last_settings(node):
            return (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "set_settings"
                and node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "last_settings"
            )

        apply_nodes = [n for n in ast.walk(func) if is_apply_last_settings(n)]
        self.assertEqual(len(apply_nodes), 1, "set_settings(last_settings) introuvable ou dupliqué")
        self.assertLess(
            apply_nodes[0].lineno, guard_if.lineno,
            "set_settings(last_settings) doit s'exécuter AVANT le garde ranking_formula, sinon "
            "une valeur héritée valide serait écrasée par le stampage 'none'",
        )


class RankingFormulaLegacyCompatibilityNonRegressionTest(unittest.TestCase):
    """Database.resolve_ranking_formula() n'a pas été modifiée par ce
    chantier : vérifie explicitement que l'interprétation des anciens
    fichiers .tournoi (sans ranking_formula) reste strictement
    inchangée."""

    def setUp(self):
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)

    def test_ancien_tournoi_sans_ranking_formula_ni_valeur_fixe(self):
        row = self.db.conn.execute(
            "SELECT value FROM settings WHERE key='ranking_formula'"
        ).fetchone()
        self.assertIsNone(row)
        formula, legacy = self.db.resolve_ranking_formula()
        self.assertEqual(formula, database.RANKING_FORMULA_CURRENT)
        self.assertIsNone(legacy)

    def test_ancien_tournoi_avec_ranking_bonus_points_historique(self):
        self.db.set_settings({"ranking_bonus_points": 77})
        row = self.db.conn.execute(
            "SELECT value FROM settings WHERE key='ranking_formula'"
        ).fetchone()
        self.assertIsNone(row)
        formula, legacy = self.db.resolve_ranking_formula()
        self.assertEqual(formula, database.RANKING_FORMULA_CURRENT)
        self.assertEqual(legacy, 77)


class SettingsTemplatesPrimesNonRegressionTest(unittest.TestCase):
    """"Paramètres sauvés" (settings_templates.py) : non touché par ce
    chantier, round-trip toujours correct pour les champs Primes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="settings_templates_primes_test_")
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(settings_templates, "_templates_dir", return_value=self._tmp.name)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_round_trip_bloc_primes_complet(self):
        values = {
            "attendance_bonus_points": "10",
            "assiduity_bonus_points": "10",
            "assiduity_consecutive_days": "2",
            "ranking_formula": database.RANKING_FORMULA_CURRENT,
            "bounty_amount": "50",
            "pko_mode": "1",
            "tournament_name": "Ne doit jamais être mémorisé",
        }
        settings_templates.save_template("Mon modèle Primes", values)
        loaded = settings_templates.load_template("Mon modèle Primes")
        for key, value in values.items():
            if key == "tournament_name":
                self.assertNotIn(key, loaded)
            else:
                self.assertEqual(loaded[key], value)


if __name__ == "__main__":
    unittest.main()
