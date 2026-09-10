# -*- coding: utf-8 -*-
"""Scénario multi-tournois EXACT demandé par l'utilisateur (2026-09-09,
"sécurisation du contrôle à distance", point 6) pour le code de session
à distance, avec de VRAIS sous-processus séparés (comme tests/test_
primes_multi_process_real_subprocess.py, même principe et mêmes
précautions — voir sa docstring) :

  - tournoi A lancé => code XXXXXX ;
  - B ouvert/créé => même code ;
  - A fermé => B garde le même code ;
  - C créé => même code ;
  - B fermé (C reste ouvert) => C garde le même code ;
  - dernier tournoi fermé => fin de session ;
  - relance du logiciel => nouveau code aléatoire.

`HOME` redirigé vers un dossier temporaire commun à tout le scénario
(jamais ~/.poker_tournament) : chaque sous-processus, y compris les
"fenêtres" tenues ouvertes, ne partage RIEN en mémoire avec les autres
— seul le disque (remote_control_auth.json, protégé par le verrou
inter-processus _remote_control_lock) fait le lien entre eux, exactement
comme en conditions réelles (plusieurs tournois = plusieurs processus
indépendants, voir spawn_app_process dans main.py)."""
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.join(_HERE, "_subprocess_remote_control_worker.py")


class _HeldWindow:
    def __init__(self, proc, code):
        self.proc = proc
        self.code = code

    def close(self, timeout=10):
        try:
            if self.proc.poll() is not None:
                return
            self.proc.stdin.write("close\n")
            self.proc.stdin.flush()
            self.proc.stdin.close()
            line = self.proc.stdout.readline()
            if line.strip() != "closed":
                raise AssertionError(f"fermeture inattendue : {line!r}")
            self.proc.wait(timeout=timeout)
        finally:
            for stream in (self.proc.stdout, self.proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass


class RemoteControlSessionCodeRealSubprocessTest(unittest.TestCase):
    def setUp(self):
        self._tmphome_ctx = tempfile.TemporaryDirectory(prefix="poker_remote_subproc_home_")
        self._tmpdata_ctx = tempfile.TemporaryDirectory(prefix="poker_remote_subproc_data_")
        self.addCleanup(self._tmphome_ctx.cleanup)
        self.addCleanup(self._tmpdata_ctx.cleanup)
        self.home = self._tmphome_ctx.name
        self.data = self._tmpdata_ctx.name
        self._held_windows = []

    def tearDown(self):
        for held in self._held_windows:
            try:
                if held.proc.poll() is None:
                    held.proc.kill()
                    held.proc.wait(timeout=5)
            except Exception:
                pass
            for stream in (held.proc.stdin, held.proc.stdout, held.proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass

    def _env(self):
        env = dict(os.environ)
        env["HOME"] = self.home
        return env

    def _run(self, *args, timeout=30):
        result = subprocess.run(
            [sys.executable, _WORKER, *args],
            env=self._env(), capture_output=True, text=True, timeout=timeout,
        )
        self.assertEqual(
            result.returncode, 0,
            f"sous-process {args} a échoué :\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        lines = [ln for ln in result.stdout.strip().splitlines() if ln.strip()]
        return lines[-1] if lines else ""

    def _hold_open(self, path, timeout=10):
        proc = subprocess.Popen(
            [sys.executable, _WORKER, "hold_open", path],
            env=self._env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        code_line = proc.stdout.readline()
        ready_line = proc.stdout.readline()
        if ready_line.strip() != "ready":
            proc.kill()
            raise AssertionError(
                f"la fenêtre sur {path} n'a pas démarré correctement : {ready_line!r}\n"
                f"stderr:\n{proc.stderr.read()}"
            )
        held = _HeldWindow(proc, code_line.strip())
        self._held_windows.append(held)
        return held

    def _path(self, name):
        return os.path.join(self.data, f"{name}.tournoi")

    def test_scenario_exact_de_la_demande(self):
        path_a, path_b, path_c = self._path("A"), self._path("B"), self._path("C")

        # -- Tournoi A lancé => code XXXXXX. -----------------------------
        window_a = self._hold_open(path_a)
        code_a = window_a.code
        self.assertRegex(code_a, r"^\d{6}$")

        # -- B ouvert/créé => MÊME code. ----------------------------------
        window_b = self._hold_open(path_b)
        self.assertEqual(window_b.code, code_a, "B doit partager le même code que A")

        # -- A fermé => B garde le même code. -----------------------------
        window_a.close()
        self.assertEqual(
            self._run("read_code", path_b), code_a,
            "la fermeture de A ne doit pas affecter le code que B continue de voir",
        )

        # -- C créé => toujours le même code. -----------------------------
        window_c = self._hold_open(path_c)
        self.assertEqual(window_c.code, code_a, "C, créé alors que B est encore ouvert, doit voir le même code")

        # -- B fermé, C reste seul ouvert => C garde le même code. --------
        window_b.close()
        self.assertEqual(
            self._run("read_code", path_c), code_a,
            "la fermeture de B (avant-dernier tournoi) ne doit pas affecter le code de C, resté ouvert",
        )

        # -- Dernier tournoi (C) fermé => fin de session. -----------------
        window_c.close()
        self.assertEqual(self._run("list_open"), "")

        # -- Relance du logiciel => nouveau code aléatoire. ---------------
        window_d = self._hold_open(self._path("D"))
        self.addCleanup(lambda: window_d.close() if window_d.proc.poll() is None else None)
        self.assertRegex(window_d.code, r"^\d{6}$")
        self.assertNotEqual(
            window_d.code, code_a,
            "une nouvelle session (tous les tournois précédents fermés) doit générer un NOUVEAU code",
        )
        window_d.close()

    def test_approbation_partagee_entre_processus(self):
        """L'approbation d'un appareil (registre PERSISTANT) et le
        jeton de session qui lui est délivré doivent être visibles et
        valides depuis N'IMPORTE QUEL processus de la session — pas
        seulement celui qui a traité la demande initiale (demande du
        2026-09-09, "approbation des téléphones", point 8 : "L'appareil
        peut donc passer Tournoi A -> Lobby -> Tournoi B -> Sit & Go
        sans nouvelle approbation")."""
        path_a, path_b = self._path("A2"), self._path("B2")
        window_a = self._hold_open(path_a)
        self.addCleanup(lambda: window_a.close() if window_a.proc.poll() is None else None)
        window_b = self._hold_open(path_b)
        self.addCleanup(lambda: window_b.close() if window_b.proc.poll() is None else None)

        browser_id = "c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1c1"
        ip = "192.168.1.60"

        # La demande est déposée depuis un sous-process JETABLE quelconque...
        status = self._run("register_device_attempt", browser_id, ip)
        self.assertEqual(status, "pending")

        # ...approuvée depuis un AUTRE sous-process jetable (représente le
        # clic "Autoriser" du responsable, indépendant du process qui a
        # reçu la requête HTTP initiale)...
        self.assertEqual(self._run("approve_device", browser_id, "Salle 1"), "True")

        # ...et un jeton délivré/vérifié reste cohérent, encore depuis un
        # AUTRE sous-process jetable (représente un tournoi différent, ou
        # le port 8765 repris par un autre processus).
        token = self._run("mint_token", browser_id)
        self.assertTrue(token)
        self.assertEqual(self._run("verify_device_session", browser_id, token), "1")

        # Révocation depuis encore un autre sous-process : invalide
        # immédiatement, vu par tous.
        self.assertEqual(self._run("revoke_device", browser_id), "True")
        self.assertEqual(self._run("verify_device_session", browser_id, token), "0")

        window_a.close()
        window_b.close()


if __name__ == "__main__":
    unittest.main()
