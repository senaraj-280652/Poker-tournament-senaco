# -*- coding: utf-8 -*-
"""Tests du chantier "fiabiliser la communication téléphone <-> PC"
(demande du 2026-09-19, suite au diagnostic de l'incident réel en club
avec la production v1.2.41 — page Éliminations/Joueurs devenue muette
alors que le PC continuait à fonctionner normalement) :

- POST /eliminate doit être IDEMPOTENT côté serveur (jamais seulement
  côté JavaScript) : un même request_id envoyé deux fois (retry après
  perte de la réponse HTTP) ne doit produire qu'UN SEUL effet métier
  (une seule élimination, un seul kill, un seul transfert de bounty) ;
  deux request_id différents doivent, eux, continuer à produire deux
  actions distinctes normalement.
- Un journal de diagnostic SOBRE (remote_control.log_remote_event) doit
  exister pour les événements anormaux (401, 502/proxy injoignable,
  doublon ignoré) — jamais pour chaque requête réussie, jamais de
  cookie/secret dans son contenu.
- Le relais (_proxy) vers un tournoi devenu injoignable doit renvoyer
  502 ET journaliser l'événement, sans jamais planter le serveur.

Ce fichier NE teste PAS le JavaScript (non exécutable par cette suite
Python) — voir le plan de validation manuelle iPhone livré séparément.
Il isole ce qui EST vérifiable côté serveur : la garantie d'idempotence
et la génération des logs, qui sont les deux points sur lesquels le
JavaScript ne doit surtout pas être la seule ligne de défense."""
import http.cookiejar
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
import remote_control  # noqa: E402
import roster  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _new_db(tmp_dir, filename, **settings):
    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    if settings:
        db.set_settings({k: str(v) for k, v in settings.items()})
    return db


def _new_db_cross_thread(tmp_dir, filename, **settings):
    """Comme _new_db, mais la connexion SQLite autorise explicitement
    d'être utilisée depuis un thread différent de celui qui l'a créée
    (check_same_thread=False) — nécessaire UNIQUEMENT pour ce fichier de
    test : il reproduit App._poll_voice_queue tournant sur un thread
    séparé du thread qui a construit self.db (setUp), alors que la vraie
    application n'a jamais ce problème (self.db est TOUJOURS créé ET
    consommé sur le même thread Tk, voir App.__init__/_poll_voice_queue
    — jamais touché depuis le thread HTTP, c'est justement tout le sens
    de voice_command_queue). Reproduit exactement la configuration
    (row_factory) posée par Database.__init__."""
    import sqlite3

    db = _new_db(tmp_dir, filename, **settings)
    path = db.path
    db.conn.close()
    db.conn = sqlite3.connect(path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    return db


# =====================================================================
# 1. Idempotence de POST /eliminate (côté serveur, logique métier réelle)
# =====================================================================
class _FakeRemoteApp:
    """Même doublure que tests/test_test_mode_and_mandatory_eliminator.py
    (_FakeRemoteApp), complétée de _remote_action_dedup (nouveau, pour
    _remote_eliminate_request) et d'un consommateur de file d'attente
    tournant dans un VRAI thread séparé — reproduit fidèlement
    App._poll_voice_queue (main.py), qui tourne normalement sur le
    thread Tk pendant que _remote_eliminate_request (thread HTTP)
    attend son résultat."""

    def __init__(self, db):
        self.db = db
        self.voice_command_queue = queue.Queue()
        self._remote_elimination_results = {}
        self._remote_action_dedup = {}
        self.clock_window = None
        self._consumer_stop = threading.Event()
        self._consumer_thread = threading.Thread(target=self._consume, daemon=True)
        self.processed_count = 0
        # Lie la VRAIE méthode (jamais une réimplémentation dans ce test)
        # à cette doublure, comme _remote_eliminate ci-dessous.
        self._remote_eliminate_request = types.MethodType(main.App._remote_eliminate_request, self)
        self._prune_remote_action_dedup = types.MethodType(main.App._prune_remote_action_dedup, self)
        self._consumer_thread.start()

    def _consume(self):
        while not self._consumer_stop.is_set():
            try:
                item = self.voice_command_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(item, tuple) and item and item[0] == "eliminate":
                _, eliminated_id, eliminator_id, request_id = item
                self.processed_count += 1
                main.App._remote_eliminate(self, eliminated_id, eliminator_id, request_id=request_id)

    def stop(self):
        self._consumer_stop.set()
        self._consumer_thread.join(timeout=2)

    def _queue_elimination_banner(self, *a, **k):
        pass

    def _trigger_movement_alert(self, *a, **k):
        pass

    def _finish_movement_alert(self, *a, **k):
        pass

    def _refresh_all(self):
        pass

    def _refresh_remote_players_cache(self):
        pass

    def _refresh_remote_moves_cache(self):
        pass

    def _check_pending_rebalance(self):
        pass


class RemoteEliminateIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_eliminate_idempotency_")
        self.addCleanup(self._tmp.cleanup)
        # _LOG_PATH/_logger (demande du 2026-09-19, incident réel où
        # l'absence de ce patch a écrit "duplicate_action_ignored" dans le
        # VRAI ~/.poker_tournament/remote_control.log pendant qu'un
        # tournoi réel tournait) : App._remote_eliminate_request appelle
        # remote_control.log_remote_event sur un doublon en cours de
        # traitement (voir test_requete_dupliquee_en_cours_de_traitement_
        # nest_jamais_reenfilee ci-dessous) — jamais isolé jusqu'ici, ce
        # test-ci n'ayant besoin d'aucun serveur HTTP réel (contrairement
        # à _RemoteControlHttpTestCase plus bas, qui patch déjà les deux).
        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        log_patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        logger_patcher = patch.object(remote_control, "_logger", None)
        self.addCleanup(log_patcher.stop)
        self.addCleanup(logger_patcher.stop)
        log_patcher.start()
        logger_patcher.start()
        self.db = _new_db_cross_thread(self._tmp.name, "t.tournoi", pko_mode=0, bounty_amount=50)
        self.addCleanup(self.db.conn.close)
        self.alice = self.db.add_player("Alice")
        self.bob = self.db.add_player("Bob")
        self.chris = self.db.add_player("Chris")
        self.fake = _FakeRemoteApp(self.db)
        self.addCleanup(self.fake.stop)

    def test_meme_request_id_envoye_deux_fois_un_seul_effet(self):
        """Coeur de la demande : le téléphone rejoue EXACTEMENT la même
        requête (même client_request_id) après avoir perdu la réponse
        HTTP de la première tentative — aucun deuxième kill, aucun
        deuxième transfert de bounty, aucune deuxième élimination."""
        result1 = self.fake._remote_eliminate_request(self.bob, self.alice, client_request_id="dup-1")
        result2 = self.fake._remote_eliminate_request(self.bob, self.alice, client_request_id="dup-1")

        self.assertTrue(result1["ok"])
        self.assertEqual(result1, result2)  # le doublon renvoie EXACTEMENT le même résultat, jamais recalculé
        self.assertEqual(self.fake.processed_count, 1)  # un seul passage réel par _remote_eliminate

        bob_row = self.db.get_player(self.bob)
        alice_row = self.db.get_player(self.alice)
        self.assertEqual(bob_row["status"], "eliminated")
        self.assertEqual(alice_row["kills"], 1)          # jamais 2
        self.assertEqual(alice_row["bounty_won"], 50)    # jamais 100

    def test_deux_request_id_differents_deux_actions_distinctes(self):
        """Non-régression explicite : deux VRAIES éliminations (request_id
        différents) doivent continuer à s'appliquer normalement, sans
        que le mécanisme d'idempotence n'en bloque une par erreur."""
        result1 = self.fake._remote_eliminate_request(self.bob, self.alice, client_request_id="req-A")
        result2 = self.fake._remote_eliminate_request(self.chris, self.alice, client_request_id="req-B")

        self.assertTrue(result1["ok"])
        self.assertTrue(result2["ok"])
        self.assertEqual(self.fake.processed_count, 2)

        alice_row = self.db.get_player(self.alice)
        self.assertEqual(alice_row["kills"], 2)
        self.assertEqual(alice_row["bounty_won"], 100)
        self.assertEqual(self.db.get_player(self.bob)["status"], "eliminated")
        self.assertEqual(self.db.get_player(self.chris)["status"], "eliminated")

    def test_requete_dupliquee_en_cours_de_traitement_nest_jamais_reenfilee(self):
        """Cas plus dur qu'un simple retry séquentiel : deux requêtes
        avec le MÊME client_request_id arrivent quasi simultanément
        (deux threads HTTP concurrents, cas réel d'un double-tap ou d'un
        retry très rapide) — la seconde doit ATTENDRE le résultat de la
        première, jamais enfiler une deuxième élimination pendant que la
        première est encore en cours de traitement."""
        results = []
        errors = []

        def call():
            try:
                results.append(self.fake._remote_eliminate_request(self.bob, self.alice, client_request_id="concurrent-1"))
            except Exception as e:  # remonté explicitement plutôt qu'avalé par le thread
                errors.append(e)

        t1 = threading.Thread(target=call)
        t2 = threading.Thread(target=call)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        if errors:
            raise errors[0]

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.fake.processed_count, 1)
        self.assertEqual(self.db.get_player(self.alice)["kills"], 1)

    def test_sans_client_request_id_comportement_historique_inchange(self):
        """Non-régression : un appelant qui ne fournit PAS de
        client_request_id (aucun rapport avec cette demande, ex. un
        futur appelant interne) garde le comportement strictement
        historique — jamais de dédoublonnage forcé sans identifiant."""
        result = self.fake._remote_eliminate_request(self.bob, self.alice)
        self.assertTrue(result["ok"])
        self.assertEqual(self.fake.processed_count, 1)


# =====================================================================
# 2. Journal de diagnostic (remote_control.log_remote_event)
# =====================================================================
class RemoteLogEventTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_log_test_")
        self.addCleanup(self._tmp.cleanup)
        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        # Le logger stdlib garde ses handlers en cache (module-level) —
        # forcé à se reconstruire pour pointer vers le fichier temporaire
        # de CE test, jamais le vrai ~/.poker_tournament de l'utilisateur.
        patcher2 = patch.object(remote_control, "_logger", None)
        self.addCleanup(patcher2.stop)
        patcher2.start()

    def _read_log(self):
        if not os.path.exists(self.log_path):
            return ""
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()

    def test_evenement_ecrit_avec_les_champs_utiles(self):
        remote_control.log_remote_event(
            "auth_required", method="GET", path="/players", port=8765,
        )
        content = self._read_log()
        self.assertIn("auth_required", content)
        self.assertIn("method=GET", content)
        self.assertIn("path=/players", content)
        self.assertIn("port=8765", content)

    def test_jamais_de_cookie_ni_de_secret_dans_le_journal(self):
        """Vérifie qu'appeler log_remote_event avec des champs innocents
        ne fait jamais fuiter, par construction, une valeur qui
        ressemblerait à un cookie/jeton/code — ce test documente
        explicitement la règle : n'appeler CETTE fonction qu'avec des
        champs déjà nettoyés (method/path/port/pid/type d'erreur),
        jamais avec self.headers ou un cookie brut."""
        remote_control.log_remote_event(
            "proxy_unreachable", method="GET", path="/eliminate",
            target_port=54321, target_pid=999, error="URLError",
        )
        content = self._read_log()
        for forbidden in ("rc_auth", "rc_bid", "Cookie", "selected_pid="):
            self.assertNotIn(forbidden, content)

    def test_ne_leve_jamais_meme_avec_un_chemin_de_fichier_invalide(self):
        """Un journal de diagnostic ne doit jamais faire planter une
        requête réelle — voir log_remote_event, qui avale ses propres
        erreurs."""
        with patch.object(remote_control, "_LOG_PATH", "/chemin/totalement/invalide/x.log"), \
             patch.object(remote_control, "_logger", None):
            try:
                remote_control.log_remote_event("test_event", foo="bar")
            except Exception as e:  # ne doit jamais arriver
                self.fail(f"log_remote_event a levé une exception : {e!r}")


# =====================================================================
# 3. Proxy vers un tournoi injoignable : 502 + journalisation
# =====================================================================
class _RemoteControlHttpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_reliability_http_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            # Phase 4, "Sécurisation du Contrôle à distance" (2026-09-20) :
            # ces tests vérifient le proxy/la journalisation, pas les
            # permissions DIRTO — un rôle ADMIN inconditionnel évite
            # d'isoler roster.py (fichier RÉEL ~/.poker_tournament/
            # roster.json, jamais touché ici) juste pour qu'authenticated_
            # jar(..., owner_name=...) retrouve un accès complet, comme
            # avant l'existence des permissions.
            patch.object(roster, "get_group", return_value=roster.ROSTER_GROUP_ADMIN),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = remote_control.RemoteControlServer(on_word=lambda w: None, port=self.port)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.code = open_windows.remote_session_code()

        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        log_patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        self.addCleanup(log_patcher.stop)
        log_patcher.start()
        logger_patcher = patch.object(remote_control, "_logger", None)
        self.addCleanup(logger_patcher.stop)
        logger_patcher.start()

    def _read_log(self):
        if not os.path.exists(self.log_path):
            return ""
        with open(self.log_path, encoding="utf-8") as f:
            return f.read()


class ProxyUnreachableTest(_RemoteControlHttpTestCase):
    def test_cible_injoignable_renvoie_502_et_journalise(self):
        jar = authenticated_jar(self.base, owner_name="Test Admin")
        dead_port = _free_port()  # personne n'écoute ici : connexion refusée garantie
        dead_pid = os.getpid() + 999999  # pid qui n'est PAS ce process-ci (own_pid)

        # /select_tournament pose le cookie "selected_pid" dans LE JAR
        # (accepte n'importe quel entier, jamais validé à cet instant —
        # voir _handle_select_tournament) : le jar porte ensuite à la
        # fois l'authentification ET cette sélection sur la requête
        # suivante, comme un vrai téléphone.
        http_request(self.base, "GET", f"/select_tournament?pid={dead_pid}", jar)

        with patch.object(
            open_windows, "list_remote_tournaments",
            return_value=[{"pid": dead_pid, "port": dead_port, "name": "Fantome", "registered_at": time.time()}],
        ):
            status, body, _ = http_request(self.base, "GET", "/players", jar)

        self.assertEqual(status, 502)
        content = self._read_log()
        self.assertIn("proxy_unreachable", content)
        self.assertIn(str(dead_port), content)

    def test_tournoi_disparu_du_registre_ne_declenche_aucun_proxy(self):
        """Si le tournoi sélectionné n'est PLUS dans le registre partagé
        (fermé proprement, déjà purgé), resolve_current_pid retombe sur
        ce process-ci (seul survivant) : jamais de tentative de relais,
        jamais de 502 — juste servi localement, exactement comme
        l'architecture existante le garantit déjà (resolve_current_pid),
        volontairement réutilisée telle quelle."""
        jar = authenticated_jar(self.base, owner_name="Test Admin")
        vanished_pid = 999999999
        http_request(self.base, "GET", f"/select_tournament?pid={vanished_pid}", jar)
        with patch.object(open_windows, "list_remote_tournaments", return_value=[]):
            status, body, _ = http_request(self.base, "GET", "/players", jar)
        self.assertEqual(status, 200)  # servi localement, jamais de 502
        self.assertEqual(json.loads(body), [])  # get_players() par défaut (aucun callback fourni)


class AuthRequiredLoggingTest(_RemoteControlHttpTestCase):
    def test_401_est_journalise_sans_fuite_de_cookie(self):
        status, body, _ = http_request(self.base, "GET", "/players")
        self.assertEqual(status, 401)
        content = self._read_log()
        self.assertIn("auth_required", content)
        self.assertIn("/players", content)
        for forbidden in ("rc_auth", "rc_bid"):
            self.assertNotIn(forbidden, content)

    def test_requete_normale_authentifiee_nest_pas_journalisee(self):
        """Pas de log de chaque requête réussie (demande explicite) :
        seuls les événements ANORMAUX doivent apparaître."""
        jar = authenticated_jar(self.base, owner_name="Test Admin")
        status, _, _ = http_request(self.base, "GET", "/players", jar)
        self.assertEqual(status, 200)
        self.assertEqual(self._read_log(), "")


if __name__ == "__main__":
    unittest.main()
