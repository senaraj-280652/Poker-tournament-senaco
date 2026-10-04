# -*- coding: utf-8 -*-
"""Extension du 2026-09-29 — "étendre le bouton Réinscrire sur Mac" aux
retardataires "Pré-inscrit" (jamais signalé absent), pour reproduire sur
Mac ce que l'accueil de l'iPhone permet déjà : confirmer l'arrivée d'un
joueur qui ne s'est pas encore présenté, sans passer par un signalement
d'absence préalable.

App._reinstate_selected, pour un joueur ACTIF pas encore arrivé
(`arrived_at` NULL — qu'il soit "Pré-inscrit" pur ou signalé absent, les
deux sont mutuellement exclusifs par construction, voir Database.
confirm_arrival), appelle désormais Database.confirm_arrival — MAIS
UNIQUEMENT si « Gestion de l'accueil » est activée pour ce tournoi,
vérifié EXPLICITEMENT (jamais seulement délégué au refus interne de
confirm_arrival).

Modification du 2026-10-03 ("bouton Inscrire pour un joueur préinscrit") :
le gate `clock_started == 1`, qui réservait jusqu'ici ce comportement à
APRÈS le démarrage du tournoi (pour ne jamais chevaucher la case à
cocher de l'onglet Joueurs, seule maîtresse de l'accueil avant le
démarrage), a été retiré à dessein — « Inscrire » est désormais un
second moyen, strictement équivalent à la case à cocher, disponible
AUSSI BIEN avant qu'après le démarrage. confirm_arrival n'a jamais
exigé le démarrage lui-même (seul le délai des retardataires l'intéresse,
et ce délai n'existe tout simplement pas tant que le tournoi n'a pas
démarré, voir Database.absence_deadline_epoch) : les deux mécanismes
partagent donc exactement la même garantie, dans les deux phases.

Couvre :
1. Pré-inscrit + accueil activé + délai OK (avant OU après le démarrage)
   -> confirme l'arrivée, "Actif" affiché immédiatement, compteur
   "accueilli(s)" à jour, jetons/table/siège/rééquilibrage jamais
   touchés.
2. Avant le démarrage -> fonctionne désormais EXACTEMENT comme la case à
   cocher (même résultat, même mise à jour du compteur, même état de
   case au rafraîchissement suivant).
3. Accueil désactivé -> aucun effet ET aucun message (comportement
   historique d'un tournoi club classique strictement préservé).
4. Délai dépassé (nécessairement après démarrage) -> refusé, message
   adapté ("Confirmation impossible"), aucune écriture.
5. Joueur déjà "Actif" -> aucun effet (rien à confirmer).
6. Joueur réellement désactivé -> reinstate_player, INCHANGÉ, y compris
   quand l'accueil est désactivé.
7. Sélection mixte (Pré-inscrit + déjà actif + réellement désactivé) :
   chacun traité selon son propre état, en un seul appel.
8. Info-bulle mise à jour.
9. Non-régression explicite : le signalement d'absence (Abs/Dés) et
   l'accueil iPhone restent hors de portée de cette extension."""
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


class _FakeAppForReinstate:
    """Correctif du 2026-10-03 ("Inscrire ignore le joueur sélectionné
    quand un autre est déjà accueilli") : _reinstate_selected lit
    désormais players_tree.selection() directement quand
    _players_checkbox_drives_accueil() est vrai — cette doublure expose
    donc les deux (méthode réelle greffée, pour qu'elle reflète la vraie
    valeur accueil/démarrage ; selection() renvoyant les mêmes _ids que
    _checked_or_selected_ids(), pour que ces tests de gating — qui ne
    portent pas sur le conflit coché/sélectionné lui-même, voir tests/
    test_inscrire_ignore_deja_accueillis.py pour celui-ci — continuent
    de cibler exactement les ids qu'ils configurent, quel que soit le
    chemin emprunté)."""

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


# =====================================================================
# 1-6. App._reinstate_selected — matrice des conditions, niveau DB pure
# =====================================================================
class ReinstatePreInscritGatingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_preinscrit_gating_test_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, accueil_enabled=True, started=True, minutes_ago=5, duration_minutes=60):
        db = _new_db(self._tmp.name, accueil_enabled=accueil_enabled)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        if started:
            _start_tournament(db, minutes_ago=minutes_ago, duration_minutes=duration_minutes)
        return db, pid

    def test_pre_inscrit_confirme_son_arrivee_quand_toutes_conditions_reunies(self):
        db, pid = self._db()
        app = _FakeAppForReinstate(db, [pid])

        main.App._reinstate_selected(app)

        after = db.get_player(pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertIsNone(after["absent_declared_at"])
        self.assertEqual(after["status"], "active")

    def test_statut_affiche_devient_actif_immediatement(self):
        db, pid = self._db()
        app = _FakeAppForReinstate(db, [pid])
        win = types.SimpleNamespace(db=db)
        win._accueil_status_label = types.MethodType(main.App._accueil_status_label, win)
        self.assertEqual(win._accueil_status_label(db.get_player(pid)), "Pré-inscrit")

        main.App._reinstate_selected(app)

        self.assertEqual(win._accueil_status_label(db.get_player(pid)), "Actif")

    def test_avant_demarrage_confirme_comme_la_case_a_cocher(self):
        """Modification du 2026-10-03 : contrairement au comportement
        antérieur ("aucun effet avant le démarrage"), « Inscrire »
        confirme désormais l'arrivée même avant le démarrage du tournoi
        — strictement équivalent à la case à cocher."""
        db, pid = self._db(started=False)
        app = _FakeAppForReinstate(db, [pid])

        main.App._reinstate_selected(app)

        after = db.get_player(pid)
        self.assertIsNotNone(after["arrived_at"])
        self.assertEqual(after["status"], "active")

    def test_accueil_desactive_aucun_effet(self):
        db, pid = self._db(accueil_enabled=False)
        app = _FakeAppForReinstate(db, [pid])

        main.App._reinstate_selected(app)

        after = db.get_player(pid)
        self.assertIsNone(after["arrived_at"])

    def test_accueil_desactive_aucun_message_meme_pour_une_seule_selection(self):
        """Comportement historique STRICTEMENT préservé : jamais de
        popup "délai dépassé" trompeur pour un tournoi qui n'utilise
        simplement pas l'accueil."""
        db, pid = self._db(accueil_enabled=False)
        app = _FakeAppForReinstate(db, [pid])

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._reinstate_selected(app)

        mock_info.assert_not_called()

    def test_delai_depasse_refuse_avec_message(self):
        db, pid = self._db(minutes_ago=65, duration_minutes=60)
        app = _FakeAppForReinstate(db, [pid])

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._reinstate_selected(app)

        mock_info.assert_called_once()
        title, message = mock_info.call_args[0]
        self.assertEqual(title, "Confirmation impossible")
        self.assertIn("confirmation d'arrivée", message)
        after = db.get_player(pid)
        self.assertIsNone(after["arrived_at"])

    def test_deja_actif_aucun_effet(self):
        db, pid = self._db()
        db.confirm_arrival(pid)
        before = db.get_player(pid)["arrived_at"]
        app = _FakeAppForReinstate(db, [pid])

        main.App._reinstate_selected(app)

        after = db.get_player(pid)
        self.assertEqual(after["arrived_at"], before)

    def test_ne_touche_ni_jetons_ni_table_ni_siege(self):
        db, pid = self._db()
        db.set_chips(pid, 15975)
        before = db.get_player(pid)
        app = _FakeAppForReinstate(db, [pid])
        seat_moves_before = db.count_seat_moves()

        main.App._reinstate_selected(app)

        after = db.get_player(pid)
        self.assertEqual(after["chips"], 15975)
        self.assertEqual(after["table_id"], before["table_id"])
        self.assertEqual(after["seat"], before["seat"])
        self.assertEqual(db.count_seat_moves(), seat_moves_before)

    def test_reellement_desactive_utilise_reinstate_player_meme_accueil_desactive(self):
        """Non-régression explicite (contrainte demandée) : le chemin
        historique reste intact, y compris quand l'accueil est
        désactivé — jamais gouverné par ce réglage."""
        db, pid = self._db(accueil_enabled=False)
        db.withdraw_player(pid)
        app = _FakeAppForReinstate(db, [pid])

        main.App._reinstate_selected(app)

        after = db.get_player(pid)
        self.assertEqual(after["status"], "active")
        self.assertIsNotNone(after["table_id"])  # reinstate_player réattribue un siège
        self.assertEqual(after["chips"], db.get_setting_int("starting_chips", 10000))


# =====================================================================
# 7. Sélection mixte
# =====================================================================
class MixedSelectionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_mixed_selection_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        _start_tournament(self.db)
        self.pid_preinscrit = self.db.add_player("Alice")
        self.pid_actif = self.db.add_player("Bob")
        self.db.confirm_arrival(self.pid_actif)
        self.pid_absent = self.db.add_player("Charlie")
        self.db.declare_absent(self.pid_absent)
        self.pid_withdrawn = self.db.add_player("Diana")
        self.db.withdraw_player(self.pid_withdrawn)

    def test_chacun_traite_selon_son_propre_etat(self):
        app = _FakeAppForReinstate(
            self.db, [self.pid_preinscrit, self.pid_actif, self.pid_absent, self.pid_withdrawn]
        )

        main.App._reinstate_selected(app)

        preinscrit_after = self.db.get_player(self.pid_preinscrit)
        self.assertIsNotNone(preinscrit_after["arrived_at"])

        absent_after = self.db.get_player(self.pid_absent)
        self.assertIsNotNone(absent_after["arrived_at"])
        self.assertIsNone(absent_after["absent_declared_at"])

        withdrawn_after = self.db.get_player(self.pid_withdrawn)
        self.assertEqual(withdrawn_after["status"], "active")
        self.assertIsNotNone(withdrawn_after["table_id"])


# =====================================================================
# 8. Compteur d'accueil et rafraîchissement (niveau Tk)
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
class ReinstatePreInscritCounterAndRefreshTest(unittest.TestCase):
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

        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_preinscrit_counter_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)

        self.win = self.root
        self.win.db = self.db
        self.win.checked_player_ids = {self.pid}
        self.win.players_sort = {"column": None, "ascending": True}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.CHECKBOX_CHECKED = main.App.CHECKBOX_CHECKED
        self.win.CHECKBOX_UNCHECKED = main.App.CHECKBOX_UNCHECKED
        self.win._players_absent_blink_active = False
        self.win._players_absent_blink_phase = False
        self.win._players_absent_blink_after_id = None

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

        self.addCleanup(lambda: cleanup_tk(
            self,
            "delete_player_btn_tooltip", "eliminate_player_btn_tooltip", "abs_des_btn_tooltip",
            "correct_arrival_btn_tooltip",
        ))

    def test_compteur_accueilli_augmente_apres_reinscrire(self):
        self.win._refresh_players_tab()
        self.assertIn("0 accueilli", self.win.stats_lbl.cget("text"))

        self.win.checked_player_ids = {self.pid}
        self.win._reinstate_selected()

        self.assertIn("1 accueilli", self.win.stats_lbl.cget("text"))

    def test_statut_colonne_devient_actif_apres_rafraichissement(self):
        self.win._refresh_players_tab()
        self.assertEqual(self.win.players_tree.set(str(self.pid), "status"), "Pré-inscrit")

        self.win.checked_player_ids = {self.pid}
        self.win._reinstate_selected()

        self.assertEqual(self.win.players_tree.set(str(self.pid), "status"), "Actif")


# =====================================================================
# 9. Info-bulle
# =====================================================================
class TooltipMentionsPreInscritTest(unittest.TestCase):
    """Vérifie, au niveau du code source, que l'info-bulle a bien été
    mise à jour pour ce nouveau cas — suite string-based, même principe
    que les vérifications de structure HTML ailleurs dans cette suite,
    faute d'un moyen simple d'inspecter une info-bulle Tk affichée."""

    def test_tooltip_mentionne_les_trois_cas(self):
        """Renommage du 2026-09-29 ("Réinscrire" -> "Inscrire") : l'info-
        bulle doit expliquer clairement les trois situations couvertes
        par ce même bouton.

        Modification du 2026-10-03 ("Statut Mac Inscrit avant le
        démarrage") : l'info-bulle ne doit PLUS mentionner un libellé de
        statut précis ("Actif") — le résultat affiché dépend désormais du
        démarrage ("Inscrit" avant, "Actif" après) ; elle décrit l'action
        ("confirme son arrivée"/"confirme son retour") sans s'engager sur
        le mot affiché ensuite."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"), encoding="utf-8") as f:
            source = f.read()
        idx = source.index('text="Inscrire", command=self._reinstate_selected')
        block = source[idx:idx + 900]
        self.assertIn("Pré-inscrit", block)
        self.assertIn("confirme son arrivée", block)
        self.assertIn("Signalé absent", block)
        self.assertIn("confirme son retour", block)
        self.assertIn("désactivé", block)
        self.assertNotIn("Actif", block)


class _FakeAppForAbsDesNotYetEligible:
    """Doublure minimale, même principe que _FakeAppForAbsDes (tests/
    test_abs_des_and_reinstate.py, reprise à l'identique plutôt
    qu'importée pour garder ces fichiers indépendants)."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)

    def _checked_or_selected_ids(self):
        return list(self._ids)

    def _clear_checked(self):
        pass

    def _refresh_all(self):
        pass

    def _refresh_remote_players_cache(self):
        pass


class NotYetEligibleMessageMentionsInscrireTest(unittest.TestCase):
    """Renommage du 2026-09-29 : le message affiché sur "Abs/Dés" pour un
    joueur signalé absent pas encore éligible à la désactivation doit
    désormais orienter vers « Inscrire », plus « Réinscrire »."""

    def test_message_mentionne_inscrire_pas_reinscrire(self):
        tmp = tempfile.TemporaryDirectory(prefix="not_yet_eligible_message_test_")
        self.addCleanup(tmp.cleanup)
        db = _new_db(tmp.name)
        self.addCleanup(db.conn.close)
        pid = db.add_player("Alice")
        _start_tournament(db, minutes_ago=1, duration_minutes=60)  # délai loin d'être dépassé
        db.declare_absent(pid)
        app = _FakeAppForAbsDesNotYetEligible(db, [pid])

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._on_abs_des_clicked(app)

        mock_info.assert_called_once()
        _, message = mock_info.call_args[0]
        self.assertIn("« Inscrire »", message)
        self.assertNotIn("Réinscrire", message)


if __name__ == "__main__":
    unittest.main()
