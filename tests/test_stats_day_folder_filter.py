# -*- coding: utf-8 -*-
"""Tests ciblés du filtre "jours" de l'onglet Statistiques (demande du
2026-09-18, 7 cases à cocher Lundi...Dimanche à droite du titre
"Tournois de la période") :

- le jour d'un tournoi est déterminé UNIQUEMENT par le nom de son
  sous-dossier réel sur disque (jamais par la date enregistrée dans le
  fichier .tournoi) — voir Database._tournament_day_matches ;
- 7/7 cochés (défaut) = BYPASS TOTAL, comportement historique
  strictement inchangé, quel que soit l'emplacement du fichier (racine,
  Lundi...Dimanche, dossier legacy/inconnu, sous-dossier quelconque) ;
- sélection partielle = ne retient que les fichiers dont AU MOINS UN
  segment du chemin relatif correspond à un jour sélectionné
  (Vendredi/sous_dossier/x.tournoi appartient à "Vendredi") ; un fichier
  à la racine ou dans un dossier sans nom de jour est alors exclu ;
- aucun jour coché = 0 résultat ;
- "Inclure les sous-dossiers" décoché : le filtre jours est SANS EFFET
  (jamais recalculé/ignoré côté UI — c'est build_period_summary qui le
  rend inopérant), fichiers de la racine conservés normalement ;
- se combine avec Type de tournois et la période, sans modifier
  find_tournament_files (partagée par 5 autres appelants).

Deux niveaux de test :
- Database._tournament_day_matches / build_period_summary : aucun Tk.
- PeriodSummaryDialog : même harnais minimal (racine Tk nue, aucun
  dialogue modal) que tests/test_period_summary_stats_tab.py."""
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


def _new_db(path, **settings):
    """Fichier .tournoi synthétique à `path` (chemin complet, sous-
    dossiers créés au besoin — jamais le cas pour find_tournament_files/
    Database elle-même, qui n'en créent aucun)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = database.Database(path)
    if settings:
        db.set_settings({k: str(v) for k, v in settings.items()})
    return db


class TournamentDayMatchesUnitTest(unittest.TestCase):
    """Database._tournament_day_matches seule : aucun Tk, aucune
    Database — vérifie exclusivement la logique de correspondance de
    chemin."""

    def test_selected_days_none_bypass_total(self):
        self.assertTrue(database._tournament_day_matches("/a/Vendredi/x.tournoi", "/a", None))
        self.assertTrue(database._tournament_day_matches("/a/x.tournoi", "/a", None))
        self.assertTrue(database._tournament_day_matches("/a/inconnu/x.tournoi", "/a", None))

    def test_sept_jours_coches_bypass_total(self):
        tous = database.STATS_WEEKDAY_FOLDER_NAMES
        self.assertTrue(database._tournament_day_matches("/a/Vendredi/x.tournoi", "/a", tous))
        self.assertTrue(database._tournament_day_matches("/a/x.tournoi", "/a", tous))
        self.assertTrue(database._tournament_day_matches("/a/inconnu/x.tournoi", "/a", tous))
        self.assertTrue(database._tournament_day_matches("/a/Vendredi/sous/x.tournoi", "/a", tous))

    def test_vendredi_seul(self):
        self.assertTrue(database._tournament_day_matches("/a/Vendredi/x.tournoi", "/a", ["Vendredi"]))
        self.assertFalse(database._tournament_day_matches("/a/Dimanche/x.tournoi", "/a", ["Vendredi"]))

    def test_dimanche_seul(self):
        self.assertTrue(database._tournament_day_matches("/a/Dimanche/x.tournoi", "/a", ["Dimanche"]))
        self.assertFalse(database._tournament_day_matches("/a/Vendredi/x.tournoi", "/a", ["Dimanche"]))

    def test_vendredi_et_dimanche(self):
        jours = ["Vendredi", "Dimanche"]
        self.assertTrue(database._tournament_day_matches("/a/Vendredi/x.tournoi", "/a", jours))
        self.assertTrue(database._tournament_day_matches("/a/Dimanche/x.tournoi", "/a", jours))
        self.assertFalse(database._tournament_day_matches("/a/Lundi/x.tournoi", "/a", jours))

    def test_racine_exclue_avec_selection_partielle(self):
        self.assertFalse(database._tournament_day_matches("/a/x.tournoi", "/a", ["Vendredi"]))

    def test_dossier_legacy_exclu_avec_selection_partielle(self):
        self.assertFalse(database._tournament_day_matches("/a/archive/x.tournoi", "/a", ["Vendredi"]))

    def test_sous_dossier_de_vendredi_reconnu_comme_vendredi(self):
        self.assertTrue(
            database._tournament_day_matches("/a/Vendredi/sous_dossier/x.tournoi", "/a", ["Vendredi"])
        )

    def test_aucun_jour_coche_exclut_tout(self):
        self.assertFalse(database._tournament_day_matches("/a/Vendredi/x.tournoi", "/a", []))
        self.assertFalse(database._tournament_day_matches("/a/x.tournoi", "/a", []))

    def test_insensible_a_la_casse(self):
        self.assertTrue(database._tournament_day_matches("/a/VENDREDI/x.tournoi", "/a", ["vendredi"]))


class BuildPeriodSummaryDayFilterTest(unittest.TestCase):
    """build_period_summary avec selected_days : vrais fichiers .tournoi
    sur disque (dossier temporaire), jamais ~/.poker_tournament."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_day_filter_test_")
        self.addCleanup(self._tmp.cleanup)
        self.root = self._tmp.name

    def _names(self, summary):
        return sorted(t["name"] for t in summary["tournaments"])

    def test_sept_jours_coches_identique_a_aucun_filtre(self):
        _new_db(os.path.join(self.root, "racine.tournoi"), tournament_name="Racine", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-02").conn.close()
        _new_db(os.path.join(self.root, "Dimanche", "d.tournoi"), tournament_name="D", tournament_date="2026-01-03").conn.close()
        _new_db(os.path.join(self.root, "archive", "a.tournoi"), tournament_name="A", tournament_date="2026-01-04").conn.close()

        sans_filtre = database.build_period_summary(self.root, recursive=True, selected_days=None)
        sept_jours = database.build_period_summary(
            self.root, recursive=True, selected_days=database.STATS_WEEKDAY_FOLDER_NAMES,
        )
        self.assertEqual(self._names(sans_filtre), self._names(sept_jours))
        self.assertEqual(self._names(sept_jours), ["A", "D", "Racine", "V"])

    def test_vendredi_seul(self):
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Dimanche", "d.tournoi"), tournament_name="D", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(self.root, recursive=True, selected_days=["Vendredi"])
        self.assertEqual(self._names(summary), ["V"])

    def test_dimanche_seul(self):
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Dimanche", "d.tournoi"), tournament_name="D", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(self.root, recursive=True, selected_days=["Dimanche"])
        self.assertEqual(self._names(summary), ["D"])

    def test_vendredi_et_dimanche(self):
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Lundi", "l.tournoi"), tournament_name="L", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Dimanche", "d.tournoi"), tournament_name="D", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(
            self.root, recursive=True, selected_days=["Vendredi", "Dimanche"],
        )
        self.assertEqual(self._names(summary), ["D", "V"])

    def test_aucun_jour_coche_avec_recursive_true_donne_zero(self):
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(self.root, recursive=True, selected_days=[])
        self.assertEqual(summary["tournaments"], [])

    def test_racine_incluse_avec_sept_jours(self):
        _new_db(os.path.join(self.root, "racine.tournoi"), tournament_name="Racine", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(
            self.root, recursive=True, selected_days=database.STATS_WEEKDAY_FOLDER_NAMES,
        )
        self.assertEqual(self._names(summary), ["Racine"])

    def test_racine_exclue_avec_selection_partielle(self):
        _new_db(os.path.join(self.root, "racine.tournoi"), tournament_name="Racine", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(self.root, recursive=True, selected_days=["Vendredi"])
        self.assertEqual(self._names(summary), ["V"])

    def test_dossier_legacy_inclus_avec_sept_jours(self):
        _new_db(os.path.join(self.root, "archive", "a.tournoi"), tournament_name="A", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(
            self.root, recursive=True, selected_days=database.STATS_WEEKDAY_FOLDER_NAMES,
        )
        self.assertEqual(self._names(summary), ["A"])

    def test_dossier_legacy_exclu_avec_selection_partielle(self):
        _new_db(os.path.join(self.root, "archive", "a.tournoi"), tournament_name="A", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(self.root, recursive=True, selected_days=["Vendredi"])
        self.assertEqual(self._names(summary), ["V"])

    def test_vendredi_sous_dossier_reconnu_comme_vendredi(self):
        _new_db(
            os.path.join(self.root, "Vendredi", "sous_dossier", "v.tournoi"),
            tournament_name="V", tournament_date="2026-01-01",
        ).conn.close()
        summary = database.build_period_summary(self.root, recursive=True, selected_days=["Vendredi"])
        self.assertEqual(self._names(summary), ["V"])

    def test_combinaison_avec_type_tournois(self):
        _new_db(os.path.join(self.root, "Vendredi", "To1.tournoi"), tournament_name="Tournoi", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "Sn1.tournoi"), tournament_name="SNG", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(
            self.root, recursive=True, selected_days=["Vendredi"],
            tournament_type=database.STATS_TOURNAMENT_TYPE_TOURNOIS,
        )
        self.assertEqual(self._names(summary), ["Tournoi"])

    def test_combinaison_avec_type_sitngo(self):
        _new_db(os.path.join(self.root, "Vendredi", "To1.tournoi"), tournament_name="Tournoi", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "Sn1.tournoi"), tournament_name="SNG", tournament_date="2026-01-01").conn.close()
        summary = database.build_period_summary(
            self.root, recursive=True, selected_days=["Vendredi"],
            tournament_type=database.STATS_TOURNAMENT_TYPE_SITNGO,
        )
        self.assertEqual(self._names(summary), ["SNG"])

    def test_combinaison_avec_periode(self):
        _new_db(os.path.join(self.root, "Vendredi", "ancien.tournoi"), tournament_name="Ancien", tournament_date="2025-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "recent.tournoi"), tournament_name="Recent", tournament_date="2026-06-01").conn.close()
        summary = database.build_period_summary(
            self.root, recursive=True, selected_days=["Vendredi"],
            date_from="2026-01-01", date_to="2026-12-31",
        )
        self.assertEqual(self._names(summary), ["Recent"])

    def test_recursive_false_ignore_le_filtre_jours(self):
        """Le point corrigé : recursive=False rend le filtre jours SANS
        EFFET, quel que soit selected_days — les fichiers de la racine
        restent traités exactement comme avant (comportement historique),
        jamais réduits à 0 par une sélection partielle mémorisée."""
        _new_db(os.path.join(self.root, "racine.tournoi"), tournament_name="Racine", tournament_date="2026-01-01").conn.close()
        _new_db(os.path.join(self.root, "Vendredi", "v.tournoi"), tournament_name="V", tournament_date="2026-01-01").conn.close()

        for selected_days in (["Vendredi"], [], database.STATS_WEEKDAY_FOLDER_NAMES, None):
            with self.subTest(selected_days=selected_days):
                summary = database.build_period_summary(
                    self.root, recursive=False, selected_days=selected_days,
                )
                # Non récursif : seul le fichier de la racine est de toute
                # façon trouvé par find_tournament_files (Vendredi/v.tournoi
                # n'est pas dans le dossier lui-même) — le filtre jours ne
                # doit RIEN retirer de plus.
                self.assertEqual(self._names(summary), ["Racine"])

    def test_aucune_ecriture_dans_les_tournoi(self):
        path = os.path.join(self.root, "Vendredi", "v.tournoi")
        _new_db(path, tournament_name="V", tournament_date="2026-01-01").conn.close()
        before = os.path.getmtime(path)
        database.build_period_summary(self.root, recursive=True, selected_days=["Vendredi"])
        database.build_period_summary(self.root, recursive=True, selected_days=[])
        database.build_period_summary(self.root, recursive=True, selected_days=database.STATS_WEEKDAY_FOLDER_NAMES)
        self.assertEqual(os.path.getmtime(path), before)


class _StubApp:
    db = None


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PeriodSummaryDialogDayCheckboxesUiTest(unittest.TestCase):
    """PeriodSummaryDialog réel (widgets Tk réels, jamais de dialogue
    modal — même précaution qu'ailleurs dans ce chantier, voir le SIGSEGV
    diagnostiqué avec un wait_window imbriqué : ce Frame n'en construit
    aucun)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : force gc.collect() sur le thread principal.
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_day_ui_")
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
        # cleanup_tk (voir tests/_tk_cleanup.py) : recursive_var.trace_add
        # (main.py) ferme un cycle sur le dialogue lui-même.
        self.addCleanup(lambda: cleanup_tk(self, "dialog"))
        # Aucune borne de période par défaut : évite toute interférence
        # avec les dates synthétiques choisies ci-dessous (même précaution
        # que le reste du chantier Statistiques).
        self.dialog.date_from_var.set("")
        self.dialog.date_to_var.set("")

    def test_sept_cases_presentes_et_cochees_par_defaut(self):
        self.assertEqual(len(self.dialog._stats_day_checkbuttons), 7)
        for day in database.STATS_WEEKDAY_FOLDER_NAMES:
            self.assertIn(day, self.dialog.stats_day_vars)
            self.assertTrue(self.dialog.stats_day_vars[day].get())

    def test_decocher_sous_dossiers_grise_les_sept_cases(self):
        self.dialog.recursive_var.set(False)
        for cb in self.dialog._stats_day_checkbuttons:
            self.assertIn("disabled", cb.state())

    def test_recocher_sous_dossiers_regrise_les_sept_cases_actives(self):
        self.dialog.recursive_var.set(False)
        self.dialog.recursive_var.set(True)
        for cb in self.dialog._stats_day_checkbuttons:
            self.assertNotIn("disabled", cb.state())

    def test_selection_partielle_conservee_pendant_sous_dossiers_decoche(self):
        self.dialog.stats_day_vars["Vendredi"].set(False)
        self.dialog.stats_day_vars["Lundi"].set(False)
        self.dialog.recursive_var.set(False)
        # Grisées, mais la VALEUR ne doit jamais être modifiée par le
        # grisage lui-même.
        self.assertFalse(self.dialog.stats_day_vars["Vendredi"].get())
        self.assertFalse(self.dialog.stats_day_vars["Lundi"].get())
        self.assertTrue(self.dialog.stats_day_vars["Mardi"].get())

    def test_selection_retrouvee_a_la_reactivation(self):
        self.dialog.stats_day_vars["Vendredi"].set(False)
        self.dialog.stats_day_vars["Lundi"].set(False)
        self.dialog.recursive_var.set(False)
        self.dialog.recursive_var.set(True)
        self.assertFalse(self.dialog.stats_day_vars["Vendredi"].get())
        self.assertFalse(self.dialog.stats_day_vars["Lundi"].get())
        self.assertTrue(self.dialog.stats_day_vars["Mardi"].get())

    def test_generate_transmet_la_selection_de_jours(self):
        _new_db(
            os.path.join(self._tmp.name, "Vendredi", "v.tournoi"),
            tournament_name="V", tournament_date="2026-01-01",
        ).conn.close()
        _new_db(
            os.path.join(self._tmp.name, "Dimanche", "d.tournoi"),
            tournament_name="D", tournament_date="2026-01-01",
        ).conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        for day, var in self.dialog.stats_day_vars.items():
            var.set(day == "Vendredi")
        self.dialog._generate()

        names = sorted(t["name"] for t in self.dialog.summary["tournaments"])
        self.assertEqual(names, ["V"])

    def test_generate_avec_sous_dossiers_decoche_ignore_la_selection_de_jours(self):
        _new_db(
            os.path.join(self._tmp.name, "racine.tournoi"),
            tournament_name="Racine", tournament_date="2026-01-01",
        ).conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog.recursive_var.set(False)
        # Sélection partielle mémorisée (grisée) : ne doit PAS réduire à 0
        # le résultat une fois "Inclure les sous-dossiers" décoché.
        for day, var in self.dialog.stats_day_vars.items():
            var.set(day == "Vendredi")
        self.dialog._generate()

        names = sorted(t["name"] for t in self.dialog.summary["tournaments"])
        self.assertEqual(names, ["Racine"])

    def test_classement_des_joueurs_et_total_coherents_avec_le_filtre_jours(self):
        db_v = _new_db(
            os.path.join(self._tmp.name, "Vendredi", "v.tournoi"),
            tournament_name="V", tournament_date="2026-01-01",
        )
        db_v.add_player("Alice")
        db_v.conn.close()
        db_d = _new_db(
            os.path.join(self._tmp.name, "Dimanche", "d.tournoi"),
            tournament_name="D", tournament_date="2026-01-01",
        )
        db_d.add_player("Bob")
        db_d.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        for day, var in self.dialog.stats_day_vars.items():
            var.set(day == "Vendredi")
        self.dialog._generate()

        player_names = sorted(p["name"] for p in self.dialog.summary["players"])
        self.assertEqual(player_names, ["Alice"])
        # Ligne TOTAL du Treeview (toujours en première ligne, voir le
        # mécanisme de tri déjà validé du chantier Statistiques).
        rows = self.dialog.players_tree.get_children()
        self.assertTrue(rows)
        total_values = self.dialog.players_tree.item(rows[0], "values")
        # index 2 : Club(0), Rang(1), Joueur(2) — colonne "rang" ajoutée
        # le 2026-09-18 (3e ajustement).
        self.assertEqual(total_values[2], "TOTAL")


if __name__ == "__main__":
    unittest.main()
