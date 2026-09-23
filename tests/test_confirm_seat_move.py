# -*- coding: utf-8 -*-
"""Confirmation individuelle d'un mouvement de tables/sièges (demande du
2026-09-19, "ergonomie iPhone" — confirmer un mouvement A/B/C un par un
depuis le téléphone pendant que D reste en attente).

Diagnostic préalable, vérifié dans le code AVANT ce correctif (voir la
conversation) : `players.table_id`/`seat` sont déjà mis à jour et commités
au moment même du calcul du rééquilibrage (rebalance_tables,
resolve_pending_rebalance, undo_last_elimination) — BIEN AVANT que la
ligne seat_moves correspondante ne soit écrite. `seat_moves` n'est donc
qu'un journal/affichage de ce qui a déjà été appliqué, jamais une zone
d'attente : confirmer un mouvement individuellement n'a donc RIEN à
"appliquer" — seulement à faire disparaître sa ligne, exactement comme
`Database.confirm_seat_move` ci-dessous."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


class ConfirmSeatMoveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="confirm_seat_move_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat):
        cur = self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-09-19 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()
        return cur.lastrowid

    def test_confirme_uniquement_la_ligne_visee(self):
        """Scénario exact de la demande : A, B, C confirmés individuellement,
        D reste seul dans la liste."""
        id_a = self._insert_move("A", "Table 1", 4, "Table 3", 2)
        id_b = self._insert_move("B", "Table 2", 2, "Table 4", 5)
        id_c = self._insert_move("C", "Table 3", 6, "Table 1", 3)
        id_d = self._insert_move("D", "Table 4", 1, "Table 2", 7)

        self.db.confirm_seat_move(id_a)
        self.db.confirm_seat_move(id_b)
        self.db.confirm_seat_move(id_c)

        remaining = self.db.get_seat_moves()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], id_d)
        self.assertEqual(remaining[0]["player_name"], "D")
        self.assertEqual(remaining[0]["old_table_name"], "Table 4")
        self.assertEqual(remaining[0]["old_seat"], 1)
        self.assertEqual(self.db.count_seat_moves(), 1)

    def test_idempotent_sur_un_id_deja_confirme(self):
        move_id = self._insert_move("A", "Table 1", 1, "Table 2", 2)
        self.db.confirm_seat_move(move_id)
        self.db.confirm_seat_move(move_id)  # ne doit lever aucune exception
        self.assertEqual(self.db.count_seat_moves(), 0)

    def test_idempotent_sur_un_id_inconnu(self):
        self._insert_move("A", "Table 1", 1, "Table 2", 2)
        self.db.confirm_seat_move(999999)  # id qui n'a jamais existé
        self.assertEqual(self.db.count_seat_moves(), 1)

    def test_ne_touche_jamais_players(self):
        """La place du joueur en base est déjà la bonne AVANT toute
        confirmation (voir le diagnostic en tête de fichier) —
        confirm_seat_move ne doit donc écrire nulle part dans players."""
        cur = self.db.conn.execute(
            "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
            "chips, status, bounty, club, table_id, seat) "
            "VALUES ('A', 1, 0, 0, 10000, 'active', 0, '', 3, 2)"
        )
        self.db.conn.commit()
        player_id = cur.lastrowid
        move_id = self._insert_move("A", "Table 1", 4, "Table 3", 2)

        before = dict(self.db.get_player(player_id))
        self.db.confirm_seat_move(move_id)
        after = dict(self.db.get_player(player_id))

        self.assertEqual(before, after)
        self.assertEqual(after["table_id"], 3)
        self.assertEqual(after["seat"], 2)


if __name__ == "__main__":
    unittest.main()
