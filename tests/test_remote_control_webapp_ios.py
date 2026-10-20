# -*- coding: utf-8 -*-
"""Mode Web App iPhone / plein écran (demande du 2026-10-03, "SENACO
SUR IPHONE : MODE WEB APP / PLEIN ÉCRAN") — premier test réel,
préparation uniquement : balises iOS statiques sur les 9 pages HTML,
icône dédiée (`/apple-touch-icon.png`), manifest minimal
(`/manifest.json`, `"display": "standalone"`), les deux routes
exemptées d'authentification.

RAPPEL EXPLICITE (demande) : AUCUNE logique existante n'est touchée —
authentification, cookies (rc_bid/rc_auth/selected_pid), SHORTID,
ADMIN/DIRTO, permissions, lobby/multi-tournois, reconnexion. Ce fichier
vérifie PRÉCISÉMENT cette absence d'impact, en plus des nouveaux
éléments eux-mêmes. Le comportement des cookies en contexte standalone
iOS (partagés ou non avec Safari) reste délibérément NON modifié pour
ce premier essai — observé lors du test manuel réel sur iPhone, jamais
contourné ici.

Couvre :
1. Les 9 templates HTML (_NO_ACCESS_PAGE, _FORBIDDEN_PAGE, _LOGIN_PAGE,
   _PAGE_TEMPLATE, _LOBBY_PAGE, _ELIMINATE_PAGE, _PHOTOS_PAGE,
   _MOVES_PAGE, _CHECKIN_PAGE) contiennent bien les 4 balises iOS et
   les 2 liens (icône, manifest), avec le nom "Senaco" exact.
2. _APPLE_TOUCH_ICON_PNG est un PNG valide, carré, 180x180.
3. _MANIFEST_JSON a la forme attendue ("Senaco", "standalone", icône).
4. /apple-touch-icon.png et /manifest.json répondent 200 avec le bon
   Content-Type, SANS aucun cookie (ni fourni, ni posé en retour) — au
   niveau HTTP réel (vrai RemoteControlServer).
5. Ces deux routes répondent IDENTIQUEMENT authentifié ou non — aucun
   couplage avec le mécanisme d'authentification.
6. Non-régression explicite, au niveau HTTP réel : une route protégée
   existante (/players) exige toujours une authentification (401) ;
   la page principale ("/") redirige toujours vers /login (302) sans
   session ; un appareil ADMIN authentifié accède toujours normalement
   à "/" (200) — rien de tout cela n'est affecté par les deux nouvelles
   routes exemptées."""
import http.cookiejar
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402
import roster  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False



_TEMPLATES = {
    "_NO_ACCESS_PAGE": remote_control._NO_ACCESS_PAGE,
    "_FORBIDDEN_PAGE": remote_control._FORBIDDEN_PAGE,
    "_LOGIN_PAGE": remote_control._LOGIN_PAGE,
    "_PAGE_TEMPLATE": remote_control._PAGE_TEMPLATE,
    "_LOBBY_PAGE": remote_control._LOBBY_PAGE,
    "_ELIMINATE_PAGE": remote_control._ELIMINATE_PAGE,
    "_PHOTOS_PAGE": remote_control._PHOTOS_PAGE,
    "_MOVES_PAGE": remote_control._MOVES_PAGE,
    "_CHECKIN_PAGE": remote_control._CHECKIN_PAGE,
}


# =========================================================================
# 1-3. Structure statique — balises, icône, manifest (aucun serveur requis)
# =========================================================================
class IosWebAppStaticTagsTest(unittest.TestCase):
    def test_les_9_templates_contiennent_les_balises_ios(self):
        for name, template in _TEMPLATES.items():
            with self.subTest(template=name):
                self.assertIn('name="apple-mobile-web-app-capable" content="yes"', template)
                self.assertIn('name="apple-mobile-web-app-title" content="Senaco"', template)
                self.assertIn('name="apple-mobile-web-app-status-bar-style"', template)
                self.assertIn('rel="apple-touch-icon" href="/apple-touch-icon.png"', template)
                self.assertIn('rel="manifest" href="/manifest.json"', template)

    def test_chaque_template_a_exactement_une_occurrence_de_chaque_balise(self):
        """Non-régression de forme : un replace_all mal ciblé aurait pu
        dupliquer les balises dans un même <head> — vérifié explicitement
        ici, une fois pour chaque page."""
        for name, template in _TEMPLATES.items():
            with self.subTest(template=name):
                self.assertEqual(template.count("apple-mobile-web-app-capable"), 1)
                self.assertEqual(template.count('rel="apple-touch-icon"'), 1)
                self.assertEqual(template.count('rel="manifest"'), 1)

    def test_nom_senaco_pas_un_autre_nom(self):
        self.assertIn('content="Senaco"', remote_control._PAGE_TEMPLATE)
        self.assertNotIn('apple-mobile-web-app-title" content="Contrôle', remote_control._PAGE_TEMPLATE)


@unittest.skipUnless(_PIL_AVAILABLE, "Pillow indisponible dans cet environnement")
class AppleTouchIconAssetTest(unittest.TestCase):
    def test_icone_est_un_png_carre_180(self):
        img = Image.open(io.BytesIO(remote_control._APPLE_TOUCH_ICON_PNG))
        self.assertEqual(img.format, "PNG")
        self.assertEqual(img.size, (180, 180))

    def test_icone_opaque_pas_de_canal_alpha_transparent(self):
        """Apple recommande une icône opaque — vérifie qu'aucun pixel
        n'est transparent (évite un rendu avec fond noir imprévisible)."""
        img = Image.open(io.BytesIO(remote_control._APPLE_TOUCH_ICON_PNG)).convert("RGBA")
        alpha_min, alpha_max = img.getchannel("A").getextrema()
        self.assertEqual((alpha_min, alpha_max), (255, 255))


class ManifestJsonContentTest(unittest.TestCase):
    def test_manifest_a_la_forme_attendue(self):
        manifest = remote_control._MANIFEST_JSON
        self.assertEqual(manifest["name"], "Senaco")
        self.assertEqual(manifest["short_name"], "Senaco")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(len(manifest["icons"]), 1)
        self.assertEqual(manifest["icons"][0]["src"], "/apple-touch-icon.png")

    def test_manifest_est_json_serialisable(self):
        """Lève une exception si jamais rendu non sérialisable par un
        futur changement (ex. un objet non-JSON glissé par erreur)."""
        json.dumps(remote_control._MANIFEST_JSON)


class AuthExemptPathsTest(unittest.TestCase):
    def test_les_deux_nouvelles_routes_sont_exemptees(self):
        self.assertIn("/apple-touch-icon.png", remote_control._AUTH_EXEMPT_PATHS)
        self.assertIn("/manifest.json", remote_control._AUTH_EXEMPT_PATHS)

    def test_les_routes_historiques_restent_exemptees_elles_aussi(self):
        """Non-régression explicite : l'ajout n'a pas remplacé
        l'ensemble, seulement complété."""
        for path in ("/login", "/authenticate", "/auth_status"):
            self.assertIn(path, remote_control._AUTH_EXEMPT_PATHS)


# =========================================================================
# 4-6. Niveau HTTP réel (vrai RemoteControlServer) — icône/manifest ET
#      non-régression de l'authentification/des cookies existants.
# =========================================================================
class _RemoteControlWebAppHttpTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_webapp_ios_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
            patch.object(roster, "get_group", return_value=roster.ROSTER_GROUP_ADMIN),
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

        # port=0 (demande du 2026-10-20) : voir tests/test_remote_control_
        # reliability.py:_RemoteControlHttpTestCase.setUp pour le détail
        # du correctif (ConnectionRefusedError intermittent).
        self.server = remote_control.RemoteControlServer(on_word=lambda w: None, port=0)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"

    def _req(self, method, path, jar=None, body=None):
        return http_request(self.base, method, path, jar, body=body)


class IconAndManifestHttpTest(_RemoteControlWebAppHttpTestCase):
    def test_icone_accessible_sans_authentification(self):
        status, body, headers = self._req("GET", "/apple-touch-icon.png")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "image/png")
        self.assertEqual(body, remote_control._APPLE_TOUCH_ICON_PNG)

    def test_manifest_accessible_sans_authentification(self):
        status, body, headers = self._req("GET", "/manifest.json")

        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/json")
        self.assertEqual(json.loads(body), remote_control._MANIFEST_JSON)

    def test_icone_ne_pose_aucun_cookie(self):
        """Confirme l'absence totale de couplage avec rc_bid/rc_auth :
        une requête sans jar ne doit en recevoir aucun en retour."""
        status, body, headers = self._req("GET", "/apple-touch-icon.png")

        self.assertNotIn("Set-Cookie", headers)

    def test_manifest_ne_pose_aucun_cookie(self):
        status, body, headers = self._req("GET", "/manifest.json")

        self.assertNotIn("Set-Cookie", headers)

    def test_icone_identique_authentifie_ou_non(self):
        jar = authenticated_jar(self.base, owner_name="Alice")
        status_auth, body_auth, _ = self._req("GET", "/apple-touch-icon.png", jar)
        status_anon, body_anon, _ = self._req("GET", "/apple-touch-icon.png")

        self.assertEqual((status_auth, body_auth), (200, body_anon))
        self.assertEqual(status_anon, 200)

    def test_manifest_identique_authentifie_ou_non(self):
        jar = authenticated_jar(self.base, owner_name="Alice")
        status_auth, body_auth, _ = self._req("GET", "/manifest.json", jar)
        status_anon, body_anon, _ = self._req("GET", "/manifest.json")

        self.assertEqual((status_auth, body_auth), (200, body_anon))
        self.assertEqual(status_anon, 200)


class AuthenticationUnaffectedTest(_RemoteControlWebAppHttpTestCase):
    """Non-régression explicite demandée : les deux nouvelles routes
    exemptées n'affaiblissent RIEN d'autre — toute route protégée reste
    protégée exactement comme avant."""

    def test_route_protegee_existante_exige_toujours_une_authentification(self):
        status, body, _ = self._req("GET", "/players")

        self.assertEqual(status, 401)

    def test_page_principale_redirige_toujours_vers_login_sans_session(self):
        status, body, headers = self._req("GET", "/")

        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")

    def test_admin_authentifie_accede_toujours_normalement_a_la_page_principale(self):
        jar = authenticated_jar(self.base, owner_name="Alice")

        status, body, _ = self._req("GET", "/", jar)

        self.assertEqual(status, 200)
        self.assertIn(b'apple-mobile-web-app-title" content="Senaco"', body)

    def test_authenticate_login_auth_status_toujours_exemptes(self):
        """Les routes historiquement exemptées ne sont pas devenues, par
        erreur, protégées à la suite de l'ajout des deux nouvelles."""
        status, _, _ = self._req("GET", "/auth_status")
        self.assertNotEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
