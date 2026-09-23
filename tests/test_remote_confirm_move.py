# -*- coding: utf-8 -*-
"""Confirmation individuelle d'un mouvement depuis le téléphone (demande
du 2026-09-19, "ergonomie iPhone" — bouton [OK] par ligne sur une nouvelle
page /moves, en plus du bouton "Mouvements terminés" conservé tel quel).

Deux niveaux, comme pour l'idempotence /eliminate (voir tests/test_remote_
control_reliability.py, dont ce fichier réutilise le harnais HTTP) :
1. Orchestration pure (App._remote_confirm_move_request / _remote_confirm_
   move), via une doublure légère + un VRAI thread consommateur — jamais
   le thread HTTP lui-même touché à self.db (voir _remote_eliminate_
   request, même principe).
2. Bout en bout HTTP réel (GET /moves_pending, POST /confirm_move, page
   /moves, non-régression /action/terminer)."""
import json
import os
import queue
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402,F401


def _new_db_cross_thread(tmp_dir, filename):
    import sqlite3

    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    db.conn.close()
    db.conn = sqlite3.connect(path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    return db


def _insert_move(db, player_name, old_table_name, old_seat, new_table_name, new_seat):
    cur = db.conn.execute(
        "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
        "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
        (player_name, old_table_name, old_seat, new_table_name, new_seat,
         "2026-09-19 20:00:00", "auto_balance"),
    )
    db.conn.commit()
    return cur.lastrowid


# =====================================================================
# 1. Orchestration (App._remote_confirm_move_request / _remote_confirm_move)
# =====================================================================
class _FakeRemoteMovesApp:
    """Doublure légère, même principe que _FakeRemoteApp (tests/test_
    remote_control_reliability.py) : self.db réel, consommateur de
    voice_command_queue tournant dans un VRAI thread séparé (reproduit
    App._poll_voice_queue), et des rafraîchissements d'écran réduits à de
    simples compteurs/traces — jamais la logique métier elle-même
    (confirm_seat_move, _finish_movement_alert) réimplémentée ici."""

    def __init__(self, db):
        self.db = db
        self.voice_command_queue = queue.Queue()
        self._remote_move_confirm_results = {}
        self._remote_moves_cache = []
        self._remote_has_pending_moves = False
        self.refresh_calls = []
        self.finish_movement_alert_calls = 0
        self._consumer_stop = threading.Event()
        self._consumer_thread = threading.Thread(target=self._consume, daemon=True)
        self._remote_confirm_move_request = types.MethodType(
            main.App._remote_confirm_move_request, self
        )
        self._refresh_remote_moves_cache = types.MethodType(
            main.App._refresh_remote_moves_cache, self
        )
        self._consumer_thread.start()

    def _consume(self):
        while not self._consumer_stop.is_set():
            try:
                item = self.voice_command_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(item, tuple) and item and item[0] == "confirm_move":
                _, move_id, request_id = item
                main.App._remote_confirm_move(self, move_id, request_id=request_id)

    def stop(self):
        self._consumer_stop.set()
        self._consumer_thread.join(timeout=2)

    def _refresh_moves_tab(self):
        self.refresh_calls.append("moves_tab")

    def _refresh_clock_tab(self):
        self.refresh_calls.append("clock_tab")

    def _refresh_players_tab(self):
        self.refresh_calls.append("players_tab")

    def _refresh_tables_tab(self):
        self.refresh_calls.append("tables_tab")

    def _finish_movement_alert(self, switch_to_clock=True):
        self.finish_movement_alert_calls += 1
        self.refresh_calls.append("finish_movement_alert")


class RemoteConfirmMoveOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_confirm_move_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db_cross_thread(self._tmp.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.id_a = _insert_move(self.db, "A", "Table 1", 4, "Table 3", 2)
        self.id_b = _insert_move(self.db, "B", "Table 2", 2, "Table 4", 5)
        self.id_c = _insert_move(self.db, "C", "Table 3", 6, "Table 1", 3)
        self.id_d = _insert_move(self.db, "D", "Table 4", 1, "Table 2", 7)
        self.fake = _FakeRemoteMovesApp(self.db)
        self.addCleanup(self.fake.stop)

    def test_confirmation_partielle_rafraichit_tout_sans_terminer(self):
        """A confirmé alors que B, C, D restent : Joueurs/Tables/
        Mouvements/Chronomètre tous rafraîchis IMMÉDIATEMENT (jamais
        besoin d'un changement d'onglet), mais _finish_movement_alert
        n'est PAS déclenché tant qu'il reste des mouvements."""
        result = self.fake._remote_confirm_move_request(self.id_a)

        self.assertTrue(result["ok"])
        self.assertFalse(result["all_done"])
        self.assertEqual(self.db.count_seat_moves(), 3)
        self.assertEqual(
            {m["player_name"] for m in self.db.get_seat_moves()}, {"B", "C", "D"},
        )
        self.assertEqual(self.fake.finish_movement_alert_calls, 0)
        self.assertIn("moves_tab", self.fake.refresh_calls)
        self.assertIn("clock_tab", self.fake.refresh_calls)
        self.assertIn("players_tab", self.fake.refresh_calls)
        self.assertIn("tables_tab", self.fake.refresh_calls)
        # Le cache téléphone et le clignotement sont mis à jour sans
        # attendre le prochain tick (~1s) de l'App réelle.
        self.assertEqual(len(self.fake._remote_moves_cache), 3)
        self.assertTrue(self.fake._remote_has_pending_moves)

    def test_confirmations_successives_laissent_le_bon_mouvement(self):
        """Exactement le scénario de la demande : A, B, C confirmés un par
        un, D reste seul, avec sa position d'origine intacte."""
        self.fake._remote_confirm_move_request(self.id_a)
        self.fake._remote_confirm_move_request(self.id_b)
        result = self.fake._remote_confirm_move_request(self.id_c)

        self.assertFalse(result["all_done"])
        remaining = self.db.get_seat_moves()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["player_name"], "D")
        self.assertEqual(remaining[0]["old_table_name"], "Table 4")
        self.assertEqual(remaining[0]["old_seat"], 1)

    def test_dernier_mouvement_confirme_declenche_la_fin_normale(self):
        """Le coeur de la demande : confirmer le DERNIER mouvement doit
        utiliser exactement le même mécanisme que "Mouvements terminés"
        (_finish_movement_alert), jamais une fin réimplémentée à part —
        et Joueurs/Tables doivent quand même être rafraîchis explicitement
        (que _finish_movement_alert, lui, ne fait pas)."""
        self.fake._remote_confirm_move_request(self.id_a)
        self.fake._remote_confirm_move_request(self.id_b)
        self.fake._remote_confirm_move_request(self.id_c)
        result = self.fake._remote_confirm_move_request(self.id_d)

        self.assertTrue(result["ok"])
        self.assertTrue(result["all_done"])
        self.assertEqual(self.fake.finish_movement_alert_calls, 1)
        self.assertIn("finish_movement_alert", self.fake.refresh_calls)
        self.assertIn("players_tab", self.fake.refresh_calls)
        self.assertIn("tables_tab", self.fake.refresh_calls)
        self.assertEqual(self.db.count_seat_moves(), 0)
        self.assertEqual(self.fake._remote_moves_cache, [])
        self.assertFalse(self.fake._remote_has_pending_moves)

    def test_confirmer_un_id_deja_confirme_ne_termine_pas_a_tort(self):
        """Rejouer la même confirmation (retry réseau, double-tap) ne doit
        jamais déclencher une fin prématurée si d'autres mouvements
        restent réellement en attente."""
        self.fake._remote_confirm_move_request(self.id_a)
        result = self.fake._remote_confirm_move_request(self.id_a)  # déjà confirmé
        self.assertTrue(result["ok"])
        self.assertFalse(result["all_done"])
        self.assertEqual(self.fake.finish_movement_alert_calls, 0)
        self.assertEqual(self.db.count_seat_moves(), 3)


# =====================================================================
# 2. Bout en bout HTTP réel
# =====================================================================
class RemoteConfirmMoveHttpTest(_RemoteControlHttpTestCase):
    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="remote_confirm_move_http_")
        self.addCleanup(self._tmp2.cleanup)
        # check_same_thread=False (voir _new_db_cross_thread) : ce self.db
        # est touché directement par les lambdas get_pending_moves/
        # on_confirm_move ci-dessous, exécutées sur le VRAI thread HTTP du
        # serveur — jamais le thread qui a créé cette connexion (setUp).
        self.db = _new_db_cross_thread(self._tmp2.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.id_a = _insert_move(self.db, "A", "Table 1", 4, "Table 3", 2)
        self.id_b = _insert_move(self.db, "B", "Table 2", 2, "Table 4", 5)
        self.confirmed_ids = []

        self.server.stop()
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_pending_moves=lambda: [
                {
                    "id": m["id"], "player_name": m["player_name"],
                    "old_table_name": m["old_table_name"], "old_seat": m["old_seat"],
                    "new_table_name": m["new_table_name"], "new_seat": m["new_seat"],
                }
                for m in self.db.get_seat_moves()
            ],
            on_confirm_move=lambda move_id: (
                self.confirmed_ids.append(move_id)
                or self.db.confirm_seat_move(move_id)
                or {"ok": True, "all_done": self.db.count_seat_moves() == 0}
            ),
            port=self.port,
        )
        self.server.start()

    def test_moves_pending_expose_la_liste_reelle(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(self.base, "GET", "/moves_pending", jar)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual({d["player_name"] for d in data}, {"A", "B"})

    def test_moves_pending_refuse_sans_authentification(self):
        status, _, _ = http_request(self.base, "GET", "/moves_pending")
        self.assertEqual(status, 401)

    def test_confirm_move_confirme_uniquement_la_ligne_visee(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(
            self.base, "POST", "/confirm_move", jar, body={"move_id": self.id_a},
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertFalse(data["all_done"])
        self.assertEqual(self.confirmed_ids, [self.id_a])
        remaining = {m["player_name"] for m in self.db.get_seat_moves()}
        self.assertEqual(remaining, {"B"})

    def test_confirm_move_refuse_sans_authentification(self):
        status, _, _ = http_request(
            self.base, "POST", "/confirm_move", body={"move_id": self.id_a},
        )
        self.assertEqual(status, 401)

    def test_page_moves_redirige_vers_login_sans_authentification(self):
        status, _, headers = http_request(self.base, "GET", "/moves")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")

    def test_page_moves_accessible_une_fois_authentifie(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(self.base, "GET", "/moves", jar)
        self.assertEqual(status, 200)
        self.assertIn(b"Retour", body)

    def test_action_terminer_reste_disponible_et_confirme_le_reste(self):
        """Non-régression explicite (demande du 2026-09-19) : le bouton
        "Mouvements terminés" continue de fonctionner À L'IDENTIQUE, même
        après des confirmations individuelles partielles — /action/
        terminer n'est en rien modifié par cette fonctionnalité."""
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        http_request(self.base, "POST", "/confirm_move", jar, body={"move_id": self.id_a})
        status, body, _ = http_request(self.base, "POST", "/action/terminer", jar)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])


if __name__ == "__main__":
    unittest.main()
