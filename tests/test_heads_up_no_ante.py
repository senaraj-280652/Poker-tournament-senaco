# -*- coding: utf-8 -*-
"""Heads-Up sans ante (demande du 2026-10-03, "IMPRESSION JOUEURS /
PLACES FIGÉES / PSEUDO / HU SANS ANTE", point 4) : exactement 2 joueurs
actifs -> l'ante affiché/utilisé "en direct" passe à 0, SANS modifier la
structure de blindes enregistrée (voir Database.get_blind_structure, qui
continue d'afficher/éditer la vraie valeur).

Implémentation (voir Database.get_current_level, database.py) : un
SEUL point de passage pour tout affichage en direct (Chronomètre Mac,
écran projecteur, fenêtre Lobby), qui force `level["ante"]` à 0 dans le
dict RENVOYÉ uniquement — jamais une écriture en base. Le nombre de
joueurs actifs est toujours recalculé en direct (jamais mis en cache),
donc un retour à 3+ joueurs (annulation d'une élimination, réinscription)
restaure l'ante normal de lui-même, sans code supplémentaire.

Couvre :
1. Ante forcé à 0 exactement à 2 joueurs actifs (niveau normal).
2. Ante normal (valeur enregistrée) à 1 ou 3+ joueurs actifs.
3. Un niveau de pause (is_break) n'est jamais affecté, même à 2 joueurs.
4. get_blind_structure() ne montre JAMAIS la substitution : toujours la
   vraie valeur enregistrée, y compris à 2 joueurs actifs.
5. La structure enregistrée en base n'est elle-même jamais modifiée par
   un appel à get_current_level (lecture seule, aucun UPDATE/commit).
6. Le retour à 3+ joueurs restaure l'ante normal automatiquement (ex. via
   reinstate_player ou un ajout de joueur), sans plomberie dédiée.
7. get_live_status() reflète la même substitution (partage le même
   dict `level` que get_current_level).
8. App._remaining_seconds() renvoie bien le niveau déjà substitué (Mac
   Chronomètre et projecteur, qui reçoit ce même objet, voient donc
   la même valeur)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402


def _structure_with_ante(ante=100):
    return [
        {"small_blind": 25, "big_blind": 50, "ante": ante, "duration_minutes": 15,
         "is_break": False, "break_label": ""},
        {"small_blind": 50, "big_blind": 100, "ante": ante * 2, "duration_minutes": 15,
         "is_break": False, "break_label": ""},
    ]


def _structure_with_break(ante=100):
    return [
        {"small_blind": 25, "big_blind": 50, "ante": ante, "duration_minutes": 15,
         "is_break": False, "break_label": ""},
        {"small_blind": 0, "big_blind": 0, "ante": 0, "duration_minutes": 10,
         "is_break": True, "break_label": "Pause"},
    ]


class HeadsUpAnteOverrideTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hu_no_ante_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_with_ante(ante=100))

    def test_ante_force_a_zero_exactement_a_deux_joueurs_actifs(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")

        level = self.db.get_current_level()

        self.assertEqual(level["ante"], 0)

    def test_ante_normal_a_un_seul_joueur_actif(self):
        self.db.add_player("Alice")

        level = self.db.get_current_level()

        self.assertEqual(level["ante"], 100)

    def test_ante_normal_a_trois_joueurs_actifs_ou_plus(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Charlie")

        level = self.db.get_current_level()

        self.assertEqual(level["ante"], 100)

    def test_ante_normal_a_zero_joueur(self):
        """Aucun joueur inscrit : la garde "== 2" ne doit jamais se
        déclencher par accident (len([]) == 0, pas 2)."""
        level = self.db.get_current_level()

        self.assertEqual(level["ante"], 100)

    def test_niveau_de_pause_jamais_affecte_meme_a_deux_joueurs(self):
        self.db.set_blind_structure(_structure_with_break(ante=100))
        self.db.set_settings({"current_level_order": 2})  # la pause
        self.db.add_player("Alice")
        self.db.add_player("Bob")

        level = self.db.get_current_level()

        self.assertTrue(level["is_break"])
        self.assertEqual(level["ante"], 0)  # valeur réellement enregistrée pour la pause (0)

    def test_niveau_normal_avant_la_pause_reste_affecte_a_deux_joueurs(self):
        self.db.set_blind_structure(_structure_with_break(ante=100))
        self.db.set_settings({"current_level_order": 1})
        self.db.add_player("Alice")
        self.db.add_player("Bob")

        level = self.db.get_current_level()

        self.assertFalse(level["is_break"])
        self.assertEqual(level["ante"], 0)


class BlindStructureNeverShowsOverrideTest(unittest.TestCase):
    """get_blind_structure() alimente la grille ÉDITABLE de l'onglet
    Blindes — elle doit toujours montrer la vraie valeur enregistrée,
    jamais la substitution Heads-Up, quel que soit le nombre de joueurs
    actifs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hu_no_ante_structure_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_with_ante(ante=100))

    def test_structure_montre_toujours_la_vraie_valeur_a_deux_joueurs(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        # Confirme que la substitution est bien active pour cette scène.
        self.assertEqual(self.db.get_current_level()["ante"], 0)

        structure = self.db.get_blind_structure()

        self.assertEqual(structure[0]["ante"], 100)
        self.assertEqual(structure[1]["ante"], 200)

    def test_get_current_level_ne_modifie_jamais_la_base(self):
        """Lecture seule : aucun appel à get_current_level ne doit écrire
        quoi que ce soit dans blind_levels."""
        self.db.add_player("Alice")
        self.db.add_player("Bob")

        for _ in range(5):
            self.db.get_current_level()

        structure = self.db.get_blind_structure()
        self.assertEqual(structure[0]["ante"], 100)
        self.assertEqual(structure[1]["ante"], 200)


class HeadsUpAnteSelfCorrectsOnPlayerCountChangeTest(unittest.TestCase):
    """Le nombre de joueurs actifs est toujours recalculé en direct : un
    retour exceptionnel à 3+ joueurs restaure l'ante normal tout seul."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hu_no_ante_selfcorrect_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_with_ante(ante=100))

    def test_elimination_vers_deux_joueurs_force_lante_a_zero(self):
        pid_a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Charlie")
        self.assertEqual(self.db.get_current_level()["ante"], 100)

        self.db.eliminate_player(pid_a, eliminated_by_id=None)

        self.assertEqual(self.db.get_current_level()["ante"], 0)

    def test_reinstate_player_vers_trois_joueurs_restaure_lante_normal(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        pid_c = self.db.add_player("Charlie")
        self.db.eliminate_player(pid_c, eliminated_by_id=None)
        self.assertEqual(self.db.get_current_level()["ante"], 0)

        self.db.reinstate_player(pid_c)

        self.assertEqual(self.db.get_current_level()["ante"], 100)

    def test_ajout_dun_joueur_vers_trois_restaure_lante_normal(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.assertEqual(self.db.get_current_level()["ante"], 0)

        self.db.add_player("Charlie")

        self.assertEqual(self.db.get_current_level()["ante"], 100)


class GetLiveStatusReflectsHeadsUpOverrideTest(unittest.TestCase):
    """get_live_status() (fenêtre Lobby SNG) partage le même dict `level`
    que get_current_level — doit donc montrer la même substitution."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hu_no_ante_live_status_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_with_ante(ante=100))

    def test_live_status_montre_lante_a_zero_a_deux_joueurs(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")

        status = self.db.get_live_status()

        self.assertEqual(status["level"]["ante"], 0)

    def test_live_status_montre_lante_normal_a_trois_joueurs(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Charlie")

        status = self.db.get_live_status()

        self.assertEqual(status["level"]["ante"], 100)


class RemainingSecondsReturnsOverriddenLevelTest(unittest.TestCase):
    """App._remaining_seconds() (Chronomètre Mac) renvoie le même niveau
    déjà substitué, transmis tel quel au projecteur (ClockWindow.refresh)
    par l'appelant — voir main.py, le tick principal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hu_no_ante_remaining_seconds_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_with_ante(ante=100))
        self.db.set_settings({"clock_started": 0, "is_paused": 1})

    def test_remaining_seconds_renvoie_lante_a_zero_a_deux_joueurs(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        app = type("FakeApp", (), {"db": self.db})()

        _, level, _ = main.App._remaining_seconds(app)

        self.assertEqual(level["ante"], 0)

    def test_remaining_seconds_renvoie_lante_normal_a_trois_joueurs(self):
        self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.add_player("Charlie")
        app = type("FakeApp", (), {"db": self.db})()

        _, level, _ = main.App._remaining_seconds(app)

        self.assertEqual(level["ante"], 100)


if __name__ == "__main__":
    unittest.main()
