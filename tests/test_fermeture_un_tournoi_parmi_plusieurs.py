# -*- coding: utf-8 -*-
"""Correction du 2026-10-14 (suite à un essai réel sur Mac, deux
tournois ouverts simultanément) : fermer l'un d'eux avec la croix rouge
transformait à tort sa fenêtre en "Senaco sans tournoi", alors que
l'autre tournoi restait ouvert.

Cause diagnostiquée : App._on_close() décidait de revenir à "Senaco sans
tournoi" en se basant sur le verrou d'unicité du Menu principal
(_acquire_menu_principal_lock_if_needed), qui ne reflète JAMAIS le
nombre de tournois ouverts (jamais posé pour un tournoi ouvert via le
Lobby ni pour un "Menu principal" supplémentaire — voir sa docstring).

Correction : open_windows.unregister(path) renvoie désormais True
UNIQUEMENT si ce retrait a réellement vidé le registre partagé (ce
tournoi était bien le DERNIER ouvert, tous processus vivants confondus,
morts déjà purgés par _prune) — False dans tous les autres cas, y
compris un chemin absent du registre ou appartenant à un AUTRE pid
(jamais supposer "c'était le dernier" dans un cas incertain). App.
_cleanup_for_close capture ce résultat (self._was_last_open_tournament),
et App._on_close ne reconstruit App(start_standalone=True) que si cette
capture est vraie.

Structure, comme tests/test_fermeture_tournoi_retour_standalone.py :
1. Tests RÉELS sur open_windows.unregister() (registre isolé sur
   disque, jamais ~/.poker_tournament — même précaution que tests/
   test_primes_enabled_toggle.py) : un seul tournoi, deux tournois,
   fermeture du premier puis du dernier, fermetures simultanées (vrais
   threads concurrents sur le vrai verrou de fichier inter-process).
2. Tests COMPORTEMENTAUX sur App._cleanup_for_close/_on_close (doublure
   légère, méthodes réelles attachées, jamais une vraie fenêtre Tk —
   même convention que tests/test_apply_blinds_button.py) : vérifient
   que self._was_last_open_tournament est posée correctement et que
   _on_close ne reconstruit une App que lorsqu'elle est vraie — y
   compris "Fin de la partie" (iPhone), qui appelle exactement la même
   _on_close.
3. Test de non-régression : le Lobby (list_open_paths) et le contrôle à
   distance (list_remote_tournaments) continuent de fonctionner
   normalement après ce changement."""
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402
import open_windows  # noqa: E402


class _IsolatedRegistryTestCase(unittest.TestCase):
    """Registre open_windows (+ fichiers associés) redirigés vers un
    dossier temporaire dédié — jamais ~/.poker_tournament, le vrai
    registre de l'utilisateur. Même précaution documentée que tests/
    test_primes_enabled_toggle.py/test_remote_control_dir_isolation.py
    (_remote_control_dir en particulier, pour ne jamais effacer le vrai
    remote_control_auth.json de l'utilisateur via le nettoyage "registre
    devenu vide")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="fermeture_multi_tournois_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        lock_path = os.path.join(self._tmp.name, "open_windows.lock")
        primes_lock_path = os.path.join(self._tmp.name, "primes_session_started.json")
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_registry_lock_path", return_value=lock_path),
            patch.object(open_windows, "_primes_session_lock_path", return_value=primes_lock_path),
            patch.object(open_windows, "_remote_control_dir", return_value=self._tmp.name),
        ):
            self.addCleanup(target.stop)
            target.start()


# =====================================================================
# 1. open_windows.unregister() — valeur de retour
# =====================================================================
class UnregisterReturnValueTest(_IsolatedRegistryTestCase):
    def test_un_seul_tournoi_renvoie_true(self):
        open_windows.register("/tmp/A.tournoi")
        self.assertTrue(open_windows.unregister("/tmp/A.tournoi"))
        self.assertEqual(open_windows.list_open_paths(), [])

    def test_deux_tournois_fermer_le_premier_renvoie_false(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        self.assertFalse(open_windows.unregister("/tmp/A.tournoi"))
        self.assertEqual(open_windows.list_open_paths(), [os.path.abspath("/tmp/B.tournoi")])

    def test_deux_tournois_fermer_ensuite_le_dernier_renvoie_true(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        open_windows.unregister("/tmp/A.tournoi")
        self.assertTrue(open_windows.unregister("/tmp/B.tournoi"))
        self.assertEqual(open_windows.list_open_paths(), [])

    def test_trois_tournois_les_deux_premiers_renvoient_false(self):
        for p in ("/tmp/A.tournoi", "/tmp/B.tournoi", "/tmp/C.tournoi"):
            open_windows.register(p)
        self.assertFalse(open_windows.unregister("/tmp/A.tournoi"))
        self.assertFalse(open_windows.unregister("/tmp/B.tournoi"))
        self.assertTrue(open_windows.unregister("/tmp/C.tournoi"))

    def test_chemin_jamais_enregistre_renvoie_false_jamais_true(self):
        """Précaution explicite de l'utilisateur : ne jamais supposer
        "c'était le dernier" si ce chemin n'était pas correctement
        présent dans le registre."""
        self.assertFalse(open_windows.unregister("/tmp/jamais_enregistre.tournoi"))

    def test_chemin_enregistre_par_un_autre_pid_renvoie_false(self):
        """Même précaution : un chemin présent mais appartenant à un
        AUTRE processus (cas anormal — ne devrait jamais arriver en
        usage normal, voir unregister()) ne doit jamais être traité
        comme "retiré par nous", donc jamais True."""
        data = {os.path.abspath("/tmp/A.tournoi"): {"pid": os.getpid() + 9999, "registered_at": 0}}
        with patch.object(open_windows, "_pid_is_running", return_value=True):
            open_windows._save(data)
            self.assertFalse(open_windows.unregister("/tmp/A.tournoi"))
            # L'entrée de l'AUTRE pid doit rester intacte : rien ne doit
            # avoir été retiré du tout.
            self.assertEqual(open_windows.list_open_paths(), [os.path.abspath("/tmp/A.tournoi")])

    def test_path_vide_renvoie_false(self):
        self.assertFalse(open_windows.unregister(""))
        self.assertFalse(open_windows.unregister(None))

    def test_fermetures_simultanees_exactement_une_seule_renvoie_true(self):
        """Deux "fenêtres" (même pid, deux tournois différents — la
        distinction pertinente pour _registry_lock est le CHEMIN, pas le
        pid) fermées par deux threads quasi simultanément : le verrou de
        fichier RÉEL (_registry_lock, inter-processus par construction,
        donc sérialise aussi ces deux threads) garantit qu'exactement un
        seul unregister() voit le registre devenir vide — jamais les
        deux, jamais aucun."""
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")

        results = {}
        barrier = threading.Barrier(2)

        def _close(path, key):
            barrier.wait(timeout=5)
            results[key] = open_windows.unregister(path)

        t1 = threading.Thread(target=_close, args=("/tmp/A.tournoi", "A"))
        t2 = threading.Thread(target=_close, args=("/tmp/B.tournoi", "B"))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        self.assertEqual(set(results.keys()), {"A", "B"})
        self.assertEqual(
            sorted(results.values()), [False, True],
            f"exactement une fermeture doit voir le registre vide, jamais les deux ni aucune : {results}",
        )
        self.assertEqual(open_windows.list_open_paths(), [])


# =====================================================================
# 2. App._cleanup_for_close / App._on_close — comportement réel
# =====================================================================
class _FakeApp:
    """Doublure légère : méthodes réelles de App attachées telles
    quelles, jamais une vraie fenêtre Tk construite ici (même principe
    que tests/test_apply_blinds_button.py::_FakeApp)."""

    _cleanup_for_close = main.App._cleanup_for_close
    _on_close = main.App._on_close
    _remote_end_tournament = main.App._remote_end_tournament

    def __init__(self, db_path):
        self.db = _FakeDb(db_path) if db_path else None
        self._remote_end_tournament_triggered = False
        self.destroy_calls = 0

    def _stop_remote_control(self):
        pass

    def _stop_whatsapp_share_server(self):
        pass

    def _cancel_tables_blink(self):
        pass

    def _cancel_players_absent_blink(self):
        pass

    def destroy(self):
        self.destroy_calls += 1


class _FakeDb:
    def __init__(self, path):
        self.path = path
        self.closed = False

    def close(self):
        self.closed = True

    def __bool__(self):
        return True


class CleanupForCloseCaptureTest(_IsolatedRegistryTestCase):
    def test_un_seul_tournoi_was_last_open_tournament_true(self):
        open_windows.register("/tmp/A.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        app._cleanup_for_close()
        self.assertTrue(app._was_last_open_tournament)
        self.assertTrue(app.db.closed)

    def test_deux_tournois_fermer_le_premier_was_last_open_tournament_false(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        app._cleanup_for_close()
        self.assertFalse(app._was_last_open_tournament)

    def test_sans_tournoi_was_last_open_tournament_false(self):
        app = _FakeApp(None)
        app._cleanup_for_close()
        self.assertFalse(app._was_last_open_tournament)

    def test_chemin_non_enregistre_was_last_open_tournament_false(self):
        """Ce tournoi n'était pas (ou plus) correctement présent dans le
        registre : ne jamais supposer qu'il était le dernier."""
        app = _FakeApp("/tmp/jamais_enregistre.tournoi")
        app._cleanup_for_close()
        self.assertFalse(app._was_last_open_tournament)


class OnCloseBehaviorTest(_IsolatedRegistryTestCase):
    def test_un_seul_tournoi_reconstruit_app_standalone(self):
        open_windows.register("/tmp/A.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        with patch.object(main, "App") as mock_app_cls:
            app._on_close()
        mock_app_cls.assert_called_once_with(start_standalone=True)
        mock_app_cls.return_value.mainloop.assert_called_once()
        self.assertEqual(app.destroy_calls, 1)

    def test_deux_tournois_fermer_le_premier_ne_reconstruit_rien(self):
        """LE scénario exact signalé : deux tournois ouverts, on en
        ferme un — sa fenêtre doit juste se fermer, aucune nouvelle
        App() ne doit apparaître."""
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        with patch.object(main, "App") as mock_app_cls:
            app._on_close()
        mock_app_cls.assert_not_called()
        self.assertEqual(app.destroy_calls, 1)
        # B reste bien seul et intact dans le registre.
        self.assertEqual(open_windows.list_open_paths(), [os.path.abspath("/tmp/B.tournoi")])

    def test_deux_tournois_fermer_ensuite_le_dernier_reconstruit_app_standalone(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        app_a = _FakeApp("/tmp/A.tournoi")
        with patch.object(main, "App") as mock_app_cls_a:
            app_a._on_close()
        mock_app_cls_a.assert_not_called()

        app_b = _FakeApp("/tmp/B.tournoi")
        with patch.object(main, "App") as mock_app_cls_b:
            app_b._on_close()
        mock_app_cls_b.assert_called_once_with(start_standalone=True)
        mock_app_cls_b.return_value.mainloop.assert_called_once()

    def test_sans_tournoi_ferme_sans_rouvrir(self):
        app = _FakeApp(None)
        with patch.object(main, "App") as mock_app_cls:
            app._on_close()
        mock_app_cls.assert_not_called()
        self.assertEqual(app.destroy_calls, 1)


class RemoteEndTournamentRespecteLaMemeRegleTest(_IsolatedRegistryTestCase):
    """"Fin de la partie" (iPhone) appelle EXACTEMENT self._on_close() —
    doit donc respecter la même règle "uniquement si dernier tournoi",
    sans perturber les autres tournois ni nécessiter de code séparé."""

    def test_fin_de_partie_sur_un_des_deux_tournois_ne_rouvre_rien(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        with patch.object(main, "App") as mock_app_cls:
            app._remote_end_tournament()
        mock_app_cls.assert_not_called()
        self.assertEqual(open_windows.list_open_paths(), [os.path.abspath("/tmp/B.tournoi")])

    def test_fin_de_partie_sur_le_dernier_tournoi_revient_a_senaco_sans_tournoi(self):
        open_windows.register("/tmp/A.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        with patch.object(main, "App") as mock_app_cls:
            app._remote_end_tournament()
        mock_app_cls.assert_called_once_with(start_standalone=True)

    def test_idempotent_un_seul_appel_reel_meme_declenche_plusieurs_fois(self):
        open_windows.register("/tmp/A.tournoi")
        app = _FakeApp("/tmp/A.tournoi")
        with patch.object(main, "App") as mock_app_cls:
            app._remote_end_tournament()
            app._remote_end_tournament()
            app._remote_end_tournament()
        mock_app_cls.assert_called_once_with(start_standalone=True)
        self.assertEqual(app.destroy_calls, 1)


# =====================================================================
# 3. Non-régression : Lobby et contrôle à distance
# =====================================================================
class LobbyAndRemoteControlNonRegressionTest(_IsolatedRegistryTestCase):
    def test_list_open_paths_reflete_toujours_les_tournois_vivants(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.register("/tmp/B.tournoi")
        self.assertEqual(
            sorted(open_windows.list_open_paths()),
            sorted([os.path.abspath("/tmp/A.tournoi"), os.path.abspath("/tmp/B.tournoi")]),
        )
        open_windows.unregister("/tmp/A.tournoi")
        self.assertEqual(open_windows.list_open_paths(), [os.path.abspath("/tmp/B.tournoi")])

    def test_list_remote_tournaments_inchange_par_ce_chantier(self):
        open_windows.register("/tmp/A.tournoi")
        open_windows.update_remote_info("/tmp/A.tournoi", 8765, "Tournoi A")
        self.assertEqual(
            [(e["port"], e["name"]) for e in open_windows.list_remote_tournaments()],
            [(8765, "Tournoi A")],
        )
        open_windows.unregister("/tmp/A.tournoi")
        self.assertEqual(open_windows.list_remote_tournaments(), [])


if __name__ == "__main__":
    unittest.main()
