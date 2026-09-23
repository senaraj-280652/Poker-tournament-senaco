"""Vérifie que la police du label Blindes/Ante de l'écran projecteur
(ClockWindow.blinds_lbl, voir clock_window.py) a bien été doublée
(46 -> 92pt), sans toucher aux autres tailles de police de cet écran
(chrono, "Round X", joueurs restants...)."""
import os
import sys
import unittest

import tkinter as tk
import tkinter.font as tkfont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import clock_window  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class BlindsAnteFontDoubledTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : détruit self.win puis self.root (déduplique
        # si déjà détruit par la cascade), les déréférence, puis force
        # gc.collect() sur le thread principal — clock_window.py:242-243
        # (ClockWindow se lie à ses propres méthodes) forme un cycle que
        # seul le ramasse-miettes cyclique peut réclamer. Appelé
        # INCONDITIONNELLEMENT ici (tearDown s'exécute toujours, même si
        # une assertion du test a échoué) — corrige l'ancien win.destroy()
        # explicite en fin de méthode, qui lui était sauté sur ce
        # chemin d'échec (voir le diagnostic livré séparément).
        cleanup_tk(self, "root", "win")

    def test_blinds_lbl_font_size_is_92(self):
        # app=None : ClockWindow ne lit jamais self.app dans __init__ (ni
        # ailleurs dans ce module), aucune fenêtre/base de données réelle
        # n'est donc nécessaire pour vérifier une simple police de label.
        # self.win (jamais une variable locale) : garantit que tearDown
        # (toujours exécuté, y compris si une assertion échoue ci-dessous)
        # peut le détruire — voir le commentaire de tearDown.
        self.win = clock_window.ClockWindow(self.root, app=None)
        self.win.withdraw()
        font = tkfont.Font(font=self.win.blinds_lbl.cget("font"))
        self.assertEqual(font.actual("size"), 92)
        # Round X (level_lbl) reste inchangé : preuve que seul le label
        # Blindes/Ante a été touché, pas tout l'écran projecteur.
        level_font = tkfont.Font(font=self.win.level_lbl.cget("font"))
        self.assertEqual(level_font.actual("size"), 28)


if __name__ == "__main__":
    unittest.main()
