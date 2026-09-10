# -*- coding: utf-8 -*-
"""Couverture automatisée de la fenêtre flottante de demande de
téléphone (demande du 2026-09-09, retour DÉFINITIF à une fenêtre
Tkinter indépendante après plusieurs tentatives d'intégration dans la
grille de Paramètres, toutes abandonnées — la dernière ayant élargi la
colonne gauche au point de repousser la colonne droite hors écran) :
remplace tests/test_remote_device_pending_panel.py (supprimé, plus
aucun panneau intégré à tester).

- RemoteDeviceRequestWindow (tk.Toplevel) présente UNE demande à la
  fois — UNE SEULE instance à la fois (voir App._remote_device_popup),
  repeuplée en place pour la demande suivante plutôt que détruite/
  recréée ;
- Autoriser/Refuser appellent open_windows comme avant (mécanisme
  d'authentification/approbation/révocation INCHANGÉ, non retesté ici —
  voir tests/test_remote_control_auth_backend.py) ;
- "Plus tard" ferme la fenêtre SANS approuver/révoquer — le badge 🔔
  reste, la demande redevient affichable au retour sur Paramètres ;
- la position de la fenêtre est mémorisée dans export_prefs (JAMAIS le
  fichier de session éphémère remote_control_auth.json, aucun secret),
  restaurée si elle reste visible à l'écran, sinon une position par
  défaut sûre est utilisée ;
- la liste "Téléphones" de Paramètres (appareils déjà approuvés) et la
  grille de _build_settings_tab ne sont plus jamais concernées par une
  demande en attente.

Utilise un VRAI tk.Tk() caché (comme tests/test_tick_never_stops_
scheduling.py) — nécessaire pour un vrai tk.Toplevel/ttk.Notebook.
open_windows ET export_prefs redirigés vers un dossier temporaire
dédié, jamais ~/.poker_tournament."""
import ast
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk

import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


_INSTANCE_METHODS_TO_GRAFT = (
    "_check_remote_device_requests",
    "_refresh_remote_devices_panel",
    "_refresh_remote_device_popup",
    "_close_remote_device_popup",
    "_on_remote_device_popup_approve",
    "_on_remote_device_popup_refuse",
    "_on_remote_device_popup_later",
    "_on_revoke_remote_device",
    "_on_rename_remote_device",
    "_is_settings_tab_active",
    "_update_settings_tab_badge",
    "_remote_device_popup_position",
    "_save_remote_device_popup_position",
)
_STATIC_METHODS_TO_GRAFT = (
    "_remote_devices_signature",
    "_is_position_onscreen",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RemoteDevicePopupWindowTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

        self._tmp = tempfile.TemporaryDirectory(prefix="remote_device_popup_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        export_prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(export_prefs, "_prefs_path", return_value=export_prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        # -- Greffe sur la racine RÉELLE (voir tests/test_tick_never_
        # stops_scheduling.py, même principe) : un vrai Notebook à deux
        # onglets (pour _is_settings_tab_active/_update_settings_tab_
        # badge), un vrai conteneur pour "Téléphones" (approuvés). -------
        self.win = self.root
        self.notebook = ttk.Notebook(self.root)
        self.other_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.other_tab, text="Joueurs")
        self.notebook.add(self.settings_tab, text="Paramètres")
        self.notebook.select(self.other_tab)
        self.win.notebook = self.notebook
        self.win.settings_tab = self.settings_tab
        self.win.remote_devices_container = ttk.Frame(self.settings_tab)

        self.win._remote_device_snoozed_keys = set()
        self.win._remote_device_popup = None
        self.win._remote_device_popup_current_key = None
        self.win._remote_device_popup_current_browser_id = None
        self.win._last_settings_tab_active = False
        self.win._last_remote_devices_panel_signature = None

        for name in _INSTANCE_METHODS_TO_GRAFT:
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in _STATIC_METHODS_TO_GRAFT:
            # @staticmethod sur App : déjà une fonction "nue" une fois
            # accédée via la classe, jamais de types.MethodType.
            setattr(self.win, name, getattr(main.App, name))

    def tearDown(self):
        popup = self.win._remote_device_popup
        if popup is not None:
            try:
                if popup.winfo_exists():
                    popup.destroy()
            except tk.TclError:
                pass

    def _register(self, ip="192.168.0.191"):
        bid = os.urandom(16).hex()
        open_windows.register_device_attempt(bid, ip)
        return bid

    def _switch_to(self, tab):
        self.notebook.select(tab)


class SingleWindowTest(RemoteDevicePopupWindowTest):
    def test_une_seule_fenetre_pour_plusieurs_demandes_deja_en_attente(self):
        """Deux demandes DÉJÀ toutes les deux en attente au moment où la
        première est traitée : la fenêtre doit être RÉUTILISÉE pour
        afficher la seconde, jamais détruite/recréée (voir _refresh_
        remote_device_popup, repeuplée en place tant qu'il reste une
        demande à montrer immédiatement après traitement de la
        précédente)."""
        bid1 = self._register("1.1.1.1")
        time.sleep(0.01)
        self._register("2.2.2.2")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup1 = self.win._remote_device_popup
        self.assertIsInstance(popup1, main.RemoteDeviceRequestWindow)
        self.assertIn("1.1.1.1", popup1._info_lbl.cget("text"))

        self.win._on_remote_device_popup_approve()
        self.assertEqual(open_windows.get_device_auth_status(bid1), "approved")

        popup2 = self.win._remote_device_popup
        self.assertIs(popup2, popup1, "la MÊME fenêtre doit être réutilisée, jamais recréée")
        self.assertIn("2.2.2.2", popup2._info_lbl.cget("text"))

    def test_nouvelle_fenetre_si_la_file_etait_devenue_vide_entre_temps(self):
        """Non-régression dans l'autre sens : si la file redevient VIDE
        (traitement de l'unique demande, fenêtre fermée) avant qu'une
        toute NOUVELLE demande n'arrive, une NOUVELLE fenêtre est
        naturellement recréée — comportement correct, pas un bug (rien
        n'impose de garder une fenêtre fermée "en réserve")."""
        bid1 = self._register("1.1.1.1")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup1 = self.win._remote_device_popup

        self.win._on_remote_device_popup_approve()
        self.assertEqual(open_windows.get_device_auth_status(bid1), "approved")
        self.assertIsNone(self.win._remote_device_popup, "plus aucune demande : la fenêtre doit s'être fermée")

        self._register("2.2.2.2")
        self.win._check_remote_device_requests()
        popup2 = self.win._remote_device_popup
        self.assertIsNotNone(popup2)
        self.assertIsNot(popup2, popup1, "l'ancienne fenêtre était déjà fermée, une nouvelle doit s'ouvrir")
        self.assertIn("2.2.2.2", popup2._info_lbl.cget("text"))

    def test_aucune_fenetre_si_rien_nest_en_attente(self):
        self.win._check_remote_device_requests()
        self.assertIsNone(self.win._remote_device_popup)


class ApproveRefuseTest(RemoteDevicePopupWindowTest):
    def test_autoriser_approuve_ferme_ou_avance_et_alimente_telephones(self):
        bid = self._register("3.3.3.3")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        self.win._remote_device_popup.show_request("ABCDEF", "3.3.3.3", "Salle 1")
        self.win._remote_device_popup._label_var.set("Salle 1 - Jean")

        self.win._on_remote_device_popup_approve()

        self.assertEqual(open_windows.get_device_auth_status(bid), "approved")
        approved = open_windows.list_approved_remote_devices()
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["label"], "Salle 1 - Jean")
        self.assertIsNone(self.win._remote_device_popup, "plus aucune demande -> fenêtre fermée")

    def test_refuser_revoque_et_naffecte_pas_telephones_approuves(self):
        bid = self._register("4.4.4.4")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        self.win._on_remote_device_popup_refuse()

        self.assertEqual(open_windows.get_device_auth_status(bid), "refused")
        self.assertEqual(open_windows.list_approved_remote_devices(), [])
        self.assertIsNone(self.win._remote_device_popup)

    def test_demande_suivante_affichee_immediatement_apres_traitement(self):
        bid1 = self._register("5.5.5.1")
        time.sleep(0.01)
        bid2 = self._register("5.5.5.2")

        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        self.assertIn("5.5.5.1", self.win._remote_device_popup._info_lbl.cget("text"))

        self.win._on_remote_device_popup_refuse()
        self.assertIn(
            "5.5.5.2", self.win._remote_device_popup._info_lbl.cget("text"),
            "la demande suivante doit être affichée immédiatement, sans attendre un nouveau sondage",
        )

        self.win._on_remote_device_popup_approve()
        self.assertIsNone(self.win._remote_device_popup)
        self.assertEqual(open_windows.get_device_auth_status(bid1), "refused")
        self.assertEqual(open_windows.get_device_auth_status(bid2), "approved")


class LaterSnoozeTest(RemoteDevicePopupWindowTest):
    def test_plus_tard_ferme_la_fenetre_sans_approuver_ni_revoquer(self):
        bid = self._register("6.6.6.6")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        self.win._on_remote_device_popup_later()

        self.assertIsNone(self.win._remote_device_popup)
        self.assertEqual(
            open_windows.get_device_auth_status(bid), "pending",
            "\"Plus tard\" ne doit ni approuver ni révoquer",
        )

    def test_badge_reste_apres_plus_tard(self):
        self._register("7.7.7.7")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        self.win._on_remote_device_popup_later()
        self.win._check_remote_device_requests()
        self.assertEqual(self.notebook.tab(self.settings_tab, "text"), "Paramètres 🔔")

    def test_demande_reapparait_au_retour_sur_parametres_sans_nouvelle_tentative(self):
        bid = self._register("8.8.8.8")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        device_before = open_windows.list_pending_remote_devices()[0]
        self.win._on_remote_device_popup_later()
        self.assertIsNone(self.win._remote_device_popup)

        # Reste fermée tant que Paramètres reste actif en continu.
        self.win._check_remote_device_requests()
        self.assertIsNone(self.win._remote_device_popup)

        # Quitte puis revient sur Paramètres, sans nouvelle tentative du
        # téléphone (même browser_id/requested_at).
        self._switch_to(self.other_tab)
        self.win._check_remote_device_requests()
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        self.assertIsNotNone(self.win._remote_device_popup)
        self.assertEqual(self.win._remote_device_popup_current_browser_id, bid)
        device_after = open_windows.list_pending_remote_devices()[0]
        self.assertEqual(device_after["requested_at"], device_before["requested_at"])


# =======================================================================
# Demande du 2026-09-09 : la fenêtre flottante ne doit être VISIBLE que
# lorsque l'onglet Paramètres est sélectionné — VISIBLE ⇔ Paramètres
# actif ET au moins une demande pending. Sur tout autre onglet (Joueurs
# ici, celui sélectionné par défaut dans setUp), elle doit être
# masquée (`withdraw`, jamais détruite ni recréée), la demande restant
# pending et le badge 🔔 restant affiché. `self.win._check_remote_
# device_requests()` reproduit ici ce que fait autant le sondage
# périodique (~2s, voir App._tick) que le nouveau déclenchement
# immédiat sur <<NotebookTabChanged>> (voir App._on_notebook_tab_
# changed) : les deux appellent EXACTEMENT la même méthode, donc la
# logique de visibilité elle-même n'a besoin d'être vérifiée qu'une
# fois, peu importe ce qui a déclenché l'appel.
# =======================================================================
class TabVisibilityTest(RemoteDevicePopupWindowTest):
    def _popup_is_visible(self):
        popup = self.win._remote_device_popup
        return popup is not None and popup.winfo_exists() and popup.state() != "withdrawn"

    def test_demande_recue_hors_parametres_popup_absent_cloche_presente(self):
        """setUp sélectionne déjà "Joueurs" par défaut — une demande qui
        arrive pendant qu'on y reste ne doit jamais créer la fenêtre."""
        self._register("20.0.0.1")
        self.win._check_remote_device_requests()
        self.assertFalse(self._popup_is_visible())
        self.assertEqual(self.notebook.tab(self.settings_tab, "text"), "Paramètres 🔔")

    def test_passage_joueurs_vers_parametres_rend_le_popup_visible(self):
        self._register("20.0.0.2")
        self.win._check_remote_device_requests()
        self.assertFalse(self._popup_is_visible())

        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        self.assertTrue(self._popup_is_visible())
        self.assertIn("20.0.0.2", self.win._remote_device_popup._info_lbl.cget("text"))

    def test_quitter_parametres_masque_le_popup_sans_supprimer_la_demande(self):
        bid = self._register("20.0.0.3")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        self.assertTrue(self._popup_is_visible())
        popup_while_visible = self.win._remote_device_popup

        self._switch_to(self.other_tab)
        self.win._check_remote_device_requests()

        self.assertIsNotNone(
            self.win._remote_device_popup,
            "la fenêtre doit seulement être MASQUÉE, jamais détruite",
        )
        self.assertIs(self.win._remote_device_popup, popup_while_visible)
        self.assertFalse(self._popup_is_visible())
        self.assertEqual(
            open_windows.get_device_auth_status(bid), "pending",
            "changer d'onglet ne doit JAMAIS être traité comme Refuser/Plus tard",
        )
        self.assertEqual(self.notebook.tab(self.settings_tab, "text"), "Paramètres 🔔", "le badge doit rester affiché")

    def test_retour_sur_parametres_reaffiche_la_meme_demande(self):
        bid = self._register("20.0.0.4")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        device_before = open_windows.list_pending_remote_devices()[0]

        self._switch_to(self.other_tab)
        self.win._check_remote_device_requests()
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        self.assertTrue(self._popup_is_visible())
        self.assertEqual(self.win._remote_device_popup_current_browser_id, bid)
        device_after = open_windows.list_pending_remote_devices()[0]
        self.assertEqual(
            device_after["requested_at"], device_before["requested_at"],
            "toujours la MÊME demande — aucune nouvelle tentative du téléphone requise",
        )

    def test_position_conservee_apres_masquage_puis_reaffichage(self):
        """Requirement explicite : ne PAS recalculer une nouvelle
        position si la fenêtre est simplement masquée puis réaffichée —
        vérifié ici en constatant que sa géométrie réelle est
        EXACTEMENT identique avant/après (jamais recréée, voir aussi
        SingleWindowTest pour l'identité de l'objet)."""
        self._register("20.0.0.5")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup = self.win._remote_device_popup
        geometry_before = popup.geometry()

        self._switch_to(self.other_tab)
        self.win._check_remote_device_requests()
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()

        self.assertIs(self.win._remote_device_popup, popup)
        self.assertEqual(popup.geometry(), geometry_before)

    def test_polling_sur_un_autre_onglet_ne_fait_jamais_apparaitre_le_popup(self):
        """Simule plusieurs sondages périodiques (~2s) consécutifs
        pendant que l'utilisateur reste sur "Joueurs" — la fenêtre ne
        doit JAMAIS apparaître, quel que soit le nombre de sondages."""
        self._register("20.0.0.6")
        for _ in range(5):
            self.win._check_remote_device_requests()
            self.assertFalse(self._popup_is_visible())

    def test_autoriser_et_refuser_fonctionnent_normalement_apres_reaffichage(self):
        """Non-régression : la nouvelle règle de visibilité ne doit rien
        changer au fonctionnement d'Autoriser/Refuser une fois la
        fenêtre effectivement visible."""
        bid1 = self._register("20.0.0.7")
        time.sleep(0.01)
        bid2 = self._register("20.0.0.8")
        self._switch_to(self.other_tab)
        self.win._check_remote_device_requests()
        self.assertFalse(self._popup_is_visible())

        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        self.assertTrue(self._popup_is_visible())

        self.win._on_remote_device_popup_approve()
        self.assertEqual(open_windows.get_device_auth_status(bid1), "approved")
        self.assertTrue(self._popup_is_visible(), "la demande suivante doit rester visible, toujours sur Paramètres")

        self.win._on_remote_device_popup_refuse()
        self.assertEqual(open_windows.get_device_auth_status(bid2), "refused")
        self.assertIsNone(self.win._remote_device_popup, "plus aucune demande : la fenêtre doit être fermée")


class NotebookTabChangedWiringTest(unittest.TestCase):
    """Vérifie le CÂBLAGE de <<NotebookTabChanged>> (analyse préalable :
    c'est l'événement virtuel émis par ttk.Notebook à chaque changement
    d'onglet sélectionné — clic, clavier, ou select() programmatique —
    déjà utilisé pour _refresh_all ; App._on_notebook_tab_changed
    appelle désormais aussi _check_remote_device_requests, pour un
    affichage/masquage IMMÉDIAT de la fenêtre flottante au changement
    d'onglet, sans attendre le sondage périodique) — au niveau du CODE
    SOURCE plutôt qu'un vrai Notebook complet (qui exigerait de
    construire tout un App réel, hors de portée raisonnable ici)."""

    def test_on_notebook_tab_changed_appelle_les_deux_rafraichissements(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=main_py)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_on_notebook_tab_changed"
        )
        calls = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertIn("_refresh_all", calls)
        self.assertIn("_check_remote_device_requests", calls)

    def test_notebook_est_bien_lie_a_on_notebook_tab_changed(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)', source)


class PopupGeometryTest(RemoteDevicePopupWindowTest):
    def test_position_sauvegardee_et_restauree(self):
        self._register("9.9.9.9")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup = self.win._remote_device_popup

        # Ferme (comme "Plus tard" — sauvegarde sa géométrie COURANTE au
        # passage, sans intérêt ici) PUIS impose la position à mémoriser
        # APRÈS cette fermeture, pour ne pas être écrasée par elle.
        self.win._on_remote_device_popup_later()
        self.win._save_remote_device_popup_position(321, 654)
        self.assertEqual(export_prefs.load_value("remote_device_popup_x"), 321)
        self.assertEqual(export_prefs.load_value("remote_device_popup_y"), 654)

        # Rouvre pour une nouvelle demande : la position mémorisée doit
        # être reprise.
        self._register("9.9.9.10")
        self.win._check_remote_device_requests()
        new_popup = self.win._remote_device_popup
        self.assertIsNot(new_popup, popup)
        x, y = self.win._remote_device_popup_position(new_popup)
        self.assertEqual((x, y), (321, 654))

    def test_position_hors_ecran_est_corrigee(self):
        export_prefs.save_value("remote_device_popup_x", 999999)
        export_prefs.save_value("remote_device_popup_y", 999999)
        self._register("10.10.10.10")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup = self.win._remote_device_popup
        x, y = self.win._remote_device_popup_position(popup)
        self.assertLess(x, 999999)
        self.assertLess(y, 999999)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)

    def test_position_non_numerique_est_ignoree(self):
        export_prefs.save_value("remote_device_popup_x", "pas un nombre")
        export_prefs.save_value("remote_device_popup_y", None)
        self._register("11.11.11.11")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup = self.win._remote_device_popup
        x, y = self.win._remote_device_popup_position(popup)
        self.assertIsInstance(x, int)
        self.assertIsInstance(y, int)

    def test_configure_de_la_fenetre_declenche_la_sauvegarde(self):
        self._register("12.12.12.12")
        self._switch_to(self.settings_tab)
        self.win._check_remote_device_requests()
        popup = self.win._remote_device_popup

        fake_event = type("FakeEvent", (), {"widget": popup})()
        popup.geometry("+150+250")
        popup._on_configure(fake_event)

        self.assertEqual(export_prefs.load_value("remote_device_popup_x"), popup.winfo_x())
        self.assertEqual(export_prefs.load_value("remote_device_popup_y"), popup.winfo_y())


class SettingsGridUntouchedTest(unittest.TestCase):
    """Demande du 2026-09-09 : plus aucune trace de l'intégration dans
    la grille de Paramètres (row/column/columnspan/sticky dédiés à un
    panneau de demande) — vérifié au niveau du CODE SOURCE, comme les
    anciens tests structurels supprimés avec tests/test_remote_device_
    pending_panel.py."""

    def test_aucune_reference_au_panneau_integre_dans_le_fichier(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            source = f.read()
        for forbidden in ("remote_pending_request_container", "_refresh_pending_request_panel"):
            self.assertNotIn(forbidden, source, f"code obsolète encore présent : {forbidden!r}")

    def test_build_settings_tab_ne_cree_aucun_widget_de_demande_en_attente(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=main_py)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_build_settings_tab"
        )
        func_source = ast.get_source_segment(source, func) or ""
        # Une simple mention en commentaire (voir RemoteDeviceRequestWindow)
        # est acceptable/utile ; ce qui compte est l'ABSENCE d'une
        # instanciation réelle ou du texte affiché par la fenêtre.
        self.assertNotIn("RemoteDeviceRequestWindow(", func_source)
        self.assertNotIn("Nouveau téléphone demande l'accès", func_source)


if __name__ == "__main__":
    unittest.main()
