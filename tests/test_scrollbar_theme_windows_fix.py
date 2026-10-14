# -*- coding: utf-8 -*-
"""Correction du 2026-10-14 — ascenseurs restés sur fond blanc sous
Windows dans "LOG > Purger les données" et le Répertoire principal.

Diagnostic : les 19 ttk.Scrollbar du fichier partagent une SEULE règle
de style globale ("Vertical.TScrollbar", posée une fois dans App.
_apply_theme sous le thème "clam" — le seul qui respecte intégralement
une coloration personnalisée sur Mac ET Windows, "aqua"/"vista" redes-
sinant l'ascenseur nativement sinon). Cette règle ne précisait pas
darkcolor/lightcolor/gripcount, nécessaires à "clam" pour calculer
correctement le relief 3D du curseur — absents, Tk retombe sur une
valeur de repli propre à la plateforme, restée blanche sous Windows.

Couvre :
1. La règle "Vertical.TScrollbar" complétée (darkcolor/lightcolor/
   gripcount/relief) SANS modifier les 4 propriétés déjà existantes
   (background/troughcolor/arrowcolor/bordercolor INCHANGÉS — demande
   explicite de préserver bordercolor en particulier).
2. La vérification explicite que "clam" est bien le thème actif après
   la tentative (jamais supposé seulement parce qu'aucune exception
   n'a été levée).
3. La journalisation DISCRÈTE (crash.log, JAMAIS de messagebox) si un
   AUTRE thème est actif.
4. Aucune autre règle de style/dimension d'ascenseur affectée.

_apply_theme ne lit/écrit que des constantes de couleur MODULE-LEVEL et
un ttk.Style(self) — testable directement sur un vrai tk.Tk() (jamais
besoin d'une App() complète, voir tests/test_menu_principal_singleton.py
pour le même principe de méthode extraite testable isolément)."""
import os
import sys
import tempfile
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


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ScrollbarStyleCompletedTest(unittest.TestCase):
    """App._apply_theme appelée directement sur un vrai tk.Tk() — elle
    ne dépend d'aucun autre attribut de App (vérifié par lecture du
    code source), donc testable sans la moindre doublure."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        main.App._apply_theme(cls.root)
        cls.style = ttk.Style(cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def _lookup(self, option):
        return self.style.lookup("Vertical.TScrollbar", option)

    def test_proprietes_existantes_preservees(self):
        """Demande explicite : background/troughcolor/arrowcolor/
        bordercolor ne doivent JAMAIS changer de valeur — bordercolor
        en particulier, sans justification technique précise."""
        self.assertEqual(self._lookup("background"), main.FELT_LIGHT)
        self.assertEqual(self._lookup("troughcolor"), main.FELT_DARK)
        self.assertEqual(self._lookup("arrowcolor"), main.GOLD)
        self.assertEqual(self._lookup("bordercolor"), main.FELT_DARK)

    def test_darkcolor_lightcolor_ajoutes_depuis_la_meme_palette(self):
        self.assertEqual(self._lookup("darkcolor"), main.FELT_DARK)
        self.assertEqual(self._lookup("lightcolor"), main.FELT_LIGHT)

    def test_gripcount_desactive(self):
        self.assertEqual(str(self._lookup("gripcount")), "0")

    def test_relief_plat_coherent_avec_le_reste_du_theme(self):
        self.assertEqual(self._lookup("relief"), "flat")

    def test_theme_actif_est_bien_clam(self):
        """Sur ce poste de développement (et sur l'immense majorité des
        postes), "clam" est disponible et doit être le thème RÉELLEMENT
        actif après _apply_theme — pas seulement accepté sans erreur."""
        self.assertEqual(self.style.theme_use(), "clam")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ThemeFallbackLoggingTest(unittest.TestCase):
    """Vérifie que _log_theme_fallback (jamais une fenêtre d'erreur)
    est appelée UNIQUEMENT si "clam" n'est PAS le thème réellement actif
    après la tentative — et jamais sinon (cas normal)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def test_jamais_appelee_si_clam_reellement_actif(self):
        with patch.object(main, "_log_theme_fallback") as mock_log:
            main.App._apply_theme(self.root)
        mock_log.assert_not_called()

    def test_appelee_avec_le_theme_reel_si_clam_indisponible(self):
        """Simule un poste où le thème réellement actif après la
        tentative n'est PAS "clam" (ex. "vista" sous Windows) — vérifie
        que la journalisation est bien déclenchée, avec le NOM RÉEL du
        thème actif (jamais une valeur supposée/codée en dur)."""
        original_theme_use = ttk.Style.theme_use

        def fake_theme_use(style_self, theme_name=None):
            if theme_name is not None:
                return original_theme_use(style_self, theme_name)
            return "vista"  # simule un repli natif Windows

        with patch.object(ttk.Style, "theme_use", fake_theme_use), \
             patch.object(main, "_log_theme_fallback") as mock_log:
            main.App._apply_theme(self.root)
        mock_log.assert_called_once_with("vista")

    def test_log_theme_fallback_ecrit_dans_crash_log_isole_jamais_de_messagebox(self):
        with tempfile.TemporaryDirectory(prefix="theme_fallback_log_test_") as tmp:
            log_path = os.path.join(tmp, "crash.log")
            with patch.object(main, "_crash_log_path", return_value=log_path), \
                 patch.object(main, "messagebox") as mock_messagebox:
                main._log_theme_fallback("vista")
            mock_messagebox.showerror.assert_not_called()
            mock_messagebox.showwarning.assert_not_called()
            mock_messagebox.showinfo.assert_not_called()
            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("vista", content)
            self.assertIn("clam", content)

    def test_log_theme_fallback_ne_leve_jamais_si_fichier_illisible(self):
        """Robustesse : une erreur d'écriture (dossier inexistant,
        permissions...) ne doit jamais remonter — _apply_theme ne doit
        jamais planter le démarrage à cause de cette seule
        journalisation."""
        with patch.object(main, "_crash_log_path", return_value="/chemin/totalement/invalide/crash.log"):
            main._log_theme_fallback("vista")  # ne doit lever aucune exception


class FlaggedDialogsUseTheSameGlobalStyleStructuralTest(unittest.TestCase):
    """Vérification demandée EN PARTICULIER pour "LOG > Purger les
    données" (DataPurgeDialog) et le Répertoire principal
    (RosterManagerDialog) : leurs ascenseurs ne précisent AUCUN style=
    particulier — ils dépendent donc entièrement, et uniquement, de la
    règle globale "Vertical.TScrollbar" corrigée ci-dessus, exactement
    comme tous les autres ascenseurs déjà corrects de l'application."""

    @classmethod
    def setUpClass(cls):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            cls.source = f.read()

    def _class_body(self, class_name):
        start = self.source.index(f"class {class_name}(")
        # Prochaine déclaration de classe de même niveau (colonne 0) après
        # celle-ci — borne de fin fiable, même principe que les tests
        # structurels déjà établis dans cette suite.
        end = self.source.index("\nclass ", start + 1)
        return self.source[start:end]

    def test_repertoire_principal_scrollbar_sans_style_particulier(self):
        body = self._class_body("RosterManagerDialog")
        scrollbar_lines = [
            line for line in body.splitlines() if "ttk.Scrollbar(" in line
        ]
        self.assertEqual(len(scrollbar_lines), 2, scrollbar_lines)
        for line in scrollbar_lines:
            self.assertNotIn("style=", line)

    def test_purger_donnees_scrollbar_sans_style_particulier(self):
        body = self._class_body("DataPurgeDialog")
        scrollbar_lines = [
            line for line in body.splitlines() if "ttk.Scrollbar(" in line
        ]
        self.assertEqual(len(scrollbar_lines), 1, scrollbar_lines)
        self.assertNotIn("style=", scrollbar_lines[0])

    def test_aucun_scrollbar_du_fichier_ne_precise_de_style_particulier(self):
        """Confirme, sur TOUT le fichier, qu'aucun des ttk.Scrollbar ne
        contourne la règle globale — la correction de cette seule règle
        profite donc bien à tous, de façon uniforme."""
        count = 0
        for line in self.source.splitlines():
            if "ttk.Scrollbar(" in line:
                count += 1
                self.assertNotIn("style=", line)
        self.assertGreaterEqual(count, 15)


if __name__ == "__main__":
    unittest.main()
