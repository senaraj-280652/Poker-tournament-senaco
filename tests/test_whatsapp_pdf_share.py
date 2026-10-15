# -*- coding: utf-8 -*-
"""Partage PDF -> WhatsApp (demande du 2026-10-10, étendue le
2026-10-11), onglets Classement/Statistiques :

- copy_file_to_clipboard/reveal_file_in_file_manager/open_whatsapp_
  desktop_or_web/prompt_whatsapp_share (main.py) : fonctions
  utilitaires PURES, jamais d'automatisation de WhatsApp (Selenium, API
  non officielle, injection de frappe clavier dans une autre
  fenêtre...) — seulement des mécanismes OFFICIELS du système
  (NSPasteboard.writeObjects_/NSURL via PyObjC sur Mac — remplace un
  premier essai via osascript/AppleScript, dont le type "furl" seul
  s'est révélé insuffisant pour WhatsApp Desktop en conditions réelles ;
  PowerShell/.NET sur Windows — pour le presse-papiers natif ;
  "open -R"/"explorer /select," en secours) et le lancement de WhatsApp
  Desktop/Web, best-effort (une erreur ici n'empêche jamais le reste ni
  ne bloque Senaco). Le collage (Cmd+V/Ctrl+V) et le clic final sur
  "Envoyer" restent des gestes RÉELS de l'utilisateur, jamais simulés
  par Senaco. La méthode NSPasteboard a été validée en conditions
  réelles le 2026-10-11 (WhatsApp Desktop, macOS Monterey 12.7.6) : PDF
  prévisualisable, envoyé, reçu et ouvert correctement.
- ClassementExportDialog/PeriodExportDialog : un export PDF réussi
  laisse désormais la fenêtre OUVERTE et active "💬 Envoi WhatsApp" (sur
  EXACTEMENT le fichier déjà généré, jamais régénéré) — un export
  CSV/Excel garde, lui, le comportement HISTORIQUE strictement inchangé
  (fermeture immédiate de la fenêtre).
- "Nom du groupe WhatsApp" (Paramètres) : préférence partagée
  (export_prefs), jamais écrite dans le fichier .tournoi — même
  traitement que "Nom du Club" — utilisée UNIQUEMENT comme rappel
  affiché au responsable, jamais pour sélectionner automatiquement un
  groupe (impossible avec des moyens officiels, voir le diagnostic du
  2026-10-10)."""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import export_prefs  # noqa: E402
import tournament_prefs  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402


# =====================================================================
# A. Fonctions utilitaires pures — jamais d'automatisation de WhatsApp.
# =====================================================================
class WhatsAppHelperFunctionsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_test_")
        self.addCleanup(self._tmp.cleanup)
        self.prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=self.prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.pdf_path = os.path.join(self._tmp.name, "classement.pdf")
        with open(self.pdf_path, "w", encoding="utf-8") as f:
            f.write("contenu pdf factice")

    # -- reveal_file_in_file_manager ------------------------------------

    def test_reveal_sur_mac_appelle_open_dash_r(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main.subprocess, "Popen") as mock_popen:
            main.reveal_file_in_file_manager(self.pdf_path)
            mock_popen.assert_called_once_with(["open", "-R", self.pdf_path])

    def test_reveal_sur_windows_appelle_explorer_select(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main.subprocess, "Popen") as mock_popen:
            main.reveal_file_in_file_manager(self.pdf_path)
            mock_popen.assert_called_once_with(["explorer", "/select,", self.pdf_path])

    def test_reveal_erreur_os_est_avalee_sans_bloquer(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main.subprocess, "Popen", side_effect=OSError):
            main.reveal_file_in_file_manager(self.pdf_path)  # ne lève rien

    # -- copy_file_to_clipboard ------------------------------------------

    def test_mac_copie_le_fichier_via_nspasteboard_writeobjects(self):
        """Demande explicite du 2026-10-11 : remplace l'ancien essai via
        osascript (type "furl" seul, insuffisant pour WhatsApp Desktop,
        incident réel) par NSPasteboard.writeObjects_([NSURL...])
        (PyObjC) — validé en conditions réelles (WhatsApp Desktop,
        macOS Monterey 12.7.6 : PDF prévisualisable, envoyé, reçu et
        ouvert correctement)."""
        mock_url = object()
        mock_pb = MagicMock()
        mock_pb.writeObjects_.return_value = True
        mock_nspasteboard = MagicMock()
        mock_nspasteboard.generalPasteboard.return_value = mock_pb
        mock_nsurl = MagicMock()
        mock_nsurl.fileURLWithPath_.return_value = mock_url
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSPasteboard", mock_nspasteboard, create=True), \
             patch.object(main, "NSURL", mock_nsurl, create=True):
            ok = main.copy_file_to_clipboard(self.pdf_path)
        self.assertTrue(ok)
        mock_nsurl.fileURLWithPath_.assert_called_once_with(self.pdf_path)
        mock_pb.clearContents.assert_called_once()
        mock_pb.writeObjects_.assert_called_once_with([mock_url])

    def test_mac_renvoie_false_si_pyobjc_indisponible(self):
        """PyObjC absent (import échoué à l'importation de main.py) :
        jamais une exception, jamais de tentative d'appel — même
        discipline best-effort que le reste de cette fonction."""
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", False):
            ok = main.copy_file_to_clipboard(self.pdf_path)
        self.assertFalse(ok)

    def test_mac_writeobjects_echoue_renvoie_false(self):
        mock_pb = MagicMock()
        mock_pb.writeObjects_.return_value = False
        mock_nspasteboard = MagicMock()
        mock_nspasteboard.generalPasteboard.return_value = mock_pb
        mock_nsurl = MagicMock()
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSPasteboard", mock_nspasteboard, create=True), \
             patch.object(main, "NSURL", mock_nsurl, create=True):
            ok = main.copy_file_to_clipboard(self.pdf_path)
        self.assertFalse(ok)

    def test_mac_exception_pyobjc_najamais_levee(self):
        """Une exception levée par le pont PyObjC (type non garanti par
        l'API Cocoa sous-jacente) ne doit jamais remonter — best-effort,
        voir le except Exception dédié dans copy_file_to_clipboard."""
        mock_nspasteboard = MagicMock()
        mock_nspasteboard.generalPasteboard.side_effect = RuntimeError("erreur ObjC")
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSPasteboard", mock_nspasteboard, create=True), \
             patch.object(main, "NSURL", MagicMock(), create=True):
            ok = main.copy_file_to_clipboard(self.pdf_path)  # ne lève rien
        self.assertFalse(ok)

    def test_windows_copie_le_fichier_via_powershell_setfiledroplist(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main.subprocess, "run") as mock_run:
            ok = main.copy_file_to_clipboard(self.pdf_path)
            self.assertTrue(ok)
            args, kwargs = mock_run.call_args
            cmd = args[0]
            self.assertEqual(cmd[0], "powershell")
            script = cmd[-1]
            self.assertIn("SetFileDropList", script)
            self.assertIn(self.pdf_path, script)
            self.assertTrue(kwargs.get("check"))

    def test_windows_echappe_les_guillemets_simples_du_chemin(self):
        tricky_path = os.path.join(self._tmp.name, "fichier'special.pdf")
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main.subprocess, "run") as mock_run:
            main.copy_file_to_clipboard(tricky_path)
            script = mock_run.call_args[0][0][-1]
            self.assertIn("fichier''special.pdf", script)

    def test_windows_echec_powershell_renvoie_false_sans_lever(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(
                 main.subprocess, "run",
                 side_effect=subprocess.CalledProcessError(1, "powershell"),
             ):
            ok = main.copy_file_to_clipboard(self.pdf_path)
            self.assertFalse(ok)

    def test_windows_copy_to_clipboard_erreur_os_najamais_levee(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main.subprocess, "run", side_effect=OSError):
            ok = main.copy_file_to_clipboard(self.pdf_path)
            self.assertFalse(ok)

    # -- open_whatsapp_desktop_or_web ------------------------------------
    # Demande explicite du 2026-10-12 : AUCUN repli sur WhatsApp Web —
    # cette fonction n'est de toute façon appelée par le bouton "💬 Envoi
    # WhatsApp" que lorsque is_whatsapp_desktop_installed() a déjà
    # renvoyé True (bouton grisé sinon, voir tests dédiés ci-dessous) ;
    # un échec malgré tout (ex. désinstallation entre la détection et le
    # clic) doit échouer SILENCIEUSEMENT, jamais lever, jamais basculer
    # sur un navigateur (main.webbrowser n'existe même plus).

    def test_mac_lance_whatsapp_desktop_via_nsworkspace_en_priorite(self):
        """Correctif du 2026-10-12 (second incident réel, "toujours pas
        de WhatsApp" après le premier correctif d'ordre d'appel) :
        NSWorkspace.openURL_ — EXACTEMENT la même résolution par bundle
        id que is_whatsapp_desktop_installed() — est tentée EN PREMIER,
        jamais "open -a WhatsApp" en sous-processus si elle réussit."""
        fake_url = object()
        fake_workspace = MagicMock()
        fake_workspace.URLForApplicationWithBundleIdentifier_.return_value = fake_url
        fake_workspace.openURL_.return_value = True
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSWorkspace") as mock_ns_workspace_cls, \
             patch.object(main.subprocess, "run") as mock_run:
            mock_ns_workspace_cls.sharedWorkspace.return_value = fake_workspace
            main.open_whatsapp_desktop_or_web()
            fake_workspace.URLForApplicationWithBundleIdentifier_.assert_called_once_with(
                "net.whatsapp.WhatsApp"
            )
            fake_workspace.openURL_.assert_called_once_with(fake_url)
            mock_run.assert_not_called()

    def test_mac_repli_sur_open_dash_a_si_nsworkspace_najamais_resolu_lurl(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSWorkspace") as mock_ns_workspace_cls, \
             patch.object(main.subprocess, "run") as mock_run:
            mock_ns_workspace_cls.sharedWorkspace.return_value.\
                URLForApplicationWithBundleIdentifier_.return_value = None
            main.open_whatsapp_desktop_or_web()
            mock_run.assert_called_once_with(["open", "-a", "WhatsApp"], check=True)

    def test_mac_repli_sur_open_dash_a_si_nsworkspace_leve_une_exception(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(
                 main, "NSWorkspace",
                 **{"sharedWorkspace.side_effect": RuntimeError("pont PyObjC indisponible")},
             ), \
             patch.object(main.subprocess, "run") as mock_run:
            main.open_whatsapp_desktop_or_web()
            mock_run.assert_called_once_with(["open", "-a", "WhatsApp"], check=True)

    def test_mac_repli_sur_open_dash_a_si_pyobjc_indisponible(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", False), \
             patch.object(main.subprocess, "run") as mock_run:
            main.open_whatsapp_desktop_or_web()
            mock_run.assert_called_once_with(["open", "-a", "WhatsApp"], check=True)

    def test_mac_echoue_silencieusement_si_lancement_impossible(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", False), \
             patch.object(
                 main.subprocess, "run",
                 side_effect=subprocess.CalledProcessError(1, "open"),
             ):
            main.open_whatsapp_desktop_or_web()  # ne lève rien

    def test_windows_tente_le_protocole_whatsapp(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main.os, "startfile", create=True) as mock_startfile:
            main.open_whatsapp_desktop_or_web()
            mock_startfile.assert_called_once_with("whatsapp://")

    def test_windows_echoue_silencieusement_si_protocole_non_enregistre(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main.os, "startfile", side_effect=OSError, create=True):
            main.open_whatsapp_desktop_or_web()  # ne lève rien

    def test_echec_total_najamais_leve_dexception(self):
        """Aucun moyen d'ouvrir WhatsApp Desktop disponible : ne doit
        JAMAIS bloquer Senaco (demande explicite)."""
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", False), \
             patch.object(main.subprocess, "run", side_effect=OSError):
            main.open_whatsapp_desktop_or_web()  # ne lève rien

    # -- is_whatsapp_desktop_installed ------------------------------------

    def test_mac_detecte_installe_via_nsworkspace(self):
        fake_url = object()
        fake_workspace = MagicMock()
        fake_workspace.URLForApplicationWithBundleIdentifier_.return_value = fake_url
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSWorkspace") as mock_ns_workspace_cls:
            mock_ns_workspace_cls.sharedWorkspace.return_value = fake_workspace
            self.assertTrue(main.is_whatsapp_desktop_installed())
            fake_workspace.URLForApplicationWithBundleIdentifier_.assert_called_once_with(
                "net.whatsapp.WhatsApp"
            )

    def test_mac_detecte_non_installe_via_nsworkspace(self):
        fake_workspace = MagicMock()
        fake_workspace.URLForApplicationWithBundleIdentifier_.return_value = None
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", True), \
             patch.object(main, "NSWorkspace") as mock_ns_workspace_cls:
            mock_ns_workspace_cls.sharedWorkspace.return_value = fake_workspace
            self.assertFalse(main.is_whatsapp_desktop_installed())

    def test_mac_sans_pyobjc_renvoie_false_jamais_une_exception(self):
        with patch.object(main.sys, "platform", "darwin"), \
             patch.object(main, "NSPASTEBOARD_AVAILABLE", False):
            self.assertFalse(main.is_whatsapp_desktop_installed())

    def test_windows_detecte_installe_via_cle_de_registre(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main, "WINREG_AVAILABLE", True), \
             patch.object(main, "winreg", create=True) as mock_winreg:
            mock_winreg.HKEY_CLASSES_ROOT = "HKCR"
            mock_winreg.OpenKey.return_value = MagicMock()
            self.assertTrue(main.is_whatsapp_desktop_installed())
            mock_winreg.OpenKey.assert_called_once_with("HKCR", "whatsapp")

    def test_windows_detecte_non_installe_si_cle_absente(self):
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main, "WINREG_AVAILABLE", True), \
             patch.object(main, "winreg", create=True) as mock_winreg:
            mock_winreg.OpenKey.side_effect = OSError
            self.assertFalse(main.is_whatsapp_desktop_installed())

    def test_plateforme_inconnue_renvoie_false(self):
        with patch.object(main.sys, "platform", "linux"):
            self.assertFalse(main.is_whatsapp_desktop_installed())

    # -- prompt_whatsapp_share -------------------------------------------

    def test_prompt_copie_dans_le_presse_papiers_et_ouvre_whatsapp(self):
        """Le presse-papiers natif est tenté, puis WhatsApp est activé —
        Finder/l'Explorateur n'est PAS révélé ici puisque le
        presse-papiers a réussi (voir le correctif du 2026-10-12, second
        incident réel : cette fenêtre supplémentaire pouvait repasser
        devant WhatsApp de façon imprévisible ; voir le test dédié
        ci-dessous pour le cas où le presse-papiers échoue)."""
        with patch.object(main, "copy_file_to_clipboard", return_value=True) as mock_clip, \
             patch.object(main, "reveal_file_in_file_manager") as mock_reveal, \
             patch.object(main, "open_whatsapp_desktop_or_web") as mock_open_wa, \
             patch.object(main, "messagebox"):
            main.prompt_whatsapp_share(None, self.pdf_path)
            mock_clip.assert_called_once_with(self.pdf_path)
            mock_reveal.assert_not_called()
            mock_open_wa.assert_called_once_with()

    def test_whatsapp_est_active_apres_le_rappel_jamais_avant(self):
        """Correctif explicite du 2026-10-12 : incident réel rapporté
        ("WhatsApp passe en arrière-plan") — sur macOS, afficher
        N'IMPORTE QUEL messagebox Tk ramène toute l'application Senaco
        au premier plan. open_whatsapp_desktop_or_web() doit donc être
        appelée APRÈS messagebox.showinfo, jamais avant — sinon
        l'affichage du rappel qui suit replace Senaco devant WhatsApp
        juste après son ouverture. Un Mock partagé entre les deux
        enregistre l'ordre RÉEL des appels (call_args_list d'un parent
        commun), pas seulement le fait que chacun a été appelé."""
        call_order = []
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(
                 main, "open_whatsapp_desktop_or_web",
                 side_effect=lambda: call_order.append("open_whatsapp"),
             ), \
             patch.object(
                 main.messagebox, "showinfo",
                 side_effect=lambda *a, **k: call_order.append("messagebox"),
             ):
            main.prompt_whatsapp_share(None, self.pdf_path)
        self.assertEqual(call_order, ["messagebox", "open_whatsapp"])

    def test_windows_whatsapp_est_active_apres_le_rappel_jamais_avant(self):
        """Même correctif, vérifié bout-en-bout sous Windows (demande
        explicite : "Vérifier également le comportement sous Windows,
        sans perturber son fonctionnement existant") — open_whatsapp_
        desktop_or_web n'est PAS mockée ici : son vrai code Windows
        (os.startfile("whatsapp://"), inchangé par ce correctif) est
        exécuté, seul os.startfile lui-même est mocké. Ordre vérifié de
        bout en bout : messagebox puis os.startfile, jamais l'inverse."""
        call_order = []
        with patch.object(main.sys, "platform", "win32"), \
             patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(
                 main.os, "startfile", create=True,
                 side_effect=lambda *_a: call_order.append("startfile"),
             ), \
             patch.object(
                 main.messagebox, "showinfo",
                 side_effect=lambda *a, **k: call_order.append("messagebox"),
             ):
            main.prompt_whatsapp_share(None, self.pdf_path)
        self.assertEqual(call_order, ["messagebox", "startfile"])

    def test_prompt_finder_explorateur_najamais_revele_si_le_presse_papiers_reussit(self):
        """Correctif explicite du 2026-10-12 (second incident réel,
        "toujours pas de WhatsApp") : contrairement au comportement
        précédent, le Finder/l'Explorateur n'est PLUS révélé quand le
        presse-papiers a déjà réussi — cette fenêtre supplémentaire,
        dont l'affichage réel est asynchrone, pouvait repasser devant
        WhatsApp de façon imprévisible après son activation, même avec
        le bon ordre d'appel. Strictement inutile de toute façon
        puisque le collage fonctionne déjà."""
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager") as mock_reveal, \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox"):
            main.prompt_whatsapp_share(None, self.pdf_path)
            mock_reveal.assert_not_called()

    def test_prompt_finder_explorateur_reste_disponible_si_le_presse_papiers_echoue(self):
        with patch.object(main, "copy_file_to_clipboard", return_value=False), \
             patch.object(main, "reveal_file_in_file_manager") as mock_reveal, \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox"):
            main.prompt_whatsapp_share(None, self.pdf_path)
            mock_reveal.assert_called_once_with(self.pdf_path)

    def test_prompt_message_mentionne_le_collage_si_presse_papiers_reussi(self):
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox") as mock_mb:
            main.prompt_whatsapp_share(None, self.pdf_path)
            args, _kwargs = mock_mb.showinfo.call_args
            self.assertIn("presse-papiers", args[1])
            self.assertIn("⌘V", args[1])
            self.assertIn("Ctrl+V", args[1])

    def test_prompt_message_ne_mentionne_pas_le_collage_si_presse_papiers_echoue(self):
        with patch.object(main, "copy_file_to_clipboard", return_value=False), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox") as mock_mb:
            main.prompt_whatsapp_share(None, self.pdf_path)
            args, _kwargs = mock_mb.showinfo.call_args
            self.assertNotIn("⌘V", args[1])
            self.assertIn("glissez", args[1])

    def test_prompt_rappelle_toujours_de_verifier_et_cliquer_envoyer_soi_meme(self):
        """Exigence explicite, inchangée : le responsable vérifie la
        conversation ET le PDF puis clique lui-même sur Envoyer — quel
        que soit le résultat du presse-papiers."""
        for clipboard_ok in (True, False):
            with patch.object(main, "copy_file_to_clipboard", return_value=clipboard_ok), \
                 patch.object(main, "reveal_file_in_file_manager"), \
                 patch.object(main, "open_whatsapp_desktop_or_web"), \
                 patch.object(main, "messagebox") as mock_mb:
                main.prompt_whatsapp_share(None, self.pdf_path)
                args, _kwargs = mock_mb.showinfo.call_args
                self.assertIn("Vérifiez la conversation", args[1])
                self.assertIn("cliquez vous-même sur Envoyer", args[1])

    def test_prompt_affiche_le_nom_du_groupe_sil_est_renseigne(self):
        export_prefs.save_value("whatsapp_group_name", "CPC Poker")
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox") as mock_mb:
            main.prompt_whatsapp_share(None, self.pdf_path)
            args, _kwargs = mock_mb.showinfo.call_args
            self.assertIn("CPC Poker", args[1])

    def test_prompt_sans_nom_de_groupe_ne_pretend_pas_en_choisir_un(self):
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox") as mock_mb:
            main.prompt_whatsapp_share(None, self.pdf_path)
            args, _kwargs = mock_mb.showinfo.call_args
            self.assertNotIn("Groupe cible", args[1])

    def test_prompt_najamais_envoye_automatiquement(self):
        """Aucune fonction réseau/d'envoi n'est jamais appelée — seuls
        le presse-papiers/reveal/open (locaux) et un message
        d'INFORMATION — jamais de askyesno qui pourrait laisser croire
        à une confirmation suffisante pour envoyer."""
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox") as mock_mb:
            main.prompt_whatsapp_share(None, self.pdf_path)
            mock_mb.showinfo.assert_called_once()
            mock_mb.askyesno.assert_not_called()

    def test_prompt_ne_modifie_jamais_le_fichier_pdf(self):
        with open(self.pdf_path, "rb") as f:
            before = f.read()
        with patch.object(main, "copy_file_to_clipboard", return_value=True), \
             patch.object(main, "reveal_file_in_file_manager"), \
             patch.object(main, "open_whatsapp_desktop_or_web"), \
             patch.object(main, "messagebox"):
            main.prompt_whatsapp_share(None, self.pdf_path)
        with open(self.pdf_path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)


@unittest.skipUnless(
    sys.platform == "darwin" and main.NSPASTEBOARD_AVAILABLE,
    "PyObjC/NSPasteboard indisponible (hors macOS, ou PyObjC non installé)",
)
class RealNSPasteboardIntegrationTest(unittest.TestCase):
    """Test RÉEL (sans aucun mock), sur le vrai presse-papiers macOS —
    même vérification que celle faite manuellement pendant le
    diagnostic du 2026-10-11, qui a confirmé que cette écriture produit
    bien les types ("public.file-url", "NSFilenamesPboardType") que
    WhatsApp Desktop (Chromium) sait résoudre, contrairement à l'ancien
    essai via osascript. Modifie réellement le presse-papiers système
    pendant son exécution (effet transitoire, sans rapport avec les
    données de l'utilisateur ni ses préférences) : valeur précédente
    jamais restaurée après coup, comme pour tout test de presse-papiers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="nspasteboard_real_test_")
        self.addCleanup(self._tmp.cleanup)
        self.pdf_path = os.path.join(self._tmp.name, "reel.pdf")
        with open(self.pdf_path, "w", encoding="utf-8") as f:
            f.write("contenu pdf factice")

    def test_copy_file_to_clipboard_pose_reellement_les_bons_types(self):
        ok = main.copy_file_to_clipboard(self.pdf_path)
        self.assertTrue(ok)
        pb = main.NSPasteboard.generalPasteboard()
        types = list(pb.types())
        self.assertIn("public.file-url", types)
        self.assertIn("NSFilenamesPboardType", types)


# =====================================================================
# B. "Nom du groupe WhatsApp" (Paramètres) — préférence partagée, jamais
#    écrite dans le fichier .tournoi.
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class WhatsAppGroupNameSettingsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_group_name_test_")
        self.addCleanup(self._tmp.cleanup)
        self.prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=self.prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        # _collect_and_save_all_settings appelle aussi tournament_prefs.
        # save_last_settings(db_values) — isolé ici du vrai
        # ~/.poker_tournament/last_settings.json, même précaution que
        # tests/test_ranking_formula_settings_save.py (incident réel du
        # 2026-10-10 : un premier essai de CE fichier de tests, oubliant
        # cette isolation, a réellement écrit dans le vrai fichier).
        tprefs_path = os.path.join(self._tmp.name, "last_settings.json")
        tprefs_patcher = patch.object(tournament_prefs, "_prefs_path", return_value=tprefs_path)
        self.addCleanup(tprefs_patcher.stop)
        tprefs_patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "T.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win.settings_vars = {
            "max_seats_per_table": tk.StringVar(value="9"),
            "min_players_per_table": tk.StringVar(value="4"),
            "ranking_formula": tk.StringVar(value="none"),
            "club_name": tk.StringVar(value="CPC"),
            "whatsapp_group_name": tk.StringVar(value="CPC Poker"),
        }

    def test_save_whatsapp_group_name_ecrit_uniquement_dans_export_prefs(self):
        main.App._save_whatsapp_group_name(self.win)
        self.assertEqual(export_prefs.load_value("whatsapp_group_name"), "CPC Poker")
        row = self.db.conn.execute(
            "SELECT value FROM settings WHERE key='whatsapp_group_name'"
        ).fetchone()
        self.assertIsNone(row)

    def test_collect_and_save_all_settings_najamais_ecrit_whatsapp_group_name_dans_le_tournoi(self):
        import types
        from unittest.mock import MagicMock
        self.win._collect_and_save_all_settings = types.MethodType(
            main.App._collect_and_save_all_settings, self.win
        )
        self.win._update_window_title = types.MethodType(main.App._update_window_title, self.win)
        self.win._trigger_movement_alert = MagicMock()
        self.win._check_pending_rebalance = MagicMock()

        with patch.object(main, "messagebox"):
            self.win._collect_and_save_all_settings()

        row = self.db.conn.execute(
            "SELECT value FROM settings WHERE key='whatsapp_group_name'"
        ).fetchone()
        self.assertIsNone(row)
        # Les AUTRES réglages, eux, continuent d'être enregistrés normalement.
        self.assertEqual(self.db.get_setting_int("min_players_per_table"), 4)

        self.win._collect_and_save_all_settings = None
        self.win._update_window_title = None
        self.win._trigger_movement_alert = None
        self.win._check_pending_rebalance = None


# =====================================================================
# C. ClassementExportDialog — "💬 Envoi WhatsApp".
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ClassementExportDialogWhatsAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="classement_whatsapp_test_")
        self.addCleanup(self._tmp.cleanup)
        self.prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=self.prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        # Détection WhatsApp (demande du 2026-10-12) fixée à "installé" :
        # ces tests portent sur l'activation du bouton liée à l'export
        # PDF, jamais sur la détection elle-même (voir WhatsAppHelper
        # FunctionsTest) — sans ce patch, le résultat dépendrait de si
        # WhatsApp Desktop est réellement installé sur la machine qui
        # exécute les tests.
        wa_patcher = patch.object(main, "is_whatsapp_desktop_installed", return_value=True)
        self.addCleanup(wa_patcher.stop)
        wa_patcher.start()
        self.db = database.Database(os.path.join(self._tmp.name, "T.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _open_dialog(self):
        dlg = main.ClassementExportDialog(self.root, self.db)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        return dlg

    @staticmethod
    def _fake_write(path, *args, **kwargs):
        with open(path, "w", encoding="utf-8") as f:
            f.write("contenu factice")

    def test_bouton_whatsapp_desactive_avant_tout_export(self):
        dlg = self._open_dialog()
        self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "disabled")

    def test_export_pdf_garde_la_fenetre_ouverte_et_active_le_bouton(self):
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "classement.pdf")
        dlg.format_var.set("pdf")
        with patch.object(self.db, "export_classement_pdf", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app") as mock_open:
            dlg._do_export()
        self.assertTrue(dlg.winfo_exists())  # jamais détruite pour un PDF
        self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "normal")
        self.assertEqual(dlg._last_pdf_path, out_path)
        mock_open.assert_called_once_with(out_path)

    def test_export_pdf_najamais_active_le_bouton_si_whatsapp_non_installe(self):
        """Demande explicite du 2026-10-12 : même avec un export PDF
        réussi, le bouton reste désactivé si WhatsApp Desktop n'est pas
        installé sur cette machine — l'export PDF lui-même n'est PAS
        bloqué pour autant (CSV/Excel non concernés par cette demande,
        et un PDF reste exportable même sans WhatsApp)."""
        with patch.object(main, "is_whatsapp_desktop_installed", return_value=False):
            dlg = self._open_dialog()
            out_path = os.path.join(self._tmp.name, "classement.pdf")
            dlg.format_var.set("pdf")
            with patch.object(self.db, "export_classement_pdf", side_effect=self._fake_write), \
                 patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
                 patch.object(main, "open_file_with_default_app"):
                dlg._do_export()
            self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "disabled")
            self.assertEqual(dlg._last_pdf_path, out_path)  # l'export lui-même a bien réussi

    def test_export_csv_ferme_la_fenetre_comme_avant(self):
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "classement.csv")
        dlg.format_var.set("csv")
        with patch.object(self.db, "export_classement_csv", side_effect=self._fake_write) as mock_csv, \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app") as mock_open:
            dlg._do_export()
        self.assertFalse(dlg.winfo_exists())  # comportement HISTORIQUE inchangé
        mock_csv.assert_called_once()
        mock_open.assert_called_once_with(out_path)

    def test_export_xlsx_ferme_la_fenetre_comme_avant(self):
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "classement.xlsx")
        dlg.format_var.set("xlsx")
        with patch.object(self.db, "export_classement_xlsx", side_effect=self._fake_write) as mock_xlsx, \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app") as mock_open:
            dlg._do_export()
        self.assertFalse(dlg.winfo_exists())
        mock_xlsx.assert_called_once()
        mock_open.assert_called_once_with(out_path)

    def test_bouton_annuler_ferme_normalement_sans_avoir_exporte(self):
        """Demande explicite : fermeture normale même si l'utilisateur
        renonce au partage — AUCUN export n'a eu lieu ici."""
        dlg = self._open_dialog()
        dlg.destroy()
        self.assertFalse(dlg.winfo_exists())

    def test_clic_whatsapp_transmet_exactement_le_pdf_deja_genere_sans_regenerer(self):
        """Méthode unique confirmée le 2026-10-12 : le bouton appelle
        directement prompt_whatsapp_share (presse-papiers + ouverture
        de WhatsApp) — plus de choix Ordinateur/Téléphone, jamais de QR
        code dans le parcours utilisateur. offer_whatsapp_share/
        WhatsAppDestinationChoiceDialog/WhatsAppQRShareDialog restent
        définies mais ne sont plus jamais appelées depuis l'interface
        (code conservé tel quel, voir tests/test_whatsapp_qr_share.py,
        qui continue de les exercer directement)."""
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "classement.pdf")
        dlg.format_var.set("pdf")
        with patch.object(self.db, "export_classement_pdf", side_effect=self._fake_write) as mock_pdf, \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export()
        with patch.object(main, "prompt_whatsapp_share") as mock_prompt, \
             patch.object(main, "offer_whatsapp_share") as mock_offer:
            dlg.whatsapp_btn.invoke()
        mock_prompt.assert_called_once_with(dlg, out_path)
        mock_offer.assert_not_called()
        mock_pdf.assert_called_once()  # jamais un second appel déclenché par le partage


# =====================================================================
# D. PeriodExportDialog — deux sections indépendantes (tournois/joueurs).
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PeriodExportDialogWhatsAppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="period_whatsapp_test_")
        self.addCleanup(self._tmp.cleanup)
        self.prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=self.prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        # Détection WhatsApp (demande du 2026-10-12) fixée à "installé" —
        # voir le même commentaire dans ClassementExportDialogWhatsAppTest.
        wa_patcher = patch.object(main, "is_whatsapp_desktop_installed", return_value=True)
        self.addCleanup(wa_patcher.stop)
        wa_patcher.start()

    def _open_dialog(self):
        summary = {"tournaments": [], "players": []}
        dlg = main.PeriodExportDialog(self.root, summary)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        return dlg

    @staticmethod
    def _fake_write(summary, path, **kwargs):
        with open(path, "w", encoding="utf-8") as f:
            f.write("contenu factice")

    def test_bouton_whatsapp_unique_desactive_avant_tout_export(self):
        """Demande explicite du 2026-10-12 : UN SEUL bouton "💬 Envoi
        WhatsApp", dans la barre du haut (remplace les deux boutons par
        tableau de la version précédente) — désactivé tant qu'aucun PDF
        n'a été généré dans cette fenêtre, quel que soit le tableau."""
        dlg = self._open_dialog()
        self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "disabled")

    def test_bouton_whatsapp_dans_la_barre_du_haut_immediatement_a_gauche_de_fermer(self):
        top_bar = [
            w for w in self._open_dialog().winfo_children() if isinstance(w, ttk.Frame)
        ][0]
        children = top_bar.winfo_children()
        texts = [w.cget("text") for w in children if "text" in w.keys()]
        self.assertIn("Fermer", texts)
        self.assertIn("💬 Envoi WhatsApp", texts)
        idx_fermer = texts.index("Fermer")
        idx_whatsapp = texts.index("💬 Envoi WhatsApp")
        # Les deux sont packés side="right" : le bouton packé APRÈS "Fermer"
        # apparaît visuellement à sa GAUCHE (ordre d'empilement Tk).
        self.assertGreater(idx_whatsapp, idx_fermer)

    def test_export_pdf_dun_tableau_active_le_bouton_unique(self):
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "synthese_tournois.pdf")
        dlg.format_var.set("pdf")
        with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app") as mock_open:
            dlg._do_export_section(
                "tournament", dlg.tournament_vars, "period_tournament",
                "Exporter les tournois de la période", "synthese_tournois",
            )
        self.assertTrue(dlg.winfo_exists())  # PDF : fenêtre gardée ouverte
        self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "normal")
        self.assertEqual(dlg._last_pdf_path, out_path)
        mock_open.assert_called_once_with(out_path)

    def test_export_pdf_du_second_tableau_remplace_le_dernier_pdf_partage(self):
        """Demande explicite du 2026-10-12, point 2 : le bouton unique
        partage le DERNIER PDF réellement généré, "qu'il s'agisse du
        tableau Tournois de la période ou du tableau Classement des
        joueurs" — un second export, depuis l'AUTRE tableau, remplace
        donc bien le chemin mémorisé pour le premier."""
        dlg = self._open_dialog()
        out_tournois = os.path.join(self._tmp.name, "synthese_tournois.pdf")
        dlg.format_var.set("pdf")
        with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_tournois), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export_section(
                "tournament", dlg.tournament_vars, "period_tournament",
                "Exporter les tournois de la période", "synthese_tournois",
            )
        self.assertEqual(dlg._last_pdf_path, out_tournois)

        out_joueurs = os.path.join(self._tmp.name, "synthese_joueurs.pdf")
        with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_joueurs), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export_section(
                "player", dlg.player_vars, "period_player",
                "Exporter le classement des joueurs", "synthese_joueurs",
            )
        self.assertEqual(dlg._last_pdf_path, out_joueurs)
        self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "normal")

        with patch.object(main, "prompt_whatsapp_share") as mock_prompt:
            dlg.whatsapp_btn.invoke()
        mock_prompt.assert_called_once_with(dlg, out_joueurs)

    def test_export_pdf_najamais_active_le_bouton_si_whatsapp_non_installe(self):
        """Même demande que pour ClassementExportDialog : un export PDF
        réussi n'active jamais le bouton si WhatsApp Desktop n'est pas
        installé sur cette machine."""
        with patch.object(main, "is_whatsapp_desktop_installed", return_value=False):
            dlg = self._open_dialog()
            out_path = os.path.join(self._tmp.name, "synthese_tournois.pdf")
            dlg.format_var.set("pdf")
            with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write), \
                 patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
                 patch.object(main, "open_file_with_default_app"):
                dlg._do_export_section(
                    "tournament", dlg.tournament_vars, "period_tournament",
                    "Exporter les tournois de la période", "synthese_tournois",
                )
            self.assertEqual(str(dlg.whatsapp_btn.cget("state")), "disabled")
            self.assertEqual(dlg._last_pdf_path, out_path)

    def test_export_csv_ferme_la_fenetre_comme_avant(self):
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "synthese_tournois.csv")
        dlg.format_var.set("csv")
        with patch.object(main, "export_period_summary_csv", side_effect=self._fake_write) as mock_csv, \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app") as mock_open:
            dlg._do_export_section(
                "tournament", dlg.tournament_vars, "period_tournament",
                "Exporter les tournois de la période", "synthese_tournois",
            )
        self.assertFalse(dlg.winfo_exists())  # comportement HISTORIQUE inchangé
        mock_csv.assert_called_once()
        mock_open.assert_called_once_with(out_path)

    def test_export_csv_apres_un_pdf_najamais_envoye_a_la_place_du_pdf(self):
        """Demande explicite du 2026-10-12, point 5 : si un export CSV
        (ou Excel) est effectué APRÈS un PDF dans la même fenêtre, ce
        fichier ne doit jamais être envoyé à la place du PDF — la
        fenêtre (et donc le bouton WhatsApp) est détruite avant qu'un
        tel envoi ne soit possible."""
        dlg = self._open_dialog()
        pdf_path = os.path.join(self._tmp.name, "synthese_tournois.pdf")
        dlg.format_var.set("pdf")
        with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=pdf_path), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export_section(
                "tournament", dlg.tournament_vars, "period_tournament",
                "Exporter les tournois de la période", "synthese_tournois",
            )
        self.assertEqual(dlg._last_pdf_path, pdf_path)

        csv_path = os.path.join(self._tmp.name, "synthese_joueurs.csv")
        dlg.format_var.set("csv")
        with patch.object(main, "export_period_summary_csv", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=csv_path), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export_section(
                "player", dlg.player_vars, "period_player",
                "Exporter le classement des joueurs", "synthese_joueurs",
            )
        self.assertFalse(dlg.winfo_exists())  # plus de fenêtre, plus de bouton à cliquer

    def test_bouton_fermer_disponible_meme_apres_un_export_pdf_en_attente(self):
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "synthese_tournois.pdf")
        dlg.format_var.set("pdf")
        with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write), \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export_section(
                "tournament", dlg.tournament_vars, "period_tournament",
                "Exporter les tournois de la période", "synthese_tournois",
            )
        self.assertTrue(dlg.winfo_exists())
        dlg.destroy()  # "Fermer" — renonce au partage, fermeture normale
        self.assertFalse(dlg.winfo_exists())

    def test_clic_whatsapp_transmet_le_dernier_pdf_sans_regenerer(self):
        """Méthode unique confirmée le 2026-10-12 : voir la même
        remarque que pour ClassementExportDialog ci-dessus —
        prompt_whatsapp_share directement, jamais offer_whatsapp_share
        (choix Ordinateur/Téléphone retiré du parcours utilisateur)."""
        dlg = self._open_dialog()
        out_path = os.path.join(self._tmp.name, "synthese_joueurs.pdf")
        dlg.format_var.set("pdf")
        with patch.object(main, "export_period_summary_pdf", side_effect=self._fake_write) as mock_pdf, \
             patch.object(main.filedialog, "asksaveasfilename", return_value=out_path), \
             patch.object(main, "open_file_with_default_app"):
            dlg._do_export_section(
                "player", dlg.player_vars, "period_player",
                "Exporter le classement des joueurs", "synthese_joueurs",
            )
        with patch.object(main, "prompt_whatsapp_share") as mock_prompt, \
             patch.object(main, "offer_whatsapp_share") as mock_offer:
            dlg.whatsapp_btn.invoke()
        mock_prompt.assert_called_once_with(dlg, out_path)
        mock_offer.assert_not_called()
        mock_pdf.assert_called_once()


# =====================================================================
# E. Dépendance PyObjC déclarée pour la distribution macOS (demande
#    explicite du 2026-10-11) — vérification STRUCTURELLE des fichiers
#    de build, même convention déjà établie par tests/test_build_msi_
#    test_workflow.py pour les fichiers de build Windows.
# =====================================================================
class PyObjCDependencyDeclaredForMacDistributionTest(unittest.TestCase):
    def _root(self):
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_macos_requirements_declare_pyobjc_framework_cocoa(self):
        with open(os.path.join(self._root(), "macos", "requirements.txt"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("pyobjc-framework-Cocoa", content)

    def test_macos_spec_liste_appkit_en_hiddenimports(self):
        with open(os.path.join(self._root(), "macos", "poker_tournament.spec"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn('hiddenimports=["AppKit"]', content)


if __name__ == "__main__":
    unittest.main()
