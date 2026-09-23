# -*- coding: utf-8 -*-
"""Correctif du défilement du tableau des mouvements sur l'écran
projecteur (demande du 2026-09-19) : diagnostic confirmé dans le code
(ClockWindow._moves_autoscroll_tick) — au moment où la dernière ligne
devient entièrement visible, l'ancien code faisait IMMÉDIATEMENT
`yview_moveto(0.0)` (retour en haut) AVANT de programmer la pause de 2s
(MOVES_AUTOSCROLL_PAUSE_MS) : la pause avait donc lieu EN HAUT de la
liste, jamais en bas — la dernière ligne n'était visible qu'un seul tick
(~45ms). Un second problème coexistait : le timer de fin de pause
n'était jamais stocké ni annulable, risquant de perturber une
reconstruction de liste survenue entre-temps (nouvelle confirmation
individuelle, par exemple).

Correctif : la pause a lieu la dernière ligne PLEINEMENT VISIBLE (aucun
yview_moveto tant que la pause n'est pas écoulée) ; le retour en haut et
la reprise du défilement n'ont lieu QUE dans _moves_resume_autoscroll,
une fois la pause écoulée ; le timer de pause est stocké
(_moves_scroll_pause_after_id) et explicitement annulé par
_update_movement_moves_table si une reconstruction survient pendant
qu'une pause est encore en cours."""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import clock_window  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovesAutoscrollPauseAtBottomTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = clock_window.ClockWindow(self.root, app=None)
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : remplace les deux addCleanup(...destroy)
        # séparés — "after_calls" est inclus car il peut contenir des
        # méthodes liées de self.win (ex. _moves_resume_autoscroll), et
        # self.win.after = _fake_after (plus bas) forme lui-même un cycle
        # self (TestCase) -> self.win -> self.win.after -> self, que
        # seul le gc.collect() de cleanup_tk peut réclamer.
        self.addCleanup(lambda: cleanup_tk(self, "win", "root", "fake_canvas", "after_calls"))
        # withdraw() comme le reste de la suite (voir test_clock_projo_
        # blinds_font.py) : une VRAIE fenêtre projecteur mappée à l'écran
        # dans ce harnais s'est avérée provoquer un crash Tcl/Tk bas
        # niveau (SIGSEGV), sans rapport avec la logique testée ici —
        # winfo_ismapped() est donc doublée ci-dessous plutôt que de
        # dépendre d'un mapping réel.
        self.win.withdraw()

        # Doublure du canvas — seul l'ORDRE des appels (yview/yview_scroll/
        # yview_moveto) est ce qui est testé ici, pas la géométrie réelle.
        self.fake_canvas = MagicMock()
        self.win._moves_canvas = self.fake_canvas
        self.win._moves_needs_scroll = True
        self.win.movement_alert_frame.winfo_ismapped = lambda: True

        # after()/after_cancel() doublés : capture les appels sans jamais
        # programmer de VRAI timer Tk — évite toute dépendance au temps
        # réel et toute fuite de callback après la fin du test (voir la
        # même précaution ailleurs dans cette suite, ex. _cancel_tables_
        # blink).
        self.after_calls = []
        self._next_after_id = 0

        def _fake_after(ms, fn):
            self._next_after_id += 1
            after_id = f"after-{self._next_after_id}"
            self.after_calls.append((after_id, ms, fn))
            return after_id

        self.win.after = _fake_after
        self.win.after_cancel = MagicMock()

    def test_atteindre_le_bas_ne_remonte_pas_immediatement_en_haut(self):
        """Coeur du correctif : la dernière ligne pleinement visible ne
        doit jamais déclencher un retour immédiat en haut."""
        self.fake_canvas.yview.return_value = (0.8, 1.0)
        self.win._moves_autoscroll_tick()

        self.assertTrue(self.win._moves_scroll_paused)
        self.fake_canvas.yview_moveto.assert_not_called()
        self.assertIsNotNone(self.win._moves_scroll_pause_after_id)

    def test_le_retour_en_haut_narrive_quapres_la_pause_ecoulee(self):
        self.fake_canvas.yview.return_value = (0.8, 1.0)
        self.win._moves_autoscroll_tick()
        self.fake_canvas.yview_moveto.assert_not_called()

        self.win._moves_resume_autoscroll()

        self.fake_canvas.yview_moveto.assert_called_once_with(0.0)
        self.assertFalse(self.win._moves_scroll_paused)
        self.assertIsNone(self.win._moves_scroll_pause_after_id)

    def test_pause_programmee_avec_la_duree_configuree(self):
        self.fake_canvas.yview.return_value = (0.8, 1.0)
        self.win._moves_autoscroll_tick()

        pause_calls = [c for c in self.after_calls if c[2] == self.win._moves_resume_autoscroll]
        self.assertEqual(len(pause_calls), 1)
        self.assertEqual(pause_calls[0][1], self.win.MOVES_AUTOSCROLL_PAUSE_MS)

    def test_vitesse_de_defilement_reguliere_pendant_la_montee(self):
        """Pas encore au bas de la liste : défilement d'un pas fixe et
        régulier, jamais de saut."""
        self.fake_canvas.yview.return_value = (0.2, 0.5)
        self.win._moves_autoscroll_tick()

        self.fake_canvas.yview_scroll.assert_called_once_with(
            self.win.MOVES_AUTOSCROLL_STEP_PX, "units"
        )
        self.fake_canvas.yview_moveto.assert_not_called()

    def test_reconstruction_pendant_une_pause_annule_le_timer_perime(self):
        """Une confirmation individuelle de mouvement (ou n'importe quelle
        autre cause de reconstruction) survenant PENDANT la pause de 2s
        ne doit jamais laisser un timer périmé se déclencher plus tard et
        perturber le nouveau cycle de défilement."""
        self.fake_canvas.yview.return_value = (0.8, 1.0)
        self.win._moves_autoscroll_tick()
        pending_id = self.win._moves_scroll_pause_after_id
        self.assertIsNotNone(pending_id)

        self.win._update_movement_moves_table([
            {"player_name": "D", "old_table_name": "Table 4", "old_seat": 1,
             "new_table_name": "Table 2", "new_seat": 7},
        ])

        self.win.after_cancel.assert_called_once_with(pending_id)
        self.assertIsNone(self.win._moves_scroll_pause_after_id)
        self.assertFalse(self.win._moves_scroll_paused)

    def test_reconstruction_sans_pause_en_cours_nappelle_pas_after_cancel(self):
        """Non-régression : pas de pause en cours -> rien à annuler,
        jamais un appel after_cancel(None) ou sur un id périmé."""
        self.win._update_movement_moves_table([
            {"player_name": "D", "old_table_name": "Table 4", "old_seat": 1,
             "new_table_name": "Table 2", "new_seat": 7},
        ])
        self.win.after_cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
