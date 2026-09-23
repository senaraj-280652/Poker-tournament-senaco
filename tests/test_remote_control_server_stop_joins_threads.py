# -*- coding: utf-8 -*-
"""Correctif de la course diagnostiquée le 2026-09-19 (crash natif
reproductible "Fatal Python error: Illegal instruction" à l'exécution de
la suite complète) : RemoteControlServer.stop() rendait la main sans
jamais attendre la fin des threads de requêtes déjà en cours
(process_request_thread, un par requête HTTP, voir socketserver.
ThreadingMixIn). Un tel thread pouvait donc continuer à exécuter du code
(y compris appeler des fonctions ensuite dépatchées par unittest.mock
dans un test suivant) bien après le retour de stop() — la course
observée en pratique.

Cause exacte, vérifiée dans la bibliothèque standard installée
(Python 3.14) : http.server.ThreadingHTTPServer pose `daemon_threads =
True`, et socketserver._Threads.append() ignore SILENCIEUSEMENT tout
thread daemon :
    def append(self, thread):
        self.reap()
        if thread.daemon:
            return          # jamais suivi si daemon=True
        super().append(thread)
Le `self._threads.join()` que server_close() appelle bien (block_on_close
reste True, jamais modifié) s'exécute donc sur une liste qui n'a JAMAIS
rien contenu — le join existe dans le code, mais est neutralisé par
construction.

Correctif MINIMAL : _ExclusiveThreadingHTTPServer suit lui-même ses
threads de requêtes (indépendamment du mécanisme _Threads, inopérant ici)
et RemoteControlServer.stop()/try_reclaim_default_port() les attendent
avec un budget total BORNÉ (_STOP_REQUEST_THREADS_TIMEOUT_SECONDS),
jamais indéfiniment — daemon_threads N'EST PAS modifié (le passer à False
ferait qu'un thread réellement bloqué sur un client lent empêcherait le
process de quitter, un risque strictement pire que la course corrigée
ici).

Ce fichier ne provoque JAMAIS volontairement le crash natif d'origine
(qui dépend d'un luck de timing sur toute la suite) — il prouve
directement le mécanisme : après stop(), aucun thread de requête suivi
ne doit plus être vivant ; et stop() ne doit jamais attendre plus que le
budget configuré, même si une requête ne se termine jamais."""
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402
import roster  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402


def _free_port():
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _ServerStopTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_stop_join_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            # Phase 4, "Sécurisation du Contrôle à distance" (2026-09-20) :
            # ce fichier teste le cycle de vie des threads de requêtes
            # (stop()), pas les permissions DIRTO — un rôle ADMIN
            # inconditionnel évite de devoir isoler roster.py (fichier
            # RÉEL ~/.poker_tournament/roster.json, jamais touché ici)
            # juste pour que authenticated_jar(..., owner_name=...)
            # retrouve un accès complet, comme avant l'existence des
            # permissions.
            patch.object(roster, "get_group", return_value=roster.ROSTER_GROUP_ADMIN),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"


class ServerStopJoinsRequestThreadsTest(_ServerStopTestCase):
    def setUp(self):
        super().setUp()
        self.handler_started = threading.Event()
        self.release_handler = threading.Event()

        def slow_on_word(word):
            self.handler_started.set()
            self.release_handler.wait(timeout=5)

        self.server = remote_control.RemoteControlServer(on_word=slow_on_word, port=self.port)
        self.server.start()
        self.jar = authenticated_jar(self.base, owner_name="Test Admin")

    def test_stop_attend_la_fin_dune_requete_en_cours_avant_de_rendre_la_main(self):
        """Coeur du correctif : stop() ne doit JAMAIS rendre la main tant
        qu'un thread de requête suivi par ce serveur est encore vivant —
        reproduit exactement la fenêtre de course (thread encore actif au
        moment de l'arrêt), sans jamais provoquer de crash natif."""
        client_result = {}

        def send_slow_action():
            client_result["status"], client_result["body"], _ = http_request(
                self.base, "POST", "/action/mouvements", self.jar
            )

        client_thread = threading.Thread(target=send_slow_action)
        client_thread.start()
        self.addCleanup(client_thread.join, timeout=5)

        self.assertTrue(
            self.handler_started.wait(timeout=2),
            "le thread serveur n'a jamais démarré le traitement de la requête",
        )
        # À cet instant précis, un thread de requête de ce serveur est
        # RÉELLEMENT en cours (bloqué dans on_word) : capture la liste
        # suivie par le correctif pour vérifier son état avant/après stop().
        request_threads = list(self.server._httpd._request_threads)
        self.assertTrue(
            any(t.is_alive() for t in request_threads),
            "aucun thread de requête actif détecté — le test ne reproduit pas la fenêtre de course",
        )

        stop_finished = threading.Event()

        def call_stop():
            self.server.stop()
            stop_finished.set()

        stop_thread = threading.Thread(target=call_stop)
        stop_thread.start()

        # stop() doit rester BLOQUÉ tant que le thread de requête n'est
        # pas relâché — sinon le correctif ne fait rien de plus qu'avant.
        self.assertFalse(
            stop_finished.wait(timeout=0.3),
            "stop() a rendu la main AVANT la fin du thread de requête encore actif",
        )

        self.release_handler.set()  # la requête peut enfin se terminer normalement
        self.assertTrue(stop_finished.wait(timeout=5), "stop() n'est jamais revenu après la fin réelle de la requête")

        # Après stop(), plus AUCUN thread de requête suivi ne doit être vivant.
        for t in request_threads:
            self.assertFalse(t.is_alive(), f"{t} tourne encore après le retour de stop()")


class ServerStopNeverBlocksIndefinitelyTest(_ServerStopTestCase):
    def setUp(self):
        super().setUp()
        # Jamais déclenché PENDANT le test lui-même (c'est précisément ce
        # qui simule un thread de requête réellement bloqué pour de bon)
        # — mais enregistré en addCleanup ICI, avant même de démarrer le
        # serveur, pour être exécuté à coup sûr (même si une assertion du
        # test échoue) : sans ce déblocage, le process_request_thread
        # créé ci-dessous resterait vivant pour tout le reste de
        # l'exécution de la suite (demande du 2026-09-19, suite au
        # diagnostic du crash natif Tcl/Tk — ce thread daemon, laissé
        # indéfiniment vivant, est le seul contributeur concret identifié
        # par l'analyse statique : un thread d'arrière-plan qui traîne
        # peut recevoir, de la part de CPython, un passage du ramasse-
        # miettes cyclique appelé à finaliser un objet Tk/Tcl d'un autre
        # test, ce qui plante). Le déblocage n'affaiblit pas ce qui est
        # vérifié : l'assertion sur la durée de stop() a déjà eu lieu
        # avant qu'il n'ait lieu.
        self.release_handler_never_set = threading.Event()
        self.addCleanup(self.release_handler_never_set.set)
        self.handler_started = threading.Event()

        def stuck_on_word(word):
            self.handler_started.set()
            self.release_handler_never_set.wait()  # bloqué pendant le test, débloqué au cleanup

        self.server = remote_control.RemoteControlServer(on_word=stuck_on_word, port=self.port)
        self.server.start()
        self.jar = authenticated_jar(self.base, owner_name="Test Admin")

    def test_stop_rend_la_main_meme_si_une_requete_ne_se_termine_jamais(self):
        """Exigence explicite de sécurité (demande du 2026-09-19) : même
        si un thread de requête ne se termine JAMAIS (client figé), stop()
        doit rendre la main après un budget borné, jamais indéfiniment —
        budget réduit ici (patch) pour un test rapide et déterministe,
        jamais les 5s réelles de production."""
        def send_request_expected_to_time_out():
            # Le serveur ne répondra jamais (handler bloqué pour de bon) :
            # ce thread client finira par expirer de son côté (timeout
            # de http_request) — capturé ici uniquement pour ne pas
            # polluer la sortie de test d'une exception dans un thread
            # daemon abandonné, sans rapport avec ce qui est vérifié.
            try:
                http_request(self.base, "POST", "/action/mouvements", self.jar)
            except Exception:
                pass

        client_thread = threading.Thread(target=send_request_expected_to_time_out)
        client_thread.daemon = True
        client_thread.start()
        self.assertTrue(self.handler_started.wait(timeout=2))

        with patch.object(remote_control, "_STOP_REQUEST_THREADS_TIMEOUT_SECONDS", 0.3):
            start = time.monotonic()
            self.server.stop()
            elapsed = time.monotonic() - start

        self.assertLess(
            elapsed, 3.0,
            "stop() a attendu bien plus longtemps que le budget configuré — risque de blocage indéfini",
        )


if __name__ == "__main__":
    unittest.main()
