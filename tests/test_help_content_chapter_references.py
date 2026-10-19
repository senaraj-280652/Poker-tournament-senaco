# -*- coding: utf-8 -*-
"""Cohérence entre le manuel utilisateur (MANUEL_UTILISATEUR_TOURNOI_CPC.docx),
l'aide intégrée générée (help_content.json) et les références de chapitre en
dur dans le code (main.py: chapter_title=..., help_browser.py:
TAB_TO_CHAPTER) — demande du 2026-10-19, suite à une anomalie réelle :
une renumérotation du manuel (insertion d'un nouveau chapitre) avait rendu
plusieurs raccourcis F1 silencieusement incorrects (aucune exception, aucun
message — HelpBrowser.show() retombe simplement sur le premier chapitre si
le titre demandé n'existe plus, voir help_browser.py), restés inaperçus
faute de tout test sur cette zone.

Deux catégories de régression couvertes séparément :
1. help_content.json jamais régénéré après une mise à jour du manuel
   (oubli de `python3 extract_help_content.py`) — TestHelpContentUpToDate.
2. Une référence chapter_title="..." (main.py) ou une valeur de
   TAB_TO_CHAPTER (help_browser.py) qui ne correspond plus à AUCUN titre
   réel du manuel, même si help_content.json est lui-même à jour (faute de
   frappe, renumérotation oubliée à un seul endroit...) —
   TestChapterReferencesResolve.

Ne modifie jamais MANUEL_UTILISATEUR_TOURNOI_CPC.docx ni help_content.json
(uniquement lus) — docx lu en lecture seule."""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest

import docx  # noqa: E402

import extract_help_content  # noqa: E402
import help_browser  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DOCX_PATH = os.path.join(_REPO_ROOT, "MANUEL_UTILISATEUR_TOURNOI_CPC.docx")
_JSON_PATH = os.path.join(_REPO_ROOT, "help_content.json")
_MAIN_PATH = os.path.join(_REPO_ROOT, "main.py")


def _load_committed_help_content():
    with open(_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def _extract_from_current_docx():
    document = docx.Document(_DOCX_PATH)
    return extract_help_content.extract(document)


def _all_hardcoded_chapter_title_refs():
    """Toutes les chaînes chapter_title="..." codées en dur dans main.py
    (jamais la variable `chapter` de App._show_help, qui vient déjà de
    TAB_TO_CHAPTER — testé séparément)."""
    with open(_MAIN_PATH, encoding="utf-8") as f:
        src = f.read()
    return set(re.findall(r'chapter_title="([^"]+)"', src))


@unittest.skipUnless(os.path.isfile(_DOCX_PATH), "manuel introuvable dans cet environnement")
class HelpContentUpToDateTest(unittest.TestCase):
    """help_content.json doit être EXACTEMENT ce que produirait
    `python3 extract_help_content.py` à partir du .docx actuel — sinon
    l'aide F1 affiche un contenu périmé sans que rien ne le signale (bug
    réel constaté le 2026-10-19 : resté périmé depuis la v1.2.43, soit
    plusieurs chantiers entiers de mise à jour du manuel jamais reflétés
    dans l'application)."""

    @classmethod
    def setUpClass(cls):
        cls.committed = _load_committed_help_content()
        cls.fresh = _extract_from_current_docx()

    def test_memes_titres_dans_le_meme_ordre(self):
        self.assertEqual(
            [e["title"] for e in self.fresh],
            [e["title"] for e in self.committed],
            "help_content.json n'est plus synchronisé avec le manuel — "
            "relancez `python3 extract_help_content.py` après toute mise à jour du .docx.",
        )

    def test_contenu_identique_entree_par_entree(self):
        self.assertEqual(self.fresh, self.committed)


class ChapterReferencesResolveTest(unittest.TestCase):
    """Chaque référence de chapitre codée en dur (main.py ou
    help_browser.TAB_TO_CHAPTER) doit correspondre EXACTEMENT (même
    numéro, même texte) à un titre réellement présent dans
    help_content.json — sinon HelpBrowser.show() retombe silencieusement
    sur le premier chapitre au lieu du bon, sans aucune erreur visible."""

    @classmethod
    def setUpClass(cls):
        cls.titles = {e["title"] for e in _load_committed_help_content()}

    def test_toutes_les_references_chapter_title_en_dur_existent(self):
        refs = _all_hardcoded_chapter_title_refs()
        self.assertGreater(len(refs), 0, "aucune référence trouvée — le motif de recherche a-t-il changé ?")
        manquantes = sorted(r for r in refs if r not in self.titles)
        self.assertEqual(
            manquantes, [],
            f"référence(s) chapter_title= introuvable(s) dans help_content.json : {manquantes}",
        )

    def test_toutes_les_valeurs_de_tab_to_chapter_existent(self):
        manquantes = sorted(
            v for v in help_browser.TAB_TO_CHAPTER.values() if v not in self.titles
        )
        self.assertEqual(
            manquantes, [],
            f"valeur(s) de TAB_TO_CHAPTER introuvable(s) dans help_content.json : {manquantes}",
        )

    # ------------------------------------------------------------------
    # Les 4 raccourcis F1 concrets explicitement redemandés (2026-10-19) :
    # Statistiques, Répertoire, Sit & Go (Lobby), Chronomètre/projecteur —
    # chacun des 4 avait un titre incorrect avant ce correctif.
    # ------------------------------------------------------------------
    def test_f1_contextuel_statistiques(self):
        self.assertEqual(
            help_browser.TAB_TO_CHAPTER["Statistiques"], "16. Statistiques — Synthèse par période",
        )
        self.assertIn(help_browser.TAB_TO_CHAPTER["Statistiques"], self.titles)

    def test_f1_contextuel_repertoire(self):
        self.assertEqual(
            help_browser.TAB_TO_CHAPTER["Répertoire"], "17. Menu Répertoire — joueurs et photos",
        )
        self.assertIn(help_browser.TAB_TO_CHAPTER["Répertoire"], self.titles)

    def test_f1_contextuel_sit_and_go_lobby(self):
        refs = _all_hardcoded_chapter_title_refs()
        self.assertIn("15. Sit & Go et gestion de plusieurs tournois", refs)
        self.assertIn("15. Sit & Go et gestion de plusieurs tournois", self.titles)

    def test_f1_contextuel_chronometre_projecteur(self):
        refs = _all_hardcoded_chapter_title_refs()
        self.assertIn("10. Onglet Chronomètre", refs)
        self.assertIn("10. Onglet Chronomètre", self.titles)
        # L'ancienne référence fautive ("9. Onglet Chronomètre", qui
        # désignait en réalité l'onglet Blindes) ne doit plus apparaître.
        with open(_MAIN_PATH, encoding="utf-8") as f:
            main_src = f.read()
        self.assertNotIn('chapter_title="9. Onglet Chronomètre"', main_src)


if __name__ == "__main__":
    unittest.main()
