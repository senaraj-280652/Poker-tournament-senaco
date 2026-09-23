# -*- coding: utf-8 -*-
"""open_windows.try_register (demande du 2026-09-16, "interdire
l'ouverture simultanée du même fichier .tournoi") :

- réservation ATOMIQUE inter-processus AVANT toute ouverture réelle
  (Database), utilisée par main.py: App.__init__ (branche `open_path`)
  et _choose_tournament_file, juste avant `self.db = Database(...)` ;
- refuse TOUJOURS si le chemin est déjà réservé, MÊME PAR LE PROCESSUS
  APPELANT LUI-MÊME (règle confirmée explicitement avec l'utilisateur :
  aucune exception "même PID" n'est nécessaire dans ce logiciel — chaque
  fenêtre de tournoi tourne dans son propre processus, et le seul cas de
  réouverture par le même processus, App._new_tournament, libère déjà
  l'ancien via unregister() avant de recommencer) ;
- deux chemins DIFFÉRENTS restent toujours ouvrables simultanément
  (multi-tournoi non cassé) ;
- récupération automatique après un crash (héritée de _prune/_pid_is_
  running, sans code supplémentaire) ;
- libération propre via unregister() (déjà existant, inchangé).

Trois volets :
- TryRegisterInProcessTest : logique de base, registre redirigé vers un
  dossier temporaire dédié (jamais ~/.poker_tournament) — même
  précaution que tests/test_open_windows_atomic_write.py et tests/
  test_primes_enabled_toggle.py.
- TryRegisterConcurrencyTest : simultanéité RÉELLE via deux threads qui
  se disputent le VRAI verrou de fichier OS (_registry_lock/fcntl.flock)
  au même instant (threading.Barrier) — flock() verrouille par
  description de fichier ouvert, pas par processus : deux threads qui
  ouvrent chacun leur propre descripteur sur le même fichier se
  disputent réellement le verrou, exactement comme deux processus
  séparés le feraient (aucune complaisance "même PID" à ce niveau OS).
- TryRegisterRealSubprocessTest : VRAIS sous-processus séparés (jamais
  seulement des objets en mémoire), HOME redirigé — même principe que
  tests/test_primes_multi_process_real_subprocess.py."""
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

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_subprocess_try_register_worker.py")


class TryRegisterInProcessTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_try_register_test_")
        self.addCleanup(self._tmpdir_ctx.cleanup)
        registry_path = os.path.join(self._tmpdir_ctx.name, "open_windows.json")
        patcher = patch.object(open_windows, "_registry_path", return_value=registry_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        # _remote_control_dir (demande du 2026-09-19) : try_register()/
        # unregister() peuvent déclencher _clear_remote_control_session_
        # files() (registre isolé ci-dessus devenu vide), qui cible le
        # VRAI ~/.poker_tournament si cette fonction n'est pas ELLE AUSSI
        # isolée — voir tests/test_remote_control_dir_isolation.py.
        dir_patcher = patch.object(open_windows, "_remote_control_dir", return_value=self._tmpdir_ctx.name)
        self.addCleanup(dir_patcher.stop)
        dir_patcher.start()

        self._files_dir = tempfile.mkdtemp(prefix="poker_try_register_files_")
        self.addCleanup(self._cleanup_files_dir)

    def _cleanup_files_dir(self):
        import shutil
        shutil.rmtree(self._files_dir, ignore_errors=True)

    def _path(self, name="A.tournoi"):
        return os.path.join(self._files_dir, name)

    def test_premiere_reservation_reussit(self):
        self.assertIsNone(open_windows.try_register(self._path()))

    def test_deuxieme_reservation_du_meme_chemin_refusee_meme_pid(self):
        """Règle absolue (voir docstring du module) : refusé même si
        c'est LE MÊME processus qui retente."""
        path = self._path()
        self.assertIsNone(open_windows.try_register(path))
        self.assertEqual(open_windows.try_register(path), os.getpid())

    def test_deux_chemins_differents_reussissent_tous_les_deux(self):
        self.assertIsNone(open_windows.try_register(self._path("A.tournoi")))
        self.assertIsNone(open_windows.try_register(self._path("B.tournoi")))

    def test_pid_mort_est_remplace_par_une_nouvelle_reservation(self):
        """Récupération après un crash : héritée de _prune()/_pid_is_
        running, sans le moindre code supplémentaire dans try_register
        lui-même."""
        path = self._path()
        open_windows._save({os.path.abspath(path): {"pid": 999999, "registered_at": 0}})
        # 999999 n'est (presque à coup sûr) le PID d'aucun processus réel
        # sur cette machine : _pid_is_running le traite comme mort, sans
        # rien patcher de spécial ici — exactement la situation réelle
        # après un plantage.
        self.assertIsNone(open_windows.try_register(path))

    def test_unregister_libere_puis_une_nouvelle_reservation_reussit(self):
        path = self._path()
        open_windows.try_register(path)
        open_windows.unregister(path)
        self.assertIsNone(open_windows.try_register(path))

    def test_update_remote_info_n_est_jamais_une_nouvelle_ouverture(self):
        """Une mise à jour de métadonnées (port du contrôle à distance)
        sur une entrée déjà possédée ne doit jamais être confondue avec
        une nouvelle tentative d'ouverture — elle ne passe d'ailleurs
        jamais par try_register()/register()."""
        path = self._path()
        open_windows.try_register(path)
        open_windows.update_remote_info(path, 8765, "Tournoi Test")
        # Une VRAIE nouvelle tentative reste refusée (rien n'a été libéré
        # par la mise à jour de métadonnées ci-dessus).
        self.assertEqual(open_windows.try_register(path), os.getpid())
        # Et les métadonnées sont bien restées associées à l'entrée
        # existante, pas remplacées.
        data = open_windows._load()
        self.assertEqual(data[os.path.abspath(path)]["remote_port"], 8765)


class TryRegisterConcurrencyTest(unittest.TestCase):
    """Simultanéité réelle sur le VRAI verrou de fichier OS — voir la
    docstring du module."""

    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_try_register_concurrency_")
        self.addCleanup(self._tmpdir_ctx.cleanup)
        registry_path = os.path.join(self._tmpdir_ctx.name, "open_windows.json")
        lock_path = os.path.join(self._tmpdir_ctx.name, "open_windows.lock")
        patcher1 = patch.object(open_windows, "_registry_path", return_value=registry_path)
        patcher2 = patch.object(open_windows, "_registry_lock_path", return_value=lock_path)
        # _remote_control_dir (demande du 2026-09-19) : voir le même
        # commentaire dans TryRegisterInProcessTest.setUp ci-dessus.
        patcher3 = patch.object(open_windows, "_remote_control_dir", return_value=self._tmpdir_ctx.name)
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)
        patcher1.start()
        patcher2.start()
        patcher3.start()

    def test_deux_tentatives_exactement_simultanees_une_seule_gagne(self):
        """Deux threads représentant deux processus DISTINCTS (PID simulés
        différents — os.getpid() forcé par thread) appellent try_register
        exactement au même instant (threading.Barrier) sur LE MÊME
        chemin : exactement un gagnant (None), un refusé (l'autre pid)."""
        path = os.path.join(self._tmpdir_ctx.name, "A.tournoi")
        results = {}
        barrier = threading.Barrier(2)
        fake_pids = {}
        FAKE_PID_1, FAKE_PID_2 = 4242424, 5353535
        real_pid = os.getpid()  # capturé AVANT le patch (voir fake_getpid)

        def fake_getpid():
            # Ne JAMAIS rappeler os.getpid() ici : patch.object ci-dessous
            # patche l'attribut du module os PARTAGÉ (open_windows.os EST
            # os), donc un appel à os.getpid() ici rappellerait cette
            # doublure elle-même — récursion infinie. `real_pid`, capturé
            # avant le patch, sert de repli.
            return fake_pids.get(threading.get_ident(), real_pid)

        def fake_pid_is_running(pid):
            # Les deux PID simulés représentent chacun un vrai process
            # actif pour ce test — seuls eux apparaîtront jamais dans le
            # registre ici, donc toujours "vivants" suffit.
            return pid in (FAKE_PID_1, FAKE_PID_2)

        def worker(name, fake_pid):
            fake_pids[threading.get_ident()] = fake_pid
            barrier.wait()
            results[name] = open_windows.try_register(path)

        with patch.object(open_windows.os, "getpid", side_effect=fake_getpid), \
             patch.object(open_windows, "_pid_is_running", side_effect=fake_pid_is_running):
            t1 = threading.Thread(target=worker, args=("t1", FAKE_PID_1))
            t2 = threading.Thread(target=worker, args=("t2", FAKE_PID_2))
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

        self.assertEqual(set(results.keys()), {"t1", "t2"})
        successes = [k for k, v in results.items() if v is None]
        failures = [(k, v) for k, v in results.items() if v is not None]
        self.assertEqual(len(successes), 1, results)
        self.assertEqual(len(failures), 1, results)
        # Le refusé reçoit bien le PID (simulé) du gagnant.
        winner_name = successes[0]
        _, loser_result = failures[0]
        expected_winner_pid = FAKE_PID_1 if winner_name == "t1" else FAKE_PID_2
        self.assertEqual(loser_result, expected_winner_pid)


class TryRegisterRealSubprocessTest(unittest.TestCase):
    """VRAIS sous-processus séparés (subprocess.Popen), jamais seulement
    des objets en mémoire — HOME redirigé, même principe que tests/
    test_primes_multi_process_real_subprocess.py."""

    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_try_register_subproc_")
        self.home = self._tmpdir_ctx.name
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self._held_procs = []
        self.addCleanup(self._kill_leftovers)

    def _kill_leftovers(self):
        for proc in self._held_procs:
            try:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
            except Exception:
                pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                try:
                    stream.close()
                except Exception:
                    pass

    def _env(self):
        env = dict(os.environ)
        env["HOME"] = self.home
        return env

    def _run(self, *args, timeout=15):
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

    def _hold_open(self, path, action="hold_open", timeout=10):
        proc = subprocess.Popen(
            [sys.executable, _WORKER, action, path],
            env=self._env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        self._held_procs.append(proc)
        first_line = proc.stdout.readline()
        if first_line.strip() != "None":
            proc.kill()
            raise AssertionError(
                f"le sous-process n'a pas réussi sa réservation initiale sur "
                f"{path} : {first_line!r}\nstderr:\n{proc.stderr.read()}"
            )
        return proc

    def test_deux_vrais_process_le_second_est_refuse(self):
        path = os.path.join(self.home, "A.tournoi")
        holder = self._hold_open(path)
        try:
            result = self._run("try_register", path)
            self.assertEqual(result, str(holder.pid))
        finally:
            holder.stdin.write("close\n")
            holder.stdin.flush()
            holder.wait(timeout=10)

    def test_apres_fermeture_propre_un_nouveau_process_peut_ouvrir(self):
        path = os.path.join(self.home, "B.tournoi")
        holder = self._hold_open(path)
        holder.stdin.write("close\n")
        holder.stdin.flush()
        holder.wait(timeout=10)

        result = self._run("try_register", path)
        self.assertEqual(result, "None")

    def test_apres_un_plantage_kill_9_un_nouveau_process_peut_ouvrir(self):
        """Récupération après un crash : le processus détenteur est tué
        BRUTALEMENT (SIGKILL, jamais unregister()) — la réservation doit
        tout de même finir par être considérée libre (_prune/_pid_is_
        running), sans intervention manuelle."""
        path = os.path.join(self.home, "C.tournoi")
        holder = self._hold_open(path, action="hold_open_no_cleanup")
        holder.kill()  # SIGKILL : aucun code de nettoyage Python ne s'exécute
        holder.wait(timeout=10)

        deadline = time.monotonic() + 5
        result = None
        while time.monotonic() < deadline:
            result = self._run("try_register", path)
            if result == "None":
                break
            time.sleep(0.1)
        self.assertEqual(result, "None")

    def test_deux_chemins_differents_deux_vrais_process_les_deux_reussissent(self):
        path_a = os.path.join(self.home, "A.tournoi")
        path_b = os.path.join(self.home, "B.tournoi")
        holder_a = self._hold_open(path_a)
        try:
            result_b = self._run("try_register", path_b)
            self.assertEqual(result_b, "None")
        finally:
            holder_a.stdin.write("close\n")
            holder_a.stdin.flush()
            holder_a.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
