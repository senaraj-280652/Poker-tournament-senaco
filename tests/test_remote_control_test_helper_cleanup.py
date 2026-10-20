# -*- coding: utf-8 -*-
"""Non-régression du plantage natif Tcl/Tk du 2026-10-20 (suite complète
de tests, EXC_BAD_ACCESS/SIGSEGV dans Tk pendant TclServiceIdle, process
responsable : la suite elle-même).

Cause réelle, confirmée par le rapport de plantage macOS (horodaté
11:52:14, exactement l'arrêt du journal de la suite) : 14 threads
"Thread-N (serve_forever)" — des boucles d'acceptation de
remote_control.RemoteControlServer — étaient encore vivants au moment du
plantage, alors qu'ils auraient dû être arrêtés depuis longtemps. Chacun
venait d'une sous-classe de _RemoteControlHttpTestCase (tests/
test_remote_control_reliability.py) qui arrêtait le serveur posé par le
setUp() de base pour le remplacer par un serveur reconfiguré, SANS
réenregistrer addCleanup(self.server.stop) pour ce serveur de
remplacement — seul l'ancien restait suivi. 13 de ces threads venaient de
RemoteAccueilToggleHttpTest (tests/test_accueil_management_toggle.py, une
méthode de test = un thread jamais arrêté), le 14e de
DirectCancelArrivalHttpRequestRefusedTest (tests/
test_correct_arrival_mac_only.py) — exactement 8 fichiers de tests
touchés par ce même motif copié-collé au total (voir _replace_server,
ajouté dans _RemoteControlHttpTestCase, qui centralise désormais la
séquence correcte).

Le mécanisme précis reliant ces threads orphelins au plantage Tcl/Tk
lui-même est déjà documenté ailleurs (voir tests/
test_remote_control_server_stop_joins_threads.py, incident du
2026-09-19) : un thread d'arrière-plan qui traîne peut être choisi par le
ramasse-miettes cyclique de CPython pour finaliser un objet Tk/Tcl
appartenant à un AUTRE thread (le thread principal/Tkinter) — Tcl/Tk
n'étant pas thread-safe, cela corrompt son état interne, qui finit par
planter plus tard. Ce fichier ne reproduit JAMAIS ce plantage natif
directement (dépend d'un coup de chance du GC, jamais déclenché à la
main) — il prouve, à la place, qu'aucun thread "serve_forever" ne peut
plus survivre après la fin d'un test utilisant ce mécanisme, ce qui
retire la cause racine identifiée."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import remote_control  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402


def _live_serve_forever_threads():
    return [t for t in threading.enumerate() if "serve_forever" in t.name]


class ReplaceServerStopsOldAndRegistersNewCleanupTest(_RemoteControlHttpTestCase):
    """Teste directement _replace_server, en dehors de toute sous-classe
    réelle : construit un second serveur, l'installe via _replace_server,
    et vérifie — SANS attendre la fin complète du test (donc avant que
    les addCleanup n'aient eu l'occasion de s'exécuter) — que l'ANCIEN
    serveur (celui du setUp() de base) est déjà arrêté tout de suite,
    comme avant ce correctif. Le nouveau, lui, est encore sciemment en
    vie à ce stade : c'est tearDown()/doCleanups() qui doit l'arrêter —
    voir ServeForeverThreadDiesAfterCleanupsTest ci-dessous pour cette
    partie-là, qui a besoin d'un cycle de vie complet de TestCase."""

    def test_remplacement_arrete_immediatement_lancien_serveur(self):
        old_thread = self.server._thread
        self.assertTrue(old_thread.is_alive(), "le serveur d'origine (posé par le setUp de base) devrait être en vie à ce stade")

        new_server = remote_control.RemoteControlServer(on_word=lambda w: None, port=_free_port())
        self._replace_server(new_server)

        self.assertFalse(old_thread.is_alive(), "l'ancien serveur doit être arrêté DÈS _replace_server, pas seulement au tearDown")
        self.assertTrue(new_server._thread.is_alive(), "le nouveau serveur doit lui être en vie — c'est tearDown qui l'arrêtera")

    def test_appeler_stop_sur_lancien_serveur_apres_remplacement_ne_leve_rien(self):
        """Garde-fou explicite contre un double-arrêt problématique (mise
        en garde du 2026-10-20) : le stop() de l'ancien serveur, déjà
        programmé par le setUp() de base, finira par s'exécuter une
        SECONDE fois au tearDown (la première a eu lieu dans
        _replace_server) — doit rester un no-op silencieux, jamais une
        exception."""
        old_server = self.server
        self._replace_server(remote_control.RemoteControlServer(on_word=lambda w: None, port=_free_port()))
        old_server.stop()  # déjà arrêté : ne doit lever aucune exception


class ServeForeverThreadDiesAfterCleanupsTest(_RemoteControlHttpTestCase):
    """Cycle de vie complet (setUp -> test -> tearDown/doCleanups), pour
    vérifier que le THREAD DE REMPLACEMENT lui-même — celui qui, avant ce
    correctif, restait orphelin pour le reste du process — est bien mort
    une fois le test terminé, pas seulement « arrêté en intention »."""

    def test_le_thread_du_serveur_de_remplacement_est_mort_apres_doCleanups(self):
        new_server = remote_control.RemoteControlServer(on_word=lambda w: None, port=_free_port())
        self._replace_server(new_server)
        new_thread = new_server._thread
        self.assertTrue(new_thread.is_alive())

        self.doCleanups()  # exécute aussi, au passage, le cleanup de self._tmp/open_windows.unregister etc.

        self.assertFalse(new_thread.is_alive(), "le thread du serveur de remplacement doit être mort après doCleanups()")


class NoOrphanedServeForeverThreadAfterRealAffectedTestClassesTest(unittest.TestCase):
    """Preuve bout-en-bout, directement sur les DEUX classes réellement
    responsables des 14 threads du plantage du 2026-10-20 : les exécute
    intégralement (chacune de leurs méthodes de test, via le cycle de vie
    normal d'unittest) et vérifie qu'aucun thread "serve_forever"
    nouveau ne reste vivant une fois toutes terminées. Si le correctif de
    _replace_server était retiré ou contourné, ce test échouerait en
    détectant les threads orphelins, exactement comme le rapport de
    plantage macOS les a révélés."""

    def _run_test_case_class(self, module_name, class_name):
        module = __import__(module_name)
        cls = getattr(module, class_name)
        suite = unittest.TestLoader().loadTestsFromTestCase(cls)
        result = unittest.TestResult()
        suite.run(result)
        self.assertTrue(
            result.wasSuccessful(),
            f"{module_name}.{class_name} ne devrait jamais échouer dans ce contexte "
            f"(erreurs : {result.errors}, échecs : {result.failures})",
        )

    def test_remote_accueil_toggle_http_test_ne_laisse_aucun_thread_orphelin(self):
        """13 méthodes de test, 13 threads orphelins avant le correctif —
        voir tests/test_accueil_management_toggle.py:RemoteAccueilToggleHttpTest."""
        before = set(_live_serve_forever_threads())
        self._run_test_case_class("test_accueil_management_toggle", "RemoteAccueilToggleHttpTest")
        after = _live_serve_forever_threads()
        nouveaux_orphelins = [t for t in after if t not in before and t.is_alive()]
        self.assertEqual(
            nouveaux_orphelins, [],
            f"thread(s) serve_forever orphelin(s) après RemoteAccueilToggleHttpTest : {nouveaux_orphelins}",
        )

    def test_direct_cancel_arrival_http_request_refused_test_ne_laisse_aucun_thread_orphelin(self):
        """1 méthode de test, 1 thread orphelin avant le correctif — voir
        tests/test_correct_arrival_mac_only.py:DirectCancelArrivalHttpRequestRefusedTest."""
        before = set(_live_serve_forever_threads())
        self._run_test_case_class("test_correct_arrival_mac_only", "DirectCancelArrivalHttpRequestRefusedTest")
        after = _live_serve_forever_threads()
        nouveaux_orphelins = [t for t in after if t not in before and t.is_alive()]
        self.assertEqual(
            nouveaux_orphelins, [],
            f"thread(s) serve_forever orphelin(s) après DirectCancelArrivalHttpRequestRefusedTest : {nouveaux_orphelins}",
        )


if __name__ == "__main__":
    unittest.main()
