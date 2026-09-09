# -*- coding: utf-8 -*-
"""Couverture automatisée du point 1 de la demande du 2026-09-09
(diagnostic du bug de non-propagation de "Calculer les primes" entre
deux fenêtres) : App._tick() enveloppe désormais tout son corps dans un
try/except Exception/finally, pour garantir que self.after(1000,
self._tick) est TOUJOURS reprogrammé — même si une erreur survient
PLUS TÔT dans la même fonction (bandeau d'élimination, contrôle à
distance, rééquilibrage en attente, sélection téléphone...) — au lieu
de laisser mourir silencieusement toute la boucle périodique de cette
fenêtre (primes comprises, placées tout à la fin de _tick()).

Exigences explicitement validées par l'utilisateur, vérifiées ici :
1. l'erreur est journalisée (via _log_exception, jamais un `except: pass`
   qui l'avalerait sans trace) ;
2. le prochain after() est quand même programmé ;
3. la boucle ne meurt pas (un appel ultérieur à _tick(), une fois
   l'incident résolu, fonctionne normalement — y compris la
   synchronisation des primes).

Appelle RÉELLEMENT App._tick() dans son ensemble (pas une fonction
auxiliaire réimplémentée) sur un vrai `tk.Tk()` (racine cachée,
`winfo_exists`/`after` sont donc de VRAIES méthodes Tk, pas des
doublures) auquel les autres attributs nécessaires sont greffés — même
principe que les autres tests de ce fichier qui appellent des méthodes
non liées de App avec une doublure comme `self` (voir tests/test_poll_
voice_queue_stops_after_end_tournament.py)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class TickNeverStopsSchedulingTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

        self._tmp = tempfile.TemporaryDirectory(prefix="tick_robustness_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        # Jamais le vrai ~/.poker_tournament (export_prefs.json /
        # open_windows.json) : _sync_primes_enabled_checkbox appelle
        # _primes_session_locked -> open_windows.primes_session_started,
        # qui lit ces fichiers — même précaution que les autres tests.
        export_prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        lock_path = os.path.join(self._tmp.name, "primes_session_started.json")
        for target in (
            patch.object(export_prefs, "_prefs_path", return_value=export_prefs_path),
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_primes_session_lock_path", return_value=lock_path),
        ):
            self.addCleanup(target.stop)
            target.start()

        # -- Greffe sur la racine RÉELLE des attributs que _tick() lit ou
        # appelle, réduits au strict nécessaire (doublures no-op) — sauf
        # ceux dont ce fichier a explicitement besoin (notebook réel,
        # primes_enabled_var/check réels, db réelle). --------------------
        self.win = self.root  # alias plus lisible dans les assertions
        self.win.notebook = ttk.Notebook(self.root)
        tab = ttk.Frame(self.win.notebook)
        self.win.notebook.add(tab, text="Joueurs")
        self.win.notebook.select(tab)

        self.win.db = self.db
        self.win.clock_window = None
        self.win._elimination_banner_current = None
        import collections
        self.win._elimination_banner_queue = collections.deque()
        self.win._remote_photo_uploaded = False
        self.win.remote_control_server = None
        self.win._tick_after_id = None

        self.win._refresh_clock_tab = lambda: None
        self.win._refresh_moves_tab = lambda: None
        self.win._refresh_players_tab = lambda: None
        self.win._refresh_remote_players_cache = lambda: None
        self.win._maybe_reclaim_default_remote_port = lambda: None
        self.win._check_pending_rebalance = lambda: None
        self.win._check_phone_selected_pid = lambda: None
        self.win._sync_single_tournament_pref_checkbox = lambda: None

        # Case + section Primes RÉELLES (comme dans _build_settings_tab) :
        # nécessaires pour que _sync_primes_enabled_checkbox (appelée en
        # toute fin de _tick, après le point d'injection de l'erreur)
        # fonctionne correctement lors du tick "propre" qui suit.
        self.win.primes_enabled_var = tk.BooleanVar(value=True)
        self.win.primes_enabled_check = ttk.Checkbutton(self.root, text="Calculer les primes")
        self.win._primes_section_widgets = [ttk.Entry(self.root) for _ in range(2)]
        self.win._update_primes_section_state = types.MethodType(
            main.App._update_primes_section_state, self.win
        )
        self.win._sync_primes_enabled_checkbox = types.MethodType(
            main.App._sync_primes_enabled_checkbox, self.win
        )

        # `self._tick` doit être une VRAIE méthode liée (pas juste une
        # fonction) pour que `self.after(1000, self._tick)` soit un
        # callable stable et comparable dans les assertions ci-dessous.
        self.win._tick = types.MethodType(main.App._tick, self.win)

        # `self.after` remplacé par un espion : on ne veut pas vraiment
        # attendre 1 seconde ni dépendre d'une vraie boucle Tk pour
        # vérifier "le prochain tick a bien été programmé" — seulement
        # QUE l'appel a eu lieu, avec les bons arguments.
        self.after_mock = MagicMock(return_value="fake_after_id")
        self.win.after = self.after_mock

    def test_exception_avant_la_synchro_des_primes_est_journalisee_et_n_arrete_pas_la_boucle(self):
        # Point d'injection : _check_phone_selected_pid est appelée bien
        # AVANT la synchronisation des primes dans _tick() (voir main.py),
        # donc une erreur ici reproduit exactement "un incident isolé sur
        # une autre fonctionnalité" du diagnostic du 2026-09-09.
        boom = RuntimeError("incident injecté (contrôle à distance/téléphone), sans rapport avec les primes")
        self.win._check_phone_selected_pid = MagicMock(side_effect=boom)

        with patch.object(main, "_log_exception") as mock_log:
            # NE DOIT PAS lever : l'exception doit rester confinée à
            # l'intérieur de _tick() (voir le try/except), jamais se
            # propager à l'appelant (ici, le "faux" minuteur Tk).
            self.win._tick()

        # 1) L'erreur est journalisée — explicitement, pas un `except:
        #    pass` qui l'aurait avalée sans trace.
        mock_log.assert_called_once()
        logged_exc_type, logged_exc_value, logged_exc_tb = mock_log.call_args[0]
        self.assertIs(logged_exc_type, RuntimeError)
        self.assertIs(logged_exc_value, boom)
        self.assertIsNotNone(logged_exc_tb, "une vraie trace doit accompagner l'erreur journalisée")

        # 2) Le prochain after() est quand même programmé.
        self.after_mock.assert_called_once_with(1000, self.win._tick)
        self.assertEqual(self.win._tick_after_id, "fake_after_id")

    def test_la_boucle_ne_meurt_pas_le_tick_suivant_fonctionne_normalement(self):
        """Après l'incident (voir test précédent), un appel ultérieur à
        _tick() — une fois la cause du problème résolue, comme le ferait
        une vraie exécution périodique ininterrompue — doit fonctionner
        exactement normalement, primes comprises : la boucle "ne meurt
        pas", elle continue de faire son travail au tick suivant."""
        self.win._check_phone_selected_pid = MagicMock(side_effect=RuntimeError("incident transitoire"))
        with patch.object(main, "_log_exception"):
            self.win._tick()
        self.after_mock.assert_called_once()

        # L'incident est résolu (comme s'il ne s'était produit qu'une
        # fois) : le tick suivant doit repasser normalement, JUSQU'AU
        # BOUT — y compris la synchronisation des primes, qui n'avait
        # jamais été atteinte lors du tick précédent.
        self.win._check_phone_selected_pid = MagicMock()  # plus d'erreur
        self.after_mock.reset_mock()

        # Une autre fenêtre a entre-temps décoché "Calculer les primes"
        # (simulé directement via la valeur globale proposée) : ce
        # deuxième tick, propre, doit désormais faire converger CE
        # tournoi vers OFF — la preuve que la boucle continue bel et
        # bien de fonctionner après l'incident, pas seulement qu'elle ne
        # plante pas.
        with patch.object(main, "_primes_enabled_proposed", return_value=False):
            with patch.object(main, "_log_exception") as mock_log_2:
                self.win._tick()
            mock_log_2.assert_not_called()  # tick propre, rien à journaliser

        self.assertEqual(self.db.get_setting("primes_enabled"), "0")
        self.assertFalse(self.win.primes_enabled_var.get())
        self.after_mock.assert_called_once_with(1000, self.win._tick)

    def test_tick_sans_incident_reste_inchange(self):
        """Non-régression : un tick tout à fait normal (aucune exception
        nulle part) continue de fonctionner exactement comme avant —
        after() programmé une seule fois, rien de journalisé."""
        with patch.object(main, "_log_exception") as mock_log:
            self.win._tick()
        mock_log.assert_not_called()
        self.after_mock.assert_called_once_with(1000, self.win._tick)


if __name__ == "__main__":
    unittest.main()
