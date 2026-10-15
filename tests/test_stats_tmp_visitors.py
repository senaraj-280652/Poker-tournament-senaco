# -*- coding: utf-8 -*-
"""Statistiques de FRÉQUENTATION des visiteurs CLUB TMP dans l'onglet
Statistiques (demande du 2026-10-15, distincte des statistiques
OFFICIELLES déjà validées — jamais mélangée avec elles) :

1. Le filtre "Club" de Statistiques propose "TMP" uniquement s'il existe
   au moins un visiteur TMP à comptabiliser sur la période (Database.
   build_period_summary: "is_tmp"/"tmp_tournaments_played", main.py:
   PeriodSummaryDialog._sync_stats_tmp_club_option/_tmp_visitor_rows).
2. TMP sélectionné : "Tournois des joueurs" affiche le nombre RÉEL de
   participations de chaque visiteur TMP (tmp_tournaments_played),
   JAMAIS le compteur officiel tournaments_played (qui les exclut
   structurellement).
3. "{N} joueur(s) distinct(s)" (info_lbl, déjà existant) donne alors
   gratuitement le nombre de visiteurs TMP distincts.
4. Un même visiteur sur plusieurs tournois reste une seule ligne, avec
   le nombre réel cumulé de ses participations.
5. Un visiteur TMP affiche "Hors classement" (jamais un rang officiel),
   même convention que l'onglet Classement.
6. Un ancien TMP redevenu membre régulier du club (plus de club "TMP"
   dans le Répertoire actuel, voir main._is_tmp_visitor_still_relevant)
   n'est plus compté comme visiteur TMP — SANS jamais recalculer ni
   modifier les anciens tournois (voir RetroactivityNonRegressionTest).
7. Les forfaits (status='withdrawn') ne comptent jamais comme des
   participations réelles, ni pour les TMP ni pour les réguliers.
8. Aucune règle déjà validée (points/classement/primes/bounties, champ
   GROUPE) n'est modifiée ; le filtre Club de l'onglet Classement
   (classement_club_listbox) n'est jamais touché par ce correctif.

Même conventions déjà établies dans tests/test_stats_player_rank.py :
vraie Database SQLite en fichier temporaire, vrai PeriodSummaryDialog
(Tk réel), roster.py/export_prefs.py redirigés vers des fichiers
temporaires via patch.object."""
import ast
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
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


# =======================================================================
# 1. Database.build_period_summary — champs additifs "is_tmp"/
#    "tmp_tournaments_played"
# =======================================================================
class BuildPeriodSummaryTmpVisitorFieldsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_tmp_visitors_")
        self.addCleanup(self._tmp.cleanup)

    def _db(self, filename):
        return database.Database(os.path.join(self._tmp.name, filename))

    def test_aucun_tmp_comportement_strictement_inchange(self):
        db = self._db("t.tournoi")
        a = db.add_player("Pierre", "CPC")
        b = db.add_player("Jacques", "CPC")
        db.eliminate_player(b, eliminated_by_id=a)
        db.close()
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        by_name = {p["name"]: p for p in summary["players"]}
        self.assertFalse(by_name["Pierre"]["is_tmp"])
        self.assertFalse(by_name["Jacques"]["is_tmp"])
        self.assertEqual(by_name["Pierre"]["tmp_tournaments_played"], 0)
        self.assertEqual(by_name["Jacques"]["tmp_tournaments_played"], 0)

    def test_tmp_sur_plusieurs_tournois_compte_chaque_participation_reelle(self):
        db1 = self._db("t1.tournoi")
        pierre1 = db1.add_player("Pierre", "CPC")
        v1 = db1.add_player("Visiteur", "TMP")
        db1.eliminate_player(v1, eliminated_by_id=pierre1)
        db1.close()

        db2 = self._db("t2.tournoi")
        pierre2 = db2.add_player("Pierre", "CPC")
        v2 = db2.add_player("Visiteur", "TMP")
        db2.eliminate_player(v2, eliminated_by_id=pierre2)
        db2.close()

        summary = database.build_period_summary(self._tmp.name, recursive=False)
        visiteur = next(p for p in summary["players"] if p["name"] == "Visiteur")
        self.assertTrue(visiteur["is_tmp"])
        self.assertEqual(visiteur["tmp_tournaments_played"], 2)
        # Jamais compté dans le compteur OFFICIEL (déjà validé) :
        self.assertEqual(visiteur["tournaments_played"], 0)

    def test_forfait_tmp_najamais_une_participation_reelle(self):
        db1 = self._db("t1.tournoi")
        pierre1 = db1.add_player("Pierre", "CPC")
        v1 = db1.add_player("Visiteur", "TMP")
        db1.eliminate_player(v1, eliminated_by_id=pierre1)
        db1.close()

        db2 = self._db("t2.tournoi")
        db2.add_player("Pierre", "CPC")
        v2 = db2.add_player("Visiteur", "TMP")
        db2.withdraw_player(v2)
        db2.close()

        summary = database.build_period_summary(self._tmp.name, recursive=False)
        visiteur = next(p for p in summary["players"] if p["name"] == "Visiteur")
        # 2 fichiers, mais UNE SEULE participation RÉELLE (le forfait du
        # second ne compte pas) — même principe déjà validé pour "played".
        self.assertEqual(visiteur["tmp_tournaments_played"], 1)
        self.assertTrue(visiteur["is_tmp"])

    def test_tmp_najamais_de_points_ni_de_classement_officiel(self):
        """Non-régression : les nouveaux champs n'affectent aucune des
        règles déjà validées (0 point, jamais de victoire/meilleur rang)."""
        db = self._db("t.tournoi")
        pierre = db.add_player("Pierre", "CPC")
        visiteur = db.add_player("Visiteur", "TMP")
        db.eliminate_player(visiteur, eliminated_by_id=pierre)
        db.close()
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        v = next(p for p in summary["players"] if p["name"] == "Visiteur")
        self.assertEqual(v["wins"], 0)
        self.assertIsNone(v["best_place"])
        self.assertEqual(v["total_points"], 0)
        self.assertEqual(v["total_presence_assiduity"], 0)
        self.assertEqual(v["total_ranking_points"], 0)
        self.assertEqual(v["total_bounty_won"], 0)


# =======================================================================
# 2. main.py — fonctions pures (aucun Tk)
# =======================================================================
class IsTmpVisitorStillRelevantTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_tmp_roster_")
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(
            roster, "_roster_path",
            return_value=os.path.join(self._tmp.name, "roster.json"),
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_jamais_inscrit_au_repertoire_reste_tmp(self):
        self.assertTrue(main._is_tmp_visitor_still_relevant("Inconnu"))

    def test_inscrit_sans_club_reste_tmp(self):
        roster.add_to_roster("Visiteur", club=None)
        self.assertTrue(main._is_tmp_visitor_still_relevant("Visiteur"))

    def test_inscrit_avec_club_tmp_reste_tmp(self):
        roster.add_to_roster("Visiteur", club="TMP")
        self.assertTrue(main._is_tmp_visitor_still_relevant("Visiteur"))
        roster.set_club("Visiteur2", "  tmp  ")
        self.assertTrue(main._is_tmp_visitor_still_relevant("Visiteur2"))

    def test_devenu_membre_dun_club_reel_najamais_tmp(self):
        roster.add_to_roster("Visiteur", club="CPC")
        self.assertFalse(main._is_tmp_visitor_still_relevant("Visiteur"))


class TmpVisitorRowsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_tmp_rows_")
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(
            roster, "_roster_path",
            return_value=os.path.join(self._tmp.name, "roster.json"),
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_filtre_sur_is_tmp_et_pertinence_actuelle(self):
        roster.add_to_roster("DevenuMembre", club="CPC")
        players = [
            {"name": "Pierre", "is_tmp": False},
            {"name": "Visiteur", "is_tmp": True},
            {"name": "DevenuMembre", "is_tmp": True},
        ]
        rows = main._tmp_visitor_rows(players)
        self.assertEqual([r["name"] for r in rows], ["Visiteur"])


# =======================================================================
# 3. UI réelle (PeriodSummaryDialog, widgets Tk réels)
# =======================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class StatsTmpClubFilterUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_tmp_ui_")
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
        self.addCleanup(lambda: cleanup_tk(self, "dialog"))
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog.date_from_var.set("")
        self.dialog.date_to_var.set("")

    def _db(self, filename):
        return database.Database(os.path.join(self._tmp.name, filename))

    def _rows(self, tree):
        return [tree.item(iid, "values") for iid in tree.get_children()]

    def _select_club(self, *labels):
        listbox = self.dialog.stats_club_listbox
        items = list(listbox.get(0, "end"))
        listbox.selection_clear(0, "end")
        for label in labels:
            listbox.selection_set(items.index(label))

    def test_tmp_absent_du_filtre_sans_visiteur(self):
        db = self._db("t.tournoi")
        a = db.add_player("Pierre", "CPC")
        b = db.add_player("Jacques", "CPC")
        db.eliminate_player(b, eliminated_by_id=a)
        db.close()
        self.dialog._generate()
        self.assertNotIn("TMP", list(self.dialog.stats_club_listbox.get(0, "end")))

    def test_tmp_present_dans_le_filtre_avec_un_visiteur(self):
        db = self._db("t.tournoi")
        a = db.add_player("Pierre", "CPC")
        v = db.add_player("Visiteur", "TMP")
        db.eliminate_player(v, eliminated_by_id=a)
        db.close()
        self.dialog._generate()
        self.assertIn("TMP", list(self.dialog.stats_club_listbox.get(0, "end")))

    def test_selection_tmp_affiche_le_nombre_reel_de_participations(self):
        db1 = self._db("t1.tournoi")
        p1 = db1.add_player("Pierre", "CPC")
        v1 = db1.add_player("Visiteur", "TMP")
        db1.eliminate_player(v1, eliminated_by_id=p1)
        db1.close()
        db2 = self._db("t2.tournoi")
        p2 = db2.add_player("Pierre", "CPC")
        v2 = db2.add_player("Visiteur", "TMP")
        db2.eliminate_player(v2, eliminated_by_id=p2)
        db2.close()

        self.dialog._generate()
        self._select_club("TMP")
        self.dialog._refresh_display()

        rows = {r[2]: r for r in self._rows(self.dialog.players_tree)[1:]}
        self.assertEqual(rows["Visiteur"][0], "TMP")  # colonne Club
        self.assertEqual(rows["Visiteur"][3], "2")  # colonne "Tournois joués" : réel, jamais 0
        self.assertEqual(rows["Visiteur"][1], "Hors classement")  # colonne Rang

    def test_nombre_de_visiteurs_distincts_via_info_lbl(self):
        db = self._db("t.tournoi")
        p = db.add_player("Pierre", "CPC")
        v1 = db.add_player("Visiteur1", "TMP")
        v2 = db.add_player("Visiteur2", "TMP")
        db.eliminate_player(v1, eliminated_by_id=p)
        db.eliminate_player(v2, eliminated_by_id=p)
        db.close()

        self.dialog._generate()
        self._select_club("TMP")
        self.dialog._refresh_display()
        self.assertIn("2 joueur(s) distinct(s)", self.dialog.info_lbl.cget("text"))

    def test_meme_visiteur_sur_plusieurs_tournois_compte_une_seule_fois(self):
        db1 = self._db("t1.tournoi")
        p1 = db1.add_player("Pierre", "CPC")
        v1 = db1.add_player("Visiteur", "TMP")
        db1.eliminate_player(v1, eliminated_by_id=p1)
        db1.close()
        db2 = self._db("t2.tournoi")
        p2 = db2.add_player("Pierre", "CPC")
        v2 = db2.add_player("Visiteur", "TMP")
        db2.eliminate_player(v2, eliminated_by_id=p2)
        db2.close()

        self.dialog._generate()
        self._select_club("TMP")
        self.dialog._refresh_display()
        rows = self._rows(self.dialog.players_tree)[1:]  # saute TOTAL
        self.assertEqual(len(rows), 1)
        self.assertIn("1 joueur(s) distinct(s)", self.dialog.info_lbl.cget("text"))

    def test_zero_point_pour_un_visiteur_tmp_affiche(self):
        db = self._db("t.tournoi")
        p = db.add_player("Pierre", "CPC")
        v = db.add_player("Visiteur", "TMP")
        db.eliminate_player(v, eliminated_by_id=p)
        db.close()
        self.dialog._generate()
        self._select_club("TMP")
        self.dialog._refresh_display()
        row = next(r for r in self._rows(self.dialog.players_tree)[1:] if r[2] == "Visiteur")
        # club, rang, name, played, pres/ass, rank_pts, bounty, total
        self.assertEqual(row[4], "0")
        self.assertEqual(row[5], "0")
        self.assertEqual(row[6], "-")
        self.assertEqual(row[7], "0")

    def test_ancien_tmp_devenu_membre_cpc_najamais_recompte(self):
        """Demande explicite du 2026-10-15 : un ancien visiteur TMP
        devenu membre régulier du club (club réel dans le Répertoire
        ACTUEL) disparaît des statistiques de fréquentation TMP — sans
        que son ancien tournoi TMP ne soit jamais modifié sur disque."""
        db = self._db("t.tournoi")
        p = db.add_player("Pierre", "CPC")
        v = db.add_player("Visiteur", "TMP")
        db.eliminate_player(v, eliminated_by_id=p)
        db.close()

        roster.add_to_roster("Visiteur", club="CPC")  # devenu membre depuis

        self.dialog._generate()
        # "TMP" ne doit même plus être proposé : plus aucun visiteur
        # pertinent (le seul ayant existé est maintenant un membre).
        self.assertNotIn("TMP", list(self.dialog.stats_club_listbox.get(0, "end")))
        # Historique JAMAIS modifié sur disque : le fichier .tournoi
        # garde bien "club" = "TMP" tel quel, intact.
        reread = database.Database(os.path.join(self._tmp.name, "t.tournoi"), read_only=True)
        visiteur_row = next(pl for pl in reread.list_players() if pl["name"] == "Visiteur")
        self.assertEqual(visiteur_row["club"], "TMP")
        reread.close()
        # Et le FAIT historique agrégé (is_tmp) reste vrai en interne,
        # même s'il n'est plus affiché comme visiteur à comptabiliser.
        self.assertTrue(
            any(pl["is_tmp"] for pl in self.dialog.summary["players"] if pl["name"] == "Visiteur")
        )

    def test_tri_rang_avec_tmp_et_club_reel_combines_ne_plante_pas(self):
        db = self._db("t.tournoi")
        p = db.add_player("Pierre", "CPC")
        j = db.add_player("Jacques", "CPC")
        v = db.add_player("Visiteur", "TMP")
        db.eliminate_player(j, eliminated_by_id=p)
        db.eliminate_player(v, eliminated_by_id=p)
        db.close()
        # "CPC" doit apparaître dans la liste du filtre (nourrie par
        # roster.list_clubs()/le club réglé dans Paramètres, voir
        # _populate_club_filter_listbox) — reconstruit le dialogue APRÈS
        # avoir réglé ce club, même principe que les tests existants
        # (voir tests/test_stats_player_rank.py::test_filtre_club_
        # recalcule_les_rangs).
        export_prefs.save_value("club_name", "CPC")
        self.dialog.destroy()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog.date_from_var.set("")
        self.dialog.date_to_var.set("")

        self.dialog._generate()
        self._select_club("CPC", "TMP")
        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "rang")
        rows_asc = self._rows(self.dialog.players_tree)[1:]
        self.assertEqual(rows_asc[-1][2], "Visiteur")  # TMP toujours en dernier

        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "rang")  # inverse
        rows_desc = self._rows(self.dialog.players_tree)[1:]
        self.assertEqual(rows_desc[-1][2], "Visiteur")  # toujours en dernier, même inversé

    def test_export_reflete_le_meme_comptage_que_lecran(self):
        db = self._db("t.tournoi")
        p = db.add_player("Pierre", "CPC")
        v = db.add_player("Visiteur", "TMP")
        db.eliminate_player(v, eliminated_by_id=p)
        db.close()
        self.dialog._generate()
        self._select_club("TMP")
        export_summary = {
            "tournaments": self.dialog.summary["tournaments"],
            "players": main._apply_tmp_hors_classement(
                main._stats_players_with_rank(
                    self.dialog._club_filtered_players(self.dialog.summary["players"])
                )
            ),
        }
        visiteur = next(p for p in export_summary["players"] if p["name"] == "Visiteur")
        self.assertEqual(visiteur["tournaments_played"], 1)
        self.assertEqual(visiteur["rang"], "Hors classement")


class _StubApp:
    db = None


# =======================================================================
# 4. Non-régression de périmètre (structurel)
# =======================================================================
class ScopeBoundaryStructuralTest(unittest.TestCase):
    """Vérifie que le mécanisme TMP de l'onglet Statistiques ne touche
    JAMAIS le filtre Club de l'onglet Classement, ni les règles déjà
    validées de points/classement/primes/bounties/GROUPE."""

    def setUp(self):
        self.source = _main_source()
        self.tree = ast.parse(self.source, filename="main.py")

    def _find_function_or_method(self, name):
        return next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef) and n.name == name
        )

    def _source_of(self, node):
        return ast.get_source_segment(self.source, node) or ""

    def _body_without_docstring(self, func):
        body_stmts = func.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        return "\n".join(self._source_of(s) for s in body_stmts)

    def test_sync_stats_tmp_club_option_ne_touche_jamais_classement(self):
        func = self._find_function_or_method("_sync_stats_tmp_club_option")
        code_only = self._body_without_docstring(func)
        self.assertNotIn("classement_club_listbox", code_only)
        self.assertIn("stats_club_listbox", code_only)

    def test_club_filtered_players_ne_touche_jamais_classement(self):
        func = self._find_function_or_method("_club_filtered_players")
        seg = self._source_of(func)
        self.assertNotIn("classement_club_listbox", seg)

    def test_populate_club_filter_listbox_jamais_modifiee_pour_tmp(self):
        """Le widget/la fonction PARTAGÉS avec le Classement ne doivent
        jamais référencer "TMP" ni les visiteurs TMP — toute la logique
        vit exclusivement dans les fonctions dédiées à Statistiques."""
        for name in ("_populate_club_filter_listbox", "build_club_filter_widget"):
            func = self._find_function_or_method(name)
            seg = self._source_of(func)
            self.assertNotIn("TMP", seg)
            self.assertNotIn("_tmp_visitor_rows", seg)


if __name__ == "__main__":
    unittest.main()
