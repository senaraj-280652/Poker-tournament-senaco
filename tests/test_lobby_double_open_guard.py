"""Test ciblé du correctif de robustesse #3 (audit du 2026-09-07) :
LobbyDialog._open_selected pouvait lancer DEUX process sur le même
fichier .tournoi si l'utilisateur cliquait deux fois rapidement sur
"🔀 Basculer vers" (ou double-cliquait deux fois de suite) pour un
tournoi pas encore ouvert : chaque clic voyait `open_windows.
find_open_pid(path)` renvoyer None tant que le process tout juste lancé
par le clic précédent n'avait pas eu le temps de s'enregistrer (voir
open_windows.register, appelé depuis App.__init__) — rien n'empêchait
alors un second `spawn_app_process([path])` sur EXACTEMENT le même
chemin, soit deux fenêtres qui écrivent en même temps dans le même
fichier SQLite (le risque explicitement mentionné dans la tooltip de ce
bouton).

Correctif : une garde purement logique (`self._launching_paths`, un
set de chemins en cours de lancement DEPUIS CETTE fenêtre) — pas de
délai arbitraire. Levée dès que le lancement est résolu : soit le
process s'est enregistré (une tentative suivante le retrouve alors
normalement via existing_pid et bascule vers lui), soit il est mort
entre-temps (une nouvelle tentative doit pouvoir relancer).

Doublure légère (même principe que tests/test_poll_voice_queue_stops_
after_end_tournament.py) : LobbyDialog._open_selected et _clear_launch_
guard_when_resolved sont des méthodes non liées, appelées directement
avec une doublure comme `self` — pas besoin de construire une vraie
fenêtre Tk."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeProc:
    def __init__(self, pid=12345, alive=True):
        self.pid = pid
        self._alive = alive

    def poll(self):
        return None if self._alive else 1


class _FakeMaster:
    """Doublure de App (voir LobbyDialog.__init__ : self.master, posé
    automatiquement par Tk) — juste assez pour own_path = getattr(self.
    master, "db", None) and self.master.db.path dans _open_selected."""

    db = None


class _FakeLobby:
    """Doublure de LobbyDialog : ne reprend que ce que _open_selected et
    _clear_launch_guard_when_resolved lisent ou appellent."""

    def __init__(self, path):
        self._path = path
        self._launching_paths = set()
        self.after_calls = []
        self.master = _FakeMaster()

    def _selected_path(self):
        return self._path

    def winfo_exists(self):
        return True

    def after(self, delay, callback):
        # Ne s'exécute jamais tout seul ici (pas de vraie boucle Tk) :
        # les tests qui veulent simuler l'écoulement du temps rappellent
        # explicitement le callback capturé.
        self.after_calls.append((delay, callback))
        return "fake_after_id"

    def _clear_launch_guard_when_resolved(self, path, proc, attempt=0):
        # _open_selected appelle self._clear_launch_guard_when_resolved(...)
        # (méthode liée) : redirige vers la vraie implémentation non liée,
        # avec cette même doublure comme `self`.
        main.LobbyDialog._clear_launch_guard_when_resolved(self, path, proc, attempt)


class LobbyDoubleOpenGuardTest(unittest.TestCase):
    def setUp(self):
        # "Un seul tournoi à la fois" (préférence ajoutée le 2026-09-07,
        # postérieure à ce correctif) : ces tests portent spécifiquement
        # sur la garde anti-double-clic, pas sur cette préférence (voir
        # tests/test_single_tournament_at_a_time.py pour elle) — jamais
        # bloquant ici, et jamais d'accès aux vrais fichiers réels
        # (export_prefs.json/open_windows.json) qu'un appel non mocké de
        # _block_second_tournament_if_needed provoquerait sinon.
        patcher = patch.object(main, "_block_second_tournament_if_needed", return_value=False)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_deux_clics_rapides_sur_un_tournoi_pas_encore_ouvert_ne_lancent_qu_un_seul_process(self):
        fake = _FakeLobby("/tmp/exemple.tournoi")
        proc = _FakeProc()
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "spawn_app_process", return_value=proc) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring:
            main.LobbyDialog._open_selected(fake)
            main.LobbyDialog._open_selected(fake)  # deuxième clic, avant tout enregistrement

        mock_spawn.assert_called_once_with(["/tmp/exemple.tournoi"])
        mock_raise.assert_called_once()
        mock_bring.assert_not_called()

    def test_un_troisieme_clic_apres_enregistrement_bascule_au_lieu_de_relancer(self):
        """Une fois le tournoi réellement ouvert (find_open_pid renvoie
        enfin un pid), un nouveau clic doit basculer vers lui normalement
        — pas être bloqué par une garde qui serait restée levée à tort."""
        fake = _FakeLobby("/tmp/exemple.tournoi")
        proc = _FakeProc()
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "spawn_app_process", return_value=proc) as mock_spawn, \
             patch.object(main, "raise_process_when_ready"):
            main.LobbyDialog._open_selected(fake)

        self.assertEqual(fake._launching_paths, {"/tmp/exemple.tournoi"})

        # Le process s'enregistre enfin : simule l'appel différé qui
        # constate ça et lève la garde (voir _clear_launch_guard_when_resolved).
        with patch.object(main.open_windows, "find_open_pid", return_value=proc.pid):
            _, callback = fake.after_calls[-1]
            callback()

        self.assertEqual(fake._launching_paths, set())

        with patch.object(main.open_windows, "find_open_pid", return_value=proc.pid), \
             patch.object(main, "spawn_app_process") as mock_spawn_again, \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring:
            main.LobbyDialog._open_selected(fake)

        mock_spawn_again.assert_not_called()
        mock_bring.assert_called_once_with(proc.pid)
        self.assertEqual(mock_spawn.call_count, 1)

    def test_process_mort_entre_deux_tentatives_leve_la_garde_et_permet_de_relancer(self):
        """Si le nouveau process meurt avant de s'être enregistré (crash
        au démarrage), la garde ne doit pas rester bloquée pour
        toujours : un nouveau clic doit pouvoir relancer normalement."""
        fake = _FakeLobby("/tmp/exemple.tournoi")
        proc = _FakeProc(alive=True)
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "spawn_app_process", return_value=proc), \
             patch.object(main, "raise_process_when_ready"):
            main.LobbyDialog._open_selected(fake)

        self.assertEqual(fake._launching_paths, {"/tmp/exemple.tournoi"})

        proc._alive = False  # le process vient de mourir
        with patch.object(main.open_windows, "find_open_pid", return_value=None):
            _, callback = fake.after_calls[-1]
            callback()

        self.assertEqual(fake._launching_paths, set())

        proc2 = _FakeProc(pid=99999)
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "spawn_app_process", return_value=proc2) as mock_spawn2, \
             patch.object(main, "raise_process_when_ready"):
            main.LobbyDialog._open_selected(fake)

        mock_spawn2.assert_called_once()

    def test_filet_de_securite_apres_cinq_essais_leve_quand_meme_la_garde(self):
        """Même si find_open_pid ne renvoie jamais rien et que le process
        semble toujours vivant (cas non prévu), la garde ne doit pas
        rester bloquée indéfiniment — même cadence que
        raise_process_when_ready (5 essais)."""
        fake = _FakeLobby("/tmp/exemple.tournoi")
        proc = _FakeProc(alive=True)
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "spawn_app_process", return_value=proc), \
             patch.object(main, "raise_process_when_ready"):
            main.LobbyDialog._open_selected(fake)

        with patch.object(main.open_windows, "find_open_pid", return_value=None):
            for _ in range(5):
                _, callback = fake.after_calls[-1]
                callback()

        self.assertEqual(fake._launching_paths, set())

    def test_erreur_au_lancement_leve_immediatement_la_garde(self):
        fake = _FakeLobby("/tmp/exemple.tournoi")
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "spawn_app_process", side_effect=OSError("échec simulé")), \
             patch.object(main.messagebox, "showerror") as mock_showerror:
            main.LobbyDialog._open_selected(fake)

        mock_showerror.assert_called_once()
        self.assertEqual(fake._launching_paths, set())

    def test_comportement_normal_tournoi_deja_ouvert_inchange(self):
        """Non-régression : le cas le plus courant (tournoi déjà ouvert,
        listé depuis open_windows.list_open_paths dans _refresh) bascule
        directement, sans jamais toucher à la garde ni à spawn_app_process."""
        fake = _FakeLobby("/tmp/exemple.tournoi")
        with patch.object(main.open_windows, "find_open_pid", return_value=555), \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring, \
             patch.object(main, "spawn_app_process") as mock_spawn:
            main.LobbyDialog._open_selected(fake)

        mock_bring.assert_called_once_with(555)
        mock_spawn.assert_not_called()
        self.assertEqual(fake._launching_paths, set())


if __name__ == "__main__":
    unittest.main()
