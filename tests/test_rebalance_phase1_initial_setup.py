# -*- coding: utf-8 -*-
"""Test ciblé de la PHASE 1 (mise en place initiale) de l'architecture de
rééquilibrage validée le 2026-09-10 : avant le tout premier démarrage du
chronomètre (clock_started == 0, valeur par défaut d'un tournoi tout
juste créé — voir DEFAULT_SETTINGS), le guidage par la grosse blinde ne
doit JAMAIS se déclencher, même si la préférence "Équilibrage guidé par
la grosse blinde" est activée (valeur par défaut) — voir database.py:
rebalance_tables. La mise en place des tables doit rester entièrement
automatique : aucune question, aucun téléphone requis, une répartition
immédiatement équilibrée qui respecte "Nombre de sièges par table".

Reproduit le scénario exact du bug rapporté (tournoi To040926, 27
joueurs, 8 sièges par table, 4 joueurs minimum par table) via de vrais
appels successifs à Database.add_player — exactement comme le fait
App._choose_tournament_file lors de la création d'un nouveau tournoi
(inscriptions une par une, AVANT le premier démarrage du chrono).

Utilise une vraie Database SQLite en mémoire (pas de doublure) : ce
correctif touche une logique métier réelle. export_prefs est mocké
partout (jamais d'accès à ~/.poker_tournament/export_prefs.json)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


class Phase1AucunGuidageAvantDemarrageTest(unittest.TestCase):
    def setUp(self):
        # True partout == "Équilibrage guidé par la grosse blinde" activé
        # (comportement par défaut) : si la question 1 de cette classe
        # (aucun pending_rebalance en Phase 1) échouait, ce serait à
        # cause d'un vrai défaut de gating, jamais parce que la
        # préférence était déjà désactivée par coïncidence.
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 8, "min_players_per_table": 4,
        })
        # clock_started reste à sa valeur par défaut ("0", voir
        # DEFAULT_SETTINGS) : c'est précisément l'état "avant le premier
        # démarrage" que cette classe vérifie — jamais modifié ici.
        self.assertEqual(self.db.get_setting_int("clock_started", -1), 0)

    def test_27_inscriptions_aucun_pending_rebalance_et_tables_equilibrees(self):
        for i in range(1, 28):
            self.db.add_player(f"Joueur {i}")
            # Vérifié à CHAQUE inscription, pas seulement à la fin : le
            # bug reproduit dans ce projet vient précisément d'un
            # pending_rebalance créé DURANT les inscriptions initiales
            # (voir add_player, qui appelle rebalance_tables() après
            # chaque siège attribué).
            self.assertIsNone(
                self.db.pending_rebalance,
                f"pending_rebalance ne doit jamais exister avant le premier "
                f"démarrage (créé après l'inscription n°{i})",
            )

        tables = self.db.list_tables()
        counts = [
            self.db.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            for t in tables
        ]
        self.assertEqual(sum(counts), 27)
        # Aucune table au-dessus de "Nombre de sièges par table".
        for t, c in zip(tables, counts):
            self.assertLessEqual(c, t["max_seats"], f"{t['name']} dépasse max_seats")
        # Répartition équilibrée (écart maximal < 2, comme le garantit le
        # mécanisme historique automatique) : avec 27 joueurs / 8 sièges,
        # 4 tables (ceil(27/8)) réparties le plus uniformément possible
        # donnent un écart d'au plus 1 (7/7/7/6).
        self.assertLessEqual(max(counts) - min(counts), 1)
        self.assertEqual(len(tables), 4)

    def test_reduction_9_vers_8_avant_demarrage_reste_automatique(self):
        """Complément à la Phase 2 (voir test_rebalance_phase2_max_seats_
        change.py, qui teste le VRAI point d'entrée App._collect_and_
        save_all_settings) : au niveau base de données seule, la
        combinaison set_all_tables_max_seats + rebalance_tables reste
        utilisable sans restriction avant le premier démarrage — c'est
        cette combinaison que main.py continue d'appeler sans condition
        quand clock_started == 0.

        18 joueurs sur 2 tables à 9 (placés directement, pas via
        add_player, pour contrôler précisément l'état de départ) : total
        > FINAL_TABLE_MAX_SEATS (10), donc la convention "table finale"
        ne doit PAS interférer avec ce test (contrairement à un total
        plus petit, où le nombre de tables serait forcé à 1 quel que soit
        max_seats — voir rebalance_tables)."""
        self.db.set_settings({"max_seats_per_table": 9})
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        for seat in range(1, 10):
            self.db.conn.execute(
                "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
                "chips, status, bounty, club, table_id, seat) "
                "VALUES (?, 1, 0, 0, 10000, 'active', 0, '', ?, ?)",
                (f"T1-{seat}", t1_id, seat),
            )
            self.db.conn.execute(
                "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
                "chips, status, bounty, club, table_id, seat) "
                "VALUES (?, 1, 0, 0, 10000, 'active', 0, '', ?, ?)",
                (f"T2-{seat}", t2_id, seat),
            )
        self.db.conn.commit()

        self.db.set_settings({"max_seats_per_table": 8})
        self.db.set_all_tables_max_seats(8)
        self.db.rebalance_tables()

        self.assertIsNone(self.db.pending_rebalance)
        tables = self.db.list_tables()
        counts = [
            self.db.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            for t in tables
        ]
        for t, c in zip(tables, counts):
            self.assertLessEqual(c, 8, f"{t['name']} dépasse max_seats=8")
        self.assertEqual(sum(counts), 18)


if __name__ == "__main__":
    unittest.main()
