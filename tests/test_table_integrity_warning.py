# -*- coding: utf-8 -*-
"""Test ciblé de App._warn_if_table_integrity_issue (main.py) — détection
SEULE, à l'ouverture d'un tournoi, d'une table déjà en surcapacité (voir
Database.check_table_integrity) : avertissement uniquement, AUCUNE
correction automatique (demande explicite du 2026-09-10)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402


def _seat_new_player(db, table_id, seat, name):
    cur = db.conn.execute(
        "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
        "chips, status, bounty, club) VALUES (?, 1, 0, 0, 10000, 'active', 0, '')",
        (name,),
    )
    player_id = cur.lastrowid
    db.conn.execute(
        "UPDATE players SET table_id=?, seat=? WHERE id=?", (table_id, seat, player_id)
    )
    db.conn.commit()
    return player_id


class WarnIfTableIntegrityIssueTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()
        self._tmp = tempfile.TemporaryDirectory(prefix="table_integrity_warning_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 8, "min_players_per_table": 4})

        # Doublure minimale de App : seule self.db est lue par la méthode
        # testée, pas besoin d'une vraie fenêtre Tk pour ça.
        self.win = types.SimpleNamespace(db=self.db)
        self.win._warn_if_table_integrity_issue = types.MethodType(
            main.App._warn_if_table_integrity_issue, self.win
        )

    def test_fichier_coherent_aucun_avertissement(self):
        for i in range(1, 9):
            self.db.add_player(f"J{i}")
        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._warn_if_table_integrity_issue()
            mock_messagebox.showwarning.assert_not_called()

    def test_fichier_incoherent_avertissement_affiche_sans_rien_corriger(self):
        t1_id = self.db.list_tables()[0]["id"]
        self.db.conn.execute("UPDATE tables_pk SET max_seats=8 WHERE id=?", (t1_id,))
        self.db.conn.commit()
        for seat in range(1, 11):
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._warn_if_table_integrity_issue()
            mock_messagebox.showwarning.assert_called_once()
            title, message = mock_messagebox.showwarning.call_args[0]
            self.assertIn("Table 1", message)
            self.assertIn("10", message)
            self.assertIn("8", message)

        # AUCUNE correction automatique : l'état doit être resté
        # rigoureusement identique après l'avertissement.
        occ_after = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (t1_id,)
        ).fetchone()["c"]
        self.assertEqual(occ_after, 10)
        max_seats_after = self.db.conn.execute(
            "SELECT max_seats FROM tables_pk WHERE id=?", (t1_id,)
        ).fetchone()["max_seats"]
        self.assertEqual(max_seats_after, 8)

    def test_sans_db_ne_plante_pas(self):
        self.win.db = None
        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._warn_if_table_integrity_issue()
            mock_messagebox.showwarning.assert_not_called()


if __name__ == "__main__":
    unittest.main()
