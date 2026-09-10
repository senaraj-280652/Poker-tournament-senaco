"""Test ciblé du correctif de la cause racine du bug "Menu principal"
inopérant après plusieurs fermetures/ouvertures en chaîne (voir main.py:
spawn_app_process) : le nouveau processus doit toujours recevoir un
stdin neuf et valide (`subprocess.DEVNULL`), jamais hériter du
descripteur de fichier 0 de son parent — sans quoi CPython peut échouer
dès son tout premier démarrage ("Fatal Python error: init_sys_streams
... Bad file descriptor") si cet héritage remonte à un ancêtre dont le
stdin a été invalidé entre-temps.

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
    def _spawn_and_capture(self):
        captured = {}

        def fake_popen(cmd, **popen_kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = popen_kwargs
            return _FakeProc()

        with patch.object(main.subprocess, "Popen", side_effect=fake_popen):
            main.spawn_app_process()
        return captured

    def test_stdin_est_toujours_devnull(self):
        captured = self._spawn_and_capture()
        self.assertEqual(captured["kwargs"].get("stdin"), main.subprocess.DEVNULL)

    def test_pas_de_capture_stdout_stderr(self):
        """spawn_app_process ne redirige jamais stdout/stderr (seul stdin
        est concerné par ce correctif) — un process légitime qui vivrait
        longtemps doit garder sa sortie standard normale."""
        captured = self._spawn_and_capture()
        self.assertNotIn("stdout", captured["kwargs"])
        self.assertNotIn("stderr", captured["kwargs"])

    def test_commande_inchangee(self):
        captured = self._spawn_and_capture()
        self.assertEqual(captured["cmd"][0], main.sys.executable)
        self.assertTrue(captured["cmd"][1].endswith("main.py"))


if __name__ == "__main__":
    unittest.main()
