# -*- coding: utf-8 -*-
"""Moteur de journalisation LOG (chantier "LOG", Phase 1, 2026-09-24) —
action_log.py (journal SQLite global, durable) + ses points d'insertion
dans remote_control.py (do_POST). AUCUN branchement visuel ici (pas de
Rechercher/Réinitialiser/Exporter, pas d'affichage CA/LOG) : uniquement
le moteur.

Deux niveaux :
1. ActionLogEngineTest* : action_log.py directement (pas de serveur
   HTTP) — schéma, persistance, les 4 résultats, robustesse, concurrence.
2. _ActionLogHttpTestCase et ses sous-classes : VRAI RemoteControlServer
   (ThreadingHTTPServer réel, comme tests/test_remote_control_dirto_
   permissions_enforcement.py, dont ce fichier reprend le même principe
   de harnais) + action_log.py redirigé vers un fichier temporaire —
   vérifie que les BONS points de do_POST journalisent, avec les BONNES
   valeurs, et que les points explicitement exclus (navigation,
   sondages, GET) n'écrivent jamais rien."""
import json
import os
import sqlite3
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import action_log  # noqa: E402
import database  # noqa: E402
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


def _read_all_rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM actions_log ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# =====================================================================
# 1. Moteur (action_log.py) sans serveur HTTP
# =====================================================================
class ActionLogEngineTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="action_log_engine_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        patcher = patch.object(action_log, "_log_path", return_value=self.db_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _log(self, **kwargs):
        base = dict(
            tournament_name="Tournoi du vendredi",
            tournament_path="/tmp/vendredi.tournoi",
            role="ADMIN",
            category="eliminations",
            action="eliminate",
            result=action_log.RESULT_SUCCESS,
        )
        base.update(kwargs)
        action_log.log_action(**base)


class CreationEtPersistanceTest(ActionLogEngineTestCase):
    def test_creation_de_la_base_au_premier_appel(self):
        self.assertFalse(os.path.exists(self.db_path))
        self._log()
        self.assertTrue(os.path.exists(self.db_path))

    def test_persistance_apres_reouverture(self):
        self._log(player_name="Alice")
        # Nouvelle connexion INDÉPENDANTE (simule une réouverture de
        # l'application) : la ligne doit toujours être là.
        rows = _read_all_rows(self.db_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player_name"], "Alice")

    def test_deuxieme_appel_ne_recree_rien_de_travers(self):
        self._log()
        self._log()
        rows = _read_all_rows(self.db_path)
        self.assertEqual(len(rows), 2)

    def test_mode_wal_actif(self):
        self._log()
        conn = sqlite3.connect(self.db_path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(mode.lower(), "wal")


class QuatreResultatsTest(ActionLogEngineTestCase):
    def test_success(self):
        self._log(result=action_log.RESULT_SUCCESS, message=None)
        self.assertEqual(_read_all_rows(self.db_path)[0]["result"], "SUCCESS")

    def test_accepted(self):
        self._log(result=action_log.RESULT_ACCEPTED, category="clock", action="toggle_pause")
        self.assertEqual(_read_all_rows(self.db_path)[0]["result"], "ACCEPTED")

    def test_denied(self):
        self._log(result=action_log.RESULT_DENIED, role="DIRTO")
        self.assertEqual(_read_all_rows(self.db_path)[0]["result"], "DENIED")

    def test_error(self):
        self._log(result=action_log.RESULT_ERROR, message="Impossible d'éliminer le dernier joueur.")
        row = _read_all_rows(self.db_path)[0]
        self.assertEqual(row["result"], "ERROR")
        self.assertEqual(row["message"], "Impossible d'éliminer le dernier joueur.")

    def test_valeur_resultat_invalide_repliee_sur_error_jamais_une_exception(self):
        # Le CHECK SQLite refuserait "BOGUS" : log_action doit le
        # détecter AVANT d'insérer plutôt que de laisser SQLite lever une
        # IntegrityError (voir sa docstring, "jamais bloquant").
        self._log(result="BOGUS")
        self.assertEqual(_read_all_rows(self.db_path)[0]["result"], "ERROR")


class ChampsTest(ActionLogEngineTestCase):
    def test_role_admin(self):
        self._log(role="ADMIN", user_name="Raj")
        row = _read_all_rows(self.db_path)[0]
        self.assertEqual(row["role"], "ADMIN")
        self.assertEqual(row["user_name"], "Raj")

    def test_role_dirto(self):
        self._log(role="DIRTO", user_name="Marie", category="tables", action="tables")
        row = _read_all_rows(self.db_path)[0]
        self.assertEqual(row["role"], "DIRTO")
        self.assertEqual(row["user_name"], "Marie")

    def test_role_none_sans_proprietaire(self):
        self._log(role="NONE", user_name=None, result=action_log.RESULT_DENIED)
        row = _read_all_rows(self.db_path)[0]
        self.assertEqual(row["role"], "NONE")
        self.assertIsNone(row["user_name"])

    def test_joueur_concerne(self):
        self._log(player_name="Bob")
        self.assertEqual(_read_all_rows(self.db_path)[0]["player_name"], "Bob")

    def test_joueur_absent_reste_null(self):
        self._log(category="clock", action="toggle_pause", player_name=None)
        self.assertIsNone(_read_all_rows(self.db_path)[0]["player_name"])

    def test_device_id_et_label_figes(self):
        self._log(device_id="abc123", device_label="iPhone de Marie")
        row = _read_all_rows(self.db_path)[0]
        self.assertEqual(row["device_id"], "abc123")
        self.assertEqual(row["device_label"], "iPhone de Marie")

    def test_tournoi_correct(self):
        self._log(tournament_name="Tournoi du dimanche", tournament_path="/tmp/dimanche.tournoi")
        row = _read_all_rows(self.db_path)[0]
        self.assertEqual(row["tournament_name"], "Tournoi du dimanche")
        self.assertEqual(row["tournament_path"], "/tmp/dimanche.tournoi")

    def test_horodatage_format_local_sans_fuseau(self):
        self._log()
        ts = _read_all_rows(self.db_path)[0]["ts"]
        # "AAAA-MM-JJ HH:MM:SS" exactement (demande explicite : pas d'UTC,
        # pas de suffixe de fuseau).
        import re
        self.assertRegex(ts, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


class RobustesseTest(ActionLogEngineTestCase):
    def test_panne_ouverture_connexion_ne_leve_jamais(self):
        with patch.object(action_log, "_open_connection", side_effect=sqlite3.OperationalError("disk full")):
            try:
                self._log()
            except Exception as e:  # pragma: no cover - ne doit jamais arriver
                self.fail(f"log_action a laissé fuiter une exception : {e}")
        # Rien n'a été écrit (la panne était avant toute insertion) — pas
        # d'exception, c'est ce qui compte, mais autant vérifier l'état.
        self.assertFalse(os.path.exists(self.db_path) and _read_all_rows(self.db_path))

    def test_panne_repertoire_injoignable_ne_leve_jamais(self):
        with patch.object(action_log, "_log_dir", side_effect=OSError("répertoire injoignable")):
            try:
                self._log()
            except Exception as e:  # pragma: no cover
                self.fail(f"log_action a laissé fuiter une exception : {e}")


class ConcurrenceTest(ActionLogEngineTestCase):
    def test_ecritures_concurrentes_depuis_plusieurs_threads(self):
        # Chaque thread ouvre/ferme sa PROPRE connexion jetable (voir
        # action_log.log_action) — simule plusieurs processus Poker
        # Tournament écrivant simultanément dans le même journal global
        # (cas réel : "Menu principal", plusieurs fenêtres = plusieurs
        # processus). Le mode WAL doit absorber ça sans perte ni
        # corruption.
        n_threads = 8
        n_per_thread = 5
        errors = []

        def worker(idx):
            try:
                for i in range(n_per_thread):
                    self._log(player_name=f"joueur-{idx}-{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        rows = _read_all_rows(self.db_path)
        self.assertEqual(len(rows), n_threads * n_per_thread)
        # Aucune perte, aucun doublon : tous les joueurs attendus présents.
        expected = {f"joueur-{i}-{j}" for i in range(n_threads) for j in range(n_per_thread)}
        self.assertEqual({r["player_name"] for r in rows}, expected)


# =====================================================================
# 2. Intégration via un VRAI RemoteControlServer
# =====================================================================
class _ActionLogHttpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="action_log_http_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        roster_path = os.path.join(self._tmp.name, "roster.json")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(roster, "_roster_path", return_value=roster_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        # Journal DIAGNOSTIQUE existant (log_remote_event) : isolé lui
        # aussi, sans rapport avec action_log.py, seulement pour ne pas
        # polluer/collisionner avec d'autres tests.
        self.diag_log_path = os.path.join(self._tmp.name, "remote_control.log")
        for target in (
            patch.object(remote_control, "_LOG_PATH", self.diag_log_path),
            patch.object(remote_control, "_logger", None),
        ):
            self.addCleanup(target.stop)
            target.start()

        # action_log.py (module sous test) redirigé vers un fichier
        # temporaire — jamais le vrai ~/.poker_tournament/actions_log.sqlite3.
        self.action_log_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        al_patcher = patch.object(action_log, "_log_path", return_value=self.action_log_path)
        self.addCleanup(al_patcher.stop)
        al_patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.tournament_name = "Tournoi du vendredi"

        self._players = []
        self._moves = []
        self._dirto_permissions = {}
        self.eliminate_result = {"ok": True, "message": ""}
        self.confirm_move_result = {"ok": True, "all_done": False}
        self.upload_photo_result = (True, "ok")
        self.delete_photo_result = (True, "ok")

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = remote_control.RemoteControlServer(
            on_word=lambda word: None,
            get_tournament_name=lambda: self.tournament_name,
            # self.db.path : simple attribut chaîne fixé à la construction
            # (jamais un accès sqlite3), donc sûr à lire depuis le thread
            # HTTP — voir main.py: _remote_control_tournament_path.
            get_tournament_path=lambda: self.db.path,
            get_players=lambda: self._players,
            get_pending_moves=lambda: self._moves,
            on_eliminate=lambda eid, xid, rid=None: self.eliminate_result,
            on_confirm_move=lambda move_id: self.confirm_move_result,
            on_upload_photo=lambda name, data: self.upload_photo_result,
            on_delete_photo=lambda name: self.delete_photo_result,
            on_rebalance_answer=lambda rid, player_id: None,
            on_end_tournament=lambda: None,
            get_dirto_permissions=lambda name: self._dirto_permissions.get((name or "").strip(), frozenset()),
            port=self.port,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.own_pid = os.getpid()

    def _rows(self):
        if not os.path.exists(self.action_log_path):
            return []
        return _read_all_rows(self.action_log_path)

    def _jar(self, owner_name=None, label="Test"):
        return authenticated_jar(self.base, owner_name=owner_name, label=label)

    def _browser_id_for_label(self, label):
        for d in open_windows.list_approved_remote_devices():
            if d["label"] == label:
                return d["browser_id"]
        raise AssertionError(f"aucun appareil approuvé avec le label {label!r}")


class EliminateLoggingTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self._players = [{"id": 1, "name": "Alice", "table": "Table 1", "seat": 3, "has_photo": False}]
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_eliminate_succes_journalise_success_avec_joueur(self):
        self.eliminate_result = {"ok": True, "message": ""}
        status, body, _ = http_request(
            self.base, "POST", "/eliminate", self.jar,
            body={"eliminated_id": 1, "eliminator_id": None},
        )
        self.assertEqual(status, 200, body)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["result"], "SUCCESS")
        self.assertEqual(row["category"], "eliminations")
        self.assertEqual(row["action"], "eliminate")
        self.assertEqual(row["player_name"], "Alice")
        self.assertEqual(row["role"], "ADMIN")
        self.assertEqual(row["user_name"], "Raj")
        self.assertEqual(row["tournament_name"], "Tournoi du vendredi")
        self.assertEqual(row["tournament_path"], self.db.path)
        self.assertEqual(row["device_label"], "iPhone de Raj")
        self.assertIsNotNone(row["device_id"])

    def test_eliminate_refuse_metier_journalise_error(self):
        self.eliminate_result = {"ok": False, "message": "Refus PKO : désignez un éliminateur."}
        status, body, _ = http_request(
            self.base, "POST", "/eliminate", self.jar,
            body={"eliminated_id": 1, "eliminator_id": None},
        )
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ERROR")
        self.assertEqual(row["message"], "Refus PKO : désignez un éliminateur.")
        self.assertEqual(row["player_name"], "Alice")


class ConfirmMoveLoggingTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self._moves = [{
            "id": 42, "player_name": "Bob", "old_table_name": "Table 1", "old_seat": 2,
            "new_table_name": "Table 2", "new_seat": 5,
        }]
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_confirm_move_succes(self):
        self.confirm_move_result = {"ok": True, "all_done": False}
        status, body, _ = http_request(self.base, "POST", "/confirm_move", self.jar, body={"move_id": 42})
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "SUCCESS")
        self.assertEqual(row["category"], "moves")
        self.assertEqual(row["action"], "confirm_move")
        self.assertEqual(row["player_name"], "Bob")

    def test_confirm_move_echec(self):
        self.confirm_move_result = {"ok": False, "message": "Délai dépassé, réessayez.", "all_done": False}
        status, body, _ = http_request(self.base, "POST", "/confirm_move", self.jar, body={"move_id": 42})
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ERROR")
        self.assertEqual(row["message"], "Délai dépassé, réessayez.")


class FireAndForgetLoggingTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_actions_journalisees_en_accepted(self):
        for action in ("terminer", "toggle_pause", "chronometre", "niveau_precedent", "niveau_suivant"):
            with self.subTest(action=action):
                status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar)
                self.assertEqual(status, 200, body)
        rows = self._rows()
        self.assertEqual(len(rows), 5)
        for row in rows:
            self.assertEqual(row["result"], "ACCEPTED")

    def test_elimination_admin_only_journalisee_accepted_categorie_admin_only(self):
        status, body, _ = http_request(self.base, "POST", "/action/elimination", self.jar)
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ACCEPTED")
        self.assertEqual(row["category"], "admin_only")
        self.assertEqual(row["action"], "elimination")


class DeniedLoggingTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._dirto_permissions = {"Marie": frozenset({"tables"})}

    def test_dirto_sans_permission_eliminate_refuse_denied(self):
        jar = self._jar(owner_name="Marie", label="iPhone de Marie")
        status, body, _ = http_request(
            self.base, "POST", "/eliminate", jar, body={"eliminated_id": 1, "eliminator_id": None},
        )
        self.assertEqual(status, 403, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "DENIED")
        self.assertEqual(row["role"], "DIRTO")
        self.assertEqual(row["user_name"], "Marie")
        self.assertEqual(row["category"], "eliminations")
        self.assertEqual(row["action"], "eliminate")

    def test_dirto_action_non_accordee_refusee_denied(self):
        jar = self._jar(owner_name="Marie", label="iPhone de Marie")
        status, body, _ = http_request(self.base, "POST", "/action/toggle_pause", jar)
        self.assertEqual(status, 403, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "DENIED")
        self.assertEqual(row["action"], "toggle_pause")

    def test_non_admin_end_tournament_refuse_denied(self):
        jar = self._jar(owner_name="Marie", label="iPhone de Marie")
        status, body, _ = http_request(self.base, "POST", "/end_tournament", jar, body={"pid": self.own_pid})
        self.assertEqual(status, 403, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "DENIED")
        self.assertEqual(row["category"], "admin_only")
        self.assertEqual(row["action"], "end_tournament")

    def test_appareil_approuve_sans_proprietaire_journalise_none_sans_user(self):
        # Appareil approuvé mais jamais lié à personne (owner_name=None) :
        # rôle NONE, aucune permission — utile pour diagnostiquer un
        # téléphone mal configuré (décision explicite du 2026-09-24).
        jar = self._jar(owner_name=None, label="iPhone non lié")
        status, body, _ = http_request(self.base, "POST", "/action/toggle_pause", jar)
        self.assertEqual(status, 403, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "DENIED")
        self.assertEqual(row["role"], "NONE")
        self.assertIsNone(row["user_name"])
        self.assertEqual(row["device_label"], "iPhone non lié")
        self.assertIsNotNone(row["device_id"])


class ActionsExcluesJamaisJournaliseesTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._dirto_permissions = {"Marie": frozenset()}
        self.jar_admin = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_navigation_et_zoom_tables_jamais_journalisees(self):
        for action in ("tables", "tables_zoom_moins", "tables_zoom_plus"):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar_admin)
            self.assertEqual(status, 200, body)
        self.assertEqual(self._rows(), [])

    def test_positionnement_visuel_mouvements_jamais_journalise(self):
        for action in ("mouvements", "mouvements_bas", "mouvements_haut"):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar_admin)
            self.assertEqual(status, 200, body)
        self.assertEqual(self._rows(), [])

    def test_refus_sur_actions_exclues_jamais_journalise_non_plus(self):
        jar_marie = self._jar(owner_name="Marie", label="iPhone de Marie")
        for action in ("tables", "mouvements", "tables_zoom_moins"):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", jar_marie)
            self.assertEqual(status, 403, body)
        self.assertEqual(self._rows(), [])

    def test_get_pages_et_sondages_jamais_journalises(self):
        for path in ("/", "/players", "/moves_pending", "/clock_state",
                      "/permission_state", "/rebalance_pending", "/roster_players"):
            http_request(self.base, "GET", path, self.jar_admin)
        self.assertEqual(self._rows(), [])

    def test_authentification_ordinaire_jamais_journalisee(self):
        # authenticated_jar() a déjà fait tout le parcours /login +
        # /authenticate (x2) + approbation dans setUp/_jar : rien de tout
        # cela ne doit avoir laissé de ligne.
        self.assertEqual(self._rows(), [])


class PhotosLoggingTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_upload_photo_succes(self):
        # http_request (voir _remote_control_auth_test_utils) encode
        # toujours `body` en JSON — cette route attend des octets JPEG
        # bruts (voir do_POST: self.rfile.read(length)), donc une requête
        # urllib directe ici, comme le ferait un vrai téléphone (le faux
        # callback on_upload_photo, lui, ignore les octets reçus).
        import urllib.request
        self.upload_photo_result = (True, "Alice")
        req = urllib.request.Request(
            self.base + "/upload_photo?player_name=Alice", data=b"\xff\xd8\xff",
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        with opener.open(req, timeout=5) as r:
            status = r.status
        self.assertEqual(status, 200)
        row = self._rows()[0]
        self.assertEqual(row["result"], "SUCCESS")
        self.assertEqual(row["category"], "photos")
        self.assertEqual(row["action"], "upload_photo")
        self.assertEqual(row["player_name"], "Alice")

    def test_delete_photo_succes(self):
        self.delete_photo_result = (True, "Alice")
        status, body, _ = http_request(self.base, "POST", "/delete_photo?player_name=Alice", self.jar)
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "SUCCESS")
        self.assertEqual(row["action"], "delete_photo")
        self.assertEqual(row["player_name"], "Alice")

    def test_delete_photo_echec(self):
        self.delete_photo_result = (False, "Échec de la suppression.")
        status, body, _ = http_request(self.base, "POST", "/delete_photo?player_name=Alice", self.jar)
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ERROR")
        self.assertEqual(row["message"], "Échec de la suppression.")


class EndTournamentLoggingTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_fin_de_partie_acceptee(self):
        with patch.object(open_windows, "list_remote_tournaments", return_value=[]):
            status, body, _ = http_request(
                self.base, "POST", "/end_tournament", self.jar, body={"pid": self.own_pid},
            )
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ACCEPTED")
        self.assertEqual(row["category"], "admin_only")
        self.assertEqual(row["action"], "end_tournament")

    def test_pid_perime_journalise_error(self):
        status, body, _ = http_request(
            self.base, "POST", "/end_tournament", self.jar, body={"pid": self.own_pid + 999},
        )
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ERROR")
        self.assertIn("plus disponible", row["message"])


class RebalanceAnswerLoggingTest(_ActionLogHttpTestCase):
    """Chantier "sélection directe du joueur UTG" (2026-09-24) : le
    téléphone envoie désormais player_id, plus un numéro de siège."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_reponse_avec_joueur_designe_journalisee_accepted_avec_son_nom(self):
        self._players = [{"id": 31, "name": "Chloé", "table": "Table 1", "seat": 4, "has_photo": False}]
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", self.jar,
            body={"request_id": "r1", "player_id": 31},
        )
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ACCEPTED")
        self.assertEqual(row["category"], "rebalance")
        self.assertEqual(row["player_name"], "Chloé")
        self.assertEqual(row["message"], "Chloé")

    def test_reponse_continuer_sans_designer_journalisee_accepted(self):
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", self.jar,
            body={"request_id": "r1", "player_id": None},
        )
        self.assertEqual(status, 200, body)
        row = self._rows()[0]
        self.assertEqual(row["result"], "ACCEPTED")
        self.assertIsNone(row["player_name"])
        self.assertEqual(row["message"], "Continuer sans désigner le joueur")


class DeviceLabelFigeTest(_ActionLogHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)

    def test_renommage_appareil_ne_modifie_jamais_les_lignes_deja_ecrites(self):
        jar = self._jar(owner_name="Raj", label="iPhone A")
        browser_id = self._browser_id_for_label("iPhone A")
        http_request(self.base, "POST", "/action/toggle_pause", jar)
        first_row = self._rows()[0]
        self.assertEqual(first_row["device_label"], "iPhone A")

        # Renommage de l'appareil APRÈS coup (même browser_id).
        ok = open_windows.approve_remote_device(browser_id, label="iPhone B")
        self.assertTrue(ok)
        http_request(self.base, "POST", "/action/toggle_pause", jar)
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["device_label"], "iPhone A", "la ligne historique ne doit jamais changer")
        self.assertEqual(rows[1]["device_label"], "iPhone B")


class TournoiIdentiteEtEtancheiteTest(unittest.TestCase):
    """Deux tournois (deux Database, deux RemoteControlServer, un seul
    fichier actions_log.sqlite3 PARTAGÉ — comme dans la vraie
    application) : chaque action se journalise contre SON PROPRE
    tournoi, sans jamais se mélanger."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="action_log_multi_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        roster_path = os.path.join(self._tmp.name, "roster.json")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(roster, "_roster_path", return_value=roster_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        open_windows.register(os.path.join(self._tmp.name, "session_marker.tournoi"))
        self.addCleanup(open_windows.unregister, os.path.join(self._tmp.name, "session_marker.tournoi"))

        self.action_log_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        al_patcher = patch.object(action_log, "_log_path", return_value=self.action_log_path)
        self.addCleanup(al_patcher.stop)
        al_patcher.start()

        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)

        self.db_a = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db_a.conn.close)
        self.db_b = database.Database(os.path.join(self._tmp.name, "B.tournoi"))
        self.addCleanup(self.db_b.conn.close)

        self.server_a = self._make_server("Tournoi A", self.db_a.path)
        self.server_a.start()
        self.addCleanup(self.server_a.stop)
        self.server_b = self._make_server("Tournoi B", self.db_b.path)
        self.server_b.start()
        self.addCleanup(self.server_b.stop)

    def _make_server(self, name, path):
        return remote_control.RemoteControlServer(
            on_word=lambda word: None,
            get_tournament_name=lambda: name,
            get_tournament_path=lambda: path,
            port=_free_port(),
        )

    def test_deux_tournois_ne_se_melangent_jamais(self):
        base_a = f"http://127.0.0.1:{self.server_a.port}"
        base_b = f"http://127.0.0.1:{self.server_b.port}"
        jar_a = authenticated_jar(base_a, owner_name="Raj", label="iPhone A")
        jar_b = authenticated_jar(base_b, owner_name="Raj", label="iPhone B")

        http_request(base_a, "POST", "/action/toggle_pause", jar_a)
        http_request(base_b, "POST", "/action/niveau_suivant", jar_b)

        rows = _read_all_rows(self.action_log_path)
        self.assertEqual(len(rows), 2)
        by_tournament = {r["tournament_name"]: r for r in rows}
        self.assertEqual(by_tournament["Tournoi A"]["tournament_path"], self.db_a.path)
        self.assertEqual(by_tournament["Tournoi A"]["action"], "toggle_pause")
        self.assertEqual(by_tournament["Tournoi B"]["tournament_path"], self.db_b.path)
        self.assertEqual(by_tournament["Tournoi B"]["action"], "niveau_suivant")
        self.assertNotEqual(by_tournament["Tournoi A"]["tournament_path"], by_tournament["Tournoi B"]["tournament_path"])


class PanneLogNeBloquePasLactionTest(_ActionLogHttpTestCase):
    """Exigence absolue : une panne du LOG ne doit JAMAIS bloquer ni
    faire échouer une action réelle du tournoi."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self._players = [{"id": 1, "name": "Alice", "table": "Table 1", "seat": 3, "has_photo": False}]
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_action_fire_and_forget_reussit_malgre_la_panne_du_log(self):
        with patch.object(action_log, "log_action", side_effect=RuntimeError("panne simulée du LOG")):
            status, body, _ = http_request(self.base, "POST", "/action/toggle_pause", self.jar)
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {"ok": True})

    def test_eliminate_reussit_malgre_la_panne_du_log(self):
        self.eliminate_result = {"ok": True, "message": ""}
        with patch.object(action_log, "log_action", side_effect=RuntimeError("panne simulée du LOG")):
            status, body, _ = http_request(
                self.base, "POST", "/eliminate", self.jar,
                body={"eliminated_id": 1, "eliminator_id": None},
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(json.loads(body), {"ok": True, "message": ""})

    def test_refus_de_permission_reste_403_malgre_la_panne_du_log(self):
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        jar_marie = self._jar(owner_name="Marie", label="iPhone de Marie")
        with patch.object(action_log, "log_action", side_effect=RuntimeError("panne simulée du LOG")):
            status, body, _ = http_request(self.base, "POST", "/action/toggle_pause", jar_marie)
        self.assertEqual(status, 403, body)

    def test_panne_reelle_du_moteur_sqlite_naffecte_pas_laction(self):
        # Panne "réelle" (pas juste log_action mocké) : le répertoire
        # global lui-même devient injoignable.
        with patch.object(action_log, "_log_dir", side_effect=OSError("répertoire injoignable")):
            status, body, _ = http_request(self.base, "POST", "/action/chronometre", self.jar)
        self.assertEqual(status, 200, body)


if __name__ == "__main__":
    unittest.main()
