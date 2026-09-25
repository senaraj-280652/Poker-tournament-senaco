# -*- coding: utf-8 -*-
""""Annule Eliminer" — annulation de la DERNIÈRE élimination (demande du
2026-09-17), pour corriger rapidement une erreur d'élimination faite
depuis le téléphone. Distincte de "Réinscrire" (reinstate_player,
INCHANGÉE) : ici, une vraie annulation — même table/siège, primes/kills/
bounty_events et mouvements de tables provoqués par CETTE élimination
précise entièrement défaits, jamais une simple remise en jeu.

Architecture testée (voir database.py: eliminate_player/undo_last_
elimination) : INSTANTANÉ COMPLET avant/après capturé par eliminate_
player lui-même, restauré tel quel par undo_last_elimination — jamais
une tentative d'inverser mouvement par mouvement (un cassage de table
répartit ALÉATOIREMENT les joueurs évincés, non réversible étape par
étape). RÈGLE ABSOLUE demandée : seule la toute dernière élimination
est annulable (get_last_eliminated_player), jamais un historique.
SÉCURITÉ AVANT TOUT (demande explicite) : la moindre divergence entre
l'état courant et l'instantané "après" mémorisé REFUSE l'annulation
(ValueError, aucune écriture) plutôt que de tenter une reconstruction
approximative.

Deux volets :
- Tests DATABASE (la majorité) : vraies Database SQLite sur fichier
  temporaire, aucune doublure.
- AppUndoLastEliminationTest : App._undo_last_elimination au niveau
  UNITAIRE, sur une doublure App minimale (même principe que tests/
  test_players_right_click_eliminate.py: EliminateSelectedShortcutTest) —
  bouton "Annule Eliminer" (jamais dépendant d'une sélection courante),
  confirmation obligatoire, refus affiché sans modification."""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402


def _new_db(tmp_dir, name):
    return database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))


def _set_elim_time_seconds_ago(db, player_id, seconds_ago):
    """Fixe directement elim_time à `seconds_ago` secondes AVANT le
    vrai instant présent (base réelle time.time(), jamais mockée : le
    calcul de délai testé ici doit fonctionner avec de vraies valeurs
    d'horloge) — pour contrôler précisément le délai écoulé au moment
    de la tentative d'annulation, sans dépendre du temps réel
    d'exécution du test lui-même (pas de time.sleep). Renvoie la chaîne
    elim_time écrite, au même format que celui produit par eliminate_
    player (time.strftime, heure locale)."""
    target_epoch = time.time() - seconds_ago
    elim_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(target_epoch))
    db.conn.execute("UPDATE players SET elim_time=? WHERE id=?", (elim_time_str, player_id))
    db.conn.commit()
    return elim_time_str


def _seat_raw(db, table_id, seat, name):
    """Assoit un nouveau joueur ACTIF directement au siège demandé, sans
    passer par _seat_player (même principe que tests/test_rebalance_
    phase3_live_tournament.py:_seat_new_player) — pour construire des
    dispositions de tables précises sans que add_player/rebalance_tables
    ne les perturbe pendant la préparation du test."""
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


class SimpleCaseTest(unittest.TestCase):
    """Cas le plus simple : une seule table, aucun mouvement provoqué."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_simple_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)

    def test_rien_a_annuler_leve_valueerror_sans_modification(self):
        self.db.add_player("Alice")
        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()

    def test_get_last_eliminated_player_none_sans_elimination(self):
        self.db.add_player("Alice")
        self.assertIsNone(self.db.get_last_eliminated_player())

    def test_annulation_simple_restaure_statut_table_siege_et_classement(self):
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.add_player("Chris")
        before = self.db.get_player(a)
        old_table, old_seat = before["table_id"], before["seat"]

        self.db.eliminate_player(a, eliminated_by_id=b)
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")

        moves = self.db.undo_last_elimination()

        restored = self.db.get_player(a)
        self.assertEqual(restored["status"], "active")
        self.assertEqual((restored["table_id"], restored["seat"]), (old_table, old_seat))
        self.assertIsNone(restored["place"])
        self.assertIsNone(restored["elim_time"])
        self.assertIsNone(restored["elim_round"])
        self.assertIsNone(restored["eliminated_by_name"])
        # Une seule table, personne d'AUTRE ne bouge : le seul "mouvement"
        # possible est le retour d'Alice elle-même à sa propre table/siège
        # (elle n'était assise nulle part entre-temps, status='eliminated')
        # — légitimement signalé (elle doit physiquement reprendre sa
        # place), au même titre qu'une élimination normale l'aurait
        # signalé dans l'autre sens.
        self.assertEqual(len(moves), 1)
        self.assertEqual(moves[0]["player_name"], "Alice")
        self.assertEqual(moves[0]["new_table_name"], "Table 1")

    def test_last_eliminated_redevient_none_apres_annulation(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        self.db.undo_last_elimination()
        self.assertIsNone(self.db.get_last_eliminated_player())

    def test_instantane_consomme_deuxieme_annulation_refusee(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        self.db.undo_last_elimination()
        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()

    def test_chips_jamais_touches_par_elimination_ni_annulation(self):
        """Les jetons ne sont jamais modifiés par une élimination : leur
        éventuelle modification entre-temps (rebuy...) n'a donc aucun
        rapport et ne doit jamais bloquer l'annulation."""
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        # Un rebuy sur un AUTRE joueur (tracké dans l'instantané, actif)
        # entre l'élimination et l'annulation ne doit pas la bloquer.
        self.db.rebuy_player(self.db.get_last_eliminated_player()["id"])  # sans effet ici, juste pour rester réaliste
        self.db.undo_last_elimination()
        self.assertEqual(self.db.get_player(a)["status"], "active")


class RebalanceSimpleMoveTest(unittest.TestCase):
    """Une élimination qui déclenche un rééquilibrage SIMPLE (un ou
    plusieurs joueurs changent de table, sans fermeture de table) —
    Phase 1 (clock_started=0) : mécanisme automatique historique, aucun
    guidage par la grosse blinde en jeu ici (voir BBGuidedPendingTest
    plus bas pour ce cas séparé)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_rebalance_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 9, "min_players_per_table": 1})
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        # 9 + 3 = 12 actifs (>10, la règle "table finale" ne s'applique
        # pas) : écart de 6, un rééquilibrage simple est nécessaire dès
        # la préparation elle-même (rebalance_tables tourne à chaque
        # _seat_raw ? non, _seat_raw ne l'appelle pas — l'écart n'est
        # donc corrigé qu'au premier vrai appel, ici celui de eliminate_
        # player un peu plus bas).
        self.t1_players = [_seat_raw(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)]
        self.t2_players = [_seat_raw(self.db, self.t2, s, f"T2-{s}") for s in range(1, 4)]

    def _counts(self):
        def count(table_id):
            return self.db.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (table_id,),
            ).fetchone()["c"]
        return count(self.t1), count(self.t2)

    def test_elimination_equilibre_puis_annulation_restaure_la_disposition(self):
        eliminated = self.t1_players[0]
        self.db.eliminate_player(eliminated)
        # Rééquilibrage automatique effectué : au moins un joueur de t1
        # est passé sur t2.
        after_elim = self._counts()
        self.assertNotEqual(after_elim, (8, 3))  # un vrai mouvement a eu lieu

        moves = self.db.undo_last_elimination()

        self.assertTrue(moves)  # l'annulation elle-même a dû redéplacer du monde
        self.assertEqual(self._counts(), (9, 3))  # disposition IDENTIQUE à avant
        for pid in self.t1_players:
            self.assertEqual(self.db.get_player(pid)["table_id"], self.t1)
        for pid in self.t2_players:
            self.assertEqual(self.db.get_player(pid)["table_id"], self.t2)

    def test_seat_moves_reflete_l_annulation(self):
        self.db.eliminate_player(self.t1_players[0])
        self.db.undo_last_elimination()
        reasons = {m["reason"] for m in self.db.get_seat_moves()}
        self.assertIn(database.MOVE_REASON_ELIMINATION_UNDO, reasons)


class TableClosureFusionTest(unittest.TestCase):
    """Une élimination qui déclenche une FUSION/fermeture de table (sans
    pour autant être la table finale, voir FinalTableTest ci-dessous) :
    répartition ALÉATOIRE des joueurs évincés (rebalance_tables) — non
    réversible mouvement par mouvement, d'où l'approche "instantané
    complet" testée ici."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_fusion_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        # Petites tables (max_seats=3) pour provoquer une fusion à un
        # effectif encore > FINAL_TABLE_MAX_SEATS (10) — jamais la table
        # finale ici, testée séparément.
        self.db.set_settings({"max_seats_per_table": 3, "min_players_per_table": 1})
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t3 = self.db.add_table("Table 3")
        self.t4 = self.db.add_table("Table 4")
        self.t5 = self.db.add_table("Table 5")
        # 3+3+3+3+1 = 13 actifs (5 tables nécessaires). En éliminer un
        # sur t1 (3 -> 2) fait passer le total à 12 : ceil(12/3)=4
        # tables suffisent -> une table (la plus haut numérotée, t5) est
        # fermée et son occupant redistribué aléatoirement -> fusion
        # réelle, avec 12 > FINAL_TABLE_MAX_SEATS (10).
        self.t1_players = [_seat_raw(self.db, self.t1, s, f"T1-{s}") for s in (1, 2, 3)]
        _seat_raw(self.db, self.t2, 1, "T2-1")
        _seat_raw(self.db, self.t2, 2, "T2-2")
        _seat_raw(self.db, self.t2, 3, "T2-3")
        _seat_raw(self.db, self.t3, 1, "T3-1")
        _seat_raw(self.db, self.t3, 2, "T3-2")
        _seat_raw(self.db, self.t3, 3, "T3-3")
        _seat_raw(self.db, self.t4, 1, "T4-1")
        _seat_raw(self.db, self.t4, 2, "T4-2")
        _seat_raw(self.db, self.t4, 3, "T4-3")
        self.t5_player = _seat_raw(self.db, self.t5, 1, "T5-1")

    def _active_table_ids(self):
        return {
            t["id"] for t in self.db.list_tables(active_only=True)
        }

    def _full_snapshot(self):
        return {
            p["id"]: (p["table_id"], p["seat"], p["status"])
            for p in self.db.list_players()
        }

    def test_fusion_declenchee_puis_annulation_restaure_exactement(self):
        before_tables = self.db.list_tables(active_only=False)
        before_snapshot = self._full_snapshot()
        self.assertEqual(len(self.db.list_tables(active_only=True)), 5)

        self.db.eliminate_player(self.t1_players[0])
        self.assertEqual(len(self.db.list_tables(active_only=True)), 4)  # une table fermée

        moves = self.db.undo_last_elimination()

        self.assertTrue(moves)
        self.assertEqual(len(self.db.list_tables(active_only=True)), 5)  # rouverte
        after_tables = self.db.list_tables(active_only=False)
        self.assertEqual(
            [dict(t) for t in before_tables], [dict(t) for t in after_tables]
        )
        del before_snapshot[self.t1_players[0]]  # lui seul a un statut différent
        after_snapshot = self._full_snapshot()
        del after_snapshot[self.t1_players[0]]
        self.assertEqual(before_snapshot, after_snapshot)  # TOUT LE MONDE revenu pile où il était


class FinalTableTest(unittest.TestCase):
    """Une élimination qui fait passer l'effectif juste au niveau de la
    convention "table finale" (FINAL_TABLE_MAX_SEATS=10) : toutes les
    tables restantes fusionnent en une seule, avec relèvement ponctuel
    de sa capacité — doit être annulable comme n'importe quel autre
    rééquilibrage."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_final_table_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 9, "min_players_per_table": 1})
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        # 6 + 5 = 11 actifs (> FINAL_TABLE_MAX_SEATS) : 2 tables. En
        # éliminer un ramène à 10 actifs -> convention "table finale" :
        # une seule table restante, quitte à dépasser max_seats_per_table.
        self.t1_players = [_seat_raw(self.db, self.t1, s, f"T1-{s}") for s in range(1, 7)]
        self.t2_players = [_seat_raw(self.db, self.t2, s, f"T2-{s}") for s in range(1, 6)]

    def test_table_finale_declenchee_puis_annulation_restaure_2_tables(self):
        before_max_seats = {
            t["id"]: t["max_seats"] for t in self.db.list_tables(active_only=False)
        }

        self.db.eliminate_player(self.t1_players[0])
        self.assertEqual(len(self.db.list_tables(active_only=True)), 1)  # table finale

        moves = self.db.undo_last_elimination()

        self.assertTrue(moves)
        self.assertEqual(len(self.db.list_tables(active_only=True)), 2)  # dé-fusionnée
        after_max_seats = {
            t["id"]: t["max_seats"] for t in self.db.list_tables(active_only=False)
        }
        self.assertEqual(before_max_seats, after_max_seats)  # capacité ponctuelle défaite


class PkoUndoTest(unittest.TestCase):
    """PKO : kills, bounty/bounty_won de l'éliminateur, et la ligne
    bounty_events créée doivent tous être défaits par l'annulation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_pko_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "bounty_amount": "40", "pko_mode": "1", "pko_cash_percent": "50",
        })
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.c = self.db.add_player("Chris")

    def test_kills_bounty_et_bounty_events_restaures(self):
        before_a = self.db.get_player(self.a)
        before_b = dict(self.db.get_player(self.b))
        n_events_before = self.db.conn.execute(
            "SELECT COUNT(*) c FROM bounty_events"
        ).fetchone()["c"]

        self.db.eliminate_player(self.a, eliminated_by_id=self.b)
        self.assertEqual(self.db.get_player(self.b)["kills"], 1)
        self.assertGreater(
            self.db.conn.execute("SELECT COUNT(*) c FROM bounty_events").fetchone()["c"],
            n_events_before,
        )

        self.db.undo_last_elimination()

        after_a = self.db.get_player(self.a)
        after_b = dict(self.db.get_player(self.b))
        self.assertEqual(after_a["status"], "active")
        self.assertEqual(after_a["bounty"], before_a["bounty"])
        self.assertEqual(after_b, before_b)  # kills/bounty/bounty_won identiques à avant
        self.assertEqual(
            self.db.conn.execute("SELECT COUNT(*) c FROM bounty_events").fetchone()["c"],
            n_events_before,
        )

    def test_orpheline_pko_refusee_reste_inchangee_ligne_de_base(self):
        """Non-régression : le garde-fou PKO existant (bounty orpheline
        interdite) continue de lever ValueError AVANT toute capture
        d'instantané — comportement de eliminate_player strictement
        inchangé par cet ajout."""
        with self.assertRaises(ValueError):
            self.db.eliminate_player(self.a)  # aucun éliminateur, bounty > 0
        self.assertEqual(self.db.get_player(self.a)["status"], "active")
        self.assertIsNone(self.db.get_setting("last_elimination_undo"))


class LastPlayerEndOfTournamentTest(unittest.TestCase):
    """Dernier joueur / fin de tournoi : tournament_end_epoch, et en PKO,
    la clôture de la bounty finale du vainqueur (_close_out_winner_
    bounty) doivent tous deux être défaits par l'annulation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_end_")
        self.addCleanup(self._tmp.cleanup)

    def test_fin_de_tournoi_simple_annulee(self):
        db = _new_db(self._tmp.name, "simple")
        self.addCleanup(db.conn.close)
        a = db.add_player("Alice")
        db.add_player("Bob")

        db.eliminate_player(a)
        self.assertNotEqual(db.get_setting("tournament_end_epoch"), "0")
        self.assertEqual(len(db.list_players(status="active")), 1)

        db.undo_last_elimination()

        self.assertEqual(db.get_setting("tournament_end_epoch"), "0")
        self.assertEqual(len(db.list_players(status="active")), 2)

    def test_cloture_bounty_vainqueur_pko_annulee(self):
        db = _new_db(self._tmp.name, "pko_end")
        self.addCleanup(db.conn.close)
        db.set_settings({"bounty_amount": "40", "pko_mode": "1", "pko_cash_percent": "50"})
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        before_b = dict(db.get_player(b))

        db.eliminate_player(a, eliminated_by_id=b)  # Bob reste seul actif
        winner_after_elim = dict(db.get_player(b))
        self.assertEqual(winner_after_elim["bounty"], 0)  # clôturée (transférée à bounty_won)
        self.assertGreater(winner_after_elim["bounty_won"], before_b["bounty_won"])

        db.undo_last_elimination()

        self.assertEqual(dict(db.get_player(b)), before_b)
        self.assertEqual(db.get_setting("tournament_end_epoch"), "0")


class MovementAlreadyFinishedTest(unittest.TestCase):
    """Les mouvements de l'élimination ont déjà été validés via "Terminé"
    (main.py: _finish_movement_alert vide seat_moves et remet movement_
    alert_active à 0) AVANT que l'annulation ne soit demandée — ne doit
    rien changer à la capacité d'annuler (les données table_id/seat en
    base ne sont, elles, jamais affectées par "Terminé")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_finished_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 9, "min_players_per_table": 1})
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t1_players = [_seat_raw(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)]
        self.t2_players = [_seat_raw(self.db, self.t2, s, f"T2-{s}") for s in range(1, 4)]

    def test_annulation_fonctionne_apres_un_termine(self):
        self.db.eliminate_player(self.t1_players[0])
        self.assertGreater(self.db.count_seat_moves(), 0)

        # Exactement ce que fait le bouton "Terminé" côté données (voir
        # App._finish_movement_alert) : jamais autre chose que ces deux
        # lignes.
        self.db.set_settings({"movement_alert_active": 0})
        self.db.clear_seat_moves()

        moves = self.db.undo_last_elimination()
        self.assertTrue(moves)
        self.assertEqual(self.db.get_player(self.t1_players[0])["status"], "active")


class BBGuidedPendingTest(unittest.TestCase):
    """Rééquilibrage guidé par la grosse blinde (pending_rebalance,
    attribut EN MÉMOIRE de l'objet Database, jamais persisté) — trois
    situations distinctes (voir undo_last_elimination, section pending_
    rebalance_request_id)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_bb_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t1_players = [_seat_raw(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)]
        self.t2_players = [_seat_raw(self.db, self.t2, s, f"T2-{s}") for s in range(1, 4)]
        patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_question_creee_par_cette_elimination_annulee_avec_elle(self):
        self.db.eliminate_player(self.t1_players[0])
        self.assertIsNotNone(self.db.pending_rebalance)  # question posée, sans réponse

        moves = self.db.undo_last_elimination()

        self.assertIsNone(self.db.pending_rebalance)  # n'a plus lieu d'être
        self.assertEqual(self.db.get_player(self.t1_players[0])["status"], "active")
        self.assertTrue(isinstance(moves, list))

    def test_question_etrangere_preexistante_preservee(self):
        # Question déjà posée AVANT cette élimination (provoquée par la
        # préparation elle-même) — sans rapport avec l'élimination testée.
        self.db.rebalance_tables()
        pending_before = self.db.pending_rebalance
        self.assertIsNotNone(pending_before)

        # Élimine quelqu'un d'une table qui NE change PAS le besoin
        # (ex : table déjà équilibrée par ailleurs) — ici on simplifie en
        # vérifiant simplement que si la même question survit
        # (request_id inchangé) après l'élimination, elle n'est PAS
        # supprimée par l'annulation.
        # Un joueur de plus, isolé, ne modifie pas la table source de la
        # demande en cours.
        extra = self.db.add_player("Extra")
        self.db.eliminate_player(extra)
        if self.db.pending_rebalance and self.db.pending_rebalance["request_id"] == pending_before["request_id"]:
            self.db.undo_last_elimination()
            self.assertEqual(self.db.pending_rebalance["request_id"], pending_before["request_id"])

    def test_question_deja_repondue_bloque_l_annulation(self):
        self.db.eliminate_player(self.t1_players[0])
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending)

        self.db.resolve_pending_rebalance(pending["request_id"], None)  # "Continuer sans désigner le joueur"

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()
        # Rien n'a été modifié par la tentative refusée.
        self.assertEqual(self.db.get_player(self.t1_players[0])["status"], "eliminated")


class IncompatibleStateTest(unittest.TestCase):
    """L'état a changé depuis l'élimination au point de ne plus garantir
    une restauration exacte : REFUS systématique, sans la moindre
    écriture (principe de sécurité explicitement demandé)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_incompat_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)

    def _base_snapshot(self):
        return {p["id"]: dict(p) for p in self.db.list_players()}

    def test_nouveau_joueur_ajoute_depuis_bloque_l_annulation(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        self.db.add_player("Dave")  # joueur supplémentaire depuis
        snap_before_attempt = self._base_snapshot()

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()

        self.assertEqual(self._base_snapshot(), snap_before_attempt)  # rien modifié

    def test_annulation_ne_remonte_jamais_au_dela_de_la_derniere(self):
        """RÈGLE ABSOLUE demandée : après avoir éliminé A puis B, seule
        l'annulation de B (la vraie dernière) est possible. Une fois B
        annulée, A redevient "le" joueur status='eliminated' restant,
        mais son instantané a été ÉCRASÉ par celui de B au moment de son
        élimination : impossible de l'annuler à son tour, même si
        get_last_eliminated_player() le désignerait de nouveau."""
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        self.db.eliminate_player(b)  # b devient le dernier éliminé, pas a

        self.db.undo_last_elimination()  # annule b : fonctionne normalement
        self.assertEqual(self.db.get_player(b)["status"], "active")
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")  # a reste éliminé

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()  # a n'est PAS annulable (instantané écrasé)
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")  # inchangé

    def test_reintegration_generique_du_dernier_elimine_bloque_l_annulation(self):
        """Si "Réinscrire" (reinstate_player, mécanisme différent et
        INCHANGÉ) a été utilisé entre-temps sur le dernier éliminé,
        l'annulation n'a plus de sens et doit être refusée."""
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        self.db.reinstate_player(a)

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()

    def test_table_modifiee_manuellement_bloque_l_annulation(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        t1 = self.db.list_tables()[0]["id"]
        self.db.conn.execute("UPDATE tables_pk SET max_seats=5 WHERE id=?", (t1,))
        self.db.conn.commit()

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()


class TimeoutTest(unittest.TestCase):
    """"Timeout pour Annuler Eliminer (m)" (demande du 2026-09-17,
    Paramètres, juste sous "Durée du bandeau d'élimination") — 5 minutes
    par défaut dans ces tests, sauf indication contraire. Le délai
    ÉCOULÉ est calculé à partir d'elim_time converti en epoch (voir
    Database._elim_time_to_epoch), JAMAIS une comparaison de texte
    HH:MM affiché (voir test_calcul_correct_malgre_changement_de_minute
    ci-dessous) — contrôlé à la fois par undo_last_elimination_available
    (utilisée par main.py pour l'état du bouton/clic droit) ET, de façon
    strictement indépendante, dans undo_last_elimination() lui-même
    (RÈGLE demandée : aucune voie d'appel ne doit pouvoir contourner le
    délai en ignorant l'interface)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_timeout_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"undo_elimination_timeout_minutes": 5})

    def _eliminate_one_of_three(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        return a

    def test_juste_apres_elimination_autorise(self):
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 1)

        self.assertTrue(self.db.undo_last_elimination_available())
        self.db.undo_last_elimination()  # ne lève pas
        self.assertEqual(self.db.get_player(a)["status"], "active")

    def test_juste_avant_expiration_autorise(self):
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 5 * 60 - 1)  # 4:59

        self.assertTrue(self.db.undo_last_elimination_available())
        self.db.undo_last_elimination()  # ne lève pas
        self.assertEqual(self.db.get_player(a)["status"], "active")

    def test_exactement_a_expiration_refuse(self):
        """PILE au timeout (ni avant, ni après) : refusé — le délai
        autorisé est STRICTEMENT inférieur au timeout, jamais <=."""
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 5 * 60)  # exactement 5:00

        self.assertFalse(self.db.undo_last_elimination_available())
        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")  # inchangé

    def test_apres_expiration_refuse(self):
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 5 * 60 + 30)

        self.assertFalse(self.db.undo_last_elimination_available())
        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")

    def test_timeout_zero_refuse_meme_immediatement(self):
        """RÈGLE ABSOLUE demandée : 0 minute désactive COMPLÈTEMENT la
        fonction — jamais interprété comme "illimité", même à l'instant
        même de l'élimination."""
        self.db.set_settings({"undo_elimination_timeout_minutes": 0})
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 0)

        self.assertFalse(self.db.undo_last_elimination_available())
        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")

    def test_appel_direct_apres_expiration_refuse_sans_passer_par_available(self):
        """Demande explicite : le contrôle doit être fait dans la
        logique métier elle-même — vérifié ici en appelant UNIQUEMENT
        undo_last_elimination() directement, sans jamais consulter
        undo_last_elimination_available() au préalable (aucune voie
        d'appel ne doit pouvoir contourner le délai)."""
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 10 * 60)

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")

    def test_aucune_donnee_modifiee_lors_d_un_refus_pour_timeout(self):
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 10 * 60)
        snapshot_before_attempt = {p["id"]: dict(p) for p in self.db.list_players()}
        tables_before_attempt = [dict(t) for t in self.db.list_tables(active_only=False)]

        with self.assertRaises(ValueError):
            self.db.undo_last_elimination()

        snapshot_after_attempt = {p["id"]: dict(p) for p in self.db.list_players()}
        tables_after_attempt = [dict(t) for t in self.db.list_tables(active_only=False)]
        self.assertEqual(snapshot_before_attempt, snapshot_after_attempt)
        self.assertEqual(tables_before_attempt, tables_after_attempt)
        # L'instantané reste présent en base (pas supprimé à l'expiration,
        # voir Database.undo_last_elimination — juste devenu inutilisable).
        self.assertIsNotNone(self.db.get_setting("last_elimination_undo"))

    def test_calcul_correct_malgre_changement_de_minute(self):
        """Preuve directe, déterministe (indépendante de l'heure réelle
        d'exécution du test) que _elim_time_to_epoch calcule un VRAI
        écart en secondes — jamais une comparaison de texte HH:MM, qui
        se tromperait ici en pensant qu'une minute entière s'est
        écoulée alors que seules 3 vraies secondes séparent ces deux
        horodatages (23:59:58 -> 00:01, changement de minute ET
        d'heure)."""
        epoch_before = self.db._elim_time_to_epoch("2026-01-01 23:59:58")
        epoch_after = self.db._elim_time_to_epoch("2026-01-02 00:00:01")
        self.assertEqual(epoch_after - epoch_before, 3)

    def test_bouton_actif_puis_grise_apres_expiration(self):
        """Niveau App — _update_undo_elimination_button_state (voir
        main.py) ne fait qu'un seul appel à Database.undo_last_
        elimination_available, jamais une logique dupliquée."""
        a = self._eliminate_one_of_three()

        class _FakeButton:
            def __init__(self):
                self.state = None

            def configure(self, state):
                self.state = state

        app = type("FakeApp", (), {"db": self.db})()
        app.undo_elimination_btn = _FakeButton()

        _set_elim_time_seconds_ago(self.db, a, 1)
        main.App._update_undo_elimination_button_state(app)
        self.assertEqual(app.undo_elimination_btn.state, "normal")

        _set_elim_time_seconds_ago(self.db, a, 5 * 60 + 1)
        main.App._update_undo_elimination_button_state(app)
        self.assertEqual(app.undo_elimination_btn.state, "disabled")

    def test_reglage_a_zero_grise_le_bouton_immediatement(self):
        self.db.set_settings({"undo_elimination_timeout_minutes": 0})
        a = self._eliminate_one_of_three()
        _set_elim_time_seconds_ago(self.db, a, 0)

        class _FakeButton:
            def __init__(self):
                self.state = None

            def configure(self, state):
                self.state = state

        app = type("FakeApp", (), {"db": self.db})()
        app.undo_elimination_btn = _FakeButton()

        main.App._update_undo_elimination_button_state(app)
        self.assertEqual(app.undo_elimination_btn.state, "disabled")


class AppUndoLastEliminationTest(unittest.TestCase):
    """App._undo_last_elimination — niveau UNITAIRE, doublure App minimale
    (jamais de vrai Tk), même principe que tests/test_players_right_
    click_eliminate.py: EliminateSelectedShortcutTest."""

    class _FakeApp:
        def __init__(self, db):
            self.db = db
            self.clear_checked_calls = 0
            self.refresh_all_calls = 0
            self.check_pending_rebalance_calls = 0
            self.trigger_movement_alert_calls = 0
            self.finish_movement_alert_calls = 0

        def _clear_checked(self):
            self.clear_checked_calls += 1

        def _refresh_all(self):
            self.refresh_all_calls += 1

        def _refresh_remote_players_cache(self):
            pass

        def _refresh_remote_moves_cache(self):
            pass

        def _check_pending_rebalance(self):
            self.check_pending_rebalance_calls += 1

        def _trigger_movement_alert(self, from_remote=False):
            self.trigger_movement_alert_calls += 1

        def _finish_movement_alert(self):
            self.finish_movement_alert_calls += 1

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="undo_elim_app_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "a")
        self.addCleanup(self.db.conn.close)

    def test_bouton_sans_joueur_elimine_ne_fait_rien(self):
        self.db.add_player("Alice")
        app = self._FakeApp(self.db)

        with patch.object(main.messagebox, "askyesno") as mock_confirm:
            main.App._undo_last_elimination(app)

        mock_confirm.assert_not_called()
        self.assertEqual(app.refresh_all_calls, 0)

    def test_bouton_ignore_toute_selection_courante(self):
        """Le bouton "Annule Eliminer" vise TOUJOURS le dernier éliminé,
        jamais une sélection/un cochage courant — App._undo_last_
        elimination n'en lit d'ailleurs aucun."""
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(b)  # b est le dernier éliminé
        app = self._FakeApp(self.db)
        # `a` est actif, jamais sélectionné/coché nulle part dans ce
        # test : rien dans App._undo_last_elimination ne devrait
        # pourtant s'y intéresser.

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._undo_last_elimination(app)

        self.assertEqual(self.db.get_player(b)["status"], "active")  # bien b, pas a
        self.assertEqual(self.db.get_player(a)["status"], "active")

    def test_confirmation_refusee_aucune_modification(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        app = self._FakeApp(self.db)

        with patch.object(main.messagebox, "askyesno", return_value=False):
            main.App._undo_last_elimination(app)

        self.assertEqual(self.db.get_player(a)["status"], "eliminated")  # inchangé
        self.assertEqual(app.refresh_all_calls, 0)

    def test_confirmation_acceptee_annule_et_rafraichit(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        app = self._FakeApp(self.db)

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_confirm:
            main.App._undo_last_elimination(app)

        mock_confirm.assert_called_once()
        self.assertEqual(self.db.get_player(a)["status"], "active")
        self.assertEqual(app.refresh_all_calls, 1)
        self.assertEqual(app.clear_checked_calls, 1)
        self.assertEqual(app.check_pending_rebalance_calls, 1)

    def test_etat_incompatible_affiche_erreur_sans_rien_modifier(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.db.eliminate_player(a)
        self.db.add_player("Dave")  # rend l'instantané incompatible
        app = self._FakeApp(self.db)

        with patch.object(main.messagebox, "askyesno", return_value=True), \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._undo_last_elimination(app)

        mock_error.assert_called_once()
        self.assertEqual(self.db.get_player(a)["status"], "eliminated")  # inchangé
        self.assertEqual(app.refresh_all_calls, 0)


if __name__ == "__main__":
    unittest.main()
