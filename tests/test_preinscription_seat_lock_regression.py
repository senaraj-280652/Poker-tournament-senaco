# -*- coding: utf-8 -*-
"""Verrou de non-régression (demande du 2026-10-03, "IMPRESSION JOUEURS
/ PLACES FIGÉES / PSEUDO / HU SANS ANTE", point 2 "places figées") —
AUCUNE modification de la logique de placement/siège n'a été faite pour
ce chantier : le diagnostic préalable a confirmé que le maintien de la
table et du siège d'un joueur pré-inscrit à travers toute confirmation
d'arrivée est DÉJÀ garanti par le code existant. Ce fichier ajoute
UNIQUEMENT les tests de non-régression explicitement demandés pour
protéger ce comportement dans la durée — aucun comportement nouveau.

Rappel du principe vérifié ICI (déjà documenté dans les docstrings de
Database.confirm_arrival / cancel_arrival / correct_arrival_
confirmation, voir database.py) : la seule opération qui attribue une
table et un siège est Database.add_player (via _seat_player +
rebalance_tables), appelée UNE SEULE FOIS à l'inscription. Aucune des
primitives de confirmation d'arrivée n'appelle jamais rebalance_tables
ni _seat_player, qu'elles soient déclenchées :
- depuis le Mac (onglet Joueurs, bouton "Inscrire" -> App.
  _reinstate_selected -> Database.confirm_arrival) ;
- depuis le téléphone (page Accueil -> App._remote_confirm_arrival ->
  Database.confirm_arrival) ;
- par une correction DIRTO (App._on_correct_arrival_clicked -> Database.
  correct_arrival_confirmation) ;
- par le démarrage du tournoi (App._clock_resume, qui ne fait que
  décocher les cases et poser des réglages d'horloge, jamais
  table_id/seat) ;
- par un signalement d'absence suivi d'un retour (Database.
  declare_absent puis confirm_arrival, le cas "retour d'un joueur
  absent" du 2026-09-29).

Chaque test ci-dessous vérifie à la fois :
(a) le joueur concerné garde exactement sa table_id/seat d'origine ;
(b) AUCUN autre joueur actif ne bouge (snapshot complet avant/après) ;
(c) Database.rebalance_tables n'est jamais appelée (espion sur la
    méthode réelle, pas seulement une vérification indirecte via
    count_seat_moves)."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402


def _new_db(tmp_dir, name="A", accueil_enabled=True):
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1 if accueil_enabled else 0})
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


def _snapshot_positions(db):
    """Table_id/seat de TOUS les joueurs actifs, par id — utilisé pour
    vérifier qu'une confirmation d'arrivée ne déplace jamais personne
    d'autre que (éventuellement, et même pour lui ce n'est jamais le
    cas) le joueur concerné."""
    return {
        p["id"]: (p["table_id"], p["seat"])
        for p in db.list_players(status="active")
    }


class _FakeAppForReinstate:
    """Doublure minimale pour App._reinstate_selected (même principe que
    tests/test_reinstate_preinscrit_extension.py, reprise à l'identique
    pour garder ce fichier indépendant). Correctif du 2026-10-03
    ("Inscrire ignore le joueur sélectionné quand un autre est déjà
    accueilli") : _reinstate_selected lit désormais players_tree.
    selection() directement quand l'accueil pilote la case — exposé ici
    (mêmes _ids que _checked_or_selected_ids, ce conflit n'étant pas
    l'objet de ces tests de verrouillage des places, voir tests/
    test_inscrire_ignore_deja_accueillis.py pour celui-ci) avec la
    méthode réelle greffée pour refléter le vrai réglage accueil/
    démarrage."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)
        self.players_tree = types.SimpleNamespace(
            selection=lambda: [str(pid) for pid in self._ids]
        )

    def _players_checkbox_drives_accueil(self):
        return main.App._players_checkbox_drives_accueil(self)

    def _checked_or_selected_ids(self):
        return list(self._ids)

    def _clear_checked(self):
        pass

    def _refresh_all(self):
        pass

    def _refresh_remote_players_cache(self):
        pass

    def _refresh_remote_checkin_cache(self):
        pass


class _FakeAppForRemoteConfirm:
    """Doublure minimale pour App._remote_confirm_arrival (même principe
    que tests/test_remote_confirm_arrival.py)."""

    def __init__(self, db):
        self.db = db
        self._remote_arrival_results = {}

    def _refresh_remote_checkin_cache(self):
        pass

    def _sync_checkbox_for_arrival(self, player_id, arrived):
        pass


class _FakeAppForClockResume:
    """Doublure minimale pour App._clock_resume — suffisamment de surface
    pour exécuter le chemin de démarrage sans UI réelle (mêmes attributs
    que les autres doublures _clock_resume de cette suite)."""

    def __init__(self, db):
        self.db = db

    def _uncheck_all_players(self):
        pass

    def _refresh_clock_tab(self):
        pass


# =====================================================================
# 1. Database.confirm_arrival — table/siège figés, aucun rééquilibrage
# =====================================================================
class ConfirmArrivalSeatLockDatabaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_confirm_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)

    def test_joueur_garde_sa_table_et_son_siege(self):
        pid = self.db.add_player("Alice")
        before = self.db.get_player(pid)

        self.assertTrue(self.db.confirm_arrival(pid))

        after = self.db.get_player(pid)
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])
        self.assertEqual(after["chips"], before["chips"])

    def test_aucun_autre_joueur_ne_bouge(self):
        pid_alice = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Charlie")
        before = _snapshot_positions(self.db)

        self.db.confirm_arrival(pid_alice)

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)

    def test_rebalance_tables_jamais_appele(self):
        pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        seat_moves_before = self.db.count_seat_moves()

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            self.db.confirm_arrival(pid)
            spy.assert_not_called()

        self.assertEqual(self.db.count_seat_moves(), seat_moves_before)

    def test_idempotent_double_confirmation_ne_deplace_personne(self):
        """Un double-tap ou un retry réseau depuis le téléphone ne doit
        jamais déclencher de mouvement de siège (voir la docstring de
        confirm_arrival)."""
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        before = self.db.get_player(pid)

        self.db.confirm_arrival(pid)

        after = self.db.get_player(pid)
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])


# =====================================================================
# 2. Database.cancel_arrival — même garantie
# =====================================================================
class CancelArrivalSeatLockDatabaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_cancel_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)

    def test_joueur_garde_sa_table_et_son_siege(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        before = self.db.get_player(pid)

        self.assertTrue(self.db.cancel_arrival(pid))

        after = self.db.get_player(pid)
        self.assertIsNone(after["arrived_at"])
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])

    def test_aucun_autre_joueur_ne_bouge(self):
        pid_alice = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.confirm_arrival(pid_alice)
        before = _snapshot_positions(self.db)

        self.db.cancel_arrival(pid_alice)

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)

    def test_rebalance_tables_jamais_appele(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            self.db.cancel_arrival(pid)
            spy.assert_not_called()


# =====================================================================
# 3. Database.correct_arrival_confirmation — même garantie (DIRTO)
# =====================================================================
class CorrectArrivalConfirmationSeatLockDatabaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_correct_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)

    def test_joueur_garde_sa_table_et_son_siege(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        before = self.db.get_player(pid)

        self.assertTrue(self.db.correct_arrival_confirmation(pid))

        after = self.db.get_player(pid)
        self.assertIsNone(after["arrived_at"])
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])

    def test_fonctionne_meme_apres_expiration_du_delai_sans_deplacer_personne(self):
        """correct_arrival_confirmation reste volontairement possible
        après l'expiration du délai des retardataires (voir sa
        docstring) — vérifie que même dans ce cas limite, aucun
        déplacement de siège n'a lieu."""
        pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.confirm_arrival(pid)  # confirmé AVANT l'expiration du délai
        # Le délai des retardataires expire ENSUITE (retardé après coup) :
        # correct_arrival_confirmation doit rester possible malgré tout.
        _start_tournament(self.db, minutes_ago=90, duration_minutes=60)
        before = _snapshot_positions(self.db)

        self.assertTrue(self.db.correct_arrival_confirmation(pid))

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)

    def test_rebalance_tables_jamais_appele(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            self.db.correct_arrival_confirmation(pid)
            spy.assert_not_called()


# =====================================================================
# 4. Chemin Mac ("Inscrire", App._reinstate_selected) — places figées
# =====================================================================
class MacReinstateSeatLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_mac_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)

    def test_confirmation_depuis_le_mac_ne_deplace_ni_le_joueur_ni_les_autres(self):
        pid_alice = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Charlie")
        before = _snapshot_positions(self.db)
        app = _FakeAppForReinstate(self.db, [pid_alice])

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            main.App._reinstate_selected(app)
            spy.assert_not_called()

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)
        self.assertIsNotNone(self.db.get_player(pid_alice)["arrived_at"])

    def test_retour_dun_joueur_signale_absent_ne_deplace_personne(self):
        """Cas explicitement cité par la demande initiale : un joueur
        signalé absent puis "Inscrire" depuis le Mac (retour, correctif
        du 2026-09-29) garde sa table/son siège, comme les autres."""
        pid_alice = self.db.add_player("Alice")
        pid_bob = self.db.add_player("Bob")
        self.db.declare_absent(pid_alice)
        before = _snapshot_positions(self.db)
        app = _FakeAppForReinstate(self.db, [pid_alice])

        main.App._reinstate_selected(app)

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)
        alice = self.db.get_player(pid_alice)
        self.assertIsNotNone(alice["arrived_at"])
        self.assertIsNone(alice["absent_declared_at"])
        self.assertEqual(self.db.get_player(pid_bob)["table_id"], before[pid_bob][0])


# =====================================================================
# 5. Chemin téléphone (App._remote_confirm_arrival) — places figées
# =====================================================================
class RemoteConfirmArrivalSeatLockTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_remote_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)

    def test_confirmation_depuis_liphone_ne_deplace_ni_le_joueur_ni_les_autres(self):
        pid_alice = self.db.add_player("Alice")
        self.db.add_player("Bob")
        before = _snapshot_positions(self.db)
        app = _FakeAppForRemoteConfirm(self.db)

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            main.App._remote_confirm_arrival(app, pid_alice, request_id="req1")
            spy.assert_not_called()

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)
        self.assertTrue(app._remote_arrival_results["req1"]["ok"])
        self.assertIsNotNone(self.db.get_player(pid_alice)["arrived_at"])


# =====================================================================
# 6. Démarrage du tournoi (App._clock_resume) — ne reseate personne
# =====================================================================
class ClockResumeDoesNotReseatTest(unittest.TestCase):
    """Le démarrage (bouton Démarrer/Reprendre) décoche les cases et pose
    les réglages d'horloge (voir App._clock_resume) — il ne doit JAMAIS
    toucher table_id/seat, qu'un joueur soit déjà arrivé, pré-inscrit ou
    signalé absent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_clock_resume_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

    def test_demarrage_ne_deplace_aucun_joueur(self):
        pid_arrived = self.db.add_player("Alice")
        self.db.confirm_arrival(pid_arrived)
        pid_preinscrit = self.db.add_player("Bob")
        pid_absent = self.db.add_player("Charlie")
        self.db.declare_absent(pid_absent)
        before = _snapshot_positions(self.db)
        app = _FakeAppForClockResume(self.db)

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            main.App._clock_resume(app)
            spy.assert_not_called()

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)
        self.assertEqual(self.db.get_setting_int("clock_started", 0), 1)

    def test_demarrage_ne_touche_ni_arrived_at_ni_absent_declared_at(self):
        """Précision du cahier des charges (2026-09-28) : les
        confirmations d'arrivée restent intactes en base après le
        démarrage, seule la case à cocher de l'onglet Joueurs cesse
        d'être pilotée par l'accueil."""
        pid_arrived = self.db.add_player("Alice")
        self.db.confirm_arrival(pid_arrived)
        pid_absent = self.db.add_player("Bob")
        self.db.declare_absent(pid_absent)
        before_arrived = self.db.get_player(pid_arrived)["arrived_at"]
        before_absent = self.db.get_player(pid_absent)["absent_declared_at"]
        app = _FakeAppForClockResume(self.db)

        main.App._clock_resume(app)

        self.assertEqual(self.db.get_player(pid_arrived)["arrived_at"], before_arrived)
        self.assertEqual(self.db.get_player(pid_absent)["absent_declared_at"], before_absent)


# =====================================================================
# 7. Scénario combiné multi-tables — snapshot complet avant/après
# =====================================================================
class MultiTableCombinedScenarioSeatLockTest(unittest.TestCase):
    """Reproduit un enchaînement réaliste (accueil, quelques arrivées,
    une absence puis un retour, une correction DIRTO) sur plusieurs
    tables, et vérifie qu'à AUCUN moment la table/le siège de QUI QUE CE
    SOIT ne change — seule add_player (à l'inscription, avant ce
    scénario) attribue des sièges."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="seat_lock_combined_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)
        # Assez de joueurs pour occuper plusieurs tables (taille de table
        # par défaut bien inférieure à 10, voir Database.add_table).
        self.pids = [self.db.add_player(f"Joueur{i}") for i in range(1, 11)]

    def test_enchainement_accueil_absence_retour_correction_ne_deplace_personne(self):
        reference = _snapshot_positions(self.db)
        self.assertEqual(len(reference), 10)

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            # Quelques arrivées normales.
            self.db.confirm_arrival(self.pids[0])
            self.db.confirm_arrival(self.pids[1])
            # Un joueur signalé absent puis qui revient (Mac).
            self.db.declare_absent(self.pids[2])
            app = _FakeAppForReinstate(self.db, [self.pids[2]])
            main.App._reinstate_selected(app)
            # Un autre confirmé depuis le téléphone.
            remote_app = _FakeAppForRemoteConfirm(self.db)
            main.App._remote_confirm_arrival(remote_app, self.pids[3])
            # Une correction DIRTO sur une confirmation faite par erreur.
            self.db.correct_arrival_confirmation(self.pids[0])

            spy.assert_not_called()

        after = _snapshot_positions(self.db)
        self.assertEqual(after, reference)


if __name__ == "__main__":
    unittest.main()
