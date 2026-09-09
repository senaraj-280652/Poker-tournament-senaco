# -*- coding: utf-8 -*-
"""Couverture automatisée du point 4 de la demande du 2026-09-09
("comme le bug visuel réel n'est pas encore reproduit de façon
certaine... ajoute aussi un test avec deux vrais processus séparés, pas
seulement deux objets dans le même process") : chaque "fenêtre" (A, B)
est un VRAI sous-process Python indépendant qui reste VIVANT pour toute
la durée où elle est censée être "ouverte" (via `subprocess.Popen`,
piloté par un petit protocole stdin "close" — voir _subprocess_primes_
worker.py:_hold_open) : c'est essentiel, puisque open_windows.py lie
chaque entrée du registre au PID du process qui l'a ouverte
(_pid_is_running) — un sous-process qui s'ouvrirait puis se terminerait
aussitôt serait immédiatement "prunable", ne représentant PAS
fidèlement une fenêtre restée ouverte. Les actions ponctuelles (décoche,
tick, lecture) s'exécutent, elles, dans des sous-process JETABLES
séparés (`subprocess.run`) — elles n'ont besoin de "posséder" aucune
entrée du registre.

`HOME` redirigé vers un dossier temporaire commun à tout le scénario
(jamais ~/.poker_tournament) : chaque sous-process, y compris les deux
"fenêtres" A/B, ne partage RIEN en mémoire avec les autres — seul le
disque (export_prefs.json / open_windows.json / primes_session_started.
json) fait le lien entre eux, exactement comme en conditions réelles.

Scénario couvert (repris du message de l'utilisateur) :
  - A et B ouverts avant tout démarrage ;
  - décoche dans A (vrai sous-process) ;
  - B converge vers OFF (vrai sous-process séparé, tick) ;
  - ferme A et B (chacune envoie "close" à SON PROPRE process, seul
    habilité à se désenregistrer lui-même) ;
  - ouvre une nouvelle session C ;
  - C démarre avec primes ON par défaut.

Ce test a effectivement détecté un vrai bug (voir l'historique de
session) : main.py lisait la valeur "proposée" (pour stamper un
tournoi flambant neuf) AVANT que open_windows.register() n'ait eu la
chance de nettoyer un résidu de session précédente — corrigé en
déplaçant la vérification "session déjà éteinte" directement DANS la
lecture (open_windows.primes_enabled_proposed()), jamais seulement au
moment de l'écriture/de l'enregistrement. Preuve concrète que ce type
de test (vrais process séparés) apporte une garantie que les doublures
en mémoire ne peuvent pas offrir."""
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_WORKER = os.path.join(_HERE, "_subprocess_primes_worker.py")


class _HeldWindow:
    """Poignée vers UNE "fenêtre" tenue ouverte par un vrai sous-process
    persistant (voir _subprocess_primes_worker.py:_hold_open)."""

    def __init__(self, proc):
        self.proc = proc

    def close(self, timeout=10):
        try:
            if self.proc.poll() is not None:
                return  # déjà terminé (ex : nettoyage après un test en échec)
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


class PrimesMultiProcessRealSubprocessTest(unittest.TestCase):
    def setUp(self):
        self._tmphome_ctx = tempfile.TemporaryDirectory(prefix="poker_primes_subproc_home_")
        self._tmpdata_ctx = tempfile.TemporaryDirectory(prefix="poker_primes_subproc_data_")
        self.addCleanup(self._tmphome_ctx.cleanup)
        self.addCleanup(self._tmpdata_ctx.cleanup)
        self.home = self._tmphome_ctx.name
        self.data = self._tmpdata_ctx.name
        self._held_windows = []

    def tearDown(self):
        # Filet de sécurité : si le test échoue avant d'avoir fermé une
        # fenêtre tenue ouverte, ne jamais laisser un sous-process
        # trainer derrière ce test.
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
        """Sous-process JETABLE (une action ponctuelle, pas de fenêtre
        à tenir ouverte) — renvoie sa dernière ligne de sortie."""
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
        """Lance et garde vivant un VRAI sous-process représentant une
        fenêtre de tournoi ouverte sur `path` — voir _HeldWindow.close()
        pour la fermeture (protocole stdin "close")."""
        args = [sys.executable, _WORKER, "hold_open", path]
        if is_new:
            args.append("--new")
        proc = subprocess.Popen(
            args, env=self._env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,
        )
        held = _HeldWindow(proc)
        self._held_windows.append(held)
        ready_line = proc.stdout.readline()
        if ready_line.strip() != "ready":
            proc.kill()
            raise AssertionError(
                f"la fenêtre sur {path} n'a pas démarré correctement : {ready_line!r}\n"
                f"stderr:\n{proc.stderr.read()}"
            )
        return held

    def _path(self, name):
        return os.path.join(self.data, f"{name}.tournoi")

    def test_scenario_deux_vrais_process_puis_nouvelle_session(self):
        path_a = self._path("A")
        path_b = self._path("B")
        path_c = self._path("C")

        # -- A et B ouverts avant tout démarrage, chacune dans un VRAI
        # sous-process qui reste vivant (représente la fenêtre tant
        # qu'elle n'est pas explicitement fermée). ----------------------
        window_a = self._hold_open(path_a, is_new=True)
        window_b = self._hold_open(path_b, is_new=True)
        self.assertEqual(self._run("read_local", path_a), "1")
        self.assertEqual(self._run("read_local", path_b), "1")
        self.assertIn(os.path.abspath(path_a), self._run("list_open").split(","))
        self.assertIn(os.path.abspath(path_b), self._run("list_open").split(","))

        # -- Décoche "Calculer les primes" DEPUIS A (sous-process
        # JETABLE séparé, ne partage RIEN en mémoire avec le sous-process
        # qui tient A ouverte). ------------------------------------------
        self._run("toggle_unchecked", path_a)
        self.assertEqual(self._run("read_local", path_a), "0")
        # B ne doit PAS avoir changé tout seul : rien ne l'a encore
        # rafraîchi (aucun tick simulé sur lui pour l'instant).
        self.assertEqual(self._run("read_local", path_b), "1")

        # -- B converge vers OFF (encore un AUTRE sous-process jetable,
        # qui simule le tick périodique de App._tick sur B). -------------
        converged = self._run("tick_and_read", path_b)
        self.assertEqual(converged, "0", "B doit converger vers OFF après un tick, en vrais process séparés")

        # -- Ferme A et B (chacune se désenregistre PROPREMENT elle-même,
        # via son propre process — voir _HeldWindow.close). --------------
        window_a.close()
        window_b.close()
        remaining = self._run("list_open")
        self.assertEqual(remaining, "", "plus aucun tournoi ouvert après la fermeture de A et B")

        # -- Nouvelle session : ouvre C (sous-process jetable, suffisant
        # pour vérifier son stamping — voir _open_new). -------------------
        self._run("open_new", path_c)

        # RÉSULTAT ATTENDU : C démarre avec primes ON par défaut, jamais
        # avec l'ancienne valeur OFF de la session précédente.
        self.assertEqual(
            self._run("read_local", path_c), "1",
            "un tournoi C d'une NOUVELLE session doit démarrer avec primes ON par défaut",
        )
        self.assertEqual(self._run("read_proposed"), "1")

    def test_fermer_seulement_a_ne_reinitialise_pas_tant_que_b_reste_ouverte(self):
        """Non-régression complémentaire : la fermeture de A seule (B
        encore ouverte) ne doit PAS réinitialiser la valeur proposée —
        seule la fermeture du DERNIER tournoi de la session le fait."""
        path_a = self._path("A2")
        path_b = self._path("B2")
        window_a = self._hold_open(path_a, is_new=True)
        window_b = self._hold_open(path_b, is_new=True)
        self._run("toggle_unchecked", path_a)

        window_a.close()
        self.assertEqual(
            self._run("read_proposed"), "0",
            "B est toujours ouverte : la session reste active, pas de réinitialisation",
        )
        window_b.close()
        self.assertEqual(self._run("read_proposed"), "1", "dernière fenêtre fermée -> réinitialisée")

    def test_plantage_sans_fermeture_propre_puis_nouvelle_session_reste_correcte(self):
        """LE scénario qui a réellement fait échouer une première version
        de ce correctif (voir l'historique de session) : si le dernier
        process d'une session disparaît SANS jamais appeler unregister()
        proprement (ex : "Forcer à quitter", plantage — simulé ici par
        `kill()`, jamais la fermeture propre "close" via _HeldWindow.
        close()), le nettoyage ACTIF de unregister() n'a JAMAIS lieu —
        seule la vérification à la LECTURE dans open_windows.
        primes_enabled_proposed() (auto-réinitialisation si list_open_
        paths() est vide) peut alors garantir qu'un tournoi créé ensuite
        démarre quand même avec les primes activées par défaut."""
        path_a = self._path("A3")
        window_a = self._hold_open(path_a, is_new=True)
        self._run("toggle_unchecked", path_a)
        self.assertEqual(self._run("read_proposed"), "0")

        # "Plantage" : tue le process SANS jamais lui laisser la chance
        # d'appeler unregister() lui-même (contrairement à .close()).
        window_a.proc.kill()
        window_a.proc.wait(timeout=5)
        for stream in (window_a.proc.stdin, window_a.proc.stdout, window_a.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass

        path_c = self._path("C3")
        self._run("open_new", path_c)
        self.assertEqual(
            self._run("read_local", path_c), "1",
            "même après un plantage (jamais de unregister propre), un "
            "tournoi d'une nouvelle session doit démarrer primes ON",
        )


if __name__ == "__main__":
    unittest.main()
