# -*- coding: utf-8 -*-
"""Correction du 2026-10-14 (bis) — essai réel sur un poste Windows (HP),
photographie à l'appui : deux fenêtres "Bienvenue"/Menu principal
affichées SIMULTANÉMENT après ce scénario précis :

1. Un nouveau tournoi est ouvert (processus A).
2. Depuis ce tournoi, "🏠 Menu principal" est cliqué : App._open_new_
   window lance un SECOND processus (B) via spawn_app_process(
   internal_menu_child=True) — volontairement EXEMPTÉ du verrou
   d'unicité historique (voir open_windows.py, section "Unicité du Menu
   principal"), donc jamais enregistré nulle part.
3. B reste affiché sur l'écran "sans tournoi" (self.db jamais défini
   dans cette fenêtre).
4. Le tournoi d'origine (A) est fermé par la croix rouge.

Diagnostic exact : A détenait seul le verrou exclusif historique
(menu_principal_<key>.json) depuis son lancement. En se fermant, il le
libère (App._cleanup_for_close). En retombant à son tour sur "sans
tournoi" (App._on_close -> App(start_standalone=True), force_check=
True), il retrouve ce verrou VIDE — B ne l'ayant jamais détenu — et
l'acquiert à son tour : deux fenêtres "Bienvenue" affichées en même
temps, B étant resté invisible à ce verrou à PID unique pendant toute sa
vie. Le correctif du 2026-10-14 (premier du jour, commit f95c61da,
paramètre force_check) ne couvre PAS ce cas : il empêche une MÊME
fenêtre de rester exemptée indéfiniment après avoir perdu son tournoi,
mais ne sait rien d'une fenêtre TIERCE (B) restée invisible.

Correction : open_windows.acquire_standalone_screen (voir sa docstring
dans open_windows.py) ajoute un second registre MULTI-entrées (fichier
séparé menu_principal_screens_<key>.json, jamais confondu avec le
verrou exclusif menu_principal_<key>.json, lui-même INTÉGRALEMENT
inchangé) dans lequel TOUTE fenêtre affichant "sans tournoi" s'inscrit
désormais — y compris les Menu principal volontaires (exclusive=False,
jamais bloqués, voir App._acquire_menu_principal_lock_if_needed). Un
retour forcé vers "sans tournoi" (exclusive=True, force_check=True) ou
un nouveau lancement externe (exclusive=True également) consulte
D'ABORD ce registre et s'efface devant toute fenêtre déjà présente — y
compris une fenêtre volontaire — au lieu d'en construire une seconde.

Structure (même conventions déjà établies dans cette suite, voir
tests/test_acquire_menu_principal_race.py et tests/test_menu_principal_
singleton.py) :
1. RegistryUnitTest — open_windows.acquire_standalone_screen/
   unregister_standalone_screen/list_standalone_screen_pids, fichiers
   réels isolés en dossier temporaire (jamais ~/.poker_tournament) :
   comportement nominal, visibilité croisée volontaire/exclusif,
   compatibilité avec un ancien build (verrou exclusif seul, sans le
   nouveau registre), nettoyage d'un PID mort (récupération après
   plantage), désinscription précise (jamais celle d'un autre pid).
2. RaceConditionTest — atomicité réelle (vrais threads Python + une
   barrière de synchronisation), même idiome que test_acquire_menu_
   principal_race.py : deux tentatives exclusives simultanées (jamais
   deux gagnants), et le scénario mixte volontaire/exclusif simultané
   (jamais deux fenêtres "sans tournoi" acceptées à la fois).
3. HpScenarioReproductionTest — reproduction comportementale EXACTE du
   scénario du HP via App._acquire_menu_principal_lock_if_needed/
   _cleanup_for_close (doublure légère, méthodes réelles attachées,
   jamais de vraie fenêtre Tk — même convention que tests/test_menu_
   principal_singleton.py), plus les cas de non-régression explicitement
   demandés (plusieurs tournois, Lobby, fermeture normale du volontaire,
   PID mort)."""
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import open_windows  # noqa: E402


def _spawn_and_wait_dead_pid():
    """PID GARANTI mort (même technique que tests/test_menu_principal_
    singleton.py et tests/test_acquire_menu_principal_race.py)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"], stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    proc.wait(timeout=5)
    return proc.pid


class _IsolatedRegistryTestCase(unittest.TestCase):
    """Isole les TROIS fichiers impliqués (verrou exclusif, son verrou OS
    de section critique — réutilisé tel quel par le nouveau registre —,
    et le nouveau registre multi-entrées) en dossier temporaire, jamais
    ~/.poker_tournament."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="menu_principal_volontaire_test_")
        self.addCleanup(self._tmp.cleanup)

        def _fake_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.json")

        def _fake_registry_lock_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_{key}.lock")

        def _fake_standalone_screens_path(key):
            return os.path.join(self._tmp.name, f"menu_principal_screens_{key}.json")

        for target, fake in (
            ("_menu_principal_lock_path", _fake_lock_path),
            ("_menu_principal_registry_lock_path", _fake_registry_lock_path),
            ("_standalone_screens_path", _fake_standalone_screens_path),
        ):
            patcher = patch.object(open_windows, target, side_effect=fake)
            patcher.start()
            self.addCleanup(patcher.stop)


# =======================================================================
# 1. open_windows.acquire_standalone_screen — comportement nominal
# =======================================================================
class RegistryUnitTest(_IsolatedRegistryTestCase):

    def test_exclusif_seul_reussit_et_pose_les_deux_fichiers(self):
        result = open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=True)
        self.assertIsNone(result)
        self.assertEqual(open_windows.menu_principal_pid("prod"), os.getpid())
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])

    def test_volontaire_toujours_accepte_meme_sans_rien_dautre(self):
        result = open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=False)
        self.assertIsNone(result)
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])
        # Le volontaire ne touche JAMAIS le verrou exclusif historique.
        self.assertIsNone(open_windows.menu_principal_pid("prod"))

    def test_volontaire_jamais_bloque_meme_si_un_exclusif_existe_deja(self):
        """Non-régression explicite : "🏠 Menu principal" ouvert depuis
        un vrai tournoi doit rester ILLIMITÉ — jamais bloqué par la
        présence d'un écran "sans tournoi" déjà affiché ailleurs."""
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            open_windows.acquire_standalone_screen(111111, "prod", exclusive=True)
            result = open_windows.acquire_standalone_screen(222222, "prod", exclusive=False)
            self.assertIsNone(result)
            self.assertEqual(
                sorted(open_windows.list_standalone_screen_pids("prod")), [111111, 222222],
            )

    def test_exclusif_bloque_par_un_volontaire_deja_present(self):
        """LE cœur du correctif : un Menu principal volontaire, déjà
        enregistré (exclusive=False), doit À LUI SEUL bloquer une
        tentative exclusive ultérieure — exactement le scénario du HP."""
        open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=False)
        other = open_windows.acquire_standalone_screen(os.getpid() + 1, "prod", exclusive=True)
        self.assertEqual(other, os.getpid())
        # Le verrou exclusif historique ne doit JAMAIS avoir été posé
        # pour ce second appel : il a été bloqué AVANT de l'atteindre.
        self.assertIsNone(open_windows.menu_principal_pid("prod"))

    def test_exclusif_bloque_par_un_autre_exclusif_deja_present(self):
        """Non-régression : le cas déjà couvert par acquire_menu_
        principal continue de fonctionner via la nouvelle fonction."""
        open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=True)
        other = open_windows.acquire_standalone_screen(os.getpid() + 1, "prod", exclusive=True)
        self.assertEqual(other, os.getpid())

    def test_compatibilite_ancien_build_verrou_exclusif_seul(self):
        """Un AUTRE processus tournant sur un ancien build (antérieur à
        ce correctif) n'alimente jamais le nouveau registre — seulement
        l'ancien fichier exclusif. Une tentative exclusive doit malgré
        tout le détecter (défense en profondeur, voir la docstring)."""
        open_windows.register_menu_principal(os.getpid(), "prod")
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [])
        result = open_windows.acquire_standalone_screen(os.getpid() + 1, "prod", exclusive=True)
        self.assertEqual(result, os.getpid())

    def test_desinscription_precise_jamais_un_autre_pid(self):
        open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=False)
        open_windows.unregister_standalone_screen(99999999, "prod")
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])
        open_windows.unregister_standalone_screen(os.getpid(), "prod")
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [])

    def test_desinscription_sans_rien_dinscrit_ne_leve_rien(self):
        open_windows.unregister_standalone_screen(os.getpid(), "prod")  # no-op silencieux
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [])

    def test_pid_mort_du_registre_multi_entrees_auto_nettoye(self):
        """Fermeture ANORMALE (plantage) d'un Menu principal volontaire :
        son entrée reste dans le fichier, mais son PID n'existe plus —
        une tentative exclusive ultérieure doit réussir normalement
        (récupération après plantage, même principe que _prune ailleurs
        dans open_windows.py)."""
        dead_pid = _spawn_and_wait_dead_pid()
        open_windows.acquire_standalone_screen(dead_pid, "prod", exclusive=False)
        # L'entrée est bien restée dans le FICHIER (plantage simulé,
        # jamais de unregister_standalone_screen pour ce pid) — vérifié
        # par une lecture BRUTE, sans purge (contrairement à list_
        # standalone_screen_pids, qui purge toujours à la lecture et
        # masquerait donc ici le point précis que ce test vérifie : la
        # purge n'intervient qu'à la PROCHAINE tentative, jamais avant).
        raw = open_windows._load_standalone_screens("prod")
        self.assertEqual(list(raw.keys()), [str(dead_pid)])

        result = open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=True)

        self.assertIsNone(result)
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])

    def test_prod_et_test_totalement_independants(self):
        """pid factice (os.getpid() + 1) pour la ligne "test" : vivacité
        forcée (patch _pid_is_running) — sans quoi ce pid, choisi au
        hasard, pourrait par malchance ne PAS correspondre à un process
        réellement vivant au moment du test (purgé par _prune, faux
        échec intermittent selon ce qui tourne par ailleurs sur la
        machine — jamais un vrai pid "presque sûr" sans ce garde-fou,
        même précaution que tests/test_acquire_menu_principal_race.py)."""
        other_pid = os.getpid() + 1
        with patch.object(open_windows, "_pid_is_running", side_effect=lambda pid: pid in (os.getpid(), other_pid)):
            open_windows.acquire_standalone_screen(os.getpid(), "prod", exclusive=False)
            result = open_windows.acquire_standalone_screen(other_pid, "test", exclusive=True)
            self.assertIsNone(result)
            self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])
            self.assertEqual(open_windows.list_standalone_screen_pids("test"), [other_pid])


# =======================================================================
# 2. Atomicité réelle — vrais threads, même idiome que test_acquire_
#    menu_principal_race.py
# =======================================================================
class RaceConditionTest(_IsolatedRegistryTestCase):

    def test_deux_tentatives_exclusives_simultanees_un_seul_gagnant(self):
        pid_a, pid_b = 100000, 100001
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            for i in range(30):
                path = open_windows._standalone_screens_path("prod")
                if os.path.exists(path):
                    os.remove(path)
                legacy_path = open_windows._menu_principal_lock_path("prod")
                if os.path.exists(legacy_path):
                    os.remove(legacy_path)

                barrier = threading.Barrier(2)
                results = {}

                def call(pid, name):
                    barrier.wait(timeout=5)
                    results[name] = open_windows.acquire_standalone_screen(pid, "prod", exclusive=True)

                t1 = threading.Thread(target=call, args=(pid_a, "a"))
                t2 = threading.Thread(target=call, args=(pid_b, "b"))
                t1.start()
                t2.start()
                t1.join(timeout=5)
                t2.join(timeout=5)

                winners = [name for name, result in results.items() if result is None]
                self.assertEqual(
                    len(winners), 1,
                    f"itération {i} : {len(winners)} gagnant(s) au lieu d'exactement 1 "
                    f"({results!r}) — deux fenêtres 'sans tournoi' auraient coexisté.",
                )
                winning_pid = pid_a if winners[0] == "a" else pid_b
                self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [winning_pid])

    def test_volontaire_et_exclusif_simultanes_jamais_deux_proprietaires_exclusifs(self):
        """Scénario mixte (un Menu principal volontaire s'enregistre
        pendant qu'un retour forcé vers "sans tournoi" tente, au même
        instant, de devenir le seul écran) : la coexistence volontaire +
        exclusif reste légitime (voir test_volontaire_jamais_bloque_
        meme_si_un_exclusif_existe_deja), mais il ne doit JAMAIS y avoir
        deux propriétaires EXCLUSIFS à la fois — garantie testée ici sur
        un grand nombre de répétitions avec de vrais threads."""
        pid_volontaire, pid_exclusif = 200000, 200001
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            for i in range(30):
                path = open_windows._standalone_screens_path("prod")
                if os.path.exists(path):
                    os.remove(path)
                legacy_path = open_windows._menu_principal_lock_path("prod")
                if os.path.exists(legacy_path):
                    os.remove(legacy_path)

                barrier = threading.Barrier(2)
                results = {}

                def call_volontaire():
                    barrier.wait(timeout=5)
                    results["volontaire"] = open_windows.acquire_standalone_screen(
                        pid_volontaire, "prod", exclusive=False,
                    )

                def call_exclusif():
                    barrier.wait(timeout=5)
                    results["exclusif"] = open_windows.acquire_standalone_screen(
                        pid_exclusif, "prod", exclusive=True,
                    )

                t1 = threading.Thread(target=call_volontaire)
                t2 = threading.Thread(target=call_exclusif)
                t1.start()
                t2.start()
                t1.join(timeout=5)
                t2.join(timeout=5)

                # Le volontaire réussit TOUJOURS (jamais bloqué).
                self.assertIsNone(results["volontaire"], f"itération {i}")
                # L'exclusif réussit (None) SEULEMENT s'il a gagné la
                # course avant l'inscription du volontaire — sinon il
                # doit avoir été bloqué par elle (renvoie son pid).
                self.assertIn(results["exclusif"], (None, pid_volontaire), f"itération {i}")
                legacy_pid = open_windows.menu_principal_pid("prod")
                if results["exclusif"] is None:
                    self.assertEqual(legacy_pid, pid_exclusif, f"itération {i}")
                else:
                    self.assertIsNone(legacy_pid, f"itération {i} : verrou exclusif posé à tort")


# =======================================================================
# 3. Reproduction comportementale exacte du scénario HP
# =======================================================================
class _StubWindow:
    """Doublure minimale d'App, même principe que tests/test_menu_
    principal_singleton.py::_StubWindow (méthodes réellement attachées
    via types.MethodType, jamais de vraie fenêtre Tk)."""

    def __init__(self):
        self.after_calls = []
        self.destroyed = False
        self.db = None
        self._holds_menu_principal_lock = False

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return f"after_id_{len(self.after_calls)}"

    def destroy(self):
        self.destroyed = True

    def _stop_remote_control(self):
        pass

    def _stop_whatsapp_share_server(self):
        pass

    def _cancel_tables_blink(self):
        pass

    def _cancel_players_absent_blink(self):
        pass


class HpScenarioReproductionTest(_IsolatedRegistryTestCase):

    def setUp(self):
        super().setUp()
        env_patcher = patch.dict(os.environ, {}, clear=False)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop(main.POKER_TOURNAMENT_INTERNAL_LAUNCH, None)
        self._is_test_build_patcher = patch.object(main, "_is_test_build", return_value=False)
        self._is_test_build_patcher.start()
        self.addCleanup(self._is_test_build_patcher.stop)

    def _make_window(self):
        win = _StubWindow()
        win._acquire_menu_principal_lock_if_needed = types.MethodType(
            main.App._acquire_menu_principal_lock_if_needed, win
        )
        win._cleanup_for_close = types.MethodType(main.App._cleanup_for_close, win)
        return win

    def test_scenario_exact_du_hp_la_seconde_fenetre_est_bloquee(self):
        """1. Tournoi A ouvert (verrou exclusif acquis). 2. "🏠 Menu
        principal" cliqué depuis A -> processus B, marqueur interne
        présent, jamais bloqué. 3. B reste "sans tournoi" (jamais
        enregistré nulle part avant ce correctif). 4. A se ferme (croix
        rouge) -> libère son verrou -> retombe sur "sans tournoi"
        (force_check=True, voir App._on_close) : AVANT le correctif, le
        verrou exclusif vide laissait A ouvrir une DEUXIÈME fenêtre,
        malgré B déjà affiché. Avec le correctif, A doit être bloqué et
        ramené vers B — jamais de doublon."""
        # pid DISTINCT pour B : App._acquire_menu_principal_lock_if_needed
        # utilise toujours os.getpid() en interne (comme pour A, le
        # process de ce test) — inutilisable tel quel pour représenter un
        # VRAI second processus (même limite déjà documentée dans
        # tests/test_menu_principal_singleton.py). On enregistre donc B
        # directement via open_windows.acquire_standalone_screen, avec
        # EXACTEMENT les arguments (exclusive=False) que produirait la
        # branche "marqueur interne présent" de _acquire_menu_principal_
        # lock_if_needed pour un vrai processus B — seule façon de
        # garantir ici deux entrées correspondant à deux pid réellement
        # DIFFÉRENTS, condition nécessaire pour vérifier que la fermeture
        # de A (_cleanup_for_close, qui retire la SIENNE par pid) ne
        # touche jamais à celle de B.
        b_pid = 999001
        a = self._make_window()
        with patch.object(open_windows, "_pid_is_running", side_effect=lambda pid: pid in (os.getpid(), b_pid)):
            self.assertTrue(a._acquire_menu_principal_lock_if_needed(None))
            self.assertTrue(a._holds_menu_principal_lock)

            # "🏠 Menu principal" depuis A -> processus B (marqueur interne,
            # jamais bloqué) -> B reste "sans tournoi" indéfiniment (self.db
            # jamais défini).
            open_windows.acquire_standalone_screen(b_pid, "prod", exclusive=False)
            self.assertEqual(
                sorted(open_windows.list_standalone_screen_pids("prod")), sorted([os.getpid(), b_pid]),
            )

            # A ferme son tournoi : libère son verrou exclusif ET sa PROPRE
            # inscription — jamais celle de B (pid différent).
            a._cleanup_for_close()
            self.assertIsNone(open_windows.menu_principal_pid("prod"))
            self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [b_pid])

            # A retombe sur "sans tournoi" (App._on_close -> force_check=True).
            with patch.object(main, "raise_process_when_ready") as mock_raise:
                result = a._acquire_menu_principal_lock_if_needed(None, force_check=True)

        self.assertFalse(result, "A doit être bloqué : B est déjà affiché sur l'écran sans tournoi")
        mock_raise.assert_called_once_with(a, b_pid)
        self.assertEqual(a.after_calls, [(3500, a.destroy)])
        self.assertFalse(a._holds_menu_principal_lock)
        # Aucun nouveau verrou exclusif n'a été posé par cette tentative
        # bloquée : le fichier reste vide (B ne le détient pas non plus,
        # B étant resté purement volontaire).
        self.assertIsNone(open_windows.menu_principal_pid("prod"))

    def test_sans_menu_principal_volontaire_le_retour_standalone_fonctionne_normalement(self):
        """Non-régression : SANS Menu principal volontaire ouvert, la
        fermeture du dernier tournoi continue de recréer normalement une
        seule fenêtre "sans tournoi" (comportement du 2026-10-14,
        premier du jour, inchangé)."""
        a = self._make_window()
        a._acquire_menu_principal_lock_if_needed(None)
        a._cleanup_for_close()

        with patch.object(main, "raise_process_when_ready") as mock_raise:
            result = a._acquire_menu_principal_lock_if_needed(None, force_check=True)

        self.assertTrue(result)
        self.assertTrue(a._holds_menu_principal_lock)
        mock_raise.assert_not_called()
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])

    def test_fermeture_normale_du_volontaire_libere_sa_place(self):
        """Si le Menu principal volontaire (B) se ferme PROPREMENT
        (_cleanup_for_close) avant que A ne retombe sur "sans tournoi",
        plus rien ne doit le bloquer."""
        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        b = self._make_window()
        b._acquire_menu_principal_lock_if_needed(None)
        del os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH]
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])

        b._cleanup_for_close()
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [])

        a = self._make_window()
        with patch.object(main, "raise_process_when_ready") as mock_raise:
            result = a._acquire_menu_principal_lock_if_needed(None, force_check=True)
        self.assertTrue(result)
        mock_raise.assert_not_called()

    def test_plantage_du_volontaire_najamais_bloque_durablement(self):
        """Fermeture ANORMALE du Menu principal volontaire (plantage,
        jamais de _cleanup_for_close) : son entrée laissée dans le
        registre ne doit jamais bloquer durablement un futur retour vers
        "sans tournoi" — auto-nettoyage via la vivacité du PID."""
        dead_pid = _spawn_and_wait_dead_pid()
        open_windows.acquire_standalone_screen(dead_pid, "prod", exclusive=False)

        a = self._make_window()
        with patch.object(main, "raise_process_when_ready") as mock_raise:
            result = a._acquire_menu_principal_lock_if_needed(None, force_check=True)

        self.assertTrue(result)
        mock_raise.assert_not_called()
        self.assertEqual(open_windows.list_standalone_screen_pids("prod"), [os.getpid()])

    def test_lobby_jamais_affecte_meme_avec_un_volontaire_deja_ouvert(self):
        """Non-régression explicite : le Lobby (open_path fourni) ne
        doit jamais être affecté par ce nouveau registre, qu'un Menu
        principal volontaire soit ouvert ou non."""
        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        b = self._make_window()
        b._acquire_menu_principal_lock_if_needed(None)
        del os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH]

        lobby_window = self._make_window()
        with patch.object(open_windows, "acquire_standalone_screen") as mock_acquire:
            result = lobby_window._acquire_menu_principal_lock_if_needed("/tmp/un_tournoi.tournoi")
        self.assertTrue(result)
        mock_acquire.assert_not_called()

    def test_menu_principal_volontaire_depuis_un_vrai_tournoi_toujours_illimite(self):
        """Non-régression explicite : plusieurs Menu principal
        volontaires successifs depuis un vrai tournoi restent tous
        acceptés, même pendant qu'un écran "sans tournoi" exclusif est
        déjà affiché — comportement "illimité" entièrement préservé."""
        a = self._make_window()
        a._acquire_menu_principal_lock_if_needed(None)

        os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
        try:
            for _ in range(3):
                child = self._make_window()
                self.assertTrue(child._acquire_menu_principal_lock_if_needed(None))
                self.assertFalse(child._holds_menu_principal_lock)
        finally:
            del os.environ[main.POKER_TOURNAMENT_INTERNAL_LAUNCH]


if __name__ == "__main__":
    unittest.main()
