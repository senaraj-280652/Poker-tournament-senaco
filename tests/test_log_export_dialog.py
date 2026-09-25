# -*- coding: utf-8 -*-
"""Fenêtre "Exporter le LOG" (correction du 2026-09-24 : remplace
l'export CSV direct implémenté un peu plus tôt le même jour) — reprend
le même principe/mécanisme que "Exporter les primes" (voir main.py:
PrimesExportDialog, database.py: export_primes_csv/xlsx/pdf), SANS la
section "Tableau à exporter" (LOG n'a qu'un seul tableau).

Quatre volets :
1. LogExportEngineTest — action_log.LOG_EXPORT_COLUMNS/
   selected_log_export_columns/export_csv/export_xlsx/export_pdf
   directement (pas de Tk) : colonnes, ordre, libellés, formats de
   fichier valides.
2. LogExportDialogGuiTest — main.LogExportDialog construit RÉELLEMENT
   (elle n'utilise que grab_set(), JAMAIS wait_window() — donc SANS le
   risque de segfault Tcl/Tk documenté dans tests/test_ask_eliminator_
   window_position.py pour les fenêtres modales bloquantes ; vérifié
   empiriquement avant d'écrire ces tests) : cases à cocher, Tout
   cocher/décocher, refus sans colonne, Annuler, appel des bonnes
   fonctions action_log.export_* selon le format choisi.
3. LogExportEndToEndTest — App._on_log_export -> LogExportDialog ->
   fichier réellement écrit, bout en bout, avec filtre actif.
4. PdfTextSanitizationTest / PdfExportUnicodeRobustnessTest /
   LogExportDialogErrorHandlingTest — diagnostic du 2026-09-24 : l'export
   PDF échouait SILENCIEUSEMENT (aucun fichier, aucun message) dès
   qu'une valeur exportée contenait un caractère hors latin-1 (repéré
   avec ACTION_LABELS["elimination"] = "Onglet Joueurs (⏸)", la police
   coeur "Helvetica" de fpdf2 ne supportant que ce jeu de caractères) —
   Tkinter avalait l'exception FPDFUnicodeEncodingException sans rien
   afficher (voir App.report_callback_exception). Corrigé par (1)
   action_log._pdf_text, désormais un filet de sécurité GÉNÉRAL (tout
   caractère hors latin-1, pas seulement ⏸) et (2) LogExportDialog.
   _do_export, qui affiche désormais une erreur claire pour toute
   exception d'export autre qu'ImportError, sans jamais fermer la
   fenêtre ni ouvrir un fichier qui n'a pas été créé, et sans jamais
   perdre la trace dans ~/.poker_tournament/crash.log (voir main.
   _log_exception, appelée explicitement puisque l'exception n'atteint
   plus report_callback_exception une fois interceptée ici).
5. FormatLogExportCriteriaTest / ExportPdfCriteriaLineContentTest /
   LogExportCriteriaWiringTest — "petite amélioration du PDF" du
   2026-09-24 : une ligne "Critères : ..." sous le titre, résumant
   Du/Au/Tournoi/Utilisateur/Fonction/Joueur EXACTEMENT tels qu'actifs
   au moment de l'export (voir action_log.format_log_export_criteria,
   App._on_log_export). Revu le 2026-09-25 ("les 3 formats doivent
   afficher les mêmes critères et le même nombre d'opérations") :
   CSV/Excel reçoivent désormais aussi cette ligne (LogExportCriteria
   WiringTest inversé en conséquence), et les 3 formats affichent en
   plus "Nombre d'opérations : N" (voir VolumeImportantTest), calculé
   depuis les lignes réellement écrites — toujours passée par la
   protection Unicode PDF (action_log._pdf_text) pour le format PDF. La
   vérification du CONTENU RÉEL du PDF utilise PyMuPDF (import fitz) si
   disponible — une dépendance de TEST uniquement, jamais requise par
   l'application elle-même (voir show_missing_export_module) : les
   tests concernés sont ignorés proprement si absente, jamais en échec.
6. VolumeImportantTest — scénario représentatif à 750 opérations
   correspondant aux filtres actifs, mêlées à d'autres hors filtre :
   affichage, compteur, les 3 exports ET Purger, bout en bout (chantier
   "CE QUI CORRESPOND AUX FILTRES = CE QUI EST AFFICHÉ = CE QUI EST
   EXPORTÉ = CE QUI PEUT ÊTRE PURGÉ", 2026-09-25)."""
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
import export_prefs  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False

try:
    import fitz  # PyMuPDF — dépendance de TEST uniquement, voir docstring de module
    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False


def _pdf_extract_text(path):
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


_SAMPLE_ROWS = [
    {
        "ts": "24/09/2026 14:32:18", "tournament": "Tournoi du vendredi", "user": "Raj",
        "role": "ADMIN", "category": "Éliminations", "action": "Élimination",
        "player": "Alice", "result": "Réussi", "message": "premier essai",
    },
    {
        "ts": "24/09/2026 13:00:00", "tournament": "Tournoi du vendredi", "user": "Marie",
        "role": "DIRTO", "category": "Chronomètre", "action": "Pause / Reprise",
        "player": "", "result": "Refusé", "message": "",
    },
]


# =======================================================================
# 1. Moteur (action_log.py) — pas de Tk
# =======================================================================
class ColumnsDefinitionTest(unittest.TestCase):
    def test_9_colonnes_definies_dans_lordre_daffichage(self):
        self.assertEqual(
            [k for k, _ in action_log.LOG_EXPORT_COLUMNS],
            ["ts", "tournament", "user", "role", "category", "action", "player", "result", "message"],
        )
        self.assertEqual(
            [h for _, h in action_log.LOG_EXPORT_COLUMNS],
            ["Date/Heure", "Tournoi", "Utilisateur", "Rôle", "Fonction", "Action", "Joueur", "Résultat", "Message"],
        )

    def test_device_id_et_tournament_path_jamais_dans_les_colonnes(self):
        keys = [k for k, _ in action_log.LOG_EXPORT_COLUMNS]
        self.assertNotIn("device_id", keys)
        self.assertNotIn("tournament_path", keys)

    def test_selected_log_export_columns_respecte_lordre_dorigine(self):
        # Cochées dans un ordre volontairement DIFFÉRENT de l'ordre
        # d'affichage — le résultat doit rester dans l'ordre d'origine.
        cols = action_log.selected_log_export_columns(["message", "ts", "player"])
        self.assertEqual([k for k, _ in cols], ["ts", "player", "message"])

    def test_selected_log_export_columns_ignore_les_non_cochees(self):
        cols = action_log.selected_log_export_columns(["ts", "action"])
        self.assertEqual(len(cols), 2)

    def test_none_renvoie_toutes_les_colonnes(self):
        cols = action_log.selected_log_export_columns(None)
        self.assertEqual(cols, action_log.LOG_EXPORT_COLUMNS)


class ExportCsvEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_csv_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_csv_utf8_sig_et_separateur_point_virgule(self):
        path = os.path.join(self._tmp.name, "out.csv")
        action_log.export_csv(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS)
        with open(path, "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "BOM utf-8-sig attendu")
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        # Ligne 0 = titre, ligne 1 = "Nombre d'opérations : N" (revu le
        # 2026-09-25 — pas de ligne "Critères" ici, criteria_line=None),
        # ligne 2 = en-têtes des colonnes — c'est celle-ci qui doit
        # utiliser ";", jamais ",".
        header = lines[2]
        self.assertIn(";", header)
        self.assertNotIn(",", header)

    def test_csv_entetes_correspondent_uniquement_aux_colonnes_selectionnees(self):
        path = os.path.join(self._tmp.name, "out.csv")
        cols = action_log.selected_log_export_columns(["ts", "user", "message"])
        action_log.export_csv(path, cols, _SAMPLE_ROWS)
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        # 0: titre, 1: "Nombre d'opérations : N" (pas de critères ici),
        # 2: en-têtes, puis les données (revu le 2026-09-25).
        self.assertEqual(lines[0], "Journal des actions")
        self.assertEqual(lines[1], f"Nombre d'opérations : {len(_SAMPLE_ROWS)}")
        self.assertEqual(lines[2], "Date/Heure;Utilisateur;Message")
        self.assertEqual(len(lines), 3 + len(_SAMPLE_ROWS))

    def test_csv_libelles_francais_deja_dans_les_lignes(self):
        path = os.path.join(self._tmp.name, "out.csv")
        action_log.export_csv(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS)
        with open(path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("Réussi", content)
        self.assertIn("Refusé", content)
        self.assertNotIn("SUCCESS", content)
        self.assertNotIn("DENIED", content)

    def test_csv_titre_et_criteres_avant_les_entetes(self):
        path = os.path.join(self._tmp.name, "out.csv")
        action_log.export_csv(
            path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS,
            title="Journal des actions", criteria_line="Critères : Utilisateur : Marie",
        )
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "Journal des actions")
        self.assertEqual(lines[1], "Critères : Utilisateur : Marie")
        self.assertEqual(lines[2], f"Nombre d'opérations : {len(_SAMPLE_ROWS)}")
        self.assertEqual(lines[3], "Date/Heure;Tournoi;Utilisateur;Rôle;Fonction;Action;Joueur;Résultat;Message")


# =======================================================================
# 1bis. "Nombre d'opérations : N" dans les 3 formats (2026-09-25) — voir
#       aussi VolumeImportantTest pour le cas N=750.
# =======================================================================
class NombreOperationsExporteesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_count_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_csv_zero_operation(self):
        path = os.path.join(self._tmp.name, "zero.csv")
        action_log.export_csv(path, action_log.LOG_EXPORT_COLUMNS, [])
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[1], "Nombre d'opérations : 0")

    def test_csv_une_operation(self):
        path = os.path.join(self._tmp.name, "un.csv")
        action_log.export_csv(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS[:1])
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[1], "Nombre d'opérations : 1")

    def test_csv_nombre_calcule_depuis_les_lignes_reelles_jamais_transmis(self):
        """Aucun paramètre "count" n'existe : le nombre est TOUJOURS
        len(rows), impossible de le faire diverger du contenu réel."""
        path = os.path.join(self._tmp.name, "sept.csv")
        rows = [_SAMPLE_ROWS[0]] * 7
        action_log.export_csv(path, action_log.LOG_EXPORT_COLUMNS, rows)
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[1], "Nombre d'opérations : 7")
        self.assertEqual(len(lines) - 3, 7)  # titre+nombre+en-têtes puis 7 lignes de données

    def test_xlsx_zero_operation(self):
        from openpyxl import load_workbook

        path = os.path.join(self._tmp.name, "zero.xlsx")
        action_log.export_xlsx(path, action_log.LOG_EXPORT_COLUMNS, [])
        wb = load_workbook(path)
        ws = wb.active
        self.assertEqual(ws.cell(row=2, column=1).value, "Nombre d'opérations : 0")

    def test_xlsx_une_operation(self):
        from openpyxl import load_workbook

        path = os.path.join(self._tmp.name, "un.xlsx")
        action_log.export_xlsx(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS[:1])
        wb = load_workbook(path)
        ws = wb.active
        self.assertEqual(ws.cell(row=2, column=1).value, "Nombre d'opérations : 1")

    def test_xlsx_avec_criteres_le_nombre_suit_la_ligne_de_criteres(self):
        from openpyxl import load_workbook

        path = os.path.join(self._tmp.name, "criteres.xlsx")
        action_log.export_xlsx(
            path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS,
            criteria_line="Critères : Utilisateur : Marie",
        )
        wb = load_workbook(path)
        ws = wb.active
        self.assertEqual(ws.cell(row=1, column=1).value, "Journal des actions")
        self.assertEqual(ws.cell(row=2, column=1).value, "Critères : Utilisateur : Marie")
        self.assertEqual(ws.cell(row=3, column=1).value, f"Nombre d'opérations : {len(_SAMPLE_ROWS)}")
        # Ligne 4 vide, ligne 5 en-têtes.
        header_values = [ws.cell(row=5, column=c).value for c in range(1, len(action_log.LOG_EXPORT_COLUMNS) + 1)]
        self.assertEqual(header_values, [h for _, h in action_log.LOG_EXPORT_COLUMNS])

    @unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) non installé — dépendance de TEST uniquement")
    def test_pdf_zero_operation(self):
        path = os.path.join(self._tmp.name, "zero.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, [])
        text = _pdf_extract_text(path)
        self.assertIn("Nombre d'opérations : 0", text)

    @unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) non installé — dépendance de TEST uniquement")
    def test_pdf_une_operation(self):
        path = os.path.join(self._tmp.name, "un.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS[:1])
        text = _pdf_extract_text(path)
        self.assertIn("Nombre d'opérations : 1", text)

    @unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) non installé — dépendance de TEST uniquement")
    def test_pdf_avec_criteres_les_deux_lignes_presentes(self):
        path = os.path.join(self._tmp.name, "criteres.pdf")
        action_log.export_pdf(
            path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS,
            criteria_line="Critères : Utilisateur : Marie",
        )
        text = _pdf_extract_text(path)
        self.assertIn("Utilisateur : Marie", text)
        self.assertIn(f"Nombre d'opérations : {len(_SAMPLE_ROWS)}", text)

    @unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) non installé — dépendance de TEST uniquement")
    def test_pdf_sans_criteres_le_nombre_reste_present(self):
        """"Nombre d'opérations" n'est JAMAIS conditionnée à la présence
        d'une ligne de critères, contrairement à celle-ci."""
        path = os.path.join(self._tmp.name, "sans_criteres.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS)
        text = _pdf_extract_text(path)
        self.assertNotIn("Critères", text)
        self.assertIn(f"Nombre d'opérations : {len(_SAMPLE_ROWS)}", text)


class ExportXlsxEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_xlsx_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_fichier_xlsx_valide_et_relisible(self):
        from openpyxl import load_workbook

        path = os.path.join(self._tmp.name, "out.xlsx")
        action_log.export_xlsx(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS, title="Journal des actions")
        self.assertTrue(os.path.exists(path))
        wb = load_workbook(path)
        ws = wb.active
        # Ligne 1 : titre ; ligne 2 : "Nombre d'opérations : N" (revu le
        # 2026-09-25, pas de ligne "Critères" ici — criteria_line=None) ;
        # ligne 3 : vide ; ligne 4 : en-têtes.
        self.assertEqual(ws.cell(row=1, column=1).value, "Journal des actions")
        self.assertEqual(ws.cell(row=2, column=1).value, f"Nombre d'opérations : {len(_SAMPLE_ROWS)}")
        header_values = [ws.cell(row=4, column=c).value for c in range(1, len(action_log.LOG_EXPORT_COLUMNS) + 1)]
        self.assertEqual(header_values, [h for _, h in action_log.LOG_EXPORT_COLUMNS])

    def test_xlsx_seulement_les_colonnes_cochees_dans_lordre(self):
        from openpyxl import load_workbook

        path = os.path.join(self._tmp.name, "out.xlsx")
        cols = action_log.selected_log_export_columns(["player", "ts"])
        action_log.export_xlsx(path, cols, _SAMPLE_ROWS)
        wb = load_workbook(path)
        ws = wb.active
        header_values = [ws.cell(row=4, column=c).value for c in (1, 2)]
        self.assertEqual(header_values, ["Date/Heure", "Joueur"])

    def test_xlsx_message_colonne_plus_large_pour_rester_lisible(self):
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        path = os.path.join(self._tmp.name, "out.xlsx")
        action_log.export_xlsx(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS)
        wb = load_workbook(path)
        ws = wb.active
        message_idx = [k for k, _ in action_log.LOG_EXPORT_COLUMNS].index("message") + 1
        role_idx = [k for k, _ in action_log.LOG_EXPORT_COLUMNS].index("role") + 1
        msg_width = ws.column_dimensions[get_column_letter(message_idx)].width
        role_width = ws.column_dimensions[get_column_letter(role_idx)].width
        self.assertGreater(msg_width, role_width)


class ExportPdfEngineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_pdf_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_fichier_pdf_valide(self):
        path = os.path.join(self._tmp.name, "out.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS, title="Journal des actions")
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as f:
            header = f.read(5)
        self.assertEqual(header, b"%PDF-")

    def test_pdf_message_long_ne_leve_pas_et_reste_dans_le_fichier(self):
        long_message = "Un très long message de test. " * 30
        rows = [dict(_SAMPLE_ROWS[0], message=long_message)]
        path = os.path.join(self._tmp.name, "long.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, rows)
        self.assertTrue(os.path.exists(path))
        self.assertGreater(os.path.getsize(path), 0)

    def test_pdf_peu_de_colonnes_reste_valide(self):
        path = os.path.join(self._tmp.name, "few.pdf")
        cols = action_log.selected_log_export_columns(["ts", "player"])
        action_log.export_pdf(path, cols, _SAMPLE_ROWS)
        with open(path, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")

    def test_pdf_aucune_ligne_reste_valide(self):
        path = os.path.join(self._tmp.name, "empty.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, [])
        with open(path, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")


# =======================================================================
# 2. main.LogExportDialog — Toplevel réel (grab_set() SEUL, jamais
#    wait_window() : sûr à piloter réellement, vérifié empiriquement).
# =======================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogExportDialogGuiTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_dialog_gui_test_")
        self.addCleanup(self._tmp.cleanup)
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        # Jamais de VRAIE ouverture OS du fichier exporté pendant les
        # tests (open_file_with_default_app lance "open"/xdg-open/
        # os.startfile en best-effort) — un test réussi ne doit jamais
        # faire apparaître Finder/Explorateur ni laisser un sous-
        # processus orphelin.
        open_patcher = patch("main.open_file_with_default_app")
        self.addCleanup(open_patcher.stop)
        open_patcher.start()

        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _open(self, rows=None, criteria_line=None):
        dlg = main.LogExportDialog(self.root, rows if rows is not None else list(_SAMPLE_ROWS), criteria_line)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        return dlg


class OuvertureEtColonnesTest(LogExportDialogGuiTestCase):
    def test_titre_de_la_fenetre(self):
        dlg = self._open()
        self.assertEqual(dlg.title(), "Exporter le LOG")

    def test_9_colonnes_proposees(self):
        dlg = self._open()
        self.assertEqual(len(dlg.col_vars), 9)
        self.assertEqual(set(dlg.col_vars.keys()), {k for k, _ in action_log.LOG_EXPORT_COLUMNS})

    def test_toutes_cochees_par_defaut(self):
        dlg = self._open()
        self.assertTrue(all(v.get() for v in dlg.col_vars.values()))

    def test_pas_de_section_tableau_a_exporter(self):
        """Contrairement à PrimesExportDialog : LOG n'a qu'un seul
        tableau, donc pas de LabelFrame "Tableau à exporter"."""
        dlg = self._open()

        def _has_labelframe_titled(widget, text):
            for c in widget.winfo_children():
                if isinstance(c, ttk.LabelFrame) and c.cget("text") == text:
                    return True
                if _has_labelframe_titled(c, text):
                    return True
            return False

        self.assertFalse(_has_labelframe_titled(dlg, "Tableau à exporter"))
        self.assertFalse(hasattr(dlg, "kind_var"))

    def test_format_par_defaut_csv(self):
        dlg = self._open()
        self.assertEqual(dlg.format_var.get(), "csv")


class ToutCocherDecocherTest(LogExportDialogGuiTestCase):
    def test_tout_decocher(self):
        dlg = self._open()

        def find_button(w, text):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == text:
                    return c
                r = find_button(c, text)
                if r:
                    return r
            return None

        find_button(dlg, "Tout décocher").invoke()
        self.assertFalse(any(v.get() for v in dlg.col_vars.values()))

    def test_tout_cocher_apres_decoche(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)

        def find_button(w, text):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == text:
                    return c
                r = find_button(c, text)
                if r:
                    return r
            return None

        find_button(dlg, "Tout cocher").invoke()
        self.assertTrue(all(v.get() for v in dlg.col_vars.values()))


class RefusSansColonneTest(LogExportDialogGuiTestCase):
    def test_aucune_colonne_cochee_refuse_lexport(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)
        with patch("main.messagebox.showerror") as mock_err, \
             patch("main.filedialog.asksaveasfilename") as mock_save:
            dlg._do_export()
        mock_err.assert_called_once()
        mock_save.assert_not_called()
        self.assertTrue(dlg.winfo_exists(), "la fenêtre ne doit pas se fermer sur un refus")


class ChoixDuFormatTest(LogExportDialogGuiTestCase):
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

    def test_extension_de_fichier_proposee_correspond_au_format(self):
        dlg = self._open()
        dlg.format_var.set("xlsx")
        with patch("main.filedialog.asksaveasfilename", return_value="") as mock_save, \
             patch("action_log.export_xlsx"):
            dlg._do_export()
        kwargs = mock_save.call_args[1]
        self.assertEqual(kwargs["defaultextension"], ".xlsx")


class ColonnesCocheesEtOrdreTest(LogExportDialogGuiTestCase):
    def test_seules_les_colonnes_cochees_sont_transmises_dans_lordre(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)
        dlg.col_vars["message"].set(True)
        dlg.col_vars["ts"].set(True)
        dlg.col_vars["user"].set(True)
        dlg.col_vars["action"].set(True)
        # Cochées dans le désordre : "message" avant "ts" — l'export
        # doit malgré tout respecter l'ordre canonique.
        with patch("main.filedialog.asksaveasfilename", return_value=os.path.join(self._tmp.name, "o.csv")), \
             patch("action_log.export_csv") as mock_export:
            dlg._do_export()
        columns_arg = mock_export.call_args[0][1]
        self.assertEqual([k for k, _ in columns_arg], ["ts", "user", "action", "message"])

    def test_exemple_de_lenonce_date_utilisateur_action_joueur(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)
        for key in ("ts", "user", "action", "player"):
            dlg.col_vars[key].set(True)
        out_path = os.path.join(self._tmp.name, "o.csv")
        dlg.format_var.set("csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        with open(out_path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        # 0: titre, 1: "Nombre d'opérations : N" (pas de critères ici —
        # _open() par défaut ne transmet aucun criteria_line), 2: en-têtes.
        self.assertEqual(lines[2], "Date/Heure;Utilisateur;Action;Joueur")


class DonneesExporteesTest(LogExportDialogGuiTestCase):
    def test_export_porte_sur_exactement_les_rows_transmises(self):
        dlg = self._open()
        out_path = os.path.join(self._tmp.name, "o.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        with open(out_path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        # titre + "Nombre d'opérations" + en-têtes (3) + données (revu le
        # 2026-09-25 — pas de plafond, pas de ligne "Critères" ici).
        self.assertEqual(len(lines), 3 + len(_SAMPLE_ROWS))

    def test_aucune_nouvelle_recherche_relancee(self):
        """La fenêtre ne connaît QUE les rows passées à la construction —
        aucun appel à action_log.search_actions ne doit jamais avoir
        lieu depuis cette fenêtre."""
        dlg = self._open()
        out_path = os.path.join(self._tmp.name, "o.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.search_actions") as mock_search:
            dlg._do_export()
        mock_search.assert_not_called()

    def test_libelles_francais_dans_le_fichier(self):
        dlg = self._open()
        out_path = os.path.join(self._tmp.name, "o.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        with open(out_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("Réussi", content)
        self.assertIn("Refusé", content)

    def test_aucune_donnee_device_id_ou_tournament_path(self):
        rows = [dict(_SAMPLE_ROWS[0]), {**_SAMPLE_ROWS[1]}]
        # Même si l'appelant avait (par erreur) glissé ces clés dans une
        # ligne, elles ne doivent jamais atteindre le fichier : seules
        # les clés de LOG_EXPORT_COLUMNS sont lues (voir export_csv).
        rows[0]["device_id"] = "secret-device-id"
        rows[0]["tournament_path"] = "/very/secret/path.tournoi"
        dlg = self._open(rows)
        out_path = os.path.join(self._tmp.name, "o.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        with open(out_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        self.assertNotIn("secret-device-id", content)
        self.assertNotIn("/very/secret/path.tournoi", content)


class AnnulerTest(LogExportDialogGuiTestCase):
    def test_annuler_ferme_la_fenetre_sans_ecrire(self):
        dlg = self._open()

        def find_button(w, text):
            for c in w.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == text:
                    return c
                r = find_button(c, text)
                if r:
                    return r
            return None

        with patch("main.filedialog.asksaveasfilename") as mock_save:
            find_button(dlg, "Annuler").invoke()
        mock_save.assert_not_called()
        self.assertFalse(dlg.winfo_exists())

    def test_annulation_de_la_boite_de_dialogue_necrit_rien(self):
        dlg = self._open()
        with patch("main.filedialog.asksaveasfilename", return_value=""), \
             patch("action_log.export_csv") as mock_export:
            dlg._do_export()
        mock_export.assert_not_called()
        self.assertTrue(dlg.winfo_exists(), "annuler la boîte de sauvegarde ne doit pas fermer la fenêtre")


class PreferencesMemoriseesTest(LogExportDialogGuiTestCase):
    def test_format_et_colonnes_memorises_apres_export(self):
        dlg = self._open()
        for v in dlg.col_vars.values():
            v.set(False)
        dlg.col_vars["ts"].set(True)
        dlg.col_vars["message"].set(True)
        dlg.format_var.set("xlsx")
        out_path = os.path.join(self._tmp.name, "o.xlsx")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        self.assertEqual(export_prefs.load_format("log"), "xlsx")
        all_keys = [k for k, _ in action_log.LOG_EXPORT_COLUMNS]
        self.assertEqual(set(export_prefs.load_columns("log", all_keys)), {"ts", "message"})

    def test_reouverture_reprend_les_preferences_memorisees(self):
        dlg1 = self._open()
        for v in dlg1.col_vars.values():
            v.set(False)
        dlg1.col_vars["player"].set(True)
        dlg1.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "o.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg1._do_export()

        dlg2 = self._open()
        self.assertEqual(dlg2.format_var.get(), "pdf")
        self.assertTrue(dlg2.col_vars["player"].get())
        self.assertFalse(dlg2.col_vars["ts"].get())


# =======================================================================
# 3. Bout en bout : App._on_log_export -> LogExportDialog -> fichier
# =======================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogExportEndToEndTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_e2e_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        al_patcher = patch.object(action_log, "_log_path", return_value=self.db_path)
        self.addCleanup(al_patcher.stop)
        al_patcher.start()
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        ep_patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(ep_patcher.stop)
        ep_patcher.start()
        open_patcher = patch("main.open_file_with_default_app")
        self.addCleanup(open_patcher.stop)
        open_patcher.start()

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
        for name in (
            "_build_log_tab", "_build_log_search_criteria_placeholder",
            "_refresh_log_tab", "_refresh_log_filter_choices",
            "_parse_log_date_field", "_current_log_filter_values", "_populate_log_tree",
            "_on_log_search", "_on_log_reset", "_on_log_export",
            "_on_log_row_double_click", "_show_log_detail_window",
            "_show_log_date_picker", "_confirm_log_purge", "_log_purge_filter_summary",
            "_on_log_purge",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in ("_build_log_tournament_label_maps", "_format_log_ts", "_log_count_label_text"):
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

    def test_export_bout_en_bout_respecte_le_filtre_affiche(self):
        self._log(player_name="Alice", user_name="Raj")
        self._log(player_name="Bob", user_name="Marie", role="DIRTO", category="clock", action="toggle_pause")
        self.win._build_log_tab()

        self.win.log_user_var.set("Marie")
        self.win._on_log_search()

        opened_dialogs = []
        real_dialog_cls = main.LogExportDialog

        def capture_dialog(master, rows, criteria_line=None):
            dlg = real_dialog_cls(master, rows, criteria_line)
            opened_dialogs.append(dlg)
            return dlg

        with patch("main.LogExportDialog", side_effect=capture_dialog):
            self.win._on_log_export()
        self.assertEqual(len(opened_dialogs), 1)
        dlg = opened_dialogs[0]
        self.assertEqual(len(dlg.rows), 1)
        self.assertEqual(dlg.rows[0]["player"], "Bob")

        out_path = os.path.join(self._tmp.name, "e2e.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()
        with open(out_path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        # 0: titre, 1: critères ("Utilisateur : Marie" -> pas la valeur
        # par défaut), 2: "Nombre d'opérations : 1", 3: en-têtes,
        # 4: Bob (revu le 2026-09-25).
        self.assertEqual(len(lines), 5)
        self.assertEqual(lines[2], "Nombre d'opérations : 1")
        self.assertIn("Bob", lines[4])
        self.assertNotIn("Alice", lines[4])
        dlg.destroy()

    def test_export_bout_en_bout_sans_plafond_respecte_lintegralite_de_laffichage(self):
        """Revu le 2026-09-25 ("CE QUI CORRESPOND AUX FILTRES = CE QUI
        EST AFFICHÉ = CE QUI EST EXPORTÉ = CE QUI PEUT ÊTRE PURGÉ") :
        l'ancien plafond de 500 lignes du tableau LOG a disparu — ce test
        garde malgré tout la vérification de l'invariant "l'export porte
        sur EXACTEMENT ce qui est actuellement affiché dans le tableau",
        qu'il s'agisse de 5, 500 ou 750 lignes (voir aussi
        VolumeImportantTest.test_750_operations pour un volume réel)."""
        for i in range(5):
            self._log(player_name=f"joueur-{i}")
        self.win._build_log_tab()

        # Simule un affichage volontairement restreint (peu importe la
        # raison réelle — il n'y a plus de plafond automatique depuis le
        # 2026-09-25) en peuplant directement le tableau avec un
        # sous-ensemble : l'export doit malgré tout suivre EXACTEMENT ce
        # sous-ensemble, jamais retourner interroger le journal complet.
        rows, _ = action_log.search_actions(limit=3)
        self.win._populate_log_tree(rows)
        self.assertEqual(len(self.win.log_tree.get_children()), 3)

        opened = []
        real_dialog_cls = main.LogExportDialog
        with patch(
            "main.LogExportDialog",
            side_effect=lambda m, r, c=None: opened.append(real_dialog_cls(m, r, c)) or opened[-1],
        ):
            self.win._on_log_export()
        self.assertEqual(len(opened[0].rows), 3, "l'export doit porter sur les lignes affichées, jamais davantage")
        opened[0].destroy()


# =======================================================================
# 3bis. Volume important (750 opérations) — chantier "CE QUI CORRESPOND
#       AUX FILTRES = CE QUI EST AFFICHÉ = CE QUI EST EXPORTÉ = CE QUI
#       PEUT ÊTRE PURGÉ" (2026-09-25) : affichage, compteur, les 3
#       exports ET Purger, bout en bout, avec les MÊMES filtres actifs
#       partout.
# =======================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class VolumeImportantTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_volume_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        al_patcher = patch.object(action_log, "_log_path", return_value=self.db_path)
        self.addCleanup(al_patcher.stop)
        al_patcher.start()
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        ep_patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(ep_patcher.stop)
        ep_patcher.start()
        open_patcher = patch("main.open_file_with_default_app")
        self.addCleanup(open_patcher.stop)
        open_patcher.start()

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
        for name in (
            "_build_log_tab", "_build_log_search_criteria_placeholder",
            "_refresh_log_tab", "_refresh_log_filter_choices",
            "_parse_log_date_field", "_current_log_filter_values", "_populate_log_tree",
            "_on_log_search", "_on_log_reset", "_on_log_export",
            "_on_log_row_double_click", "_show_log_detail_window",
            "_show_log_date_picker", "_confirm_log_purge", "_log_purge_filter_summary",
            "_on_log_purge",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in ("_build_log_tournament_label_maps", "_format_log_ts", "_log_count_label_text"):
            setattr(self.win, name, getattr(main.App, name))
        self.win._LOG_CALENDAR_MONTH_NAMES_FR = main.App._LOG_CALENDAR_MONTH_NAMES_FR

        self.addCleanup(lambda: cleanup_tk(self, "root", "win", "notebook", "log_tab"))

        # 750 opérations correspondant au filtre qui sera actif
        # (Utilisateur = Marie) + 30 hors filtre (Raj) pour vérifier que
        # rien de ce qui ne correspond pas aux filtres n'est jamais
        # affiché, exporté ou purgé par erreur.
        for i in range(750):
            action_log.log_action(
                tournament_name="Tournoi du vendredi", tournament_path="/tmp/vendredi.tournoi",
                role="DIRTO", category="eliminations", action="eliminate",
                result=action_log.RESULT_SUCCESS, user_name="Marie", player_name=f"Joueur{i}",
            )
        for i in range(30):
            action_log.log_action(
                tournament_name="Tournoi du vendredi", tournament_path="/tmp/vendredi.tournoi",
                role="ADMIN", category="clock", action="toggle_pause",
                result=action_log.RESULT_SUCCESS, user_name="Raj",
            )

    def test_750_operations_bout_en_bout_affichage_export_et_purge(self):
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()

        # 1. Les 750 sont récupérées ET affichées, aucune troncature.
        self.assertEqual(len(self.win.log_tree.get_children()), 750)

        # 2. Le compteur annonce exactement 750.
        self.assertEqual(self.win.log_count_lbl.cget("text"), "750 opérations affichées")

        # 3. Export : la fenêtre reçoit exactement les 750 lignes
        #    affichées (jamais les 780 réellement en base).
        opened = []
        real_dialog_cls = main.LogExportDialog
        with patch(
            "main.LogExportDialog",
            side_effect=lambda m, r, c=None: opened.append(real_dialog_cls(m, r, c)) or opened[-1],
        ):
            self.win._on_log_export()
        dlg = opened[0]
        self.assertEqual(len(dlg.rows), 750)
        self.assertIn("Utilisateur : Marie", dlg.criteria_line)

        # Les 3 formats, appelés avec exactement les mêmes rows/critères
        # que la fenêtre a capturés (comme le ferait réellement
        # LogExportDialog._do_export pour chacun des 3 boutons radio) :
        # chacun doit contenir exactement 750 opérations et annoncer
        # "Nombre d'opérations : 750".
        csv_path = os.path.join(self._tmp.name, "export750.csv")
        action_log.export_csv(
            csv_path, action_log.LOG_EXPORT_COLUMNS, dlg.rows,
            title="Journal des actions", criteria_line=dlg.criteria_line,
        )
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            csv_lines = f.read().splitlines()
        self.assertIn("Nombre d'opérations : 750", csv_lines)
        # titre + critères + nombre + en-têtes = 4 lignes avant les données.
        self.assertEqual(len(csv_lines) - 4, 750)

        xlsx_path = os.path.join(self._tmp.name, "export750.xlsx")
        action_log.export_xlsx(
            xlsx_path, action_log.LOG_EXPORT_COLUMNS, dlg.rows,
            title="Journal des actions", criteria_line=dlg.criteria_line,
        )
        from openpyxl import load_workbook
        wb = load_workbook(xlsx_path)
        ws = wb.active
        self.assertEqual(ws.cell(row=3, column=1).value, "Nombre d'opérations : 750")
        # max_row = 5 lignes d'en-tête (titre/critères/nombre/vide/en-têtes) + 750 données.
        self.assertEqual(ws.max_row - 5, 750)
        wb.close()

        if _FITZ_AVAILABLE:
            pdf_path = os.path.join(self._tmp.name, "export750.pdf")
            action_log.export_pdf(
                pdf_path, action_log.LOG_EXPORT_COLUMNS, dlg.rows,
                title="Journal des actions", criteria_line=dlg.criteria_line,
            )
            text = _pdf_extract_text(pdf_path)
            self.assertIn("Nombre d'opérations : 750", text)
            self.assertIn("Utilisateur : Marie", text)

        dlg.destroy()

        # 4. Purger, avec les MÊMES filtres (Utilisateur = Marie, dates
        #    vides) : compte et supprime exactement ces 750 opérations,
        #    laisse les 30 de Raj intactes.
        with patch.object(self.win, "_confirm_log_purge", return_value=True) as mock_confirm, \
             patch("main.messagebox.showinfo") as mock_info:
            self.win._on_log_purge()
        mock_confirm.assert_called_once()
        self.assertEqual(mock_confirm.call_args[0][2], 750, "le compte annoncé doit être exactement 750")
        message = mock_info.call_args[0][1]
        self.assertIn("750", message)

        remaining, _ = action_log.search_actions()
        self.assertEqual(len(remaining), 30)
        self.assertTrue(all(r["user_name"] == "Raj" for r in remaining))


# =======================================================================
# 4. Diagnostic du 2026-09-24 : export PDF silencieusement en échec sur
#    tout caractère hors latin-1 — voir la docstring de module.
# =======================================================================
class PdfTextSanitizationTest(unittest.TestCase):
    """action_log._pdf_text directement — pas de Tk, pas de fichier."""

    def test_symbole_pause_ne_leve_pas_et_reste_lisible(self):
        result = action_log._pdf_text("Onglet Joueurs (⏸)")
        self.assertNotIn("⏸", result)
        self.assertIn("Onglet Joueurs (", result)
        self.assertTrue(all(ord(c) <= 0xFF for c in result), "le résultat doit être entièrement latin-1")

    def test_accents_francais_preserves_a_lidentique(self):
        text = "café à la crème, ça déçoit, être, Élimination"
        self.assertEqual(action_log._pdf_text(text), text)

    def test_symbole_euro_remplace_par_eur(self):
        self.assertEqual(action_log._pdf_text("Montant : 50€"), "Montant : 50EUR")

    def test_tiret_cadratin_remplace_par_tiret_simple(self):
        self.assertEqual(action_log._pdf_text("Refus — désignez un éliminateur"), "Refus - désignez un éliminateur")

    def test_emoji_ne_leve_pas_et_devient_un_caractere_de_repli(self):
        result = action_log._pdf_text("🎉 Bravo 🎉")
        self.assertNotIn("🎉", result)
        self.assertIn("Bravo", result)
        self.assertTrue(all(ord(c) <= 0xFF for c in result))

    def test_aucun_caractere_dorigine_nest_silencieusement_perdu(self):
        """Chaque caractère d'entrée produit AU MOINS un caractère de
        sortie (jamais de suppression pure) — même les caractères
        irréductibles (emoji) deviennent "?", jamais rien."""
        text = "A🎉B⏸C"
        result = action_log._pdf_text(text)
        self.assertGreaterEqual(len(result), 3)  # au moins A, B, C
        self.assertIn("A", result)
        self.assertIn("B", result)
        self.assertIn("C", result)

    def test_valeur_none_renvoie_chaine_vide(self):
        self.assertEqual(action_log._pdf_text(None), "")

    def test_ne_leve_jamais_quel_que_soit_le_caractere(self):
        """Balayage large : aucun point de code, même exotique, ne doit
        faire lever _pdf_text."""
        sample_codepoints = [
            0x23F8,   # ⏸ pause
            0x1F389,  # 🎉 emoji (hors plan de base)
            0x03A9,   # Ω grec
            0x4E2D,   # 中 sinogramme
            0x0301,   # marque combinante isolée
            0x2014,   # — tiret cadratin
            0x20AC,   # € euro
        ]
        for cp in sample_codepoints:
            with self.subTest(codepoint=hex(cp)):
                ch = chr(cp)
                result = action_log._pdf_text(f"test{ch}fin")
                self.assertTrue(all(ord(c) <= 0xFF for c in result))


class PdfExportUnicodeRobustnessTest(unittest.TestCase):
    """action_log.export_pdf avec des données représentatives d'un LOG
    réel contenant des caractères hors latin-1 — reproduction directe du
    bug diagnostiqué (aucune exception, fichier PDF réellement créé)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_pdf_unicode_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_action_elimination_le_cas_exact_du_diagnostic(self):
        """Reproduction EXACTE du bug signalé : une ligne avec
        action="elimination" (libellé "Onglet Joueurs (⏸)", jamais
        modifié — demande explicite de le conserver tel quel)."""
        rows = [{
            "ts": "24/09/2026 14:20:00", "tournament": "Tournoi du vendredi", "user": "Raj",
            "role": "ADMIN", "category": action_log.category_label("admin_only"),
            "action": action_log.action_label("elimination"),
            "player": "", "result": "Accepté", "message": "",
        }]
        path = os.path.join(self._tmp.name, "elimination.pdf")
        # Ne doit lever AUCUNE exception.
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, rows, title="Journal des actions")
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")
        self.assertGreater(os.path.getsize(path), 0)

    def test_emoji_dans_nom_de_joueur_et_message(self):
        rows = [{
            "ts": "24/09/2026 10:00:00", "tournament": "Tournoi du vendredi", "user": "Raj",
            "role": "ADMIN", "category": "Éliminations", "action": "Élimination",
            "player": "🎉Alice🎉", "result": "Réussi",
            "message": "Bravo 🏆 ! Café à la crème — 50€ … “bonus” ‘go’.",
        }]
        path = os.path.join(self._tmp.name, "emoji.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, rows)
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")

    def test_combinaison_de_toutes_les_colonnes_avec_caracteres_varies(self):
        rows = [
            {
                "ts": "24/09/2026 14:32:18", "tournament": "Tournoi d'été — édition 2026",
                "user": "Raj", "role": "ADMIN", "category": "Éliminations", "action": "Élimination",
                "player": "Nguyễn Văn A", "result": "Réussi", "message": "café, thé, €50, — fin —",
            },
            {
                "ts": "24/09/2026 14:20:00", "tournament": "Tournoi d'été — édition 2026",
                "user": "Marie", "role": "DIRTO", "category": "Administration",
                "action": action_log.action_label("elimination"),
                "player": "", "result": "Accepté", "message": "",
            },
        ]
        path = os.path.join(self._tmp.name, "combined.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, rows, title="Journal des actions")
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as f:
            self.assertEqual(f.read(5), b"%PDF-")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogExportDialogErrorHandlingTest(LogExportDialogGuiTestCase):
    """LogExportDialog._do_export — gestion des exceptions d'export
    AUTRES qu'ImportError (correction du 2026-09-24)."""

    def test_pdf_reproduit_le_bug_diagnostique_puis_reussit_apres_correction(self):
        """Bout en bout, SANS aucun mock d'action_log.export_pdf : la
        vraie fonction, avec la vraie donnée qui plantait avant le
        correctif de _pdf_text."""
        rows = [{
            "ts": "24/09/2026 14:20:00", "tournament": "Tournoi du vendredi", "user": "Raj",
            "role": "ADMIN", "category": "Administration",
            "action": action_log.action_label("elimination"),
            "player": "", "result": "Accepté", "message": "",
        }]
        dlg = self._open(rows)
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "diagnostic.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path):
            dlg._do_export()  # ne doit lever aucune exception
        self.assertTrue(os.path.exists(out_path), "le PDF doit être réellement créé")
        self.assertFalse(dlg.winfo_exists(), "la fenêtre se ferme après un export réussi")

    def test_erreur_pdf_affiche_un_message_et_ne_ferme_pas_la_fenetre(self):
        dlg = self._open()
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "should_not_exist.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_pdf", side_effect=RuntimeError("panne simulée")), \
             patch("main.messagebox.showerror") as mock_err, \
             patch("main._log_exception") as mock_log:
            dlg._do_export()
        mock_err.assert_called_once()
        self.assertIn("panne simulée", mock_err.call_args[0][1])
        self.assertTrue(dlg.winfo_exists(), "la fenêtre doit rester ouverte après un échec")
        self.assertFalse(os.path.exists(out_path), "aucun fichier ne doit exister après un échec")
        mock_log.assert_called_once()

    def test_erreur_pdf_ne_tente_pas_douvrir_le_fichier(self):
        dlg = self._open()
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "x.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_pdf", side_effect=RuntimeError("panne")), \
             patch("main.messagebox.showerror"), \
             patch("main._log_exception"), \
             patch("main.open_file_with_default_app") as mock_open:
            dlg._do_export()
        mock_open.assert_not_called()

    def test_erreur_xlsx_meme_comportement_que_pdf(self):
        """Non-régression explicite : la gestion générique d'erreur
        s'applique aussi au format Excel, pas seulement PDF."""
        dlg = self._open()
        dlg.format_var.set("xlsx")
        out_path = os.path.join(self._tmp.name, "x.xlsx")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_xlsx", side_effect=RuntimeError("panne xlsx")), \
             patch("main.messagebox.showerror") as mock_err, \
             patch("main._log_exception") as mock_log:
            dlg._do_export()
        mock_err.assert_called_once()
        self.assertTrue(dlg.winfo_exists())
        mock_log.assert_called_once()

    def test_erreur_csv_meme_comportement(self):
        dlg = self._open()
        dlg.format_var.set("csv")
        out_path = os.path.join(self._tmp.name, "x.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_csv", side_effect=RuntimeError("panne csv")), \
             patch("main.messagebox.showerror") as mock_err, \
             patch("main._log_exception") as mock_log:
            dlg._do_export()
        mock_err.assert_called_once()
        self.assertTrue(dlg.winfo_exists())
        mock_log.assert_called_once()

    def test_importerror_toujours_gere_specifiquement(self):
        """Non-régression : le traitement spécifique d'ImportError
        (message "module manquant") doit rester distinct de la
        nouvelle gestion générique — jamais remplacé par elle."""
        dlg = self._open()
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "x.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_pdf", side_effect=ImportError("fpdf2 manquant")), \
             patch("main.show_missing_export_module") as mock_missing, \
             patch("main.messagebox.showerror") as mock_err:
            dlg._do_export()
        mock_missing.assert_called_once_with("pdf")
        mock_err.assert_not_called()
        self.assertTrue(dlg.winfo_exists())

    def test_succes_csv_xlsx_pdf_ferme_toujours_la_fenetre_et_ouvre_le_fichier(self):
        """Non-régression du comportement de succès (les 3 formats) —
        la correction de gestion d'erreur ne doit rien changer au
        chemin normal, déjà couvert ailleurs mais reconfirmé ici pour
        les 3 formats d'un coup, dans le même test que les erreurs."""
        for fmt, ext in (("csv", ".csv"), ("xlsx", ".xlsx"), ("pdf", ".pdf")):
            with self.subTest(format=fmt):
                dlg = self._open()
                dlg.format_var.set(fmt)
                out_path = os.path.join(self._tmp.name, f"succes{ext}")
                with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
                     patch("main.open_file_with_default_app") as mock_open:
                    dlg._do_export()
                self.assertTrue(os.path.exists(out_path))
                self.assertFalse(dlg.winfo_exists())
                mock_open.assert_called_once_with(out_path)


# =======================================================================
# 5. Ligne "Critères : ..." du PDF (demande du 2026-09-24)
# =======================================================================
class FormatLogExportCriteriaTest(unittest.TestCase):
    """action_log.format_log_export_criteria — pas de Tk, pas de fichier."""

    def test_aucun_filtre_renvoie_toutes_les_actions(self):
        self.assertEqual(action_log.format_log_export_criteria(), "Critères : Toutes les actions")

    def test_tous_les_defauts_explicites_renvoie_toutes_les_actions(self):
        self.assertEqual(
            action_log.format_log_export_criteria(
                date_from=None, date_to=None, tournament_label="Tous",
                user_label="Tous", function_label="Toutes", player_label="Tous",
            ),
            "Critères : Toutes les actions",
        )

    def test_exemple_exact_de_la_demande(self):
        result = action_log.format_log_export_criteria(date_from="01/09/2026", date_to="24/09/2026")
        self.assertEqual(
            result,
            "Critères : Du 01/09/2026 au 24/09/2026 — Tournoi : Tous — "
            "Utilisateur : Tous — Fonction : Toutes — Joueur : Tous",
        )

    def test_du_seul_sans_au(self):
        result = action_log.format_log_export_criteria(date_from="01/09/2026")
        self.assertIn("Du 01/09/2026", result)
        self.assertNotIn(" au ", result)

    def test_au_seul_sans_du(self):
        result = action_log.format_log_export_criteria(date_to="24/09/2026")
        self.assertIn("Jusqu'au 24/09/2026", result)

    def test_chaine_vide_traitee_comme_champ_vide(self):
        # "" doit se comporter exactement comme None (champ non rempli).
        self.assertEqual(
            action_log.format_log_export_criteria(date_from="", date_to=""),
            "Critères : Toutes les actions",
        )

    def test_seul_le_tournoi_est_filtre(self):
        result = action_log.format_log_export_criteria(tournament_label="Tournoi du vendredi")
        self.assertEqual(
            result,
            "Critères : Tournoi : Tournoi du vendredi — Utilisateur : Tous — "
            "Fonction : Toutes — Joueur : Tous",
        )

    def test_tous_les_6_criteres_actifs_simultanement(self):
        result = action_log.format_log_export_criteria(
            date_from="01/09/2026", date_to="24/09/2026", tournament_label="Tournoi A",
            user_label="Marie", function_label="Chronomètre", player_label="Alice",
        )
        self.assertEqual(
            result,
            "Critères : Du 01/09/2026 au 24/09/2026 — Tournoi : Tournoi A — "
            "Utilisateur : Marie — Fonction : Chronomètre — Joueur : Alice",
        )

    def test_tournoi_utilisateur_fonction_joueur_toujours_presents_meme_par_defaut(self):
        """Même quand seules les dates sont filtrées, les 4 autres
        critères restent affichés à leur valeur par défaut (voir
        l'exemple de la demande : "Tournoi : Tous" y figure malgré
        tout)."""
        result = action_log.format_log_export_criteria(date_from="01/09/2026", date_to="24/09/2026")
        for segment in ("Tournoi : Tous", "Utilisateur : Tous", "Fonction : Toutes", "Joueur : Tous"):
            self.assertIn(segment, result)


@unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) non installé — dépendance de TEST uniquement")
class ExportPdfCriteriaLineContentTest(unittest.TestCase):
    """Vérifie le CONTENU RÉEL du PDF produit (texte extrait via
    PyMuPDF) — pas seulement que export_pdf ne lève pas."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_pdf_criteria_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_ligne_toutes_les_actions_presente_si_aucun_filtre(self):
        path = os.path.join(self._tmp.name, "out.pdf")
        criteria = action_log.format_log_export_criteria()
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS, criteria_line=criteria)
        text = _pdf_extract_text(path)
        self.assertIn("Critères : Toutes les actions", text)

    def test_ligne_criteres_correspond_a_lexemple_de_la_demande(self):
        path = os.path.join(self._tmp.name, "out.pdf")
        criteria = action_log.format_log_export_criteria(date_from="01/09/2026", date_to="24/09/2026")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS, criteria_line=criteria)
        text = _pdf_extract_text(path)
        # "—" est lui-même passé par _pdf_text (protection Unicode) et
        # devient "-" dans le PDF final — recherché tel quel ici.
        self.assertIn("Crit", text)
        self.assertIn("Du 01/09/2026 au 24/09/2026", text)
        self.assertIn("Tournoi : Tous", text)
        self.assertIn("Utilisateur : Tous", text)
        self.assertIn("Fonction : Toutes", text)
        self.assertIn("Joueur : Tous", text)

    def test_aucune_ligne_dessinee_si_criteria_line_absent(self):
        path = os.path.join(self._tmp.name, "out.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS)
        text = _pdf_extract_text(path)
        self.assertNotIn("Critères", text)

    def test_aucune_ligne_dessinee_si_criteria_line_vide(self):
        path = os.path.join(self._tmp.name, "out.pdf")
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS, criteria_line="")
        text = _pdf_extract_text(path)
        self.assertNotIn("Critères", text)

    def test_titre_toujours_present_avec_ou_sans_criteres(self):
        path = os.path.join(self._tmp.name, "out.pdf")
        action_log.export_pdf(
            path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS,
            title="Journal des actions", criteria_line="Critères : Toutes les actions",
        )
        text = _pdf_extract_text(path)
        self.assertIn("Journal des actions", text)

    def test_criteres_avec_caracteres_hors_latin1_ne_font_pas_planter(self):
        """Le texte des critères doit lui aussi passer par la protection
        Unicode du PDF (demande explicite) — testé avec le symbole ⏸,
        un emoji et un tiret cadratin dans un libellé de filtre."""
        path = os.path.join(self._tmp.name, "out.pdf")
        criteria = "Critères : Tournoi : Café — Édition 2026 🎉 (⏸)"
        # Ne doit lever AUCUNE exception.
        action_log.export_pdf(path, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS, criteria_line=criteria)
        text = _pdf_extract_text(path)
        self.assertNotIn("🎉", text)
        self.assertNotIn("⏸", text)
        self.assertIn("Café", text)
        self.assertIn("Tournoi : Café", text)

    def test_tableau_inchange_par_lajout_des_criteres(self):
        """"Ne change pas le contenu du tableau" (demande explicite) : le
        texte extrait est IDENTIQUE avec ou sans ligne de critères, à
        l'exception de cette seule ligne en plus — comparaison directe
        plutôt qu'une recherche de sous-chaînes (une valeur peut être
        repliée sur plusieurs lignes par le retour à la ligne
        automatique d'une colonne étroite, ex. "24/09/2026 14:32:18"
        wrappé en deux lignes ; comparer le texte entier évite ce faux
        négatif tout en restant une vérification stricte)."""
        path_avec = os.path.join(self._tmp.name, "avec.pdf")
        path_sans = os.path.join(self._tmp.name, "sans.pdf")
        action_log.export_pdf(
            path_avec, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS,
            criteria_line="Critères : Toutes les actions",
        )
        action_log.export_pdf(path_sans, action_log.LOG_EXPORT_COLUMNS, _SAMPLE_ROWS)
        text_avec = _pdf_extract_text(path_avec)
        text_sans = _pdf_extract_text(path_sans)
        self.assertEqual(text_avec.replace("Critères : Toutes les actions\n", ""), text_sans)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogExportCriteriaWiringTest(LogExportDialogGuiTestCase):
    """Câblage main.py : App._on_log_export construit la ligne de
    critères depuis les filtres actifs. Revu le 2026-09-25 ("les 3
    formats doivent afficher les mêmes critères et le même nombre
    d'opérations") : LogExportDialog._do_export la transmet désormais
    aux 3 fonctions action_log.export_csv/export_xlsx/export_pdf,
    jamais plus seulement à export_pdf."""

    def test_do_export_transmet_criteria_line_a_export_pdf(self):
        dlg = self._open(criteria_line="Critères : Toutes les actions")
        dlg.format_var.set("pdf")
        out_path = os.path.join(self._tmp.name, "o.pdf")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_pdf") as mock_pdf:
            dlg._do_export()
        self.assertEqual(mock_pdf.call_args[1]["criteria_line"], "Critères : Toutes les actions")

    def test_export_csv_recoit_aussi_criteria_line(self):
        dlg = self._open(criteria_line="Critères : Toutes les actions")
        dlg.format_var.set("csv")
        out_path = os.path.join(self._tmp.name, "o.csv")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_csv") as mock_csv:
            dlg._do_export()
        self.assertEqual(mock_csv.call_args[1]["criteria_line"], "Critères : Toutes les actions")

    def test_export_xlsx_recoit_aussi_criteria_line(self):
        dlg = self._open(criteria_line="Critères : Toutes les actions")
        dlg.format_var.set("xlsx")
        out_path = os.path.join(self._tmp.name, "o.xlsx")
        with patch("main.filedialog.asksaveasfilename", return_value=out_path), \
             patch("action_log.export_xlsx") as mock_xlsx:
            dlg._do_export()
        self.assertEqual(mock_xlsx.call_args[1]["criteria_line"], "Critères : Toutes les actions")

    def test_criteria_line_none_par_defaut(self):
        """Construction sans 3e argument (rétro-compatibilité) : aucune
        ligne n'est transmise à export_pdf."""
        dlg = main.LogExportDialog(self.root, list(_SAMPLE_ROWS))
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        self.assertIsNone(dlg.criteria_line)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class OnLogExportBuildsCriteriaLineTest(unittest.TestCase):
    """App._on_log_export — construction de la ligne de critères depuis
    les widgets de filtre RÉELLEMENT affichés (même harnais que tests/
    test_log_tab_functional.py)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_export_criteria_wiring_test_")
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
        for name in (
            "_build_log_tab", "_build_log_search_criteria_placeholder",
            "_refresh_log_tab", "_refresh_log_filter_choices",
            "_parse_log_date_field", "_current_log_filter_values", "_populate_log_tree",
            "_on_log_search", "_on_log_reset", "_on_log_export",
            "_on_log_row_double_click", "_show_log_detail_window",
            "_show_log_date_picker", "_confirm_log_purge", "_log_purge_filter_summary",
            "_on_log_purge",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in ("_build_log_tournament_label_maps", "_format_log_ts", "_log_count_label_text"):
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

    def test_sans_filtre_transmet_toutes_les_actions(self):
        self._log(player_name="Alice")
        self.win._build_log_tab()
        with patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        criteria_line = mock_dialog.call_args[0][2]
        self.assertEqual(criteria_line, "Critères : Toutes les actions")

    def test_avec_dates_transmet_la_ligne_correspondante(self):
        # Dates DYNAMIQUES (corrigé le 2026-09-25 — les bornes fixes
        # "01/09/2026"/"24/09/2026" ne couvraient plus la ligne journalisée
        # ci-dessous une fois cette date dépassée dans le calendrier réel,
        # log_action horodatant toujours avec le vrai datetime.now(), non
        # simulable) : "Du" largement avant maintenant, "Au" = aujourd'hui,
        # pour rester valide quel que soit le jour d'exécution du test.
        self._log(player_name="Alice")
        self.win._build_log_tab()
        date_from = (datetime.now() - timedelta(days=30)).strftime("%d/%m/%Y")
        date_to = datetime.now().strftime("%d/%m/%Y")
        self.win.log_date_from_var.set(date_from)
        self.win.log_date_to_var.set(date_to)
        self.win._on_log_search()
        with patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        criteria_line = mock_dialog.call_args[0][2]
        self.assertEqual(
            criteria_line,
            f"Critères : Du {date_from} au {date_to} — Tournoi : Tous — "
            "Utilisateur : Tous — Fonction : Toutes — Joueur : Tous",
        )

    def test_avec_filtre_utilisateur_transmet_le_bon_libelle(self):
        self._log(player_name="Alice", user_name="Raj")
        self._log(player_name="Bob", user_name="Marie", role="DIRTO")
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        with patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        criteria_line = mock_dialog.call_args[0][2]
        self.assertIn("Utilisateur : Marie", criteria_line)

    def test_criteres_coherents_avec_les_lignes_exportees(self):
        """Les critères transmis décrivent exactement le filtre qui a
        produit les lignes également transmises — jamais désynchronisés."""
        self._log(player_name="Alice", user_name="Raj")
        self._log(player_name="Bob", user_name="Marie", role="DIRTO")
        self.win._build_log_tab()
        self.win.log_user_var.set("Marie")
        self.win._on_log_search()
        with patch("main.LogExportDialog") as mock_dialog:
            self.win._on_log_export()
        rows = mock_dialog.call_args[0][1]
        criteria_line = mock_dialog.call_args[0][2]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player"], "Bob")
        self.assertIn("Utilisateur : Marie", criteria_line)


if __name__ == "__main__":
    unittest.main()
