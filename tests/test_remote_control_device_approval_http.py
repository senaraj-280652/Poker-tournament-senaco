# -*- coding: utf-8 -*-
"""Couverture automatisée HTTP (VRAI RemoteControlServer, ThreadingHTTPServer
réel sur 127.0.0.1 — comme tests/test_remote_control_end_tournament_
reconnect.py, un bug de cycle de vie réel n'est prouvé/corrigé que par de
vraies requêtes) du durcissement du contrôle à distance (demande du
2026-09-09, "sécurisation du contrôle à distance" PUIS "approbation des
téléphones") : code à 6 chiffres, approbation par appareil, anti-
bruteforce, protection SERVEUR de chaque route sensible.

Ne teste PAS la logique interne d'open_windows.py isolément (voir tests/
test_remote_control_auth_backend.py) ni le scénario multi-tournois à
VRAIS sous-processus séparés (voir tests/test_remote_control_multi_
process_real_subprocess.py).

open_windows redirigé vers un dossier temporaire dédié (jamais
~/.poker_tournament) ; open_windows.list_remote_tournaments mocké là où
utilisé (jamais d'accès au vrai registre partagé)."""
import http.cookiejar
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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


class _RemoteControlHttpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_device_http_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            # Phase 4, "Sécurisation du Contrôle à distance" (2026-09-20) :
            # ce fichier teste l'authentification NIVEAU 1 (code,
            # approbation, anti-bruteforce), pas les permissions DIRTO —
            # un rôle ADMIN inconditionnel évite d'isoler roster.py
            # (fichier RÉEL ~/.poker_tournament/roster.json, jamais
            # touché ici) juste pour qu'authenticated_jar(...,
            # owner_name=...) retrouve un accès complet, comme avant
            # l'existence des permissions.
            patch.object(roster, "get_group", return_value=roster.ROSTER_GROUP_ADMIN),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        # _LOG_PATH/_logger (demande du 2026-09-19, incident réel où ce
        # fichier — AllSensitiveRoutesProtectedTest en particulier,
        # itérant sur toutes les routes sensibles pour vérifier leur 401 —
        # a écrit dans le VRAI ~/.poker_tournament/remote_control.log
        # pendant qu'un tournoi réel tournait) : jamais isolé jusqu'ici.
        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        log_patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        logger_patcher = patch.object(remote_control, "_logger", None)
        self.addCleanup(log_patcher.stop)
        self.addCleanup(logger_patcher.stop)
        log_patcher.start()
        logger_patcher.start()

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = remote_control.RemoteControlServer(on_word=lambda w: None, port=self.port)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.code = open_windows.remote_session_code()

    def _req(self, method, path, jar=None, body=None):
        return http_request(self.base, method, path, jar, body=body)

    def _new_jar_with_browser_id(self):
        jar = http.cookiejar.CookieJar()
        self._req("GET", "/login", jar)
        return jar


# =======================================================================
# 1. Parcours complet : code correct -> pending -> Autoriser -> accès.
# =======================================================================
class FullFlowTest(_RemoteControlHttpTestCase):
    def test_nouveau_telephone_bon_code_toujours_bloque_en_attente(self):
        jar = self._new_jar_with_browser_id()
        status, body, _ = self._req("POST", "/authenticate", jar, body={"code": self.code})
        self.assertEqual(json.loads(body), {"ok": True, "status": "pending"})
        # Aucun cookie d'authentification tant que non approuvé.
        self.assertNotIn("rc_auth", {c.name for c in jar})
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)

    def test_nouveau_telephone_131261_toujours_bloque_en_attente(self):
        """131261 NE contourne JAMAIS l'approbation (demande explicite
        du 2026-09-09, "approbation des téléphones", point 6)."""
        jar = self._new_jar_with_browser_id()
        status, body, _ = self._req("POST", "/authenticate", jar, body={"code": "131261"})
        self.assertEqual(json.loads(body), {"ok": True, "status": "pending"})
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)

    def test_autoriser_donne_acces(self):
        jar = self._new_jar_with_browser_id()
        self._req("POST", "/authenticate", jar, body={"code": self.code})
        pending = open_windows.list_pending_remote_devices()
        self.assertEqual(len(pending), 1)
        browser_id = pending[0]["browser_id"]

        open_windows.approve_remote_device(browser_id, label="Salle 1 - Jean")
        # Phase 4, "Sécurisation du Contrôle à distance" (2026-09-20) :
        # l'approbation (niveau 1) ne suffit plus, à elle seule, à
        # accéder à une route FONCTIONNELLE comme /players (voir
        # resolve_role) — ce test vérifie le parcours d'approbation lui-
        # même, donc un rôle ADMIN explicite ici (roster.get_group
        # patché en ADMIN pour toute la classe, voir setUp).
        open_windows.set_remote_device_owner(browser_id, "Test Admin")

        # /auth_status détecte l'approbation et pose rc_auth tout seul
        # (sondé toutes les 2s par /login côté téléphone en pratique).
        status, body, _ = self._req("GET", "/auth_status", jar)
        self.assertEqual(json.loads(body), {"status": "approved"})
        self.assertIn("rc_auth", {c.name for c in jar})

        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 200)
        status, body, _ = self._req("GET", "/", jar)
        self.assertEqual(status, 200)

    def test_refuser_naccorde_jamais_lacces(self):
        jar = self._new_jar_with_browser_id()
        self._req("POST", "/authenticate", jar, body={"code": self.code})
        browser_id = open_windows.list_pending_remote_devices()[0]["browser_id"]

        open_windows.revoke_remote_device(browser_id)  # "Refuser"

        status, body, _ = self._req("GET", "/auth_status", jar)
        self.assertEqual(json.loads(body), {"status": "refused"})
        self.assertNotIn("rc_auth", {c.name for c in jar})
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)

    def test_appareil_deja_approuve_nouveau_code_de_session_acces_immediat(self):
        """"appareil approuvé + nouveau code de session => accès à la
        nouvelle session" (test explicitement demandé)."""
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 200)

        # Nouvelle session : ferme et rouvre (rotation du code).
        open_windows.unregister(self._session_path)
        open_windows.register(self._session_path)
        new_code = open_windows.remote_session_code()
        self.assertNotEqual(new_code, self.code)

        # L'ancien cookie ne fonctionne plus...
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)

        # ...mais ressaisir le NOUVEAU code donne un accès IMMÉDIAT, sans
        # repasser par une approbation.
        status, body, _ = self._req("POST", "/authenticate", jar, body={"code": new_code})
        self.assertEqual(json.loads(body), {"ok": True, "status": "approved"})
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 200)


# =======================================================================
# 2. Révocation immédiate et résistance à la falsification de cookies.
# =======================================================================
class RevocationAndForgeryTest(_RemoteControlHttpTestCase):
    def test_revocation_coupe_immediatement_lacces_en_cours(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, _, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 200)

        pending_or_approved = open_windows.list_approved_remote_devices()
        browser_id = pending_or_approved[0]["browser_id"]
        open_windows.revoke_remote_device(browser_id)

        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)
        status, _, headers = self._req("GET", "/", jar)
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")

    def test_ancien_cookie_apres_revocation_reste_refuse_meme_reessaye(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        browser_id = open_windows.list_approved_remote_devices()[0]["browser_id"]
        open_windows.revoke_remote_device(browser_id)
        for _ in range(3):
            status, _, _ = self._req("GET", "/players", jar)
            self.assertEqual(status, 401)

    def test_falsification_dun_identifiant_navigateur_najamais_dapprobation(self):
        """Fabriquer soi-même un cookie rc_bid/rc_auth (sans passer par
        une VRAIE approbation) ne doit jamais suffire — voir open_windows.
        verify_device_session : un browser_id inventé n'a par construction
        aucune entrée "approved"."""
        jar = http.cookiejar.CookieJar()
        forged_bid = "f" * 32
        forged_token = "a" * 64
        cookie_bid = http.cookiejar.Cookie(
            0, "rc_bid", forged_bid, None, False, "127.0.0.1", False, False,
            "/", True, False, None, True, None, None, {},
        )
        cookie_auth = http.cookiejar.Cookie(
            0, "rc_auth", forged_token, None, False, "127.0.0.1", False, False,
            "/", True, False, None, True, None, None, {},
        )
        jar.set_cookie(cookie_bid)
        jar.set_cookie(cookie_auth)
        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)

    def test_jeton_dune_session_precedente_refuse(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        status, _, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 200)

        # Nouvelle session : le fichier éphémère change entièrement.
        open_windows.unregister(self._session_path)
        open_windows.register(self._session_path)

        status, body, _ = self._req("GET", "/players", jar)
        self.assertEqual(status, 401)


# =======================================================================
# 3. Changement de tournoi : l'authentification suit, sans ressaisir le
#    code. Le VRAI relais (proxy) inter-processus ne peut être testé
#    fidèlement qu'avec de VRAIS processus séparés — own_pid vaut
#    littéralement os.getpid() pour CHAQUE RemoteControlServer, donc
#    deux instances dans le MÊME process de test partageraient le même
#    "own_pid" et casseraient la logique self/other de resolve_current_
#    pid (boucle de relais sur elle-même) sans rien prouver de réel ;
#    voir tests/test_remote_control_multi_process_real_subprocess.py:
#    test_approbation_partagee_entre_processus, qui vérifie exactement
#    cette propriété (jeton délivré/vérifié depuis un AUTRE processus)
#    avec de VRAIS sous-processus séparés — un test bien plus probant
#    que deux serveurs dans le même process ne pourrait jamais l'être.
#    Le relais du Cookie lui-même (Handler._proxy, qui transmet l'en-
#    tête Cookie tel quel) est un mécanisme préexistant, déjà couvert
#    par tests/test_remote_control_end_tournament_reconnect.py pour le
#    cookie "selected_pid" — rc_bid/rc_auth empruntent exactement le
#    même chemin, sans code spécifique supplémentaire à tester ici.
# =======================================================================
class TournamentSwitchPreservesAuthTest(_RemoteControlHttpTestCase):
    def test_selection_du_meme_tournoi_depuis_le_lobby_ne_redemande_pas_le_code(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        own_pid = os.getpid()
        fake_list = [{"pid": own_pid, "port": self.port, "name": "Ceci"}]
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=fake_list):
            status, _, _ = self._req("GET", f"/select_tournament?pid={own_pid}", jar)
            self.assertEqual(status, 302)
            # Toujours servi LOCALEMENT (current_pid == own_pid, jamais
            # relayé) : l'authentification déjà acquise doit rester valide.
            status, body, _ = self._req("GET", "/players", jar)
            self.assertEqual(status, 200, body)

    def test_lobbylist_reste_accessible_a_un_appareil_deja_authentifie(self):
        jar = authenticated_jar(self.base, code=self.code, owner_name="Test Admin")
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=[]):
            status, _, _ = self._req("GET", "/lobbylist", jar)
            self.assertEqual(status, 200)

    def test_deux_navigateurs_differents_approbations_independantes(self):
        jar1 = self._new_jar_with_browser_id()
        jar2 = self._new_jar_with_browser_id()
        self._req("POST", "/authenticate", jar1, body={"code": self.code})
        self._req("POST", "/authenticate", jar2, body={"code": self.code})
        pending = open_windows.list_pending_remote_devices()
        self.assertEqual(len(pending), 2)

        bid1 = {c.name: c.value for c in jar1}["rc_bid"]
        target = next(d for d in pending if d["browser_id"] == bid1)
        open_windows.approve_remote_device(target["browser_id"], label="Salle 1")
        # Phase 4 (voir la remarque équivalente dans test_autoriser_donne_acces).
        open_windows.set_remote_device_owner(target["browser_id"], "Test Admin")

        status, body, _ = self._req("GET", "/auth_status", jar1)
        self.assertEqual(json.loads(body)["status"], "approved")
        status, body, _ = self._req("GET", "/auth_status", jar2)
        self.assertEqual(json.loads(body)["status"], "pending")

        status, _, _ = self._req("GET", "/players", jar1)
        self.assertEqual(status, 200)
        status, _, _ = self._req("GET", "/players", jar2)
        self.assertEqual(status, 401)


# =======================================================================
# 4. Anti-bruteforce, vu depuis le serveur HTTP (429, message, expiration).
# =======================================================================
class HttpRateLimitTest(_RemoteControlHttpTestCase):
    def test_5_mauvais_codes_declenchent_un_429(self):
        """Le blocage se déclenche SUR le 5e échec lui-même (voir
        open_windows._record_remote_auth_attempt) : les 5 réponses aux
        tentatives fautives restent {"ok": false}/200 (chacune est
        évaluée AVANT que son propre échec ne soit enregistré) — c'est
        la 6e requête, quelle qu'elle soit, qui observe le 429."""
        jar = self._new_jar_with_browser_id()
        for i in range(5):
            status, body, _ = self._req("POST", "/authenticate", jar, body={"code": "000000"})
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(body)["ok"])
        status, body, _ = self._req("POST", "/authenticate", jar, body={"code": "000000"})
        self.assertEqual(status, 429)
        data = json.loads(body)
        self.assertFalse(data["ok"])
        self.assertIn("min", data["message"])

    def test_code_correct_pendant_le_blocage_reste_429(self):
        jar = self._new_jar_with_browser_id()
        for _ in range(5):
            self._req("POST", "/authenticate", jar, body={"code": "000000"})
        status, body, _ = self._req("POST", "/authenticate", jar, body={"code": self.code})
        self.assertEqual(status, 429, "même le bon code ne doit jamais être évalué pendant un blocage actif")

    def test_expiration_du_blocage_redonne_lacces_normal(self):
        jar = self._new_jar_with_browser_id()
        for _ in range(5):
            self._req("POST", "/authenticate", jar, body={"code": "000000"})
        status, _, _ = self._req("POST", "/authenticate", jar, body={"code": self.code})
        self.assertEqual(status, 429)

        # Force l'expiration (sans attendre réellement).
        browser_id = {c.name: c.value for c in jar}["rc_bid"]
        data = open_windows._read_json_or_empty(open_windows._remote_ratelimit_path())
        data[f"browser:{browser_id}"]["block_until"] = time.time() - 1
        open_windows._atomic_write_json(open_windows._remote_ratelimit_path(), data)

        status, body, _ = self._req("POST", "/authenticate", jar, body={"code": self.code})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"ok": True, "status": "pending"})


# =======================================================================
# 5. Inventaire des routes sensibles : toutes protégées côté serveur.
# =======================================================================
class AllSensitiveRoutesProtectedTest(_RemoteControlHttpTestCase):
    def test_aucune_route_daction_accessible_sans_authentification(self):
        """Routes JSON/données/actions : 401 direct (voir _AUTH_PAGE_
        PATHS dans remote_control.py — seules les vraies pages plein
        écran comme /lobbylist ou "/" reçoivent un 302 vers /login à la
        place, voir test_pages_non_authentifiees_redirigent_vers_login
        ci-dessous)."""
        cases = [
            ("POST", "/action/elimination", None),
            ("POST", "/eliminate", {"eliminated_id": 1, "eliminator_id": None}),
            ("POST", "/upload_photo?player_name=Alice", None),
            ("POST", "/delete_photo?player_name=Alice", None),
            ("POST", "/rebalance_answer", {"request_id": "x", "seat": None}),
            ("POST", "/end_tournament", {"pid": os.getpid()}),
            ("GET", "/players", None),
            ("GET", "/roster_players", None),
            ("GET", "/clock_state", None),
            ("GET", "/rebalance_pending", None),
            ("GET", "/photo_image?name=Alice", None),
        ]
        jar = http.cookiejar.CookieJar()  # jamais authentifié
        for method, path, body in cases:
            status, resp_body, _ = self._req(method, path, jar, body=body)
            self.assertEqual(
                status, 401,
                f"{method} {path} aurait dû être refusé sans authentification (reçu {status}: {resp_body!r})",
            )

    def test_pages_non_authentifiees_redirigent_vers_login(self):
        jar = http.cookiejar.CookieJar()
        for path in ("/", "/index.html", "/eliminate", "/photos", "/lobbylist", "/select_tournament?pid=1"):
            status, _, headers = self._req("GET", path, jar)
            self.assertEqual(status, 302, path)
            self.assertEqual(headers.get("Location"), "/login", path)

    def test_login_et_auth_status_restent_accessibles_sans_authentification(self):
        jar = http.cookiejar.CookieJar()
        status, _, _ = self._req("GET", "/login", jar)
        self.assertEqual(status, 200)
        status, body, _ = self._req("GET", "/auth_status", jar)
        self.assertEqual(status, 200)
        self.assertIn(json.loads(body)["status"], ("pending", "approved", "refused"))


if __name__ == "__main__":
    unittest.main()
