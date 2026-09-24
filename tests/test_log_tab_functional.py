# -*- coding: utf-8 -*-
"""Couverture FONCTIONNELLE de l'onglet LOG (chantier "LOG", Phase 2,
2026-09-24) : tableau de résultats, les 6 filtres, Réinitialiser,
Rechercher, retour sur l'onglet (filtres CONSERVÉS), export CSV, détail
au double-clic. La séparation STRUCTURELLE CA/LOG (widgets présents,
bonne hiérarchie de conteneurs) reste couverte par tests/test_ca_log_
tab_wiring.py ; ce fichier-ci ne reteste pas cette partie-là.

action_log est systématiquement redirigé vers un fichier temporaire
(jamais le vrai ~/.poker_tournament/actions_log.sqlite3, voir setUp)."""
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import action_log  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


# Index des colonnes de self.log_tree (voir App._build_log_tab).
_COL_TS, _COL_TOURNAMENT, _COL_USER, _COL_ROLE, _COL_CATEGORY, _COL_ACTION, \
    _COL_PLAYER, _COL_RESULT, _COL_MESSAGE = range(9)

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
class LogTabFunctionalTestCase(unittest.TestCase):
    """Harnais réduit (un vrai Notebook à un seul onglet "LOG", toujours
    sélectionné) — même principe que tests/test_ca_log_tab_wiring.py::
    LogTabRealBuildTest, mais avec les méthodes fonctionnelles en plus
    (_refresh_log_tab et ses dépendances) et _refresh_all (pour vérifier
    le déclenchement au retour sur l'onglet)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_tab_functional_test_")
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

    def _tree_rows(self):
        return [self.win.log_tree.item(i, "values") for i in self.win.log_tree.get_children()]


# =======================================================================
# 1. Tableau + chargement initial (§11 de l'analyse validée)
# =======================================================================
class ChargementInitialEtTableauTest(LogTabFunctionalTestCase):
    def test_chargement_initial_affiche_les_operations_recentes_plus_recent_dabord(self):
        self._log(player_name="Alice")
        self._log(player_name="Bob", category="clock", action="toggle_pause", result=action_log.RESULT_DENIED)
        self.win._build_log_tab()
        rows = self._tree_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][_COL_PLAYER], "Bob")
        self.assertEqual(rows[0][_COL_RESULT], "Refusé")
        self.assertEqual(rows[1][_COL_PLAYER], "Alice")
        self.assertEqual(rows[1][_COL_RESULT], "Réussi")

    def test_tableau_naffiche_jamais_device_id_ni_tournament_path(self):
        self._log(
            player_name="Alice", device_id="secret-device-id",
            tournament_path="/very/secret/path.tournoi",
        )
        self.win._build_log_tab()
        for row in self._tree_rows():
            self.assertNotIn("secret-device-id", row)
            for cell in row:
                self.assertNotIn("/very/secret/path.tournoi", str(cell))

    def test_date_affichee_au_format_jjmmaaaa_hhmmss(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        ts_display = self._tree_rows()[0][_COL_TS]
        # "JJ/MM/AAAA HH:MM:SS" — jamais le format de stockage brut.
        datetime.strptime(ts_display, "%d/%m/%Y %H:%M:%S")

    def test_role_none_affiche_non_lie(self):
        self._log(user_name=None, role="NONE", player_name=None, category="clock", action="chronometre")
        self.win._build_log_tab()
        self.assertEqual(self._tree_rows()[0][_COL_ROLE], "Non lié")

    def test_base_vide_naffiche_rien_sans_erreur(self):
        self.win._build_log_tab()
        self.assertEqual(self._tree_rows(), [])
        self.assertEqual(self.win.log_count_lbl.cget("text"), "0 opération affichée")


# =======================================================================
# 1bis. Compteur "N opération(s) affichée(s)" et suppression du plafond
#       de 500 lignes (chantier "CE QUI CORRESPOND AUX FILTRES = CE QUI
#       EST AFFICHÉ = CE QUI EST EXPORTÉ = CE QUI PEUT ÊTRE PURGÉ",
#       2026-09-25).
# =======================================================================
class CompteurEtAbsenceDePlafondTest(LogTabFunctionalTestCase):
    def test_zero_operation(self):
        self.win._build_log_tab()
        self.assertEqual(self.win.log_count_lbl.cget("text"), "0 opération affichée")

    def test_une_operation(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        self.assertEqual(self.win.log_count_lbl.cget("text"), "1 opération affichée")

    def test_plusieurs_operations(self):
        for name in ("Alice", "Bob", "Chloé", "David"):
            self._log(player_name=name)
        self.win._build_log_tab()
        self.assertEqual(len(self._tree_rows()), 4)
        self.assertEqual(self.win.log_count_lbl.cget("text"), "4 opérations affichées")

    def test_compteur_correspond_exactement_au_nombre_de_lignes_du_tableau(self):
        for i in range(37):
            self._log(player_name=f"joueur-{i}")
        self.win._build_log_tab()
        self.assertEqual(
            self.win.log_count_lbl.cget("text"),
            f"{len(self._tree_rows())} opérations affichées",
        )

    def test_plus_de_500_operations_toutes_affichees_sans_troncature(self):
        """Coeur de la demande : "le LOG ne doit plus être limité à 500
        opérations" — au-delà de l'ancien plafond, tout doit rester
        affiché, sans aucun message de troncature."""
        for i in range(600):
            self._log(player_name=f"joueur-{i}")
        self.win._build_log_tab()
        self.assertEqual(len(self._tree_rows()), 600)
        self.assertEqual(self.win.log_count_lbl.cget("text"), "600 opérations affichées")
        # Aucune trace d'un ancien message de troncature à 500.
        self.assertNotIn("500", self.win.log_count_lbl.cget("text"))

    def test_filtre_restreint_le_compteur_en_consequence(self):
        self._log(player_name="Alice", user_name="Raj")
        self._log(player_name="Bob", user_name="Marie", role="DIRTO")
        self._log(player_name="Chloé", user_name="Marie", role="DIRTO")
        self.win._build_log_tab()
        self.assertEqual(self.win.log_count_lbl.cget("text"), "3 opérations affichées")
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.assertEqual(self.win.log_count_lbl.cget("text"), "2 opérations affichées")


# =======================================================================
# 2. Filtres
# =======================================================================
class FiltresTest(LogTabFunctionalTestCase):
    def setUp(self):
        super().setUp()
        self._log(
            tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
            user_name="Raj", player_name="Alice", category="eliminations", action="eliminate",
        )
        self._log(
            tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi",
            user_name="Marie", role="DIRTO", player_name="Bob",
            category="clock", action="toggle_pause", result=action_log.RESULT_DENIED,
        )
        self.win._build_log_tab()

    def test_combobox_alimentees_depuis_les_donnees_reelles(self):
        self.assertEqual(set(self.win.log_user_combo["values"]), {"Tous", "Marie", "Raj"})
        self.assertEqual(set(self.win.log_player_combo["values"]), {"Tous", "Alice", "Bob"})
        self.assertEqual(set(self.win.log_function_combo["values"]), {"Toutes", "Éliminations", "Chronomètre"})
        self.assertEqual(
            set(self.win.log_tournament_combo["values"]), {"Tous", "Tournoi A", "Tournoi B"},
        )

    def test_filtre_tournoi(self):
        self.win.log_tournament_var.set("Tournoi A")
        self.win._on_log_search()
        rows = self._tree_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][_COL_TOURNAMENT], "Tournoi A")

    def test_filtre_utilisateur(self):
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        rows = self._tree_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][_COL_PLAYER], "Bob")

    def test_filtre_fonction(self):
        self.win.log_function_var.set("Chronomètre")
        self.win._on_log_search()
        rows = self._tree_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][_COL_CATEGORY], "Chronomètre")

    def test_filtre_joueur(self):
        self.win.log_player_var.set("Alice")
        self.win._on_log_search()
        rows = self._tree_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][_COL_PLAYER], "Alice")

    def test_combinaison_de_plusieurs_filtres(self):
        self.win.log_tournament_var.set("Tournoi B")
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.assertEqual(len(self._tree_rows()), 1)

        self.win.log_user_var.set("Raj")
        self.win._on_log_search()
        self.assertEqual(self._tree_rows(), [])

    def test_tous_toutes_naucun_filtre(self):
        self.win.log_tournament_var.set("Tous")
        self.win.log_user_var.set("Tous")
        self.win.log_function_var.set("Toutes")
        self.win.log_player_var.set("Tous")
        self.win._on_log_search()
        self.assertEqual(len(self._tree_rows()), 2)


# =======================================================================
# 3. Validation Du/Au
# =======================================================================
class ValidationDatesTest(LogTabFunctionalTestCase):
    def setUp(self):
        super().setUp()
        self._log(player_name="Alice")
        self.win._build_log_tab()

    def test_date_calendairement_invalide_affiche_erreur_et_ne_modifie_pas_le_tableau(self):
        before = self._tree_rows()
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set("31/13/2026")
            self.win._on_log_search()
        self.assertTrue(mock_err.called)
        self.assertEqual(self._tree_rows(), before)

    def test_format_incorrect_affiche_erreur(self):
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set("2026-09-24")
            self.win._on_log_search()
        self.assertTrue(mock_err.called)

    def test_annee_sur_2_chiffres_refusee(self):
        # Piège connu de strptime("%Y") : accepterait sinon "24/09/26"
        # comme l'an 26 — la regex JJ/MM/AAAA doit l'exclure d'abord.
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set("24/09/26")
            self.win._on_log_search()
        self.assertTrue(mock_err.called)

    def test_espaces_autour_de_la_date_tolérés(self):
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set("  24/09/2026  ")
            self.win._on_log_search()
        self.assertFalse(mock_err.called)

    def test_du_superieur_a_au_affiche_erreur(self):
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set("24/09/2026")
            self.win.log_date_to_var.set("20/09/2026")
            self.win._on_log_search()
        self.assertTrue(mock_err.called)

    def test_dates_vides_ne_bloquent_pas_la_recherche(self):
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set("")
            self.win.log_date_to_var.set("")
            self.win._on_log_search()
        self.assertFalse(mock_err.called)
        self.assertEqual(len(self._tree_rows()), 1)

    def test_intervalle_valide_englobant_aujourdhui_conserve_la_ligne(self):
        today = datetime.now()
        with patch("main.messagebox.showerror") as mock_err:
            self.win.log_date_from_var.set((today - timedelta(days=1)).strftime("%d/%m/%Y"))
            self.win.log_date_to_var.set((today + timedelta(days=1)).strftime("%d/%m/%Y"))
            self.win._on_log_search()
        self.assertFalse(mock_err.called)
        self.assertEqual(len(self._tree_rows()), 1)

    def test_intervalle_excluant_aujourdhui_ne_renvoie_rien(self):
        today = datetime.now()
        self.win.log_date_from_var.set((today - timedelta(days=10)).strftime("%d/%m/%Y"))
        self.win.log_date_to_var.set((today - timedelta(days=5)).strftime("%d/%m/%Y"))
        self.win._on_log_search()
        self.assertEqual(self._tree_rows(), [])


# =======================================================================
# 4. Réinitialiser (option B validée)
# =======================================================================
class ReinitialiserTest(LogTabFunctionalTestCase):
    def test_reinitialiser_vide_les_filtres_et_relance_une_recherche_sans_filtre(self):
        self._log(tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi", player_name="Alice")
        self._log(
            tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi",
            player_name="Bob", user_name="Marie", role="DIRTO",
        )
        self.win._build_log_tab()

        self.win.log_date_from_var.set("01/01/2020")
        self.win.log_user_var.set("Marie")
        self.win.log_player_var.set("Bob")
        self.win._on_log_search()
        self.assertEqual(len(self._tree_rows()), 1)

        self.win._on_log_reset()
        self.assertEqual(self.win.log_date_from_var.get(), "")
        self.assertEqual(self.win.log_date_to_var.get(), "")
        self.assertEqual(self.win.log_tournament_var.get(), "Tous")
        self.assertEqual(self.win.log_user_var.get(), "Tous")
        self.assertEqual(self.win.log_function_var.get(), "Toutes")
        self.assertEqual(self.win.log_player_var.get(), "Tous")
        self.assertEqual(len(self._tree_rows()), 2)
        self.assertEqual(self.win.log_count_lbl.cget("text"), "2 opérations affichées")


# =======================================================================
# 5. Retour sur l'onglet LOG (filtres CONSERVÉS, jamais réinitialisés)
# =======================================================================
class RetourSurLongletTest(LogTabFunctionalTestCase):
    def test_retour_sur_log_relance_la_recherche_avec_les_filtres_conserves(self):
        self._log(user_name="Raj", player_name="Alice")
        self._log(user_name="Marie", role="DIRTO", player_name="Bob")
        self.win._build_log_tab()

        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        self.assertEqual(len(self._tree_rows()), 1)
        self.assertEqual(self.win.log_count_lbl.cget("text"), "1 opération affichée")

        # Une nouvelle action est journalisée PENDANT que l'utilisateur
        # est ailleurs — doit apparaître au retour, filtre "Marie"
        # toujours actif (jamais remis à zéro silencieusement).
        self._log(user_name="Marie", role="DIRTO", player_name="Chloé")
        self.win._refresh_all()  # current == "LOG" dans ce harnais réduit
        self.assertEqual(self.win.log_user_var.get(), "Marie")
        rows = self._tree_rows()
        self.assertEqual({r[_COL_PLAYER] for r in rows}, {"Bob", "Chloé"})
        self.assertEqual(self.win.log_count_lbl.cget("text"), "2 opérations affichées")


# =======================================================================
# 6. Export CSV
# =======================================================================
class ExportOpensDialogTest(LogTabFunctionalTestCase):
    """_on_log_export n'écrit plus rien lui-même depuis la correction du
    2026-09-24 ("Exporter" ouvre désormais LogExportDialog, sur le même
    principe que "Exporter les primes") — la couverture complète du
    MÉCANISME d'export (CSV/Excel/PDF, colonnes, libellés...) vit dans
    tests/test_log_export_dialog.py ; ce test-ci vérifie seulement que
    _on_log_export construit bien l'instantané des lignes affichées et
    ouvre la fenêtre avec (ou refuse si le tableau est vide)."""

    def setUp(self):
        super().setUp()
        self._log(player_name="Alice", user_name="Raj", role="ADMIN", message="premier essai")
        self._log(
            player_name="Bob", user_name=None, role="NONE",
            category="clock", action="toggle_pause", result=action_log.RESULT_DENIED,
            device_id="secret-device-id",
        )
        self.win._build_log_tab()

    def test_aucun_resultat_affiche_un_message_et_nouvre_pas_la_fenetre(self):
        for item in self.win.log_tree.get_children():
            self.win.log_tree.delete(item)
        with patch("main.messagebox.showinfo") as mock_info, \
             patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        mock_info.assert_called_once()
        mock_dialog.assert_not_called()

    def test_ouvre_logexportdialog_avec_linstantane_des_lignes_affichees(self):
        with patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        mock_dialog.assert_called_once()
        args = mock_dialog.call_args[0]
        self.assertIs(args[0], self.win)
        rows = args[1]
        self.assertEqual(len(rows), 2)
        self.assertEqual({r["player"] for r in rows}, {"Alice", "Bob"})
        self.assertEqual(
            {"ts", "tournament", "user", "role", "category", "action", "player", "result", "message"},
            set(rows[0].keys()),
        )
        for row in rows:
            self.assertNotIn("secret-device-id", row.values())

    def test_instantane_reflete_le_filtre_actif(self):
        self.win.log_player_var.set("Alice")
        self.win._on_log_search()
        with patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        rows = mock_dialog.call_args[0][1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player"], "Alice")


# =======================================================================
# 7. Détail au double-clic
# =======================================================================
class DetailDoubleClicTest(LogTabFunctionalTestCase):
    def _collect_texts(self, widget, out):
        if isinstance(widget, ttk.Label):
            out.append(widget.cget("text"))
        if isinstance(widget, tk.Text):
            out.append(widget.get("1.0", "end"))
        for child in widget.winfo_children():
            self._collect_texts(child, out)

    def test_double_clic_ouvre_une_fenetre_non_modale_avec_les_bons_champs(self):
        self._log(
            player_name="Alice", user_name="Raj", role="ADMIN",
            message="un message assez long pour vérifier la lisibilité complète",
            device_id="secret-device-id", device_label="iPhone de Raj",
        )
        self.win._build_log_tab()
        item_id = self.win.log_tree.get_children()[0]
        row = self.win._log_rows_by_item[item_id]

        before = set(self.win.winfo_children())
        self.win._show_log_detail_window(row)
        after = set(self.win.winfo_children())
        new_windows = [w for w in (after - before) if isinstance(w, tk.Toplevel)]
        self.assertEqual(len(new_windows), 1)
        detail_win = new_windows[0]

        # Non modale : ni grab_set ni wait_window ne doivent bloquer —
        # vérifié indirectement par le simple fait que ce test continue
        # à s'exécuter juste après l'appel ci-dessus, sans jamais se
        # figer (une fenêtre modale bloquerait ici).
        self.assertIsNone(detail_win.grab_status(), "aucun grab : la fenêtre doit rester non modale")

        texts = []
        self._collect_texts(detail_win, texts)
        joined = "\n".join(texts)
        self.assertIn("iPhone de Raj", joined)
        self.assertIn("un message assez long pour vérifier la lisibilité complète", joined)
        self.assertIn("Alice", joined)
        self.assertIn("Raj", joined)
        self.assertIn("Réussi", joined)
        self.assertNotIn("secret-device-id", joined)
        detail_win.destroy()

    def test_double_clic_sur_zone_vide_ne_leve_jamais(self):
        self.win._build_log_tab()
        fake_event = type("FakeEvent", (), {"y": 99999})()
        self.win._on_log_row_double_click(fake_event)  # ne doit pas lever


# =======================================================================
# 8. Désambiguïsation des libellés Tournoi (méthode statique, sans Tk)
# =======================================================================
class TournamentLabelDisambiguationTest(unittest.TestCase):
    def test_pas_de_doublon_nom_simple(self):
        entries = [{"tournament_name": "Tournoi A", "tournament_path": "/tmp/a.tournoi"}]
        path_to_label, label_to_path = main.App._build_log_tournament_label_maps(entries)
        self.assertEqual(path_to_label, {"/tmp/a.tournoi": "Tournoi A"})
        self.assertEqual(label_to_path, {"Tournoi A": "/tmp/a.tournoi"})

    def test_doublon_nom_dossiers_parents_differents(self):
        entries = [
            {"tournament_name": "Tournoi du vendredi", "tournament_path": "/data/2026-09/v.tournoi"},
            {"tournament_name": "Tournoi du vendredi", "tournament_path": "/data/2026-10/v.tournoi"},
        ]
        path_to_label, label_to_path = main.App._build_log_tournament_label_maps(entries)
        self.assertEqual(len(set(path_to_label.values())), 2, "les 2 libellés doivent être distincts")
        for label in path_to_label.values():
            self.assertIn("Tournoi du vendredi", label)
        self.assertEqual(set(label_to_path.values()), {"/data/2026-09/v.tournoi", "/data/2026-10/v.tournoi"})

    def test_doublon_nom_et_meme_dossier_parent_repli_sur_numerotation(self):
        entries = [
            {"tournament_name": "Tournoi du vendredi", "tournament_path": "/data/a/v1.tournoi"},
            {"tournament_name": "Tournoi du vendredi", "tournament_path": "/data/a/v2.tournoi"},
        ]
        path_to_label, label_to_path = main.App._build_log_tournament_label_maps(entries)
        self.assertEqual(len(set(path_to_label.values())), 2)
        self.assertTrue(any(l.endswith("#2") for l in path_to_label.values()))

    def test_tournament_path_jamais_present_dans_un_libelle(self):
        entries = [{"tournament_name": "T", "tournament_path": "/very/secret/path.tournoi"}]
        path_to_label, _ = main.App._build_log_tournament_label_maps(entries)
        self.assertNotIn("/very/secret/path.tournoi", path_to_label["/very/secret/path.tournoi"])

    def test_trois_tournois_memes_nom_et_dossier(self):
        entries = [
            {"tournament_name": "T", "tournament_path": f"/data/a/v{i}.tournoi"} for i in range(1, 4)
        ]
        path_to_label, label_to_path = main.App._build_log_tournament_label_maps(entries)
        self.assertEqual(len(set(path_to_label.values())), 3)
        self.assertEqual(len(label_to_path), 3)


if __name__ == "__main__":
    unittest.main()
