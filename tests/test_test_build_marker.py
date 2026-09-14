# -*- coding: utf-8 -*-
"""Tests de la version « TEST » côte à côte avec une installation de
production (demande du 2026-09-12) — voir windows/README.md, section
« Version de TEST ».

Deux niveaux de couverture :
1. `main._is_test_build`/`_app_title_prefix`/`App._show_about` : logique
   d'affichage du marqueur "[TEST]", avec des doublures (aucune
   dépendance à un vrai exécutable compilé).
2. Vérification STATIQUE des fichiers de build eux-mêmes (`windows/
   app.wxs`, `windows/app-test.wxs`, `windows/poker_tournament.spec`,
   `windows/poker_tournament-test.spec`) : garde-fou automatique contre
   une fusion accidentelle qui ferait fuiter TEST_BUILD_MARKER (ou toute
   caractéristique de la ligne TEST) dans le spec/wxs de PRODUCTION, ou
   qui ferait converger par erreur UpgradeCode/dossier d'installation
   des deux lignes (le risque exact identifié dans le diagnostic du
   2026-09-12 : deux installateurs partageant dossier + UpgradeCode
   risquent de partager des composants MSI, donc de s'écraser l'un
   l'autre)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(REPO_ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# =======================================================================
# 1. main.py : détection du marqueur + câblage dans l'affichage.
# =======================================================================
class IsTestBuildTest(unittest.TestCase):
    def test_absent_par_defaut(self):
        """Ni _MEIPASS ni marqueur au dossier des sources (situation de
        CE dépôt de développement, et de tout build de production) :
        False."""
        self.assertFalse(main._is_test_build())

    def test_vrai_si_marqueur_present_dans_meipass(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_test_") as d:
            open(os.path.join(d, "TEST_BUILD_MARKER"), "w").close()
            with patch.object(main.sys, "_MEIPASS", d, create=True):
                self.assertTrue(main._is_test_build())

    def test_faux_si_meipass_sans_marqueur(self):
        """Build PyInstaller de PRODUCTION simulée (_MEIPASS existe, mais
        sans TEST_BUILD_MARKER dedans, exactement comme poker_tournament.
        spec, qui ne le référence jamais dans ses `datas`)."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_prod_") as d:
            with patch.object(main.sys, "_MEIPASS", d, create=True):
                self.assertFalse(main._is_test_build())


class AppTitlePrefixTestMarkerTest(unittest.TestCase):
    def test_prefixe_test_ajoute_quand_build_de_test(self):
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main, "dev_suffix", return_value=""):
            self.assertEqual(
                main._app_title_prefix(),
                f"[TEST] {main.APP_NAME} v{main.APP_VERSION}",
            )

    def test_pas_de_prefixe_test_en_build_normale(self):
        with patch.object(main, "_is_test_build", return_value=False), \
             patch.object(main, "dev_suffix", return_value=""):
            self.assertEqual(
                main._app_title_prefix(),
                f"{main.APP_NAME} v{main.APP_VERSION}",
            )

    def test_cumul_avec_dev_suffix(self):
        """Les deux préfixes peuvent en théorie se cumuler (voir la
        docstring de _app_title_prefix) — "[TEST] " reste devant."""
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main, "dev_suffix", return_value="-dev [abc1234]"):
            self.assertEqual(
                main._app_title_prefix(),
                f"[TEST] {main.APP_NAME} v{main.APP_VERSION}-dev [abc1234]",
            )

    def test_prefixe_test_numerote_quand_numero_de_build_present(self):
        """Demande du 2026-09-14 : chaque build TEST identifiable par un
        numéro (ex. "[TEST 3]") — voir TestBuildNumberTest/TestBuildLabelTest
        ci-dessous pour la couverture de _test_build_number/_label."""
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main, "_test_build_number", return_value="3"), \
             patch.object(main, "dev_suffix", return_value=""):
            self.assertEqual(
                main._app_title_prefix(),
                f"[TEST 3] {main.APP_NAME} v{main.APP_VERSION}",
            )


class TestBuildNumberTest(unittest.TestCase):
    """Demande du 2026-09-14 : numéro identifiant CE build TEST précis,
    lu depuis windows/assets/TEST_BUILD_NUMBER (écrit par le workflow
    GitHub Actions dédié avec ${{ github.run_number }}, voir tests/
    test_build_msi_test_workflow.py) — jamais codé en dur dans main.py."""

    def test_none_si_pas_un_build_test(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.assertIsNone(main._test_build_number())

    def test_none_si_fichier_absent(self):
        """Build TEST manuel/local sans passer par le workflow dédié :
        TEST_BUILD_MARKER présent (donc _is_test_build() vrai) mais pas
        TEST_BUILD_NUMBER — ne doit jamais lever, juste renvoyer None."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_test_no_number_") as d:
            open(os.path.join(d, "TEST_BUILD_MARKER"), "w").close()
            with patch.object(main.sys, "_MEIPASS", d, create=True), \
                 patch.object(main, "_is_test_build", return_value=True):
                self.assertIsNone(main._test_build_number())

    def test_none_si_fichier_vide(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_test_number_empty_") as d:
            open(os.path.join(d, "TEST_BUILD_NUMBER"), "w").close()
            with patch.object(main.sys, "_MEIPASS", d, create=True), \
                 patch.object(main, "_is_test_build", return_value=True):
                self.assertIsNone(main._test_build_number())

    def test_numero_lu_depuis_le_fichier(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_test_number_") as d:
            with open(os.path.join(d, "TEST_BUILD_NUMBER"), "w", encoding="utf-8") as f:
                f.write("3")
            with patch.object(main.sys, "_MEIPASS", d, create=True), \
                 patch.object(main, "_is_test_build", return_value=True):
                self.assertEqual(main._test_build_number(), "3")

    def test_espaces_et_retour_a_la_ligne_ignores(self):
        """Le workflow écrit sans retour à la ligne (-NoNewline), mais un
        éventuel fichier généré manuellement avec un \\n ne doit pas
        casser l'affichage."""
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_test_number_ws_") as d:
            with open(os.path.join(d, "TEST_BUILD_NUMBER"), "w", encoding="utf-8") as f:
                f.write("  7\n")
            with patch.object(main.sys, "_MEIPASS", d, create=True), \
                 patch.object(main, "_is_test_build", return_value=True):
                self.assertEqual(main._test_build_number(), "7")


class TestBuildLabelTest(unittest.TestCase):
    def test_vide_si_pas_un_build_test(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.assertEqual(main._test_build_label(), "")

    def test_generique_si_pas_de_numero(self):
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main, "_test_build_number", return_value=None):
            self.assertEqual(main._test_build_label(), "[TEST]")

    def test_numerote_si_numero_present(self):
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main, "_test_build_number", return_value="3"):
            self.assertEqual(main._test_build_label(), "[TEST 3]")


class _DummySelfForAbout:
    """N'a besoin d'être QUE le `self` passé à App._show_about (voir son
    corps : uniquement utilisé comme repli de `parent`, jamais accédé
    autrement)."""


class ShowAboutTestMarkerTest(unittest.TestCase):
    def test_about_affiche_test_quand_build_de_test(self):
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main.licensing, "license_info", return_value=None), \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._show_about(_DummySelfForAbout())
        title, message = mock_info.call_args.args[:2]
        self.assertEqual(title, "À propos")
        self.assertTrue(
            message.startswith(f"[TEST] {main.APP_NAME}"),
            message,
        )

    def test_about_affiche_test_numerote(self):
        with patch.object(main, "_is_test_build", return_value=True), \
             patch.object(main, "_test_build_number", return_value="3"), \
             patch.object(main.licensing, "license_info", return_value=None), \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._show_about(_DummySelfForAbout())
        _title, message = mock_info.call_args.args[:2]
        self.assertTrue(
            message.startswith(f"[TEST 3] {main.APP_NAME}"),
            message,
        )

    def test_about_normal_sans_marqueur(self):
        with patch.object(main, "_is_test_build", return_value=False), \
             patch.object(main.licensing, "license_info", return_value=None), \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._show_about(_DummySelfForAbout())
        _title, message = mock_info.call_args.args[:2]
        self.assertTrue(message.startswith(main.APP_NAME), message)
        self.assertNotIn("[TEST]", message)


# =======================================================================
# 2. Vérification statique des fichiers de build (wxs/spec).
# =======================================================================
class BuildFilesIsolationTest(unittest.TestCase):
    """Ces tests lisent les VRAIS fichiers du dépôt (pas des doublures) —
    ils échoueront si quelqu'un modifie l'un des 4 fichiers d'une façon
    qui réintroduirait le risque de cohabitation identifié le
    2026-09-12."""

    def setUp(self):
        self.prod_wxs = _read("windows", "app.wxs")
        self.test_wxs = _read("windows", "app-test.wxs")
        self.prod_spec = _read("windows", "poker_tournament.spec")
        self.test_spec = _read("windows", "poker_tournament-test.spec")

    def test_upgrade_codes_differents(self):
        import re
        prod_code = re.search(r'UpgradeCode="([0-9A-F-]+)"', self.prod_wxs).group(1)
        test_code = re.search(r'UpgradeCode="([0-9A-F-]+)"', self.test_wxs).group(1)
        self.assertNotEqual(prod_code, test_code)
        # Toujours celui identifié dans le diagnostic du 2026-09-12 —
        # détecte une régression si quelqu'un le change par erreur.
        self.assertEqual(prod_code, "B0D02965-BECE-4162-856C-144E0675F6AE")

    def test_package_names_differents(self):
        import re
        prod_name = re.search(r'<Package Name="([^"]+)"', self.prod_wxs).group(1)
        test_name = re.search(r'<Package Name="([^"]+)"', self.test_wxs).group(1)
        self.assertNotEqual(prod_name, test_name)
        self.assertIn("TEST", test_name)
        self.assertNotIn("TEST", prod_name)

    def test_dossiers_installation_differents(self):
        import re
        prod_dir = re.search(r'<Directory Id="INSTALLFOLDER" Name="([^"]+)"', self.prod_wxs).group(1)
        test_dir = re.search(r'<Directory Id="INSTALLFOLDER" Name="([^"]+)"', self.test_wxs).group(1)
        self.assertNotEqual(prod_dir, test_dir)

    def test_chemins_executable_differents(self):
        """La source de l'exécutable référencée par chaque .wxs ne doit
        JAMAIS pointer vers le même dossier windows/dist/... — sinon les
        deux lignes finiraient par installer le même fichier."""
        import re
        prod_src = re.search(r'Source="([^"]+PokerTournament[^"]*\.exe)"', self.prod_wxs).group(1)
        test_src = re.search(r'Source="([^"]+PokerTournament[^"]*\.exe)"', self.test_wxs).group(1)
        self.assertNotEqual(prod_src, test_src)
        self.assertIn("PokerTournamentTest", test_src)
        self.assertNotIn("PokerTournamentTest", prod_src)

    def test_raccourcis_differents(self):
        prod_shortcuts = set(__import__("re").findall(r'Name="(Poker Senaco[^"]*)"', self.prod_wxs))
        test_shortcuts = set(__import__("re").findall(r'Name="(Poker Senaco[^"]*)"', self.test_wxs))
        self.assertEqual(prod_shortcuts, {"Poker Senaco"})
        self.assertEqual(test_shortcuts, {"Poker Senaco TEST"})

    def test_cle_registre_differente(self):
        self.assertIn(r"Software\PokerTournament", self.prod_wxs)
        self.assertNotIn(r"Software\PokerTournamentTest", self.prod_wxs)
        self.assertIn(r"Software\PokerTournamentTest", self.test_wxs)

    def test_marqueur_test_absent_du_spec_de_production(self):
        """Garde-fou central : si ce test échoue un jour, un build de
        PRODUCTION embarquerait TEST_BUILD_MARKER et afficherait "[TEST]"
        à tort."""
        self.assertNotIn("TEST_BUILD_MARKER", self.prod_spec)

    def test_marqueur_test_present_dans_le_spec_de_test(self):
        self.assertIn("TEST_BUILD_MARKER", self.test_spec)

    def test_numero_test_absent_du_spec_de_production(self):
        """Même garde-fou que pour TEST_BUILD_MARKER, appliqué au
        mécanisme de numérotation (demande du 2026-09-14) : un build de
        PRODUCTION ne doit jamais chercher/embarquer TEST_BUILD_NUMBER."""
        self.assertNotIn("TEST_BUILD_NUMBER", self.prod_spec)

    def test_numero_test_reference_dans_le_spec_de_test(self):
        self.assertIn("TEST_BUILD_NUMBER", self.test_spec)

    def test_noms_pyinstaller_differents(self):
        import re
        prod_names = set(re.findall(r'name="([^"]+)"', self.prod_spec))
        test_names = set(re.findall(r'name="([^"]+)"', self.test_spec))
        self.assertEqual(prod_names, {"PokerTournament"})
        self.assertEqual(test_names, {"PokerTournamentTest"})

    def test_marqueur_fichier_existe_sur_disque(self):
        self.assertTrue(
            os.path.exists(os.path.join(REPO_ROOT, "windows", "assets", "TEST_BUILD_MARKER"))
        )


if __name__ == "__main__":
    unittest.main()
