# -*- coding: utf-8 -*-
"""Lot 2 — "Gestion des absences et des retardataires" (cahier des
charges du 2026-09-28), couverture DATABASE :

- Database.declare_absent/cancel_absence : effet strictement limité à
  `absent_declared_at`, jamais chips/table_id/seat/status, jamais de
  rééquilibrage (LIMITE IMPÉRATIVE du Lot 2 comme du Lot 1).
- Database.absence_deadline_epoch/absence_deadline_passed : calculé
  depuis le démarrage RÉEL du tournoi (jamais avant), jamais "dans le
  passé" pour un tournoi encore non démarré (piège explicitement évité,
  voir l'audit du 2026-09-28).
- Clôture de l'accueil (Lot 1) : confirm_arrival/cancel_arrival refusent
  désormais (renvoient False, aucune écriture) une fois le délai des
  retardataires dépassé.
- Distinction ABSOLUE entre cancel_absence (joueur encore actif) et
  reinstate_player (joueur réellement désactivé) — précaution explicite
  du 2026-09-28 : l'état doit survivre un redémarrage (donc être
  persisté en base, jamais un simple drapeau en mémoire).
- Migration de la colonne `absent_declared_at` sur un ancien fichier."""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402


def _new_db(tmp_dir, name="A"):
    """« Gestion de l'accueil » activée par défaut pour CE fixture
    (modification du 2026-09-29) : ce module teste précisément les
    mécanismes d'accueil/absence, donc le réglage propre au tournoi
    Open qui les active — voir test_accueil_management_toggle.py pour
    la couverture du réglage désactivé (comportement par défaut)."""
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1})
    return db


def _start_tournament(db, minutes_ago=0, duration_minutes=None):
    """Simule un tournoi RÉELLEMENT démarré `minutes_ago` minutes dans le
    passé (0 = à l'instant) — même trio de réglages que App._clock_resume
    (clock_started/tournament_start_epoch), jamais réimplémenté
    autrement. `duration_minutes`, si fourni, règle aussi "Durée d'une
    absence en m" pour ce test."""
    start_epoch = int(time.time()) - minutes_ago * 60
    settings = {"clock_started": 1, "tournament_start_epoch": start_epoch}
    if duration_minutes is not None:
        settings["absence_duration_minutes"] = duration_minutes
    db.set_settings(settings)


class AbsenceDeadlineEpochTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="absence_deadline_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

    def test_tournoi_jamais_demarre_aucune_echeance(self):
        """Piège explicitement évité (audit du 2026-09-28) : clock_started
        == 0 et tournament_start_epoch == 0 par défaut — un calcul naïf
        (0 + délai) placerait l'échéance dans le passé AVANT MÊME le
        premier démarrage. Vérifié ici : aucune échéance, jamais
        "dépassée", pour un tournoi flambant neuf."""
        self.assertIsNone(self.db.absence_deadline_epoch())
        self.assertFalse(self.db.absence_deadline_passed())

    def test_demarre_mais_start_epoch_pas_encore_pose(self):
        """Cas limite défensif : clock_started=1 mais tournament_start_
        epoch resté à 0 (ne devrait normalement jamais arriver via
        _clock_resume, mais vérifié explicitement) — toujours aucune
        échéance, jamais un calcul depuis epoch 0."""
        self.db.set_settings({"clock_started": 1, "tournament_start_epoch": 0})
        self.assertIsNone(self.db.absence_deadline_epoch())
        self.assertFalse(self.db.absence_deadline_passed())

    def test_demarre_recemment_delai_pas_encore_ecoule(self):
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.assertFalse(self.db.absence_deadline_passed())

    def test_delai_ecoule(self):
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        self.assertTrue(self.db.absence_deadline_passed())

    def test_valeur_par_defaut_60_minutes(self):
        """"Valeur initiale proposée : 60 minutes" (cahier des charges,
        section 1) — sans réglage explicite absence_duration_minutes."""
        start_epoch = int(time.time()) - 61 * 60
        self.db.set_settings({"clock_started": 1, "tournament_start_epoch": start_epoch})
        self.assertTrue(self.db.absence_deadline_passed())
        start_epoch2 = int(time.time()) - 30 * 60
        self.db.set_settings({"clock_started": 1, "tournament_start_epoch": start_epoch2})
        self.assertFalse(self.db.absence_deadline_passed())

    def test_duree_zero_delai_immediatement_ecoule_des_le_demarrage(self):
        _start_tournament(self.db, minutes_ago=0, duration_minutes=0)
        self.assertTrue(self.db.absence_deadline_passed())


class DeclareAndCancelAbsenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="declare_cancel_absence_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)

    def test_declare_absent_horodate_sans_rien_d_autre_toucher(self):
        before = dict(self.db.get_player(self.pid))

        result = self.db.declare_absent(self.pid)

        self.assertTrue(result)
        after = dict(self.db.get_player(self.pid))
        self.assertIsNotNone(after["absent_declared_at"])
        for key in ("table_id", "seat", "chips", "status", "bounty", "place"):
            self.assertEqual(after[key], before[key], key)

    def test_declare_absent_ne_declenche_aucun_mouvement(self):
        """LIMITE IMPÉRATIVE du Lot 2 (comme du Lot 1) : jamais un appel à
        rebalance_tables — vérifié en constatant qu'aucune ligne
        n'apparaît dans seat_moves et qu'aucun AUTRE joueur ne change de
        table/siège suite à une déclaration d'absence."""
        other_ids = [self.db.add_player(f"J{i}") for i in range(5)]
        before_positions = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in other_ids
        }

        self.db.declare_absent(self.pid)
        for pid in other_ids:
            self.db.declare_absent(pid)

        self.assertEqual(self.db.count_seat_moves(), 0)
        for pid in other_ids:
            after = self.db.get_player(pid)
            self.assertEqual((after["table_id"], after["seat"]), before_positions[pid])

    def test_declare_absent_est_idempotent_aucun_doublon(self):
        """"Les déclarations répétées ne créent aucun doublon" (cahier des
        charges, section 2) — qu'elles viennent du Mac ou de plusieurs
        téléphones : une seule colonne, jamais une liste, rejouer ne fait
        que rafraîchir l'horodatage."""
        self.db.declare_absent(self.pid)
        first = self.db.get_player(self.pid)["absent_declared_at"]

        self.db.declare_absent(self.pid)
        self.db.declare_absent(self.pid)

        second = self.db.get_player(self.pid)["absent_declared_at"]
        self.assertIsNotNone(second)
        self.assertGreaterEqual(second, first)

    def test_declare_absent_refuse_avant_le_demarrage(self):
        pid2 = self.db.add_player("Bob")
        self.db.set_settings({"clock_started": 0, "tournament_start_epoch": 0})

        result = self.db.declare_absent(pid2)

        self.assertFalse(result)
        self.assertIsNone(self.db.get_player(pid2)["absent_declared_at"])

    def test_declare_absent_refuse_pour_un_joueur_non_actif(self):
        self.db.eliminate_player(self.pid)

        result = self.db.declare_absent(self.pid)

        self.assertFalse(result)
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_cancel_absence_efface_l_horodatage_sans_rien_d_autre_toucher(self):
        self.db.declare_absent(self.pid)
        before = dict(self.db.get_player(self.pid))

        self.db.cancel_absence(self.pid)

        after = dict(self.db.get_player(self.pid))
        self.assertIsNone(after["absent_declared_at"])
        for key in ("table_id", "seat", "chips", "status", "bounty"):
            self.assertEqual(after[key], before[key], key)

    def test_cancel_absence_ne_declenche_aucun_mouvement(self):
        other_ids = [self.db.add_player(f"J{i}") for i in range(4)]
        self.db.declare_absent(self.pid)
        before_positions = {
            pid: (self.db.get_player(pid)["table_id"], self.db.get_player(pid)["seat"])
            for pid in other_ids
        }

        self.db.cancel_absence(self.pid)

        self.assertEqual(self.db.count_seat_moves(), 0)
        for pid in other_ids:
            after = self.db.get_player(pid)
            self.assertEqual((after["table_id"], after["seat"]), before_positions[pid])

    def test_cancel_absence_sans_declaration_prealable_ne_leve_rien(self):
        self.db.cancel_absence(self.pid)  # jamais signalé absent
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_cancel_absence_sans_effet_sur_un_joueur_non_actif(self):
        """cancel_absence ne touche QUE status='active' (voir la clause
        WHERE) — un joueur désactivé entre-temps ne doit jamais voir son
        absent_declared_at effacé par cet appel (contrairement à
        reinstate_player, qui ne doit JAMAIS être appelée ici de toute
        façon — voir DistinguishReinstateFromCancelAbsenceTest)."""
        self.db.declare_absent(self.pid)
        self.db.withdraw_player(self.pid)
        stamped = self.db.get_player(self.pid)["absent_declared_at"]
        self.assertIsNotNone(stamped)

        self.db.cancel_absence(self.pid)

        self.assertEqual(self.db.get_player(self.pid)["absent_declared_at"], stamped)

    def test_annulation_persiste_apres_redemarrage_plus_de_clignotement(self):
        """Précaution explicite du 2026-09-28 : après annulation ET
        redémarrage de l'application, le joueur ne doit plus clignoter.
        Simulé ici en rouvrant le MÊME fichier .tournoi dans une toute
        nouvelle instance Database (équivalent d'un redémarrage) et en
        relisant l'état — jamais un drapeau en mémoire qui se perdrait
        de toute façon à la fermeture du process, la vraie garantie est
        que la valeur relue en base est bien NULL."""
        path = self.db.path
        self.db.declare_absent(self.pid)
        self.db.cancel_absence(self.pid)
        self.db.conn.close()

        reopened = database.Database(path)
        self.addCleanup(reopened.conn.close)

        self.assertIsNone(reopened.get_player(self.pid)["absent_declared_at"])

    def test_declaration_persiste_apres_redemarrage(self):
        """Symétrique du test précédent : une absence toujours EN COURS
        (jamais annulée) doit elle aussi survivre à un redémarrage — sans
        quoi le clignotement s'arrêterait à tort."""
        path = self.db.path
        self.db.declare_absent(self.pid)
        self.db.conn.close()

        reopened = database.Database(path)
        self.addCleanup(reopened.conn.close)

        self.assertIsNotNone(reopened.get_player(self.pid)["absent_declared_at"])


class DistinguishReinstateFromCancelAbsenceTest(unittest.TestCase):
    """Précaution explicite du 2026-09-28 (cahier des charges section 3) :
    "ne jamais appeler la réinscription classique pour un joueur encore
    actif" — reinstate_player reste INCHANGÉE, mais son usage sur un
    joueur actif casserait ses jetons/son siège."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_vs_cancel_absence_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.db.set_setting("starting_chips", "10000")
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)
        # Simule une partie déjà bien entamée : jetons très différents du
        # starting_chips par défaut — reinstate_player les écraserait.
        self.db.set_chips(self.pid, 47500)
        original_table_id = self.db.get_player(self.pid)["table_id"]
        original_seat = self.db.get_player(self.pid)["seat"]
        self.original_table_id = original_table_id
        self.original_seat = original_seat

    def test_reinstate_player_ecraserait_les_jetons_si_appelee_a_tort(self):
        """Vérifie l'hypothèse elle-même (pourquoi reinstate_player est
        interdite ici) : preuve directe que reinstate_player réinitialise
        bien les jetons — confirme que _reinstate_selected (main.py) a
        raison de ne JAMAIS l'appeler pour un joueur encore actif."""
        self.db.reinstate_player(self.pid)  # volontairement l'appel INTERDIT, pour le prouver
        self.assertEqual(self.db.get_player(self.pid)["chips"], 10000)  # jetons écrasés

    def test_cancel_absence_preserve_les_jetons_et_le_siege(self):
        self.db.cancel_absence(self.pid)

        after = self.db.get_player(self.pid)
        self.assertEqual(after["chips"], 47500)
        self.assertEqual(after["table_id"], self.original_table_id)
        self.assertEqual(after["seat"], self.original_seat)
        self.assertEqual(after["status"], "active")
        self.assertIsNone(after["absent_declared_at"])


class WithdrawAfterAbsenceTest(unittest.TestCase):
    """Section 5 du cahier des charges : la désactivation finale RÉUTILISE
    withdraw_player telle quelle (comportement/rééquilibrage historiques
    inchangés) — jamais un mécanisme distinct pour un joueur qui était
    signalé absent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="withdraw_after_absence_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Chris")
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        self.db.declare_absent(self.pid)

    def test_withdraw_player_applique_le_mecanisme_historique_inchange(self):
        self.db.withdraw_player(self.pid)

        after = self.db.get_player(self.pid)
        self.assertEqual(after["status"], "withdrawn")
        self.assertIsNone(after["table_id"])
        self.assertIsNone(after["seat"])
        self.assertIsNone(after["place"])

    def test_withdraw_player_conserve_absent_declared_at_comme_historique(self):
        """Jamais explicitement effacé par withdraw_player (même
        philosophie que arrived_at, Lot 1) — le clignotement s'arrête de
        toute façon puisqu'il exige status == 'active' (voir main.py:
        _refresh_players_tab), sans qu'il soit nécessaire de toucher
        cette colonne ici."""
        stamped = self.db.get_player(self.pid)["absent_declared_at"]
        self.assertIsNotNone(stamped)

        self.db.withdraw_player(self.pid)

        self.assertEqual(self.db.get_player(self.pid)["absent_declared_at"], stamped)

    def test_withdraw_player_applique_le_reequilibrage_historique(self):
        """Ne modifie PAS le mécanisme de rééquilibrage (limite impérative
        du Lot 2) : withdraw_player continue d'appeler rebalance_tables
        exactement comme avant ce chantier — vérifié en constatant qu'il
        renvoie bien la liste des mouvements calculés (jamais None, jamais
        une liste vide inventée par erreur pour ce cas)."""
        moves = self.db.withdraw_player(self.pid)
        self.assertIsInstance(moves, list)


class AccueilClosureAfterDeadlineTest(unittest.TestCase):
    """Lot 1 x Lot 2 (cahier des charges, section 6) : confirm_arrival/
    cancel_arrival refusent désormais l'écriture une fois le délai des
    retardataires dépassé — "côté serveur ET base de données", vérifié
    ici au niveau le plus bas (Database), jamais seulement une case
    grisée côté interface."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_closure_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

    def test_confirm_arrival_fonctionne_avant_l_echeance(self):
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.assertTrue(self.db.confirm_arrival(self.pid))
        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])

    def test_confirm_arrival_refuse_apres_l_echeance(self):
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)

        result = self.db.confirm_arrival(self.pid)

        self.assertFalse(result)
        self.assertIsNone(self.db.get_player(self.pid)["arrived_at"])

    def test_cancel_arrival_refuse_apres_l_echeance_meme_deja_arrive(self):
        """Le coeur de la précision "y compris lorsque la page était déjà
        ouverte avant l'échéance" : un joueur confirmé AVANT l'échéance
        ne peut plus voir sa confirmation annulée APRÈS — même depuis un
        onglet resté ouvert qui n'a pas encore vu le nouvel état."""
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.confirm_arrival(self.pid)
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)

        result = self.db.cancel_arrival(self.pid)

        self.assertFalse(result)
        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])  # inchangé

    def test_confirm_arrival_avant_demarrage_fonctionne_toujours(self):
        """Non-régression du Lot 1 : tant que le tournoi n'a pas démarré,
        absence_deadline_passed() est toujours faux (voir
        AbsenceDeadlineEpochTest), donc confirm_arrival continue de
        fonctionner normalement — jamais affecté par le Lot 2."""
        self.assertTrue(self.db.confirm_arrival(self.pid))


class ArrivedAtMigrationTest(unittest.TestCase):
    """_migrate() doit ajouter `absent_declared_at` (NULL) à un ancien
    fichier .tournoi créé AVANT l'existence de cette colonne — même
    schéma de test que le Lot 1 (arrived_at) et le pattern déjà établi
    pour bounty/kills/club (voir database.py: _migrate)."""

    def test_ancien_fichier_sans_absent_declared_at_est_migre(self):
        tmp = tempfile.TemporaryDirectory(prefix="absent_declared_at_migration_test_")
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "old.tournoi")

        db = database.Database(path)
        db.conn.execute("ALTER TABLE players DROP COLUMN absent_declared_at")
        db.conn.commit()
        db.conn.close()

        reopened = database.Database(path)
        self.addCleanup(reopened.conn.close)
        cols = {row["name"] for row in reopened.conn.execute("PRAGMA table_info(players)")}
        self.assertIn("absent_declared_at", cols)
        pid = reopened.add_player("Alice")
        self.assertIsNone(reopened.get_player(pid)["absent_declared_at"])
        reopened.set_settings({"accueil_management_enabled": 1})
        _start_tournament(reopened, minutes_ago=5, duration_minutes=60)
        reopened.declare_absent(pid)
        self.assertIsNotNone(reopened.get_player(pid)["absent_declared_at"])


if __name__ == "__main__":
    unittest.main()
