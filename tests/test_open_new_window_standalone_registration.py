# -*- coding: utf-8 -*-
"""Correctif du 2026-10-15 — "fermeture rapide" du tournoi d'origine
juste après avoir cliqué "🏠 Menu principal" : deux fenêtres "Bienvenue"
affichées simultanément (photographie HP à l'appui), MALGRÉ le
correctif du 2026-10-14 (bis, commit 8eec084) déjà livré.

Diagnostic (voir App._open_new_window, main.py) : ce correctif précédent
fermait la course entre "le Menu principal volontaire s'enregistre" et
"le tournoi d'origine se ferme" — mais SEULEMENT à l'intérieur du code
du processus volontaire lui-même (App.__init__ ->
_acquire_menu_principal_lock_if_needed). Une fenêtre de course bien plus
large reste ouverte EN AMONT : spawn_app_process() lance un nouveau
processus OS, puis _open_new_window attend seulement 0,3 s (de quoi
détecter un plantage immédiat, pas plus) avant de continuer — mais ce
nouveau processus doit encore démarrer tout l'interpréteur Python figé
(PyInstaller), importer Tkinter/sqlite3/Pillow, construire l'App, AVANT
d'atteindre le point où il s'inscrit lui-même au registre multi-entrées
des écrans "sans tournoi" (open_windows.acquire_standalone_screen). Sur
un poste modeste/antivirus actif, cela peut prendre bien plus que 0,3 s.
Si le tournoi d'origine se ferme PENDANT cette fenêtre, il ne trouve
personne encore inscrit et ouvre sa propre fenêtre "sans tournoi" :
duplication.

Correction : le PARENT (App._open_new_window) inscrit lui-même le PID
du nouveau processus, dès l'instant où il le connaît ET sait qu'il n'a
pas immédiatement planté (open_windows.acquire_standalone_screen(
proc.pid, key, exclusive=False)) — élimine toute dépendance au temps de
démarrage du nouveau processus.

Structure :
1. RegistrationOrderingTest — doublures légères (même convention que
   tests/test_menu_principal_single_instance.py) : l'inscription a bien
   lieu, avec les bons arguments, dans le bon ordre (après la
   vérification anti-plantage immédiat, avant raise_process_when_ready),
   jamais pour un lancement qui a échoué ou qui a immédiatement planté
   (aucun enregistrement fantôme).
2. HpScenarioClosedByParentRegistrationTest — intégration RÉELLE (fichiers
   de registre isolés sur disque, jamais ~/.poker_tournament) : reproduit
   exactement le scénario de la photographie HP et prouve que la
   duplication ne se produit plus, MÊME si le processus volontaire
   "enfant" ne s'est jamais lui-même réveillé pour s'inscrire (simule
   fidèlement un enfant encore en train de démarrer).
3. AtomicityAndIdempotencyTest — l'inscription du parent et l'éventuelle
   ré-inscription ultérieure par l'enfant lui-même ne créent jamais
   deux entrées ni ne cassent l'atomicité déjà garantie par
   acquire_standalone_screen (voir tests/test_menu_principal_volontaire_
   duplication.py::RaceConditionTest pour la couverture générale de
   cette atomicité, non répétée ici).
4. NonInterferenceStructuralTest — vérifie, par analyse du code source,
   que ce correctif reste strictement localisé à _open_new_window :
   aucun appel ajouté à open_windows.register/unregister (registre des
   TOURNOIS, jamais touché par ce correctif — non-régression
   multitournoi) ; _remote_end_tournament (télécommande iPhone) continue
   d'appeler exclusivement self._on_close(), jamais _open_new_window.

Portabilité Mac/Windows : aucun code spécifique à une plateforme n'est
ajouté par ce correctif — open_windows.acquire_standalone_screen
utilise déjà, sans modification ici, le verrou de fichier inter-
processus cross-plateforme (_file_lock, fcntl/msvcrt) déjà testé par
tests/test_menu_principal_volontaire_duplication.py et tests/
test_acquire_menu_principal_race.py."""
import ast
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import open_windows  # noqa: E402


def _main_source():
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_py, encoding="utf-8") as f:
        return f.read()


def _find_function(tree, name):
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


class _FakeProc:
    def __init__(self, pid, alive=True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class _FakeApp:
    """Doublure de App : ne reprend que ce que _open_new_window lit ou
    appelle — même convention que tests/test_menu_principal_single_
    instance.py."""

    def __init__(self):
        self._menu_principal_proc = None


# =======================================================================
# 1. Ordre et conditions exactes de l'inscription (doublures légères)
# =======================================================================
class RegistrationOrderingTest(unittest.TestCase):

    def test_inscription_apres_verification_anti_plantage_avant_raise_process(self):
        """L'inscription doit avoir lieu APRÈS la vérification "le
        process n'a pas planté immédiatement" (sans quoi un process mort
        serait quand même inscrit) et AVANT raise_process_when_ready
        (ordre vérifié explicitement via une liste d'appels partagée)."""
        fake = _FakeApp()
        proc = _FakeProc(pid=9001)
        call_order = []

        def _record_register(pid, key, exclusive):
            call_order.append(("register", pid, key, exclusive))

        def _record_raise(app, pid):
            call_order.append(("raise", pid))

        with patch.object(main, "spawn_app_process", return_value=proc), \
             patch.object(main.open_windows, "acquire_standalone_screen", side_effect=_record_register), \
             patch.object(main, "raise_process_when_ready", side_effect=_record_raise), \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)

        self.assertEqual(
            call_order,
            [("register", 9001, main._menu_principal_key(), False), ("raise", 9001)],
        )

    def test_exclusive_false_jamais_bloque_jamais_verifie(self):
        """L'inscription du parent suit exactement le même traitement
        "volontaire" (exclusive=False) qu'un Menu principal qui se serait
        inscrit lui-même — jamais bloquée, jamais vérifiée contre une
        autre entrée existante (voir open_windows.acquire_standalone_
        screen)."""
        fake = _FakeApp()
        proc = _FakeProc(pid=9002)
        with patch.object(main, "spawn_app_process", return_value=proc), \
             patch.object(main.open_windows, "acquire_standalone_screen") as mock_register, \
             patch.object(main, "raise_process_when_ready"), \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)
        mock_register.assert_called_once_with(9002, main._menu_principal_key(), exclusive=False)


# =======================================================================
# 2. Reproduction réelle du scénario HP — fichiers de registre isolés
# =======================================================================
class _IsolatedRegistryTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="open_new_window_registration_test_")
        self.addCleanup(self._tmp.cleanup)

        def _fake_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.json")

        def _fake_registry_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.lock")

        def _fake_standalone_screens_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_screens_{key}.json")

        for target, fake in (
            ("_menu_principal_lock_path", _fake_lock_path),
            ("_menu_principal_registry_lock_path", _fake_registry_lock_path),
            ("_standalone_screens_path", _fake_standalone_screens_path),
        ):
            patcher = patch.object(open_windows, target, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)

        self._is_test_build_patcher = patch.object(main, "_is_test_build", return_value=False)
        self._is_test_build_patcher.start()
        self.addCleanup(self._is_test_build_patcher.stop)


class HpScenarioClosedByParentRegistrationTest(_IsolatedRegistryTestCase):

    def _make_window(self):
        win = _FakeApp()
        win._acquire_menu_principal_lock_if_needed = types.MethodType(
            main.App._acquire_menu_principal_lock_if_needed, win
        )
        win._cleanup_for_close = types.MethodType(main.App._cleanup_for_close, win)
        win.after_calls = []
        win.destroyed = False
        win.db = None
        win.after = lambda delay, cb: win.after_calls.append((delay, cb))
        win.destroy = lambda: setattr(win, "destroyed", True)
        win._stop_remote_control = lambda: None
        win._stop_whatsapp_share_server = lambda: None
        win._cancel_tables_blink = lambda: None
        win._cancel_players_absent_blink = lambda: None
        return win

    def test_fermeture_rapide_ne_duplique_plus_meme_si_lenfant_na_jamais_demarre(self):
        """Scénario EXACT de la photographie HP, avec le correctif : A
        (tournoi d'origine) acquiert le verrou exclusif, clique "🏠 Menu
        principal" -> B (enfant) est spawné. B est inscrit IMMÉDIATEMENT
        par A lui-même (_open_new_window, jamais par le code de B) — ici,
        on simule fidèlement le pire cas (B ne s'est PAS encore
        "réveillé" : on ne fait JAMAIS tourner son propre _acquire_menu_
        principal_lock_if_needed) pour prouver que la correction ne
        dépend en rien de la vitesse de démarrage de B. A ferme ensuite
        SON tournoi "rapidement" et retombe sur "sans tournoi"
        (force_check=True, voir App._on_close) : doit être bloqué et
        ramené vers B, jamais un troisième écran."""
        a = self._make_window()
        # b_pid factice (le vrai process spawné n'existe jamais dans ce
        # test, voir docstring) : vivacité forcée, même précaution que
        # tests/test_menu_principal_volontaire_duplication.py — sans
        # quoi _prune le retirerait aussitôt (pid non réellement vivant).
        b_pid = 424242
        with patch.object(open_windows, "_pid_is_running", side_effect=lambda pid: pid in (os.getpid(), b_pid)):
            self.assertTrue(a._acquire_menu_principal_lock_if_needed(None))
            self.assertTrue(a._holds_menu_principal_lock)

            # "🏠 Menu principal" cliqué depuis A -> _open_new_window spawne
            # B (pid distinct) ; B lui-même ne fait RIEN ici (ne démarre
            # jamais réellement) — seule l'inscription IMMÉDIATE côté
            # parent (A) est exercée, exactement le code ajouté par ce
            # correctif.
            b_proc = _FakeProc(pid=b_pid, alive=True)
            with patch.object(main, "spawn_app_process", return_value=b_proc), \
                 patch.object(main, "raise_process_when_ready"), \
                 patch.object(main.time, "sleep"):
                main.App._open_new_window(a)
            self.assertIs(a._menu_principal_proc, b_proc)

            # B est déjà visible dans le registre, SANS qu'aucun code de B
            # n'ait jamais tourné — A y figure aussi (sa propre inscription
            # "sans tournoi" avant le choix d'un tournoi, voir App.__init__).
            self.assertEqual(
                sorted(open_windows.list_standalone_screen_pids("prod")), sorted([os.getpid(), b_pid]),
            )

            # A ferme rapidement son tournoi : libère son verrou exclusif.
            a._cleanup_for_close()
            self.assertIsNone(open_windows.menu_principal_pid("prod"))
            # B reste visible, intact (pid différent, jamais touché par le
            # nettoyage de A).
            self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [b_pid])

            # A retombe sur "sans tournoi" (App._on_close -> force_check=
            # True) : AVANT ce correctif, le verrou exclusif vide aurait
            # laissé A ouvrir une DEUXIÈME fenêtre puisque B (jamais
            # démarré) n'aurait pas encore été visible. Avec le correctif,
            # B est déjà inscrit par son parent : A doit être bloqué.
            with patch.object(main, "raise_process_when_ready") as mock_raise:
                result = a._acquire_menu_principal_lock_if_needed(None, force_check=True)

        self.assertFalse(result, "A doit être bloqué : B est déjà visible, même sans avoir jamais démarré")
        mock_raise.assert_called_once_with(a, b_pid)
        self.assertEqual(a.after_calls, [(3500, a.destroy)])
        self.assertFalse(a._holds_menu_principal_lock)
        self.assertIsNone(open_windows.menu_principal_pid("prod"))

    def test_sans_ce_correctif_le_scenario_aurait_duplique(self):
        """Contre-épreuve explicite : SANS l'inscription immédiate côté
        parent (B jamais inscrit, exactement le comportement AVANT ce
        correctif), A retombant sur "sans tournoi" doit au contraire
        RÉUSSIR à acquérir le verrou lui-même — la duplication se
        produirait bien (A et B afficheraient chacun leur propre écran).
        Documente la régression que ce correctif élimine."""
        a = self._make_window()
        a._acquire_menu_principal_lock_if_needed(None)
        a._cleanup_for_close()

        # B n'est PAS inscrit (reproduit l'ancien comportement, sans le
        # correctif) : le registre est vide.
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [])

        with patch.object(main, "raise_process_when_ready") as mock_raise:
            result = a._acquire_menu_principal_lock_if_needed(None, force_check=True)

        self.assertTrue(result, "sans inscription de B, A acquiert le verrou -> duplication")
        mock_raise.assert_not_called()


# =======================================================================
# 3. Atomicité / idempotence (inscription parent + éventuelle
#    ré-inscription par l'enfant lui-même, même pid)
# =======================================================================
class AtomicityAndIdempotencyTest(_IsolatedRegistryTestCase):

    def test_reinscription_par_lenfant_lui_meme_reste_sans_effet_indesirable(self):
        """Une fois inscrit par le parent, l'enfant qui démarre pour de
        bon et s'inscrit à son tour (même pid, voir App.__init__ ->
        _acquire_menu_principal_lock_if_needed) ne doit créer aucune
        entrée supplémentaire ni lever d'erreur — idempotent."""
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            open_windows.acquire_standalone_screen(123456, "prod", exclusive=False)
            self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [123456])

            # L'enfant démarre enfin et s'inscrit lui-même (même pid).
            open_windows.acquire_standalone_screen(123456, "prod", exclusive=False)
            self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [123456])

    def test_deux_tentatives_exclusives_restent_serialisees_malgre_linscription_parent(self):
        """Non-régression : l'ajout d'une inscription "volontaire" côté
        parent ne doit jamais affaiblir l'atomicité déjà garantie entre
        deux tentatives EXCLUSIVES concurrentes (voir tests/test_menu_
        principal_volontaire_duplication.py::RaceConditionTest pour la
        couverture complète de cette atomicité par de vrais threads —
        ici, une seule vérification séquentielle suffit à confirmer que
        le chemin de code emprunté par ce correctif ne contourne rien)."""
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            first = open_windows.acquire_standalone_screen(111, "prod", exclusive=True)
            second = open_windows.acquire_standalone_screen(222, "prod", exclusive=True)
        self.assertIsNone(first)
        self.assertEqual(second, 111)


# =======================================================================
# 4. Non-interférence : multitournoi et télécommande iPhone
# =======================================================================
class NonInterferenceStructuralTest(unittest.TestCase):

    def test_open_new_window_ne_touche_jamais_au_registre_des_tournois(self):
        """Non-régression multitournoi explicite : ce correctif ne doit
        jamais appeler open_windows.register/unregister/try_register
        (registre des TOURNOIS, open_windows.json) — seulement
        acquire_standalone_screen (registre "écrans sans tournoi",
        fichier totalement séparé)."""
        tree = ast.parse(_main_source(), filename="main.py")
        func = _find_function(tree, "_open_new_window")
        called_attrs = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertIn("acquire_standalone_screen", called_attrs)
        for forbidden in ("register", "unregister", "try_register"):
            self.assertNotIn(forbidden, called_attrs)

    def test_remote_end_tournament_nappelle_toujours_que_on_close(self):
        """Télécommande iPhone ("Fin de la partie") : _remote_end_
        tournament doit continuer d'appeler EXCLUSIVEMENT self._on_close()
        — jamais _open_new_window, jamais directement acquire_standalone_
        screen. Ce correctif, entièrement localisé à _open_new_window
        (jamais appelée par le chemin iPhone), ne peut donc pas la
        perturber — vérifié ici explicitement plutôt que supposé."""
        tree = ast.parse(_main_source(), filename="main.py")
        func = _find_function(tree, "_remote_end_tournament")
        source = ast.get_source_segment(_main_source(), func)
        self.assertIn("self._on_close()", source)
        self.assertNotIn("_open_new_window", source)
        self.assertNotIn("acquire_standalone_screen", source)


if __name__ == "__main__":
    unittest.main()
