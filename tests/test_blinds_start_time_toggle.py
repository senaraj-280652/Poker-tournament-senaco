"""Tests ciblés de la bascule "Hr de Début" de l'onglet Blindes (voir
main.py: App._toggle_blinds_start_time / App._refresh_blinds_tab).

Construit une vraie fenêtre Tk (nécessaire : _refresh_blinds_tab crée de
vrais widgets ttk.Label/ttk.Entry dans self.blinds_rows_frame), mais sans
base de données ni App complète — une doublure minimale porte juste les
quelques attributs que ces deux méthodes lisent, et les méthodes réelles
leur sont attachées directement (affectation de classe) pour exécuter le
vrai code, pas une réimplémentation parallèle."""
import os
import sys
import unittest
from datetime import datetime
from unittest.mock import patch

import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _rounds():
    return [
        {"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0},
        {"duration": 20, "sb": 50, "bb": 100, "ante": 0, "pause": 5},
        {"duration": 15, "sb": 75, "bb": 150, "ante": 0, "pause": 0},
    ]


class _BlindsTabHarness:
    # Méthodes réelles de App, attachées telles quelles (pas de copie/
    # réécriture) : on teste le vrai code de production.
    _refresh_blinds_tab = main.App._refresh_blinds_tab
    _toggle_blinds_start_time = main.App._toggle_blinds_start_time

    def __init__(self, root, rounds):
        self.blinds_rows_frame = ttk.Frame(root)
        self.blinds_field_width_var = tk.IntVar(value=10)
        self._blinds_start_now_minutes = None
        self._blind_row_vars = []
        self._rounds = rounds

    def _blind_rounds_from_db(self):
        return self._rounds


def _hr_de_debut_texts(harness):
    """Texte affiché dans la colonne "Hr de Début" (colonne 1) après un
    _refresh_blinds_tab, une valeur par round dans l'ordre (row 1, 2...
    — row 0 est l'en-tête, exclue)."""
    by_row = {}
    for child in harness.blinds_rows_frame.grid_slaves():
        info = child.grid_info()
        if int(info["column"]) == 1 and int(info["row"]) != 0:
            by_row[int(info["row"])] = child.cget("text")
    return [by_row[r] for r in sorted(by_row)]


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class HrDeDebutToggleTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_base_00h00_par_defaut_comme_avant_ce_correctif(self):
        harness = _BlindsTabHarness(self.root, _rounds())
        harness._refresh_blinds_tab()
        self.assertEqual(_hr_de_debut_texts(harness), ["00:00", "00:15", "00:40"])

    def test_premier_clic_utilise_l_heure_reelle_et_recalcule_toute_la_colonne(self):
        harness = _BlindsTabHarness(self.root, _rounds())
        harness._refresh_blinds_tab()
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 6, 14, 32)
            harness._toggle_blinds_start_time()
        self.assertEqual(harness._blinds_start_now_minutes, 14 * 60 + 32)
        self.assertEqual(_hr_de_debut_texts(harness), ["14:32", "14:47", "15:12"])

    def test_premier_clic_gere_le_passage_de_minuit(self):
        harness = _BlindsTabHarness(self.root, _rounds())
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 6, 23, 50)
            harness._toggle_blinds_start_time()
        self.assertEqual(_hr_de_debut_texts(harness), ["23:50", "00:05", "00:30"])

    def test_deuxieme_clic_revient_a_00h00_et_recalcule(self):
        harness = _BlindsTabHarness(self.root, _rounds())
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 6, 10, 0)
            harness._toggle_blinds_start_time()  # 1er clic : heure réelle
            harness._toggle_blinds_start_time()  # 2e clic : bascule -> 00:00
        self.assertIsNone(harness._blinds_start_now_minutes)
        self.assertEqual(_hr_de_debut_texts(harness), ["00:00", "00:15", "00:40"])

    def test_troisieme_clic_revient_a_l_heure_reelle_courante(self):
        """Bascule répétée (pas seulement 2 clics) : chaque clic impair
        recapture l'heure ACTUELLE (pas l'ancienne valeur figée)."""
        harness = _BlindsTabHarness(self.root, _rounds())
        with patch("main.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 9, 6, 10, 0)
            harness._toggle_blinds_start_time()
            harness._toggle_blinds_start_time()
            mock_dt.now.return_value = datetime(2026, 9, 6, 18, 5)
            harness._toggle_blinds_start_time()
        self.assertEqual(harness._blinds_start_now_minutes, 18 * 60 + 5)


if __name__ == "__main__":
    unittest.main()
