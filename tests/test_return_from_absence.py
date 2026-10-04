# -*- coding: utf-8 -*-
"""Correctif du 2026-09-29 — "anomalie de réinscription sur Mac" :
avant ce correctif, confirmer l'arrivée d'un joueur signalé absent
(Database.confirm_arrival) n'effaçait jamais `absent_declared_at` — le
joueur redevenait "Actif" à l'affichage (voir App._accueil_status_label)
mais continuait de clignoter en rouge, et surtout le Mac n'avait AUCUN
moyen équivalent de traiter ce retour (le bouton "Réinscrire" appelait
uniquement `cancel_absence`, qui n'horodate jamais `arrived_at`).

Couvre :
1. Database.confirm_arrival efface désormais `absent_declared_at` dans
   la MÊME requête UPDATE que `arrived_at` — jamais d'état intermédiaire
   incohérent, refuse toujours après expiration du délai (AUCUNE
   écriture dans ce cas, ni l'un ni l'autre champ).
2. App._reinstate_selected (Mac), pour un joueur actif signalé absent,
   confirme désormais l'arrivée — même résultat que l'iPhone (absence
   annulée, arrivée confirmée, "Actif" affiché) — refuse après
   expiration (même garde-fou, message inchangé), sans effet sur un
   joueur actif jamais signalé absent, et SANS RIEN CHANGER au chemin
   "réellement désactivé -> reinstate_player" (jetons/table/siège/
   rééquilibrage historique).
3. Le clignotement s'arrête immédiatement, aussi bien pour un retour
   confirmé depuis le Mac (rafraîchissement complet, tags recalculés)
   que depuis le téléphone (App._clear_absent_blink_tag, ligne par
   ligne, sans reconstruire tout le tableau).
4. Équivalence stricte Mac / iPhone : les deux chemins produisent
   EXACTEMENT le même état final en base.
5. Interdiction de déclarer absent un joueur déjà accueilli : inchangée
   (Database.declare_absent)."""
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


# =====================================================================
# 1. Database.confirm_arrival — efface aussi absent_declared_at
# =====================================================================
class ConfirmArrivalClearsAbsenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="confirm_arrival_clears_absence_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)

    def test_confirmer_l_arrivee_efface_le_signalement_d_absence(self):
        self.db.declare_absent(self.pid)
        self.assertIsNotNone(self.db.get_player(self.pid)["absent_declared_at"])

        result = self.db.confirm_arrival(self.pid)

        self.assertTrue(result)
        after = self.db.get_player(self.pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertIsNone(after["absent_declared_at"])

    def test_les_deux_champs_changent_ensemble_jamais_seulement_l_un(self):
        """Non-régression de l'atomicité : une seule requête UPDATE pose
        arrived_at ET efface absent_declared_at — jamais un état où l'un
        serait posé et l'autre pas."""
        self.db.declare_absent(self.pid)
        before = self.db.get_player(self.pid)
        self.assertIsNone(before["arrived_at"])
        self.assertIsNotNone(before["absent_declared_at"])

        self.db.confirm_arrival(self.pid)

        after = self.db.get_player(self.pid)
        self.assertTrue(bool(after["arrived_at"]) and not bool(after["absent_declared_at"]))

    def test_confirmer_l_arrivee_d_un_joueur_jamais_signale_absent_ne_change_rien_d_autre(self):
        """Non-régression : idempotent, sans effet de bord pour le cas
        normal (jamais signalé absent)."""
        result = self.db.confirm_arrival(self.pid)

        self.assertTrue(result)
        after = self.db.get_player(self.pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertIsNone(after["absent_declared_at"])

    def test_refuse_apres_expiration_du_delai_aucune_ecriture(self):
        """Conserve l'interdiction existante : après expiration, refuse
        purement et simplement — ni arrived_at ni absent_declared_at ne
        doivent changer (contrainte explicitement demandée)."""
        self.db.declare_absent(self.pid)
        self.db.set_settings({
            "tournament_start_epoch": int(time.time()) - 65 * 60,
            "absence_duration_minutes": 60,
        })
        self.assertTrue(self.db.absence_deadline_passed())

        result = self.db.confirm_arrival(self.pid)

        self.assertFalse(result)
        after = self.db.get_player(self.pid)
        self.assertIsNone(after["arrived_at"])
        self.assertIsNotNone(after["absent_declared_at"])

    def test_declare_absent_refuse_toujours_pour_un_joueur_deja_accueilli(self):
        """Non-régression explicite (contrainte demandée) : ce correctif
        ne change rien à l'interdiction inverse, déjà en place."""
        self.db.confirm_arrival(self.pid)

        self.assertFalse(self.db.declare_absent(self.pid))
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])


# =====================================================================
# 2. App._reinstate_selected (Mac) — traite le retour d'un absent
# =====================================================================
class _FakeAppForReinstate:
    """Correctif du 2026-10-03 ("Inscrire ignore le joueur sélectionné
    quand un autre est déjà accueilli") : _reinstate_selected lit
    désormais players_tree.selection() directement quand l'accueil
    pilote la case — exposé ici (mêmes _ids que _checked_or_selected_ids,
    ce conflit n'étant pas l'objet de ces tests, voir tests/test_
    inscrire_ignore_deja_accueillis.py pour celui-ci) avec la méthode
    réelle greffée pour refléter le vrai réglage accueil/démarrage."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)
        self.clear_checked_calls = 0
        self.refresh_all_calls = 0
        self.refresh_remote_players_calls = 0
        self.refresh_remote_checkin_calls = 0
        self.players_tree = types.SimpleNamespace(
            selection=lambda: [str(pid) for pid in self._ids]
        )

    def _players_checkbox_drives_accueil(self):
        return main.App._players_checkbox_drives_accueil(self)

    def _checked_or_selected_ids(self):
        return list(self._ids)

    def _clear_checked(self):
        self.clear_checked_calls += 1

    def _refresh_all(self):
        self.refresh_all_calls += 1

    def _refresh_remote_players_cache(self):
        self.refresh_remote_players_calls += 1

    def _refresh_remote_checkin_cache(self):
        self.refresh_remote_checkin_calls += 1


class ReinstateSelectedHandlesReturnFromAbsenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_return_from_absence_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)

    def test_confirme_l_arrivee_et_annule_l_absence_ensemble(self):
        self.db.declare_absent(self.pid)
        app = _FakeAppForReinstate(self.db, [self.pid])

        main.App._reinstate_selected(app)

        after = self.db.get_player(self.pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertIsNone(after["absent_declared_at"])
        self.assertEqual(after["status"], "active")

    def test_statut_affiche_devient_actif(self):
        """Vérifie le résultat via App._accueil_status_label, exactement
        ce que l'utilisateur voit dans la colonne "Statut"."""
        self.db.declare_absent(self.pid)
        app = _FakeAppForReinstate(self.db, [self.pid])
        win = types.SimpleNamespace(db=self.db)
        win._accueil_status_label = types.MethodType(main.App._accueil_status_label, win)

        main.App._reinstate_selected(app)

        label = win._accueil_status_label(self.db.get_player(self.pid))
        self.assertEqual(label, "Actif")

    def test_ne_touche_ni_jetons_ni_table_ni_siege_ni_rééquilibrage(self):
        self.db.declare_absent(self.pid)
        self.db.set_chips(self.pid, 24680)
        before = self.db.get_player(self.pid)
        app = _FakeAppForReinstate(self.db, [self.pid])
        seat_moves_before = self.db.count_seat_moves()

        main.App._reinstate_selected(app)

        after = self.db.get_player(self.pid)
        self.assertEqual(after["chips"], 24680)
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])
        self.assertEqual(self.db.count_seat_moves(), seat_moves_before)

    def test_refuse_apres_expiration_et_affiche_le_message(self):
        self.db.declare_absent(self.pid)
        self.db.set_settings({
            "tournament_start_epoch": int(time.time()) - 65 * 60,
            "absence_duration_minutes": 60,
        })
        app = _FakeAppForReinstate(self.db, [self.pid])

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._reinstate_selected(app)

        mock_info.assert_called_once()
        after = self.db.get_player(self.pid)
        self.assertIsNone(after["arrived_at"])
        self.assertIsNotNone(after["absent_declared_at"])

    def test_actif_pre_inscrit_confirme_desormais_son_arrivee(self):
        """Extension du 2026-09-29 ("étendre le bouton Réinscrire") : un
        joueur actif "Pré-inscrit" (jamais signalé absent) confirme
        maintenant son arrivée via Réinscrire, exactement comme un
        joueur revenu d'une absence — voir tests/
        test_reinstate_preinscrit_extension.py pour la couverture
        dédiée de cette extension."""
        app = _FakeAppForReinstate(self.db, [self.pid])

        main.App._reinstate_selected(app)

        after = self.db.get_player(self.pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertIsNone(after["absent_declared_at"])

    def test_actif_deja_arrive_reste_sans_effet(self):
        """Non-régression : un joueur déjà "Actif" (arrivée déjà
        confirmée) ne doit jamais être retouché "par accident" en
        cliquant Réinscrire sur une sélection qui le contient."""
        self.db.confirm_arrival(self.pid)
        before = self.db.get_player(self.pid)["arrived_at"]
        app = _FakeAppForReinstate(self.db, [self.pid])

        main.App._reinstate_selected(app)

        after = self.db.get_player(self.pid)
        self.assertEqual(after["arrived_at"], before)
        self.assertIsNone(after["absent_declared_at"])

    def test_joueur_reellement_desactive_utilise_toujours_reinstate_player(self):
        """Non-régression explicite (contrainte demandée) : le chemin
        historique reste intact, jamais touché par ce correctif."""
        self.db.withdraw_player(self.pid)
        app = _FakeAppForReinstate(self.db, [self.pid])

        main.App._reinstate_selected(app)

        after = self.db.get_player(self.pid)
        self.assertEqual(after["status"], "active")
        self.assertIsNotNone(after["table_id"])  # reinstate_player réattribue un siège
        self.assertEqual(after["chips"], self.db.get_setting_int("starting_chips", 10000))


# =====================================================================
# 3. Clignotement : s'arrête dès le retour confirmé
# =====================================================================
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
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class BlinkStopsOnReturnFromAbsenceMacTest(unittest.TestCase):
    """Depuis le Mac : le rafraîchissement complet déclenché par
    _reinstate_selected (via _refresh_all -> _refresh_players_tab)
    recalcule les tags depuis la base — le clignotement s'arrête
    immédiatement puisque absent_declared_at est désormais NULL."""

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

        self._tmp = tempfile.TemporaryDirectory(prefix="blink_stops_mac_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(self.pid)

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = {self.pid}
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in _GRAFTED_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_players_cache = lambda: None
        self.win._refresh_remote_checkin_cache = lambda: None
        # _reinstate_selected appelle self._refresh_all() (App réelle :
        # rafraîchit plusieurs onglets) — ici, seul _refresh_players_tab
        # est greffé et suffit à ce test (c'est lui qui recalcule les
        # tags de clignotement depuis la base).
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

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

        # Démarre le cycle de clignotement (état initial : le joueur EST
        # signalé absent).
        self.win._refresh_players_tab()
        self.assertIn("absent_blink", self.win.players_tree.item(str(self.pid), "tags"))
        self.assertTrue(self.win._players_absent_blink_active)

    def test_le_clignotement_s_arrete_apres_reinscrire(self):
        self.win._reinstate_selected()

        tags = self.win.players_tree.item(str(self.pid), "tags")
        self.assertNotIn("absent_blink", tags)
        self.assertFalse(self.win._players_absent_blink_active)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class BlinkStopsOnReturnFromAbsencePhoneTest(unittest.TestCase):
    """Depuis le téléphone : App._clear_absent_blink_tag retire le tag
    de CETTE seule ligne (sans reconstruire tout le tableau) et arrête
    le cycle partagé si plus aucune ligne ne clignote."""

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

        self._tmp = tempfile.TemporaryDirectory(prefix="blink_stops_phone_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid_a = self.db.add_player("Alice")
        self.pid_b = self.db.add_player("Bob")
        _start_tournament(self.db)
        self.db.declare_absent(self.pid_a)
        self.db.declare_absent(self.pid_b)

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = set()
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in _GRAFTED_METHODS + ("_sync_checkbox_for_arrival", "_clear_absent_blink_tag", "_apply_accueil_status_display", "_apply_checkbox_display"):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_players_cache = lambda: None
        self.win._refresh_remote_checkin_cache = lambda: None

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

        self.win._refresh_players_tab()
        self.assertTrue(self.win._players_absent_blink_active)

    def test_confirmation_telephone_arrete_le_clignotement_de_cette_ligne(self):
        self.db.confirm_arrival(self.pid_a)

        self.win._sync_checkbox_for_arrival(self.pid_a, True)

        tags_a = self.win.players_tree.item(str(self.pid_a), "tags")
        self.assertNotIn("absent_blink", tags_a)

    def test_le_cycle_continue_si_un_autre_joueur_clignote_encore(self):
        self.db.confirm_arrival(self.pid_a)

        self.win._sync_checkbox_for_arrival(self.pid_a, True)

        tags_b = self.win.players_tree.item(str(self.pid_b), "tags")
        self.assertIn("absent_blink", tags_b)
        self.assertTrue(self.win._players_absent_blink_active)

    def test_le_cycle_s_arrete_quand_le_dernier_absent_revient(self):
        self.db.confirm_arrival(self.pid_a)
        self.win._sync_checkbox_for_arrival(self.pid_a, True)
        self.db.confirm_arrival(self.pid_b)

        self.win._sync_checkbox_for_arrival(self.pid_b, True)

        self.assertFalse(self.win._players_absent_blink_active)


# =====================================================================
# 4. Équivalence stricte Mac / iPhone
# =====================================================================
class MacAndPhoneProduceIdenticalResultTest(unittest.TestCase):
    """Les deux chemins (Réinscrire sur Mac, confirmer l'arrivée depuis
    l'accueil iPhone) doivent produire EXACTEMENT le même état final en
    base pour un joueur signalé absent revenu avant le délai."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mac_phone_equivalence_test_")
        self.addCleanup(self._tmp.cleanup)

    def _prepare(self, name):
        db = _new_db(self._tmp.name, name=name)
        pid = db.add_player("Alice")
        _start_tournament(db)
        db.declare_absent(pid)
        return db, pid

    def test_meme_etat_final_en_base(self):
        db_mac, pid_mac = self._prepare("Mac")
        self.addCleanup(db_mac.conn.close)
        db_phone, pid_phone = self._prepare("Phone")
        self.addCleanup(db_phone.conn.close)

        # Mac : bouton "Réinscrire".
        app = _FakeAppForReinstate(db_mac, [pid_mac])
        main.App._reinstate_selected(app)

        # iPhone : confirmer l'arrivée depuis l'accueil des joueurs
        # (App._remote_confirm_arrival, voir _remote_confirm_arrival_
        # request — appelée ici directement, comme le fait _poll_voice_
        # queue sur le thread Tk).
        fake_phone_app = types.SimpleNamespace(
            db=db_phone, _remote_arrival_results={},
            _refresh_remote_checkin_cache=lambda: None,
            _sync_checkbox_for_arrival=lambda player_id, arrived: None,
        )
        main.App._remote_confirm_arrival(fake_phone_app, pid_phone, request_id="req1")

        after_mac = db_mac.get_player(pid_mac)
        after_phone = db_phone.get_player(pid_phone)
        for key in ("arrived_at", "absent_declared_at"):
            self.assertEqual(bool(after_mac[key]), bool(after_phone[key]), key)
        self.assertIsNotNone(after_mac["arrived_at"])
        self.assertIsNone(after_mac["absent_declared_at"])
        self.assertEqual(after_mac["status"], after_phone["status"])
        self.assertEqual(after_mac["chips"], after_phone["chips"])


if __name__ == "__main__":
    unittest.main()
