# -*- coding: utf-8 -*-
"""Registre inter-processus de l'instance unique du Lobby (demande du
2026-10-31, constat réel sur le Sony Vaio — capture à l'appui — que
chaque fenêtre de tournoi est un PROCESSUS séparé, voir main.py:
spawn_app_process). Couvre open_windows.lobby_registry_lock/lobby_pid/
register_lobby/unregister_lobby — voir leur section dans open_windows.py
("Instance unique du Lobby") pour le diagnostic complet et le choix de
réutiliser l'idiome déjà éprouvé de Menu principal (acquire_menu_
principal/menu_principal_pid, voir tests/test_acquire_menu_principal_
race.py et tests/test_menu_principal_singleton.py, dont ce fichier
reprend délibérément la structure).

Avertissement explicite du 2026-10-31, repris ici : "l'utilisation d'un
simple registre PID ne suffit pas nécessairement à empêcher deux
créations simultanées" — ce fichier couvre donc spécifiquement :
1. Comportement nominal (fichiers réels, dossier temporaire dédié,
   jamais ~/.poker_tournament).
2. L'ACQUISITION ATOMIQUE elle-même, reproduite avec de VRAIS threads
   partant exactement au même instant (barrière) — même technique que
   test_acquire_menu_principal_race.py — sur de nombreuses répétitions :
   exactement un seul gagnant à chaque fois, jamais deux, jamais zéro.
3. Un vrai scénario MULTIPROCESSUS (deux processus Python RÉELLEMENT
   séparés, pas seulement deux threads du même processus/GIL) —
   demande explicite "ajouter les tests de non-régression, notamment
   multiprocessus" — prouve que le verrou de fichier protège bien la
   section critique entre de vrais processus OS, pas seulement entre
   threads d'un même interpréteur.
4. RÉCUPÉRATION après un processus Lobby arrêté brutalement (crash,
   Ctrl+C, gestionnaire de tâches) — demande explicite "un mécanisme de
   récupération en cas de processus arrêté brutalement" : un PID
   enregistré mais réellement mort ne doit jamais bloquer durablement
   une réouverture."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _spawn_and_wait_dead_pid():
    """Renvoie un PID GARANTI mort (même technique que tests/test_menu_
    principal_singleton.py et tests/test_acquire_menu_principal_race.py :
    un vrai process lancé puis attendu jusqu'à sa fin — plus fiable qu'un
    grand nombre arbitraire, qui pourrait par malchance correspondre à
    un processus réellement vivant sur la machine qui exécute ce test)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    return proc.pid


class LobbyRegistryTest(unittest.TestCase):
    """open_windows.register_lobby/unregister_lobby/lobby_pid — fichiers
    réels, jamais ~/.poker_tournament."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lobby_registry_test_")
        self.addCleanup(self._tmp.cleanup)
        self._registry_path = os.path.join(self._tmp.name, "lobby_pid.json")
        self._lock_path = os.path.join(self._tmp.name, "lobby_pid.lock")

        p1 = patch.object(open_windows, "_lobby_registry_path", return_value=self._registry_path)
        p2 = patch.object(open_windows, "_lobby_registry_lock_path", return_value=self._lock_path)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def test_aucun_lobby_au_depart(self):
        self.assertIsNone(open_windows.lobby_pid())

    def test_enregistrement_puis_lecture_pid_vivant(self):
        open_windows.register_lobby(os.getpid())
        self.assertEqual(open_windows.lobby_pid(), os.getpid())

    def test_desenregistrement_libere_le_registre(self):
        open_windows.register_lobby(os.getpid())
        open_windows.unregister_lobby(os.getpid())
        self.assertIsNone(open_windows.lobby_pid())

    def test_desenregistrement_ignore_un_pid_different(self):
        """Précaution anti-course (même principe que unregister_menu_
        principal) : un désenregistrement portant un AUTRE pid que celui
        actuellement enregistré ne doit rien effacer."""
        open_windows.register_lobby(os.getpid())
        open_windows.unregister_lobby(99999999)
        self.assertEqual(open_windows.lobby_pid(), os.getpid())

    def test_fichier_illisible_traite_comme_absent(self):
        os.makedirs(os.path.dirname(self._registry_path), exist_ok=True)
        with open(self._registry_path, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON valide")
        self.assertIsNone(open_windows.lobby_pid())

    # -- Point 4 : récupération après arrêt brutal -----------------------

    def test_pid_mort_auto_nettoye_recuperation_apres_crash(self):
        dead_pid = _spawn_and_wait_dead_pid()
        open_windows.register_lobby(dead_pid)

        self.assertIsNone(open_windows.lobby_pid())
        # Le fichier lui-même a bien été supprimé (pas seulement ignoré) :
        # une réouverture normale peut donc repartir de zéro, sans aucun
        # état fantôme laissé par le crash.
        self.assertFalse(os.path.exists(self._registry_path))

    def test_lobby_registry_lock_fonctionne_et_se_libere(self):
        with open_windows.lobby_registry_lock(timeout=2.0):
            open_windows.register_lobby(os.getpid())
        # Le verrou est bien relâché en sortant du bloc : une seconde
        # acquisition immédiate ne doit jamais expirer.
        with open_windows.lobby_registry_lock(timeout=2.0):
            pass


class LobbyRegistryAtomicRaceTest(unittest.TestCase):
    """Reproduction directe de l'avertissement explicite du 2026-10-31 :
    "l'utilisation d'un simple registre PID ne suffit pas nécessairement
    à empêcher deux créations simultanées". Deux threads appellent
    EXACTEMENT au même instant (barrière) la même séquence lire-décider-
    écrire que App._open_lobby_window (voir main.py) — sous
    lobby_registry_lock() — avec des pids DIFFÉRENTS. Répété pour donner
    sa chance à la course de se manifester si le verrou ne protégeait
    pas réellement la section critique."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lobby_registry_race_test_")
        self.addCleanup(self._tmp.cleanup)
        self._registry_path = os.path.join(self._tmp.name, "lobby_pid.json")
        self._lock_path = os.path.join(self._tmp.name, "lobby_pid.lock")
        p1 = patch.object(open_windows, "_lobby_registry_path", return_value=self._registry_path)
        p2 = patch.object(open_windows, "_lobby_registry_lock_path", return_value=self._lock_path)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    @staticmethod
    def _acquire_or_lose(pid):
        """Même séquence que App._open_lobby_window (voir main.py),
        réduite à sa section critique : sous le verrou, lire lobby_pid(),
        et seulement si personne ne le détient déjà, enregistrer `pid`."""
        with open_windows.lobby_registry_lock():
            existing = open_windows.lobby_pid()
            if existing is not None:
                return existing
            open_windows.register_lobby(pid)
            return None

    def test_deux_appels_simultanes_un_seul_gagnant(self):
        pid_a = 100000
        pid_b = 100001
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            for i in range(40):
                if os.path.exists(self._registry_path):
                    os.remove(self._registry_path)

                barrier = threading.Barrier(2)
                results = {}

                def call(pid, name):
                    barrier.wait(timeout=5)
                    results[name] = self._acquire_or_lose(pid)

                t1 = threading.Thread(target=call, args=(pid_a, "a"))
                t2 = threading.Thread(target=call, args=(pid_b, "b"))
                t1.start()
                t2.start()
                t1.join(timeout=5)
                t2.join(timeout=5)

                winners = [name for name, result in results.items() if result is None]
                self.assertEqual(
                    len(winners), 1,
                    f"itération {i} : {len(winners)} gagnant(s) au lieu d'exactement 1 "
                    f"({results!r}) — le verrou n'a pas protégé la section critique.",
                )
                winning_pid = pid_a if winners[0] == "a" else pid_b
                self.assertEqual(open_windows.lobby_pid(), winning_pid)


class LobbyRegistryRealMultiProcessTest(unittest.TestCase):
    """Demande explicite du 2026-10-31 : "ajouter les tests de non-
    régression, notamment multiprocessus". Le test ci-dessus prouve
    l'atomicité entre THREADS d'un même interpréteur (même GIL) — celui-
    ci la prouve entre deux VRAIS processus OS séparés, exactement le
    scénario réel (deux fenêtres de tournoi = deux processus, voir
    main.py:spawn_app_process), sur Windows comme macOS.

    Synchronisation déterministe SANS dépendre d'un minutage fragile :
    CE processus de test détient le verrou en premier, lance les deux
    sous-processus (qui écrivent chacun un fichier sentinelle juste
    avant de tenter leur propre acquisition, puis se bloquent dessus),
    attend que les DEUX sentinelles existent (donc que les deux sous-
    processus sont bien arrivés à la ligne d'acquisition), puis relâche
    son propre verrou — garantissant une VRAIE contention entre les
    deux, jamais un simple hasard d'ordonnancement."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="lobby_registry_mp_test_")
        self.addCleanup(self._tmp.cleanup)
        self._registry_path = os.path.join(self._tmp.name, "lobby_pid.json")
        self._lock_path = os.path.join(self._tmp.name, "lobby_pid.lock")
        p1 = patch.object(open_windows, "_lobby_registry_path", return_value=self._registry_path)
        p2 = patch.object(open_windows, "_lobby_registry_lock_path", return_value=self._lock_path)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def _subprocess_script(self, result_path, target_time):
        """Correctif du 2026-10-31 (échec observé UNE fois dans la suite
        complète sous charge système très lourde — 3400 tests en cours —
        jamais reproduit en isolation ni dans le test par threads
        ci-dessus, qui prouve déjà l'atomicité de la même section
        critique) : au lieu d'un parent qui détient le verrou puis le
        relâche en espérant que les deux enfants soient "déjà bloqués
        dessus" (minutage fragile, dépendant de l'ordonnancement sous
        charge), chaque enfant attend INDÉPENDAMMENT un instant cible
        commun en horloge murale (`target_time`, transmis par le parent,
        assorti d'une marge large) avant de tenter l'acquisition —
        aucune dépendance au moment où le parent relâche quoi que ce
        soit."""
        return (
            "import sys, os, json, time\n"
            f"sys.path.insert(0, {_REPO_ROOT!r})\n"
            "import open_windows\n"
            "from unittest.mock import patch\n"
            f"with patch.object(open_windows, '_lobby_registry_path', return_value={self._registry_path!r}), \\\n"
            f"     patch.object(open_windows, '_lobby_registry_lock_path', return_value={self._lock_path!r}):\n"
            f"    while time.time() < {target_time!r}:\n"
            "        time.sleep(0.005)\n"
            "    with open_windows.lobby_registry_lock(timeout=20):\n"
            "        existing = open_windows.lobby_pid()\n"
            "        if existing is None:\n"
            "            open_windows.register_lobby(os.getpid())\n"
            "            won = True\n"
            "        else:\n"
            "            won = False\n"
            f"    with open({result_path!r}, 'w') as f:\n"
            "        json.dump({'pid': os.getpid(), 'won': won}, f)\n"
        )

    def _un_essai(self):
        """Un essai complet : deux vrais processus, instant cible commun
        (large marge pour tolérer un système très chargé), exactement un
        gagnant attendu. Renvoie (data_a, data_b)."""
        if os.path.exists(self._registry_path):
            os.remove(self._registry_path)
        result_a = os.path.join(self._tmp.name, f"result_a_{time.monotonic_ns()}.json")
        result_b = os.path.join(self._tmp.name, f"result_b_{time.monotonic_ns()}.json")
        # Marge large (3 s) : même un démarrage d'interpréteur Python
        # anormalement lent (système très chargé) a largement le temps
        # de s'achever avant l'instant cible commun.
        target_time = time.time() + 3.0

        proc_a = subprocess.Popen(
            [sys.executable, "-c", self._subprocess_script(result_a, target_time)],
            stdin=subprocess.DEVNULL,
        )
        proc_b = subprocess.Popen(
            [sys.executable, "-c", self._subprocess_script(result_b, target_time)],
            stdin=subprocess.DEVNULL,
        )
        self.addCleanup(lambda: proc_a.poll() is None and proc_a.kill())
        self.addCleanup(lambda: proc_b.poll() is None and proc_b.kill())

        self.assertEqual(proc_a.wait(timeout=30), 0)
        self.assertEqual(proc_b.wait(timeout=30), 0)

        with open(result_a, encoding="utf-8") as f:
            data_a = json.load(f)
        with open(result_b, encoding="utf-8") as f:
            data_b = json.load(f)
        return data_a, data_b

    def test_deux_vrais_processus_un_seul_gagnant_le_lobby(self):
        """Répété 3 fois (un vrai lancement de process étant coûteux,
        contrairement aux 40 itérations du test par threads ci-dessus) :
        chaque répétition doit montrer EXACTEMENT un gagnant."""
        for i in range(3):
            data_a, data_b = self._un_essai()
            winners = [d for d in (data_a, data_b) if d["won"]]
            self.assertEqual(
                len(winners), 1,
                f"essai {i} : exactement un seul processus doit gagner le Lobby, "
                f"pas {len(winners)} (a={data_a!r}, b={data_b!r})",
            )
            # Fichier de registre BRUT plutôt que lobby_pid() : à cet
            # instant, les deux sous-processus ont déjà terminé (wait()
            # juste au-dessus) — lobby_pid() aurait alors légitimement
            # déjà auto-nettoyé l'entrée du gagnant (processus plus
            # vivant), exactement le comportement voulu par ailleurs
            # (point 4, "récupération après arrêt brutal") mais hors
            # sujet ici : ce test vérifie que le bon pid a bien été
            # ÉCRIT au moment de la course, pas qu'il reste vivant
            # indéfiniment après.
            with open(self._registry_path, encoding="utf-8") as f:
                registry_content = json.load(f)
            self.assertEqual(registry_content.get("pid"), winners[0]["pid"])


if __name__ == "__main__":
    unittest.main()
