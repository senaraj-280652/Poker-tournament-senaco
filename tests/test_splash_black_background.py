# -*- coding: utf-8 -*-
"""Fond noir de l'écran de démarrage Windows (demande du 2026-10-12) —
voir windows/assets/splash.png, affiché par le bootloader PyInstaller
natif (Splash(), voir windows/poker_tournament.spec et windows/poker_
tournament-test.spec — les DEUX builds PROD et TEST partagent ce MÊME
fichier image) AVANT même que Python ne démarre : aucun code Python/
Tkinter ne dessine cet écran, le "fond" est purement les pixels de ce
PNG — voir App.__init__ (main.py), qui se contente de le FERMER
(pyi_splash.close()), jamais de le dessiner.

Ce fichier verrouille le résultat de la modification (changement
d'ASSET, pas de code) : fond uniformément noir, logo/texte/bordure dorée
toujours intacts et lisibles — pour qu'une future régénération
accidentelle de cette image ne réintroduise pas le fond vert feutre
d'origine sans que la suite de tests ne le signale.

N'affecte QUE windows/assets/splash.png — jamais macos/poker_tournament.
spec (aucun écran de démarrage sur macOS, confirmé : aucune référence à
Splash() dans ce fichier), ni aucun autre écran/thème de l'application."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

_SPLASH_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "windows", "assets", "splash.png",
)
_MACOS_SPEC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "macos", "poker_tournament.spec",
)


@unittest.skipUnless(_PIL_AVAILABLE, "Pillow indisponible dans cet environnement")
class SplashBackgroundIsBlackTest(unittest.TestCase):
    def setUp(self):
        self.assertTrue(os.path.isfile(_SPLASH_PATH), f"Fichier introuvable : {_SPLASH_PATH}")
        self.im = Image.open(_SPLASH_PATH).convert("RGB")
        self.addCleanup(self.im.close)

    def test_taille_et_format_inchanges(self):
        """Seuls les PIXELS changent — jamais les dimensions (le .spec
        PyInstaller ne les référence pas, mais une taille différente
        serait un signe que ce n'est plus la même image)."""
        self.assertEqual(self.im.size, (480, 300))

    def test_coins_sont_noirs(self):
        """Les 4 coins sont loin du logo/texte centraux — doivent être
        du noir pur après la conversion (ils l'étaient en vert feutre
        sombre avant ce correctif)."""
        w, h = self.im.size
        px = self.im.load()
        # Légèrement à l'intérieur du bord doré (quelques pixels), pas
        # sur le bord lui-même.
        for x, y in ((5, 5), (w - 6, 5), (5, h - 6), (w - 6, h - 6)):
            self.assertEqual(px[x, y], (0, 0, 0), f"Pixel ({x},{y}) non noir : {px[x, y]}")

    def test_fond_majoritairement_noir(self):
        """La majorité des pixels de l'image sont désormais purement
        noirs (l'ancien fond en dégradé vert occupait la plus grande
        partie de l'image) — garde-fou contre une régénération partielle
        ou un oubli du dégradé."""
        w, h = self.im.size
        px = self.im.load()
        black_count = sum(
            1 for y in range(h) for x in range(w) if px[x, y] == (0, 0, 0)
        )
        total = w * h
        self.assertGreater(black_count / total, 0.5)

    def test_logo_et_texte_toujours_presents_et_lisibles(self):
        """Les éléments clairs (boîte du logo, bordure dorée, texte
        blanc/doré) restent intacts — jamais remplacés par du noir.
        Vérifié par un échantillon de pixels clairs connus plutôt qu'une
        comparaison pixel-à-pixel complète (robuste à un realignement
        mineur du rendu)."""
        w, h = self.im.size
        px = self.im.load()
        # Bordure dorée, coin supérieur gauche exact (0,0).
        r, g, b = px[0, 0]
        self.assertGreater(max(r, g, b), 150, "Bordure dorée attendue, pixel trop sombre")
        # Boîte blanche du logo : centre horizontal, environ 1/4 de la hauteur.
        r, g, b = px[w // 2, h // 4]
        self.assertGreater(min(r, g, b), 200, "Boîte du logo attendue blanche à cet endroit")

    def test_au_moins_un_pixel_non_noir_et_non_blanc_pur(self):
        """Confirme la présence du texte doré/tan (ni noir ni blanc pur)
        — ex. le sous-titre "Senaco" ou la ligne de séparation."""
        w, h = self.im.size
        px = self.im.load()
        found_gold = False
        for y in range(h):
            for x in range(w):
                r, g, b = px[x, y]
                if r > 180 and 130 < g < 220 and b < 130:
                    found_gold = True
                    break
            if found_gold:
                break
        self.assertTrue(found_gold, "Aucune couleur dorée/tan trouvée (texte/bordure)")


class MacosSpecUnaffectedTest(unittest.TestCase):
    def test_macos_spec_ne_reference_aucun_splash(self):
        """Confirme (non-régression) qu'aucun écran de démarrage n'existe
        sur macOS — le changement de fond noir est donc purement un
        correctif d'asset Windows, sans contrepartie macOS à maintenir."""
        if not os.path.isfile(_MACOS_SPEC_PATH):
            self.skipTest("macos/poker_tournament.spec introuvable dans cet environnement")
        with open(_MACOS_SPEC_PATH, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("Splash(", content)
        self.assertNotIn("splash.png", content)


if __name__ == "__main__":
    unittest.main()
