"""Tests ciblés du BUG 2 : le Lobby iPhone marquait "(celui-ci)" le
tournoi qui héberge physiquement le port 8765 (le "routeur" réseau, voir
remote_control.py) au lieu du tournoi réellement COURANT pour ce
téléphone (sa sélection explicite, ou à défaut le plus récemment
ouvert) — ces deux notions doivent être indépendantes (voir
resolve_current_pid).

Deux niveaux :
- ResolveCurrentPidTest : la fonction pure resolve_current_pid, tous
  les cas de figure demandés (cookie valide/mort/absent/invalide, own_pid
  comme routeur qui ne doit pas gagner par défaut).
- LobbyListMarksCorrectTournamentTest : un vrai RemoteControlServer réel
  (HTTP réel sur 127.0.0.1), open_windows.list_remote_tournaments mocké
  (jamais d'accès à ~/.poker_tournament/open_windows.json) — vérifie que
  /lobbylist et le routage de contenu (/) reflètent bien resolve_current_
  pid, y compris après un "rafraîchissement" (rejouer la même requête ne
  doit rien rétablir arbitrairement)."""
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get_text(url, jar=None, extra_cookie=None):
    """extra_cookie : voir LobbyListMarksCorrectTournamentTest — les
    tests de sélection ont besoin d'ajouter leur PROPRE cookie
    "selected_pid" en plus des cookies d'authentification déjà acquis
    (voir setUp: self.jar), un http.cookiejar seul ne suffit pas ici
    puisque ce cookie de test n'a jamais été POSÉ par une vraie réponse
    du serveur — combiné à la main dans l'en-tête Cookie."""
    req = urllib.request.Request(url)
    cookie_parts = []
    if jar is not None:
        cookie_parts.append("; ".join(f"{c.name}={c.value}" for c in jar))
    if extra_cookie:
        cookie_parts.append(extra_cookie)
    if cookie_parts:
        req.add_header("Cookie", "; ".join(cookie_parts))
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode("utf-8")


class ResolveCurrentPidTest(unittest.TestCase):
    @staticmethod
    def _live(*entries):
        return [
            {"pid": pid, "port": 10000 + pid, "name": f"t{pid}", "registered_at": ts}
            for pid, ts in entries
        ]

    def test_cookie_valide_et_vivant_est_priorise(self):
        live = self._live((20, 1), (19, 2), (18, 3))
        pid = remote_control.resolve_current_pid("selected_pid=19", own_pid=20, live_tournaments=live)
        self.assertEqual(pid, 19)

    def test_cookie_absent_retombe_sur_le_plus_recemment_ouvert(self):
        live = self._live((20, 1), (19, 2), (18, 3))
        pid = remote_control.resolve_current_pid("", own_pid=20, live_tournaments=live)
        self.assertEqual(pid, 18)

    def test_cookie_correspondant_a_un_tournoi_ferme_est_invalide(self):
        # pid 19 n'est plus dans live_tournaments : fermé entre-temps.
        live = self._live((20, 1), (18, 3))
        pid = remote_control.resolve_current_pid("selected_pid=19", own_pid=20, live_tournaments=live)
        self.assertEqual(pid, 18)

    def test_cookie_non_entier_est_invalide(self):
        live = self._live((20, 1), (18, 3))
        pid = remote_control.resolve_current_pid("selected_pid=abc", own_pid=20, live_tournaments=live)
        self.assertEqual(pid, 18)

    def test_cookie_correspondant_au_detenteur_du_port_8765_reste_valide(self):
        """Le détenteur du routeur PEUT être le tournoi courant — juste
        pas AUTOMATIQUEMENT (voir test suivant). Une sélection explicite
        et valide vers lui doit continuer de fonctionner normalement."""
        live = self._live((20, 1), (19, 2))
        pid = remote_control.resolve_current_pid("selected_pid=20", own_pid=20, live_tournaments=live)
        self.assertEqual(pid, 20)

    def test_aucun_tournoi_vivant_retombe_sur_own_pid(self):
        pid = remote_control.resolve_current_pid("selected_pid=999", own_pid=42, live_tournaments=[])
        self.assertEqual(pid, 42)

    def test_le_detenteur_du_port_8765_ne_gagne_pas_automatiquement(self):
        """Scénario exact rapporté : tournoi20 (own_pid, ouvert en
        premier, devenu routeur) ne doit PAS rester "courant" une fois
        tournoi19 puis tournoi18 ouverts plus tard, même sans sélection
        explicite depuis le téléphone."""
        live = self._live((20, 100), (19, 200), (18, 300))
        pid = remote_control.resolve_current_pid("", own_pid=20, live_tournaments=live)
        self.assertEqual(pid, 18)
        self.assertNotEqual(pid, 20)

    def test_reprise_du_port_8765_par_un_survivant_ne_le_rend_pas_plus_recent(self):
        """Un tournoi qui reprend le port 8765 après la fermeture du
        routeur (voir App._maybe_reclaim_default_remote_port) ne doit pas
        pour autant devenir "le plus récemment ouvert" : registered_at
        n'est jamais modifié par cette reprise (voir open_windows.
        update_remote_info, un simple merge)."""
        # tournoi19 a repris le port 8765 (own_pid=19 maintenant), mais son
        # registered_at (2) reste antérieur à celui de tournoi18 (3).
        live = self._live((19, 2), (18, 3))
        pid = remote_control.resolve_current_pid("", own_pid=19, live_tournaments=live)
        self.assertEqual(pid, 18)


class LobbyListMarksCorrectTournamentTest(unittest.TestCase):
    """Depuis le durcissement du 2026-09-09, /lobbylist exige un appareil
    authentifié ET approuvé — open_windows redirigé vers un dossier
    temporaire dédié (jamais ~/.poker_tournament), self.jar déjà
    authentifié/approuvé (voir _remote_control_auth_test_utils.
    authenticated_jar) pour chaque requête protégée."""

    def setUp(self):
        self.test_port = _free_port()
        patcher = patch.object(remote_control, "DEFAULT_PORT", self.test_port)
        self.addCleanup(patcher.stop)
        patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="lobby_current_tournament_test_")
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
        self._session_path = os.path.join(self._tmp.name, "session_marker.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None, get_tournament_name=lambda: "tournoi20", port=self.test_port,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.jar = authenticated_jar(f"http://127.0.0.1:{self.test_port}")
        self.own_pid = os.getpid()
        self.live = [
            {"pid": self.own_pid, "port": self.test_port, "name": "tournoi20", "registered_at": 1},
            {"pid": self.own_pid + 1000, "port": 40001, "name": "tournoi19", "registered_at": 2},
            {"pid": self.own_pid + 2000, "port": 40002, "name": "tournoi18", "registered_at": 3},
        ]

    def test_lobbylist_marque_le_plus_recent_pas_le_routeur(self):
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html = _get_text(f"http://127.0.0.1:{self.test_port}/lobbylist", jar=self.jar)
        self.assertIn("tournoi18 (celui-ci)", html)
        self.assertNotIn("tournoi20 (celui-ci)", html)
        self.assertNotIn("tournoi19 (celui-ci)", html)

    def test_actualiser_ne_retablit_pas_arbitrairement_le_routeur(self):
        """Le bouton 🔄 Actualiser rejoue exactement cette même requête :
        deux appels consécutifs, sans qu'aucun select_tournament n'ait eu
        lieu entre les deux, doivent donner EXACTEMENT le même résultat."""
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html1 = _get_text(f"http://127.0.0.1:{self.test_port}/lobbylist", jar=self.jar)
            html2 = _get_text(f"http://127.0.0.1:{self.test_port}/lobbylist", jar=self.jar)
        self.assertEqual(html1, html2)
        self.assertIn("tournoi18 (celui-ci)", html2)

    def test_selected_pid_mort_est_invalide_proprement(self):
        dead_pid = self.own_pid + 9999  # absent de self.live : "fermé"
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html = _get_text(
                f"http://127.0.0.1:{self.test_port}/lobbylist",
                jar=self.jar, extra_cookie=f"selected_pid={dead_pid}",
            )
        self.assertIn("tournoi18 (celui-ci)", html)

    def test_selection_explicite_valide_est_respectee(self):
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html = _get_text(
                f"http://127.0.0.1:{self.test_port}/lobbylist",
                jar=self.jar, extra_cookie=f"selected_pid={self.own_pid + 1000}",  # tournoi19
            )
        self.assertIn("tournoi19 (celui-ci)", html)
        self.assertNotIn("tournoi18 (celui-ci)", html)


if __name__ == "__main__":
    unittest.main()
