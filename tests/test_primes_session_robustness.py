# -*- coding: utf-8 -*-
"""Tests de robustesse du verrou de session "Calculer les primes"
(demande du 2026-09-14, suite au diagnostic d'un cas réel observé sur un
poste Windows : primes_session_started.json avait disparu alors qu'un
tournoi restait vivant dans open_windows.json, et une pause/reprise du
chronomètre ne le recréait pas).

Trois correctifs couverts, chacun dans sa propre classe :
1. App._clock_resume répare désormais automatiquement (appel idempotent
   à mark_primes_session_started) le fichier de session lors d'une
   reprise après pause d'un tournoi déjà démarré.
2. open_windows._pid_is_running (branche Windows) ne confond plus une
   erreur de détection transitoire avec la preuve qu'un process est
   mort — voir _win32_open_process_handle, substituable même sur une
   machine non-Windows.
3. open_windows._registry_lock (nouveau verrou inter-processus, même
   mécanisme déjà éprouvé que _remote_control_lock, factorisé dans
   _file_lock) protège register()/unregister()/le verrou de session
   Primes contre les pertes de mise à jour entre processus concurrents.

Cause INITIALE de la disparition volontairement non affirmée ici (voir
le diagnostic du 2026-09-14 : non démontrée) — ces tests couvrent les
PROTECTIONS ajoutées, pas une reproduction de la cause elle-même."""
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.join(_HERE, "_subprocess_primes_worker.py")


# =======================================================================
# 1. _clock_resume répare le fichier de session lors d'une reprise.
# =======================================================================
class _ClockResumeStub:
    """Doublure minimale d'App : seulement ce que lit/appelle
    _clock_resume (voir main.py), sans construire la moindre fenêtre."""

    def __init__(self, db):
        self.db = db
        self.refresh_calls = 0

    def _elapsed_before_pause(self):
        return self.db.get_setting_int("paused_accum_seconds", 0)

    def _refresh_clock_tab(self):
        self.refresh_calls += 1


class ClockResumeRepairsSessionFileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="clock_resume_repair_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        lock_path = os.path.join(self._tmp.name, "primes_session_started.json")
        registry_lock_path = os.path.join(self._tmp.name, "open_windows.lock")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_primes_session_lock_path", return_value=lock_path),
            patch.object(open_windows, "_registry_lock_path", return_value=registry_lock_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
            # _remote_control_dir (demande du 2026-09-19, incident réel où
            # ce genre de test — register() sur un registre isolé devenu
            # vide — a effacé le VRAI remote_control_auth.json de
            # l'utilisateur) : voir tests/test_remote_control_dir_
            # isolation.py pour le mécanisme complet.
            patch.object(open_windows, "_remote_control_dir", return_value=self._tmp.name),
        ):
            target.start()
            self.addCleanup(target.stop)

        self.stub = _ClockResumeStub(self.db)
        self.stub._clock_resume = types.MethodType(main.App._clock_resume, self.stub)

        # Simule un tournoi déjà démarré ET déjà enregistré (comme le
        # ferait App.__init__ juste après l'ouverture) — condition
        # nécessaire pour que primes_session_started() ne s'auto-efface
        # pas via "plus aucun tournoi ouvert" pendant le test.
        open_windows.register(self.db.path)
        self.db.set_settings({"clock_started": 1, "is_paused": 1, "paused_accum_seconds": 30})
        open_windows.mark_primes_session_started(True)

    def test_fichier_supprime_pendant_la_pause_est_recree_a_la_reprise(self):
        # Simule la disparition constatée sur le HP : le fichier de
        # session n'existe plus, alors que le tournoi (lui) reste
        # enregistré dans open_windows.json (jamais touché ici).
        os.remove(open_windows._primes_session_lock_path())
        self.assertFalse(open_windows.primes_session_started())

        self.stub._clock_resume()  # reprise après pause (is_paused=1 -> 0)

        self.assertTrue(open_windows.primes_session_started())
        self.assertTrue(open_windows.locked_primes_enabled())
        self.assertEqual(self.db.get_setting_int("is_paused", 1), 0)
        self.assertEqual(self.stub.refresh_calls, 1)

    def test_fichier_deja_present_reste_inchange_idempotent(self):
        """Non-régression : quand tout va bien (fichier déjà présent),
        cet appel supplémentaire ne doit rien changer ni rien casser —
        mark_primes_session_started est idempotente."""
        before = open_windows._read_primes_session_data()
        self.stub._clock_resume()
        after = open_windows._read_primes_session_data()
        self.assertEqual(before, after)


# =======================================================================
# 2. _pid_is_running (Windows) : erreur transitoire != process mort.
# =======================================================================
class Win32PidIsRunningTest(unittest.TestCase):
    """Patch open_windows.sys.platform (même convention déjà établie
    dans tests/test_bring_pid_to_front_script.py) : exerçable sur
    n'importe quelle machine, ctypes.windll n'existe même pas hors
    Windows — d'où _win32_open_process_handle, substituable lui, voir
    sa docstring dans open_windows.py."""

    def test_handle_valide_pid_vivant(self):
        with patch.object(open_windows.sys, "platform", "win32"), \
             patch.object(open_windows, "_win32_open_process_handle", return_value=4242):
            self.assertTrue(open_windows._pid_is_running(1234))

    def test_handle_nul_pid_reellement_mort(self):
        with patch.object(open_windows.sys, "platform", "win32"), \
             patch.object(open_windows, "_win32_open_process_handle", return_value=0):
            self.assertFalse(open_windows._pid_is_running(1234))

    def test_exception_transitoire_jamais_assimilee_a_un_pid_mort(self):
        """Le coeur du correctif : une erreur inattendue (panne
        transitoire d'OpenProcess, antivirus, etc.) ne doit JAMAIS
        conclure "mort" — sans quoi un tournoi bien vivant risquerait de
        se voir retiré du registre, et primes_session_started.json
        effacé à tort (l'incident diagnostiqué le 2026-09-14)."""
        with patch.object(open_windows.sys, "platform", "win32"), \
             patch.object(open_windows, "_win32_open_process_handle", side_effect=OSError("boom")):
            self.assertTrue(open_windows._pid_is_running(1234))

    def test_pid_non_entier_reste_faux_avant_meme_windows(self):
        with patch.object(open_windows.sys, "platform", "win32"):
            self.assertFalse(open_windows._pid_is_running("pas-un-pid"))
            self.assertFalse(open_windows._pid_is_running(None))


class PosixPidIsRunningRealDeadPidTest(unittest.TestCase):
    """Un VRAI pid mort (jamais un nombre arbitraire, qui pourrait par
    malchance correspondre à un processus réellement vivant sur la
    machine qui exécute ce test) doit toujours être nettoyé — sur la
    plateforme réelle de CE test (POSIX, cette suite tournant sur
    macOS/Linux)."""

    def test_vrai_pid_mort_est_bien_detecte_comme_tel(self):
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=5)
        self.assertFalse(open_windows._pid_is_running(proc.pid))

    def test_pid_propre_process_de_test_est_vivant(self):
        self.assertTrue(open_windows._pid_is_running(os.getpid()))


# =======================================================================
# 3/5/6. _registry_lock : vraie concurrence, sans perte de mise à jour ;
# plusieurs tournois, la fermeture de l'un ne casse jamais l'état Primes
# tant qu'un autre reste vivant.
# =======================================================================
class _HeldWindow:
    def __init__(self, proc):
        self.proc = proc

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


class RegistryLockRealConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self._tmphome_ctx = tempfile.TemporaryDirectory(prefix="registry_lock_home_")
        self._tmpdata_ctx = tempfile.TemporaryDirectory(prefix="registry_lock_data_")
        self.addCleanup(self._tmphome_ctx.cleanup)
        self.addCleanup(self._tmpdata_ctx.cleanup)
        self.home = self._tmphome_ctx.name
        self.data = self._tmpdata_ctx.name
        self._held = []

    def tearDown(self):
        for held in self._held:
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

    def _hold_open(self, path, is_new=True, timeout=10):
        args = [sys.executable, _WORKER, "hold_open", path]
        if is_new:
            args.append("--new")
        proc = subprocess.Popen(
            args, env=self._env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        held = _HeldWindow(proc)
        self._held.append(held)
        ready_line = proc.stdout.readline()
        if ready_line.strip() != "ready":
            proc.kill()
            raise AssertionError(f"fenêtre {path} pas démarrée : {ready_line!r}\n{proc.stderr.read()}")
        return held

    def _path(self, name):
        return os.path.join(self.data, f"{name}.tournoi")

    def _read_registry(self):
        with patch.dict(os.environ, self._env()):
            return open_windows._load()

    def _primes_session_started(self):
        with patch.dict(os.environ, self._env()):
            return open_windows.primes_session_started()

    # -- 6. Plusieurs tournois : fermer l'un ne casse jamais l'état de --
    # -- l'autre, tant qu'il reste vivant. -------------------------------

    def test_fermeture_dun_tournoi_preserve_la_session_dun_autre_vivant(self):
        path_a = self._path("A")
        path_b = self._path("B")
        window_a = self._hold_open(path_a, is_new=True)
        window_b = self._hold_open(path_b, is_new=True)

        self._run("start_tournament", path_a)
        self.assertTrue(self._primes_session_started())

        # Ferme B (comme la "deuxième fenêtre" du cas réel) : A reste
        # vivant, la session Primes doit rester verrouillée.
        window_b.close()

        self.assertTrue(
            self._primes_session_started(),
            "fermer UN tournoi ne doit jamais effacer la session Primes tant qu'un autre (A) reste vivant",
        )
        registry = self._read_registry()
        self.assertIn(os.path.abspath(path_a), registry)
        self.assertNotIn(os.path.abspath(path_b), registry)

        window_a.close()
        self.assertFalse(
            self._primes_session_started(),
            "une fois VRAIMENT plus aucun tournoi ouvert, la session doit, elle, se terminer",
        )

    # -- 4. Deuxième fenêtre jamais enregistrée (Bienvenue) : aucun effet -

    def test_fenetre_jamais_enregistree_ne_touche_a_rien(self):
        """Représente la "seconde fenêtre" du cas réel QUAND elle n'a
        jamais choisi de tournoi (self.db reste None dans le vrai code,
        donc aucun register()/unregister() n'est jamais appelé pour
        elle) : rien à simuler côté worker, ce test vérifie plutôt
        qu'un unregister() portant sur un chemin JAMAIS enregistré (donc
        strictement équivalent à "cette fenêtre n'a rien fait") reste
        totalement sans effet sur une session par ailleurs active."""
        path_a = self._path("A")
        window_a = self._hold_open(path_a, is_new=True)
        self._run("start_tournament", path_a)
        self.assertTrue(self._primes_session_started())

        never_registered = self._path("jamais_enregistre")
        with patch.dict(os.environ, self._env()):
            open_windows.unregister(never_registered)

        self.assertTrue(self._primes_session_started())
        registry = self._read_registry()
        self.assertIn(os.path.abspath(path_a), registry)

        window_a.close()

    # -- 5. Concurrence réelle : aucune perte de mise à jour --------------

    def test_concurrence_reelle_register_unregister_sans_perte(self):
        """Pendant que A reste ouvert (et la session démarrée), une
        vraie rafale de processus concurrents s'enregistrent PUIS se
        désenregistrent aussitôt (chacun un VRAI sous-process séparé,
        PAS des threads du même process) : à la fin, le registre ne doit
        contenir QUE A, et la session Primes doit être restée
        verrouillée du début à la fin, sans aucune perte de mise à jour
        intermédiaire côté A."""
        path_a = self._path("A")
        window_a = self._hold_open(path_a, is_new=True)
        self._run("start_tournament", path_a)
        self.assertTrue(self._primes_session_started())

        N = 6
        errors = []

        repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        def churn(i):
            try:
                path = self._path(f"churn_{i}")
                result = subprocess.run(
                    [sys.executable, "-c", (
                        "import sys, open_windows;"
                        "open_windows.register(sys.argv[1]);"
                        "open_windows.unregister(sys.argv[1])"
                    ), path],
                    env=self._env(), cwd=repo_root,
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    errors.append((i, result.stdout, result.stderr))
            except Exception as e:
                errors.append((i, "exception", str(e)))

        threads = [threading.Thread(target=churn, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [])
        # La session est restée verrouillée PENDANT toute la rafale, et
        # A n'a jamais disparu du registre malgré l'écriture concurrente
        # de N autres processus sur le MÊME fichier.
        self.assertTrue(self._primes_session_started())
        registry = self._read_registry()
        self.assertEqual(list(registry.keys()), [os.path.abspath(path_a)])

        window_a.close()


# =======================================================================
# 7. Mode Test ON/OFF continue de fonctionner comme prévu (non-régression
#    explicite, en plus de la ré-exécution de tests/test_primes_test_
#    mode_override.py déjà existant).
# =======================================================================
class ModeTestStillWorksWithRegistryLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mode_test_still_works_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        lock_path = os.path.join(self._tmp.name, "primes_session_started.json")
        registry_lock_path = os.path.join(self._tmp.name, "open_windows.lock")
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_primes_session_lock_path", return_value=lock_path),
            patch.object(open_windows, "_registry_lock_path", return_value=registry_lock_path),
            # _remote_control_dir (demande du 2026-09-19) : voir le même
            # commentaire dans ClockResumeRepairsSessionFileTest.setUp
            # ci-dessus.
            patch.object(open_windows, "_remote_control_dir", return_value=self._tmp.name),
        ):
            target.start()
            self.addCleanup(target.stop)

        open_windows.register(self.db.path)
        open_windows.mark_primes_session_started(True)

        self.win = types.SimpleNamespace(db=self.db, test_mode_var=types.SimpleNamespace(_v=False))
        self.win.test_mode_var.get = lambda: self.win.test_mode_var._v
        self.win.test_mode_var.set = lambda v: setattr(self.win.test_mode_var, "_v", v)
        self.win._test_mode_enabled = types.MethodType(main.App._test_mode_enabled, self.win)
        self.win._primes_section_effectively_locked = types.MethodType(
            main.App._primes_section_effectively_locked, self.win
        )

    def test_mode_test_off_reste_verrouille(self):
        self.win.test_mode_var.set(False)
        self.assertTrue(self.win._primes_section_effectively_locked())

    def test_mode_test_on_deverrouille(self):
        self.win.test_mode_var.set(True)
        self.assertFalse(self.win._primes_section_effectively_locked())


if __name__ == "__main__":
    unittest.main()
