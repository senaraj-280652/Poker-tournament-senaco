# -*- coding: utf-8 -*-
"""Tests de l'architecture de correction du DÉPASSEMENT DE CAPACITÉ
(distincte du doublon de siège) validée le 2026-09-10 :

1. capacité configurée = limite dure pour toute table normale, jamais
   négociable ;
2. exception UNIQUE : la vraie table finale (≤ FINAL_TABLE_MAX_SEATS
   joueurs, une seule table nécessaire) ;
3. le guidage par la grosse blinde ne modifie jamais une capacité, ne
   décide que du choix sportif ENTRE des tables déjà valides ;
4. lors du passage du tournoi de 10 à 11 joueurs (la table finale cesse
   d'exister en tant que table unique, une deuxième table s'ouvre), on
   relocalise D'ABORD les joueurs excédentaires (siège le plus élevé,
   déterministe — jamais dépendant d'une réponse BB, par nature
   asynchrone), PUIS seulement abaisse max_seats — jamais l'inverse ;
5. chaque déplacement porte une raison distincte (voir MOVE_REASON_*),
   pour que l'historique explique pourquoi un joueur a été déplacé.

Utilise une vraie Database SQLite en mémoire/fichier temporaire (pas de
doublure) : ce correctif touche une logique métier réelle. export_prefs
est mocké partout (jamais d'accès au vrai fichier de préférences de
l'utilisateur)."""
import os
import sys
import tempfile
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


def _assert_capacity_ok(test_case, db, context=""):
    """Vérifie l'invariant central : aucune table active n'a d'occupation
    réelle supérieure à SA PROPRE tables_pk.max_seats (couvre à la fois
    les tables normales et une éventuelle vraie table finale, dont le
    max_seats a été légitimement relevé — voir check_table_integrity,
    qui applique exactement la même règle)."""
    for t in db.list_tables():
        occ = db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
            (t["id"],),
        ).fetchone()["c"]
        test_case.assertLessEqual(
            occ, t["max_seats"],
            f"{context} : {t['name']} a {occ} joueurs pour max_seats={t['max_seats']}",
        )


def _assert_no_active_player_on_inactive_table(test_case, db, context=""):
    inactive_ids = {t["id"] for t in db.list_tables(active_only=False) if t["is_active"] == 0}
    for p in db.list_players(status="active"):
        test_case.assertNotIn(
            p["table_id"], inactive_ids, f"{context} : {p['name']} sur une table inactive"
        )


class Phase1JamaisDeSurcapaciteTest(unittest.TestCase):
    """1 à 10 joueurs -> comportement "table finale" préservé (Sit & Go) ;
    11e joueur -> transition atomique ; poursuite jusqu'à 36 joueurs :
    invariant de capacité vérifié après CHAQUE inscription."""

    def setUp(self):
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()
        self._tmp = tempfile.TemporaryDirectory(prefix="capacity_arch_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 8, "min_players_per_table": 4})

    def test_8_joueurs_une_seule_table(self):
        for i in range(1, 9):
            self.db.add_player(f"J{i}")
        tables = self.db.list_tables()
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["max_seats"], 8)
        _assert_capacity_ok(self, self.db, "8 joueurs")

    def test_9_joueurs_table_finale_a_9(self):
        for i in range(1, 10):
            self.db.add_player(f"J{i}")
        tables = self.db.list_tables()
        self.assertEqual(len(tables), 1, "un Sit & Go de 9 doit rester sur UNE seule table")
        self.assertEqual(tables[0]["max_seats"], 9)
        _assert_capacity_ok(self, self.db, "9 joueurs")

    def test_10_joueurs_table_finale_a_10(self):
        for i in range(1, 11):
            self.db.add_player(f"J{i}")
        tables = self.db.list_tables()
        self.assertEqual(len(tables), 1, "un Sit & Go de 10 doit rester sur UNE seule table")
        self.assertEqual(tables[0]["max_seats"], 10)
        _assert_capacity_ok(self, self.db, "10 joueurs")

    def test_11e_joueur_deux_tables_aucune_surcapacite(self):
        for i in range(1, 11):
            self.db.add_player(f"J{i}")
        _assert_capacity_ok(self, self.db, "avant le 11e")

        self.db.add_player("J11")

        tables = self.db.list_tables()
        self.assertEqual(len(tables), 2, "l'exception de table finale doit cesser au 11e joueur")
        for t in tables:
            self.assertEqual(t["max_seats"], 8, f"{t['name']} doit revenir à la capacité configurée")
        _assert_capacity_ok(self, self.db, "après le 11e joueur")
        self.assertEqual(
            sum(
                self.db.conn.execute(
                    "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (t["id"],)
                ).fetchone()["c"]
                for t in tables
            ),
            11,
        )

    def test_36_inscriptions_toujours_valide(self):
        for i in range(1, 37):
            self.db.add_player(f"J{i}")
            _assert_capacity_ok(self, self.db, f"après inscription #{i}")
            _assert_no_active_player_on_inactive_table(self, self.db, f"après inscription #{i}")
        self.assertEqual(len(self.db.list_players(status="active")), 36)


class PassageDuTournoiDe10A11JoueursApresDemarrageTest(unittest.TestCase):
    """clock_started=1 : le passage du tournoi de 10 à 11 joueurs (la
    table finale cesse d'exister en tant que table unique, une deuxième
    table s'ouvre, les joueurs se répartissent sur au moins 2 tables)
    reste toujours atomique, que le guidage BB soit activé ou non — le
    guidage ne peut jamais autoriser une surcapacité, y compris
    temporaire, sur une table normale."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="capacity_arch_transition_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 8, "min_players_per_table": 4, "clock_started": 1,
        })
        t1_id = self.db.list_tables()[0]["id"]
        self.db.conn.execute("UPDATE tables_pk SET max_seats=10 WHERE id=?", (t1_id,))
        self.db.conn.commit()
        self.ids = [
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}") for seat in range(1, 11)
        ]
        self.t1_id = t1_id

    def test_bb_off_relocalisation_immediate_sans_surcapacite(self):
        with patch.object(database.export_prefs, "load_value", return_value=False):
            self.db.add_player("J11")
        _assert_capacity_ok(self, self.db, "BB désactivé, 11e joueur")
        tables = self.db.list_tables()
        self.assertEqual(len(tables), 2)
        for t in tables:
            self.assertEqual(t["max_seats"], 8)

    def test_bb_on_relocalisation_immediate_meme_sans_reponse(self):
        with patch.object(database.export_prefs, "load_value", return_value=True):
            self.db.add_player("J11")
            # Aucune réponse BB donnée : la capacité doit malgré tout
            # être respectée immédiatement (mouvement structurel, jamais
            # dépendant d'une réponse humaine).
            _assert_capacity_ok(self, self.db, "BB activé, 11e joueur, aucune réponse")
            tables = self.db.list_tables()
            self.assertEqual(len(tables), 2)
            for t in tables:
                self.assertEqual(t["max_seats"], 8)

    def test_bb_on_mouvement_structurel_etiquete_correctement(self):
        # add_player() appelle rebalance_tables(record_moves=False) — pas
        # d'archivage pour une simple inscription (comportement historique
        # inchangé) : on appelle donc rebalance_tables(record_moves=True)
        # directement, comme le ferait une élimination, pour vérifier
        # l'étiquetage réellement archivé dans l'historique.
        with patch.object(database.export_prefs, "load_value", return_value=True):
            _seat_new_player(self.db, self.t1_id, 11, "J11")
            moves = self.db.rebalance_tables(record_moves=True)

        self.assertTrue(moves)
        structural_moves = [m for m in moves if m["reason"] == database.MOVE_REASON_STRUCTURAL]
        self.assertTrue(
            structural_moves,
            "le mouvement de mise en conformité (11e joueur) doit être "
            "étiqueté MOVE_REASON_STRUCTURAL",
        )
        archived = self.db.get_seat_moves()
        self.assertTrue(
            any(m["reason"] == database.MOVE_REASON_STRUCTURAL for m in archived)
        )
        _assert_capacity_ok(self, self.db, "après étiquetage")


class VraieTableFinaleNonRegressionTest(unittest.TestCase):
    """La vraie table finale (éliminations en cours de jeu, pas une
    inscription) continue de fonctionner normalement après ce correctif."""

    def setUp(self):
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()
        self._tmp = tempfile.TemporaryDirectory(prefix="capacity_arch_finaltable_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 8, "min_players_per_table": 1, "clock_started": 1,
        })

    def test_eliminations_ramenent_a_une_table_finale_a_10(self):
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        for seat in range(1, 9):
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")
        for seat in range(1, 4):
            _seat_new_player(self.db, t2_id, seat, f"T2-{seat}")
        # 11 actifs -> élimine 1 pour tomber à 10 (table finale).
        pid_to_eliminate = self.db.conn.execute(
            "SELECT id FROM players WHERE table_id=? LIMIT 1", (t2_id,)
        ).fetchone()["id"]
        self.db.eliminate_player(pid_to_eliminate)

        tables = self.db.list_tables()
        self.assertEqual(len(tables), 1, "10 joueurs restants doivent fusionner sur une table finale")
        self.assertEqual(tables[0]["max_seats"], 10)
        _assert_capacity_ok(self, self.db, "table finale après éliminations")
        self.assertEqual(len(self.db.list_players(status="active")), 10)


class HistoriqueEtAlerteUniqueTest(unittest.TestCase):
    """Pas de double enregistrement dans l'historique, pas de double
    alerte, pour un même événement — même si un mouvement structurel est
    suivi immédiatement d'un mouvement d'équilibrage sportif."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="capacity_arch_history_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 8, "min_players_per_table": 1, "clock_started": 1,
        })

    def test_pas_de_double_entree_pour_un_meme_joueur_dans_un_seul_appel(self):
        """Un joueur déplacé plusieurs fois au sein d'UN SEUL appel à
        rebalance_tables() (ex. mise en conformité puis équilibrage) ne
        doit apparaître qu'UNE SEULE FOIS dans la liste des mouvements
        renvoyée (diff avant/après de l'appel entier, pas un journal
        d'étapes intermédiaires)."""
        with patch.object(database.export_prefs, "load_value", return_value=False):
            t1_id = self.db.list_tables()[0]["id"]
            self.db.conn.execute("UPDATE tables_pk SET max_seats=10 WHERE id=?", (t1_id,))
            self.db.conn.commit()
            for seat in range(1, 11):
                _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")
            moves = self.db.rebalance_tables(record_moves=True)

        player_names_moved = [m["player_name"] for m in moves]
        self.assertEqual(
            len(player_names_moved), len(set(player_names_moved)),
            "un même joueur ne doit apparaître qu'une seule fois dans les mouvements d'un appel",
        )
        _assert_capacity_ok(self, self.db, "après rééquilibrage complet")

    def test_chaque_mouvement_archive_porte_une_raison(self):
        with patch.object(database.export_prefs, "load_value", return_value=False):
            t1_id = self.db.list_tables()[0]["id"]
            self.db.conn.execute("UPDATE tables_pk SET max_seats=10 WHERE id=?", (t1_id,))
            self.db.conn.commit()
            # 11 joueurs (pas 10) : force réellement la transition hors de
            # l'exception de table finale, donc un vrai mouvement à
            # archiver (10 seuls sur une table déjà à max_seats=10 ne
            # produirait aucun mouvement, l'état étant déjà valide).
            for seat in range(1, 12):
                _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")
            self.db.rebalance_tables(record_moves=True)

        moves = self.db.get_seat_moves()
        self.assertTrue(moves)
        for m in moves:
            self.assertIn(m["reason"], database.MOVE_REASON_LABELS)
            self.assertNotEqual(m["reason"], "", "chaque nouveau mouvement doit porter une vraie raison")


class CheckTableIntegrityTest(unittest.TestCase):
    """Database.check_table_integrity() — détection SEULE, lecture seule,
    aucune correction automatique."""

    def setUp(self):
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()
        self._tmp = tempfile.TemporaryDirectory(prefix="capacity_arch_integrity_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 8, "min_players_per_table": 4})

    def test_fichier_coherent_aucun_probleme(self):
        for i in range(1, 9):
            self.db.add_player(f"J{i}")
        self.assertEqual(self.db.check_table_integrity(), [])

    def test_fichier_incoherent_detecte_sans_rien_corriger(self):
        # Simule un ANCIEN fichier déjà incohérent : écrit directement en
        # base un état que le correctif est censé désormais empêcher de
        # produire, pour vérifier que la détection le repère SANS le
        # réparer (aucune correction automatique, demande explicite).
        t1_id = self.db.list_tables()[0]["id"]
        # max_seats explicitement à 8 (la table est créée à la valeur du
        # réglage AU MOMENT de sa création, ici "9" par défaut avant le
        # set_settings du setUp — fixé ici à la main pour reproduire
        # précisément le scénario diagnostiqué : max_seats=8, 10 joueurs).
        self.db.conn.execute("UPDATE tables_pk SET max_seats=8 WHERE id=?", (t1_id,))
        self.db.conn.commit()
        for seat in range(1, 11):
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")

        problems = self.db.check_table_integrity()
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]["table_name"], "Table 1")
        self.assertEqual(problems[0]["occupation"], 10)
        self.assertEqual(problems[0]["max_seats"], 8)

        # AUCUNE correction : l'état doit être resté rigoureusement
        # identique après l'appel à check_table_integrity().
        occ_after = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (t1_id,)
        ).fetchone()["c"]
        self.assertEqual(occ_after, 10)
        max_seats_after = self.db.conn.execute(
            "SELECT max_seats FROM tables_pk WHERE id=?", (t1_id,)
        ).fetchone()["max_seats"]
        self.assertEqual(max_seats_after, 8)

    def test_vraie_table_finale_nest_jamais_un_probleme(self):
        t1_id = self.db.list_tables()[0]["id"]
        self.db.conn.execute("UPDATE tables_pk SET max_seats=10 WHERE id=?", (t1_id,))
        self.db.conn.commit()
        for seat in range(1, 11):
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")
        # 10 joueurs, max_seats=10 (légitimement relevé) : pas un problème.
        self.assertEqual(self.db.check_table_integrity(), [])


if __name__ == "__main__":
    unittest.main()
