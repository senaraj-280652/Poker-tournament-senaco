# -*- coding: utf-8 -*-
"""Bouton "Supprimer" du Répertoire (demande du 2026-10-15) :

1. Un nouveau bouton "Supprimer" est ajouté immédiatement à côté du
   bouton "Importer les joueurs d'un tournoi existant..." — appelle la
   MÊME méthode _delete que le bouton déjà existant plus bas, jamais
   une logique dupliquée (voir ButtonPlacementStructuralTest).
2. roster_tree passe en sélection MULTIPLE (selectmode="extended") pour
   permettre de supprimer "le ou les joueurs sélectionnés" en une seule
   confirmation, qui les nomme TOUS.
3. Aucune sélection -> aucune suppression, aucune confirmation affichée.
4. Aucune suppression ne touche à un quelconque fichier .tournoi
   (résultats/classements/participations/historique) — roster.py et
   player_photos.py sont entièrement indépendants des tournois déjà
   créés (voir RosterNeverTouchesTournamentDataTest).
5. Répertoire actualisé après suppression (self._refresh()).
6. Les 6 actions à CIBLE UNIQUE (Renommer, Modifier le club/groupe/
   téléphone/mail/pseudo) refusent désormais d'agir silencieusement sur
   la première ligne si PLUSIEURS sont cochées — message dédié, aucune
   action — au lieu de l'ambiguïté qu'introduirait silencieusement le
   passage en sélection multiple sans cette garde.

Même conventions déjà établies dans tests/test_roster_add_club_prompt.py
(RosterManagerDialog réel, Tk réel, roster.py/export_prefs.py redirigés
vers des fichiers temporaires)."""
import ast
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402

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


class _StubApp:
    db = None


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RosterDeleteMultiSelectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_delete_multiselect_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(roster, "_roster_path", return_value=roster_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self.dialog = main.RosterManagerDialog(self.root, _StubApp())
        self.addCleanup(lambda: cleanup_tk(self, "dialog"))

    def test_selectmode_extended(self):
        self.assertEqual(str(self.dialog.roster_tree.cget("selectmode")), "extended")

    def test_aucune_selection_ne_supprime_rien_ni_naffiche_de_confirmation(self):
        roster.add_to_roster("Alice")
        with patch.object(main.messagebox, "askyesno") as mock_yn:
            self.dialog._delete()
        mock_yn.assert_not_called()
        self.assertIn("Alice", roster.load_roster())

    def test_annulation_de_la_confirmation_ne_supprime_rien(self):
        roster.add_to_roster("Alice")
        self.dialog._refresh()
        self.dialog.roster_tree.selection_set("Alice")
        with patch.object(main.messagebox, "askyesno", return_value=False):
            self.dialog._delete()
        self.assertIn("Alice", roster.load_roster())

    def test_une_seule_selection_confirmation_singuliere(self):
        roster.add_to_roster("Alice")
        self.dialog._refresh()
        self.dialog.roster_tree.selection_set("Alice")
        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_yn:
            self.dialog._delete()
        question = mock_yn.call_args[0][1]
        self.assertIn("Retirer Alice du répertoire ?", question)
        self.assertNotIn("Alice", roster.load_roster())

    def test_plusieurs_selections_confirmation_nomme_tous_les_joueurs(self):
        for name in ("Alice", "Bob", "Chris"):
            roster.add_to_roster(name)
        self.dialog._refresh()
        self.dialog.roster_tree.selection_set("Alice", "Bob")
        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_yn:
            self.dialog._delete()
        question = mock_yn.call_args[0][1]
        self.assertIn("Alice", question)
        self.assertIn("Bob", question)
        remaining = sorted(roster.load_roster())
        self.assertEqual(remaining, ["Chris"])

    def test_refresh_appele_une_seule_fois_apres_suppression_multiple(self):
        for name in ("Alice", "Bob"):
            roster.add_to_roster(name)
        self.dialog._refresh()
        self.dialog.roster_tree.selection_set("Alice", "Bob")
        calls = []
        original_refresh = self.dialog._refresh
        self.dialog._refresh = lambda: (calls.append(1), original_refresh())[-1]
        with patch.object(main.messagebox, "askyesno", return_value=True):
            self.dialog._delete()
        self.assertEqual(len(calls), 1)

    def test_photo_supprimee_avec_le_joueur(self):
        roster.add_to_roster("Alice")
        self.dialog._refresh()
        self.dialog.roster_tree.selection_set("Alice")
        with patch.object(main.messagebox, "askyesno", return_value=True), \
             patch.object(main.player_photos, "delete_photo") as mock_delete_photo:
            self.dialog._delete()
        mock_delete_photo.assert_called_once_with("Alice")

    def test_joueur_absent_du_repertoire_apres_suppression_najamais_rafraichi_a_tort(self):
        """Après suppression, le joueur retiré ne doit plus apparaître
        dans le Treeview (Répertoire bien actualisé, demande explicite)."""
        roster.add_to_roster("Alice")
        roster.add_to_roster("Bob")
        self.dialog._refresh()
        self.dialog.roster_tree.selection_set("Alice")
        with patch.object(main.messagebox, "askyesno", return_value=True):
            self.dialog._delete()
        self.assertFalse(self.dialog.roster_tree.exists("Alice"))
        self.assertTrue(self.dialog.roster_tree.exists("Bob"))


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class SingleTargetActionsGuardedAgainstMultiSelectionTest(unittest.TestCase):
    """Les 6 actions à cible unique refusent désormais d'agir sur la
    première ligne si PLUSIEURS sont cochées, au lieu d'une ambiguïté
    silencieuse introduite par le passage en sélection multiple."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_guard_multiselect_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(roster, "_roster_path", return_value=roster_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        roster.add_to_roster("Alice")
        roster.add_to_roster("Bob")
        self.dialog = main.RosterManagerDialog(self.root, _StubApp())
        self.addCleanup(lambda: cleanup_tk(self, "dialog"))
        self.dialog.roster_tree.selection_set("Alice", "Bob")

    def test_require_single_selection_refuse_plusieurs_et_affiche_un_message(self):
        with patch.object(main.messagebox, "showinfo") as mock_info:
            result = self.dialog._require_single_selection()
        self.assertIsNone(result)
        mock_info.assert_called_once()
        self.assertIn("un seul joueur", mock_info.call_args[0][1])

    def test_rename_najamais_declenche_avec_plusieurs_selections(self):
        with patch.object(main.messagebox, "showinfo"), \
             patch.object(main.simpledialog, "askstring") as mock_ask:
            self.dialog._rename()
        mock_ask.assert_not_called()

    def test_edit_club_najamais_declenche_avec_plusieurs_selections(self):
        with patch.object(main.messagebox, "showinfo"), \
             patch.object(main, "ask_club_dialog") as mock_dialog:
            self.dialog._edit_club()
        mock_dialog.assert_not_called()

    def test_edit_group_najamais_declenche_avec_plusieurs_selections(self):
        with patch.object(main.messagebox, "showinfo"), \
             patch.object(main, "ask_group_dialog") as mock_dialog:
            self.dialog._edit_group()
        mock_dialog.assert_not_called()

    def test_edit_phone_najamais_declenche_avec_plusieurs_selections(self):
        with patch.object(main.messagebox, "showinfo"), \
             patch.object(main.simpledialog, "askstring") as mock_ask:
            self.dialog._edit_phone()
        mock_ask.assert_not_called()

    def test_edit_mail_najamais_declenche_avec_plusieurs_selections(self):
        with patch.object(main.messagebox, "showinfo"), \
             patch.object(main.simpledialog, "askstring") as mock_ask:
            self.dialog._edit_mail()
        mock_ask.assert_not_called()

    def test_edit_pseudo_najamais_declenche_avec_plusieurs_selections(self):
        with patch.object(main.messagebox, "showinfo"), \
             patch.object(main.simpledialog, "askstring") as mock_ask:
            self.dialog._edit_pseudo()
        mock_ask.assert_not_called()

    def test_une_seule_selection_fonctionne_toujours_normalement(self):
        """Non-régression : avec UNE SEULE ligne cochée, le comportement
        reste exactement celui d'avant ce correctif."""
        self.dialog.roster_tree.selection_set("Alice")
        with patch.object(main.simpledialog, "askstring", return_value="Toto") as mock_ask:
            self.dialog._edit_phone()
        mock_ask.assert_called_once()


class RosterNeverTouchesTournamentDataTest(unittest.TestCase):
    """Vérification STRUCTURELLE (AST) : _delete ne référence jamais
    self.db ni aucune fonction de la base de données des tournois —
    seuls roster.py/player_photos.py (indépendants des .tournoi) sont
    appelés, exactement comme avant ce correctif."""

    def setUp(self):
        self.source = _main_source()
        self.tree = ast.parse(self.source, filename="main.py")

    def _find_method(self, class_name, method_name):
        cls_node = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.ClassDef) and n.name == class_name
        )
        return next(
            n for n in ast.walk(cls_node)
            if isinstance(n, ast.FunctionDef) and n.name == method_name
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

    def test_delete_najamais_self_db(self):
        func = self._find_method("RosterManagerDialog", "_delete")
        code_only = self._body_without_docstring(func)
        self.assertNotIn("self.db", code_only)
        self.assertNotIn("self.app.db", code_only)

    def test_delete_utilise_roster_et_player_photos_uniquement(self):
        func = self._find_method("RosterManagerDialog", "_delete")
        called_attrs = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        allowed = {
            "remove_from_roster", "delete_photo", "askyesno", "join",
            "_selected_names", "_refresh",
        }
        self.assertTrue(called_attrs <= allowed, called_attrs)


class ButtonPlacementStructuralTest(unittest.TestCase):
    """Le nouveau bouton "Supprimer" (demande du 2026-10-15) est bien
    placé immédiatement à côté d'"Importer les joueurs d'un tournoi
    existant...", et appelle la MÊME méthode _delete que le bouton déjà
    existant plus bas — jamais une seconde implémentation."""

    def setUp(self):
        self.source = _main_source()

    def test_bouton_supprimer_juste_apres_importer(self):
        idx_importer = self.source.index('text="Importer les joueurs d\'un tournoi existant...')
        # "command=self._delete)" (fermeture immédiate) : exclut
        # self._delete_selected (onglet Joueurs/modèles, méthode SANS
        # rapport, dont le nom commence par le même préfixe).
        idx_supprimer_add_frame = self.source.index('command=self._delete)', idx_importer)
        # Rien d'autre qu'un bloc court (le ttk.Button lui-même et son
        # commentaire) ne doit séparer les deux dans le code source.
        between = self.source[idx_importer:idx_supprimer_add_frame]
        self.assertLess(len(between), 600, between)

    def test_deux_boutons_supprimer_appellent_la_meme_methode(self):
        occurrences = self.source.count('text="Supprimer", command=self._delete)')
        self.assertEqual(occurrences, 2)


if __name__ == "__main__":
    unittest.main()
