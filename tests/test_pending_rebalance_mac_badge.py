# -*- coding: utf-8 -*-
"""Tests ciblés du "point à étudier" de la PHASE 3 (demande du
2026-09-10) : une indication discrète dans l'onglet Tables quand un
rééquilibrage attend qu'on désigne le joueur UTG à déplacer (voir
main.py: _update_pending_rebalance_badge), et un bouton "Continuer sans
désigner le joueur" permettant au responsable de débloquer CE mouvement
précis depuis le Mac (voir _continue_pending_rebalance_without_bb) —
SANS reproduire l'ancienne fenêtre intrusive, et SANS toucher à la
préférence globale "Équilibrage guidé par UTG" (contrairement à
_on_bb_rebalance_prompt_toggle, qui la décoche en plus de résoudre la
demande en cours). Textes mis à jour le 2026-09-24 (chantier "sélection
directe du joueur UTG") — mécanisme et assertions inchangés : ce fichier
n'exerce que le chemin "Continuer sans désigner" (player_id=None),
jamais la sélection d'un joueur précis.

Vérifie :
1. le badge/bouton sont masqués tant qu'aucun rééquilibrage n'est en
   attente, et apparaissent dès qu'un pending_rebalance existe ;
2. le bouton "Continuer sans désigner le joueur" résout UNIQUEMENT la
   demande courante (via database.py: resolve_pending_rebalance,
   player_id=None — le même mécanisme qu'une réponse téléphone
   équivalente) ;
3. la préférence globale bb_rebalance_prompt_var n'est jamais modifiée
   par ce bouton ;
4. le badge disparaît une fois la demande résolue.

N'instancie PAS App(tk.Tk) au complet : seuls les widgets/attributs
lus par ces méthodes précises sont greffés sur un vrai tk.Tk() (comme
tests/test_tick_never_stops_scheduling.py le fait déjà pour App._tick)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


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


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PendingRebalanceMacBadgeTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (setUpClass/tearDownClass),
    pas un par méthode de test — voir la même remarque dans
    tests/test_rebalance_phase2_max_seats_change.py (instabilité Tcl/Tk
    observée en multipliant les racines réelles dans la suite complète,
    sans rapport avec la logique testée ici)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : les 4 méthodes de main.App greffées sur
        # cls.root dans setUp forment chacune un cycle (méthode liée dont
        # __self__ est cls.root) que seul gc.collect() peut réclamer.
        cleanup_tk(cls, "root")

    def setUp(self):
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="pending_rebalance_badge_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({
            "max_seats_per_table": 9, "min_players_per_table": 1, "clock_started": 1,
        })
        self.t1_id = self.db.list_tables()[0]["id"]
        self.t2_id = self.db.add_table("Table 2")
        for seat in range(1, 10):
            _seat_new_player(self.db, self.t1_id, seat, f"T1-{seat}")
        for seat in range(1, 4):
            _seat_new_player(self.db, self.t2_id, seat, f"T2-{seat}")

        # Greffe, sur la racine réelle, exactement les widgets construits
        # par App._build_tables_tab pour ce badge (voir main.py), et les
        # méthodes réelles qu'on teste — le reste (rafraîchissements
        # d'autres onglets) est remplacé par des doublures no-op, sans
        # rapport avec ce qui est vérifié ici.
        self.win = self.root
        self.win.db = self.db
        self.win._pending_rebalance_frame = ttk.Frame(self.root)
        self.addCleanup(self.win._pending_rebalance_frame.destroy)  # racine partagée entre tests, voir setUpClass
        self.win._pending_rebalance_label = ttk.Label(self.win._pending_rebalance_frame, text="")
        self.win._pending_rebalance_label.pack(side="left")
        self.win.bb_rebalance_prompt_var = tk.BooleanVar(value=True)

        self.win._update_pending_rebalance_badge = types.MethodType(
            main.App._update_pending_rebalance_badge, self.win
        )
        self.win._check_pending_rebalance = types.MethodType(
            main.App._check_pending_rebalance, self.win
        )
        self.win._resolve_pending_rebalance = types.MethodType(
            main.App._resolve_pending_rebalance, self.win
        )
        self.win._continue_pending_rebalance_without_bb = types.MethodType(
            main.App._continue_pending_rebalance_without_bb, self.win
        )
        self.win._refresh_all = MagicMock()
        self.win._refresh_remote_players_cache = MagicMock()
        self.win._refresh_remote_moves_cache = MagicMock()
        self.win._trigger_movement_alert = MagicMock()
        self.win._finish_movement_alert = MagicMock()

    def test_badge_cache_tant_qu_aucun_rebalance_en_attente(self):
        self.assertIsNone(self.db.pending_rebalance)
        self.win._check_pending_rebalance()
        self.assertNotEqual(self.win._pending_rebalance_frame.winfo_manager(), "pack")

    def test_badge_apparait_avec_le_nom_de_la_table_concernee(self):
        self.db.rebalance_tables()
        self.assertIsNotNone(self.db.pending_rebalance)  # écart 9 vs 3

        self.win._check_pending_rebalance()

        self.assertEqual(self.win._pending_rebalance_frame.winfo_manager(), "pack")
        self.assertIn("Table 1", self.win._pending_rebalance_label.cget("text"))

    def test_continuer_sans_bb_resout_uniquement_la_demande_courante(self):
        self.db.rebalance_tables()
        pending = self.db.pending_rebalance
        self.assertIsNotNone(pending)
        self.win._check_pending_rebalance()
        self.assertEqual(self.win._pending_rebalance_frame.winfo_manager(), "pack")

        self.win._continue_pending_rebalance_without_bb()

        # La demande a bien été consommée (résolue), et un mouvement a
        # été décidé (mécanisme historique _legacy_pick_mover, exactement
        # comme "Continuer sans désigner le joueur" depuis un téléphone).
        self.win._trigger_movement_alert.assert_called_once()
        c1 = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (self.t1_id,)
        ).fetchone()["c"]
        c2 = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (self.t2_id,)
        ).fetchone()["c"]
        self.assertEqual(c1 + c2, 12)
        self.assertLess(abs(c1 - c2), 6)  # l'écart initial (6) a diminué

        # La préférence globale n'a JAMAIS été touchée par ce bouton
        # (contrairement à _on_bb_rebalance_prompt_toggle).
        self.assertTrue(self.win.bb_rebalance_prompt_var.get())

        # Le badge disparaît (soit la demande est totalement résolue,
        # soit — si un nouvel écart persiste ailleurs — une NOUVELLE
        # demande a pu être reposée ; dans les deux cas, l'ANCIENNE
        # demande, elle, ne peut plus être répondue une seconde fois).
        self.assertNotEqual(
            (self.db.pending_rebalance or {}).get("request_id"),
            pending["request_id"],
        )

    def test_bouton_sans_effet_si_aucune_demande_en_attente(self):
        """Garde-fou : cliquer sur ce bouton sans demande en attente (ne
        devrait normalement pas être possible, le badge étant caché) ne
        doit rien casser ni rien résoudre."""
        self.assertIsNone(self.db.pending_rebalance)
        self.win._continue_pending_rebalance_without_bb()
        self.win._trigger_movement_alert.assert_not_called()
        self.win._refresh_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
