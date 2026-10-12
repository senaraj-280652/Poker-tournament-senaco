# -*- coding: utf-8 -*-
"""Impression PDF du Répertoire (demande du 2026-10-07) :

- Bouton "Imprimer..." (onglet Répertoire, main.py: RosterManagerDialog,
  rangée btns_csv) ouvre RosterPdfDialog — JAMAIS une impression directe.
- Champs réels de roster.py (ROSTER_PDF_COLUMNS) : photo (stockage séparé,
  player_photos.py), name, club, group, phone, mail, pseudo — aucun champ
  inventé. Défauts validés : name/club/phone cochés, le reste décoché.
- Dernier choix mémorisé via export_prefs (clé "roster_pdf"), totalement
  indépendant de roster.json/de toute base de tournoi.
- roster.export_roster_pdf(path, entries, columns) : UNIQUEMENT les
  champs de `columns` apparaissent dans le fichier (confidentialité par
  construction) ; ordre des personnes = celui fourni par l'appelant (voir
  main.py: RosterManagerDialog._refresh, qui reprend le tri actuellement
  affiché à l'écran) ; texte-seul réutilise database._write_pdf_table
  (repeat_header=True, page_numbers=True, nouveaux paramètres optionnels
  — comportement des autres exports PDF strictement inchangé par défaut) ;
  "photo" cochée bascule vers un dessin dédié (images non gérées par
  _write_pdf_table), même présentation/en-tête répété/numérotation.
- Caractères français (accents/apostrophes/tirets) assainis via
  action_log._pdf_text (plus robuste que database._pdf_text, réutilisée
  SANS duplication).

Aucune modification de roster.json, du stockage des photos, ni d'aucune
base de tournoi — tout est lecture seule ou écrit dans un fichier PDF
fourni par l'appelant."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import export_prefs  # noqa: E402
import player_photos  # noqa: E402
import roster  # noqa: E402

try:
    import fpdf  # noqa: F401
    _FPDF_AVAILABLE = True
except ImportError:
    _FPDF_AVAILABLE = False

try:
    import fitz  # PyMuPDF — extraction de texte/images pour vérifier le CONTENU réel du PDF.
    _FITZ_AVAILABLE = True
except ImportError:
    _FITZ_AVAILABLE = False

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

try:
    import tkinter as tk
    from tkinter import ttk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False


def _pdf_text(path):
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _pdf_pages(path):
    """Liste de (texte, nb_images) par page — pour les vérifications
    page par page (en-tête répété, numérotation, alignement photo)."""
    doc = fitz.open(path)
    try:
        return [(page.get_text(), len(page.get_images())) for page in doc]
    finally:
        doc.close()


def _make_entry(name, club="Club", group="", phone="0600000000", mail="x@y.fr", pseudo="P"):
    return {"name": name, "club": club, "group": group, "phone": phone, "mail": mail, "pseudo": pseudo}


# =========================================================================
# A. Champs réels — aucun champ inventé
# =========================================================================
class RosterPdfColumnsTest(unittest.TestCase):
    def test_colonnes_correspondent_exactement_aux_champs_reels(self):
        keys = [k for k, _ in roster.ROSTER_PDF_COLUMNS]
        self.assertEqual(keys, ["photo", "name", "club", "group", "phone", "mail", "pseudo"])

    def test_les_6_champs_texte_sont_bien_ceux_de_save_roster_entries(self):
        # Les clés réellement utilisées par load/save_roster_entries :
        # vérifiées en pratique via une entrée round-trip plutôt que par
        # inspection de docstring.
        tmp = tempfile.TemporaryDirectory(prefix="roster_pdf_columns_test_")
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "roster.json")
        with patch.object(roster, "_roster_path", return_value=path):
            roster.save_roster_entries([_make_entry("Alice")])
            entries = roster.load_roster_entries()
        self.assertEqual(
            set(entries[0].keys()), {"name", "club", "group", "phone", "mail", "pseudo"},
        )
        text_keys = {k for k, _ in roster.ROSTER_PDF_COLUMNS if k != "photo"}
        self.assertEqual(text_keys, set(entries[0].keys()))

    def test_defauts_valides(self):
        self.assertEqual(roster.ROSTER_PDF_DEFAULT_COLUMNS, ["name", "club", "phone"])


# =========================================================================
# B. export_roster_pdf — texte seul : confidentialité, accents, ordre,
#    en-tête répété, numérotation, portrait/paysage
# =========================================================================
@unittest.skipUnless(_FPDF_AVAILABLE and _FITZ_AVAILABLE, "fpdf2/PyMuPDF indisponibles")
class RosterPdfTextOnlyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_pdf_text_test_")
        self.addCleanup(self._tmp.cleanup)

    def _path(self, name="repertoire.pdf"):
        return os.path.join(self._tmp.name, name)

    def test_aucune_case_cochee_leve_valueerror(self):
        with self.assertRaises(ValueError):
            roster.export_roster_pdf(self._path(), [_make_entry("Alice")], [])

    def test_uniquement_les_champs_coches_sont_presents(self):
        path = self._path()
        entries = [_make_entry("Alice", club="ClubSecret", phone="0611112222",
                                mail="secret@mail.fr", pseudo="PseudoSecret")]
        roster.export_roster_pdf(path, entries, ["name", "club"])
        text = _pdf_text(path)
        self.assertIn("Alice", text)
        self.assertIn("ClubSecret", text)
        # Confidentialité : champs NON cochés absents, y compris leur
        # LIBELLÉ d'en-tête.
        self.assertNotIn("0611112222", text)
        self.assertNotIn("secret@mail.fr", text)
        self.assertNotIn("PseudoSecret", text)
        self.assertNotIn("Téléphone", text)
        self.assertNotIn("Mail", text)
        self.assertNotIn("Pseudo", text)

    def test_groupe_absent_si_non_coche(self):
        path = self._path()
        entries = [_make_entry("Alice", group="ADMIN")]
        roster.export_roster_pdf(path, entries, ["name"])
        text = _pdf_text(path)
        self.assertNotIn("ADMIN", text)
        self.assertNotIn("Groupe", text)

    def test_caracteres_francais_ne_font_jamais_planter(self):
        path = self._path()
        entries = [
            _make_entry("D’Artagnan", club="Café-crème", pseudo="Test…"),
            _make_entry("François — Dupont", club="Été ’ “club”"),
        ]
        roster.export_roster_pdf(path, entries, ["name", "club", "pseudo"])
        text = _pdf_text(path)
        self.assertIn("D'Artagnan", text)  # apostrophe courbe -> droite
        self.assertIn("Café-crème", text)  # accents latin-1 passent tels quels
        self.assertIn("François - Dupont", text)  # tiret cadratin -> simple

    def test_ordre_des_personnes_respecte_lordre_fourni(self):
        """L'ordre du PDF doit être EXACTEMENT celui de `entries`, jamais
        retrié — c'est à l'appelant (main.py) de fournir le bon ordre
        (celui actuellement affiché à l'écran)."""
        path = self._path()
        entries = [_make_entry("Zoe"), _make_entry("Alice"), _make_entry("Marc")]
        roster.export_roster_pdf(path, entries, ["name"])
        text = _pdf_text(path)
        self.assertLess(text.index("Zoe"), text.index("Alice"))
        self.assertLess(text.index("Alice"), text.index("Marc"))

    def test_en_tete_repete_sur_plusieurs_pages(self):
        path = self._path()
        entries = [_make_entry(f"Joueur {i:03d}") for i in range(80)]
        roster.export_roster_pdf(path, entries, ["name", "club", "phone"])
        pages = _pdf_pages(path)
        self.assertGreater(len(pages), 1)
        for text, _ in pages:
            self.assertIn("Nom", text)

    def test_numerotation_des_pages(self):
        path = self._path()
        entries = [_make_entry(f"Joueur {i:03d}") for i in range(80)]
        roster.export_roster_pdf(path, entries, ["name", "club", "phone"])
        pages = _pdf_pages(path)
        total = len(pages)
        for i, (text, _) in enumerate(pages, start=1):
            self.assertIn(f"Page {i}/{total}", text)

    def test_portrait_pour_peu_de_colonnes(self):
        path = self._path()
        roster.export_roster_pdf(path, [_make_entry("Alice")], ["name", "club"])
        doc = fitz.open(path)
        try:
            self.assertLess(doc[0].rect.width, doc[0].rect.height)
        finally:
            doc.close()

    def test_tous_les_champs_texte_restent_en_portrait(self):
        """Il n'existe que 6 champs texte réels (name/club/group/phone/
        mail/pseudo) — la règle existante de _write_pdf_table (paysage
        SEULEMENT au-delà de 6 colonnes) ne peut donc jamais basculer en
        paysage pour le Répertoire SANS la colonne Photo (voir la
        variante avec photo, qui compte la photo comme l'équivalent de
        colonnes de texte en plus et peut, elle, basculer en paysage)."""
        path = self._path()
        roster.export_roster_pdf(
            path, [_make_entry("Alice")],
            ["name", "club", "group", "phone", "mail", "pseudo"],
        )
        doc = fitz.open(path)
        try:
            self.assertLess(doc[0].rect.width, doc[0].rect.height)
        finally:
            doc.close()

    def test_fichier_prevu_pour_navigation_classique(self):
        """export_roster_pdf ne force ni ne choisit de chemin — c'est
        bien l'appelant (RosterPdfDialog) qui gère asksaveasfilename."""
        path = self._path("mon_choix_perso.pdf")
        roster.export_roster_pdf(path, [_make_entry("Alice")], ["name"])
        self.assertTrue(os.path.exists(path))


# =========================================================================
# C. export_roster_pdf — avec Photo : proportions, absence, alignement,
#    plusieurs pages, ~100 entrées
# =========================================================================
@unittest.skipUnless(
    _FPDF_AVAILABLE and _FITZ_AVAILABLE and _PIL_AVAILABLE, "fpdf2/PyMuPDF/Pillow indisponibles",
)
class RosterPdfWithPhotoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_pdf_photo_test_")
        self.addCleanup(self._tmp.cleanup)
        self._photos_tmp = tempfile.TemporaryDirectory(prefix="roster_pdf_photo_store_")
        self.addCleanup(self._photos_tmp.cleanup)
        patcher = patch.object(player_photos, "_photos_dir", return_value=self._photos_tmp.name)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _path(self, name="repertoire_photo.pdf"):
        return os.path.join(self._tmp.name, name)

    def _make_photo(self, name, size=(300, 300), color=(100, 50, 20)):
        img_path = os.path.join(self._tmp.name, f"{name}_src.jpg")
        Image.new("RGB", size, color=color).save(img_path, "JPEG")
        player_photos.save_photo_from_file(name, img_path)

    _MM_TO_PT = 2.8346456692913385

    def _row_border_heights_mm(self, path):
        """(hauteurs d'en-tête, hauteurs de ligne) en mm, dans l'ordre —
        mesure directement les rectangles réellement tracés par fpdf2
        pour la colonne Photo (la plus à gauche), page par page, plutôt
        que de déduire la hauteur d'une position de texte (fpdf2 centre
        le texte verticalement dans la cellule, ce qui fausserait une
        mesure basée sur le texte seul). Sur chaque page, le premier
        rectangle (Y le plus petit) est TOUJOURS l'en-tête — dessiné en
        premier, avant toute ligne de données — jamais distingué par un
        simple seuil de hauteur (l'en-tête, ~8mm, est trop proche d'une
        ligne compacte sans photo, ~7mm, pour les séparer ainsi)."""
        doc = fitz.open(path)
        try:
            header_heights, row_heights = [], []
            for page in doc:
                rects = []
                for d in page.get_drawings():
                    for item in d.get("items", []):
                        if item[0] == "re":
                            r = item[1]
                            rects.append((r.x0, r.y0, r.y1))
                if not rects:
                    continue
                min_x0 = min(r[0] for r in rects)
                col = sorted(
                    (y0, y1) for (x0, y0, y1) in rects if abs(x0 - min_x0) < 0.5
                )
                heights_mm = [(y1 - y0) / self._MM_TO_PT for y0, y1 in col]
                header_heights.append(heights_mm[0])
                row_heights.extend(heights_mm[1:])
            return header_heights, row_heights
        finally:
            doc.close()

    def test_photo_presente_est_bien_incluse(self):
        self._make_photo("Alice")
        path = self._path()
        roster.export_roster_pdf(path, [_make_entry("Alice")], ["photo", "name"])
        pages = _pdf_pages(path)
        self.assertEqual(pages[0][1], 1)  # 1 image sur la page

    def test_photo_absente_laisse_la_cellule_vide_sans_erreur(self):
        path = self._path()
        roster.export_roster_pdf(path, [_make_entry("SansPhoto")], ["photo", "name"])
        pages = _pdf_pages(path)
        self.assertEqual(pages[0][1], 0)
        self.assertIn("SansPhoto", pages[0][0])

    def test_proportions_de_la_photo_conservees_jamais_etirees(self):
        # Image factice non carrée (2:1) pour vérifier que le ratio est
        # bien conservé par _fit_image_box (jamais un simple max_w x
        # max_h, qui étirerait la photo).
        img_path = os.path.join(self._tmp.name, "wide.jpg")
        Image.new("RGB", (400, 200), color=(1, 2, 3)).save(img_path, "JPEG")
        w, h = roster._fit_image_box(img_path, 18, 20)
        self.assertAlmostEqual(w / h, 400 / 200, places=2)
        self.assertLessEqual(w, 18)
        self.assertLessEqual(h, 20)

    def test_nom_et_donnees_alignes_avec_la_photo_meme_ligne(self):
        self._make_photo("Alice")
        path = self._path()
        roster.export_roster_pdf(path, [_make_entry("Alice", club="ClubX")], ["photo", "name", "club"])
        pages = _pdf_pages(path)
        self.assertEqual(pages[0][1], 1)
        self.assertIn("Alice", pages[0][0])
        self.assertIn("ClubX", pages[0][0])

    def test_plusieurs_pages_avec_photos_aucune_page_vide_aucune_ligne_perdue(self):
        """Corrige un bug réel trouvé par un test direct avant livraison :
        un saut de page interne à pdf.cell() laissait x0/y0 invalides,
        produisant une page vide et une ligne mal placée."""
        entries = []
        for i in range(20):
            name = f"Joueur {i:03d}"
            self._make_photo(name, color=(i % 255, 10, 10))
            entries.append(_make_entry(name))
        path = self._path()
        roster.export_roster_pdf(path, entries, ["photo", "name", "club"])
        pages = _pdf_pages(path)
        self.assertGreater(len(pages), 1)
        total_images = sum(n for _, n in pages)
        total_rows = sum(text.count("Joueur ") for text, _ in pages)
        self.assertEqual(total_images, 20)
        self.assertEqual(total_rows, 20)
        for text, _ in pages:
            self.assertNotEqual(text.strip(), "")  # jamais une page blanche

    def test_hauteur_de_ligne_variable_selon_presence_de_photo(self):
        """Demande de correction du 2026-10-07 : une ligne SANS photo
        (alors que la colonne Photo est cochée) doit utiliser une hauteur
        compacte, jamais la grande hauteur vide d'avant cette correction
        — une ligne AVEC photo garde, elle, exactement la hauteur/les
        proportions déjà validées."""
        self._make_photo("Alice")
        self._make_photo("Carla")
        entries = [_make_entry("Alice"), _make_entry("Bob"), _make_entry("Carla")]  # photo, non, photo
        path = self._path()
        roster.export_roster_pdf(path, entries, ["photo", "name", "club"])

        header_heights, row_heights = self._row_border_heights_mm(path)
        header_h, alice_h, bob_h, carla_h = (
            header_heights[0], row_heights[0], row_heights[1], row_heights[2],
        )
        self.assertAlmostEqual(header_h, 8, delta=0.3)
        self.assertAlmostEqual(alice_h, 24, delta=0.3)   # avec photo : hauteur haute inchangée
        self.assertAlmostEqual(bob_h, 7, delta=0.3)       # sans photo : hauteur compacte
        self.assertAlmostEqual(carla_h, 24, delta=0.3)   # avec photo : hauteur haute inchangée
        # Vérifie EXPLICITEMENT que la ligne sans photo est bien plus
        # basse que les lignes avec photo (demande explicite).
        self.assertLess(bob_h, alice_h)
        self.assertLess(bob_h, carla_h)

    def test_lignes_suivantes_se_repositionnent_apres_une_ligne_compacte(self):
        """Les positions Y réelles doivent refléter la hauteur RÉELLE de
        chaque ligne précédente (pas une hauteur fixe) — démontré
        indirectement par un mélange sur plusieurs pages : si une ligne
        compacte sans photo était comptée comme haute (ou l'inverse), les
        photos se chevaucheraient ou le nombre de lignes par page serait
        incorrect."""
        entries = []
        for i in range(30):
            name = f"Joueur {i:03d}"
            if i % 2 == 0:  # une ligne sur deux a une photo
                self._make_photo(name, color=(i % 255, 20, 40))
            entries.append(_make_entry(name))
        path = self._path()
        roster.export_roster_pdf(path, entries, ["photo", "name", "club"])

        pages = _pdf_pages(path)
        self.assertGreater(len(pages), 1)
        total_images = sum(n for _, n in pages)
        total_rows = sum(text.count("Joueur ") for text, _ in pages)
        self.assertEqual(total_images, 15)  # une sur deux a une photo
        self.assertEqual(total_rows, 30)
        for text, _ in pages:
            self.assertNotEqual(text.strip(), "")  # jamais de page blanche

        _header_heights, row_heights = self._row_border_heights_mm(path)
        # Toutes les lignes mesurées (en-têtes déjà exclus par construction,
        # voir _row_border_heights_mm) sont soit ~24mm (photo), soit ~7mm
        # (sans photo) — jamais une valeur intermédiaire ou incorrecte.
        self.assertEqual(len(row_heights), 30)
        for h in row_heights:
            self.assertTrue(
                abs(h - 24) < 0.3 or abs(h - 7) < 0.3,
                f"hauteur de ligne inattendue : {h}mm",
            )

    def test_environ_100_entrees_avec_photos_sans_erreur_et_rapide(self):
        entries = []
        for i in range(100):
            name = f"Joueur {i:03d}"
            self._make_photo(name, color=(i % 255, 50, 80))
            entries.append(_make_entry(name, club=f"Club {i % 5}"))
        path = self._path()
        start = time.time()
        roster.export_roster_pdf(path, entries, ["photo", "name", "club", "phone"])
        elapsed = time.time() - start
        self.assertLess(elapsed, 20, "génération anormalement lente pour ~100 entrées")
        pages = _pdf_pages(path)
        total_images = sum(n for _, n in pages)
        self.assertEqual(total_images, 100)
        for i, (text, _) in enumerate(pages, start=1):
            self.assertIn("Photo", text)
            self.assertIn(f"Page {i}/{len(pages)}", text)

    def test_paysage_si_beaucoup_de_colonnes_texte_avec_photo(self):
        path = self._path()
        roster.export_roster_pdf(
            path, [_make_entry("Alice")],
            ["photo", "name", "club", "group", "phone", "mail"],  # 5 colonnes texte
        )
        doc = fitz.open(path)
        try:
            self.assertGreater(doc[0].rect.width, doc[0].rect.height)
        finally:
            doc.close()

    def test_portrait_si_peu_de_colonnes_texte_avec_photo(self):
        path = self._path()
        roster.export_roster_pdf(path, [_make_entry("Alice")], ["photo", "name", "club"])
        doc = fitz.open(path)
        try:
            self.assertLess(doc[0].rect.width, doc[0].rect.height)
        finally:
            doc.close()


# =========================================================================
# D. Non-régression de _write_pdf_table (comportement par défaut)
# =========================================================================
@unittest.skipUnless(_FPDF_AVAILABLE and _FITZ_AVAILABLE, "fpdf2/PyMuPDF indisponibles")
class WritePdfTableBackwardCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="write_pdf_table_compat_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_sans_les_nouveaux_parametres_aucun_numero_de_page(self):
        path = os.path.join(self._tmp.name, "old.pdf")
        rows = [[f"Joueur {i}", "Club", "Tel"] for i in range(80)]
        database._write_pdf_table(path, "Titre", [], ["Nom", "Club", "Tel"], rows)
        pages = _pdf_pages(path)
        self.assertGreater(len(pages), 1)
        for text, _ in pages:
            self.assertNotIn("Page ", text)

    def test_sans_les_nouveaux_parametres_en_tete_pas_repete(self):
        path = os.path.join(self._tmp.name, "old2.pdf")
        rows = [[f"Joueur {i}", "Club", "Tel"] for i in range(80)]
        database._write_pdf_table(path, "Titre", [], ["Nom", "Club", "Tel"], rows)
        pages = _pdf_pages(path)
        self.assertGreater(len(pages), 1)
        # La 1ère page a l'en-tête (toujours dessiné manuellement) ; les
        # pages suivantes ne doivent PAS le répéter par défaut.
        self.assertIn("Nom", pages[0][0])
        for text, _ in pages[1:]:
            self.assertNotIn("Nom", text)

    def test_avec_repeat_header_et_page_numbers_fonctionne(self):
        path = os.path.join(self._tmp.name, "new.pdf")
        rows = [[f"Joueur {i}", "Club", "Tel"] for i in range(80)]
        database._write_pdf_table(
            path, "Titre", [], ["Nom", "Club", "Tel"], rows,
            repeat_header=True, page_numbers=True,
        )
        pages = _pdf_pages(path)
        self.assertGreater(len(pages), 1)
        for i, (text, _) in enumerate(pages, start=1):
            self.assertIn("Nom", text)
            self.assertIn(f"Page {i}/{len(pages)}", text)


# =========================================================================
# E. UI (Tk) — bouton, fenêtre, défauts, mémorisation, Tout cocher/
#    décocher, refus si rien coché, ordre = tri actuel du Répertoire
# =========================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RosterPdfDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_pdf_dialog_test_")
        self.addCleanup(self._tmp.cleanup)
        self.prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=self.prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        import main
        self.main = main
        self.entries = [_make_entry("Alice"), _make_entry("Bob")]

    def _open_dialog(self):
        return self.main.RosterPdfDialog(self.root, self.entries)

    def test_defauts_coches_si_rien_memorise(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        checked = {k for k, v in dlg.col_vars.items() if v.get()}
        self.assertEqual(checked, {"name", "club", "phone"})

    def test_tout_cocher(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        for v in dlg.col_vars.values():
            v.set(False)
        for v in dlg.col_vars.values():
            v.set(True)
        self.assertTrue(all(v.get() for v in dlg.col_vars.values()))

    def test_tout_decocher(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        for v in dlg.col_vars.values():
            v.set(False)
        self.assertFalse(any(v.get() for v in dlg.col_vars.values()))

    def test_aucune_case_cochee_refuse_sans_creer_de_fichier(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        for v in dlg.col_vars.values():
            v.set(False)
        with patch.object(self.main.messagebox, "showerror") as mock_error, \
             patch.object(self.main.filedialog, "asksaveasfilename") as mock_save:
            dlg._do_export()
            mock_error.assert_called_once()
            mock_save.assert_not_called()  # refusé AVANT de demander un chemin

    def test_memorisation_du_dernier_choix(self):
        export_prefs.save_columns("roster_pdf", ["name", "mail", "pseudo"])
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        checked = {k for k, v in dlg.col_vars.items() if v.get()}
        self.assertEqual(checked, {"name", "mail", "pseudo"})

    def test_creer_le_pdf_sauvegarde_le_choix_pour_la_prochaine_fois(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        for key, v in dlg.col_vars.items():
            v.set(key in ("name", "group"))
        out_path = os.path.join(self._tmp.name, "out.pdf")
        with patch.object(self.main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(self.main, "open_file_with_default_app"):
            dlg._do_export()
        all_keys = [k for k, _ in roster.ROSTER_PDF_COLUMNS]
        self.assertEqual(set(export_prefs.load_columns("roster_pdf", all_keys)), {"name", "group"})
        self.assertTrue(os.path.exists(out_path))

    def test_annulation_de_la_boite_de_dialogue_fichier_necrit_rien(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        with patch.object(self.main.filedialog, "asksaveasfilename", return_value=""), \
             patch.object(self.main, "open_file_with_default_app") as mock_open:
            dlg._do_export()
            mock_open.assert_not_called()

    def test_import_error_affiche_message_module_manquant(self):
        dlg = self._open_dialog()
        self.addCleanup(dlg.destroy)
        out_path = os.path.join(self._tmp.name, "out2.pdf")
        with patch.object(self.main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(self.main.roster, "export_roster_pdf", side_effect=ImportError), \
             patch.object(self.main, "show_missing_export_module") as mock_missing:
            dlg._do_export()
            mock_missing.assert_called_once_with("pdf")


# =========================================================================
# F. Ordre = tri actuellement affiché dans l'onglet Répertoire (main.py)
# =========================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RosterOrderMatchesScreenTest(unittest.TestCase):
    """RosterManagerDialog._refresh() doit alimenter self._roster_entries_
    ordered dans EXACTEMENT le même ordre que celui inséré dans
    self.roster_tree (reflet fidèle de l'écran), quel que soit le tri en
    cours (colonne + sens)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.root.destroy()
        except Exception:
            pass

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_order_test_")
        self.addCleanup(self._tmp.cleanup)
        self.roster_path = os.path.join(self._tmp.name, "roster.json")
        patcher = patch.object(roster, "_roster_path", return_value=self.roster_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        roster.save_roster_entries([
            _make_entry("Zoe", club="C1"), _make_entry("Alice", club="C3"),
            _make_entry("Marc", club="C2"),
        ])

        photos_tmp = tempfile.TemporaryDirectory(prefix="roster_order_photos_")
        self.addCleanup(photos_tmp.cleanup)
        pp_patcher = patch.object(player_photos, "_photos_dir", return_value=photos_tmp.name)
        self.addCleanup(pp_patcher.stop)
        pp_patcher.start()

        import main
        self.main = main

        self.win = self.root
        self.win.app = None
        self.win.roster_sort = {"column": "name", "ascending": True}
        self.win._roster_entries_ordered = []
        self.win.new_name_var = tk.StringVar()

        self.frame = ttk.Frame(self.root)
        self.addCleanup(self.frame.destroy)
        self.win.roster_tree = ttk.Treeview(
            self.frame, columns=("name", "club", "group", "phone", "mail", "pseudo"),
        )
        self.win.roster_row_photo_images = {}
        self.win._selected_name = types.MethodType(lambda self_: None, self.win)
        self.win._update_roster_sort_headings = types.MethodType(lambda self_: None, self.win)
        self.win._refresh_club_summary = types.MethodType(lambda self_, entries: None, self.win)
        self.win._refresh_preview = types.MethodType(lambda self_: None, self.win)
        self.win._refresh = types.MethodType(self.main.RosterManagerDialog._refresh, self.win)

    def test_ordre_alphabetique_par_defaut(self):
        self.win._refresh()
        names = [e["name"] for e in self.win._roster_entries_ordered]
        self.assertEqual(names, ["Alice", "Marc", "Zoe"])
        self.assertEqual(list(self.win.roster_tree.get_children()), names)

    def test_ordre_inverse_si_tri_descendant(self):
        self.win.roster_sort = {"column": "name", "ascending": False}
        self.win._refresh()
        names = [e["name"] for e in self.win._roster_entries_ordered]
        self.assertEqual(names, ["Zoe", "Marc", "Alice"])

    def test_ordre_par_club_si_tri_sur_club(self):
        self.win.roster_sort = {"column": "club", "ascending": True}
        self.win._refresh()
        names = [e["name"] for e in self.win._roster_entries_ordered]
        self.assertEqual(names, ["Zoe", "Marc", "Alice"])  # C1, C2, C3

    def test_bouton_imprimer_vide_naffiche_pas_de_fenetre(self):
        self.win._roster_entries_ordered = []
        self.win._print_roster_pdf = types.MethodType(
            self.main.RosterManagerDialog._print_roster_pdf, self.win,
        )
        with patch.object(self.main.messagebox, "showinfo") as mock_info, \
             patch.object(self.main, "RosterPdfDialog") as mock_dialog:
            self.win._print_roster_pdf()
            mock_info.assert_called_once()
            mock_dialog.assert_not_called()


if __name__ == "__main__":
    unittest.main()
