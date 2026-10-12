# -*- coding: utf-8 -*-
"""Tests ciblés pour l'UI de "Purger Données" (chantier du 2026-10-07) :
App._on_purge_choice (le petit choix LOG/DONNÉES/Annuler) et
main.DataPurgeDialog (le dialogue de purge par date/type, avec ses
garde-fous de sauvegarde). Toute la logique métier (classification,
recherche, vérification de couverture, suppression atomique) est déjà
couverte séparément par tests/test_data_purge.py — ce fichier-ci ne
vérifie que le CÂBLAGE et le COMPORTEMENT de l'interface elle-même, sur
des dossiers/.tournoi SYNTHÉTIQUES (jamais le vrai ~/.poker_tournament).

Convention reprise de tests/test_ca_log_tab_wiring.py : une racine tk.Tk
réelle, les méthodes App réellement utilisées greffées dessus via
types.MethodType (jamais une App complète, trop lourde à construire),
nettoyage via tests/_tk_cleanup.cleanup_tk (évite les plantages Tcl/Tk
déjà diagnostiqués pour cette suite)."""
import os
import queue
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import action_log  # noqa: E402
import backup_history  # noqa: E402
import backup_restore  # noqa: E402
import data_purge  # noqa: E402
import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_tournoi(tmp_dir, filename, tournament_date="2026-01-01", is_sng=None):
    """is_sng=None (par défaut) laisse la clé 'is_sng' totalement
    absente du fichier, comme un ancien fichier jamais créé via "Sit &
    Go rapide" — voir data_purge.classify_tournament_for_purge."""
    path = os.path.join(tmp_dir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = database.Database(path)
    settings = {"tournament_date": tournament_date}
    if is_sng is not None:
        settings["is_sng"] = 1 if is_sng else 0
    db.set_settings(settings)
    db.conn.close()
    return path


def _all_buttons(widget):
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ttk.Button):
            found.append(child)
        found.extend(_all_buttons(child))
    return found


def _toplevel_children(widget):
    return [w for w in widget.winfo_children() if isinstance(w, tk.Toplevel)]


def _pump_until_scan_idle(dlg, timeout=10):
    """Fait avancer le sondage de la recherche en arrière-plan de `dlg`
    (voir DataPurgeDialog._start_scan/_poll_scan_queue, incident macOS
    du 2026-10-08 : la recherche tourne désormais TOUJOURS sur un thread
    séparé) jusqu'à ce qu'elle soit terminée — nécessaire après TOUTE
    action qui déclenche _refresh_count (construction du dialogue,
    changement de date/type, ajout manuel de dossier) avant de vérifier
    dlg.count_lbl/_purge_paths/l'état des boutons.

    N'appelle JAMAIS dlg.update()/update_idletasks() — un essai avec
    dlg.update() a provoqué un VRAI SIGSEGV ici (reproduit de façon
    fiable), même risque que le update_idletasks() déjà diagnostiqué
    pendant le correctif du calendrier : un appel réentrant à update()
    juste après un grab_set() frais sur ce Tk/Aqua. Appelle à la place
    directement DataPurgeDialog._poll_scan_queue (une méthode normale,
    sans rapport avec la boucle d'événements) après avoir annulé le
    sondage programmé via self.after, pour ne jamais accumuler d'appels
    programmés en double."""
    start = time.monotonic()
    while dlg._scan_in_progress:
        if dlg._poll_after_id is not None:
            try:
                dlg.after_cancel(dlg._poll_after_id)
            except tk.TclError:
                pass
            dlg._poll_after_id = None
        dlg._poll_scan_queue()
        if time.monotonic() - start > timeout:
            raise AssertionError("le scan ne s'est jamais terminé (timeout de test)")
        time.sleep(0.01)


def _set_valid_purge_dates(dlg, date_from="01/01/2000", date_to="31/12/2099"):
    """Renseigne Du/Au avec une période large et valide (demande
    explicite du 2026-10-08, "les deux dates sont désormais
    OBLIGATOIRES pour Purger Données") couvrant très largement toutes
    les dates synthétiques utilisées par les fixtures de ce fichier
    (toutes en 2026) — PAR DÉFAUT pour chaque dialogue de test, afin
    que les tests déjà validés (comptes, sauvegarde, confirmation...)
    continuent d'exercer exactement le même comportement qu'avant cette
    règle de sécurité. Un test qui veut spécifiquement exercer une
    date manquante/invalide/inversée écrase ensuite l'un des deux
    champs lui-même. Chaque `.set()` relance une recherche (trace_add)
    — toujours suivi de _pump_until_scan_idle par l'appelant."""
    dlg.date_from_var.set(date_from)
    dlg.date_to_var.set(date_to)
    _pump_until_scan_idle(dlg)


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class PurgeChoiceDialogTest(unittest.TestCase):
    """App._on_purge_choice — la fenêtre de choix LOG/DONNÉES/Annuler
    elle-même (demande du 2026-10-07) : ne doit JAMAIS déclencher les
    deux chemins à la fois, ni en déclencher aucun sur Annuler/fermeture."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root._on_purge_choice = types.MethodType(main.App._on_purge_choice, self.root)
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def test_bouton_log_appelle_on_log_purge_strictement_inchange_et_ferme_le_choix(self):
        calls = []
        self.root._on_log_purge = lambda: calls.append(True)
        self.root._on_purge_choice()
        win = _toplevel_children(self.root)[0]
        btn = next(b for b in _all_buttons(win) if b.cget("text") == "LOG")
        btn.invoke()
        self.assertEqual(calls, [True])
        self.assertEqual(_toplevel_children(self.root), [])

    def test_bouton_donnees_ouvre_bien_dataPurgeDialog_avec_lapp_comme_parent(self):
        self.root._on_log_purge = lambda: self.fail("_on_log_purge ne doit pas être appelé")
        with patch("main.DataPurgeDialog") as mock_dialog:
            self.root._on_purge_choice()
            win = _toplevel_children(self.root)[0]
            btn = next(b for b in _all_buttons(win) if b.cget("text") == "DONNÉES")
            btn.invoke()
        mock_dialog.assert_called_once_with(self.root)

    def test_bouton_annuler_ne_declenche_ni_log_ni_donnees(self):
        calls = []
        self.root._on_log_purge = lambda: calls.append("log")
        with patch("main.DataPurgeDialog", side_effect=lambda master: calls.append("donnees")):
            self.root._on_purge_choice()
            win = _toplevel_children(self.root)[0]
            btn = next(b for b in _all_buttons(win) if b.cget("text") == "Annuler")
            btn.invoke()
        self.assertEqual(calls, [])
        self.assertEqual(_toplevel_children(self.root), [])


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class DataPurgeDialogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_dialog_test_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)
        self.usb = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.usb)

        self._patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=self.tournament_folder),
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.poker_data_dir),
            patch.object(backup_history, "_history_dir", return_value=self.poker_data_dir),
            # Découverte multi-dossiers (2026-10-07) : les 3 autres
            # sources automatiques sont neutralisées par défaut (chaque
            # test qui en a besoin les repatch explicitement) — sinon
            # DataPurgeDialog lirait les VRAIES export_prefs.json/
            # open_windows.json/actions_log.sqlite3 de la machine qui
            # exécute les tests. Avec ces 3 neutres, self.tournament_folder
            # reste le SEUL dossier candidat, exactement le comportement
            # d'avant la découverte multi-dossiers — aucune régression
            # des tests déjà existants.
            patch.object(export_prefs, "load_value", return_value=None),
            patch.object(open_windows, "list_open_paths", return_value=[]),
            patch.object(action_log, "list_tournaments", return_value=[]),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _make_tournoi(self, filename, date="2026-01-01"):
        return _new_tournoi(self.tournament_folder, filename, date)

    def _make_dialog(self):
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        _pump_until_scan_idle(dlg)
        _set_valid_purge_dates(dlg)
        return dlg

    def _do_real_backup_now(self, dlg):
        """Déclenche "Faire une sauvegarde maintenant..." via le VRAI
        circuit App._run_backup_flow (jamais un second mécanisme, voir
        sa docstring) — seul filedialog.askdirectory est simulé (choix
        de l'utilisateur), create_backup/backup_history.record_backup
        s'exécutent réellement sur les dossiers temporaires du test."""
        with patch("main.filedialog.askdirectory", return_value=self.usb), \
                patch("main.messagebox.showinfo"):
            dlg._on_backup_now()

    # ------------------------------------------------------------------
    # Critères (date/type) et compteur en direct
    # ------------------------------------------------------------------

    def test_defaults_tournois_et_sitngo_coches_autres_decoche_purger_desactive(self):
        self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        self.assertTrue(dlg.type_vars[data_purge.PURGE_TYPE_TOURNOIS].get())
        self.assertTrue(dlg.type_vars[data_purge.PURGE_TYPE_SITNGO].get())
        self.assertFalse(dlg.type_vars[data_purge.PURGE_TYPE_OTHER].get())
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")

    def test_compteur_initial_reflete_les_criteres_par_defaut(self):
        self._make_tournoi("To1.tournoi")
        self._make_tournoi("Sn1.tournoi")
        self._make_tournoi("vieux.tournoi")  # type "other", décoché par défaut
        dlg = self._make_dialog()
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 2 tournois")

    def test_compteur_se_met_a_jour_en_direct_sur_changement_de_type(self):
        self._make_tournoi("To1.tournoi")
        self._make_tournoi("vieux.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")
        dlg.type_vars[data_purge.PURGE_TYPE_OTHER].set(True)
        dlg._on_criteria_changed()
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 2 tournois")

    def test_changement_de_critere_invalide_une_verification_precedente(self):
        p1 = self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self._do_real_backup_now(dlg)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")
        # Toute modification de critère doit invalider la vérification —
        # jamais rester "Purger" utilisable pour des critères différents
        # de ceux réellement vérifiés.
        dlg.type_vars[data_purge.PURGE_TYPE_OTHER].set(True)
        dlg._on_criteria_changed()
        _pump_until_scan_idle(dlg)
        self.assertIsNone(dlg._coverage)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")

    def test_aucun_tournoi_concerne_verifier_affiche_un_message_et_ne_change_rien(self):
        dlg = self._make_dialog()
        with patch("main.messagebox.showinfo") as mock_info:
            dlg._on_verify_backup()
        mock_info.assert_called_once()
        self.assertIsNone(dlg._coverage)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")

    # ------------------------------------------------------------------
    # Vérification de la sauvegarde
    # ------------------------------------------------------------------

    def test_verification_sans_sauvegarde_bloque_et_affiche_le_bouton_sauvegarder(self):
        self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")
        self.assertEqual(dlg.backup_now_btn.winfo_manager(), "pack")

    def test_sauvegarde_maintenant_relance_la_verification_et_debloque_purger(self):
        self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")
        self._do_real_backup_now(dlg)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")
        self.assertEqual(dlg.backup_now_btn.winfo_manager(), "")

    def test_sauvegarde_annulee_par_lutilisateur_ne_debloque_rien(self):
        """askdirectory annulé (chaîne vide) -> App._run_backup_flow
        renvoie None, aucune entrée d'historique n'est créée, la purge
        reste bloquée (demande explicite du 2026-10-07)."""
        self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        with patch("main.filedialog.askdirectory", return_value=""):
            dlg._on_backup_now()
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")
        self.assertEqual(backup_history.load_history(), [])

    def test_ancienne_sauvegarde_sans_hash_affiche_le_texte_exact_demande(self):
        """Formulation EXACTE exigée par l'utilisateur (2026-10-07) :
        "Ancienne sauvegarde vérifiée — contrôle d'intégrité SHA-256
        indisponible" — jamais reformulée, jamais transformée en
        sauvegarde vérifiée par hash."""
        import json
        self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self._do_real_backup_now(dlg)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")

        history = backup_history.load_history()
        backup_dir = history[0]["backup_dir"]
        manifest_path = os.path.join(backup_dir, backup_restore.MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        m.pop("tournament_file_hashes", None)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(m, f)

        dlg._run_coverage_check()
        self.assertIn(
            "Ancienne sauvegarde vérifiée — contrôle d'intégrité SHA-256 indisponible",
            dlg.backup_status_lbl.cget("text"),
        )
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")

    # ------------------------------------------------------------------
    # Exécution de la purge (confirmation, tournoi ouvert, suppression)
    # ------------------------------------------------------------------

    def test_purger_sans_verification_prealable_reste_bloque_defensivement(self):
        """Garde-fou défensif (le bouton est normalement désactivé dans
        ce cas) : même appelé directement, _on_purge_clicked doit
        refuser de purger si _coverage n'a jamais été vérifié avec
        succès."""
        p1 = self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        with patch("main.messagebox.showerror") as mock_err:
            dlg._on_purge_clicked()
        mock_err.assert_called_once()
        self.assertTrue(os.path.isfile(p1))

    def test_annulation_de_la_confirmation_ne_supprime_rien(self):
        p1 = self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self._do_real_backup_now(dlg)
        with patch.object(dlg, "_confirm_purge", return_value=False):
            dlg._on_purge_clicked()
        self.assertTrue(os.path.isfile(p1))

    def test_confirmation_acceptee_supprime_bien_les_fichiers_et_ferme_le_dialogue(self):
        p1 = self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self._do_real_backup_now(dlg)
        with patch.object(dlg, "_confirm_purge", return_value=True), \
                patch("main.messagebox.showinfo"):
            dlg._on_purge_clicked()
        self.assertFalse(os.path.isfile(p1))

    def test_tournoi_ouvert_bloque_la_purge_et_previent_lutilisateur_sans_rien_supprimer(self):
        p1 = self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg._on_verify_backup()
        self._do_real_backup_now(dlg)
        with patch.object(open_windows, "list_open_paths", return_value=[p1]), \
                patch("main.messagebox.showerror") as mock_err:
            dlg._on_purge_clicked()
        mock_err.assert_called_once()
        self.assertTrue(os.path.isfile(p1))

    def test_message_de_confirmation_contient_bien_periode_types_compte_et_sauvegarde(self):
        self._make_tournoi("To1.tournoi")
        dlg = self._make_dialog()
        dlg.date_from_var.set("01/01/2026")
        dlg.date_to_var.set("31/01/2026")
        _pump_until_scan_idle(dlg)
        dlg._on_verify_backup()
        self._do_real_backup_now(dlg)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")

        captured = {}
        orig_confirm = main.DataPurgeDialog._confirm_purge

        def _capture_and_cancel(self_dlg):
            # Reproduit juste assez de _confirm_purge pour inspecter le
            # message construit, sans ouvrir de vraie fenêtre modale
            # bloquante dans ce test.
            raw_from = self_dlg.date_from_var.get().strip()
            raw_to = self_dlg.date_to_var.get().strip()
            captured["period"] = f"{raw_from} / {raw_to}"
            return False

        with patch.object(main.DataPurgeDialog, "_confirm_purge", _capture_and_cancel):
            dlg._on_purge_clicked()
        self.assertEqual(captured["period"], "01/01/2026 / 31/01/2026")

    # ------------------------------------------------------------------
    # Calendrier Du/Au — bug rapporté le 2026-10-07 : visible mais
    # inutilisable depuis "Purger Données" (navigation/sélection sans
    # effet), alors que le même calendrier fonctionne normalement depuis
    # l'onglet LOG. Cause exacte : voir DataPurgeDialog._show_log_date_
    # picker, qui délègue désormais à App._show_log_date_picker avec
    # self=DataPurgeDialog (jamais self.app) pour que le petit Toplevel
    # du calendrier soit un VRAI DESCENDANT Tk de DataPurgeDialog —
    # condition nécessaire pour rester cliquable sous le grab_set() de
    # ce dernier (un grab Tk local ne route les événements que vers la
    # fenêtre qui l'a posé ET ses descendants, jamais vers un Toplevel
    # frère, même visible au premier plan).
    #
    # L'invocation directe des boutons (Button.invoke()) exécute leur
    # `command` SANS passer par la file d'événements X11/Aqua : elle ne
    # peut donc pas, par elle-même, reproduire le blocage de clics que
    # provoquait le bug (invoke() aurait "marché" même sur la version
    # cassée). C'est pourquoi chaque test ci-dessous vérifie en plus la
    # propriété STRUCTURELLE réellement en cause — winfo_parent() du
    # calendrier et l'identité du widget qui tient le grab (voir
    # grab_current()) — seule façon de couvrir ce bug dans les limites
    # de ce que Tkinter permet de tester sans affichage réel.
    # ------------------------------------------------------------------

    def _open_calendar(self, dlg, which):
        """Ouvre le calendrier Du (which=0) ou Au (which=1) en invoquant
        le VRAI bouton 📅 de DataPurgeDialog (jamais un appel direct à
        _show_log_date_picker) — couvre aussi le câblage command= lui-
        même, pas seulement la méthode qu'il appelle."""
        cal_buttons = [b for b in _all_buttons(dlg) if b.cget("text") == "📅"]
        self.assertEqual(len(cal_buttons), 2, "un bouton 📅 pour Du et un pour Au")
        cal_buttons[which].invoke()
        new_toplevels = _toplevel_children(dlg)
        self.assertEqual(len(new_toplevels), 1, "le calendrier doit s'ouvrir exactement une fois")
        return new_toplevels[0]

    def test_calendrier_du_est_un_descendant_tk_de_dataPurgeDialog_jamais_de_la_racine(self):
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=0)
        self.assertEqual(
            str(picker.winfo_parent()), str(dlg),
            "le calendrier doit être un DESCENDANT Tk de DataPurgeDialog — "
            "sinon le grab_set() de DataPurgeDialog le rend incliquable",
        )
        self.assertNotEqual(str(picker.winfo_parent()), str(self.root))
        picker.destroy()

    def test_calendrier_au_est_aussi_un_descendant_tk_de_dataPurgeDialog(self):
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=1)
        self.assertEqual(str(picker.winfo_parent()), str(dlg))
        picker.destroy()

    def test_le_grab_est_cede_au_calendrier_puis_restaure_a_dataPurgeDialog(self):
        """Second correctif (2026-10-07) : un bug réel persistait sur
        macOS malgré la seule correction de parenté Tk (le calendrier
        comme descendant), parce que DataPurgeDialog restait grabbing
        PENDANT l'ouverture du calendrier — un grab IMBRIQUÉ, jamais
        fiable au niveau réel du système de fenêtres macOS/Aqua, même
        quand Tk lui-même (grab_current/focus_get) rapporte un état
        cohérent. Le correctif final impose qu'il n'y ait JAMAIS deux
        grabs actifs en même temps : DataPurgeDialog cède explicitement
        son grab au calendrier à l'ouverture (grab_current() doit alors
        désigner le calendrier, PLUS JAMAIS DataPurgeDialog), puis le
        récupère explicitement à la fermeture de celui-ci."""
        dlg = self._make_dialog()
        self.assertEqual(str(dlg.grab_current()), str(dlg))

        picker = self._open_calendar(dlg, which=0)
        self.assertEqual(
            str(dlg.grab_current()), str(picker),
            "le calendrier doit détenir SEUL le grab pendant qu'il est ouvert "
            "— jamais un grab imbriqué avec DataPurgeDialog",
        )

        picker.destroy()
        self.assertEqual(
            str(dlg.grab_current()), str(dlg),
            "le grab de DataPurgeDialog doit être restauré dès la fermeture du calendrier",
        )

    def test_le_grab_est_cede_et_restaure_aussi_pour_le_calendrier_au(self):
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=1)
        self.assertEqual(str(dlg.grab_current()), str(picker))
        picker.destroy()
        self.assertEqual(str(dlg.grab_current()), str(dlg))

    def test_le_grab_est_restaure_meme_apres_selection_dun_jour(self):
        """La restauration du grab (liée à <Destroy>) doit fonctionner
        de façon identique que le calendrier soit fermé nativement
        (test ci-dessus) ou par une sélection de jour via _select, qui
        appelle picker.destroy() lui-même (voir App._show_log_date_
        picker, inchangée)."""
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=0)
        day_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "1")
        day_btn.invoke()
        self.assertEqual(_toplevel_children(dlg), [])
        self.assertEqual(str(dlg.grab_current()), str(dlg))

    def test_clic_sur_un_jour_renseigne_du_ferme_le_calendrier_et_recalcule_le_compte(self):
        self._make_tournoi("To150126.tournoi", "2026-01-15")
        dlg = self._make_dialog()
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")
        # Pré-remplit "Du" avec une date déjà valide (voir App.
        # _show_log_date_picker : repart de la date déjà saisie si elle
        # est valide, sinon d'aujourd'hui) pour que le calendrier
        # s'ouvre sur janvier 2026 de façon déterministe, indépendamment
        # de la date système réelle au moment du test.
        dlg.date_from_var.set("01/01/2026")
        picker = self._open_calendar(dlg, which=0)
        day_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "15")
        day_btn.invoke()
        _pump_until_scan_idle(dlg)
        self.assertEqual(_toplevel_children(dlg), [], "le calendrier doit se fermer après le clic sur un jour")
        self.assertEqual(dlg.date_from_var.get(), "15/01/2026", "la date choisie doit aller dans LE CHAMP DU")
        self.assertEqual(
            dlg.date_to_var.get(), "31/12/2099",
            "jamais touché par le calendrier Du — reste à sa valeur pré-remplie par _make_dialog",
        )
        self.assertEqual(
            dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi",
            "le compteur doit être recalculé (même résultat ici : le 15/01/2026 reste >= 15/01/2026)",
        )

    def test_clic_sur_un_jour_renseigne_au_jamais_du(self):
        self._make_tournoi("To150126.tournoi", "2026-01-15")
        dlg = self._make_dialog()
        dlg.date_to_var.set("01/03/2026")
        picker = self._open_calendar(dlg, which=1)
        day_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "20")
        day_btn.invoke()
        _pump_until_scan_idle(dlg)
        self.assertEqual(_toplevel_children(dlg), [])
        self.assertEqual(dlg.date_to_var.get(), "20/03/2026")
        self.assertEqual(
            dlg.date_from_var.get(), "01/01/2000",
            "jamais touché par le calendrier Au — reste à sa valeur pré-remplie par _make_dialog",
        )
        # 15/01/2026 <= 20/03/2026 : toujours concerné par ce nouveau "Au".
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")

    def test_navigation_mois_precedent_suivant_puis_selection_dune_date(self):
        dlg = self._make_dialog()
        dlg.date_from_var.set("15/06/2026")
        picker = self._open_calendar(dlg, which=0)
        prev_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "◀")
        next_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "▶")
        prev_btn.invoke()  # mai 2026
        prev_btn.invoke()  # avril 2026
        next_btn.invoke()  # mai 2026
        day_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "10")
        day_btn.invoke()
        self.assertEqual(_toplevel_children(dlg), [])
        self.assertEqual(dlg.date_from_var.get(), "10/05/2026")

    def test_on_peut_rouvrir_le_calendrier_pour_lautre_champ_apres_fermeture_du_premier(self):
        """Items 8/9 du scénario rapporté : refermer le calendrier d'un
        champ (ici sans choisir de date, via la fermeture native de la
        fenêtre) ne doit laisser AUCUNE fenêtre parasite, et le
        calendrier de l'AUTRE champ doit ensuite s'ouvrir normalement,
        lui aussi comme descendant de DataPurgeDialog."""
        dlg = self._make_dialog()
        picker_du = self._open_calendar(dlg, which=0)
        picker_du.destroy()
        self.assertEqual(_toplevel_children(dlg), [])

        picker_au = self._open_calendar(dlg, which=1)
        self.assertEqual(str(picker_au.winfo_parent()), str(dlg))
        picker_au.destroy()
        self.assertEqual(_toplevel_children(dlg), [])

    # ------------------------------------------------------------------
    # Fermeture de DataPurgeDialog / calendrier — SIGSEGV réel rapporté
    # le 2026-10-07 (postérieur au correctif de transfert de grab
    # ci-dessus) : fermer DataPurgeDialog PENDANT que le calendrier est
    # encore ouvert déclenchait un appel Tk réentrant (grab_set()/
    # focus_force() sur le dialogue PENDANT sa propre destruction). Voir
    # DataPurgeDialog._close_dialog (point de passage unique) et
    # _restore_dialog_grab (vérifie désormais self._closing).
    # ------------------------------------------------------------------

    def test_fermeture_normale_du_calendrier_puis_retour_au_dialogue(self):
        """1. Fermeture normale (sélection d'un jour) : le dialogue
        n'est jamais marqué "en fermeture", reste pleinement vivant et
        utilisable ensuite (on peut rouvrir un calendrier normalement)."""
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=0)
        day_btn = next(b for b in _all_buttons(picker) if b.cget("text") == "10")
        day_btn.invoke()

        self.assertFalse(dlg._closing)
        self.assertTrue(dlg.winfo_exists())
        self.assertEqual(str(dlg.grab_current()), str(dlg))

        picker2 = self._open_calendar(dlg, which=1)
        self.assertEqual(str(picker2.winfo_parent()), str(dlg))
        picker2.destroy()

    def test_fermeture_de_dataPurgeDialog_alors_que_le_calendrier_est_encore_ouvert(self):
        """2. + 6. : fermer DataPurgeDialog (via _close_dialog, SEUL
        point de passage) PENDANT que le calendrier est encore ouvert ne
        doit JAMAIS appeler grab_set()/focus_force() sur le dialogue
        lui-même — exactement la séquence qui a provoqué le SIGSEGV réel
        rapporté sur macOS. Aucun callback Tk ne doit donc toucher
        DataPurgeDialog après le début de sa destruction (self._closing
        posé AVANT destroy())."""
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=0)
        self.assertEqual(str(dlg.grab_current()), str(picker))

        grab_set_calls = []
        focus_force_calls = []
        orig_grab_set = dlg.grab_set
        orig_focus_force = dlg.focus_force
        dlg.grab_set = lambda *a, **k: (grab_set_calls.append(1), orig_grab_set(*a, **k))[-1]
        dlg.focus_force = lambda *a, **k: (focus_force_calls.append(1), orig_focus_force(*a, **k))[-1]

        dlg._close_dialog()

        self.assertEqual(
            grab_set_calls, [],
            "grab_set() ne doit JAMAIS être appelé sur DataPurgeDialog pendant sa propre destruction",
        )
        self.assertEqual(
            focus_force_calls, [],
            "focus_force() ne doit JAMAIS être appelé sur DataPurgeDialog pendant sa propre destruction",
        )
        self.assertTrue(dlg._closing)
        self.assertFalse(dlg.winfo_exists())

    def test_fermeture_par_annuler_pose_le_drapeau_et_detruit_le_dialogue(self):
        """3. Fermeture par le bouton "Annuler"."""
        dlg = self._make_dialog()
        self.assertFalse(dlg._closing)
        annuler_btn = next(b for b in _all_buttons(dlg) if b.cget("text") == "Annuler")
        annuler_btn.invoke()
        self.assertTrue(dlg._closing)
        self.assertFalse(dlg.winfo_exists())

    def test_fermeture_par_wm_delete_window_utilise_la_meme_fermeture_securisee(self):
        """4. Fermeture native (croix de la fenêtre / WM_DELETE_WINDOW)
        — doit passer EXACTEMENT par le même _close_dialog qu'Annuler,
        jamais un self.destroy() direct non protégé."""
        dlg = self._make_dialog()
        registered_cmd = dlg.protocol("WM_DELETE_WINDOW")
        self.assertTrue(registered_cmd, "WM_DELETE_WINDOW doit être enregistré sur DataPurgeDialog")
        self.assertFalse(dlg._closing)

        dlg.tk.call(registered_cmd)  # simule la fermeture native (bouton de fermeture du WM)

        self.assertTrue(dlg._closing)
        self.assertFalse(dlg.winfo_exists())

    def test_absence_de_double_restauration_grab_focus_lors_dune_fermeture_normale(self):
        """5. La cascade de destruction du calendrier déclenche <Destroy>
        pour chacun de ses ~30 widgets enfants (boutons de jour, frames)
        EN PLUS de lui-même — grab_set()/focus_force() sur DataPurgeDialog
        ne doivent s'exécuter qu'UNE SEULE fois au total, jamais une par
        widget enfant détruit."""
        dlg = self._make_dialog()
        picker = self._open_calendar(dlg, which=0)

        grab_set_calls = []
        focus_force_calls = []
        orig_grab_set = dlg.grab_set
        orig_focus_force = dlg.focus_force
        dlg.grab_set = lambda *a, **k: (grab_set_calls.append(1), orig_grab_set(*a, **k))[-1]
        dlg.focus_force = lambda *a, **k: (focus_force_calls.append(1), orig_focus_force(*a, **k))[-1]

        picker.destroy()

        self.assertEqual(len(grab_set_calls), 1, "le grab doit être restauré EXACTEMENT une fois")
        self.assertEqual(len(focus_force_calls), 1, "le focus doit être restauré EXACTEMENT une fois")

    def test_close_dialog_appele_deux_fois_ne_detruit_quune_seule_fois(self):
        """Robustesse de _close_dialog lui-même : un double déclenchement
        (ex. Annuler cliqué puis WM_DELETE_WINDOW qui se chevaucherait)
        ne doit jamais appeler destroy() une seconde fois."""
        dlg = self._make_dialog()
        orig_destroy = dlg.destroy
        destroy_calls = []
        dlg.destroy = lambda *a, **k: (destroy_calls.append(1), orig_destroy(*a, **k))[-1]

        dlg._close_dialog()
        dlg._close_dialog()

        self.assertEqual(len(destroy_calls), 1)


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class MultiFolderDiscoveryDialogTest(unittest.TestCase):
    """Découverte multi-dossiers dans DataPurgeDialog (demande du
    2026-10-07, diagnostic "0 tournoi" quand "Dossier par défaut" est
    vide) — la logique de combinaison/dédoublonnage elle-même est déjà
    couverte par tests/test_data_purge.py (CollectCandidateFoldersTest/
    SplitAccessibleFoldersTest/FindPurgeableTournamentsMultiTest) : ce
    fichier-ci ne vérifie que le CÂBLAGE (quelles sources l'interface
    fournit réellement, l'affichage "Dossiers examinés"/"inaccessibles",
    et "Ajouter un dossier à vérifier...")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_multi_dialog_")
        self.addCleanup(self._tmp.cleanup)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)

        self.usb = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.usb)

        self._patchers = [
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.poker_data_dir),
            patch.object(backup_history, "_history_dir", return_value=self.poker_data_dir),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _make_tournoi(self, parent_dir, filename, date="2026-01-01"):
        return _new_tournoi(parent_dir, filename, date)

    def _make_dialog(self, day_folder=None, last_dir=None, open_paths=(), action_log_paths=()):
        # Patchers démarrés (jamais un simple `with` scopé à la seule
        # construction) et arrêtés à la fin du TEST (addCleanup) : ces 4
        # sources doivent rester actives pendant TOUTE la durée du test,
        # y compris lors d'un appel ultérieur à "Faire une sauvegarde
        # maintenant..." (_on_backup_now -> App._run_backup_flow ->
        # backup_restore.create_backup, qui relit default_tournament_
        # folder() si non explicitement fourni) — sinon ce dernier
        # retomberait sur le VRAI réglage de la machine qui exécute les
        # tests entre la construction et cet appel.
        patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=day_folder),
            patch.object(export_prefs, "load_value", return_value=last_dir),
            patch.object(open_windows, "list_open_paths", return_value=list(open_paths)),
            patch.object(action_log, "list_tournaments", return_value=[
                {"tournament_name": "x", "tournament_path": p} for p in action_log_paths
            ]),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        _pump_until_scan_idle(dlg)
        _set_valid_purge_dates(dlg)
        return dlg

    def test_cas_du_mac_aucun_dossier_par_defaut_mais_last_tournament_dir_valide(self):
        """Cas RÉEL rencontré sur le Mac de l'utilisateur (2026-10-07) :
        "Dossier par défaut" vide (None), mais last_tournament_dir
        pointe vers un dossier valide contenant de vrais .tournoi —
        "Tournois concernés" ne doit PLUS afficher 0 dans ce cas."""
        last_dir = os.path.join(self._tmp.name, "samedi")
        p1 = self._make_tournoi(last_dir, "To031026.tournoi", "2026-10-03")
        dlg = self._make_dialog(day_folder=None, last_dir=last_dir)
        self.assertEqual(dlg._accessible_folders, [os.path.abspath(last_dir)])
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")
        self.assertIn(os.path.abspath(p1), dlg._purge_paths)

    def test_tournoi_actuellement_ouvert_dans_un_dossier_different_est_pris_en_compte(self):
        """Le dossier du tournoi actuellement ouvert (via open_windows)
        doit être examiné même s'il n'a aucun rapport avec "Dossier par
        défaut" ou last_tournament_dir (ex : fichier posé directement
        dans le dossier personnel)."""
        day_folder = os.path.join(self._tmp.name, "club")
        os.makedirs(day_folder)
        home_dir = os.path.join(self._tmp.name, "home")
        p_open = self._make_tournoi(home_dir, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=day_folder, open_paths=[p_open])
        self.assertIn(os.path.abspath(home_dir), dlg._accessible_folders)
        # Le tournoi ouvert est TROUVÉ par la découverte (son dossier est
        # examiné) mais JAMAIS proposé à la purge (voir open_windows,
        # revérifié indépendamment par find_open_tournaments/
        # purge_tournaments) :
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")

    def test_plusieurs_dossiers_action_log_sont_tous_examines(self):
        d1 = os.path.join(self._tmp.name, "Vendredi")
        d2 = os.path.join(self._tmp.name, "dimanche")
        p1 = self._make_tournoi(d1, "To010126.tournoi", "2026-01-01")
        p2 = self._make_tournoi(d2, "To020126.tournoi", "2026-01-02")
        dlg = self._make_dialog(action_log_paths=[p1, p2])
        self.assertEqual(
            sorted(dlg._accessible_folders), sorted([os.path.abspath(d1), os.path.abspath(d2)]),
        )
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 2 tournois")

    def test_chevauchement_de_dossiers_et_dedoublonnage_des_dossiers_eux_memes(self):
        """"Dossier par défaut" et last_tournament_dir pointant vers le
        MÊME dossier : ne doit apparaître qu'UNE SEULE fois dans
        "Dossiers examinés"."""
        folder = os.path.join(self._tmp.name, "club")
        self._make_tournoi(folder, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=folder, last_dir=folder)
        self.assertEqual(dlg._accessible_folders, [os.path.abspath(folder)])
        self.assertEqual(dlg.folders_examined_lbl.cget("text"), "Dossiers examinés : 1 dossier")

    def test_meme_tournoi_decouvert_depuis_plusieurs_sources_napparait_quune_fois(self):
        """Le MÊME fichier .tournoi, atteignable à la fois via "Dossier
        par défaut" (recherche récursive) ET via le dossier d'un
        tournoi ouvert (qui se trouve être le même dossier) : ne doit
        apparaître qu'UNE SEULE fois dans le compte."""
        folder = os.path.join(self._tmp.name, "club")
        p1 = self._make_tournoi(folder, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=folder, open_paths=[p1])
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")
        self.assertEqual(dlg._purge_paths.count(os.path.abspath(p1)), 1)

    def test_dossier_inaccessible_est_signale_separement(self):
        missing = os.path.join(self._tmp.name, "n_existe_plus")
        dlg = self._make_dialog(day_folder=None, action_log_paths=[os.path.join(missing, "x.tournoi")])
        self.assertEqual(dlg._inaccessible_folders, [os.path.abspath(missing)])
        self.assertEqual(
            dlg.folders_inaccessible_lbl.cget("text"),
            "Dossiers connus mais actuellement inaccessibles : 1 dossier",
        )
        self.assertEqual(str(dlg._folders_inaccessible_row.winfo_manager()), "pack")

    def test_dossier_usb_ou_externe_absent_naffiche_rien_et_ne_bloque_pas_louverture(self):
        """Un dossier connu correspondant à une clé USB/disque externe
        actuellement absent ne doit JAMAIS bloquer l'ouverture du
        dialogue — seulement apparaître dans "inaccessibles", le reste
        de l'interface restant pleinement utilisable."""
        usb_path = "/Volumes/CLE_USB_ABSENTE/tournois"
        day_folder = os.path.join(self._tmp.name, "club")
        self._make_tournoi(day_folder, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=day_folder, last_dir=usb_path)
        self.assertEqual(dlg._inaccessible_folders, [os.path.abspath(usb_path)])
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")  # pas encore vérifié, mais pas bloqué non plus

    def test_dossier_inaccessible_absent_naffiche_pas_la_ligne_quand_il_ny_en_a_aucun(self):
        day_folder = os.path.join(self._tmp.name, "club")
        self._make_tournoi(day_folder, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=day_folder)
        self.assertEqual(dlg._inaccessible_folders, [])
        self.assertEqual(str(dlg._folders_inaccessible_row.winfo_manager()), "")

    def test_ajout_manuel_dun_dossier_elargit_la_recherche(self):
        day_folder = os.path.join(self._tmp.name, "club")
        self._make_tournoi(day_folder, "To010126.tournoi", "2026-01-01")
        extra = os.path.join(self._tmp.name, "extra")
        p_extra = self._make_tournoi(extra, "To020126.tournoi", "2026-01-02")
        dlg = self._make_dialog(day_folder=day_folder)
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 1 tournoi")

        with patch("main.filedialog.askdirectory", return_value=extra), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=False):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg)

        self.assertIn(os.path.abspath(extra), dlg._accessible_folders)
        self.assertEqual(dlg.count_lbl.cget("text"), "Tournois concernés : 2 tournois")
        self.assertIn(os.path.abspath(p_extra), dlg._purge_paths)

    def test_ajout_manuel_annule_ne_change_rien(self):
        day_folder = os.path.join(self._tmp.name, "club")
        self._make_tournoi(day_folder, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=day_folder)
        before = list(dlg._accessible_folders)

        with patch("main.filedialog.askdirectory", return_value=""):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg)

        self.assertEqual(dlg._accessible_folders, before)
        self.assertEqual(dlg._manual_folders, [])

    def test_dossier_manuel_ne_modifie_aucune_preference_persistee(self):
        """"Ajouter un dossier à vérifier..." est STRICTEMENT limité à
        la session de purge en cours : ne doit JAMAIS écrire dans
        tournament_prefs (Dossier par défaut) ni dans export_prefs
        (last_tournament_dir)."""
        day_folder = os.path.join(self._tmp.name, "club")
        self._make_tournoi(day_folder, "To010126.tournoi", "2026-01-01")
        extra = os.path.join(self._tmp.name, "extra")
        os.makedirs(extra)
        dlg = self._make_dialog(day_folder=day_folder)

        with patch("main.filedialog.askdirectory", return_value=extra), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=False), \
                patch("tournament_prefs.save_last_settings") as mock_tp_save, \
                patch("export_prefs.save_value") as mock_ep_save:
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg)

        mock_tp_save.assert_not_called()
        mock_ep_save.assert_not_called()
        # Et le dossier automatique "Dossier par défaut" lui-même reste
        # inchangé (toujours résolu une seule fois, à l'ouverture) :
        self.assertEqual(dlg._auto_tournament_day_folder, day_folder)

    def test_filtre_dates_et_types_coherent_sur_plusieurs_dossiers(self):
        d1 = os.path.join(self._tmp.name, "a")
        d2 = os.path.join(self._tmp.name, "b")
        p_in = self._make_tournoi(d1, "To150126.tournoi", "2026-01-15")
        self._make_tournoi(d1, "To010126.tournoi", "2026-01-01")  # hors période
        self._make_tournoi(d2, "Sn160126.tournoi", "2026-01-16")  # type non coché
        dlg = self._make_dialog(day_folder=d1, action_log_paths=[os.path.join(d2, "Sn160126.tournoi")])
        dlg.date_from_var.set("10/01/2026")
        dlg.date_to_var.set("20/01/2026")
        dlg.type_vars[data_purge.PURGE_TYPE_SITNGO].set(False)  # Sit & Go explicitement décoché
        dlg._on_criteria_changed()
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._purge_paths, [os.path.abspath(p_in)])

    def test_tournoi_ouvert_reste_toujours_bloque_meme_decouvert_par_un_autre_dossier(self):
        day_folder = os.path.join(self._tmp.name, "club")
        p_open = self._make_tournoi(day_folder, "To010126.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=day_folder, open_paths=[p_open])
        dlg._on_verify_backup()
        with patch("main.filedialog.askdirectory", return_value=self.usb), \
                patch("main.messagebox.showinfo"):
            dlg._on_backup_now()
        with patch.object(open_windows, "list_open_paths", return_value=[p_open]), \
                patch("main.messagebox.showerror") as mock_err:
            dlg._on_purge_clicked()
        mock_err.assert_called_once()
        self.assertTrue(os.path.isfile(p_open))

    def test_sauvegarde_multi_dossiers_memes_noms_couvre_bien_toute_la_selection_et_debloque_purger(self):
        """Reproduit EXACTEMENT l'incident réel rapporté le 2026-10-08 :
        la sauvegarde lancée depuis "Faire une sauvegarde maintenant..."
        annonçait "Tournois sauvegardés : 0" alors que le scan de Purger
        Données trouvait des fichiers dans plusieurs dossiers (dont deux
        portant le même nom) — correctif : self._purge_paths/self.
        _accessible_folders sont désormais transmis explicitement,
        aucune collision, et "Vérifier la sauvegarde..." reconnaît
        ensuite les DEUX fichiers, débloquant réellement "Purger..."."""
        day_folder = os.path.join(self._tmp.name, "club_dimanche")
        last_dir = os.path.join(self._tmp.name, "poste_distant_dimanche")
        p1 = self._make_tournoi(day_folder, "To1.tournoi", "2026-01-01")
        p2 = self._make_tournoi(last_dir, "To1.tournoi", "2026-01-02")  # même nom, autre dossier
        dlg = self._make_dialog(day_folder=day_folder, last_dir=last_dir)
        self.assertEqual(sorted(dlg._purge_paths), sorted([os.path.abspath(p1), os.path.abspath(p2)]))

        with patch("main.filedialog.askdirectory", return_value=self.usb), \
                patch("main.messagebox.showinfo") as mock_info:
            dlg._on_backup_now()
        mock_info.assert_called_once()
        success_text = mock_info.call_args[0][1]
        self.assertIn("Tournois sauvegardés : 2", success_text, "jamais 0 alors que 2 étaient sélectionnés")

        self.assertIsNotNone(dlg._coverage, "_on_backup_now doit relancer la vérification automatiquement")
        self.assertTrue(dlg._coverage["all_covered"])
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")
        # Les fichiers d'origine restent eux-mêmes totalement intacts.
        self.assertTrue(os.path.isfile(p1))
        self.assertTrue(os.path.isfile(p2))

    def test_divergence_entre_la_selection_demandee_et_le_compte_reel_affiche_une_erreur_jamais_un_succes(self):
        """Garde-fou défensif supplémentaire (ne devrait jamais arriver
        en pratique : create_backup annule déjà tout au moindre fichier
        manquant) — demande explicite du 2026-10-08, point 6 : "si aucun
        tournoi n'est effectivement sauvegardé alors que la sélection
        est non vide, afficher une erreur claire, jamais un succès
        trompeur". Simulé ici en forçant create_backup à renvoyer un
        compte divergent."""
        day_folder = os.path.join(self._tmp.name, "club")
        p1 = self._make_tournoi(day_folder, "To1.tournoi", "2026-01-01")
        dlg = self._make_dialog(day_folder=day_folder)
        self.assertEqual(dlg._purge_paths, [os.path.abspath(p1)])

        fake_result = {"backup_dir": self.usb, "tournament_file_count": 0, "poker_tournament_data_included": True}
        with patch("main.filedialog.askdirectory", return_value=self.usb), \
                patch.object(backup_restore, "create_backup", return_value=fake_result), \
                patch("main.messagebox.showerror") as mock_err, \
                patch("main.messagebox.showinfo") as mock_info, \
                patch.object(backup_history, "record_backup") as mock_record:
            dlg._on_backup_now()
        mock_err.assert_called_once()
        self.assertIn("incohérent", mock_err.call_args[0][1])
        mock_info.assert_not_called()
        mock_record.assert_not_called()
        self.assertIsNone(dlg._coverage)
        self.assertEqual(str(dlg.purge_btn.cget("state")), "disabled")


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class BackgroundScanDialogTest(unittest.TestCase):
    """Recherche BORNÉE, ANNULABLE, en ARRIÈRE-PLAN (incident macOS réel
    du 2026-10-08 : ajouter /Users/raj comme dossier candidat a bloqué
    Senaco indéfiniment — curseur arc-en-ciel, forçage de fermeture).
    data_purge.py (scan_folder_for_tournaments/scan_candidate_folders/
    scan_candidate_folders_for_purge) est déjà couvert en détail, sans
    Tkinter, par tests/test_data_purge.py — ce fichier-ci vérifie
    uniquement l'ORCHESTRATION du threading et l'interface elle-même."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_bg_scan_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)
        self.usb = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.usb)

        self._patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=self.tournament_folder),
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.poker_data_dir),
            patch.object(backup_history, "_history_dir", return_value=self.poker_data_dir),
            patch.object(export_prefs, "load_value", return_value=None),
            patch.object(open_windows, "list_open_paths", return_value=[]),
            patch.object(action_log, "list_tournaments", return_value=[]),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _make_tournoi(self, filename, date="2026-01-01"):
        return _new_tournoi(self.tournament_folder, filename, date)

    def _make_dialog(self):
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        _pump_until_scan_idle(dlg)
        _set_valid_purge_dates(dlg)
        return dlg

    # ------------------------------------------------------------------
    # 1. /Users/raj en recherche non récursive (scénario réel rapporté)
    # ------------------------------------------------------------------

    def test_ajout_manuel_de_Users_raj_non_recursif_ne_bloque_jamais_et_trouve_directement(self):
        """Reproduit EXACTEMENT le scénario rapporté le 2026-10-08 : un
        dossier personnel réel (/Users/raj), potentiellement très
        volumineux, ajouté manuellement en mode "ce dossier uniquement"
        (le choix PAR DÉFAUT) — ne doit jamais bloquer, doit se
        terminer rapidement."""
        if not os.path.isdir("/Users/raj"):
            self.skipTest("/Users/raj n'existe pas sur cette machine")
        dlg = self._make_dialog()
        start = time.monotonic()
        with patch("main.filedialog.askdirectory", return_value="/Users/raj"), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=False):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg, timeout=15)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 15, "ne doit jamais bloquer indéfiniment")
        self.assertIn(os.path.abspath("/Users/raj"), dlg._accessible_folders)
        self.assertFalse(dlg._scan_incomplete, "une recherche NON récursive sur un seul niveau ne devrait pas être tronquée")

    def test_ajout_manuel_recursif_est_un_choix_explicite_jamais_par_defaut(self):
        """"Ce dossier uniquement" doit être PRÉSÉLECTIONNÉ — un choix
        récursif exige un geste explicite de l'utilisateur (demande du
        2026-10-08).

        Utilise _build_recursive_scope_dialog (construction SEULE,
        sans wait_window ni transition de grab) plutôt que la vraie
        _ask_recursive_scope bloquante : un essai de clic via self.after
        PENDANT son wait_window a provoqué un VRAI SIGSEGV, reproduit de
        façon fiable (même famille de crash Tk/Aqua réentrant que celle
        déjà rencontrée pendant ce chantier) — jamais retenter cette
        combinaison dans un test."""
        dlg = self._make_dialog()
        extra = os.path.join(self._tmp.name, "extra")
        os.makedirs(extra)

        win, recursive_var, result = dlg._build_recursive_scope_dialog(extra)
        self.assertFalse(recursive_var.get(), '"Ce dossier uniquement" doit être présélectionné')
        ok_btn = next(b for b in _all_buttons(win) if b.cget("text") == "OK")
        ok_btn.invoke()  # ne change pas la sélection — valide juste le choix par défaut
        self.assertFalse(result["value"])
        self.assertFalse(win.winfo_exists())

    def test_choix_recursif_explicite_est_bien_respecte(self):
        extra = os.path.join(self._tmp.name, "extra")
        p_nested = _new_tournoi(os.path.join(extra, "sous_dossier"), "To020126.tournoi", "2026-01-02")
        dlg = self._make_dialog()
        with patch("main.filedialog.askdirectory", return_value=extra), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=True):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg)
        self.assertTrue(dlg._manual_folder_recursive[os.path.abspath(extra)])
        self.assertIn(os.path.abspath(p_nested), dlg._purge_paths)

    # ------------------------------------------------------------------
    # 2. Dossier volumineux avec limites atteintes
    # ------------------------------------------------------------------

    def test_dossier_volumineux_tronque_affiche_le_resultat_partiel_et_bloque_verifier(self):
        big = os.path.join(self._tmp.name, "gros_dossier")
        _new_tournoi(big, "To010126.tournoi", "2026-01-01")
        for i in range(200):
            os.makedirs(os.path.join(big, f"d{i}"))
        dlg = self._make_dialog()
        real_scan = data_purge.scan_candidate_folders_for_purge

        # Patcher le module directement (data_purge.DEFAULT_SCAN_MAX_
        # ENTRIES, valeur par défaut déjà LIÉE à la signature de la
        # fonction au moment de sa définition) n'aurait aucun effet ici
        # — on force donc une limite minuscule via un wrapper explicite,
        # pour forcer une troncature déterministe sans dépendre d'un
        # dossier réellement énorme.
        def _tiny_scan(folders, recursive_folders=None, date_from=None, date_to=None,
                        types=None, cancel_event=None, **_kw):
            return real_scan(
                folders, recursive_folders=recursive_folders, date_from=date_from, date_to=date_to,
                types=types, max_entries=5, max_seconds=999, cancel_event=cancel_event,
            )

        with patch("main.filedialog.askdirectory", return_value=big), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=True), \
                patch.object(data_purge, "scan_candidate_folders_for_purge", side_effect=_tiny_scan):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg, timeout=15)
        self.assertTrue(dlg._scan_incomplete)
        self.assertTrue(dlg._truncated_folders)
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")
        self.assertIn("incomplet", dlg.scan_status_lbl.cget("text").lower())

    # ------------------------------------------------------------------
    # 3. Annulation pendant la recherche
    # ------------------------------------------------------------------

    def test_annulation_pendant_la_recherche_marque_le_resultat_incomplet(self):
        dlg = self._make_dialog()
        release_event = threading.Event()

        def _slow_scan(*a, **k):
            release_event.wait(5)
            return {
                "purge_paths": [], "all_files": [], "other_files": [], "unreadable_files": [],
                "truncated_folders": {}, "skipped_dirs": [], "cancelled": True,
            }

        with patch.object(data_purge, "scan_candidate_folders_for_purge", side_effect=_slow_scan):
            dlg._start_scan()
            self.assertTrue(dlg._scan_in_progress)
            self.assertEqual(str(dlg.cancel_scan_btn.winfo_manager()), "pack")
            dlg._on_cancel_scan()
            self.assertTrue(dlg._scan_cancel_event.is_set())
            release_event.set()
            _pump_until_scan_idle(dlg, timeout=10)

        self.assertTrue(dlg._scan_cancelled)
        self.assertTrue(dlg._scan_incomplete)
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")
        self.assertEqual(str(dlg.cancel_scan_btn.winfo_manager()), "")

    # ------------------------------------------------------------------
    # 4. Fermeture du dialogue PENDANT la recherche
    # ------------------------------------------------------------------

    def test_fermeture_du_dialogue_pendant_la_recherche_ne_bloque_jamais_et_annule(self):
        """Sécurité Tkinter/macOS (demande explicite du 2026-10-08) :
        fermer DataPurgeDialog pendant qu'une recherche tourne doit (1)
        annuler le travail en cours, (2) ne JAMAIS bloquer, (3) ne
        déclencher AUCUN callback Tk vers une fenêtre détruite, (4) ne
        réintroduire aucun risque de SIGSEGV (conserve le correctif
        _closing déjà validé pour le calendrier)."""
        dlg = self._make_dialog()
        release_event = threading.Event()

        def _slow_scan(*a, **k):
            release_event.wait(5)
            return {
                "purge_paths": [], "all_files": [], "other_files": [], "unreadable_files": [],
                "truncated_folders": {}, "skipped_dirs": [], "cancelled": True,
            }

        with patch.object(data_purge, "scan_candidate_folders_for_purge", side_effect=_slow_scan):
            dlg._start_scan()
            self.assertTrue(dlg._scan_in_progress)

            start = time.monotonic()
            dlg._close_dialog()
            elapsed = time.monotonic() - start

            self.assertLess(elapsed, 0.5, "_close_dialog ne doit JAMAIS bloquer, même pendant un scan actif")
            self.assertTrue(dlg._scan_cancel_event.is_set(), "la fermeture doit signaler l'annulation au thread")
            self.assertFalse(dlg.winfo_exists())
            self.assertIsNone(dlg._poll_after_id, "le sondage programmé doit être désarmé")

            release_event.set()  # débloque le thread tardif
        time.sleep(0.2)  # laisse le thread se terminer et déposer son résultat (jamais lu, jamais de crash)

    # ------------------------------------------------------------------
    # 5. Recherche partielle bloquant la purge
    # ------------------------------------------------------------------

    def test_recherche_tronquee_bloque_purger_meme_si_des_tournois_sont_affiches(self):
        p1 = self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg._scan_incomplete = True
        dlg._truncated_folders = {self.tournament_folder: data_purge.SCAN_REASON_MAX_ENTRIES}
        dlg._purge_paths = [p1]  # un résultat partiel PEUT afficher des tournois déjà trouvés
        with patch("main.messagebox.showerror") as mock_err:
            dlg._on_purge_clicked()
        mock_err.assert_called_once()
        self.assertTrue(os.path.isfile(p1))

    def test_recherche_annulee_bloque_verifier_la_sauvegarde(self):
        dlg = self._make_dialog()
        dlg._scan_cancelled = True
        dlg._scan_incomplete = True
        dlg._purge_paths = [self._make_tournoi("To010126.tournoi")]
        with patch("main.messagebox.showinfo") as mock_info:
            dlg._on_verify_backup()
        mock_info.assert_called_once()
        self.assertIsNone(dlg._coverage)

    # ------------------------------------------------------------------
    # 6. Recherche terminée (propre) : vérification normale autorisée
    # ------------------------------------------------------------------

    def test_recherche_complete_et_non_annulee_autorise_la_verification_normale(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertFalse(dlg._scan_incomplete)
        self.assertEqual(str(dlg.verify_btn.cget("state")), "normal")
        with patch("main.filedialog.askdirectory", return_value=self.usb), \
                patch("main.messagebox.showinfo"):
            dlg._on_backup_now()
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")

    # ------------------------------------------------------------------
    # 7. Absence d'appels Tk depuis le thread de recherche
    # ------------------------------------------------------------------

    def test_run_scan_worker_est_une_methode_statique_sans_acces_a_self(self):
        """Garantie STRUCTURELLE (pas seulement conventionnelle) qu'aucun
        appel Tkinter ne peut être fait depuis le thread de recherche :
        _run_scan_worker est une méthode STATIQUE — elle ne reçoit
        jamais `self` (l'instance Tk), seulement une queue.Queue et des
        valeurs Python simples. Impossible, par construction, d'y
        référencer un widget."""
        import inspect
        raw = inspect.getattr_static(main.DataPurgeDialog, "_run_scan_worker")
        self.assertIsInstance(raw, staticmethod)
        sig = inspect.signature(main.DataPurgeDialog._run_scan_worker)
        self.assertNotIn("self", sig.parameters)

    def test_le_thread_de_recherche_ne_touche_que_la_queue_jamais_un_widget(self):
        """Exécute RÉELLEMENT _run_scan_worker sur un thread séparé (pas
        seulement une inspection statique) et vérifie qu'il ne lève
        jamais et ne communique qu'via la queue fournie."""
        q = queue.Queue()
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=main.DataPurgeDialog._run_scan_worker,
            args=(q, 1, [self.tournament_folder], {self.tournament_folder}, None, None,
                  {data_purge.PURGE_TYPE_TOURNOIS}, cancel_event),
            daemon=True,
        )
        thread.start()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        generation, result = q.get_nowait()
        self.assertEqual(generation, 1)
        self.assertIn("purge_paths", result)

    # ------------------------------------------------------------------
    # 9. Plusieurs dossiers et dédoublonnage (niveau dialogue/thread)
    # ------------------------------------------------------------------

    def test_plusieurs_dossiers_dedoublonnage_via_le_scan_en_arriere_plan(self):
        p1 = self._make_tournoi("To010126.tournoi")
        extra = os.path.join(self._tmp.name, "extra")
        p2 = _new_tournoi(extra, "To020126.tournoi", "2026-01-02")
        dlg = self._make_dialog()
        with patch("main.filedialog.askdirectory", return_value=extra), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=False):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg)
        self.assertEqual(sorted(dlg._purge_paths), sorted([os.path.abspath(p1), os.path.abspath(p2)]))
        self.assertEqual(len(dlg._purge_paths), len(set(dlg._purge_paths)))


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class ClassificationDisplayDialogTest(unittest.TestCase):
    """Câblage UI de la transparence sur la classification réelle des
    tournois (demande explicite du 2026-10-08, diagnostic "0 Sit & Go
    trouvé" malgré des sitngo1.tournoi...sitngon.tournoi réels — voir
    data_purge.classify_tournament_for_purge). La logique métier (is_sng
    interne prioritaire sur le nom, exclusion des sauvegardes à toutes
    profondeurs, fichiers illisibles jamais éligibles) est déjà couverte
    en détail, sans Tkinter, par tests/test_data_purge.py — ce fichier-
    ci vérifie uniquement que DataPurgeDialog affiche bien, séparément,
    les "Autres / non classés" (consultables même décochés) et les
    fichiers illisibles (jamais une exception, jamais purgeables)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_classification_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)
        self.usb = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.usb)

        self._patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=self.tournament_folder),
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.poker_data_dir),
            patch.object(backup_history, "_history_dir", return_value=self.poker_data_dir),
            patch.object(export_prefs, "load_value", return_value=None),
            patch.object(open_windows, "list_open_paths", return_value=[]),
            patch.object(action_log, "list_tournaments", return_value=[]),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _make_tournoi(self, filename, date="2026-01-01", is_sng=None):
        return _new_tournoi(self.tournament_folder, filename, date, is_sng=is_sng)

    def _make_dialog(self):
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        _pump_until_scan_idle(dlg)
        _set_valid_purge_dates(dlg)
        return dlg

    def test_sitngo_renomme_avec_is_sng_est_compte_en_sit_and_go_jamais_en_autre(self):
        """Reproduit le scénario réel rapporté le 2026-10-08 : des Sit &
        Go ont été renommés "sitngo1.tournoi"..."sitngon.tournoi" mais
        portent bien is_sng=1 à l'intérieur — ne doivent plus tomber
        dans "Autres / non classés"."""
        self._make_tournoi("sitngo1.tournoi", is_sng=True)
        dlg = self._make_dialog()
        self.assertEqual(dlg._other_files, [])
        self.assertTrue(dlg._other_files_row.winfo_manager(), "la ligne compteur reste toujours visible")
        self.assertEqual(str(dlg._other_files_toggle_btn.cget("state")), "disabled")
        self.assertGreaterEqual(len(dlg._purge_paths), 1)

    def test_fichier_ancien_sans_is_sng_et_sans_prefixe_apparait_dans_autres_consultable_meme_decoche(self):
        """Un ancien fichier réel (type "essai ac.tournoi") sans is_sng
        et sans préfixe reconnu doit rester visible (nom + chemin) dans
        "Autres / non classés" — MÊME quand cette catégorie n'est pas
        cochée (décochée par défaut) — jamais besoin de la cocher pour
        l'identifier."""
        p = self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        self.assertFalse(dlg.type_vars[data_purge.PURGE_TYPE_OTHER].get(), '"Autres" doit rester décoché par défaut')
        self.assertIn(os.path.abspath(p), dlg._other_files)
        self.assertTrue(dlg._other_files_row.winfo_manager())
        self.assertIn(os.path.abspath(p), dlg.other_files_text.get("1.0", "end"))
        self.assertNotIn(os.path.abspath(p), dlg._purge_paths)

    def test_fichier_sqlite_corrompu_apparait_separement_et_nest_jamais_purgeable(self):
        """Un fichier .tournoi corrompu doit être signalé séparément
        (avec une indication du problème), jamais confondu avec "Autres
        / non classés", et jamais éligible à une purge quels que soient
        les types cochés."""
        p = self._make_tournoi("corrompu.tournoi")
        with open(p, "r+b") as f:
            f.seek(0)
            f.write(b"\x00" * 32)
        dlg = self._make_dialog()
        self.assertEqual(len(dlg._unreadable_files), 1)
        self.assertEqual(dlg._unreadable_files[0]["path"], os.path.abspath(p))
        self.assertTrue(dlg._unreadable_files[0]["error"])
        self.assertTrue(dlg._unreadable_files_row.winfo_manager())
        self.assertIn(os.path.abspath(p), dlg.unreadable_files_text.get("1.0", "end"))
        self.assertNotIn(os.path.abspath(p), dlg._other_files)
        self.assertNotIn(os.path.abspath(p), dlg._purge_paths)
        for var in dlg.type_vars.values():
            var.set(True)
        dlg._on_criteria_changed()
        _pump_until_scan_idle(dlg)
        self.assertNotIn(os.path.abspath(p), dlg._purge_paths)

    def test_dossier_de_sauvegarde_jamais_affiche_ni_en_autre_ni_en_illisible_ni_en_purgeable(self):
        """Une copie de sauvegarde (PokerTournament_Backup_*) ne doit
        apparaître dans AUCUNE des trois catégories affichées — elle
        doit être entièrement invisible à la recherche de purge, à
        toutes les profondeurs."""
        backup_dir = os.path.join(
            self.tournament_folder, f"{backup_restore.BACKUP_FOLDER_PREFIX}2026-10-07_1232", "sous_dossier",
        )
        p_backup = _new_tournoi(backup_dir, "sitngo1.tournoi", is_sng=True)
        dlg = self._make_dialog()
        self.assertNotIn(os.path.abspath(p_backup), dlg._purge_paths)
        self.assertNotIn(os.path.abspath(p_backup), dlg._other_files)
        self.assertNotIn(os.path.abspath(p_backup), [e["path"] for e in dlg._unreadable_files])

    def test_compteurs_toujours_visibles_meme_a_zero_bouton_afficher_desactive(self):
        """Revu le 2026-10-08 (bis) : contrairement au comportement
        précédent (ligne entière masquée à 0), "Autres / non classés"
        et "Fichiers illisibles ou corrompus" doivent TOUJOURS afficher
        leur compteur, y compris "0 fichier" — seul le bouton
        "Afficher..." est désactivé (jamais masqué, pour stabiliser la
        présentation) et la liste défilante reste repliée."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(dlg._other_files, [])
        self.assertEqual(dlg._unreadable_files, [])
        self.assertTrue(dlg._other_files_row.winfo_manager(), "la ligne compteur reste toujours visible")
        self.assertTrue(dlg._unreadable_files_row.winfo_manager(), "la ligne compteur reste toujours visible")
        self.assertEqual(dlg.other_files_lbl.cget("text"), "Autres / non classés : 0 fichier")
        self.assertEqual(dlg.unreadable_files_lbl.cget("text"), "Fichiers illisibles ou corrompus : 0 fichier")
        self.assertEqual(str(dlg._other_files_toggle_btn.cget("state")), "disabled")
        self.assertEqual(str(dlg._unreadable_files_toggle_btn.cget("state")), "disabled")
        self.assertEqual(dlg._other_files_toggle_btn.cget("text"), "Afficher...")
        self.assertEqual(dlg._unreadable_files_toggle_btn.cget("text"), "Afficher...")
        self.assertFalse(dlg._other_files_list_frame.winfo_manager())
        self.assertFalse(dlg._unreadable_files_list_frame.winfo_manager())

    def test_nouvelle_recherche_reinitialise_bien_les_deux_listes(self):
        """Un ajout manuel de dossier relance une recherche complète
        (_refresh_folders) — les anciennes listes "Autres"/illisibles
        ne doivent jamais survivre périmées d'une recherche précédente."""
        self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(len(dlg._other_files), 1)
        extra = os.path.join(self._tmp.name, "extra_vide")
        os.makedirs(extra)
        with patch("main.filedialog.askdirectory", return_value=extra), \
                patch.object(main.DataPurgeDialog, "_ask_recursive_scope", return_value=False):
            dlg._on_add_folder()
        _pump_until_scan_idle(dlg)
        self.assertEqual(len(dlg._other_files), 1, "le fichier 'Autre' du premier dossier doit toujours être signalé")

    # ------------------------------------------------------------------
    # Correctif du 2026-10-08 (bis) : listes longues sans ascenseur
    # ("Autres" avec 25 fichiers débordait du cadre, illisible au-delà)
    # ------------------------------------------------------------------

    def test_liste_autres_a_une_hauteur_fixe_independante_du_nombre_de_fichiers(self):
        """Reproduit le nombre exact rapporté (25 fichiers "Autres") :
        la hauteur du widget (en lignes) doit rester FIXE quel que soit
        le nombre réel d'entrées — c'est elle qui garantit l'absence de
        débordement sur les autres éléments/boutons de la fenêtre, pas
        une limite sur le nombre de fichiers affichés."""
        paths = [self._make_tournoi(f"essai{i}.tournoi") for i in range(25)]
        dlg = self._make_dialog()
        self.assertEqual(len(dlg._other_files), 25)
        self.assertIsInstance(dlg.other_files_text, tk.Text)
        self.assertEqual(int(dlg.other_files_text.cget("height")), 8)
        content = dlg.other_files_text.get("1.0", "end")
        for p in paths:
            self.assertIn(os.path.abspath(p), content, "aucun fichier ne doit disparaître de la liste, même au-delà de la hauteur visible")

    def test_liste_autres_possede_un_ascenseur_vertical_relie_au_defilement(self):
        """Un seul ascenseur vertical, réellement relié au widget Text
        (jamais un simple décor) — défiler via l'ascenseur ne doit
        lever aucune exception."""
        self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        scrollbars = [w for w in dlg._other_files_list_frame.winfo_children() if isinstance(w, ttk.Scrollbar)]
        self.assertEqual(len(scrollbars), 1, "la liste doit avoir exactement un ascenseur vertical")
        self.assertEqual(str(scrollbars[0].cget("orient")), "vertical")
        dlg.other_files_text.yview_scroll(1, "units")

    def test_bouton_afficher_masquer_fonctionne_toujours_avec_la_liste_defilante(self):
        """Exigence explicite : conserver les boutons "Afficher.../
        Masquer" malgré le passage à une liste défilante."""
        self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        self.assertFalse(dlg._other_files_list_frame.winfo_manager(), "repliée par défaut")
        toggle_btn = _all_buttons(dlg._other_files_row)[0]
        self.assertEqual(toggle_btn.cget("text"), "Afficher...")
        toggle_btn.invoke()
        self.assertTrue(dlg._other_files_list_frame.winfo_manager())
        self.assertEqual(toggle_btn.cget("text"), "Masquer")
        toggle_btn.invoke()
        self.assertFalse(dlg._other_files_list_frame.winfo_manager())
        self.assertEqual(toggle_btn.cget("text"), "Afficher...")

    def test_nom_de_fichier_tres_long_reste_integralement_present_sans_troncature(self):
        """Exigence explicite : conserver les chemins complets, sans
        les tronquer — wrap="char" garantit un retour à la ligne
        caractère par caractère plutôt qu'une coupure du texte."""
        long_name = "essai_" + ("x" * 180) + ".tournoi"
        p = self._make_tournoi(long_name)
        dlg = self._make_dialog()
        self.assertEqual(str(dlg.other_files_text.cget("wrap")), "char")
        content = dlg.other_files_text.get("1.0", "end")
        self.assertIn(os.path.abspath(p), content)

    def test_le_widget_texte_est_en_lecture_seule_pour_lutilisateur(self):
        """La liste est purement informative — jamais un champ que
        l'utilisateur pourrait modifier lui-même : à l'état "disabled",
        Tk ignore silencieusement toute tentative d'insertion (aucune
        exception, mais aucun effet non plus)."""
        p = self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(str(dlg.other_files_text.cget("state")), "disabled")
        before = dlg.other_files_text.get("1.0", "end")
        dlg.other_files_text.insert("1.0", "tentative d'édition")
        self.assertEqual(dlg.other_files_text.get("1.0", "end"), before)
        self.assertIn(os.path.abspath(p), before)

    def test_liste_autres_reprend_les_couleurs_du_theme_senaco_fond_vert_texte_clair(self):
        """Correction visuelle demandée le 2026-10-08 : tk.Text ignore
        le thème ttk et s'affichait en fond blanc/texte clair,
        illisible. Doit reprendre EXACTEMENT les couleurs déjà
        utilisées ailleurs dans la fenêtre pour du texte sur fond vert
        foncé (main.FELT / main.CREAM — voir style.configure("TFrame"/
        "TLabel", ...)), jamais une couleur arbitraire nouvelle."""
        self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(str(dlg.other_files_text.cget("background")), main.FELT)
        self.assertEqual(str(dlg.other_files_text.cget("foreground")), main.CREAM)

    def test_liste_illisibles_a_aussi_une_hauteur_fixe_et_un_ascenseur(self):
        """Exigence explicite : appliquer le même principe à la liste
        "Fichiers illisibles ou corrompus"."""
        for i in range(3):
            p = self._make_tournoi(f"corrompu{i}.tournoi")
            with open(p, "r+b") as f:
                f.seek(0)
                f.write(b"\x00" * 32)
        dlg = self._make_dialog()
        self.assertEqual(len(dlg._unreadable_files), 3)
        self.assertIsInstance(dlg.unreadable_files_text, tk.Text)
        self.assertEqual(int(dlg.unreadable_files_text.cget("height")), 8)
        scrollbars = [w for w in dlg._unreadable_files_list_frame.winfo_children() if isinstance(w, ttk.Scrollbar)]
        self.assertEqual(len(scrollbars), 1)
        self.assertEqual(str(dlg.unreadable_files_text.cget("state")), "disabled")
        self.assertEqual(str(dlg.unreadable_files_text.cget("background")), main.FELT)
        self.assertEqual(str(dlg.unreadable_files_text.cget("foreground")), main.CREAM)

    def test_classification_et_compteurs_inchanges_par_le_correctif_dinterface(self):
        """Exigence explicite : ne modifier ni la classification, ni
        les compteurs — seule l'AFFICHAGE de la liste change, jamais ce
        qui est compté ou proposé à la purge."""
        self._make_tournoi("sitngo1.tournoi", is_sng=True)
        self._make_tournoi("essai ac.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(len(dlg._other_files), 1)
        self.assertEqual(len(dlg._purge_paths), 1)
        self.assertIn("Tournois concernés : 1", dlg.count_lbl.cget("text"))


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class PurgeDateSecurityDialogTest(unittest.TestCase):
    """Sécurité des dates de purge (revue explicite du 2026-10-08,
    "LOG → Purger → DONNÉES") : les deux dates Du/Au sont désormais
    OBLIGATOIRES pour "Purger Données" — jamais une date vide
    interprétée comme une période illimitée. La garantie RÉELLE vit
    dans data_purge.scan_candidate_folders_for_purge(require_date_
    range=True) (voir tests.test_data_purge.RequireDateRangeTest) ;
    ce fichier-ci vérifie seulement le CÂBLAGE et le COMPORTEMENT de
    l'interface : aucune popup pendant la saisie, message non modal
    sous les champs, boutons désactivés, et qu'un appel direct à
    _run_scan_worker/scan_candidate_folders_for_purge passe bien
    require_date_range=True (donc que ce chemin d'exécution réel
    applique la protection, pas seulement une validation locale
    contournable)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_date_security_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)

        self._patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=self.tournament_folder),
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.poker_data_dir),
            patch.object(backup_history, "_history_dir", return_value=self.poker_data_dir),
            patch.object(export_prefs, "load_value", return_value=None),
            patch.object(open_windows, "list_open_paths", return_value=[]),
            patch.object(action_log, "list_tournaments", return_value=[]),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _make_tournoi(self, filename, date="2026-01-01"):
        return _new_tournoi(self.tournament_folder, filename, date)

    def _make_dialog(self):
        """Volontairement SANS _set_valid_purge_dates ici (contrairement
        aux autres classes de ce fichier) : cette classe teste
        précisément le comportement Du/Au, qui doivent rester à leur
        état initial réel (vides) tant qu'un test ne les renseigne pas
        lui-même."""
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        _pump_until_scan_idle(dlg)
        return dlg

    # ------------------------------------------------------------------
    # Les 5 situations demandées explicitement
    # ------------------------------------------------------------------

    def test_les_deux_dates_vides_ne_selectionne_rien(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(dlg._purge_paths, [])
        self.assertEqual(dlg._date_status, "missing")
        self.assertTrue(dlg._dates_required)
        self.assertIn("Renseignez les deux dates", dlg.date_status_lbl.cget("text"))
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")

    def test_seule_la_date_de_debut_renseignee_ne_selectionne_rien(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_from_var.set("01/01/2026")
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._purge_paths, [])
        self.assertEqual(dlg._date_status, "missing")
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")

    def test_seule_la_date_de_fin_renseignee_ne_selectionne_rien(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._purge_paths, [])
        self.assertEqual(dlg._date_status, "missing")
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")

    def test_date_invalide_ne_selectionne_rien_et_naffiche_aucune_popup(self):
        """Protection CONSERVÉE (date impossible toujours bloquée) mais
        présentée de façon non modale désormais — jamais une popup."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        with patch("main.messagebox.showerror") as mock_err:
            dlg.date_from_var.set("31/02/2026")  # 31 février n'existe pas
            dlg.date_to_var.set("31/12/2026")
            _pump_until_scan_idle(dlg)
        mock_err.assert_not_called()
        self.assertEqual(dlg._purge_paths, [])
        self.assertEqual(dlg._date_status, "invalid")
        self.assertIn("invalide", dlg.date_status_lbl.cget("text").lower())
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")

    def test_periode_inversee_ne_selectionne_rien_et_naffiche_aucune_popup(self):
        """Du > Au : protection CONSERVÉE, et réellement appliquée par
        data_purge.py (require_date_range=True) puisque ces deux dates,
        individuellement valides, sont transmises telles quelles à la
        VRAIE recherche — jamais seulement court-circuitées ici."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        with patch("main.messagebox.showerror") as mock_err:
            dlg.date_from_var.set("01/06/2026")
            dlg.date_to_var.set("01/01/2026")
            _pump_until_scan_idle(dlg)
        mock_err.assert_not_called()
        self.assertEqual(dlg._purge_paths, [])
        self.assertEqual(dlg._date_status, "inverted")
        self.assertTrue(dlg._dates_required, "doit venir de data_purge.py (dates_required), pas seulement d'ici")
        self.assertIn("Du", dlg.date_status_lbl.cget("text"))
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")

    def test_periode_complete_et_valide_autorise_la_selection(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_from_var.set("01/01/2026")
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)
        self.assertEqual(len(dlg._purge_paths), 1)
        self.assertEqual(dlg._date_status, "ok")
        self.assertFalse(dlg._dates_required)
        self.assertEqual(dlg.date_status_lbl.cget("text"), "")
        self.assertEqual(str(dlg.verify_btn.cget("state")), "normal")

    # ------------------------------------------------------------------
    # Saisie manuelle progressive — jamais de popup, boutons désactivés
    # ------------------------------------------------------------------

    def test_saisie_manuelle_progressive_naffiche_aucune_popup_et_bloque_la_selection(self):
        """Tape "01/01/2026" caractère par caractère (demande explicite
        du 2026-10-08, "éviter les popups répétitives pendant la saisie
        manuelle ; ne valider complètement une date qu'une fois sa
        saisie terminée") — aucune popup à aucun moment, Vérifier reste
        désactivé jusqu'à ce que les DEUX champs soient complets."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)
        with patch("main.messagebox.showerror") as mock_err, \
                patch("main.messagebox.showinfo") as mock_info:
            partial = ""
            for ch in "01/01/2026":
                partial += ch
                dlg.date_from_var.set(partial)
                _pump_until_scan_idle(dlg)
                if partial != "01/01/2026":
                    self.assertEqual(dlg._date_status, "incomplete")
                    self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")
        mock_err.assert_not_called()
        mock_info.assert_not_called()
        self.assertEqual(dlg._date_status, "ok")
        self.assertEqual(len(dlg._purge_paths), 1)
        self.assertEqual(str(dlg.verify_btn.cget("state")), "normal")

    def test_message_non_modal_disparait_une_fois_les_deux_dates_valides(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertNotEqual(dlg.date_status_lbl.cget("text"), "")
        dlg.date_from_var.set("01/01/2026")
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg.date_status_lbl.cget("text"), "")

    def test_verifier_refuse_avec_message_explicite_quand_les_dates_manquent(self):
        """"Au moment de lancer une action" (demande explicite) : même
        appelé directement (garde-fou défensif, le bouton est déjà
        désactivé dans ce cas), _on_verify_backup doit refuser avec un
        message explicite dédié aux dates, jamais confondu avec une
        recherche tronquée/annulée."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        with patch("main.messagebox.showinfo") as mock_info:
            dlg._on_verify_backup()
        mock_info.assert_called_once()
        self.assertIn("ates obligatoires", mock_info.call_args[0][0] + mock_info.call_args[0][1])
        self.assertIsNone(dlg._coverage)

    # ------------------------------------------------------------------
    # Erreur imprévue pendant la recherche (point 3)
    # ------------------------------------------------------------------

    def test_erreur_imprevue_pendant_la_recherche_ne_bloque_pas_indefiniment(self):
        """Défaut identifié le 2026-10-08 : _run_scan_worker n'avait
        aucun try/except — une exception imprévue laissait le thread
        mourir sans jamais déposer de résultat, bloquant _poll_scan_
        queue indéfiniment sur "Recherche en cours..."."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_from_var.set("01/01/2026")
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)
        self.assertEqual(len(dlg._purge_paths), 1)  # état "normal" avant l'erreur

        with patch.object(
            data_purge, "scan_candidate_folders_for_purge",
            side_effect=RuntimeError("panne disque simulée"),
        ):
            dlg._start_scan()
            _pump_until_scan_idle(dlg, timeout=10)

        self.assertFalse(dlg._scan_in_progress, "jamais bloqué indéfiniment sur 'Recherche en cours...'")
        self.assertEqual(dlg._purge_paths, [], "toute sélection doit être vidée")
        self.assertTrue(dlg._scan_incomplete)
        self.assertIsNotNone(dlg._scan_error)
        self.assertIn("panne disque simulée", dlg.scan_status_lbl.cget("text"))
        self.assertEqual(str(dlg.verify_btn.cget("state")), "disabled")
        self.assertEqual(str(dlg.cancel_scan_btn.winfo_manager()), "")

    def test_aucune_purge_possible_apres_une_erreur_imprevue(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_from_var.set("01/01/2026")
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)

        with patch.object(
            data_purge, "scan_candidate_folders_for_purge",
            side_effect=ValueError("corruption inattendue"),
        ):
            dlg._start_scan()
            _pump_until_scan_idle(dlg, timeout=10)

        with patch("main.messagebox.showerror") as mock_err:
            dlg._on_purge_clicked()
        mock_err.assert_called_once()
        self.assertIn("incomplète", mock_err.call_args[0][0] + mock_err.call_args[0][1])

    def test_le_thread_de_recherche_passe_bien_require_date_range_true(self):
        """Garde-fou STRUCTUREL : le seul chemin d'exécution réel
        (_run_scan_worker) doit toujours activer require_date_range,
        quels que soient les critères — vérifié en inspectant l'appel
        réel à data_purge.scan_candidate_folders_for_purge plutôt qu'en
        supposant que _parse_dates/_start_scan suffisent."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        with patch.object(
            data_purge, "scan_candidate_folders_for_purge",
            wraps=data_purge.scan_candidate_folders_for_purge,
        ) as mock_scan:
            dlg.date_from_var.set("01/01/2026")
            dlg.date_to_var.set("31/12/2026")
            _pump_until_scan_idle(dlg)
        self.assertTrue(mock_scan.called)
        self.assertTrue(mock_scan.call_args.kwargs.get("require_date_range"))

    # ------------------------------------------------------------------
    # Clignotement jaune vif/gras du message (demande explicite du
    # 2026-10-08, "améliorer la visibilité du message d'avertissement")
    # ------------------------------------------------------------------

    def test_message_clignote_en_jaune_vif_et_gras_quand_les_dates_sont_vides(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(dlg._date_status, "missing")
        self.assertEqual(str(dlg.date_status_lbl.cget("foreground")), main.ALERT_YELLOW)
        self.assertIn("bold", str(dlg.date_status_lbl.cget("font")).lower())
        self.assertIsNotNone(dlg._blink_after_id, "un rappel after() doit être programmé")

    def test_clignotement_alterne_jaune_vif_et_couleur_de_fond_toutes_les_500ms(self):
        """_blink_tick n'est jamais déclenchée par une vraie boucle
        d'événements en test (aucun mainloop qui dispatche self.after())
        — on l'appelle directement, exactement comme _poll_scan_queue
        via _pump_until_scan_idle, pour vérifier l'alternance elle-même
        sans dépendre d'un minutage réel."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertEqual(str(dlg.date_status_lbl.cget("foreground")), main.ALERT_YELLOW)
        first_after_id = dlg._blink_after_id
        dlg.after_cancel(first_after_id)
        dlg._blink_after_id = None

        dlg._blink_tick()
        self.assertEqual(str(dlg.date_status_lbl.cget("foreground")), main.FELT, "doit se fondre dans le fond")
        self.assertIsNotNone(dlg._blink_after_id, "le prochain tick doit être reprogrammé")
        second_after_id = dlg._blink_after_id
        dlg.after_cancel(second_after_id)
        dlg._blink_after_id = None

        dlg._blink_tick()
        self.assertEqual(str(dlg.date_status_lbl.cget("foreground")), main.ALERT_YELLOW)
        dlg.after_cancel(dlg._blink_after_id)
        dlg._blink_after_id = None

    def test_clignotement_sarrete_immediatement_quand_les_dates_deviennent_valides(self):
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertIsNotNone(dlg._blink_after_id)
        dlg.date_from_var.set("01/01/2026")
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._date_status, "ok")
        self.assertIsNone(dlg._blink_after_id, "le clignotement doit être arrêté, pas seulement figé")
        self.assertEqual(str(dlg.date_status_lbl.cget("foreground")), main.MUTED)
        self.assertNotIn("bold", str(dlg.date_status_lbl.cget("font")).lower())
        self.assertEqual(dlg.date_status_lbl.cget("text"), "")

    def test_clignotement_reste_actif_pour_chacun_des_quatre_statuts_bloquants(self):
        """"Conserver ce comportement tant qu'une date manque, est
        incomplète ou invalide, ou que la période est inversée" —
        vérifie les 4 statuts explicitement, pas seulement "missing"."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        dlg.date_to_var.set("31/12/2026")
        _pump_until_scan_idle(dlg)

        dlg.date_from_var.set("0")  # "incomplete"
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._date_status, "incomplete")
        self.assertIsNotNone(dlg._blink_after_id)
        self.assertEqual(str(dlg.date_status_lbl.cget("foreground")), main.ALERT_YELLOW)

        dlg.date_from_var.set("31/02/2026")  # "invalid"
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._date_status, "invalid")
        self.assertIsNotNone(dlg._blink_after_id)

        dlg.date_from_var.set("")  # "missing"
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._date_status, "missing")
        self.assertIsNotNone(dlg._blink_after_id)

        dlg.date_from_var.set("01/06/2027")  # "inverted" (postérieur à Au)
        _pump_until_scan_idle(dlg)
        self.assertEqual(dlg._date_status, "inverted")
        self.assertIsNotNone(dlg._blink_after_id)

    def test_aucun_autre_element_de_la_fenetre_ne_clignote(self):
        """"Ne pas faire clignoter les autres éléments" — count_lbl et
        scan_status_lbl ne doivent jamais recevoir ALERT_YELLOW."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertNotEqual(str(dlg.count_lbl.cget("foreground")), main.ALERT_YELLOW)
        self.assertNotEqual(str(dlg.scan_status_lbl.cget("foreground")), main.ALERT_YELLOW)
        self.assertNotEqual(str(dlg.other_files_lbl.cget("foreground")), main.ALERT_YELLOW)

    def test_fermeture_du_dialogue_annule_le_rappel_after_de_clignotement(self):
        """"Arrêter proprement les rappels Tkinter after() à la
        fermeture" — même discipline que self._poll_after_id déjà
        vérifiée ailleurs (voir BackgroundScanDialogTest)."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        self.assertIsNotNone(dlg._blink_after_id)
        dlg._close_dialog()
        self.assertIsNone(dlg._blink_after_id, "le rappel de clignotement doit être désarmé")

    def test_clignotement_ne_relance_jamais_un_second_rappel_en_double(self):
        """_start_date_status_blink ne doit jamais empiler un second
        self.after() tant qu'un premier est déjà programmé — sinon
        plusieurs cycles de clignotement se chevaucheraient."""
        self._make_tournoi("To010126.tournoi")
        dlg = self._make_dialog()
        first_id = dlg._blink_after_id
        dlg._update_date_status_display()  # rappelée sans faire avancer le statut
        self.assertEqual(dlg._blink_after_id, first_id, "ne doit pas reprogrammer un second after()")


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class ConfirmPurgeModalSafetyTest(unittest.TestCase):
    """_confirm_purge (confirmation finale avant suppression) — risque
    de SIGSEGV identifié le 2026-10-08 : cette fenêtre posait son propre
    grab_set() SANS que DataPurgeDialog ne cède d'abord le sien (même
    défaut structurel que l'ancien bug du calendrier, corrigé à son
    tour via _show_child_modal_window/_begin_child_modal).

    IMPORTANT sur la méthode de test : AUCUN test ici ne déclenche un
    clic via self.after PENDANT un vrai wait_window() — cette
    combinaison précise a provoqué un VRAI SIGSEGV ailleurs dans ce
    chantier (voir BackgroundScanDialogTest.test_ajout_manuel_recursif_
    est_un_choix_explicite_jamais_par_defaut, qui a dû être réécrit pour
    l'éviter). On utilise systématiquement _build_confirm_purge_dialog
    (construction seule) + _begin_child_modal (transfert de grab seul,
    sans wait_window) pour mettre en place EXACTEMENT le même état
    qu'une confirmation réellement affichée, puis on interagit via
    .invoke() — jamais via wait_window réel."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_confirm_modal_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)
        self.usb = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.usb)

        self._patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=self.tournament_folder),
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.poker_data_dir),
            patch.object(backup_history, "_history_dir", return_value=self.poker_data_dir),
            patch.object(export_prefs, "load_value", return_value=None),
            patch.object(open_windows, "list_open_paths", return_value=[]),
            patch.object(action_log, "list_tournaments", return_value=[]),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _make_dialog(self):
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        _pump_until_scan_idle(dlg)
        _set_valid_purge_dates(dlg)
        p1 = _new_tournoi(self.tournament_folder, "To010126.tournoi", "2026-01-01")
        dlg._purge_paths = [p1]
        return dlg

    def _confirm_btn(self, win, text):
        return next(b for b in _all_buttons(win) if b.cget("text") == text)

    # ------------------------------------------------------------------
    # 1. Ouverture et fermeture normale (Purger)
    # ------------------------------------------------------------------

    def test_ouverture_et_fermeture_normale_purger_confirme(self):
        dlg = self._make_dialog()
        self.assertEqual(str(dlg.grab_current()), str(dlg))
        win, result = dlg._build_confirm_purge_dialog()
        dlg._begin_child_modal(win)
        self.assertEqual(
            str(dlg.grab_current()), str(win),
            "la confirmation doit détenir SEULE le grab pendant qu'elle est ouverte",
        )
        self._confirm_btn(win, "Purger").invoke()
        self.assertTrue(result["confirmed"])
        self.assertFalse(win.winfo_exists())
        self.assertEqual(str(dlg.grab_current()), str(dlg), "le grab doit être restauré à DataPurgeDialog")

    def test_confirm_purge_delegue_a_show_child_modal_window_jamais_son_propre_grab(self):
        """Garde-fou STRUCTUREL contre une régression future : la VRAIE
        _confirm_purge() (pas ses deux moitiés testées séparément
        ci-dessus) doit déléguer à _show_child_modal_window, jamais
        poser elle-même grab_set()/wait_window() directement sur sa
        fenêtre. _show_child_modal_window est mockée ici (ne bloque
        donc jamais) — si _confirm_purge revenait à l'ancien défaut
        (grab_set()/wait_window() directs), ce mock ne serait jamais
        appelé et ce test se bloquerait sur un wait_window() réel sans
        aucun clic, détectable immédiatement par un délai de test
        anormal."""
        dlg = self._make_dialog()
        with patch.object(main.DataPurgeDialog, "_show_child_modal_window") as mock_modal:
            dlg._confirm_purge()
        mock_modal.assert_called_once()
        called_win = mock_modal.call_args[0][0]
        self.assertIsInstance(called_win, tk.Toplevel)
        # Le mock empêche tout transfert de grab : si _confirm_purge
        # n'avait PAS délégué (ancien défaut : grab_set() direct sur sa
        # fenêtre avant même d'atteindre _show_child_modal_window), le
        # grab aurait déjà changé de main ICI — il doit rester sur
        # DataPurgeDialog, inchangé.
        self.assertEqual(str(dlg.grab_current()), str(dlg))
        called_win.destroy()

    # ------------------------------------------------------------------
    # 2. Annuler et croix macOS
    # ------------------------------------------------------------------

    def test_annuler_ne_confirme_pas_et_restaure_le_grab(self):
        dlg = self._make_dialog()
        win, result = dlg._build_confirm_purge_dialog()
        dlg._begin_child_modal(win)
        self._confirm_btn(win, "Annuler").invoke()
        self.assertFalse(result["confirmed"])
        self.assertFalse(win.winfo_exists())
        self.assertEqual(str(dlg.grab_current()), str(dlg))

    def test_croix_macos_wm_delete_window_equivaut_a_annuler(self):
        dlg = self._make_dialog()
        win, result = dlg._build_confirm_purge_dialog()
        dlg._begin_child_modal(win)
        registered_cmd = win.protocol("WM_DELETE_WINDOW")
        self.assertTrue(registered_cmd, "WM_DELETE_WINDOW doit être enregistré")
        win.tk.call(registered_cmd)
        self.assertFalse(result["confirmed"])
        self.assertFalse(win.winfo_exists())
        self.assertEqual(str(dlg.grab_current()), str(dlg))

    # ------------------------------------------------------------------
    # 3. Fermeture du parent (DataPurgeDialog) avec confirmation ouverte
    # ------------------------------------------------------------------

    def test_fermeture_du_parent_avec_confirmation_ouverte_ne_bloque_pas_et_ne_plante_pas(self):
        dlg = self._make_dialog()
        win, result = dlg._build_confirm_purge_dialog()
        dlg._begin_child_modal(win)
        self.assertEqual(str(dlg.grab_current()), str(win))

        start = time.monotonic()
        dlg._close_dialog()
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.5, "_close_dialog ne doit JAMAIS bloquer, même avec une confirmation ouverte")
        self.assertFalse(dlg.winfo_exists())
        self.assertFalse(win.winfo_exists(), "la confirmation doit être détruite en cascade avec son parent")
        self.assertFalse(result["confirmed"], "fermer le parent ne doit jamais équivaloir à confirmer la purge")

    # ------------------------------------------------------------------
    # 4. + 5. Absence de double restauration / de callback vers une
    #    fenêtre détruite
    # ------------------------------------------------------------------

    def test_absence_de_double_restauration_du_grab_focus(self):
        """La cascade de destruction de la confirmation déclenche
        <Destroy> pour chacun de ses widgets enfants (label, boutons,
        frame) EN PLUS d'elle-même — grab_set()/focus_force() sur
        DataPurgeDialog ne doivent s'exécuter qu'UNE SEULE fois."""
        dlg = self._make_dialog()
        win, result = dlg._build_confirm_purge_dialog()
        dlg._begin_child_modal(win)

        grab_set_calls = []
        focus_force_calls = []
        orig_grab_set = dlg.grab_set
        orig_focus_force = dlg.focus_force
        dlg.grab_set = lambda *a, **k: (grab_set_calls.append(1), orig_grab_set(*a, **k))[-1]
        dlg.focus_force = lambda *a, **k: (focus_force_calls.append(1), orig_focus_force(*a, **k))[-1]

        self._confirm_btn(win, "Annuler").invoke()

        self.assertEqual(len(grab_set_calls), 1, "le grab doit être restauré EXACTEMENT une fois")
        self.assertEqual(len(focus_force_calls), 1, "le focus doit être restauré EXACTEMENT une fois")

    def test_aucun_callback_vers_dataPurgeDialog_apres_le_debut_de_sa_destruction(self):
        """Fermer DataPurgeDialog PENDANT que la confirmation est
        ouverte ne doit JAMAIS appeler grab_set()/focus_force() sur lui
        — exactement la séquence qui a provoqué le SIGSEGV du
        calendrier avant son propre correctif _closing."""
        dlg = self._make_dialog()
        win, result = dlg._build_confirm_purge_dialog()
        dlg._begin_child_modal(win)

        grab_set_calls = []
        focus_force_calls = []
        orig_grab_set = dlg.grab_set
        orig_focus_force = dlg.focus_force
        dlg.grab_set = lambda *a, **k: (grab_set_calls.append(1), orig_grab_set(*a, **k))[-1]
        dlg.focus_force = lambda *a, **k: (focus_force_calls.append(1), orig_focus_force(*a, **k))[-1]

        dlg._close_dialog()

        self.assertEqual(grab_set_calls, [], "grab_set() ne doit JAMAIS être appelé sur DataPurgeDialog pendant sa propre destruction")
        self.assertEqual(focus_force_calls, [], "focus_force() ne doit JAMAIS être appelé sur DataPurgeDialog pendant sa propre destruction")

    # ------------------------------------------------------------------
    # 6. Non-régression : le flux normal (via la VRAIE _confirm_purge,
    #    appelée seulement en mode "test rapide" — Purger invoqué
    #    immédiatement après construction, jamais de wait_window laissé
    #    en suspens puisque le clic a déjà eu lieu avant que le thread
    #    n'entre dans l'attente) continue de fonctionner de bout en
    #    bout, et le calendrier/la recherche en arrière-plan ne sont pas
    #    affectés par ce correctif.
    # ------------------------------------------------------------------

    def test_purge_complete_via_on_purge_clicked_fonctionne_toujours_de_bout_en_bout(self):
        """Non-régression du chemin complet : _on_purge_clicked ->
        _confirm_purge (réellement appelée, pas mockée) -> purge
        réelle. Le clic sur "Purger" est programmé via self.after,
        mais déclenché depuis le MÊME call stack qu'un clic réel
        aurait été — contrairement aux tests qui avaient crashé, le
        bouton cliqué ici appartient à DataPurgeDialog lui-même (le
        VRAI point d'entrée utilisateur), jamais un widget à l'intérieur
        d'un Toplevel enfant pendant son propre wait_window."""
        dlg = self._make_dialog()
        p1 = dlg._purge_paths[0]
        dlg._on_verify_backup()
        with patch("main.filedialog.askdirectory", return_value=self.usb), \
                patch("main.messagebox.showinfo"):
            dlg._on_backup_now()
        self.assertEqual(str(dlg.purge_btn.cget("state")), "normal")

        with patch.object(dlg, "_confirm_purge", return_value=True), \
                patch("main.messagebox.showinfo"):
            dlg._on_purge_clicked()
        self.assertFalse(os.path.isfile(p1))

    def test_calendrier_fonctionne_toujours_normalement(self):
        """Non-régression explicite : le calendrier (correctif
        _closing/grab déjà validé manuellement) n'est pas affecté par
        le correctif de _confirm_purge."""
        dlg = self._make_dialog()
        cal_buttons = [b for b in _all_buttons(dlg) if b.cget("text") == "📅"]
        self.assertEqual(len(cal_buttons), 2)
        cal_buttons[0].invoke()
        picker = _toplevel_children(dlg)[0]
        self.assertEqual(str(picker.winfo_parent()), str(dlg))
        self.assertEqual(str(dlg.grab_current()), str(picker))
        picker.destroy()
        self.assertEqual(str(dlg.grab_current()), str(dlg))


@unittest.skipUnless(_TK_AVAILABLE, "Pas d'affichage Tk disponible dans cet environnement")
class DataPurgeDialogParentWindowTest(unittest.TestCase):
    """DataPurgeDialog.__init__(master, parent_window=None) — demande du
    2026-10-12 ("🗑️ Purger les données" au Menu principal) : `master`
    reste l'objet dont les méthodes (_show_log_date_picker, _run_backup_
    flow) sont réutilisées via self.app, tandis que `parent_window`,
    SÉPARÉ, devient la fenêtre Tk réelle (super().__init__/transient).

    Historique (onglet LOG, DataPurgeDialog(self.root) SANS parent_
    window) : `master` sert aux DEUX rôles, comme avant — vérifié ici
    NON-RÉGRESSÉ. Nouveau (Menu principal) : `master` peut être une
    fenêtre racine withdraw() (comme App.__init__ à ce stade du
    démarrage) tant que parent_window désigne une fenêtre RÉELLEMENT
    visible — vérifié ici que c'est bien CETTE fenêtre-là, jamais
    `master`, qui devient la cible transient()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_parent_window_test_")
        self.addCleanup(self._tmp.cleanup)
        self._patchers = [
            patch.object(backup_restore, "default_tournament_folder", return_value=self._tmp.name),
            patch.object(backup_restore, "default_poker_data_dir", return_value=self._tmp.name),
            patch.object(export_prefs, "load_value", return_value=None),
            patch.object(open_windows, "list_open_paths", return_value=[]),
            patch.object(action_log, "list_tournaments", return_value=[]),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.root = tk.Tk()
        self.root.withdraw()
        for name in ("_show_log_date_picker", "_parse_log_date_field", "_run_backup_flow"):
            setattr(self.root, name, types.MethodType(getattr(main.App, name), self.root))
        self.visible_win = tk.Toplevel(self.root)
        self.addCleanup(lambda: cleanup_tk(self, "root", "visible_win"))

    def test_sans_parent_window_transient_vers_master_comme_avant(self):
        """Non-régression : appel historique (onglet LOG) INCHANGÉ."""
        dlg = main.DataPurgeDialog(self.root)
        self.addCleanup(dlg.destroy)
        self.assertEqual(dlg.tk.call("wm", "transient", dlg._w), str(self.root))
        self.assertIs(dlg.app, self.root)

    def test_avec_parent_window_transient_vers_parent_window_pas_master(self):
        """Nouveau chemin (Menu principal) : transient() cible bien
        parent_window, jamais master — même si master est withdraw()."""
        dlg = main.DataPurgeDialog(self.root, parent_window=self.visible_win)
        self.addCleanup(dlg.destroy)
        self.assertEqual(dlg.tk.call("wm", "transient", dlg._w), str(self.visible_win))
        self.assertNotEqual(dlg.tk.call("wm", "transient", dlg._w), str(self.root))

    def test_avec_parent_window_self_app_reste_master(self):
        """self.app reste `master` (jamais parent_window) : les méthodes
        déléguées (_show_log_date_picker/_run_backup_flow) continuent de
        fonctionner à l'identique, quel que soit parent_window."""
        dlg = main.DataPurgeDialog(self.root, parent_window=self.visible_win)
        self.addCleanup(dlg.destroy)
        self.assertIs(dlg.app, self.root)
        self.assertIsNot(dlg.app, self.visible_win)


if __name__ == "__main__":
    unittest.main()
