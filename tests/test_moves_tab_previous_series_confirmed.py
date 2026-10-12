# -*- coding: utf-8 -*-
"""Complète "Movt Précédent" (demande du 2026-10-06, suite) : le premier
test manuel a montré qu'un mouvement VALIDÉ (confirm_seat_move, bouton
[OK] du téléphone) avant "Terminé" disparaissait sans trace — "Movt
Précédent" ne retrouvait donc que les mouvements encore en attente au
moment de "Terminé", jamais ceux déjà validés (y compris, cas extrême,
AUCUN si toute la série avait été validée un par un depuis le téléphone,
le dernier déclenchant automatiquement _finish_movement_alert alors que
seat_moves était déjà vide).

Architecture (voir la conversation pour le diagnostic complet) :
- `confirmed_moves_this_cycle` (database.py) : journal ATOMIQUE, alimenté
  UNIQUEMENT par `confirm_seat_move` (copie la ligne complète juste avant
  son DELETE habituel de seat_moves, même transaction, commit unique —
  jamais par `_sync_seat_moves`, qui n'est pas modifiée). Vidé à chaque
  "Terminé", une fois la série archivée.
- `previous_seat_moves.status` ('pending'/'confirmed') : nouvelle colonne
  (migration ALTER TABLE additive, comme reason sur seat_moves) — pilote
  SEULE le libellé Raison affiché ("Non Validé"/"Déjà Validé"),
  jamais la vraie valeur `reason` archivée.
- `archive_seat_moves_as_previous()` : lit désormais l'UNION de
  seat_moves (pending) ET confirmed_moves_this_cycle (confirmed) — ne
  fait rien QUE SI LES DEUX sont vides (corrige le cas "tout confirmé
  depuis le téléphone, rien à archiver selon l'ancienne logique")."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock

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


# =========================================================================
# A — Couche base de données
# =========================================================================
class ConfirmedMovesThisCycleDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="confirmed_moves_cycle_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat):
        cur = self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-10-06 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()
        return cur.lastrowid

    def _confirmed_names(self):
        return [
            r["player_name"] for r in
            self.db.conn.execute("SELECT * FROM confirmed_moves_this_cycle ORDER BY id")
        ]

    def test_confirmer_copie_la_ligne_complete_avant_suppression(self):
        move_id = self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.confirm_seat_move(move_id)
        rows = list(self.db.conn.execute("SELECT * FROM confirmed_moves_this_cycle"))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["player_name"], "Alice")
        self.assertEqual(row["old_table_name"], "Table 1")
        self.assertEqual(row["old_seat"], 3)
        self.assertEqual(row["new_table_name"], "Table 2")
        self.assertEqual(row["new_seat"], 5)
        # Toujours supprimée de seat_moves comme avant (contrat inchangé).
        self.assertEqual(self.db.count_seat_moves(), 0)

    def test_confirmer_retourne_toujours_none_comme_avant(self):
        """Conserve le contrat actuel (aucune valeur de retour) pour ne
        pas perturber App._remote_confirm_move, seul appelant existant."""
        move_id = self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.assertIsNone(self.db.confirm_seat_move(move_id))

    def test_double_confirmation_du_meme_id_najoute_aucun_doublon(self):
        """Point de sécurité explicitement demandé : un retry réseau ou
        un double-tap téléphone sur le MÊME id, une fois la ligne déjà
        supprimée de seat_moves, ne doit rien ajouter de plus."""
        move_id = self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.confirm_seat_move(move_id)
        self.db.confirm_seat_move(move_id)  # retry
        self.db.confirm_seat_move(move_id)  # encore
        self.assertEqual(self._confirmed_names(), ["Alice"])

    def test_confirmer_un_id_inconnu_najoute_rien(self):
        self.db.confirm_seat_move(999999)
        self.assertEqual(self._confirmed_names(), [])
        self.assertEqual(self.db.count_seat_moves(), 0)

    def test_plusieurs_confirmations_distinctes_saccumulent_dans_lordre(self):
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        id_b = self._insert_move("B", "T1", 2, "T2", 2)
        self.db.confirm_seat_move(id_a)
        self.db.confirm_seat_move(id_b)
        self.assertEqual(self._confirmed_names(), ["A", "B"])

    def test_jamais_lue_par_count_ou_get_seat_moves(self):
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        self._insert_move("B", "T1", 2, "T2", 2)  # reste en attente
        self.db.confirm_seat_move(id_a)
        self.assertEqual(self.db.count_seat_moves(), 1)
        self.assertEqual([m["player_name"] for m in self.db.get_seat_moves()], ["B"])

    def test_videe_par_clear_confirmed_moves_this_cycle(self):
        move_id = self._insert_move("Alice", "Table 1", 3, "Table 2", 5)
        self.db.confirm_seat_move(move_id)
        self.db.clear_confirmed_moves_this_cycle()
        self.assertEqual(self._confirmed_names(), [])


# =========================================================================
# B — archive_seat_moves_as_previous() : union pending + confirmed, status
# =========================================================================
class ArchiveWithConfirmedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="archive_confirmed_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat):
        cur = self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-10-06 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()
        return cur.lastrowid

    def _previous_as_pairs(self):
        return [(r["player_name"], r["status"]) for r in self.db.get_previous_seat_moves()]

    def _finish_cycle(self):
        """Équivalent minimal de App._finish_movement_alert pour cette
        couche BDD : archive, puis vide les deux sources."""
        self.db.archive_seat_moves_as_previous()
        self.db.clear_seat_moves()
        self.db.clear_confirmed_moves_this_cycle()

    def test_scenario_reference_ab_valides_cd_en_attente(self):
        """SCÉNARIO DE RÉFÉRENCE exact de la demande : A, B validés
        téléphone ; C, D encore en attente ; puis Terminé."""
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        id_b = self._insert_move("B", "T1", 2, "T2", 2)
        self._insert_move("C", "T1", 3, "T2", 3)
        self._insert_move("D", "T1", 4, "T2", 4)
        self.db.confirm_seat_move(id_a)
        self.db.confirm_seat_move(id_b)

        self._finish_cycle()

        self.assertEqual(
            self._previous_as_pairs(),
            [("A", "confirmed"), ("B", "confirmed"), ("C", "pending"), ("D", "pending")],
        )

    def test_scenario_2_tout_valide_le_dernier_declenche_la_fin_automatique(self):
        """SCÉNARIO IMPORTANT N°2 : A, B, C, D tous validés successivement
        — la validation du dernier déclencherait normalement
        _finish_movement_alert automatiquement (main.py), alors que
        seat_moves est déjà vide à ce moment précis. Même chose simulée
        ici au niveau BDD : archiver doit malgré tout retrouver les 4."""
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        id_b = self._insert_move("B", "T1", 2, "T2", 2)
        id_c = self._insert_move("C", "T1", 3, "T2", 3)
        id_d = self._insert_move("D", "T1", 4, "T2", 4)
        for i in (id_a, id_b, id_c, id_d):
            self.db.confirm_seat_move(i)
        self.assertEqual(self.db.count_seat_moves(), 0)  # bien vide à ce stade

        self._finish_cycle()

        self.assertEqual(
            self._previous_as_pairs(),
            [("A", "confirmed"), ("B", "confirmed"), ("C", "confirmed"), ("D", "confirmed")],
        )

    def test_scenario_3_terminer_sans_rien_ne_touche_pas_a_la_serie_memorisee(self):
        """SCÉNARIO IMPORTANT N°3 : après avoir archivé une série
        complète, un "Terminé" déclenché alors qu'il n'existe NI
        seat_moves NI confirmed_moves_this_cycle ne doit surtout pas
        remplacer/effacer previous_seat_moves."""
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        self.db.confirm_seat_move(id_a)
        self._finish_cycle()
        before = self._previous_as_pairs()
        self.assertEqual(before, [("A", "confirmed")])

        self.assertEqual(self.db.count_seat_moves(), 0)
        self.assertEqual(
            list(self.db.conn.execute("SELECT * FROM confirmed_moves_this_cycle")), []
        )
        self._finish_cycle()  # rien à archiver cette fois

        self.assertEqual(self._previous_as_pairs(), before)

    def test_tout_en_attente_aucune_confirmation(self):
        """Non-régression du comportement déjà validé : si rien n'a été
        confirmé, tout apparaît en status='pending' comme avant."""
        self._insert_move("A", "T1", 1, "T2", 1)
        self._insert_move("B", "T1", 2, "T2", 2)
        self._finish_cycle()
        self.assertEqual(self._previous_as_pairs(), [("A", "pending"), ("B", "pending")])

    def test_tout_confirme_aucun_en_attente(self):
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        self.db.confirm_seat_move(id_a)
        self._finish_cycle()
        self.assertEqual(self._previous_as_pairs(), [("A", "confirmed")])

    def test_une_nouvelle_serie_remplace_toujours_lancienne(self):
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        self.db.confirm_seat_move(id_a)
        self._finish_cycle()

        self._insert_move("B", "T3", 1, "T4", 1)
        self._finish_cycle()

        self.assertEqual(self._previous_as_pairs(), [("B", "pending")])

    def test_migration_ajoute_la_colonne_status_a_un_fichier_existant_sans(self):
        """Un fichier .tournoi créé AVANT cette colonne (ex. pendant les
        tests manuels de ce chantier) doit continuer à s'ouvrir, et toute
        ligne déjà présente sans status est traitée comme 'pending' —
        jamais une valeur inventée."""
        path = os.path.join(self._tmp.name, "old_schema.tournoi")
        conn = database.sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE previous_seat_moves (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "player_name TEXT NOT NULL, old_table_name TEXT, old_seat INTEGER, "
            "new_table_name TEXT, new_seat INTEGER, moved_at TEXT NOT NULL, "
            "reason TEXT NOT NULL DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO previous_seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            ("OldRow", "T1", 1, "T2", 1, "2026-10-01 10:00:00", ""),
        )
        conn.commit()
        conn.close()

        reopened = database.Database(path)
        self.addCleanup(reopened.conn.close)
        rows = reopened.get_previous_seat_moves()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")


# =========================================================================
# C — Atomicité confirm_seat_move (copie + suppression dans UNE transaction)
# =========================================================================
class ConfirmSeatMoveAtomicityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="confirm_atomicity_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _insert_move(self, player_name="Alice"):
        cur = self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, "Table 1", 3, "Table 2", 5, "2026-10-06 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()
        return cur.lastrowid

    def test_echec_de_la_copie_naffecte_jamais_seat_moves(self):
        """Si l'INSERT vers confirmed_moves_this_cycle échoue (simulé ici
        en supprimant la table), le DELETE de seat_moves ne doit PAS non
        plus avoir eu lieu — rollback intégral, jamais un mouvement
        supprimé sans copie réussie."""
        move_id = self._insert_move()
        self.db.conn.execute("DROP TABLE confirmed_moves_this_cycle")
        with self.assertRaises(database.sqlite3.OperationalError):
            self.db.confirm_seat_move(move_id)
        # Toujours là : le rollback a bien annulé le DELETE.
        self.assertEqual(self.db.count_seat_moves(), 1)

    def test_succes_complet_laisse_les_deux_tables_coherentes(self):
        move_id = self._insert_move()
        self.db.confirm_seat_move(move_id)
        self.assertEqual(self.db.count_seat_moves(), 0)
        self.assertEqual(
            len(list(self.db.conn.execute("SELECT * FROM confirmed_moves_this_cycle"))), 1
        )


# =========================================================================
# D — UI : libellé Raison selon status, non-régression de l'existant
# =========================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovesTabPreviousSeriesStatusLabelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        main.App._apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="moves_tab_status_label_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win._moves_tab_player_mode = False
        self.win._moves_tab_display_mode_applied = None
        self.win._moves_tab_normal_col_widths = None
        self.win._moves_tab_show_previous_moves = False
        self.win.voice_awaiting_resume = False
        self.win.clock_window = None

        self.win.moves_tab = ttk.Frame(self.root)
        self.addCleanup(self.win.moves_tab.destroy)
        self.win._print_movement_slips_filled = lambda: None
        self.win._print_movement_slips = lambda: None
        self.win._clock_resume = lambda: None
        self.win._refresh_clock_tab = lambda: None
        self.win._resume_rebalance_if_needed = lambda: None
        self.win.clock_tab = MagicMock()

        self.win.notebook = MagicMock()
        self.win.notebook.select.return_value = "tabid"
        self.win.notebook.tab.return_value = "Mouvements"
        self.win.after = lambda ms, fn: "after-id"
        self.win.after_cancel = MagicMock()

        for const in (
            "MOVES_TAB_ALL_COLUMNS", "MOVES_TAB_PLAYER_COLUMNS",
            "MOVES_TAB_PLAYER_DEFAULT_WIDTHS", "MOVES_PLAYER_AUTOSCROLL_INTERVAL_MS",
            "MOVES_PLAYER_AUTOSCROLL_PAUSE_MS",
        ):
            setattr(self.win, const, getattr(main.App, const))

        for name in (
            "_build_moves_tab", "_apply_moves_tab_display_mode", "_refresh_moves_tab",
            "_on_moves_tree_button_release", "_save_moves_player_column_widths_if_changed",
            "_moves_player_autoscroll_tick", "_moves_player_reset_to_top",
            "_moves_player_resume_autoscroll", "_toggle_previous_moves_display",
            "_finish_movement_alert",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))

        self.win._build_moves_tab()

    def _insert_move(self, player_name, old_table_name, old_seat, new_table_name, new_seat):
        cur = self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-10-06 20:00:00", "auto_balance"),
        )
        self.db.conn.commit()
        return cur.lastrowid

    def _rows(self):
        cols = self.win.moves_tree["columns"]
        return [
            dict(zip(cols, self.win.moves_tree.item(iid, "values")))
            for iid in self.win.moves_tree.get_children()
            if "previous_move" in self.win.moves_tree.item(iid, "tags")
        ]

    def test_scenario_reference_affichage_complet(self):
        """Le scénario A/B/C/D exact, vérifié jusqu'à l'affichage final,
        toutes les lignes en italique via le tag previous_move (déjà
        couvert par ailleurs) et avec le bon libellé chacune."""
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        id_b = self._insert_move("B", "T1", 2, "T2", 2)
        self._insert_move("C", "T1", 3, "T2", 3)
        self._insert_move("D", "T1", 4, "T2", 4)
        self.db.confirm_seat_move(id_a)
        self.db.confirm_seat_move(id_b)

        self.win._finish_movement_alert()
        self.win._toggle_previous_moves_display()

        rows = {r["player"]: r["reason"] for r in self._rows()}
        self.assertEqual(
            rows,
            {
                "A": "Déjà Validé", "B": "Déjà Validé",
                "C": "Non Validé", "D": "Non Validé",
            },
        )

    def test_scenario_2_affichage_tout_deja_valide(self):
        id_a = self._insert_move("A", "T1", 1, "T2", 1)
        id_b = self._insert_move("B", "T1", 2, "T2", 2)
        for i in (id_a, id_b):
            self.db.confirm_seat_move(i)
        # Simule l'auto-déclenchement de _finish_movement_alert quand le
        # dernier mouvement est confirmé depuis le téléphone (seat_moves
        # déjà vide à cet instant, comme dans App._remote_confirm_move).
        self.win._finish_movement_alert()
        self.win._toggle_previous_moves_display()

        rows = {r["player"]: r["reason"] for r in self._rows()}
        self.assertEqual(rows, {"A": "Déjà Validé", "B": "Déjà Validé"})

    def test_raison_dorigine_jamais_modifiee_meme_pour_un_mouvement_confirme(self):
        move_id = self._insert_move("Alice", "T1", 1, "T2", 1)
        self.db.confirm_seat_move(move_id)
        self.win._finish_movement_alert()
        self.assertEqual(self.db.get_previous_seat_moves()[0]["reason"], "auto_balance")


if __name__ == "__main__":
    unittest.main()
