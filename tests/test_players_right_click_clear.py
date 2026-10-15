# -*- coding: utf-8 -*-
"""Clic droit pour vider les champs "Nom du joueur" et "Recherche" de
l'onglet Joueurs (demande explicite du 2026-10-15) —
App._on_player_name_right_click / App._on_player_search_right_click.

Exigences couvertes :
1. Un clic droit (Button-2 ET Button-3, même convention déjà établie
   ailleurs dans ce fichier — Button-3 la plupart des plateformes,
   Button-2 sur Mac avec certains trackpads/souris) vide IMMÉDIATEMENT
   le champ concerné.
2. "Nom du joueur" : referme aussi le menu d'auto-complétion Répertoire
   éventuellement affiché (_hide_autocomplete) — jamais d'ajout, jamais
   de suppression, jamais d'accès à roster.py/self.db.
3. "Recherche" : réapplique _on_player_search_keyrelease pour nettoyer
   la sélection du tableau qui en découlait — jamais de filtrage, jamais
   d'accès à roster.py/self.db, jamais d'appel à l'auto-complétion
   Répertoire.
4. Chaque gestionnaire renvoie "break" (neutralise un éventuel menu
   contextuel natif résiduel côté OS).
5. Vérification STRUCTURELLE (AST, même convention que tests/test_
   players_search.py::PlayerSearchSafetyStructuralTest) qu'aucune
   fonction de mutation (ajout/suppression/élimination de joueur,
   répertoire, réglages) n'est jamais appelée par ces deux gestionnaires.
6. Câblage : les deux champs sont bien liés à Button-2 ET Button-3, vers
   le bon gestionnaire chacun — jamais croisés, jamais ajoutés à un
   autre widget.

Même conventions que tests/test_players_search.py : VRAI tk.StringVar/
ttk.Treeview quand utile, jamais de vraie App() complète."""
import ast
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _main_source():
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_py, encoding="utf-8") as f:
        return f.read()


# =====================================================================
# 1. Comportement réel
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayerNameRightClickTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self.name_var = tk.StringVar(value="Jean-Pie")
        self.hide_autocomplete = MagicMock()
        self.win = types.SimpleNamespace(
            new_player_var=self.name_var, _hide_autocomplete=self.hide_autocomplete,
        )
        self.win._on_player_name_right_click = types.MethodType(
            main.App._on_player_name_right_click, self.win
        )

    def test_vide_immediatement_le_champ(self):
        result = self.win._on_player_name_right_click()
        self.assertEqual(self.name_var.get(), "")
        self.assertEqual(result, "break")

    def test_referme_le_menu_dautocompletion(self):
        self.win._on_player_name_right_click()
        self.hide_autocomplete.assert_called_once()

    def test_champ_deja_vide_reste_vide_sans_erreur(self):
        self.name_var.set("")
        result = self.win._on_player_name_right_click()
        self.assertEqual(self.name_var.get(), "")
        self.assertEqual(result, "break")

    def test_accepte_un_evenement_tkinter_reel(self):
        """Appelée en conditions réelles avec un objet Event (pas juste
        None) — le binding passe toujours un événement."""
        entry = ttk.Entry(self.root, textvariable=self.name_var)
        entry.pack()
        self.addCleanup(entry.destroy)
        fake_event = types.SimpleNamespace(widget=entry)
        result = self.win._on_player_name_right_click(fake_event)
        self.assertEqual(self.name_var.get(), "")
        self.assertEqual(result, "break")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayerSearchRightClickTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self.tree = ttk.Treeview(self.root, columns=("name",), show="headings")
        self.tree.heading("name", text="Nom")
        self.tree.pack()
        for name in ("Alice", "Bruno", "Chloé"):
            self.tree.insert("", "end", iid=name, values=(name,))
        self.search_var = tk.StringVar(value="Bru")
        self.win = types.SimpleNamespace(
            players_tree=self.tree, player_search_var=self.search_var,
        )
        self.win._on_player_search_keyrelease = types.MethodType(
            main.App._on_player_search_keyrelease, self.win
        )
        self.win._on_player_search_right_click = types.MethodType(
            main.App._on_player_search_right_click, self.win
        )
        self.addCleanup(lambda: cleanup_tk(self, "tree", "win"))

    def test_vide_immediatement_le_champ(self):
        result = self.win._on_player_search_right_click()
        self.assertEqual(self.search_var.get(), "")
        self.assertEqual(result, "break")

    def test_nettoie_la_selection_issue_de_la_recherche(self):
        """"Bru" avait positionné la sélection sur "Bruno" — le clic
        droit doit la retirer, exactement comme un champ vidé au
        clavier (réutilise _on_player_search_keyrelease, pas une
        logique dupliquée)."""
        self.win._on_player_search_keyrelease()
        self.assertEqual(self.tree.selection(), ("Bruno",))

        self.win._on_player_search_right_click()

        self.assertEqual(self.search_var.get(), "")
        self.assertEqual(self.tree.selection(), ())

    def test_liste_jamais_filtree_ni_reordonnee(self):
        avant = self.tree.get_children()
        self.win._on_player_search_right_click()
        self.assertEqual(self.tree.get_children(), avant)

    def test_champ_deja_vide_reste_vide_sans_erreur(self):
        self.search_var.set("")
        result = self.win._on_player_search_right_click()
        self.assertEqual(self.search_var.get(), "")
        self.assertEqual(result, "break")


# =====================================================================
# 2. Vérification structurelle : jamais de mutation, jamais de
#    destruction (demande explicite : "aucune suppression de joueur,
#    modification de la base de données ou opération destructive").
# =====================================================================
class _SourceHelper:
    @classmethod
    def setUpClass(cls):
        cls.source = _main_source()
        cls.tree = ast.parse(cls.source, filename="main.py")

    def _find_method(self, class_name, method_name):
        cls_node = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.ClassDef) and n.name == class_name
        )
        return next(
            n for n in ast.walk(cls_node)
            if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def _source_of(self, node):
        return ast.get_source_segment(self.source, node) or ""

    def _body_without_docstring(self, func):
        body_stmts = func.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        return "\n".join(self._source_of(s) for s in body_stmts)


class RightClickSafetyStructuralTest(_SourceHelper, unittest.TestCase):
    _FORBIDDEN_CALLS = {
        "add_player", "delete_player", "eliminate_player", "rebuy_player",
        "remove_player", "withdraw_player", "set_settings", "add_to_roster",
        "remove_from_roster", "load_roster",
    }

    def test_nom_du_joueur_aucun_appel_de_mutation(self):
        func = self._find_method("App", "_on_player_name_right_click")
        called_attrs = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertEqual(called_attrs & self._FORBIDDEN_CALLS, set())
        self.assertTrue(called_attrs <= {"set", "_hide_autocomplete"}, called_attrs)

    def test_nom_du_joueur_jamais_db_ni_roster(self):
        func = self._find_method("App", "_on_player_name_right_click")
        code_only = self._body_without_docstring(func)
        self.assertNotIn("roster.", code_only)
        self.assertNotIn("self.db", code_only)
        self.assertNotIn("_add_player", code_only)

    def test_recherche_aucun_appel_de_mutation(self):
        func = self._find_method("App", "_on_player_search_right_click")
        called_attrs = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertEqual(called_attrs & self._FORBIDDEN_CALLS, set())
        self.assertTrue(called_attrs <= {"set", "_on_player_search_keyrelease"}, called_attrs)

    def test_recherche_jamais_db_ni_roster_ni_autocompletion(self):
        func = self._find_method("App", "_on_player_search_right_click")
        code_only = self._body_without_docstring(func)
        self.assertNotIn("roster.", code_only)
        self.assertNotIn("self.db", code_only)
        self.assertNotIn("_on_player_name_keyrelease", code_only)
        self.assertNotIn("_show_autocomplete", code_only)

    def test_les_deux_gestionnaires_renvoient_break(self):
        for name in ("_on_player_name_right_click", "_on_player_search_right_click"):
            func = self._find_method("App", name)
            seg = self._source_of(func)
            self.assertIn('return "break"', seg)


class RightClickBindingStructuralTest(_SourceHelper, unittest.TestCase):
    def test_champ_nom_lie_aux_deux_boutons_droits(self):
        func = self._find_method("App", "_build_players_tab")
        seg = self._source_of(func)
        self.assertIn(
            'self.new_player_entry.bind("<Button-2>", self._on_player_name_right_click)', seg,
        )
        self.assertIn(
            'self.new_player_entry.bind("<Button-3>", self._on_player_name_right_click)', seg,
        )

    def test_champ_recherche_lie_aux_deux_boutons_droits(self):
        func = self._find_method("App", "_build_players_tab")
        seg = self._source_of(func)
        self.assertIn(
            'self.player_search_entry.bind("<Button-2>", self._on_player_search_right_click)', seg,
        )
        self.assertIn(
            'self.player_search_entry.bind("<Button-3>", self._on_player_search_right_click)', seg,
        )

    def test_jamais_croises_entre_les_deux_champs(self):
        """Le gestionnaire du champ Recherche n'est jamais lié au champ
        Nom du joueur, et réciproquement."""
        func = self._find_method("App", "_build_players_tab")
        seg = self._source_of(func)
        self.assertNotIn(
            'self.new_player_entry.bind("<Button-2>", self._on_player_search_right_click)', seg,
        )
        self.assertNotIn(
            'self.player_search_entry.bind("<Button-2>", self._on_player_name_right_click)', seg,
        )


if __name__ == "__main__":
    unittest.main()
