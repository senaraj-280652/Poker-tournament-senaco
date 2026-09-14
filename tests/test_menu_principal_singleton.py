# -*- coding: utf-8 -*-
"""Tests de l'unicité du "Menu principal" par ligne prod/test (demande
du 2026-09-14) — un second lancement EXTERNE (raccourci Windows/macOS
re-double-cliqué) ne doit jamais ouvrir une deuxième session
indépendante, PENDANT TOUTE LA DURÉE DE VIE du processus déjà lancé
(pas seulement le temps de l'écran "Bienvenue") — mais ne doit JAMAIS
bloquer :
- les tournois ouverts depuis le Lobby (spawn_app_process([path])) ;
- les fenêtres supplémentaires volontaires ("🏠 Menu principal",
  spawn_app_process(internal_menu_child=True)) ;
- Poker Senaco PROD et Poker Senaco TEST l'un par rapport à l'autre.

Couvre :
1. open_windows.py : register_menu_principal/unregister_menu_principal/
   menu_principal_pid — logique de fichier pure, fichiers réels en
   dossier temporaire (jamais ~/.poker_tournament), auto-nettoyage d'un
   PID mort (récupération après crash).
2. main.py : App._acquire_menu_principal_lock_if_needed — extraite
   d'App.__init__ pour rester testable isolément (même principe que
   _build_ranking_formula_widget) — et App._cleanup_for_close pour la
   libération.
3. main.py : spawn_app_process(internal_menu_child=True) pose bien le
   marqueur d'environnement POKER_TOURNAMENT_INTERNAL_LAUNCH.
4. main.py : _menu_principal_key() sépare bien prod/test.

N'instancie PAS App(tk.Tk) au complet (trop coûteux/fragile) — voir les
doublures ci-dessous, même style que tests/test_tick_never_stops_
scheduling.py (racine Tk réelle minimalement greffée) et tests/
test_primes_multi_process_real_subprocess.py (vrais process séparés
pour un PID garanti mort)."""
import os
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import open_windows  # noqa: E402


def _spawn_and_wait_dead_pid():
    """Renvoie un PID GARANTI mort (un vrai process lancé puis attendu
    jusqu'à sa fin) — plus fiable qu'un grand nombre arbitraire, qui
    pourrait par malchance correspondre à un processus réellement vivant
    sur la machine qui exécute ce test."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    return proc.pid


class MenuPrincipalRegistryTest(unittest.TestCase):
    """open_windows.register_menu_principal/unregister_menu_principal/
    menu_principal_pid — fichiers réels, jamais ~/.poker_tournament."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="menu_principal_registry_test_")
        self.addCleanup(self._tmp.cleanup)

        def _fake_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.json")

        patcher = patch.object(open_windows, "_menu_principal_lock_path", side_effect=_fake_lock_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_aucun_verrou_au_depart(self):
        self.assertIsNone(open_windows.menu_principal_pid("prod"))

    def test_enregistrement_puis_lecture_pid_vivant(self):
        open_windows.register_menu_principal(os.getpid(), "prod")
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

    def test_desenregistrement_libere_le_verrou(self):
        open_windows.register_menu_principal(os.getpid(), "prod")
        open_windows.unregister_menu_principal(os.getpid(), "prod")
        self.assertIsNone(open_windows.menu_principal_pid("prod"))

    def test_desenregistrement_ignore_un_pid_different(self):
        """Précaution anti-course (même principe que unregister() pour
        open_windows.json) : un désenregistrement portant un AUTRE pid
        que celui actuellement enregistré ne doit rien effacer. Utilise
        os.getpid() (garanti vivant, ce process de test lui-même) comme
        pid ENREGISTRÉ, pour ne pas confondre ce test avec l'auto-
        nettoyage d'un pid mort (voir test_pid_mort_auto_nettoye_
        recuperation_apres_crash) : un pid arbitraire non vivant serait
        de toute façon nettoyé par menu_principal_pid, quel que soit le
        comportement testé ici."""
        open_windows.register_menu_principal(os.getpid(), "prod")
        open_windows.unregister_menu_principal(99999999, "prod")
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

    def test_pid_mort_auto_nettoye_recuperation_apres_crash(self):
        dead_pid = _spawn_and_wait_dead_pid()
        open_windows.register_menu_principal(dead_pid, "prod")
        self.assertIsNone(open_windows.menu_principal_pid("prod"))
        # Le fichier lui-même a bien été supprimé (pas seulement ignoré).
        self.assertFalse(os.path.exists(open_windows._menu_principal_lock_path("prod")))

    def test_prod_et_test_totalement_independants(self):
        open_windows.register_menu_principal(os.getpid(), "prod")
        self.assertIsNone(open_windows.menu_principal_pid("test"))
        open_windows.register_menu_principal(os.getpid(), "test")
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())
        self.assertEqual(open_windows.menu_principal_pid("test"), os.getpid())
        open_windows.unregister_menu_principal(os.getpid(), "prod")
        self.assertIsNone(open_windows.menu_principal_pid("prod"))
        self.assertEqual(open_windows.menu_principal_pid("test"), os.getpid(),
                          "libérer la ligne prod ne doit jamais toucher la ligne test")

    def test_fichier_illisible_traite_comme_absent(self):
        path = open_windows._menu_principal_lock_path("prod")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON valide")
        self.assertIsNone(open_windows.menu_principal_pid("prod"))


class _StubWindow:
    """Doublure minimale d'App : seulement ce que lit/appelle
    _acquire_menu_principal_lock_if_needed et _cleanup_for_close, sans
    construire la moindre fenêtre Tk (voir main.py: App._acquire_menu_
    principal_lock_if_needed, extraite précisément pour permettre ceci)."""

    def __init__(self):
        self.after_calls = []
        self.destroyed = False
        self.db = None
        self._holds_menu_principal_lock = False

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return f"after_id_{len(self.after_calls)}"

    def destroy(self):
        self.destroyed = True

    def _stop_remote_control(self):
        pass


@unittest.skipUnless(hasattr(main.App, "_acquire_menu_principal_lock_if_needed"), "méthode absente")
class AcquireMenuPrincipalLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="menu_principal_acquire_test_")
        self.addCleanup(self._tmp.cleanup)

        def _fake_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.json")

        lock_patcher = patch.object(open_windows, "_menu_principal_lock_path", side_effect=_fake_lock_path)
        lock_patcher.start()
        self.addCleanup(lock_patcher.stop)

        # POKER_TOURNAMENT_INTERNAL_LAUNCH ne doit jamais fuiter d'un test
        # à l'autre (ni depuis l'environnement réel de la machine).
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop(main.POKER_TOURNAMENT_INTERNAL_LAUNCH, None)

        self.win = _StubWindow()
        self.win._acquire_menu_principal_lock_if_needed = types.MethodType(
            main.App._acquire_menu_principal_lock_if_needed, self.win
        )
        self.win._cleanup_for_close = types.MethodType(main.App._cleanup_for_close, self.win)

    # -- 1/2. Double lancement externe (PROD et TEST) => une seule session --

    def _double_lancement_externe(self, is_test_build):
        with patch.object(main, "_is_test_build", return_value=is_test_build), \
             patch.object(main, "raise_process_when_ready") as mock_raise:
            first = self.win._acquire_menu_principal_lock_if_needed(None)
            self.assertTrue(first)
            self.assertTrue(self.win._holds_menu_principal_lock)
            mock_raise.assert_not_called()

            # Second "lancement" simulé sur LE MÊME objet : menu_principal_
            # pid() retrouve le PID déjà enregistré par le premier appel
            # (celui du process de test lui-même, donc réellement vivant)
            # — reproduit fidèlement "un autre process externe existe déjà".
            second = self.win._acquire_menu_principal_lock_if_needed(None)
            self.assertFalse(second)
            mock_raise.assert_called_once_with(self.win, os.getpid())
            self.assertEqual(self.win.after_calls, [(3500, self.win.destroy)])

    def test_double_lancement_externe_prod(self):
        self._double_lancement_externe(is_test_build=False)

    def test_double_lancement_externe_test(self):
        self._double_lancement_externe(is_test_build=True)

    # -- 3. PROD et TEST coexistent -----------------------------------

    def test_prod_et_test_coexistent(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.assertTrue(self.win._acquire_menu_principal_lock_if_needed(None))
        win_test = _StubWindow()
        win_test._acquire_menu_principal_lock_if_needed = types.MethodType(
            main.App._acquire_menu_principal_lock_if_needed, win_test
        )
        with patch.object(main, "_is_test_build", return_value=True):
            self.assertTrue(win_test._acquire_menu_principal_lock_if_needed(None))
        self.assertTrue(self.win._holds_menu_principal_lock)
        self.assertTrue(win_test._holds_menu_principal_lock)

    # -- 4/9. Lobby (open_path fourni) => jamais bloqué, jamais vérifié --

    def test_lobby_avec_open_path_jamais_bloque(self):
        # Verrou déjà détenu par "quelqu'un" (nous-mêmes) : un lancement
        # avec un chemin réel doit malgré tout toujours réussir, SANS
        # même consulter le verrou.
        with patch.object(main, "_is_test_build", return_value=False):
            self.win._acquire_menu_principal_lock_if_needed(None)
        with patch.object(open_windows, "menu_principal_pid") as mock_check:
            result = self.win._acquire_menu_principal_lock_if_needed("/tmp/un_tournoi.tournoi")
        self.assertTrue(result)
        mock_check.assert_not_called()

    def test_plusieurs_lobby_successifs_toujours_autorises(self):
        for path in ("/tmp/a.tournoi", "/tmp/b.tournoi", "/tmp/c.tournoi"):
            win = _StubWindow()
            win._acquire_menu_principal_lock_if_needed = types.MethodType(
                main.App._acquire_menu_principal_lock_if_needed, win
            )
            self.assertTrue(win._acquire_menu_principal_lock_if_needed(path))
            self.assertFalse(win._holds_menu_principal_lock, "le Lobby ne pose jamais ce verrou")

    # -- 5. "🏠 Menu principal" (marqueur interne) => jamais bloqué -------

    def test_lancement_interne_menu_principal_jamais_bloque(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.win._acquire_menu_principal_lock_if_needed(None)  # verrou détenu
        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        try:
            win2 = _StubWindow()
            win2._acquire_menu_principal_lock_if_needed = types.MethodType(
                main.App._acquire_menu_principal_lock_if_needed, win2
            )
            with patch.object(open_windows, "menu_principal_pid") as mock_check:
                result = win2._acquire_menu_principal_lock_if_needed(None)
            self.assertTrue(result)
            mock_check.assert_not_called()
            self.assertFalse(win2._holds_menu_principal_lock)
        finally:
            del os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH]

    # -- 6. Ramène bien le PID EXISTANT (pas soi-même) au premier plan ---

    def test_ramene_le_pid_existant_au_premier_plan(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.win._acquire_menu_principal_lock_if_needed(None)
            win2 = _StubWindow()
            win2._acquire_menu_principal_lock_if_needed = types.MethodType(
                main.App._acquire_menu_principal_lock_if_needed, win2
            )
            with patch.object(main, "raise_process_when_ready") as mock_raise:
                result = win2._acquire_menu_principal_lock_if_needed(None)
        self.assertFalse(result)
        mock_raise.assert_called_once_with(win2, os.getpid())
        self.assertTrue(win2.after_calls)
        self.assertEqual(win2.after_calls[0][0], 3500)
        self.assertFalse(win2.destroyed, "self.after programme le destroy, ne l'appelle pas tout de suite")

    # -- 6bis. Le verrou reste détenu même après qu'un tournoi soit ouvert --

    def test_verrou_conserve_apres_ouverture_d_un_tournoi(self):
        """LE scénario signalé par l'utilisateur : le verrou ne doit
        JAMAIS être relâché simplement parce qu'un tournoi a été choisi
        — seule la fermeture réelle (_cleanup_for_close) le libère."""
        with patch.object(main, "_is_test_build", return_value=False):
            self.win._acquire_menu_principal_lock_if_needed(None)
        # Un tournoi est maintenant "affiché" dans cette fenêtre — rien
        # dans le code de choix de tournoi ne doit toucher au verrou.
        self.win.db = object()  # simule self.db désormais défini
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

        win2 = _StubWindow()
        win2._acquire_menu_principal_lock_if_needed = types.MethodType(
            main.App._acquire_menu_principal_lock_if_needed, win2
        )
        with patch.object(main, "_is_test_build", return_value=False), \
             patch.object(main, "raise_process_when_ready") as mock_raise:
            result = win2._acquire_menu_principal_lock_if_needed(None)
        self.assertFalse(result, "un second lancement externe doit être bloqué même tournoi déjà ouvert")
        mock_raise.assert_called_once_with(win2, os.getpid())

    # -- 7. Fermeture normale => verrou libéré ---------------------------

    def test_fermeture_normale_libere_le_verrou(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.win._acquire_menu_principal_lock_if_needed(None)
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

        self.win._cleanup_for_close()

        self.assertIsNone(open_windows.menu_principal_pid("prod"))
        self.assertFalse(self.win._holds_menu_principal_lock)

    def test_cleanup_sans_verrou_ne_fait_rien_de_travers(self):
        """Un process Lobby/"Menu principal" interne (qui n'a jamais posé
        ce verrou) doit pouvoir appeler _cleanup_for_close normalement
        sans qu'aucune erreur ne survienne ni qu'aucun verrou d'un AUTRE
        process ne soit touché par erreur."""
        with patch.object(main, "_is_test_build", return_value=False):
            other = _StubWindow()
            other._acquire_menu_principal_lock_if_needed = types.MethodType(
                main.App._acquire_menu_principal_lock_if_needed, other
            )
            other._acquire_menu_principal_lock_if_needed(None)  # un AUTRE process détient le verrou
        self.win._cleanup_for_close()  # jamais posé lui-même : ne doit rien libérer
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())


class SpawnAppProcessInternalMarkerTest(unittest.TestCase):
    """spawn_app_process(internal_menu_child=True) pose bien le
    marqueur d'environnement, jamais sinon (Lobby, appel par défaut)."""

    def test_marqueur_pose_pour_menu_principal_interne(self):
        with patch.object(main.subprocess, "Popen") as mock_popen:
            main.spawn_app_process(internal_menu_child=True)
        _args, kwargs = mock_popen.call_args
        self.assertEqual(kwargs["env"].get(main.POKER_TOURNAMENT_INTERNAL_LAUNCH), "1")

    def test_aucun_marqueur_par_defaut_lobby(self):
        with patch.object(main.subprocess, "Popen") as mock_popen:
            main.spawn_app_process(["/tmp/un_tournoi.tournoi"])
        _args, kwargs = mock_popen.call_args
        self.assertIsNone(kwargs["env"])


class MenuPrincipalKeyTest(unittest.TestCase):
    def test_prod_par_defaut(self):
        with patch.object(main, "_is_test_build", return_value=False):
            self.assertEqual(main._menu_principal_key(), "prod")

    def test_test_si_build_de_test(self):
        with patch.object(main, "_is_test_build", return_value=True):
            self.assertEqual(main._menu_principal_key(), "test")


if __name__ == "__main__":
    unittest.main()
