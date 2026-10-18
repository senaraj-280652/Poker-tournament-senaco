# -*- coding: utf-8 -*-
"""Second mode d'affichage "Participation" de l'onglet LOG (demande du
2026-10-16) : "permettre à un administrateur de consulter l'historique
des participations aux tournois du club pour un joueur, qu'il soit
membre officiel ou temporaire (TMP)."

Exigences couvertes :
1. "Participation" apparaît TOUJOURS dans la liste "Fonction" (jamais
   conditionnée à la présence de catégories réelles dans le journal).
2. La sélectionner bascule l'onglet (critères/tableau 9 colonnes du
   journal masqués, second mode affiché) — SANS jamais toucher au
   journal actions_log.py ni aux 8 fonctions existantes.
3. La recherche (Database.find_player_participations, database.py) ne
   fait QUE lire les fichiers .tournoi (read_only=True) — jamais une
   écriture, jamais une modification.
4. Colonnes du tableau : Date / Tournoi / Type / Statut / Classement.
   "Classement" réutilise EXCLUSIVEMENT players_rows()/official_rang
   (is_tmp_club) déjà validés — un TMP affiche "Hors classement".
5. Recherche en ARRIÈRE-PLAN, ANNULABLE, réutilisant exactement le même
   mécanisme déjà validé que "Purger les données" (data_purge.scan_
   folder_for_tournaments + thread/queue/polling).
6. Aucune modification du schéma de base de données.

Même conventions déjà établies : vraie Database SQLite en fichier
temporaire, vrai harnais Tk réduit (self.win = self.root, méthodes
greffées) identique à tests/test_log_tab_functional.py."""
import ast
import os
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

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import action_log  # noqa: E402
import data_purge  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
import export_prefs  # noqa: E402
import tournament_prefs  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _main_source():
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_py, encoding="utf-8") as f:
        return f.read()


def _database_source():
    db_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database.py")
    with open(db_py, encoding="utf-8") as f:
        return f.read()


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
# 1. database.py — _tournament_type_label / find_player_participations
# =======================================================================
class TournamentTypeLabelTest(unittest.TestCase):
    """Revu le 2026-10-18 : "is_sng" (réglage en base, écrit UNE SEULE
    fois à la création par "Sit & Go rapide", jamais modifié ensuite —
    voir main.App._apply_sng_defaults) est désormais la source PRIORITAIRE
    et la plus fiable, devant le nom de fichier — qui reste un repli
    nécessaire pour les tournois créés avant l'introduction de ce
    réglage (jamais en base chez eux, mais déjà conformes à la convention
    de nom "To"/"Sn")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tournament_type_label_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, filename, is_sng=None):
        db = database.Database(os.path.join(self._tmp.name, filename))
        self.addCleanup(db.close)
        if is_sng is not None:
            db.set_settings({"is_sng": is_sng})
        return db

    def test_prefixe_to_sans_is_sng(self):
        db = self._db("To270826.tournoi")
        self.assertEqual(database._tournament_type_label(db, db.path), "Tournoi")

    def test_prefixe_sn_sans_is_sng(self):
        """Repli sur le nom de fichier : cas d'un Sit & Go créé AVANT
        l'introduction du réglage "is_sng" en base — ne doit pas être
        mal classé "Tournoi" pour autant."""
        db = self._db("Sn270826.tournoi")
        self.assertEqual(database._tournament_type_label(db, db.path), "Sit & Go")

    def test_hors_convention_inconnu(self):
        db = self._db("tournoi.tournoi")
        self.assertEqual(database._tournament_type_label(db, db.path), "Inconnu")
        db2 = self._db("mon_tournoi_special.tournoi")
        self.assertEqual(database._tournament_type_label(db2, db2.path), "Inconnu")

    def test_is_sng_prioritaire_meme_si_le_fichier_est_renomme(self):
        """Cœur de la demande du 2026-10-18 : un Sit & Go renommé "à la
        main" sans le préfixe "Sn" reste correctement identifié grâce au
        réglage "is_sng", jamais mal classé "Inconnu" ou "Tournoi" par
        la seule faute du nom de fichier."""
        db = self._db("MaSoireeDuJeudi.tournoi", is_sng=1)
        self.assertEqual(database._tournament_type_label(db, db.path), "Sit & Go")

    def test_is_sng_prioritaire_meme_si_le_nom_suit_la_convention_tournoi(self):
        db = self._db("To270826.tournoi", is_sng=1)
        self.assertEqual(database._tournament_type_label(db, db.path), "Sit & Go")

    def test_is_sng_absent_reste_tournoi_normal(self):
        """Non-régression : un réglage "is_sng" absent (cas de l'immense
        majorité des tournois normaux, jamais écrit par "Nouveau
        tournoi") ne doit jamais faire basculer un tournoi normal vers
        "Sit & Go"."""
        db = self._db("To1.tournoi")
        self.assertEqual(db.get_setting_int("is_sng", 0), 0)
        self.assertEqual(database._tournament_type_label(db, db.path), "Tournoi")


class FindPlayerParticipationsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_participation_find_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, filename):
        return database.Database(os.path.join(self._tmp.name, filename))

    def test_regulier_classe_affiche_son_official_rang(self):
        db = self._db("To1.tournoi")
        p = db.add_player("Pierre", "CPC")
        j = db.add_player("Jacques", "CPC")
        db.eliminate_player(j, eliminated_by_id=p)
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Pierre")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["classement"], 1)
        self.assertEqual(res[0]["status"], "Actif")
        self.assertEqual(res[0]["type"], "Tournoi")  # "To1.tournoi" commence par "To"
        self.assertEqual(res[0]["tournament_name"], "Nouveau tournoi")

    def test_tmp_affiche_toujours_hors_classement(self):
        db = self._db("To270826.tournoi")
        p = db.add_player("Pierre", "CPC")
        v = db.add_player("Visiteur", "TMP")
        db.eliminate_player(v, eliminated_by_id=p)
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Visiteur")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["classement"], "Hors classement")
        self.assertEqual(res[0]["status"], "Éliminé")
        self.assertEqual(res[0]["type"], "Tournoi")

    def test_forfait_jamais_classe(self):
        db = self._db("To1.tournoi")
        a = db.add_player("Alice", "CPC")
        db.add_player("Bob", "CPC")
        db.withdraw_player(a)
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Alice")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["status"], "Forfait")
        self.assertEqual(res[0]["classement"], "-")

    def test_tournoi_en_cours_jamais_classe(self):
        """Deux joueurs encore actifs (aucun seul survivant) : pas encore
        classé -> "-", jamais un nombre inventé."""
        db = self._db("To1.tournoi")
        db.add_player("Alice", "CPC")
        db.add_player("Bob", "CPC")
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Alice")
        self.assertEqual(res[0]["classement"], "-")
        self.assertEqual(res[0]["status"], "Actif")

    def test_correspondance_de_nom_insensible_casse_et_espaces(self):
        db = self._db("To1.tournoi")
        db.add_player("Pierre Dupont", "CPC")
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        for variant in ("  pierre dupont  ", "PIERRE DUPONT", "Pierre Dupont"):
            res = database.find_player_participations(paths, variant)
            self.assertEqual(len(res), 1, variant)

    def test_nom_absent_renvoie_liste_vide(self):
        db = self._db("To1.tournoi")
        db.add_player("Pierre", "CPC")
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        self.assertEqual(database.find_player_participations(paths, "Inconnu"), [])

    def test_plusieurs_tournois_tries_par_date_croissante(self):
        db1 = self._db("A.tournoi")
        db1.set_settings({"tournament_date": "2026-03-01"})
        db1.add_player("Pierre", "CPC")
        db1.close()
        db2 = self._db("B.tournoi")
        db2.set_settings({"tournament_date": "2026-01-01"})
        db2.add_player("Pierre", "CPC")
        db2.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Pierre")
        self.assertEqual([r["date"] for r in res], ["2026-01-01", "2026-03-01"])

    def test_fichier_illisible_ignore_sans_exception(self):
        bad_path = os.path.join(self._tmp.name, "corrompu.tournoi")
        with open(bad_path, "w", encoding="utf-8") as f:
            f.write("ceci n'est pas un fichier sqlite valide")
        db = self._db("ok.tournoi")
        db.add_player("Pierre", "CPC")
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Pierre")
        self.assertEqual(len(res), 1)  # le fichier corrompu est silencieusement ignoré

    def test_cancel_event_arrete_avant_le_premier_fichier(self):
        db = self._db("To1.tournoi")
        db.add_player("Pierre", "CPC")
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        ev = threading.Event()
        ev.set()
        self.assertEqual(database.find_player_participations(paths, "Pierre", cancel_event=ev), [])

    def test_najoute_jamais_najecrit_rien_dans_le_fichier(self):
        """Précaution explicite : AUCUNE modification d'un fichier de
        tournoi réel — comparaison du contenu binaire avant/après."""
        db = self._db("To1.tournoi")
        p = db.add_player("Pierre", "CPC")
        j = db.add_player("Jacques", "CPC")
        db.eliminate_player(j, eliminated_by_id=p)
        path = db.path
        db.close()
        with open(path, "rb") as f:
            before = f.read()
        database.find_player_participations([path], "Pierre")
        with open(path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_colonnes_renvoyees_jamais_davantage(self):
        db = self._db("To1.tournoi")
        pierre = db.add_player("Pierre", "CPC")
        jacques = db.add_player("Jacques", "CPC")
        db.eliminate_player(jacques, eliminated_by_id=pierre)
        db.close()
        paths = database.find_tournament_files(self._tmp.name)
        res = database.find_player_participations(paths, "Pierre")
        self.assertEqual(
            set(res[0].keys()), {"date", "tournament_name", "path", "type", "status", "classement"},
        )


class FindPlayerParticipationsDateRangeTest(unittest.TestCase):
    """Filtres Du/Au (demande du 2026-10-17) : date_from/date_to sont des
    chaînes ISO "AAAA-MM-JJ", bornes INCLUSES, comparées à get_tournament_
    date() — même convention que build_period_summary, jamais une
    logique de comparaison dupliquée."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_participation_dates_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, filename, date):
        db = database.Database(os.path.join(self._tmp.name, filename))
        db.set_settings({"tournament_date": date})
        return db

    def _paths(self):
        return database.find_tournament_files(self._tmp.name)

    def test_date_from_exclut_les_participations_anterieures(self):
        db1 = self._db("A.tournoi", "2026-01-15")
        db1.add_player("Pierre", "CPC")
        db1.close()
        db2 = self._db("B.tournoi", "2026-06-15")
        db2.add_player("Pierre", "CPC")
        db2.close()
        res = database.find_player_participations(self._paths(), "Pierre", date_from="2026-03-01")
        self.assertEqual([r["date"] for r in res], ["2026-06-15"])

    def test_date_to_exclut_les_participations_posterieures(self):
        db1 = self._db("A.tournoi", "2026-01-15")
        db1.add_player("Pierre", "CPC")
        db1.close()
        db2 = self._db("B.tournoi", "2026-06-15")
        db2.add_player("Pierre", "CPC")
        db2.close()
        res = database.find_player_participations(self._paths(), "Pierre", date_to="2026-03-01")
        self.assertEqual([r["date"] for r in res], ["2026-01-15"])

    def test_bornes_incluses(self):
        db = self._db("A.tournoi", "2026-03-01")
        db.add_player("Pierre", "CPC")
        db.close()
        res_from = database.find_player_participations(self._paths(), "Pierre", date_from="2026-03-01")
        res_to = database.find_player_participations(self._paths(), "Pierre", date_to="2026-03-01")
        self.assertEqual(len(res_from), 1)
        self.assertEqual(len(res_to), 1)

    def test_combinaison_date_from_et_date_to(self):
        for name, date in (("A.tournoi", "2026-01-01"), ("B.tournoi", "2026-03-15"), ("C.tournoi", "2026-06-01")):
            db = self._db(name, date)
            db.add_player("Pierre", "CPC")
            db.close()
        res = database.find_player_participations(
            self._paths(), "Pierre", date_from="2026-02-01", date_to="2026-05-01",
        )
        self.assertEqual([r["date"] for r in res], ["2026-03-15"])

    def test_aucune_borne_renvoie_tout_comme_avant(self):
        """Non-régression : date_from/date_to=None (défaut) ne filtre
        rien — comportement identique à avant l'ajout de ces 2 nouveaux
        paramètres optionnels."""
        for name, date in (("A.tournoi", "2026-01-01"), ("B.tournoi", "2026-06-01")):
            db = self._db(name, date)
            db.add_player("Pierre", "CPC")
            db.close()
        res = database.find_player_participations(self._paths(), "Pierre")
        self.assertEqual(len(res), 2)

    def test_cancel_event_toujours_respecte_avec_des_bornes(self):
        db = self._db("A.tournoi", "2026-03-01")
        db.add_player("Pierre", "CPC")
        db.close()
        ev = threading.Event()
        ev.set()
        res = database.find_player_participations(
            self._paths(), "Pierre", date_from="2026-01-01", date_to="2026-12-31", cancel_event=ev,
        )
        self.assertEqual(res, [])


class FindPlayerParticipationsReadOnlyStructuralTest(unittest.TestCase):
    """Vérification STRUCTURELLE (AST) : find_player_participations
    n'ouvre chaque fichier .tournoi qu'en lecture seule, jamais la
    moindre instruction d'écriture SQL."""

    def setUp(self):
        self.source = _database_source()
        self.tree = ast.parse(self.source, filename="database.py")

    def _find_function(self, name):
        return next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        )

    def _source_of(self, node):
        return ast.get_source_segment(self.source, node) or ""

    def test_ouvre_toujours_en_lecture_seule(self):
        func = self._find_function("find_player_participations")
        seg = self._source_of(func)
        self.assertIn("read_only=True", seg)

    def test_jamais_dinstruction_decriture_sql(self):
        func = self._find_function("find_player_participations")
        seg = self._source_of(func)
        for forbidden in ("INSERT", "UPDATE", "DELETE", "ALTER TABLE", "CREATE TABLE", "db.conn.execute"):
            self.assertNotIn(forbidden, seg)

    def test_reutilise_official_rang_et_is_tmp_club_jamais_une_formule_dupliquee(self):
        func = self._find_function("find_player_participations")
        seg = self._source_of(func)
        self.assertIn("official_rang", seg)
        self.assertIn("is_tmp_club(", seg)
        self.assertIn("players_rows()", seg)
        self.assertNotIn("official_standings(", seg)  # jamais recalculé ici, déjà fait par players_rows


# =======================================================================
# 2. main.py — UI (même harnais réduit que test_log_tab_functional.py)
# =======================================================================
_LOG_METHODS = (
    "_build_log_tab", "_build_log_search_criteria_placeholder",
    "_refresh_log_tab", "_refresh_log_filter_choices",
    "_parse_log_date_field", "_current_log_filter_values", "_populate_log_tree",
    "_on_log_search", "_on_log_reset", "_on_log_export",
    "_on_log_row_double_click", "_show_log_detail_window",
    "_show_log_date_picker", "_confirm_log_purge", "_log_purge_filter_summary",
    "_on_log_purge", "_refresh_all", "_on_purge_choice",
    "_apply_log_view_mode", "_build_log_participation_tab", "_build_log_persistent_bar",
    "_browse_log_participation_folder", "_start_log_participation_search",
    "_poll_log_participation_queue", "_on_log_participation_search_finished",
    "_on_cancel_log_participation_search", "_update_log_participation_scan_ui",
    "_on_log_participation_export",
    "_return_to_normal_log_view", "_apply_log_participation_tournament_filter",
)
_LOG_STATIC_METHODS = (
    "_build_log_tournament_label_maps", "_format_log_ts", "_log_count_label_text",
    "_run_log_participation_search_worker",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogParticipationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_participation_ui_")
        self.addCleanup(self._tmp.cleanup)
        for target in (
            patch.object(action_log, "_log_path", return_value=os.path.join(self._tmp.name, "actions_log.sqlite3")),
            patch.object(roster, "_roster_path", return_value=os.path.join(self._tmp.name, "roster.json")),
            patch.object(export_prefs, "_prefs_path", return_value=os.path.join(self._tmp.name, "export_prefs.json")),
            patch.object(tournament_prefs, "_prefs_path", return_value=os.path.join(self._tmp.name, "last_settings.json")),
        ):
            self.addCleanup(target.stop)
            target.start()

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

        self.tournaments_dir = os.path.join(self._tmp.name, "tournaments")
        os.makedirs(self.tournaments_dir, exist_ok=True)

        self.win._build_log_tab()

    def _wait_for_search(self, timeout=10):
        """Fait avancer le sondage de la recherche en arrière-plan sans
        JAMAIS appeler root.update()/update_idletasks() — voir tests/
        test_data_purge_dialog.py::_pump_until_scan_idle, même risque
        documenté (VRAI SIGSEGV reproduit, 2026-10-08) pour exactement
        ce genre de mécanisme thread+file d'attente+self.after. Appelle
        directement _poll_log_participation_queue (une méthode normale,
        sans rapport avec la boucle d'événements), après avoir annulé le
        sondage programmé via self.after pour ne jamais en accumuler."""
        start = time.monotonic()
        while self.win._log_participation_scan_in_progress:
            if self.win._log_participation_poll_after_id is not None:
                try:
                    self.win.after_cancel(self.win._log_participation_poll_after_id)
                except tk.TclError:
                    pass
                self.win._log_participation_poll_after_id = None
            self.win._poll_log_participation_queue()
            if time.monotonic() - start > timeout:
                raise AssertionError("la recherche ne s'est jamais terminée (timeout de test)")
            time.sleep(0.01)

    def _rows(self):
        return [self.win.log_participation_tree.item(i, "values") for i in self.win.log_participation_tree.get_children()]


class ParticipationAlwaysInFunctionListTest(LogParticipationTestCase):
    def test_participation_toujours_proposee(self):
        self.assertIn(main.LOG_PARTICIPATION_LABEL, self.win.log_function_combo["values"])

    def test_participation_toujours_proposee_meme_sans_aucune_categorie(self):
        """Aucune action journalisée : les 8 fonctions normales
        n'apparaissent pas (pas de catégorie réelle), mais
        "Participation" reste toujours là."""
        self.assertEqual(
            set(self.win.log_function_combo["values"]), {"Toutes", main.LOG_PARTICIPATION_LABEL},
        )


class ViewToggleTest(LogParticipationTestCase):
    def test_selection_participation_masque_le_mode_normal(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.assertEqual(self.win._log_normal_criteria_frame.winfo_manager(), "")
        self.assertEqual(self.win.log_count_lbl.winfo_manager(), "")
        self.assertEqual(self.win._log_normal_results_frame.winfo_manager(), "")
        self.assertEqual(self.win._log_participation_frame.winfo_manager(), "pack")

    def test_retour_a_toutes_restaure_le_mode_normal(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_function_var.set("Toutes")
        self.win._apply_log_view_mode()
        self.assertEqual(self.win._log_normal_criteria_frame.winfo_manager(), "pack")
        self.assertEqual(self.win.log_count_lbl.winfo_manager(), "pack")
        self.assertEqual(self.win._log_normal_results_frame.winfo_manager(), "pack")
        self.assertEqual(self.win._log_participation_frame.winfo_manager(), "")

    def test_reinitialiser_restaure_aussi_le_mode_normal(self):
        """_on_log_reset doit appeler _apply_log_view_mode (défense en
        profondeur : ce bouton est de toute façon caché en mode
        Participation, mais le mode doit être restauré si jamais la
        variable "Fonction" est remise à "Toutes" par ce chemin)."""
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win._on_log_reset()
        self.assertEqual(self.win.log_function_var.get(), "Toutes")
        self.assertEqual(self.win._log_normal_criteria_frame.winfo_manager(), "pack")

    def test_aucune_des_8_fonctions_najamais_modifiee_par_le_bascule(self):
        """Le journal actions_log.py n'est jamais interrogé pendant un
        changement de mode d'affichage pur (pas de recherche)."""
        with patch.object(action_log, "search_actions") as mock_search:
            self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
            self.win._apply_log_view_mode()
        mock_search.assert_not_called()


class RefreshLogTabGuardedInParticipationModeTest(LogParticipationTestCase):
    def test_refresh_log_tab_najamais_action_log_en_mode_participation(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        with patch.object(action_log, "search_actions") as mock_search:
            self.win._refresh_log_tab()
        mock_search.assert_not_called()

    def test_refresh_log_tab_fonctionne_normalement_hors_participation(self):
        with patch.object(action_log, "search_actions", return_value=([], False)) as mock_search:
            self.win._refresh_log_tab()
        mock_search.assert_called_once()


class ParticipationSearchTest(LogParticipationTestCase):
    def test_dossier_vide_naffiche_pas_de_boite_derreur(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set("")
        self.win.log_participation_player_var.set("Pierre")
        with patch.object(main.messagebox, "showerror") as mock_err:
            self.win._start_log_participation_search()
        mock_err.assert_called_once()
        self.assertFalse(self.win._log_participation_scan_in_progress)

    def test_nom_vide_naffiche_pas_de_boite_derreur(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("")
        with patch.object(main.messagebox, "showerror") as mock_err:
            self.win._start_log_participation_search()
        mock_err.assert_called_once()

    def test_recherche_trouve_un_regulier_et_un_tmp(self):
        db = database.Database(os.path.join(self.tournaments_dir, "To270826.tournoi"))
        pierre = db.add_player("Pierre", "CPC")
        visiteur = db.add_player("Visiteur", "TMP")
        db.eliminate_player(visiteur, eliminated_by_id=pierre)
        db.close()

        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("Visiteur")
        self.win._start_log_participation_search()
        self._wait_for_search()

        rows = self._rows()
        self.assertEqual(len(rows), 1)
        _date, tournament, ttype, status, classement = rows[0]
        self.assertEqual(ttype, "Tournoi")
        self.assertEqual(status, "Éliminé")
        self.assertEqual(classement, "Hors classement")
        self.assertIn("participation", self.win.log_participation_status_lbl.cget("text"))

    def test_recherche_annulee_affiche_resultat_partiel(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("Pierre")
        self.win._start_log_participation_search()
        self.win._on_cancel_log_participation_search()
        self._wait_for_search()
        self.assertIn("annulée", self.win.log_participation_status_lbl.cget("text"))

    def test_une_nouvelle_recherche_annule_la_precedente(self):
        """Démarrer une 2e recherche doit invalider la 1ère (génération),
        jamais un résultat tardif qui écraserait le résultat actuel."""
        db = database.Database(os.path.join(self.tournaments_dir, "To1.tournoi"))
        db.add_player("Pierre", "CPC")
        db.close()
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("Pierre")

        gen1 = self.win._log_participation_scan_generation
        self.win._start_log_participation_search()
        gen2 = self.win._log_participation_scan_generation
        self.assertNotEqual(gen1, gen2)
        self._wait_for_search()
        # La 1ère annulée en interne par le lancement de la 2e, résultat
        # final cohérent (pas d'exception, pas de blocage).
        self.assertFalse(self.win._log_participation_scan_in_progress)

    def test_fichier_tournoi_jamais_modifie_par_une_recherche(self):
        path = os.path.join(self.tournaments_dir, "To1.tournoi")
        db = database.Database(path)
        db.add_player("Pierre", "CPC")
        db.close()
        with open(path, "rb") as f:
            before = f.read()

        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("Pierre")
        self.win._start_log_participation_search()
        self._wait_for_search()

        with open(path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_dossier_prerempli_avec_le_defaut_general(self):
        tournament_prefs.save_last_settings({"tournament_day_folder": "/dossier/general"})
        self.win._build_log_participation_tab()  # reconstruit après le réglage, comme Statistiques
        self.assertEqual(self.win.log_participation_folder_var.get(), "/dossier/general")


class ScopeBoundaryStructuralTest(unittest.TestCase):
    """Vérifie que le mécanisme "Participation" ne touche jamais au
    journal actions_log.py ni aux 8 fonctions existantes, et que le
    thread de recherche est bien une méthode STATIQUE sans référence à
    self/Tkinter — même principe que DataPurgeDialog._run_scan_worker."""

    def setUp(self):
        self.source = _main_source()
        self.tree = ast.parse(self.source, filename="main.py")

    def _find_method(self, class_name, method_name):
        cls_node = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.ClassDef) and n.name == class_name
        )
        return next(
            n for n in ast.walk(cls_node)
            if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def _source_of(self, node):
        return ast.get_source_segment(self.source, node) or ""

    def test_worker_est_une_methode_statique(self):
        func = self._find_method("App", "_run_log_participation_search_worker")
        self.assertTrue(any(isinstance(d, ast.Name) and d.id == "staticmethod" for d in func.decorator_list))

    def test_worker_najamais_self_ni_messagebox_ni_tkinter(self):
        func = self._find_method("App", "_run_log_participation_search_worker")
        seg = self._source_of(func)
        self.assertNotIn("self.", seg)
        self.assertNotIn("messagebox", seg)
        self.assertNotIn("self.after", seg)

    def test_worker_reutilise_scan_folder_for_tournaments(self):
        func = self._find_method("App", "_run_log_participation_search_worker")
        seg = self._source_of(func)
        self.assertIn("data_purge.scan_folder_for_tournaments", seg)
        self.assertIn("find_player_participations", seg)

    def test_refresh_log_tab_najamais_de_recherche_participation(self):
        func = self._find_method("App", "_refresh_log_tab")
        seg = self._source_of(func)
        self.assertIn("LOG_PARTICIPATION_LABEL", seg)

    def test_huit_fonctions_existantes_jamais_touchees_par_participation(self):
        """Les méthodes qui alimentent les 8 fonctions existantes ne
        référencent jamais "Participation" dans leur logique de calcul
        (seule _refresh_log_filter_choices l'ajoute en tant que valeur
        TOUJOURS proposée, jamais une catégorie réelle) — _on_log_export
        est volontairement EXCLUE de cette liste : c'est le point
        d'entrée UNIQUE du bouton "Exporter" (demande du 2026-10-16,
        "il faut le réutiliser, sans ajouter de nouveau bouton"), donc
        DOIT référencer LOG_PARTICIPATION_LABEL pour y distinguer les
        deux modes (voir test_export_dispatche_selon_le_mode ci-dessous
        pour la couverture dédiée de ce dispatch)."""
        for name in ("_current_log_filter_values", "_populate_log_tree", "_on_log_purge"):
            func = self._find_method("App", name)
            seg = self._source_of(func)
            self.assertNotIn("LOG_PARTICIPATION_LABEL", seg, name)


# =======================================================================
# 3. Export du mode "Participation" (demande du 2026-10-16) :
#    - "Exporter" (self.log_export_btn) reste l'UNIQUE bouton, réutilisé
#      dans les deux modes — jamais un second bouton ni une seconde
#      implémentation (voir App._on_log_export, point d'entrée unique).
#    - Colonnes Date/Tournoi/Type/Statut/Classement, dans cet ordre,
#      EXACTEMENT celles affichées à l'écran.
#    - Titres/libellés/compteurs d'un historique de participation,
#      jamais d'un journal d'opérations.
#    - Préférences (export_prefs) et colonnes ENTIÈREMENT indépendantes
#      de celles du mode "Journal des actions" (clé "log_participation",
#      jamais "log").
#    - Mode normal : comportement de l'export strictement inchangé.
# =======================================================================
class LogExportDispatchTest(LogParticipationTestCase):
    """_on_log_export (demande du 2026-10-16) : point d'entrée UNIQUE,
    qui dispatche selon le mode actuellement affiché — jamais un second
    bouton, jamais une seconde implémentation parallèle."""

    def test_mode_normal_najamais_lexport_participation(self):
        with patch.object(self.win, "_on_log_participation_export") as mock_part, \
             patch.object(main.messagebox, "showinfo"):
            self.win._on_log_export()
        mock_part.assert_not_called()

    def test_mode_participation_appelle_lexport_participation(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        with patch.object(self.win, "_on_log_participation_export") as mock_part:
            self.win._on_log_export()
        mock_part.assert_called_once()

    def test_un_seul_bouton_exporter_dans_les_deux_modes(self):
        """Non-régression : self.log_export_btn reste le MÊME widget
        Tk, quel que soit le mode affiché — jamais un second bouton
        créé par le passage en mode Participation."""
        btn_before = self.win.log_export_btn
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.assertIs(self.win.log_export_btn, btn_before)
        self.win.log_function_var.set("Toutes")
        self.win._apply_log_view_mode()
        self.assertIs(self.win.log_export_btn, btn_before)

    def test_aucun_resultat_participation_naffiche_pas_de_boite(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        with patch.object(main.messagebox, "showinfo") as mock_info, \
             patch.object(main, "LogParticipationExportDialog") as mock_dialog:
            self.win._on_log_export()
        mock_info.assert_called_once()
        mock_dialog.assert_not_called()

    def test_mode_normal_toujours_vers_logexportdialog(self):
        """Non-régression explicite : en mode normal, le comportement
        existant (ouverture de LogExportDialog) reste strictement
        inchangé, même après ce chantier."""
        self.win.log_tree.insert(
            "", "end",
            values=("16/10/2026 10:00:00", "T", "Raj", "ADMIN", "Éliminations", "eliminate", "Alice", "Réussi", ""),
        )
        with patch.object(main, "LogExportDialog") as mock_dialog, \
             patch.object(main, "LogParticipationExportDialog") as mock_part_dialog:
            self.win._on_log_export()
        mock_dialog.assert_called_once()
        mock_part_dialog.assert_not_called()

    def test_export_participation_transmet_les_lignes_affichees_dans_lordre(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_tree.insert(
            "", "end", values=("01/01/2026", "Tournoi A", "Tournoi", "Éliminé", 2),
        )
        self.win.log_participation_tree.insert(
            "", "end", values=("02/01/2026", "Tournoi B", "Sit & Go", "Éliminé", "Hors classement"),
        )
        with patch.object(main, "LogParticipationExportDialog") as mock_dialog:
            self.win._on_log_export()
        mock_dialog.assert_called_once()
        args = mock_dialog.call_args[0]
        rows = args[1]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["tournament"], "Tournoi A")
        self.assertEqual(rows[1]["classement"], "Hors classement")


class LogParticipationExportDialogGuiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_participation_export_gui_")
        self.addCleanup(self._tmp.cleanup)
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        # default_export_dir (demande du 2026-10-16, "Dossier par
        # défaut") lit tournament_prefs à chaque export — jamais le vrai
        # ~/.poker_tournament/last_settings.json de ce poste pendant les
        # tests, pour rester isolé de tout réglage réel déjà présent.
        tp_patcher = patch.object(
            tournament_prefs, "_prefs_path",
            return_value=os.path.join(self._tmp.name, "last_settings.json"),
        )
        self.addCleanup(tp_patcher.stop)
        tp_patcher.start()
        open_patcher = patch("main.open_file_with_default_app")
        self.addCleanup(open_patcher.stop)
        open_patcher.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _rows(self):
        return [
            {
                "date": "16/10/2026", "tournament": "Nouveau tournoi", "type": "Tournoi",
                "status": "Éliminé", "classement": "Hors classement",
            },
            {
                "date": "17/10/2026", "tournament": "Autre tournoi", "type": "Sit & Go",
                "status": "Actif", "classement": 1,
            },
        ]

    def _open(self, rows=None, player_name="Visiteur"):
        dlg = main.LogParticipationExportDialog(
            self.root, rows if rows is not None else self._rows(), player_name,
        )
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        return dlg


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogParticipationExportColumnsTest(LogParticipationExportDialogGuiTestCase):
    def test_5_colonnes_dans_lordre(self):
        self.assertEqual(
            [k for k, _ in action_log.LOG_PARTICIPATION_EXPORT_COLUMNS],
            ["date", "tournament", "type", "status", "classement"],
        )
        self.assertEqual(
            [h for _, h in action_log.LOG_PARTICIPATION_EXPORT_COLUMNS],
            ["Date", "Tournoi", "Type", "Statut", "Classement"],
        )

    def test_toutes_cochees_par_defaut(self):
        dlg = self._open()
        self.assertTrue(all(v.get() for v in dlg.col_vars.values()))

    def test_titre_de_la_fenetre(self):
        dlg = self._open()
        self.assertEqual(dlg.title(), "Exporter l'historique de participation")

    def test_pas_de_section_tableau_a_exporter(self):
        """Même principe que LogExportDialog : un seul tableau, aucune
        section de choix dédiée (contrairement à Primes, qui en a
        deux)."""
        dlg = self._open()

        def _labelframe_texts(widget):
            texts = []
            for c in widget.winfo_children():
                if isinstance(c, ttk.LabelFrame):
                    texts.append(c.cget("text"))
                texts.extend(_labelframe_texts(c))
            return texts

        self.assertEqual(sorted(_labelframe_texts(dlg)), ["Colonnes à exporter", "Format"])


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogParticipationExportWordingTest(LogParticipationExportDialogGuiTestCase):
    """Demande explicite : "les titres, libellés et compteurs des
    exports CSV, Excel et PDF doivent correspondre à un historique de
    participation, et non à un journal d'opérations." """

    def test_csv_titre_et_compteur_participation(self):
        dlg = self._open(player_name="Visiteur")
        out_path = os.path.join(self._tmp.name, "out.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        with open(out_path, encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("Historique de participation", content)
        self.assertIn("Visiteur", content)
        self.assertIn("Nombre de participations : 2", content)
        self.assertNotIn("Journal des actions", content)
        self.assertNotIn("Nombre d'opérations", content)

    def test_xlsx_titre_feuille_et_compteur(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl indisponible dans cet environnement")
        dlg = self._open(player_name="Visiteur")
        dlg.format_var.set("xlsx")
        out_path = os.path.join(self._tmp.name, "out.xlsx")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        wb = load_workbook(out_path)
        ws = wb.active
        self.assertEqual(ws.title, "Participation")
        col_a_values = [row[0].value for row in ws.iter_rows(min_col=1, max_col=1)]
        self.assertTrue(any(v and "Nombre de participations : 2" == v for v in col_a_values))
        self.assertTrue(any(v and "Historique de participation" in str(v) for v in col_a_values))

    def test_pdf_sexporte_sans_erreur(self):
        try:
            import fpdf  # noqa: F401
        except ImportError:
            self.skipTest("fpdf2 indisponible dans cet environnement")
        dlg = self._open(player_name="Visiteur")
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "out.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        self.assertTrue(os.path.exists(out_path))
        self.assertGreater(os.path.getsize(out_path), 0)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogParticipationExportFormatDispatchTest(LogParticipationExportDialogGuiTestCase):
    def test_choix_csv_appelle_export_csv(self):
        dlg = self._open()
        dlg.format_var.set("csv")
        out_path = os.path.join(self._tmp.name, "out.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_csv") as mock_export, \
             patch("action_log.export_xlsx") as mock_xlsx, \
             patch("action_log.export_pdf") as mock_pdf:
            dlg._do_export()
        mock_export.assert_called_once()
        mock_xlsx.assert_not_called()
        mock_pdf.assert_not_called()

    def test_choix_xlsx_appelle_export_xlsx(self):
        dlg = self._open()
        dlg.format_var.set("xlsx")
        out_path = os.path.join(self._tmp.name, "out.xlsx")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_csv") as mock_csv, \
             patch("action_log.export_xlsx") as mock_export, \
             patch("action_log.export_pdf") as mock_pdf:
            dlg._do_export()
        mock_export.assert_called_once()
        mock_csv.assert_not_called()
        mock_pdf.assert_not_called()

    def test_choix_pdf_appelle_export_pdf(self):
        dlg = self._open()
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "out.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_csv") as mock_csv, \
             patch("action_log.export_xlsx") as mock_xlsx, \
             patch("action_log.export_pdf") as mock_export:
            dlg._do_export()
        mock_export.assert_called_once()
        mock_csv.assert_not_called()
        mock_xlsx.assert_not_called()

    def test_aucune_colonne_cochee_refuse_lexport(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)
        with patch.object(main.messagebox, "showerror") as mock_err, \
             patch("main.filedialog.asksaveasfilename") as mock_save:
            dlg._do_export()
        mock_err.assert_called_once()
        mock_save.assert_not_called()


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogParticipationInitialDirJamaisLaRacineDuDisqueTest(LogParticipationExportDialogGuiTestCase):
    """Correctif du 2026-10-16 : essai manuel réel ayant échoué avec
    "Read-only file system" sur "/participation_CYANE_...pdf" (Mac,
    asksaveasfilename sans initialdir). Vérifié ici pour les 3 formats
    qu'un dossier RÉEL, accessible en écriture, est désormais toujours
    transmis — jamais "/", et jamais au détriment de la liberté de
    choix de l'utilisateur (voir le second test)."""

    def test_initialdir_toujours_fourni_et_jamais_la_racine(self):
        for fmt in ("csv", "xlsx", "pdf"):
            with self.subTest(fmt=fmt):
                dlg = self._open()
                dlg.format_var.set(fmt)
                export_fn = {"csv": "export_csv", "xlsx": "export_xlsx", "pdf": "export_pdf"}[fmt]
                with patch("main.filedialog.asksaveasfilename", return_value="") as mock_save, \
                     patch(f"action_log.{export_fn}"):
                    dlg._do_export()
                kwargs = mock_save.call_args[1]
                self.assertIn("initialdir", kwargs)
                self.assertNotEqual(kwargs["initialdir"], "/")
                self.assertTrue(os.path.isdir(kwargs["initialdir"]))
                self.assertTrue(os.access(kwargs["initialdir"], os.W_OK))

    def test_utilisateur_reste_libre_de_choisir_un_autre_dossier(self):
        dlg = self._open()
        dlg.format_var.set("csv")
        chosen_dir = os.path.join(self._tmp.name, "ailleurs")
        os.makedirs(chosen_dir, exist_ok=True)
        chosen_path = os.path.join(chosen_dir, "mon_historique.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=chosen_path), \
             patch("action_log.export_csv") as mock_export:
            dlg._do_export()
        self.assertEqual(mock_export.call_args[0][0], chosen_path)

    def test_dossier_par_defaut_configure_et_valide_est_utilise(self):
        """Demande du 2026-10-16 : le "Dossier par défaut" de Senaco
        (tournament_day_folder) devient le point de départ quand il est
        renseigné et réellement accessible."""
        configured = os.path.join(self._tmp.name, "dossier_du_jour")
        os.makedirs(configured, exist_ok=True)
        tournament_prefs.save_last_settings({"tournament_day_folder": configured})
        dlg = self._open()
        dlg.format_var.set("csv")
        with patch("main.filedialog.asksaveasfilename", return_value="") as mock_save, \
             patch("action_log.export_csv"):
            dlg._do_export()
        self.assertEqual(mock_save.call_args[1]["initialdir"], configured)

    def test_dossier_par_defaut_introuvable_avertit_puis_replie_sur_le_personnel(self):
        configured = os.path.join(self._tmp.name, "disparu")
        tournament_prefs.save_last_settings({"tournament_day_folder": configured})
        dlg = self._open()
        dlg.format_var.set("csv")
        with patch("main.filedialog.asksaveasfilename", return_value="") as mock_save, \
             patch("action_log.export_csv"), \
             patch.object(main.messagebox, "showwarning") as mock_warn:
            dlg._do_export()
        mock_warn.assert_called_once()
        self.assertIn(configured, mock_warn.call_args[0][1])
        self.assertEqual(mock_save.call_args[1]["initialdir"], os.path.expanduser("~"))


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogParticipationExportPreferencesIndependentTest(LogParticipationExportDialogGuiTestCase):
    """Demande explicite : "les préférences d'export des deux modes
    doivent être indépendantes." """

    def test_format_memorise_sous_une_cle_dediee_jamais_log(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)
        next(iter(dlg.col_vars.values())).set(True)
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "out.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_pdf"):
            dlg._do_export()
        self.assertEqual(export_prefs.load_format("log_participation"), "pdf")
        self.assertEqual(export_prefs.load_format("log"), "csv")  # valeur par défaut, jamais affectée

    def test_colonnes_cochees_jamais_influencees_par_les_preferences_log(self):
        export_prefs.save_columns("log", ["ts", "player"])
        dlg = self._open()
        self.assertTrue(all(v.get() for v in dlg.col_vars.values()))

    def test_preferences_log_jamais_affectees_par_un_export_participation(self):
        export_prefs.save_columns("log_participation", ["date"])
        export_prefs.save_format("log_participation", "pdf")
        self.assertEqual(
            set(export_prefs.load_columns("log", [k for k, _ in action_log.LOG_EXPORT_COLUMNS])),
            set(k for k, _ in action_log.LOG_EXPORT_COLUMNS),
        )


class ExportColumnsAndEnginesIndependenceTest(unittest.TestCase):
    """Niveau action_log.py seul (aucun Tk) : les colonnes/fonctions
    d'export des deux modes restent indépendantes, et le paramètre
    count_label ajouté le 2026-10-16 ne change rien par défaut."""

    def test_selected_log_participation_export_columns_respecte_lordre(self):
        cols = action_log.selected_log_participation_export_columns(["classement", "date"])
        self.assertEqual([k for k, _ in cols], ["date", "classement"])

    def test_none_renvoie_toutes_les_colonnes_participation(self):
        self.assertEqual(
            action_log.selected_log_participation_export_columns(None),
            action_log.LOG_PARTICIPATION_EXPORT_COLUMNS,
        )

    def test_count_label_par_defaut_inchange_pour_les_8_fonctions(self):
        with tempfile.TemporaryDirectory(prefix="count_label_default_") as tmp:
            path = os.path.join(tmp, "out.csv")
            action_log.export_csv(path, action_log.LOG_EXPORT_COLUMNS, [], title="Journal des actions")
            with open(path, encoding="utf-8-sig") as f:
                content = f.read()
            self.assertIn("Nombre d'opérations : 0", content)

    def test_count_label_personnalise_remplace_le_texte_par_defaut(self):
        with tempfile.TemporaryDirectory(prefix="count_label_custom_") as tmp:
            path = os.path.join(tmp, "out.csv")
            action_log.export_csv(
                path, action_log.LOG_PARTICIPATION_EXPORT_COLUMNS, [],
                title="Historique de participation", count_label="Nombre de participations",
            )
            with open(path, encoding="utf-8-sig") as f:
                content = f.read()
            self.assertIn("Nombre de participations : 0", content)
            self.assertNotIn("Nombre d'opérations", content)


# =======================================================================
# 3. main.py — filtres Du/Au/Tournoi + bouton "Retour" (demande du
#    2026-10-17)
# =======================================================================
class DateFilterTest(LogParticipationTestCase):
    """"Du"/"Au" (demande du 2026-10-17) : réutilisent _parse_log_date_
    field tel quel, validés au clic sur "Rechercher" puis transmis à
    Database.find_player_participations (voir FindPlayerParticipations
    DateRangeTest, tests/test_log_participation_tab.py, pour la
    couverture de ce filtrage côté base) — ici on vérifie seulement le
    câblage UI (validation, transmission, non-régression champs vides)."""

    def _make_tournament(self, filename, date, name):
        db = database.Database(os.path.join(self.tournaments_dir, filename))
        db.set_settings({"tournament_date": date, "tournament_name": name})
        db.add_player("Pierre", "CPC")
        db.close()

    def _set_mode_and_player(self, player="Pierre"):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set(player)

    def test_du_exclut_les_participations_anterieures(self):
        self._make_tournament("A.tournoi", "2026-01-15", "Tournoi Janvier")
        self._make_tournament("B.tournoi", "2026-06-15", "Tournoi Juin")
        self._set_mode_and_player()
        self.win.log_participation_date_from_var.set("01/03/2026")
        self.win._start_log_participation_search()
        self._wait_for_search()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Tournoi Juin")

    def test_au_exclut_les_participations_posterieures(self):
        self._make_tournament("A.tournoi", "2026-01-15", "Tournoi Janvier")
        self._make_tournament("B.tournoi", "2026-06-15", "Tournoi Juin")
        self._set_mode_and_player()
        self.win.log_participation_date_to_var.set("01/03/2026")
        self.win._start_log_participation_search()
        self._wait_for_search()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Tournoi Janvier")

    def test_du_et_au_combines(self):
        self._make_tournament("A.tournoi", "2026-01-01", "Janvier")
        self._make_tournament("B.tournoi", "2026-03-15", "Mars")
        self._make_tournament("C.tournoi", "2026-06-01", "Juin")
        self._set_mode_and_player()
        self.win.log_participation_date_from_var.set("01/02/2026")
        self.win.log_participation_date_to_var.set("01/05/2026")
        self.win._start_log_participation_search()
        self._wait_for_search()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Mars")

    def test_champs_vides_najoute_aucune_borne_comme_avant(self):
        """Non-régression : Du/Au laissés vides (comportement d'origine
        avant cet ajout) ne filtrent toujours rien."""
        self._make_tournament("A.tournoi", "2026-01-01", "Janvier")
        self._set_mode_and_player()
        self.win._start_log_participation_search()
        self._wait_for_search()
        self.assertEqual(len(self._rows()), 1)

    def test_date_invalide_refuse_la_recherche_sans_lancer_de_thread(self):
        self._set_mode_and_player()
        self.win.log_participation_date_from_var.set("31/13/2026")  # mois 13 inexistant
        with patch.object(main.messagebox, "showerror") as mock_err:
            self.win._start_log_participation_search()
        mock_err.assert_called_once()
        self.assertFalse(self.win._log_participation_scan_in_progress)

    def test_du_superieur_a_au_refuse_la_recherche(self):
        self._set_mode_and_player()
        self.win.log_participation_date_from_var.set("01/06/2026")
        self.win.log_participation_date_to_var.set("01/01/2026")
        with patch.object(main.messagebox, "showerror") as mock_err:
            self.win._start_log_participation_search()
        mock_err.assert_called_once()
        self.assertFalse(self.win._log_participation_scan_in_progress)


class TodayButtonTest(LogParticipationTestCase):
    """Bouton "Aujourd'hui" (demande du 2026-10-17) dans le mode
    Participation — même calendrier partagé que le mode normal
    (App._show_log_date_picker, show_today_button=True par défaut,
    voir tests/test_log_purge.py::LogDatePickerTest pour la couverture
    équivalente côté Journal des actions), jamais une copie."""

    def _open_picker(self, target_var):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win._show_log_date_picker(target_var, self.win.log_participation_search_btn)
        return next(w for w in self.win.winfo_children() if isinstance(w, tk.Toplevel))

    def test_bouton_present_pour_du(self):
        picker = self._open_picker(self.win.log_participation_date_from_var)
        self.assertIsNotNone(_find_button(picker, "Aujourd'hui"))
        picker.destroy()

    def test_bouton_present_pour_au(self):
        picker = self._open_picker(self.win.log_participation_date_to_var)
        self.assertIsNotNone(_find_button(picker, "Aujourd'hui"))
        picker.destroy()

    def test_bouton_remplit_du_au_format_jj_mm_aaaa_et_ferme_le_popup(self):
        picker = self._open_picker(self.win.log_participation_date_from_var)
        _find_button(picker, "Aujourd'hui").invoke()
        self.assertEqual(
            self.win.log_participation_date_from_var.get(), datetime.now().strftime("%d/%m/%Y"),
        )
        self.assertEqual([w for w in self.win.winfo_children() if isinstance(w, tk.Toplevel)], [])

    def test_bouton_remplit_au(self):
        picker = self._open_picker(self.win.log_participation_date_to_var)
        _find_button(picker, "Aujourd'hui").invoke()
        self.assertEqual(
            self.win.log_participation_date_to_var.get(), datetime.now().strftime("%d/%m/%Y"),
        )

    def test_selection_normale_dun_jour_toujours_possible(self):
        self.win.log_participation_date_from_var.set("")
        picker = self._open_picker(self.win.log_participation_date_from_var)
        btn = _find_button(picker, "15")
        self.assertIsNotNone(btn)
        btn.invoke()
        value = self.win.log_participation_date_from_var.get()
        datetime.strptime(value, "%d/%m/%Y")
        self.assertTrue(value.startswith("15/"))

    def test_le_filtrage_reste_declenche_par_rechercher_jamais_par_aujourdhui(self):
        """Exigence 4 explicite : "Aujourd'hui" ne fait que renseigner le
        champ, jamais lancer lui-même une recherche."""
        picker = self._open_picker(self.win.log_participation_date_from_var)
        with patch.object(main.data_purge, "scan_folder_for_tournaments") as mock_scan:
            _find_button(picker, "Aujourd'hui").invoke()
        mock_scan.assert_not_called()


class TournamentFilterTest(LogParticipationTestCase):
    """"Tournoi" (demande du 2026-10-17 : filtrage INSTANTANÉ, jamais une
    nouvelle lecture disque pour ce seul filtre, contrairement à Du/Au
    ci-dessus ; demande du 2026-10-18 : 3 valeurs FIXES "Tous"/"Tournoi"/
    "Sit & Go", filtrant sur le TYPE réellement identifié — voir
    database._tournament_type_label, réglage "is_sng" en priorité sur le
    nom de fichier — remplaçant l'ancien filtre par nom de tournoi
    trouvé)."""

    def _make_tournament(self, filename, name, is_sng=None):
        db = database.Database(os.path.join(self.tournaments_dir, filename))
        settings = {"tournament_name": name}
        if is_sng is not None:
            settings["is_sng"] = is_sng
        db.set_settings(settings)
        db.add_player("Pierre", "CPC")
        db.close()

    def _search(self, folder=None, player="Pierre"):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(folder or self.tournaments_dir)
        self.win.log_participation_player_var.set(player)
        self.win._start_log_participation_search()
        self._wait_for_search()

    def test_valeurs_fixes_des_la_construction_avant_toute_recherche(self):
        self.assertEqual(
            tuple(self.win.log_participation_tournament_combo["values"]),
            ("Tous", "Tournoi", "Sit & Go"),
        )

    def test_valeurs_ne_changent_jamais_apres_une_recherche(self):
        """Contrairement à l'ancien comportement (liste reconstruite
        depuis les tournois trouvés), ces 3 valeurs restent EXACTEMENT
        les mêmes après une recherche, quels que soient les tournois
        réellement trouvés."""
        self._make_tournament("To1.tournoi", "Tournoi Alpha")
        self._search()
        self.assertEqual(
            tuple(self.win.log_participation_tournament_combo["values"]),
            ("Tous", "Tournoi", "Sit & Go"),
        )

    def test_filtre_tournoi_ne_garde_que_les_tournois_classiques(self):
        self._make_tournament("To1.tournoi", "Tournoi Alpha")
        self._make_tournament("Sn1.tournoi", "Sit & Go Beta")
        self._search()
        self.win.log_participation_tournament_var.set("Tournoi")
        self.win._apply_log_participation_tournament_filter()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Tournoi Alpha")
        self.assertEqual(rows[0][2], "Tournoi")

    def test_filtre_sit_and_go_ne_garde_que_les_sit_and_go(self):
        self._make_tournament("To1.tournoi", "Tournoi Alpha")
        self._make_tournament("Sn1.tournoi", "Sit & Go Beta")
        self._search()
        self.win.log_participation_tournament_var.set("Sit & Go")
        self.win._apply_log_participation_tournament_filter()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Sit & Go Beta")
        self.assertEqual(rows[0][2], "Sit & Go")

    def test_filtre_sit_and_go_reconnait_is_sng_meme_sans_prefixe_sn(self):
        """Cœur de la demande du 2026-10-18 : un Sit & Go dont le fichier
        ne suit pas la convention "Sn" doit quand même apparaître sous
        "Sit & Go", grâce au réglage "is_sng" en base."""
        self._make_tournament("MaSoiree.tournoi", "Soirée spéciale", is_sng=1)
        self._search()
        self.win.log_participation_tournament_var.set("Sit & Go")
        self.win._apply_log_participation_tournament_filter()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Soirée spéciale")

    def test_ligne_inconnu_najamais_affichee_sous_tournoi_ou_sit_and_go(self):
        self._make_tournament("hors_convention.tournoi", "Mystère")
        self._search()
        self.assertEqual(len(self._rows()), 1)  # visible sous "Tous"

        self.win.log_participation_tournament_var.set("Tournoi")
        self.win._apply_log_participation_tournament_filter()
        self.assertEqual(self._rows(), [])

        self.win.log_participation_tournament_var.set("Sit & Go")
        self.win._apply_log_participation_tournament_filter()
        self.assertEqual(self._rows(), [])

    def test_selection_filtre_instantanement_sans_nouveau_balayage(self):
        self._make_tournament("To1.tournoi", "Tournoi Alpha")
        self._make_tournament("Sn1.tournoi", "Sit & Go Beta")
        self._search()

        with patch.object(data_purge, "scan_folder_for_tournaments") as mock_scan:
            self.win.log_participation_tournament_var.set("Tournoi")
            self.win._apply_log_participation_tournament_filter()
        mock_scan.assert_not_called()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Tournoi Alpha")

    def test_tous_reaffiche_lensemble_apres_un_filtre(self):
        self._make_tournament("To1.tournoi", "Tournoi Alpha")
        self._make_tournament("Sn1.tournoi", "Sit & Go Beta")
        self._search()
        self.win.log_participation_tournament_var.set("Tournoi")
        self.win._apply_log_participation_tournament_filter()
        self.win.log_participation_tournament_var.set("Tous")
        self.win._apply_log_participation_tournament_filter()
        self.assertEqual(len(self._rows()), 2)

    def test_selection_persiste_et_sapplique_a_une_nouvelle_recherche(self):
        """Plus de réinitialisation automatique à "Tous" (demande du
        2026-10-18, valeurs désormais fixes, toujours valides) : la
        sélection "Sit & Go" reste active et filtre déjà le résultat
        d'une recherche ultérieure, sans action supplémentaire."""
        self._make_tournament("To1.tournoi", "Tournoi Alpha")
        self._search()
        self.win.log_participation_tournament_var.set("Sit & Go")
        self.win._apply_log_participation_tournament_filter()
        self.assertEqual(self._rows(), [])

        other_dir = tempfile.mkdtemp(prefix="log_participation_other_", dir=self._tmp.name)
        other_db = database.Database(os.path.join(other_dir, "Sn1.tournoi"))
        other_db.set_settings({"tournament_name": "Sit & Go Gamma"})
        other_db.add_player("Pierre", "CPC")
        other_db.close()
        self._search(folder=other_dir)

        self.assertEqual(self.win.log_participation_tournament_var.get(), "Sit & Go")
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "Sit & Go Gamma")


class ParticipationFiltersExportIntegrationTest(LogParticipationTestCase):
    """Vérification de bout en bout demandée explicitement ("Vérifier
    leur fonctionnement combiné") : une recherche filtrée par Du/Au puis
    par Tournoi (type, depuis le 2026-10-18), exportée, ne contient QUE
    les lignes réellement affichées à l'écran — jamais l'ensemble
    complet du dernier résultat de recherche."""

    def test_export_ne_contient_que_les_lignes_filtrees_par_type(self):
        for filename, date, name in (
            ("To1.tournoi", "2026-01-15", "Tournoi Alpha"),
            ("Sn1.tournoi", "2026-01-20", "Sit & Go Beta"),
        ):
            db = database.Database(os.path.join(self.tournaments_dir, filename))
            db.set_settings({"tournament_date": date, "tournament_name": name})
            db.add_player("Pierre", "CPC")
            db.close()

        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("Pierre")
        self.win._start_log_participation_search()
        self._wait_for_search()
        self.assertEqual(len(self._rows()), 2)

        self.win.log_participation_tournament_var.set("Sit & Go")
        self.win._apply_log_participation_tournament_filter()

        with patch.object(main, "LogParticipationExportDialog") as mock_dialog:
            self.win._on_log_participation_export()
        mock_dialog.assert_called_once()
        rows = mock_dialog.call_args[0][1]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tournament"], "Sit & Go Beta")
        self.assertEqual(rows[0]["type"], "Sit & Go")


class ReturnButtonTest(LogParticipationTestCase):
    """Bouton "Retour" (demande du 2026-10-17) : même mécanisme qu'un
    choix "Toutes" dans "Fonction", n'efface aucun champ dans aucun des
    deux modes."""

    def test_appelle_le_meme_mecanisme_quun_choix_toutes(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win._return_to_normal_log_view()
        self.assertEqual(self.win.log_function_var.get(), "Toutes")
        self.assertEqual(self.win._log_normal_criteria_frame.winfo_manager(), "pack")
        self.assertEqual(self.win._log_participation_frame.winfo_manager(), "")

    def test_ne_vide_aucun_champ_du_mode_participation(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win.log_participation_folder_var.set(self.tournaments_dir)
        self.win.log_participation_player_var.set("Pierre")
        self.win.log_participation_date_from_var.set("01/01/2026")
        self.win.log_participation_date_to_var.set("31/12/2026")
        self.win._return_to_normal_log_view()
        self.assertEqual(self.win.log_participation_folder_var.get(), self.tournaments_dir)
        self.assertEqual(self.win.log_participation_player_var.get(), "Pierre")
        self.assertEqual(self.win.log_participation_date_from_var.get(), "01/01/2026")
        self.assertEqual(self.win.log_participation_date_to_var.get(), "31/12/2026")

    def test_ne_vide_aucun_champ_du_mode_normal_apres_un_aller_retour(self):
        self.win.log_date_from_var.set("01/01/2026")
        self.win.log_date_to_var.set("31/12/2026")
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.win._return_to_normal_log_view()
        self.assertEqual(self.win.log_date_from_var.get(), "01/01/2026")
        self.assertEqual(self.win.log_date_to_var.get(), "31/12/2026")

    def test_bouton_visible_et_place_en_premier_dans_le_mode_participation(self):
        self.win.log_function_var.set(main.LOG_PARTICIPATION_LABEL)
        self.win._apply_log_view_mode()
        self.assertEqual(self.win.log_participation_back_btn.cget("text"), "◀ Retour au Journal des actions")
        criteria = self.win.log_participation_back_btn.master.master
        self.assertIs(criteria.winfo_children()[0], self.win.log_participation_back_btn.master)


if __name__ == "__main__":
    unittest.main()
