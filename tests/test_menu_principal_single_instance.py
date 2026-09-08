"""Test ciblé de l'unicité de "Menu principal" (demande du 2026-09-07) :
un clic pendant que le Menu principal déjà lancé DEPUIS CETTE fenêtre
est encore vivant ne doit PAS créer un second process — il ramène
l'existant au premier plan à la place. Après fermeture (le process
précédent s'est terminé), un nouveau clic relance normalement.

Cause de la duplication AVANT ce correctif (voir _open_new_window) :
spawn_app_process() était appelé À CHAQUE clic, sans jamais vérifier si
un précédent lancement depuis CETTE fenêtre était toujours vivant — rien
n'était mémorisé entre deux appels.

Sur la VRAIE modalité (interaction bloquée avec la fenêtre tournoi tant
que Menu principal est ouvert) : techniquement IMPOSSIBLE avec
l'architecture actuelle — "Menu principal" est un second PROCESS
indépendant (spawn_app_process, voir sa docstring), et grab_set/
transient/wait_window de Tkinter ne s'appliquent qu'à l'intérieur d'un
même process. Correction volontairement limitée à l'UNICITÉ (choix
explicite de l'utilisateur, qui a écarté l'option "refonte en Toplevel
local" pour rester sur une modification minimale) — aucun test ci-dessous
ne prétend donc vérifier un blocage d'interaction réel, seulement
l'absence de doublon et le retour au premier plan.

Ne touche à aucun autre mécanisme : "Un seul tournoi à la fois" continue
de piloter uniquement les 3 commandes de lancement (voir
tests/test_single_tournament_at_a_time.py), jamais l'unicité de la
fenêtre elle-même — vérifié ici en non-régression."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeProc:
    def __init__(self, pid, alive=True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class _FakeApp:
    """Doublure de App : ne reprend que ce que _open_new_window lit ou
    appelle."""

    def __init__(self):
        self._menu_principal_proc = None


class MenuPrincipalSingleInstanceTest(unittest.TestCase):
    def test_premier_appel_lance_bien_un_process_et_le_memorise(self):
        fake = _FakeApp()
        proc = _FakeProc(pid=111)
        with patch.object(main, "spawn_app_process", return_value=proc) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring, \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)
        mock_spawn.assert_called_once()
        mock_raise.assert_called_once_with(fake, 111)
        mock_bring.assert_not_called()
        self.assertIs(fake._menu_principal_proc, proc)

    def test_second_appel_avant_fermeture_ne_relance_pas_un_process(self):
        """Coeur de la demande : tant que le précédent est encore vivant
        (poll() -> None), un second appel ne doit JAMAIS appeler
        spawn_app_process une deuxième fois."""
        fake = _FakeApp()
        first_proc = _FakeProc(pid=111, alive=True)
        fake._menu_principal_proc = first_proc  # déjà lancé par un appel précédent

        with patch.object(main, "spawn_app_process") as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring:
            main.App._open_new_window(fake)

        mock_spawn.assert_not_called()
        mock_raise.assert_not_called()

    def test_second_appel_ramene_la_fenetre_existante_au_premier_plan(self):
        fake = _FakeApp()
        first_proc = _FakeProc(pid=222, alive=True)
        fake._menu_principal_proc = first_proc

        with patch.object(main, "spawn_app_process") as mock_spawn, \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring:
            main.App._open_new_window(fake)

        mock_bring.assert_called_once_with(222)
        mock_spawn.assert_not_called()
        # La référence mémorisée reste celle du process déjà vivant, pas
        # remplacée par un lancement qui n'a pas eu lieu.
        self.assertIs(fake._menu_principal_proc, first_proc)

    def test_plusieurs_clics_rapproches_ne_creent_toujours_qu_un_seul_process(self):
        """Trois clics d'affilée pendant que le premier reste vivant :
        un seul spawn_app_process au total."""
        fake = _FakeApp()
        proc = _FakeProc(pid=333, alive=True)
        with patch.object(main, "spawn_app_process", return_value=proc) as mock_spawn, \
             patch.object(main, "raise_process_when_ready"), \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring, \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)  # 1er clic : lance réellement
            main.App._open_new_window(fake)  # 2e clic : déjà vivant
            main.App._open_new_window(fake)  # 3e clic : toujours vivant

        mock_spawn.assert_called_once()
        self.assertEqual(mock_bring.call_count, 2)

    def test_apres_fermeture_un_nouvel_appel_relance_normalement(self):
        """Le process précédent s'est terminé (poll() -> un code de
        retour) : un nouveau clic doit pouvoir relancer Menu principal
        normalement, pas rester bloqué indéfiniment."""
        fake = _FakeApp()
        old_proc = _FakeProc(pid=444, alive=False)  # déjà fermé
        fake._menu_principal_proc = old_proc

        new_proc = _FakeProc(pid=555, alive=True)
        with patch.object(main, "spawn_app_process", return_value=new_proc) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring, \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)

        mock_spawn.assert_called_once()
        mock_bring.assert_not_called()
        mock_raise.assert_called_once_with(fake, 555)
        self.assertIs(fake._menu_principal_proc, new_proc)

    def test_erreur_de_lancement_ne_memorise_rien(self):
        """Un OSError au spawn (voir le try/except existant, inchangé)
        ne doit pas laisser une référence à un process qui n'existe pas
        — un nouveau clic doit pouvoir retenter normalement."""
        fake = _FakeApp()
        with patch.object(main, "spawn_app_process", side_effect=OSError("échec simulé")), \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._open_new_window(fake)
        mock_error.assert_called_once()
        self.assertIsNone(fake._menu_principal_proc)

    def test_process_mort_immediatement_ne_memorise_rien(self):
        """Le process se termine tout de suite après son lancement (voir
        le check existant returncode is not None) : ne doit pas non plus
        être mémorisé comme "vivant" — sans quoi un nouveau clic resterait
        bloqué à tort sur un process déjà mort."""
        fake = _FakeApp()
        dead_proc = _FakeProc(pid=666, alive=False)
        with patch.object(main, "spawn_app_process", return_value=dead_proc), \
             patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.time, "sleep"), \
             patch.object(main, "raise_process_when_ready") as mock_raise:
            main.App._open_new_window(fake)
        mock_error.assert_called_once()
        mock_raise.assert_not_called()
        self.assertIsNone(fake._menu_principal_proc)


class NoModalityClaimedTest(unittest.TestCase):
    """Documente explicitement la limite technique (choix assumé de
    l'utilisateur) : rien dans _open_new_window ne prétend bloquer
    l'interaction avec la fenêtre tournoi — seule l'unicité est garantie."""

    def test_open_new_window_ne_touche_a_aucun_mecanisme_de_grab(self):
        import ast

        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=main_py)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_open_new_window"
        )
        called_names = {
            n.func.attr for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        self.assertNotIn("grab_set", called_names)
        self.assertNotIn("transient", called_names)
        self.assertNotIn("wait_window", called_names)


class PreferenceUnaffectedTest(unittest.TestCase):
    """Non-régression explicite : ce correctif d'unicité ne touche pas à
    "Un seul tournoi à la fois" (le garde backend et le grisage des 3
    commandes restent testés séparément dans
    tests/test_single_tournament_at_a_time.py — juste revérifié ici que
    _open_new_window ne les appelle toujours pas)."""

    def test_open_new_window_n_appelle_toujours_pas_le_garde_backend(self):
        import ast

        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=main_py)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_open_new_window"
        )
        calls_block = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_block_second_tournament_if_needed"
            for n in ast.walk(func)
        )
        self.assertFalse(calls_block)

    def test_lancement_reussit_toujours_meme_option_activee_et_tournoi_ouvert(self):
        fake = _FakeApp()
        proc = _FakeProc(pid=777, alive=True)
        with patch.object(main, "_block_second_tournament_if_needed") as mock_block, \
             patch.object(main.export_prefs, "load_value", return_value=True), \
             patch.object(main.open_windows, "list_open_paths", return_value=["/tmp/moi.tournoi"]), \
             patch.object(main, "spawn_app_process", return_value=proc) as mock_spawn, \
             patch.object(main, "raise_process_when_ready"), \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)
        mock_block.assert_not_called()
        mock_spawn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
