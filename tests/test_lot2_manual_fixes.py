# -*- coding: utf-8 -*-
"""Lot 2 — corrections après les premiers essais manuels (2026-09-29) :

1. Clignotement téléphone pas systématique -> minuteur JS persistant
   (voir remote_control._ELIMINATE_PAGE), et côté Mac, un rafraîchissement
   sans changement d'état n'interrompt plus le cycle en cours.
2. Interdire de signaler absent un joueur déjà accueilli (`arrived_at`) —
   appliqué en base, sur Mac et sur téléphone.
3. Interdire d'annuler une absence après l'échéance — appliqué en base,
   sur Mac et sur téléphone. Ne touche JAMAIS reinstate_player.
4. Glisser-déposer vers "Abs/Dés" (bandeau, en haut) : ne doit plus
   nécessiter de "glisser à droite d'abord".

Voir tests/test_absence_management.py, tests/test_abs_des_and_reinstate.py,
tests/test_abs_des_button_state_and_blink.py et tests/test_remote_
absence_management.py pour la couverture Lot 2 déjà existante, non
répétée ici — uniquement les quatre correctifs."""
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
import remote_control  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name="A"):
    """« Gestion de l'accueil » activée par défaut pour ces fixtures
    (modification du 2026-09-29) : ce module teste les quatre correctifs
    du Lot 2, tous liés à l'accueil/aux absences — réglage propre au
    tournoi Open qui les active — voir test_accueil_management_toggle.py
    pour la couverture du réglage désactivé (comportement par défaut)."""
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


class _FakeAppForAbsDes:
    """Même doublure que tests/test_abs_des_and_reinstate.py (reprise à
    l'identique plutôt qu'importée, pour garder ces fichiers indépendants,
    convention déjà établie dans cette suite). Correctif du 2026-10-03
    ("Inscrire ignore le joueur sélectionné quand un autre est déjà
    accueilli") : _reinstate_selected lit désormais players_tree.
    selection() directement quand l'accueil pilote la case — exposé ici
    (mêmes _ids que _checked_or_selected_ids, ce conflit n'étant pas
    l'objet de ces tests) avec la méthode réelle greffée pour refléter le
    vrai réglage accueil/démarrage."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)
        self.clear_checked_calls = 0
        self.refresh_all_calls = 0
        self.refresh_remote_players_calls = 0
        self.refresh_remote_checkin_calls = 0
        self._withdraw_selected = types.MethodType(main.App._withdraw_selected, self)
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


# =====================================================================
# Bug 2 — interdire de signaler absent un joueur déjà accueilli
# =====================================================================
class DeclareAbsentRefusesArrivedPlayerDbTest(unittest.TestCase):
    """Niveau base de données — la SEULE autorité réelle (cahier des
    charges : "applique cette interdiction ... directement dans la base
    de données")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="declare_absent_arrived_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)

    def test_refuse_si_arrived_at_pose(self):
        self.db.confirm_arrival(self.pid)

        result = self.db.declare_absent(self.pid)

        self.assertFalse(result)
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_fonctionne_normalement_sans_arrivee_confirmee(self):
        result = self.db.declare_absent(self.pid)
        self.assertTrue(result)

    def test_verite_est_arrived_at_jamais_un_etat_de_case_a_cocher(self):
        """Le coeur de la demande : "vérifie le statut d'accueil
        enregistré (arrived_at), et non l'état visuel de la case,
        puisque toutes les cases sont décochées au démarrage du
        tournoi" — reproduit ici exactement ce scénario : arrivée
        confirmée AVANT le démarrage, tournoi démarré (les cases se
        décochent, voir Lot 1/_clock_resume), puis tentative de
        signalement."""
        self.db.confirm_arrival(self.pid)  # avant le démarrage
        _start_tournament(self.db)  # démarre : les cases se décochent (Lot 1)

        result = self.db.declare_absent(self.pid)

        self.assertFalse(result)


class OnAbsDesClickedRoutesArrivedPlayerTest(unittest.TestCase):
    """Niveau Mac — _on_abs_des_clicked route un joueur déjà accueilli
    vers une désactivation directe, jamais un signalement d'absence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="abs_des_arrived_routing_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.confirm_arrival(self.pid)
        _start_tournament(self.db)

    def test_demande_confirmation_et_desactive_directement(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_confirm:
            main.App._on_abs_des_clicked(app)

        mock_confirm.assert_called_once()
        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])  # jamais signalé

    def test_aucune_condition_de_delai_pour_un_joueur_deja_accueilli(self):
        """"Un joueur déjà accueilli doit conserver sa possibilité de
        désactivation selon les règles habituelles" — donc AUCUNE
        attente du délai des retardataires, contrairement à un joueur
        signalé absent."""
        _start_tournament(self.db, minutes_ago=1, duration_minutes=60)  # délai très loin d'être dépassé
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._on_abs_des_clicked(app)

        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")

    def test_refus_de_la_confirmation_ne_desactive_pas(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=False):
            main.App._on_abs_des_clicked(app)

        self.assertEqual(self.db.get_player(self.pid)["status"], "active")

    def test_rééquilibrage_historique_applique(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._on_abs_des_clicked(app)

        after = self.db.get_player(self.pid)
        self.assertIsNone(after["table_id"])
        self.assertIsNone(after["seat"])


class RemoteDeclareAbsenceRefusesArrivedPlayerTest(unittest.TestCase):
    """Niveau téléphone (orchestration Tk) — _remote_declare_absence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_declare_absence_arrived_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.confirm_arrival(self.pid)
        _start_tournament(self.db)

    def test_refuse_avec_message_explicite(self):
        fake = types.SimpleNamespace(db=self.db, _remote_absence_results={})

        main.App._remote_declare_absence(fake, self.pid, request_id="r1")

        result = fake._remote_absence_results["r1"]
        self.assertFalse(result["ok"])
        self.assertIn("accueilli", result["message"])
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])


class RemoteWithdrawAbsentPlayerAcceptsArrivedPlayerTest(unittest.TestCase):
    """Niveau téléphone — _remote_withdraw_absent_player accepte un
    joueur déjà accueilli (désactivation directe), sans condition de
    délai, contrairement à un joueur signalé absent."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_withdraw_arrived_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.confirm_arrival(self.pid)
        _start_tournament(self.db, minutes_ago=1, duration_minutes=60)  # délai loin d'être dépassé

    def test_desactive_un_joueur_accueilli_sans_attendre_le_delai(self):
        fake = types.SimpleNamespace(
            db=self.db, _remote_absence_results={},
            _refresh_remote_players_cache=lambda: None,
            _refresh_remote_checkin_cache=lambda: None,
            _refresh_players_tab=lambda: None,
            _refresh_tables_tab=lambda: None,
        )

        main.App._remote_withdraw_absent_player(fake, self.pid, request_id="r1")

        result = fake._remote_absence_results["r1"]
        self.assertTrue(result["ok"])
        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")


# =====================================================================
# Bug 3 — interdire l'annulation d'une absence après l'échéance
# =====================================================================
class CancelAbsenceRefusesAfterDeadlineDbTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cancel_absence_deadline_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

    def test_fonctionne_avant_l_echeance(self):
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)

        result = self.db.cancel_absence(self.pid)

        self.assertTrue(result)
        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])

    def test_refuse_apres_l_echeance(self):
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)  # échéance dépassée

        result = self.db.cancel_absence(self.pid)

        self.assertFalse(result)
        self.assertIsNotNone(self.db.get_player(self.pid)["absent_declared_at"])  # inchangé

    def test_ne_touche_jamais_reinstate_player(self):
        """"Ne modifie pas sans nécessité le mécanisme historique de
        réinscription des joueurs réellement désactivés" — un joueur
        RÉELLEMENT désactivé (withdrawn) reste réinscriptible SANS AUCUNE
        condition de délai, cette restriction ne concerne QUE cancel_
        absence."""
        self.db.withdraw_player(self.pid)
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)  # échéance dépassée

        self.db.reinstate_player(self.pid)  # jamais gardée par absence_deadline_passed

        self.assertEqual(self.db.get_player(self.pid)["status"], "active")


class ReinstateSelectedRefusesCancelAfterDeadlineTest(unittest.TestCase):
    """Niveau Mac — _reinstate_selected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_selected_deadline_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)  # échéance dépassée

    def test_refuse_et_affiche_un_message(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._reinstate_selected(app)

        mock_info.assert_called_once()
        self.assertIsNotNone(self.db.get_player(self.pid)["absent_declared_at"])  # toujours signalé

    def test_reinscription_dun_joueur_reellement_desactive_reste_inchangee(self):
        """Non-régression explicite : la réinscription CLASSIQUE (joueur
        withdrawn) continue de fonctionner à l'identique, même après
        l'échéance du délai des retardataires — cette restriction ne la
        concerne jamais."""
        pid_withdrawn = self.db.add_player("Bob")
        self.db.withdraw_player(pid_withdrawn)
        app = _FakeAppForAbsDes(self.db, [pid_withdrawn])

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._reinstate_selected(app)

        mock_info.assert_not_called()
        self.assertEqual(self.db.get_player(pid_withdrawn)["status"], "active")


class RemoteCancelAbsenceRefusesAfterDeadlineTest(unittest.TestCase):
    """Niveau téléphone (orchestration Tk) — _remote_cancel_absence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_cancel_absence_deadline_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)  # échéance dépassée

    def test_refuse_avec_message_explicite(self):
        fake = types.SimpleNamespace(db=self.db, _remote_absence_results={})

        main.App._remote_cancel_absence(fake, self.pid, request_id="r1")

        result = fake._remote_absence_results["r1"]
        self.assertFalse(result["ok"])
        self.assertIn("dépassé", result["message"])
        self.assertIsNotNone(self.db.get_player(self.pid)["absent_declared_at"])  # inchangé


# =====================================================================
# Bug 1 — clignotement fiable (Mac : un rafraîchissement sans changement
# n'interrompt plus un cycle en cours)
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayersBlinkNotInterruptedByUnrelatedRefreshTest(unittest.TestCase):
    """_refresh_players_tab ne doit PLUS annuler/reprogrammer le cycle de
    clignotement à chaque rafraîchissement quand l'ensemble des absents
    n'a pas changé (correctif du 2026-09-29, "les rafraîchissements
    périodiques ne doivent pas interrompre ou réinitialiser constamment
    l'animation")."""

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

        self._tmp = tempfile.TemporaryDirectory(prefix="players_blink_not_interrupted_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(self.pid)

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = set()
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED

        for meth in (
            "_refresh_players_tab", "_update_tournament_started_buttons",
            "_pending_old_seat_by_name", "_update_checked_count_label",
            "_update_undo_elimination_button_state", "_players_checkbox_drives_accueil",
            "_update_players_stats_label", "_cancel_players_absent_blink",
            "_players_absent_blink_tick", "_update_sort_headings",
            # Complément du 2026-09-29 ("Pré-inscrit"/"Actif") :
            # _refresh_players_tab en a désormais besoin.
            "_accueil_status_label",
        ):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

        parent = ttk.Frame(self.root)
        self.addCleanup(parent.destroy)
        columns = (
            "sel", "id", "name", "club", "table", "seat", "chips", "buyin", "rebuy",
            "addon", "bounty", "status", "rang", "elim_time", "elim_round", "eliminated_by",
        )
        self.win.players_tree = ttk.Treeview(parent, columns=columns, show="tree headings")
        for c in columns:
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
        self.addCleanup(lambda: self.win._cancel_players_absent_blink())

    def test_rafraichissement_sans_changement_garde_le_meme_cycle(self):
        self.win._refresh_players_tab()
        after_id_1 = self.win._players_absent_blink_after_id

        # Rafraîchissement supplémentaire SANS rien changer à l'absence
        # (même joueur, toujours signalé) : le cycle déjà programmé ne
        # doit JAMAIS être annulé/reprogrammé pour ça.
        self.win._refresh_players_tab()
        after_id_2 = self.win._players_absent_blink_after_id

        self.assertEqual(after_id_1, after_id_2)

    def test_apparition_du_premier_absent_demarre_le_cycle(self):
        self.db.cancel_absence(self.pid)
        self.win._refresh_players_tab()
        self.assertIsNone(getattr(self.win, "_players_absent_blink_after_id", None))

        self.db.declare_absent(self.pid)
        self.win._refresh_players_tab()

        self.assertIsNotNone(self.win._players_absent_blink_after_id)

    def test_disparition_du_dernier_absent_arrete_le_cycle(self):
        self.win._refresh_players_tab()
        self.assertIsNotNone(self.win._players_absent_blink_after_id)

        self.db.withdraw_player(self.pid)
        self.win._refresh_players_tab()

        self.assertIsNone(self.win._players_absent_blink_after_id)


# =====================================================================
# Bug 1 (téléphone) / Bug 4 — vérification structurelle du gabarit JS
# (même principe que tests/test_movement_alert_nudge_remote.py : le
# contenu du template HTML/JS est vérifié par recherche de sous-chaînes,
# jamais une regex sur du HTML arbitraire — aucun exécuteur JS dans cette
# suite, voir sa docstring pour ce choix déjà établi).
# =====================================================================
class EliminatePageTemplateStructureTest(unittest.TestCase):
    def test_clignotement_pilote_par_un_minuteur_js_pas_une_animation_css(self):
        """Bug 1 : plus de @keyframes lié à la durée de vie de l'élément —
        un minuteur JS persistant (tickAbsentBlink) bascule une classe."""
        page = remote_control._ELIMINATE_PAGE
        self.assertNotIn("@keyframes player-absent-blink", page)
        self.assertIn("function tickAbsentBlink", page)
        self.assertIn("setInterval(tickAbsentBlink, 600)", page)
        self.assertIn("absent-blink-on", page)

    def test_glissement_vers_abs_des_detecte_la_sortie_de_la_liste(self):
        """Bug 4 : un doigt qui quitte #columns par le haut engage le
        glissement sans condition, quelle que soit l'orientation dx/dy —
        plus besoin de "glisser à droite d'abord"."""
        page = remote_control._ELIMINATE_PAGE
        self.assertIn("aboveList", page)
        self.assertIn("getElementById('columns').getBoundingClientRect()", page)
        # L'heuristique historique (comparaison dy/dx) reste présente et
        # continue de s'appliquer TANT QUE le doigt reste dans la liste —
        # jamais supprimée, seulement complétée (glissement Éliminé/
        # Éliminateur inchangé).
        self.assertIn("Math.abs(dy) >= Math.abs(dx)", page)

    def test_handle_abs_des_drop_route_un_joueur_deja_accueilli(self):
        """Bug 2 (téléphone) : handleAbsDesDrop consulte drag.arrived
        avant toute autre branche."""
        page = remote_control._ELIMINATE_PAGE
        idx_handle = page.index("function handleAbsDesDrop")
        idx_arrived = page.index("drag.arrived", idx_handle)
        idx_absent_check = page.index("if (!drag.absent)", idx_handle)
        self.assertLess(idx_arrived, idx_absent_check)

    def test_page_contient_toujours_le_bouton_abs_des(self):
        self.assertIn("btn-abs-des", remote_control._ELIMINATE_PAGE)


if __name__ == "__main__":
    unittest.main()
