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

    def _stop_whatsapp_share_server(self):
        # Appelé inconditionnellement par _cleanup_for_close depuis le
        # 2026-10-11 (partage PDF par QR code, voir main.py) — sans
        # rapport avec ce que teste ce fichier (verrou "Menu principal"),
        # donc un simple no-op ici, même principe que _stop_remote_
        # control juste au-dessus.
        pass

    def _cancel_tables_blink(self):
        # Appelé inconditionnellement par _cleanup_for_close (voir
        # tests/test_tables_tab_movement_pending_banner.py pour le vrai
        # comportement) — sans rapport avec ce que teste ce fichier
        # (verrou "Menu principal"), donc un simple no-op ici.
        pass

    def _cancel_players_absent_blink(self):
        # Même principe que _cancel_tables_blink ci-dessus (Lot 2,
        # "Gestion des absences et des retardataires", 2026-09-28) :
        # appelé inconditionnellement par _cleanup_for_close, sans rapport
        # avec ce que teste ce fichier.
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

        # Correctif du 2026-10-12 (course corrigée, voir tests/test_
        # acquire_menu_principal_race.py) : _acquire_menu_principal_
        # lock_if_needed appelle désormais open_windows.acquire_menu_
        # principal, qui pose son PROPRE verrou de fichier OS (voir
        # _menu_principal_registry_lock_path) — jamais ~/.poker_
        # tournament pendant les tests, même précaution que pour le
        # fichier JSON ci-dessus.
        def _fake_registry_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.lock")

        registry_lock_patcher = patch.object(
            open_windows, "_menu_principal_registry_lock_path", side_effect=_fake_registry_lock_path,
        )
        registry_lock_patcher.start()
        self.addCleanup(registry_lock_patcher.stop)

        # Correctif du 2026-10-14 (bis) : _acquire_menu_principal_lock_
        # if_needed appelle désormais open_windows.acquire_standalone_
        # screen, qui lit/écrit un SECOND fichier (registre multi-entrées
        # des écrans "sans tournoi") — jamais ~/.poker_tournament pendant
        # les tests, même précaution que pour les deux fichiers ci-dessus.
        def _fake_standalone_screens_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_screens_{key}.json")

        standalone_patcher = patch.object(
            open_windows, "_standalone_screens_path", side_effect=_fake_standalone_screens_path,
        )
        standalone_patcher.start()
        self.addCleanup(standalone_patcher.stop)

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


class ForceCheckOverridesExemptionTest(unittest.TestCase):
    """Correction du 2026-10-14 : "Senaco sans tournoi déjà ouvert, un
    nouveau lancement externe ouvre quand même un second Bienvenue".

    Cause exacte (confirmée sur un poste réel : menu_principal_prod.json
    absent malgré une instance "sans tournoi" bel et bien vivante) :
    POKER_TOURNAMENT_INTERNAL_LAUNCH est posée UNE SEULE FOIS, à la
    création du PROCESSUS, par spawn_app_process(internal_menu_child=
    True) ("🏠 Menu principal") — et reste donc vraie pour TOUTE la
    durée de vie de ce processus, y compris après la fermeture de son
    tournoi, quand il retombe en "Senaco sans tournoi" (App(start_
    standalone=True) ou App(force_menu_principal_check=True),
    construite EN MÉMOIRE dans ce même processus, héritant du même
    environnement). force_check=True (App._acquire_menu_principal_
    lock_if_needed) force la VÉRITABLE vérification du verrou dans ce
    cas précis, en ignorant l'exemption — sans jamais toucher à
    l'exemption pour les autres cas (Lobby, "🏠 Menu principal" ouvrant
    délibérément un AUTRE tournoi), couverts par AcquireMenuPrincipal
    LockTest ci-dessus, entièrement inchangés."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="force_check_test_")
        self.addCleanup(self._tmp.cleanup)

        def _fake_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.json")

        def _fake_registry_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.lock")

        def _fake_standalone_screens_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_screens_{key}.json")

        for target in (
            patch.object(open_windows, "_menu_principal_lock_path", side_effect=_fake_lock_path),
            patch.object(open_windows, "_menu_principal_registry_lock_path", side_effect=_fake_registry_lock_path),
            patch.object(open_windows, "_standalone_screens_path", side_effect=_fake_standalone_screens_path),
        ):
            target.start()
            self.addCleanup(target.stop)

        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop(main.POKER_TOURNAMENT_INTERNAL_LAUNCH, None)

    def _make_window(self):
        win = _StubWindow()
        win._acquire_menu_principal_lock_if_needed = types.MethodType(
            main.App._acquire_menu_principal_lock_if_needed, win
        )
        return win

    # -- 1. Transition depuis un lancement interne : force_check ignore
    #    l'exemption POKER_TOURNAMENT_INTERNAL_LAUNCH ------------------

    def test_transition_depuis_lancement_interne_pose_reellement_le_verrou(self):
        """LE scénario exact diagnostiqué : un processus "🏠 Menu
        principal" (marqueur présent) qui retombe "sans tournoi" doit
        RÉELLEMENT poser le verrou avec force_check=True, pas seulement
        se déclarer "True" sans rien vérifier (comme le ferait l'ancien
        comportement, voir test_lancement_interne_menu_principal_jamais_
        bloque pour le contraste exact)."""
        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        win = self._make_window()
        with patch.object(main, "_is_test_build", return_value=False):
            result = win._acquire_menu_principal_lock_if_needed(None, force_check=True)
        self.assertTrue(result)
        self.assertTrue(win._holds_menu_principal_lock)
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

    def test_sans_force_check_le_meme_processus_reste_exempte(self):
        """Non-régression explicite : SANS force_check, le comportement
        historique (exemption totale, jamais de verrou posé) reste
        inchangé pour ce même processus/marqueur."""
        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        win = self._make_window()
        with patch.object(open_windows, "menu_principal_pid") as mock_check:
            result = win._acquire_menu_principal_lock_if_needed(None)
        self.assertTrue(result)
        mock_check.assert_not_called()
        self.assertFalse(win._holds_menu_principal_lock)

    # -- 2. Fenêtre "sans tournoi" déjà ouverte : la seconde transition
    #    est bloquée et ramenée au premier plan, jamais de doublon -----

    def test_fenetre_sans_tournoi_deja_ouverte_bloque_la_seconde(self):
        """Première fenêtre déjà "sans tournoi" (verrou détenu, peu
        importe comment elle y est arrivée) : une SECONDE transition
        vers "sans tournoi" (ex. un lancement externe répété pendant
        que la première est déjà affichée) doit être bloquée et ramenée
        au premier plan — jamais un second processus "sans tournoi" en
        double."""
        first = self._make_window()
        with patch.object(main, "_is_test_build", return_value=False):
            self.assertTrue(first._acquire_menu_principal_lock_if_needed(None, force_check=True))
        self.assertTrue(first._holds_menu_principal_lock)

        # Deuxième fenêtre, marquée "lancement interne" (reproduit le
        # processus exact à l'origine du bug) tentant elle aussi de
        # retomber "sans tournoi" au même instant.
        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        second = self._make_window()
        with patch.object(main, "_is_test_build", return_value=False), \
             patch.object(main, "raise_process_when_ready") as mock_raise:
            result = second._acquire_menu_principal_lock_if_needed(None, force_check=True)
        self.assertFalse(result)
        mock_raise.assert_called_once_with(second, os.getpid())
        self.assertEqual(second.after_calls, [(3500, second.destroy)])
        self.assertFalse(second._holds_menu_principal_lock)
        # La première fenêtre n'est JAMAIS perturbée : son verrou reste
        # intact, rien ne l'a fermée ni modifiée.
        self.assertFalse(first.destroyed)
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

    # -- 3. Lancements externes répétés : chaque nouvelle tentative,
    #    tant que la première "sans tournoi" reste ouverte, échoue -----

    def test_lancements_externes_repetes_toujours_bloques(self):
        first = self._make_window()
        with patch.object(main, "_is_test_build", return_value=False):
            self.assertTrue(first._acquire_menu_principal_lock_if_needed(None, force_check=True))

        for _ in range(3):
            repeated = self._make_window()
            with patch.object(main, "_is_test_build", return_value=False), \
                 patch.object(main, "raise_process_when_ready") as mock_raise:
                result = repeated._acquire_menu_principal_lock_if_needed(None)
            self.assertFalse(result, "un lancement externe répété doit toujours être bloqué")
            mock_raise.assert_called_once_with(repeated, os.getpid())

    # -- 4. Jamais d'impact sur les cas déjà exemptés légitimement -----

    def test_force_check_naffecte_jamais_le_lobby(self):
        """force_check n'est jamais passé par le Lobby (open_path
        fourni) — vérifié ici que même si on le passait, un open_path
        réel garde la priorité (jamais de vérification)."""
        win = self._make_window()
        with patch.object(open_windows, "menu_principal_pid") as mock_check:
            result = win._acquire_menu_principal_lock_if_needed(
                "/tmp/un_tournoi.tournoi", force_check=True,
            )
        self.assertTrue(result)
        mock_check.assert_not_called()


class NewTournamentForceMenuPrincipalCheckTest(unittest.TestCase):
    """App._new_tournament (Fichier > "Nouveau tournoi.../Ouvrir..." ou
    "🏠 Menu principal" depuis "Senaco sans tournoi") — doit calculer
    force_menu_principal_check=(self.db is None) AVANT de nettoyer/
    détruire, et le transmettre à la nouvelle App()."""

    def _make_app(self, db):
        app = _StubWindow()
        app.db = db
        app._new_tournament = types.MethodType(main.App._new_tournament, app)
        app._cleanup_for_close = types.MethodType(main.App._cleanup_for_close, app)
        return app

    def test_depuis_un_vrai_tournoi_force_check_false(self):
        """Fichier > "Nouveau tournoi.../Ouvrir..." depuis un VRAI
        tournoi (self.db not None) : comportement historique inchangé,
        jamais de vérification forcée (préserve l'ouverture déliberée
        d'un AUTRE tournoi, même depuis une fenêtre "Menu principal"
        interne)."""
        fake_db = types.SimpleNamespace(path="/tmp/un_vrai_tournoi.tournoi", close=lambda: None)
        app = self._make_app(db=fake_db)
        # open_windows.unregister mocké (jamais le vrai registre
        # ~/.poker_tournament : ce test porte sur l'appel à App(...),
        # pas sur l'état réel du registre, déjà couvert ailleurs).
        with patch.object(open_windows, "unregister", return_value=False), \
             patch.object(main, "App") as mock_app_cls:
            app._new_tournament()
        mock_app_cls.assert_called_once_with(force_menu_principal_check=False)
        mock_app_cls.return_value.mainloop.assert_called_once()

    def test_depuis_senaco_sans_tournoi_force_check_true(self):
        """"🏠 Menu principal" cliqué DEPUIS "Senaco sans tournoi"
        (self.db is None) : force_menu_principal_check=True — même
        situation ambiguë que App._on_close avec start_standalone,
        jamais une action "ouvrir délibérément un autre tournoi"."""
        app = self._make_app(db=None)
        with patch.object(main, "App") as mock_app_cls:
            app._new_tournament()
        mock_app_cls.assert_called_once_with(force_menu_principal_check=True)
        mock_app_cls.return_value.mainloop.assert_called_once()

    def test_nettoyage_termine_avant_toute_destruction(self):
        """Précaution explicite de l'utilisateur : le nettoyage
        (_cleanup_for_close, donc la fermeture réelle des ressources du
        tournoi) doit être ENTIÈREMENT terminé avant self.destroy() et
        avant la construction de la nouvelle App()."""
        app = self._make_app(db=None)
        call_order = []
        app._cleanup_for_close = lambda: call_order.append("cleanup")
        app.destroy = lambda: call_order.append("destroy")
        with patch.object(main, "App") as mock_app_cls:
            mock_app_cls.side_effect = lambda **kw: call_order.append("new_app") or mock_app_cls.return_value
            app._new_tournament()
        self.assertEqual(call_order[:3], ["cleanup", "destroy", "new_app"])


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
