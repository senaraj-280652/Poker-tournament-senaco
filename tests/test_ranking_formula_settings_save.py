# -*- coding: utf-8 -*-
"""Tests ciblés de App._collect_and_save_all_settings (main.py) pour le
réglage "Système de points distribués" (ranking_formula, demande du
2026-09-10) :

- un choix réel (none/current/progressive/sitngo_cpc) s'enregistre
  normalement, comme n'importe quel autre réglage ;
- une valeur VIDE ("") — cas du placeholder RANKING_FORMULA_LEGACY_
  PLACEHOLDER affiché tant qu'un ancien tournoi à valeur fixe historique
  (ranking_bonus_points) n'a reçu AUCUN choix explicite — n'est JAMAIS
  écrite dans ranking_formula, pour ne jamais remplacer silencieusement
  cette valeur fixe par une formule à l'occasion d'un "Appliquer" portant
  sur d'autres réglages.

Même précaution anti-instabilité Tcl/Tk que tests/test_rebalance_phase2_
max_seats_change.py : une seule racine Tk par CLASSE de test."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import database  # noqa: E402
import main  # noqa: E402
import tournament_prefs  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RankingFormulaSaveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : 2 méthodes de main.App greffées sur cls.root
        # forment chacune un cycle, réclamé ici par gc.collect().
        cleanup_tk(cls, "root")

    def setUp(self):
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_save_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        prefs_path = os.path.join(self._tmp.name, "last_settings.json")
        patcher = patch.object(tournament_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.win = self.root
        self.win.db = self.db
        self.win._collect_and_save_all_settings = types.MethodType(
            main.App._collect_and_save_all_settings, self.win
        )
        self.win._update_window_title = types.MethodType(main.App._update_window_title, self.win)
        self.win._trigger_movement_alert = MagicMock()
        self.win._check_pending_rebalance = MagicMock()
        self.win.settings_vars = {
            "max_seats_per_table": tk.StringVar(value="9"),
            "min_players_per_table": tk.StringVar(value="4"),
            "ranking_formula": tk.StringVar(value="none"),
        }

    def test_choix_reel_enregistre_normalement(self):
        self.win.settings_vars["ranking_formula"].set("progressive")
        with patch.object(main, "messagebox"):
            self.win._collect_and_save_all_settings()
        self.assertEqual(self.db.get_setting("ranking_formula"), "progressive")

    def test_valeur_vide_placeholder_nest_jamais_ecrite(self):
        # Simule un ancien tournoi à valeur fixe (aucun choix explicite
        # encore fait dans la nouvelle liste) : le widget garde la
        # StringVar interne à "" (voir main.py: initial_internal).
        self.db.set_settings({"ranking_bonus_points": 77})
        self.win.settings_vars["ranking_formula"].set("")

        with patch.object(main, "messagebox"):
            self.win._collect_and_save_all_settings()

        # ranking_formula ne doit PAS avoir été créé dans ce fichier —
        # la valeur fixe historique reste donc seule source de vérité.
        row = self.db.conn.execute(
            "SELECT value FROM settings WHERE key='ranking_formula'"
        ).fetchone()
        self.assertIsNone(row)
        formula, legacy = self.db.resolve_ranking_formula()
        self.assertEqual(legacy, 77)

    def test_autres_reglages_enregistres_meme_si_ranking_formula_vide(self):
        self.db.set_settings({"ranking_bonus_points": 10})
        self.win.settings_vars["ranking_formula"].set("")
        self.win.settings_vars["min_players_per_table"].set("6")

        with patch.object(main, "messagebox"):
            self.win._collect_and_save_all_settings()

        self.assertEqual(self.db.get_setting_int("min_players_per_table"), 6)


if __name__ == "__main__":
    unittest.main()
