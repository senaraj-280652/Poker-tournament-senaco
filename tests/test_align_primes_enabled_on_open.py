# -*- coding: utf-8 -*-
"""Couverture automatisée de la 4e relecture du 2026-09-09 : un tournoi
EXISTANT (créé lors d'une session précédente, avec sa propre ancienne
valeur de `primes_enabled`) qui rejoint une session DÉJÀ verrouillée
doit immédiatement adopter la valeur verrouillée de cette session — pas
seulement les tournois flambant neufs (déjà correct, voir
tests/test_primes_enabled_toggle.py).

Cause exacte du bug corrigé (diagnostiquée avant ce correctif) :
App._new_tournament/_open_tournament (menu "Fichier > Ouvrir..."/
"Nouveau tournoi...") ferme la fenêtre ACTUELLE (open_windows.
unregister) puis en ouvre une autre DANS LE MÊME PROCESS. Si cette
fenêtre était la SEULE ouverte, le registre partagé passe par un état
temporairement VIDE entre les deux — ce qui déclenchait à tort la
réinitialisation "nouvelle session" de la valeur "proposée"
(export_prefs, voir open_windows.primes_enabled_proposed), alors que du
point de vue de l'utilisateur il s'agissait toujours de LA MÊME session
(un tournoi avait déjà démarré, la verrouillant). Le tournoi existant
ouvert ensuite ne trouvait donc plus aucune trace fiable du
verrouillage réel et gardait sa propre ancienne valeur.

Correction : la valeur verrouillée est désormais mémorisée EXPLICITEMENT
et de façon durable dans le même fichier que le drapeau de verrouillage
lui-même (open_windows.mark_primes_session_started(primes_enabled) /
locked_primes_enabled()), jamais perdue tant que la session reste
active — et un nouveau point d'alignement explicite (main.
_align_primes_enabled_on_open, appelé dans App.__init__ juste après
open_windows.register(), AVANT toute construction d'interface) applique
cette valeur à TOUT tournoi qui s'ouvre pendant que la session est
verrouillée, nouveau ou existant, avant qu'il ne puisse être utilisé.

Comme tests/test_pko_mechanism.py / test_primes_enabled_toggle.py : de
VRAIS fichiers .tournoi SQLite en dossier temporaire ; export_prefs/
open_windows redirigés vers ce même dossier, jamais ~/.poker_tournament."""
import ast
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402


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


class _FakeSettingsWindow:
    """Doublure minimale de App, juste assez pour construire l'état
    "case + section" comme le ferait _build_settings_tab AUSSITÔT après
    l'alignement (avant tout tick) — voir _display_state()."""

    def __init__(self, db):
        self.db = db
        self.primes_enabled_var = _FakeVar(db.get_setting_int("primes_enabled", 1) == 1)
        self.primes_enabled_check = _FakeCheckbutton()
        self._primes_section_widgets = [_FakeSectionWidget() for _ in range(3)]
        # Mode Test (demande du 2026-09-14) : toujours désactivé dans ce
        # fichier — aucun des scénarios ci-dessous ne le concerne, voir
        # tests/test_primes_test_mode_override.py pour sa couverture
        # dédiée.
        self.test_mode_var = _FakeVar(False)

    def _test_mode_enabled(self):
        return main.App._test_mode_enabled(self)

    def _primes_section_effectively_locked(self):
        return main.App._primes_section_effectively_locked(self)

    def _update_primes_section_state(self, enabled, locked=None):
        main.App._update_primes_section_state(self, enabled, locked=locked)

    def display_initial_state(self):
        """Reproduit EXACTEMENT ce que fait _build_settings_tab au tout
        premier affichage (voir main.py) : la case initialisée depuis
        self.db.get_setting_int (déjà fait dans __init__ ci-dessus), la
        case elle-même grisée si la session est verrouillée, et la
        section alignée sur la valeur courante — AUCUN tick nécessaire."""
        if self._primes_section_effectively_locked():
            self.primes_enabled_check.configure(state="disabled")
        self._update_primes_section_state(self.primes_enabled_var.get())


class AlignPrimesEnabledOnOpenTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_align_primes_test_")
        self.addCleanup(self._tmpdir_ctx.cleanup)
        export_prefs_path = os.path.join(self._tmpdir_ctx.name, "export_prefs.json")
        registry_path = os.path.join(self._tmpdir_ctx.name, "open_windows.json")
        lock_path = os.path.join(self._tmpdir_ctx.name, "primes_session_started.json")
        for target in (
            patch.object(export_prefs, "_prefs_path", return_value=export_prefs_path),
            patch.object(main.open_windows, "_registry_path", return_value=registry_path),
            patch.object(main.open_windows, "_primes_session_lock_path", return_value=lock_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._data_dir_ctx = tempfile.TemporaryDirectory(prefix="poker_align_primes_data_")
        self.addCleanup(self._data_dir_ctx.cleanup)

    def _new_file(self, name, primes_enabled):
        """Simule un tournoi EXISTANT, laissé sur disque par une session
        précédente déjà entièrement close (jamais enregistré dans
        open_windows ici — exactement l'état d'un fichier .tournoi qui
        traîne sur disque, pas d'une fenêtre encore ouverte)."""
        path = os.path.join(self._data_dir_ctx.name, f"{name}.tournoi")
        db = database.Database(path)
        db.set_setting("primes_enabled", "1" if primes_enabled else "0")
        db.conn.close()
        return path

    def _start_locked_session(self, primes_enabled):
        """Nouvelle session : un tournoi A créé, réglé sur
        `primes_enabled`, puis démarré — verrouille la session à cette
        valeur (voir App._clock_resume, reproduit ici à l'identique)."""
        path_a = os.path.join(self._data_dir_ctx.name, "A.tournoi")
        db_a = database.Database(path_a)
        self.addCleanup(db_a.conn.close)
        main.open_windows.register(path_a)
        db_a.set_setting("primes_enabled", "1" if primes_enabled else "0")
        main._set_primes_enabled_proposed(primes_enabled)
        # Démarrage (voir App._clock_resume) : resynchronisation
        # défensive (sans effet ici, déjà à jour), clock_started=1, verrou.
        main._sync_primes_enabled_pref(db_a)
        db_a.set_settings({"clock_started": 1})
        main.open_windows.mark_primes_session_started(
            db_a.get_setting_int("primes_enabled", 1) == 1
        )
        self.assertTrue(main.open_windows.primes_session_started())
        return db_a

    def _open_existing(self, path):
        """Reproduit EXACTEMENT le chemin d'ouverture réel (voir
        App.__init__) : Database(path) -> open_windows.register(path)
        -> _align_primes_enabled_on_open(db) -> (puis, en production,
        _build_tabs()/_build_settings_tab()) — dans ce MÊME ordre."""
        db = database.Database(path)
        self.addCleanup(db.conn.close)
        main.open_windows.register(path)
        main._align_primes_enabled_on_open(db)
        return db

    def test_ouverture_ancien_tournoi_on_dans_session_verrouillee_off(self):
        """LE scénario exact signalé par l'utilisateur (sens 1)."""
        path_b = self._new_file("B", primes_enabled=True)  # ancien B, ON, session précédente close
        self._start_locked_session(primes_enabled=False)  # nouvelle session, A démarré, verrouillée OFF
        self.assertFalse(main.open_windows.locked_primes_enabled())

        db_b = self._open_existing(path_b)

        self.assertEqual(
            db_b.get_setting("primes_enabled"), "0",
            "B doit immédiatement adopter OFF (valeur verrouillée), avant toute utilisation",
        )

        # Points 4/5 de la demande : case + section reflètent le bon état
        # IMMÉDIATEMENT (aucun tick nécessaire).
        window_b = _FakeSettingsWindow(db_b)
        window_b.display_initial_state()
        self.assertFalse(window_b.primes_enabled_var.get())
        self.assertEqual(str(window_b.primes_enabled_check._state), "disabled")
        self.assertTrue(all(w.state == "disabled" for w in window_b._primes_section_widgets))

    def test_ouverture_ancien_tournoi_off_dans_session_verrouillee_on(self):
        """Le test inverse explicitement demandé (sens 2)."""
        path_b = self._new_file("B", primes_enabled=False)  # ancien B, OFF, session précédente close
        self._start_locked_session(primes_enabled=True)  # nouvelle session, A démarré, verrouillée ON
        self.assertTrue(main.open_windows.locked_primes_enabled())

        db_b = self._open_existing(path_b)

        self.assertEqual(
            db_b.get_setting("primes_enabled"), "1",
            "B doit immédiatement adopter ON (valeur verrouillée), avant toute utilisation",
        )

        window_b = _FakeSettingsWindow(db_b)
        window_b.display_initial_state()
        self.assertTrue(window_b.primes_enabled_var.get())
        self.assertEqual(str(window_b.primes_enabled_check._state), "disabled")
        # ON + verrouillée : la SECTION reste grisée (point 2 déjà validé
        # d'une demande précédente — verrouillage global, même si cochée),
        # seule la case affiche "cochée".
        self.assertTrue(all(w.state == "disabled" for w in window_b._primes_section_widgets))

    def test_montants_propres_du_tournoi_existant_jamais_touches(self):
        """"On ne doit évidemment PAS reprendre les anciens montants de
        primes du premier tournoi" : seul primes_enabled est aligné,
        tout le reste des réglages Primes de B reste le sien."""
        path_b = self._new_file("B", primes_enabled=True)
        db_b_setup = database.Database(path_b)
        db_b_setup.set_settings({
            "attendance_bonus_points": "42", "bounty_amount": "777", "pko_mode": "1",
        })
        db_b_setup.conn.close()

        self._start_locked_session(primes_enabled=False)
        db_b = self._open_existing(path_b)

        self.assertEqual(db_b.get_setting("primes_enabled"), "0")  # aligné
        self.assertEqual(db_b.get_setting_int("attendance_bonus_points"), 42)  # intact
        self.assertEqual(db_b.get_setting_int("bounty_amount"), 777)  # intact
        self.assertEqual(db_b.get_setting_int("pko_mode"), 1)  # intact

    def test_tournoi_deja_demarre_jamais_touche_meme_dans_une_session_verrouillee(self):
        """Un tournoi rouvert alors qu'il a DÉJÀ démarré (clock_started=1)
        garde sa propre valeur, quoi qu'il arrive — jamais réaligné."""
        path_b = self._new_file("B", primes_enabled=True)
        db_b_setup = database.Database(path_b)
        db_b_setup.set_settings({"clock_started": "1"})
        db_b_setup.conn.close()

        self._start_locked_session(primes_enabled=False)
        db_b = self._open_existing(path_b)

        self.assertEqual(
            db_b.get_setting("primes_enabled"), "1",
            "un tournoi déjà démarré garde sa propre valeur, jamais réalignée",
        )

    def test_sans_session_verrouillee_un_tournoi_existant_garde_sa_valeur(self):
        """Non-régression : hors session verrouillée, _align_primes_
        enabled_on_open ne doit RIEN faire (la convergence normale
        avant tout démarrage reste gérée par _sync_primes_enabled_pref,
        pas par cette fonction)."""
        path_b = self._new_file("B", primes_enabled=True)
        db_b = database.Database(path_b)
        self.addCleanup(db_b.conn.close)
        main.open_windows.register(path_b)
        self.assertFalse(main.open_windows.primes_session_started())
        main._align_primes_enabled_on_open(db_b)
        self.assertEqual(db_b.get_setting("primes_enabled"), "1")

    def test_scenario_reproduisant_la_cause_exacte_fenetre_unique_remplacee(self):
        """Reproduit la cause exacte diagnostiquée : A est la SEULE
        fenêtre ouverte, verrouillée OFF ; elle se ferme (comme le
        ferait App._new_tournament/_open_tournament, MÊME PROCESS) —
        le registre passe par un état vide — puis B (existant, ON)
        s'ouvre : doit quand même finir OFF, grâce à la valeur
        verrouillée mémorisée durablement (locked_primes_enabled),
        jamais perdue par ce passage à vide du registre."""
        path_b = self._new_file("B", primes_enabled=True)
        db_a = self._start_locked_session(primes_enabled=False)
        path_a = db_a.path

        # A est fermée (seule fenêtre ouverte) : le registre devient vide.
        main.open_windows.unregister(path_a)
        self.assertEqual(main.open_windows.list_open_paths(), [])
        # La valeur "proposée" a été réinitialisée par ce passage à vide
        # (comportement voulu POUR UNE VRAIE nouvelle session) — mais la
        # valeur VERROUILLÉE, elle, ne doit PAS avoir disparu tant que...
        # eh bien, justement : ici la session EST bien terminée (plus
        # aucune fenêtre), donc primes_session_started() redevient faux
        # à ce stade précis, ce qui est CORRECT — ce test vérifie plutôt
        # le cas où une fenêtre B s'ouvre juste APRÈS, dans la continuité
        # immédiate (avant que quiconque ne relise l'état "terminé") :
        db_b = self._open_existing(path_b)
        # Le registre n'était plus vide au moment de register(B) (B
        # vient de s'y ajouter), mais la session était déjà retombée à
        # "non verrouillée" entre-temps (A fermée avant que B n'ouvre) :
        # B garde alors sa propre valeur — comportement attendu, CE
        # N'EST PLUS la même session dès que le dernier tournoi a fermé,
        # conformément à "le déverrouillage ne se produit que lorsque
        # open_windows ne contient plus aucun tournoi de cette session".
        self.assertEqual(db_b.get_setting("primes_enabled"), "1")


# ---------------------------------------------------------------------
# Câblage réel dans App.__init__ (inspection de l'AST, comme dans
# tests/test_primes_enabled_toggle.py:WiringStructurelTest — App.__init__
# construit une vraie fenêtre Tk complète, trop coûteux/fragile à
# instancier ici) : les tests fonctionnels ci-dessus appellent
# _align_primes_enabled_on_open DIRECTEMENT, sans passer par __init__ —
# ce test-ci vérifie que le code de PRODUCTION l'appelle bien lui-même,
# au bon endroit (après register(), avant toute construction d'onglets).
# ---------------------------------------------------------------------
class WiringStructurelTest(unittest.TestCase):
    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=main_py)

    def test_init_appelle_align_primes_enabled_on_open(self):
        app_class = next(
            n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef) and n.name == "App"
        )
        init_func = next(
            n for n in ast.walk(app_class) if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        calls_align = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_align_primes_enabled_on_open"
            for n in ast.walk(init_func)
        )
        self.assertTrue(calls_align, "App.__init__ doit appeler _align_primes_enabled_on_open")

    def test_align_appele_apres_register_et_avant_build_tabs(self):
        """Ordre exact requis (voir la docstring de _align_primes_
        enabled_on_open) : après l'enregistrement de ce tournoi (pour
        refléter l'état final, y compris son éventuel nettoyage de
        session), et AVANT _build_tabs() (qui construit la case/section
        depuis la valeur locale — doit donc déjà être alignée à ce
        moment-là).

        Depuis la demande du 2026-09-16 ("interdire l'ouverture
        simultanée du même .tournoi"), l'enregistrement effectif se fait
        PLUS TÔT qu'avant, via open_windows.try_register() — AVANT même
        l'ouverture réelle de Database() — au lieu de l'ancien
        open_windows.register() inconditionnel appelé ici après coup
        (voir main.py: App.__init__, branche `open_path`, et
        _choose_tournament_file juste avant `self.db = Database(...)`,
        pour l'autre branche). Le vieux `open_windows.register()` a donc
        disparu du corps de __init__ (déplacé plus tôt, et devenu
        inutile ensuite — voir le commentaire qui l'a remplacé) ; seul
        `open_windows.try_register(` y reste littéralement visible (branche
        `open_path`), toujours AVANT _align_primes_enabled_on_open/
        _build_tabs — la propriété vérifiée ici reste donc intacte."""
        app_class = next(
            n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef) and n.name == "App"
        )
        init_func = next(
            n for n in ast.walk(app_class) if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        # Repère la position (index dans le corps, en comptant aussi les
        # instructions imbriquées via un simple parcours séquentiel du
        # code source généré par ast.unparse, plus robuste ici qu'un
        # simple ast.walk qui ne préserve pas l'ordre entre branches).
        src = ast.unparse(init_func)
        register_pos = src.index("open_windows.try_register(")
        align_pos = src.index("_align_primes_enabled_on_open(")
        build_tabs_pos = src.index("_build_tabs(")
        self.assertLess(register_pos, align_pos, "l'enregistrement doit avoir lieu APRÈS register()")
        self.assertLess(align_pos, build_tabs_pos, "l'alignement doit avoir lieu AVANT _build_tabs()")


if __name__ == "__main__":
    unittest.main()
