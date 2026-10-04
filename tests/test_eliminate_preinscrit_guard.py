# -*- coding: utf-8 -*-
"""Correctif du 2026-09-30 — "menu contextuel des joueurs pré-inscrits" :
un joueur "Pré-inscrit" (accueil activé pour ce tournoi, jamais
accueilli — voir App._accueil_status_label) n'a pas encore été constaté
présent, il ne doit donc jamais pouvoir être éliminé — ni via le
raccourci clic droit de l'onglet Joueurs (App._on_players_tree_right_
click), ni via le bouton rouge "Éliminer" (App._eliminate_selected).

Couvre :
1. Clic droit sur un joueur Pré-inscrit (accueil activé) : aucune
   action, "Qui a éliminé ce joueur ?" ne s'ouvre pas.
2. Clic droit sur un joueur "Actif" (déjà accueilli) : comportement
   inchangé (raccourci d'élimination directe).
3. Bouton "Éliminer" sur une sélection contenant un Pré-inscrit : filtré
   silencieusement, aucune élimination pour lui.
4. Sélection mixte (Pré-inscrit + Actif) : seul l'Actif est éliminé.
5. « Gestion de l'accueil » désactivée : comportement historique
   intégralement inchangé pour les deux points d'entrée, y compris pour
   un joueur qui n'a jamais eu son arrivée confirmée.
6. Non-régression explicite : annulation de la dernière élimination
   (clic droit sur un joueur éliminé) et désactivation (forfait, via
   Abs/Dés/Inscrire) restent hors de portée de ce correctif."""
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


class _FakeAppForRightClick:
    """Doublure minimale pour App._on_players_tree_right_click : seule
    self.db est réelle, le reste (arbre, sélection) est un espion léger
    qui enregistre les appels plutôt que de les exécuter, pour vérifier
    précisément SI _eliminate_selected a été invoquée."""

    def __init__(self, db, row_iid):
        self.db = db
        self._row_iid = row_iid
        self.selection_set_calls = []
        self.eliminate_selected_calls = []
        self.undo_last_elimination_calls = 0

    class _FakeTree:
        def __init__(self, outer):
            self._outer = outer

        def identify_row(self, y):
            return self._outer._row_iid

        def selection_set(self, row_iid):
            self._outer.selection_set_calls.append(row_iid)

    def __post_init(self):
        self.players_tree = self._FakeTree(self)

    def _eliminate_selected(self, skip_confirmation=False, ids=None, force_mandatory_eliminator=False):
        self.eliminate_selected_calls.append(
            {"skip_confirmation": skip_confirmation, "ids": ids, "force_mandatory_eliminator": force_mandatory_eliminator}
        )

    def _undo_last_elimination(self):
        self.undo_last_elimination_calls += 1


def _make_right_click_app(db, row_iid):
    app = _FakeAppForRightClick(db, row_iid)
    app.players_tree = _FakeAppForRightClick._FakeTree(app)
    return app


class RightClickPreInscritGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="right_click_preinscrit_guard_test_")
        self.addCleanup(self._tmp.cleanup)

    def _fake_event(self):
        return types.SimpleNamespace(y=0)

    def test_pre_inscrit_aucune_action(self):
        db = _new_db(self._tmp.name)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        _start_tournament(db)
        app = _make_right_click_app(db, str(pid))

        main.App._on_players_tree_right_click(app, self._fake_event())

        self.assertEqual(app.eliminate_selected_calls, [])

    def test_actif_declenche_toujours_le_raccourci(self):
        db = _new_db(self._tmp.name)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        _start_tournament(db)
        db.confirm_arrival(pid)
        app = _make_right_click_app(db, str(pid))

        main.App._on_players_tree_right_click(app, self._fake_event())

        self.assertEqual(len(app.eliminate_selected_calls), 1)
        call = app.eliminate_selected_calls[0]
        self.assertEqual(call["ids"], [pid])
        self.assertTrue(call["skip_confirmation"])
        self.assertTrue(call["force_mandatory_eliminator"])

    def test_pre_inscrit_mais_accueil_desactive_comportement_historique(self):
        db = _new_db(self._tmp.name, accueil_enabled=False)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        _start_tournament(db)
        app = _make_right_click_app(db, str(pid))

        main.App._on_players_tree_right_click(app, self._fake_event())

        self.assertEqual(len(app.eliminate_selected_calls), 1)

    def test_joueur_elimine_declenche_toujours_l_annulation(self):
        """Non-régression explicite : l'annulation de la dernière
        élimination (clic droit sur un joueur éliminé) reste hors de
        portée de ce correctif."""
        db = _new_db(self._tmp.name)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        pid2 = db.add_player("Bob")
        _start_tournament(db)
        db.eliminate_player(pid)
        app = _make_right_click_app(db, str(pid))

        main.App._on_players_tree_right_click(app, self._fake_event())

        self.assertEqual(app.undo_last_elimination_calls, 1)
        self.assertEqual(app.eliminate_selected_calls, [])


class EliminateSelectedPreInscritGuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eliminate_selected_preinscrit_guard_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_pre_inscrit_seul_dans_la_selection_aucune_elimination(self):
        db = _new_db(self._tmp.name)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        db.add_player("Bob")  # garde au moins un autre actif
        _start_tournament(db)
        app = types.SimpleNamespace(db=db)
        app._eliminate_selected = types.MethodType(main.App._eliminate_selected, app)

        with patch.object(main.messagebox, "askyesno") as mock_confirm:
            app._eliminate_selected(ids=[pid])

        mock_confirm.assert_not_called()
        self.assertEqual(db.get_player(pid)["status"], "active")

    def test_selection_mixte_seul_l_actif_est_eliminable(self):
        db = _new_db(self._tmp.name)
        self.addCleanup(db.conn.close)
        pid_preinscrit = db.add_player("Alice")
        pid_actif = db.add_player("Bob")
        db.confirm_arrival(pid_actif)
        _start_tournament(db)
        app = types.SimpleNamespace(db=db)
        app._eliminate_selected = types.MethodType(main.App._eliminate_selected, app)
        app._ask_eliminator = lambda exclude_id, mandatory: None
        app._queue_elimination_banner = lambda pid, eliminator_id: None
        app._clear_checked = lambda: None
        app._trigger_movement_alert = lambda: None
        app._finish_movement_alert = lambda: None
        app._refresh_all = lambda: None
        app._refresh_remote_players_cache = lambda: None
        app._refresh_remote_moves_cache = lambda: None
        app._refresh_remote_checkin_cache = lambda: None
        app._check_pending_rebalance = lambda: None
        app._test_mode_enabled = lambda: True  # autorise l'élimination groupée sans éliminateur

        with patch.object(main.messagebox, "askyesno", return_value=True):
            app._eliminate_selected(ids=[pid_preinscrit, pid_actif])

        self.assertEqual(db.get_player(pid_preinscrit)["status"], "active")
        self.assertEqual(db.get_player(pid_actif)["status"], "eliminated")

    def test_accueil_desactive_comportement_historique_meme_jamais_accueilli(self):
        """« Gestion de l'accueil » désactivée : un joueur qui n'a
        jamais eu son arrivée confirmée reste normalement éliminable
        (arrived_at n'a alors aucune signification)."""
        db = _new_db(self._tmp.name, accueil_enabled=False)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        db.add_player("Bob")
        _start_tournament(db)
        app = types.SimpleNamespace(db=db)
        app._eliminate_selected = types.MethodType(main.App._eliminate_selected, app)
        app._ask_eliminator = lambda exclude_id, mandatory: None
        app._queue_elimination_banner = lambda pid, eliminator_id: None
        app._clear_checked = lambda: None
        app._trigger_movement_alert = lambda: None
        app._finish_movement_alert = lambda: None
        app._refresh_all = lambda: None
        app._refresh_remote_players_cache = lambda: None
        app._refresh_remote_moves_cache = lambda: None
        app._refresh_remote_checkin_cache = lambda: None
        app._check_pending_rebalance = lambda: None
        app._test_mode_enabled = lambda: False

        with patch.object(main.messagebox, "askyesno", return_value=True):
            app._eliminate_selected(ids=[pid])

        self.assertEqual(db.get_player(pid)["status"], "eliminated")


if __name__ == "__main__":
    unittest.main()
