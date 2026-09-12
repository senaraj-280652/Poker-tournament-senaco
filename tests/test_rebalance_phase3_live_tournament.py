# -*- coding: utf-8 -*-
"""Tests ciblés de la PHASE 3 (tournoi en cours, clock_started == 1) de
l'architecture de rééquilibrage validée le 2026-09-10 :

- guidage BB activé + réponse d'un téléphone -> mouvement guidé normal
  (comportement existant, non-régression) ;
- guidage BB activé + AUCUNE réponse (aucun téléphone connecté, ou un
  téléphone connecté qui ne répond pas) -> la demande reste ouverte
  INDÉFINIMENT, sans qu'aucun mouvement ne soit jamais décidé
  silencieusement (le responsable reste seul maître de la décision —
  voir database.py: rebalance_tables, section "simple équilibrage") ;
- guidage BB désactivé -> comportement automatique historique conservé,
  y compris EN COURS DE TOURNOI (clock_started == 1) : la désactivation
  de la préférence n'a jamais eu de rapport avec clock_started.

Utilise une vraie Database SQLite en mémoire (pas de doublure). export_
prefs est mocké partout (jamais d'accès au vrai fichier de préférences
de l'utilisateur)."""
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


class _TwoTablesLiveTournamentTestCase(unittest.TestCase):
    """2 tables, écart de 6 joueurs (9 vs 3), tournoi déjà démarré
    (clock_started=1) — même préparation que test_rebalance_pending_
    revalidation.py, avec clock_started explicitement à 1 (PHASE 3)."""

    def setUp(self):
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1_id = self.db.list_tables()[0]["id"]
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


class GuidageAvecReponseTest(_TwoTablesLiveTournamentTestCase):
    """4. Après démarrage, guidage BB actif + téléphone répond : le
    mouvement guidé s'applique normalement (comportement existant,
    non-régression de cette architecture)."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_reponse_valide_deplace_bien_un_joueur(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending)
        self.assertEqual(pending["table_id"], self.t1_id)

        answered_seat = pending["seats"][0]
        moves = self.db.resolve_pending_rebalance(pending["request_id"], answered_seat)

        self.assertTrue(moves)
        c1, c2 = self._counts()
        self.assertEqual(c1 + c2, 12)
        # Un seul mouvement décidé par appel (voir rebalance_tables) :
        # l'écart initial de 6 (9 vs 3) doit avoir strictement diminué,
        # sans être nécessairement retombé à 0 en un seul coup.
        self.assertLess(abs(c1 - c2), 6)


class AucuneResolutionSilencieuseTest(_TwoTablesLiveTournamentTestCase):
    """5. Après démarrage, guidage BB actif, AUCUNE réponse (aucun
    téléphone connecté, ou téléphone connecté mais qui ne répond pas —
    les deux cas sont indiscernables ici : rebalance_tables() ne sait
    rien d'un éventuel téléphone connecté, voir son analyse) : la
    demande doit rester ouverte indéfiniment, avec le MÊME request_id, et
    AUCUN joueur ne doit jamais être déplacé automatiquement — même après
    de nombreux recalculs successifs (simulant plusieurs ticks/
    événements sans réponse)."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_pending_reste_ouvert_sans_deplacement_apres_plusieurs_recalculs(self):
        self.db.rebalance_tables()
        first_request_id = self.db.pending_rebalance["request_id"]
        counts_before = self._counts()

        # Simule de nombreux événements/ticks successifs SANS qu'aucune
        # réponse n'arrive jamais (ni seat précis, ni "Continuer sans
        # indiquer la BB") : rebalance_tables() est rappelée à chaque
        # fois, exactement comme le ferait _tick ou une autre action sans
        # rapport (voir sa docstring).
        for _ in range(10):
            self.db.rebalance_tables()
            self.assertIsNotNone(
                self.db.pending_rebalance,
                "aucune résolution automatique ne doit jamais survenir sans réponse explicite",
            )
            self.assertEqual(
                self.db.pending_rebalance["request_id"], first_request_id,
                "la demande doit rester la MÊME tant qu'aucune réponse n'arrive",
            )
            self.assertEqual(
                self._counts(), counts_before,
                "aucun joueur ne doit avoir été déplacé sans réponse explicite",
            )


class GuidageDesactiveTest(_TwoTablesLiveTournamentTestCase):
    """7. Guidage BB désactivé (même en cours de tournoi, clock_started
    == 1) : comportement automatique historique conservé — jamais de
    pending_rebalance, l'écart est résorbé immédiatement par le
    mécanisme _legacy_pick_mover, exactement comme avant l'introduction
    du guidage. Confirme que la désactivation de la préférence n'a
    jamais eu de rapport avec clock_started : les deux conditions
    (préférence désactivée, ou clock_started == 0) mènent à la même
    branche automatique dans rebalance_tables()."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(database.export_prefs, "load_value", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_ecart_resorbe_immediatement_sans_pending_rebalance(self):
        self.db.rebalance_tables()
        self.assertIsNone(self.db.pending_rebalance)
        c1, c2 = self._counts()
        self.assertLessEqual(abs(c1 - c2), 1)
        self.assertEqual(c1 + c2, 12)


if __name__ == "__main__":
    unittest.main()
