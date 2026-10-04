# -*- coding: utf-8 -*-
"""Lot 2 — "Gestion des absences et des retardataires" (cahier des
charges du 2026-09-28), couverture du contrôle à distance :

1. Orchestration pure (App._remote_declare_absence_request/_remote_
   cancel_absence_request/_remote_withdraw_absent_player_request), via
   une doublure légère + un VRAI thread consommateur — même principe que
   tests/test_remote_confirm_arrival.py (Lot 1), dont ce fichier reprend
   directement le patron.
2. Bout en bout HTTP réel (POST /declare_absence, /cancel_absence,
   /withdraw_absent_player, GET /absence_state, permission "eliminations"
   réutilisée — aucune permission séparée créée pour le Lot 2)."""
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402,F401


def _new_db_cross_thread(tmp_dir, filename):
    import sqlite3

    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    # « Gestion de l'accueil » activée par défaut pour ces fixtures
    # (modification du 2026-09-29) : ce module teste précisément la
    # gestion des absences/retardataires, réglage propre au tournoi
    # Open qui l'active — voir test_accueil_management_toggle.py pour
    # la couverture du réglage désactivé (comportement par défaut).
    db.set_settings({"accueil_management_enabled": 1})
    db.conn.close()
    db.conn = sqlite3.connect(path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


# =====================================================================
# 1. Orchestration (App._remote_declare_absence_request / _remote_cancel_
#    absence_request / _remote_withdraw_absent_player_request)
# =====================================================================
class _FakeRemoteAbsenceApp:
    """Doublure légère, même principe que _FakeRemoteCheckinApp (tests/
    test_remote_confirm_arrival.py) : self.db réel, consommateur de
    voice_command_queue tournant dans un VRAI thread séparé (reproduit
    App._poll_voice_queue), jamais la logique métier réimplémentée ici."""

    def __init__(self, db):
        self.db = db
        self.voice_command_queue = queue.Queue()
        self._remote_absence_results = {}
        self.players_refreshed = 0
        self.checkin_refreshed = 0
        self.tables_refreshed = 0
        self._consumer_stop = threading.Event()
        self._consumer_thread = threading.Thread(target=self._consume, daemon=True)
        for meth in (
            "_remote_declare_absence_request", "_remote_cancel_absence_request",
            "_remote_withdraw_absent_player_request", "_await_remote_absence_result",
        ):
            setattr(self, meth, types.MethodType(getattr(main.App, meth), self))
        self._consumer_thread.start()

    def _consume(self):
        while not self._consumer_stop.is_set():
            try:
                item = self.voice_command_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(item, tuple) and item and item[0] == "declare_absence":
                _, player_id, request_id = item
                main.App._remote_declare_absence(self, player_id, request_id=request_id)
            elif isinstance(item, tuple) and item and item[0] == "cancel_absence":
                _, player_id, request_id = item
                main.App._remote_cancel_absence(self, player_id, request_id=request_id)
            elif isinstance(item, tuple) and item and item[0] == "withdraw_absent_player":
                _, player_id, request_id = item
                main.App._remote_withdraw_absent_player(self, player_id, request_id=request_id)

    def stop(self):
        self._consumer_stop.set()
        self._consumer_thread.join(timeout=2)

    def _refresh_remote_players_cache(self):
        self.players_refreshed += 1

    def _refresh_remote_checkin_cache(self):
        self.checkin_refreshed += 1

    def _refresh_players_tab(self):
        pass

    def _refresh_tables_tab(self):
        self.tables_refreshed += 1


class RemoteDeclareAbsenceOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_declare_absence_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db_cross_thread(self._tmp.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.fake = _FakeRemoteAbsenceApp(self.db)
        self.addCleanup(self.fake.stop)

    def test_declare_absence_horodate_le_bon_joueur(self):
        result = self.fake._remote_declare_absence_request(self.pid)

        self.assertTrue(result["ok"])
        self.assertIsNotNone(self.db.get_player(self.pid)["absent_declared_at"])
        self.assertEqual(self.fake.players_refreshed, 1)

    def test_declare_absence_refuse_avant_le_demarrage(self):
        pid2 = self.db.add_player("Bob")
        self.db.set_settings({"clock_started": 0, "tournament_start_epoch": 0})

        result = self.fake._remote_declare_absence_request(pid2)

        self.assertFalse(result["ok"])
        self.assertIsNone(self.db.get_player(pid2)["absent_declared_at"])

    def test_declare_absence_refuse_pour_joueur_inconnu(self):
        result = self.fake._remote_declare_absence_request(999999)
        self.assertFalse(result["ok"])

    def test_cancel_absence_annule_uniquement_le_signalement(self):
        self.fake._remote_declare_absence_request(self.pid)
        self.db.set_chips(self.pid, 12345)

        result = self.fake._remote_cancel_absence_request(self.pid)

        self.assertTrue(result["ok"])
        after = self.db.get_player(self.pid)
        self.assertIsNone(after["absent_declared_at"])
        self.assertEqual(after["chips"], 12345)  # jamais réinitialisés (pas reinstate_player)


class RemoteWithdrawAbsentPlayerOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_withdraw_absent_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db_cross_thread(self._tmp.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.fake = _FakeRemoteAbsenceApp(self.db)
        self.addCleanup(self.fake.stop)

    def test_desactive_un_absent_eligible(self):
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        self.db.declare_absent(self.pid)

        result = self.fake._remote_withdraw_absent_player_request(self.pid)

        self.assertTrue(result["ok"])
        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")
        self.assertEqual(self.fake.tables_refreshed, 1)

    def test_refuse_si_pas_signale_absent(self):
        """Précaution du 2026-09-28 : jamais une confiance aveugle envers
        le client, même si celui-ci a déjà demandé confirmation avant
        d'appeler cette route — l'éligibilité réelle est revérifiée ici."""
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)

        result = self.fake._remote_withdraw_absent_player_request(self.pid)

        self.assertFalse(result["ok"])
        self.assertEqual(self.db.get_player(self.pid)["status"], "active")

    def test_refuse_si_delai_pas_encore_depasse(self):
        """"Ne jamais désactiver automatiquement un joueur" (cahier des
        charges, section 4) — même signalé absent, la désactivation reste
        REFUSÉE tant que le délai n'est pas dépassé, quelle que soit la
        demande reçue."""
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)

        result = self.fake._remote_withdraw_absent_player_request(self.pid)

        self.assertFalse(result["ok"])
        self.assertEqual(self.db.get_player(self.pid)["status"], "active")


# =====================================================================
# 2. Bout en bout HTTP réel
# =====================================================================
class RemoteAbsenceManagementHttpTest(_RemoteControlHttpTestCase):
    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="remote_absence_http_")
        self.addCleanup(self._tmp2.cleanup)
        self.db = _new_db_cross_thread(self._tmp2.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.pid_a = self.db.add_player("Alice")
        self.pid_b = self.db.add_player("Bob")
        self.declared_ids = []
        self.cancelled_ids = []
        self.withdrawn_ids = []

        def _get_players():
            return [
                {"id": p["id"], "name": p["name"], "absent": bool(p["absent_declared_at"])}
                for p in self.db.list_players(status="active")
            ]

        def _declare(player_id):
            self.declared_ids.append(player_id)
            ok = self.db.declare_absent(player_id)
            return {"ok": ok, "message": "" if ok else "refusé"}

        def _cancel(player_id):
            self.cancelled_ids.append(player_id)
            self.db.cancel_absence(player_id)
            return {"ok": True, "message": ""}

        def _withdraw(player_id):
            self.withdrawn_ids.append(player_id)
            if not self.db.absence_deadline_passed():
                return {"ok": False, "message": "Délai pas dépassé."}
            self.db.withdraw_player(player_id)
            return {"ok": True, "message": ""}

        self.server.stop()
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_players=_get_players,
            on_declare_absence=_declare,
            on_cancel_absence=_cancel,
            on_withdraw_absent_player=_withdraw,
            get_absence_deadline_passed=lambda: self.db.absence_deadline_passed(),
            # « Gestion de l'accueil » activée pour ce test HTTP bout en
            # bout (modification du 2026-09-29) : ce module teste
            # précisément les routes d'absence, désormais refusées côté
            # serveur lorsque ce réglage est désactivé (défaut).
            get_accueil_management_enabled=lambda: True,
            port=self.port,
        )
        self.server.start()

    def test_declare_absence_confirme_uniquement_le_joueur_vise(self):
        _start_tournament(self.db)
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(
            self.base, "POST", "/declare_absence", jar, body={"player_id": self.pid_a},
        )

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(self.declared_ids, [self.pid_a])
        self.assertIsNotNone(self.db.get_player(self.pid_a)["absent_declared_at"])
        self.assertIsNone(self.db.get_player(self.pid_b)["absent_declared_at"])

    def test_declare_absence_refuse_sans_authentification(self):
        status, _, _ = http_request(
            self.base, "POST", "/declare_absence", body={"player_id": self.pid_a},
        )
        self.assertEqual(status, 401)

    def test_cancel_absence_annule_le_joueur_vise(self):
        _start_tournament(self.db)
        self.db.declare_absent(self.pid_a)
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(
            self.base, "POST", "/cancel_absence", jar, body={"player_id": self.pid_a},
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(self.cancelled_ids, [self.pid_a])
        self.assertIsNone(self.db.get_player(self.pid_a)["absent_declared_at"])

    def test_withdraw_absent_player_desactive_apres_le_delai(self):
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        self.db.declare_absent(self.pid_a)
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(
            self.base, "POST", "/withdraw_absent_player", jar, body={"player_id": self.pid_a},
        )

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(self.db.get_player(self.pid_a)["status"], "withdrawn")

    def test_withdraw_absent_player_refuse_sans_authentification(self):
        status, _, _ = http_request(
            self.base, "POST", "/withdraw_absent_player", body={"player_id": self.pid_a},
        )
        self.assertEqual(status, 401)

    def test_absence_state_expose_le_delai(self):
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(self.base, "GET", "/absence_state", jar)

        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["deadline_passed"])

    def test_absence_state_avant_le_delai(self):
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(self.base, "GET", "/absence_state", jar)

        data = json.loads(body)
        self.assertFalse(data["deadline_passed"])

    def test_absence_state_refuse_sans_authentification(self):
        status, _, _ = http_request(self.base, "GET", "/absence_state")
        self.assertEqual(status, 401)

    def test_page_eliminate_contient_le_bouton_abs_des(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(self.base, "GET", "/eliminate", jar)
        self.assertEqual(status, 200)
        self.assertIn(b"btn-abs-des", body)


if __name__ == "__main__":
    unittest.main()
