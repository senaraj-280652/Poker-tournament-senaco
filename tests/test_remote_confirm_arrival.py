# -*- coding: utf-8 -*-
"""Accueil des joueurs — Lot 1 du cahier des charges "Senaco — Accueil des
joueurs et gestion des absences" (2026-09-27) : recherche des joueurs
pré-inscrits, confirmation de leur arrivée, affichage table/siège,
compteurs, permission dédiée "checkin".

Trois niveaux, même structure que tests/test_remote_confirm_move.py
(précédent direct, réutilisé comme modèle) :
1. Database.confirm_arrival/cancel_arrival — effet strictement limité à
   `arrived_at`, jamais table_id/seat/chips/status, jamais de
   rééquilibrage (LIMITE IMPÉRATIVE du Lot 1 : ne jamais toucher au
   mécanisme de rééquilibrage).
2. Orchestration pure (App._remote_confirm_arrival_request/_remote_
   confirm_arrival, et l'annulation), via une doublure légère + un VRAI
   thread consommateur — jamais le thread HTTP lui-même touché à self.db.
3. Bout en bout HTTP réel (GET /checkin_players, POST /confirm_arrival,
   POST /cancel_arrival, page /checkin, permission "checkin")."""
import json
import os
import queue
import sys
import tempfile
import threading
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
    # (modification du 2026-09-29) : ce module teste précisément
    # l'accueil des joueurs, réglage propre au tournoi Open qui l'active
    # — voir test_accueil_management_toggle.py pour la couverture du
    # réglage désactivé (comportement par défaut).
    db.set_settings({"accueil_management_enabled": 1})
    db.conn.close()
    db.conn = sqlite3.connect(path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    return db


# =====================================================================
# 1. Database.confirm_arrival / cancel_arrival
# =====================================================================
class ConfirmArrivalDatabaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="confirm_arrival_db_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "t.tournoi"))
        self.db.set_settings({"accueil_management_enabled": 1})
        self.addCleanup(self.db.conn.close)

    def test_arrived_at_absent_a_l_inscription(self):
        pid = self.db.add_player("Alice")
        self.assertIsNone(self.db.get_player(pid)["arrived_at"])

    def test_confirm_arrival_horodate_sans_rien_d_autre_toucher(self):
        pid = self.db.add_player("Alice")
        before = dict(self.db.get_player(pid))

        self.db.confirm_arrival(pid)

        after = dict(self.db.get_player(pid))
        self.assertIsNotNone(after["arrived_at"])
        for key in ("table_id", "seat", "chips", "status", "bounty", "buyin_count"):
            self.assertEqual(after[key], before[key], key)

    def test_confirm_arrival_ne_declenche_aucun_mouvement(self):
        """LIMITE IMPÉRATIVE du Lot 1 : jamais un appel à rebalance_tables
        depuis confirm_arrival — vérifié ici en constatant qu'aucune ligne
        n'apparaît dans seat_moves et qu'AUCUN autre joueur ne change de
        table/siège suite à une confirmation d'arrivée."""
        ids = [self.db.add_player(f"J{i}") for i in range(6)]
        before_positions = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in ids
        }

        for pid in ids:
            self.db.confirm_arrival(pid)

        self.assertEqual(self.db.count_seat_moves(), 0)
        for pid in ids:
            after = self.db.get_player(pid)
            self.assertEqual((after["table_id"], after["seat"]), before_positions[pid])

    def test_confirm_arrival_est_idempotent(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        first = self.db.get_player(pid)["arrived_at"]

        self.db.confirm_arrival(pid)  # double-tap / retry réseau

        second = self.db.get_player(pid)["arrived_at"]
        self.assertIsNotNone(second)
        self.assertGreaterEqual(second, first)

    def test_cancel_arrival_efface_l_horodatage_sans_rien_d_autre_toucher(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        before = dict(self.db.get_player(pid))

        self.db.cancel_arrival(pid)

        after = dict(self.db.get_player(pid))
        self.assertIsNone(after["arrived_at"])
        for key in ("table_id", "seat", "chips", "status"):
            self.assertEqual(after[key], before[key], key)

    def test_cancel_arrival_sans_confirmation_prealable_ne_leve_rien(self):
        pid = self.db.add_player("Alice")
        self.db.cancel_arrival(pid)  # jamais confirmé : doit rester sans effet, pas d'exception
        self.assertIsNone(self.db.get_player(pid)["arrived_at"])


class ArrivedAtMigrationTest(unittest.TestCase):
    """_migrate() doit ajouter `arrived_at` (NULL) à un ancien fichier
    .tournoi créé AVANT l'existence de cette colonne (même schéma de test
    que le pattern déjà établi pour bounty/kills/club, voir database.py:
    _migrate)."""

    def test_ancien_fichier_sans_arrived_at_est_migre(self):
        tmp = tempfile.TemporaryDirectory(prefix="arrived_at_migration_test_")
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "old.tournoi")

        db = database.Database(path)
        db.conn.execute("ALTER TABLE players DROP COLUMN arrived_at")
        db.conn.commit()
        db.conn.close()

        reopened = database.Database(path)
        self.addCleanup(reopened.conn.close)
        cols = {row["name"] for row in reopened.conn.execute("PRAGMA table_info(players)")}
        self.assertIn("arrived_at", cols)
        pid = reopened.add_player("Alice")
        self.assertIsNone(reopened.get_player(pid)["arrived_at"])
        reopened.set_settings({"accueil_management_enabled": 1})
        reopened.confirm_arrival(pid)
        self.assertIsNotNone(reopened.get_player(pid)["arrived_at"])


class CheckinPermissionLabelsTest(unittest.TestCase):
    def test_checkin_present_dans_remote_permission_labels(self):
        self.assertIn(database.REMOTE_PERMISSION_CHECKIN, database.REMOTE_PERMISSION_LABELS)
        self.assertEqual(database.REMOTE_PERMISSION_CHECKIN, "checkin")

    def test_checkin_present_dans_all_permissions(self):
        self.assertIn(remote_control._PERM_CHECKIN, remote_control._ALL_PERMISSIONS)


# =====================================================================
# 2. Orchestration (App._remote_confirm_arrival_request / _remote_confirm_
#    arrival, et l'annulation)
# =====================================================================
class _FakeRemoteCheckinApp:
    """Doublure légère, même principe que _FakeRemoteMovesApp (tests/
    test_remote_confirm_move.py) : self.db réel, consommateur de voice_
    command_queue tournant dans un VRAI thread séparé (reproduit App.
    _poll_voice_queue), jamais la logique métier réimplémentée ici."""

    def __init__(self, db):
        self.db = db
        self.voice_command_queue = queue.Queue()
        self._remote_arrival_results = {}
        self._remote_checkin_cache = []
        # Synchronisation avec la case à cocher de l'onglet Joueurs (Lot 1,
        # précision du 2026-09-28) : hors du périmètre de ce fichier (voir
        # tests/test_players_checkbox_accueil_sync.py pour sa couverture
        # dédiée) — simple doublure sans effet ici.
        self._sync_checkbox_for_arrival = lambda player_id, arrived: None
        self._consumer_stop = threading.Event()
        self._consumer_thread = threading.Thread(target=self._consume, daemon=True)
        for meth in (
            "_remote_confirm_arrival_request", "_remote_cancel_arrival_request",
            "_await_remote_arrival_result", "_refresh_remote_checkin_cache",
        ):
            setattr(self, meth, types.MethodType(getattr(main.App, meth), self))
        self._consumer_thread.start()

    def _consume(self):
        while not self._consumer_stop.is_set():
            try:
                item = self.voice_command_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if isinstance(item, tuple) and item and item[0] == "confirm_arrival":
                _, player_id, request_id = item
                main.App._remote_confirm_arrival(self, player_id, request_id=request_id)
            elif isinstance(item, tuple) and item and item[0] == "cancel_arrival":
                _, player_id, request_id = item
                main.App._remote_cancel_arrival(self, player_id, request_id=request_id)

    def stop(self):
        self._consumer_stop.set()
        self._consumer_thread.join(timeout=2)


class RemoteConfirmArrivalOrchestrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_confirm_arrival_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db_cross_thread(self._tmp.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.id_a = self.db.add_player("Alice")
        self.id_b = self.db.add_player("Bob")
        self.fake = _FakeRemoteCheckinApp(self.db)
        self.addCleanup(self.fake.stop)

    def test_confirmer_l_arrivee_horodate_uniquement_ce_joueur(self):
        result = self.fake._remote_confirm_arrival_request(self.id_a)

        self.assertTrue(result["ok"])
        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])
        self.assertIsNone(self.db.get_player(self.id_b)["arrived_at"])

    def test_confirmer_rafraichit_le_cache_immediatement(self):
        self.fake._remote_confirm_arrival_request(self.id_a)

        cached = {p["id"]: p for p in self.fake._remote_checkin_cache}
        self.assertIsNotNone(cached[self.id_a]["arrived_at"])
        self.assertIsNone(cached[self.id_b]["arrived_at"])

    def test_annuler_une_arrivee_deja_confirmee_est_refuse(self):
        """Modification du 2026-09-30 ("accueil : correction des erreurs
        réservée au Mac") : une fois "Actif" (arrivée confirmée), l'iPhone
        ne peut plus JAMAIS annuler — quel que soit le délai. Seul le
        Mac (case à cocher avant démarrage, ou "Corriger l'arrivée" après
        démarrage) peut encore défaire une confirmation. Voir App._remote_
        cancel_arrival et tests/test_correct_arrival_mac_only.py."""
        self.fake._remote_confirm_arrival_request(self.id_a)

        result = self.fake._remote_cancel_arrival_request(self.id_a)

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("message"))
        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])

    def test_confirmer_un_joueur_deja_arrive_reste_sans_risque(self):
        """Double-tap / retry réseau : rejouer la même confirmation reste
        ok, sans effet de bord supplémentaire (idempotence)."""
        self.fake._remote_confirm_arrival_request(self.id_a)
        result = self.fake._remote_confirm_arrival_request(self.id_a)

        self.assertTrue(result["ok"])

    def test_confirmer_un_id_inexistant_echoue_proprement(self):
        result = self.fake._remote_confirm_arrival_request(999999)

        self.assertFalse(result["ok"])
        self.assertTrue(result.get("message"))

    def test_aucun_tournoi_ouvert_echoue_proprement(self):
        """_remote_confirm_arrival directement (sans passer par la file
        d'attente/le sondage, non nécessaires pour ce cas) : self.db=None
        doit renvoyer un échec propre, jamais une exception."""
        fake = types.SimpleNamespace(db=None, _remote_arrival_results={})

        main.App._remote_confirm_arrival(fake, self.id_a, request_id="req1")

        result = fake._remote_arrival_results["req1"]
        self.assertFalse(result["ok"])
        self.assertTrue(result.get("message"))


# =====================================================================
# 3. Bout en bout HTTP réel
# =====================================================================
class RemoteConfirmArrivalHttpTest(_RemoteControlHttpTestCase):
    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="remote_confirm_arrival_http_")
        self.addCleanup(self._tmp2.cleanup)
        self.db = _new_db_cross_thread(self._tmp2.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.id_a = self.db.add_player("Alice")
        self.id_b = self.db.add_player("Bob")
        self.confirmed_ids = []
        self.cancelled_ids = []

        def _checkin_players():
            tables = {t["id"]: t["name"] for t in self.db.list_tables(active_only=False)}
            return [
                {
                    "id": p["id"], "name": p["name"], "club": p["club"],
                    "table": tables.get(p["table_id"]), "seat": p["seat"],
                    "arrived_at": p["arrived_at"],
                }
                for p in self.db.list_players(status="active")
            ]

        def _confirm(player_id):
            self.confirmed_ids.append(player_id)
            self.db.confirm_arrival(player_id)
            return {"ok": True, "message": ""}

        def _cancel(player_id):
            self.cancelled_ids.append(player_id)
            self.db.cancel_arrival(player_id)
            return {"ok": True, "message": ""}

        self.server.stop()
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_checkin_players=_checkin_players,
            on_confirm_arrival=_confirm,
            on_cancel_arrival=_cancel,
            # « Gestion de l'accueil » activée pour ce test HTTP bout en
            # bout (modification du 2026-09-29) : ce module teste
            # précisément les routes d'accueil, désormais refusées côté
            # serveur lorsque ce réglage est désactivé (défaut).
            get_accueil_management_enabled=lambda: True,
            port=self.port,
        )
        self.server.start()

    def test_checkin_players_expose_la_liste_reelle(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(self.base, "GET", "/checkin_players", jar)
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual({d["name"] for d in data}, {"Alice", "Bob"})
        self.assertTrue(all(d["arrived_at"] is None for d in data))

    def test_checkin_players_refuse_sans_authentification(self):
        status, _, _ = http_request(self.base, "GET", "/checkin_players")
        self.assertEqual(status, 401)

    def test_confirm_arrival_confirme_uniquement_le_joueur_vise(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(
            self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.id_a},
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertEqual(self.confirmed_ids, [self.id_a])
        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])
        self.assertIsNone(self.db.get_player(self.id_b)["arrived_at"])

    def test_confirm_arrival_refuse_sans_authentification(self):
        status, _, _ = http_request(
            self.base, "POST", "/confirm_arrival", body={"player_id": self.id_a},
        )
        self.assertEqual(status, 401)

    def test_cancel_arrival_annule_le_joueur_vise(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        http_request(self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.id_a})

        status, body, _ = http_request(
            self.base, "POST", "/cancel_arrival", jar, body={"player_id": self.id_a},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertEqual(self.cancelled_ids, [self.id_a])
        self.assertIsNone(self.db.get_player(self.id_a)["arrived_at"])

    def test_page_checkin_redirige_vers_login_sans_authentification(self):
        status, _, headers = http_request(self.base, "GET", "/checkin")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")

    def test_page_checkin_accessible_une_fois_authentifie(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(self.base, "GET", "/checkin", jar)
        self.assertEqual(status, 200)
        self.assertIn(b"Retour", body)

    def test_bouton_accueil_present_sur_la_page_principale(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(self.base, "GET", "/", jar)
        self.assertEqual(status, 200)
        self.assertIn(b"btn-checkin", body)

    def test_confirm_move_reste_totalement_inchange(self):
        """Non-régression explicite : /confirm_move (Mouvements) n'a rien
        à voir avec /confirm_arrival (Accueil) et continue de fonctionner
        indépendamment — refusé ici faute d'un on_confirm_move fourni à ce
        serveur de test (valeur par défaut sûre), jamais une confusion des
        deux routes."""
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = http_request(
            self.base, "POST", "/confirm_move", jar, body={"move_id": 1},
        )
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertTrue(data["ok"])
        self.assertFalse(data["all_done"])


if __name__ == "__main__":
    unittest.main()
