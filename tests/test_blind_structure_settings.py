# -*- coding: utf-8 -*-
"""Chantier "Paramètres > Structure des blindes — durées variables + 2
pauses programmables" (2026-09-24) : remplace la ligne unique "Durée
d'un Round" par 2 lignes (Durée / Nb Rounds), et la ligne unique "Durée
de la Pause" par 2 lignes programmables (Durée / Après Round).

Deux volets, comme les autres chantiers de ce module :
1. DurationScheduleTest / BreakScheduleTest — structures.py directement
   (generate_blind_structure) : l'algorithme lui-même, indépendant de
   toute interface.
2. GenerateCustomBlindStructureTest et consorts — doublure de App (même
   principe que tests/test_apply_blinds_button.py : self.db RÉEL,
   _refresh_all/_refresh_clock_tab/_collect_and_save_all_settings de
   simples compteurs, jamais une vraie fenêtre Tk construite ici) :
   validations, régénération en cours de tournoi (niveau conservé,
   Chronomètre rafraîchi), compatibilité avec le bouton "Appliquer"."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import structures  # noqa: E402


# =====================================================================
# 1. structures.py directement
# =====================================================================
class DurationScheduleTest(unittest.TestCase):
    def _durations(self, **kwargs):
        rows = structures.generate_blind_structure(start_small_blind=25, start_big_blind=50, **kwargs)
        return [r["duration_minutes"] for r in rows if not r["is_break"]]

    def test_sans_echeancier_comportement_historique_inchange(self):
        durations = self._durations(duration_minutes=15)
        self.assertTrue(all(d == 15 for d in durations))
        self.assertEqual(len(durations), structures.GENERATED_ROUNDS_COUNT)

    def test_ligne1_sans_nb_rounds_sapplique_a_tous_les_rounds(self):
        durations = self._durations(duration_schedule=[(20, None)])
        self.assertTrue(all(d == 20 for d in durations))

    def test_ligne1_6_rounds_puis_ligne2_reste_du_tournoi(self):
        durations = self._durations(duration_schedule=[(20, 6), (15, None)])
        self.assertEqual(durations[:6], [20] * 6)
        self.assertEqual(durations[6:], [15] * (len(durations) - 6))

    def test_ligne2_absente_ligne1_se_prolonge(self):
        # Seule la ligne 1 est fournie (avec un compteur) : au-delà, la
        # dernière durée connue (celle de la ligne 1) se prolonge —
        # décision explicite du 2026-09-24, jamais une erreur.
        durations = self._durations(duration_schedule=[(20, 6)])
        self.assertEqual(durations[:6], [20] * 6)
        self.assertEqual(durations[6:], [20] * (len(durations) - 6))

    def test_deux_lignes_chiffrees_epuisees_prolonge_la_derniere(self):
        durations = self._durations(duration_schedule=[(20, 6), (15, 4)])
        self.assertEqual(durations[:6], [20] * 6)
        self.assertEqual(durations[6:10], [15] * 4)
        self.assertEqual(durations[10:], [15] * (len(durations) - 10))


class BreakScheduleTest(unittest.TestCase):
    def _rows(self, **kwargs):
        return structures.generate_blind_structure(start_small_blind=25, start_big_blind=50, **kwargs)

    def _game_round_index_of(self, rows):
        """Pour chaque ligne, le numéro de round DE JEU en cours à cet
        endroit (les pauses ne l'incrémentent jamais)."""
        out = []
        counter = 0
        for r in rows:
            if not r["is_break"]:
                counter += 1
            out.append(counter)
        return out

    def test_pause_apres_round_4(self):
        rows = self._rows(duration_schedule=[(20, None)], break_schedule=[(10, 4)])
        idx = self._game_round_index_of(rows)
        break_positions = [idx[i] for i, r in enumerate(rows) if r["is_break"]]
        self.assertEqual(break_positions, [4])
        self.assertEqual(next(r["duration_minutes"] for r in rows if r["is_break"]), 10)

    def test_deux_pauses_apres_4_et_8(self):
        rows = self._rows(duration_schedule=[(20, None)], break_schedule=[(10, 4), (15, 8)])
        idx = self._game_round_index_of(rows)
        break_positions = [idx[i] for i, r in enumerate(rows) if r["is_break"]]
        self.assertEqual(break_positions, [4, 8])
        durations = [r["duration_minutes"] for r in rows if r["is_break"]]
        self.assertEqual(durations, [10, 15])

    def test_pauses_ne_comptent_jamais_comme_des_rounds(self):
        rows = self._rows(duration_schedule=[(20, None)], break_schedule=[(10, 4)])
        idx = self._game_round_index_of(rows)
        # Le round DE JEU juste après la pause reste le 5e, jamais le 6e :
        # la pause elle-même n'a pas avancé le compteur.
        break_row_pos = next(i for i, r in enumerate(rows) if r["is_break"])
        self.assertEqual(idx[break_row_pos + 1], 5)

    def test_exemple_complet_de_la_demande(self):
        """Reproduit exactement l'exemple donné : 20min x6, 15min pour le
        reste, pause 10min après round 4, pause 15min après round 8."""
        rows = self._rows(
            duration_schedule=[(20, 6), (15, None)],
            break_schedule=[(10, 4), (15, 8)],
        )
        # Aplati (durée, is_break) pour comparaison directe.
        flat = [(r["duration_minutes"], r["is_break"]) for r in rows[:11]]
        self.assertEqual(flat, [
            (20, False), (20, False), (20, False), (20, False),  # rounds 1-4
            (10, True),                                           # pause 10 min
            (20, False), (20, False),                              # rounds 5-6
            (15, False), (15, False),                              # rounds 7-8
            (15, True),                                            # pause 15 min
            (15, False),                                           # round 9
        ])

    def test_echeancier_vide_explicite_aucune_pause(self):
        rows = self._rows(duration_schedule=[(20, None)], break_schedule=[])
        self.assertFalse(any(r["is_break"] for r in rows))

    def test_none_reste_lancien_comportement_break_every(self):
        rows = self._rows(duration_minutes=15, break_duration_minutes=15, break_every=4)
        self.assertTrue(any(r["is_break"] for r in rows))


# =====================================================================
# 2. App._generate_custom_blind_structure
# =====================================================================
class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


def _settings(**overrides):
    base = {
        "start_small_blind": "25", "start_big_blind": "50",
        "ante_start_level": "4", "start_ante": "25",
        "round_duration_minutes": "20", "round_count_1": "",
        "round_duration_minutes_2": "", "round_count_2": "",
        "break_minutes_1": "", "break_after_round_1": "",
        "break_minutes_2": "", "break_after_round_2": "",
    }
    base.update(overrides)
    return {k: _FakeVar(v) for k, v in base.items()}


class _FakeApp:
    _generate_custom_blind_structure = main.App._generate_custom_blind_structure
    _parse_optional_positive_int = main.App._parse_optional_positive_int

    def __init__(self, db, settings_vars):
        self.db = db
        self.settings_vars = settings_vars
        self.refresh_all_calls = 0
        self.refresh_clock_calls = 0
        self.collect_and_save_calls = 0

    def _refresh_all(self):
        self.refresh_all_calls += 1

    def _refresh_clock_tab(self):
        self.refresh_clock_calls += 1

    def _collect_and_save_all_settings(self):
        self.collect_and_save_calls += 1


class _GenerateCustomBlindStructureTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="blind_structure_settings_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _app(self, **overrides):
        app = _FakeApp(self.db, _settings(**overrides))
        app.blinds_tree = "dummy"  # hasattr(...) suffit (voir _apply_blinds_from_tab)
        return app

    def _generate(self, app, confirm=True):
        with patch.object(main, "messagebox") as mock_mb:
            mock_mb.askyesno.return_value = confirm
            app._generate_custom_blind_structure()
            return mock_mb


class RegenerationSimpleTest(_GenerateCustomBlindStructureTestCase):
    def test_une_seule_duree_pour_tout_le_tournoi(self):
        app = self._app(round_duration_minutes="20")
        self._generate(app)
        levels = self.db.get_blind_structure()
        durations = [l["duration_minutes"] for l in levels if not l["is_break"]]
        self.assertTrue(all(d == 20 for d in durations))

    def test_20min_6_rounds_puis_15min_le_reste(self):
        app = self._app(round_duration_minutes="20", round_count_1="6", round_duration_minutes_2="15")
        self._generate(app)
        levels = self.db.get_blind_structure()
        durations = [l["duration_minutes"] for l in levels if not l["is_break"]]
        self.assertEqual(durations[:6], [20] * 6)
        self.assertEqual(durations[6:], [15] * (len(durations) - 6))

    def test_pause_apres_round_4(self):
        app = self._app(break_minutes_1="10", break_after_round_1="4")
        self._generate(app)
        levels = self.db.get_blind_structure()
        breaks = [l for l in levels if l["is_break"]]
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0]["duration_minutes"], 10)

    def test_deux_durees_et_deux_pauses_simultanement(self):
        app = self._app(
            round_duration_minutes="20", round_count_1="6", round_duration_minutes_2="15",
            break_minutes_1="10", break_after_round_1="4",
            break_minutes_2="15", break_after_round_2="8",
        )
        self._generate(app)
        levels = self.db.get_blind_structure()
        flat = [(l["duration_minutes"], l["is_break"]) for l in levels[:11]]
        self.assertEqual(flat, [
            (20, False), (20, False), (20, False), (20, False),
            (10, True),
            (20, False), (20, False),
            (15, False), (15, False),
            (15, True),
            (15, False),
        ])

    def test_premiere_pause_vide_ignoree(self):
        app = self._app(break_minutes_2="15", break_after_round_2="8")
        self._generate(app)
        breaks = [l for l in self.db.get_blind_structure() if l["is_break"]]
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0]["duration_minutes"], 15)

    def test_aucune_confirmation_aucune_ecriture(self):
        before = self.db.get_blind_structure()
        app = self._app(round_duration_minutes="99")
        self._generate(app, confirm=False)
        self.assertEqual(self.db.get_blind_structure(), before)
        self.assertEqual(app.refresh_all_calls, 0)


class ValidationTest(_GenerateCustomBlindStructureTestCase):
    def test_valeurs_invalides_naltere_pas_la_base(self):
        before = self.db.get_blind_structure()
        app = self._app(round_duration_minutes="abc")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()
        self.assertEqual(self.db.get_blind_structure(), before)
        self.assertEqual(app.refresh_all_calls, 0)

    def test_nb_rounds_invalide_ligne1(self):
        app = self._app(round_count_1="-3")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()
        mock_mb.askyesno.assert_not_called()

    def test_ligne2_sans_duree_alors_que_nb_rounds_ligne1_rempli(self):
        app = self._app(round_count_1="6", round_duration_minutes_2="")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()

    def test_pause_duree_sans_after_round(self):
        app = self._app(break_minutes_1="10", break_after_round_1="")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()

    def test_pause_after_round_sans_duree(self):
        app = self._app(break_minutes_1="", break_after_round_1="4")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()

    def test_deux_pauses_meme_round(self):
        app = self._app(
            break_minutes_1="10", break_after_round_1="4",
            break_minutes_2="15", break_after_round_2="4",
        )
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()

    def test_deuxieme_pause_avant_la_premiere(self):
        app = self._app(
            break_minutes_1="10", break_after_round_1="8",
            break_minutes_2="15", break_after_round_2="4",
        )
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()

    def test_pause_apres_round_inexistant_refusee(self):
        app = self._app(break_minutes_1="10", break_after_round_1="999")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_called_once()

    def test_nb_rounds_superieur_a_20_accepte_sans_erreur(self):
        # Décision explicite du 2026-09-24 : aucune erreur/avertissement,
        # simplement sans effet visible (le barème ne compte que 20
        # rounds de toute façon).
        app = self._app(round_duration_minutes="20", round_count_1="50", round_duration_minutes_2="15")
        mock_mb = self._generate(app)
        mock_mb.showerror.assert_not_called()
        levels = self.db.get_blind_structure()
        durations = [l["duration_minutes"] for l in levels if not l["is_break"]]
        self.assertTrue(all(d == 20 for d in durations))


class TournoiEnCoursTest(_GenerateCustomBlindStructureTestCase):
    def test_niveau_courant_conserve_si_toujours_valide(self):
        self.db.set_settings({
            "current_level_order": 5, "level_start_epoch": 1000,
            "paused_accum_seconds": 42, "is_paused": 0, "clock_started": 1,
        })
        app = self._app(round_duration_minutes="20")
        self._generate(app)
        self.assertEqual(self.db.get_setting_int("current_level_order"), 5)
        self.assertEqual(self.db.get_setting_int("level_start_epoch"), 1000)
        self.assertEqual(self.db.get_setting_int("paused_accum_seconds"), 42)
        self.assertEqual(self.db.get_setting_int("is_paused"), 0)
        self.assertEqual(self.db.get_setting_int("clock_started"), 1)

    def test_jamais_de_remise_au_niveau_1(self):
        self.db.set_settings({"current_level_order": 5})
        app = self._app(round_duration_minutes="20", round_count_1="6", round_duration_minutes_2="15")
        self._generate(app)
        self.assertNotEqual(self.db.get_setting_int("current_level_order"), 1)
        self.assertEqual(self.db.get_setting_int("current_level_order"), 5)

    def test_structure_raccourcie_ramene_au_dernier_niveau_valide(self):
        # Structure standard normale = 20 rounds + pauses (~25 lignes).
        # On force le niveau courant bien au-delà de ce que produira la
        # régénération pour vérifier le clamp — même règle que le
        # bouton "Appliquer"/App._generate_custom_blind_structure
        # historique.
        self.db.set_settings({"current_level_order": 999})
        app = self._app(round_duration_minutes="20")
        self._generate(app)
        new_len = len(self.db.get_blind_structure())
        self.assertEqual(self.db.get_setting_int("current_level_order"), new_len)
        self.assertLess(new_len, 999)

    def test_chronometre_et_chrono_projo_rafraichis(self):
        app = self._app(round_duration_minutes="20")
        self._generate(app)
        self.assertEqual(app.refresh_clock_calls, 1)
        self.assertEqual(app.refresh_all_calls, 1)


class CompatibiliteAnciensTournoisTest(_GenerateCustomBlindStructureTestCase):
    def test_ancien_tournoi_sans_les_nouveaux_settings_fonctionne(self):
        # Un ancien .tournoi n'a JAMAIS écrit round_count_1/round_
        # duration_minutes_2/... : Database.get_setting renvoie alors
        # simplement le repli "" fourni par le widget (voir _build_
        # settings_tab) — non simulé ici via la base (elle ne les
        # connaît pas non plus par défaut), seulement via des settings_
        # vars "vides" comme le ferait le widget pour un tel fichier.
        app = self._app(round_duration_minutes="15")
        self._generate(app)
        levels = self.db.get_blind_structure()
        durations = [l["duration_minutes"] for l in levels if not l["is_break"]]
        self.assertTrue(all(d == 15 for d in durations))
        # Comportement historique : pauses automatiques toutes les 4
        # rounds n'existent PAS ici (nouveau comportement : sans pause
        # définie, aucune pause) — voir CONSIGNE : cette régénération
        # n'utilise plus jamais break_every.
        breaks = [l for l in levels if l["is_break"]]
        self.assertEqual(breaks, [])


class CompatibiliteBoutonAppliquerTest(_GenerateCustomBlindStructureTestCase):
    def test_regeneration_puis_appliquer_fonctionnent_ensemble(self):
        """Régénère depuis Paramètres, puis simule une édition manuelle
        suivie d'un clic sur "Appliquer" (onglet Blindes) — les deux
        chantiers doivent cohabiter sans interaction particulière."""
        app = self._app(round_duration_minutes="20", round_count_1="4", round_duration_minutes_2="15")
        self._generate(app)
        levels_after_regen = self.db.get_blind_structure()
        self.assertEqual(levels_after_regen[0]["duration_minutes"], 20)

        # "Appliquer" (voir tests/test_apply_blinds_button.py) réutilise
        # une chaîne totalement indépendante (_collect_blinds_from_
        # widgets -> _rounds_to_flat_structure -> set_blind_structure) :
        # vérifie juste ici que set_blind_structure reste bien la source
        # unique que les deux chantiers partagent, sans rien dupliquer.
        rounds = [
            {"duration": 30, "sb": l["small_blind"], "bb": l["big_blind"], "ante": l["ante"], "pause": 0}
            for l in levels_after_regen if not l["is_break"]
        ][:3]
        flat = main.App._rounds_to_flat_structure(app, rounds)
        self.db.set_blind_structure(flat)
        self.assertEqual(len(self.db.get_blind_structure()), 3)


if __name__ == "__main__":
    unittest.main()
