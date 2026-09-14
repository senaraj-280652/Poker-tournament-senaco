# -*- coding: utf-8 -*-
"""Tests de l'exception "Mode Test" au verrouillage de la section Primes
(demande du 2026-09-14) — voir App._primes_section_effectively_locked,
_on_primes_enabled_toggle, _update_primes_section_state,
_sync_primes_enabled_checkbox, _on_test_mode_toggle (main.py).

Règle demandée :
- Mode Test NON coché : comportement STRICTEMENT inchangé — dès que
  primes_session_started() (verrouillage RÉEL de la session, jamais
  modifié par cette demande) est vrai, la section reste verrouillée ;
- Mode Test coché : la section Primes ("Calculer les primes", présence,
  assiduité, nombre de jours, système de points, bounty, PKO, tous les
  autres contrôles) reste modifiable même si la session est réellement
  verrouillée ;
- décocher Mode Test pendant une session déjà démarrée rétablit
  IMMÉDIATEMENT le verrouillage normal (pas seulement au prochain tick) ;
- recocher Mode Test déverrouille à nouveau ;
- l'exception ne doit JAMAIS se propager à une autre fenêtre de la
  session : primes_session_started() lui-même (mocké ici via
  main._primes_session_locked, jamais réécrit) et la valeur "proposée"
  globale (_set_primes_enabled_proposed) doivent rester intacts/non
  appelés quand Mode Test outrepasse un verrouillage réel.

Patch `main._primes_session_locked` (la fonction MODULE-LEVEL qui lit le
registre partagé réel) plutôt que d'écrire de vrais fichiers JSON : ce
fichier teste la LOGIQUE de l'exception Mode Test elle-même (déjà
distincte du mécanisme de session, voir tests/test_primes_multi_process_
real_subprocess.py et tests/test_primes_multi_tournament_session_
scenario.py pour la couverture du registre réel), pas le registre.

N'instancie PAS App(tk.Tk) au complet : widgets réellement nécessaires
greffés sur une racine Tk réelle partagée pour toute la classe (même
précaution anti-flakiness Tcl/Tk que tests/test_primes_section_ranking_
combobox_state.py, dont la construction du bloc "Système de points
distribués" — un VRAI Combobox — est réutilisée telle quelle)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PrimesTestModeOverrideTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (voir la même précaution
    dans tests/test_primes_section_ranking_combobox_state.py)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_test_mode_override_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win.settings_vars = {}
        self.win.test_mode_var = tk.BooleanVar(value=False)
        self.win.primes_enabled_var = tk.BooleanVar(
            value=self.db.get_setting_int("primes_enabled", 1) == 1
        )

        for meth in (
            "_primes_section_effectively_locked", "_on_primes_enabled_toggle",
            "_update_primes_section_state", "_sync_primes_enabled_checkbox",
            "_on_test_mode_toggle", "_test_mode_enabled",
            "_build_ranking_formula_widget",
        ):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))
        # Stubs des méthodes hors-sujet appelées en aval, pour ne pas
        # avoir à construire tout le reste de l'onglet Paramètres/Primes.
        self.win._update_window_title = lambda: None
        self.win._refresh_bounty_tab = lambda: None

        self.parent = ttk.Frame(self.root)
        self.addCleanup(self.parent.destroy)

        self.win.primes_enabled_check = ttk.Checkbutton(
            self.parent, text="Calculer les primes", variable=self.win.primes_enabled_var,
        )

        # Widgets représentatifs de TOUS les types de contrôles réels de
        # la section (voir _build_settings_tab) : Entry générique
        # (présence/assiduité/nombre de jours/bounty/% PKO — tous
        # identiques du point de vue de _update_primes_section_state),
        # Checkbutton générique (Mode PKO), et le VRAI bloc "Système de
        # points distribués" (cas spécial Combobox readonly/disabled,
        # voir _build_ranking_formula_widget).
        self.attendance_entry = ttk.Entry(self.parent)
        self.pko_check = ttk.Checkbutton(self.parent, text="Mode PKO")
        (
            self.ranking_lbl, _ranking_row, self.ranking_combo,
            self.ranking_short_lbl, _legacy_note,
        ) = self.win._build_ranking_formula_widget(self.parent, 0)

        self.win._primes_section_widgets = [
            self.attendance_entry, self.pko_check,
            self.ranking_lbl, self.ranking_combo, self.ranking_short_lbl,
        ]
        self.win._update_primes_section_state(self.win.primes_enabled_var.get())

    def _assert_section_editable(self):
        self.assertEqual(str(self.attendance_entry.cget("state")), "normal")
        self.assertEqual(str(self.pko_check.cget("state")), "normal")
        self.assertEqual(str(self.ranking_combo.cget("state")), "readonly")
        self.assertEqual(str(self.win.primes_enabled_check.cget("state")), "normal")

    def _assert_section_locked(self):
        self.assertEqual(str(self.attendance_entry.cget("state")), "disabled")
        self.assertEqual(str(self.pko_check.cget("state")), "disabled")
        self.assertEqual(str(self.ranking_combo.cget("state")), "disabled")
        self.assertEqual(str(self.win.primes_enabled_check.cget("state")), "disabled")

    # -- 1. Session démarrée + Mode Test OFF => verrouillée (inchangé) --

    def test_session_demarree_mode_test_off_section_verrouillee(self):
        with patch.object(main, "_primes_session_locked", return_value=True):
            self.win.test_mode_var.set(False)
            self.win._sync_primes_enabled_checkbox()
            self._assert_section_locked()

            # Tentative de clic malgré tout ("filet de sécurité") :
            # revient à la valeur en base, jamais modifiée.
            before = self.db.get_setting_int("primes_enabled", 1)
            self.win.primes_enabled_var.set(not bool(before))
            self.win._on_primes_enabled_toggle()
            self.assertEqual(self.db.get_setting_int("primes_enabled", 1), before)
            self._assert_section_locked()

    # -- 2. Session démarrée + Mode Test ON => modifiable -----------------

    def test_session_demarree_mode_test_on_section_modifiable(self):
        with patch.object(main, "_primes_session_locked", return_value=True):
            self.win.test_mode_var.set(True)
            self.win._sync_primes_enabled_checkbox()
            self._assert_section_editable()

            # Le clic doit RÉELLEMENT prendre effet (pas de revert) :
            # "Calculer les primes" suit la même règle que le reste. La
            # décocher grise à son tour le reste de la section — même
            # comportement PRÉEXISTANT et volontaire que hors Mode Test
            # (_update_primes_section_state : enabled=False => grisé,
            # indépendamment du verrouillage de session, non retesté
            # ici) : ce n'est PAS ce qu'on vérifie. Ce qu'on vérifie,
            # c'est que le clic est bien PRIS EN COMPTE (jamais annulé/
            # reverté par le verrouillage réel simulé, comme il l'aurait
            # été hors Mode Test) — d'où le recochage immédiat ensuite,
            # qui doit lui aussi prendre effet et rendre la section de
            # nouveau modifiable.
            before = self.db.get_setting_int("primes_enabled", 1)
            self.win.primes_enabled_var.set(not bool(before))
            self.win._on_primes_enabled_toggle()
            self.assertEqual(self.db.get_setting_int("primes_enabled", 1), int(not bool(before)))

            self.win.primes_enabled_var.set(bool(before))
            self.win._on_primes_enabled_toggle()
            self.assertEqual(self.db.get_setting_int("primes_enabled", 1), int(bool(before)))
            self._assert_section_editable()

    def test_mode_test_on_ne_propage_jamais_a_la_valeur_proposee_globale(self):
        """L'exception doit rester strictement locale à CE tournoi : la
        valeur "proposée" globale (qui alimenterait d'AUTRES fenêtres
        pas encore démarrées de la même session) ne doit jamais être
        écrite quand Mode Test outrepasse un verrouillage RÉEL — sinon
        cette exception fuiterait vers une fenêtre qui n'est pas, elle,
        en Mode Test."""
        with patch.object(main, "_primes_session_locked", return_value=True), \
             patch.object(main, "_set_primes_enabled_proposed") as mock_proposed:
            self.win.test_mode_var.set(True)
            self.win.primes_enabled_var.set(False)
            self.win._on_primes_enabled_toggle()
            mock_proposed.assert_not_called()

    # -- 3. ON -> OFF après démarrage => verrouillage immédiat -----------

    def test_bascule_on_vers_off_apres_demarrage_verrouille_immediatement(self):
        with patch.object(main, "_primes_session_locked", return_value=True):
            self.win.test_mode_var.set(True)
            self.win._on_test_mode_toggle()
            self._assert_section_editable()

            self.win.test_mode_var.set(False)
            self.win._on_test_mode_toggle()  # simule le command= de la case Mode Test
            self._assert_section_locked()

    # -- 4. OFF -> ON après démarrage => déverrouillage -------------------

    def test_bascule_off_vers_on_apres_demarrage_deverrouille(self):
        with patch.object(main, "_primes_session_locked", return_value=True):
            self.win.test_mode_var.set(False)
            self.win._on_test_mode_toggle()
            self._assert_section_locked()

            self.win.test_mode_var.set(True)
            self.win._on_test_mode_toggle()
            self._assert_section_editable()

    # -- 5. Session NON démarrée => comportement existant inchangé -------

    def test_session_non_demarree_mode_test_sans_effet(self):
        with patch.object(main, "_primes_session_locked", return_value=False):
            for test_mode in (False, True):
                with self.subTest(test_mode=test_mode):
                    self.win.test_mode_var.set(test_mode)
                    self.win.primes_enabled_var.set(True)
                    self.win._sync_primes_enabled_checkbox()
                    self._assert_section_editable()

                    # Décocher "Calculer les primes" grise le reste,
                    # Mode Test ou non — comportement inchangé.
                    self.win.primes_enabled_var.set(False)
                    self.win._on_primes_enabled_toggle()
                    self.assertEqual(str(self.attendance_entry.cget("state")), "disabled")
                    self.assertEqual(str(self.ranking_combo.cget("state")), "disabled")
                    # remet à True pour l'itération suivante du subTest
                    self.win.primes_enabled_var.set(True)
                    self.win._on_primes_enabled_toggle()

    # -- 6. Aucune régression du verrouillage multi-tournois hors Mode Test --

    def test_hors_mode_test_la_valeur_proposee_globale_est_toujours_ecrite_session_non_verrouillee(self):
        """Non-régression explicite : hors Mode Test, avec une session
        NON verrouillée, le comportement multi-tournois existant (écriture
        de la valeur "proposée" globale, pour que d'autres fenêtres pas
        encore démarrées convergent) doit rester intact."""
        with patch.object(main, "_primes_session_locked", return_value=False), \
             patch.object(main, "_set_primes_enabled_proposed") as mock_proposed:
            self.win.test_mode_var.set(False)
            self.win.primes_enabled_var.set(False)
            self.win._on_primes_enabled_toggle()
            mock_proposed.assert_called_once_with(False)

    def test_mode_test_off_interroge_toujours_le_mecanisme_reel(self):
        """Hors Mode Test, le mécanisme réel (main._primes_session_
        locked, donc in fine primes_session_started.json/open_windows.
        json) continue d'être appelé normalement — ni remplacé, ni
        contourné par cette demande. (En Mode Test, il est court-
        circuité AVANT d'être appelé — c'est le comportement voulu,
        voir test_session_demarree_mode_test_on_section_modifiable et
        _primes_section_effectively_locked — donc pas testé ici.)"""
        with patch.object(main, "_primes_session_locked", return_value=True) as mock_locked:
            self.win.test_mode_var.set(False)
            self.win._sync_primes_enabled_checkbox()
            mock_locked.assert_called()
            self._assert_section_locked()


if __name__ == "__main__":
    unittest.main()
