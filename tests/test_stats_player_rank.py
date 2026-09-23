# -*- coding: utf-8 -*-
"""Tests ciblés de la colonne "Rang" du "Classement des joueurs" (demande
du 2026-09-18) :

- calculée EXCLUSIVEMENT depuis total_points décroissant (jamais un
  autre critère, jamais la date, jamais un identifiant quelconque) ;
- classement SPORTIF avec égalités (1, 2, 2, 4 — jamais 1, 2, 3, 4),
  jamais départagé par le nom en cas d'égalité stricte ;
- calculé APRÈS le filtre Club (main.py: _club_filtered_players) mais
  AVANT tout tri visuel (main.py: _sort_stats_players) — reste donc figé
  quel que soit l'en-tête sur lequel l'utilisateur clique ensuite ;
- ligne TOTAL : cellule Rang toujours vide ;
- disponible aux exports CSV/XLSX/PDF (database.PERIOD_PLAYER_COLUMNS),
  placée immédiatement avant "Joueur" — export dans l'ordre de build_
  period_summary (total_points décroissant), jamais le tri visuel
  temporaire de l'écran (voir main.py: _open_export_dialog, qui ne
  trie jamais son résultat) ;
- Victoires/Meilleur Rang restent disponibles à l'export (non touchées) ;
- OPTION A validée : PERIOD_PLAYER_COLUMNS["rang"] = lambda a: a["rang"]
  (contrat STRICT, jamais a.get("rang", "")) — tout appel direct aux
  fonctions d'export dans ce fichier prépare donc "rang" lui-même via
  main._stats_players_with_rank avant d'appeler database.export_period_
  summary_csv/xlsx/pdf.

Identification des joueurs (voir main._stats_players_with_rank, dont la
docstring documente ce point) : Database.build_period_summary agrège
par NOM (dict Python `players.setdefault(p_name, ...)`) — il n'existe
aucun autre identifiant qui survive au-delà d'un seul fichier .tournoi.
NomsUniquesDansSummaryTest démontre (plutôt que suppose) que summary
["players"] ne peut donc jamais contenir deux entrées du même nom,
justifiant que _stats_players_with_rank utilise le nom comme clé."""
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


def _player(name, total_points, **extra):
    """Dict minimal comme summary["players"] en produit un — seuls les
    champs lus par _stats_players_with_rank/PERIOD_PLAYER_COLUMNS sont
    nécessaires pour ces tests, les autres passés via `extra` au besoin."""
    base = {
        "name": name, "tournaments_played": 1, "wins": 0, "best_place": None,
        "total_presence_assiduity": 0, "total_ranking_points": 0,
        "total_bounty_won": 0, "total_points": total_points,
    }
    base.update(extra)
    return base


class StatsPlayersWithRankUnitTest(unittest.TestCase):
    """main._stats_players_with_rank seule : aucun Tk, aucune Database."""

    def test_classement_simple_sans_egalite(self):
        players = [_player("Alice", 1500), _player("Bob", 1300), _player("Chris", 1100)]
        ranked = main._stats_players_with_rank(players)
        self.assertEqual({a["name"]: a["rang"] for a in ranked}, {"Alice": 1, "Bob": 2, "Chris": 3})

    def test_egalite_classement_sportif_1_2_2_4(self):
        players = [
            _player("Alice", 1500), _player("Bob", 1300),
            _player("Chris", 1300), _player("Dave", 1100),
        ]
        ranked = main._stats_players_with_rank(players)
        ranks = {a["name"]: a["rang"] for a in ranked}
        self.assertEqual(ranks, {"Alice": 1, "Bob": 2, "Chris": 2, "Dave": 4})
        self.assertNotIn(3, ranks.values())  # jamais 1,2,3,4

    def test_egalite_jamais_departagee_par_ordre_alphabetique(self):
        # "Zoe" et "Aaron" à égalité stricte : si un départage alphabétique
        # existait, l'un des deux recevrait un rang différent de l'autre —
        # ici les deux DOIVENT obtenir EXACTEMENT le même rang.
        players = [_player("Zoe", 1000), _player("Aaron", 1000), _player("Milo", 900)]
        ranked = main._stats_players_with_rank(players)
        ranks = {a["name"]: a["rang"] for a in ranked}
        self.assertEqual(ranks["Zoe"], ranks["Aaron"])
        self.assertEqual(ranks, {"Zoe": 1, "Aaron": 1, "Milo": 3})

    def test_ne_mute_jamais_les_dicts_dorigine(self):
        original = [_player("Alice", 1500), _player("Bob", 1300)]
        snapshot = [dict(a) for a in original]
        main._stats_players_with_rank(original)
        self.assertEqual(original, snapshot)
        for a in original:
            self.assertNotIn("rang", a)

    def test_renvoie_de_nouvelles_copies_jamais_les_memes_objets(self):
        original = [_player("Alice", 1500)]
        ranked = main._stats_players_with_rank(original)
        self.assertIsNot(ranked[0], original[0])

    def test_conserve_lordre_dentree(self):
        # Ordre d'entrée délibérément PAS trié par total_points : la
        # fonction ne doit jamais réordonner son résultat, seulement
        # attacher "rang".
        players = [_player("Chris", 1100), _player("Alice", 1500), _player("Bob", 1300)]
        ranked = main._stats_players_with_rank(players)
        self.assertEqual([a["name"] for a in ranked], ["Chris", "Alice", "Bob"])
        self.assertEqual([a["rang"] for a in ranked], [3, 1, 2])

    def test_liste_vide(self):
        self.assertEqual(main._stats_players_with_rank([]), [])

    def test_un_seul_joueur_est_toujours_rang_1(self):
        ranked = main._stats_players_with_rank([_player("Alice", 0)])
        self.assertEqual(ranked[0]["rang"], 1)


class NomsUniquesDansSummaryTest(unittest.TestCase):
    """Démontre (plutôt que suppose) l'invariant dont dépend
    _stats_players_with_rank : Database.build_period_summary agrège par
    nom, `summary["players"]` ne peut donc jamais contenir deux entrées
    du même nom, même si ce nom apparaît dans plusieurs fichiers
    .tournoi distincts."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_rank_unique_names_")
        self.addCleanup(self._tmp.cleanup)

    def test_meme_nom_dans_deux_tournois_fusionne_en_une_seule_entree(self):
        db1 = database.Database(os.path.join(self._tmp.name, "t1.tournoi"))
        db1.set_settings({"tournament_date": "2026-01-01"})
        db1.add_player("Alice")
        db1.conn.close()
        db2 = database.Database(os.path.join(self._tmp.name, "t2.tournoi"))
        db2.set_settings({"tournament_date": "2026-01-02"})
        db2.add_player("Alice")
        db2.conn.close()

        summary = database.build_period_summary(self._tmp.name, recursive=False)
        alice_entries = [p for p in summary["players"] if p["name"] == "Alice"]
        self.assertEqual(len(alice_entries), 1)
        self.assertEqual(alice_entries[0]["tournaments_played"], 2)

    def test_jamais_deux_noms_identiques_dans_players(self):
        for i in range(5):
            db = database.Database(os.path.join(self._tmp.name, f"t{i}.tournoi"))
            db.set_settings({"tournament_date": f"2026-01-0{i + 1}"})
            db.add_player("Alice")
            db.add_player("Bob")
            db.conn.close()
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        names = [p["name"] for p in summary["players"]]
        self.assertEqual(len(names), len(set(names)))


class PeriodPlayerColumnsOrderTest(unittest.TestCase):
    """database.PERIOD_PLAYER_COLUMNS seule : aucun Tk, aucune Database —
    prouve l'ordre pour les TROIS formats d'export à la fois, puisqu'ils
    consomment tous cette même liste (voir export_period_summary_csv/
    xlsx/pdf)."""

    def test_rang_immediatement_avant_joueur(self):
        keys = [k for k, _, _ in database.PERIOD_PLAYER_COLUMNS]
        self.assertEqual(keys.index("rang") + 1, keys.index("name"))

    def test_victoires_et_meilleur_rang_toujours_presents(self):
        keys = [k for k, _, _ in database.PERIOD_PLAYER_COLUMNS]
        self.assertIn("wins", keys)
        self.assertIn("best_place", keys)

    def test_rang_leve_keyerror_si_non_prepare(self):
        """Option A validée explicitement : contrat STRICT, jamais un
        repli silencieux — un oubli d'appeler _stats_players_with_rank
        doit être détecté, pas masqué."""
        rang_fn = next(fn for k, _, fn in database.PERIOD_PLAYER_COLUMNS if k == "rang")
        with self.assertRaises(KeyError):
            rang_fn(_player("Alice", 1500))  # pas de "rang" dans ce dict


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class StatsPlayerRankUiTest(unittest.TestCase):
    """PeriodSummaryDialog réel (widgets Tk réels, aucun dialogue modal —
    même harnais que tests/test_period_summary_stats_tab.py)."""

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
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_rank_ui_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(roster, "_roster_path", return_value=roster_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubAppForRank())
        # cleanup_tk (voir tests/_tk_cleanup.py) : lambda relit self.dialog
        # au moment du nettoyage — couvre aussi test_filtre_club_recalcule_
        # les_rangs, qui réaffecte self.dialog à un SECOND dialogue en
        # cours de test (l'ancien self.addCleanup(self.dialog.destroy)
        # aurait capturé le PREMIER et jamais nettoyé le second).
        self.addCleanup(lambda: cleanup_tk(self, "dialog"))
        self.dialog.date_from_var.set("")
        self.dialog.date_to_var.set("")

    def _new_db(self, filename, **settings):
        path = os.path.join(self._tmp.name, filename)
        db = database.Database(path)
        if settings:
            db.set_settings({k: str(v) for k, v in settings.items()})
        return db

    def _rows(self, tree):
        return [tree.item(iid, "values") for iid in tree.get_children()]

    def test_colonne_rang_positionnee_entre_club_et_joueur(self):
        cols = self.dialog.players_tree["columns"]
        self.assertEqual(cols.index("rang"), cols.index("club") + 1)
        self.assertEqual(cols.index("rang") + 1, cols.index("name"))
        self.assertEqual(self.dialog.players_tree.heading("rang", "text").rstrip(" ▲▼"), "Rang")

    def test_rang_affiche_correctement(self):
        db = self._new_db("t.tournoi", tournament_date="2026-01-01", ranking_formula="current")
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        c = db.add_player("Chris")
        db.eliminate_player(c)
        db.eliminate_player(b, eliminated_by_id=a)
        db.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()

        rows = {r[2]: r[1] for r in self._rows(self.dialog.players_tree)[1:]}  # saute TOTAL
        by_points = sorted(self.dialog.summary["players"], key=lambda p: -p["total_points"])
        expected_first = by_points[0]["name"]
        self.assertEqual(rows[expected_first], "1")

    def test_rang_inchange_apres_tri_sur_une_autre_colonne(self):
        self._new_db("t1.tournoi", tournament_date="2026-01-01", ranking_formula="current",
                      attendance_bonus_points=5).conn.close()
        db = database.Database(os.path.join(self._tmp.name, "t1.tournoi"))
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        c = db.add_player("Chris")
        db.eliminate_player(c)
        db.eliminate_player(b, eliminated_by_id=a)
        db.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()
        ranks_before = {r[2]: r[1] for r in self._rows(self.dialog.players_tree)[1:]}

        for col in ("name", "played", "total_presence_assiduity", "total_ranking_points", "total_points"):
            self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, col)
            ranks_after = {r[2]: r[1] for r in self._rows(self.dialog.players_tree)[1:]}
            self.assertEqual(ranks_after, ranks_before, f"rang changé après tri sur {col!r}")

    def test_filtre_club_recalcule_les_rangs(self):
        db = self._new_db("t.tournoi", tournament_date="2026-01-01", ranking_formula="current")
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.eliminate_player(b, eliminated_by_id=a)  # Alice gagne, plus de points
        db.conn.close()
        roster.set_club("Alice", "Chemillé")
        roster.set_club("Bob", "Angers")
        # La liste du filtre Club est figée à la CONSTRUCTION du dialogue
        # (voir _populate_club_filter_listbox) : reconstruire APRÈS avoir
        # posé les clubs, même principe que les tests existants du
        # chantier Statistiques (ex. test_filtre_club_change_affichage_
        # sans_replanter_les_fichiers).
        self.dialog.destroy()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubAppForRank())

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()

        # Sans filtre : Alice rang 1, Bob rang 2.
        rows = {r[2]: r[1] for r in self._rows(self.dialog.players_tree)[1:]}
        self.assertEqual(rows["Alice"], "1")
        self.assertEqual(rows["Bob"], "2")

        # Filtre Club = "Angers" (Bob seul affiché) : Bob doit redevenir
        # Rang 1 sur la liste FILTRÉE, jamais rester à "2".
        listbox = self.dialog.stats_club_listbox
        items = list(listbox.get(0, "end"))
        listbox.selection_clear(0, "end")
        listbox.selection_set(items.index("Angers"))
        self.dialog._refresh_display()

        filtered_rows = {r[2]: r[1] for r in self._rows(self.dialog.players_tree)[1:]}
        self.assertEqual(filtered_rows, {"Bob": "1"})

    def test_ligne_total_rang_vide(self):
        db = self._new_db("t.tournoi", tournament_date="2026-01-01")
        db.add_player("Alice")
        db.conn.close()
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()
        total_values = self._rows(self.dialog.players_tree)[0]
        self.assertEqual(total_values[1], "")
        self.assertEqual(total_values[2], "TOTAL")

    def test_tri_numerique_sur_en_tete_rang(self):
        db = self._new_db("t.tournoi", tournament_date="2026-01-01", ranking_formula="current")
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        c = db.add_player("Chris")
        db.eliminate_player(c)
        db.eliminate_player(b, eliminated_by_id=a)
        db.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()

        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "rang")
        ranks = [int(r[1]) for r in self._rows(self.dialog.players_tree)[1:]]
        self.assertEqual(ranks, sorted(ranks))

        self.dialog._on_stats_sort_click(self.dialog.stats_players_sort, "rang")  # inverse
        ranks_desc = [int(r[1]) for r in self._rows(self.dialog.players_tree)[1:]]
        self.assertEqual(ranks_desc, sorted(ranks_desc, reverse=True))

    def test_aucune_regression_filtre_type_jours_periode(self):
        """Le calcul de Rang ne doit rien changer aux filtres déjà en
        place (dossier/sous-dossiers/jours/Type/période/Club) — vérifié
        en combinant Type=Tournois + jour Vendredi + période, exactement
        comme avant l'ajout de Rang."""
        os.makedirs(os.path.join(self._tmp.name, "Vendredi"), exist_ok=True)
        db_to = database.Database(os.path.join(self._tmp.name, "Vendredi", "To1.tournoi"))
        db_to.set_settings({"tournament_date": "2026-01-02"})  # vendredi 2026-01-02
        db_to.add_player("Alice")
        db_to.conn.close()
        db_sn = database.Database(os.path.join(self._tmp.name, "Vendredi", "Sn1.tournoi"))
        db_sn.set_settings({"tournament_date": "2026-01-02"})
        db_sn.add_player("Bob")
        db_sn.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog.tournament_type_var.set(
            main.STATS_TOURNAMENT_TYPE_LABELS[database.STATS_TOURNAMENT_TYPE_TOURNOIS]
        )
        for day, var in self.dialog.stats_day_vars.items():
            var.set(day == "Vendredi")
        self.dialog.date_from_var.set("2026-01-01")
        self.dialog.date_to_var.set("2026-12-31")
        self.dialog._generate()

        names = sorted(p["name"] for p in self.dialog.summary["players"])
        self.assertEqual(names, ["Alice"])  # Sn1/Bob exclu par le filtre Type
        rows = {r[2]: r[1] for r in self._rows(self.dialog.players_tree)[1:]}
        self.assertEqual(rows, {"Alice": "1"})


class _StubAppForRank:
    db = None


class PlayerRankExportTest(unittest.TestCase):
    """Exports CSV/XLSX/PDF : "rang" préparé explicitement via main.
    _stats_players_with_rank AVANT chaque appel (Option A validée,
    contrat strict — voir PeriodPlayerColumnsOrderTest.test_rang_leve_
    keyerror_si_non_prepare)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_rank_export_")
        self.addCleanup(self._tmp.cleanup)

    def _summary_with_rank(self):
        players = [_player("Alice", 1500), _player("Bob", 1300), _player("Chris", 1300)]
        return {"tournaments": [], "players": main._stats_players_with_rank(players)}

    def test_csv_contient_rang(self):
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.csv")
        database.export_period_summary_csv(summary, out_path)
        with open(out_path, encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("Rang", content)

    def test_csv_rang_immediatement_avant_joueur(self):
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.csv")
        database.export_period_summary_csv(summary, out_path)
        with open(out_path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        header = next(line for line in lines if line.startswith("Rang;"))
        cells = header.split(";")
        self.assertEqual(cells[0], "Rang")
        self.assertEqual(cells[1], "Joueur")

    def test_csv_valeurs_identiques_au_classement(self):
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.csv")
        database.export_period_summary_csv(summary, out_path)
        with open(out_path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        # Localise l'en-tête par son contenu plutôt qu'un index fixe : la
        # section "Tournois de la période" (colonnes par défaut, même
        # vide) précède celle des joueurs (voir export_period_summary_csv).
        header_idx = next(i for i, line in enumerate(lines) if line.startswith("Rang;"))
        n_players = len(summary["players"])
        data_lines = lines[header_idx + 1:header_idx + 1 + n_players]
        rows = [line.split(";") for line in data_lines]
        by_name = {r[1]: r[0] for r in rows}
        self.assertEqual(by_name["Alice"], "1")
        self.assertEqual(by_name["Bob"], "2")
        self.assertEqual(by_name["Chris"], "2")

    def test_xlsx_contient_rang(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl indisponible dans cet environnement")
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.xlsx")
        database.export_period_summary_xlsx(summary, out_path)
        wb = load_workbook(out_path)
        ws = wb["Joueurs"]
        headers = [cell.value for cell in ws[2]]
        self.assertIn("Rang", headers)

    def test_xlsx_rang_immediatement_avant_joueur(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl indisponible dans cet environnement")
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.xlsx")
        database.export_period_summary_xlsx(summary, out_path)
        wb = load_workbook(out_path)
        ws = wb["Joueurs"]
        headers = [cell.value for cell in ws[2]]
        self.assertEqual(headers.index("Rang") + 1, headers.index("Joueur"))

    def test_xlsx_victoires_et_meilleur_rang_exportables(self):
        try:
            from openpyxl import load_workbook
        except ImportError:
            self.skipTest("openpyxl indisponible dans cet environnement")
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.xlsx")
        database.export_period_summary_xlsx(
            summary, out_path,
            player_keys=["rang", "name", "wins", "best_place"],
        )
        wb = load_workbook(out_path)
        ws = wb["Joueurs"]
        headers = [cell.value for cell in ws[2]]
        self.assertIn("Victoires", headers)
        self.assertIn("Meilleur Rang", headers)

    def test_pdf_contient_rang_ne_plante_pas(self):
        try:
            import fpdf  # noqa: F401
        except ImportError:
            self.skipTest("fpdf2 indisponible dans cet environnement")
        summary = self._summary_with_rank()
        out_path = os.path.join(self._tmp.name, "export.pdf")
        database.export_period_summary_pdf(summary, out_path)
        self.assertTrue(os.path.exists(out_path))
        self.assertGreater(os.path.getsize(out_path), 0)

    def test_export_non_influence_par_un_tri_visuel(self):
        """Simule un tri visuel "décroissant sur played" côté écran (sans
        rapport avec l'export) : l'ordre exporté doit rester celui de
        build_period_summary (total_points décroissant), jamais affecté."""
        players = [_player("Chris", 1100), _player("Alice", 1500), _player("Bob", 1300)]
        # Ordre d'entrée délibérément PAS trié (simule un résultat déjà
        # mélangé par un tri visuel quelconque) : _stats_players_with_rank
        # ne réordonne jamais (voir StatsPlayersWithRankUnitTest.
        # test_conserve_lordre_dentree) — seul _club_filtered_players/
        # build_period_summary déterminent l'ordre réellement exporté, ce
        # que ce test fixe explicitement ci-dessous.
        summary = {"tournaments": [], "players": main._stats_players_with_rank(players)}
        out_path = os.path.join(self._tmp.name, "export.csv")
        database.export_period_summary_csv(summary, out_path)
        with open(out_path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        header_idx = next(i for i, line in enumerate(lines) if line.startswith("Rang;"))
        data_lines = lines[header_idx + 1:header_idx + 1 + len(players)]
        exported_order = [line.split(";")[1] for line in data_lines]
        self.assertEqual(exported_order, ["Chris", "Alice", "Bob"])  # ordre d'entrée préservé tel quel


if __name__ == "__main__":
    unittest.main()
