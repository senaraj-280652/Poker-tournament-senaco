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


def _select_tournament_cookie(any_entry_port, target_pid, timeout=5):
    """Voir tests/test_menu_principal_after_iphone_close.py — même
    principe, reproduit ici pour rester un fichier autonome."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(f"http://127.0.0.1:{any_entry_port}/select_tournament?pid={target_pid}")
    try:
        resp = opener.open(req, timeout=timeout)
        cookie = resp.headers.get("Set-Cookie")
        resp.close()
        return cookie
    except urllib.error.HTTPError as e:
        cookie = e.headers.get("Set-Cookie")
        e.close()
        return cookie


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

        # Sélectionne explicitement B depuis le téléphone (via A, peu
        # importe le port interrogé — voir _handle_select_tournament).
        cookie = _select_tournament_cookie(port_a, pid_b)
        self.assertIsNotNone(cookie)

        # 3) Le nom affiché sur l'écran principal (chargé via le port de
        # A, avec la sélection de B) doit être celui de B, pas de A.
        html = _get_text(f"http://127.0.0.1:{port_a}/", cookie=cookie)
        m = re.search(r'class="tournoi">([^<]+)<', html)
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

        # Sans sélection (pas de cookie, peu importe le port interrogé) :
        # repli sur le plus récemment ouvert (voir resolve_current_pid,
        # BUG 2) — C ici, ni A (routeur) ni B (dernière sélection
        # explicite, qui ne doit pas "rester collée" sans cookie).
        html_no_cookie = _get_text(f"http://127.0.0.1:{port_a}/")
        m3 = re.search(r'class="tournoi">([^<]+)<', html_no_cookie)
        self.assertEqual(m3.group(1), "_scratch_C")

    def test_bouton_retour_present_dans_lobbylist_et_va_vers_racine(self):
        pid_a, port_a = self._spawn_tournament("A")
        _pid_b, _port_b = self._spawn_tournament("B")  # 2e tournoi : bouton Lobby visible

        html = _get_text(f"http://127.0.0.1:{port_a}/lobbylist")
        self.assertIn('id="btn-back"', html)
        self.assertIn("<button id=\"btn-back\"", html)  # un vrai bouton, pas juste un <a>
        self.assertIn("window.location.href='/'", html)


if __name__ == "__main__":
    unittest.main()
