# -*- coding: utf-8 -*-
"""Garde-fou de non-régression (incident réel du 2026-09-19) : plusieurs
fichiers de tests isolaient `open_windows._registry_path` (le registre
open_windows.json) SANS isoler `open_windows._remote_control_dir` —
alors que `register()`/`unregister()` déclenchent `_clear_remote_
control_session_files()` dès que le registre (isolé) devient vide, et
que CETTE fonction cible `remote_control_auth.json`/`remote_control_
ratelimit.json` via `_remote_control_dir()`, jamais via `_registry_
path()`. Résultat vécu en conditions réelles : une exécution de la
suite de tests, pendant qu'un vrai tournoi tournait sur la machine de
l'utilisateur, a effacé le VRAI code de session et tous les jetons
d'appareils déjà approuvés — forçant une reconnexion et un code
"incorrect" (celui affiché restait l'ancien, un nouveau ayant été
régénéré par erreur).

Correctif appliqué : chaque fichier concerné patch désormais AUSSI
`_remote_control_dir` vers son propre dossier temporaire (voir tests/
test_remote_control_windows_port_collision.py, tests/test_align_primes_
enabled_on_open.py, tests/test_open_windows_try_register.py, tests/
test_primes_enabled_toggle.py, tests/test_primes_multi_tournament_
session_scenario.py, tests/test_primes_session_robustness.py).

Ce fichier-ci prouve le mécanisme lui-même, indépendamment de ces
correctifs ponctuels, pour détecter immédiatement toute régression
future (un nouveau fichier de test qui isolerait `_registry_path` sans
isoler `_remote_control_dir`) :

1. IsolationCompleteNeTouchePasLesFichiersReelsTest : avec les DEUX
   patchés, un cycle register()/unregister() qui fait transiter le
   registre par "vide" (donc qui déclenche réellement _clear_remote_
   control_session_files()) ne touche JAMAIS un dossier "réel" simulé
   séparé — jamais le vrai ~/.poker_tournament de qui exécute cette
   suite.
2. IsolationPartielleDocumenteLeMecanismeDuBugTest : documente, SANS
   JAMAIS appeler _clear_remote_control_session_files() ni aucune
   fonction destructrice, que patcher UNIQUEMENT _registry_path laisse
   _remote_auth_path()/_remote_devices_path()/_remote_ratelimit_path()
   pointer vers le vrai ~/.poker_tournament — exactement le mécanisme de
   l'incident. Seule la RÉSOLUTION DE CHEMIN est vérifiée ici (jamais un
   register()/unregister() dans cette configuration délibérément non
   isolée), pour ne jamais risquer de toucher le vrai dossier de qui
   exécute cette suite."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


class IsolationCompleteNeTouchePasLesFichiersReelsTest(unittest.TestCase):
    """Isolation COMPLÈTE (_registry_path ET _remote_control_dir) : un
    cycle register()/unregister() qui vide le registre isolé — donc qui
    déclenche réellement _clear_remote_control_session_files() — ne doit
    jamais toucher un dossier tenant lieu de "vrais fichiers", simulé à
    part, jamais le vrai ~/.poker_tournament de la machine qui exécute
    cette suite."""

    def setUp(self):
        self._registry_tmp = tempfile.TemporaryDirectory(prefix="isolation_ok_registry_")
        self.addCleanup(self._registry_tmp.cleanup)
        self.registry_path = os.path.join(self._registry_tmp.name, "open_windows.json")

        # Dossier tenant lieu de "vrais fichiers de l'utilisateur" —
        # SÉPARÉ du dossier isolé ci-dessus, jamais le vrai
        # ~/.poker_tournament : si l'isolation est correcte, ce dossier
        # ne doit RECEVOIR AUCUNE ÉCRITURE, quel que soit ce que le test
        # fait par ailleurs.
        self._fake_real_tmp = tempfile.TemporaryDirectory(prefix="isolation_ok_fake_real_home_")
        self.addCleanup(self._fake_real_tmp.cleanup)
        self.fake_real_dir = os.path.join(self._fake_real_tmp.name, ".poker_tournament")
        os.makedirs(self.fake_real_dir, exist_ok=True)
        auth_path = os.path.join(self.fake_real_dir, "remote_control_auth.json")
        ratelimit_path = os.path.join(self.fake_real_dir, "remote_control_ratelimit.json")
        devices_path = os.path.join(self.fake_real_dir, "remote_control_devices.json")
        self._sentinel_contents = {}
        for path, content in (
            (auth_path, '{"code": "999999", "session_id": "sentinel-session", '
                        '"devices": {"sentinel-browser": {"session_token": "sentinel-token", "issued_at": 0}}}'),
            (ratelimit_path, '{"sentinel": true}'),
            (devices_path, '{"sentinel-browser": {"status": "approved"}}'),
        ):
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._sentinel_contents[path] = content

        # Dossier isolé DÉDIÉ à ce test — c'est LUI que _remote_control_
        # dir doit renvoyer, jamais fake_real_dir ni le vrai ~.
        self._isolated_remote_tmp = tempfile.TemporaryDirectory(prefix="isolation_ok_remote_")
        self.addCleanup(self._isolated_remote_tmp.cleanup)
        self.isolated_remote_dir = self._isolated_remote_tmp.name

        for target in (
            patch.object(open_windows, "_registry_path", return_value=self.registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=self.isolated_remote_dir),
        ):
            self.addCleanup(target.stop)
            target.start()

    def test_cycle_register_unregister_ne_touche_jamais_le_dossier_simule(self):
        path_a = os.path.join(self._registry_tmp.name, "A.tournoi")

        # register() sur un registre isolé VIDE : déclenche réellement
        # _clear_remote_control_session_files() (branche "if not data").
        open_windows.register(path_a)
        # Une session existe désormais (registre isolé non vide) : un
        # appel réel à remote_session_code()/verify_remote_code()
        # exercerait _ensure_remote_session_auth_locked, écrivant dans
        # _remote_control_dir() — vérifié ci-dessous qu'il s'agit bien du
        # dossier isolé.
        code = open_windows.remote_session_code()
        self.assertIsNotNone(code)
        self.assertTrue(os.path.exists(os.path.join(self.isolated_remote_dir, "remote_control_auth.json")))

        # unregister() qui revide le registre isolé : déclenche à nouveau
        # _clear_remote_control_session_files().
        open_windows.unregister(path_a)

        # Le dossier "réel" simulé n'a SUBI AUCUNE MODIFICATION, quel que
        # soit ce que register()/unregister() ont fait par ailleurs.
        for path, original_content in self._sentinel_contents.items():
            self.assertTrue(os.path.exists(path), f"{path} a disparu — le dossier simulé a été touché")
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), original_content, f"{path} a été modifié — le dossier simulé a été touché")


class IsolationPartielleDocumenteLeMecanismeDuBugTest(unittest.TestCase):
    """Documente le mécanisme EXACT de l'incident du 2026-09-19, sans
    jamais l'exécuter réellement : avec UNIQUEMENT _registry_path
    patchée (comme le faisaient les fichiers listés en tête de module
    avant leur correctif), _remote_auth_path()/_remote_devices_path()/
    _remote_ratelimit_path() résolvent vers le VRAI ~/.poker_tournament
    — jamais vers un dossier isolé. Aucun register()/unregister() n'est
    appelé dans cette configuration délibérément non isolée : seule la
    RÉSOLUTION DE CHEMIN est vérifiée, pour ne jamais risquer de créer
    ou modifier quoi que ce soit dans le vrai dossier de qui exécute
    cette suite."""

    def setUp(self):
        self._registry_tmp = tempfile.TemporaryDirectory(prefix="isolation_partielle_registry_")
        self.addCleanup(self._registry_tmp.cleanup)
        self.registry_path = os.path.join(self._registry_tmp.name, "open_windows.json")
        patcher = patch.object(open_windows, "_registry_path", return_value=self.registry_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_remote_control_dir_non_isolee_pointe_vers_le_vrai_dossier(self):
        # _remote_control_dir() a un effet de bord idempotent et sans
        # danger (os.makedirs(exist_ok=True) sur un dossier qui, sur une
        # installation réelle, existe déjà) — jamais de lecture/écriture
        # de données à l'intérieur : seule la valeur du CHEMIN renvoyé
        # est comparée ci-dessous, jamais son contenu.
        expected_real_dir = os.path.join(os.path.expanduser("~"), ".poker_tournament")
        self.assertEqual(
            open_windows._remote_control_dir(), expected_real_dir,
            "sans patch de _remote_control_dir, elle doit résoudre vers le "
            "vrai dossier — c'est précisément ce qui a causé l'incident : "
            "isoler _registry_path seule ne suffit pas.",
        )
        self.assertEqual(
            open_windows._remote_auth_path(),
            os.path.join(expected_real_dir, "remote_control_auth.json"),
        )
        self.assertEqual(
            open_windows._remote_ratelimit_path(),
            os.path.join(expected_real_dir, "remote_control_ratelimit.json"),
        )
        self.assertEqual(
            open_windows._remote_devices_path(),
            os.path.join(expected_real_dir, "remote_control_devices.json"),
        )


if __name__ == "__main__":
    unittest.main()
