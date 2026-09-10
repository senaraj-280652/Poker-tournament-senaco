"""Test d'intégration ciblé : l'écran principal iPhone (et le message de
confirmation "Fin de la partie") doit toujours refléter le tournoi
RÉELLEMENT sélectionné (cookie "selected_pid"), jamais celui qui héberge
le port 8765 par accident de relais.

Cause du bug : Handler._proxy() (remote_control.py) ne relayait pas
l'en-tête Cookie vers le process cible. Ce cookie porte la sélection du
téléphone (voir /select_tournament) ; sans lui, le SECOND saut (routeur
-> tournoi réellement sélectionné) réévalue "qui est courant ?" en
l'absence de toute sélection et peut retomber sur un troisième tournoi
(celui le plus récemment ouvert, voir resolve_current_pid) au lieu de
conclure "c'est moi, sers localement" — le nom affiché ne correspondait
alors plus au tournoi sélectionné, et rechargeait indéfiniment la même
valeur incorrecte (aucun rapport avec un cache : chaque requête relayée
reproduit exactement la même erreur de routage).

Utilise 2 VRAIS processus séparés (comme test_menu_principal_after_
iphone_close.py) — un registre mocké n'aurait pas suffi : ce bug est
spécifiquement dans le relais HTTP RÉEL entre deux vrais serveurs."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import open_windows  # noqa: E402

MAIN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def _get_text(url, cookie=None, timeout=5):
    req = urllib.request.Request(url)
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def _select_tournament_cookie(any_entry_port, target_pid, auth_cookie=None, timeout=5):
    """Voir tests/test_menu_principal_after_iphone_close.py — même
    principe, reproduit ici pour rester un fichier autonome. `auth_
    cookie` (voir _authenticate, demande du 2026-09-09) : requis depuis
    que /select_tournament exige un appareil authentifié/approuvé."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"http://127.0.0.1:{any_entry_port}/select_tournament?pid={target_pid}")
    if auth_cookie:
        req.add_header("Cookie", auth_cookie)
    try:
        resp = opener.open(req, timeout=timeout)
        cookie = resp.headers.get("Set-Cookie")
        resp.close()
        return cookie
    except urllib.error.HTTPError as e:
        cookie = e.headers.get("Set-Cookie")
        e.close()
        return cookie


def _authenticate(entry_port, register_cleanup, timeout=5):
    """Voir tests/test_menu_principal_after_iphone_close.py — même
    principe (docstring complète là-bas), reproduit ici pour rester un
    fichier autonome : authentifie ce test comme un téléphone APPROUVÉ
    pour la session réelle en cours, nécessaire depuis l'ajout du code
    à 6 chiffres + approbation par appareil (demande du 2026-09-09).
    `register_cleanup` (typiquement self.addCleanup) révoque l'appareil
    de test à la fin, pour ne pas laisser une entrée "approuvée" dans le
    VRAI registre partagé de l'utilisateur. Renvoie l'en-tête Cookie
    combiné (rc_bid + rc_auth)."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)

    def _cookie_value(headers, name):
        for line in headers.get_all("Set-Cookie") or []:
            if line.startswith(name + "="):
                return line.split(";", 1)[0]
        return None

    resp = opener.open(urllib.request.Request(f"http://127.0.0.1:{entry_port}/login"), timeout=timeout)
    rc_bid = _cookie_value(resp.headers, "rc_bid")
    resp.close()
    if not rc_bid:
        raise AssertionError("rc_bid non reçu depuis /login")
    browser_id = rc_bid.split("=", 1)[1]

    code = open_windows.remote_session_code()
    if not code:
        raise AssertionError("aucune session de contrôle à distance active (aucun tournoi ouvert ?)")

    def _authenticate_once():
        data = json.dumps({"code": code}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{entry_port}/authenticate", data=data, method="POST",
            headers={"Content-Type": "application/json", "Cookie": rc_bid},
        )
        resp = opener.open(req, timeout=timeout)
        body = json.loads(resp.read().decode("utf-8"))
        rc_auth = _cookie_value(resp.headers, "rc_auth")
        resp.close()
        return body, rc_auth

    body, _ = _authenticate_once()
    if body != {"ok": True, "status": "pending"}:
        raise AssertionError(f"/authenticate (1ère fois) inattendu : {body!r}")

    register_cleanup(open_windows.revoke_remote_device, browser_id)
    if not open_windows.approve_remote_device(browser_id, label="Test intégration (jetable)"):
        raise AssertionError("approve_remote_device a échoué de façon inattendue")

    body, rc_auth = _authenticate_once()
    if body != {"ok": True, "status": "approved"} or not rc_auth:
        raise AssertionError(f"/authenticate (2e fois, après approbation) inattendu : {body!r}, rc_auth={rc_auth!r}")

    return f"{rc_bid}; {rc_auth}"


def _wait_for_remote_port(path, timeout=15):
    deadline = time.time() + timeout
    abs_path = os.path.abspath(path)
    while time.time() < deadline:
        for t in open_windows.list_remote_tournaments():
            if open_windows.find_path_for_pid(t["pid"]) == abs_path:
                return t["port"]
        time.sleep(0.2)
    return None


@unittest.skipUnless(
    os.environ.get("POKER_RUN_SLOW_INTEGRATION_TESTS") == "1",
    "Test d'intégration lent (2 vrais sous-processus) : mettre "
    "POKER_RUN_SLOW_INTEGRATION_TESTS=1 pour l'exécuter explicitement.",
)
class MainScreenReflectsSelectedTournamentTest(unittest.TestCase):
    def setUp(self):
        self.procs = []
        self.addCleanup(self._kill_all)
        self.tmpdir = tempfile.mkdtemp(prefix="poker_name_test_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)

    def _kill_all(self):
        for p in self.procs:
            try:
                p.terminate()
            except OSError:
                pass
        deadline = time.time() + 3
        for p in self.procs:
            remaining = max(0, deadline - time.time())
            try:
                p.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except OSError:
                    pass

    def _spawn_tournament(self, name):
        """Crée le fichier .tournoi et lui donne un nom DISTINCTIF
        ("_scratch_A"/"_scratch_B", jamais le nom par défaut "Nouveau
        tournoi" identique pour tous) AVANT de lancer le vrai process
        dessus — une simple Database() locale, fermée aussitôt, pour ne
        jamais se retrouver avec 2 connexions SQLite concurrentes sur le
        même fichier."""
        path = os.path.join(self.tmpdir, f"_scratch_{name}.tournoi")
        db = database.Database(path)
        display_name = f"_scratch_{name}"
        db.set_setting("tournament_name", display_name)
        db.conn.close()

        proc = subprocess.Popen([sys.executable, MAIN_PY, path])
        self.procs.append(proc)
        port = _wait_for_remote_port(path)
        self.assertIsNotNone(
            port, f"le contrôle à distance de {name} n'a jamais démarré (Paramètres > "
                  "Contrôle à distance doit être activé globalement pour ce test)",
        )
        return proc.pid, port

    def test_ecran_principal_et_confirmation_refletent_le_tournoi_selectionne(self):
        # A ouvert en premier (tient le port 8765 "routeur" le cas
        # échéant), B ensuite, PUIS C — le plus récent des trois. On
        # sélectionne B (ni le routeur, ni le plus récent) : c'est
        # EXACTEMENT le cas qui démasque le bug (sans le relais du
        # Cookie par _proxy, le 2e saut vers B retombait, faute de
        # cookie, sur "le plus récent" = C, pas B). Sélectionner
        # justement le plus récent (C) aurait masqué le bug par
        # coïncidence (voir le commentaire de resolve_current_pid).
        pid_a, port_a = self._spawn_tournament("A")
        pid_b, port_b = self._spawn_tournament("B")
        _pid_c, _port_c = self._spawn_tournament("C")

        # Authentification (demande du 2026-09-09, "sécurisation du
        # contrôle à distance") : UNE SEULE fois, l'état est partagé
        # entre tous les processus de la session via open_windows.
        auth_cookie = _authenticate(port_a, self.addCleanup)

        # Sélectionne explicitement B depuis le téléphone (via A, peu
        # importe le port interrogé — voir _handle_select_tournament).
        selection_cookie = _select_tournament_cookie(port_a, pid_b, auth_cookie=auth_cookie)
        self.assertIsNotNone(selection_cookie)
        cookie = f"{auth_cookie}; {selection_cookie}"

        # 3) Le nom affiché sur l'écran principal (chargé via le port de
        # A, avec la sélection de B) doit être celui de B, pas de A.
        # `[^>]*` avant le `>` : tolère un attribut supplémentaire sur
        # cette balise (ex : id="tournoi-name", voir le correctif de
        # reconnexion automatique après "Fin de la partie") sans lier ce
        # test à l'ordre exact des attributs HTML.
        html = _get_text(f"http://127.0.0.1:{port_a}/", cookie=cookie)
        m = re.search(r'class="tournoi"[^>]*>([^<]+)<', html)
        self.assertIsNotNone(m, "nom du tournoi introuvable dans la page")
        self.assertEqual(m.group(1), "_scratch_B")

        # own_pid embarqué (voir /end_tournament) doit être celui de B :
        # le message de confirmation agira bien sur LE BON tournoi.
        m_pid = re.search(r"var OWN_PID = (\d+);", html)
        self.assertIsNotNone(m_pid)
        self.assertEqual(int(m_pid.group(1)), pid_b)

        # 5/6) Le message de confirmation "Fin de la partie" doit
        # contenir le nom de B, pas celui de A ni un nom générique — et
        # ne dépend donc pas de qui détient le port 8765.
        m_confirm = re.search(r"var TOURNAMENT_NAME = (\"[^\"]*\");", html)
        self.assertIsNotNone(m_confirm)
        self.assertEqual(json.loads(m_confirm.group(1)), "_scratch_B")
        self.assertIn(
            "Confirmer la Fin de partie pour le ' + TOURNAMENT_NAME + ' ?",
            html,
        )

        # 4) "Actualiser" = exactement la même requête rejouée (avec le
        # même cookie, comme le ferait le téléphone) : doit redonner
        # EXACTEMENT le même nom, jamais revenir à celui de A.
        html2 = _get_text(f"http://127.0.0.1:{port_a}/", cookie=cookie)
        self.assertEqual(html, html2)

        # Sans sélection (mais toujours authentifié — seule la sélection
        # de tournoi est absente ici, pas l'authentification elle-même,
        # qui reste requise depuis le 2026-09-09) : repli sur le plus
        # récemment ouvert (voir resolve_current_pid, BUG 2) — C ici, ni
        # A (routeur) ni B (dernière sélection explicite, qui ne doit
        # pas "rester collée" sans cookie de sélection).
        html_no_cookie = _get_text(f"http://127.0.0.1:{port_a}/", cookie=auth_cookie)
        m3 = re.search(r'class="tournoi"[^>]*>([^<]+)<', html_no_cookie)
        self.assertEqual(m3.group(1), "_scratch_C")

    def test_bouton_retour_present_dans_lobbylist_et_va_vers_racine(self):
        pid_a, port_a = self._spawn_tournament("A")
        _pid_b, _port_b = self._spawn_tournament("B")  # 2e tournoi : bouton Lobby visible

        auth_cookie = _authenticate(port_a, self.addCleanup)
        html = _get_text(f"http://127.0.0.1:{port_a}/lobbylist", cookie=auth_cookie)
        self.assertIn('id="btn-back"', html)
        self.assertIn("<button id=\"btn-back\"", html)  # un vrai bouton, pas juste un <a>
        self.assertIn("window.location.href='/'", html)


if __name__ == "__main__":
    unittest.main()
