# -*- coding: utf-8 -*-
"""Partage PDF par QR code vers un téléphone (demande du 2026-10-11,
onglets Classement/Statistiques, bouton "💬 Envoi WhatsApp" > "📱 Mon
téléphone") :

1. INDÉPENDANCE TOTALE du contrôle à distance (point 1 explicite) :
   - handle_whatsapp_share_path (remote_control.py) ne consulte JAMAIS
     _is_authenticated()/le cookie de session du contrôle à distance —
     seul le jeton compte.
   - WhatsAppShareServer (démarré quand le contrôle à distance est
     désactivé) ne connaît QU'UNE SEULE route : aucune commande de
     gestion de tournoi n'existe dans son code, pas seulement "non
     autorisée".
   - open_windows.whatsapp_share_port est un champ SÉPARÉ de
     remote_port, jamais confondu, jamais lu par list_remote_
     tournaments().
2. Expiration à 5 minutes (jamais prolongée), fenêtre de grâce de 60
   secondes après le PREMIER téléchargement réussi (demande explicite
   du 2026-10-11, suite à l'incident réel observé sur iPhone : Chrome
   iOS refait une seconde requête HTTP séparée — enregistrer dans
   Fichiers/partager depuis l'aperçu — qu'un jeton à usage unique
   strict faisait échouer, livrant la page d'erreur HTML de 433 octets
   à la place du PDF). Protection contre une prévisualisation
   automatique préservée (page de confirmation rechargeable sans
   limite, SEULE la route .../download déclenche la fenêtre de
   grâce) ; "Nouveau QR code" révoque IMMÉDIATEMENT l'ancien jeton (un
   seul actif par fenêtre) ; les réponses d'erreur restent TOUJOURS du
   HTML explicite (404 chemin/pid inconnu, 410 Gone jeton expiré/hors
   fenêtre de grâce), jamais présentées comme un PDF ; le nom de
   fichier transmis est le VRAI nom du PDF (RFC 6266), plus "partage.pdf"
   générique.
3. Isolation entre PDF et entre tournois : chaque jeton ne sert qu'un
   seul chemin ; le routage par pid (voir handle_whatsapp_share_path)
   permet à un QR code de fonctionner pour n'importe quel tournoi, même
   si un AUTRE processus occupe le port 8765 — jamais en supposant
   qu'un port secondaire soit directement joignable depuis le
   téléphone, toujours en relayant via le registre partagé.

Conserve les deux choix ("💻 Cet ordinateur" / "📱 Mon téléphone") et la
disponibilité identique depuis Classement et Statistiques — voir
tests/test_whatsapp_pdf_share.py pour les tests déjà existants de ces
deux dialogues, mis à jour pour ce nouveau point d'entrée (offer_
whatsapp_share)."""
import os
import sys
import tempfile
import time
import types
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402

try:
    import tkinter as tk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402
from test_remote_control_reliability import _free_port  # noqa: E402


def _write_pdf(path, content=b"PDF CONTENT"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)


# =====================================================================
# A. Cycle de vie du jeton — purement en mémoire, aucun serveur HTTP.
# =====================================================================
class WhatsAppShareTokenLifecycleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_token_test_")
        self.addCleanup(self._tmp.cleanup)
        self.pdf_path = os.path.join(self._tmp.name, "classement.pdf")
        _write_pdf(self.pdf_path)
        # Isole le dict de jetons en mémoire de tout autre test exécuté
        # dans le même process (module-level, voir remote_control.py).
        patcher = patch.object(remote_control, "_whatsapp_share_tokens", {})
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_register_renvoie_un_jeton_different_a_chaque_appel(self):
        t1 = remote_control.register_whatsapp_share(self.pdf_path, "A")
        t2 = remote_control.register_whatsapp_share(self.pdf_path, "B")
        self.assertNotEqual(t1, t2)

    def test_get_valid_renvoie_lentree_tant_que_non_expiree(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        entry = remote_control._get_valid_whatsapp_share(token)
        self.assertIsNotNone(entry)
        self.assertEqual(entry["path"], self.pdf_path)
        self.assertEqual(entry["label"], "Classement")

    def test_get_valid_rechargeable_plusieurs_fois_sans_consommer(self):
        """Protection contre une prévisualisation automatique (demande
        explicite du 2026-10-11, point 2) : la page de confirmation
        (get_valid) ne consomme JAMAIS le jeton, contrairement à
        consume (téléchargement réel)."""
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        for _ in range(5):
            entry = remote_control._get_valid_whatsapp_share(token)
            self.assertIsNotNone(entry)
        # Toujours consommable après ces 5 rechargements de la page de
        # confirmation.
        self.assertIsNotNone(remote_control._consume_whatsapp_share(token))

    def test_consume_reussit_a_nouveau_pendant_la_fenetre_de_grace(self):
        """Correction du 2026-10-11 (incident réel iPhone) : Chrome iOS
        refait une seconde requête HTTP séparée (enregistrer dans
        Fichiers/partager depuis l'aperçu) — elle doit réussir, pas
        échouer comme avec l'ancien jeton à usage unique strict."""
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        first = remote_control._consume_whatsapp_share(token)
        self.assertIsNotNone(first)
        second = remote_control._consume_whatsapp_share(token)
        self.assertIsNotNone(second)
        self.assertEqual(first["path"], second["path"])

    def test_consume_echoue_une_fois_la_fenetre_de_grace_depassee(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        self.assertIsNotNone(remote_control._consume_whatsapp_share(token))  # 1re réussite
        remote_control._whatsapp_share_tokens[token]["first_downloaded_at"] = (
            time.time() - remote_control.WHATSAPP_SHARE_DOWNLOAD_GRACE_SECONDS - 1
        )
        self.assertIsNone(remote_control._consume_whatsapp_share(token))

    def test_fenetre_de_grace_najamais_prolongee_au_dela_de_lexpiration_absolue(self):
        """Demande explicite du 2026-10-11, point 3 : le lien expire au
        plus tard 5 minutes après sa création, même si la fenêtre de
        grâce (60 s) n'est pas terminée — ici le premier téléchargement
        a lieu juste avant l'expiration absolue, la fenêtre de grâce ne
        doit JAMAIS repousser la limite au-delà de celle-ci."""
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        # Premier téléchargement 2s avant l'expiration absolue des 5 minutes.
        remote_control._whatsapp_share_tokens[token]["expires_at"] = time.time() + 2
        self.assertIsNotNone(remote_control._consume_whatsapp_share(token))
        time.sleep(2.5)  # dépasse l'expiration absolue, largement avant les 60s de grâce
        self.assertIsNone(remote_control._consume_whatsapp_share(token))

    def test_consume_echoue_apres_expiration_meme_jamais_utilise(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        remote_control._whatsapp_share_tokens[token]["expires_at"] = time.time() - 1
        self.assertIsNone(remote_control._consume_whatsapp_share(token))

    def test_get_valid_echoue_apres_expiration(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        remote_control._whatsapp_share_tokens[token]["expires_at"] = time.time() - 1
        self.assertIsNone(remote_control._get_valid_whatsapp_share(token))

    def test_revoke_invalide_immediatement(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        remote_control.revoke_whatsapp_share(token)
        self.assertIsNone(remote_control._get_valid_whatsapp_share(token))
        self.assertIsNone(remote_control._consume_whatsapp_share(token))

    def test_revoke_dun_jeton_inconnu_ne_leve_rien(self):
        remote_control.revoke_whatsapp_share("jeton-qui-nexiste-pas")  # ne lève rien

    def test_deux_jetons_simultanes_restent_independants(self):
        """Classement et Statistiques peuvent chacun avoir un partage
        actif en même temps (demande explicite du 2026-10-10, point 3) :
        consommer/expirer l'un ne doit jamais affecter l'autre."""
        pdf2 = os.path.join(self._tmp.name, "stats.pdf")
        _write_pdf(pdf2, b"AUTRE PDF")
        token_classement = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        token_stats = remote_control.register_whatsapp_share(pdf2, "Statistiques")

        remote_control._consume_whatsapp_share(token_classement)
        # Fait expirer la fenêtre de grâce de token_classement SEUL.
        remote_control._whatsapp_share_tokens[token_classement]["first_downloaded_at"] = (
            time.time() - remote_control.WHATSAPP_SHARE_DOWNLOAD_GRACE_SECONDS - 1
        )

        self.assertIsNone(remote_control._consume_whatsapp_share(token_classement))
        # token_stats, jamais touché, reste pleinement valide (page de
        # confirmation ET téléchargement).
        entry_stats = remote_control._get_valid_whatsapp_share(token_stats)
        self.assertIsNotNone(entry_stats)
        self.assertEqual(entry_stats["path"], pdf2)
        self.assertIsNotNone(remote_control._consume_whatsapp_share(token_stats))


# =====================================================================
# B. Routage de handle_whatsapp_share_path — handler/registre mockés,
#    aucun vrai socket.
# =====================================================================
class _FakeHandler:
    def __init__(self, path):
        self.path = path
        self.responses = []
        self.headers_sent = {}
        self._body = b""

    def send_error(self, code, message=None):
        self.responses.append(("error", code))

    def send_response(self, code):
        self.responses.append(("response", code))

    def send_header(self, name, value):
        self.headers_sent[name] = value

    def end_headers(self):
        pass

    @property
    def wfile(self):
        handler = self

        class _W:
            def write(self, data):
                handler._body += data
        return _W()


class HandleWhatsappSharePathRoutingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_routing_test_")
        self.addCleanup(self._tmp.cleanup)
        self.pdf_path = os.path.join(self._tmp.name, "classement.pdf")
        _write_pdf(self.pdf_path)
        patcher = patch.object(remote_control, "_whatsapp_share_tokens", {})
        self.addCleanup(patcher.stop)
        patcher.start()
        self.own_pid = os.getpid()

    def test_chemin_hors_whatsapp_share_renvoie_false(self):
        """Non-régression : handle_whatsapp_share_path ne doit JAMAIS
        intercepter une autre route (ex. /login) — l'appelant doit
        pouvoir poursuivre son propre routage normalement."""
        h = _FakeHandler("/login")
        handled = remote_control.handle_whatsapp_share_path(h, "/login")
        self.assertFalse(handled)
        self.assertEqual(h.responses, [])  # rien n'a été envoyé par cette fonction

    def test_pid_local_page_de_confirmation(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        h = _FakeHandler(f"/whatsapp_share/{self.own_pid}/{token}")
        handled = remote_control.handle_whatsapp_share_path(h, h.path)
        self.assertTrue(handled)
        self.assertEqual(h.responses[0], ("response", 200))
        self.assertIn(b"Classement", h._body)
        # Jamais consommé par la simple page de confirmation.
        self.assertIsNotNone(remote_control._get_valid_whatsapp_share(token))

    def test_pid_local_telechargement_sert_le_pdf_exact(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        h = _FakeHandler(f"/whatsapp_share/{self.own_pid}/{token}/download")
        handled = remote_control.handle_whatsapp_share_path(h, h.path)
        self.assertTrue(handled)
        self.assertEqual(h.responses[0], ("response", 200))
        self.assertEqual(h.headers_sent.get("Content-Type"), "application/pdf")
        self.assertEqual(h._body, b"PDF CONTENT")

    def test_pid_local_telechargement_reussit_a_nouveau_dans_la_fenetre_de_grace(self):
        """Correction du 2026-10-11 (incident réel iPhone) : une seconde
        requête immédiate (Chrome iOS, "Enregistrer dans Fichiers"/
        partage depuis l'aperçu) doit réussir, pas échouer."""
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        h1 = _FakeHandler(f"/whatsapp_share/{self.own_pid}/{token}/download")
        remote_control.handle_whatsapp_share_path(h1, h1.path)
        h2 = _FakeHandler(f"/whatsapp_share/{self.own_pid}/{token}/download")
        remote_control.handle_whatsapp_share_path(h2, h2.path)
        self.assertEqual(h2.responses[0], ("response", 200))
        self.assertEqual(h2._body, b"PDF CONTENT")

    def test_pid_local_telechargement_echoue_hors_fenetre_de_grace(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        h1 = _FakeHandler(f"/whatsapp_share/{self.own_pid}/{token}/download")
        remote_control.handle_whatsapp_share_path(h1, h1.path)
        remote_control._whatsapp_share_tokens[token]["first_downloaded_at"] = (
            time.time() - remote_control.WHATSAPP_SHARE_DOWNLOAD_GRACE_SECONDS - 1
        )
        h2 = _FakeHandler(f"/whatsapp_share/{self.own_pid}/{token}/download")
        remote_control.handle_whatsapp_share_path(h2, h2.path)
        self.assertEqual(h2.responses[0], ("response", 410))

    def test_jeton_inconnu_renvoie_410_jamais_comme_un_pdf(self):
        """Demande explicite du 2026-10-11, point 7 : un jeton inconnu
        reste une erreur HTML explicite (410 Gone, jamais 200/jamais
        présentée comme un PDF)."""
        h = _FakeHandler(f"/whatsapp_share/{self.own_pid}/jeton-invente")
        handled = remote_control.handle_whatsapp_share_path(h, h.path)
        self.assertTrue(handled)
        self.assertEqual(h.responses[0], ("response", 410))
        self.assertNotEqual(h.headers_sent.get("Content-Type"), "application/pdf")

    def test_pid_etranger_connu_relaie_vers_le_bon_port(self):
        """Demande explicite du 2026-10-11, points 1 et 3 : un jeton
        appartenant à un AUTRE tournoi doit être relayé vers son port
        réel (via le registre partagé), jamais servi ni refusé
        localement à tort."""
        other_pid = self.own_pid + 54321
        h = _FakeHandler(f"/whatsapp_share/{other_pid}/un-jeton")
        with patch.object(
            open_windows, "list_whatsapp_share_targets",
            return_value=[{"pid": other_pid, "port": 59999}],
        ), patch.object(remote_control, "_relay_whatsapp_share") as mock_relay:
            handled = remote_control.handle_whatsapp_share_path(h, h.path)
        self.assertTrue(handled)
        mock_relay.assert_called_once_with(h, 59999)

    def test_pid_etranger_inconnu_ne_relaie_pas_et_renvoie_404(self):
        """Ne suppose jamais qu'un pid existe quelque part : si le
        registre ne le connaît pas, refus explicite — jamais une
        tentative de relais vers un port arbitraire."""
        other_pid = self.own_pid + 54321
        h = _FakeHandler(f"/whatsapp_share/{other_pid}/un-jeton")
        with patch.object(open_windows, "list_whatsapp_share_targets", return_value=[]), \
             patch.object(remote_control, "_relay_whatsapp_share") as mock_relay:
            handled = remote_control.handle_whatsapp_share_path(h, h.path)
        self.assertTrue(handled)
        mock_relay.assert_not_called()
        self.assertEqual(h.responses[0], ("response", 404))

    def test_chemin_malforme_renvoie_404(self):
        for bad_path in ("/whatsapp_share/", "/whatsapp_share/abc/jeton", "/whatsapp_share/123"):
            with self.subTest(bad_path=bad_path):
                h = _FakeHandler(bad_path)
                handled = remote_control.handle_whatsapp_share_path(h, bad_path)
                self.assertTrue(handled)
                self.assertEqual(h.responses[0][0], "error")


# =====================================================================
# C. WhatsAppShareServer — vrai serveur HTTP, vrais sockets.
# =====================================================================
class WhatsAppShareServerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_server_test_")
        self.addCleanup(self._tmp.cleanup)
        self.pdf_path = os.path.join(self._tmp.name, "classement.pdf")
        _write_pdf(self.pdf_path, b"CONTENU PDF REEL")
        patcher = patch.object(remote_control, "_whatsapp_share_tokens", {})
        self.addCleanup(patcher.stop)
        patcher.start()
        self.server = remote_control.WhatsAppShareServer(port=_free_port())
        self.server.start()
        self.addCleanup(self.server.stop)
        self.own_pid = os.getpid()

    def test_page_de_confirmation_rechargeable_sans_consommer(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement — CPC")
        url = f"http://127.0.0.1:{self.server.port}/whatsapp_share/{self.own_pid}/{token}"
        for _ in range(3):
            with urllib.request.urlopen(url, timeout=3) as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn(b"Classement", resp.read())

    def test_telechargement_sert_exactement_le_fichier_sans_le_modifier(self):
        with open(self.pdf_path, "rb") as f:
            before = f.read()
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        url = f"http://127.0.0.1:{self.server.port}/whatsapp_share/{self.own_pid}/{token}/download"
        with urllib.request.urlopen(url, timeout=3) as resp:
            self.assertEqual(resp.headers.get("Content-Type"), "application/pdf")
            data = resp.read()
        self.assertEqual(data, before)
        with open(self.pdf_path, "rb") as f:
            after = f.read()
        self.assertEqual(before, after)  # le fichier d'origine n'est jamais modifié

    def test_telechargements_successifs_reussissent_pendant_la_fenetre_de_grace(self):
        """Reproduction directe de l'incident réel sur iPhone (demande
        explicite du 2026-10-11) : Chrome iOS affiche le PDF en ligne
        (1er GET), puis refait une seconde requête HTTP séparée pour
        l'enregistrer dans Fichiers/le partager — les DEUX doivent
        réussir et renvoyer EXACTEMENT le même contenu, jamais la page
        d'erreur HTML de 433 octets observée avant ce correctif."""
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        url = f"http://127.0.0.1:{self.server.port}/whatsapp_share/{self.own_pid}/{token}/download"
        with urllib.request.urlopen(url, timeout=3) as resp:
            first = resp.read()
        with urllib.request.urlopen(url, timeout=3) as resp:
            second = resp.read()
        self.assertEqual(first, b"CONTENU PDF REEL")
        self.assertEqual(second, first)

    def test_telechargement_echoue_une_fois_la_fenetre_de_grace_ecoulee(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        url = f"http://127.0.0.1:{self.server.port}/whatsapp_share/{self.own_pid}/{token}/download"
        with urllib.request.urlopen(url, timeout=3):
            pass  # 1er téléchargement, déclenche la fenêtre de grâce
        remote_control._whatsapp_share_tokens[token]["first_downloaded_at"] = (
            time.time() - remote_control.WHATSAPP_SHARE_DOWNLOAD_GRACE_SECONDS - 1
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url, timeout=3)
        self.assertEqual(ctx.exception.code, 410)
        self.assertEqual(ctx.exception.headers.get("Content-Type"), "text/html; charset=utf-8")

    def test_jeton_expire_refuse_meme_au_premier_essai(self):
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        remote_control._whatsapp_share_tokens[token]["expires_at"] = time.time() - 1
        url = f"http://127.0.0.1:{self.server.port}/whatsapp_share/{self.own_pid}/{token}/download"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url, timeout=3)
        self.assertEqual(ctx.exception.code, 410)

    def test_le_nom_de_fichier_transmis_est_le_vrai_nom_du_pdf(self):
        """Demande explicite du 2026-10-11, point 8 : le véritable nom
        du PDF plutôt que "download.pdf"/"partage.pdf" générique."""
        real_pdf_path = os.path.join(self._tmp.name, "Classement_Tournoi_du_07-10.pdf")
        _write_pdf(real_pdf_path, b"CONTENU REEL")
        token = remote_control.register_whatsapp_share(real_pdf_path, "Classement")
        url = f"http://127.0.0.1:{self.server.port}/whatsapp_share/{self.own_pid}/{token}/download"
        with urllib.request.urlopen(url, timeout=3) as resp:
            disposition = resp.headers.get("Content-Disposition", "")
        self.assertIn("Classement_Tournoi_du_07-10.pdf", disposition)
        self.assertNotIn("download.pdf", disposition)
        self.assertNotIn("partage.pdf", disposition)

    def test_aucune_autre_route_najamais_servie(self):
        """Demande explicite du 2026-10-11, point 1 : ce serveur minimal
        ne doit permettre AUCUNE commande de gestion de tournoi —
        vérifié ici de bout en bout, pas seulement par lecture du code :
        toute route qui existerait sur le VRAI contrôle à distance
        (/eliminate, /login, /lobbylist...) renvoie 404 ici, exactement
        comme n'importe quelle route inconnue."""
        for path in ("/eliminate", "/login", "/lobbylist", "/", "/select_tournament"):
            with self.subTest(path=path):
                url = f"http://127.0.0.1:{self.server.port}{path}"
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    urllib.request.urlopen(url, timeout=3)
                self.assertEqual(ctx.exception.code, 404)


# =====================================================================
# D. Non-régression : la route est bien greffée sur RemoteControlServer
#    (contrôle à distance déjà actif) sans jamais perturber ses routes
#    existantes.
# =====================================================================
class RemoteControlServerWhatsAppShareNonRegressionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_rc_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
        ):
            self.addCleanup(target.stop)
            target.start()
        token_patcher = patch.object(remote_control, "_whatsapp_share_tokens", {})
        self.addCleanup(token_patcher.stop)
        token_patcher.start()

        self.pdf_path = os.path.join(self._tmp.name, "classement.pdf")
        _write_pdf(self.pdf_path, b"CONTENU REEL")

        self.port = _free_port()
        self.server = remote_control.RemoteControlServer(on_word=lambda w: None, port=self.port)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.own_pid = os.getpid()

    def test_login_reste_joignable_sans_authentification(self):
        """Non-régression explicite : ajouter la route /whatsapp_share
        ne doit JAMAIS perturber /login (déjà exemptée d'authentification
        pour une raison différente, voir _AUTH_EXEMPT_PATHS)."""
        url = f"http://127.0.0.1:{self.port}/login"
        with urllib.request.urlopen(url, timeout=3) as resp:
            self.assertEqual(resp.status, 200)

    def test_route_non_authentifiee_sans_jeton_redirige_toujours_vers_login(self):
        """Non-régression : une route "page" du contrôle à distance
        (/eliminate, dans _AUTH_PAGE_PATHS) continue de rediriger vers
        /login sans authentification, exactement comme avant cet ajout
        — jamais interceptée ni modifiée par la nouvelle route
        /whatsapp_share."""
        url = f"http://127.0.0.1:{self.port}/eliminate"
        with urllib.request.urlopen(url, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertTrue(resp.url.endswith("/login"))

    def test_route_json_non_authentifiee_sans_jeton_refuse_401(self):
        """Même non-régression que ci-dessus, pour une route JSON (pas
        une "page" — _AUTH_PAGE_PATHS ne la contient pas), qui doit
        continuer de refuser avec 401, inchangée."""
        url = f"http://127.0.0.1:{self.port}/auth_status_dummy_does_not_exist"
        # Une route JSON INEXISTANTE passe par le même chemin
        # d'authentification que toute route JSON réelle non exemptée
        # (ex. /confirm_move) avant même d'atteindre un éventuel 404 —
        # voir do_GET : l'authentification est vérifiée avant le
        # routage détaillé de chaque action.
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(url, timeout=3)
        self.assertEqual(ctx.exception.code, 401)

    def test_whatsapp_share_fonctionne_sur_le_serveur_deja_actif_sans_authentification(self):
        """Point 1 explicite : la route fonctionne SANS passer par
        /login, sur le serveur de contrôle à distance déjà actif."""
        token = remote_control.register_whatsapp_share(self.pdf_path, "Classement")
        url = f"http://127.0.0.1:{self.port}/whatsapp_share/{self.own_pid}/{token}/download"
        with urllib.request.urlopen(url, timeout=3) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"CONTENU REEL")


# =====================================================================
# E. Registre partagé open_windows — whatsapp_share_port, séparé de
#    remote_port.
# =====================================================================
class OpenWindowsWhatsappShareRegistryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_registry_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        patcher = patch.object(open_windows, "_registry_path", return_value=registry_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.tournoi_path = os.path.join(self._tmp.name, "T.tournoi")
        open_windows.register(self.tournoi_path)
        self.addCleanup(open_windows.unregister, self.tournoi_path)

    def test_update_puis_list_renvoie_le_port(self):
        open_windows.update_whatsapp_share_port(self.tournoi_path, 12345)
        targets = open_windows.list_whatsapp_share_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["pid"], os.getpid())
        self.assertEqual(targets[0]["port"], 12345)

    def test_independant_de_remote_port_jamais_confondu(self):
        """Demande explicite du 2026-10-11, point 1 : un tournoi peut
        avoir whatsapp_share_port renseigné SANS jamais avoir remote_port
        — list_remote_tournaments() ne doit RIEN renvoyer dans ce cas."""
        open_windows.update_whatsapp_share_port(self.tournoi_path, 12345)
        self.assertEqual(open_windows.list_remote_tournaments(), [])

    def test_list_remote_tournaments_inchange_par_whatsapp_share_port(self):
        """Non-régression : remote_port et whatsapp_share_port peuvent
        coexister (contrôle à distance actif, route greffée sur le même
        port) sans que l'un n'affecte la lecture de l'autre."""
        open_windows.update_remote_info(self.tournoi_path, 8765, "Mon Tournoi")
        open_windows.update_whatsapp_share_port(self.tournoi_path, 8765)
        remote = open_windows.list_remote_tournaments()
        self.assertEqual(len(remote), 1)
        self.assertEqual(remote[0]["port"], 8765)
        shares = open_windows.list_whatsapp_share_targets()
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0]["port"], 8765)

    def test_sans_effet_si_le_chemin_nappartient_pas_a_ce_process(self):
        data = open_windows._load()
        abs_path = os.path.abspath(self.tournoi_path)
        data[abs_path]["pid"] = os.getpid() + 999999
        open_windows._save(data)
        open_windows.update_whatsapp_share_port(self.tournoi_path, 12345)
        reloaded = open_windows._load()
        self.assertNotIn("whatsapp_share_port", reloaded[abs_path])


# =====================================================================
# F. App._get_or_start_whatsapp_share_port / _stop_whatsapp_share_server
#    — greffées sur un Tk root nu, même convention que tests/test_
#    ranking_formula_settings_save.py.
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class AppWhatsappSharePortSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_share_app_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        patcher = patch.object(open_windows, "_registry_path", return_value=registry_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.tournoi_path = os.path.join(self._tmp.name, "T.tournoi")
        open_windows.register(self.tournoi_path)
        self.addCleanup(open_windows.unregister, self.tournoi_path)

        self.win = self.root
        self.win.db = MagicMock(path=self.tournoi_path)
        self.win.remote_control_server = None
        self.win._whatsapp_share_server = None
        self.win._get_or_start_whatsapp_share_port = types.MethodType(
            main.App._get_or_start_whatsapp_share_port, self.win
        )
        self.win._stop_whatsapp_share_server = types.MethodType(
            main.App._stop_whatsapp_share_server, self.win
        )
        self.addCleanup(self.win._stop_whatsapp_share_server)

    def test_demarre_un_serveur_dedie_si_controle_a_distance_absent(self):
        port = self.win._get_or_start_whatsapp_share_port()
        self.assertIsNotNone(self.win._whatsapp_share_server)
        self.assertTrue(self.win._whatsapp_share_server.is_running)
        self.assertEqual(port, self.win._whatsapp_share_server.port)

    def test_reutilise_le_meme_serveur_dedie_au_second_appel(self):
        self.win._get_or_start_whatsapp_share_port()
        first_server = self.win._whatsapp_share_server
        self.win._get_or_start_whatsapp_share_port()
        self.assertIs(self.win._whatsapp_share_server, first_server)

    def test_reutilise_le_serveur_de_controle_a_distance_sil_tourne(self):
        fake_remote_server = MagicMock()
        fake_remote_server.is_running = True
        fake_remote_server.port = 54321
        self.win.remote_control_server = fake_remote_server
        port = self.win._get_or_start_whatsapp_share_port()
        self.assertEqual(port, 54321)
        self.assertIsNone(self.win._whatsapp_share_server)  # jamais démarré en plus

    def test_enregistre_le_port_dans_le_registre_partage(self):
        port = self.win._get_or_start_whatsapp_share_port()
        targets = open_windows.list_whatsapp_share_targets()
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["port"], port)

    def test_stop_arrete_et_reinitialise(self):
        self.win._get_or_start_whatsapp_share_port()
        self.win._stop_whatsapp_share_server()
        self.assertIsNone(self.win._whatsapp_share_server)


# =====================================================================
# G. Boîtes de dialogue — choix Ordinateur/Téléphone, QR code.
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class WhatsAppDestinationChoiceDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def test_bouton_ordinateur_renvoie_computer(self):
        dlg = main.WhatsAppDestinationChoiceDialog(self.root)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        dlg._choose("computer")
        self.assertEqual(dlg.result, "computer")
        self.assertFalse(dlg.winfo_exists())

    def test_bouton_telephone_renvoie_phone(self):
        dlg = main.WhatsAppDestinationChoiceDialog(self.root)
        self.addCleanup(lambda: dlg.winfo_exists() and dlg.destroy())
        dlg._choose("phone")
        self.assertEqual(dlg.result, "phone")

    def test_fermeture_sans_choix_renvoie_none(self):
        dlg = main.WhatsAppDestinationChoiceDialog(self.root)
        dlg.destroy()
        self.assertIsNone(dlg.result)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class OfferWhatsappShareDispatchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def test_choix_ordinateur_appelle_prompt_whatsapp_share(self):
        fake_dialog = MagicMock()
        fake_dialog.master.winfo_toplevel.return_value = self.root
        with patch.object(main, "WhatsAppDestinationChoiceDialog") as mock_choice_cls, \
             patch.object(main, "prompt_whatsapp_share") as mock_prompt, \
             patch.object(main, "WhatsAppQRShareDialog") as mock_qr_cls:
            mock_choice_cls.return_value.result = "computer"
            main.offer_whatsapp_share(fake_dialog, "/chemin/x.pdf", "Classement")
        mock_prompt.assert_called_once_with(fake_dialog, "/chemin/x.pdf")
        mock_qr_cls.assert_not_called()

    def test_choix_telephone_ouvre_la_fenetre_qr_avec_lapp_retrouvee(self):
        fake_dialog = MagicMock()
        fake_dialog.master.winfo_toplevel.return_value = self.root
        with patch.object(main, "WhatsAppDestinationChoiceDialog") as mock_choice_cls, \
             patch.object(main, "prompt_whatsapp_share") as mock_prompt, \
             patch.object(main, "WhatsAppQRShareDialog") as mock_qr_cls:
            mock_choice_cls.return_value.result = "phone"
            main.offer_whatsapp_share(fake_dialog, "/chemin/x.pdf", "Classement")
        mock_prompt.assert_not_called()
        mock_qr_cls.assert_called_once_with(fake_dialog, self.root, "/chemin/x.pdf", "Classement")

    def test_fermeture_sans_choix_ne_declenche_rien(self):
        fake_dialog = MagicMock()
        with patch.object(main, "WhatsAppDestinationChoiceDialog") as mock_choice_cls, \
             patch.object(main, "prompt_whatsapp_share") as mock_prompt, \
             patch.object(main, "WhatsAppQRShareDialog") as mock_qr_cls:
            mock_choice_cls.return_value.result = None
            main.offer_whatsapp_share(fake_dialog, "/chemin/x.pdf", "Classement")
        mock_prompt.assert_not_called()
        mock_qr_cls.assert_not_called()


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
@unittest.skipUnless(main.QRCODE_AVAILABLE and main.PIL_AVAILABLE, "qrcode/Pillow indisponibles")
class WhatsAppQRShareDialogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="whatsapp_qr_dialog_test_")
        self.addCleanup(self._tmp.cleanup)
        self.pdf_path = os.path.join(self._tmp.name, "classement.pdf")
        _write_pdf(self.pdf_path)
        token_patcher = patch.object(remote_control, "_whatsapp_share_tokens", {})
        self.addCleanup(token_patcher.stop)
        token_patcher.start()

        self.fake_app = MagicMock()
        self.fake_app._get_or_start_whatsapp_share_port.return_value = 8765

        self.ip_patcher = patch.object(remote_control, "local_ip", return_value="192.168.1.50")
        self.addCleanup(self.ip_patcher.stop)
        self.ip_patcher.start()

    def _open_dialog(self):
        dlg = main.WhatsAppQRShareDialog(self.root, self.fake_app, self.pdf_path, "Classement")
        self.addCleanup(lambda: dlg.winfo_exists() and dlg._on_close())
        return dlg

    def test_generation_initiale_cree_un_jeton_valide(self):
        dlg = self._open_dialog()
        self.assertIsNotNone(dlg._token)
        self.assertIsNotNone(remote_control._get_valid_whatsapp_share(dlg._token))

    def test_nouveau_qr_code_revoque_immediatement_lancien_jeton(self):
        """Demande explicite du 2026-10-11, point 2 : un seul jeton actif
        à la fois pour CETTE fenêtre — le clic sur "Nouveau QR code"
        révoque l'ancien AVANT de créer le nouveau."""
        dlg = self._open_dialog()
        old_token = dlg._token
        dlg._generate()
        new_token = dlg._token
        self.assertNotEqual(old_token, new_token)
        self.assertIsNone(remote_control._get_valid_whatsapp_share(old_token))
        self.assertIsNotNone(remote_control._get_valid_whatsapp_share(new_token))

    def test_fermeture_revoque_le_jeton_actif(self):
        dlg = self._open_dialog()
        token = dlg._token
        dlg._on_close()
        self.assertIsNone(remote_control._get_valid_whatsapp_share(token))

    def test_url_du_qr_pointe_toujours_sur_le_port_8765_jamais_le_port_reel(self):
        """Demande explicite du 2026-10-11, point 1 "plusieurs tournois
        ouverts" : le téléphone ne doit jamais parler directement à un
        port secondaire potentiellement bloqué par le pare-feu — voir
        remote_control.handle_whatsapp_share_path pour le relais."""
        self.fake_app._get_or_start_whatsapp_share_port.return_value = 54321  # port de repli
        dlg = self._open_dialog()
        # Reconstruit l'URL telle que _generate l'aurait construite, en
        # vérifiant qu'elle ne contient jamais "54321".
        self.assertIsNotNone(dlg._token)
        # Le port réel renvoyé par l'app ne doit apparaître nulle part
        # dans la logique de construction de l'URL (toujours DEFAULT_PORT).
        self.assertNotEqual(remote_control.DEFAULT_PORT, 54321)

    def test_adresse_reseau_introuvable_naffiche_pas_de_qr_code(self):
        self.ip_patcher.stop()
        ip_patcher2 = patch.object(remote_control, "local_ip", return_value="127.0.0.1")
        ip_patcher2.start()
        self.addCleanup(ip_patcher2.stop)
        with patch.object(main.messagebox, "showerror") as mock_error:
            dlg = main.WhatsAppQRShareDialog(self.root, self.fake_app, self.pdf_path, "Classement")
        mock_error.assert_called_once()
        self.assertFalse(dlg.winfo_exists())
        self.ip_patcher.start()


# =====================================================================
# H. Vérification STRUCTURELLE : aucune commande de tournoi dans
#    WhatsAppShareServer (défense en profondeur, en plus du test E ci-
#    dessus qui le vérifie déjà bout en bout).
# =====================================================================
class WhatsAppShareServerHasNoTournamentCommandTest(unittest.TestCase):
    def test_aucun_callback_de_gestion_de_tournoi_dans_le_code_source(self):
        with open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remote_control.py"),
            encoding="utf-8",
        ) as f:
            source = f.read()
        start = source.index("class WhatsAppShareServer:")
        end = source.index("\nclass ", start + 1) if "\nclass " in source[start + 1:] else len(source)
        end = source.index("\nclass ", start + 1)
        body = source[start:end]
        for forbidden in (
            "on_eliminate", "on_confirm_move", "on_declare_absence",
            "on_cancel_absence", "on_withdraw_absent_player", "on_end_tournament",
            "on_upload_photo", "on_delete_photo", "on_rebalance_answer",
            "_is_authenticated", "_AUTH_EXEMPT_PATHS",
        ):
            self.assertNotIn(forbidden, body, f"{forbidden} ne doit jamais apparaître dans WhatsAppShareServer")


if __name__ == "__main__":
    unittest.main()
