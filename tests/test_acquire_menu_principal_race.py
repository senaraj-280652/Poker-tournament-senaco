# -*- coding: utf-8 -*-
"""Correctif du 2026-10-12 — essai réel sur un poste Windows (HP) : "deux
lancements successifs de Senaco ont ouvert deux fenêtres identiques".

Diagnostic : App._acquire_menu_principal_lock_if_needed appelait jusque-
là open_windows.menu_principal_pid() (lecture) PUIS open_windows.
register_menu_principal() (écriture) comme deux étapes SÉPARÉES, sans le
moindre verrou commun les protégeant ensemble — une vraie course entre
deux lancements survenus à quelques millisecondes d'écart : chacun des
deux process pouvait lire "aucune instance" avant que L'UN DES DEUX
n'ait eu le temps d'écrire son propre verrou, et les deux continuaient
alors chacun vers son propre écran "Bienvenue".

Correction : open_windows.acquire_menu_principal(pid, key) regroupe
désormais lecture ET décision ET écriture en UNE SEULE section critique,
protégée par un verrou de fichier natif de l'OS (_menu_principal_
registry_lock, même idiome déjà éprouvé pour open_windows.json/primes_
session_started.json — voir _registry_lock dans open_windows.py).

Ce fichier teste SPÉCIFIQUEMENT l'atomicité de cette nouvelle fonction —
voir tests/test_menu_principal_singleton.py pour la couverture générale,
déjà existante, du reste du mécanisme (double lancement externe, Lobby/
"Menu principal" interne toujours exemptés, indépendance prod/test,
auto-nettoyage d'un PID mort, libération à la fermeture), INCHANGÉE par
ce correctif et non répétée ici.

Couvre :
1. Comportement nominal (sans course) : premier appel réussit (renvoie
   None), second appel pour le même pid "gagnant" échoue (renvoie son
   pid), aucune écrasement du verrou.
2. LA course elle-même, reproduite avec de VRAIS threads Python
   appelant acquire_menu_principal EXACTEMENT au même instant (une
   barrière de synchronisation force le départ simultané) : sur un
   grand nombre de répétitions, EXACTEMENT UN SEUL "gagnant" (renvoie
   None) à chaque fois, jamais deux, jamais zéro — le fichier de verrou
   final ne contient jamais que SON pid.
3. Indépendance prod/test préservée par la nouvelle fonction (chacune sa
   propre ligne, son propre fichier de verrou OS).
4. PID mort : auto-nettoyage toujours opérant à travers acquire_menu_
   principal (menu_principal_pid, appelée À L'INTÉRIEUR de la section
   critique, reste inchangée)."""
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


def _spawn_and_wait_dead_pid():
    """Renvoie un PID GARANTI mort (même technique que tests/test_menu_
    principal_singleton.py — un vrai process lancé puis attendu jusqu'à
    sa fin, plus fiable qu'un grand nombre arbitraire)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    return proc.pid


class AcquireMenuPrincipalAtomicTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="acquire_menu_principal_race_test_")
        self.addCleanup(self._tmp.cleanup)

        def _fake_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.json")

        def _fake_registry_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.lock")

        p1 = patch.object(open_windows, "_menu_principal_lock_path", side_effect=_fake_lock_path)
        p1.start()
        self.addCleanup(p1.stop)
        p2 = patch.object(
            open_windows, "_menu_principal_registry_lock_path", side_effect=_fake_registry_lock_path,
        )
        p2.start()
        self.addCleanup(p2.stop)

    # -- 1. Comportement nominal, sans course ----------------------------

    def test_premier_appel_reussit_renvoie_none(self):
        result = open_windows.acquire_menu_principal(os.getpid(), "prod")
        self.assertIsNone(result)
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

    def test_second_appel_echoue_renvoie_le_pid_existant(self):
        open_windows.acquire_menu_principal(os.getpid(), "prod")

        result = open_windows.acquire_menu_principal(os.getpid() + 1, "prod")

        self.assertEqual(result, os.getpid())
        # Le verrou n'a PAS changé de main : toujours le premier pid.
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())

    # -- 2. La course elle-même, avec de vrais threads --------------------

    def test_deux_appels_simultanes_un_seul_gagnant(self):
        """Reproduction directe du bug : deux threads appellent
        acquire_menu_principal EXACTEMENT au même instant (barrière),
        avec des pids DIFFÉRENTS. _pid_is_running forcée à toujours
        "vivant" (patch.object ci-dessous) : ce test cible UNIQUEMENT
        l'atomicité de la section critique elle-même (un seul gagnant
        possible), jamais la détection de vivacité d'un PID — déjà
        couverte séparément par test_pid_mort_remplace_automatiquement
        et par tests/test_menu_principal_singleton.py — qui, avec des
        pids FACTICES non réellement vivants, ferait l'inverse de ce
        qu'on veut tester ici (un "gagnant" ultérieur verrait le pid du
        premier comme "mort" et écraserait son verrou à tort, un faux
        positif SANS rapport avec l'atomicité réellement testée).

        Répété plusieurs fois pour donner sa chance à la course de se
        manifester si le verrou ne protégeait pas réellement la section
        critique (sans le correctif, ce test échoue de façon
        intermittente mais reproductible en quelques dizaines
        d'itérations — avec le correctif, jamais)."""
        pid_a = 100000
        pid_b = 100001
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            for i in range(40):
                # Repart d'un état propre à chaque itération.
                lock_path = open_windows._menu_principal_lock_path("prod")
                if os.path.exists(lock_path):
                    os.remove(lock_path)

                barrier = threading.Barrier(2)
                results = {}

                def call(pid, name):
                    barrier.wait(timeout=5)
                    results[name] = open_windows.acquire_menu_principal(pid, "prod")

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
                # Le fichier final ne contient que le pid du gagnant.
                final_pid = open_windows.menu_principal_pid("prod")
                winning_pid = pid_a if winners[0] == "a" else pid_b
                self.assertEqual(final_pid, winning_pid)

    # -- 3. Indépendance prod/test ----------------------------------------

    def test_prod_et_test_independants_via_acquire(self):
        result_prod = open_windows.acquire_menu_principal(os.getpid(), "prod")
        result_test = open_windows.acquire_menu_principal(os.getpid(), "test")

        self.assertIsNone(result_prod)
        self.assertIsNone(result_test)
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())
        self.assertEqual(open_windows.menu_principal_pid("test"), os.getpid())

    # -- 4. Auto-nettoyage d'un PID mort, via acquire_menu_principal ------

    def test_pid_mort_remplace_automatiquement(self):
        dead_pid = _spawn_and_wait_dead_pid()
        open_windows.register_menu_principal(dead_pid, "prod")

        result = open_windows.acquire_menu_principal(os.getpid(), "prod")

        self.assertIsNone(result)  # le pid mort n'a pas bloqué l'acquisition
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())


if __name__ == "__main__":
    unittest.main()
