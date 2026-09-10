"""Tests ciblés de App._on_voice_word (main.py) pour le cas asynchrone
introduit par le rééquilibrage guidé par la grosse blinde (voir
database.py: pending_rebalance / resolve_pending_rebalance et
main.py: _check_pending_rebalance / _resolve_pending_rebalance).

Scénario couvert en priorité : une élimination déclenche une question
"quel siège est actuellement grosse blinde ?" envoyée aux téléphones du
contrôle à distance (voir remote_control.py) ; TANT QUE personne n'y a
répondu, self.db.pending_rebalance reste non None. _on_voice_word
("chronometre", déclenché par Ctrl+Maj+C ou le bouton "Chronomètre" du
contrôle à distance) ne doit JAMAIS relancer le chronomètre pendant
cette fenêtre d'attente, même si aucune alerte de mouvement (bandeau
"Terminé") n'est encore active — c'est précisément la différence avec
l'ancien garde-fou (qui ne testait que movement_alert_active).

N'instancie PAS App(tk.Tk) au complet (base de données, fenêtres,
écran projecteur...) : _on_voice_word ne touche, pour les mots testés
ici, que self.db/self.voice_awaiting_resume et une poignée de méthodes
qu'on peut simplement remplacer par des doublures — exactement ce que
ferait une vraie App, mais sans le coût ni les effets de bord d'une
interface graphique réelle. C'est une fonction non liée (App._on_voice_
word) appelée directement avec cette doublure comme "self", technique
standard pour tester une seule méthode d'une classe sans construire
l'objet complet."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402  (import après sys.path.insert, nécessaire)


class _FakeDB:
    """Doublure minimale de TournamentDatabase : seuls les points lus par
    _on_voice_word pour les mots "chronometre"/"terminer"/"elimination"
    (get_setting_int, pending_rebalance) sont modélisés."""

    def __init__(self, pending_rebalance=None, movement_alert_active=0,
                 is_paused=1, clock_started=1):
        self.pending_rebalance = pending_rebalance
        self._settings = {
            "movement_alert_active": movement_alert_active,
            "is_paused": is_paused,
            "clock_started": clock_started,
        }

    def get_setting_int(self, key, default=0):
        return self._settings.get(key, default)


class _FakeApp:
    """Doublure de App : reprend uniquement les attributs/méthodes que
    App._on_voice_word lit ou appelle. `calls` journalise l'ordre des
    méthodes déclenchées pour que chaque test vérifie exactement ce qui
    a (ou n'a pas) été appelé, sans dépendre de l'état interne réel du
    chronomètre (self.db.set_settings n'est jamais appelé ici — voir
    _FakeDB, volontairement en lecture seule)."""

    def __init__(self, db, voice_awaiting_resume):
        self.db = db
        self.voice_awaiting_resume = voice_awaiting_resume
        self.calls = []

    def _advance_elimination_banner(self):
        self.calls.append("advance_elimination_banner")

    def _voice_resume_clock(self):
        # Reproduit l'effet réel de App._voice_resume_clock pertinent ici
        # (voir main.py) : la reprise consomme voice_awaiting_resume.
        self.calls.append("voice_resume_clock")
        self.voice_awaiting_resume = False

    def _voice_show_clock(self):
        self.calls.append("voice_show_clock")

    def _voice_start_elimination(self):
        self.calls.append("voice_start_elimination")
        self.voice_awaiting_resume = True

    def _finish_movement_alert(self):
        # Reproduit l'effet réel de App._finish_movement_alert pertinent
        # ici (voir main.py) : referme l'alerte et relance le chrono.
        self.calls.append("finish_movement_alert")
        self.db._settings["movement_alert_active"] = 0
        self.db._settings["is_paused"] = 0


def _send(word, db, voice_awaiting_resume):
    """Appelle App._on_voice_word (fonction non liée) avec une _FakeApp
    fraîche portant l'état donné, et renvoie cette doublure pour
    inspection (fake.calls, fake.voice_awaiting_resume)."""
    fake = _FakeApp(db, voice_awaiting_resume)
    main.App._on_voice_word(fake, word)
    return fake


class ChronometreBloqueTantQueBBEnAttenteTest(unittest.TestCase):
    """1. Question BB en attente de réponse : Ctrl+Maj+C ne doit jamais
    relancer le chrono prématurément — c'est le scénario du correctif."""

    def test_chronometre_ne_relance_pas_pendant_question_bb_en_attente(self):
        db = _FakeDB(
            pending_rebalance={"request_id": 1, "table_id": 7, "seats": [3, 5]},
            movement_alert_active=0,  # pas encore de mouvement décidé
        )
        fake = _send("chronometre", db, voice_awaiting_resume=True)

        self.assertNotIn("voice_resume_clock", fake.calls)
        # Le bandeau d'élimination affiché (s'il y en a un) continue
        # d'avancer normalement : Ctrl+Maj+C n'est pas bloqué pour tout,
        # seulement pour la reprise du chrono.
        self.assertIn("advance_elimination_banner", fake.calls)
        # La reprise reste "en attente" : Ctrl+Maj+C pourra fonctionner
        # dès que la question sera résolue (voir test suivant).
        self.assertTrue(fake.voice_awaiting_resume)

    def test_repetition_ctrl_maj_c_reste_sans_effet_tant_que_ca_dure(self):
        """Un utilisateur impatient qui répète Ctrl+Maj+C avant toute
        réponse ne doit jamais finir par relancer le chrono par
        accident (pas de fenêtre où un appel répété change l'issue)."""
        db = _FakeDB(pending_rebalance={"request_id": 2, "table_id": 1, "seats": [1]})
        for _ in range(3):
            fake = _send("chronometre", db, voice_awaiting_resume=True)
            self.assertNotIn("voice_resume_clock", fake.calls)


class ApresResolutionSansMouvementTest(unittest.TestCase):
    """2. Réponse à la question BB qui ne déclenche finalement aucun
    mouvement (écart de rééquilibrage résorbé) : pending_rebalance
    redevient None, movement_alert_active reste à 0 — Ctrl+Maj+C doit
    alors se comporter exactement comme avant l'apparition de cette
    question (comportement historique inchangé)."""

    def test_chronometre_relance_normalement(self):
        db = _FakeDB(pending_rebalance=None, movement_alert_active=0)
        fake = _send("chronometre", db, voice_awaiting_resume=True)
        self.assertIn("voice_resume_clock", fake.calls)
        self.assertFalse(fake.voice_awaiting_resume)


class ApresResolutionAvecMouvementTest(unittest.TestCase):
    """3. Réponse qui déclenche un vrai mouvement de table :
    _trigger_movement_alert (main.py) met movement_alert_active à 1 et
    voice_awaiting_resume à False AVANT que quiconque ne puisse
    répondre à "Chronomètre" — donc, comme avant ce correctif,
    Ctrl+Maj+C n'essaie même pas de relancer le chrono (chemin
    "voice_awaiting_resume" jamais atteint) et se contente de ramener
    l'écran projecteur au premier plan ; "Terminé" (Ctrl+Maj+T) reste
    le seul moyen de clore l'alerte et relancer le chrono."""

    def test_chronometre_ne_relance_pas_montre_juste_le_chrono(self):
        db = _FakeDB(pending_rebalance=None, movement_alert_active=1, is_paused=1)
        fake = _send("chronometre", db, voice_awaiting_resume=False)
        self.assertNotIn("voice_resume_clock", fake.calls)
        self.assertIn("voice_show_clock", fake.calls)

    def test_termine_referme_alerte_et_relance_chrono(self):
        """4/5. "Terminé" (Ctrl+Maj+T) referme l'alerte de mouvement et
        relance le chrono, exactement comme avant ce correctif."""
        db = _FakeDB(pending_rebalance=None, movement_alert_active=1, is_paused=1)
        fake = _send("terminer", db, voice_awaiting_resume=False)
        self.assertIn("finish_movement_alert", fake.calls)
        self.assertEqual(db.get_setting_int("movement_alert_active"), 0)
        self.assertEqual(db.get_setting_int("is_paused"), 0)


class RepriseApresResolutionTest(unittest.TestCase):
    """4. Reprise avec Ctrl+Maj+C et 5. reprise avec Ctrl+Maj+T : les
    deux chemins de reprise historiques (sans mouvement -> Chronomètre,
    avec mouvement -> Terminé) restent corrects une fois la question BB
    sortie du chemin (pending_rebalance redevenu None dans les deux
    cas, que ce soit parce qu'aucun mouvement n'était nécessaire ou
    parce que resolve_pending_rebalance l'a déjà consommée pour créer
    le mouvement en attente)."""

    def test_ctrl_maj_c_reprend_quand_aucun_mouvement_necessaire(self):
        db = _FakeDB(pending_rebalance=None, movement_alert_active=0)
        fake = _send("chronometre", db, voice_awaiting_resume=True)
        self.assertIn("voice_resume_clock", fake.calls)

    def test_ctrl_maj_t_reprend_quand_un_mouvement_a_eu_lieu(self):
        db = _FakeDB(pending_rebalance=None, movement_alert_active=1, is_paused=1)
        fake = _send("terminer", db, voice_awaiting_resume=False)
        self.assertIn("finish_movement_alert", fake.calls)
        self.assertEqual(db.get_setting_int("is_paused"), 0)


class AutresMotsInchangesTest(unittest.TestCase):
    """Garde-fou : les mots non concernés par ce correctif ("elimination")
    conservent leur comportement, pour confirmer que la modification
    est bien localisée à la branche "chronometre" / voice_awaiting_
    resume et ne fuit pas ailleurs dans le dispatch."""

    def test_elimination_toujours_autorisee_hors_alerte(self):
        db = _FakeDB(pending_rebalance=None, movement_alert_active=0)
        fake = _send("elimination", db, voice_awaiting_resume=False)
        self.assertIn("voice_start_elimination", fake.calls)


if __name__ == "__main__":
    unittest.main()
