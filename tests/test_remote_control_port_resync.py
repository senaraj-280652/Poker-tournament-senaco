# -*- coding: utf-8 -*-
"""Non-régression du ConnectionRefusedError intermittent diagnostiqué le
2026-10-20 (suite complète, test_action_log.EndTournamentLoggingTest.
test_fin_de_partie_acceptee) : un port sondé via un socket jetable
(_free_port()) puis réutilisé PLUS TARD pour le vrai bind() de
RemoteControlServer.start() est une vraie fenêtre de course — rien
n'empêche l'OS d'attribuer ce même port à un autre socket entre les deux.
RemoteControlServer.start() sait déjà s'en remettre (repli sur un port
libre quelconque, voir son `except OSError`), mais tout le code de TEST
qui mettait en cache le port AVANT start() (ou sans jamais le
resynchroniser avec self.server.port après) ne suivait pas ce repli — la
requête HTTP partait alors vers un port mort.

Correctif appliqué dans 14 fichiers (voir tests/test_remote_control_
reliability.py:_RemoteControlHttpTestCase.setUp pour le détail) :
construire systématiquement les serveurs de test avec port=0 (choix
atomique par l'OS, aucune fenêtre de course possible) et ne JAMAIS lire
le port ailleurs que sur self.server.port, APRÈS start().

Ce fichier PROUVE le mécanisme : force une VRAIE collision (un socket
jetable occupe délibérément un port précis), construit un
RemoteControlServer en lui demandant CE port occupé (reproduisant
exactement la condition qui déclenche le repli interne de start()), puis
vérifie qu'une requête HTTP construite à partir de self.server.port
(jamais une valeur mise en cache avant start()) atteint bien le serveur
réel — alors qu'une requête vers le port initialement demandé (occupé)
échoue, comme attendu."""
import os
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402


class PortCollisionForcesRealBindResyncTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_port_resync_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
        ):
            self.addCleanup(target.stop)
            target.start()

        # Occupe RÉELLEMENT un port précis (un socket qui écoute pour de
        # bon, pas seulement sondé puis relâché) — reproduit la condition
        # exacte qui fait échouer le bind() interne de RemoteControlServer.
        # start() et déclenche son repli (voir son `except OSError`).
        # Bind sur "0.0.0.0" (toutes interfaces), EXACTEMENT comme
        # _ExclusiveThreadingHTTPServer (voir remote_control.py) — un
        # bind sur "127.0.0.1" spécifiquement ne provoque PAS toujours de
        # conflit avec un bind ultérieur sur "0.0.0.0" au même numéro de
        # port (constaté empiriquement : ce sont deux "noms" de socket
        # distincts pour le noyau) ; il faut occuper la MÊME adresse
        # générique pour garantir une vraie collision EADDRINUSE.
        self._occupying_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(self._occupying_socket.close)
        self._occupying_socket.bind(("0.0.0.0", 0))
        self._occupying_socket.listen(1)
        self.occupied_port = self._occupying_socket.getsockname()[1]

    def test_requete_http_utilise_le_port_reellement_attribue_apres_collision(self):
        server = remote_control.RemoteControlServer(on_word=lambda w: None, port=self.occupied_port)
        server.start()
        self.addCleanup(server.stop)

        # La collision a bien eu lieu : start() n'a PAS pu obtenir le
        # port demandé (déjà occupé par self._occupying_socket) — sinon
        # ce test ne reproduirait rien et passerait pour la mauvaise
        # raison.
        self.assertNotEqual(
            server.port, self.occupied_port,
            "le port occupé aurait dû forcer le repli de start() sur un AUTRE port",
        )

        # Construit la requête à partir du port RÉEL (self.server.port
        # après start()) — jamais une valeur mise en cache avant : c'est
        # exactement le motif désormais appliqué partout dans les 14
        # fichiers corrigés.
        real_url = f"http://127.0.0.1:{server.port}/login"
        import urllib.request
        with urllib.request.urlopen(real_url, timeout=3) as resp:
            self.assertEqual(resp.status, 200)

    def test_le_port_initialement_demande_reste_bien_inatteignable_pour_ce_serveur(self):
        """Contre-épreuve : confirme que la collision simulée est réelle
        — le port qu'on a délibérément occupé ne sert jamais les routes
        de RemoteControlServer (il appartient à self._occupying_socket,
        un simple socket en écoute passive, sans HTTP derrière)."""
        server = remote_control.RemoteControlServer(on_word=lambda w: None, port=self.occupied_port)
        server.start()
        self.addCleanup(server.stop)

        import urllib.error
        import urllib.request
        stale_url = f"http://127.0.0.1:{self.occupied_port}/login"
        with self.assertRaises((urllib.error.URLError, ConnectionError, TimeoutError)):
            urllib.request.urlopen(stale_url, timeout=1)


if __name__ == "__main__":
    unittest.main()
