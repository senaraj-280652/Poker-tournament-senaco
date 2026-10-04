# -*- coding: utf-8 -*-
"""Correction du PDF imprimé (demande du 2026-10-02, "correction du PDF
imprimé") : le PDF récapitulatif des paramètres (bouton « Imprimer
Paramètres... », Database.export_settings_pdf/SETTINGS_PRINT_FIELDS)
n'avait jamais été mis à jour depuis plusieurs chantiers récents —
5 réglages réellement enregistrés par tournoi en étaient absents :
accueil_management_enabled, absence_duration_minutes (Lot 2, "Gestion
des absences et des retardataires"), primes_enabled (interrupteur
général primes/bounty/PKO), elimination_banner_seconds, undo_
elimination_timeout_minutes.

Diagnostic validé par l'utilisateur avant programmation (voir l'échange
du 2026-10-02) — volontairement LAISSÉS DE CÔTÉ : "Jours de tournoi /
Sit & Go" (purement informatif) et le type Tournoi/Sit & Go (non
modifiable dans Paramètres), les préférences GÉNÉRALES de session ("Un
seul tournoi à la fois", etc., jamais écrites dans le fichier .tournoi),
les Permissions DIRTO/Contrôle à distance (onglet CA, structure
différente), et le code personnel (jamais affiché nulle part).

Couvre :
1. SETTINGS_PRINT_FIELDS/SETTINGS_PRINT_BOOLEAN_KEYS/SETTINGS_PRINT_
   DEFAULTS — structure attendue.
2. Database.export_settings_pdf — bout en bout, PDF réellement généré
   PUIS son texte extrait (PyMuPDF/fitz) pour vérifier le contenu réel,
   pas seulement l'existence du fichier :
   a. les 5 nouveaux réglages apparaissent, avec leur valeur propre à CE
      tournoi (jamais une valeur générale/par défaut quand une valeur
      réelle est enregistrée) ;
   b. les 3 booléens s'affichent "Oui"/"Non" ;
   c. un ancien fichier n'ayant JAMAIS ces 5 clés affiche la valeur
      réellement utilisée par le logiciel pour ce cas précis (jamais une
      case vide ni une préférence importée d'ailleurs) ;
   d. non-régression des champs déjà présents (pko_mode, ranking_
      formula, club_name)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402

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


def _pdf_text(path):
    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


# =====================================================================
# 1. Structure de SETTINGS_PRINT_FIELDS/BOOLEAN_KEYS/DEFAULTS.
# =====================================================================
class SettingsPrintFieldsStructureTest(unittest.TestCase):
    def test_les_5_nouveaux_reglages_sont_presents(self):
        keys = [k for k, _ in database.SETTINGS_PRINT_FIELDS]
        for key in (
            "accueil_management_enabled", "absence_duration_minutes",
            "primes_enabled", "elimination_banner_seconds",
            "undo_elimination_timeout_minutes",
        ):
            self.assertIn(key, keys)

    def test_aucun_doublon_de_cle(self):
        keys = [k for k, _ in database.SETTINGS_PRINT_FIELDS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_les_3_booleens_sont_dans_boolean_keys(self):
        self.assertEqual(
            database.SETTINGS_PRINT_BOOLEAN_KEYS,
            {"pko_mode", "accueil_management_enabled", "primes_enabled"},
        )

    def test_les_defauts_correspondent_au_comportement_reel_du_logiciel(self):
        """Mêmes valeurs que celles utilisées ailleurs dans l'app pour un
        ancien fichier sans ces clés (voir App._build_settings_tab,
        Database.DEFAULT_SETTINGS) — jamais une invention de cette
        fonction d'export."""
        self.assertEqual(database.SETTINGS_PRINT_DEFAULTS["primes_enabled"], "1")
        self.assertEqual(database.SETTINGS_PRINT_DEFAULTS["absence_duration_minutes"], "60")
        self.assertEqual(database.SETTINGS_PRINT_DEFAULTS["elimination_banner_seconds"], "5")
        self.assertEqual(database.SETTINGS_PRINT_DEFAULTS["undo_elimination_timeout_minutes"], "5")

    def test_jours_de_tournoi_et_type_sng_volontairement_absents(self):
        """Décision explicite de l'utilisateur (voir le diagnostic validé
        le 2026-10-02) : ne pas inclure ces deux champs."""
        keys = [k for k, _ in database.SETTINGS_PRINT_FIELDS]
        self.assertNotIn("tournament_days_of_week", keys)
        self.assertNotIn("is_sng", keys)


@unittest.skipUnless(_FPDF_AVAILABLE, "fpdf2 indisponible dans cet environnement")
class ExportSettingsPdfGenerationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="settings_pdf_export_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.out_path = os.path.join(self._tmp.name, "export.pdf")

    def test_pdf_genere_et_non_vide(self):
        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        self.assertTrue(os.path.exists(self.out_path))
        self.assertGreater(os.path.getsize(self.out_path), 0)


@unittest.skipUnless(_FPDF_AVAILABLE, "fpdf2 indisponible dans cet environnement")
@unittest.skipUnless(_FITZ_AVAILABLE, "PyMuPDF (fitz) indisponible dans cet environnement")
class ExportSettingsPdfContentTest(unittest.TestCase):
    """Vérification du CONTENU réel du PDF généré (texte extrait via
    PyMuPDF) — pas seulement de son existence."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="settings_pdf_content_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.out_path = os.path.join(self._tmp.name, "export.pdf")

    def test_les_5_nouveaux_reglages_avec_leurs_libelles_apparaissent(self):
        self.db.set_settings({
            "accueil_management_enabled": 1,
            "absence_duration_minutes": 45,
            "primes_enabled": 0,
            "elimination_banner_seconds": 10,
            "undo_elimination_timeout_minutes": 15,
        })

        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)

        for label in (
            "Gestion de l'accueil", "Durée d'une absence (minutes)",
            "Calculer les primes", "Durée du bandeau d'élimination (secondes)",
            "Timeout pour Annuler Éliminer (minutes)",
        ):
            self.assertIn(label, text, f"libellé absent du PDF : {label}")

    def test_valeurs_propres_a_ce_tournoi_jamais_remplacees_par_le_defaut(self):
        """Point de sécurité explicitement demandé (item 4) : des valeurs
        non-standard, réellement enregistrées pour CE tournoi, doivent
        apparaître telles quelles — jamais les valeurs par défaut."""
        self.db.set_settings({
            "absence_duration_minutes": 99,
            "elimination_banner_seconds": 22,
            "undo_elimination_timeout_minutes": 33,
        })

        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)

        self.assertIn("99", text)
        self.assertIn("22", text)
        self.assertIn("33", text)
        # Les défauts (60/5/5) ne doivent apparaître nulle part à la
        # place de ces valeurs réellement enregistrées — recherche sur
        # la ligne du libellé précis pour éviter un faux positif avec un
        # "60"/"5" présent ailleurs dans le document (montants, etc.).
        self.assertNotIn("Durée d'une absence (minutes)\n60", text)

    def test_accueil_actif_affiche_oui(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)
        idx = text.index("Gestion de l'accueil")
        self.assertIn("Oui", text[idx:idx + 60])

    def test_accueil_inactif_affiche_non(self):
        self.db.export_settings_pdf(self.out_path, club_name="Club Test")  # jamais activé
        text = _pdf_text(self.out_path)
        idx = text.index("Gestion de l'accueil")
        self.assertIn("Non", text[idx:idx + 60])

    def test_primes_desactivees_affiche_non(self):
        self.db.set_settings({"primes_enabled": 0})
        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)
        idx = text.index("Calculer les primes")
        self.assertIn("Non", text[idx:idx + 60])

    def test_ancien_fichier_sans_ces_cles_affiche_les_valeurs_reellement_en_vigueur(self):
        """Simule un fichier .tournoi antérieur à ces chantiers : aucune
        des 5 clés n'existe en base — le PDF doit refléter EXACTEMENT ce
        que le logiciel utiliserait réellement pour ce tournoi dans ce
        cas (voir Database.accueil_management_enabled/get_setting_int),
        jamais une case vide ni une préférence générale importée
        d'ailleurs."""
        for key in (
            "accueil_management_enabled", "absence_duration_minutes",
            "primes_enabled", "elimination_banner_seconds",
            "undo_elimination_timeout_minutes",
        ):
            self.db.conn.execute("DELETE FROM settings WHERE key=?", (key,))
        self.db.conn.commit()

        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)

        idx = text.index("Gestion de l'accueil")
        self.assertIn("Non", text[idx:idx + 60])
        idx = text.index("Durée d'une absence (minutes)")
        self.assertIn("60", text[idx:idx + 60])
        idx = text.index("Calculer les primes")
        self.assertIn("Oui", text[idx:idx + 60])
        idx = text.index("Durée du bandeau d'élimination (secondes)")
        self.assertIn("5", text[idx:idx + 60])
        idx = text.index("Timeout pour Annuler Éliminer (minutes)")
        self.assertIn("5", text[idx:idx + 60])

    def test_non_regression_pko_mode_toujours_oui_non(self):
        self.db.set_settings({"pko_mode": 1})
        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)
        idx = text.index("Mode PKO")
        self.assertIn("Oui", text[idx:idx + 60])

    def test_non_regression_club_name_et_nom_du_tournoi(self):
        self.db.set_settings({"tournament_name": "Tournoi du vendredi"})
        self.db.export_settings_pdf(self.out_path, club_name="Club des Copains")
        text = _pdf_text(self.out_path)
        self.assertIn("Club des Copains", text)
        self.assertIn("Tournoi du vendredi", text)

    def test_non_regression_ranking_formula_resolue(self):
        self.db.set_settings({"ranking_formula": database.RANKING_FORMULA_PROGRESSIVE})
        self.db.export_settings_pdf(self.out_path, club_name="Club Test")
        text = _pdf_text(self.out_path)
        self.assertIn("Progressive", text)


if __name__ == "__main__":
    unittest.main()
