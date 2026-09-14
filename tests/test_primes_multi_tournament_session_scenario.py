# -*- coding: utf-8 -*-
"""Test de bout en bout, MULTI-TOURNOIS (demande explicite du
2026-09-09, "je veux un test automatisé couvrant ce scénario complet
multi-processus/multi-tournois, pas seulement la lecture de la
préférence globale") : reproduit EXACTEMENT le scénario numéroté fourni
par l'utilisateur pour l'interrupteur général "Calculer les primes",
avec de VRAIS fichiers .tournoi SQLite en dossier temporaire (jamais
mockés) et le VRAI mécanisme de propagation (export_prefs.json +
open_windows.json + primes_session_started.json, tous trois redirigés
vers ce même dossier temporaire, jamais le vrai ~/.poker_tournament).

Contrairement à tests/test_primes_enabled_toggle.py (qui teste chaque
fonction — _sync_primes_enabled_pref, _primes_session_locked,
_on_primes_enabled_toggle... — en isolation), ce fichier enchaîne
plusieurs "fenêtres" simulées (une par tournoi, comme autant de process
séparés — voir _SimulatedTournamentWindow) et fait réellement circuler
l'état ENTRE elles via le disque partagé, exactement comme le ferait le
vrai mécanisme (App._tick, 1x/seconde) : c'est la garantie qu'aucune
étape individuellement correcte ne cache une divergence à l'échelle du
scénario complet.

Scénario couvert (numérotation reprise du message de l'utilisateur) :
  1-2. Tournois A et B ouverts, aucun démarré.
  3-4. "Calculer les primes" cochée par défaut dans A et B.
  5-6. Décochée dans A -> B doit converger tout seul au tick suivant
       (case ET toute la section grisées), sans aucune divergence.
  7.   Tournoi C créé APRÈS cette désactivation -> créé directement
       décoché, jamais avec l'ancienne valeur cochée.
  (test inverse) Recochée avant tout démarrage -> A, B et C reconvergent
       tous vers "cochée".
  (fin) Le premier tournoi démarre -> valeur verrouillée pour A, B, C ;
       un tournoi D créé ENSUITE hérite de cette valeur verrouillée, et
       plus aucune case ne peut la changer.
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402


class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeCheckbutton:
    def __init__(self, state="normal"):
        self._state = state

    def configure(self, **kwargs):
        if "state" in kwargs:
            self._state = kwargs["state"]

    def cget(self, key):
        assert key == "state"
        return self._state


class _FakeSectionWidget:
    def __init__(self):
        self.state = "normal"

    def configure(self, state):
        self.state = state


class _SimulatedTournamentWindow:
    """Doublure d'UNE fenêtre de tournoi RÉELLE (un process indépendant
    en production, voir spawn_app_process) : porte sa PROPRE Database
    (vrai fichier .tournoi en dossier temporaire) et ses PROPRES widgets
    simulés (jamais partagés entre fenêtres, exactement comme deux vrais
    process auraient chacun leur propre mémoire) — mais toutes les
    fenêtres du scénario partagent le MÊME stockage sur disque
    (export_prefs.json / open_windows.json / primes_session_started.json,
    redirigés vers un dossier temporaire commun, voir setUp), exactement
    comme en conditions réelles.

    Les méthodes ci-dessous appellent les VRAIES fonctions de main.py
    (jamais réimplémentées ici) en se passant elles-mêmes comme `self` —
    même principe que _FakeApp dans tests/test_single_tournament_at_a_
    time.py et tests/test_primes_enabled_toggle.py."""

    def __init__(self, db, name):
        self.db = db
        self.name = name
        self.primes_enabled_var = _FakeVar(db.get_setting_int("primes_enabled", 1) == 1)
        self.primes_enabled_check = _FakeCheckbutton()
        self._primes_section_widgets = [_FakeSectionWidget() for _ in range(3)]
        self.refresh_bounty_tab_calls = 0
        # Mode Test (demande du 2026-09-14) : toujours désactivé dans ce
        # scénario — aucun des tests ci-dessous ne le concerne, voir
        # tests/test_primes_test_mode_override.py pour sa couverture
        # dédiée.
        self.test_mode_var = _FakeVar(False)

    def _refresh_bounty_tab(self):
        self.refresh_bounty_tab_calls += 1

    def _test_mode_enabled(self):
        return main.App._test_mode_enabled(self)

    def _primes_section_effectively_locked(self):
        return main.App._primes_section_effectively_locked(self)

    def _update_primes_section_state(self, enabled, locked=None):
        main.App._update_primes_section_state(self, enabled, locked=locked)

    # -- Actions "utilisateur"/"tick", chacune un appel direct aux VRAIES
    # méthodes de main.py, dans le même ordre que le code réel. --------
    def tick(self):
        """Un "tick" de _tick (1x/seconde en conditions réelles, voir
        main.py) pour tout ce qui concerne les primes : resynchronisation
        de la copie locale vers la valeur globale proposée (si pas
        encore démarré), puis rafraîchissement case + section."""
        main._sync_primes_enabled_pref(self.db)
        main.App._sync_primes_enabled_checkbox(self)

    def click_primes_checkbox(self, new_value):
        """Simule un clic utilisateur sur "Calculer les primes" (voir
        _on_primes_enabled_toggle) : positionne d'abord la BooleanVar
        comme le ferait Tkinter juste avant d'invoquer la commande."""
        self.primes_enabled_var.set(new_value)
        main.App._on_primes_enabled_toggle(self)

    def start_tournament(self):
        """Simule App._clock_resume au tout premier démarrage
        (transition clock_started 0->1) : resynchronisation défensive de
        dernière minute puis pose du verrou de session — dans le même
        ordre que le code réel."""
        main._sync_primes_enabled_pref(self.db)
        self.db.set_settings({"clock_started": 1})
        open_windows.mark_primes_session_started()

    def primes_enabled_in_db(self):
        return self.db.get_setting_int("primes_enabled", 1) == 1

    def section_widgets_state(self):
        return {w.state for w in self._primes_section_widgets}


class PrimesMultiTournamentSessionScenarioTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_primes_scenario_test_")
        self.tmpdir = self._tmpdir_ctx.name
        self.addCleanup(self._tmpdir_ctx.cleanup)

        # Redirige les TROIS stockages partagés vers ce même dossier
        # temporaire — jamais ~/.poker_tournament, le vrai répertoire de
        # l'utilisateur (même précaution que test_open_windows_atomic_
        # write.py et ExportPrefsNoStaleOverwriteTest).
        export_prefs_path = os.path.join(self.tmpdir, "export_prefs.json")
        registry_path = os.path.join(self.tmpdir, "open_windows.json")
        lock_path = os.path.join(self.tmpdir, "primes_session_started.json")
        for target in (
            patch.object(export_prefs, "_prefs_path", return_value=export_prefs_path),
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_primes_session_lock_path", return_value=lock_path),
        ):
            self.addCleanup(target.stop)
            target.start()

        self._n_tournaments = 0
        self._dbs = []

    def _open_new_tournament(self, name, is_new=True):
        """Ouvre un tournoi : soit un fichier flambant neuf (is_new=True,
        reproduisant EXACTEMENT le stamping fait par main.py:
        _choose_tournament_file au moment de la création — la VRAIE
        fonction main._primes_enabled_proposed() est appelée ici, jamais
        mockée ni réimplémentée ; son câblage exact dans le code de
        production est vérifié séparément par tests/test_primes_enabled_
        toggle.py: WiringStructurelTest.test_choose_tournament_file_
        stampe_les_nouveaux_tournois), soit un fichier déjà existant
        (is_new=False, backfill INSERT OR IGNORE -> primes_enabled="1"
        par défaut, comportement de compatibilité inchangé). Enregistre
        aussi la fenêtre dans le registre partagé open_windows (comme le
        ferait App.__init__), pour que le verrouillage de session la
        "voie" correctement."""
        self._n_tournaments += 1
        path = os.path.join(self.tmpdir, f"{name}.tournoi")
        db = database.Database(path)
        self._dbs.append(db)
        self.addCleanup(db.conn.close)
        if is_new:
            db.set_setting("primes_enabled", "1" if main._primes_enabled_proposed() else "0")
        open_windows.register(path)
        self.addCleanup(lambda p=path: open_windows.unregister(p))
        return _SimulatedTournamentWindow(db, name)

    def test_scenario_complet_multi_tournois_avant_et_apres_verrouillage(self):
        # ------------------------------------------------------------
        # 1-2. Ouvre A puis B, dans la même session. Aucun démarré.
        # ------------------------------------------------------------
        A = self._open_new_tournament("A")
        B = self._open_new_tournament("B")

        # ------------------------------------------------------------
        # 3-4. "Calculer les primes" cochée par défaut dans A et B.
        # ------------------------------------------------------------
        self.assertTrue(A.primes_enabled_var.get())
        self.assertTrue(B.primes_enabled_var.get())
        self.assertTrue(A.primes_enabled_in_db())
        self.assertTrue(B.primes_enabled_in_db())
        self.assertFalse(main._primes_session_locked())

        # ------------------------------------------------------------
        # 5. Décoche "Calculer les primes" dans A.
        # ------------------------------------------------------------
        A.click_primes_checkbox(False)
        self.assertFalse(A.primes_enabled_in_db())
        self.assertEqual(A.section_widgets_state(), {"disabled"})
        # B ne doit PAS encore avoir changé : rien ne l'a rafraîchi.
        self.assertTrue(B.primes_enabled_in_db())

        # ------------------------------------------------------------
        # 6. SANS fermer/recharger B, on attend le mécanisme de
        #    synchronisation prévu — un seul "tick" suffit (voir
        #    _sync_primes_enabled_pref, appelée sans condition à chaque
        #    tick de CHAQUE fenêtre ouverte).
        # ------------------------------------------------------------
        B.tick()

        # RÉSULTAT ATTENDU : B a basculé tout seul, case ET section.
        self.assertFalse(B.primes_enabled_var.get(), "B doit refléter la décoche faite depuis A")
        self.assertFalse(B.primes_enabled_in_db())
        self.assertEqual(
            B.section_widgets_state(), {"disabled"},
            "toute la section Primes de B doit devenir inactive/grisée",
        )
        # Aucune divergence entre A et B.
        self.assertEqual(A.primes_enabled_in_db(), B.primes_enabled_in_db())
        self.assertEqual(main._primes_enabled_proposed(), False)

        # ------------------------------------------------------------
        # 7. Crée un tournoi C APRÈS cette désactivation.
        # ------------------------------------------------------------
        C = self._open_new_tournament("C")

        # RÉSULTAT ATTENDU : C créé directement décoché, jamais avec
        # l'ancienne valeur cochée.
        self.assertFalse(C.primes_enabled_var.get())
        self.assertFalse(C.primes_enabled_in_db())

        # ------------------------------------------------------------
        # Test inverse : avant qu'aucun tournoi n'ait démarré, on
        # recoche depuis un des tournois (ici C, pour bien montrer que
        # ça marche aussi depuis le petit dernier, pas seulement A).
        # ------------------------------------------------------------
        self.assertFalse(main._primes_session_locked(), "toujours pas démarré à ce stade")
        C.click_primes_checkbox(True)
        self.assertTrue(C.primes_enabled_in_db())
        # A et B pas encore rafraîchis...
        A.tick()
        B.tick()
        # RÉSULTAT ATTENDU : tous les tournois ouverts repassent à
        # primes activées, sans exception.
        for win in (A, B, C):
            self.assertTrue(win.primes_enabled_var.get(), f"{win.name} doit être recoché")
            self.assertTrue(win.primes_enabled_in_db(), f"{win.name} doit être recoché en base")
            self.assertEqual(win.section_widgets_state(), {"normal"}, f"{win.name} : section réactivée")

        # ------------------------------------------------------------
        # Enfin : démarre le PREMIER tournoi de la session (A).
        # ------------------------------------------------------------
        A.start_tournament()
        self.assertTrue(main._primes_session_locked())

        # RÉSULTAT ATTENDU : la valeur devient verrouillée pour A, B et C.
        for win in (A, B, C):
            win.tick()
            self.assertEqual(
                str(win.primes_enabled_check._state), "disabled",
                f"{win.name} : la case elle-même doit être verrouillée",
            )
            self.assertEqual(
                win.section_widgets_state(), {"disabled"},
                f"{win.name} : toute la section doit rester grisée même si encore cochée",
            )
            self.assertTrue(win.primes_enabled_in_db(), f"{win.name} : valeur figée = cochée")

        # Filet de sécurité : plus aucune fenêtre ne peut changer la
        # valeur globale une fois verrouillé (même en tentant quand même
        # le clic, le garde de _on_primes_enabled_toggle doit refuser).
        B.click_primes_checkbox(False)
        self.assertTrue(
            B.primes_enabled_in_db(),
            "un clic sur une case pourtant verrouillée ne doit rien changer",
        )
        self.assertTrue(B.primes_enabled_var.get(), "la case doit revenir à la valeur réelle")

        # ------------------------------------------------------------
        # Un tournoi D créé ENSUITE hérite bien de cette valeur
        # verrouillée (jamais d'une ancienne valeur périmée).
        # ------------------------------------------------------------
        D = self._open_new_tournament("D")
        self.assertTrue(D.primes_enabled_in_db(), "D doit hériter de la valeur verrouillée (cochée)")
        D.tick()
        self.assertEqual(
            str(D.primes_enabled_check._state), "disabled",
            "D rejoint une session déjà verrouillée : sa case doit l'être aussi",
        )
        self.assertEqual(D.section_widgets_state(), {"disabled"})

    def test_scenario_variante_verrouille_sur_decoche(self):
        """Même scénario, mais la session se verrouille alors que
        « Calculer les primes » est DÉCOCHÉE — un tournoi D créé ensuite
        doit alors hériter de la valeur verrouillée DÉCOCHÉE, pas
        cochée par défaut : preuve que _choose_tournament_file ne
        retombe jamais sur DEFAULT_SETTINGS une fois la session
        verrouillée, mais bien sur la valeur globale figée."""
        A = self._open_new_tournament("A")
        B = self._open_new_tournament("B")
        A.click_primes_checkbox(False)
        B.tick()
        self.assertFalse(B.primes_enabled_in_db())

        A.start_tournament()
        self.assertTrue(main._primes_session_locked())
        B.tick()
        self.assertFalse(B.primes_enabled_in_db(), "figé décoché, pas remis à 1 par erreur")

        D = self._open_new_tournament("D")
        self.assertFalse(
            D.primes_enabled_in_db(),
            "D doit hériter de la valeur verrouillée (décochée), pas du défaut DEFAULT_SETTINGS",
        )


if __name__ == "__main__":
    unittest.main()
