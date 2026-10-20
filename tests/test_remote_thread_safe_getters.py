# -*- coding: utf-8 -*-
"""Correctif du 2026-09-29 — "ERR_CONNECTION_CLOSED" sur TOUTE la page
d'accueil du contrôle à distance, incident réel signalé par
l'utilisateur : `get_accueil_management_enabled` et `get_absence_
deadline_passed`, les deux callbacks `get_*` passés à RemoteControlServer
(voir main.py: App._start_remote_control_if_enabled), touchaient
`self.db` DIRECTEMENT depuis le thread appelant — qui, pour ces deux-là
seulement, est le thread HTTP du contrôle à distance (`ThreadingHTTPServer`
crée un thread PAR CONNEXION), jamais le thread principal Tkinter.

Or `self.db.conn` (voir database.py: `sqlite3.connect(path)`, sans
`check_same_thread=False`) est lié EXCLUSIVEMENT au thread qui l'a créé —
le thread principal, puisque `self.db` est toujours ouvert depuis une
action utilisateur (menu, dialogue). L'appeler depuis le thread HTTP lève
`sqlite3.ProgrammingError`, JAMAIS interceptée dans `do_GET`/`do_POST` :
la connexion HTTP est alors abandonnée par `socketserver` (voir
`ThreadingMixIn.process_request_thread`/`BaseServer.handle_error`) SANS
qu'aucune réponse n'ait été envoyée — exactement "ERR_CONNECTION_CLOSED"
côté client. Comme `get_accueil_management_enabled` est appelée sans
condition pour construire la page "/" (page d'ACCUEIL du contrôle à
distance elle-même, voir remote_control.py: do_GET), CHAQUE chargement de
CETTE page échouait, depuis N'IMPORTE QUEL appareil — plus aucun accès
possible.

Correctif : comme TOUS les autres callbacks `get_*` de ce serveur
(`get_players`, `get_clock_paused`, `get_has_pending_moves`,
`get_checkin_players`, `get_pending_moves`, `get_pending_rebalance`...),
les deux valeurs sont désormais mises en CACHE dans un simple attribut
Python (`self._remote_accueil_management_enabled`/`self._remote_absence_
deadline_passed`), écrit EXCLUSIVEMENT depuis le thread principal
(initialisation dans App.__init__, peuplement au démarrage du serveur,
rafraîchissement périodique dans _tick, mise à jour immédiate dans
App._on_accueil_management_toggle) et simplement LU par la lambda passée
au serveur — jamais un accès SQLite depuis le thread HTTP.

Ce module couvre :
1. Reproduction du mécanisme exact (sqlite3.ProgrammingError) sur un
   accès direct à self.db.conn depuis un thread autre que celui qui l'a
   créé — documente la cause réelle.
2. La mise à jour immédiate des deux caches par App._on_accueil_
   management_toggle (thread principal, donc self.db y reste accessible
   sans risque).
3. Reproduction bout-en-bout, avec un VRAI RemoteControlServer et une
   VRAIE connexion SQLite : l'ANCIEN câblage (callback touchant self.db
   directement) casse la page "/" ; le NOUVEAU câblage (callback lisant
   un cache) la laisse fonctionner normalement."""
import os
import queue
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402,F401


def _new_db(tmp_dir, name="A"):
    return database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


# =====================================================================
# 1. Reproduction du mécanisme exact (documente la cause réelle)
# =====================================================================
class CrossThreadSqliteAccessRaisesTest(unittest.TestCase):
    """Une connexion sqlite3 créée SANS check_same_thread=False (voir
    database.py: Database.__init__, `sqlite3.connect(path)`) ne peut être
    utilisée QUE depuis le thread qui l'a ouverte — jamais un simple
    résultat incorrect, une exception franche à chaque fois. C'est cette
    exception, non interceptée par do_GET/do_POST, qui interrompait la
    connexion HTTP sans réponse."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="cross_thread_sqlite_test_")
        self.addCleanup(self._tmp.cleanup)
        # Créée ICI, sur le thread de CE test — exactement comme self.db
        # est créée sur le thread principal Tkinter dans l'application
        # réelle, jamais sur le thread du serveur de contrôle à distance.
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.db.add_player("Alice")

    def _call_from_other_thread(self, func):
        errors = []
        results = []

        def worker():
            try:
                results.append(func())
            except Exception as e:  # noqa: BLE001 — capturé pour inspection, jamais avalé
                errors.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=5)
        return results, errors

    def test_accueil_management_enabled_depuis_un_autre_thread_leve_programmingerror(self):
        results, errors = self._call_from_other_thread(self.db.accueil_management_enabled)

        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], sqlite3.ProgrammingError)

    def test_absence_deadline_passed_depuis_un_autre_thread_leve_programmingerror(self):
        results, errors = self._call_from_other_thread(self.db.absence_deadline_passed)

        self.assertEqual(results, [])
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], sqlite3.ProgrammingError)

    def test_le_meme_appel_depuis_le_thread_qui_a_cree_la_connexion_fonctionne(self):
        """Non-régression du sens inverse : ce n'est PAS le simple fait
        d'appeler ces méthodes qui pose problème, seulement de les
        appeler depuis un thread ÉTRANGER — depuis ce thread-ci (celui
        qui a ouvert self.db), tout fonctionne normalement."""
        self.assertFalse(self.db.accueil_management_enabled())
        self.assertFalse(self.db.absence_deadline_passed())


# =====================================================================
# 2. Mise à jour immédiate des caches par _on_accueil_management_toggle
# =====================================================================
class AccueilToggleUpdatesRemoteCachesImmediatelyTest(unittest.TestCase):
    """_on_accueil_management_toggle s'exécute sur le thread principal
    (commande d'un Checkbutton Tk) : y lire/écrire self.db directement
    reste sans danger — c'est exactement ce qui permet une mise à jour
    IMMÉDIATE des deux caches, sans attendre le prochain tick (~1s)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="accueil_toggle_cache_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.db.add_player("Alice")
        self.win = types.SimpleNamespace(
            db=self.db,
            accueil_management_var=types.SimpleNamespace(get=lambda: True),
            _remote_accueil_management_enabled=False,
            _remote_absence_deadline_passed=False,
        )
        for meth in ("_on_accueil_management_toggle", "_update_accueil_management_widgets"):
            setattr(self.win, meth, types.MethodType(getattr(main.App, meth), self.win))

    def test_activation_met_a_jour_le_cache_accueil_immediatement(self):
        self.win._on_accueil_management_toggle()

        self.assertTrue(self.win._remote_accueil_management_enabled)
        self.assertTrue(self.db.accueil_management_enabled())

    def test_desactivation_met_a_jour_le_cache_accueil_immediatement(self):
        self.db.set_settings({"accueil_management_enabled": 1})
        self.win._remote_accueil_management_enabled = True
        self.win.accueil_management_var = types.SimpleNamespace(get=lambda: False)

        self.win._on_accueil_management_toggle()

        self.assertFalse(self.win._remote_accueil_management_enabled)

    def test_desactivation_remet_le_cache_delai_a_faux_meme_si_deja_depasse(self):
        """Un délai déjà dépassé avant la désactivation laissait
        `_remote_absence_deadline_passed` à True (valeur alors mise en
        cache) : la désactivation doit le rebasculer à False
        immédiatement — Database.absence_deadline_passed() renvoie de
        toute façon toujours False une fois le réglage désactivé (voir
        sa docstring), le cache ne doit jamais rester en décalage."""
        self.db.set_settings({"accueil_management_enabled": 1})
        _start_tournament(self.db, minutes_ago=65, duration_minutes=60)
        self.assertTrue(self.db.absence_deadline_passed())
        self.win._remote_absence_deadline_passed = True
        self.win.accueil_management_var = types.SimpleNamespace(get=lambda: False)

        self.win._on_accueil_management_toggle()

        self.assertFalse(self.win._remote_absence_deadline_passed)


# =====================================================================
# 3. Reproduction bout-en-bout : ancien câblage cassé, nouveau câblage OK
# =====================================================================
class HomePageCrossThreadRegressionTest(_RemoteControlHttpTestCase):
    """Avec un VRAI RemoteControlServer et une VRAIE connexion SQLite
    (check_same_thread par défaut, créée sur le thread de CE test —
    jamais sur celui du serveur HTTP, exactement comme self.db dans
    l'application réelle) : l'ANCIEN câblage de get_accueil_management_
    enabled (callback touchant self.db directement) casse la page "/"
    avec la même signature que l'incident réel (connexion interrompue,
    aucune réponse) ; le NOUVEAU câblage (callback lisant un simple
    cache) la laisse fonctionner normalement."""

    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="home_page_cross_thread_test_")
        self.addCleanup(self._tmp2.cleanup)
        # Connexion SQLite standard (check_same_thread=True implicite),
        # créée ICI sur le thread de ce test — jamais sur le thread HTTP
        # du serveur démarré ci-dessous.
        self.db = _new_db(self._tmp2.name)
        self.addCleanup(self.db.conn.close)

    def test_ancien_cablage_direct_sur_self_db_casse_la_page_accueil(self):
        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            # Reproduit EXACTEMENT l'ancien bogue (avant le correctif du
            # 2026-09-29) : lecture directe de self.db depuis le thread
            # HTTP, plutôt qu'un cache.
            get_accueil_management_enabled=lambda: self.db.accueil_management_enabled(),
            port=self.port,
        ))
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        with self.assertRaises(Exception):
            http_request(self.base, "GET", "/", jar)

    def test_nouveau_cablage_en_cache_laisse_fonctionner_la_page_accueil(self):
        # Même donnée que le test précédent, mais lue UNE FOIS depuis le
        # thread principal (ce test), puis exposée par une simple
        # fermeture Python — exactement le rôle de self._remote_accueil_
        # management_enabled dans l'application réelle.
        cached_accueil_enabled = self.db.accueil_management_enabled()
        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_accueil_management_enabled=lambda: cached_accueil_enabled,
            port=self.port,
        ))
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(self.base, "GET", "/", jar)

        self.assertEqual(status, 200)
        self.assertIn(b"Contr\xc3\xb4le \xc3\xa0 distance", body)

    def test_ancien_cablage_absence_deadline_passed_casse_la_page_eliminations(self):
        """Même mécanisme pour l'autre callback fautif (get_absence_
        deadline_passed, préexistant avant ce complément) — visible sur
        /eliminate plutôt que "/", puisque c'est la page qui sonde ce
        callback (via _ABSENCE_STATE_PERMISSIONS/{abs_des_button})."""
        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_accueil_management_enabled=lambda: True,
            get_absence_deadline_passed=lambda: self.db.absence_deadline_passed(),
            port=self.port,
        ))
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        # "/" ne dépend pas de get_absence_deadline_passed : reste
        # fonctionnelle, contrairement à l'incident réel où LES DEUX
        # callbacks étaient fautifs en même temps.
        status, _, _ = http_request(self.base, "GET", "/", jar)
        self.assertEqual(status, 200)

        with self.assertRaises(Exception):
            http_request(self.base, "GET", "/absence_state", jar)

    def test_nouveau_cablage_absence_deadline_passed_fonctionne(self):
        cached_deadline_passed = self.db.absence_deadline_passed()
        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_accueil_management_enabled=lambda: True,
            get_absence_deadline_passed=lambda: cached_deadline_passed,
            port=self.port,
        ))
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

        status, body, _ = http_request(self.base, "GET", "/absence_state", jar)

        self.assertEqual(status, 200)
        self.assertIn(b'"deadline_passed": false', body)


if __name__ == "__main__":
    unittest.main()
