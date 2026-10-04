# -*- coding: utf-8 -*-
"""Onglet Joueurs — bouton "🖨️ Imprimer" et PDF Nom/Pseudo/Table/Siège
(demande du 2026-10-03, "onglet Joueurs — bouton Imprimer et PDF des
places") — pour communiquer À L'AVANCE à chaque joueur sa place, avant
un gros tournoi. RÉUTILISE Database._write_pdf_table (même moteur que
les autres exports PDF de ce fichier — export_settings_pdf, export_
players_pdf... — voir le diagnostic du 2026-10-03), jamais un système
parallèle.

Décision validée le 2026-10-03 : AUCUNE séparation Nom/Prénom (le nom
reste un seul champ texte libre partout dans Senaco) — colonnes finales
Nom/Pseudo/Table/Siège, pas les 5 initialement envisagées.

Couvre :
1. Database.export_players_seating_pdf — génération + CONTENU réel du
   PDF (PyMuPDF, comme tests/test_settings_pdf_export.py), les deux
   tris ("name"/"table"), joueurs actifs uniquement, pseudo/table/siège
   corrects, cas limites (pas encore placé, pas de pseudo).
2. PlayersSeatingPrintDialog — choix par défaut "name", bascule "table",
   "Annuler" laisse result=None.
3. App._print_players_seating — patron identique à _print_settings_pdf/
   _print_movement_slips (asksaveasfilename -> export -> ouverture),
   annulation à chaque étape, ImportError (fpdf2 absent)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402

try:
    import fpdf  # noqa: F401
    _FPDF_AVAILABLE = True
except ImportError:
    _FPDF_AVAILABLE = False

try:
    import fitz  # PyMuPDF — extraction de texte pour vérifier le CONTENU réel du PDF.
    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _pdf_text(path):
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


# =====================================================================
# 1. Database.export_players_seating_pdf
# =====================================================================
@unittest.skipUnless(_FPDF_AVAILABLE, "fpdf2 indisponible dans cet environnement")
class ExportPlayersSeatingPdfGenerationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_seating_pdf_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.out_path = os.path.join(self._tmp.name, "placement.pdf")

    def test_pdf_genere_et_non_vide(self):
        self.db.add_player("Dupont Jean", pseudo="Toto")
        self.db.export_players_seating_pdf(self.out_path)
        self.assertTrue(os.path.exists(self.out_path))
        self.assertGreater(os.path.getsize(self.out_path), 0)

    def test_fonctionne_sans_aucun_joueur(self):
        self.db.export_players_seating_pdf(self.out_path)  # ne doit pas lever
        self.assertTrue(os.path.exists(self.out_path))


@unittest.skipUnless(_FPDF_AVAILABLE, "fpdf2 indisponible dans cet environnement")
@unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) indisponible dans cet environnement")
class ExportPlayersSeatingPdfContentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_seating_pdf_content_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.out_path = os.path.join(self._tmp.name, "placement.pdf")

    def test_colonnes_nom_pseudo_table_siege_presentes(self):
        self.db.set_settings({"tournament_name": "Tournoi du vendredi"})
        self.db.add_player("Dupont Jean", pseudo="Toto")
        self.db.export_players_seating_pdf(self.out_path)
        text = _pdf_text(self.out_path)
        for expected in ("Nom", "Pseudo", "Table", "Siège", "Tournoi du vendredi", "Dupont Jean", "Toto"):
            self.assertIn(expected, text)

    def test_aucune_colonne_prenom_separee(self):
        """Décision validée le 2026-10-03 : pas de séparation Nom/Prénom
        — un seul champ "Nom", qui porte le nom complet tel que saisi."""
        self.db.add_player("Dupont Jean")
        self.db.export_players_seating_pdf(self.out_path)
        text = _pdf_text(self.out_path)
        self.assertNotIn("Prénom", text)
        self.assertIn("Dupont Jean", text)  # le nom complet, en un seul champ

    def test_table_et_siege_reellement_attribues(self):
        pid = self.db.add_player("Alice")
        player = self.db.get_player(pid)
        self.assertIsNotNone(player["table_id"])  # _seat_player a déjà placé Alice
        self.assertIsNotNone(player["seat"])

        self.db.export_players_seating_pdf(self.out_path)
        text = _pdf_text(self.out_path)
        tables = {t["id"]: t["name"] for t in self.db.list_tables(active_only=False)}
        self.assertIn(tables[player["table_id"]], text)
        self.assertIn(str(player["seat"]), text)

    def test_seul_les_joueurs_actifs_sont_listes(self):
        self.db.add_player("Alice")
        bob_id = self.db.add_player("Bob")
        self.db.withdraw_player(bob_id)

        self.db.export_players_seating_pdf(self.out_path)
        text = _pdf_text(self.out_path)

        self.assertIn("Alice", text)
        self.assertNotIn("Bob", text)

    def test_pseudo_absent_affiche_une_cellule_vide_pas_derreur(self):
        self.db.add_player("Alice")  # pas de pseudo
        self.db.export_players_seating_pdf(self.out_path)  # ne doit pas lever
        text = _pdf_text(self.out_path)
        self.assertIn("Alice", text)

    def test_tri_par_nom_est_alphabetique(self):
        self.db.add_player("Zoe")
        self.db.add_player("Alice")
        self.db.export_players_seating_pdf(self.out_path, sort_by="name")
        text = _pdf_text(self.out_path)
        self.assertLess(text.index("Alice"), text.index("Zoe"))

    def test_tri_par_table_utilise_table_id_pas_le_nom_affiche(self):
        """Évite le piège "Table 10" trié avant "Table 2" en alphabétique
        — le tri doit porter sur table_id/seat bruts, jamais sur la
        chaîne affichée. list_players/list_tables entièrement simulées
        (jamais de vraie insertion/rééquilibrage) pour isoler la LOGIQUE
        DE TRI de tout effet de bord du placement automatique (add_player
        /rebalance_tables pourraient autrement réattribuer/fermer des
        tables créées exprès pour ce scénario)."""
        fake_tables = [{"id": 1, "name": "Table 10"}, {"id": 2, "name": "Table 2"}]
        fake_players = [
            {"name": "Alice", "pseudo": "", "table_id": 1, "seat": 1},  # table_id 1 = "Table 10"
            {"name": "Bob", "pseudo": "", "table_id": 2, "seat": 1},    # table_id 2 = "Table 2"
        ]
        with patch.object(self.db, "list_tables", return_value=fake_tables), \
             patch.object(self.db, "list_players", return_value=fake_players):
            self.db.export_players_seating_pdf(self.out_path, sort_by="table")
        text = _pdf_text(self.out_path)
        # table_id(Alice)=1 < table_id(Bob)=2 : Alice doit apparaître
        # AVANT Bob, conformément au tri par table_id brut — bien que
        # "Table 2" (celle de Bob) trierait AVANT "Table 10" (celle
        # d'Alice) en ordre alphabétique du nom affiché.
        self.assertLess(text.index("Alice"), text.index("Bob"))

    def test_tri_par_table_place_les_non_assignes_en_dernier(self):
        fake_tables = [{"id": 1, "name": "Table 1"}]
        fake_players = [
            {"name": "Alice", "pseudo": "", "table_id": None, "seat": None},
            {"name": "Bob", "pseudo": "", "table_id": 1, "seat": 1},
        ]
        with patch.object(self.db, "list_tables", return_value=fake_tables), \
             patch.object(self.db, "list_players", return_value=fake_players):
            self.db.export_players_seating_pdf(self.out_path, sort_by="table")
        text = _pdf_text(self.out_path)
        self.assertLess(text.index("Bob"), text.index("Alice"))


# =====================================================================
# 2. PlayersSeatingPrintDialog
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayersSeatingPrintDialogTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_resultat_par_defaut_nom(self):
        dlg = main.PlayersSeatingPrintDialog(self.root)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        self.assertEqual(dlg.sort_var.get(), "name")

    def test_confirmer_definit_le_resultat_et_ferme(self):
        dlg = main.PlayersSeatingPrintDialog(self.root)
        dlg.sort_var.set("table")
        dlg._confirm()
        self.assertEqual(dlg.result, "table")
        self.assertFalse(dlg.winfo_exists())

    def test_annuler_laisse_le_resultat_a_none(self):
        dlg = main.PlayersSeatingPrintDialog(self.root)
        self.assertIsNone(dlg.result)
        dlg.destroy()  # équivalent du clic "Annuler"
        self.assertIsNone(dlg.result)


# =====================================================================
# 3. App._print_players_seating
# =====================================================================
class _FakeAppForPrintSeating:
    """Doublure minimale : _print_players_seating n'utilise que self.db,
    self.wait_window, filedialog/messagebox (module main, patchés), et
    la classe PlayersSeatingPrintDialog (patchée pour éviter tout VRAI
    Toplevel/wait_window — risque de segfault Tcl/Tk déjà documenté dans
    ce harnais)."""

    def __init__(self, db):
        self.db = db
        self.wait_window_calls = 0

    def wait_window(self, dialog):
        self.wait_window_calls += 1


class _FakeSeatingDialog:
    def __init__(self, result):
        self.result = result


class PrintPlayersSeatingHandlerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="print_players_seating_handler_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.app = _FakeAppForPrintSeating(self.db)

    def test_annulation_de_la_boite_de_tri_nappelle_rien_dautre(self):
        with patch.object(main, "PlayersSeatingPrintDialog", return_value=_FakeSeatingDialog(None)), \
             patch.object(main.filedialog, "asksaveasfilename") as mock_save:
            main.App._print_players_seating(self.app)
        mock_save.assert_not_called()
        self.assertEqual(self.app.wait_window_calls, 1)

    def test_annulation_du_enregistrer_sous_naexporte_rien(self):
        with patch.object(main, "PlayersSeatingPrintDialog", return_value=_FakeSeatingDialog("name")), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=""), \
             patch.object(self.db, "export_players_seating_pdf") as mock_export:
            main.App._print_players_seating(self.app)
        mock_export.assert_not_called()

    def test_tri_choisi_est_transmis_a_lexport(self):
        out_path = os.path.join(self._tmp.name, "out.pdf")
        with patch.object(main, "PlayersSeatingPrintDialog", return_value=_FakeSeatingDialog("table")), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app") as mock_open, \
             patch.object(self.db, "export_players_seating_pdf") as mock_export:
            main.App._print_players_seating(self.app)
        mock_export.assert_called_once_with(out_path, sort_by="table")
        mock_open.assert_called_once_with(out_path)

    def test_fpdf2_absent_affiche_le_message_dedie(self):
        out_path = os.path.join(self._tmp.name, "out.pdf")
        with patch.object(main, "PlayersSeatingPrintDialog", return_value=_FakeSeatingDialog("name")), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(self.db, "export_players_seating_pdf", side_effect=ImportError), \
             patch.object(main, "show_missing_export_module") as mock_missing, \
             patch.object(main, "open_file_with_default_app") as mock_open:
            main.App._print_players_seating(self.app)
        mock_missing.assert_called_once_with("pdf")
        mock_open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
