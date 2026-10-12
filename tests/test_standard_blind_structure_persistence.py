# -*- coding: utf-8 -*-
"""« Enr. Struc. Std » (demande du 2026-10-05) — enregistrer la
structure de blindes actuellement affichée dans l'onglet Blindes comme
nouvelle Structure Standard PERSISTANTE, d'une session à l'autre.

Mécanisme (structures.py) :
- `save_custom_standard_structure(levels)` / `load_custom_standard_
  structure()` — un seul fichier dédié
  `~/.poker_tournament/standard_blind_structure.json`, JAMAIS un modèle
  nommé de blind_templates.py (n'apparaît donc jamais dans « 📂
  Récupérer Blindes... »).
- `default_blind_structure()` : renvoie la structure personnalisée si
  elle existe, sinon la structure historique codée en dur, STRICTEMENT
  inchangée — point de passage UNIQUE déjà utilisé par les boutons
  « Structure standard » (onglets Blindes ET Chronomètre) ET par
  `Database._init_defaults` (nouveau tournoi) : les trois en héritent
  automatiquement, sans modification de leur propre code.

Bouton « Enr. Struc. Std » (App._save_current_as_standard_structure,
main.py) : réutilise `_collect_blinds_from_widgets`/
`_rounds_to_flat_structure` (même validation que « Appliquer »/
« Enregistrer Blindes sous... »), demande confirmation Oui/Non, et
N'APPLIQUE PAS la structure à ce tournoi (différence volontaire avec
« Enregistrer Blindes sous... ») — un simple enregistrement pour
l'avenir."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import blind_templates  # noqa: E402
import database  # noqa: E402
import main  # noqa: E402
import structures  # noqa: E402

try:
    import tkinter as tk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False


def _sample_levels():
    """Structure plate quelconque, distincte de la structure historique
    — utilisée pour vérifier que l'override est bien PRIS EN COMPTE
    (jamais confondue avec la structure codée en dur par coïncidence)."""
    return [
        {"small_blind": 100, "big_blind": 200, "ante": 0, "duration_minutes": 20, "is_break": False},
        {"small_blind": 200, "big_blind": 400, "ante": 50, "duration_minutes": 20, "is_break": False},
        {"small_blind": 0, "big_blind": 0, "ante": 0, "duration_minutes": 10, "is_break": True, "break_label": "Pause"},
        {"small_blind": 300, "big_blind": 600, "ante": 75, "duration_minutes": 20, "is_break": False},
    ]


# =========================================================================
# 1. Persistance de bas niveau (structures.py), sans toucher au vrai
#    ~/.poker_tournament
# =========================================================================
class StandardStructurePersistenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="standard_blind_structure_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "standard_blind_structure.json")
        patcher = patch.object(structures, "_standard_structure_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_load_renvoie_none_sans_fichier(self):
        self.assertIsNone(structures.load_custom_standard_structure())

    def test_round_trip_exact(self):
        levels = _sample_levels()
        structures.save_custom_standard_structure(levels)

        loaded = structures.load_custom_standard_structure()

        self.assertEqual(loaded, levels)

    def test_enregistrer_une_deuxieme_fois_ecrase_jamais_naccumule(self):
        structures.save_custom_standard_structure(_sample_levels())
        nouvelle = [{"small_blind": 10, "big_blind": 20, "ante": 0, "duration_minutes": 5, "is_break": False}]

        structures.save_custom_standard_structure(nouvelle)

        self.assertEqual(structures.load_custom_standard_structure(), nouvelle)

    def test_fichier_illisible_renvoie_none_sans_lever(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON valide")

        self.assertIsNone(structures.load_custom_standard_structure())

    def test_chemin_dedie_hors_du_dossier_blind_templates(self):
        """Demande explicite : ne doit JAMAIS apparaître dans
        blind_templates.list_templates() — vérifié ici par construction,
        le fichier vit un niveau au-dessus du sous-dossier blind_templates/,
        jamais dedans."""
        path = structures._standard_structure_path()
        self.assertEqual(os.path.basename(path), "standard_blind_structure.json")
        self.assertNotIn("blind_templates", path)

    def test_jamais_liste_par_recuperer_blindes(self):
        """Vérification COMPORTEMENTALE (pas seulement le chemin) :
        blind_templates.list_templates(), isolé dans le même dossier
        temporaire que ce test, ne voit jamais la Structure Standard
        personnalisée enregistrée ci-dessus."""
        templates_dir = os.path.join(self._tmp.name, "blind_templates")
        with patch.object(blind_templates, "_templates_dir", return_value=templates_dir):
            os.makedirs(templates_dir, exist_ok=True)
            blind_templates.save_template("Un modèle normal", _sample_levels())
            structures.save_custom_standard_structure(_sample_levels())

            self.assertEqual(blind_templates.list_templates(), ["Un modèle normal"])


# =========================================================================
# 2. default_blind_structure() — non-régression historique + override
# =========================================================================
class DefaultBlindStructureTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="default_blind_structure_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "standard_blind_structure.json")
        patcher = patch.object(structures, "_standard_structure_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_sans_override_structure_historique_strictement_inchangee(self):
        """Verrou de non-régression explicite demandé : sans override,
        la structure renvoyée doit être EXACTEMENT celle générée par les
        paramètres historiques (25/50, antes dès le niveau 4, paliers de
        15 min, pause toutes les 4 rounds) — jamais une approximation."""
        expected = structures.generate_blind_structure(
            start_small_blind=25, start_big_blind=50, ante_start_level=4,
            start_ante=25, duration_minutes=15, break_duration_minutes=15,
            break_every=4,
        )

        self.assertEqual(structures.default_blind_structure(), expected)

    def test_premier_niveau_valeurs_historiques_exactes(self):
        level = structures.default_blind_structure()[0]
        self.assertEqual(
            (level["small_blind"], level["big_blind"], level["ante"], level["duration_minutes"], level["is_break"]),
            (25, 50, 0, 15, False),
        )

    def test_avec_override_renvoie_exactement_loverride(self):
        levels = _sample_levels()
        structures.save_custom_standard_structure(levels)

        self.assertEqual(structures.default_blind_structure(), levels)

    def test_override_jamais_confondu_avec_la_structure_historique(self):
        """Les valeurs de _sample_levels() sont volontairement très
        différentes de 25/50 — confirme qu'on ne lit PAS accidentellement
        la structure codée en dur une fois l'override enregistré."""
        structures.save_custom_standard_structure(_sample_levels())

        level = structures.default_blind_structure()[0]
        self.assertEqual((level["small_blind"], level["big_blind"]), (100, 200))


# =========================================================================
# 3. Intégration — nouveau tournoi + boutons "Structure standard"
# =========================================================================
class NewTournamentUsesCustomStandardTest(unittest.TestCase):
    """Database._init_defaults() (création d'un tournoi tout neuf) doit
    hériter automatiquement de l'override, sans aucune modification de
    son propre code — via default_blind_structure() uniquement."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="new_tournament_standard_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "standard_blind_structure.json")
        patcher = patch.object(structures, "_standard_structure_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_nouveau_tournoi_demarre_avec_loverride(self):
        structures.save_custom_standard_structure(_sample_levels())

        db_path = os.path.join(self._tmp.name, "nouveau.tournoi")
        db = database.Database(db_path)
        self.addCleanup(db.conn.close)

        stored = [dict(r) for r in db.get_blind_structure()]
        self.assertEqual(stored[0]["small_blind"], 100)
        self.assertEqual(stored[0]["big_blind"], 200)
        self.assertEqual(len(stored), len(_sample_levels()))

    def test_nouveau_tournoi_sans_override_reste_historique(self):
        db_path = os.path.join(self._tmp.name, "nouveau2.tournoi")
        db = database.Database(db_path)
        self.addCleanup(db.conn.close)

        stored = [dict(r) for r in db.get_blind_structure()]
        self.assertEqual((stored[0]["small_blind"], stored[0]["big_blind"]), (25, 50))


class ResetButtonsUseCustomStandardTest(unittest.TestCase):
    """Les deux boutons "Structure standard" (Blindes et Chronomètre)
    appellent tous deux default_blind_structure() sans modification —
    vérifié ici en appelant réellement les méthodes App, avec confirm
    forcé à Oui."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reset_buttons_standard_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "standard_blind_structure.json")
        patcher = patch.object(structures, "_standard_structure_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        structures.save_custom_standard_structure(_sample_levels())

    def test_bouton_blindes_tab_applique_loverride(self):
        win = types.SimpleNamespace(db=self.db)
        win._refresh_blinds_tab = lambda: None
        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._reset_blind_structure_from_tab(win)

        stored = [dict(r) for r in self.db.get_blind_structure()]
        self.assertEqual(stored[0]["small_blind"], 100)

    def test_bouton_chronometre_tab_applique_loverride(self):
        win = types.SimpleNamespace(db=self.db)
        win._go_to_level = lambda order: None
        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._reset_blind_structure(win)

        stored = [dict(r) for r in self.db.get_blind_structure()]
        self.assertEqual(stored[0]["small_blind"], 100)


# =========================================================================
# 4. Bouton "Enr. Struc. Std" — confirmation, contenu exact, non-application
# =========================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class SaveCurrentAsStandardStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        from _tk_cleanup import cleanup_tk
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="save_as_standard_button_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "standard_blind_structure.json")
        patcher = patch.object(structures, "_standard_structure_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = types.SimpleNamespace(db=self.db)
        self.win._collect_blinds_from_widgets = types.MethodType(
            main.App._collect_blinds_from_widgets, self.win
        )
        self.win._rounds_to_flat_structure = types.MethodType(
            main.App._rounds_to_flat_structure, self.win
        )
        self.win._save_current_as_standard_structure = types.MethodType(
            main.App._save_current_as_standard_structure, self.win
        )
        self.win._blind_row_vars = [
            {
                "duration": tk.StringVar(self.root, value="20"),
                "sb": tk.StringVar(self.root, value="100"),
                "bb": tk.StringVar(self.root, value="200"),
                "ante": tk.StringVar(self.root, value="0"),
                "pause": tk.StringVar(self.root, value="0"),
            },
            {
                "duration": tk.StringVar(self.root, value="20"),
                "sb": tk.StringVar(self.root, value="200"),
                "bb": tk.StringVar(self.root, value="400"),
                "ante": tk.StringVar(self.root, value="50"),
                "pause": tk.StringVar(self.root, value="10"),
            },
        ]

    def test_non_naucun_enregistrement(self):
        with patch.object(main.messagebox, "askyesno", return_value=False):
            self.win._save_current_as_standard_structure()

        self.assertIsNone(structures.load_custom_standard_structure())

    def test_oui_enregistre_exactement_la_structure_affichee(self):
        with patch.object(main.messagebox, "askyesno", return_value=True), \
             patch.object(main.messagebox, "showinfo") as mock_info:
            self.win._save_current_as_standard_structure()

        mock_info.assert_called_once()
        saved = structures.load_custom_standard_structure()
        self.assertEqual(len(saved), 3)  # round 1 (pas de pause) + round 2 + sa pause
        self.assertEqual((saved[0]["small_blind"], saved[0]["big_blind"]), (100, 200))
        self.assertEqual((saved[1]["small_blind"], saved[1]["big_blind"], saved[1]["ante"]), (200, 400, 50))
        self.assertTrue(saved[2]["is_break"])
        self.assertEqual(saved[2]["duration_minutes"], 10)
        self.assertEqual(saved[2]["break_label"], "Pause")

    def test_naffecte_jamais_le_tournoi_en_cours(self):
        """Différence volontaire avec "Enregistrer Blindes sous..." :
        AUCUN effet sur ce tournoi, seulement sur l'enregistrement
        persistant pour l'avenir."""
        before = [dict(r) for r in self.db.get_blind_structure()]

        with patch.object(main.messagebox, "askyesno", return_value=True), \
             patch.object(main.messagebox, "showinfo"):
            self.win._save_current_as_standard_structure()

        after = [dict(r) for r in self.db.get_blind_structure()]
        self.assertEqual(after, before)

    def test_valeur_invalide_naffiche_jamais_la_confirmation(self):
        self.win._blind_row_vars[0]["sb"].set("pas un nombre")

        with patch.object(main.messagebox, "askyesno") as mock_confirm, \
             patch.object(main.messagebox, "showerror") as mock_error:
            self.win._save_current_as_standard_structure()

        mock_confirm.assert_not_called()
        mock_error.assert_called_once()
        self.assertIsNone(structures.load_custom_standard_structure())


if __name__ == "__main__":
    unittest.main()
