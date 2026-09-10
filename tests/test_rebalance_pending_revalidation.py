"""Test ciblé du correctif de database.py: Database.rebalance_tables —
revalidation d'une question "grosse blinde" (pending_rebalance) DÉJÀ EN
ATTENTE, à chaque recalcul (pas seulement à la réponse). Reproduit le
symptôme décrit dans le code (voir rebalance_tables) : "une deuxième
question de grosse blinde alors qu'un seul déplacement était en réalité
nécessaire" — c'est-à-dire une demande devenue obsolète (l'écart qu'elle
visait à combler a été résorbé par un autre moyen entre-temps) qui
restait affichée sur le téléphone au lieu d'être effacée.

Utilise une vraie Database SQLite en mémoire (pas de doublure) : ce
correctif touche une logique métier réelle (comptage de joueurs par
table), pas un simple branchement — la tester sans la vraie détection
(_detect_simple_rebalance_need) n'aurait rien prouvé. export_prefs est
mocké partout (jamais d'accès à ~/.poker_tournament/export_prefs.json,
le vrai fichier de préférences de l'utilisateur)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


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


def _move_player(db, player_id, table_id, seat):
    db.conn.execute(
        "UPDATE players SET table_id=?, seat=? WHERE id=?",
        (table_id, seat, player_id),
    )
    db.conn.commit()


class _TwoTablesTestCase(unittest.TestCase):
    """Prépare 2 tables avec un écart de 6 joueurs (9 vs 3) — bien au-delà
    du seuil de déclenchement (>=2) et bien au-dessus de FINAL_TABLE_
    MAX_SEATS (10 joueurs actifs au total, 12 ici) pour ne jamais
    déclencher la fusion "table finale" ni la consolidation par
    min_players_per_table, qui perturberaient sinon le nombre de tables
    en cours de test."""

    def setUp(self):
        # export_prefs.load_value mocké : jamais d'accès au vrai fichier
        # ~/.poker_tournament/export_prefs.json. True partout ==
        # "Équilibrage guidé par la grosse blinde" activé (comportement
        # par défaut) — voir Database._bb_rebalance_prompt_enabled.
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 9, "min_players_per_table": 1})

        self.t1_id = self.db.list_tables()[0]["id"]  # "Table 1", créée par _init_defaults
        self.t2_id = self.db.add_table("Table 2")

        self.t1_players = [
            _seat_new_player(self.db, self.t1_id, seat, f"T1-{seat}")
            for seat in range(1, 10)
        ]
        self.t2_players = [
            _seat_new_player(self.db, self.t2_id, seat, f"T2-{seat}")
            for seat in range(1, 4)
        ]

    def _counts(self):
        def count(table_id):
            return self.db.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (table_id,),
            ).fetchone()["c"]
        return count(self.t1_id), count(self.t2_id)


class PendingRebalanceEffaceeSiEcartResorbeTest(_TwoTablesTestCase):
    def test_premier_appel_pose_bien_une_question(self):
        self.assertEqual(self._counts(), (9, 3))
        self.db.rebalance_tables()
        self.assertIsNotNone(self.db.pending_rebalance)
        self.assertEqual(self.db.pending_rebalance["table_id"], self.t1_id)
        self.assertEqual(self.db.pending_rebalance["seats"], list(range(1, 10)))

    def test_ecart_resorbe_entretemps_efface_la_question_au_lieu_de_la_garder(self):
        """Scénario exact demandé : une question BB est posée pour Table 1
        (9 vs 3) ; AVANT toute réponse, l'écart est résorbé par un autre
        moyen (ici : 3 joueurs déplacés de Table 1 vers Table 2, comme le
        ferait une élimination concurrente ailleurs ou tout autre
        événement — voir le commentaire de rebalance_tables). Le PROCHAIN
        recalcul (rebalance_tables, tel qu'appelé par eliminate_player/
        add_player à chaque mutation) doit alors effacer pending_rebalance
        plutôt que le laisser affiché pour rien sur le téléphone."""
        self.db.rebalance_tables()
        self.assertIsNotNone(self.db.pending_rebalance)  # question posée

        # Écart résorbé "par un autre moyen" (avant toute réponse) :
        # 9 vs 3 -> 6 vs 6, gap < 2, plus aucun mouvement nécessaire.
        for i, pid in enumerate(self.t1_players[:3]):
            _move_player(self.db, pid, self.t2_id, seat=4 + i)
        self.assertEqual(self._counts(), (6, 6))

        self.db.rebalance_tables()

        self.assertIsNone(
            self.db.pending_rebalance,
            "pending_rebalance aurait dû être effacé : l'écart qui avait "
            "motivé la question n'existe plus, plus rien à demander.",
        )

    def test_reappel_sans_changement_conserve_le_meme_request_id(self):
        """Toujours la même table source, écart toujours présent : la
        demande reste EXACTEMENT la même (même request_id) — ne doit
        jamais être recréée à chaque tick/recalcul, sous peine d'invalider
        une réponse déjà envoyée par un téléphone entre-temps (voir
        commentaire de rebalance_tables)."""
        self.db.rebalance_tables()
        first = self.db.pending_rebalance
        self.assertIsNotNone(first)

        self.db.rebalance_tables()  # rien n'a changé entre les deux appels
        second = self.db.pending_rebalance

        self.assertIsNotNone(second)
        self.assertEqual(second["request_id"], first["request_id"])
        self.assertEqual(second["table_id"], self.t1_id)

    def test_besoin_bascule_sur_une_autre_table_genere_une_nouvelle_demande(self):
        """Le manque a basculé sur une AUTRE table entre-temps (ex : un
        cassage de table a redistribué les joueurs) : l'ancienne demande
        ne correspond plus à rien de valide et doit être remplacée par une
        nouvelle, avec un NOUVEL identifiant (l'ancien ne doit plus jamais
        pouvoir "gagner" côté téléphone)."""
        self.db.rebalance_tables()
        first = self.db.pending_rebalance
        self.assertEqual(first["table_id"], self.t1_id)

        # Table 1 se vide (5 restants), Table 2 se remplit (7, encore
        # largement sous son max_seats=9) : le besoin bascule maintenant
        # sur Table 2, qui devient la plus pleine des deux (écart de 2).
        for i, pid in enumerate(self.t1_players[:4]):
            _move_player(self.db, pid, self.t2_id, seat=4 + i)
        self.assertEqual(self._counts(), (5, 7))

        self.db.rebalance_tables()
        second = self.db.pending_rebalance

        self.assertIsNotNone(second)
        self.assertEqual(second["table_id"], self.t2_id)
        self.assertNotEqual(second["request_id"], first["request_id"])


if __name__ == "__main__":
    unittest.main()
