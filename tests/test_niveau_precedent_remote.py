# -*- coding: utf-8 -*-
"""Tests du bouton "Niveau Précédent" côté contrôle à distance (demande
du 2026-09-11) : déjà présent dans l'onglet Chronomètre du Mac (bouton +
App._clock_prev_level, existants), mais jamais câblé côté téléphone.

Vérifie :
- App._clock_prev_level()/_go_to_level() (logique CENTRALE déjà
  existante, réutilisée SANS duplication) : niveau 2 -> 1, bornage au
  niveau 1, retour correct sur une pause, correction immédiate après un
  clic sur "Niveau Suivant" (misclick), redémarrage du chrono à la
  durée pleine du niveau ciblé ;
- App._on_voice_word("niveau_precedent") appelle bien _clock_prev_level
  (même mécanisme que "niveau_suivant", voir tests/test_voice_word_bb_
  rebalance.py pour le même style de doublure) ;
- remote_control.py : action whitelistée, bouton HTML présent
  IMMÉDIATEMENT AU-DESSUS de "Niveau Suivant" ;
- synchronisation Mac/iPhone : l'action envoyée depuis le téléphone
  produit EXACTEMENT le même état en base que l'appui du bouton Mac
  (les deux passent par la même fonction centrale, donc pas de logique
  de blindes dupliquée nulle part)."""
import os
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402
import remote_control  # noqa: E402


def _structure_avec_pause():
    """3 lignes : niveau 1 (normal), niveau 2 (PAUSE), niveau 3 (normal)
    — pour vérifier explicitement le retour correct sur une pause."""
    return [
        {"small_blind": 25, "big_blind": 50, "ante": 0, "duration_minutes": 15, "is_break": False},
        {"small_blind": 0, "big_blind": 0, "ante": 0, "duration_minutes": 10, "is_break": True, "break_label": "Pause"},
        {"small_blind": 50, "big_blind": 100, "ante": 0, "duration_minutes": 15, "is_break": False},
    ]


class _FakeApp:
    """Doublure minimale de App : seuls self.db et les méthodes réellement
    exercées par _go_to_level/_clock_prev_level/_clock_next_level sont
    présents — _refresh_clock_tab est une doublure (pas de vraie fenêtre
    Chronomètre construite ici), comme le fait déjà tests/test_voice_
    word_bb_rebalance.py pour d'autres méthodes non liées de App."""

    def __init__(self, db):
        self.db = db
        self.refresh_calls = 0

    def _refresh_clock_tab(self):
        self.refresh_calls += 1

    _go_to_level = main.App._go_to_level
    _clock_prev_level = main.App._clock_prev_level
    _clock_next_level = main.App._clock_next_level


class GoToLevelCentraleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="niveau_precedent_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_blind_structure(_structure_avec_pause())
        self.app = _FakeApp(self.db)

    def test_niveau2_vers_precedent_va_au_niveau1(self):
        self.db.set_settings({"current_level_order": 2})
        self.app._clock_prev_level()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 1)
        level = self.db.get_current_level()
        self.assertEqual((level["small_blind"], level["big_blind"]), (25, 50))
        self.assertFalse(level["is_break"])

    def test_impossible_de_descendre_avant_le_niveau_1(self):
        self.db.set_settings({"current_level_order": 1})
        self.app._clock_prev_level()  # ne doit lever aucune exception
        self.assertEqual(self.db.get_setting_int("current_level_order"), 1)

    def test_retour_correct_vers_une_pause(self):
        self.db.set_settings({"current_level_order": 3})
        self.app._clock_prev_level()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 2)
        level = self.db.get_current_level()
        self.assertTrue(level["is_break"])
        self.assertEqual(level["break_label"], "Pause")

    def test_misclick_niveau_suivant_puis_precedent_revient_au_meme_niveau(self):
        self.db.set_settings({"current_level_order": 1})
        self.app._clock_next_level()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 2)  # atterrit sur la pause
        self.app._clock_prev_level()
        self.assertEqual(self.db.get_setting_int("current_level_order"), 1)
        level = self.db.get_current_level()
        self.assertEqual((level["small_blind"], level["big_blind"]), (25, 50))

    def test_chronometre_redemarre_a_la_duree_pleine_niveau_precedent(self):
        self.db.set_settings({
            "current_level_order": 2,
            "level_start_epoch": 1000,
            "paused_accum_seconds": 555,  # simule un temps déjà écoulé sur le niveau courant
        })
        before = self.db.get_setting_int("level_start_epoch")
        self.app._clock_prev_level()
        after = self.db.get_setting_int("level_start_epoch")
        self.assertGreater(after, before)  # repart d'un epoch frais
        self.assertEqual(self.db.get_setting_int("paused_accum_seconds"), 0)  # aucun temps déjà écoulé

    def test_affichage_mac_rafraichi_immediatement(self):
        self.db.set_settings({"current_level_order": 2})
        self.app._clock_prev_level()
        self.assertEqual(self.app.refresh_calls, 1)


class OnVoiceWordNiveauPrecedentTest(unittest.TestCase):
    """Même style de doublure que tests/test_voice_word_bb_rebalance.py :
    App._on_voice_word (fonction non liée) appelée directement, sans
    construire une vraie App."""

    def test_niveau_precedent_appelle_clock_prev_level(self):
        calls = []

        class _FakeDb:
            # _on_voice_word lit "movement_alert_active" avant même de
            # regarder `word` (voir sa docstring/garde en tête de
            # fonction) : self.db doit donc être "vrai" et répondre à
            # get_setting_int, pas juste non-None.
            def get_setting_int(self, key, default=0):
                return 0

        class _Fake:
            db = _FakeDb()

            def _clock_prev_level(self):
                calls.append("clock_prev_level")

        fake = _Fake()
        main.App._on_voice_word(fake, "niveau_precedent")
        self.assertEqual(calls, ["clock_prev_level"])

    def test_niveau_suivant_toujours_inchange(self):
        """Non-régression explicite : l'ajout de "niveau_precedent" ne
        doit pas perturber le branchement existant de "niveau_suivant"."""
        calls = []

        class _FakeDb:
            def get_setting_int(self, key, default=0):
                return 0

        class _Fake:
            db = _FakeDb()

            def _clock_next_level(self):
                calls.append("clock_next_level")

        fake = _Fake()
        main.App._on_voice_word(fake, "niveau_suivant")
        self.assertEqual(calls, ["clock_next_level"])


class RemoteControlWiringTest(unittest.TestCase):
    """Câblage côté remote_control.py : action whitelistée, bouton HTML
    présent, placé IMMÉDIATEMENT au-dessus de "Niveau Suivant"."""

    def test_action_whitelistee(self):
        self.assertIn("niveau_precedent", remote_control._VALID_ACTIONS)

    def test_niveau_suivant_toujours_whitelistee(self):
        self.assertIn("niveau_suivant", remote_control._VALID_ACTIONS)

    def test_bouton_html_present_et_correctement_cable(self):
        self.assertIn(
            '<button id="btn-niveau-precedent" onclick="sendAction(\'niveau_precedent\', this)">'
            '⏮ Niveau Précédent</button>',
            remote_control._PAGE_TEMPLATE,
        )

    def test_bouton_place_immediatement_au_dessus_de_niveau_suivant(self):
        idx_precedent = remote_control._PAGE_TEMPLATE.index('<button id="btn-niveau-precedent"')
        idx_suivant = remote_control._PAGE_TEMPLATE.index('<button id="btn-niveau-suivant"')
        self.assertLess(idx_precedent, idx_suivant)
        # Rien d'autre entre les deux boutons (ordre visuel EXACT demandé) :
        # seule la fin de la balise du 1er bouton (jusqu'à son </button>)
        # doit séparer le début des deux balises <button ...>.
        fin_bouton_precedent = remote_control._PAGE_TEMPLATE.index("</button>", idx_precedent) + len("</button>")
        entre = remote_control._PAGE_TEMPLATE[fin_bouton_precedent:idx_suivant]
        self.assertNotIn("<button", entre)


class SynchronisationMacIphoneTest(unittest.TestCase):
    """L'action déclenchée depuis le téléphone (dispatch _on_voice_word)
    produit EXACTEMENT le même état en base que l'appui direct du
    bouton Mac — aucune logique de blindes dupliquée entre les deux
    chemins, les deux passent par _clock_prev_level()/_go_to_level()."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="niveau_precedent_sync_test_")
        self.addCleanup(self._tmp.cleanup)

    def _make_app(self, path):
        db = database.Database(path)
        db.set_blind_structure(_structure_avec_pause())
        db.set_settings({"current_level_order": 3})
        return _FakeApp(db), db

    def test_dispatch_telephone_et_bouton_mac_produisent_le_meme_etat(self):
        # Chemin "bouton Mac" : appel direct.
        app_mac, db_mac = self._make_app(os.path.join(self._tmp.name, "mac.tournoi"))
        app_mac._clock_prev_level()

        # Chemin "téléphone" : passe par le dispatch _on_voice_word,
        # exactement comme le ferait _poll_voice_queue en recevant le mot
        # déposé par remote_control.py (voir App.__init__: on_word=...).
        app_phone, db_phone = self._make_app(os.path.join(self._tmp.name, "phone.tournoi"))
        main.App._on_voice_word(app_phone, "niveau_precedent")

        self.assertEqual(
            db_mac.get_setting_int("current_level_order"),
            db_phone.get_setting_int("current_level_order"),
        )
        level_mac = db_mac.get_current_level()
        level_phone = db_phone.get_current_level()
        self.assertEqual(
            (level_mac["small_blind"], level_mac["big_blind"], level_mac["is_break"]),
            (level_phone["small_blind"], level_phone["big_blind"], level_phone["is_break"]),
        )
        db_mac.conn.close()
        db_phone.conn.close()


if __name__ == "__main__":
    unittest.main()
