"""Test ciblé de la préférence "Un seul tournoi à la fois" (onglet
Paramètres, demande du 2026-09-07 — deuxième passe, ergonomie révisée
après test manuel) : quand elle est active, grise "Nouveau tournoi" /
"Sit & Go rapide" / "Ouvrir un tournoi existant" sur l'écran "Bienvenue"
(_choose_tournament_file) dès qu'un tournoi est déjà ouvert AILLEURS,
et les réactive dès qu'il n'y en a plus — sans jamais fermer/modifier un
tournoi déjà ouvert, sans jamais toucher au simple retour au premier
plan d'un tournoi déjà actif (Lobby), et sans rien changer au
comportement multi-tournoi quand la préférence est désactivée.

Architecture définitive (3e passe, 2026-09-07 — "Menu principal" doit
TOUJOURS pouvoir s'ouvrir) :
1. _open_new_window (🏠 Menu principal) : TOUJOURS lancée, jamais
   bloquée par _block_second_tournament_if_needed — "Menu principal"
   n'est pas lui-même un second tournoi, juste un écran d'accueil (voir
   OpenNewWindowAlwaysOpensTest).
2. Interface (première protection) : _refresh_launch_buttons_state —
   grise/réactive les 3 boutons de création de cet écran "Bienvenue"
   (_choose_tournament_file), se reprogramme elle-même (win.after, 1s)
   tant que cette fenêtre reste affichée : détecte donc en direct
   l'ouverture/fermeture d'un tournoi ailleurs ET un changement de la
   préférence pendant que cet écran reste ouvert, sans dépendre d'un
   tick externe (App._tick n'existe pas encore à ce stade, voir
   App.__init__).
3. Backend (deuxième protection, filet de sécurité) : _block_second_
   tournament_if_needed, appelée au tout début de new_tournament/
   new_sng/open_tournament (voir _choose_tournament_file) ET de
   LobbyDialog._open_selected (jamais avant le retour au premier plan
   d'un tournoi déjà ouvert, préservé) — vérifié structurellement
   ci-dessous (NoBypassPathExistsTest) : la vérification y est la toute
   première instruction (avant tout sélecteur de fichier ou effet de
   bord), pour bloquer même un appel qui contournerait l'état grisé de
   la couche 2.
4. Synchronisation multi-process de la case Paramètres :
   App._sync_single_tournament_pref_checkbox, appelée depuis _tick
   (1x/s, comme _check_phone_selected_pid) — un tournoi A déjà ouvert
   relit désormais la valeur GLOBALE réelle en continu, au lieu de ne
   la lire qu'une fois à son propre démarrage (voir
   PreferenceSyncAcrossProcessesTest).

Préférence GLOBALE stockée via export_prefs (~/.poker_tournament/
export_prefs.json), même mécanisme déjà utilisé pour "Son prochain
changement Blindes" (voir _next_blinds_sound_lead_seconds) — jamais
d'accès au vrai fichier ici, toutes les lectures/écritures export_prefs
sont mockées (même principe que tests/test_next_blinds_sound.py)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import tkinter as tk
from tkinter import ttk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import export_prefs  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _load_value_default_passthrough(key, default=None):
    """Simule export_prefs.load_value quand rien n'a encore été
    enregistré : renvoie toujours `default`, comme le vrai module."""
    return default


class _FakeApp:
    """Doublure de App : ne reprend que ce que _open_new_window et
    _on_single_tournament_toggle lisent ou appellent."""

    def __init__(self, db_path="/tmp/moi.tournoi"):
        self.db = _FakeDb(db_path) if db_path else None
        self.single_tournament_var = _FakeVar(True)
        self.messagebox_calls = []
        # Unicité de "Menu principal" (voir tests/test_menu_principal_
        # single_instance.py pour son comportement propre) : ici, aucun
        # Menu principal jamais lancé depuis cette doublure.
        self._menu_principal_proc = None

    def winfo_exists(self):
        return True


class _FakeDb:
    def __init__(self, path):
        self.path = path


class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeProc:
    def __init__(self, pid=54321):
        self.pid = pid

    def poll(self):
        return None


class _FakeLobby:
    """Doublure de LobbyDialog : voir tests/test_lobby_double_open_guard.py
    pour le même principe, complétée ici avec self.master (le tournoi
    qui a ouvert ce Lobby)."""

    def __init__(self, path, master_db_path="/tmp/moi.tournoi"):
        self._path = path
        self._launching_paths = set()
        self.after_calls = []
        self.master = _FakeApp(master_db_path)

    def _selected_path(self):
        return self._path

    def winfo_exists(self):
        return True

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return "fake_after_id"

    def _clear_launch_guard_when_resolved(self, path, proc, attempt=0):
        main.LobbyDialog._clear_launch_guard_when_resolved(self, path, proc, attempt)


# ---------------------------------------------------------------------
# 1. Valeur par défaut = activée. 2. Persistance.
# ---------------------------------------------------------------------
class DefaultAndPersistenceTest(unittest.TestCase):
    def test_1_valeur_par_defaut_activee(self):
        with patch.object(main.export_prefs, "load_value", side_effect=_load_value_default_passthrough):
            self.assertTrue(main._single_tournament_pref_enabled())

    def test_2_valeur_enregistree_est_relue_correctement(self):
        with patch.object(main.export_prefs, "load_value", return_value=False):
            self.assertFalse(main._single_tournament_pref_enabled())
        with patch.object(main.export_prefs, "load_value", return_value=True):
            self.assertTrue(main._single_tournament_pref_enabled())

    def test_2_bis_le_toggle_enregistre_bien_sous_la_meme_cle(self):
        fake = _FakeApp()
        fake.single_tournament_var = _FakeVar(False)
        with patch.object(main.export_prefs, "save_value") as mock_save:
            main.App._on_single_tournament_toggle(fake)
        mock_save.assert_called_once_with(main.SINGLE_TOURNAMENT_PREF_KEY, False)


# ---------------------------------------------------------------------
# 3, 4, 6, 8 : _other_tournament_is_open / _block_second_tournament_if_needed
#
# AUCUNE exclusion de "own_path" ici (bug corrigé le 2026-09-07, voir la
# docstring de _other_tournament_is_open) : la question posée est
# toujours "existe-t-il au moins UN tournoi ouvert", jamais "un AUTRE
# que moi" — donc `list_open_paths()` renvoyant ne serait-ce qu'UNE
# entrée (même si cette entrée EST celle de l'appelant) suffit à
# bloquer quand l'option est cochée.
# ---------------------------------------------------------------------
class BlockSecondTournamentTest(unittest.TestCase):
    def test_3_aucun_tournoi_ouvert_ouverture_autorisee(self):
        with patch.object(main.open_windows, "list_open_paths", return_value=[]), \
             patch.object(main.export_prefs, "load_value", return_value=True), \
             patch.object(main.messagebox, "showinfo") as mock_info:
            blocked = main._block_second_tournament_if_needed(None)
        self.assertFalse(blocked)
        mock_info.assert_not_called()

    def test_4_un_tournoi_deja_ouvert_et_option_activee_bloque(self):
        with patch.object(
            main.open_windows, "list_open_paths", return_value=["/tmp/moi.tournoi"],
        ), patch.object(main.export_prefs, "load_value", return_value=True), \
           patch.object(main.messagebox, "showinfo") as mock_info:
            blocked = main._block_second_tournament_if_needed(None)
        self.assertTrue(blocked)
        mock_info.assert_called_once()
        # Le message doit être clair, comme demandé.
        args, kwargs = mock_info.call_args
        message = args[1] if len(args) > 1 else kwargs.get("message", "")
        self.assertIn("déjà ouvert", message)
        self.assertIn("Un seul tournoi à la fois", message)

    def test_6_option_desactivee_multi_tournoi_autorise(self):
        with patch.object(
            main.open_windows, "list_open_paths",
            return_value=["/tmp/moi.tournoi", "/tmp/autre.tournoi"],
        ), patch.object(main.export_prefs, "load_value", return_value=False), \
           patch.object(main.messagebox, "showinfo") as mock_info:
            blocked = main._block_second_tournament_if_needed(None)
        self.assertFalse(blocked)
        mock_info.assert_not_called()

    def test_8_pid_mort_dans_le_registre_ne_bloque_pas_a_tort(self):
        """open_windows.list_open_paths() prune déjà les PID morts en
        interne (voir _prune) : un registre dont l'unique entrée avait
        un PID mort ressort donc vide d'ici — _other_tournament_is_open
        ne doit pas réinventer une détection de vivacité, lui faire
        confiance entièrement."""
        with patch.object(
            main.open_windows, "list_open_paths", return_value=[],
        ), patch.object(main.export_prefs, "load_value", return_value=True), \
           patch.object(main.messagebox, "showinfo") as mock_info:
            blocked = main._block_second_tournament_if_needed(None)
        self.assertFalse(blocked)
        mock_info.assert_not_called()

    def test_son_propre_tournoi_compte_deja_comme_ouvert(self):
        """Le coeur du correctif du 2026-09-07 : un registre contenant
        UNIQUEMENT le tournoi de l'appelant lui-même (ex : le tout
        dernier tournoi restant après avoir fermé les autres) doit
        quand même bloquer — jamais d'exclusion de "soi-même"."""
        with patch.object(
            main.open_windows, "list_open_paths", return_value=["/tmp/mon_seul_tournoi.tournoi"],
        ), patch.object(main.export_prefs, "load_value", return_value=True), \
           patch.object(main.messagebox, "showinfo") as mock_info:
            blocked = main._block_second_tournament_if_needed(None)
        self.assertTrue(blocked)
        mock_info.assert_called_once()


# ---------------------------------------------------------------------
# _open_new_window (🏠 Menu principal) : demande du 2026-09-07 (3e
# passe) — TOUJOURS lancée, quel que soit l'état de la préférence ou du
# registre. "Menu principal" n'est pas lui-même un second tournoi ; la
# protection se fait entièrement à l'intérieur de l'écran "Bienvenue"
# qu'il affiche (boutons grisés + garde backend, voir NoBypassPathExistsTest
# et LaunchButtonsStateTest).
# ---------------------------------------------------------------------
class OpenNewWindowAlwaysOpensTest(unittest.TestCase):
    def test_jamais_bloque_meme_option_activee_et_tournoi_ouvert(self):
        fake = _FakeApp()
        with patch.object(main, "_block_second_tournament_if_needed") as mock_block, \
             patch.object(main.export_prefs, "load_value", return_value=True), \
             patch.object(main.open_windows, "list_open_paths", return_value=["/tmp/moi.tournoi"]), \
             patch.object(main, "spawn_app_process", return_value=_FakeProc()) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)
        mock_block.assert_not_called()
        mock_spawn.assert_called_once()
        mock_raise.assert_called_once()

    def test_non_regression_spawn_normal_quand_option_desactivee(self):
        fake = _FakeApp()
        with patch.object(main.export_prefs, "load_value", return_value=False), \
             patch.object(main, "spawn_app_process", return_value=_FakeProc()) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(fake)
        mock_spawn.assert_called_once()
        mock_raise.assert_called_once()


# ---------------------------------------------------------------------
# 7 : clic sur le tournoi déjà ouvert -> jamais bloqué, jamais un
# second process, juste le premier plan (comportement actuel préservé).
# ---------------------------------------------------------------------
class LobbySwitchToAlreadyOpenNeverBlockedTest(unittest.TestCase):
    def test_7_tournoi_deja_ouvert_ramene_au_premier_plan_sans_verification(self):
        fake = _FakeLobby("/tmp/deja_ouvert.tournoi")
        # spawn_app_process/raise_process_when_ready mockés avec une
        # doublure inoffensive même si le test attend qu'ils ne soient
        # PAS appelés — voir le commentaire équivalent dans
        # OpenNewWindowBlockingTest.test_4_bis ci-dessus.
        with patch.object(main.open_windows, "find_open_pid", return_value=777), \
             patch.object(main.open_windows, "bring_pid_to_front") as mock_bring, \
             patch.object(main, "_block_second_tournament_if_needed") as mock_block, \
             patch.object(main, "spawn_app_process", return_value=_FakeProc()) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise:
            main.LobbyDialog._open_selected(fake)
        mock_bring.assert_called_once_with(777)
        mock_spawn.assert_not_called()
        mock_raise.assert_not_called()
        # Le court-circuit "déjà ouvert" doit précéder toute vérification
        # de la préférence : jamais bloqué, jamais même consulté.
        mock_block.assert_not_called()

    def test_tournoi_pas_encore_ouvert_est_bien_soumis_au_blocage(self):
        """Non-régression : le court-circuit ci-dessus ne doit jouer que
        pour un tournoi VRAIMENT déjà ouvert — un chemin pas encore
        ouvert reste soumis à la préférence."""
        fake = _FakeLobby("/tmp/pas_encore_ouvert.tournoi")
        with patch.object(main.open_windows, "find_open_pid", return_value=None), \
             patch.object(main, "_block_second_tournament_if_needed", return_value=True) as mock_block, \
             patch.object(main, "spawn_app_process", return_value=_FakeProc()) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise:
            main.LobbyDialog._open_selected(fake)
        mock_block.assert_called_once_with(fake)
        mock_spawn.assert_not_called()
        mock_raise.assert_not_called()


# ---------------------------------------------------------------------
# Preuve structurelle : les 4 SEULES fonctions qui déterminent quel
# tournoi ouvrir (new_tournament/new_sng/open_tournament : que faire de
# result["path"] avant tout spawn ; _open_selected : quel tournoi
# rejoindre/lancer depuis le Lobby) vérifient bien le garde backend,
# en tout premier — avant le moindre sélecteur de fichier ou effet de
# bord — pour new_tournament/new_sng/open_tournament.
# ---------------------------------------------------------------------
class NoBypassPathExistsTest(unittest.TestCase):
    def _parse_main(self):
        import ast

        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            return ast.parse(f.read(), filename=main_py)

    def _find_function(self, tree, name):
        import ast

        matches = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name]
        self.assertEqual(len(matches), 1, f"{name} introuvable ou nom ambigu dans main.py")
        return matches[0]

    def test_open_new_window_n_appelle_jamais_le_garde(self):
        """Demande du 2026-09-07 (3e passe) : "Menu principal" doit
        TOUJOURS pouvoir s'ouvrir, y compris préférence activée et
        tournoi déjà ouvert — _open_new_window ne doit donc plus jamais
        appeler _block_second_tournament_if_needed. La protection est
        entièrement dans l'écran "Bienvenue" qu'il affiche (boutons
        grisés + leur propre garde, voir les tests suivants)."""
        import ast

        tree = self._parse_main()
        func = self._find_function(tree, "_open_new_window")
        calls_block = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_block_second_tournament_if_needed"
            for n in ast.walk(func)
        )
        self.assertFalse(calls_block)

    def test_les_trois_commandes_de_bienvenue_verifient_le_garde_en_tout_premier(self):
        """new_tournament/new_sng/open_tournament (voir _choose_
        tournament_file) : la toute première instruction de chacune doit
        être `if _block_second_tournament_if_needed(...): return` — pas
        seulement "quelque part dans le corps" : aucun sélecteur de
        fichier ni effet de bord ne doit pouvoir s'exécuter avant."""
        import ast

        tree = self._parse_main()
        for name in ("new_tournament", "new_sng", "open_tournament"):
            func = self._find_function(tree, name)
            first = func.body[0]
            self.assertIsInstance(first, ast.If, f"{name} : la garde n'est pas la 1ère instruction")
            self.assertIsInstance(first.test, ast.Call, f"{name} : condition inattendue")
            self.assertIsInstance(first.test.func, ast.Name)
            self.assertEqual(first.test.func.id, "_block_second_tournament_if_needed")
            self.assertTrue(
                any(isinstance(s, ast.Return) for s in first.body),
                f"{name} : la garde ne provoque pas de `return` immédiat",
            )

    def test_open_selected_appelle_toujours_le_garde(self):
        """LobbyDialog._open_selected : inchangé par cette ergonomie
        révisée — le garde reste appelé (après le court-circuit "déjà
        ouvert", voir LobbySwitchToAlreadyOpenNeverBlockedTest)."""
        import ast

        tree = self._parse_main()
        func = self._find_function(tree, "_open_selected")
        calls_block = any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_block_second_tournament_if_needed"
            for n in ast.walk(func)
        )
        self.assertTrue(calls_block)

    def test_spawn_app_process_a_toujours_exactement_deux_appelants(self):
        import ast

        tree = self._parse_main()

        def calls_spawn(node):
            return any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "spawn_app_process"
                for n in ast.walk(node)
            )

        functions_calling_spawn = {
            node.name for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and calls_spawn(node)
        }
        self.assertEqual(
            functions_calling_spawn, {"_open_new_window", "_open_selected"},
            "Un nouvel appel à spawn_app_process() a été ajouté ailleurs — vérifier "
            "qu'il reste couvert (garde backend et/ou interface grisée).",
        )


# ---------------------------------------------------------------------
# _refresh_launch_buttons_state : la couche interface (Menu principal).
# Widgets RÉELS (ttk.Button), pas de doublure — c'est justement l'état
# .cget("state") qui est testé.
# ---------------------------------------------------------------------
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LaunchButtonsStateTest(unittest.TestCase):
    # Une seule racine Tk RÉELLE partagée par toute la classe (pas une
    # par test) : réduit la création/destruction de vraies fenêtres à un
    # strict minimum — une racine Tk implique la création/destruction
    # d'un interpréteur Tcl complet, plus coûteux et plus sujet à la
    # race connue "Tcl_AsyncDelete: async handler deleted by the wrong
    # thread" (finalisation Tcl par le ramasse-miettes Python depuis un
    # autre thread, présente ailleurs dans cette suite indépendamment de
    # ce fichier — voir l'historique de session) que le simple Toplevel/
    # boutons ci-dessous, eux bien recréés à chaque test pour une
    # isolation complète entre tests.
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py) : force gc.collect() sur
        # le thread principal après destroy().
        cleanup_tk(cls, "root")

    def setUp(self):
        self.win = tk.Toplevel(self.root)
        self.buttons = [ttk.Button(self.win) for _ in range(3)]

    def tearDown(self):
        # cleanup_tk (voir tests/_tk_cleanup.py) : gère déjà le cas
        # "fenêtre déjà détruite" (test_ne_se_reprogramme_pas_si_la_
        # fenetre_est_deja_detruite) via son propre garde-fou interne.
        cleanup_tk(self, "win", "buttons")

    def _states(self):
        return [str(b.cget("state")) for b in self.buttons]

    def test_1_option_cochee_et_aucun_tournoi_ouvert_boutons_actifs(self):
        with patch.object(main, "_single_tournament_pref_enabled", return_value=True), \
             patch.object(main, "_other_tournament_is_open", return_value=False), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["normal", "normal", "normal"])

    def test_2_option_cochee_et_un_tournoi_vivant_boutons_desactives(self):
        with patch.object(main, "_single_tournament_pref_enabled", return_value=True), \
             patch.object(main, "_other_tournament_is_open", return_value=True), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["disabled", "disabled", "disabled"])

    def test_3_fermeture_du_dernier_tournoi_reactive_au_sondage_suivant(self):
        """Simule deux passages successifs de la fonction (comme le
        ferait win.after en conditions réelles, ici appelés à la main
        pour ne pas dépendre d'un vrai minuteur d'1 s dans les tests) :
        un tournoi ouvert d'abord, puis plus aucun."""
        with patch.object(main, "_single_tournament_pref_enabled", return_value=True), \
             patch.object(main, "_other_tournament_is_open", return_value=True), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["disabled", "disabled", "disabled"])

        with patch.object(main, "_single_tournament_pref_enabled", return_value=True), \
             patch.object(main, "_other_tournament_is_open", return_value=False), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["normal", "normal", "normal"])

    def test_4_pid_mort_dans_open_windows_boutons_actifs(self):
        """_other_tournament_is_open s'appuie sur open_windows.
        list_open_paths(), qui ignore déjà les PID morts (_prune) — un
        registre contenant une entrée à PID mort doit donc se comporter
        exactement comme un registre vide ici."""
        with patch.object(main, "_single_tournament_pref_enabled", return_value=True), \
             patch.object(main.open_windows, "list_open_paths", return_value=[]), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["normal", "normal", "normal"])

    def test_5_option_decochee_et_tournois_ouverts_boutons_actifs(self):
        with patch.object(main, "_single_tournament_pref_enabled", return_value=False), \
             patch.object(main, "_other_tournament_is_open", return_value=True), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["normal", "normal", "normal"])

    def test_6_changement_de_preference_pendant_l_affichage_est_pris_en_compte(self):
        """Deux passages successifs (voir test_3) : seule la préférence
        change entre les deux, l'état enregistré change bien avec elle —
        sans jamais avoir besoin de fermer/rouvrir cette fenêtre."""
        with patch.object(main, "_single_tournament_pref_enabled", return_value=True), \
             patch.object(main, "_other_tournament_is_open", return_value=True), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["disabled", "disabled", "disabled"])

        with patch.object(main, "_single_tournament_pref_enabled", return_value=False), \
             patch.object(main, "_other_tournament_is_open", return_value=True), \
             patch.object(self.win, "after"):
            main._refresh_launch_buttons_state(self.win, self.buttons)
        self.assertEqual(self._states(), ["normal", "normal", "normal"])

    def test_se_reprogramme_elle_meme_tant_que_la_fenetre_existe(self):
        with patch.object(main, "_single_tournament_pref_enabled", return_value=False), \
             patch.object(main, "_other_tournament_is_open", return_value=False), \
             patch.object(self.win, "after") as mock_after:
            main._refresh_launch_buttons_state(self.win, self.buttons)
        mock_after.assert_called_once()
        self.assertEqual(mock_after.call_args[0][0], 1000)

    def test_ne_se_reprogramme_pas_si_la_fenetre_est_deja_detruite(self):
        self.win.destroy()
        with patch.object(main, "_single_tournament_pref_enabled") as mock_pref:
            main._refresh_launch_buttons_state(self.win, self.buttons)
        mock_pref.assert_not_called()


# ---------------------------------------------------------------------
# Reproduction EXACTE du scénario manuel du 2026-09-07 (deuxième
# rapport) : option décochée, 2 tournois ouverts, option cochée SANS
# rien fermer, puis fermetures successives — l'état des 3 commandes (et
# du garde backend) doit être recalculé à chaque étape à partir du
# registre RÉEL actuel, jamais du nombre de tournois ouverts au moment
# où la préférence a été cochée.
#
# `_open_paths` : registre partagé simulé, mutable au fil du scénario
# (open_windows.list_open_paths mocké avec side_effect=lambda: list
# (_open_paths) — une COPIE à chaque appel, comme le ferait le vrai
# fichier JSON relu à chaque fois) ; `_pref_enabled` : préférence
# simulée, également mutable en cours de scénario.
# ---------------------------------------------------------------------
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ManualScenarioReproductionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py) : force gc.collect() sur
        # le thread principal après destroy().
        cleanup_tk(cls, "root")

    def setUp(self):
        self._open_paths = []
        self._pref_enabled = False
        self.win = tk.Toplevel(self.root)
        self.buttons = [ttk.Button(self.win) for _ in range(3)]
        patcher1 = patch.object(
            main.open_windows, "list_open_paths", side_effect=lambda: list(self._open_paths)
        )
        patcher2 = patch.object(
            main.export_prefs, "load_value",
            side_effect=lambda key, default=None: self._pref_enabled,
        )
        patcher3 = patch.object(self.win, "after")
        patcher4 = patch.object(main.messagebox, "showinfo")
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        self.addCleanup(patcher3.stop)
        self.addCleanup(patcher4.stop)
        patcher1.start()
        patcher2.start()
        patcher3.start()
        self.mock_showinfo = patcher4.start()

    def tearDown(self):
        # cleanup_tk (voir tests/_tk_cleanup.py) : force gc.collect() sur
        # le thread principal après destroy().
        cleanup_tk(self, "win", "buttons")

    def _refresh(self):
        """Simule "je reviens/j'affiche le Menu principal" : appelle
        explicitement le mécanisme de rafraîchissement, exactement comme
        _choose_tournament_file le fait à chaque fois qu'il construit
        cet écran."""
        main._refresh_launch_buttons_state(self.win, self.buttons)

    def _states(self):
        return [str(b.cget("state")) for b in self.buttons]

    def test_scenario_manuel_complet(self):
        # 1) Option décochée, 2) j'ouvre 2 tournois.
        self._pref_enabled = False
        self._open_paths = ["/tmp/tournoi_A.tournoi", "/tmp/tournoi_B.tournoi"]
        self._refresh()
        self.assertEqual(self._states(), ["normal"] * 3, "option décochée + 2 tournois -> actifs")
        self.assertFalse(main._block_second_tournament_if_needed(self.win))

        # 3) Je coche "Un seul tournoi à la fois" SANS rien fermer.
        self._pref_enabled = True
        self._refresh()
        self.assertEqual(self._states(), ["disabled"] * 3, "option cochée, 2 tournois encore ouverts -> gris")
        self.assertTrue(main._block_second_tournament_if_needed(self.win))

        # 4) Je ferme le tournoi en cours (A) -> 5) il reste 1 tournoi (B).
        self._open_paths = ["/tmp/tournoi_B.tournoi"]
        # 6) Je reviens au Menu principal : le rafraîchissement doit être
        # rappelé explicitement ici, pas réutiliser un état mémorisé.
        self._refresh()
        self.assertEqual(
            self._states(), ["disabled"] * 3,
            "1 seul tournoi restant (même si ce n'est plus celui d'avant) -> toujours gris",
        )
        self.assertTrue(
            main._block_second_tournament_if_needed(self.win),
            "le garde backend doit refuser un lancement tant qu'il reste 1 tournoi vivant",
        )

        # Fermeture du dernier tournoi (B) -> 0 tournoi vivant.
        self._open_paths = []
        self._refresh()
        self.assertEqual(self._states(), ["normal"] * 3, "plus aucun tournoi ouvert -> réactivés")
        self.assertFalse(main._block_second_tournament_if_needed(self.win))

    def test_pid_mort_au_milieu_du_scenario_ne_compte_pas(self):
        """À n'importe quelle étape, une entrée dont le process a
        disparu ne doit jamais maintenir les boutons grisés à tort —
        open_windows.list_open_paths() l'a déjà retirée (_prune), donc
        simulée ici simplement absente de _open_paths."""
        self._pref_enabled = True
        self._open_paths = []  # l'unique tournoi qui existait a un PID mort : déjà pruné
        self._refresh()
        self.assertEqual(self._states(), ["normal"] * 3)
        self.assertFalse(main._block_second_tournament_if_needed(self.win))

    def test_retour_repete_au_menu_principal_recalcule_a_chaque_fois(self):
        """"Peu importe l'historique précédent" : plusieurs allers-retours
        (ouverture/fermeture) doivent chacun donner le bon état, jamais
        un état qui "resterait collé" au passage précédent."""
        self._pref_enabled = True
        for open_paths, expected in [
            ([], "normal"),
            (["/tmp/x.tournoi"], "disabled"),
            (["/tmp/x.tournoi", "/tmp/y.tournoi"], "disabled"),
            ([], "normal"),
            (["/tmp/z.tournoi"], "disabled"),
        ]:
            self._open_paths = open_paths
            self._refresh()
            self.assertEqual(self._states(), [expected] * 3, f"pour open_paths={open_paths}")


# ---------------------------------------------------------------------
# _sync_single_tournament_pref_checkbox : lue en isolation, avant le
# scénario multi-process complet ci-dessous.
# ---------------------------------------------------------------------
class SyncCheckboxTest(unittest.TestCase):
    def test_case_decochee_devient_cochee_si_la_valeur_globale_change(self):
        fake = _FakeApp()
        fake.single_tournament_var = _FakeVar(False)
        with patch.object(main.export_prefs, "load_value", return_value=True):
            main.App._sync_single_tournament_pref_checkbox(fake)
        self.assertTrue(fake.single_tournament_var.get())

    def test_case_cochee_devient_decochee_si_la_valeur_globale_change(self):
        fake = _FakeApp()
        fake.single_tournament_var = _FakeVar(True)
        with patch.object(main.export_prefs, "load_value", return_value=False):
            main.App._sync_single_tournament_pref_checkbox(fake)
        self.assertFalse(fake.single_tournament_var.get())

    def test_ne_touche_pas_la_variable_si_deja_a_jour(self):
        """Évite tout scintillement inutile du widget à chaque tick
        (1x/s) quand rien n'a changé."""
        fake = _FakeApp()
        real_var = _FakeVar(True)
        calls = []
        real_var.set = lambda v: calls.append(v)  # espionne sans casser .get()
        fake.single_tournament_var = real_var
        with patch.object(main.export_prefs, "load_value", return_value=True):
            main.App._sync_single_tournament_pref_checkbox(fake)
        self.assertEqual(calls, [])

    def test_ne_declenche_jamais_d_ecriture_meme_quand_la_valeur_change(self):
        """Point de vérification explicite : ce rappel périodique (1x/s,
        potentiellement sur PLUSIEURS process en même temps) est une
        LECTURE pure — il ne doit jamais appeler export_prefs.save_value,
        sous peine de pouvoir réécrire une valeur plus récente écrite par
        un autre process entre-temps (voir ExportPrefsNoStaleOverwriteTest
        pour la garantie côté stockage). Vérifié dans les deux sens
        (valeur qui change ET valeur inchangée)."""
        for load_return in (True, False):
            fake = _FakeApp()
            fake.single_tournament_var = _FakeVar(not load_return)
            with patch.object(main.export_prefs, "load_value", return_value=load_return), \
                 patch.object(main.export_prefs, "save_value") as mock_save:
                main.App._sync_single_tournament_pref_checkbox(fake)
            mock_save.assert_not_called()


# ---------------------------------------------------------------------
# Reproduction EXACTE du scénario multi-process du 2026-09-07 (3e
# rapport) : "Un seul tournoi à la fois" est une préférence GLOBALE,
# une seule valeur dans export_prefs.json — deux tournois A et B,
# simulés ici par deux doublures INDÉPENDANTES (chacune sa propre
# self.single_tournament_var, exactement comme deux process séparés
# auraient chacun la leur en mémoire), partageant le MÊME stockage
# sous-jacent simulé (_store, qui tient lieu de export_prefs.json —
# load_value/save_value mockés pour lire/écrire dedans, jamais le vrai
# fichier)."""
# ---------------------------------------------------------------------
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PreferenceSyncAcrossProcessesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py) : force gc.collect() sur
        # le thread principal après destroy().
        cleanup_tk(cls, "root")

    def setUp(self):
        self._store = {}  # tient lieu de export_prefs.json
        self._open_paths = []  # tient lieu du registre open_windows

        def fake_load(key, default=None):
            return self._store.get(key, default)

        def fake_save(key, value):
            self._store[key] = value

        for target, side_effect in (
            (patch.object(main.export_prefs, "load_value", side_effect=fake_load), None),
            (patch.object(main.export_prefs, "save_value", side_effect=fake_save), None),
            (patch.object(main.open_windows, "list_open_paths", side_effect=lambda: list(self._open_paths)), None),
            (patch.object(main.messagebox, "showinfo"), None),
        ):
            self.addCleanup(target.stop)
            target.start()

        self.win = tk.Toplevel(self.root)
        self.launch_buttons = [ttk.Button(self.win) for _ in range(3)]

    def tearDown(self):
        # cleanup_tk (voir tests/_tk_cleanup.py) : force gc.collect() sur
        # le thread principal après destroy().
        cleanup_tk(self, "win", "launch_buttons")

    def _refresh_menu_principal(self):
        main._refresh_launch_buttons_state(self.win, self.launch_buttons)

    def _button_states(self):
        return [str(b.cget("state")) for b in self.launch_buttons]

    def test_scenario_complet_A_et_B(self):
        # 1) Option décochée (valeur par défaut jamais enregistrée -> False
        # ici, puisque _store est vide et load_value renvoie `default`
        # ci-dessus — mais le vrai défaut applicatif est True : on force
        # explicitement False pour coller au scénario "1. décochée").
        self._store["single_tournament_at_a_time"] = False

        # 2) tournoi A ouvert, 3) tournoi B ouvert (deux doublures
        # indépendantes, chacune avec SA PROPRE variable de case, comme
        # deux vrais process).
        app_a = _FakeApp(db_path="/tmp/A.tournoi")
        app_a.single_tournament_var = _FakeVar(main._single_tournament_pref_enabled())
        app_b = _FakeApp(db_path="/tmp/B.tournoi")
        app_b.single_tournament_var = _FakeVar(main._single_tournament_pref_enabled())
        self._open_paths = ["/tmp/A.tournoi", "/tmp/B.tournoi"]
        self.assertFalse(app_a.single_tournament_var.get())
        self.assertFalse(app_b.single_tournament_var.get())

        # 4) dans B, coche l'option.
        app_b.single_tournament_var.set(True)
        main.App._on_single_tournament_toggle(app_b)
        self.assertTrue(self._store["single_tournament_at_a_time"])

        # 5) A relit/rafraîchit Paramètres (voir _tick) => sa case devient cochée.
        main.App._sync_single_tournament_pref_checkbox(app_a)
        self.assertTrue(app_a.single_tournament_var.get(), "A doit refléter la valeur globale mise à jour par B")

        # 6) fermeture de B. 7) A reste ouvert.
        self._open_paths = ["/tmp/A.tournoi"]

        # 8) depuis A, ouverture de Menu principal => TOUJOURS AUTORISÉE,
        # même préférence cochée et un tournoi (A lui-même) ouvert.
        with patch.object(main, "spawn_app_process", return_value=_FakeProc()) as mock_spawn, \
             patch.object(main, "raise_process_when_ready") as mock_raise, \
             patch.object(main.time, "sleep"):
            main.App._open_new_window(app_a)
        mock_spawn.assert_called_once()
        mock_raise.assert_called_once()

        # 9) Menu principal voit 1 tournoi vivant (A) + option cochée
        # => 10) ses 3 commandes sont grisées.
        self._refresh_menu_principal()
        self.assertEqual(self._button_states(), ["disabled"] * 3)
        # Filet de sécurité backend toujours actif en parallèle.
        self.assertTrue(main._block_second_tournament_if_needed(self.win))

        # 11) fermeture de A => Menu principal détecte 0 tournoi
        # => 12) les 3 commandes redeviennent actives.
        self._open_paths = []
        self._refresh_menu_principal()
        self.assertEqual(self._button_states(), ["normal"] * 3)
        self.assertFalse(main._block_second_tournament_if_needed(self.win))

    def test_scenario_inverse_decocher_dans_un_process(self):
        """Même chose dans l'autre sens : décocher dans un process doit
        se refléter dans l'autre, et le multi-tournoi redevient permis."""
        self._store["single_tournament_at_a_time"] = True
        app_a = _FakeApp(db_path="/tmp/A.tournoi")
        app_a.single_tournament_var = _FakeVar(main._single_tournament_pref_enabled())
        app_b = _FakeApp(db_path="/tmp/B.tournoi")
        app_b.single_tournament_var = _FakeVar(main._single_tournament_pref_enabled())
        self._open_paths = ["/tmp/A.tournoi", "/tmp/B.tournoi"]
        self.assertTrue(app_a.single_tournament_var.get())
        self.assertTrue(app_b.single_tournament_var.get())

        # Décoche dans B.
        app_b.single_tournament_var.set(False)
        main.App._on_single_tournament_toggle(app_b)
        self.assertFalse(self._store["single_tournament_at_a_time"])

        # A relit -> sa case devient décochée.
        main.App._sync_single_tournament_pref_checkbox(app_a)
        self.assertFalse(app_a.single_tournament_var.get())

        # Menu principal (depuis A) autorise maintenant le multi-tournoi.
        self._refresh_menu_principal()
        self.assertEqual(self._button_states(), ["normal"] * 3)
        self.assertFalse(main._block_second_tournament_if_needed(self.win))

    def test_pid_mort_ne_compte_pas_comme_tournoi_vivant(self):
        self._store["single_tournament_at_a_time"] = True
        # Le process de A a disparu : open_windows._prune l'a déjà retiré
        # du registre — simulé ici par son absence de _open_paths.
        self._open_paths = []
        self._refresh_menu_principal()
        self.assertEqual(self._button_states(), ["normal"] * 3)
        self.assertFalse(main._block_second_tournament_if_needed(self.win))

    def test_persistance_apres_redemarrage(self):
        """Un "nouveau process" (valeur jamais encore lue en mémoire,
        voir _FakeApp) doit lire la valeur GLOBALE telle qu'elle a été
        laissée par le précédent, jamais un défaut codé en dur."""
        self._store["single_tournament_at_a_time"] = True
        redémarré = _FakeApp(db_path="/tmp/nouveau_process.tournoi")
        redémarré.single_tournament_var = _FakeVar(main._single_tournament_pref_enabled())
        self.assertTrue(redémarré.single_tournament_var.get())


# ---------------------------------------------------------------------
# export_prefs.py RÉEL (pas mocké ici, contrairement à tout le reste de
# ce fichier) : vérifie que le mécanisme de stockage lui-même protège
# bien contre l'écrasement silencieux d'une valeur globale par une
# écriture concurrente sans rapport — jamais le vrai ~/.poker_tournament/
# export_prefs.json (chemin redirigé vers un fichier temporaire).
# ---------------------------------------------------------------------
class ExportPrefsNoStaleOverwriteTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="poker_export_prefs_test_")
        self.addCleanup(self._tmpdir.cleanup)
        tmp_path = os.path.join(self._tmpdir.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=tmp_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_save_value_sur_une_autre_cle_n_ecrase_pas_single_tournament_at_a_time(self):
        """export_prefs.save_value relit le fichier juste avant d'écrire
        (voir sa docstring) : un appel qui ne concerne QUE
        "un_autre_reglage" ne doit jamais perdre "single_tournament_at_
        a_time" déjà enregistrée juste avant — même en simulant un autre
        process qui vient de l'écrire "entre-temps" (ici : simplement
        avant cet appel-ci, le fichier étant la seule source commune)."""
        export_prefs.save_value(main.SINGLE_TOURNAMENT_PREF_KEY, True)
        export_prefs.save_value("un_autre_reglage_sans_rapport", 42)

        self.assertTrue(export_prefs.load_value(main.SINGLE_TOURNAMENT_PREF_KEY))
        self.assertEqual(export_prefs.load_value("un_autre_reglage_sans_rapport"), 42)

    def test_persistance_reelle_sur_disque(self):
        """Persistance après "redémarrage" : load_value relit vraiment le
        fichier à chaque appel (pas de cache en mémoire entre deux
        appels indépendants)."""
        export_prefs.save_value(main.SINGLE_TOURNAMENT_PREF_KEY, True)
        self.assertTrue(export_prefs.load_value(main.SINGLE_TOURNAMENT_PREF_KEY, False))


if __name__ == "__main__":
    unittest.main()
