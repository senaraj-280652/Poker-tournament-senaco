# -*- coding: utf-8 -*-
"""Tests de l'avertissement "mouvements en attente" + clignotement des
tables concernées sur l'onglet Tables (demande du 2026-09-16, suite au
diagnostic confirmé sur le snapshot réel du club — voir
App._pending_old_seat_by_name).

Rappel du diagnostic (conservé tel quel, comportement métier inchangé) :
tant que movement_alert_active=1 et que "Terminé" n'a pas été cliqué,
l'onglet Tables affiche VOLONTAIREMENT les anciennes positions des
joueurs concernés par un mouvement pas encore confirmé — ce qui, sans
indication visuelle sur CET onglet précis, donne à tort l'impression
d'une base de données désynchronisée (le total "Nombre de joueurs" est
lui toujours exact, voir tests/test_tables_tab_player_counts.py).

Cette amélioration ajoute, UNIQUEMENT côté affichage de l'onglet Tables
(main.py: App._build_tables_tab / _refresh_tables_tab) :
- un bandeau d'avertissement + un bouton "Terminé" (délègue entièrement
  à App._finish_movement_alert, voir son paramètre switch_to_clock —
  aucune logique de validation dupliquée) ;
- un clignotement (App._tables_blink_tick / _cancel_tables_blink) des
  SEULES tables concernées par un mouvement en attente (table de départ
  ET table d'arrivée), piloté par un seul callback Tkinter after() à la
  fois, jamais un thread ni une boucle bloquante.

N'instancie PAS App(tk.Tk) au complet : seuls les widgets/méthodes
réellement nécessaires sont greffés sur une racine Tk réelle partagée
pour toute la classe (même précaution anti-flakiness Tcl/Tk que
tests/test_tables_tab_player_counts.py et tests/test_pending_rebalance_
mac_badge.py — multiplier les vrais tk.Tk() dans la suite complète a
provoqué une instabilité Tcl/Tk sans rapport avec la logique testée).
"""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

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


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class TablesTabMovementPendingTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (voir la docstring du
    module)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : les nombreuses méthodes de main.App greffées
        # sur cls.root dans setUp forment chacune un cycle, réclamé ici.
        cleanup_tk(cls, "root")

    def setUp(self):
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=1.0)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="tables_movement_pending_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 7, "min_players_per_table": 1})
        self.db.set_all_tables_max_seats(7)

        self.win = self.root
        self.win.db = self.db
        self.win.voice_awaiting_resume = False
        self.win.clock_window = None
        self.win._tables_zoom = 1.0
        self.win._tables_scroll_paused = False
        self.win._remote_photo_uploaded = False

        # Dépendances de _finish_movement_alert hors du périmètre testé ici
        # (onglet Chronomètre, onglet Mouvements, fenêtre projecteur
        # séparée) : de simples doublures — jamais la logique métier
        # elle-même, qui n'est JAMAIS réimplémentée ici (voir
        # _finish_movement_alert_from_tables, qui délègue entièrement).
        self.win.notebook = MagicMock()
        self.win.clock_tab = MagicMock()
        self.win.moves_tab = MagicMock()
        self.win._refresh_clock_tab = lambda: None
        self.win._refresh_moves_tab = lambda: None
        self.win._clock_resume = lambda: None

        # Onglet Tables construit via le VRAI _build_tables_tab (pour
        # exercer la construction réelle du bandeau/bouton ajoutés par
        # cette demande), sur un ttk.Frame greffé à la place de
        # self.tables_tab — jamais un tk.Tk() de plus.
        self.win.tables_tab = ttk.Frame(self.root)
        self.addCleanup(self.win.tables_tab.destroy)
        self.win._tables_autoscroll_tick = lambda: None
        self.win._rebalance = lambda: None
        self.win._on_tables_mousewheel = lambda event: None
        self.win._continue_pending_rebalance_without_bb = lambda: None

        # Correctif du 2026-09-25 (diagnostic "question UTG posée avant
        # exécution physique des mouvements précédents") : _finish_
        # movement_alert appelle désormais _resume_rebalance_if_needed,
        # elle-même liée ici pour de vrai (jamais réimplémentée) — ses
        # scénarios (une seule table, ou déjà fusionnée) ne peuvent de
        # toute façon jamais produire de nouveau besoin réel. Ses propres
        # dépendances (rafraîchissements/caches téléphone) restent, elles,
        # hors du périmètre testé ici — simples doublures, comme
        # _refresh_clock_tab/_clock_resume ci-dessus.
        self.win._refresh_all = lambda: None
        self.win._trigger_movement_alert = lambda from_remote=False: None
        self.win._check_pending_rebalance = lambda: None
        self.win._refresh_remote_players_cache = lambda: None
        self.win._refresh_remote_moves_cache = lambda: None

        self.win._build_tables_tab = types.MethodType(main.App._build_tables_tab, self.win)
        self.win._refresh_tables_tab = types.MethodType(main.App._refresh_tables_tab, self.win)
        self.win._pending_old_seat_by_name = types.MethodType(
            main.App._pending_old_seat_by_name, self.win
        )
        self.win._tables_blink_tick = types.MethodType(main.App._tables_blink_tick, self.win)
        self.win._cancel_tables_blink = types.MethodType(main.App._cancel_tables_blink, self.win)
        self.win._resume_rebalance_if_needed = types.MethodType(
            main.App._resume_rebalance_if_needed, self.win
        )
        self.win._finish_movement_alert = types.MethodType(
            main.App._finish_movement_alert, self.win
        )
        self.win._finish_movement_alert_from_tables = types.MethodType(
            main.App._finish_movement_alert_from_tables, self.win
        )

        self.win._build_tables_tab()
        # tables_canvas EN DOUBLURE (MagicMock) : _refresh_tables_tab
        # n'appelle sur lui que des détails de défilement (update_
        # idletasks/configure(scrollregion=...)/bbox/yview_moveto), sans
        # aucun rapport avec ce qui est testé ici — un vrai tk.Canvas
        # s'est avéré risquer un crash bas niveau Tcl/Tk dans ce harnais
        # greffé (voir la même précaution dans test_tables_tab_player_
        # counts.py). Remplacé APRÈS _build_tables_tab() : celui-ci en
        # crée un vrai en interne, jamais utilisé ensuite.
        self.win.tables_canvas = MagicMock()
        self.win.tables_canvas.bbox.return_value = None

    def tearDown(self):
        # Jamais de callback after() de clignotement qui fuite d'un test
        # au suivant : la racine Tk est PARTAGÉE pour toute la classe.
        self.win._cancel_tables_blink()

    # -- Aides -----------------------------------------------------------

    def _seat(self, table_id, seat, name):
        cur = self.db.conn.execute(
            "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
            "chips, status, bounty, club) VALUES (?, 1, 0, 0, 10000, 'active', 0, '')",
            (name,),
        )
        player_id = cur.lastrowid
        self.db.conn.execute(
            "UPDATE players SET table_id=?, seat=? WHERE id=?", (table_id, seat, player_id)
        )
        self.db.conn.commit()
        return player_id

    def _table_id_by_name(self, name):
        for t in self.db.list_tables(active_only=False):
            if t["name"] == name:
                return t["id"]
        return None

    def _record_pending_move(self, player_name, old_table_name, old_seat,
                              new_table_name, new_seat):
        """Insère directement une ligne seat_moves + active movement_alert_
        active — équivalent au strict nécessaire, côté base, de ce que
        fait App._trigger_movement_alert (le reste de cette méthode ne
        concerne que l'UI hors du périmètre de ce fichier : signal
        sonore, bascule d'onglet, écran projecteur)."""
        self.db.conn.execute(
            "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
            "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
            (player_name, old_table_name, old_seat, new_table_name, new_seat,
             "2026-01-01 00:00:00", "fermeture_table"),
        )
        self.db.conn.commit()
        self.db.set_settings({"movement_alert_active": 1})

    def _pack_visible(self, widget):
        try:
            widget.pack_info()
            return True
        except tk.TclError:
            return False

    def _table_frames(self):
        return [w for w in self.win.tables_inner.winfo_children() if isinstance(w, tk.LabelFrame)]

    def _frame_by_title_prefix(self, prefix):
        for f in self._table_frames():
            if f.cget("text").startswith(prefix):
                return f
        return None

    def _names_in_frame(self, frame):
        return [
            w.cget("text") for w in frame.winfo_children() if isinstance(w, tk.Label)
        ]

    # -- 1/7 : avertissement --------------------------------------------

    def test_avertissement_visible_si_mouvement_en_attente(self):
        t1 = self.db.list_tables()[0]["id"]
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Alice", "Table 2", 1, "Table 1", 1)
        self.win._refresh_tables_tab()
        self.assertTrue(self._pack_visible(self.win._movement_pending_frame))
        self.assertIn("Mouvements de tables en attente", self.win._movement_pending_label.cget("text"))

    def test_aucun_mouvement_aucun_avertissement(self):
        t1 = self.db.list_tables()[0]["id"]
        self._seat(t1, 1, "Alice")
        self.win._refresh_tables_tab()
        self.assertFalse(self._pack_visible(self.win._movement_pending_frame))

    # -- 2 : bouton Terminé visible ---------------------------------------

    def test_bouton_termine_visible_si_mouvement_en_attente(self):
        t1 = self.db.list_tables()[0]["id"]
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Alice", "Table 2", 1, "Table 1", 1)
        self.win._refresh_tables_tab()
        buttons = [
            w for w in self.win._movement_pending_frame.winfo_children()
            if isinstance(w, ttk.Button)
        ]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(buttons[0].cget("text"), "Terminé")
        self.assertTrue(self._pack_visible(buttons[0]))

    # -- 3/4/5/6 : clignotement -------------------------------------------

    def test_demarrage_du_clignotement_et_seules_les_tables_concernees(self):
        t1 = self.db.list_tables()[0]["id"]
        t2 = self.db.add_table("Table 2")
        t3 = self.db.add_table("Table 3")
        self._seat(t1, 1, "Restée1")   # Table 1 : jamais déplacée (mais table d'arrivée)
        self._seat(t3, 1, "Restée3")   # Table 3 : totalement étrangère au mouvement
        # Mouvement en attente : Table 2 (départ) -> Table 1 (arrivée).
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)
        self.win._refresh_tables_tab()

        self.assertIsNotNone(self.win._tables_blink_after_id)
        self.assertEqual(self.win._tables_blink_ids, {t1, t2})
        self.assertNotIn(t3, self.win._tables_blink_ids)

    def test_aucun_mouvement_aucun_clignotement(self):
        t1 = self.db.list_tables()[0]["id"]
        self._seat(t1, 1, "Alice")
        self.win._refresh_tables_tab()
        self.assertEqual(self.win._tables_blink_ids, set())
        self.assertIsNone(self.win._tables_blink_after_id)

    def test_bascule_couleur_sans_toucher_aux_noms_des_joueurs(self):
        """Le clignotement change bg/fg/bordure du CADRE — jamais le texte
        des joueurs, qui doit rester lisible en permanence (demande
        explicite)."""
        t1 = self.db.list_tables()[0]["id"]
        self.db.add_table("Table 2")
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)
        self.win._refresh_tables_tab()

        frame = self._frame_by_title_prefix("Table 1")
        names_before = self._names_in_frame(frame)
        bg_before = frame.cget("bg")

        self.win._tables_blink_tick()  # une bascule manuelle (sans attendre les 600 ms)
        frame_after = self._frame_by_title_prefix("Table 1")  # même widget (pas reconstruit)
        self.assertIs(frame, frame_after)
        self.assertEqual(self._names_in_frame(frame_after), names_before)
        self.assertNotEqual(frame_after.cget("bg"), bg_before)

    # -- 8 : refresh répétés = pas de multiplication des after() ----------

    def test_refresh_repetes_pas_de_multiplication_after(self):
        t1 = self.db.list_tables()[0]["id"]
        self.db.add_table("Table 2")
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)

        calls = {"after": [], "cancel": []}
        fake_ids = iter(["fake-1", "fake-2", "fake-3"])

        def fake_after(ms, fn):
            fid = next(fake_ids)
            calls["after"].append(fid)
            return fid

        def fake_after_cancel(fid):
            calls["cancel"].append(fid)

        with patch.object(self.win, "after", side_effect=fake_after), \
             patch.object(self.win, "after_cancel", side_effect=fake_after_cancel):
            self.win._refresh_tables_tab()
            self.win._refresh_tables_tab()
            self.win._refresh_tables_tab()

        self.assertEqual(calls["after"], ["fake-1", "fake-2", "fake-3"])
        # Chaque nouveau rafraîchissement annule le précédent AVANT d'en
        # programmer un nouveau — jamais deux callbacks vivants à la fois.
        self.assertEqual(calls["cancel"], ["fake-1", "fake-2"])
        self.assertEqual(self.win._tables_blink_after_id, "fake-3")

    # -- 9 : changement d'onglet puis retour -------------------------------

    def test_quitter_puis_revenir_sur_tables_reprend_correctement(self):
        t1 = self.db.list_tables()[0]["id"]
        t2 = self.db.add_table("Table 2")
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)

        self.win._refresh_tables_tab()  # affichage initial de l'onglet Tables
        self.assertTrue(self._pack_visible(self.win._movement_pending_frame))
        self.assertIsNotNone(self.win._tables_blink_after_id)

        # "Quitter l'onglet" : rien d'autre ne rafraîchit Tables entre-temps
        # (voir _refresh_all/_tick, qui ne le font QUE si Tables est
        # l'onglet courant) — seul un retour dessus (_on_notebook_tab_
        # changed -> _refresh_all -> _refresh_tables_tab) le refait.
        self.win._refresh_tables_tab()  # "retour" sur l'onglet

        self.assertTrue(self._pack_visible(self.win._movement_pending_frame))
        self.assertEqual(self.win._tables_blink_ids, {t1, t2})
        self.assertIsNotNone(self.win._tables_blink_after_id)

    # -- 10 : clic Terminé depuis Tables ------------------------------------

    def test_clic_termine_depuis_tables_arrete_tout_immediatement(self):
        t1 = self.db.list_tables()[0]["id"]
        t2 = self.db.add_table("Table 2")
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)
        self.win._refresh_tables_tab()
        self.assertIsNotNone(self.win._tables_blink_after_id)

        self.win._finish_movement_alert_from_tables()

        self.assertEqual(self.db.get_setting_int("movement_alert_active", 0), 0)
        self.assertEqual(self.db.count_seat_moves(), 0)
        self.assertFalse(self._pack_visible(self.win._movement_pending_frame))
        self.assertIsNone(self.win._tables_blink_after_id)
        self.assertEqual(self.win._tables_blink_ids, set())
        # Reste bien sur Tables (switch_to_clock=False) : jamais basculé
        # sur Chronomètre comme le ferait le bouton de l'onglet Mouvements.
        self.win.notebook.select.assert_not_called()

    # -- 11 : "Terminé" validé ailleurs (mécanisme existant) ----------------

    def test_termine_valide_ailleurs_arrete_aussi_ici_au_prochain_refresh(self):
        t1 = self.db.list_tables()[0]["id"]
        self.db.add_table("Table 2")
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)
        self.win._refresh_tables_tab()
        self.assertIsNotNone(self.win._tables_blink_after_id)

        # Mécanisme EXISTANT (onglet Mouvements / téléphone / raccourci
        # clavier) : switch_to_clock reste à sa valeur par défaut (True),
        # comportement strictement inchangé pour cet appelant-là.
        self.win._finish_movement_alert()
        self.win.notebook.select.assert_called_once_with(self.win.clock_tab)

        # Prochain rafraîchissement approprié de Tables (retour dessus).
        self.win._refresh_tables_tab()

        self.assertFalse(self._pack_visible(self.win._movement_pending_frame))
        self.assertIsNone(self.win._tables_blink_after_id)

    # -- 12 : fermeture pendant clignotement --------------------------------

    def test_destruction_pendant_clignotement_pas_d_erreur(self):
        t1 = self.db.list_tables()[0]["id"]
        self.db.add_table("Table 2")
        self._seat(t1, 1, "Alice")
        self._record_pending_move("Bougé", "Table 2", 1, "Table 1", 2)
        self.win._refresh_tables_tab()
        self.assertIsNotNone(self.win._tables_blink_after_id)

        # Comme _cleanup_for_close (fermeture du tournoi/de la fenêtre) :
        # annulation explicite, sans exception.
        self.win._cancel_tables_blink()
        self.assertIsNone(self.win._tables_blink_after_id)

        # Widgets détruits entre-temps (fenêtre fermée) : un appel résiduel
        # au tick (ex : callback déjà en file d'attente Tk juste avant
        # l'annulation ci-dessus) ne doit jamais lever d'exception.
        self.win.tables_inner.destroy()
        try:
            self.win._tables_blink_tick()
        except Exception as e:  # pragma: no cover - le test échoue si levée
            self.fail(f"_tables_blink_tick a levé une exception après destruction : {e}")

    # -- 13/14 : scénario complet 11 -> 10 -> 4 puis Terminé ----------------

    def test_scenario_complet_11_joueurs_vers_4_puis_termine(self):
        """Reproduit exactement le scénario du diagnostic : 11 joueurs/2
        tables (max 7/table), élimination du 11e -> passage à 10 ->
        fermeture automatique de Table 2 (convention "table finale", voir
        database.FINAL_TABLE_MAX_SEATS) -> puis éliminations
        supplémentaires JUSQU'À 4, sans jamais cliquer "Terminé" — la
        base doit rester correcte à chaque étape (4 joueurs, tous
        réellement sur Table 1, Table 2 inactive) alors que l'écran
        continue d'afficher 2 des survivants à leur ancienne place
        (Table 2, gelée) tant que "Terminé" n'est pas validé. Puis
        "Terminé" : tout redevient cohérent immédiatement."""
        t1 = self.db.list_tables()[0]["id"]
        # Table 1 : P1..P6 ; Table 2 : P7..P11 — 11 joueurs, réparation
        # déterministe (pas de dépendance à l'algorithme de placement
        # automatique, voir _seat).
        p_ids = {}
        for i in range(1, 7):
            p_ids[f"P{i}"] = self._seat(t1, i, f"P{i}")
        t2 = self.db.add_table("Table 2")
        for i in range(7, 12):
            p_ids[f"P{i}"] = self._seat(t2, i - 6, f"P{i}")
        self.assertEqual(len(self.db.list_players(status="active")), 11)

        self.win._refresh_tables_tab()
        self.assertFalse(self._pack_visible(self.win._movement_pending_frame))

        # Élimine P1 (Table 1) : 11 -> 10 joueurs actifs -> convention
        # "table finale" (voir database.FINAL_TABLE_MAX_SEATS=10) ->
        # Table 2 (P7..P11) fusionne sur Table 1, même si 7 < 10.
        moves = self.db.eliminate_player(p_ids["P1"])
        self.assertTrue(moves)  # un vrai mouvement a bien eu lieu
        self.db.set_settings({"movement_alert_active": 1})  # voir _trigger_movement_alert

        tables_after_close = list(self.db.list_tables())
        self.assertEqual(len(tables_after_close), 1)
        self.assertEqual(
            {p["table_id"] for p in self.db.list_players(status="active")}, {t1},
        )

        # Continue les éliminations JUSQU'À 4, sans jamais cliquer
        # "Terminé" : élimine P2,P3,P4 (jamais déplacés) et P7,P8,P9
        # (déplacés), en gardant volontairement P5,P6 (jamais déplacés)
        # et P10,P11 (déplacés) comme survivants finaux — exactement la
        # combinaison "2 jamais déplacés + 2 déplacés en attente" qui
        # reproduit le 2+2 fantôme observé sur le vrai snapshot.
        for name in ("P2", "P3", "P4", "P7", "P8", "P9"):
            self.db.eliminate_player(p_ids[name])

        active = self.db.list_players(status="active")
        self.assertEqual({p["name"] for p in active}, {"P5", "P6", "P10", "P11"})
        self.assertEqual({p["table_id"] for p in active}, {t1})  # vérité base : tous sur Table 1
        self.assertEqual(self.db.get_setting_int("movement_alert_active", 0), 1)  # jamais cliqué

        self.win._refresh_tables_tab()

        # Écran : Table 1 (réels, jamais déplacés) + Table 2 fantôme
        # (déplacés, encore gelés à leur ancienne place) — le 2+2 exact du
        # diagnostic, alors que la base n'a qu'une seule table active.
        frame_t1 = self._frame_by_title_prefix("Table 1")
        frame_t2 = self._frame_by_title_prefix("Table 2")
        self.assertIsNotNone(frame_t1)
        self.assertIsNotNone(frame_t2)
        self.assertEqual(
            {n.split("— ")[-1] for n in self._names_in_frame(frame_t1)}, {"P5", "P6"},
        )
        self.assertEqual(
            {n.split("— ")[-1] for n in self._names_in_frame(frame_t2)}, {"P10", "P11"},
        )
        self.assertTrue(self._pack_visible(self.win._movement_pending_frame))
        self.assertEqual(self.win._tables_blink_ids, {t1, t2})
        self.assertIsNotNone(self.win._tables_blink_after_id)

        # Clic sur "Terminé" (depuis Tables) : résolution immédiate et
        # complète.
        self.win._finish_movement_alert_from_tables()

        self.assertEqual(self.db.get_setting_int("movement_alert_active", 0), 0)
        self.assertEqual(self.db.count_seat_moves(), 0)
        self.assertFalse(self._pack_visible(self.win._movement_pending_frame))
        self.assertIsNone(self.win._tables_blink_after_id)

        frame_t1_final = self._frame_by_title_prefix("Table 1")
        self.assertIsNone(self._frame_by_title_prefix("Table 2"))  # plus de carte fantôme
        self.assertEqual(
            {n.split("— ")[-1] for n in self._names_in_frame(frame_t1_final)},
            {"P5", "P6", "P10", "P11"},
        )
        self.assertEqual(frame_t1_final.cget("text"), "Table 1 — 4 joueurs")


if __name__ == "__main__":
    unittest.main()
