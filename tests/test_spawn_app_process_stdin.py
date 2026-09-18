"""Test ciblé du correctif de la cause racine du bug "Menu principal"
inopérant après plusieurs fermetures/ouvertures en chaîne (voir main.py:
spawn_app_process) : le nouveau processus doit toujours recevoir un
stdin neuf et valide (`subprocess.DEVNULL`), jamais hériter du
descripteur de fichier 0 de son parent — sans quoi CPython peut échouer
dès son tout premier démarrage ("Fatal Python error: init_sys_streams
... Bad file descriptor") si cet héritage remonte à un ancêtre dont le
stdin a été invalidé entre-temps.

Étendu le 2026-09-18 : le même raisonnement s'applique désormais à
stdout ET stderr (diagnostic "le nouveau processus s'est arrêté
immédiatement (code 1)" reproduit deux fois sur une vraie instance dont
`lsof` a montré les fd 1/2 "(revoked)" — hérités tels quels par le
nouveau process jusqu'ici, puisque seul stdin était redirigé). Les
anciens tests qui affirmaient l'absence de toute redirection stdout/
stderr sont donc désormais FAUX par construction et ont été remplacés
ci-dessous par leur exact inverse.

Mocke subprocess.Popen (jamais de vrai process lancé ici — testé par
ailleurs, avec de vrais process, dans test_menu_principal_after_iphone_
close.py) : ce test porte spécifiquement sur les arguments passés à
Popen."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeProc:
    def __init__(self, pid=99999):
        self.pid = pid

    def poll(self):
        return None  # toujours vivant, pour ce test


class SpawnAppProcessStdinTest(unittest.TestCase):
    def _spawn_and_capture(self, **spawn_kwargs):
        captured = {}

        def fake_popen(cmd, **popen_kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = popen_kwargs
            return _FakeProc()

        with patch.object(main.subprocess, "Popen", side_effect=fake_popen):
            main.spawn_app_process(**spawn_kwargs)
        return captured

    def test_stdin_est_toujours_devnull(self):
        captured = self._spawn_and_capture()
        self.assertEqual(captured["kwargs"].get("stdin"), main.subprocess.DEVNULL)

    def test_stdout_nest_pas_herite_du_parent(self):
        """stdout ne doit jamais être absent des kwargs (= hérité tel
        quel du parent) : il doit pointer vers un descripteur ouvert par
        CE process, jamais dépendre de l'état des fd du parent."""
        captured = self._spawn_and_capture()
        self.assertIn("stdout", captured["kwargs"])
        self.assertIsNotNone(captured["kwargs"]["stdout"])

    def test_stderr_nest_pas_herite_du_parent(self):
        captured = self._spawn_and_capture()
        self.assertIn("stderr", captured["kwargs"])
        self.assertIsNotNone(captured["kwargs"]["stderr"])

    def test_stdout_stderr_utilisent_un_descripteur_valide(self):
        """Le fichier passé à Popen pour stdout/stderr doit avoir un
        fileno() valide (pas fermé) AU MOMENT de l'appel à Popen — c'est
        ce que subprocess duplique pour le nouveau process ; peu importe
        que ce fichier soit refermé côté parent juste après (voir le
        test dédié plus bas), Popen a déjà fait sa propre copie."""
        captured = {}

        real_popen = main.subprocess.Popen

        def fake_popen(cmd, **popen_kwargs):
            # Vérifie ICI, avant que spawn_app_process ne referme son
            # `with open(...)` — c'est le seul moment où le fichier est
            # garanti encore ouvert côté appelant.
            for key in ("stdout", "stderr"):
                f = popen_kwargs.get(key)
                self.assertIsNotNone(f, f"{key} manquant")
                self.assertFalse(f.closed, f"{key} déjà fermé au moment de l'appel à Popen")
                # lève ValueError si le descripteur est invalide/fermé.
                fd = f.fileno()
                self.assertGreaterEqual(fd, 0)
            captured["cmd"] = cmd
            captured["kwargs"] = popen_kwargs
            return _FakeProc()

        with patch.object(main.subprocess, "Popen", side_effect=fake_popen):
            main.spawn_app_process()
        self.assertTrue(captured)

    def test_meme_fichier_de_log_reutilise_le_repertoire_existant(self):
        """Le fichier de sortie doit vivre dans ~/.poker_tournament (même
        répertoire que crash.log), pas ailleurs — voir
        _spawned_child_log_path."""
        captured = self._spawn_and_capture()
        log_name = captured["kwargs"]["stdout"].name
        self.assertEqual(
            os.path.dirname(log_name),
            os.path.dirname(main._crash_log_path()),
        )
        self.assertEqual(os.path.basename(log_name), "menu_principal_child.log")

    def test_stdout_et_stderr_pointent_vers_le_meme_fichier(self):
        captured = self._spawn_and_capture()
        self.assertEqual(
            captured["kwargs"]["stdout"].name, captured["kwargs"]["stderr"].name,
        )

    def test_fichier_de_log_reste_valide_apres_fermeture_cote_parent(self):
        """Exigence explicite : le process enfant doit garder un
        descripteur valide même une fois que le parent a refermé son
        propre objet fichier après Popen — comportement standard de
        subprocess.Popen (duplication du fd). Vérifié avec un VRAI Popen
        (pas de mock), mais SANS lancer main.py/Tk (aucun intérêt ici, et
        risquerait de laisser une vraie fenêtre "Bienvenue" ouverte) :
        seule la commande réellement exécutée est remplacée par un
        script Python minimal qui écrit sur stdout/stderr puis quitte —
        tous les kwargs (stdin/stdout/stderr/env), eux, restent
        exactement ceux construits par spawn_app_process."""
        import subprocess
        import tempfile

        real_popen = subprocess.Popen

        def fake_popen(cmd, **popen_kwargs):
            # Ignore `cmd` (main.py) : lance un script inoffensif à la
            # place, en conservant tous les autres kwargs à l'identique.
            tiny_script = "import sys; sys.stdout.write('out-ok\\n'); sys.stderr.write('err-ok\\n')"
            return real_popen([sys.executable, "-c", tiny_script], **popen_kwargs)

        with tempfile.TemporaryDirectory() as tmp:
            fake_log = os.path.join(tmp, "menu_principal_child.log")
            with patch.object(main, "_spawned_child_log_path", return_value=fake_log), \
                 patch.object(main.subprocess, "Popen", side_effect=fake_popen):
                proc = main.spawn_app_process()
                # À ce stade, le `with open(...)` de spawn_app_process est
                # déjà refermé côté parent (on est sorti de la fonction) :
                # exactement le scénario à couvrir.
                self.addCleanup(lambda: proc.poll() is None and proc.kill())
                self.assertEqual(proc.wait(timeout=10), 0)
            with open(fake_log, encoding="utf-8", errors="replace") as f:
                content = f.read()
        # Les DEUX doivent être présents : la preuve que stdout ET stderr
        # ont pu écrire dans ce fichier après que le parent a fermé son
        # propre descripteur — celui du process enfant reste valide.
        self.assertIn("out-ok", content)
        self.assertIn("err-ok", content)

    def test_lancement_interne_conserve_la_variable_denvironnement(self):
        """Non-régression explicite : ce correctif ne doit rien changer
        au marqueur POKER_TOURNAMENT_INTERNAL_LAUNCH (voir
        _acquire_menu_principal_lock_if_needed, qui en dépend)."""
        captured = self._spawn_and_capture(internal_menu_child=True)
        env = captured["kwargs"].get("env")
        self.assertIsNotNone(env)
        self.assertEqual(env.get(main.POKER_TOURNAMENT_INTERNAL_LAUNCH), "1")

    def test_pas_de_marqueur_interne_sans_internal_menu_child(self):
        captured = self._spawn_and_capture()
        self.assertIsNone(captured["kwargs"].get("env"))

    def test_commande_inchangee(self):
        captured = self._spawn_and_capture()
        self.assertEqual(captured["cmd"][0], main.sys.executable)
        self.assertTrue(captured["cmd"][1].endswith("main.py"))


if __name__ == "__main__":
    unittest.main()
