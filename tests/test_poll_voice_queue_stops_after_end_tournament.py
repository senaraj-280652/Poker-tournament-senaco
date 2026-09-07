"""Test ciblé du correctif de robustesse #1 (audit du 2026-09-07) :
App._poll_voice_queue vidait sa file avec un `while True`, y compris
APRÈS avoir traité ("end_tournament",) — qui détruit la fenêtre
(_remote_end_tournament -> _on_close -> self.destroy()). Si un second
élément se trouvait déjà dans la file (ex : un tap "Chronomètre" arrivé
presque en même temps que "Fin de la partie"), il était traité juste
après sur une fenêtre déjà détruite / une base déjà fermée — self.db
n'est jamais remis à None par _cleanup_for_close, donc les gardes
"if not self.db: return" des autres gestionnaires ne l'arrêtaient pas,
et l'appel suivant à self.db.get_setting_int(...) levait
sqlite3.ProgrammingError (exception réelle, capturée par le
report_callback_exception de l'appli — pas de plantage visible, mais un
vrai trou de robustesse et un traceback confus dans crash.log).

Doublure légère (voir tests/test_voice_word_bb_rebalance.py pour le même
principe) : App._poll_voice_queue est une méthode non liée, appelée
directement avec une doublure comme `self` — pas besoin de construire
une App complète pour tester cette seule boucle."""
import os
import queue
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeApp:
    """Doublure de App : ne reprend que ce que _poll_voice_queue lit ou
    appelle. `calls` journalise, dans l'ordre, chaque gestionnaire
    réellement invoqué."""

    def __init__(self, items):
        self.voice_command_queue = queue.Queue()
        for item in items:
            self.voice_command_queue.put(item)
        self.calls = []
        self.after_calls = 0

    def winfo_exists(self):
        return True

    def after(self, delay, callback):
        self.after_calls += 1
        return "fake_after_id"

    def _remote_end_tournament(self):
        self.calls.append(("end_tournament",))

    def _remote_eliminate(self, eliminated_id, eliminator_id):
        self.calls.append(("eliminate", eliminated_id, eliminator_id))

    def _resolve_pending_rebalance(self, request_id, seat, from_remote):
        self.calls.append(("rebalance_answer", request_id, seat, from_remote))

    def _on_voice_word(self, word):
        self.calls.append(("voice_word", word))

    def _poll_voice_queue(self):
        # Jamais réellement invoqué (self.after ci-dessus est une
        # doublure qui se contente d'enregistrer l'appel) — juste
        # nécessaire pour que `self._poll_voice_queue` soit un attribut
        # valide à passer à self.after(150, self._poll_voice_queue).
        pass


class PollVoiceQueueStopsAfterEndTournamentTest(unittest.TestCase):
    def test_element_suivant_deja_dans_la_file_nest_jamais_traite(self):
        """Scénario exact du risque : ("end_tournament",) PUIS un second
        élément déjà présent dans la file au moment de cet appel."""
        fake = _FakeApp([("end_tournament",), "chronometre"])

        main.App._poll_voice_queue(fake)

        self.assertEqual(fake.calls, [("end_tournament",)])
        # Le second élément n'a même pas été retiré de la file (jamais
        # consommé par get_nowait(), donc toujours là si on le relit).
        self.assertEqual(fake.voice_command_queue.qsize(), 1)
        self.assertEqual(fake.voice_command_queue.get_nowait(), "chronometre")

    def test_plusieurs_elements_apres_end_tournament_aucun_nest_traite(self):
        fake = _FakeApp([("end_tournament",), ("eliminate", 1, 2), "terminer"])

        main.App._poll_voice_queue(fake)

        self.assertEqual(fake.calls, [("end_tournament",)])
        self.assertEqual(fake.voice_command_queue.qsize(), 2)

    def test_ne_reprogramme_pas_de_nouveau_passage(self):
        """self.after(150, self._poll_voice_queue) ne doit plus être
        appelé une fois qu'on sait que la fenêtre se ferme : inutile
        (le process se termine de toute façon), et évite tout risque
        résiduel si jamais il finissait par se redéclencher."""
        fake = _FakeApp([("end_tournament",)])

        main.App._poll_voice_queue(fake)

        self.assertEqual(fake.after_calls, 0)

    def test_comportement_normal_sans_end_tournament_inchange(self):
        """Non-régression : sans "end_tournament" dans la file, tous les
        éléments restent traités dans l'ordre, et un nouveau passage
        reste programmé — comportement visible normal non modifié."""
        fake = _FakeApp(["chronometre", ("eliminate", 1, 2), ("rebalance_answer", "r1", 3)])

        main.App._poll_voice_queue(fake)

        self.assertEqual(
            fake.calls,
            [
                ("voice_word", "chronometre"),
                ("eliminate", 1, 2),
                ("rebalance_answer", "r1", 3, True),
            ],
        )
        self.assertEqual(fake.voice_command_queue.qsize(), 0)
        self.assertEqual(fake.after_calls, 1)

    def test_end_tournament_seul_dans_la_file_reste_traite_normalement(self):
        fake = _FakeApp([("end_tournament",)])

        main.App._poll_voice_queue(fake)

        self.assertEqual(fake.calls, [("end_tournament",)])


if __name__ == "__main__":
    unittest.main()
