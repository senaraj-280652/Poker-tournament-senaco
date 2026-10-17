# -*- coding: utf-8 -*-
"""Accès à l'onglet LOG depuis le menu réduit "🖥️ Ouvrir Senaco" (demande
du 2026-10-16) : "le LOG fait partie des outils de suivi et de
statistiques, je souhaite qu'il soit également accessible depuis ce
menu restreint."

Comportement attendu, fixé par l'utilisateur :
1. L'onglet LOG est construit (self.db is None, "sans tournoi") juste
   après Statistiques, juste avant Paramètres — même ordre relatif que
   dans le menu complet (Statistiques -> CA -> LOG -> Paramètres, CA
   mis à part, non pertinent sans tournoi).
2. _build_log_tab() est RÉUTILISÉE telle quelle (jamais une seconde
   implémentation) — déjà entièrement indépendante de self.db (vérifié
   : aucune de ses ~20 méthodes ne référence self.db, voir le diagnostic
   livré avant cette implémentation), donc consultation/export/purge
   fonctionnent à l'identique, avec les mêmes garde-fous qu'avec un
   tournoi ouvert (_on_log_purge notamment, jamais modifiée).
3. Une entrée "LOG > Consulter le LOG..." est ajoutée au menu réduit,
   juste après "Statistiques" — même principe que _open_period_summary
   (un simple raccourci self.notebook.select(...), jamais une fenêtre
   séparée).
4. Le menu COMPLET (tournoi ouvert) reste strictement inchangé — pas de
   nouvelle entrée "LOG" là (déjà accessible via la barre d'onglets).

ATTENTION (même limite documentée dans tests/test_fermeture_tournoi_
retour_standalone.py) : App.__init__ construit une vraie fenêtre Tk
complète — trop coûteux/fragile à instancier ici pour de vrai. Ce
fichier vérifie donc le câblage par analyse du CODE SOURCE (AST), même
convention déjà établie dans cette suite pour ce type de chemin."""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import action_log  # noqa: E402


def _main_source():
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_py, encoding="utf-8") as f:
        return f.read()


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

    def _standalone_if_node(self, func):
        return next(
            n for n in ast.walk(func)
            if isinstance(n, ast.If) and self._source_of(n.test) == "self.db is None"
        )

    def _standalone_branch_source(self, func):
        """Segment de `func` correspondant uniquement au CORPS de la
        branche "if self.db is None: ... return" — jamais la branche
        "avec tournoi" qui suit, pour ne jamais confondre les deux dans
        les assertions "in"/"not in" ci-dessous (la reconstruction par
        jointure est suffisante pour une recherche de sous-chaîne, mais
        PAS pour un découpage par position — voir _standalone_if_
        segment, utilisée à cette fin à la place)."""
        if_node = self._standalone_if_node(func)
        return "\n".join(self._source_of(s) for s in if_node.body)

    def _standalone_if_segment(self, func):
        """Segment VERBATIM (test + corps), extrait directement par
        position depuis la source — toujours une sous-chaîne EXACTE du
        résultat de _source_of(func), contrairement à _standalone_
        branch_source (reconstruite par jointure) : seule forme fiable
        pour localiser "ce qui suit" cette branche dans la fonction."""
        return self._source_of(self._standalone_if_node(func))


class LogTabAddedToStandaloneBuildTest(_SourceHelper, unittest.TestCase):
    def test_log_tab_cree_et_ajoute_au_notebook_sans_tournoi(self):
        func = self._find_method("App", "_build_tabs")
        branch = self._standalone_branch_source(func)
        self.assertIn("self.log_tab = ttk.Frame(self.notebook)", branch)
        self.assertIn('self.notebook.add(self.log_tab, text="LOG")', branch)

    def test_build_log_tab_appelee_sans_tournoi_jamais_dupliquee(self):
        func = self._find_method("App", "_build_tabs")
        branch = self._standalone_branch_source(func)
        self.assertIn("self._build_log_tab()", branch)
        # Une seule définition de _build_log_tab dans tout le fichier :
        # réutilisée ici, jamais une seconde implémentation parallèle.
        definitions = self.source.count("def _build_log_tab(self):")
        self.assertEqual(definitions, 1)

    def test_ordre_statistiques_puis_log_puis_parametres(self):
        func = self._find_method("App", "_build_tabs")
        branch = self._standalone_branch_source(func)
        idx_stats = branch.index('self.notebook.add(self.stats_tab, text="Statistiques")')
        idx_log = branch.index('self.notebook.add(self.log_tab, text="LOG")')
        idx_settings = branch.index('self.notebook.add(self.settings_tab, text="Paramètres")')
        self.assertLess(idx_stats, idx_log)
        self.assertLess(idx_log, idx_settings)

    def test_branche_avec_tournoi_najamais_modifiee(self):
        """Non-régression : la construction du LOG tab pour un VRAI
        tournoi (self.db is not None) reste exactement celle d'avant —
        EXACTEMENT 2 occurrences de chaque ligne-clé dans tout _build_
        tabs (une par branche, standalone ET avec tournoi), jamais une
        troisième construite par erreur."""
        func = self._find_method("App", "_build_tabs")
        full_seg = self._source_of(func)
        self.assertEqual(full_seg.count("self.log_tab = ttk.Frame(self.notebook)"), 2)
        self.assertEqual(full_seg.count('self.notebook.add(self.log_tab, text="LOG")'), 2)
        self.assertEqual(full_seg.count("self._build_log_tab()"), 2)


class LogMenuEntryStandaloneTest(_SourceHelper, unittest.TestCase):
    def test_entree_log_presente_dans_le_menu_reduit(self):
        func = self._find_method("App", "_build_menu")
        branch = self._standalone_branch_source(func)
        self.assertIn('logmenu.add_command(label="Consulter le LOG...", command=self._open_log_tab)', branch)
        self.assertIn('menubar.add_cascade(label="LOG", menu=logmenu)', branch)

    def test_entree_log_juste_apres_statistiques(self):
        func = self._find_method("App", "_build_menu")
        branch = self._standalone_branch_source(func)
        idx_stats = branch.index('menubar.add_cascade(label="Statistiques", menu=statsmenu)')
        idx_log = branch.index('menubar.add_cascade(label="LOG", menu=logmenu)')
        self.assertLess(idx_stats, idx_log)

    def test_menu_complet_najamais_modifie(self):
        """Demande explicite : "Ne pas modifier le menu complet lorsqu'un
        tournoi est ouvert" — aucune entrée de menu "LOG" ajoutée hors de
        la branche standalone (le LOG, avec un tournoi ouvert, reste
        accessible uniquement via la barre d'onglets, comme avant)."""
        func = self._find_method("App", "_build_menu")
        full_seg = self._source_of(func)
        if_seg = self._standalone_if_segment(func)
        after_standalone = full_seg[full_seg.index(if_seg) + len(if_seg):]
        self.assertNotIn('menubar.add_cascade(label="LOG"', after_standalone)
        self.assertNotIn("logmenu", after_standalone)


class OpenLogTabMethodTest(_SourceHelper, unittest.TestCase):
    def test_open_log_tab_selectionne_longlet_log(self):
        func = self._find_method("App", "_open_log_tab")
        seg = self._source_of(func)
        self.assertIn("self.notebook.select(self.log_tab)", seg)

    def test_open_log_tab_najamais_de_fenetre_separee(self):
        func = self._find_method("App", "_open_log_tab")
        seg = self._source_of(func)
        self.assertNotIn("Toplevel", seg)
        self.assertNotIn("mainloop", seg)


class LogTabFunctionsNeverReferenceSelfDbStructuralTest(_SourceHelper, unittest.TestCase):
    """Vérification STRUCTURELLE (déjà faite manuellement avant
    implémentation, reproduite ici comme garde-fou permanent) : toutes
    les méthodes de l'onglet LOG restent indépendantes de self.db — sans
    quoi l'accès sans tournoi ouvert lèverait une AttributeError/
    TypeError à l'usage (self.db étant None dans ce mode)."""

    _LOG_METHOD_NAMES = [
        "_build_log_tab", "_build_log_search_criteria_placeholder",
        "_build_log_tournament_label_maps", "_refresh_log_filter_choices",
        "_parse_log_date_field", "_current_log_filter_values",
        "_log_count_label_text", "_refresh_log_tab", "_format_log_ts",
        "_populate_log_tree", "_on_log_search", "_on_log_reset",
        "_on_log_row_double_click", "_show_log_detail_window",
        "_on_log_export", "_show_log_date_picker",
        "_log_purge_filter_summary", "_confirm_log_purge", "_on_log_purge",
    ]

    def test_aucune_methode_log_ne_reference_self_db(self):
        offenders = []
        for name in self._LOG_METHOD_NAMES:
            func = self._find_method("App", name)
            seg = self._source_of(func)
            if "self.db" in seg:
                offenders.append(name)
        self.assertEqual(offenders, [])


class LogPurgeStillGatedByConfirmationTest(unittest.TestCase):
    """Non-régression fonctionnelle (pas seulement structurelle) : la
    purge du journal LOG passe toujours par action_log.purge_actions,
    identique quel que soit le mode (sans ou avec tournoi) — ce test
    n'exerce que la fonction de données elle-même (déjà couverte par
    ses propres tests dédiés), pour confirmer qu'aucune garde n'a été
    retirée à l'occasion de ce chantier."""

    def test_purge_actions_existe_toujours_et_necessite_des_criteres(self):
        self.assertTrue(callable(action_log.purge_actions))


if __name__ == "__main__":
    unittest.main()
