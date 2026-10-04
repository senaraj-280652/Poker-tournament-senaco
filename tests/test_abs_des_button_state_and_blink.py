# -*- coding: utf-8 -*-
"""Lot 2 — "Gestion des absences et des retardataires" (cahier des
charges du 2026-09-28), couverture de :

- App._update_tournament_started_buttons : "Abs/Dés" grisé TANT QUE le
  tournoi n'a pas démarré (même raison qu'"Éliminer" — l'absence est un
  concept post-démarrage), texte de l'info-bulle basculé en conséquence.
- App._refresh_players_tab : tague "absent_blink" chaque ligne ACTIVE
  signalée absente, démarre le cycle de clignotement (_players_absent_
  blink_tick) s'il y en a au moins une, l'arrête sinon.

Même harnais qu'ailleurs dans cette suite pour du VRAI Tk (racine
partagée par classe via setUpClass/tearDownClass, jamais détruite en
cours de route — voir tests/test_players_checkbox_header_sort.py, dont
ce fichier reprend directement le patron de doublure)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
import player_photos  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


PLAYERS_COLUMNS = (
    "sel", "id", "name", "club", "table", "seat", "chips", "buyin", "rebuy",
    "addon", "bounty", "status", "rang", "elim_time", "elim_round", "eliminated_by",
)

_GRAFTED_METHODS = (
    "_refresh_players_tab", "_update_tournament_started_buttons",
    "_pending_old_seat_by_name", "_update_checked_count_label",
    "_update_undo_elimination_button_state", "_players_checkbox_drives_accueil",
    "_update_players_stats_label", "_cancel_players_absent_blink",
    "_players_absent_blink_tick", "_update_sort_headings",
    # Complément du 2026-09-29 ("Pré-inscrit"/"Actif") :
    # _refresh_players_tab appelle désormais aussi _accueil_status_label
    # pour chaque joueur.
    "_accueil_status_label",
)


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    import time
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class AbsDesButtonStateTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (voir tests/test_players_
    checkbox_header_sort.py pour la même précaution anti-flakiness
    Tcl/Tk — jamais détruite avant tearDownClass)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        roster_patcher_list = patch.object(roster, "list_clubs", return_value=[])
        self.addCleanup(roster_patcher_list.stop)
        roster_patcher_list.start()
        photo_patcher = patch.object(player_photos, "get_photo_path", return_value=None)
        self.addCleanup(photo_patcher.stop)
        photo_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="abs_des_button_state_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        # « Gestion de l'accueil » activée (modification du 2026-09-29) :
        # ce test couvre précisément le grisement/texte "Abs/Dés" propre
        # au réglage activé — voir test_accueil_management_toggle.py
        # pour le comportement "Désactiver (forfait)" quand désactivé.
        self.db.set_settings({"accueil_management_enabled": 1})
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = set()
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in _GRAFTED_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

        parent = ttk.Frame(self.root)
        self.addCleanup(parent.destroy)
        self.win.players_tree = ttk.Treeview(parent, columns=PLAYERS_COLUMNS, show="tree headings")
        for c in PLAYERS_COLUMNS:
            self.win.players_tree.heading(c, text="")
        self.win.new_player_club_combo = ttk.Combobox(parent)
        self.win.stats_lbl = ttk.Label(parent)
        self.win.checked_count_lbl = ttk.Label(parent)
        self.win.delete_player_btn = ttk.Button(parent)
        self.win.delete_player_btn_tooltip = main.Tooltip(self.win.delete_player_btn, "")
        self.win.eliminate_player_btn = ttk.Button(parent)
        self.win.eliminate_player_btn_tooltip = main.Tooltip(self.win.eliminate_player_btn, "")
        self.win._eliminate_btn_tooltip_normal_text = ""
        self.win.undo_elimination_btn = ttk.Button(parent)
        self.win.abs_des_btn = ttk.Button(parent)
        self.win._abs_des_btn_tooltip_normal_text = "TEXTE NORMAL"
        self.win.abs_des_btn_tooltip = main.Tooltip(self.win.abs_des_btn, self.win._abs_des_btn_tooltip_normal_text)
        self.win.correct_arrival_btn = ttk.Button(parent)
        self.win._correct_arrival_btn_tooltip_normal_text = ""
        self.win.correct_arrival_btn_tooltip = main.Tooltip(self.win.correct_arrival_btn, "")
        self.win.player_photo_images = {}

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

    def test_grise_avant_le_demarrage(self):
        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "disabled")
        self.assertIn("n'a pas encore démarré", self.win.abs_des_btn_tooltip.text)

    def test_actif_apres_le_demarrage(self):
        _start_tournament(self.db)

        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "normal")
        self.assertEqual(self.win.abs_des_btn_tooltip.text, "TEXTE NORMAL")

    def test_se_regrise_pour_un_tournoi_jamais_redemarre(self):
        """"Se remet d'eux-mêmes dans leur état initial pour un nouveau
        tournoi, où clock_started repart à 0" (même garantie que Éliminer/
        Supprimer, voir _update_tournament_started_buttons)."""
        _start_tournament(self.db)
        self.win._refresh_players_tab()
        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "normal")

        self.db.set_settings({"clock_started": 0, "tournament_start_epoch": 0})
        self.win._refresh_players_tab()

        self.assertEqual(str(self.win.abs_des_btn.cget("state")), "disabled")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayersAbsentBlinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        roster_patcher_list = patch.object(roster, "list_clubs", return_value=[])
        self.addCleanup(roster_patcher_list.stop)
        roster_patcher_list.start()
        photo_patcher = patch.object(player_photos, "get_photo_path", return_value=None)
        self.addCleanup(photo_patcher.stop)
        photo_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="players_absent_blink_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.db.set_settings({"accueil_management_enabled": 1})
        self.addCleanup(self.db.conn.close)
        self.pid_absent = self.db.add_player("Alice")
        self.pid_normal = self.db.add_player("Bob")
        _start_tournament(self.db)

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = set()
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in _GRAFTED_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

        parent = ttk.Frame(self.root)
        self.addCleanup(parent.destroy)
        self.win.players_tree = ttk.Treeview(parent, columns=PLAYERS_COLUMNS, show="tree headings")
        for c in PLAYERS_COLUMNS:
            self.win.players_tree.heading(c, text="")
        self.win.new_player_club_combo = ttk.Combobox(parent)
        self.win.stats_lbl = ttk.Label(parent)
        self.win.checked_count_lbl = ttk.Label(parent)
        self.win.delete_player_btn = ttk.Button(parent)
        self.win.delete_player_btn_tooltip = main.Tooltip(self.win.delete_player_btn, "")
        self.win.eliminate_player_btn = ttk.Button(parent)
        self.win.eliminate_player_btn_tooltip = main.Tooltip(self.win.eliminate_player_btn, "")
        self.win._eliminate_btn_tooltip_normal_text = ""
        self.win.undo_elimination_btn = ttk.Button(parent)
        self.win.abs_des_btn = ttk.Button(parent)
        self.win._abs_des_btn_tooltip_normal_text = ""
        self.win.abs_des_btn_tooltip = main.Tooltip(self.win.abs_des_btn, "")
        self.win.correct_arrival_btn = ttk.Button(parent)
        self.win._correct_arrival_btn_tooltip_normal_text = ""
        self.win.correct_arrival_btn_tooltip = main.Tooltip(self.win.correct_arrival_btn, "")
        self.win.player_photo_images = {}

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))
        # Le cycle de clignotement démarré par un test peut laisser un
        # after() programmé — annulé explicitement en fin de CHAQUE test
        # (pas seulement à la destruction de la racine partagée), pour
        # qu'aucun test suivant de la même classe n'en hérite.
        self.addCleanup(lambda: self.win._cancel_players_absent_blink())

    def test_tague_absent_blink_pour_un_actif_signale(self):
        self.db.declare_absent(self.pid_absent)

        self.win._refresh_players_tab()

        tags = self.win.players_tree.item(str(self.pid_absent), "tags")
        self.assertIn("absent_blink", tags)
        other_tags = self.win.players_tree.item(str(self.pid_normal), "tags")
        self.assertNotIn("absent_blink", other_tags)

    def test_demarre_le_cycle_quand_au_moins_un_absent(self):
        self.db.declare_absent(self.pid_absent)

        self.win._refresh_players_tab()

        self.assertTrue(self.win._players_absent_blink_active)
        self.assertIsNotNone(getattr(self.win, "_players_absent_blink_after_id", None))

    def test_n_arrete_pas_le_cycle_sans_aucun_absent(self):
        self.win._refresh_players_tab()

        self.assertFalse(self.win._players_absent_blink_active)
        self.assertIsNone(getattr(self.win, "_players_absent_blink_after_id", None))

    def test_annulation_arrete_le_clignotement_au_rafraichissement_suivant(self):
        self.db.declare_absent(self.pid_absent)
        self.win._refresh_players_tab()
        self.assertTrue(self.win._players_absent_blink_active)

        self.db.cancel_absence(self.pid_absent)
        self.win._refresh_players_tab()

        self.assertFalse(self.win._players_absent_blink_active)
        tags = self.win.players_tree.item(str(self.pid_absent), "tags")
        self.assertNotIn("absent_blink", tags)

    def test_desactivation_arrete_le_clignotement(self):
        """Un joueur DÉSACTIVÉ (status != 'active') n'est plus tagué
        "absent_blink" même si absent_declared_at reste en base
        (historique, voir Database.withdraw_player, jamais explicitement
        effacé) — le clignotement exige status == 'active' EN PLUS."""
        self.db.declare_absent(self.pid_absent)
        self.win._refresh_players_tab()
        self.assertTrue(self.win._players_absent_blink_active)

        self.db.withdraw_player(self.pid_absent)
        self.win._refresh_players_tab()

        self.assertFalse(self.win._players_absent_blink_active)
        tags = self.win.players_tree.item(str(self.pid_absent), "tags")
        self.assertNotIn("absent_blink", tags)

    def test_tick_alterne_la_couleur_sans_planter(self):
        self.db.declare_absent(self.pid_absent)
        self.win._refresh_players_tab()

        # Un cycle est déjà en cours (démarré par _refresh_players_tab) :
        # on l'annule et on appelle le tick directement, à la main, pour
        # observer un changement de couleur déterministe sans dépendre
        # d'un after() réel (jamais de time.sleep dans cette suite).
        # _cancel_players_absent_blink() remet aussi "active" à False
        # (correctif du 2026-09-29 — "annuler" doit tout éteindre) : on le
        # remet donc explicitement à True avant CHAQUE appel manuel au
        # tick, sans quoi il se contenterait de ressortir aussitôt.
        self.win._cancel_players_absent_blink()
        self.win._players_absent_blink_active = True
        self.win._players_absent_blink_phase = False

        self.win._players_absent_blink_tick()
        first = self.win.players_tree.tag_configure("absent_blink", "background")
        self.win._cancel_players_absent_blink()
        self.win._players_absent_blink_active = True
        self.win._players_absent_blink_tick()
        second = self.win.players_tree.tag_configure("absent_blink", "background")

        self.assertNotEqual(first, second)
        self.win._cancel_players_absent_blink()


if __name__ == "__main__":
    unittest.main()
