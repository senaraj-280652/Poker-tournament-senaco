# -*- coding: utf-8 -*-
"""Bouton "Purger" de l'onglet LOG (correction du 2026-09-24 au
chantier "LOG", Phase 2 ; comportement REVU le 2026-09-25) : les
options prédéfinies ("Plus de 1 an", "Tout le journal"...) envisagées un
temps ont été EXPLICITEMENT écartées — Purger réutilise les 6 mêmes
champs que Rechercher (Du/Au, obligatoires, plus Tournoi/Utilisateur/
Fonction/Joueur, facultatifs), avec confirmation obligatoire et
suppression réellement définitive.

Revu le 2026-09-25 ("Purgeons ce qui est dans les filtres en cours,
comme ça on voit bien ce qui va être effacé") : la version précédente
ignorait délibérément Tournoi/Utilisateur/Fonction/Joueur ("Purger =
suppression par période uniquement") — ce choix est ABANDONNÉ, jugé
après coup surprenant. Purger supprime désormais exactement ce que
Rechercher afficherait pour les mêmes filtres, sans la limite
d'affichage.

Trois volets :
1. PurgeActionsEngineTest — action_log.purge_actions directement (pas
   de Tk) : bornes inclusives, comptage exact, fichier jamais supprimé,
   schéma/index conservés, pas de VACUUM, fichier absent/vide,
   concurrence avec log_action, aucune base .tournoi touchée.
2. LogDatePickerTest (calendrier — Toplevel NON modal, sûr à piloter
   réellement) / ConfirmationDialogStructuralTest (Toplevel MODAL —
   grab_set()+wait_window(), vérifiée par AST, jamais pilotée pour de
   vrai, voir sa docstring) / LogPurgeFlowTest (confirmation mockée) —
   comportement GUI (main.py), même harnais réduit que tests/test_log_
   tab_functional.py.
3. Non-régression Phase 1/Phase 2 : relancée séparément par l'appelant
   (tests/test_action_log.py, tests/test_action_log_search.py, tests/
   test_log_tab_functional.py), pas dupliquée ici."""
import ast
import os
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from datetime import datetime
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import action_log  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _find_widget(root, predicate):
    if predicate(root):
        return root
    for child in root.winfo_children():
        found = _find_widget(child, predicate)
        if found is not None:
            return found
    return None


def _find_button(root, text):
    return _find_widget(root, lambda w: isinstance(w, ttk.Button) and w.cget("text") == text)


# =======================================================================
# 1. action_log.purge_actions (pas de Tk)
# =======================================================================
class ActionLogPurgeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="action_log_purge_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        patcher = patch.object(action_log, "_log_path", return_value=self.db_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _log(self, **kwargs):
        base = dict(
            tournament_name="Tournoi du vendredi", tournament_path="/tmp/vendredi.tournoi",
            role="ADMIN", category="eliminations", action="eliminate",
            result=action_log.RESULT_SUCCESS, user_name="Raj",
        )
        base.update(kwargs)
        action_log.log_action(**base)

    def _set_ts(self, ts, **kwargs):
        self._log(**kwargs)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE actions_log SET ts = ? WHERE id = (SELECT MAX(id) FROM actions_log)", (ts,))
            conn.commit()
        finally:
            conn.close()

    def _all_rows(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM actions_log ORDER BY id").fetchall()]
        finally:
            conn.close()


class BornesOuvertesTest(ActionLogPurgeTestCase):
    """Revu le 2026-09-25 ("je veux exactement la même interprétation
    que pour l'affichage/recherche" pour Du/Au) : ts_from/ts_to sont
    désormais TOUS LES DEUX FACULTATIFS pour purge_actions ET
    count_actions — plus aucun ValueError pour une date manquante,
    EXACTEMENT comme search_actions. "Du" absent = depuis le tout
    premier enregistrement, "Au" absent = jusqu'au tout dernier, les
    deux absents = toute la période disponible — jamais une date
    fabriquée artificiellement (pas de "01/01/1900")."""

    def setUp(self):
        super().setUp()
        self._set_ts("2020-01-01 00:00:00", player_name="TresAncien")
        self._set_ts("2026-09-20 10:00:00", player_name="Milieu1", user_name="Raj")
        self._set_ts("2026-09-21 10:00:00", player_name="Milieu2", user_name="Marie", role="DIRTO")
        self._set_ts("2030-12-31 23:59:59", player_name="TresRecent")

    def test_ts_from_absent_supprime_depuis_le_debut(self):
        """ts_from=None, ts_to borné : supprime tout jusqu'à ts_to
        INCLUS, à partir du tout premier enregistrement — jamais depuis
        une date fabriquée."""
        deleted = action_log.purge_actions(None, "2026-09-21 23:59:59")
        self.assertEqual(deleted, 3)  # TresAncien, Milieu1, Milieu2
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertEqual(remaining, {"TresRecent"})

    def test_ts_to_absent_supprime_jusqua_la_fin(self):
        """ts_to=None, ts_from borné : supprime tout depuis ts_from
        INCLUS jusqu'au tout dernier enregistrement."""
        deleted = action_log.purge_actions("2026-09-20 00:00:00", None)
        self.assertEqual(deleted, 3)  # Milieu1, Milieu2, TresRecent
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertEqual(remaining, {"TresAncien"})

    def test_les_deux_bornes_absentes_supprime_toute_la_periode(self):
        deleted = action_log.purge_actions(None, None)
        self.assertEqual(deleted, 4)
        self.assertEqual(self._all_rows(), [])

    def test_chaine_vide_traitee_comme_bornes_absentes_pas_comme_erreur(self):
        """Une chaîne vide ("" — ce que peut renvoyer l'appelant pour un
        champ Du/Au vide) doit être traitée exactement comme None,
        jamais comme une erreur."""
        deleted = action_log.purge_actions("", "")
        self.assertEqual(deleted, 4)

    def test_bornes_absentes_combinees_a_un_autre_filtre(self):
        """Aucune date fournie, mais un filtre Utilisateur actif : seule
        la ligne correspondante doit être supprimée — "ce qui correspond
        aux filtres et est affiché = ce qui sera purgé", même sans
        aucune date."""
        deleted = action_log.purge_actions(None, None, user_name="Marie")
        self.assertEqual(deleted, 1)
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertEqual(remaining, {"TresAncien", "Milieu1", "TresRecent"})

    def test_count_actions_accepte_aussi_les_bornes_absentes(self):
        self.assertEqual(action_log.count_actions(), 4)
        self.assertEqual(action_log.count_actions(None, None, user_name="Marie"), 1)
        self.assertEqual(action_log.count_actions(None, "2026-09-21 23:59:59"), 3)
        self.assertEqual(action_log.count_actions("2026-09-20 00:00:00", None), 3)


class FichierAbsentOuVideTest(ActionLogPurgeTestCase):
    def test_fichier_absent_renvoie_zero_sans_creer_la_base(self):
        self.assertFalse(os.path.exists(self.db_path))
        deleted = action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")
        self.assertEqual(deleted, 0)
        self.assertFalse(os.path.exists(self.db_path), "purge_actions ne doit jamais créer le fichier")

    def test_base_vide_de_lignes_renvoie_zero(self):
        # Force la création du fichier (schéma vide) sans y écrire de ligne.
        self._log()
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM actions_log")
        conn.commit()
        conn.close()
        deleted = action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")
        self.assertEqual(deleted, 0)


class SuppressionInclusiveTest(ActionLogPurgeTestCase):
    def setUp(self):
        super().setUp()
        self._set_ts("2026-09-19 23:59:59", player_name="AvantDu")
        self._set_ts("2026-09-20 00:00:00", player_name="PremierJourDebut")
        self._set_ts("2026-09-20 12:00:00", player_name="PremierJourMilieu")
        self._set_ts("2026-09-22 12:00:00", player_name="Milieu")
        self._set_ts("2026-09-24 23:59:59", player_name="DernierJourFin")
        self._set_ts("2026-09-25 00:00:00", player_name="ApresAu")

    def test_suppression_inclusive_du_premier_jour(self):
        """"Du" = début de journée INCLUS : toute la journée du 20/09,
        pas seulement 00:00:00 pile, doit être supprimée."""
        action_log.purge_actions("2026-09-20 00:00:00", "2026-09-24 23:59:59")
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertNotIn("PremierJourDebut", remaining)
        self.assertNotIn("PremierJourMilieu", remaining)

    def test_operations_avant_du_conservees(self):
        action_log.purge_actions("2026-09-20 00:00:00", "2026-09-24 23:59:59")
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertIn("AvantDu", remaining)

    def test_operations_apres_au_conservees(self):
        action_log.purge_actions("2026-09-20 00:00:00", "2026-09-24 23:59:59")
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertIn("ApresAu", remaining)

    def test_premier_jour_00h00_supprime(self):
        action_log.purge_actions("2026-09-20 00:00:00", "2026-09-24 23:59:59")
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertNotIn("PremierJourDebut", remaining)

    def test_dernier_jour_23h59_59_supprime(self):
        action_log.purge_actions("2026-09-20 00:00:00", "2026-09-24 23:59:59")
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertNotIn("DernierJourFin", remaining)

    def test_nombre_supprime_exact(self):
        deleted = action_log.purge_actions("2026-09-20 00:00:00", "2026-09-24 23:59:59")
        # PremierJourDebut, PremierJourMilieu, Milieu, DernierJourFin = 4
        self.assertEqual(deleted, 4)
        self.assertEqual(len(self._all_rows()), 2)  # AvantDu + ApresAu restants


class PurgeRespecteTousLesFiltresTest(ActionLogPurgeTestCase):
    """Revu le 2026-09-25 : purge_actions accepte désormais les 4 mêmes
    filtres optionnels que search_actions (tournament_path/user_name/
    category/player_name), EN PLUS des deux dates obligatoires — chacun
    doit restreindre réellement la suppression, exactement comme il
    restreint l'affichage de Rechercher (voir _build_filters_clause,
    source commune aux deux fonctions)."""

    def test_seul_le_tournoi_filtre_est_supprime(self):
        self._set_ts("2026-09-20 10:00:00", tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi")
        self._set_ts("2026-09-21 10:00:00", tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi")
        deleted = action_log.purge_actions(
            "2026-09-20 00:00:00", "2026-09-22 23:59:59", tournament_path="/tmp/a.tournoi",
        )
        self.assertEqual(deleted, 1)
        remaining = {r["tournament_path"] for r in self._all_rows()}
        self.assertEqual(remaining, {"/tmp/b.tournoi"})

    def test_seul_lutilisateur_filtre_est_supprime(self):
        self._set_ts("2026-09-20 10:00:00", user_name="Raj")
        self._set_ts("2026-09-20 11:00:00", user_name="Marie", role="DIRTO")
        deleted = action_log.purge_actions(
            "2026-09-20 00:00:00", "2026-09-20 23:59:59", user_name="Marie",
        )
        self.assertEqual(deleted, 1)
        remaining = {r["user_name"] for r in self._all_rows()}
        self.assertEqual(remaining, {"Raj"})

    def test_seule_la_categorie_filtree_est_supprimee(self):
        self._set_ts("2026-09-20 10:00:00", category="eliminations", action="eliminate")
        self._set_ts("2026-09-20 11:00:00", category="clock", action="toggle_pause")
        deleted = action_log.purge_actions(
            "2026-09-20 00:00:00", "2026-09-20 23:59:59", category="clock",
        )
        self.assertEqual(deleted, 1)
        remaining = {r["category"] for r in self._all_rows()}
        self.assertEqual(remaining, {"eliminations"})

    def test_seul_le_joueur_filtre_est_supprime(self):
        self._set_ts("2026-09-20 10:00:00", player_name="Alice")
        self._set_ts("2026-09-20 11:00:00", player_name="Bob")
        deleted = action_log.purge_actions(
            "2026-09-20 00:00:00", "2026-09-20 23:59:59", player_name="Bob",
        )
        self.assertEqual(deleted, 1)
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertEqual(remaining, {"Alice"})

    def test_tous_les_filtres_combines(self):
        self._set_ts(
            "2026-09-20 10:00:00", tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
            user_name="Raj", category="eliminations", action="eliminate", player_name="Alice",
        )
        self._set_ts(
            "2026-09-20 11:00:00", tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
            user_name="Raj", category="eliminations", action="eliminate", player_name="Bob",
        )
        deleted = action_log.purge_actions(
            "2026-09-20 00:00:00", "2026-09-20 23:59:59", tournament_path="/tmp/a.tournoi",
            user_name="Raj", category="eliminations", player_name="Alice",
        )
        self.assertEqual(deleted, 1)
        remaining = {r["player_name"] for r in self._all_rows()}
        self.assertEqual(remaining, {"Bob"})

    def test_sans_filtre_supplementaire_supprime_toute_la_periode_comme_avant(self):
        """Non-régression : ts_from/ts_to seuls (les 4 autres filtres à
        None) doivent continuer à supprimer TOUTE la période, exactement
        comme avant ce chantier — le changement de comportement n'ajoute
        une restriction QUE si l'appelant fournit explicitement un
        filtre."""
        self._set_ts(
            "2026-09-20 10:00:00", tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
            user_name="Raj", player_name="Alice",
        )
        self._set_ts(
            "2026-09-21 10:00:00", tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi",
            user_name="Marie", role="DIRTO", player_name="Bob", category="clock", action="toggle_pause",
        )
        deleted = action_log.purge_actions("2026-09-20 00:00:00", "2026-09-22 23:59:59")
        self.assertEqual(deleted, 2)
        self.assertEqual(self._all_rows(), [])


class NeTouchePasAuFichierNiAuSchemaTest(ActionLogPurgeTestCase):
    def test_ne_supprime_jamais_le_fichier(self):
        self._log()
        action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")
        self.assertTrue(os.path.exists(self.db_path), "le fichier lui-même ne doit jamais être supprimé")

    def test_conserve_le_schema_et_les_5_index(self):
        self._log()
        action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")
        conn = sqlite3.connect(self.db_path)
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            ).fetchall()}
        finally:
            conn.close()
        self.assertIn("actions_log", names)
        for idx in (
            "idx_actions_log_ts", "idx_actions_log_tournament", "idx_actions_log_user",
            "idx_actions_log_category", "idx_actions_log_player",
        ):
            self.assertIn(idx, names)

    def test_ne_lance_jamais_vacuum(self):
        self._log()
        calls = []
        real_open = action_log._open_connection

        class _CountingConn:
            def __init__(self, real_conn):
                self._real_conn = real_conn

            def execute(self, sql, params=()):
                calls.append(sql)
                return self._real_conn.execute(sql, params)

            def executescript(self, script):
                calls.append(script)
                return self._real_conn.executescript(script)

            def commit(self):
                self._real_conn.commit()

            def close(self):
                self._real_conn.close()

        def counting_open(path):
            return _CountingConn(real_open(path))

        with patch.object(action_log, "_open_connection", counting_open):
            action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")
        self.assertFalse(
            any("VACUUM" in c.upper() for c in calls), f"aucun VACUUM attendu, obtenu : {calls}",
        )

    def test_delete_jamais_drop(self):
        self._log()
        calls = []
        real_open = action_log._open_connection

        class _CountingConn:
            def __init__(self, real_conn):
                self._real_conn = real_conn

            def execute(self, sql, params=()):
                calls.append(sql)
                return self._real_conn.execute(sql, params)

            def executescript(self, script):
                return self._real_conn.executescript(script)

            def commit(self):
                self._real_conn.commit()

            def close(self):
                self._real_conn.close()

        def counting_open(path):
            return _CountingConn(real_open(path))

        with patch.object(action_log, "_open_connection", counting_open):
            action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")
        self.assertFalse(any("DROP" in c.upper() for c in calls))
        self.assertTrue(any(c.strip().upper().startswith("DELETE") for c in calls))


class ConcurrenceAvecLogActionTest(ActionLogPurgeTestCase):
    def test_purge_pendant_ecriture_continue_naleve_jamais(self):
        self._log(player_name="seed")
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    self._log(player_name=f"joueur-{i}")
                except Exception as e:  # pragma: no cover
                    errors.append(("write", e))
                i += 1

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            for _ in range(10):
                try:
                    action_log.purge_actions("1970-01-01 00:00:00", "1970-01-02 00:00:00")  # ne supprime rien
                except Exception as e:  # pragma: no cover
                    errors.append(("purge", e))
                time.sleep(0.01)
        finally:
            stop.set()
            t.join(timeout=5)
        self.assertEqual(errors, [])


class AucuneBaseTournoiToucheeTest(ActionLogPurgeTestCase):
    def test_purge_ne_touche_jamais_un_fichier_tournoi(self):
        self._log()
        tournoi_path = os.path.join(self._tmp.name, "session.tournoi")
        roster_path = os.path.join(self._tmp.name, "roster.json")
        with patch.object(roster, "_roster_path", return_value=roster_path):
            db = database.Database(tournoi_path)
            db.add_player("Alice")
            db.conn.commit()
            db.conn.close()

        with open(tournoi_path, "rb") as f:
            before = f.read()

        action_log.purge_actions("2020-01-01 00:00:00", "2030-12-31 23:59:59")

        with open(tournoi_path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after, "purge_actions ne doit jamais modifier une base .tournoi")


# =======================================================================
# 2. GUI (main.py) — même harnais réduit que tests/test_log_tab_
#    functional.py
# =======================================================================
_LOG_METHODS = (
    "_build_log_tab", "_build_log_search_criteria_placeholder",
    "_refresh_log_tab", "_refresh_log_filter_choices",
    "_parse_log_date_field", "_current_log_filter_values", "_populate_log_tree",
    "_on_log_search", "_on_log_reset", "_on_log_export",
    "_on_log_row_double_click", "_show_log_detail_window",
    "_show_log_date_picker", "_confirm_log_purge", "_log_purge_filter_summary",
    "_on_log_purge", "_refresh_all",
)
_LOG_STATIC_METHODS = ("_build_log_tournament_label_maps", "_format_log_ts", "_log_count_label_text")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogPurgeGuiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_purge_gui_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        al_patcher = patch.object(action_log, "_log_path", return_value=self.db_path)
        self.addCleanup(al_patcher.stop)
        al_patcher.start()

        self.root = tk.Tk()
        self.root.withdraw()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="LOG")
        self.notebook.select(self.log_tab)

        self.win = self.root
        self.win.notebook = self.notebook
        self.win.log_tab = self.log_tab

        for name in _LOG_METHODS:
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in _LOG_STATIC_METHODS:
            setattr(self.win, name, getattr(main.App, name))
        self.win._LOG_CALENDAR_MONTH_NAMES_FR = main.App._LOG_CALENDAR_MONTH_NAMES_FR

        self.addCleanup(lambda: cleanup_tk(self, "root", "win", "notebook", "log_tab"))

    def _log(self, **kwargs):
        base = dict(
            tournament_name="Tournoi du vendredi", tournament_path="/tmp/vendredi.tournoi",
            role="ADMIN", category="eliminations", action="eliminate",
            result=action_log.RESULT_SUCCESS, user_name="Raj",
        )
        base.update(kwargs)
        action_log.log_action(**base)

    def _set_ts(self, ts, **kwargs):
        self._log(**kwargs)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE actions_log SET ts = ? WHERE id = (SELECT MAX(id) FROM actions_log)", (ts,))
            conn.commit()
        finally:
            conn.close()

    def _tree_rows(self):
        return [self.win.log_tree.item(i, "values") for i in self.win.log_tree.get_children()]


# -----------------------------------------------------------------------
# 2a. Bouton calendrier
# -----------------------------------------------------------------------
class LogDatePickerTest(LogPurgeGuiTestCase):
    def test_bouton_calendrier_ouvre_un_popup(self):
        self.win._build_log_tab()
        before = set(self.win.winfo_children())
        self.win._show_log_date_picker(self.win.log_date_from_var, self.win.log_reset_btn)
        after = set(self.win.winfo_children())
        new_toplevels = [w for w in (after - before) if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(new_toplevels), 1)
        new_toplevels[0].destroy()

    def test_selection_dun_jour_remplit_jj_mm_aaaa(self):
        self.win._build_log_tab()
        self.win.log_date_from_var.set("")
        self.win._show_log_date_picker(self.win.log_date_from_var, self.win.log_reset_btn)
        picker = next(w for w in self.win.winfo_children() if isinstance(w, tk.Toplevel))
        btn = _find_button(picker, "15")
        self.assertIsNotNone(btn, "le jour 15 doit être cliquable dans la grille du mois courant")
        btn.invoke()
        value = self.win.log_date_from_var.get()
        datetime.strptime(value, "%d/%m/%Y")  # lève si le format n'est pas JJ/MM/AAAA
        self.assertTrue(value.startswith("15/"))

    def test_ouvre_sur_la_date_deja_saisie_si_valide(self):
        self.win._build_log_tab()
        self.win.log_date_from_var.set("24/03/2025")
        self.win._show_log_date_picker(self.win.log_date_from_var, self.win.log_reset_btn)
        picker = next(w for w in self.win.winfo_children() if isinstance(w, tk.Toplevel))
        month_lbl = _find_widget(picker, lambda w: isinstance(w, ttk.Label) and "2025" in w.cget("text"))
        self.assertIsNotNone(month_lbl)
        self.assertIn("Mars", month_lbl.cget("text"))
        picker.destroy()

    def test_ouvre_sur_aujourdhui_si_date_invalide(self):
        self.win._build_log_tab()
        self.win.log_date_from_var.set("pas une date")
        self.win._show_log_date_picker(self.win.log_date_from_var, self.win.log_reset_btn)
        picker = next(w for w in self.win.winfo_children() if isinstance(w, tk.Toplevel))
        today = datetime.now()
        expected_year = str(today.year)
        month_lbl = _find_widget(picker, lambda w: isinstance(w, ttk.Label) and expected_year in w.cget("text"))
        self.assertIsNotNone(month_lbl)
        picker.destroy()


# -----------------------------------------------------------------------
# 2b. Fenêtre de confirmation
# -----------------------------------------------------------------------
class ConfirmationDialogStructuralTest(unittest.TestCase):
    """_confirm_log_purge (Toplevel + grab_set() + wait_window()) n'est
    JAMAIS piloté réellement ici (after() + clic sur un vrai bouton) :
    un premier essai a fait planter l'INTERPRÈTE PYTHON LUI-MÊME
    (segfault Tcl/Tk, code de sortie 139) dans cet environnement précis
    — EXACTEMENT le même risque déjà documenté et déjà contourné de la
    même façon par tests/test_ask_eliminator_window_position.py (voir sa
    docstring de module) pour un autre Toplevel grab_set()+wait_window()
    de ce fichier. Choix délibéré, pas un renoncement : la boîte de
    dialogue reste sûre dans l'application réelle (fenêtre principale
    complète, jamais une racine greffée) ; le câblage est vérifié ici
    par ANALYSE STATIQUE (AST) du CODE SOURCE, comme le fait déjà ce
    même fichier de précédent pour _ask_eliminator."""

    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=main_py)

    def _find_method(self, class_name, method_name):
        cls = next(n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef) and n.name == class_name)
        return next(
            n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def _string_constants(self, node):
        return [n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)]

    def test_est_bien_une_fenetre_modale_grab_set(self):
        func = self._find_method("App", "_confirm_log_purge")
        calls = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertIn("grab_set", calls)
        self.assertIn("wait_window", calls)

    def test_libelles_des_boutons_sont_annuler_et_purger(self):
        func = self._find_method("App", "_confirm_log_purge")
        button_calls = [
            n for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "Button"
        ]
        labels = set()
        for call in button_calls:
            for kw in call.keywords:
                if kw.arg == "text" and isinstance(kw.value, ast.Constant):
                    labels.add(kw.value.value)
        self.assertIn("Annuler", labels)
        self.assertIn("Purger", labels)

    def test_message_mentionne_irreversible(self):
        func = self._find_method("App", "_confirm_log_purge")
        constants = self._string_constants(func)
        self.assertTrue(
            any("irréversible" in c.lower() for c in constants),
            "le message de confirmation doit mentionner explicitement l'irréversibilité",
        )

    def test_message_utilise_les_deux_dates_formatees(self):
        func = self._find_method("App", "_confirm_log_purge")
        func_source = ast.get_source_segment(self.source, func) or ""
        self.assertIn("date_from.strftime", func_source)
        self.assertIn("date_to.strftime", func_source)

    def test_message_presente_les_bornes_ouvertes_en_francais(self):
        """Revu le 2026-09-25 : Du/Au vides ne doivent jamais afficher une
        date fabriquée — le message doit formuler explicitement chaque
        borne ouverte ("depuis le début"/"jusqu'à la fin du journal") et
        le cas où les deux sont vides ("toute la période disponible")."""
        func = self._find_method("App", "_confirm_log_purge")
        constants = self._string_constants(func)
        joined = " ".join(constants).lower()
        self.assertIn("début du journal", joined)
        self.assertIn("fin du journal", joined)
        self.assertIn("toute la période disponible", joined)

    def test_annuler_et_fermeture_de_fenetre_partagent_le_meme_gestionnaire(self):
        """La croix de fermeture (WM_DELETE_WINDOW) doit passer par
        EXACTEMENT la même fonction que "Annuler" — jamais un second
        chemin indépendant qui pourrait diverger."""
        func = self._find_method("App", "_confirm_log_purge")
        func_source = ast.get_source_segment(self.source, func) or ""
        self.assertIn('protocol("WM_DELETE_WINDOW", _cancel)', func_source)

    def test_cancel_met_confirmed_a_false_purge_a_true_avant_destroy(self):
        func = self._find_method("App", "_confirm_log_purge")
        inner_funcs = {n.name: n for n in ast.walk(func) if isinstance(n, ast.FunctionDef)}
        self.assertIn("_cancel", inner_funcs)
        self.assertIn("_purge", inner_funcs)

        def _sets_confirmed_to(node, expected):
            for n in ast.walk(node):
                if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant) and n.value.value is expected:
                    for target in n.targets:
                        if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) \
                                and target.value.id == "result":
                            return True
            return False

        self.assertTrue(_sets_confirmed_to(inner_funcs["_cancel"], False))
        self.assertTrue(_sets_confirmed_to(inner_funcs["_purge"], True))

    def test_renvoie_le_flag_confirmed(self):
        func = self._find_method("App", "_confirm_log_purge")
        func_source = ast.get_source_segment(self.source, func) or ""
        self.assertIn('return result["confirmed"]', func_source)


# -----------------------------------------------------------------------
# 2c. Flux complet Purger (confirmation mockée pour isoler la logique)
# -----------------------------------------------------------------------
class LogPurgeFlowTest(LogPurgeGuiTestCase):
    def test_du_vide_purge_depuis_le_debut_du_journal(self):
        """Revu le 2026-09-25 : "Du" vide ne refuse plus la purge — elle
        porte sur tout ce qui précède "Au" inclus, depuis le tout premier
        enregistrement, exactement comme Rechercher l'afficherait."""
        self._set_ts("2020-01-01 00:00:00", player_name="Ancien")
        self._set_ts("2026-06-01 00:00:00", player_name="Alice")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("")
        self.win.log_date_to_var.set("31/12/2026")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(action_log.search_actions()[0], [], "les deux lignes (Du vide) doivent être supprimées")

    def test_au_vide_purge_jusqua_la_fin_du_journal(self):
        self._set_ts("2026-06-01 00:00:00", player_name="Alice")
        self._set_ts("2030-01-01 00:00:00", player_name="Futur")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2026")
        self.win.log_date_to_var.set("")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(action_log.search_actions()[0], [], "les deux lignes (Au vide) doivent être supprimées")

    def test_deux_dates_vides_purge_toute_la_periode_disponible(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("")
        self.win.log_date_to_var.set("")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(action_log.search_actions()[0], [])

    def test_dates_vides_avec_filtre_utilisateur_ne_supprime_que_ce_filtre(self):
        """Exemple donné explicitement : Du/Au vides, Utilisateur = Marie
        -> seules les opérations de Marie doivent être supprimées, pas
        tout le journal."""
        self._log(player_name="Alice", user_name="Raj")
        self._log(player_name="Bob", user_name="Marie", role="DIRTO")
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.assertEqual(len(self._tree_rows()), 1)
        with patch.object(self.win, "_confirm_log_purge", return_value=True) as mock_confirm, \
             patch("main.messagebox.showinfo") as mock_info:
            self.win._on_log_purge()
        mock_confirm.assert_called_once()
        # count transmis à la confirmation = exactement les lignes de Marie affichées.
        self.assertEqual(mock_confirm.call_args[0][2], 1)
        rows, _ = action_log.search_actions()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_name"], "Raj")
        message = mock_info.call_args[0][1]
        self.assertIn("1", message)

    def test_date_invalide_refuse_la_purge(self):
        self.win._build_log_tab()
        self.win.log_date_from_var.set("31/13/2026")
        self.win.log_date_to_var.set("31/12/2026")
        with patch("main.messagebox.showerror") as mock_err, \
             patch.object(self.win, "_confirm_log_purge") as mock_confirm:
            self.win._on_log_purge()
        mock_err.assert_called_once()
        mock_confirm.assert_not_called()

    def test_du_superieur_a_au_refuse_la_purge(self):
        self.win._build_log_tab()
        self.win.log_date_from_var.set("31/12/2026")
        self.win.log_date_to_var.set("01/01/2026")
        with patch("main.messagebox.showerror") as mock_err, \
             patch.object(self.win, "_confirm_log_purge") as mock_confirm:
            self.win._on_log_purge()
        mock_err.assert_called_once()
        mock_confirm.assert_not_called()

    def test_confirmation_refusee_naboutit_a_aucune_suppression(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=False), \
             patch("main.messagebox.showinfo") as mock_info:
            self.win._on_log_purge()
        mock_info.assert_not_called()
        self.assertEqual(len(action_log.search_actions()[0]), 1)

    def test_purge_confirmee_supprime_et_affiche_le_nombre_exact(self):
        self._log(player_name="Alice")
        self._log(player_name="Bob")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo") as mock_info:
            self.win._on_log_purge()
        mock_info.assert_called_once()
        message = mock_info.call_args[0][1]
        self.assertIn("2", message)
        rows, _ = action_log.search_actions()
        self.assertEqual(rows, [])

    def test_aucune_operation_a_supprimer_message_dedie(self):
        self._set_ts("2020-01-01 00:00:00", player_name="HorsPeriode")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2026")
        self.win.log_date_to_var.set("31/12/2026")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo") as mock_info:
            self.win._on_log_purge()
        message = mock_info.call_args[0][1]
        self.assertIn("Aucune opération à purger", message)

    def test_purge_respecte_le_filtre_utilisateur_affiche(self):
        """Revu le 2026-09-25 : un filtre "Utilisateur = Marie" affiché à
        l'écran doit désormais restreindre RÉELLEMENT ce qui est
        supprimé, exactement comme il restreint l'affichage — comportement
        INVERSE de l'ancien test_purge_ignore_le_filtre_tournoi_
        utilisateur_fonction_joueur, dont le principe même vient d'être
        abandonné."""
        self._set_ts(
            "2026-09-20 10:00:00", tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
            user_name="Raj", player_name="Alice", category="eliminations", action="eliminate",
        )
        self._set_ts(
            "2026-09-21 10:00:00", tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi",
            user_name="Marie", role="DIRTO", player_name="Bob", category="clock", action="toggle_pause",
        )
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.assertEqual(len(self._tree_rows()), 1)

        self.win.log_date_from_var.set("20/09/2026")
        self.win.log_date_to_var.set("21/09/2026")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        rows, _ = action_log.search_actions()
        # Seule la ligne de Marie (Bob, Tournoi B) doit avoir disparu ;
        # celle de Raj (Alice, Tournoi A) doit rester, malgré une période
        # qui les couvre toutes les deux.
        self.assertEqual(len(rows), 1, "seule la ligne correspondant au filtre Utilisateur doit être supprimée")
        self.assertEqual(rows[0]["user_name"], "Raj")
        self.assertEqual(rows[0]["player_name"], "Alice")

    def test_confirmation_recoit_le_resume_des_filtres_actifs(self):
        """Le résumé des filtres actifs (revu le 2026-09-25, "on voit bien
        ce qui va être effacé") doit réellement atteindre _confirm_log_
        purge, pas seulement exister quelque part dans _on_log_purge."""
        self._log(
            player_name="Alice", user_name="Marie", role="DIRTO",
            tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
        )
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True) as mock_confirm, \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        mock_confirm.assert_called_once()
        filter_summary = mock_confirm.call_args[0][-1]
        self.assertIn("Marie", filter_summary)

    def test_confirmation_resume_aucun_filtre_quand_tout_est_sur_tous(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True) as mock_confirm, \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        filter_summary = mock_confirm.call_args[0][-1]
        self.assertIn("aucun", filter_summary.lower())

    def test_dates_du_au_conservees_apres_purge(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(self.win.log_date_from_var.get(), "01/01/2020")
        self.assertEqual(self.win.log_date_to_var.get(), "31/12/2030")

    def test_tableau_rafraichi_apres_purge(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        self.assertEqual(len(self._tree_rows()), 1)
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(self._tree_rows(), [])

    def test_combobox_retombe_sur_tous_quand_la_valeur_disparait(self):
        self._log(player_name="Alice", user_name="Marie", role="DIRTO")
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.assertEqual(self.win.log_user_var.get(), "Marie")

        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(
            self.win.log_user_var.get(), "Tous",
            "Marie n'existe plus dans le journal après la purge : repli attendu sur Tous",
        )
        self.assertNotIn("Marie", self.win.log_user_combo["values"])

    def test_combobox_conservee_quand_la_valeur_existe_toujours(self):
        self._set_ts("2026-09-20 10:00:00", player_name="Alice", user_name="Marie", role="DIRTO")
        self._set_ts("2020-01-01 10:00:00", player_name="Bob", user_name="Marie", role="DIRTO")
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()

        # Purge une période qui ne couvre que la 1ère ligne — "Marie"
        # doit RESTER présente (la 2e ligne, hors période, la garde
        # valide dans le journal).
        self.win.log_date_from_var.set("20/09/2026")
        self.win.log_date_to_var.set("20/09/2026")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo"):
            self.win._on_log_purge()
        self.assertEqual(self.win.log_user_var.get(), "Marie")
        self.assertIn("Marie", self.win.log_user_combo["values"])

    def test_base_inexistante_purge_ne_leve_pas(self):
        self.win._build_log_tab()
        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_date_to_var.set("31/12/2030")
        with patch.object(self.win, "_confirm_log_purge", return_value=True), \
             patch("main.messagebox.showinfo") as mock_info:
            self.win._on_log_purge()  # ne doit pas lever
        message = mock_info.call_args[0][1]
        self.assertIn("Aucune opération à purger", message)


if __name__ == "__main__":
    unittest.main()
