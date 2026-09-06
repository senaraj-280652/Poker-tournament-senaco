"""Tests ciblés du correctif "perte de connexion iPhone après Fin de la
partie" (voir remote_control.py : RemoteControlServer.try_reclaim_
default_port, /end_tournament -> other_tournaments_remain, et
main.py : App._maybe_reclaim_default_remote_port).

Utilise de vrais RemoteControlServer (ThreadingHTTPServer réels, sur
127.0.0.1) et de vraies requêtes HTTP — le bug concerne le cycle de vie
réel de sockets/processus, une doublure n'aurait rien prouvé. DEFAULT_PORT
est systématiquement remplacé par un port libre choisi dynamiquement
(jamais 8765) : ces tests ne doivent JAMAIS dépendre du vrai port 8765
ni entrer en conflit avec une véritable instance de l'application
éventuellement lancée sur cette machine.

open_windows.list_remote_tournaments est mocké partout où utilisé
(jamais d'accès à ~/.poker_tournament/open_windows.json, le vrai
registre partagé de l'utilisateur)."""
import json
import os
import socket
import sys
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_control  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get(url):
    with urllib.request.urlopen(url, timeout=2) as r:
        return r.status, r.read()


def _post_json(url, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class _RemoteControlTestCase(unittest.TestCase):
    def setUp(self):
        self.test_port = _free_port()
        patcher = patch.object(remote_control, "DEFAULT_PORT", self.test_port)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _make_server(self, name="Tournoi", **kwargs):
        # port=self.test_port explicite : RemoteControlServer.__init__ lie
        # sa valeur par défaut à DEFAULT_PORT au moment de la DÉFINITION du
        # module (pas à l'appel) — patcher l'attribut du module ci-dessus
        # ne change donc pas ce défaut déjà figé, d'où ce paramètre
        # explicite (exactement ce que fait main.py: _start_remote_control_
        # if_enabled en pratique, où DEFAULT_PORT est bien le vrai défaut).
        return remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_tournament_name=lambda: name,
            port=self.test_port,
            **kwargs,
        )


class TryReclaimDefaultPortTest(_RemoteControlTestCase):
    def test_le_survivant_recupere_le_port_apres_la_fermeture_du_routeur(self):
        router = self._make_server("Routeur")
        router.start()
        self.assertEqual(router.port, self.test_port)  # premier arrivé : port par défaut

        survivor = self._make_server("Survivant")
        survivor.start()
        self.addCleanup(survivor.stop)
        self.assertNotEqual(survivor.port, self.test_port)  # déjà pris par le routeur
        old_survivor_port = survivor.port

        # "Fin de la partie" sur le tournoi qui tenait le port par défaut.
        router.stop()

        reclaimed = survivor.try_reclaim_default_port()

        self.assertTrue(reclaimed)
        self.assertEqual(survivor.port, self.test_port)
        self.assertTrue(survivor.is_running)

        # Joignable sur le nouveau port...
        status, _ = _get(f"http://127.0.0.1:{self.test_port}/clock_state")
        self.assertEqual(status, 200)

        # ...plus du tout sur l'ancien (vraiment relâché, pas dupliqué).
        with self.assertRaises(OSError):
            _get(f"http://127.0.0.1:{old_survivor_port}/clock_state")

    def test_deja_sur_le_port_par_defaut_ne_fait_rien(self):
        router = self._make_server()
        router.start()
        self.addCleanup(router.stop)

        self.assertFalse(router.try_reclaim_default_port())
        self.assertEqual(router.port, self.test_port)
        self.assertTrue(router.is_running)

    def test_port_toujours_pris_ne_fait_perdre_aucun_port(self):
        """Le routeur n'a PAS fermé (port par défaut toujours occupé) :
        la tentative doit échouer proprement, sans jamais laisser le
        serveur appelant sans aucun port utilisable (voir la docstring de
        try_reclaim_default_port : le nouveau port n'est adopté qu'une
        fois son bind() confirmé, l'ancien n'est arrêté qu'ensuite)."""
        router = self._make_server("Routeur")
        router.start()
        self.addCleanup(router.stop)

        other = self._make_server("Autre")
        other.start()
        self.addCleanup(other.stop)
        other_port = other.port
        self.assertNotEqual(other_port, self.test_port)

        reclaimed = other.try_reclaim_default_port()

        self.assertFalse(reclaimed)
        self.assertEqual(other.port, other_port)
        self.assertTrue(other.is_running)
        # Toujours pleinement fonctionnel sur son port d'origine.
        status, _ = _get(f"http://127.0.0.1:{other_port}/clock_state")
        self.assertEqual(status, 200)


class EndTournamentOtherTournamentsRemainTest(_RemoteControlTestCase):
    def setUp(self):
        super().setUp()
        self.ended_calls = []
        self.server = self._make_server(
            "Ceci", on_end_tournament=lambda: self.ended_calls.append(True)
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.own_pid = os.getpid()  # capturé par start() via own_pid = os.getpid()

    def test_other_tournaments_remain_true_si_dautres_tournois_existent(self):
        fake_list = [
            {"pid": self.own_pid, "port": self.test_port, "name": "Ceci"},
            {"pid": self.own_pid + 1, "port": 55555, "name": "Un autre tournoi"},
        ]
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=fake_list):
            status, body = _post_json(
                f"http://127.0.0.1:{self.test_port}/end_tournament", {"pid": self.own_pid}
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertTrue(body["other_tournaments_remain"])
        self.assertEqual(self.ended_calls, [True])

    def test_other_tournaments_remain_false_si_seul_tournoi_ouvert(self):
        fake_list = [{"pid": self.own_pid, "port": self.test_port, "name": "Ceci"}]
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=fake_list):
            status, body = _post_json(
                f"http://127.0.0.1:{self.test_port}/end_tournament", {"pid": self.own_pid}
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(body["other_tournaments_remain"])
        self.assertEqual(self.ended_calls, [True])

    def test_refuse_et_ne_ferme_rien_si_le_pid_ne_correspond_pas(self):
        """Comportement déjà existant (non touché par ce correctif) —
        vérifié ici pour garantir que other_tournaments_remain n'a pas
        changé cette garde de sécurité."""
        status, body = _post_json(
            f"http://127.0.0.1:{self.test_port}/end_tournament", {"pid": self.own_pid + 999}
        )
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertNotIn("other_tournaments_remain", body)
        self.assertEqual(self.ended_calls, [])


if __name__ == "__main__":
    unittest.main()
