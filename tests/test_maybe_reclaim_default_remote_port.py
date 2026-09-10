"""Tests ciblés de App._maybe_reclaim_default_remote_port (main.py) :
la décision (faut-il tenter de reprendre le port 8765 ?), séparément du
vrai bind() réseau déjà couvert par tests/test_remote_control_end_
tournament_reconnect.py (RemoteControlServer.try_reclaim_default_port).
Doublure légère (voir tests/test_voice_word_bb_rebalance.py pour le même
principe) : aucun vrai serveur HTTP ni base de données ici, seulement la
logique de décision. open_windows est entièrement mocké (jamais d'accès
à ~/.poker_tournament/open_windows.json)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402

TEST_DEFAULT_PORT = 18765  # jamais le vrai 8765, purement conventionnel ici


class _FakeServer:
    def __init__(self, port, running=True, reclaim_succeeds=False):
        self.port = port
        self.is_running = running
        self.reclaim_calls = 0
        self._reclaim_succeeds = reclaim_succeeds

    def try_reclaim_default_port(self):
        self.reclaim_calls += 1
        if self._reclaim_succeeds:
            self.port = TEST_DEFAULT_PORT
            return True
        return False


class _FakeDB:
    path = "/fake/tournoi.tournoi"


class _FakeApp:
    def __init__(self, server):
        self.remote_control_server = server
        self.db = _FakeDB()
        self._remote_control_tournament_name = "Mon tournoi"
        self.refresh_calls = 0

    def _refresh_remote_control_status(self):
        self.refresh_calls += 1


def _call(fake, registry):
    with patch.object(main.remote_control, "DEFAULT_PORT", TEST_DEFAULT_PORT), \
         patch.object(main.open_windows, "list_remote_tournaments", return_value=registry) as list_mock, \
         patch.object(main.open_windows, "update_remote_info") as update_mock:
        main.App._maybe_reclaim_default_remote_port(fake)
        return list_mock, update_mock


class MaybeReclaimDefaultRemotePortTest(unittest.TestCase):
    def test_aucun_serveur_ne_fait_rien(self):
        fake = _FakeApp(server=None)
        # Ne doit lever aucune exception.
        _call(fake, registry=[])

    def test_serveur_arrete_ne_tente_rien(self):
        server = _FakeServer(port=54321, running=False)
        fake = _FakeApp(server)
        _call(fake, registry=[])
        self.assertEqual(server.reclaim_calls, 0)

    def test_deja_sur_le_port_par_defaut_ne_tente_rien(self):
        server = _FakeServer(port=TEST_DEFAULT_PORT, running=True)
        fake = _FakeApp(server)
        _call(fake, registry=[{"pid": 1, "port": TEST_DEFAULT_PORT, "name": "X"}])
        self.assertEqual(server.reclaim_calls, 0)

    def test_quelquun_dautre_tient_deja_le_port_ne_tente_rien(self):
        server = _FakeServer(port=54321, running=True, reclaim_succeeds=True)
        fake = _FakeApp(server)
        registry = [
            {"pid": 999, "port": TEST_DEFAULT_PORT, "name": "Autre tournoi (routeur)"},
            {"pid": os.getpid(), "port": 54321, "name": "Mon tournoi"},
        ]
        _call(fake, registry)
        self.assertEqual(server.reclaim_calls, 0)
        self.assertEqual(server.port, 54321)  # inchangé

    def test_personne_sur_le_port_par_defaut_tente_et_reussit(self):
        server = _FakeServer(port=54321, running=True, reclaim_succeeds=True)
        fake = _FakeApp(server)
        registry = [{"pid": os.getpid(), "port": 54321, "name": "Mon tournoi"}]  # pas de 18765 dans la liste

        list_mock, update_mock = _call(fake, registry)

        self.assertEqual(server.reclaim_calls, 1)
        self.assertEqual(server.port, TEST_DEFAULT_PORT)
        update_mock.assert_called_once_with(fake.db.path, TEST_DEFAULT_PORT, "Mon tournoi")
        self.assertEqual(fake.refresh_calls, 1)

    def test_tentative_echouee_ne_met_pas_a_jour_le_registre(self):
        server = _FakeServer(port=54321, running=True, reclaim_succeeds=False)
        fake = _FakeApp(server)
        registry = [{"pid": os.getpid(), "port": 54321, "name": "Mon tournoi"}]

        _, update_mock = _call(fake, registry)

        self.assertEqual(server.reclaim_calls, 1)
        self.assertEqual(server.port, 54321)  # inchangé, aucune casse
        update_mock.assert_not_called()
        self.assertEqual(fake.refresh_calls, 0)


if __name__ == "__main__":
    unittest.main()
