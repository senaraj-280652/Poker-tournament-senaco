# -*- coding: utf-8 -*-
"""Transition « Inscrit » -> « Actif » au démarrage du tournoi (demande
du 2026-10-03, "STATUT MAC INSCRIT AVANT LE DÉMARRAGE") — couvre
spécifiquement la transition complète explicitement demandée :

    Pré-inscrit -> Inscrit -> démarrage du tournoi -> Actif

Cette transition doit être PUREMENT visuelle au moment du démarrage
(App._clock_resume, clock_started 0 -> 1) : aucune nouvelle confirmation
(Database.confirm_arrival n'est JAMAIS appelée par _clock_resume),
`arrived_at` strictement inchangé, aucune écriture table_id/seat, aucun
rééquilibrage. Voir App._accueil_status_label (main.py) pour la source
unique de ce libellé, et tests/test_players_preinscrit_actif_status.py
pour la couverture unitaire de la fonction elle-même (dont la même
transition, au niveau le plus bas, sans passer par _clock_resume)."""
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


class _FakeAppForClockResume:
    """Doublure minimale pour App._clock_resume (même principe que
    tests/test_preinscription_seat_lock_regression.py::_FakeAppForClock
    Resume, reprise à l'identique pour garder ce fichier indépendant)."""

    def __init__(self, db):
        self.db = db

    def _uncheck_all_players(self):
        pass

    def _refresh_clock_tab(self):
        pass


def _snapshot_positions(db):
    return {
        p["id"]: (p["table_id"], p["seat"])
        for p in db.list_players(status="active")
    }


class ClockResumeTransitionsInscritToActifTest(unittest.TestCase):
    """App._clock_resume ne fait jamais qu'un changement de réglages
    (clock_started, level_start_epoch, etc.) et décoche les cases — voir
    sa docstring, main.py — jamais confirm_arrival, jamais table_id/seat,
    jamais rebalance_tables."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="inscrit_actif_transition_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.win = types.SimpleNamespace(db=self.db)
        self.win._accueil_status_label = types.MethodType(main.App._accueil_status_label, self.win)

    def test_transition_complete_pre_inscrit_inscrit_demarrage_actif(self):
        """La transition demandée, bout en bout, au niveau du libellé."""
        pid = self.db.add_player("Alice")
        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Pré-inscrit")

        self.db.confirm_arrival(pid)
        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Inscrit")

        app = _FakeAppForClockResume(self.db)
        main.App._clock_resume(app)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Actif")

    def test_arrived_at_strictement_inchange_pendant_la_transition(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        arrived_at_avant = self.db.get_player(pid)["arrived_at"]

        app = _FakeAppForClockResume(self.db)
        main.App._clock_resume(app)

        self.assertEqual(self.db.get_player(pid)["arrived_at"], arrived_at_avant)

    def test_aucune_confirmation_arrival_appelee_par_le_demarrage(self):
        """confirm_arrival ne doit JAMAIS être appelée par _clock_resume —
        seul un changement de réglages, jamais une écriture sur les
        joueurs eux-mêmes."""
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        app = _FakeAppForClockResume(self.db)

        with patch.object(
            database.Database, "confirm_arrival", wraps=self.db.confirm_arrival
        ) as spy:
            main.App._clock_resume(app)
            spy.assert_not_called()

    def test_table_et_siege_inchanges_aucun_rebalance_pendant_la_transition(self):
        pid_inscrit = self.db.add_player("Alice")
        pid_preinscrit = self.db.add_player("Bob")
        self.db.confirm_arrival(pid_inscrit)
        before = _snapshot_positions(self.db)
        app = _FakeAppForClockResume(self.db)

        with patch.object(
            database.Database, "rebalance_tables", wraps=self.db.rebalance_tables
        ) as spy:
            main.App._clock_resume(app)
            spy.assert_not_called()

        after = _snapshot_positions(self.db)
        self.assertEqual(after, before)

    def test_preinscrit_reste_preinscrit_seul_linscrit_devient_actif(self):
        """La transition est bien PAR JOUEUR (selon son propre arrived_at),
        jamais un simple affichage global : un Pré-inscrit resté tel
        n'est jamais affecté par le démarrage."""
        pid_inscrit = self.db.add_player("Alice")
        pid_preinscrit = self.db.add_player("Bob")
        self.db.confirm_arrival(pid_inscrit)
        app = _FakeAppForClockResume(self.db)

        main.App._clock_resume(app)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid_inscrit)), "Actif")
        self.assertEqual(
            self.win._accueil_status_label(self.db.get_player(pid_preinscrit)), "Pré-inscrit"
        )

    def test_absent_declare_reste_absent_apres_le_demarrage(self):
        """Non-régression : un joueur signalé absent (jamais confirmé)
        garde "Absent", priorité inchangée, une fois le tournoi démarré.
        declare_absent exige déjà le démarrage (voir sa docstring) — posé
        ici directement via les réglages, le point testé étant la
        priorité du libellé "Absent" elle-même, pas la transition de
        démarrage (couverte par les autres tests de cette classe)."""
        self.db.set_settings({
            "clock_started": 1,
            "tournament_start_epoch": int(time.time()),
        })
        pid = self.db.add_player("Alice")

        self.assertTrue(self.db.declare_absent(pid))

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Absent")

    def test_accueil_desactive_reste_actif_avant_et_apres(self):
        """Non-régression : réglage désactivé, le libellé "Actif" ne
        bouge jamais, avant ou après le démarrage — seule la variante
        accueil activée distingue Inscrit/Actif."""
        self.db.set_settings({"accueil_management_enabled": 0})
        pid = self.db.add_player("Alice")
        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Actif")
        app = _FakeAppForClockResume(self.db)

        main.App._clock_resume(app)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Actif")


if __name__ == "__main__":
    unittest.main()
