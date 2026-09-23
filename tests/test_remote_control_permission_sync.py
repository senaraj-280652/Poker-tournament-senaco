# -*- coding: utf-8 -*-
"""Synchronisation automatique de l'état d'autorisation du téléphone
(demande du 2026-09-24, incident réel en club) : après que RAJ (ADMIN) a
accordé "Gérer les éliminations" à MARIE (DIRTO), son iPhone restait
bloqué sur "Aucune fonction autorisée — contactez un ADMIN" — même après
une actualisation manuelle de Safari.

CAUSE EXACTE (diagnostic, voir la conversation) :

1. Rôle/propriétaire/permissions sont déjà recalculés à CHAQUE requête
   par resolve_role (Phase 4, remote_control.py) — jamais figés dans le
   HTML une fois pour toutes, jamais mis en cache serveur au-delà du
   cache de permissions déjà existant (rafraîchi chaque tick/immédiate-
   ment après Accorder-Modifier/Retirer, voir main.py).
2. MAIS aucune page déjà ouverte ne revérifiait JAMAIS son propre état
   après son premier rendu — ni /clock_state (paused/has_pending_moves
   uniquement, sans rapport avec le rôle), ni aucun autre sondage
   existant. Une page ouverte AVANT le changement restait donc figée
   indéfiniment sur le rendu de son premier chargement, quel que soit
   le délai écoulé.
3. En PLUS de cette absence de sondage, _send_html/_send_json ne
   posaient aucun en-tête Cache-Control — Safari iOS peut donc servir
   une copie EN CACHE de "/" même sur une actualisation MANUELLE
   (symptôme "n'apparaît pas systématiquement" observé), sans même
   recontacter le serveur.

CORRECTION (harnais de test ne peut pas exécuter le JavaScript réel —
voir la remarque dans d'autres fichiers de cette suite — donc ce fichier
vérifie : la donnée serveur que le sondage côté page consulte reflète
CHAQUE transition sans nouvelle authentification ; le script de sondage
est bien injecté dans les pages concernées ; les en-têtes Cache-Control
sont bien posés ; et qu'un changement de permission ne déclenche par
lui-même AUCUNE action fonctionnelle) :

- GET /permission_state (nouveau) : {"role": ..., "permissions": [...]}
  — accessible à TOUT appareil authentifié niveau 1, JAMAIS gardée par
  une permission (sinon un appareil qui vient de tout perdre ne
  pourrait plus jamais détecter qu'il a tout perdu) ;
- _AUTH_REDIRECT_SCRIPT (déjà injecté dans TOUTES les pages téléphone)
  sonde ce nouvel endpoint toutes les 3s et recharge la page (reloadApp,
  même anti-cache que le bouton 🔄) dès que la signature rôle+permissions
  change — jamais une mise à jour du DOM à partir de données mises en
  cache côté client, toujours un rechargement complet redemandé au
  serveur ;
- Cache-Control: no-store ajouté à _send_html/_send_json."""
import os
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import open_windows  # noqa: E402
import remote_control  # noqa: E402
import roster  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class PermissionSyncTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="permission_sync_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        roster_path = os.path.join(self._tmp.name, "roster.json")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(roster, "_roster_path", return_value=roster_path),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        log_patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        logger_patcher = patch.object(remote_control, "_logger", None)
        self.addCleanup(log_patcher.stop)
        self.addCleanup(logger_patcher.stop)
        log_patcher.start()
        logger_patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.on_word_calls = []
        self.on_eliminate_calls = []

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._permission_cache = {}
        self.server = remote_control.RemoteControlServer(
            on_word=lambda word: self.on_word_calls.append(word),
            on_eliminate=self._on_eliminate,
            get_dirto_permissions=lambda name: self._permission_cache.get((name or "").strip(), frozenset()),
            port=self.port,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)

    def _on_eliminate(self, eliminated_id, eliminator_id, client_request_id=None):
        self.on_eliminate_calls.append((eliminated_id, eliminator_id, client_request_id))
        return {"ok": True, "message": ""}

    def _refresh_permission_cache(self):
        self._permission_cache = {
            auth["dirto_name"]: frozenset(auth["permissions"])
            for auth in self.db.list_dirto_authorizations()
        }

    def _grant(self, dirto_name, admin_name, permissions):
        self.db.set_dirto_authorization(dirto_name, admin_name, permissions)
        self._refresh_permission_cache()

    def _revoke(self, dirto_name):
        self.db.clear_dirto_authorization(dirto_name)
        self._refresh_permission_cache()

    def _permission_state(self, jar):
        status, body, _ = http_request(self.base, "GET", "/permission_state", jar)
        self.assertEqual(status, 200, body)
        import json
        return json.loads(body)


class NoAuthorityRequiredForPollingEndpointTest(PermissionSyncTestCase):
    """GET /permission_state doit rester accessible quel que soit le
    rôle/les permissions — c'est justement lui qui doit permettre à un
    appareil sans rien de détecter qu'il vient d'obtenir quelque chose
    (ou l'inverse)."""

    def test_appareil_non_lie_peut_interroger_son_propre_etat(self):
        jar = authenticated_jar(self.base)  # pas de owner_name
        data = self._permission_state(jar)
        self.assertEqual(data, {"role": "NONE", "permissions": []})

    def test_dirto_sans_aucune_permission_peut_interroger_son_propre_etat(self):
        jar = authenticated_jar(self.base, owner_name="Marie")
        data = self._permission_state(jar)
        self.assertEqual(data, {"role": "DIRTO", "permissions": []})

    def test_admin_voit_son_role(self):
        jar = authenticated_jar(self.base, owner_name="Raj")
        data = self._permission_state(jar)
        self.assertEqual(data["role"], "ADMIN")


class CacheControlHeadersTest(PermissionSyncTestCase):
    """Diagnostic point 5 : sans Cache-Control, Safari peut servir une
    copie en cache de "/" même sur une actualisation manuelle."""

    def test_page_accueil_jamais_mise_en_cache(self):
        jar = authenticated_jar(self.base, owner_name="Raj")
        _, _, headers = http_request(self.base, "GET", "/", jar)
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_permission_state_jamais_mis_en_cache(self):
        jar = authenticated_jar(self.base, owner_name="Raj")
        _, _, headers = http_request(self.base, "GET", "/permission_state", jar)
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_page_sans_acces_jamais_mise_en_cache(self):
        jar = authenticated_jar(self.base, owner_name="Marie")  # zéro permission
        _, _, headers = http_request(self.base, "GET", "/", jar)
        self.assertEqual(headers.get("Cache-Control"), "no-store")


class PollingScriptInjectedTest(PermissionSyncTestCase):
    """Vérifie STRUCTURELLEMENT (jamais l'exécution réelle du
    JavaScript, impossible depuis cette suite Python) que le sondage est
    bien injecté sur les pages concernées, en particulier _NO_ACCESS_PAGE
    — la page EXACTE sur laquelle MARIE restait bloquée dans l'incident
    réel."""

    def test_page_sans_acces_contient_le_sondage(self):
        jar = authenticated_jar(self.base, owner_name="Marie")
        _, body, _ = http_request(self.base, "GET", "/", jar)
        text = body.decode("utf-8")
        self.assertIn("pollPermissionState", text)
        self.assertIn("/permission_state", text)
        self.assertIn("setInterval(pollPermissionState, 3000)", text)

    def test_page_accueil_admin_contient_le_sondage(self):
        jar = authenticated_jar(self.base, owner_name="Raj")
        _, body, _ = http_request(self.base, "GET", "/", jar)
        self.assertIn("pollPermissionState", body.decode("utf-8"))

    def test_page_eliminate_contient_le_sondage(self):
        self._grant("Marie", "Raj", ["eliminations"])
        jar = authenticated_jar(self.base, owner_name="Marie")
        _, body, _ = http_request(self.base, "GET", "/eliminate", jar)
        self.assertIn("pollPermissionState", body.decode("utf-8"))


class TransitionsOnAlreadyOpenSessionTest(PermissionSyncTestCase):
    """Coeur de la demande : les 5 transitions minimales, vérifiées SANS
    nouvelle authentification (même jar du début à la fin — reproduit
    fidèlement "une page déjà ouverte" côté serveur, ce que le sondage
    côté page interroge en boucle)."""

    def test_1_aucun_droit_vers_eliminations(self):
        jar = authenticated_jar(self.base, owner_name="Marie")
        self.assertEqual(self._permission_state(jar), {"role": "DIRTO", "permissions": []})

        self._grant("Marie", "Raj", ["eliminations"])

        self.assertEqual(
            self._permission_state(jar), {"role": "DIRTO", "permissions": ["eliminations"]},
        )
        # La page reconstruite reflète bien "Gérer les éliminations",
        # jamais "Joueurs" (strictement ADMIN-only, voir requirement).
        _, body, _ = http_request(self.base, "GET", "/", jar)
        text = body.decode("utf-8")
        self.assertIn('id="btn-eliminations"', text)
        self.assertNotIn('id="btn-elimination"', text)

    def test_2_eliminations_vers_eliminations_et_tables(self):
        jar = authenticated_jar(self.base, owner_name="Marie")
        self._grant("Marie", "Raj", ["eliminations"])
        self.assertEqual(
            self._permission_state(jar), {"role": "DIRTO", "permissions": ["eliminations"]},
        )

        self._grant("Marie", "Raj", ["eliminations", "tables"])

        self.assertEqual(
            self._permission_state(jar), {"role": "DIRTO", "permissions": ["eliminations", "tables"]},
        )
        _, body, _ = http_request(self.base, "GET", "/", jar)
        text = body.decode("utf-8")
        self.assertIn('id="btn-eliminations"', text)
        self.assertIn('id="btn-tables"', text)

    def test_3_eliminations_vers_aucun_droit(self):
        jar = authenticated_jar(self.base, owner_name="Marie")
        self._grant("Marie", "Raj", ["eliminations"])
        self.assertEqual(
            self._permission_state(jar), {"role": "DIRTO", "permissions": ["eliminations"]},
        )

        self._revoke("Marie")

        self.assertEqual(self._permission_state(jar), {"role": "DIRTO", "permissions": []})
        _, body, _ = http_request(self.base, "GET", "/", jar)
        self.assertIn(remote_control._NO_PERMISSION_MESSAGE, body.decode("utf-8"))

    def test_4_dirto_vers_admin(self):
        """Changement de PROPRIÉTAIRE (pas seulement de permissions) :
        le même appareil, désormais lié à Raj (ADMIN) au lieu de Marie
        (DIRTO)."""
        jar = authenticated_jar(self.base, owner_name="Marie")
        self._grant("Marie", "Raj", ["eliminations"])
        self.assertEqual(
            self._permission_state(jar), {"role": "DIRTO", "permissions": ["eliminations"]},
        )

        browser_id = {c.name: c.value for c in jar}["rc_bid"]
        ok = open_windows.set_remote_device_owner(browser_id, "Raj")
        self.assertTrue(ok)

        data = self._permission_state(jar)
        self.assertEqual(data["role"], "ADMIN")
        _, body, _ = http_request(self.base, "GET", "/", jar)
        text = body.decode("utf-8")
        self.assertIn('id="btn-end-tournament"', text)  # accès ADMIN complet

    def test_5_proprietaire_vers_telephone_non_lie(self):
        jar = authenticated_jar(self.base, owner_name="Marie")
        self._grant("Marie", "Raj", ["eliminations"])
        self.assertEqual(
            self._permission_state(jar), {"role": "DIRTO", "permissions": ["eliminations"]},
        )

        browser_id = {c.name: c.value for c in jar}["rc_bid"]
        ok = open_windows.clear_remote_device_owner(browser_id)
        self.assertTrue(ok)

        self.assertEqual(self._permission_state(jar), {"role": "NONE", "permissions": []})
        _, body, _ = http_request(self.base, "GET", "/", jar)
        self.assertIn(remote_control._NO_PERMISSION_MESSAGE, body.decode("utf-8"))


class PermissionChangeHasNoSideEffectTest(PermissionSyncTestCase):
    """Une modification de permission ne doit ACTUALISER que l'affichage
    — jamais déclencher, par elle-même, une action fonctionnelle
    (aucun appel à on_word/on_eliminate)."""

    def test_octroi_modification_retrait_naffectent_jamais_on_word_ou_on_eliminate(self):
        jar = authenticated_jar(self.base, owner_name="Marie")

        self._grant("Marie", "Raj", ["eliminations"])
        self._grant("Marie", "Raj", ["eliminations", "tables"])
        self._grant("Marie", "Raj", ["tables"])
        self._revoke("Marie")

        # Le sondage lui-même (utilisé par la page pour détecter ces
        # changements) est un simple GET, jamais un déclencheur d'action.
        for _ in range(3):
            self._permission_state(jar)

        self.assertEqual(self.on_word_calls, [])
        self.assertEqual(self.on_eliminate_calls, [])


if __name__ == "__main__":
    unittest.main()
