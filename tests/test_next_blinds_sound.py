"""Tests ciblés du nouveau son "Son prochain changement Blindes" (voir
main.py: _open_clock_sounds_dialog / _maybe_play_next_blinds_sound /
_next_blinds_sound_lead_seconds / _save_clock_sound_lead_seconds).

Couvre précisément les 3 conditions explicitement demandées pour ne PAS
jouer ce son (pause en cours — déjà exclue par l'appelant —, round
suivant = pause, blindes identiques au round suivant), le fait qu'il ne
se joue qu'UNE SEULE FOIS par round, et le repli à 60s en cas de réglage
absent/invalide — sans jamais toucher au fichier réel de préférences
(~/.poker_tournament/export_prefs.json, voir export_prefs.py) : toutes
les lectures/écritures export_prefs sont doublées (unittest.mock.patch)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeDB:
    def __init__(self, next_level=None):
        self._next_level = next_level

    def get_next_level(self):
        return self._next_level


class _FakeApp:
    """Doublure de App : ne reprend que ce que _maybe_play_next_blinds_
    sound lit/appelle (self.db.get_next_level, self._next_blinds_sound_
    lead_seconds, self._play_clock_sound, self._next_blinds_sound_
    played_for_order)."""

    def __init__(self, db, lead_seconds=60, played_for_order=None):
        self.db = db
        self._lead_seconds = lead_seconds
        self._next_blinds_sound_played_for_order = played_for_order
        self.played = []

    def _next_blinds_sound_lead_seconds(self):
        return self._lead_seconds

    def _play_clock_sound(self, setting_key):
        self.played.append(setting_key)


CURRENT = {"level_order": 3, "is_break": False, "small_blind": 100, "big_blind": 200}


def _maybe_play(fake, next_level, remaining=60, lead_seconds=60, played_for_order=None):
    fake.db = _FakeDB(next_level)
    fake._lead_seconds = lead_seconds
    fake._next_blinds_sound_played_for_order = played_for_order
    main.App._maybe_play_next_blinds_sound(fake, CURRENT, remaining)
    return fake


class NePasJouerTest(unittest.TestCase):
    """Les 3 cas explicitement exclus par la demande, plus les garde-fous
    évidents (pas de round suivant, délai non atteint)."""

    def test_pas_de_round_suivant(self):
        fake = _FakeApp(db=None)
        _maybe_play(fake, next_level=None)
        self.assertEqual(fake.played, [])

    def test_round_suivant_est_une_pause(self):
        fake = _FakeApp(db=None)
        next_level = {"is_break": True, "small_blind": 150, "big_blind": 300}
        _maybe_play(fake, next_level)
        self.assertEqual(fake.played, [])

    def test_blindes_identiques_au_round_suivant(self):
        fake = _FakeApp(db=None)
        next_level = {"is_break": False, "small_blind": 100, "big_blind": 200}  # == CURRENT
        _maybe_play(fake, next_level)
        self.assertEqual(fake.played, [])

    def test_delai_pas_encore_atteint(self):
        fake = _FakeApp(db=None)
        next_level = {"is_break": False, "small_blind": 150, "big_blind": 300}
        _maybe_play(fake, next_level, remaining=120, lead_seconds=60)
        self.assertEqual(fake.played, [])

    def test_delai_invalide_ou_nul_desactive(self):
        fake = _FakeApp(db=None)
        next_level = {"is_break": False, "small_blind": 150, "big_blind": 300}
        _maybe_play(fake, next_level, remaining=10, lead_seconds=0)
        self.assertEqual(fake.played, [])


class JoueUneSeuleFoisParRoundTest(unittest.TestCase):
    def test_joue_quand_toutes_les_conditions_sont_reunies(self):
        fake = _FakeApp(db=None)
        next_level = {"is_break": False, "small_blind": 150, "big_blind": 300}
        _maybe_play(fake, next_level, remaining=45, lead_seconds=60)
        self.assertEqual(fake.played, ["sound_next_blinds_path"])
        self.assertEqual(fake._next_blinds_sound_played_for_order, CURRENT["level_order"])

    def test_ne_rejoue_pas_tant_que_le_round_est_le_meme(self):
        fake = _FakeApp(db=None)
        next_level = {"is_break": False, "small_blind": 150, "big_blind": 300}
        _maybe_play(fake, next_level, remaining=45, lead_seconds=60)
        # Rappel immédiat (tick suivant, toujours dans le même round) :
        # déjà marqué joué pour ce level_order -> pas de second appel.
        fake.db = _FakeDB(next_level)
        main.App._maybe_play_next_blinds_sound(fake, CURRENT, remaining=10)
        self.assertEqual(fake.played, ["sound_next_blinds_path"])

    def test_rejoue_au_round_suivant(self):
        fake = _FakeApp(db=None, played_for_order=CURRENT["level_order"])
        other_level = {"level_order": 4, "is_break": False, "small_blind": 150, "big_blind": 300}
        next_level = {"is_break": False, "small_blind": 200, "big_blind": 400}
        fake.db = _FakeDB(next_level)
        main.App._maybe_play_next_blinds_sound(fake, other_level, remaining=30)
        self.assertEqual(fake.played, ["sound_next_blinds_path"])
        self.assertEqual(fake._next_blinds_sound_played_for_order, 4)


class DelaiConfigureTest(unittest.TestCase):
    """_next_blinds_sound_lead_seconds / _save_clock_sound_lead_seconds :
    jamais d'accès au vrai fichier export_prefs.json ici (mocké)."""

    def test_valeur_par_defaut_60_si_rien_de_regle(self):
        with patch.object(main.export_prefs, "load_value", return_value="60") as m:
            self.assertEqual(main.App._next_blinds_sound_lead_seconds(_FakeApp(None)), 60)
            m.assert_called_once_with("sound_next_blinds_path_lead_seconds", "60")

    def test_valeur_reglee_est_utilisee(self):
        with patch.object(main.export_prefs, "load_value", return_value="90"):
            self.assertEqual(main.App._next_blinds_sound_lead_seconds(_FakeApp(None)), 90)

    def test_valeur_invalide_retombe_a_60(self):
        with patch.object(main.export_prefs, "load_value", return_value="abc"):
            self.assertEqual(main.App._next_blinds_sound_lead_seconds(_FakeApp(None)), 60)

    def test_valeur_vide_retombe_a_60(self):
        with patch.object(main.export_prefs, "load_value", return_value=""):
            self.assertEqual(main.App._next_blinds_sound_lead_seconds(_FakeApp(None)), 60)

    def test_valeur_negative_ou_nulle_retombe_a_60(self):
        with patch.object(main.export_prefs, "load_value", return_value="0"):
            self.assertEqual(main.App._next_blinds_sound_lead_seconds(_FakeApp(None)), 60)
        with patch.object(main.export_prefs, "load_value", return_value="-5"):
            self.assertEqual(main.App._next_blinds_sound_lead_seconds(_FakeApp(None)), 60)

    def test_save_ecrit_sous_la_bonne_cle(self):
        var = type("V", (), {"get": lambda self: "45"})()
        with patch.object(main.export_prefs, "save_value") as m:
            main.App._save_clock_sound_lead_seconds(_FakeApp(None), "sound_next_blinds_path", var)
            m.assert_called_once_with("sound_next_blinds_path_lead_seconds", "45")


if __name__ == "__main__":
    unittest.main()
