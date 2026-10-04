# -*- coding: utf-8 -*-
"""Synchronisation de la case à cocher de l'onglet Joueurs avec l'accueil
des joueurs (Lot 1, précisions du 2026-09-28) :

- Avant le démarrage du tournoi (clock_started == 0), la case à cocher
  EST l'indicateur d'accueil : cocher confirme l'arrivée (Database.
  confirm_arrival), décocher l'annule (Database.cancel_arrival) —
  synchronisé avec le téléphone dans les deux sens (App._sync_checkbox_
  for_arrival, appelée par _remote_confirm_arrival/_remote_cancel_
  arrival). "Tout cocher"/"Tout décocher" suivent la même règle.
- Le compteur "X accueilli(s)" (onglet Joueurs, à côté de "Entrées : Y")
  est mis à jour à chaque changement.
- Au démarrage (App._clock_resume, transition clock_started 0->1),
  toutes les cases sont décochées SANS toucher arrived_at (les
  confirmations restent enregistrées, le compteur reste correct).
- Après le démarrage, la case redevient une simple sélection pour les
  commandes groupées (Éliminer/Désactiver/Réactiver/Supprimer/Rebuy/
  Add-on, voir _checked_or_selected_ids, INCHANGÉ ici) : plus jamais
  pilotée par l'accueil, y compris pour un retardataire confirmé depuis
  le téléphone après le démarrage — mais le compteur, lui, continue
  d'augmenter pour ces retardataires.

Voir tests/test_remote_confirm_arrival.py pour la couverture Database/
HTTP de confirm_arrival/cancel_arrival elle-même (non répétée ici), et
tests/test_players_checkbox_header_sort.py pour le tri par cette même
colonne (comportement inchangé par ce chantier)."""
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
import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name="A"):
    """« Gestion de l'accueil » activée par défaut pour ces fixtures
    (modification du 2026-09-29) : ce module teste précisément la
    synchronisation case à cocher/accueil, réglage propre au tournoi
    Open qui l'active — voir test_accueil_management_toggle.py pour la
    couverture du réglage désactivé (comportement par défaut)."""
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1})
    return db


_CHECKBOX_METHODS = (
    "_apply_checkbox_display", "_update_checked_count_label",
    "_players_checkbox_drives_accueil", "_update_players_stats_label",
    # Complément du 2026-09-29 ("Pré-inscrit"/"Actif") : _on_players_
    # tree_click/_check_all_players/_uncheck_all_players/_sync_checkbox_
    # for_arrival en ont désormais besoin (voir main.py).
    "_accueil_status_label", "_apply_accueil_status_display",
    # Correctif du 2026-09-29 ("retour d'un joueur absent") : _sync_
    # checkbox_for_arrival appelle désormais aussi _clear_absent_blink_
    # tag (qui peut elle-même appeler _cancel_players_absent_blink) pour
    # arrêter immédiatement le clignotement d'un retardataire confirmé.
    "_clear_absent_blink_tag", "_cancel_players_absent_blink",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayersCheckboxAccueilClickTest(unittest.TestCase):
    """_on_players_tree_click, avec un VRAI ttk.Treeview — identify/
    identify_column/identify_row patchés pour éviter tout calcul de
    coordonnées réel (bbox/update_idletasks) : voir tests/test_players_
    right_click_eliminate.py pour le diagnostic complet du crash Tcl/Tk
    (SIGSEGV) reproductible dans cet environnement que ça évite."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_checkbox_accueil_click_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        self.tree.insert("", "end", iid=str(self.pid), values=("", "Alice"))

        self.checkin_cache_refreshes = 0
        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids=set(),
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
        )
        for meth in ("_on_players_tree_click",) + _CHECKBOX_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_checkin_cache = self._count_checkin_refresh

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def _count_checkin_refresh(self):
        self.checkin_cache_refreshes += 1

    def _click_checkbox(self, row_iid):
        event = types.SimpleNamespace(x=0, y=0)
        with patch.object(self.tree, "identify", return_value="cell"), \
             patch.object(self.tree, "identify_column", return_value="#1"), \
             patch.object(self.tree, "identify_row", return_value=row_iid):
            self.win._on_players_tree_click(event)

    # -- Avant démarrage : la case pilote l'accueil ----------------------

    def test_avant_demarrage_cocher_confirme_larrivee(self):
        self._click_checkbox(str(self.pid))

        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])
        self.assertIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.pid), "sel"), main.App.CHECKBOX_CHECKED)
        self.assertEqual(self.checkin_cache_refreshes, 1)
        self.assertIn("1 accueilli", self.win.stats_lbl.cget("text"))

    def test_avant_demarrage_decocher_annule_larrivee(self):
        self.db.confirm_arrival(self.pid)
        self.win.checked_player_ids.add(self.pid)
        self.win._apply_checkbox_display(str(self.pid))

        self._click_checkbox(str(self.pid))

        self.assertIsNone(self.db.get_player(self.pid)["arrived_at"])
        self.assertNotIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.pid), "sel"), main.App.CHECKBOX_UNCHECKED)
        self.assertEqual(self.checkin_cache_refreshes, 1)
        self.assertIn("0 accueilli", self.win.stats_lbl.cget("text"))

    # -- Après démarrage : la case redevient une simple sélection -------

    def test_apres_demarrage_cocher_ne_touche_pas_larrivee(self):
        self.db.set_settings({"clock_started": 1})

        self._click_checkbox(str(self.pid))

        self.assertIsNone(self.db.get_player(self.pid)["arrived_at"])
        self.assertIn(self.pid, self.win.checked_player_ids)  # simple sélection, quand même cochée
        self.assertEqual(self.checkin_cache_refreshes, 0)  # aucun lien avec l'accueil une fois démarré

    def test_apres_demarrage_decocher_ne_touche_pas_une_arrivee_dejas_confirmee(self):
        self.db.confirm_arrival(self.pid)  # arrivé AVANT le démarrage
        self.db.set_settings({"clock_started": 1})
        self.win.checked_player_ids.add(self.pid)
        self.win._apply_checkbox_display(str(self.pid))

        self._click_checkbox(str(self.pid))

        self.assertIsNotNone(self.db.get_player(self.pid)["arrived_at"])  # inchangé
        self.assertNotIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.checkin_cache_refreshes, 0)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class CheckAllUncheckAllAccueilTest(unittest.TestCase):
    """"Tout cocher"/"Tout décocher" (onglet Joueurs) suivent la même
    règle que le clic individuel sur une case."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_checkbox_accueil_bulk_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.id_a = self.db.add_player("Alice")
        self.id_b = self.db.add_player("Bob")

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        for pid, name in ((self.id_a, "Alice"), (self.id_b, "Bob")):
            self.tree.insert("", "end", iid=str(pid), values=("", name))

        self.checkin_cache_refreshes = 0
        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids=set(),
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
        )
        for meth in ("_check_all_players", "_uncheck_all_players") + _CHECKBOX_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._refresh_remote_checkin_cache = self._count_checkin_refresh

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def _count_checkin_refresh(self):
        self.checkin_cache_refreshes += 1

    def test_tout_cocher_avant_demarrage_confirme_toutes_les_arrivees(self):
        self.win._check_all_players()

        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])
        self.assertIsNotNone(self.db.get_player(self.id_b)["arrived_at"])
        self.assertEqual(self.win.checked_player_ids, {self.id_a, self.id_b})
        self.assertIn("2 accueilli", self.win.stats_lbl.cget("text"))

    def test_tout_decocher_avant_demarrage_annule_toutes_les_arrivees(self):
        self.win._check_all_players()

        self.win._uncheck_all_players()

        self.assertIsNone(self.db.get_player(self.id_a)["arrived_at"])
        self.assertIsNone(self.db.get_player(self.id_b)["arrived_at"])
        self.assertEqual(self.win.checked_player_ids, set())
        self.assertIn("0 accueilli", self.win.stats_lbl.cget("text"))

    def test_tout_cocher_apres_demarrage_ne_touche_pas_larrivee(self):
        self.db.set_settings({"clock_started": 1})

        self.win._check_all_players()

        self.assertIsNone(self.db.get_player(self.id_a)["arrived_at"])
        self.assertIsNone(self.db.get_player(self.id_b)["arrived_at"])
        self.assertEqual(self.win.checked_player_ids, {self.id_a, self.id_b})  # sélection quand même
        self.assertEqual(self.checkin_cache_refreshes, 0)

    def test_tout_decocher_apres_demarrage_ne_touche_pas_les_arrivees_deja_confirmees(self):
        self.db.confirm_arrival(self.id_a)  # arrivé avant le démarrage
        self.db.set_settings({"clock_started": 1})
        self.win.checked_player_ids = {self.id_a, self.id_b}

        self.win._uncheck_all_players()

        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])  # inchangé
        self.assertEqual(self.win.checked_player_ids, set())
        self.assertEqual(self.checkin_cache_refreshes, 0)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class SyncCheckboxForArrivalTest(unittest.TestCase):
    """App._sync_checkbox_for_arrival : répercussion immédiate, sur
    l'onglet Joueurs, d'une confirmation/annulation d'arrivée décidée
    depuis le téléphone (appelée par _remote_confirm_arrival/_remote_
    cancel_arrival, voir tests/test_remote_confirm_arrival.py pour leur
    couverture complète — ici, seulement leur effet sur la case/le
    compteur)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="sync_checkbox_arrival_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        # Valeur initiale = le glyphe "décoché" (comme le rendrait un vrai
        # _refresh_players_tab), pas une chaîne vide : test_apres_
        # demarrage_confirmation_ne_coche_plus_mais_le_compteur_avance a
        # besoin d'une valeur de départ SIGNIFIANTE pour vérifier qu'elle
        # reste bien inchangée (jamais cochée) après le démarrage.
        self.tree.insert(
            "", "end", iid=str(self.pid), values=(main.App.CHECKBOX_UNCHECKED, "Alice"),
        )

        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids=set(),
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
        )
        for meth in ("_sync_checkbox_for_arrival",) + _CHECKBOX_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def test_avant_demarrage_confirmation_coche_la_case(self):
        self.db.confirm_arrival(self.pid)

        self.win._sync_checkbox_for_arrival(self.pid, True)

        self.assertIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.pid), "sel"), main.App.CHECKBOX_CHECKED)
        self.assertIn("1 accueilli", self.win.stats_lbl.cget("text"))

    def test_avant_demarrage_annulation_decoche_la_case(self):
        self.db.confirm_arrival(self.pid)
        self.win._sync_checkbox_for_arrival(self.pid, True)
        self.db.cancel_arrival(self.pid)

        self.win._sync_checkbox_for_arrival(self.pid, False)

        self.assertNotIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.pid), "sel"), main.App.CHECKBOX_UNCHECKED)
        self.assertIn("0 accueilli", self.win.stats_lbl.cget("text"))

    def test_apres_demarrage_confirmation_ne_coche_plus_mais_le_compteur_avance(self):
        """Le coeur de la précision du 2026-09-28 : un retardataire
        confirmé depuis le téléphone APRÈS le démarrage ne doit JAMAIS
        cocher la case (plus aucun lien avec l'accueil, voir _players_
        checkbox_drives_accueil), mais le compteur "accueilli(s)" doit
        quand même progresser."""
        self.db.set_settings({"clock_started": 1})
        self.db.confirm_arrival(self.pid)

        self.win._sync_checkbox_for_arrival(self.pid, True)

        self.assertNotIn(self.pid, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.pid), "sel"), main.App.CHECKBOX_UNCHECKED)
        self.assertIn("1 accueilli", self.win.stats_lbl.cget("text"))  # progresse quand même


class PlayersStatsLabelCounterTest(unittest.TestCase):
    """App._update_players_stats_label : format et calcul du compteur
    "X accueilli(s)", sans nécessiter de Tk réel (ttk.Label fonctionne
    déjà sans mainloop pour un simple .config(text=...)/.cget)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_stats_label_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

    def _win_with_label(self):
        win = types.SimpleNamespace(db=self.db, stats_lbl=_FakeLabel())
        win._update_players_stats_label = types.MethodType(
            main.App._update_players_stats_label, win
        )
        return win

    def test_aucun_accueilli_affiche_zero(self):
        self.db.add_player("Alice")
        win = self._win_with_label()

        win._update_players_stats_label()

        self.assertIn("0 accueilli(s)", win.stats_lbl.text)
        self.assertIn("Actifs : 1", win.stats_lbl.text)
        self.assertIn("Entrées : 1", win.stats_lbl.text)

    def test_compte_uniquement_les_actifs_arrives(self):
        pid_a = self.db.add_player("Alice")
        pid_b = self.db.add_player("Bob")
        pid_c = self.db.add_player("Chris")
        self.db.confirm_arrival(pid_a)
        self.db.confirm_arrival(pid_b)
        self.db.eliminate_player(pid_b)  # arrivé, puis éliminé : ne compte plus
        self.db.withdraw_player(pid_c)   # jamais arrivé, désisté

        win = self._win_with_label()
        win._update_players_stats_label()

        self.assertIn("1 accueilli(s)", win.stats_lbl.text)

    def test_annuler_une_arrivee_fait_redescendre_le_compteur(self):
        pid = self.db.add_player("Alice")
        self.db.confirm_arrival(pid)
        win = self._win_with_label()
        win._update_players_stats_label()
        self.assertIn("1 accueilli(s)", win.stats_lbl.text)

        self.db.cancel_arrival(pid)
        win._update_players_stats_label()

        self.assertIn("0 accueilli(s)", win.stats_lbl.text)


class _FakeLabel:
    """Doublure minimale de ttk.Label pour PlayersStatsLabelCounterTest —
    aucun Tk réel nécessaire, juste .config(text=...)/.text."""

    def __init__(self):
        self.text = ""

    def config(self, text):
        self.text = text


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ClockResumeUnchecksBoxesTest(unittest.TestCase):
    """App._clock_resume, transition clock_started 0->1 (démarrage) :
    toutes les cases de l'onglet Joueurs sont décochées, SANS toucher
    arrived_at — même harnais d'isolation open_windows/export_prefs que
    tests/test_primes_session_robustness.py (_ClockResumeStub), complété
    ici de players_tree/checked_player_ids/stats_lbl puisque cette
    précision touche justement leur interaction avec _clock_resume."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="clock_resume_accueil_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        lock_path = os.path.join(self._tmp.name, "primes_session_started.json")
        registry_lock_path = os.path.join(self._tmp.name, "open_windows.lock")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_primes_session_lock_path", return_value=lock_path),
            patch.object(open_windows, "_registry_lock_path", return_value=registry_lock_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
            patch.object(open_windows, "_remote_control_dir", return_value=self._tmp.name),
        ):
            target.start()
            self.addCleanup(target.stop)
        open_windows.register(self.db.path)

        self.id_a = self.db.add_player("Alice")
        self.id_b = self.db.add_player("Bob")
        self.db.confirm_arrival(self.id_a)  # arrivé AVANT le démarrage

        self.tree = ttk.Treeview(self.root, columns=("sel", "name", "status"), show="headings")
        for pid, name in ((self.id_a, "Alice"), (self.id_b, "Bob")):
            self.tree.insert("", "end", iid=str(pid), values=("", name))

        self.win = types.SimpleNamespace(
            db=self.db,
            players_tree=self.tree,
            checked_player_ids={self.id_a},
            CHECKBOX_CHECKED=main.App.CHECKBOX_CHECKED,
            CHECKBOX_UNCHECKED=main.App.CHECKBOX_UNCHECKED,
            checked_count_lbl=ttk.Label(self.root),
            stats_lbl=ttk.Label(self.root),
            refresh_clock_tab_calls=0,
        )
        for meth in ("_check_all_players", "_uncheck_all_players", "_clock_resume") + _CHECKBOX_METHODS:
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        self.win._apply_checkbox_display(str(self.id_a))
        self.win._elapsed_before_pause = types.MethodType(main.App._elapsed_before_pause, self.win)

        def _refresh_clock_tab():
            self.win.refresh_clock_tab_calls += 1
        self.win._refresh_clock_tab = _refresh_clock_tab

        self.addCleanup(lambda: cleanup_tk(self, "win", "tree"))

    def test_demarrage_decoche_toutes_les_cases(self):
        self.assertEqual(self.tree.set(str(self.id_a), "sel"), main.App.CHECKBOX_CHECKED)

        self.win._clock_resume()

        self.assertEqual(self.win.checked_player_ids, set())
        self.assertEqual(self.tree.set(str(self.id_a), "sel"), main.App.CHECKBOX_UNCHECKED)
        self.assertEqual(self.tree.set(str(self.id_b), "sel"), main.App.CHECKBOX_UNCHECKED)

    def test_demarrage_ne_touche_pas_arrived_at(self):
        self.win._clock_resume()

        self.assertIsNotNone(self.db.get_player(self.id_a)["arrived_at"])  # confirmation conservée
        self.assertIsNone(self.db.get_player(self.id_b)["arrived_at"])  # jamais arrivé, reste tel quel

    def test_demarrage_pose_bien_clock_started(self):
        self.win._clock_resume()

        self.assertEqual(self.db.get_setting_int("clock_started", 0), 1)
        self.assertEqual(self.win.refresh_clock_tab_calls, 1)

    def test_apres_demarrage_un_retardataire_confirme_ne_coche_plus_rien(self):
        """Non-régression bout-en-bout : après _clock_resume, une
        confirmation d'arrivée (simulée directement en base, comme le
        ferait le téléphone) ne doit plus jamais faire réapparaître de
        case cochée pour ce joueur."""
        self.win._clock_resume()

        self.db.confirm_arrival(self.id_b)
        self.win._apply_checkbox_display(str(self.id_b))  # même sans passer par _sync_checkbox_for_arrival

        self.assertNotIn(self.id_b, self.win.checked_player_ids)
        self.assertEqual(self.tree.set(str(self.id_b), "sel"), main.App.CHECKBOX_UNCHECKED)


if __name__ == "__main__":
    unittest.main()
