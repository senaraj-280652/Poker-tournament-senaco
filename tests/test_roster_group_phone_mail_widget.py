# -*- coding: utf-8 -*-
"""Répertoire — widgets d'édition des champs "Groupe"/"Téléphone"/"Mail"
(demande du 2026-09-20, Phase 1 du chantier "Sécurisation du Contrôle à
distance") : RosterManagerDialog._edit_group/_edit_phone/_edit_mail
(même principe que _edit_club, déjà existante — un bouton par champ,
jamais de formulaire multi-champs) et le Treeview à 5 colonnes
(name/club/group/phone/mail).

_edit_group/_edit_phone/_edit_mail sont testées SANS jamais ouvrir la
vraie fenêtre modale sous-jacente (ask_group_dialog / simpledialog.
askstring) — mêmes précautions que tests/test_roster_add_club_prompt.py
pour ask_club_dialog : jamais de wait_window()/grab_set() réellement
exécuté dans ce harnais (risque de segfault Tcl/Tk déjà documenté),
toujours mocké au niveau du module appelant."""
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
    """Doublure minimale de RosterManagerDialog pour _edit_group/_edit_
    phone/_edit_mail : seuls _selected_name() et _refresh() sont lus/
    appelés par ces méthodes — même style que _FakeRosterDialog dans
    tests/test_roster_add_club_prompt.py."""

    def __init__(self, selected_name=None):
        self._selected = selected_name
        self.refresh_calls = 0

    def _selected_name(self):
        return self._selected

    def _refresh(self):
        self.refresh_calls += 1


class RosterFieldEditMethodsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_group_phone_mail_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(patcher.stop)
        patcher.start()


class EditGroupTest(RosterFieldEditMethodsTestCase):
    def test_aucune_selection_naffiche_rien_ne_leve_pas(self):
        dialog = _FakeRosterDialog(selected_name=None)
        with patch.object(main, "ask_group_dialog") as mock_dialog:
            with patch.object(main.messagebox, "showinfo") as mock_info:
                main.RosterManagerDialog._edit_group(dialog)
        mock_dialog.assert_not_called()
        mock_info.assert_called_once()
        self.assertEqual(dialog.refresh_calls, 0)

    def test_validation_admin_persiste_et_rafraichit(self):
        roster.add_to_roster("Alice")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main, "ask_group_dialog", return_value="ADMIN") as mock_dialog:
            main.RosterManagerDialog._edit_group(dialog)
        mock_dialog.assert_called_once_with(dialog, title="Groupe de Alice", current_group="")
        self.assertEqual(roster.get_group("Alice"), "ADMIN")
        self.assertEqual(dialog.refresh_calls, 1)

    def test_validation_dirto_persiste(self):
        roster.add_to_roster("Bob")
        dialog = _FakeRosterDialog(selected_name="Bob")
        with patch.object(main, "ask_group_dialog", return_value="DIRTO"):
            main.RosterManagerDialog._edit_group(dialog)
        self.assertEqual(roster.get_group("Bob"), "DIRTO")

    def test_validation_non_classe_efface_le_groupe(self):
        roster.set_group("Alice", "ADMIN")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main, "ask_group_dialog", return_value=""):
            main.RosterManagerDialog._edit_group(dialog)
        self.assertEqual(roster.get_group("Alice"), "")

    def test_annulation_ne_change_rien(self):
        """`ask_group_dialog` renvoie None si annulé/fermé (voir sa
        docstring) — distinct de "" (choix explicite "non classé")."""
        roster.set_group("Alice", "ADMIN")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main, "ask_group_dialog", return_value=None):
            main.RosterManagerDialog._edit_group(dialog)
        self.assertEqual(roster.get_group("Alice"), "ADMIN")  # inchangé
        self.assertEqual(dialog.refresh_calls, 0)

    def test_pre_rempli_avec_le_groupe_courant(self):
        roster.set_group("Alice", "DIRTO")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main, "ask_group_dialog", return_value="DIRTO") as mock_dialog:
            main.RosterManagerDialog._edit_group(dialog)
        mock_dialog.assert_called_once_with(dialog, title="Groupe de Alice", current_group="DIRTO")


class EditPhoneTest(RosterFieldEditMethodsTestCase):
    def test_aucune_selection_ne_leve_pas(self):
        dialog = _FakeRosterDialog(selected_name=None)
        with patch.object(main.simpledialog, "askstring") as mock_ask:
            with patch.object(main.messagebox, "showinfo"):
                main.RosterManagerDialog._edit_phone(dialog)
        mock_ask.assert_not_called()

    def test_validation_persiste_et_rafraichit(self):
        roster.add_to_roster("Alice")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value="0102030405"):
            main.RosterManagerDialog._edit_phone(dialog)
        self.assertEqual(roster.get_phone("Alice"), "0102030405")
        self.assertEqual(dialog.refresh_calls, 1)

    def test_annulation_none_ne_change_rien(self):
        roster.set_phone("Alice", "0102030405")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value=None):
            main.RosterManagerDialog._edit_phone(dialog)
        self.assertEqual(roster.get_phone("Alice"), "0102030405")
        self.assertEqual(dialog.refresh_calls, 0)

    def test_validation_chaine_vide_efface(self):
        roster.set_phone("Alice", "0102030405")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value=""):
            main.RosterManagerDialog._edit_phone(dialog)
        self.assertEqual(roster.get_phone("Alice"), "")

    def test_espaces_superflus_retires(self):
        roster.add_to_roster("Alice")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value="  0102030405  "):
            main.RosterManagerDialog._edit_phone(dialog)
        self.assertEqual(roster.get_phone("Alice"), "0102030405")


class EditMailTest(RosterFieldEditMethodsTestCase):
    def test_validation_persiste(self):
        roster.add_to_roster("Alice")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value="alice@cpc.fr"):
            main.RosterManagerDialog._edit_mail(dialog)
        self.assertEqual(roster.get_mail("Alice"), "alice@cpc.fr")

    def test_annulation_ne_change_rien(self):
        roster.set_mail("Alice", "alice@cpc.fr")
        dialog = _FakeRosterDialog(selected_name="Alice")
        with patch.object(main.simpledialog, "askstring", return_value=None):
            main.RosterManagerDialog._edit_mail(dialog)
        self.assertEqual(roster.get_mail("Alice"), "alice@cpc.fr")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RosterTreeColumnsTest(unittest.TestCase):
    """Treeview à 5 colonnes (name/club/group/phone/mail) : même style de
    harnais minimal réel que RosterSelectsNewMemberTest dans tests/
    test_roster_add_club_prompt.py — méthodes RÉELLES de
    RosterManagerDialog greffées sur un SimpleNamespace, jamais
    réimplémentées. Un seul tk.Tk() pour toute la classe."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_tree_columns_test_")
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
            self.root, columns=("name", "club", "group", "phone", "mail"), show="tree headings",
        )
        for col, label in (("name", "Nom"), ("club", "Club"), ("group", "Groupe"),
                           ("phone", "Téléphone"), ("mail", "Mail")):
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

    def test_refresh_peuple_group_phone_mail_dans_le_tree(self):
        roster.set_group("Alice", "ADMIN")
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "alice@cpc.fr")

        self.dlg._refresh()

        self.assertEqual(self.roster_tree.set("Alice", "group"), "ADMIN")
        self.assertEqual(self.roster_tree.set("Alice", "phone"), "0102030405")
        self.assertEqual(self.roster_tree.set("Alice", "mail"), "alice@cpc.fr")

    def test_refresh_personne_non_classee_colonnes_vides_pas_derreur(self):
        roster.add_to_roster("Bob")  # jamais classé, ancienne fiche typique

        self.dlg._refresh()  # ne doit pas lever

        self.assertEqual(self.roster_tree.set("Bob", "group"), "")
        self.assertEqual(self.roster_tree.set("Bob", "phone"), "")
        self.assertEqual(self.roster_tree.set("Bob", "mail"), "")

    def test_tri_par_groupe_ne_leve_pas(self):
        roster.set_group("Alice", "ADMIN")
        roster.set_group("Bob", "DIRTO")
        self.dlg._refresh()

        self.dlg._sort_roster_by("group")  # ne doit pas lever TclError

        self.assertEqual(self.dlg.roster_sort["column"], "group")

    def test_en_tete_groupe_affiche_la_fleche_de_tri(self):
        roster.add_to_roster("Alice")
        self.dlg._refresh()
        self.dlg._sort_roster_by("group")
        self.assertEqual(self.roster_tree.heading("group")["text"], "Groupe ▲")


if __name__ == "__main__":
    unittest.main()
