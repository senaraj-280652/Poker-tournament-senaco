# -*- coding: utf-8 -*-
"""Garde-fous statiques sur le workflow GitHub Actions dédié au build
« TEST » (demande du 2026-09-12) — .github/workflows/build-msi-test.yml.

Lit le fichier YAML réel (analyse texte simple, SANS dépendance à PyYAML
— non déclarée ailleurs dans ce dépôt, voir windows/requirements.txt —
pour ne pas ajouter une dépendance externe à la suite de tests) et
vérifie que les contraintes de sécurité voulues restent respectées dans
le temps, même après une future modification du fichier :

1. déclenchement MANUEL uniquement (workflow_dispatch) — aucun push, tag
   ou branche ne doit jamais déclencher ce workflow ;
2. construit windows/poker_tournament-test.spec puis windows/app-test.wxs
   (jamais les fichiers de PRODUCTION poker_tournament.spec / app.wxs) ;
3. produit et publie PokerTournament-TEST-<numéro de run>.msi (jamais
   PokerTournament-16.msi, le nom de la production) ;
4. AUCUNE injection de LICENSE_SECRET (aucune étape ne le référence) ;
5. AUCUNE publication de Release GitHub (pas de action-gh-release) ;
6. le workflow de PRODUCTION (build-msi.yml) reste, lui, inchangé dans
   ses garanties habituelles (déclenchement sur tag + injection du
   secret de licence + publication en Release) — pour détecter si une
   future modification y ferait fuiter par erreur une caractéristique du
   workflow TEST (ou l'inverse)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEST_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "build-msi-test.yml")
PROD_WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "build-msi.yml")


def _raw(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _code_lines(raw):
    """Lignes du YAML en ignorant les commentaires (ligne dont le
    premier caractère non-blanc est "#") — pour ne jamais confondre une
    référence dans un commentaire explicatif avec une vraie instruction
    exécutée par GitHub Actions."""
    return [line for line in raw.splitlines() if not line.strip().startswith("#")]


def _on_block(raw):
    """Extrait le contenu du bloc "on:" (jusqu'à la prochaine clé de
    premier niveau, ex. "jobs:") — suffisant ici, pas besoin d'un vrai
    parseur YAML pour un fichier aussi simple."""
    m = re.search(r"^on:\n((?:[ \t]+.*\n|\n)*)", raw, re.MULTILINE)
    assert m, "bloc 'on:' introuvable"
    return m.group(1)


class BuildMsiTestWorkflowExistsTest(unittest.TestCase):
    def test_fichier_present(self):
        self.assertTrue(os.path.exists(TEST_WORKFLOW_PATH))


class TriggerManuelUniquementTest(unittest.TestCase):
    def setUp(self):
        self.raw = _raw(TEST_WORKFLOW_PATH)
        self.on_code = _code_lines(_on_block(self.raw))

    def test_workflow_dispatch_present(self):
        self.assertTrue(any("workflow_dispatch:" in l for l in self.on_code))

    def test_aucun_autre_trigger_de_premier_niveau(self):
        """Seule ligne non vide sous "on:" (hors commentaires) :
        "workflow_dispatch:" — un "push:"/"pull_request:"/"schedule:"/...
        ajouté par erreur ferait échouer ce test."""
        non_blank = [l for l in self.on_code if l.strip()]
        self.assertEqual(len(non_blank), 1, non_blank)
        self.assertEqual(non_blank[0].strip(), "workflow_dispatch:")

    def test_pas_de_declenchement_sur_tag(self):
        code = "\n".join(_code_lines(self.raw))
        self.assertNotIn("tags:", code)
        self.assertNotIn('"v*"', code)
        self.assertNotIn("push:", code)


class CheminsSpecEtWxsTest(unittest.TestCase):
    def setUp(self):
        self.code = "\n".join(_code_lines(_raw(TEST_WORKFLOW_PATH)))

    def test_utilise_le_spec_test(self):
        self.assertIn("windows/poker_tournament-test.spec", self.code)

    def test_utilise_le_wxs_test(self):
        self.assertIn("windows/app-test.wxs", self.code)

    def test_n_utilise_pas_le_spec_de_production(self):
        # "poker_tournament.spec" est un suffixe de "poker_tournament-
        # test.spec" : on vérifie l'absence de la commande PyInstaller
        # invoquant EXACTEMENT le fichier de production (sans "-test").
        self.assertNotIn("pyinstaller windows/poker_tournament.spec", self.code)

    def test_n_utilise_pas_le_wxs_de_production(self):
        self.assertNotIn("wix build windows/app.wxs", self.code)


class NomMsiEtArtifactTest(unittest.TestCase):
    """Demande du 2026-09-14 : le .msi et l'artifact sont désormais
    suffixés par ${{ github.run_number }} (numéro de run de CE workflow,
    auto-incrémenté) pour distinguer immédiatement chaque build TEST —
    "si cela peut être fait sans perturber le workflow TEST". Le YAML
    n'est PAS évalué ici (pas de vrai run GitHub Actions dans les
    tests) : on vérifie donc la présence littérale de l'expression."""

    def setUp(self):
        self.code = "\n".join(_code_lines(_raw(TEST_WORKFLOW_PATH)))

    def test_msi_de_sortie_nomme_test_avec_numero_de_run(self):
        self.assertIn(
            "windows/dist/PokerTournament-TEST-${{ github.run_number }}.msi",
            self.code,
        )

    def test_msi_de_sortie_jamais_nomme_comme_la_production(self):
        self.assertNotIn("PokerTournament-16.msi", self.code)

    def test_artifact_upload_present_avec_bon_nom_et_chemin(self):
        self.assertIn("actions/upload-artifact@v4", self.code)
        # Le bloc "with:" de l'étape upload-artifact doit contenir name
        # et path corrects — recherche du bloc qui suit upload-artifact.
        idx = self.code.index("actions/upload-artifact@v4")
        following = self.code[idx:idx + 400]
        self.assertIn(
            "name: PokerTournament-TEST-${{ github.run_number }}", following
        )
        self.assertIn(
            "path: windows/dist/PokerTournament-TEST-${{ github.run_number }}.msi",
            following,
        )

    def test_un_seul_upload_artifact(self):
        self.assertEqual(self.code.count("actions/upload-artifact@"), 1)


class NumeroDeBuildTestTest(unittest.TestCase):
    """Demande du 2026-09-14 : le workflow écrit windows/assets/
    TEST_BUILD_NUMBER avec ${{ github.run_number }} AVANT PyInstaller,
    pour que main.py._test_build_number() (embarqué via windows/
    poker_tournament-test.spec) puisse l'exposer dans "À propos" et le
    titre de fenêtre — voir tests/test_test_build_marker.py pour la
    couverture de cette lecture côté application."""

    def setUp(self):
        self.raw = _raw(TEST_WORKFLOW_PATH)
        self.code = "\n".join(_code_lines(self.raw))

    def test_ecrit_test_build_number_avec_le_numero_de_run(self):
        self.assertIn("windows/assets/TEST_BUILD_NUMBER", self.code)
        self.assertIn(
            'Value "${{ github.run_number }}"',
            self.code,
        )

    def test_ecriture_du_numero_precede_pyinstaller(self):
        idx_number = self.code.index("windows/assets/TEST_BUILD_NUMBER")
        idx_pyinstaller = self.code.index("pyinstaller windows/poker_tournament-test.spec")
        self.assertLess(
            idx_number,
            idx_pyinstaller,
            "TEST_BUILD_NUMBER doit être écrit AVANT l'appel à PyInstaller "
            "pour être embarqué dans le .exe",
        )


class AucuneInjectionLicenceTest(unittest.TestCase):
    def setUp(self):
        self.raw = _raw(TEST_WORKFLOW_PATH)
        self.code = "\n".join(_code_lines(self.raw))

    def test_aucune_reference_a_license_secret_hors_commentaire(self):
        """LICENSE_SECRET ne doit apparaître QUE dans des commentaires
        (expliquant son absence volontaire) — jamais dans une ligne de
        code réellement exécutée par le workflow."""
        offending = [l for l in _code_lines(self.raw) if "LICENSE_SECRET" in l]
        self.assertEqual(offending, [])

    def test_aucune_ecriture_de_license_secret_py(self):
        self.assertNotIn("_license_secret.py", self.code)

    def test_aucun_bloc_env_de_step(self):
        """Le workflow de production a un bloc "env:" (LICENSE_SECRET)
        sur son étape d'injection — le workflow TEST ne doit déclarer
        aucun bloc "env:" du tout, n'ayant besoin d'aucun secret."""
        self.assertNotIn("env:", self.code)


class AucuneReleaseGithubTest(unittest.TestCase):
    def setUp(self):
        self.code = "\n".join(_code_lines(_raw(TEST_WORKFLOW_PATH)))

    def test_aucune_etape_action_gh_release(self):
        self.assertNotIn("action-gh-release", self.code)

    def test_aucun_declenchement_conditionne_a_un_tag_ref(self):
        self.assertNotIn("refs/tags", self.code)

    def test_permissions_lecture_seule(self):
        """Pas besoin de "contents: write" (requis par action-gh-release
        dans le workflow de production) puisqu'aucune Release n'est
        publiée ici."""
        self.assertIn("contents: read", self.code)
        self.assertNotIn("contents: write", self.code)


class WorkflowDeProductionInchangeTest(unittest.TestCase):
    """Vérifie que build-msi.yml garde ses garanties HABITUELLES — sert
    de repère : si CE test échoue, c'est que la production a changé (par
    ex. quelqu'un y a retiré l'injection de licence ou le déclenchement
    par tag), pas que le fichier TEST a un problème."""

    def setUp(self):
        self.code = "\n".join(_code_lines(_raw(PROD_WORKFLOW_PATH)))

    def test_production_se_declenche_toujours_sur_tag(self):
        self.assertIn("push:", self.code)
        self.assertIn("tags:", self.code)
        self.assertIn('"v*"', self.code)

    def test_production_injecte_toujours_license_secret(self):
        self.assertIn("LICENSE_SECRET", self.code)

    def test_production_publie_toujours_une_release(self):
        self.assertIn("action-gh-release", self.code)

    def test_production_utilise_toujours_ses_propres_fichiers(self):
        self.assertIn("windows/poker_tournament.spec", self.code)
        self.assertIn("windows/app.wxs", self.code)
        self.assertNotIn("poker_tournament-test.spec", self.code)
        self.assertNotIn("app-test.wxs", self.code)


if __name__ == "__main__":
    unittest.main()
