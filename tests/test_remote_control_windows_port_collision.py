# -*- coding: utf-8 -*-
"""Diagnostic ET correctif du 502 multi-tournois reproduit sur le HP le
2026-09-15 (TEST 4) : "Ce tournoi n'est momentanément plus joignable"
quand l'iPhone sélectionne, depuis le Lobby, un tournoi qui n'est PAS
celui qui héberge le routeur (port 8765).

CAUSE DÉMONTRÉE (voir le rapport de diagnostic livré séparément pour
l'analyse complète) : RemoteControlServer.start()/try_reclaim_default_
port() n'ont AUCUN moyen de savoir si un port est déjà utilisé par un
AUTRE processus autrement qu'en observant si le serveur HTTP lève
OSError. http.server.HTTPServer (dont hérite ThreadingHTTPServer) pose
SO_REUSEADDR avant chaque bind() (voir socketserver.TCPServer.
server_bind, HTTPServer.allow_reuse_address = True). Sur POSIX
(macOS/Linux), SO_REUSEADDR ne fait sauter QUE la contrainte TIME_WAIT :
un bind() sur un port où un AUTRE socket écoute déjà ACTIVEMENT échoue
toujours avec OSError (voir DeuxServeursReelsPosixTest ci-dessous, qui
passe SANS AUCUN changement après ce correctif). Mais sous Windows,
SO_REUSEADDR a un comportement radicalement différent (documenté par
Microsoft) : il autorise PLUSIEURS sockets à bind() ET écouter
SIMULTANÉMENT sur le MÊME port, sans jamais lever d'erreur — c'est ce
qui a laissé PID 5296 et PID 6832 croire, tous deux, avoir obtenu le
port 8765.

CORRECTIF (remote_control.py: _ExclusiveThreadingHTTPServer) : sous
Windows UNIQUEMENT, pose SO_EXCLUSIVEADDRUSE à la place de SO_REUSEADDR
(jamais les deux ensemble sur le même socket, incompatible selon
Microsoft) — restaure sous Windows la garantie déjà vraie sous POSIX,
sans toucher à l'architecture (routeur/relais/Lobby inchangés) ni au
comportement POSIX existant (précaution explicite : SO_REUSEADDR y est
aussi ce qui permet à try_reclaim_default_port() de refonctionner tout
de suite après la fermeture d'un routeur ayant réellement servi des
requêtes HTTP, sans attendre l'expiration de TIME_WAIT — le retirer
purement et simplement aurait été une régression POSIX).

Structure de ce fichier :
1. _RemoteControlTestCase : fixture partagée (comme tests/test_remote_
   control_end_tournament_reconnect.py).
2. DeuxServeursReelsPosixTest : scénario complet avec de VRAIS sockets
   sur cette machine (POSIX) — A obtient 8765, B un autre port réel, le
   registre contient les deux vrais ports, le Lobby peut relayer vers
   les deux, A ferme et B récupère 8765 (registre mis à jour), PUIS
   l'ordre inverse (B ferme, A continue sur 8765 sans interruption).
3. ExclusiveThreadingHTTPServerServerBindTest : teste directement
   server_bind() sur les DEUX branches (Windows simulé / POSIX réel),
   sans dépendre d'une vraie machine Windows ni d'un vrai
   socket.SO_EXCLUSIVEADDRUSE (absent de macOS)."""
import os
import socket
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402
import remote_control  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _RemoteControlTestCase(unittest.TestCase):
    """Mêmes précautions que tests/test_remote_control_end_tournament_
    reconnect.py (DEFAULT_PORT patché vers un port libre, jamais le vrai
    8765 ; open_windows redirigé vers un dossier temporaire dédié)."""

    def setUp(self):
        self.test_port = _free_port()
        patcher = patch.object(remote_control, "DEFAULT_PORT", self.test_port)
        self.addCleanup(patcher.stop)
        patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_winbug_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        lock_path = os.path.join(self._tmp.name, "open_windows.lock")
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_registry_lock_path", return_value=lock_path),
        ):
            self.addCleanup(target.stop)
            target.start()

    def _make_server(self, name="Tournoi"):
        # port=self.test_port explicite : RemoteControlServer.__init__ lie
        # sa valeur par défaut à DEFAULT_PORT au moment de la DÉFINITION du
        # module (pas à l'appel) — voir la même remarque dans tests/
        # test_remote_control_end_tournament_reconnect.py.
        return remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_tournament_name=lambda: name,
            port=self.test_port,
        )


class DeuxServeursReelsPosixTest(_RemoteControlTestCase):
    """Scénario complet demandé, avec de VRAIS sockets — PASSE sans
    aucune modification après le correctif (POSIX intégralement
    inchangé) : A obtient le port public, B obtient un port réel
    distinct, le registre partagé contient les deux vrais ports, A
    ferme et B peut récupérer le port public (registre mis à jour), et
    dans l'ordre inverse (B ferme en premier), A n'est jamais affecté."""

    def test_scenario_complet_A_puis_B_puis_fermeture_A_puis_reclaim_B(self):
        path_a = os.path.join(self._tmp.name, "A.tournoi")
        path_b = os.path.join(self._tmp.name, "B.tournoi")

        # 1. A démarre et obtient le port public (8765 en réalité).
        open_windows.register(path_a)
        server_a = self._make_server("Tournoi A")
        server_a.start()
        self.assertEqual(server_a.port, self.test_port)
        open_windows.update_remote_info(path_a, server_a.port, "Tournoi A")

        # 2. B démarre PENDANT que A fonctionne encore.
        open_windows.register(path_b)
        server_b = self._make_server("Sit & Go B")
        server_b.start()
        self.addCleanup(lambda: server_b.stop() if server_b.is_running else None)
        # 3. B échoue proprement sur le port public et obtient un AUTRE
        #    port réel.
        self.assertNotEqual(server_b.port, self.test_port)
        self.assertTrue(server_b.is_running)
        open_windows.update_remote_info(path_b, server_b.port, "Sit & Go B")

        # 4. open_windows.json contient les DEUX ports, tous les deux
        #    réels et différents.
        by_name = {t["name"]: t for t in open_windows.list_remote_tournaments()}
        self.assertEqual(by_name["Tournoi A"]["port"], self.test_port)
        self.assertEqual(by_name["Sit & Go B"]["port"], server_b.port)
        self.assertNotEqual(by_name["Tournoi A"]["port"], by_name["Sit & Go B"]["port"])

        # 5. Le Lobby (servi par A, sur le port public) peut relayer vers
        #    A comme vers B : on vérifie ici qu'il DISPOSE bien des deux
        #    vrais ports nécessaires à _proxy/resolve_proxy_port (déjà
        #    couverts par ailleurs, voir remote_control.py).
        self.assertIn(self.test_port, (t["port"] for t in open_windows.list_remote_tournaments()))
        self.assertIn(server_b.port, (t["port"] for t in open_windows.list_remote_tournaments()))
        b_port_before_reclaim = server_b.port

        # 6. A ferme.
        server_a.stop()
        open_windows.unregister(path_a)

        # 7. B doit pouvoir récupérer le port public.
        reclaimed = server_b.try_reclaim_default_port()
        self.assertTrue(reclaimed)
        self.assertEqual(server_b.port, self.test_port)
        self.assertTrue(server_b.is_running)

        # 8. open_windows.json doit refléter le NOUVEAU port de B (voir
        #    main.py: _maybe_reclaim_default_remote_port, qui appelle
        #    update_remote_info après un reclaim réussi — reproduit ici
        #    directement, ce fichier ne teste pas App elle-même).
        open_windows.update_remote_info(path_b, server_b.port, "Sit & Go B")
        by_name = {t["name"]: t for t in open_windows.list_remote_tournaments()}
        self.assertEqual(by_name["Sit & Go B"]["port"], self.test_port)

        # 9. L'iPhone (adresse publique = self.test_port, 8765 en
        #    réalité) peut continuer à joindre B directement dessus.
        self.assertEqual(server_b.url.rsplit(":", 1)[1], str(self.test_port))
        self.assertNotEqual(server_b.port, b_port_before_reclaim)

    def test_ordre_inverse_B_ferme_A_continue_normalement_sur_le_port_public(self):
        """Non-régression explicite demandée : si c'est B (pas le
        détenteur du port public) qui ferme, A ne doit JAMAIS être
        affecté — aucun changement de port, aucune interruption."""
        path_a = os.path.join(self._tmp.name, "A.tournoi")
        path_b = os.path.join(self._tmp.name, "B.tournoi")

        open_windows.register(path_a)
        server_a = self._make_server("Tournoi A")
        server_a.start()
        self.addCleanup(server_a.stop)
        self.assertEqual(server_a.port, self.test_port)
        open_windows.update_remote_info(path_a, server_a.port, "Tournoi A")

        open_windows.register(path_b)
        server_b = self._make_server("Sit & Go B")
        server_b.start()
        self.assertNotEqual(server_b.port, self.test_port)
        open_windows.update_remote_info(path_b, server_b.port, "Sit & Go B")

        # B ferme.
        server_b.stop()
        open_windows.unregister(path_b)

        # A reste strictement inchangé : toujours en vie, toujours sur
        # le même port, jamais sollicité par une tentative de reclaim
        # (déjà sur DEFAULT_PORT — voir try_reclaim_default_port).
        self.assertTrue(server_a.is_running)
        self.assertEqual(server_a.port, self.test_port)
        self.assertFalse(server_a.try_reclaim_default_port())  # déjà dessus : rien à faire

        by_name = {t["name"]: t for t in open_windows.list_remote_tournaments()}
        self.assertEqual(set(by_name), {"Tournoi A"})
        self.assertEqual(by_name["Tournoi A"]["port"], self.test_port)


class ExclusiveThreadingHTTPServerServerBindTest(unittest.TestCase):
    """Vérifie DIRECTEMENT server_bind() de _ExclusiveThreadingHTTPServer
    sur ses deux branches, sans construire de vrai serveur (donc sans
    dépendre de la présence réelle de socket.SO_EXCLUSIVEADDRUSE, qui
    n'existe que sous Windows — absente de macOS/Linux). Utilise
    __new__ (jamais __init__, qui créerait un vrai socket et bind()rait
    réellement) pour obtenir une instance dont `type(instance) is
    _ExclusiveThreadingHTTPServer` : nécessaire pour que le super().
    server_bind() interne se résolve correctement."""

    def _bare_instance(self):
        inst = remote_control._ExclusiveThreadingHTTPServer.__new__(
            remote_control._ExclusiveThreadingHTTPServer
        )
        inst.socket = MagicMock()
        inst.server_address = ("0.0.0.0", 8765)
        return inst

    def test_sous_windows_pose_so_exclusiveaddruse_jamais_so_reuseaddr(self):
        inst = self._bare_instance()
        fake_option = 0xBEEF  # valeur arbitraire : seule l'identité compte ici
        with patch.object(remote_control.sys, "platform", "win32"), \
             patch.object(remote_control.socket, "SO_EXCLUSIVEADDRUSE", fake_option, create=True), \
             patch.object(remote_control.ThreadingHTTPServer, "server_bind") as mock_super_bind:
            inst.socket.getsockname.return_value = ("0.0.0.0", 8765)
            inst.server_bind()

        # SO_EXCLUSIVEADDRUSE posé, avec la valeur 1 (activé) — jamais
        # SO_REUSEADDR (voir l'absence totale d'appel à la classe
        # parente ci-dessous, qui est la SEULE à poser SO_REUSEADDR).
        inst.socket.setsockopt.assert_called_once_with(socket.SOL_SOCKET, fake_option, 1)
        # bind() réel appelé directement (jamais via la classe parente,
        # qui poserait SO_REUSEADDR en plus).
        inst.socket.bind.assert_called_once_with(("0.0.0.0", 8765))
        mock_super_bind.assert_not_called()

    def test_hors_windows_delegue_integralement_a_la_classe_parente(self):
        """POSIX : AUCUNE différence avec avant ce correctif — server_bind
        appelle uniquement super().server_bind() (qui pose SO_REUSEADDR
        comme toujours), jamais SO_EXCLUSIVEADDRUSE."""
        inst = self._bare_instance()
        with patch.object(remote_control.sys, "platform", "darwin"), \
             patch.object(remote_control.ThreadingHTTPServer, "server_bind") as mock_super_bind:
            inst.server_bind()

        inst.socket.setsockopt.assert_not_called()
        inst.socket.bind.assert_not_called()  # c'est la classe parente qui l'aurait fait
        mock_super_bind.assert_called_once()

    def test_windows_sans_lattribut_so_exclusiveaddruse_retombe_sur_le_parent(self):
        """Filet de sécurité : si jamais socket.SO_EXCLUSIVEADDRUSE
        n'existait pas (jamais le cas en pratique sous Windows, mais
        cette branche protège contre une build Python inhabituelle) —
        jamais de crash, simple repli sur le comportement POSIX habituel."""
        inst = self._bare_instance()
        with patch.object(remote_control.sys, "platform", "win32"):
            self.assertFalse(hasattr(remote_control.socket, "SO_EXCLUSIVEADDRUSE"))
            with patch.object(remote_control.ThreadingHTTPServer, "server_bind") as mock_super_bind:
                inst.server_bind()
        inst.socket.setsockopt.assert_not_called()
        mock_super_bind.assert_called_once()


if __name__ == "__main__":
    unittest.main()
