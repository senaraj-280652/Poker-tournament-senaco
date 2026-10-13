# -*- coding: utf-8 -*-
"""Fermeture d'un tournoi (croix rouge de la fenêtre, et "Fin de la
partie" côté iPhone) — demande explicite du 2026-10-14, suite à un essai
réel sur Mac : "tout Senaco disparaît" après la fermeture du dernier
tournoi, obligeant à relancer le logiciel pour en ouvrir un autre.

Comportement attendu, fixé par l'utilisateur :
1. Croix rouge d'un tournoi (App._on_close) : ferme ce tournoi
   normalement (_cleanup_for_close, inchangée — mêmes données
   enregistrées qu'avant), PUIS revient automatiquement à "Senaco sans
   tournoi" dans ce même processus (App(start_standalone=True)) —
   jamais de réouverture si aucun tournoi n'était ouvert (comportement
   d'origine conservé pour fermer Senaco sans tournoi lui-même).
2. "Fin de la partie" depuis l'iPhone (App._remote_end_tournament) :
   appelle EXACTEMENT le même _on_close — même comportement, sans
   perturber les autres tournois (chacun son propre processus) ni le
   Lobby.
3. "Fichier > Quitter" (App._quit_app) : sortie réelle et définitive,
   JAMAIS de réouverture automatique — chemin délibérément distinct de
   _on_close.
4. "🏠 Menu principal" depuis Senaco sans tournoi (App._new_tournament,
   déjà existante) : permet d'ouvrir/créer un autre tournoi sans
   relancer le logiciel — non re-testé ici (couverture déjà existante,
   voir tests/test_ouvrir_senaco_sans_tournoi.py).
5. Plusieurs tournois ouverts (Lobby) : chacun son propre processus,
   _acquire_menu_principal_lock_if_needed (réutilisée telle quelle par
   start_standalone=True) garantit qu'une seule fenêtre "sans tournoi"
   existe à la fois sur ce poste — jamais de fenêtre dupliquée.

ATTENTION (voir tests/test_align_primes_enabled_on_open.py, docstring de
WiringStructurelTest, et tests/test_bienvenue_window_height.py) :
App.__init__ construit une vraie fenêtre Tk complète — trop coûteux/
fragile à instancier ici, et _on_close (chemin "avec tournoi") appelle
RÉELLEMENT App(...).mainloop() en production. Ce fichier vérifie donc le
câblage par analyse du CODE SOURCE (AST), jamais en exécutant App.
__init__/_on_close/_quit_app pour de vrai — même convention déjà établie
dans cette suite pour ce type de chemin."""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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


class AppInitStandaloneParamTest(_SourceHelper, unittest.TestCase):
    def test_signature_accepte_start_standalone(self):
        func = self._find_method("App", "__init__")
        arg_names = [a.arg for a in func.args.args]
        self.assertIn("start_standalone", arg_names)
        defaults_by_name = dict(zip(arg_names[-len(func.args.defaults):], func.args.defaults))
        self.assertIn("start_standalone", defaults_by_name)
        self.assertIs(defaults_by_name["start_standalone"].value, False)

    def test_start_standalone_saute_choose_tournament_file(self):
        """Avec start_standalone=True : self.db reste None, _choose_
        tournament_file() n'est PAS appelée — mais le verrou de Menu
        principal (_acquire_menu_principal_lock_if_needed) l'est TOUJOURS,
        exactement comme pour un lancement normal (garantie contre une
        fenêtre "sans tournoi" dupliquée)."""
        func = self._find_method("App", "__init__")
        seg = self._source_of(func)
        self.assertIn("if not start_standalone and not self._choose_tournament_file():", seg)
        idx_lock = seg.index("self._acquire_menu_principal_lock_if_needed(open_path)")
        idx_standalone_check = seg.index("if not start_standalone and not self._choose_tournament_file():")
        self.assertLess(idx_lock, idx_standalone_check)


class OnCloseStructuralTest(_SourceHelper, unittest.TestCase):
    def test_sans_tournoi_ferme_sans_rouvrir(self):
        """self.db is None (déjà "🖥️ Ouvrir Senaco") : _on_close ne doit
        JAMAIS reconstruire de nouvelle App — rien à quoi revenir."""
        func = self._find_method("App", "_on_close")
        if_node = next(
            n for n in ast.walk(func)
            if isinstance(n, ast.If) and self._source_of(n.test) == "self.db is None"
        )
        branch_seg = "\n".join(self._source_of(s) for s in if_node.body)
        self.assertIn("self._cleanup_for_close()", branch_seg)
        self.assertIn("self.destroy()", branch_seg)
        self.assertNotIn("App(", branch_seg)
        self.assertTrue(any(isinstance(s, ast.Return) for s in if_node.body))

    def test_avec_tournoi_revient_a_senaco_sans_tournoi(self):
        """self.db is not None : après le early-return ci-dessus, _on_
        close doit nettoyer/détruire PUIS reconstruire une App(start_
        standalone=True) dans ce même processus (jamais Bienvenue, jamais
        un second processus)."""
        func = self._find_method("App", "_on_close")
        seg = self._source_of(func)
        idx_after_guard = seg.index("return\n") + len("return\n")
        tail = seg[idx_after_guard:]
        self.assertIn("self._cleanup_for_close()", tail)
        self.assertIn("self.destroy()", tail)
        self.assertIn("App(start_standalone=True).mainloop()", tail)
        self.assertLess(tail.index("self.destroy()"), tail.index("App(start_standalone=True)"))

    def test_app_standalone_gardee_par_was_last_open_tournament(self):
        """Correction du 2026-10-14 (plusieurs tournois ouverts, fermer
        l'un d'eux rouvrait Senaco sans tournoi à tort) : App(start_
        standalone=True) ne doit JAMAIS être appelée sans condition —
        uniquement si self._was_last_open_tournament (capturée par
        _cleanup_for_close juste avant) est vraie."""
        func = self._find_method("App", "_on_close")
        if_was_last = next(
            n for n in ast.walk(func)
            if isinstance(n, ast.If) and self._source_of(n.test) == "was_last"
        )
        branch_seg = "\n".join(self._source_of(s) for s in if_was_last.body)
        self.assertIn("App(start_standalone=True).mainloop()", branch_seg)
        seg = self._source_of(func)
        self.assertIn("was_last = self._was_last_open_tournament", seg)
        # Ordre : _cleanup_for_close() (pose l'attribut) -> capture dans
        # `was_last` -> self.destroy() (prudence : jamais relire un
        # attribut sur une fenêtre déjà détruite) -> `if was_last:`.
        idx_capture = seg.index("was_last = self._was_last_open_tournament")
        idx_cleanup = seg.rindex("self._cleanup_for_close()", 0, idx_capture)
        idx_destroy = seg.index("self.destroy()", idx_capture)
        idx_if_was_last = seg.index("if was_last:")
        self.assertLess(idx_cleanup, idx_capture)
        self.assertLess(idx_capture, idx_destroy)
        self.assertLess(idx_destroy, idx_if_was_last)

    def test_cleanup_for_close_pose_was_last_open_tournament(self):
        func = self._find_method("App", "_cleanup_for_close")
        seg = self._source_of(func)
        self.assertIn("self._was_last_open_tournament = False", seg)
        self.assertIn(
            "self._was_last_open_tournament = open_windows.unregister(self.db.path)", seg,
        )
        # Valeur par défaut (False) posée AVANT la tentative via self.db,
        # pour ne jamais rester indéfinie si self.db est None.
        self.assertLess(
            seg.index("self._was_last_open_tournament = False"),
            seg.index("self._was_last_open_tournament = open_windows.unregister"),
        )

    def test_remote_end_tournament_utilise_toujours_on_close(self):
        """"Fin de la partie" (iPhone) hérite automatiquement du nouveau
        comportement — AUCUN changement requis à _remote_end_tournament
        elle-même, elle continue d'appeler self._on_close()."""
        func = self._find_method("App", "_remote_end_tournament")
        seg = self._source_of(func)
        self.assertIn("self._on_close()", seg)


class QuitAppStructuralTest(_SourceHelper, unittest.TestCase):
    def test_quit_app_existe_et_ne_rouvre_jamais(self):
        func = self._find_method("App", "_quit_app")
        body_stmts = func.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        code_only = "\n".join(self._source_of(s) for s in body_stmts)
        self.assertIn("self._cleanup_for_close()", code_only)
        self.assertIn("self.destroy()", code_only)
        self.assertNotIn("App(", code_only)
        self.assertNotIn("mainloop", code_only)

    def test_les_deux_menus_quitter_appellent_quit_app_jamais_on_close(self):
        """Les deux occurrences de "Fichier > Quitter" (menu réduit sans
        tournoi, et menu complet avec tournoi) doivent toutes les deux
        appeler _quit_app — jamais _on_close, qui rouvrirait Senaco sans
        tournoi au lieu de quitter réellement."""
        func = self._find_method("App", "_build_menu")
        seg = self._source_of(func)
        quitter_commands = [
            line.strip() for line in seg.splitlines() if 'label="Quitter"' in line
        ]
        self.assertEqual(len(quitter_commands), 2, quitter_commands)
        for line in quitter_commands:
            self.assertIn("command=self._quit_app", line)
            self.assertNotIn("command=self._on_close", line)


if __name__ == "__main__":
    unittest.main()
