# -*- coding: utf-8 -*-
"""Tests de BackupProgressDialog/_run_blocking_with_progress (main.py) —
demande explicite du 2026-10-12, "barres de progression RÉELLES" pour
les sauvegardes/restaurations du Menu principal ET de Purge Données
(qui partagent toutes deux App._run_backup_flow), jamais une simulation
ni une barre indéterminée, et jamais 100% annoncé avant la fin RÉELLE
de l'opération.

Utilise une vraie racine Tk (voir tests/_tk_cleanup.py, convention déjà
établie par le reste de la suite) : wait_window() fait réellement
tourner la boucle d'événements Tk pendant le polling de la queue
alimentée par le thread d'arrière-plan — un mock de Tk ne testerait
rien de ce comportement réel."""
import os
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backup_restore  # noqa: E402
import main  # noqa: E402

from _tk_cleanup import cleanup_tk  # noqa: E402


def _write(path, content="x" * 50):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class BackupProgressDialogUnitTest(unittest.TestCase):
    """Vérifie handle_event directement, SANS thread ni
    _run_blocking_with_progress — isole la règle centrale demandée
    explicitement : jamais 100% avant l'événement sentinel ("done",)."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.dialog = None
        self.addCleanup(lambda: cleanup_tk(self, "root", "dialog"))

    def _make_dialog(self):
        self.dialog = main.BackupProgressDialog(self.root, "Test")
        return self.dialog

    def test_progression_plafonnee_a_99_tant_que_done_non_recu(self):
        dlg = self._make_dialog()
        dlg.handle_event(("progress", "backup", "tournament_files", 1, 3, "a.tournoi"))
        self.assertEqual(dlg.progress["value"], 33)
        # Dernier fichier d'une étape : jamais 100%, même quand
        # current == total, tant que "done" n'est pas reçu — une autre
        # étape peut suivre (ex. poker_data après tournament_files), ou
        # l'opération peut encore échouer juste après ce fichier.
        dlg.handle_event(("progress", "backup", "tournament_files", 3, 3, "c.tournoi"))
        self.assertEqual(dlg.progress["value"], 99)
        self.assertTrue(dlg.winfo_exists())

    def test_done_force_100_et_detruit_la_fenetre(self):
        dlg = self._make_dialog()
        dlg.handle_event(("progress", "backup", "poker_data", 2, 2, "data.json"))
        dlg.handle_event(("done",))
        self.assertFalse(dlg.winfo_exists())

    def test_libelles_connus_affiches_pour_chaque_stage_prefix(self):
        dlg = self._make_dialog()
        dlg.handle_event(("progress", "safety_backup", "tournament_files", 1, 1, "x.tournoi"))
        self.assertIn("Sauvegarde de sécurité", dlg.stage_var.get())
        dlg.handle_event(("progress", "restore", "poker_data", 1, 1, "y.json"))
        self.assertIn("Restauration", dlg.stage_var.get())

    def test_fermeture_native_est_desactivee(self):
        """WM_DELETE_WINDOW (croix native) ne doit jamais fermer cette
        fenêtre elle-même — seul l'événement "done" (voir handle_event)
        le fait, pour ne jamais laisser l'utilisateur interrompre une
        copie en cours au milieu."""
        dlg = self._make_dialog()
        cmd = dlg.protocol("WM_DELETE_WINDOW")
        self.assertTrue(cmd)
        dlg.tk.call(cmd)
        self.assertTrue(dlg.winfo_exists())


class RunBlockingWithProgressIntegrationTest(unittest.TestCase):
    """Teste _run_blocking_with_progress avec le VRAI backup_restore.
    create_backup (dossiers synthétiques, jamais ~/.poker_tournament) :
    vérifie que le résultat renvoyé est identique à un appel direct,
    que des événements de progression RÉELS sont bien reçus pour
    chaque fichier, que la fenêtre se détruit à la fin, et que le grab
    de la fenêtre "parent" (déjà actif, comme dans les deux vrais
    points d'appel : la fenêtre "Bienvenue" et DataPurgeDialog) est
    restauré après — même mécanisme que App._begin_child_modal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="backup_progress_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        self.dest = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.dest)
        for i in range(5):
            _write(os.path.join(self.tournament_folder, f"To{i}.tournoi"))
        for i in range(6):
            _write(os.path.join(self.poker_data_dir, f"data{i}.json"))

        self.root = tk.Tk()
        self.root.withdraw()
        # Simule l'état réel des deux appelants (la fenêtre "Bienvenue",
        # ou DataPurgeDialog) : une fenêtre PARENT qui détient déjà son
        # propre grab au moment de l'appel.
        self.parent = tk.Toplevel(self.root)
        self.parent.grab_set()
        self.addCleanup(lambda: cleanup_tk(self, "root", "parent"))

    def test_resultat_identique_a_un_appel_direct_et_fenetre_detruite(self):
        events = []
        original_handle_event = main.BackupProgressDialog.handle_event

        def _recording_handle_event(self_dialog, event):
            events.append(event)
            original_handle_event(self_dialog, event)

        with patch.object(main.BackupProgressDialog, "handle_event", _recording_handle_event):
            result = main._run_blocking_with_progress(
                self.parent, "Sauvegarde en cours…",
                lambda progress_callback: backup_restore.create_backup(
                    self.dest, tournament_folder=self.tournament_folder,
                    poker_data_dir=self.poker_data_dir, progress_callback=progress_callback,
                ),
            )

        self.assertEqual(result["tournament_file_count"], 5)
        self.assertTrue(result["poker_tournament_data_included"])
        self.assertTrue(os.path.isdir(result["backup_dir"]))

        # Un événement de progression par fichier RÉELLEMENT copié (5
        # .tournoi + 6 fichiers de données), puis le sentinel "done" en
        # tout dernier.
        progress_events = [e for e in events if e[0] == "progress"]
        self.assertEqual(len(progress_events), 5 + 6)
        self.assertEqual(events[-1], ("done",))
        tournament_events = [e for e in progress_events if e[2] == "tournament_files"]
        data_events = [e for e in progress_events if e[2] == "poker_data"]
        self.assertEqual(len(tournament_events), 5)
        self.assertEqual(len(data_events), 6)
        self.assertEqual(tournament_events[-1][3:5], (5, 5))
        self.assertEqual(data_events[-1][3:5], (6, 6))

        # Le grab de `parent` doit être restauré après la fermeture de
        # la fenêtre de progression — jamais deux grabs actifs en même
        # temps, jamais le parent laissé SANS grab une fois la
        # progression terminée.
        self.assertEqual(str(self.parent.grab_current()), str(self.parent))

    def test_exception_backuperror_est_relevee_dans_le_thread_principal(self):
        def _blocking_fn(progress_callback):
            raise backup_restore.BackupError("destination invalide")

        with self.assertRaises(backup_restore.BackupError):
            main._run_blocking_with_progress(self.parent, "Sauvegarde en cours…", _blocking_fn)
        # La fenêtre doit malgré tout avoir été correctement fermée (le
        # sentinel "done" est toujours envoyé par le thread, même en
        # cas d'échec) et le grab du parent restauré, exactement comme
        # pour le cas de succès.
        self.assertEqual(str(self.parent.grab_current()), str(self.parent))


if __name__ == "__main__":
    unittest.main()
