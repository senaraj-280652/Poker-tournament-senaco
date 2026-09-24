# -*- coding: utf-8 -*-
"""Chantier "sélection directe du joueur UTG" (2026-09-24) : remplace la
question "quel siège est grosse blinde ?" par "quel joueur est UTG ?" —
le téléphone désigne directement le joueur à déplacer (player_id), le
serveur ne dérive plus rien d'un numéro de siège.

Deux volets, comme les autres chantiers de ce module :
1. PlayerStillAtTableTest / ResolvePendingRebalancePlayerIdTest —
   database.py directement (pas de serveur HTTP) : validation par
   identité de joueur, toutes les protections existantes (request_id
   périmé, table source changée, joueur éliminé/déplacé entre-temps,
   double réponse, "Continuer sans désigner le joueur", destination et
   capacité inchangées).
2. RebalancePendingHttpTest / RebalanceAnswerHttpTest / PermissionsTest —
   VRAI RemoteControlServer (même principe que tests/test_remote_
   control_dirto_permissions_enforcement.py) : format JSON de
   /rebalance_pending, /rebalance_answer envoie bien player_id, ADMIN/
   DIRTO.
3. WidgetTextTest — vérification statique des textes visibles de
   remote_control._REBALANCE_WIDGET (aucun ancien texte "grosse blinde"
   ne doit y rester)."""
import os
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def _seat_new_player(db, table_id, seat, name):
    cur = db.conn.execute(
        "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
        "chips, status, bounty, club) VALUES (?, 1, 0, 0, 10000, 'active', 0, '')",
        (name,),
    )
    player_id = cur.lastrowid
    db.conn.execute(
        "UPDATE players SET table_id=?, seat=? WHERE id=?",
        (table_id, seat, player_id),
    )
    db.conn.commit()
    return player_id


# =====================================================================
# 1. database.py directement
# =====================================================================
class _TwoTablesDbTestCase(unittest.TestCase):
    """Écart de 6 joueurs (9 vs 3), comme les autres tests de ce chantier
    (tests/test_rebalance_phase3_live_tournament.py)."""

    def setUp(self):
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1_id = self.db.list_tables()[0]["id"]
        self.t2_id = self.db.add_table("Table 2")
        self.t1_players = {
            seat: _seat_new_player(self.db, self.t1_id, seat, f"T1-{seat}")
            for seat in range(1, 10)
        }
        self.t2_players = {
            seat: _seat_new_player(self.db, self.t2_id, seat, f"T2-{seat}")
            for seat in range(1, 4)
        }
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

    def _counts(self):
        def count(table_id):
            return self.db.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (table_id,),
            ).fetchone()["c"]
        return count(self.t1_id), count(self.t2_id)


class PlayerStillAtTableTest(_TwoTablesDbTestCase):
    def test_joueur_actif_a_la_bonne_table_est_valide(self):
        pid = self.t1_players[3]
        self.assertEqual(self.db._player_still_at_table(self.t1_id, pid), pid)

    def test_joueur_inexistant_est_invalide(self):
        self.assertIsNone(self.db._player_still_at_table(self.t1_id, 999999))

    def test_joueur_elimine_est_invalide(self):
        pid = self.t1_players[3]
        self.db.eliminate_player(pid)
        self.assertIsNone(self.db._player_still_at_table(self.t1_id, pid))

    def test_joueur_deplace_vers_une_autre_table_est_invalide(self):
        pid = self.t1_players[3]
        self.db.conn.execute("UPDATE players SET table_id=? WHERE id=?", (self.t2_id, pid))
        self.db.conn.commit()
        self.assertIsNone(self.db._player_still_at_table(self.t1_id, pid))
        # ... mais reste valide pour SA nouvelle table.
        self.assertEqual(self.db._player_still_at_table(self.t2_id, pid), pid)


class ResolvePendingRebalancePlayerIdTest(_TwoTablesDbTestCase):
    def test_joueur_valide_deplace_exactement_ce_joueur(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending)
        designated = self.t1_players[5]

        moves = self.db.resolve_pending_rebalance(pending["request_id"], designated)

        self.assertTrue(moves)
        self.assertEqual(moves[0]["player_name"], "T1-5")
        # Le joueur désigné n'est plus à la table 1.
        row = self.db.get_player(designated)
        self.assertNotEqual(row["table_id"], self.t1_id)
        c1, c2 = self._counts()
        self.assertEqual(c1 + c2, 12)

    def test_joueur_inexistant_ne_deplace_personne(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        before = self._counts()

        moves = self.db.resolve_pending_rebalance(pending["request_id"], 999999)

        self.assertEqual(moves, [])
        self.assertEqual(self._counts(), before)
        # La demande reste ouverte (aucune substitution, pas consommée).
        self.assertEqual(self.db.pending_rebalance["request_id"], pending["request_id"])

    def test_joueur_elimine_entretemps_ne_deplace_personne(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        designated = self.t1_players[7]
        self.db.eliminate_player(designated)
        before = self._counts()

        moves = self.db.resolve_pending_rebalance(pending["request_id"], designated)

        self.assertEqual(moves, [])
        self.assertEqual(self._counts(), before)

    def test_joueur_deplace_vers_une_autre_table_entretemps_ne_deplace_personne(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        designated = self.t1_players[2]
        # Reseat manuel vers t2 (simule une action concurrente).
        self.db.conn.execute(
            "UPDATE players SET table_id=?, seat=99 WHERE id=?", (self.t2_id, designated)
        )
        self.db.conn.commit()

        moves = self.db.resolve_pending_rebalance(pending["request_id"], designated)

        self.assertEqual(moves, [])

    def test_reponse_perimee_request_id_inconnu_ne_deplace_personne(self):
        self.db.rebalance_tables()
        before = self._counts()

        moves = self.db.resolve_pending_rebalance("request-id-perime-inconnu", self.t1_players[1])

        self.assertEqual(moves, [])
        self.assertEqual(self._counts(), before)

    def test_double_reponse_un_seul_deplacement_maximum(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        designated = self.t1_players[1]

        first = self.db.resolve_pending_rebalance(pending["request_id"], designated)
        self.assertTrue(first)
        # Rejoue EXACTEMENT la même réponse (même request_id, même
        # joueur) : déjà consommée, ne doit plus rien faire.
        second = self.db.resolve_pending_rebalance(pending["request_id"], designated)
        self.assertEqual(second, [])

    def test_table_source_changee_lancienne_reponse_ne_deplace_personne(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        old_request_id = pending["request_id"]
        # Fait disparaître l'écart sur t1 (referme t1 en vidant tous ses
        # joueurs vers "actif ailleurs" n'est pas trivial ici ; on simule
        # plutôt un besoin qui bascule en ajoutant tellement de joueurs à
        # t2 que t1 devient la table la MOINS pleine à son tour) :
        for seat in range(4, 10):
            _seat_new_player(self.db, self.t2_id, seat, f"T2-{seat}")
        # Un recalcul (ex. déclenché par une autre action) doit remplacer
        # la demande d'origine par une nouvelle, cohérente.
        moves = self.db.resolve_pending_rebalance(old_request_id, self.t1_players[1])
        self.assertEqual(moves, [])
        self.assertNotEqual(
            (self.db.pending_rebalance or {}).get("request_id"), old_request_id,
        )

    def test_continuer_sans_designer_utilise_toujours_legacy_pick_mover(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance

        moves = self.db.resolve_pending_rebalance(pending["request_id"], None)

        self.assertTrue(moves)
        self.assertEqual(moves[0]["reason"], database.MOVE_REASON_BB_SKIPPED)

    def test_joueur_designe_journalise_reason_utg_guided(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        moves = self.db.resolve_pending_rebalance(pending["request_id"], self.t1_players[1])
        self.assertEqual(moves[0]["reason"], database.MOVE_REASON_BB_GUIDED)

    def test_aucun_depassement_de_capacite(self):
        self.db.set_settings({"max_seats_per_table": 9})
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        moves = self.db.resolve_pending_rebalance(pending["request_id"], self.t1_players[1])
        self.assertTrue(moves)
        dest_table_id = self.db.get_player(self.t1_players[1])["table_id"]
        count = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (dest_table_id,)
        ).fetchone()["c"]
        max_seats = self.db.conn.execute(
            "SELECT max_seats FROM tables_pk WHERE id=?", (dest_table_id,)
        ).fetchone()["max_seats"]
        self.assertLessEqual(count, max_seats)


# =====================================================================
# 2. Intégration HTTP (VRAI RemoteControlServer)
# =====================================================================
class _RebalanceHttpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="rebalance_utg_http_test_")
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

        self.diag_log_path = os.path.join(self._tmp.name, "remote_control.log")
        for target in (
            patch.object(remote_control, "_LOG_PATH", self.diag_log_path),
            patch.object(remote_control, "_logger", None),
        ):
            self.addCleanup(target.stop)
            target.start()

        # action_log.py (LOG Phase 1) redirigé, comme dans tests/test_
        # action_log.py — n'affecte jamais le vrai ~/.poker_tournament.
        import action_log
        self.action_log_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        al_patcher = patch.object(action_log, "_log_path", return_value=self.action_log_path)
        self.addCleanup(al_patcher.stop)
        al_patcher.start()

        self._players = []
        self._pending = None
        self.rebalance_answer_calls = []
        self._dirto_permissions = {}

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = remote_control.RemoteControlServer(
            on_word=lambda word: None,
            get_tournament_name=lambda: "Tournoi",
            get_tournament_path=lambda: "/tmp/x.tournoi",
            get_players=lambda: self._players,
            get_pending_rebalance=lambda: self._pending,
            on_rebalance_answer=lambda rid, pid: self.rebalance_answer_calls.append((rid, pid)),
            get_dirto_permissions=lambda name: self._dirto_permissions.get((name or "").strip(), frozenset()),
            port=self.port,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

    def _jar(self, owner_name=None, label="Test"):
        return authenticated_jar(self.base, owner_name=owner_name, label=label)


class RebalancePendingHttpTest(_RebalanceHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_players_contient_player_id_name_seat_trie_par_siege(self):
        self._pending = {"request_id": "r1", "table_id": 1, "table_name": "Table 1", "seats": [1, 4, 6]}
        self._players = [
            {"id": 23, "name": "Bob", "table": "Table 1", "seat": 6, "has_photo": False},
            {"id": 17, "name": "Alice", "table": "Table 1", "seat": 1, "has_photo": False},
            {"id": 31, "name": "Chloé", "table": "Table 1", "seat": 4, "has_photo": False},
            {"id": 99, "name": "Autre table", "table": "Table 2", "seat": 1, "has_photo": False},
        ]
        status, body, _ = http_request(self.base, "GET", "/rebalance_pending", self.jar)
        self.assertEqual(status, 200, body)
        import json
        data = json.loads(body)
        self.assertEqual(data["request_id"], "r1")
        self.assertEqual(
            data["players"],
            [
                {"player_id": 17, "name": "Alice", "seat": 1},
                {"player_id": 31, "name": "Chloé", "seat": 4},
                {"player_id": 23, "name": "Bob", "seat": 6},
            ],
        )
        self.assertNotIn("seats", data)
        self.assertNotIn("seat_players", data)

    def test_aucun_pending_renvoie_null(self):
        self._pending = None
        status, body, _ = http_request(self.base, "GET", "/rebalance_pending", self.jar)
        self.assertEqual(status, 200, body)
        self.assertEqual(body, b"null")


class RebalanceAnswerHttpTest(_RebalanceHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = self._jar(owner_name="Raj", label="iPhone de Raj")

    def test_envoie_bien_player_id_au_callback(self):
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", self.jar,
            body={"request_id": "r1", "player_id": 42},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(self.rebalance_answer_calls, [("r1", 42)])

    def test_continuer_sans_designer_envoie_player_id_none(self):
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", self.jar,
            body={"request_id": "r1", "player_id": None},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(self.rebalance_answer_calls, [("r1", None)])


class PermissionsTest(_RebalanceHttpTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)

    def test_admin_fonctionne(self):
        jar = self._jar(owner_name="Raj", label="iPhone de Raj")
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", jar, body={"request_id": "r1", "player_id": 1},
        )
        self.assertEqual(status, 200, body)

    def test_dirto_avec_permission_rebalance_fonctionne(self):
        self._dirto_permissions = {"Marie": frozenset({"rebalance"})}
        jar = self._jar(owner_name="Marie", label="iPhone de Marie")
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", jar, body={"request_id": "r1", "player_id": 1},
        )
        self.assertEqual(status, 200, body)

    def test_dirto_sans_permission_rebalance_refuse(self):
        self._dirto_permissions = {"Marie": frozenset()}
        jar = self._jar(owner_name="Marie", label="iPhone de Marie")
        status, body, _ = http_request(
            self.base, "POST", "/rebalance_answer", jar, body={"request_id": "r1", "player_id": 1},
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(self.rebalance_answer_calls, [])

    def test_autorisation_dirto_existante_cle_rebalance_compatible(self):
        # Simule une autorisation DÉJÀ accordée avant ce chantier (clé de
        # stockage "rebalance", jamais renommée) : doit continuer à
        # fonctionner sans aucune migration.
        self._dirto_permissions = {"Marie": frozenset({"rebalance", "tables"})}
        jar = self._jar(owner_name="Marie", label="iPhone de Marie")
        status, body, _ = http_request(self.base, "GET", "/rebalance_pending", jar)
        self.assertEqual(status, 200, body)


# =====================================================================
# 3. Textes visibles (statique, aucun Tk/navigateur nécessaire)
# =====================================================================
class WidgetTextTest(unittest.TestCase):
    def test_nouveau_texte_utg_present(self):
        self.assertIn("Quel joueur est UTG ?", remote_control._REBALANCE_WIDGET)
        self.assertIn("Sélectionnez le joueur qui doit être déplacé.", remote_control._REBALANCE_WIDGET)
        self.assertIn("Continuer sans désigner le joueur", remote_control._REBALANCE_WIDGET)

    def test_ancien_texte_grosse_blinde_absent(self):
        self.assertNotIn("grosse blinde", remote_control._REBALANCE_WIDGET.lower())
        self.assertNotIn("indiquer la bb", remote_control._REBALANCE_WIDGET.lower())

    def test_widget_envoie_player_id_jamais_seat(self):
        self.assertIn("player_id:", remote_control._REBALANCE_WIDGET)
        self.assertNotIn("seat: seat", remote_control._REBALANCE_WIDGET)

    def test_libelles_base_de_donnees_a_jour(self):
        self.assertEqual(
            database.REMOTE_PERMISSION_LABELS[database.REMOTE_PERMISSION_REBALANCE],
            "Répondre au rééquilibrage (UTG)",
        )
        self.assertEqual(database.MOVE_REASON_LABELS[database.MOVE_REASON_BB_GUIDED], "UTG (guidé)")
        self.assertNotIn("grosse blinde", database.MOVE_REASON_LABELS[database.MOVE_REASON_BB_GUIDED].lower())

    def test_cle_de_permission_rebalance_inchangee(self):
        # Compatibilité : la CLÉ de stockage ne doit jamais changer, seul
        # le libellé affiché (vérifié ci-dessus) a été mis à jour.
        self.assertEqual(database.REMOTE_PERMISSION_REBALANCE, "rebalance")
        self.assertEqual(remote_control._PERM_REBALANCE, "rebalance")


if __name__ == "__main__":
    unittest.main()
