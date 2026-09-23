# -*- coding: utf-8 -*-
"""Petites flèches ↓/↑ de part et d'autre de "Afficher Mouvements" côté
contrôle à distance (demande du 2026-09-20) : déplacent verticalement le
bandeau "Changement de tables en cours" du Chrono Projo (voir
ClockWindow.nudge_movement_alert_position, tests/test_clock_projo_
movement_alert_drag.py pour sa couverture détaillée — pas dupliquée ici).

Ce fichier vérifie uniquement le CÂBLAGE de bout en bout :
- remote_control.py : actions "mouvements_bas"/"mouvements_haut"
  whitelistées, boutons HTML présents et correctement câblés, disposés
  [↓] [Afficher Mouvements] [↑] (même principe que le [Z−] [Plan des
  tables] [Z+] déjà existant) ;
- "Afficher Mouvements" (showMoves()) reste EXACTEMENT inchangé ;
- App._on_voice_word("mouvements_bas"/"mouvements_haut") appelle bien
  ClockWindow.nudge_movement_alert_position avec le bon SIGNE
  (mouvements_bas -> pas positif = vers le bas ; mouvements_haut -> pas
  négatif = vers le haut) ;
- ni "mouvements_bas" ni "mouvements_haut" ne touchent jamais self.db —
  aucune confirmation/suppression/modification de mouvement n'est
  déclenchée par ces deux actions, seule la position d'affichage du
  bandeau est concernée."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import remote_control  # noqa: E402


class RemoteControlWiringTest(unittest.TestCase):
    """Câblage côté remote_control.py : actions whitelistées, boutons
    HTML présents, disposés [↓] [Afficher Mouvements] [↑]."""

    def test_actions_whitelistees(self):
        self.assertIn("mouvements_bas", remote_control._VALID_ACTIONS)
        self.assertIn("mouvements_haut", remote_control._VALID_ACTIONS)

    def test_action_mouvements_toujours_whitelistee(self):
        """Non-régression explicite : l'ajout des flèches ne doit pas
        perturber l'action existante "mouvements" (showMoves())."""
        self.assertIn("mouvements", remote_control._VALID_ACTIONS)

    def test_bouton_afficher_mouvements_inchange(self):
        """"Afficher Mouvements" conserve EXACTEMENT son fonctionnement
        actuel (onclick="showMoves()", jamais sendAction) — non-régression
        explicitement demandée."""
        self.assertIn(
            '<button id="btn-mouvements" onclick="showMoves()">📋 Afficher Mouvements</button>',
            remote_control._PAGE_TEMPLATE,
        )

    def test_boutons_fleches_presents_et_correctement_cables(self):
        self.assertIn(
            '<button id="btn-mouvements-bas" onclick="sendAction(\'mouvements_bas\', this)" '
            'title="Descendre le bandeau Mouvements sur le PC">↓</button>',
            remote_control._PAGE_TEMPLATE,
        )
        self.assertIn(
            '<button id="btn-mouvements-haut" onclick="sendAction(\'mouvements_haut\', this)" '
            'title="Monter le bandeau Mouvements sur le PC">↑</button>',
            remote_control._PAGE_TEMPLATE,
        )

    def test_disposition_bas_afficher_haut_dans_cet_ordre_exact(self):
        """[↓] [Afficher Mouvements] [↑], dans cet ordre visuel exact —
        même principe que [Z−] [Plan des tables] [Z+]."""
        idx_bas = remote_control._PAGE_TEMPLATE.index('<button id="btn-mouvements-bas"')
        idx_afficher = remote_control._PAGE_TEMPLATE.index('<button id="btn-mouvements"')
        idx_haut = remote_control._PAGE_TEMPLATE.index('<button id="btn-mouvements-haut"')
        self.assertLess(idx_bas, idx_afficher)
        self.assertLess(idx_afficher, idx_haut)
        # Rien d'autre entre les trois boutons (même vérification stricte
        # que test_niveau_precedent_remote.py::RemoteControlWiringTest).
        fin_bas = remote_control._PAGE_TEMPLATE.index("</button>", idx_bas) + len("</button>")
        entre_bas_afficher = remote_control._PAGE_TEMPLATE[fin_bas:idx_afficher]
        self.assertNotIn("<button", entre_bas_afficher)
        fin_afficher = remote_control._PAGE_TEMPLATE.index("</button>", idx_afficher) + len("</button>")
        entre_afficher_haut = remote_control._PAGE_TEMPLATE[fin_afficher:idx_haut]
        self.assertNotIn("<button", entre_afficher_haut)


class _FakeDb:
    """"movement_alert_active" est lu par _on_voice_word AVANT même de
    regarder `word` (voir sa docstring/garde en tête de fonction, main.py)
    — self.db doit donc être "vrai" et répondre à get_setting_int, comme
    dans tests/test_niveau_precedent_remote.py::OnVoiceWordNiveauPrecedentTest."""
    def get_setting_int(self, key, default=0):
        return 0


class _FakeClockWindow:
    """Doublure minimale de ClockWindow : seule nudge_movement_alert_
    position (et winfo_exists) est exercée par ce dispatch — jamais de
    vraie fenêtre Tk construite ici (couverture géométrique complète dans
    tests/test_clock_projo_movement_alert_drag.py)."""
    MOVEMENT_ALERT_NUDGE_STEP_PX = 40

    def __init__(self):
        self.nudge_calls = []

    def winfo_exists(self):
        return True

    def nudge_movement_alert_position(self, delta_y):
        self.nudge_calls.append(delta_y)


class OnVoiceWordMouvementsNudgeTest(unittest.TestCase):
    """Même style de doublure que tests/test_niveau_precedent_remote.py :
    App._on_voice_word (fonction non liée) appelée directement, sans
    construire une vraie App."""

    def _fake(self, clock_window):
        class _Fake:
            db = _FakeDb()

        fake = _Fake()
        fake.clock_window = clock_window
        return fake

    def test_mouvements_bas_deplace_vers_le_bas_pas_positif(self):
        win = _FakeClockWindow()
        main.App._on_voice_word(self._fake(win), "mouvements_bas")
        self.assertEqual(win.nudge_calls, [win.MOVEMENT_ALERT_NUDGE_STEP_PX])

    def test_mouvements_haut_deplace_vers_le_haut_pas_negatif(self):
        win = _FakeClockWindow()
        main.App._on_voice_word(self._fake(win), "mouvements_haut")
        self.assertEqual(win.nudge_calls, [-win.MOVEMENT_ALERT_NUDGE_STEP_PX])

    def test_mouvements_toujours_inchange(self):
        """Non-régression explicite : l'ajout des flèches ne doit pas
        perturber le branchement existant de "mouvements" (showMoves(),
        onglet Mouvements ramené au premier plan sur le Mac)."""
        calls = []

        class _FakeMovesTab:
            pass

        class _FakeNotebook:
            def select(self, tab):
                calls.append(("select", tab))

        class _Fake:
            db = _FakeDb()
            clock_window = None
            notebook = _FakeNotebook()
            moves_tab = _FakeMovesTab()

            def _refresh_moves_tab(self):
                calls.append("refresh_moves_tab")

            def lift(self):
                calls.append("lift")

            def focus_force(self):
                calls.append("focus_force")

        fake = _Fake()
        main.App._on_voice_word(fake, "mouvements")
        self.assertEqual(
            calls,
            [("select", fake.moves_tab), "refresh_moves_tab", "lift", "focus_force"],
        )

    def test_sans_fenetre_chrono_ouverte_ne_leve_jamais(self):
        """clock_window est None tant que le Chrono Projo n'a pas été
        ouvert (voir App.__init__) — un nudge reçu avant ça ne doit
        jamais lever, juste ne rien faire."""
        fake = self._fake(None)
        main.App._on_voice_word(fake, "mouvements_bas")  # ne doit pas lever
        main.App._on_voice_word(fake, "mouvements_haut")  # ne doit pas lever

    def test_fenetre_chrono_detruite_ne_leve_jamais(self):
        """winfo_exists() False (fenêtre fermée entre-temps) : le nudge
        ne doit jamais être tenté sur une fenêtre détruite."""
        class _DestroyedClockWindow(_FakeClockWindow):
            def winfo_exists(self):
                return False

        win = _DestroyedClockWindow()
        main.App._on_voice_word(self._fake(win), "mouvements_bas")  # ne doit pas lever
        self.assertEqual(win.nudge_calls, [])

    def test_aucun_acces_a_la_base_pour_les_deux_actions(self):
        """Garde-fou explicite demandé : ni "mouvements_bas" ni
        "mouvements_haut" ne doivent jamais confirmer/supprimer/modifier
        un mouvement — vérifié ici en s'assurant qu'aucune méthode de
        self.db autre que get_setting_int (lue AVANT le dispatch, voir
        _FakeDb) n'est jamais appelée."""
        class _StrictDb(_FakeDb):
            def __getattr__(self, name):
                raise AssertionError(
                    f"self.db.{name} ne doit jamais être appelé par "
                    "mouvements_bas/mouvements_haut"
                )

        class _Fake:
            db = _StrictDb()
            clock_window = _FakeClockWindow()

        fake = _Fake()
        main.App._on_voice_word(fake, "mouvements_bas")
        main.App._on_voice_word(fake, "mouvements_haut")
        self.assertEqual(
            fake.clock_window.nudge_calls,
            [_FakeClockWindow.MOVEMENT_ALERT_NUDGE_STEP_PX, -_FakeClockWindow.MOVEMENT_ALERT_NUDGE_STEP_PX],
        )


if __name__ == "__main__":
    unittest.main()
