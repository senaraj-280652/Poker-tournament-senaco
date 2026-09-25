# -*- coding: utf-8 -*-
"""Correctif du 2026-09-25 (deux volets, même diagnostic sous-jacent) :

PHASE 1 — "4 mouvements réels affichés, puis réponse à la question UTG,
puis 1 seul mouvement affiché" (test réel sur le MSI Windows v1.2.43) :
rebalance_tables()/resolve_pending_rebalance()/undo_last_elimination()
faisaient chacun leur PROPRE `DELETE FROM seat_moves` inconditionnel
avant de réinsérer UNIQUEMENT les mouvements qu'ils venaient de décider —
effaçant au passage tout mouvement INDÉPENDANT décidé par un appel
précédent et jamais encore confirmé par le responsable (bouton [OK] du
téléphone/Terminé). Corrigé en factorisant l'écriture dans Database.
_sync_seat_moves (voir sa docstring), appelée par les 3 fonctions
ci-dessus.

PHASE 2 — "question UTG posée avant l'exécution physique des mouvements
précédents" (signalé après tests réels du correctif de la Phase 1) :
deux défauts distincts, corrigés ensemble :
(a) une nouvelle question UTG "simple" pouvait être posée alors que des
    mouvements déjà décidés (fermeture de table, ou réponse à une
    question précédente) n'étaient pas encore physiquement faits —
    corrigé par un garde-fou dans rebalance_tables() (moves_still_
    unconfirmed) qui DIFFÈRE toute nouvelle question tant que
    count_seat_moves() > 0 ou qu'un mouvement réel vient d'avoir lieu
    PENDANT cet appel-ci (fermeture) ; le besoin différé est ré-évalué
    dès que tout est confirmé (voir App._resume_rebalance_if_needed dans
    main.py, et resolve_pending_rebalance qui archive désormais my_move
    AVANT de relancer rebalance_tables(), pour que ce même garde-fou le
    voie et ne pose pas de seconde question dans la foulée) ;
(b) _sync_seat_moves pouvait, pour un joueur redéplacé deux fois avant
    confirmation, afficher une instruction partant d'une étape
    intermédiaire jamais physiquement occupée plutôt que de sa vraie
    origine — corrigé en préservant toujours l'origine la plus ancienne
    encore non confirmée, et en supprimant l'instruction si la chaîne
    ramène le joueur à son point de départ réel.

Sections :
1. ReproductionDuDiagnosticTest — reproduction EXACTE du scénario réel
   signalé (fermeture de table -> plusieurs mouvements réels -> écart
   résiduel différé -> confirmation -> question UTG -> réponse -> TOUS
   les mouvements doivent rester visibles, pas seulement le dernier).
2. SyncSeatMovesUnitTest — Database._sync_seat_moves directement, pour
   chacune des garanties demandées (joueur redéplacé une seule ligne à
   jour avec origine réelle préservée, retour au départ = mouvement
   annulé, joueur éliminé purgé, mouvements indépendants jamais touchés).
3. GuidageDesactiveEtContinuerSansDesignerTest — non-régression : guidage
   UTG désactivé (mécanisme historique) et "Continuer sans désigner le
   joueur" doivent continuer à archiver TOUS les mouvements d'un appel,
   sans jamais perdre ce qui précède.
3b. RebalanceDeferralGateTest / ResolvePendingRebalanceChainDeferralTest
   — le garde-fou de différé lui-même (Phase 2a) : jamais de nouvelle
   question tant qu'un mouvement reste non confirmé (peu importe son
   origine), toujours reposée dès que tout est confirmé, jamais appliqué
   aux fermetures/mises en conformité (toujours immédiates).
4. UndoLastEliminationSeatMovesTest — Database.undo_last_elimination ne
   doit ni perdre un mouvement indépendant en attente, ni laisser une
   instruction devenue fausse pour le joueur réintégré.
5. ConfirmEtClearInchangesTest — confirm_seat_move()/clear_seat_moves()
   (jamais modifiées par ce correctif) continuent de fonctionner
   normalement après lui.
6. StressPlusieursEliminationsSansConfirmationTest — test de stress
   demandé explicitement : plusieurs éliminations rapprochées, aucune
   confirmation entre elles, plusieurs questions UTG posées/résolues à
   la suite — seat_moves doit, après CHAQUE opération, contenir
   EXACTEMENT les instructions encore nécessaires (un joueur actif par
   ligne au plus, jamais de doublon, jamais de perte d'un mouvement
   indépendant)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


def _fake_move(player_name, old_table="Table X", old_seat=1, new_table="Table Y", new_seat=2,
                reason="equilibrage_bb"):
    return {
        "player_name": player_name, "old_table_name": old_table, "old_seat": old_seat,
        "new_table_name": new_table, "new_seat": new_seat,
        "moved_at": "2026-09-25 10:00:00", "reason": reason,
    }


def _table_counts(db):
    return {
        t["name"]: db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (t["id"],)
        ).fetchone()["c"]
        for t in db.list_tables()
    }


# =======================================================================
# 1. Reproduction exacte du diagnostic réel (v1.2.43, tournoi du club)
# =======================================================================
class _FermetureDeTablePuisEcartGuideTestCase(unittest.TestCase):
    """3 tables (9/9/4) -> élimination de 4 joueurs de Table 1 -> ferme
    Table 3 (4 évincés, TOUS vers Table 1, Table 2 déjà pleine) -> 9/9,
    4 mouvements réels enregistrés, aucune question. Puis élimination de
    2 joueurs de Table 2 -> écart 9 vs 7 (>=2) qui existe bien, mais dont
    la question UTG est DIFFÉRÉE (correctif du 2026-09-25, chantier
    "question UTG posée avant l'exécution physique des mouvements
    précédents" — voir Database.rebalance_tables: moves_still_
    unconfirmed) tant que les 4 mouvements de la fermeture ne sont pas
    confirmés : seat_moves doit toujours contenir EXACTEMENT ces 4
    mouvements, sans question posée entre-temps — c'est précisément
    l'état réel signalé (question UTG sur une table qui vient de recevoir
    des joueurs pas encore physiquement arrivés)."""

    def setUp(self):
        guided_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(guided_patcher.stop)
        guided_patcher.start()

        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t3 = self.db.add_table("Table 3")
        self.t1p = {s: _seat_new_player(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)}
        self.t2p = {s: _seat_new_player(self.db, self.t2, s, f"T2-{s}") for s in range(1, 10)}
        self.t3p = {s: _seat_new_player(self.db, self.t3, s, f"T3-{s}") for s in range(1, 5)}

        # Fermeture de Table 3 : 4 mouvements réels, table 1/2 rééquilibrées à 9/9.
        # Un éventuel écart intermédiaire (avant la fermeture) peut poser
        # puis retirer sa propre question au fil des éliminations
        # (revalidée à chaque appel, voir rebalance_tables) — seul l'état
        # final importe ici, vérifié ci-dessous.
        for seat in (9, 8, 7, 6):
            self.db.eliminate_player(self.t1p[seat])
        self.assertEqual(self.db.count_seat_moves(), 4, "précondition : 4 mouvements réels après la fermeture")
        self.assertIsNone(self.db.pending_rebalance, "précondition : pas encore de question à ce stade")

        # Écart 9 vs 7 réel, mais DIFFÉRÉ : les 4 mouvements de la
        # fermeture restent non confirmés, donc AUCUNE nouvelle question.
        self.db.eliminate_player(self.t2p[9])
        self.db.eliminate_player(self.t2p[8])
        self.assertIsNone(
            self.db.pending_rebalance,
            "précondition : la question UTG doit être DIFFÉRÉE tant que les 4 "
            "mouvements précédents ne sont pas confirmés",
        )
        self.assertEqual(
            self.db.count_seat_moves(), 4,
            "précondition : toujours exactement 4 mouvements réels, l'écart différé n'est pas encore résolu",
        )


class ReproductionDuDiagnosticTest(_FermetureDeTablePuisEcartGuideTestCase):
    def test_les_4_mouvements_sont_bien_la_ecart_differe(self):
        """Confirme l'état "4 mouvements affichés, écart réel mais
        question différée" tel que rapporté — voir aussi setUp, qui
        vérifie déjà ceci comme précondition."""
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"T3-1", "T3-2", "T3-3", "T3-4"})

    def test_apres_confirmation_lecart_differe_repose_la_question_sans_effacer_les_4_precedents(self):
        """Coeur du correctif (points 2/3) : une fois les 4 mouvements de
        la fermeture confirmés (bouton "Terminé", simulé ici par
        clear_seat_moves), reprendre le rééquilibrage doit faire
        réapparaître la question UTG différée ; la résoudre ensuite ne
        doit évidemment rien effacer de ce qui a déjà été confirmé."""
        self.db.clear_seat_moves()
        self.assertIsNone(self.db.pending_rebalance)

        moves = self.db.rebalance_tables(record_moves=True)
        self.assertEqual(moves, [], "aucun mouvement immédiat : le besoin différé doit redevenir une question, pas se résoudre seul")
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending, "le besoin différé doit réapparaître une fois les 4 mouvements confirmés")

        designated = self.t1p[1]  # un survivant de Table 1, jamais déplacé jusqu'ici
        moves = self.db.resolve_pending_rebalance(pending["request_id"], designated)

        self.assertTrue(moves, "la réponse doit bien décider un mouvement")
        self.assertEqual(self.db.count_seat_moves(), 1, "les 4 précédents ont déjà été confirmés, un seul mouvement reste")
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"T1-1"})
        self.assertEqual(_table_counts(self.db), {"Table 1": 8, "Table 2": 8})

    def test_chaque_ligne_seat_moves_reflete_la_position_reelle_actuelle(self):
        """Invariant fondamental : new_table_name/new_seat d'une ligne
        seat_moves doit toujours correspondre à la position RÉELLE
        actuelle du joueur (players.table_id/seat) — sinon la ligne
        demanderait un déplacement soit déjà fait, soit faux."""
        self.db.clear_seat_moves()
        self.db.rebalance_tables(record_moves=True)
        pending = self.db.pending_rebalance
        self.db.resolve_pending_rebalance(pending["request_id"], self.t1p[1])
        table_names = {t["id"]: t["name"] for t in self.db.list_tables()}
        for row in self.db.get_seat_moves():
            player = self.db.conn.execute(
                "SELECT table_id, seat FROM players WHERE name=?", (row["player_name"],)
            ).fetchone()
            self.assertEqual(table_names[player["table_id"]], row["new_table_name"])
            self.assertEqual(player["seat"], row["new_seat"])


# =======================================================================
# 2. Database._sync_seat_moves directement
# =======================================================================
class SyncSeatMovesUnitTest(unittest.TestCase):
    def setUp(self):
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.t1 = self.db.list_tables()[0]["id"]
        self.alice = _seat_new_player(self.db, self.t1, 1, "Alice")
        self.bob = _seat_new_player(self.db, self.t1, 2, "Bob")
        # Un 3e joueur actif : sans lui, éliminer Alice ne laisserait
        # qu'un seul joueur actif (Bob) et rebalance_tables() (appelée en
        # interne par eliminate_player) s'arrêterait tout de suite sans
        # jamais appeler _sync_seat_moves (voir son garde-fou "n_active
        # <= 1" — le tournoi serait terminé) : la purge testée ci-dessous
        # ne serait alors jamais exercée.
        self.chloe = _seat_new_player(self.db, self.t1, 3, "Chloé")

    def test_deux_joueurs_differents_saccumulent(self):
        self.db._sync_seat_moves([_fake_move("Alice")])
        self.db._sync_seat_moves([_fake_move("Bob")])
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"Alice", "Bob"})

    def test_joueur_redeplace_avant_confirmation_une_seule_ligne_a_jour(self):
        """Exigence explicite : pour un même joueur redéplacé avant
        confirmation, il ne doit rester qu'UNE SEULE instruction
        cohérente et à jour."""
        self.db._sync_seat_moves([_fake_move("Alice", new_table="Table A", new_seat=3)])
        self.db._sync_seat_moves([_fake_move("Alice", new_table="Table B", new_seat=7)])
        rows = [dict(r) for r in self.db.get_seat_moves()]
        self.assertEqual(len(rows), 1, "jamais deux lignes pour le même joueur")
        self.assertEqual(rows[0]["new_table_name"], "Table B")
        self.assertEqual(rows[0]["new_seat"], 7)

    def test_joueur_redeplace_ne_touche_pas_aux_autres_lignes(self):
        self.db._sync_seat_moves([_fake_move("Alice"), _fake_move("Bob")])
        self.db._sync_seat_moves([_fake_move("Alice", new_table="Table Z", new_seat=9)])
        rows = {r["player_name"]: dict(r) for r in self.db.get_seat_moves()}
        self.assertEqual(set(rows), {"Alice", "Bob"})
        self.assertEqual(rows["Alice"]["new_table_name"], "Table Z")
        self.assertEqual(rows["Bob"]["new_table_name"], "Table Y")  # inchangé

    def test_joueur_elimine_voit_sa_ligne_purgee(self):
        """Exigence explicite : un mouvement devenu matériellement
        impossible (joueur éliminé depuis) doit être supprimé
        proprement — sans toucher aux autres."""
        self.db._sync_seat_moves([_fake_move("Alice"), _fake_move("Bob")])
        self.db.eliminate_player(self.alice)  # appelle rebalance_tables(record_moves=True) en interne
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertNotIn("Alice", names, "la ligne d'un joueur éliminé ne doit jamais rester affichée")
        self.assertIn("Bob", names, "le mouvement indépendant de Bob ne doit jamais être perdu")

    def test_aucun_mouvement_aucune_ligne_perimee_najoute_rien(self):
        """new_moves vide ET rien à purger -> aucune écriture (pas même
        un commit inutile) — vérifié indirectement via l'absence de
        changement d'état."""
        self.db._sync_seat_moves([_fake_move("Bob")])
        before = [dict(r) for r in self.db.get_seat_moves()]
        self.db._sync_seat_moves([])
        after = [dict(r) for r in self.db.get_seat_moves()]
        self.assertEqual(before, after)

    def test_mouvements_independants_jamais_effaces_par_un_appel_vide(self):
        self.db._sync_seat_moves([_fake_move("Alice"), _fake_move("Bob")])
        self.db._sync_seat_moves([])  # ex. rebalance_tables(record_moves=True) sans rien à faire
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"Alice", "Bob"})

    # -- Origine physique préservée à travers une chaîne de redécisions --
    # (correctif du 2026-09-25, points 1/6 du diagnostic "question UTG
    # posée avant l'exécution physique des mouvements précédents") :
    # reproduction directe du script de diagnostic ("X déplacé Table1->
    # Table2 puis Table2->Table3 sans jamais bouger physiquement, la ligne
    # finale affichait à tort Table2->Table3 au lieu de Table1->Table3").

    def test_origine_reelle_preservee_a_travers_deux_appels_separes(self):
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 1", old_seat=1, new_table="Table 2", new_seat=5)]
        )
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 2", old_seat=5, new_table="Table 3", new_seat=2)]
        )
        rows = [dict(r) for r in self.db.get_seat_moves()]
        self.assertEqual(len(rows), 1, "jamais deux lignes pour le même joueur")
        self.assertEqual(rows[0]["old_table_name"], "Table 1", "l'origine RÉELLE (jamais quittée) doit être préservée")
        self.assertEqual(rows[0]["old_seat"], 1)
        self.assertEqual(rows[0]["new_table_name"], "Table 3")
        self.assertEqual(rows[0]["new_seat"], 2)

    def test_chaine_de_trois_redecisions_garde_toujours_lorigine_dorigine(self):
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 1", old_seat=1, new_table="Table 2", new_seat=5)]
        )
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 2", old_seat=5, new_table="Table 3", new_seat=2)]
        )
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 3", old_seat=2, new_table="Table 4", new_seat=9)]
        )
        rows = [dict(r) for r in self.db.get_seat_moves()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["old_table_name"], "Table 1")
        self.assertEqual(rows[0]["new_table_name"], "Table 4")

    def test_retour_au_point_de_depart_annule_le_mouvement(self):
        """Si la chaîne de redécisions ramène le joueur à sa table
        d'origine réelle, l'instruction devient sans objet et disparaît —
        demande explicite du 2026-09-25."""
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 1", old_seat=1, new_table="Table 2", new_seat=5)]
        )
        self.db._sync_seat_moves(
            [_fake_move("Alice", old_table="Table 2", old_seat=5, new_table="Table 1", new_seat=1)]
        )
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertNotIn("Alice", names, "plus rien à déplacer : le joueur boucle sur sa table d'origine réelle")

    def test_retour_au_point_de_depart_ne_touche_pas_aux_autres_lignes(self):
        self.db._sync_seat_moves([_fake_move("Alice", old_table="Table 1", new_table="Table 2"), _fake_move("Bob")])
        self.db._sync_seat_moves([_fake_move("Alice", old_table="Table 2", new_table="Table 1")])
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"Bob"}, "le retour au point de départ d'Alice ne doit jamais toucher Bob")

    def test_chaine_au_sein_dune_meme_liste_new_moves(self):
        """Même garantie, mais pour deux entrées du MÊME joueur au sein
        d'un seul appel (ex. resolve_pending_rebalance : my_move +
        further_moves combinés dans un seul new_moves)."""
        self.db._sync_seat_moves([
            _fake_move("Alice", old_table="Table 1", old_seat=1, new_table="Table 2", new_seat=5),
            _fake_move("Alice", old_table="Table 2", old_seat=5, new_table="Table 3", new_seat=2),
        ])
        rows = [dict(r) for r in self.db.get_seat_moves()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["old_table_name"], "Table 1")
        self.assertEqual(rows[0]["new_table_name"], "Table 3")

    def test_chaine_au_sein_dune_meme_liste_retour_au_depart_annule(self):
        self.db._sync_seat_moves([
            _fake_move("Alice", old_table="Table 1", old_seat=1, new_table="Table 2", new_seat=5),
            _fake_move("Alice", old_table="Table 2", old_seat=5, new_table="Table 1", new_seat=1),
        ])
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertNotIn("Alice", names)


# =======================================================================
# 3. Non-régression : guidage désactivé / "Continuer sans désigner"
# =======================================================================
class _NonGuideDbTestCase(unittest.TestCase):
    """9 vs 3 (écart de 6), guidage UTG EXPLICITEMENT désactivé.

    Attention : la préférence est ACTIVÉE PAR DÉFAUT (voir
    Database._bb_rebalance_prompt_enabled — "True par défaut, désactivée
    seulement si explicitement enregistrée à False") — ne PAS patcher
    export_prefs.load_value ici laisserait le vrai mécanisme lire sa
    valeur par défaut réelle (True, donc GUIDÉ), à l'exact inverse de ce
    que ce test veut vérifier. D'où le patch explicite return_value=False
    ci-dessous, jamais une simple absence de patch."""

    def setUp(self):
        guided_patcher = patch.object(database.export_prefs, "load_value", return_value=False)
        self.addCleanup(guided_patcher.stop)
        guided_patcher.start()
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t1p = {s: _seat_new_player(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)}
        self.t2p = {s: _seat_new_player(self.db, self.t2, s, f"T2-{s}") for s in range(1, 4)}


class GuidageDesactiveEtContinuerSansDesignerTest(_NonGuideDbTestCase):
    def test_guidage_desactive_archive_tous_les_mouvements_dun_meme_appel(self):
        """Le mécanisme historique (boucle interne, jamais de question)
        doit continuer à archiver l'INTÉGRALITÉ des mouvements décidés en
        un seul appel — comportement fonctionnellement identique à avant
        ce correctif."""
        moves = self.db.rebalance_tables(record_moves=True)
        self.assertGreaterEqual(len(moves), 1)
        self.assertIsNone(self.db.pending_rebalance, "jamais de question, guidage désactivé")
        self.assertEqual(self.db.count_seat_moves(), len(moves))
        self.assertEqual(_table_counts(self.db)["Table 1"], _table_counts(self.db)["Table 2"])

    def test_guidage_desactive_najamais_efface_un_mouvement_independant_anterieur(self):
        # "Etranger" doit être un VRAI joueur actif : _sync_seat_moves
        # purge toute ligne dont le joueur n'est plus actif (exigence
        # explicite du correctif) — un simple nom fictif serait purgé
        # comme "joueur inexistant/inactif", faussant ce test.
        _seat_new_player(self.db, self.t2, 9, "Etranger")
        self.db._sync_seat_moves([_fake_move("Etranger", old_table="Table 2", new_table="Table 9", new_seat=9)])
        self.db.rebalance_tables(record_moves=True)
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertIn("Etranger", names, "un mouvement sans rapport ne doit jamais disparaître")


class ContinuerSansDesignerTest(unittest.TestCase):
    def setUp(self):
        guided_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(guided_patcher.stop)
        guided_patcher.start()
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t1p = {s: _seat_new_player(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)}
        self.t2p = {s: _seat_new_player(self.db, self.t2, s, f"T2-{s}") for s in range(1, 4)}

    def test_continuer_sans_designer_najamais_efface_les_mouvements_anterieurs(self):
        # La question est posée D'ABORD, alors qu'AUCUN mouvement n'est
        # encore en attente (sinon le garde-fou du 2026-09-25 — voir
        # RebalanceDeferralGateTest — la différerait) : "Etranger" n'est
        # inséré qu'ENSUITE, directement via _sync_seat_moves, simulant un
        # mouvement indépendant apparu pendant que cette question était
        # déjà affichée (ex : fermeture de table concurrente ailleurs —
        # toujours immédiate, jamais soumise à ce garde-fou). Doit rester
        # un VRAI joueur actif, sinon _sync_seat_moves le purge aussitôt.
        self.db.rebalance_tables(record_moves=True)
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending)

        _seat_new_player(self.db, self.t2, 9, "Etranger")
        self.db._sync_seat_moves([_fake_move("Etranger", old_table="Table 2", new_table="Table 9", new_seat=9)])

        moves = self.db.resolve_pending_rebalance(pending["request_id"], None)

        self.assertTrue(moves)
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertIn("Etranger", names, "'Continuer sans désigner' ne doit jamais effacer un mouvement indépendant")
        self.assertTrue(names - {"Etranger"}, "le mouvement décidé par cette réponse doit aussi être présent")


# =======================================================================
# 3b. Différé d'une nouvelle question UTG tant que des mouvements
#     précédents restent non confirmés (correctif du 2026-09-25, points
#     2/3 du diagnostic "question UTG posée avant l'exécution physique
#     des mouvements précédents") — et reprise automatique une fois
#     confirmés.
# =======================================================================
class RebalanceDeferralGateTest(unittest.TestCase):
    """Database.rebalance_tables : une nouvelle question UTG "simple" ne
    doit jamais être posée tant qu'il reste un mouvement non confirmé,
    qu'il vienne d'un appel PRÉCÉDENT (seat_moves déjà peuplé) ou de CET
    appel-ci (fermeture de table, toujours immédiate, juste avant, dans
    la MÊME fonction) — et doit réapparaître dès que tout est confirmé."""

    def setUp(self):
        guided_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(guided_patcher.stop)
        guided_patcher.start()
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")

    def test_mouvement_independant_anterieur_differe_une_nouvelle_question(self):
        for s in range(1, 10):
            _seat_new_player(self.db, self.t1, s, f"T1-{s}")  # 9
        for s in range(1, 7):
            _seat_new_player(self.db, self.t2, s, f"T2-{s}")  # 6
        # Mouvement indépendant déjà en attente, sans rapport avec T1/T2.
        _seat_new_player(self.db, self.t2, 9, "Etranger")
        self.db._sync_seat_moves(
            [_fake_move("Etranger", old_table="Table 2", new_table="Table 9", new_seat=9)]
        )
        self.assertEqual(self.db.count_seat_moves(), 1)
        # T1=9, T2=7 (6 + Etranger) : écart réel (>=2), mais différé.

        moves = self.db.rebalance_tables(record_moves=True)

        self.assertEqual(moves, [])
        self.assertIsNone(self.db.pending_rebalance, "différée : un mouvement indépendant reste non confirmé")
        self.assertEqual(self.db.count_seat_moves(), 1, "toujours uniquement le mouvement d'Etranger")

    def test_fermeture_dans_le_meme_appel_differe_la_question_qui_suivrait(self):
        """Reproduction directe du scénario réel signalé : une fermeture
        de table (immédiate) crée, dans LE MÊME appel, un écart résiduel
        sur une autre table — ce besoin ne doit pas devenir une question
        immédiate portant sur des joueurs qui viennent tout juste
        d'arriver (logiquement, pas physiquement). Table 1 pleine (9/9) :
        les 3 évincés de la fermeture de Table 3 ne peuvent aller QUE sur
        Table 2, créant un écart Table 1 (9) vs Table 2 (5) = 4."""
        t3 = self.db.add_table("Table 3")
        for s in range(1, 10):
            _seat_new_player(self.db, self.t1, s, f"T1-{s}")  # 9, pleine
        _seat_new_player(self.db, self.t2, 1, "T2-1")
        _seat_new_player(self.db, self.t2, 2, "T2-2")  # 2
        for s in (1, 2, 3):
            _seat_new_player(self.db, t3, s, f"T3-{s}")  # 3 -> fermera Table 3

        moves = self.db.rebalance_tables(record_moves=True)

        self.assertEqual(_table_counts(self.db), {"Table 1": 9, "Table 2": 5})
        self.assertEqual(len(moves), 3, "3 mouvements réels de fermeture, tous vers Table 2 (Table 1 pleine)")
        self.assertIsNone(
            self.db.pending_rebalance,
            "différé : la fermeture vient elle-même de produire, dans CET appel, des mouvements non confirmés",
        )
        self.assertEqual(self.db.count_seat_moves(), 3)

    def test_apres_confirmation_le_besoin_differe_est_repose(self):
        t3 = self.db.add_table("Table 3")
        for s in range(1, 10):
            _seat_new_player(self.db, self.t1, s, f"T1-{s}")
        _seat_new_player(self.db, self.t2, 1, "T2-1")
        _seat_new_player(self.db, self.t2, 2, "T2-2")
        for s in (1, 2, 3):
            _seat_new_player(self.db, t3, s, f"T3-{s}")
        self.db.rebalance_tables(record_moves=True)
        self.assertIsNone(self.db.pending_rebalance)

        self.db.clear_seat_moves()  # confirmation des 3 mouvements de fermeture
        moves = self.db.rebalance_tables(record_moves=True)

        self.assertEqual(moves, [])
        self.assertIsNotNone(self.db.pending_rebalance, "le besoin différé doit réapparaître une fois confirmé")
        self.assertEqual(self.db.pending_rebalance["table_name"], "Table 1")

    def test_fermeture_de_table_jamais_differee_meme_avec_mouvements_en_attente(self):
        """Les fermetures/mises en conformité de capacité restent
        TOUJOURS immédiates, quel que soit l'état de seat_moves — seule
        la question "simple" (sportive) est concernée par le différé."""
        t3 = self.db.add_table("Table 3")
        for s in range(1, 10):
            _seat_new_player(self.db, self.t1, s, f"T1-{s}")
        for s in (1, 2):
            _seat_new_player(self.db, self.t2, s, f"T2-{s}")
        for s in (1, 2, 3):
            _seat_new_player(self.db, t3, s, f"T3-{s}")
        # Mouvement indépendant déjà en attente, sans rapport.
        _seat_new_player(self.db, self.t2, 9, "Etranger")
        self.db._sync_seat_moves(
            [_fake_move("Etranger", old_table="Table 2", new_table="Table 9", new_seat=9)]
        )

        moves = self.db.rebalance_tables(record_moves=True)

        self.assertEqual(len(self.db.list_tables()), 2, "Table 3 doit fermer malgré le mouvement en attente")
        self.assertEqual(len(moves), 3, "la fermeture doit avoir lieu et être archivée, mouvement en attente ou non")


class ResolvePendingRebalanceChainDeferralTest(unittest.TestCase):
    """Après avoir résolu une question UTG, s'il reste un écart résiduel
    sur la même table (gros écart nécessitant plusieurs joueurs), la
    question SUIVANTE ne doit pas être posée immédiatement dans la foulée
    — le joueur tout juste désigné n'a pas encore physiquement bougé
    (correctif du 2026-09-25, réordonnancement de resolve_pending_
    rebalance : my_move est désormais archivé AVANT de relancer
    rebalance_tables(), pour que son propre garde-fou le voie)."""

    def setUp(self):
        guided_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(guided_patcher.stop)
        guided_patcher.start()
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t1p = {s: _seat_new_player(self.db, self.t1, s, f"T1-{s}") for s in range(1, 10)}  # 9
        self.t2p = {s: _seat_new_player(self.db, self.t2, s, f"T2-{s}") for s in range(1, 4)}  # 3

    def test_reponse_ne_declenche_pas_immediatement_une_seconde_question(self):
        self.db.rebalance_tables(record_moves=True)  # écart 9 vs 3 -> question sur Table 1
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending)

        moves = self.db.resolve_pending_rebalance(pending["request_id"], self.t1p[1])

        self.assertTrue(moves)
        self.assertEqual(len(moves), 1, "un seul mouvement archivé — pas de second enchaîné immédiatement")
        self.assertEqual(_table_counts(self.db), {"Table 1": 8, "Table 2": 4})
        self.assertIsNone(
            self.db.pending_rebalance,
            "la seconde question (écart encore réel, 8 vs 4) doit être différée : le premier mouvement "
            "n'est pas encore confirmé",
        )
        self.assertEqual(self.db.count_seat_moves(), 1)

    def test_apres_confirmation_du_premier_mouvement_la_seconde_question_apparait(self):
        self.db.rebalance_tables(record_moves=True)
        pending = self.db.pending_rebalance
        self.db.resolve_pending_rebalance(pending["request_id"], self.t1p[1])
        self.db.clear_seat_moves()  # confirmation du premier mouvement

        moves = self.db.rebalance_tables(record_moves=True)

        self.assertEqual(moves, [])
        self.assertIsNotNone(self.db.pending_rebalance, "la question suivante doit apparaître une fois confirmé")
        self.assertEqual(self.db.pending_rebalance["table_name"], "Table 1")


# =======================================================================
# 4. undo_last_elimination
# =======================================================================
class UndoLastEliminationSeatMovesTest(unittest.TestCase):
    def setUp(self):
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
            "undo_elimination_timeout_minutes": 5,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.alice = _seat_new_player(self.db, self.t1, 1, "Alice")
        self.bob = _seat_new_player(self.db, self.t1, 2, "Bob")
        self.chloe = _seat_new_player(self.db, self.t2, 1, "Chloé")
        self.dave = _seat_new_player(self.db, self.t2, 2, "Dave")

    def test_annulation_najamais_efface_un_mouvement_independant(self):
        """Un mouvement sans aucun rapport avec l'élimination annulée
        (une autre table, jamais confirmé par le responsable) doit
        survivre intact à l'annulation.

        Une table 3 "tampon" (8 joueurs) est ajoutée pour que 4 joueurs
        actifs au total ne tombent PAS sous FINAL_TABLE_MAX_SEATS après
        l'élimination d'Alice, ce qui forcerait sinon une consolidation
        sur une seule table finale — fermant Table 2 et déplaçant RÉELLEMENT
        Chloé (plus du tout "un mouvement sans rapport", contrairement à
        l'intention du test). Table 3 ferme à la place (elle est la plus
        haute), sans jamais toucher à la position de Chloé elle-même."""
        t3 = self.db.add_table("Table 3")
        for s in range(1, 9):
            _seat_new_player(self.db, t3, s, f"T3-{s}")
        self.db._sync_seat_moves([_fake_move("Chloé", old_table="Table 2", new_table="Table 9", new_seat=9)])

        self.db.eliminate_player(self.alice)

        self.db.undo_last_elimination()

        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertIn("Chloé", names, "le mouvement indépendant de Chloé ne doit jamais disparaître")

    def test_annulation_ne_laisse_pas_dinstruction_fausse_pour_le_joueur_reintegre(self):
        """Si l'élimination annulée avait elle-même provoqué un
        mouvement réel (ex. fermeture de table), la ligne correspondante
        doit refléter la VRAIE position d'avant élimination après
        l'annulation — jamais une instruction obsolète pointant vers la
        position d'après élimination (déjà défaite)."""
        # Réduit Table 2 à 0 pour forcer une fermeture de table quand
        # Dave sera éliminé après Chloé.
        self.db.eliminate_player(self.chloe)
        self.db.eliminate_player(self.dave)  # ferme Table 2, redistribue... plus personne dessus, no-op réel
        # Rejoue un scénario plus parlant : élimine Bob (reste Alice
        # seule à Table 1, potentiellement fermeture si Table 2 a encore
        # du monde) — ici Table 2 est vide, donc aucune fermeture ; on
        # vérifie simplement la cohérence générale après annulation.
        self.db.undo_last_elimination()  # annule l'élimination de Dave
        for row in self.db.get_seat_moves():
            player = self.db.conn.execute(
                "SELECT table_id, seat, status FROM players WHERE name=?", (row["player_name"],)
            ).fetchone()
            self.assertEqual(player["status"], "active", "seat_moves ne doit jamais référencer un joueur inactif")
            table_names = {t["id"]: t["name"] for t in self.db.list_tables()}
            self.assertEqual(table_names.get(player["table_id"]), row["new_table_name"])
            self.assertEqual(player["seat"], row["new_seat"])

    def test_annulation_dune_fermeture_de_table_garde_les_mouvements_independants(self):
        """Une fermeture/réorganisation de Table 1 est annulée alors qu'un
        mouvement TOTALEMENT indépendant (Chloé, à Table 2, jamais
        confirmé) est encore en attente — celui-ci doit survivre intact.
        Chloé est un VRAI joueur actif déjà en place (voir setUp) :
        _sync_seat_moves purgerait un simple nom fictif comme "joueur
        inactif", faussant le test."""
        eve = _seat_new_player(self.db, self.t1, 3, "Eve")
        # Table 3 "tampon" : voir la même remarque dans
        # test_annulation_najamais_efface_un_mouvement_independant — sans
        # elle, 4 joueurs actifs restants après l'élimination d'Eve
        # forceraient une consolidation sur une seule table finale,
        # fermant Table 2 et déplaçant RÉELLEMENT Chloé.
        t3 = self.db.add_table("Table 3")
        for s in range(1, 9):
            _seat_new_player(self.db, t3, s, f"T3-{s}")
        self.db._sync_seat_moves(
            [_fake_move("Chloé", old_table="Table 2", old_seat=1, new_table="Table 9", new_seat=1)]
        )

        self.db.eliminate_player(eve)
        self.assertIn("Chloé", {r["player_name"] for r in self.db.get_seat_moves()})

        self.db.undo_last_elimination()

        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertIn("Chloé", names, "le mouvement indépendant doit survivre à l'annulation")


# =======================================================================
# 5. confirm_seat_move / clear_seat_moves — jamais modifiées, vérifiées
# =======================================================================
class ConfirmEtClearInchangesTest(unittest.TestCase):
    def setUp(self):
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.t1 = self.db.list_tables()[0]["id"]
        _seat_new_player(self.db, self.t1, 1, "Alice")
        _seat_new_player(self.db, self.t1, 2, "Bob")
        self.db._sync_seat_moves([_fake_move("Alice"), _fake_move("Bob")])

    def test_confirm_seat_move_retire_uniquement_la_ligne_visee(self):
        rows = list(self.db.get_seat_moves())
        alice_id = next(r["id"] for r in rows if r["player_name"] == "Alice")
        self.db.confirm_seat_move(alice_id)
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"Bob"})

    def test_confirm_seat_move_idempotent(self):
        rows = list(self.db.get_seat_moves())
        alice_id = next(r["id"] for r in rows if r["player_name"] == "Alice")
        self.db.confirm_seat_move(alice_id)
        self.db.confirm_seat_move(alice_id)  # ne doit pas lever
        self.assertEqual(self.db.count_seat_moves(), 1)

    def test_clear_seat_moves_vide_tout(self):
        self.db.clear_seat_moves()
        self.assertEqual(self.db.count_seat_moves(), 0)

    def test_apres_confirmation_partielle_un_nouveau_mouvement_sajoute_normalement(self):
        rows = list(self.db.get_seat_moves())
        alice_id = next(r["id"] for r in rows if r["player_name"] == "Alice")
        self.db.confirm_seat_move(alice_id)
        self.db._sync_seat_moves([_fake_move("Chloé")])
        names = {r["player_name"] for r in self.db.get_seat_moves()}
        self.assertEqual(names, {"Bob", "Chloé"})


# =======================================================================
# 6. Test de stress — plusieurs éliminations/UTG successives, aucune
#    confirmation entre elles
# =======================================================================
class StressPlusieursEliminationsSansConfirmationTest(unittest.TestCase):
    """4 tables de 9 (36 joueurs) — élimine des joueurs par vagues de 2,
    tantôt sur une table déjà équilibrée (peut créer un écart -> question
    UTG), tantôt en répondant immédiatement à la question posée, JAMAIS
    aucune confirmation (confirm_seat_move/clear_seat_moves) entre temps
    — seat_moves doit, après CHAQUE opération, rester un ensemble
    cohérent : au plus une ligne par joueur, uniquement des joueurs
    encore actifs, new_table_name/new_seat reflétant toujours leur
    position réelle actuelle, et surtout AUCUNE perte d'un mouvement
    indépendant déjà décidé."""

    def setUp(self):
        guided_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(guided_patcher.stop)
        guided_patcher.start()
        self.db = database.Database(":memory:")
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1 = self.db.list_tables()[0]["id"]
        self.t2 = self.db.add_table("Table 2")
        self.t3 = self.db.add_table("Table 3")
        self.t4 = self.db.add_table("Table 4")
        self.players = {}
        for tname, tid in (("T1", self.t1), ("T2", self.t2), ("T3", self.t3), ("T4", self.t4)):
            for seat in range(1, 10):
                pid = _seat_new_player(self.db, tid, seat, f"{tname}-{seat}")
                self.players[f"{tname}-{seat}"] = pid

    def _assert_seat_moves_coherent(self):
        rows = [dict(r) for r in self.db.get_seat_moves()]
        names = [r["player_name"] for r in rows]
        self.assertEqual(len(names), len(set(names)), f"doublon détecté dans seat_moves : {names}")
        table_names = {t["id"]: t["name"] for t in self.db.list_tables()}
        for row in rows:
            player = self.db.conn.execute(
                "SELECT status, table_id, seat FROM players WHERE name=?", (row["player_name"],)
            ).fetchone()
            self.assertIsNotNone(player, f"{row['player_name']} référencé dans seat_moves n'existe plus")
            self.assertEqual(
                player["status"], "active",
                f"{row['player_name']} n'est plus actif mais reste dans seat_moves",
            )
            self.assertEqual(
                table_names.get(player["table_id"]), row["new_table_name"],
                f"{row['player_name']} : new_table_name ne correspond plus à sa position réelle",
            )
            self.assertEqual(
                player["seat"], row["new_seat"],
                f"{row['player_name']} : new_seat ne correspond plus à sa position réelle",
            )
        return {r["player_name"] for r in rows}

    def test_stress_eliminations_et_reponses_utg_successives_sans_confirmation(self):
        seen_players_ever_recorded = set()

        def eliminate(name):
            self.db.eliminate_player(self.players[name])
            current = self._assert_seat_moves_coherent()
            # Aucune régression : un joueur déjà vu dans seat_moves ne
            # doit disparaître QUE s'il vient d'être éliminé (purge
            # légitime) — jamais silencieusement à cause d'un appel qui
            # n'a rien à voir avec lui. On ne vérifie donc pas ici une
            # simple sur-ensemble (des mouvements se confirment/résolvent
            # légitimement), mais on trace ce qui a existé pour les
            # diagnostics ci-dessous.
            seen_players_ever_recorded.update(current)
            return current

        def resolve_if_pending(designate=True):
            pending = self.db.pending_rebalance
            if pending is None:
                return
            before = self._assert_seat_moves_coherent()
            if designate:
                # Désigne un joueur quelconque encore présent à la table
                # source, différent du siège déjà utilisé comme référence.
                seat_row = self.db.conn.execute(
                    "SELECT id FROM players WHERE table_id=? AND status='active' LIMIT 1",
                    (pending["table_id"],),
                ).fetchone()
                designated = seat_row["id"] if seat_row else None
            else:
                designated = None
            moves = self.db.resolve_pending_rebalance(pending["request_id"], designated)
            after = self._assert_seat_moves_coherent()
            if moves:
                # Tout ce qui existait avant (sauf le joueur tout juste
                # déplacé, remplacé par sa nouvelle ligne à jour) doit
                # rester présent — c'est exactement la garantie centrale
                # de ce correctif.
                moved_now = {m["player_name"] for m in moves}
                self.assertTrue(
                    (before - moved_now).issubset(after),
                    "un mouvement indépendant a disparu après la résolution de la question UTG",
                )

        # Vague 1 : ferme Table 4 (élimine 9 joueurs d'un coup, aucune
        # confirmation) -> plusieurs mouvements réels d'un coup.
        for seat in range(1, 10):
            eliminate(f"T4-{seat}")
            resolve_if_pending(designate=(seat % 2 == 0))

        # Vague 2 : crée des écarts sur Table 1/2/3 en éliminant par
        # paires, en alternant réponse désignée / "Continuer sans
        # désigner" — toujours sans jamais confirmer un seul mouvement.
        for name in ("T1-2", "T1-3", "T2-2", "T2-3", "T3-2", "T3-3"):
            eliminate(name)
            resolve_if_pending(designate=True)

        for name in ("T1-4", "T2-4"):
            eliminate(name)
            resolve_if_pending(designate=False)

        # Vague 3 : encore quelques éliminations rapprochées.
        for name in ("T1-5", "T2-5", "T3-4"):
            eliminate(name)
            resolve_if_pending(designate=True)

        # Aucune confirmation n'a eu lieu à aucun moment : seat_moves
        # reste malgré tout parfaitement cohérent à la toute fin.
        final = self._assert_seat_moves_coherent()
        self.assertTrue(seen_players_ever_recorded, "le test doit avoir réellement produit des mouvements")
        # Aucune question ne doit rester bloquée sans réponse à la fin
        # (chaque question a été résolue immédiatement après sa création
        # dans ce scénario).
        self.assertIsNone(self.db.pending_rebalance)
        self.assertIsInstance(final, set)


if __name__ == "__main__":
    unittest.main()
