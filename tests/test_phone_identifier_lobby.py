# -*- coding: utf-8 -*-
"""Afficher l'identifiant du téléphone sur l'iPhone (demande du
2026-10-01) — à côté de chaque nom de tournoi dans le lobby
(/lobbylist), l'identifiant permanent (`short_id`, 6 caractères hex
majuscules) de l'appareil CONNECTÉ, le même que celui désormais affiché
en permanence dans l'onglet CA du Mac (voir App._refresh_remote_devices_
panel, tests/test_remote_device_owner_widget.py pour sa couverture
dédiée) — jamais le code secret de connexion, jamais calculé à partir
du propriétaire ni d'un autre appareil.

Couvre :
1. open_windows.get_remote_device_short_id — lecture seule, None pour
   un appareil non approuvé/inconnu/absent, jamais d'exception.
2. /lobbylist (bout en bout HTTP réel) : l'identifiant affiché
   correspond EXACTEMENT à celui du browser_id de LA requête (jamais un
   autre appareil ni le propriétaire), identique pour chaque tournoi de
   la liste, absent du code secret/du HTML si l'appareil n'a pas de
   short_id résolvable, non-régression de "(celui-ci)"/tri/navigation.
3. Deux téléphones distincts obtiennent chacun leur PROPRE identifiant,
   jamais celui de l'autre."""
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _get_text(url, jar=None):
    req = urllib.request.Request(url)
    if jar is not None:
        cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
        if cookie:
            req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode("utf-8")


class _TmpRemoteControlTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phone_identifier_lobby_test_")
        self.addCleanup(self._tmp.cleanup)
        self._registry_path = os.path.join(self._tmp.name, "open_windows.json")
        self._remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(self._remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=self._registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=self._remote_dir),
        ):
            self.addCleanup(target.stop)
            target.start()

    def _open_session(self, name="session.tournoi"):
        path = os.path.join(self._tmp.name, name)
        open_windows.register(path)
        self.addCleanup(lambda: open_windows.unregister(path) if path in open_windows.list_open_paths() else None)
        return path


# =====================================================================
# 1. open_windows.get_remote_device_short_id
# =====================================================================
class GetRemoteDeviceShortIdTest(_TmpRemoteControlTest):
    def test_browser_id_vide_renvoie_none(self):
        self.assertIsNone(open_windows.get_remote_device_short_id(""))
        self.assertIsNone(open_windows.get_remote_device_short_id(None))

    def test_browser_id_inconnu_renvoie_none(self):
        self.assertIsNone(open_windows.get_remote_device_short_id("inconnu"))

    def test_appareil_pending_renvoie_none(self):
        self._open_session()
        status = open_windows.register_device_attempt("browser-1", "127.0.0.1")
        self.assertEqual(status, "pending")
        self.assertIsNone(open_windows.get_remote_device_short_id("browser-1"))

    def test_appareil_revoque_renvoie_none(self):
        self._open_session()
        open_windows.register_device_attempt("browser-1", "127.0.0.1")
        open_windows.approve_remote_device("browser-1")
        open_windows.revoke_remote_device("browser-1")
        self.assertIsNone(open_windows.get_remote_device_short_id("browser-1"))

    def test_appareil_approuve_renvoie_son_short_id(self):
        self._open_session()
        open_windows.register_device_attempt("browser-1", "127.0.0.1")
        open_windows.approve_remote_device("browser-1")
        devices = open_windows.list_approved_remote_devices()
        expected = next(d["short_id"] for d in devices if d["browser_id"] == "browser-1")

        short_id = open_windows.get_remote_device_short_id("browser-1")

        self.assertEqual(short_id, expected)
        self.assertRegex(short_id, r"^[0-9A-F]{6}$")


# =====================================================================
# 2-3. /lobbylist bout en bout HTTP réel
# =====================================================================
class LobbylistShowsPhoneIdentifierTest(unittest.TestCase):
    def setUp(self):
        self.test_port = _free_port()
        patcher = patch.object(remote_control, "DEFAULT_PORT", self.test_port)
        self.addCleanup(patcher.stop)
        patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="phone_identifier_lobby_http_test_")
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

        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        log_patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        logger_patcher = patch.object(remote_control, "_logger", None)
        self.addCleanup(log_patcher.stop)
        self.addCleanup(logger_patcher.stop)
        log_patcher.start()
        logger_patcher.start()

        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None, get_tournament_name=lambda: "Tournoi du vendredi", port=self.test_port,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.base = f"http://127.0.0.1:{self.test_port}"
        self.own_pid = os.getpid()
        self.live = [
            {"pid": self.own_pid, "port": self.test_port, "name": "Tournoi du vendredi", "registered_at": 1},
        ]

    def _own_short_id(self):
        devices = open_windows.list_approved_remote_devices()
        self.assertEqual(len(devices), 1)
        return devices[0]["short_id"]

    def test_identifiant_affiche_correspond_a_lappareil_connecte(self):
        """Cœur de la demande : l'identifiant affiché doit être celui
        de l'onglet CA pour CE téléphone précis — jamais une valeur
        inventée côté page."""
        jar = authenticated_jar(self.base, owner_name=None)
        short_id = self._own_short_id()

        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html = _get_text(f"{self.base}/lobbylist", jar=jar)

        self.assertIn(f"Tournoi du vendredi — {short_id} (celui-ci)", html)

    def test_meme_identifiant_repete_pour_chaque_tournoi_de_la_liste(self):
        jar = authenticated_jar(self.base, owner_name=None)
        short_id = self._own_short_id()
        live = [
            {"pid": self.own_pid, "port": self.test_port, "name": "Tournoi A", "registered_at": 1},
            {"pid": self.own_pid + 1, "port": self.test_port + 1, "name": "Tournoi B", "registered_at": 2},
        ]

        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=live):
            html = _get_text(f"{self.base}/lobbylist", jar=jar)

        self.assertIn(f"Tournoi A — {short_id}", html)
        self.assertIn(f"Tournoi B — {short_id}", html)

    def test_deux_telephones_distincts_obtiennent_chacun_leur_propre_identifiant(self):
        """Non-régression de sécurité explicitement demandée : vérifier
        que l'identifiant affiché correspond bien au téléphone connecté,
        jamais à un autre appareil — ici, deux appareils réellement
        différents (deux jars/deux browser_id distincts)."""
        jar1 = authenticated_jar(self.base, label="Téléphone 1", owner_name=None)
        devices_after_1 = {d["browser_id"] for d in open_windows.list_approved_remote_devices()}
        jar2 = authenticated_jar(self.base, label="Téléphone 2", owner_name=None)
        devices_after_2 = open_windows.list_approved_remote_devices()
        self.assertEqual(len(devices_after_2), 2)
        id1 = next(d["short_id"] for d in devices_after_2 if d["browser_id"] in devices_after_1)
        id2 = next(d["short_id"] for d in devices_after_2 if d["browser_id"] not in devices_after_1)
        self.assertNotEqual(id1, id2)

        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html1 = _get_text(f"{self.base}/lobbylist", jar=jar1)
            html2 = _get_text(f"{self.base}/lobbylist", jar=jar2)

        self.assertIn(f"Tournoi du vendredi — {id1} (celui-ci)", html1)
        self.assertNotIn(id2, html1)
        self.assertIn(f"Tournoi du vendredi — {id2} (celui-ci)", html2)
        self.assertNotIn(id1, html2)

    def test_identifiant_different_apres_renommage_sur_le_mac(self):
        """Non-régression du point soulevé au diagnostic : même après
        qu'un ADMIN ait renommé l'appareil sur le Mac (label personnalisé
        remplaçant le short_id dans le champ de renommage), l'identifiant
        affiché sur l'iPhone reste le short_id d'origine, INCHANGÉ — un
        renommage ne doit jamais faire perdre la correspondance."""
        jar = authenticated_jar(self.base, owner_name=None)
        short_id = self._own_short_id()
        browser_id = open_windows.list_approved_remote_devices()[0]["browser_id"]
        devices = open_windows._read_json_or_empty(open_windows._remote_devices_path())
        devices[browser_id]["label"] = "iPhone de Marc"
        open_windows._atomic_write_json(open_windows._remote_devices_path(), devices)

        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html = _get_text(f"{self.base}/lobbylist", jar=jar)

        self.assertIn(f"Tournoi du vendredi — {short_id} (celui-ci)", html)

    def test_jamais_le_code_secret_dans_la_page(self):
        jar = authenticated_jar(self.base, owner_name=None)
        code = open_windows.remote_session_code()

        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=self.live):
            html = _get_text(f"{self.base}/lobbylist", jar=jar)

        self.assertNotIn(code, html)
        self.assertNotIn("131261", html)

    def test_sans_authentification_la_page_nest_pas_servie(self):
        """Non-régression : /lobbylist reste protégée par authentification
        comme avant ce chantier (redirection vers /login pour une page de
        navigation plein écran, voir _AUTH_PAGE_PATHS — jamais un 401 JSON
        ici) — ce correctif n'a rien changé à ça."""
        status, _, headers = http_request(self.base, "GET", "/lobbylist")
        self.assertEqual(status, 302)
        self.assertEqual(headers.get("Location"), "/login")

    def test_non_regression_navigation_et_tri(self):
        """L'ajout de l'identifiant ne doit rien changer au reste du
        comportement déjà couvert ailleurs (tri alphabétique, bouton
        Retour, select_tournament) — simple sondage de surface ici,
        la couverture détaillée reste dans test_lobby_current_tournament.py
        et test_iphone_main_screen_reflects_selection.py."""
        jar = authenticated_jar(self.base, owner_name=None)
        live = [
            {"pid": self.own_pid, "port": self.test_port, "name": "Zoulou", "registered_at": 1},
            {"pid": self.own_pid + 1, "port": self.test_port + 1, "name": "Alpha", "registered_at": 2},
        ]
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=live):
            html = _get_text(f"{self.base}/lobbylist", jar=jar)
        self.assertLess(html.index("Alpha"), html.index("Zoulou"))
        self.assertIn('id="btn-back"', html)


if __name__ == "__main__":
    unittest.main()
