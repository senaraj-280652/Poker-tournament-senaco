# -*- coding: utf-8 -*-
"""Couverture automatisée du mécanisme PKO (demande du 2026-09-08) —
règles du club : tout en points (jamais en euros), pas de rebuy dans les
tournois. Utilise exclusivement les vraies méthodes de Database sur des
fichiers .tournoi SYNTHÉTIQUES en dossier temporaire (jamais un vrai
fichier ni ~/.poker_tournament).

Couvre : bounty classique sans PKO, PKO à plusieurs éliminations (dont
augmentation progressive et joueur multi-kills), calcul de Mon Bounty /
Moy Bounty (arrondi), TOTAL incluant Mon Bounty, récupération de la
bounty finale du vainqueur (sans jamais augmenter Nb Bounty), impossibilité
d'une bounty PKO orpheline (éliminateur obligatoire, y compris depuis le
contrôle à distance avec réponse explicite affichable sur le téléphone —
demande complémentaire du 2026-09-08), cohérence de l'historique PKO,
exports (libellés en points, pas en €), absence de double comptage, et
conservation exacte du nombre total de points sur une simulation complète
sans rebuy à plusieurs joueurs."""
import os
import queue
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402


def _new_db(tmp_dir, name, bounty_amount, pko_mode, pko_cash_percent=50):
    path = os.path.join(tmp_dir, f"{name}.tournoi")
    db = database.Database(path)
    db.set_settings({
        "bounty_amount": str(bounty_amount),
        "pko_mode": "1" if pko_mode else "0",
        "pko_cash_percent": str(pko_cash_percent),
        "starting_chips": "10000",
    })
    return db


class ClassicBountySansPkoTest(unittest.TestCase):
    """Section 2 de la demande : le mode classique reste inchangé."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "classique", bounty_amount=30, pko_mode=False)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.c = self.db.add_player("Chris")

    def test_nb_val_mon_bounty_et_total_classiques(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        self.db.eliminate_player(self.c, eliminated_by_id=self.a)
        row = next(r for r in self.db.get_bounty_bonuses() if r["name"] == "Alice")
        self.assertEqual(row["nombre"], 2)  # Nb Bounty = nb réel d'éliminations
        self.assertEqual(row["valeur"], 30)  # Val Bounty = valeur fixe du tournoi
        self.assertEqual(row["montant"], 60)  # Mon Bounty = Nb x Val
        summary = next(r for r in self.db.get_primes_summary() if r["name"] == "Alice")
        self.assertEqual(summary["total"], summary["presence"] + summary["assiduite"]
                          + summary["cl_montant"] + summary["bo_montant"])

    def test_bo_valeur_reste_val_bounty_hors_pko_dans_primes_columns(self):
        cols = self.db.primes_columns()
        header = dict((k, h) for k, h, _ in cols)["bo_valeur"]
        self.assertEqual(header, "Val Bounty")


class PkoPlusieursEliminationsTest(unittest.TestCase):
    """Section 3 : PKO à plusieurs éliminations, bounty qui grandit,
    joueur qui élimine plusieurs adversaires, élimination d'un joueur
    portant déjà une bounty augmentée."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "pko", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.c = self.db.add_player("Chris")
        self.d = self.db.add_player("Dan")
        # 5e joueur qui reste actif tout du long : évite que le tournoi ne
        # se termine (et donc qu'une clôture automatique de bounty finale,
        # testée séparément dans VictoryCollectTest, ne vienne perturber
        # les assertions ci-dessous qui portent uniquement sur la
        # progression/transmission normale d'une bounty PKO.
        self.e = self.db.add_player("Eve")

    def test_bounty_augmente_progressivement_puis_se_transmet(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)  # Alice: 100 -> 150 (50 payés, 50 grow)
        alice = self.db.get_player(self.a)
        self.assertEqual(alice["bounty"], 150)
        self.assertEqual(alice["bounty_won"], 50)

        self.db.eliminate_player(self.c, eliminated_by_id=self.a)  # Alice élimine un 2e : bounty=200
        alice = self.db.get_player(self.a)
        self.assertEqual(alice["bounty"], 200)
        self.assertEqual(alice["bounty_won"], 100)  # cumul de ses 2 kills
        self.assertEqual(alice["kills"], 2)

        # Alice (bounty déjà augmentée à 200) est éliminée par Dan.
        self.db.eliminate_player(self.a, eliminated_by_id=self.d)
        dan = self.db.get_player(self.d)
        self.assertEqual(dan["bounty"], 100 + 100)  # 100 initiale + 100 grow (200 x 50%)
        self.assertEqual(dan["bounty_won"], 100)  # 200 x 50% payés immédiatement
        alice = self.db.get_player(self.a)
        self.assertEqual(alice["bounty"], 0)  # transférée intégralement, plus rien sur elle


class MonBountyMoyBountyCalculTest(unittest.TestCase):
    """Section 3-4 : Mon Bounty = vrais gains PKO (pas Nb x valeur fixe),
    Moy Bounty = Mon Bounty / Nb Bounty arrondi, TOTAL inclut le vrai
    Mon Bounty PKO, sans double comptage."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "pko_calc", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.c = self.db.add_player("Chris")
        self.d = self.db.add_player("Dan")
        # 5e joueur toujours actif : évite qu'Alice ne devienne vainqueur
        # (et donc que sa bounty finale ne soit clôturée automatiquement,
        # testé séparément dans VictoryCollectTest) dans les tests qui
        # l'éliminent contre plusieurs adversaires.
        self.e = self.db.add_player("Eve")

    def test_mon_bounty_egale_les_vrais_gains_pko_pas_kills_fois_valeur_fixe(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)  # Alice encaisse 50
        self.db.eliminate_player(self.c, eliminated_by_id=self.a)  # Alice encaisse 50 de plus = 100
        row = next(r for r in self.db.get_bounty_bonuses() if r["name"] == "Alice")
        self.assertEqual(row["nombre"], 2)
        self.assertEqual(row["montant"], 100)  # bounty_won réel, PAS 2 x 100 = 200 (ancien calcul)
        self.assertNotEqual(row["montant"], row["nombre"] * 100)

    def test_moy_bounty_est_lamoyenne_arrondie_pas_une_valeur_fixe(self):
        # Alice élimine Bob (100 -> paie 50) puis Chris qui portait
        # entre-temps une bounty à 100 aussi (paie 50) : gains 50+50=100
        # sur 2 kills -> moyenne exacte 50.
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        self.db.eliminate_player(self.c, eliminated_by_id=self.a)
        row = next(r for r in self.db.get_bounty_bonuses() if r["name"] == "Alice")
        self.assertEqual(row["valeur"], round(row["montant"] / row["nombre"]))
        self.assertEqual(row["valeur"], 50)

    def test_moy_bounty_arrondie_bankers_rounding_convention_du_logiciel(self):
        # 3 kills à 50 pts chacun (bounty fixe 100 x 50% payés immédiat) =
        # 150 pts sur 3 kills -> moyenne exacte 50 (round() = même
        # convention déjà utilisée ailleurs dans le code, ex.
        # cash_part = round(bounty * pct / 100)) — Eve reste active, pas
        # de clôture automatique de bounty finale ici (voir setUp).
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)  # +50
        self.db.eliminate_player(self.c, eliminated_by_id=self.a)  # +50
        self.db.eliminate_player(self.d, eliminated_by_id=self.a)  # +50
        row = next(r for r in self.db.get_bounty_bonuses() if r["name"] == "Alice")
        self.assertEqual(row["nombre"], 3)
        self.assertEqual(row["montant"], 150)
        self.assertEqual(row["valeur"], round(150 / 3))

    def test_moy_bounty_zero_kill_ne_divise_pas_par_zero(self):
        row = next(r for r in self.db.get_bounty_bonuses() if r["name"] == "Dan")
        self.assertEqual(row["nombre"], 0)
        self.assertEqual(row["valeur"], 0)  # pas de ZeroDivisionError, affiché "-" côté UI

    def test_total_du_tableau_primes_utilise_le_vrai_mon_bounty_pko(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        summary = next(r for r in self.db.get_primes_summary() if r["name"] == "Alice")
        self.assertEqual(
            summary["total"],
            summary["presence"] + summary["assiduite"] + summary["cl_montant"] + summary["bo_montant"],
        )
        self.assertEqual(summary["bo_montant"], 50)  # pas l'ancien calcul kills x valeur fixe

    def test_bo_valeur_devient_moy_bounty_dans_primes_columns_en_pko(self):
        cols = self.db.primes_columns()
        header = dict((k, h) for k, h, _ in cols)["bo_valeur"]
        self.assertEqual(header, "Moy Bounty")


class VictoryCollectTest(unittest.TestCase):
    """Section 6 : récupération de la bounty finale du vainqueur, sans
    jamais augmenter Nb Bounty, tracée distinctement dans l'historique."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "victoire", bounty_amount=100, pko_mode=True, pko_cash_percent=40)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")

    def test_bounty_du_vainqueur_transferee_a_bounty_won_automatiquement(self):
        # Alice élimine Bob (avant-dernier) : Alice devient vainqueur avec
        # une bounty encore portée qui doit être clôturée automatiquement.
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        winner = self.db.get_player(self.a)
        self.assertEqual(winner["status"], "active")
        self.assertEqual(winner["bounty"], 0)  # clôturée : plus rien "en attente"
        # bounty_won = 40 (part immédiate du kill de Bob) + 160 (100 initiale
        # + 60 de grow, clôturés) = 200 = bounty totale injectée (2 x 100).
        self.assertEqual(winner["bounty_won"], 200)

    def test_nb_bounty_pas_augmente_par_la_cloture_du_vainqueur(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        winner = self.db.get_player(self.a)
        self.assertEqual(winner["kills"], 1)  # un seul vrai kill (Bob), pas 2

    def test_evenement_victory_collect_trace_distinctement_dans_historique(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        events = self.db.get_bounty_events()
        types = [e["event_type"] for e in events]
        self.assertIn("victory_collect", types)
        self.assertIn("elimination", types)
        victory_event = next(e for e in events if e["event_type"] == "victory_collect")
        self.assertEqual(victory_event["eliminated_name"], "Alice")
        self.assertIsNone(victory_event["eliminator_name"])  # jamais une élimination
        self.assertEqual(victory_event["amount_won"], 160)  # ce qui restait porté
        self.assertEqual(victory_event["added_to_eliminator_bounty"], 0)

    def test_cloture_est_idempotente_sans_effet_si_rappelee(self):
        self.db.eliminate_player(self.b, eliminated_by_id=self.a)
        before = dict(self.db.get_player(self.a))
        self.db._close_out_winner_bounty(self.a)  # rappel direct, ne doit rien changer
        after = dict(self.db.get_player(self.a))
        self.assertEqual(before["bounty_won"], after["bounty_won"])
        self.assertEqual(before["bounty"], after["bounty"])
        self.assertEqual(len(self.db.get_bounty_events()), 2)  # pas un 3e événement

    def test_cloture_absente_hors_pko(self):
        db2 = _new_db(self._tmp.name, "victoire_classique", bounty_amount=100, pko_mode=False)
        self.addCleanup(db2.conn.close)
        a2 = db2.add_player("Alice2")
        b2 = db2.add_player("Bob2")
        db2.eliminate_player(b2, eliminated_by_id=a2)
        winner = db2.get_player(a2)
        # Mode classique inchangé (section 2/6 hors périmètre) : Bob2
        # portait bien une bounty (100, transférée normalement en
        # 'elimination' classique, cash_part=bounty entier) mais la
        # bounty PROPRE d'Alice2 (sa bounty initiale à elle, jamais
        # transférée en mode classique) reste "en attente" sur elle —
        # aucune clôture automatique n'a lieu hors PKO.
        self.assertEqual(winner["bounty"], 100)  # sa propre bounty initiale, jamais clôturée
        self.assertEqual(winner["bounty_won"], 100)  # celle de Bob2, transférée normalement
        event_types = [e["event_type"] for e in db2.get_bounty_events()]
        self.assertEqual(event_types, ["elimination"])  # jamais de 'victory_collect' hors PKO


class OrphanBountyForbiddenTest(unittest.TestCase):
    """Section 7 : en PKO, une bounty > 0 ne doit jamais devenir orpheline
    — éliminateur obligatoire, ValueError sinon, aucune modification."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "orphelin", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.c = self.db.add_player("Chris")

    def test_refuse_sans_eliminateur_si_bounty_positive_en_pko(self):
        with self.assertRaises(ValueError):
            self.db.eliminate_player(self.b, eliminated_by_id=None)
        # Rien n'a été modifié : Bob toujours actif, toujours sa bounty.
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "active")
        self.assertEqual(bob["bounty"], 100)
        self.assertEqual(len(self.db.get_bounty_events()), 0)

    def test_refuse_avec_eliminateur_invalide_en_pko(self):
        with self.assertRaises(ValueError):
            self.db.eliminate_player(self.b, eliminated_by_id=999999)  # id inexistant
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "active")

    def test_autorise_sans_eliminateur_hors_pko(self):
        """Non-régression explicite : cette obligation ne s'applique
        JAMAIS hors PKO (préservation du fonctionnement existant)."""
        db2 = _new_db(self._tmp.name, "classique_sans_elim", bounty_amount=50, pko_mode=False)
        self.addCleanup(db2.conn.close)
        x = db2.add_player("X")
        y = db2.add_player("Y")
        db2.eliminate_player(y, eliminated_by_id=None)  # ne doit pas lever
        self.assertEqual(db2.get_player(y)["status"], "eliminated")

    def test_autorise_sans_eliminateur_en_pko_si_bounty_nulle(self):
        """Non-régression : l'obligation ne s'applique que si bounty > 0."""
        self.db.set_settings({"bounty_amount": "0"})
        db2 = _new_db(self._tmp.name, "pko_bounty_nulle", bounty_amount=0, pko_mode=True)
        self.addCleanup(db2.conn.close)
        x = db2.add_player("X")
        y = db2.add_player("Y")
        db2.eliminate_player(y, eliminated_by_id=None)  # bounty=0 : pas de risque d'orphelin
        self.assertEqual(db2.get_player(y)["status"], "eliminated")


class HistoriqueCoherenceTest(unittest.TestCase):
    """Section 5 : le tableau du bas (historique) et celui du haut
    (totaux) doivent être mathématiquement cohérents."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "historique", bounty_amount=80, pko_mode=True, pko_cash_percent=60)
        self.addCleanup(self.db.conn.close)
        self.players = {n: self.db.add_player(n) for n in ("P1", "P2", "P3", "P4", "P5")}

    def test_points_gagnes_correspond_a_la_part_immediate(self):
        self.db.eliminate_player(self.players["P5"], eliminated_by_id=self.players["P1"])
        ev = self.db.get_bounty_events()[0]
        self.assertEqual(ev["amount_won"], round(80 * 60 / 100))

    def test_ajoute_a_sa_prime_correspond_a_la_part_qui_grossit(self):
        self.db.eliminate_player(self.players["P5"], eliminated_by_id=self.players["P1"])
        ev = self.db.get_bounty_events()[0]
        self.assertEqual(ev["added_to_eliminator_bounty"], 80 - ev["amount_won"])

    def test_somme_historique_egale_bounty_won_du_tableau_du_haut(self):
        self.db.eliminate_player(self.players["P5"], eliminated_by_id=self.players["P1"])
        self.db.eliminate_player(self.players["P4"], eliminated_by_id=self.players["P1"])
        self.db.eliminate_player(self.players["P3"], eliminated_by_id=self.players["P2"])
        events_for_p1 = [
            e for e in self.db.get_bounty_events()
            if e["event_type"] == "elimination"
            and e["eliminator_name"] == "P1"
        ]
        total_p1_from_history = sum(e["amount_won"] for e in events_for_p1)
        p1_row = next(r for r in self.db.get_bounty_bonuses() if r["name"] == "P1")
        self.assertEqual(total_p1_from_history, p1_row["montant"])


class ExportsLibellesTest(unittest.TestCase):
    """Section 8-9 : plus aucune mention "€" liée au PKO/bounty ; les
    exports utilisent les vrais gains PKO."""

    def test_result_columns_prime_gagnee_en_points(self):
        header = dict((k, h) for k, h, _ in database.RESULT_COLUMNS)["bounty_won"]
        self.assertNotIn("€", header)
        self.assertIn("pts", header)

    def test_period_tournament_columns_primes_distribuees_en_points(self):
        header = dict((k, h) for k, h, _ in database.PERIOD_TOURNAMENT_COLUMNS)["bounty_distributed"]
        self.assertNotIn("€", header)
        self.assertIn("pts", header)

    def test_gain_hors_perimetre_reste_tel_quel(self):
        """Non-régression explicite : "Gain (€)" (grille de gains/buy-in,
        fonctionnalité séparée) n'est PAS concerné par cette correction."""
        header = dict((k, h) for k, h, _ in database.RESULT_COLUMNS)["gain"]
        self.assertIn("€", header)

    def test_exports_resultats_utilisent_les_vrais_gains_pko(self):
        with tempfile.TemporaryDirectory(prefix="pko_test_") as tmp:
            db = _new_db(tmp, "export_resultats", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
            try:
                a = db.add_player("Alice")
                b = db.add_player("Bob")
                db.eliminate_player(b, eliminated_by_id=a)  # Alice vainqueur, bounty clôturée
                rows = db._results_rows()
                alice_row = next(r for r in rows if r["name"] == "Alice")
                self.assertEqual(alice_row["bounty_won"], 200)  # 100 initiale + 100 gagnés sur Bob
            finally:
                db.conn.close()

    def test_synthese_par_periode_reflete_les_vrais_gains_pko(self):
        with tempfile.TemporaryDirectory(prefix="pko_test_") as tmp:
            db = _new_db(tmp, "synthese", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
            try:
                a = db.add_player("Alice")
                b = db.add_player("Bob")
                db.set_settings({"tournament_name": "Tournoi synthèse"})
                db.eliminate_player(b, eliminated_by_id=a)
            finally:
                db.conn.close()
            summary = database.build_period_summary(tmp, recursive=False)
            tournament = summary["tournaments"][0]
            self.assertEqual(tournament["bounty_distributed"], 200)
            alice_agg = next(p for p in summary["players"] if p["name"] == "Alice")
            self.assertEqual(alice_agg["total_bounty_won"], 200)


class LectureSeuleTournoiDejaTermineTest(unittest.TestCase):
    """Filet de sécurité en lecture seule pour un tournoi PKO déjà
    terminé AVANT ce correctif (bounty du vainqueur jamais clôturée en
    base) : l'affichage/export doit quand même être correct, sans jamais
    écrire (utilisable sur une connexion read_only)."""

    def test_get_bounty_bonuses_inclut_la_bounty_non_clôturee_du_vainqueur(self):
        with tempfile.TemporaryDirectory(prefix="pko_test_") as tmp:
            path = os.path.join(tmp, "ancien_tournoi.tournoi")
            db = database.Database(path)
            db.set_settings({"bounty_amount": "100", "pko_mode": "1", "pko_cash_percent": "50"})
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            # Simule l'ANCIEN comportement (avant ce correctif) : on
            # élimine Bob, puis on annule manuellement la clôture
            # automatique pour reproduire un tournoi qui aurait été joué
            # avec l'ancien code (bounty jamais transférée à bounty_won).
            db.eliminate_player(b, eliminated_by_id=a)
            db.conn.execute(
                "UPDATE players SET bounty=150, bounty_won=50 WHERE id=?", (a,)
            )
            db.conn.commit()
            db.conn.close()

            db_ro = database.Database(path, read_only=True)
            try:
                row = next(r for r in db_ro.get_bounty_bonuses() if r["name"] == "Alice")
                self.assertEqual(row["montant"], 200)  # 50 déjà encaissés + 150 encore portés
            finally:
                db_ro.close()

            # Vérifie qu'aucune écriture n'a eu lieu : le fichier rouvert
            # en écriture doit toujours montrer l'état "non clôturé".
            db_check = database.Database(path)
            try:
                alice = db_check.get_player(a)
                self.assertEqual(alice["bounty"], 150)
                self.assertEqual(alice["bounty_won"], 50)
            finally:
                db_check.conn.close()


class ConservationExacteSimulationTest(unittest.TestCase):
    """Section 10, invariant demandé : simulation complète SANS REBUY à
    plusieurs joueurs.

    points de bounty initiaux du tournoi
    = points PKO définitivement attribués + bounties encore portées par
      les joueurs actifs (vérifié à CHAQUE étape) ; et à la fin du
      tournoi : points de bounty initiaux = total des points PKO
      définitivement attribués (plus aucune bounty encore portée nulle
      part, le vainqueur ayant été clôturé)."""

    def test_invariant_verifie_a_chaque_etape_et_a_la_fin(self):
        with tempfile.TemporaryDirectory(prefix="pko_test_") as tmp:
            BOUNTY = 40
            N_PLAYERS = 6
            db = _new_db(tmp, "conservation", bounty_amount=BOUNTY, pko_mode=True, pko_cash_percent=35)
            try:
                names = [f"J{i}" for i in range(1, N_PLAYERS + 1)]
                ids = {n: db.add_player(n) for n in names}
                total_injected = N_PLAYERS * BOUNTY

                def assert_invariant():
                    players = db.list_players()
                    total_won = sum(p["bounty_won"] for p in players)
                    total_carried = sum(p["bounty"] for p in players)
                    self.assertEqual(total_won + total_carried, total_injected)

                assert_invariant()  # état initial, rien n'a encore bougé

                chain = [
                    ("J6", "J1"), ("J5", "J1"), ("J4", "J2"),
                    ("J3", "J1"), ("J2", "J1"),  # avant-dernier : J1 devient vainqueur
                ]
                for victim, killer in chain:
                    db.eliminate_player(ids[victim], eliminated_by_id=ids[killer])
                    assert_invariant()  # AUCUN point créé/perdu à aucune étape

                # Fin de tournoi : un seul actif, sa bounty a été clôturée
                # automatiquement (voir VictoryCollectTest) — invariant final.
                active = db.list_players(status="active")
                self.assertEqual(len(active), 1)
                self.assertEqual(active[0]["name"], "J1")
                self.assertEqual(active[0]["bounty"], 0)
                total_won_final = sum(p["bounty_won"] for p in db.list_players())
                self.assertEqual(total_won_final, total_injected)  # invariant final exact
                total_carried_final = sum(p["bounty"] for p in db.list_players())
                self.assertEqual(total_carried_final, 0)  # plus aucune bounty en attente nulle part
            finally:
                db.conn.close()

    def test_pas_de_double_comptage_sur_le_tableau_primes_complet(self):
        """La somme de 'Mon Bounty' sur TOUS les joueurs du tableau du
        haut doit correspondre exactement au total réellement attribué
        (bounty_won cumulé après clôture du vainqueur), sans rien de plus
        ni de moins."""
        with tempfile.TemporaryDirectory(prefix="pko_test_") as tmp:
            BOUNTY = 60
            db = _new_db(tmp, "double_comptage", bounty_amount=BOUNTY, pko_mode=True, pko_cash_percent=45)
            try:
                names = ["A", "B", "C", "D"]
                ids = {n: db.add_player(n) for n in names}
                db.eliminate_player(ids["D"], eliminated_by_id=ids["A"])
                db.eliminate_player(ids["C"], eliminated_by_id=ids["B"])
                db.eliminate_player(ids["B"], eliminated_by_id=ids["A"])  # avant-dernier, A vainqueur

                total_mon_bounty = sum(r["montant"] for r in db.get_bounty_bonuses())
                total_injected = 4 * BOUNTY
                self.assertEqual(total_mon_bounty, total_injected)
            finally:
                db.conn.close()


class _FakeApp:
    """Doublure de App : ne reprend que ce que _remote_eliminate/
    _remote_eliminate_request lisent ou appellent (même principe que
    tests/test_poll_voice_queue_stops_after_end_tournament.py) — self.db
    est une VRAIE Database sur un fichier .tournoi synthétique, tout le
    reste (bandeau, rééquilibrage, rafraîchissement) est un no-op.

    `test_mode` : conservé pour compatibilité avec les appels existants,
    mais SANS AUCUN EFFET sur _remote_eliminate depuis la correction du
    2026-09-09 (2e relecture utilisateur) — le Mode Test ne facilite que
    l'élimination GROUPÉE (_eliminate_selected, onglet Joueurs), un
    concept qui n'existe pas côté téléphone (toujours une seule
    élimination à la fois). Voir test_test_mode_est_sans_effet_sur_
    remote_eliminate ci-dessous, qui vérifie explicitement cette absence
    d'effet, et tests/test_test_mode_and_mandatory_eliminator.py pour la
    règle réellement appliquée (mandatory ⟺ primes activées ET bounty >
    0, indépendant du Mode Test)."""

    def __init__(self, db, test_mode=False):
        self.db = db
        self.voice_command_queue = queue.Queue()
        self._remote_elimination_results = {}
        self.clock_window = None  # écran projecteur : jamais ouvert dans ces tests
        self.test_mode = test_mode

    def _test_mode_enabled(self):
        return self.test_mode

    def _queue_elimination_banner(self, *a, **k):
        pass

    def _trigger_movement_alert(self, *a, **k):
        pass

    def _finish_movement_alert(self, *a, **k):
        pass

    def _refresh_all(self):
        pass

    def _refresh_remote_players_cache(self):
        pass

    def _refresh_remote_moves_cache(self):
        pass

    def _check_pending_rebalance(self):
        pass


class RemoteEliminatePkoOrphanTest(unittest.TestCase):
    """Demande complémentaire du 2026-09-08 : un refus d'élimination
    distante en PKO (bounty orpheline évitée) ne doit JAMAIS être
    silencieux — le téléphone doit recevoir un message explicite. Teste
    _remote_eliminate directement (logique métier, thread Tk) — voir
    RemoteEliminateRequestRoundTripTest ci-dessous pour le VRAI
    aller-retour file d'attente + sondage (_remote_eliminate_request,
    thread HTTP)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_remote_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "remote_pko", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.c = self.db.add_player("Chris")  # 3e joueur : évite que l'élimination de Bob ne termine le tournoi
        self.fake = _FakeApp(self.db)

    def test_1_eliminateur_valide_reussit(self):
        main.App._remote_eliminate(self.fake, self.b, self.a, request_id="req-ok")
        result = self.fake._remote_elimination_results["req-ok"]
        self.assertEqual(result, {"ok": True, "message": ""})
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "eliminated")
        alice = self.db.get_player(self.a)
        self.assertEqual(alice["bounty_won"], 50)  # transfert PKO normal, comportement inchangé

    def test_2_sans_eliminateur_refuse_et_joueur_reste_actif(self):
        main.App._remote_eliminate(self.fake, self.b, None, request_id="req-none")
        result = self.fake._remote_elimination_results["req-none"]
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["message"],
            "Élimination impossible : en mode PKO, vous devez désigner "
            "le joueur qui a éliminé ce joueur.",
        )
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "active")  # jamais éliminé
        self.assertEqual(bob["bounty"], 100)  # bounty intacte
        self.assertEqual(len(self.db.get_bounty_events()), 0)  # rien créé

    def test_3_eliminateur_invalide_refuse_et_joueur_reste_actif(self):
        """Éliminateur inexistant (id qui ne correspond à aucun joueur
        actif) — même traitement que "pas d'éliminateur du tout"."""
        main.App._remote_eliminate(self.fake, self.b, 999999, request_id="req-invalid")
        result = self.fake._remote_elimination_results["req-invalid"]
        self.assertFalse(result["ok"])
        self.assertIn("PKO", result["message"])
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "active")
        self.assertEqual(bob["bounty"], 100)
        self.assertEqual(len(self.db.get_bounty_events()), 0)

    def test_eliminateur_dune_autre_table_refuse_comme_invalide(self):
        """Même règle qu'_ask_eliminator (onglet Joueurs) : un joueur
        d'une autre table ne peut pas être désigné éliminateur — traité
        comme "pas d'éliminateur" en PKO, jamais silencieusement."""
        table2 = self.db.add_table("Table 2")
        self.db.conn.execute("UPDATE players SET table_id=? WHERE id=?", (table2, self.c))
        self.db.conn.commit()
        main.App._remote_eliminate(self.fake, self.b, self.c, request_id="req-autre-table")
        result = self.fake._remote_elimination_results["req-autre-table"]
        self.assertFalse(result["ok"])
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "active")

    def test_pas_de_refus_pko_si_bounty_nulle(self):
        """Non-régression : l'obligation d'éliminateur ne s'applique que
        si la bounty du joueur éliminé est > 0 (même hors éliminateur
        désigné) — vrai qu'importe le Mode Test, qui n'a plus aucun
        effet sur _remote_eliminate depuis la CORRECTION du 2026-09-09
        (2e relecture utilisateur : le Mode Test ne facilite que
        l'élimination GROUPÉE, un concept qui n'existe pas côté
        téléphone — voir tests/test_test_mode_and_mandatory_eliminator.py
        pour la couverture de la règle corrigée, indépendante de
        test_mode, et tests/test_primes_enabled_toggle.py pour la
        vérification structurelle que ce couplage a bien disparu)."""
        db2 = _new_db(self._tmp.name, "remote_pko_bounty_nulle", bounty_amount=0, pko_mode=True)
        self.addCleanup(db2.conn.close)
        x = db2.add_player("X")
        y = db2.add_player("Y")
        db2.add_player("Z")
        fake2 = _FakeApp(db2)
        main.App._remote_eliminate(fake2, y, None, request_id="req-nulle")
        result = fake2._remote_elimination_results["req-nulle"]
        self.assertTrue(result["ok"])
        self.assertEqual(db2.get_player(y)["status"], "eliminated")

    def test_test_mode_est_sans_effet_sur_remote_eliminate(self):
        """Le drapeau `test_mode` de _FakeApp (conservé dans cette classe
        pour compatibilité, voir sa docstring) ne doit plus influencer le
        résultat de _remote_eliminate, dans un sens comme dans l'autre —
        même bounty > 0 en PKO, le refus reste identique."""
        for test_mode in (False, True):
            with self.subTest(test_mode=test_mode):
                db2 = _new_db(
                    self._tmp.name, f"remote_pko_test_mode_{test_mode}",
                    bounty_amount=100, pko_mode=True,
                )
                self.addCleanup(db2.conn.close)
                db2.add_player("X")
                y = db2.add_player("Y")
                db2.add_player("Z")
                fake2 = _FakeApp(db2, test_mode=test_mode)
                main.App._remote_eliminate(fake2, y, None, request_id="req")
                result = fake2._remote_elimination_results["req"]
                self.assertFalse(result["ok"], f"test_mode={test_mode} ne doit rien changer")
                self.assertEqual(db2.get_player(y)["status"], "active")

    def test_garde_protegee_au_niveau_database_quel_que_soit_lappelant(self):
        """Vérifie que la protection ne dépend pas de _remote_eliminate :
        Database.eliminate_player lève ValueError elle-même si on tente
        de la contourner (appel direct, comme le ferait n'importe quel
        autre appelant futur) — filet de sécurité de dernier recours,
        voir aussi OrphanBountyForbiddenTest plus haut dans ce fichier."""
        with self.assertRaises(ValueError):
            self.db.eliminate_player(self.b, eliminated_by_id=None)
        bob = self.db.get_player(self.b)
        self.assertEqual(bob["status"], "active")


class RemoteEliminateRequestRoundTripTest(unittest.TestCase):
    """Vrai aller-retour file d'attente + sondage (_remote_eliminate_
    request, tel qu'appelé depuis le thread HTTP du téléphone) : un
    thread séparé simule _poll_voice_queue (thread Tk) pour prouver que
    le message explicite traverse bien tout le mécanisme, exactement
    comme en conditions réelles — pas seulement _remote_eliminate en
    isolation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pko_remote_rt_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "remote_rt", bounty_amount=100, pko_mode=True, pko_cash_percent=50)
        self.addCleanup(self.db.conn.close)
        self.a = self.db.add_player("Alice")
        self.b = self.db.add_player("Bob")
        self.db.add_player("Chris")
        self.fake = _FakeApp(self.db)

    def _call_remote_eliminate_request_in_background(self, eliminated_id, eliminator_id, out):
        """_remote_eliminate_request (thread HTTP réel) ne touche jamais
        self.db (uniquement la file + un dict) : c'est donc LUI qui tourne
        dans le thread séparé ici, comme en conditions réelles. C'est
        _remote_eliminate (qui touche self.db, SQLite) qui doit rester sur
        le thread ayant créé la connexion — le thread de CE test, voir
        ci-dessous — sqlite3 refuse tout accès cross-thread à une même
        connexion, exactement la contrainte qui a motivé toute cette
        architecture par file d'attente dans l'application réelle."""
        out["result"] = main.App._remote_eliminate_request(self.fake, eliminated_id, eliminator_id)

    def test_4_reponse_explicite_recue_par_le_thread_appelant_cote_succes(self):
        out = {}
        worker = threading.Thread(
            target=self._call_remote_eliminate_request_in_background, args=(self.b, self.a, out),
        )
        worker.start()
        item = self.fake.voice_command_queue.get(timeout=2)
        _, eliminated_id, eliminator_id, request_id = item
        main.App._remote_eliminate(self.fake, eliminated_id, eliminator_id, request_id=request_id)
        worker.join(timeout=2)
        self.assertEqual(out["result"], {"ok": True, "message": ""})
        self.assertEqual(self.db.get_player(self.b)["status"], "eliminated")

    def test_4_reponse_explicite_recue_par_le_thread_appelant_cote_refus(self):
        """Le dict {"ok": False, "message": "..."} renvoyé par
        _remote_eliminate_request est EXACTEMENT ce que remote_control.py
        renvoie ensuite tel quel au téléphone (voir do_POST /eliminate) —
        donc directement exploitable/affichable côté client."""
        out = {}
        worker = threading.Thread(
            target=self._call_remote_eliminate_request_in_background, args=(self.b, None, out),
        )
        worker.start()
        item = self.fake.voice_command_queue.get(timeout=2)
        _, eliminated_id, eliminator_id, request_id = item
        main.App._remote_eliminate(self.fake, eliminated_id, eliminator_id, request_id=request_id)
        worker.join(timeout=2)
        result = out["result"]
        self.assertFalse(result["ok"])
        self.assertTrue(result["message"])  # jamais vide : toujours affichable
        self.assertIn("PKO", result["message"])
        self.assertEqual(self.db.get_player(self.b)["status"], "active")  # rien modifié

    def test_timeout_ne_bloque_pas_indefiniment_si_rien_ne_consomme_la_file(self):
        """Filet de sécurité : si le thread Tk ne traite jamais la
        demande (situation anormale), _remote_eliminate_request doit
        quand même finir par répondre plutôt que de bloquer le thread
        HTTP indéfiniment."""
        import time as _time
        started = _time.monotonic()
        result = main.App._remote_eliminate_request(self.fake, self.b, self.a)
        elapsed = _time.monotonic() - started
        self.assertFalse(result["ok"])
        self.assertTrue(result["message"])
        self.assertLess(elapsed, 5.0)  # borné, pas un blocage infini


if __name__ == "__main__":
    unittest.main()
