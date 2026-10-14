# -*- coding: utf-8 -*-
"""Champ "Recherche" de l'onglet Joueurs (demande explicite du
2026-10-14, "retrouver rapidement un joueur dans une liste de 100+ sans
l'ascenseur") — App._on_player_search_keyrelease.

Exigences couvertes :
1. Recherche progressive (D -> DU -> DUP), insensible à la casse,
   déplace UNIQUEMENT la sélection (jamais de filtrage ni de
   réordonnancement — self.players_tree.get_children() n'est jamais
   modifié par la recherche elle-même).
2. Plusieurs correspondances : le premier dans l'ORDRE D'AFFICHAGE
   ACTUEL (pas un tri alphabétique recalculé).
3. Aucune correspondance / champ vidé : sélection supprimée (jamais un
   joueur incorrect laissé sélectionné).
4. Retour arrière : même gestionnaire, aucune logique séparée.
5. Défilement automatique (.see()) vers la ligne trouvée.
6. Totalement indépendante du champ "Nom du joueur"/de l'auto-
   complétion Répertoire (roster.py jamais consulté).
7. Ne déclenche AUCUNE action involontaire : vérifié à la fois par
   construction (la fonction ne fait que lire players_tree et déplacer
   sa sélection) et par inspection du code source (AST) — aucun appel à
   _add_player/db.*/roster.*.
8. Réappliquée automatiquement par _refresh_players_tab() si le champ
   n'est pas vide (sélection cohérente après un rafraîchissement
   déclenché depuis l'iPhone).
9. Disposition : "Recherche :" + champ AVANT self.stats_lbl ("Actifs
   ..."), sur la même ligne ; les champs/boutons "Nom du joueur",
   "Ajouter", "Temp" restent des lignes INCHANGÉES.

Même principe que tests/test_players_right_click_eliminate.py::
PlayersTreeRightClickTest : un VRAI ttk.Treeview (identify_row/
selection_set/see réels, pas la peine de les réimplémenter à la main),
un seul tk.Tk() pour toute la classe, jamais de vraie App() complète."""
import ast
import os
import sys
import types
import unittest
from unittest.mock import patch

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
# 1. Comportement réel (VRAI Treeview, jamais de vraie App complète)
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayerSearchKeyreleaseTest(unittest.TestCase):
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
        self.search_var = tk.StringVar()
        self.win = types.SimpleNamespace(
            players_tree=self.tree, player_search_var=self.search_var,
        )
        self.win._on_player_search_keyrelease = types.MethodType(
            main.App._on_player_search_keyrelease, self.win
        )
        self.addCleanup(lambda: cleanup_tk(self, "tree", "win"))

    def _insert(self, *names_in_order):
        """Insère les joueurs dans L'ORDRE DONNÉ (reproduit l'ordre
        d'affichage actuel du tableau, qu'il soit alphabétique ou non —
        voir App._refresh_players_tab, players_tree.get_children()
        reflète toujours cet ordre d'insertion/affichage)."""
        for idx, name in enumerate(names_in_order):
            self.tree.insert("", "end", iid=str(idx + 1), values=(name,))

    def _search(self, text):
        self.search_var.set(text)
        self.win._on_player_search_keyrelease()

    def test_recherche_progressive_affine_la_selection(self):
        self._insert("Albert", "Dupont", "Dupuis", "Durand", "Martin")
        self._search("D")
        self.assertEqual(self.tree.selection(), ("2",))  # Dupont, 1er "D"

        self._search("DU")
        self.assertEqual(self.tree.selection(), ("2",))  # toujours Dupont

        self._search("DUP")
        self.assertEqual(self.tree.selection(), ("2",))  # Dupont (et Dupuis) -> Dupont reste 1er

        self._search("DUPU")
        self.assertEqual(self.tree.selection(), ("3",))  # affiné sur Dupuis

    def test_insensible_a_la_casse(self):
        self._insert("Albert", "Dupont")
        self._search("dup")
        self.assertEqual(self.tree.selection(), ("2",))
        self._search("DUP")
        self.assertEqual(self.tree.selection(), ("2",))

    def test_plusieurs_correspondances_premier_dans_l_ordre_actuel(self):
        """Ordre d'affichage délibérément NON alphabétique (ex. trié par
        table) : le premier trouvé doit être celui affiché en premier,
        jamais un recalcul alphabétique."""
        self._insert("Martin", "Durand", "Dupont", "Albert")
        self._search("D")
        self.assertEqual(self.tree.selection(), ("2",))  # Durand, premier "D" affiché

    def test_aucune_correspondance_supprime_la_selection(self):
        self._insert("Albert", "Dupont")
        self.tree.selection_set("1")  # sélection manuelle préexistante
        self._search("ZZZ")
        self.assertEqual(self.tree.selection(), ())
        self.assertEqual(self.tree.focus(), "")

    def test_champ_vide_supprime_la_selection(self):
        self._insert("Albert", "Dupont")
        self._search("D")
        self.assertEqual(self.tree.selection(), ("2",))
        self._search("")
        self.assertEqual(self.tree.selection(), ())
        self.assertEqual(self.tree.focus(), "")

    def test_retour_arriere_elargit_la_recherche(self):
        """Même gestionnaire : le Retour arrière relit simplement le
        texte déjà raccourci par l'Entry — aucune logique séparée."""
        self._insert("Albert", "Dupont", "Dupuis")
        self._search("DUPU")
        self.assertEqual(self.tree.selection(), ("3",))  # Dupuis
        self._search("DUP")  # "Retour arrière" simulé : une lettre en moins
        self.assertEqual(self.tree.selection(), ("2",))  # revient à Dupont

    def test_defilement_automatique_vers_la_ligne_trouvee(self):
        """La ligne trouvée doit être explicitement amenée dans la zone
        visible via players_tree.see(iid) — vérifié en espionnant le
        VRAI .see() du Treeview (jamais update_idletasks()/update() ici :
        voir tests/test_bienvenue_window_height.py, SIGSEGV Tcl/Tk
        reproductible dans ce harnais avec cet appel précis, déjà
        diagnostiqué ailleurs dans cette suite — reproduit une seconde
        fois en écrivant ce test, confirmant qu'il faut l'éviter
        systématiquement)."""
        self.tree.configure(height=3)  # force une zone visible réduite
        self._insert(*[f"Joueur{i:03d}" for i in range(50)])
        with patch.object(self.tree, "see", wraps=self.tree.see) as mock_see:
            self._search("joueur049")
            mock_see.assert_called_once_with("50")
        self.assertEqual(self.tree.selection(), ("50",))

    def test_liste_jamais_filtree_ni_reordonnee(self):
        """Exigence explicite : tous les joueurs restent affichés, dans
        le même ordre, quel que soit le texte recherché."""
        names = ["Martin", "Durand", "Dupont", "Albert"]
        self._insert(*names)
        before = self.tree.get_children()
        self._search("D")
        after_match = self.tree.get_children()
        self._search("ZZZ")
        after_no_match = self.tree.get_children()
        self._search("")
        after_empty = self.tree.get_children()
        self.assertEqual(before, after_match)
        self.assertEqual(before, after_no_match)
        self.assertEqual(before, after_empty)
        self.assertEqual(len(self.tree.get_children()), len(names))

    def test_aucune_mutation_des_colonnes_affichees(self):
        """La recherche ne doit jamais modifier les valeurs affichées
        d'une ligne (seulement sa sélection/focus)."""
        self._insert("Albert", "Dupont")
        before = {iid: self.tree.item(iid, "values") for iid in self.tree.get_children()}
        self._search("D")
        self._search("DU")
        self._search("ZZZ")
        after = {iid: self.tree.item(iid, "values") for iid in self.tree.get_children()}
        self.assertEqual(before, after)


# =====================================================================
# 2. Vérifications structurelles (AST) — câblage et sécurité
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


class PlayerSearchSafetyStructuralTest(_SourceHelper, unittest.TestCase):
    def test_jamais_dappel_a_une_fonction_de_mutation(self):
        """Vérifie, sur le CODE RÉEL, qu'aucun appel pouvant ajouter/
        supprimer/modifier un joueur ou accéder au Répertoire n'existe
        dans _on_player_search_keyrelease — seuls selection_set/focus/
        see sur players_tree, et la lecture de player_search_var."""
        func = self._find_method("App", "_on_player_search_keyrelease")
        called_attrs = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        forbidden = {
            "add_player", "delete_player", "eliminate_player", "rebuy_player",
            "set_settings", "add_to_roster", "load_roster", "list_players",
        }
        self.assertEqual(called_attrs & forbidden, set())
        allowed = {"get", "strip", "lower", "selection_set", "focus", "see", "get_children", "set", "startswith"}
        self.assertTrue(called_attrs <= allowed, f"appel(s) inattendu(s) : {called_attrs - allowed}")

    def test_jamais_de_reference_a_roster_ou_db(self):
        """Code RÉEL uniquement (docstring exclue : elle mentionne elle-
        même "roster.py"/"self.db" en explication de ce qui N'est PAS
        fait, ce qui ferait échouer à tort une recherche de sous-chaîne
        sur le texte complet)."""
        func = self._find_method("App", "_on_player_search_keyrelease")
        body_stmts = func.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        code_only = "\n".join(self._source_of(s) for s in body_stmts)
        self.assertNotIn("roster.", code_only)
        self.assertNotIn("self.db", code_only)

    def test_jamais_dappel_a_lautocompletion_repertoire(self):
        func = self._find_method("App", "_on_player_search_keyrelease")
        seg = self._source_of(func)
        self.assertNotIn("_on_player_name_keyrelease", seg)
        self.assertNotIn("_show_autocomplete", seg)


class RefreshPlayersTabReapplicationStructuralTest(_SourceHelper, unittest.TestCase):
    def test_reapplique_la_recherche_si_champ_non_vide(self):
        """getattr(self, "player_search_var", None) plutôt qu'un accès
        direct : de nombreux tests existants appellent _refresh_players_
        tab sur des doublures légères de App n'ayant jamais construit
        _build_players_tab (donc sans cet attribut) — vérifié que ce
        garde reste bien présent, pour ne jamais les casser."""
        func = self._find_method("App", "_refresh_players_tab")
        seg = self._source_of(func)
        self.assertIn('getattr(self, "player_search_var", None)', seg)
        self.assertIn("self._on_player_search_keyrelease()", seg)
        idx_getattr = seg.index('getattr(self, "player_search_var", None)')
        idx_call = seg.index("self._on_player_search_keyrelease()")
        self.assertLess(idx_getattr, idx_call)


class BuildPlayersTabLayoutStructuralTest(_SourceHelper, unittest.TestCase):
    def test_recherche_avant_stats_lbl_meme_ligne(self):
        """"Recherche :" + champ doivent être construits AVANT
        self.stats_lbl (demande explicite : "Actifs..." déplacé juste
        après la zone de recherche, même ligne/frame stats_row)."""
        func = self._find_method("App", "_build_players_tab")
        seg = self._source_of(func)
        idx_search_label = seg.index('text="Recherche :"')
        idx_search_entry = seg.index("self.player_search_entry = ttk.Entry(stats_row")
        idx_stats_lbl = seg.index("self.stats_lbl = ttk.Label(stats_row")
        self.assertLess(idx_search_label, idx_search_entry)
        self.assertLess(idx_search_entry, idx_stats_lbl)

    def test_ajouter_temp_nom_du_joueur_inchanges(self):
        """Non-régression explicite : les lignes de construction de
        "Nom du joueur"/"Ajouter"/"Temp" restent exactement celles
        d'avant ce chantier."""
        func = self._find_method("App", "_build_players_tab")
        seg = self._source_of(func)
        self.assertIn('ttk.Label(top, text="Nom du joueur :").pack(side="left")', seg)
        self.assertIn('ttk.Button(top, text="Ajouter", command=self._add_player).pack(side="left", padx=5)', seg)
        self.assertIn('text="Temp (ne pas ajouter au répertoire)"', seg)
        self.assertIn('self.new_player_entry.bind("<KeyRelease>", self._on_player_name_keyrelease)', seg)
        self.assertIn('self.new_player_entry.bind("<Return>", lambda e: self._add_player())', seg)

    def test_champ_recherche_independant_du_champ_nom(self):
        func = self._find_method("App", "_build_players_tab")
        seg = self._source_of(func)
        self.assertIn("self.player_search_var = tk.StringVar()", seg)
        self.assertIn(
            'self.player_search_entry.bind("<KeyRelease>", self._on_player_search_keyrelease)', seg,
        )
        # Le champ Recherche n'est JAMAIS lié à _on_player_name_keyrelease
        # (auto-complétion Répertoire) ni à _add_player.
        search_entry_block_start = seg.index("self.player_search_entry = ttk.Entry(")
        search_entry_block = seg[search_entry_block_start:search_entry_block_start + 500]
        self.assertNotIn("_on_player_name_keyrelease", search_entry_block)
        self.assertNotIn("_add_player", search_entry_block)


if __name__ == "__main__":
    unittest.main()
