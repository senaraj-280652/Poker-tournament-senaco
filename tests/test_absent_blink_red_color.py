# -*- coding: utf-8 -*-
"""Correctif du 2026-09-29 — "clignotement rouge des absents" : avant ce
correctif, une ligne signalée absente portait DEUX tags Tk, `(row_tag,
"absent_blink")` (row_tag = "evenrow"/"oddrow", pour le zébrage). Un
ttk::treeview donne priorité au PREMIER tag de la liste pour une
propriété de style en conflit — `row_tag` ne définissant que
`background`, son fond crème écrasait systématiquement le fond rouge
d'alerte d'`absent_blink`, ne laissant visible que l'alternance de
`foreground` (crème/noir, jamais disputée par row_tag) : un clignotement
perçu, mais jamais rouge.

Correctif : une ligne en cours de clignotement porte DÉSORMAIS
uniquement le tag "absent_blink" (voir App._refresh_players_tab), sans
jamais le combiner avec evenrow/oddrow — supprime le conflit à la
racine. App._clear_absent_blink_tag (retour depuis le téléphone) et le
rafraîchissement complet (retour depuis le Mac, via _reinstate_selected)
restituent tous deux le zébrage habituel dès que le clignotement
s'arrête.

Couvre :
1. Une ligne signalée absente porte EXACTEMENT le tag ("absent_blink",)
   — jamais combiné avec evenrow/oddrow.
2. Le fond passe réellement au rouge d'alerte (DANGER_RED) pendant la
   phase "on" du cycle, et le texte reste lisible dans les deux phases
   (contraste crème/rouge, noir/crème).
3. Plusieurs joueurs absents clignotent simultanément, en phase.
4. Le retour d'un joueur arrête son clignotement ET restitue le
   zébrage habituel de SA ligne — aussi bien depuis le Mac
   (rafraîchissement complet) que depuis le téléphone (mise à jour
   ligne par ligne).
5. Le zébrage des AUTRES joueurs (non concernés) reste inchangé."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import player_photos  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name="A"):
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1})
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


PLAYERS_COLUMNS = (
    "sel", "id", "name", "club", "table", "seat", "chips", "buyin", "rebuy",
    "addon", "bounty", "status", "rang", "elim_time", "elim_round", "eliminated_by",
)

_GRAFTED_METHODS = (
    "_refresh_players_tab", "_update_tournament_started_buttons",
    "_pending_old_seat_by_name", "_update_checked_count_label",
    "_update_undo_elimination_button_state", "_players_checkbox_drives_accueil",
    "_update_players_stats_label", "_cancel_players_absent_blink",
    "_players_absent_blink_tick", "_update_sort_headings", "_accueil_status_label",
    "_reinstate_selected", "_checked_or_selected_ids", "_clear_checked",
    "_sync_checkbox_for_arrival", "_clear_absent_blink_tag",
    "_apply_accueil_status_display", "_apply_checkbox_display",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class AbsentBlinkRedColorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        roster_patcher = patch.object(roster, "list_clubs", return_value=[])
        self.addCleanup(roster_patcher.stop)
        roster_patcher.start()
        photo_patcher = patch.object(player_photos, "get_photo_path", return_value=None)
        self.addCleanup(photo_patcher.stop)
        photo_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="absent_blink_red_color_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        # 4 joueurs, dans cet ordre : Alice (0, evenrow), Bob (1, oddrow),
        # Charlie (2, evenrow), Diana (3, oddrow) — avant tout absent.
        self.pid_alice = self.db.add_player("Alice")
        self.pid_bob = self.db.add_player("Bob")
        self.pid_charlie = self.db.add_player("Charlie")
        self.pid_diana = self.db.add_player("Diana")
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
        self.win._refresh_remote_players_cache = lambda: None
        self.win._refresh_remote_checkin_cache = lambda: None
        self.win._refresh_all = self.win._refresh_players_tab

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

        # self.win == self.root, PARTAGÉ entre toutes les méthodes de
        # cette classe (voir setUpClass) : ces attributs de cycle de
        # clignotement peuvent donc avoir survécu à un test précédent —
        # remis à un état neutre connu ICI pour que chaque test reste
        # déterministe, indépendamment de l'ordre d'exécution.
        self.win._players_absent_blink_active = False
        self.win._players_absent_blink_phase = False
        self.win._players_absent_blink_after_id = None

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

    # -- 1. Composition exacte des tags ---------------------------------

    def test_ligne_absente_porte_uniquement_absent_blink(self):
        self.db.declare_absent(self.pid_bob)

        self.win._refresh_players_tab()

        tags = self.win.players_tree.item(str(self.pid_bob), "tags")
        self.assertEqual(tags, ("absent_blink",))

    def test_ligne_non_absente_garde_son_zebrage_normal(self):
        self.db.declare_absent(self.pid_bob)

        self.win._refresh_players_tab()

        # Alice = index 0 -> evenrow, Charlie = index 2 -> evenrow,
        # Diana = index 3 -> oddrow (Bob, index 1, est absent : voir
        # test ci-dessus).
        self.assertEqual(self.win.players_tree.item(str(self.pid_alice), "tags"), ("evenrow",))
        self.assertEqual(self.win.players_tree.item(str(self.pid_charlie), "tags"), ("evenrow",))
        self.assertEqual(self.win.players_tree.item(str(self.pid_diana), "tags"), ("oddrow",))

    # -- 2. Le fond devient réellement rouge, texte lisible -------------

    def test_le_fond_devient_rouge_pendant_la_phase_on(self):
        self.db.declare_absent(self.pid_bob)

        # _refresh_players_tab démarre le cycle et déclenche déjà un
        # premier tick immédiat (transition "aucun absent" -> "au moins
        # un absent", voir sa docstring) : la phase "on" (rouge) est donc
        # déjà active sans appel supplémentaire.
        self.win._refresh_players_tab()

        self.assertTrue(self.win._players_absent_blink_phase)
        bg = self.win.players_tree.tag_configure("absent_blink", "background")
        fg = self.win.players_tree.tag_configure("absent_blink", "foreground")
        self.assertEqual(str(bg), main.DANGER_RED)
        self.assertEqual(str(fg), main.CREAM)

    def test_le_fond_redevient_neutre_pendant_la_phase_off(self):
        self.db.declare_absent(self.pid_bob)
        self.win._refresh_players_tab()  # 1er tick automatique : phase "on"

        self.win._players_absent_blink_tick()  # 2e tick : phase "off"

        self.assertFalse(self.win._players_absent_blink_phase)
        bg = self.win.players_tree.tag_configure("absent_blink", "background")
        fg = self.win.players_tree.tag_configure("absent_blink", "foreground")
        self.assertEqual(str(bg), main.CREAM)
        self.assertEqual(str(fg), "black")

    def test_contraste_texte_suffisant_dans_les_deux_phases(self):
        """Lisibilité du nom (demande explicite) : jamais la même
        couleur pour le texte et le fond, dans aucune des deux phases."""
        self.db.declare_absent(self.pid_bob)
        self.win._refresh_players_tab()

        self.win._players_absent_blink_tick()
        bg_on = str(self.win.players_tree.tag_configure("absent_blink", "background"))
        fg_on = str(self.win.players_tree.tag_configure("absent_blink", "foreground"))
        self.assertNotEqual(bg_on, fg_on)

        self.win._players_absent_blink_tick()
        bg_off = str(self.win.players_tree.tag_configure("absent_blink", "background"))
        fg_off = str(self.win.players_tree.tag_configure("absent_blink", "foreground"))
        self.assertNotEqual(bg_off, fg_off)

    # -- 3. Plusieurs absents simultanément ------------------------------

    def test_plusieurs_absents_clignotent_simultanement_en_phase(self):
        self.db.declare_absent(self.pid_bob)
        self.db.declare_absent(self.pid_diana)

        self.win._refresh_players_tab()

        self.assertEqual(self.win.players_tree.item(str(self.pid_bob), "tags"), ("absent_blink",))
        self.assertEqual(self.win.players_tree.item(str(self.pid_diana), "tags"), ("absent_blink",))
        # Un seul tag_configure partagé pilote les DEUX lignes à la fois
        # (voir _players_absent_blink_tick) : elles sont donc
        # nécessairement toujours en phase l'une avec l'autre — vérifié
        # en observant que le tick automatique de démarrage (déclenché
        # par _refresh_players_tab ci-dessus) les a déjà fait passer
        # toutes deux au rouge d'alerte ensemble.
        bg = str(self.win.players_tree.tag_configure("absent_blink", "background"))
        self.assertEqual(bg, main.DANGER_RED)
        # Les deux lignes utilisent le MÊME tag => même couleur, par
        # construction Tk (impossible qu'elles divergent tant qu'elles
        # portent toutes deux "absent_blink").
        self.assertEqual(self.win.players_tree.item(str(self.pid_bob), "tags"), ("absent_blink",))
        self.assertEqual(self.win.players_tree.item(str(self.pid_diana), "tags"), ("absent_blink",))

    # -- 4. Le retour restitue le zébrage habituel -----------------------

    def test_retour_depuis_le_mac_restitue_le_zebrage(self):
        self.db.declare_absent(self.pid_bob)  # index 1 -> oddrow attendu
        self.win._refresh_players_tab()
        self.assertEqual(self.win.players_tree.item(str(self.pid_bob), "tags"), ("absent_blink",))

        self.win.checked_player_ids = {self.pid_bob}
        self.win._reinstate_selected()

        tags = self.win.players_tree.item(str(self.pid_bob), "tags")
        self.assertEqual(tags, ("oddrow",))
        self.assertNotIn("absent_blink", tags)

    def test_retour_depuis_le_telephone_restitue_le_zebrage_de_cette_ligne(self):
        self.db.declare_absent(self.pid_bob)  # index 1 -> oddrow attendu
        self.win._refresh_players_tab()

        self.db.confirm_arrival(self.pid_bob)
        self.win._sync_checkbox_for_arrival(self.pid_bob, True)

        tags = self.win.players_tree.item(str(self.pid_bob), "tags")
        self.assertEqual(tags, ("oddrow",))
        self.assertNotIn("absent_blink", tags)

    def test_retour_arrete_le_clignotement_mais_pas_pour_les_autres(self):
        self.db.declare_absent(self.pid_bob)
        self.db.declare_absent(self.pid_diana)
        self.win._refresh_players_tab()

        self.db.confirm_arrival(self.pid_bob)
        self.win._sync_checkbox_for_arrival(self.pid_bob, True)

        self.assertNotIn("absent_blink", self.win.players_tree.item(str(self.pid_bob), "tags"))
        self.assertIn("absent_blink", self.win.players_tree.item(str(self.pid_diana), "tags"))
        self.assertTrue(self.win._players_absent_blink_active)

    # -- 5. Zébrage des autres joueurs inchangé --------------------------

    def test_zebrage_des_autres_joueurs_inchange_apres_un_retour(self):
        self.db.declare_absent(self.pid_bob)
        self.win._refresh_players_tab()

        self.win.checked_player_ids = {self.pid_bob}
        self.win._reinstate_selected()

        self.assertEqual(self.win.players_tree.item(str(self.pid_alice), "tags"), ("evenrow",))
        self.assertEqual(self.win.players_tree.item(str(self.pid_charlie), "tags"), ("evenrow",))
        self.assertEqual(self.win.players_tree.item(str(self.pid_diana), "tags"), ("oddrow",))


if __name__ == "__main__":
    unittest.main()
