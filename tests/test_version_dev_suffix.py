# -*- coding: utf-8 -*-
"""Couverture automatisée de l'identification de version en
développement (demande du 2026-09-09) : "Poker Tournament vX.Y.Z-dev
[HASH]" (ou "[HASH*]" si des modifications locales ne sont pas encore
commitées) dans le titre du Menu principal et de chaque fenêtre de
tournoi — jamais en build officielle (PyInstaller/MSI), et JAMAIS besoin
de modifier APP_VERSION à la main pour ça (voir version.py:dev_suffix).

Deux niveaux de test :
- `version.py` (_short_git_hash/_working_tree_dirty/dev_suffix) : logique
  combinatoire testée avec des doublures (rapide, déterministe, aucune
  dépendance à l'état RÉEL du dépôt de ce projet au moment du test —
  voir DevSuffixCombinationLogicTest) PUIS revalidée avec un VRAI dépôt
  Git temporaire (git init/commit/modification réelle — voir
  RealGitRepoDetectionTest), pour prouver que les commandes Git
  elles-mêmes sont correctement interprétées, pas seulement mockées.
- `main.py` (_app_title_prefix/App.__init__/_update_window_title) :
  câblage dans les titres de fenêtre — Menu principal (vérifié par
  inspection de l'AST, comme le reste de la suite pour App.__init__,
  trop coûteux à instancier réellement) et fenêtre de tournoi (doublure
  légère, même principe que tests/test_test_mode_and_mandatory_
  eliminator.py:WindowTitleModeTestIndicatorTest)."""
import ast
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import version  # noqa: E402


def _reset_dev_suffix_cache():
    """dev_suffix() met son résultat en cache au niveau du module (voir
    sa docstring) — sans ce reset explicite avant/après chaque test, un
    premier appel "figerait" la valeur pour tout le reste de CE process
    de test (unittest discover exécute tous les fichiers dans le MÊME
    process)."""
    version._DEV_SUFFIX_CACHE = None


class DevSuffixCombinationLogicTest(unittest.TestCase):
    """dev_suffix() : les 4 combinaisons explicitement demandées, avec
    _short_git_hash/_working_tree_dirty/sys.frozen mockés — indépendant
    de l'état réel du dépôt Git de ce projet au moment du test."""

    def setUp(self):
        _reset_dev_suffix_cache()
        self.addCleanup(_reset_dev_suffix_cache)

    def test_mode_source_repo_propre(self):
        with patch.object(version.sys, "frozen", False, create=True), \
             patch.object(version, "_short_git_hash", return_value="294b4c9"), \
             patch.object(version, "_working_tree_dirty", return_value=False):
            self.assertEqual(version.dev_suffix(), "-dev [294b4c9]")

    def test_mode_source_repo_dirty(self):
        with patch.object(version.sys, "frozen", False, create=True), \
             patch.object(version, "_short_git_hash", return_value="294b4c9"), \
             patch.object(version, "_working_tree_dirty", return_value=True):
            self.assertEqual(version.dev_suffix(), "-dev [294b4c9*]")

    def test_git_indisponible(self):
        """Hash introuvable (git absent, pas un dépôt...) : repli sur
        "-dev" seul, jamais une exception, jamais un titre trompeur."""
        with patch.object(version.sys, "frozen", False, create=True), \
             patch.object(version, "_short_git_hash", return_value=None), \
             patch.object(version, "_working_tree_dirty", return_value=False) as mock_dirty:
            self.assertEqual(version.dev_suffix(), "-dev")
            mock_dirty.assert_not_called()  # inutile d'interroger la propreté sans hash

    def test_mode_frozen_msi(self):
        """Build officielle (PyInstaller) : chaîne vide, JAMAIS d'appel
        à Git — aucune dépendance obligatoire à Git en production."""
        with patch.object(version.sys, "frozen", True, create=True), \
             patch.object(version, "_short_git_hash") as mock_hash, \
             patch.object(version, "_working_tree_dirty") as mock_dirty:
            self.assertEqual(version.dev_suffix(), "")
            mock_hash.assert_not_called()
            mock_dirty.assert_not_called()

    def test_calcule_une_seule_fois_par_process(self):
        """Mis en cache : un second appel ne relance jamais Git."""
        with patch.object(version.sys, "frozen", False, create=True), \
             patch.object(version, "_short_git_hash", return_value="abc1234") as mock_hash, \
             patch.object(version, "_working_tree_dirty", return_value=False) as mock_dirty:
            first = version.dev_suffix()
            second = version.dev_suffix()
        self.assertEqual(first, second)
        mock_hash.assert_called_once()
        mock_dirty.assert_called_once()


class RealGitRepoDetectionTest(unittest.TestCase):
    """_short_git_hash/_working_tree_dirty face à un VRAI dépôt Git
    temporaire (jamais le vrai dépôt de ce projet, dont l'état au moment
    du test n'est pas déterministe) : prouve que les commandes Git
    elles-mêmes (pas seulement la logique de combinaison ci-dessus) sont
    correctement interprétées."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_version_git_test_")
        self.addCleanup(self._tmp.cleanup)
        self.repo = self._tmp.name
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")

    def _git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, capture_output=True, text=True, timeout=10,
        )

    def test_hash_court_et_propre_apres_un_commit(self):
        with open(os.path.join(self.repo, "fichier.txt"), "w") as f:
            f.write("contenu initial\n")
        self._git("add", "fichier.txt")
        commit_result = self._git("commit", "-q", "-m", "premier commit")
        self.assertEqual(commit_result.returncode, 0, commit_result.stderr)

        h = version._short_git_hash(repo_dir=self.repo)
        self.assertIsNotNone(h)
        self.assertRegex(h, r"^[0-9a-f]{4,}$")
        self.assertFalse(version._working_tree_dirty(repo_dir=self.repo))

    def test_dirty_apres_modification_non_commitee(self):
        path = os.path.join(self.repo, "fichier.txt")
        with open(path, "w") as f:
            f.write("contenu initial\n")
        self._git("add", "fichier.txt")
        self._git("commit", "-q", "-m", "premier commit")
        self.assertFalse(version._working_tree_dirty(repo_dir=self.repo))

        with open(path, "a") as f:
            f.write("une modification non commitée\n")
        self.assertTrue(
            version._working_tree_dirty(repo_dir=self.repo),
            "une modification de fichier suivi non commitée doit rendre le dépôt 'dirty'",
        )

    def test_dirty_avec_un_nouveau_fichier_non_suivi(self):
        with open(os.path.join(self.repo, "premier.txt"), "w") as f:
            f.write("x\n")
        self._git("add", "premier.txt")
        self._git("commit", "-q", "-m", "premier commit")
        self.assertFalse(version._working_tree_dirty(repo_dir=self.repo))

        with open(os.path.join(self.repo, "nouveau.txt"), "w") as f:
            f.write("nouveau fichier jamais ajouté\n")
        self.assertTrue(
            version._working_tree_dirty(repo_dir=self.repo),
            "un nouveau fichier non suivi doit aussi rendre le dépôt 'dirty'",
        )

    def test_meme_commit_hash_stable_avant_et_apres_modification(self):
        """Le hash HEAD ne change PAS tant qu'aucun commit n'est fait —
        seule _working_tree_dirty distingue les deux états, exactement
        le point demandé par l'utilisateur (294b4c9 vs 294b4c9*)."""
        with open(os.path.join(self.repo, "fichier.txt"), "w") as f:
            f.write("x\n")
        self._git("add", "fichier.txt")
        self._git("commit", "-q", "-m", "premier commit")
        h_before = version._short_git_hash(repo_dir=self.repo)

        with open(os.path.join(self.repo, "fichier.txt"), "a") as f:
            f.write("modifié\n")
        h_after = version._short_git_hash(repo_dir=self.repo)

        self.assertEqual(h_before, h_after)
        self.assertTrue(version._working_tree_dirty(repo_dir=self.repo))

    def test_dossier_qui_n_est_pas_un_depot_git(self):
        """Hors dépôt Git : hash introuvable, jamais d'exception."""
        with tempfile.TemporaryDirectory(prefix="poker_pas_un_depot_") as not_a_repo:
            self.assertIsNone(version._short_git_hash(repo_dir=not_a_repo))
            self.assertFalse(version._working_tree_dirty(repo_dir=not_a_repo))

    def test_git_absent_du_path_ne_plante_pas(self):
        """Simule 'git' introuvable (PATH vide) : repli propre, jamais
        d'exception qui remonterait jusqu'à l'appelant."""
        with patch.object(subprocess, "run", side_effect=FileNotFoundError("git introuvable")):
            self.assertIsNone(version._short_git_hash(repo_dir=self.repo))
            self.assertFalse(version._working_tree_dirty(repo_dir=self.repo))


# =======================================================================
# main.py : câblage dans les titres de fenêtre.
# =======================================================================
class AppTitlePrefixTest(unittest.TestCase):
    def test_combine_nom_version_et_dev_suffix(self):
        with patch.object(main, "dev_suffix", return_value="-dev [294b4c9]"):
            self.assertEqual(
                main._app_title_prefix(),
                f"{main.APP_NAME} v{main.APP_VERSION}-dev [294b4c9]",
            )

    def test_titre_propre_en_build_officielle(self):
        """dev_suffix() vide (build figée) -> aucun résidu, exactement
        "{APP_NAME} v{APP_VERSION}"."""
        with patch.object(main, "dev_suffix", return_value=""):
            self.assertEqual(main._app_title_prefix(), f"{main.APP_NAME} v{main.APP_VERSION}")


class MenuPrincipalTitleWiringTest(unittest.TestCase):
    """App.__init__ construit une vraie fenêtre Tk complète (licence,
    écran d'accueil...) : trop coûteux/fragile à instancier ici — câblage
    vérifié par inspection de l'AST, comme le reste de la suite (voir
    tests/test_align_primes_enabled_on_open.py:WiringStructurelTest)."""

    def test_init_utilise_app_title_prefix_pour_le_titre_initial(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=main_py)
        app_class = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "App")
        init_func = next(
            n for n in ast.walk(app_class) if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        title_calls = [
            n for n in ast.walk(init_func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "title"
        ]
        self.assertTrue(title_calls, "App.__init__ doit fixer un titre initial")
        first_title_call = title_calls[0]
        self.assertEqual(len(first_title_call.args), 1)
        arg = first_title_call.args[0]
        self.assertIsInstance(arg, ast.Call)
        self.assertIsInstance(arg.func, ast.Name)
        self.assertEqual(
            arg.func.id, "_app_title_prefix",
            "le titre initial (Menu principal) doit utiliser _app_title_prefix()",
        )


class _FakeDbForTitle:
    def __init__(self, name, date, path="/tmp/tournoi18.tournoi"):
        self._name = name
        self._date = date
        self.path = path

    def get_setting(self, key, default=None):
        return self._name if key == "tournament_name" else default

    def get_tournament_date(self):
        return self._date


class _FakeAppForTitle:
    """Doublure de App pour _update_window_title (même principe que
    tests/test_test_mode_and_mandatory_eliminator.py:
    _FakeAppForTestMode), avec un self.db RENSEIGNÉ (contrairement à
    cette doublure-là) pour tester le cas "fenêtre de tournoi", pas
    seulement l'écran d'accueil."""

    def __init__(self, db=None, test_mode=False):
        self.db = db
        self.test_mode_var = _FakeVarForTitle(test_mode)
        self.title_calls = []

        class _FakeHeaderLabel:
            def __init__(self):
                self.configured = []

            def config(self, **kwargs):
                self.configured.append(kwargs)

        self.header_title_lbl = _FakeHeaderLabel()

    def title(self, text):
        self.title_calls.append(text)


class _FakeVarForTitle:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class TournamentWindowTitleTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(main, "dev_suffix", return_value="-dev [294b4c9]")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_titre_tournoi_inclut_le_prefixe_version(self):
        db = _FakeDbForTitle("tournoi18", "2026-08-31")
        app = _FakeAppForTitle(db=db, test_mode=False)
        with patch("main.format_date_fr", return_value="31/08/2026"):
            main.App._update_window_title(app)
        self.assertEqual(
            app.title_calls[-1],
            f"{main.APP_NAME} v{main.APP_VERSION}-dev [294b4c9] — Tournoi : tournoi18 du 31/08/2026",
        )

    def test_titre_menu_principal_sans_tournoi(self):
        """self.db est None (écran d'accueil) : uniquement le préfixe,
        sans " — Tournoi : ..."."""
        app = _FakeAppForTitle(db=None, test_mode=False)
        main.App._update_window_title(app)
        self.assertEqual(app.title_calls[-1], f"{main.APP_NAME} v{main.APP_VERSION}-dev [294b4c9]")

    def test_mode_test_plus_titre_tournoi(self):
        """Combinaison explicitement demandée : "🧪 MODE TEST — Poker
        Tournament v1.2.38-dev [294b4c9] — Tournoi : ..." — Mode Test
        reste le préfixe le plus à gauche."""
        db = _FakeDbForTitle("tournoi18", "2026-08-31")
        app = _FakeAppForTitle(db=db, test_mode=True)
        with patch("main.format_date_fr", return_value="31/08/2026"):
            main.App._update_window_title(app)
        self.assertEqual(
            app.title_calls[-1],
            f"🧪 MODE TEST — {main.APP_NAME} v{main.APP_VERSION}-dev [294b4c9] — Tournoi : tournoi18 du 31/08/2026",
        )


if __name__ == "__main__":
    unittest.main()
