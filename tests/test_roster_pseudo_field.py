# -*- coding: utf-8 -*-
"""Répertoire — champ "Pseudo" (demande du 2026-10-03, "ajouter la
colonne Pseudo au Répertoire") : même principe que group/phone/mail
(voir tests/test_roster_group_phone_mail_widget.py et tests/test_roster_
admin_dirto_fields.py, dont ce fichier reprend la structure pour le
nouveau champ) — recherche préalable confirmée : AUCUNE donnée pseudo
n'existait nulle part dans Senaco avant ce chantier (ni roster.py, ni la
table players, ni aucune UI) ; ce champ est donc entièrement nouveau,
jamais un doublon.

_edit_pseudo est testée SANS jamais ouvrir la vraie fenêtre modale
sous-jacente (simpledialog.askstring) — même précaution que pour les
autres champs (risque de segfault Tcl/Tk déjà documenté avec un vrai
wait_window()/grab_set() dans ce harnais)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

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


class _FakeRosterDialog:
    """Doublure minimale pour _edit_pseudo : seuls _selected_name() et
    _refresh() sont lus/appelés — même style que _FakeRosterDialog dans
    tests/test_roster_group_phone_mail_widget.py."""

    def __init__(self, selected_name=None):
        self._selected = selected_name
        self.refresh_calls = 0

    def _selected_name(self):
        return self._selected

    def _refresh(self):
        self.refresh_calls += 1


class RosterPseudoTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_pseudo_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(patcher.stop)
        patcher.start()


# =====================================================================
# 1. roster.py — get_pseudo/set_pseudo, stockage, migration, fusion.
# =====================================================================
class PseudoStorageTest(RosterPseudoTestCase):
    def test_aucun_pseudo_par_defaut(self):
        roster.add_to_roster("Alice")
        self.assertEqual(roster.get_pseudo("Alice"), "")

    def test_set_puis_get_persiste(self):
        roster.add_to_roster("Alice")
        roster.set_pseudo("Alice", "Toto")
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")

    def test_set_pseudo_cree_lentree_si_absente(self):
        roster.set_pseudo("Nouveau", "Surnom")
        self.assertEqual(roster.get_pseudo("Nouveau"), "Surnom")
        self.assertIn("Nouveau", roster.load_roster())

    def test_set_pseudo_vide_efface(self):
        roster.set_pseudo("Alice", "Toto")
        roster.set_pseudo("Alice", "")
        self.assertEqual(roster.get_pseudo("Alice"), "")

    def test_espaces_superflus_retires(self):
        roster.add_to_roster("Alice")
        roster.set_pseudo("Alice", "  Toto  ")
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")

    def test_get_pseudo_nom_absent_renvoie_chaine_vide(self):
        self.assertEqual(roster.get_pseudo("Inconnu"), "")

    def test_load_roster_entries_inclut_toujours_pseudo(self):
        roster.add_to_roster("Alice")
        entries = roster.load_roster_entries()
        self.assertEqual(entries[0]["pseudo"], "")

    def test_save_roster_entries_persiste_le_pseudo(self):
        roster.save_roster_entries([{"name": "Alice", "pseudo": "Toto"}])
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")

    def test_save_roster_entries_sans_pseudo_le_met_a_vide(self):
        """Un appelant qui ne fournit pas "pseudo" (ex. ancien code non
        mis à jour) obtient "" — jamais une KeyError."""
        roster.save_roster_entries([{"name": "Alice", "club": "CPC"}])
        self.assertEqual(roster.get_pseudo("Alice"), "")

    def test_save_roster_names_seuls_preserve_pseudo_existant(self):
        """save_roster(names) (compatibilité, ne reçoit QUE des noms) ne
        doit jamais perdre un pseudo déjà connu pour un nom conservé."""
        roster.set_pseudo("Alice", "Toto")
        roster.save_roster(["Alice", "Bob"])
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")
        self.assertEqual(roster.get_pseudo("Bob"), "")

    def test_renommer_conserve_le_pseudo(self):
        roster.set_pseudo("Alice", "Toto")
        roster.rename_in_roster("Alice", "Alicia")
        self.assertEqual(roster.get_pseudo("Alicia"), "Toto")
        self.assertNotIn("Alice", roster.load_roster())

    def test_fusion_avec_nom_cible_existant_garde_le_pseudo_non_vide(self):
        roster.set_pseudo("Bob", "Bobby")  # "Bob" a déjà un pseudo
        roster.add_to_roster("Alice")  # "Alice" n'en a pas

        roster.rename_in_roster("Alice", "Bob")  # Alice fusionne dans Bob

        self.assertEqual(roster.get_pseudo("Bob"), "Bobby")  # préservé

    def test_doublon_sur_disque_garde_le_premier_pseudo_non_vide(self):
        """Même principe déjà en place pour club/group/phone/mail :
        doublons bruts dans le fichier, la première valeur NON VIDE
        rencontrée l'emporte."""
        import json
        path = roster._roster_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump([
                {"name": "Alice", "pseudo": ""},
                {"name": "Alice", "pseudo": "Toto"},
            ], f)
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")

    def test_ancien_fichier_sans_pseudo_est_migre_sans_erreur(self):
        """Ancien format roster.json (dicts sans "pseudo", ou simples
        chaînes) : migré automatiquement à la lecture, jamais une
        exception."""
        import json
        path = roster._roster_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"name": "Alice", "club": "CPC"}, "Bob"], f)
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["pseudo"], "")
        self.assertEqual(entries["Bob"]["pseudo"], "")

    def test_remove_from_roster_retire_le_pseudo_avec_tout_le_reste(self):
        roster.set_pseudo("Alice", "Toto")
        roster.remove_from_roster("Alice")
        self.assertNotIn("Alice", roster.load_roster())


# =====================================================================
# 2. App.RosterManagerDialog._edit_pseudo
# =====================================================================
class EditPseudoTest(RosterPseudoTestCase):
    def test_aucune_selection_naffiche_rien_ne_leve_pas(self):
        dialog = _FakeRosterDialog(selected_name=None)
        with patch.object(main.simpledialog, "askstring") as mock_ask:
            with patch.object(main.messagebox, "showinfo") as mock_info:
                main.RosterManagerDialog._edit_pseudo(dialog)
        mock_ask.assert_not_called()
        mock_info.assert_called_once()
        self.assertEqual(dialog.refresh_calls, 0)

    def test_validation_persiste_et_rafraichit(self):
        roster.add_to_roster("Alice")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value="Toto"):
            main.RosterManagerDialog._edit_pseudo(dialog)
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")
        self.assertEqual(dialog.refresh_calls, 1)

    def test_annulation_none_ne_change_rien(self):
        roster.set_pseudo("Alice", "Toto")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value=None):
            main.RosterManagerDialog._edit_pseudo(dialog)
        self.assertEqual(roster.get_pseudo("Alice"), "Toto")
        self.assertEqual(dialog.refresh_calls, 0)

    def test_validation_chaine_vide_efface(self):
        roster.set_pseudo("Alice", "Toto")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value=""):
            main.RosterManagerDialog._edit_pseudo(dialog)
        self.assertEqual(roster.get_pseudo("Alice"), "")

    def test_pre_rempli_avec_le_pseudo_courant(self):
        roster.set_pseudo("Alice", "Toto")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value="Toto") as mock_ask:
            main.RosterManagerDialog._edit_pseudo(dialog)
        mock_ask.assert_called_once_with("Pseudo", "Pseudo de Alice :", initialvalue="Toto")


# =====================================================================
# 3. Treeview du Répertoire — colonne Pseudo.
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RosterTreePseudoColumnTest(unittest.TestCase):
    """Même harnais que RosterTreeColumnsTest dans tests/test_roster_
    group_phone_mail_widget.py, étendu à la colonne "pseudo"."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_tree_pseudo_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        roster_patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(roster_patcher.stop)
        roster_patcher.start()
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        prefs_patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self.roster_tree = ttk.Treeview(
            self.root, columns=("name", "club", "group", "phone", "mail", "pseudo"),
            show="tree headings",
        )
        for col, label in (("name", "Nom"), ("club", "Club"), ("group", "Groupe"),
                           ("phone", "Téléphone"), ("mail", "Mail"), ("pseudo", "Pseudo")):
            self.roster_tree.heading(col, text=label)

        self.club_summary_tree = ttk.Treeview(self.root, columns=("club", "count"), show="headings")
        self.club_summary_tree.heading("club", text="Club")
        self.club_summary_tree.heading("count", text="Nombre")
        self.summary_box = ttk.LabelFrame(self.root, text="")
        self.preview_lbl = tk.Label(self.root)

        self.dlg = types.SimpleNamespace(
            roster_tree=self.roster_tree,
            club_summary_tree=self.club_summary_tree,
            summary_box=self.summary_box,
            preview_lbl=self.preview_lbl,
            roster_sort={"column": "name", "ascending": True},
            roster_row_photo_images={},
            _preview_photo=None,
        )
        for meth in (
            "_refresh", "_refresh_club_summary", "_refresh_preview",
            "_selected_name", "_update_roster_sort_headings", "_sort_roster_by",
        ):
            setattr(self.dlg, meth, types.MethodType(getattr(main.RosterManagerDialog, meth), self.dlg))
        self.addCleanup(lambda: cleanup_tk(
            self, "dlg", "roster_tree", "club_summary_tree", "summary_box", "preview_lbl",
        ))

    def test_refresh_peuple_le_pseudo_dans_le_tree(self):
        roster.set_pseudo("Alice", "Toto")
        self.dlg._refresh()
        self.assertEqual(self.roster_tree.set("Alice", "pseudo"), "Toto")

    def test_refresh_sans_pseudo_colonne_vide_pas_derreur(self):
        roster.add_to_roster("Bob")
        self.dlg._refresh()  # ne doit pas lever
        self.assertEqual(self.roster_tree.set("Bob", "pseudo"), "")

    def test_tri_par_pseudo_ne_leve_pas(self):
        roster.set_pseudo("Alice", "Zed")
        roster.set_pseudo("Bob", "Abc")
        self.dlg._refresh()

        self.dlg._sort_roster_by("pseudo")

        self.assertEqual(self.dlg.roster_sort["column"], "pseudo")

    def test_en_tete_pseudo_affiche_la_fleche_de_tri(self):
        roster.add_to_roster("Alice")
        self.dlg._refresh()
        self.dlg._sort_roster_by("pseudo")
        self.assertEqual(self.roster_tree.heading("pseudo")["text"], "Pseudo ▲")


if __name__ == "__main__":
    unittest.main()
