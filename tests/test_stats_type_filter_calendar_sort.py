# -*- coding: utf-8 -*-
"""Amélioration de l'écran Statistiques (demande du 2026-09-17/18),
trois volets, tous validés avant codage (voir l'analyse et son
complément livrés séparément) :

1. "Type de tournois" (Tournois/SitnGo/Tous, à droite de "Inclure les
   sous-dossiers") — filtre LOCAL à Database.build_period_summary
   (jamais find_tournament_files elle-même, partagée par 5 autres
   appelants sans rapport), sur la convention de nommage établie par
   main.py: tournament_day_folder_proposal ("To..." / "Sn..."). OPTION A
   validée : "Tous" garde TOUS les fichiers, y compris ceux hors
   convention (ex. l'ancien repli "tournoi.tournoi") — comportement
   strictement identique à avant l'existence de ce filtre.
2. Calendrier pour les deux dates de période (main.py: pick_date_dialog/
   _build_calendar_grid/_shift_month) — bibliothèque standard `calendar`
   uniquement, aucune dépendance externe. Saisie manuelle des champs
   conservée intégralement ; "Effacer" vide explicitement le champ
   (préserve "laisser vide = pas de borne"), "Annuler"/fermeture ne
   change rien.
3. Tri par en-tête cliquable des deux tableaux ("Tournois de la
   période" / "Classement des joueurs") — mécanisme FACTORISÉ entre les
   deux (_sorted_rows/_sort_stats_players/_apply_stats_sort_arrows),
   jamais les 4 mécanismes de tri déjà existants ailleurs (roster/
   players/primes/classement, hors périmètre). Règles validées :
   - la ligne TOTAL reste TOUJOURS en première ligne, jamais triée ;
   - le dernier tri choisi survit à Générer/période/Type/Club/tout
     rafraîchissement (état posé sur l'instance PeriodSummaryDialog) ;
   - "Meilleur Rang" : best_place=None ("-") reste TOUJOURS en dernière
     position, dans les DEUX sens (absence de classement, pas un rang
     à inverser) ;
   - tri purement VISUEL : jamais de mutation de self.summary, jamais
     d'écriture dans un fichier .tournoi.

Conventions reprises de tests/test_period_summary_stats_tab.py : fichiers
.tournoi synthétiques en dossier temporaire, roster.py/export_prefs.py
toujours patchés vers un fichier temporaire, un seul tk.Tk() par classe
UI, PeriodSummaryDialog RÉEL construit dessus (ttk.Frame, jamais de
grab_set/wait_window propre à ce widget — sûr à instancier tel quel)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, filename, **settings):
    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    if settings:
        db.set_settings({k: str(v) for k, v in settings.items()})
    return db


class _StubApp:
    """Comme tests/test_period_summary_stats_tab.py:_StubApp — seul
    attribut lu par PeriodSummaryDialog.__init__ : `db` (None ici, dossier
    par défaut vide, sans incidence sur ces tests)."""
    db = None


# =====================================================================
# 1. Filtre "Type de tournois" — niveau Database (aucun Tk nécessaire)
# =====================================================================
class TournamentTypeFilterTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_type_filter_")
        self.addCleanup(self._tmp.cleanup)

    def test_tournois_garde_uniquement_les_fichiers_to(self):
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        _new_db(self._tmp.name, "tournoi.tournoi", tournament_date="2026-09-03").conn.close()

        summary = database.build_period_summary(
            self._tmp.name, recursive=False,
            tournament_type=database.STATS_TOURNAMENT_TYPE_TOURNOIS,
        )
        names = {t["date"] for t in summary["tournaments"]}
        self.assertEqual(names, {"2026-09-01"})

    def test_sitngo_garde_uniquement_les_fichiers_sn(self):
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        _new_db(self._tmp.name, "tournoi.tournoi", tournament_date="2026-09-03").conn.close()

        summary = database.build_period_summary(
            self._tmp.name, recursive=False,
            tournament_type=database.STATS_TOURNAMENT_TYPE_SITNGO,
        )
        dates = {t["date"] for t in summary["tournaments"]}
        self.assertEqual(dates, {"2026-09-02"})

    def test_tous_garde_tout_y_compris_hors_convention(self):
        """OPTION A validée : "Tous" ne doit JAMAIS faire disparaître un
        fichier qui apparaissait déjà dans Statistiques avant ce filtre —
        y compris "tournoi.tournoi" (repli historique), ou tout autre nom
        ne commençant ni par To ni par Sn."""
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        _new_db(self._tmp.name, "tournoi.tournoi", tournament_date="2026-09-03").conn.close()
        _new_db(self._tmp.name, "ancien_fichier_renomme.tournoi", tournament_date="2026-09-04").conn.close()

        summary_default = database.build_period_summary(self._tmp.name, recursive=False)
        summary_explicit = database.build_period_summary(
            self._tmp.name, recursive=False,
            tournament_type=database.STATS_TOURNAMENT_TYPE_ALL,
        )
        dates_default = {t["date"] for t in summary_default["tournaments"]}
        dates_explicit = {t["date"] for t in summary_explicit["tournaments"]}
        expected = {"2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04"}
        self.assertEqual(dates_default, expected)  # comportement par défaut (paramètre omis) inchangé
        self.assertEqual(dates_explicit, expected)

    def test_combinaison_type_et_periode(self):
        """Exemple exact de la demande : Type=SitnGo + une période donnée
        -> uniquement les Sn... compris dans cette période, un seul
        passage sur les fichiers (jamais deux parcours distincts)."""
        _new_db(self._tmp.name, "Sn010926.tournoi", tournament_date="2026-09-01").conn.close()  # avant la période
        _new_db(self._tmp.name, "Sn150926.tournoi", tournament_date="2026-09-15").conn.close()  # dans la période
        _new_db(self._tmp.name, "Sn300926.tournoi", tournament_date="2026-09-30").conn.close()  # après la période
        _new_db(self._tmp.name, "To160926.tournoi", tournament_date="2026-09-16").conn.close()  # dans la période, mais To

        summary = database.build_period_summary(
            self._tmp.name, recursive=False,
            date_from="2026-09-05", date_to="2026-09-16",
            tournament_type=database.STATS_TOURNAMENT_TYPE_SITNGO,
        )
        dates = {t["date"] for t in summary["tournaments"]}
        self.assertEqual(dates, {"2026-09-15"})

    def test_type_absent_equivaut_a_tous_non_regression(self):
        """Appel sans `tournament_type` du tout (ancien code appelant,
        avant cette demande) : comportement strictement identique à
        avant — aucune régression pour un éventuel autre appelant."""
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        self.assertEqual(len(summary["tournaments"]), 2)


# =====================================================================
# 2. Calendrier — fonctions pures (aucun Tk)
# =====================================================================
class ShiftMonthTest(unittest.TestCase):
    def test_avance_un_mois_normal(self):
        self.assertEqual(main._shift_month(2026, 5, 1), (2026, 6))

    def test_recule_un_mois_normal(self):
        self.assertEqual(main._shift_month(2026, 5, -1), (2026, 4))

    def test_franchit_l_annee_suivante(self):
        self.assertEqual(main._shift_month(2026, 12, 1), (2027, 1))

    def test_recule_franchit_l_annee_precedente(self):
        self.assertEqual(main._shift_month(2026, 1, -1), (2025, 12))

    def test_plusieurs_mois_a_la_fois(self):
        self.assertEqual(main._shift_month(2026, 11, 3), (2027, 2))


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class CalendarGridTest(unittest.TestCase):
    """_build_calendar_grid — VRAIS widgets Tk (Label/Button dans un
    Frame ordinaire), jamais de fenêtre modale (aucun grab_set/
    wait_window ici, voir la docstring du module) : sûr, pas de risque
    de segfault documenté ailleurs dans cette suite."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.frame = ttk.Frame(self.root)
        self.addCleanup(self.frame.destroy)

    def _day_button(self, day):
        for w in self.frame.winfo_children():
            if isinstance(w, ttk.Button) and w.cget("text") == str(day):
                return w
        return None

    def test_un_bouton_par_jour_du_mois(self):
        main._build_calendar_grid(self.frame, 2026, 9, lambda d: None)
        # Septembre 2026 compte 30 jours.
        for day in range(1, 31):
            self.assertIsNotNone(self._day_button(day), f"jour {day} manquant")

    def test_clic_sur_un_jour_appelle_on_pick_avec_liso_correct(self):
        picked = []
        main._build_calendar_grid(self.frame, 2026, 9, picked.append)
        btn = self._day_button(17)
        btn.invoke()
        self.assertEqual(picked, ["2026-09-17"])

    def test_reconstruction_efface_lancienne_grille(self):
        main._build_calendar_grid(self.frame, 2026, 1, lambda d: None)  # janvier : 31 jours
        main._build_calendar_grid(self.frame, 2026, 2, lambda d: None)  # février 2026 : 28 jours
        self.assertIsNone(self._day_button(30))  # aucun jour 30 (février) : pas un résidu de janvier
        self.assertIsNotNone(self._day_button(28))


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PickDateDialogWiringTest(unittest.TestCase):
    """Câblage de pick_date_dialog vérifié par ANALYSE STATIQUE (source),
    jamais en ouvrant réellement la fenêtre modale (grab_set +
    wait_window) — même précaution que tests/test_ask_eliminator_window_
    position.py: AskEliminatorSourceWiringTest, pour le même risque de
    segfault Tcl/Tk déjà documenté dans ce harnais précis."""

    @classmethod
    def setUpClass(cls):
        import inspect
        cls.source = inspect.getsource(main.pick_date_dialog)

    def test_utilise_build_calendar_grid(self):
        self.assertIn("_build_calendar_grid(", self.source)

    def test_propose_aujourdhui_effacer_annuler(self):
        for marker in ("Aujourd'hui", "Effacer", "Annuler"):
            self.assertIn(marker, self.source)

    def test_fermeture_par_la_croix_equivaut_a_annuler(self):
        self.assertIn("WM_DELETE_WINDOW", self.source)
        self.assertIn("choose_cancel", self.source)

    def test_navigation_mois_precedent_suivant(self):
        self.assertIn("_shift_month(", self.source)


# =====================================================================
# 3. Tri — fonctions pures (aucun Tk)
# =====================================================================
class SortedRowsTest(unittest.TestCase):
    def test_colonne_none_renvoie_une_copie_inchangee(self):
        rows = [{"name": "b"}, {"name": "a"}]
        result = main._sorted_rows(rows, {"column": None, "ascending": True}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual(result, rows)
        self.assertIsNot(result, rows)  # copie, jamais la liste d'origine

    def test_ne_modifie_jamais_la_liste_dorigine(self):
        rows = [{"name": "b", "date": "2026-01-02", "winner": "X", "bounty_distributed": 1},
                {"name": "a", "date": "2026-01-01", "winner": "Y", "bounty_distributed": 2}]
        original_order = list(rows)
        main._sorted_rows(rows, {"column": "name", "ascending": True}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual(rows, original_order)

    def test_date_tri_chronologique_reel_pas_texte_affiche(self):
        """Dates choisies pour qu'un tri sur le texte FRANÇAIS affiché
        (JJ/MM/AAAA) donnerait un résultat FAUX : "01/12/2026" (1er
        décembre) est alphabétiquement AVANT "05/01/2026" (5 janvier),
        alors que le 5 janvier est chronologiquement BIEN avant le 1er
        décembre. Le tri doit porter sur la clé ISO brute t["date"]."""
        rows = [
            {"date": "2026-12-01", "name": "B"},
            {"date": "2026-01-05", "name": "A"},
        ]
        result = main._sorted_rows(rows, {"column": "date", "ascending": True}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual([r["date"] for r in result], ["2026-01-05", "2026-12-01"])

        result_desc = main._sorted_rows(rows, {"column": "date", "ascending": False}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual([r["date"] for r in result_desc], ["2026-12-01", "2026-01-05"])

    def test_tournoi_tri_alphabetique_insensible_a_la_casse(self):
        rows = [{"name": "bob"}, {"name": "Alice"}, {"name": "chris"}]
        result = main._sorted_rows(rows, {"column": "name", "ascending": True}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual([r["name"] for r in result], ["Alice", "bob", "chris"])

    def test_vainqueur_tri_alphabetique_insensible_a_la_casse_et_inversion(self):
        rows = [{"winner": "bob"}, {"winner": "Alice"}, {"winner": "-"}]
        asc = main._sorted_rows(rows, {"column": "winner", "ascending": True}, main.STATS_TOURNAMENTS_SORT_KEYS)
        desc = main._sorted_rows(rows, {"column": "winner", "ascending": False}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual([r["winner"] for r in asc], list(reversed([r["winner"] for r in desc])))

    def test_primes_tri_numerique_pas_alphabetique(self):
        rows = [{"bounty_distributed": 100}, {"bounty_distributed": 2}, {"bounty_distributed": 10}]
        result = main._sorted_rows(rows, {"column": "bounty", "ascending": True}, main.STATS_TOURNAMENTS_SORT_KEYS)
        self.assertEqual([r["bounty_distributed"] for r in result], [2, 10, 100])  # jamais [10, 100, 2]


class SortStatsPlayersTest(unittest.TestCase):
    def test_joueur_alphabetique_insensible_a_la_casse(self):
        players = [{"name": "bob"}, {"name": "Alice"}]
        result = main._sort_stats_players(players, {"column": "name", "ascending": True})
        self.assertEqual([p["name"] for p in result], ["Alice", "bob"])

    def test_tournois_joues_numerique(self):
        players = [{"tournaments_played": 10, "name": "A"}, {"tournaments_played": 2, "name": "B"}]
        result = main._sort_stats_players(players, {"column": "played", "ascending": True})
        self.assertEqual([p["tournaments_played"] for p in result], [2, 10])

    def test_pts_pres_ass_numerique(self):
        """"Pts Prés/Ass" (demande du 2026-09-18, 2e ajustement — remplace
        "Victoires" à l'écran) : toujours un entier simple (jamais None),
        tri numérique ordinaire."""
        players = [
            {"total_presence_assiduity": 10, "name": "A"},
            {"total_presence_assiduity": 2, "name": "B"},
        ]
        result = main._sort_stats_players(
            players, {"column": "total_presence_assiduity", "ascending": True}
        )
        self.assertEqual([p["total_presence_assiduity"] for p in result], [2, 10])

    def test_pts_pres_ass_inversion(self):
        players = [
            {"total_presence_assiduity": 2, "name": "A"},
            {"total_presence_assiduity": 10, "name": "B"},
        ]
        result = main._sort_stats_players(
            players, {"column": "total_presence_assiduity", "ascending": False}
        )
        self.assertEqual([p["total_presence_assiduity"] for p in result], [10, 2])

    def test_pts_gain_clsmt_numerique(self):
        """"Pts Gain Clsmt" (remplace "Meilleur Rang" à l'écran) :
        toujours un entier simple (jamais None, contrairement à
        best_place) — tri numérique ordinaire, aucun cas particulier."""
        players = [
            {"total_ranking_points": 100, "name": "A"},
            {"total_ranking_points": 2, "name": "B"},
        ]
        result = main._sort_stats_players(
            players, {"column": "total_ranking_points", "ascending": True}
        )
        self.assertEqual([p["total_ranking_points"] for p in result], [2, 100])

    def test_pts_gain_clsmt_inversion(self):
        players = [
            {"total_ranking_points": 2, "name": "A"},
            {"total_ranking_points": 100, "name": "B"},
        ]
        result = main._sort_stats_players(
            players, {"column": "total_ranking_points", "ascending": False}
        )
        self.assertEqual([p["total_ranking_points"] for p in result], [100, 2])

    def test_total_pts_numerique(self):
        players = [{"total_points": 100, "name": "A"}, {"total_points": 2, "name": "B"}]
        result = main._sort_stats_players(players, {"column": "total_points", "ascending": True})
        self.assertEqual([p["total_points"] for p in result], [2, 100])

    def test_wins_nest_plus_triable_via_ce_mecanisme(self):
        """Non-régression du 2e ajustement (2026-09-18) : "wins" n'est
        plus une colonne de CE Treeview (remplacée par "Pts Prés/Ass") —
        _sort_stats_players ne la reconnaît donc plus comme colonne
        triable et renvoie la liste inchangée (comportement générique de
        _sorted_rows pour toute colonne absente de STATS_PLAYERS_SORT_
        KEYS), jamais une erreur. `wins` reste par ailleurs intact et
        disponible à l'export (voir PrimesPointsColumnsTest)."""
        players = [{"wins": 10, "name": "A"}, {"wins": 2, "name": "B"}]
        result = main._sort_stats_players(players, {"column": "wins", "ascending": True})
        self.assertEqual([p["name"] for p in result], ["A", "B"])  # ordre inchangé

    def test_meilleur_rang_croissant_none_toujours_en_dernier(self):
        """"best" n'est plus câblée sur aucun en-tête de ce Treeview
        (remplacée par "Pts Gain Clsmt"), mais _sort_stats_players
        conserve ce traitement (voir sa docstring) — vérifié ici au
        niveau de la fonction pure, indépendamment de ce qui est
        réellement câblé à l'écran."""
        players = [
            {"name": "C", "best_place": 3}, {"name": "None1", "best_place": None},
            {"name": "A", "best_place": 1}, {"name": "B", "best_place": 2},
            {"name": "None2", "best_place": None},
        ]
        result = main._sort_stats_players(players, {"column": "best", "ascending": True})
        self.assertEqual([p["name"] for p in result[:3]], ["A", "B", "C"])
        self.assertEqual({p["name"] for p in result[3:]}, {"None1", "None2"})

    def test_meilleur_rang_decroissant_none_toujours_en_dernier(self):
        """Cas explicitement demandé : les None restent en DERNIER même
        en tri décroissant (jamais inversés vers le début, contrairement
        à toutes les autres colonnes)."""
        players = [
            {"name": "C", "best_place": 3}, {"name": "None1", "best_place": None},
            {"name": "A", "best_place": 1}, {"name": "B", "best_place": 2},
        ]
        result = main._sort_stats_players(players, {"column": "best", "ascending": False})
        self.assertEqual([p["name"] for p in result[:3]], ["C", "B", "A"])  # décroissant parmi les classés
        self.assertEqual(result[3]["name"], "None1")  # toujours en dernier, pas en premier

    def test_ne_modifie_jamais_la_liste_dorigine(self):
        players = [{"name": "b", "best_place": 2}, {"name": "a", "best_place": 1}]
        original = list(players)
        main._sort_stats_players(players, {"column": "best", "ascending": True})
        self.assertEqual(players, original)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ApplySortArrowsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.tree = ttk.Treeview(self.root, columns=("name", "date"), show="headings")
        self.addCleanup(self.tree.destroy)
        self.headers = {"name": "Tournoi", "date": "Date"}

    def test_colonne_active_porte_la_fleche_croissante(self):
        main._apply_stats_sort_arrows(self.tree, {"column": "name", "ascending": True}, self.headers)
        self.assertEqual(self.tree.heading("name", "text"), "Tournoi ▲")
        self.assertEqual(self.tree.heading("date", "text"), "Date")

    def test_colonne_active_porte_la_fleche_decroissante(self):
        main._apply_stats_sort_arrows(self.tree, {"column": "date", "ascending": False}, self.headers)
        self.assertEqual(self.tree.heading("date", "text"), "Date ▼")
        self.assertEqual(self.tree.heading("name", "text"), "Tournoi")

    def test_aucune_colonne_active_texte_nu_partout(self):
        main._apply_stats_sort_arrows(self.tree, {"column": None, "ascending": True}, self.headers)
        self.assertEqual(self.tree.heading("name", "text"), "Tournoi")
        self.assertEqual(self.tree.heading("date", "text"), "Date")


# =====================================================================
# 4. Intégration UI — VRAI PeriodSummaryDialog (voir tests/test_period_
#    summary_stats_tab.py: PeriodSummaryDialogUiTestCase, même principe)
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class StatsUiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_ui_type_sort_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(roster, "_roster_path", return_value=roster_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())
        self.addCleanup(self.dialog.destroy)
        self.dialog.folder_var.set(self._tmp.name)

    def _rows(self, tree, skip_total=True):
        children = list(tree.get_children())
        if skip_total:
            children = children[1:]
        return [tree.item(iid, "values") for iid in children]


class TypeFilterUiTest(StatsUiTestCase):
    def test_defaut_est_tous(self):
        self.assertEqual(
            self.dialog.tournament_type_var.get(),
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_ALL],
        )

    def test_generer_avec_tournois_ne_garde_que_to(self):
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        self.dialog.tournament_type_var.set(
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_TOURNOIS]
        )
        self.dialog._generate()
        rows = self._rows(self.dialog.tournaments_tree)
        self.assertEqual(len(rows), 1)

    def test_generer_avec_sitngo_ne_garde_que_sn(self):
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        self.dialog.tournament_type_var.set(
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_SITNGO]
        )
        self.dialog._generate()
        rows = self._rows(self.dialog.tournaments_tree)
        self.assertEqual(len(rows), 1)

    def test_tous_garde_les_deux_y_compris_hors_convention(self):
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        _new_db(self._tmp.name, "tournoi.tournoi", tournament_date="2026-09-03").conn.close()
        self.dialog._generate()  # "Tous" est déjà la valeur par défaut
        rows = self._rows(self.dialog.tournaments_tree)
        self.assertEqual(len(rows), 3)

    def test_export_reflete_le_filtre_type(self):
        """Cohérence export (demande explicite) : self.summary, transmis
        tel quel à PeriodExportDialog par _open_export_dialog, ne
        contient déjà QUE les tournois du type choisi — rien à changer
        côté export lui-même, la synthèse est filtrée en amont."""
        _new_db(self._tmp.name, "To010926.tournoi", tournament_date="2026-09-01").conn.close()
        _new_db(self._tmp.name, "Sn020926.tournoi", tournament_date="2026-09-02").conn.close()
        self.dialog.tournament_type_var.set(
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_SITNGO]
        )
        self.dialog._generate()
        self.assertEqual(len(self.dialog.summary["tournaments"]), 1)
        self.assertEqual(self.dialog.summary["tournaments"][0]["date"], "2026-09-02")


class PickPeriodDateTest(StatsUiTestCase):
    """_pick_period_date — pick_date_dialog est ESPIONNÉE (jamais
    réellement ouverte ici, voir PickDateDialogWiringTest plus haut)."""

    def test_date_choisie_est_reportee_dans_le_champ(self):
        with patch.object(main, "pick_date_dialog", return_value="2026-09-15") as mock_pick:
            self.dialog._pick_period_date(self.dialog.date_from_var)
        mock_pick.assert_called_once()
        self.assertEqual(self.dialog.date_from_var.get(), "2026-09-15")

    def test_annulation_ne_modifie_pas_le_champ(self):
        self.dialog.date_to_var.set("2026-01-01")
        with patch.object(main, "pick_date_dialog", return_value=None):
            self.dialog._pick_period_date(self.dialog.date_to_var)
        self.assertEqual(self.dialog.date_to_var.get(), "2026-01-01")

    def test_effacer_vide_le_champ(self):
        self.dialog.date_from_var.set("2026-01-01")
        with patch.object(main, "pick_date_dialog", return_value=""):
            self.dialog._pick_period_date(self.dialog.date_from_var)
        self.assertEqual(self.dialog.date_from_var.get(), "")


class TournamentsSortUiTest(StatsUiTestCase):
    def setUp(self):
        super().setUp()
        # Bornes de période élargies (par défaut : 1er janvier de
        # l'année courante -> aujourd'hui, voir PeriodSummaryDialog.
        # __init__) : sans ça, un tournoi daté en décembre (choisi
        # ci-dessous exprès pour déjouer un tri par TEXTE affiché, voir
        # plus bas) serait exclu par le filtre de PÉRIODE si "aujourd'hui"
        # tombe avant, sans aucun rapport avec ce qui est testé ici.
        self.dialog.date_from_var.set("")
        self.dialog.date_to_var.set("")
        # Dates choisies pour qu'un tri (erroné) sur le texte JJ/MM/AAAA
        # AFFICHÉ donnerait un résultat DIFFÉRENT du vrai ordre
        # chronologique ("01/12/2026" < "15/06/2026" < "20/01/2026"
        # alphabétiquement, alors que le 20 janvier est chronologiquement
        # le premier) — voir aussi SortedRowsTest.test_date_tri_
        # chronologique_reel_pas_texte_affiche (même principe, niveau
        # fonction pure).
        _new_db(self._tmp.name, "T1.tournoi", tournament_date="2026-12-01",
                tournament_name="Zephyr").conn.close()
        _new_db(self._tmp.name, "T2.tournoi", tournament_date="2026-01-20",
                tournament_name="alpha").conn.close()
        _new_db(self._tmp.name, "T3.tournoi", tournament_date="2026-06-15",
                tournament_name="Milieu").conn.close()
        self.dialog._generate()

    def test_clic_sur_date_trie_chronologique_puis_inverse(self):
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "date")
        names_asc = [r[1] for r in self._rows(self.dialog.tournaments_tree)]
        # Ordre chronologique réel : 20 janvier, puis 15 juin, puis
        # 1er décembre — PAS l'ordre alphabétique du texte affiché.
        self.assertEqual(names_asc, ["alpha", "Milieu", "Zephyr"])

        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "date")
        names_desc = [r[1] for r in self._rows(self.dialog.tournaments_tree)]
        self.assertEqual(names_desc, ["Zephyr", "Milieu", "alpha"])

    def test_clic_sur_tournoi_alphabetique_insensible_a_la_casse(self):
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")
        names = [r[1] for r in self._rows(self.dialog.tournaments_tree)]
        self.assertEqual(names, ["alpha", "Milieu", "Zephyr"])

    def test_total_reste_toujours_en_premiere_ligne(self):
        for col in ("date", "name", "winner", "bounty"):
            self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, col)
            first_row = self.dialog.tournaments_tree.item(
                self.dialog.tournaments_tree.get_children()[0], "values"
            )
            self.assertEqual(first_row[1], "TOTAL")

    def test_fleches_dans_les_entetes(self):
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")
        self.assertIn("▲", self.dialog.tournaments_tree.heading("name", "text"))
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")
        self.assertIn("▼", self.dialog.tournaments_tree.heading("name", "text"))

    def test_nouvelle_colonne_repart_dans_son_ordre_croissant(self):
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")  # -> décroissant
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "date")  # nouvelle colonne
        self.assertTrue(self.dialog.tournaments_sort["ascending"])

    def test_tri_survit_a_un_nouveau_generer(self):
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")
        self.dialog._generate()
        names = [r[1] for r in self._rows(self.dialog.tournaments_tree)]
        self.assertEqual(names, ["alpha", "Milieu", "Zephyr"])

    def test_tri_survit_au_changement_de_type(self):
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "name")
        self.dialog.tournament_type_var.set(
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_ALL]
        )
        self.dialog._generate()
        names = [r[1] for r in self._rows(self.dialog.tournaments_tree)]
        self.assertEqual(names, ["alpha", "Milieu", "Zephyr"])

    def test_aucune_ecriture_dans_les_fichiers_tournoi(self):
        before = {}
        for name in ("T1.tournoi", "T2.tournoi", "T3.tournoi"):
            db = database.Database(os.path.join(self._tmp.name, name), read_only=True)
            before[name] = db.get_setting("tournament_name")
            db.close()

        for col in ("date", "name", "winner", "bounty"):
            self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, col)
            self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, col)

        for name in ("T1.tournoi", "T2.tournoi", "T3.tournoi"):
            db = database.Database(os.path.join(self._tmp.name, name), read_only=True)
            self.assertEqual(db.get_setting("tournament_name"), before[name])
            db.close()


class PlayersSortUiTest(StatsUiTestCase):
    def setUp(self):
        super().setUp()
        # Tournoi TERMINÉ (3 joueurs, classement complet 1/2/3), présence
        # + classement réglés pour donner des valeurs distinctes à "Pts
        # Prés/Ass"/"Pts Gain Clsmt" (colonnes visibles depuis le 2e
        # ajustement du 2026-09-18, remplaçant "Victoires"/"Meilleur
        # Rang" à l'écran — wins/best_place restent calculés à
        # l'identique dans build_period_summary, voir PrimesPointsColumns
        # Test/PrizePoolColumnRemovedTest dans tests/test_period_summary_
        # stats_tab.py pour leur couverture dédiée, y compris à l'export).
        db1 = _new_db(
            self._tmp.name, "T1.tournoi", tournament_date="2026-09-01",
            attendance_bonus_points=5, ranking_formula="current",
        )
        a = db1.add_player("Alice")
        b = db1.add_player("Bob")
        c = db1.add_player("Chris")
        db1.eliminate_player(c)  # place=3
        db1.eliminate_player(b)  # place=2
        # Alice reste seule active -> vainqueur (place=1).
        db1.conn.close()
        # Tournoi ENCORE EN COURS (aucune élimination) : best_place=None
        # pour ses deux joueurs (donc total_ranking_points=0, jamais
        # classés) — la présence, elle, reste créditée (get_presence_
        # bonuses boucle sur tous les joueurs, sans condition de rang).
        db2 = _new_db(
            self._tmp.name, "T2.tournoi", tournament_date="2026-09-02",
            attendance_bonus_points=7,
        )
        db2.add_player("Dave")
        db2.add_player("Eve")
        db2.conn.close()
        self.dialog._generate()

    def test_colonnes_visibles_remplacees(self):
        """Non-régression du 2e ajustement (2026-09-18) : "Pts Prés/Ass"/
        "Pts Gain Clsmt" visibles à la place de "Victoires"/"Meilleur
        Rang" dans CE Treeview — jamais les deux en même temps."""
        headers = [
            self.dialog.players_tree.heading(c, "text")
            for c in self.dialog.players_tree["columns"]
        ]
        self.assertTrue(any(h.startswith("Pts Prés/Ass") for h in headers))
        self.assertTrue(any(h.startswith("Pts Gain Clsmt") for h in headers))
        self.assertFalse(any(h.startswith("Victoires") for h in headers))
        self.assertFalse(any(h.startswith("Meilleur Rang") for h in headers))

    def test_clic_sur_joueur_alphabetique(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "name")
        # index 2 : Club(0), Rang(1), Joueur(2) — colonne "rang" ajoutée
        # le 2026-09-18 (3e ajustement).
        names = [r[2] for r in self._rows(self.dialog.players_tree)]
        self.assertEqual(names, ["Alice", "Bob", "Chris", "Dave", "Eve"])

    def test_clic_sur_tournois_joues_numerique(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "played")
        played = [r[3] for r in self._rows(self.dialog.players_tree)]
        self.assertEqual(played, sorted(played, key=int))

    def _int_column(self, index):
        return [
            int(r[index].replace(" ", "")) for r in self._rows(self.dialog.players_tree)
        ]

    def test_clic_sur_pts_pres_ass_numerique_croissant(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_presence_assiduity")
        values = self._int_column(4)
        self.assertEqual(values, sorted(values))

    def test_clic_sur_pts_pres_ass_numerique_decroissant(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_presence_assiduity")
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_presence_assiduity")
        values = self._int_column(4)
        self.assertEqual(values, sorted(values, reverse=True))

    def test_clic_sur_pts_gain_clsmt_numerique_croissant(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_ranking_points")
        values = self._int_column(5)
        self.assertEqual(values, sorted(values))
        # Alice (1re, ranking le plus élevé) doit se retrouver en dernier
        # en tri croissant, Dave/Eve (jamais classés, 0) en tête.
        names = [r[2] for r in self._rows(self.dialog.players_tree)]
        self.assertEqual(names[-1], "Alice")

    def test_clic_sur_pts_gain_clsmt_numerique_decroissant(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_ranking_points")
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_ranking_points")
        values = self._int_column(5)
        self.assertEqual(values, sorted(values, reverse=True))
        names = [r[2] for r in self._rows(self.dialog.players_tree)]
        self.assertEqual(names[0], "Alice")  # ranking le plus élevé, en tête en décroissant

    def test_clic_sur_total_pts_numerique(self):
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_points")
        pts = [r[7] for r in self._rows(self.dialog.players_tree)]
        pts_int = [int(p.replace(" ", "")) for p in pts]
        self.assertEqual(pts_int, sorted(pts_int))

    def test_total_reste_toujours_en_premiere_ligne(self):
        for col in ("name", "played", "total_presence_assiduity", "total_ranking_points", "total_points"):
            self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, col)
            first_row = self.dialog.players_tree.item(
                self.dialog.players_tree.get_children()[0], "values"
            )
            # index 2 : Club(0), Rang(1), Joueur(2) — colonne "rang"
            # ajoutée le 2026-09-18 (3e ajustement) ; TOUJOURS vide sur
            # cette ligne (voir StatsPlayerRankUiTest).
            self.assertEqual(first_row[1], "")
            self.assertEqual(first_row[2], "TOTAL")

    def test_cumul_pts_pres_ass_et_gain_clsmt_dans_la_ligne_total(self):
        """Même principe que les cumuls Bounty/TOTAL Pts déjà en place :
        la ligne TOTAL affiche la somme, sur les joueurs actuellement
        affichés, de total_presence_assiduity/total_ranking_points."""
        total_row = self.dialog.players_tree.item(
            self.dialog.players_tree.get_children()[0], "values"
        )
        expected_presence = sum(
            p["total_presence_assiduity"] for p in self.dialog.summary["players"]
        )
        expected_ranking = sum(
            p["total_ranking_points"] for p in self.dialog.summary["players"]
        )
        self.assertEqual(total_row[2], "TOTAL")
        self.assertEqual(int(total_row[4].replace(" ", "")), expected_presence)
        self.assertEqual(int(total_row[5].replace(" ", "")), expected_ranking)

    def test_cumul_ligne_total_reflete_le_filtre_club(self):
        """Demande explicite : si un Club est sélectionné, la ligne TOTAL
        doit cumuler uniquement les joueurs effectivement AFFICHÉS après
        filtre Club — jamais le total de toute la synthèse."""
        roster.set_club("Alice", "Chemillé")
        roster.set_club("Bob", "Chemillé")
        roster.set_club("Chris", "Angers")
        roster.set_club("Dave", "Angers")
        roster.set_club("Eve", "Angers")
        self.dialog.destroy()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()

        listbox = self.dialog.stats_club_listbox
        items = list(listbox.get(0, "end"))
        listbox.selection_clear(0, "end")
        listbox.selection_set(items.index("Chemillé"))
        self.dialog._refresh_display()

        displayed_names = {r[2] for r in self._rows(self.dialog.players_tree)}
        self.assertEqual(displayed_names, {"Alice", "Bob"})

        expected_presence = sum(
            p["total_presence_assiduity"] for p in self.dialog.summary["players"]
            if p["name"] in ("Alice", "Bob")
        )
        expected_ranking = sum(
            p["total_ranking_points"] for p in self.dialog.summary["players"]
            if p["name"] in ("Alice", "Bob")
        )
        all_presence = sum(
            p["total_presence_assiduity"] for p in self.dialog.summary["players"]
        )
        self.assertNotEqual(expected_presence, all_presence)  # preuve que le filtre agit bien

        total_row = self.dialog.players_tree.item(
            self.dialog.players_tree.get_children()[0], "values"
        )
        self.assertEqual(int(total_row[4].replace(" ", "")), expected_presence)
        self.assertEqual(int(total_row[5].replace(" ", "")), expected_ranking)

    def test_tri_ne_mute_jamais_self_summary(self):
        """Le tri doit rester purement visuel : self.summary (la donnée
        source) ne doit jamais être réordonné ni modifié par un clic sur
        un en-tête."""
        players_before = list(self.dialog.summary["players"])  # copie, pour comparer le contenu
        original_list_ref = self.dialog.summary["players"]  # référence, pour vérifier qu'elle n'est jamais remplacée
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_presence_assiduity")
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "total_ranking_points")
        self.assertEqual(self.dialog.summary["players"], players_before)  # même contenu, même ordre
        self.assertIs(self.dialog.summary["players"], original_list_ref)  # jamais remplacée par une autre liste

    def test_tri_survit_au_changement_du_filtre_club(self):
        roster.set_club("Alice", "Chemillé")
        roster.set_club("Bob", "Angers")
        self.dialog.destroy()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())
        self.dialog.folder_var.set(self._tmp.name)
        # Reconstruit les mêmes fichiers pour ce nouveau dialogue (le
        # dossier temporaire, lui, est conservé par setUp).
        self.dialog._generate()
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "name")

        listbox = self.dialog.stats_club_listbox
        items = list(listbox.get(0, "end"))
        listbox.selection_clear(0, "end")
        listbox.selection_set(items.index("Chemillé"))
        self.dialog._refresh_display()

        names = [r[2] for r in self._rows(self.dialog.players_tree)]
        self.assertEqual(names, ["Alice"])  # filtre Club appliqué
        self.assertEqual(self.dialog.stats_players_sort["column"], "name")  # tri conservé

    def test_interaction_type_periode_et_tri_combines(self):
        """Exemple bout-en-bout : en plus des tournois de setUp (T1/T2,
        noms hors convention -> exclus dès que Type != Tous), ajoute un
        Sn hors période, un To dans la période, et un Sn dans la
        période — filtre Type=SitnGo + période resserrée, puis trie :
        seul le Sn de la période doit apparaître (ni T1/T2 hors
        convention, ni le To, ni le Sn hors période)."""
        _new_db(self._tmp.name, "Sn200926.tournoi", tournament_date="2026-09-20").conn.close()
        _new_db(self._tmp.name, "To250926.tournoi", tournament_date="2026-09-25").conn.close()  # To : exclu
        _new_db(self._tmp.name, "Sn991231.tournoi", tournament_date="2099-12-31").conn.close()  # hors période

        self.dialog.tournament_type_var.set(
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_SITNGO]
        )
        self.dialog.date_from_var.set("2026-01-01")
        self.dialog.date_to_var.set("2026-12-31")
        self.dialog._generate()
        self.dialog._on_stats_sort_click(self.dialog.tournaments_sort, "date")

        dates_in_summary = {t["date"] for t in self.dialog.summary["tournaments"]}
        self.assertEqual(dates_in_summary, {"2026-09-20"})


if __name__ == "__main__":
    unittest.main()
