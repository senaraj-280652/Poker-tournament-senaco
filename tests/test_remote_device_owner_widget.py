# -*- coding: utf-8 -*-
"""Panneau "Téléphones autorisés" de Paramètres — liaison propriétaire
(demande du 2026-09-20, Phase 2) : App._refresh_remote_devices_panel
affiche désormais, pour chaque appareil approuvé, son propriétaire
(résolu EN DIRECT via roster.get_group, jamais mis en cache) et des
boutons Attribuer/Changer/Retirer — App._on_assign_remote_device_owner/
_on_clear_remote_device_owner appellent open_windows comme prévu.

Même harnais que tests/test_remote_device_popup_window.py (racine Tk
réelle greffée des vraies méthodes App, open_windows redirigé vers un
dossier temporaire) — étend juste la liste des méthodes greffées et
ajoute roster (redirigé lui aussi vers un fichier temporaire, jamais
~/.poker_tournament/roster.json)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


_INSTANCE_METHODS_TO_GRAFT = (
    "_refresh_remote_devices_panel",
    "_on_revoke_remote_device",
    "_on_rename_remote_device",
    "_on_assign_remote_device_owner",
    "_on_clear_remote_device_owner",
)
_STATIC_METHODS_TO_GRAFT = (
    "_remote_devices_signature",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RemoteDeviceOwnerWidgetTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self._tmp = tempfile.TemporaryDirectory(prefix="remote_device_owner_widget_test_")
        self.addCleanup(self._tmp.cleanup)
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        for target in (
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(open_windows, "list_open_paths", return_value=["/fake/a.tournoi"]),
            patch.object(roster, "_roster_path", return_value=roster_path),
        ):
            self.addCleanup(target.stop)
            target.start()

        self.win = self.root
        self.win.remote_devices_container = ttk.Frame(self.root)
        self.win._last_remote_devices_panel_signature = None

        for name in _INSTANCE_METHODS_TO_GRAFT:
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in _STATIC_METHODS_TO_GRAFT:
            setattr(self.win, name, getattr(main.App, name))

        self.addCleanup(lambda: cleanup_tk(self, "root", "win", "remote_devices_container"))

    def _approve(self, ip="192.168.0.191"):
        bid = os.urandom(16).hex()
        open_windows.register_device_attempt(bid, ip)
        open_windows.approve_remote_device(bid, label="Téléphone Test")
        return bid

    def _row_texts(self):
        """Concatène le texte de tous les Label du panneau, pour des
        assertions simples sans dépendre de la structure exacte des
        sous-Frames."""
        texts = []

        def _walk(widget):
            if isinstance(widget, (ttk.Label, tk.Label)):
                texts.append(widget.cget("text"))
            for child in widget.winfo_children():
                _walk(child)

        _walk(self.win.remote_devices_container)
        return texts

    def _button_texts(self):
        texts = []

        def _walk(widget):
            if isinstance(widget, (ttk.Button, tk.Button)):
                texts.append(widget.cget("text"))
            for child in widget.winfo_children():
                _walk(child)

        _walk(self.win.remote_devices_container)
        return texts


class AffichageProprietaireTest(RemoteDeviceOwnerWidgetTest):
    def test_appareil_non_lie_affiche_le_message_exact_demande(self):
        self._approve()
        self.win._refresh_remote_devices_panel()
        self.assertIn("Aucune fonction autorisée — contactez un ADMIN", self._row_texts())

    def test_appareil_non_lie_propose_attribuer_jamais_retirer(self):
        self._approve()
        self.win._refresh_remote_devices_panel()
        buttons = self._button_texts()
        self.assertIn("Attribuer...", buttons)
        self.assertNotIn("Retirer la liaison", buttons)

    def test_appareil_lie_affiche_nom_et_groupe(self):
        bid = self._approve()
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        open_windows.set_remote_device_owner(bid, "Alice")

        self.win._refresh_remote_devices_panel()

        self.assertIn("Propriétaire : Alice (ADMIN)", self._row_texts())

    def test_appareil_lie_dirto_affiche_le_bon_groupe(self):
        bid = self._approve()
        roster.set_group("Bob", roster.ROSTER_GROUP_DIRTO)
        open_windows.set_remote_device_owner(bid, "Bob")

        self.win._refresh_remote_devices_panel()

        self.assertIn("Propriétaire : Bob (DIRTO)", self._row_texts())

    def test_appareil_lie_propose_changer_et_retirer(self):
        bid = self._approve()
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        open_windows.set_remote_device_owner(bid, "Alice")

        self.win._refresh_remote_devices_panel()

        buttons = self._button_texts()
        self.assertIn("Changer...", buttons)
        self.assertIn("Retirer la liaison", buttons)
        self.assertNotIn("Attribuer...", buttons)

    def test_groupe_resolu_en_direct_pas_en_cache(self):
        """Le groupe affiché doit refléter l'état ACTUEL du Répertoire,
        jamais une valeur figée au moment de l'attribution — changer le
        groupe d'Alice APRÈS l'avoir liée à l'appareil doit se répercuter
        au prochain rafraîchissement, sans aucune action sur le lien
        appareil<->propriétaire lui-même."""
        bid = self._approve()
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        open_windows.set_remote_device_owner(bid, "Alice")
        self.win._refresh_remote_devices_panel()
        self.assertIn("Propriétaire : Alice (ADMIN)", self._row_texts())

        roster.set_group("Alice", roster.ROSTER_GROUP_DIRTO)  # reclassée dans le Répertoire
        self.win._refresh_remote_devices_panel()

        self.assertIn("Propriétaire : Alice (DIRTO)", self._row_texts())
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")  # lien inchangé

    def test_proprietaire_devenu_invalide_naffiche_jamais_dexception(self):
        """Personne retirée du Répertoire (ou son groupe effacé) après
        avoir été liée à un appareil : ne doit jamais lever, doit rester
        visible comme nécessitant une correction plutôt que de
        disparaître silencieusement."""
        bid = self._approve()
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        open_windows.set_remote_device_owner(bid, "Alice")
        roster.set_group("Alice", "")  # reclassée "non classé"

        self.win._refresh_remote_devices_panel()  # ne doit pas lever

        self.assertIn(
            "Propriétaire : Alice (⚠ absent du Répertoire ADMIN/DIRTO)", self._row_texts(),
        )


class AssignerRetirerTest(RemoteDeviceOwnerWidgetTest):
    def test_assigner_appelle_set_remote_device_owner(self):
        bid = self._approve()
        with patch.object(main, "ask_device_owner_dialog", return_value="Alice") as mock_dialog:
            self.win._on_assign_remote_device_owner(bid)
        mock_dialog.assert_called_once_with(self.win, current_owner="")
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")

    def test_assigner_transmet_le_proprietaire_courant_au_dialogue(self):
        bid = self._approve()
        with patch.object(main, "ask_device_owner_dialog", return_value="Bob") as mock_dialog:
            self.win._on_assign_remote_device_owner(bid, current_owner="Alice")
        mock_dialog.assert_called_once_with(self.win, current_owner="Alice")

    def test_annulation_none_ne_change_rien(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        with patch.object(main, "ask_device_owner_dialog", return_value=None):
            self.win._on_assign_remote_device_owner(bid, current_owner="Alice")
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")

    def test_dialogue_renvoie_chaine_vide_retire_la_liaison(self):
        """"(Aucun...)" dans ask_device_owner_dialog renvoie "" — doit
        retirer la liaison exactement comme le bouton dédié."""
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        with patch.object(main, "ask_device_owner_dialog", return_value=""):
            self.win._on_assign_remote_device_owner(bid, current_owner="Alice")
        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_bouton_retirer_retire_sans_dialogue(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        self.win._on_clear_remote_device_owner(bid)
        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_retirer_ne_revoque_pas_lappareil(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        self.win._on_clear_remote_device_owner(bid)
        approved_ids = [d["browser_id"] for d in open_windows.list_approved_remote_devices()]
        self.assertIn(bid, approved_ids)

    def test_panneau_rafraichi_apres_attribution(self):
        bid = self._approve()
        with patch.object(main, "ask_device_owner_dialog", return_value="Alice"):
            self.win._on_assign_remote_device_owner(bid)
        self.assertIn("Propriétaire : Alice", " ".join(self._row_texts()))


class SignatureIncluetProprietaireTest(RemoteDeviceOwnerWidgetTest):
    def test_signature_change_quand_le_proprietaire_change(self):
        bid = self._approve()
        approved = open_windows.list_approved_remote_devices()
        sig_avant = self.win._remote_devices_signature(approved)

        open_windows.set_remote_device_owner(bid, "Alice")
        approved = open_windows.list_approved_remote_devices()
        sig_apres = self.win._remote_devices_signature(approved)

        self.assertNotEqual(sig_avant, sig_apres)

    def test_signature_stable_si_rien_ne_change(self):
        self._approve()
        approved = open_windows.list_approved_remote_devices()
        sig1 = self.win._remote_devices_signature(approved)
        sig2 = self.win._remote_devices_signature(approved)
        self.assertEqual(sig1, sig2)


class NonRegressionRevoqueRenommeTest(RemoteDeviceOwnerWidgetTest):
    """Révoquer/renommer un appareil doit continuer à fonctionner
    exactement comme avant ce chantier."""

    def test_revoquer_fonctionne_toujours(self):
        bid = self._approve()
        self.win._on_revoke_remote_device(bid)
        self.assertEqual(open_windows.list_approved_remote_devices(), [])

    def test_revoquer_un_appareil_lie_retire_aussi_le_lien(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        self.win._on_revoke_remote_device(bid)
        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_renommer_fonctionne_toujours(self):
        bid = self._approve()
        label_var = tk.StringVar(value="Nouveau nom")
        self.win._on_rename_remote_device(bid, label_var)
        devices = {d["browser_id"]: d["label"] for d in open_windows.list_approved_remote_devices()}
        self.assertEqual(devices[bid], "Nouveau nom")

    def test_renommer_ninterfere_pas_avec_le_proprietaire(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        label_var = tk.StringVar(value="Nouveau nom")
        self.win._on_rename_remote_device(bid, label_var)
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")


class PanneauVideTest(RemoteDeviceOwnerWidgetTest):
    def test_aucun_appareil_naffiche_toujours_le_message_dorigine(self):
        self.win._refresh_remote_devices_panel()
        self.assertIn("Aucun téléphone approuvé pour l'instant.", self._row_texts())


if __name__ == "__main__":
    unittest.main()
