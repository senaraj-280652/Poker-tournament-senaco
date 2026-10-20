# -*- coding: utf-8 -*-
"""Affichage PERMANENT de l'identifiant du téléphone (correction du
2026-10-01 à la demande précédente "afficher l'identifiant du téléphone
sur l'iPhone") : l'identifiant ne doit plus apparaître UNIQUEMENT dans
le lobby (/lobbylist, voir tests/test_phone_identifier_lobby.py) mais à
côté du nom du tournoi sur TOUTE page qui l'affiche — un seul tournoi
ouvert ("/") comme plusieurs (chaque page + le lobby).

Réutilise open_windows.get_remote_device_short_id SANS dupliquer la
logique de résolution : remote_control.py factorise désormais ce calcul
dans deux méthodes partagées du Handler, _own_phone_short_id (résout le
browser_id du cookie de la requête) et _tournament_display_name (nom
échappé + suffixe " — IDENTIFIANT"), utilisées par CHAQUE route qui
affiche le nom du tournoi, /lobbylist y compris (voir son refactor).

Couvre :
1. Chaque page (/, /eliminate, /photos, /moves, /checkin, et la page
   "aucun accès" affichée à un rôle NONE) affiche l'identifiant à côté
   du nom du tournoi, qu'un seul tournoi soit ouvert ou plusieurs.
2. Le même téléphone obtient le MÊME identifiant sur toutes les pages ;
   deux téléphones distincts obtiennent chacun le leur, jamais celui de
   l'autre.
3. `tournament_name_json` (comparaison de noms réels en JS, voir
   confirmEndTournament) reste le nom PUR, sans identifiant — seul
   l'affichage HTML en est pourvu.
4. Jamais le code secret de connexion dans aucune de ces pages.
5. Non-duplication de la logique (vérification structurelle du
   source) : un seul appel à open_windows.get_remote_device_short_id
   dans tout remote_control.py, et chaque route qui affiche le nom du
   tournoi passe par _tournament_display_name, jamais une resolution
   séparée."""
import os
import re
import sys
import tempfile
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402
import roster  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402

_PAGES_WITH_TOURNAMENT_NAME = ("/", "/eliminate", "/photos", "/moves", "/checkin")



def _get_text(url, jar=None):
    req = urllib.request.Request(url)
    if jar is not None:
        cookie = "; ".join(f"{c.name}={c.value}" for c in jar)
        if cookie:
            req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=3) as r:
        return r.read().decode("utf-8")


class _PhoneIdentifierHttpTestCase(unittest.TestCase):
    """Même principe que _RemoteControlHttpTestCase (tests/test_remote_
    control_reliability.py) : rôle ADMIN inconditionnel (roster.py non
    isolé, inutile ici — seul l'affichage est testé, jamais les
    permissions elles-mêmes), mais avec get_accueil_management_enabled
    =True pour que /checkin reste atteignable (sinon refusée côté
    serveur, voir _send_accueil_disabled, hors sujet de ce fichier)."""

    TOURNAMENT_NAME = "Tournoi du vendredi"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="phone_identifier_display_test_")
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

        # port=0 (demande du 2026-10-20) : voir tests/test_remote_control_
        # reliability.py:_RemoteControlHttpTestCase.setUp pour le détail
        # du correctif (ConnectionRefusedError intermittent).
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_tournament_name=lambda: self.TOURNAMENT_NAME,
            get_accueil_management_enabled=lambda: True,
            port=0,
        )
        self.server.start()
        self.addCleanup(self.server.stop)
        self.port = self.server.port
        self.base = f"http://127.0.0.1:{self.port}"

        self.log_path = os.path.join(self._tmp.name, "remote_control.log")
        log_patcher = patch.object(remote_control, "_LOG_PATH", self.log_path)
        logger_patcher = patch.object(remote_control, "_logger", None)
        self.addCleanup(log_patcher.stop)
        self.addCleanup(logger_patcher.stop)
        log_patcher.start()
        logger_patcher.start()

    def _short_id_for(self, browser_id):
        devices = open_windows.list_approved_remote_devices()
        return next(d["short_id"] for d in devices if d["browser_id"] == browser_id)

    def _only_short_id(self):
        devices = open_windows.list_approved_remote_devices()
        self.assertEqual(len(devices), 1)
        return devices[0]["short_id"]


# =====================================================================
# 1-2. Chaque page affiche l'identifiant — un seul tournoi ouvert.
# =====================================================================
class PhoneIdentifierOnEveryPageTest(_PhoneIdentifierHttpTestCase):
    def test_identifiant_visible_sur_chaque_page_un_seul_tournoi_ouvert(self):
        jar = authenticated_jar(self.base, owner_name="Admin Test")
        short_id = self._only_short_id()
        expected = f"{self.TOURNAMENT_NAME} — {short_id}"

        for path in _PAGES_WITH_TOURNAMENT_NAME:
            with self.subTest(path=path):
                html = _get_text(f"{self.base}{path}", jar=jar)
                self.assertIn(expected, html, f"identifiant absent de {path}")

    def test_meme_identifiant_sur_toutes_les_pages_pour_ce_telephone(self):
        """Non seulement présent partout, mais TOUJOURS LE MÊME pour un
        même téléphone naviguant d'une page à l'autre."""
        jar = authenticated_jar(self.base, owner_name="Admin Test")
        short_id = self._only_short_id()

        found = set()
        for path in _PAGES_WITH_TOURNAMENT_NAME:
            html = _get_text(f"{self.base}{path}", jar=jar)
            self.assertIn(f"— {short_id}", html)
            found.add(short_id)
        self.assertEqual(len(found), 1)

    def test_page_sans_acces_role_none_affiche_aussi_lidentifiant(self):
        """Un appareil approuvé mais SANS propriétaire (rôle "NONE") reçoit
        _NO_ACCESS_PAGE à la place de la page normale — même cette page
        d'erreur affiche le nom du tournoi, donc l'identifiant doit lui
        aussi y figurer (cohérence totale, demande explicite "en
        permanence")."""
        jar = authenticated_jar(self.base, owner_name=None)  # pas de propriétaire -> rôle NONE
        short_id = self._only_short_id()

        html = _get_text(f"{self.base}/", jar=jar)

        self.assertIn(f"{self.TOURNAMENT_NAME} — {short_id}", html)

    def test_jamais_le_code_secret_sur_ces_pages(self):
        jar = authenticated_jar(self.base, owner_name="Admin Test")
        code = open_windows.remote_session_code()

        for path in _PAGES_WITH_TOURNAMENT_NAME:
            html = _get_text(f"{self.base}{path}", jar=jar)
            self.assertNotIn(code, html)
            self.assertNotIn("131261", html)


# =====================================================================
# 2 (suite). Deux téléphones distincts, chacun son identifiant.
# =====================================================================
class TwoDistinctPhonesTest(_PhoneIdentifierHttpTestCase):
    def test_chaque_telephone_voit_son_propre_identifiant_jamais_lautre(self):
        jar1 = authenticated_jar(self.base, label="Téléphone 1", owner_name="Admin Test")
        before = {d["browser_id"] for d in open_windows.list_approved_remote_devices()}
        jar2 = authenticated_jar(self.base, label="Téléphone 2", owner_name="Admin Test")
        devices = open_windows.list_approved_remote_devices()
        self.assertEqual(len(devices), 2)
        id1 = next(d["short_id"] for d in devices if d["browser_id"] in before)
        id2 = next(d["short_id"] for d in devices if d["browser_id"] not in before)
        self.assertNotEqual(id1, id2)

        for path in _PAGES_WITH_TOURNAMENT_NAME:
            html1 = _get_text(f"{self.base}{path}", jar=jar1)
            html2 = _get_text(f"{self.base}{path}", jar=jar2)
            self.assertIn(f"— {id1}", html1)
            self.assertNotIn(id2, html1)
            self.assertIn(f"— {id2}", html2)
            self.assertNotIn(id1, html2)


# =====================================================================
# 3. tournament_name_json reste pur (JS, confirmEndTournament).
# =====================================================================
class TournamentNameJsonStaysPureTest(_PhoneIdentifierHttpTestCase):
    def test_tournament_name_json_ne_contient_pas_lidentifiant(self):
        jar = authenticated_jar(self.base, owner_name="Admin Test")
        short_id = self._only_short_id()

        html = _get_text(f"{self.base}/", jar=jar)

        # L'affichage HTML (topbar) PORTE l'identifiant...
        self.assertIn(f"{self.TOURNAMENT_NAME} — {short_id}", html)
        # ...mais la variable JS utilisée par confirmEndTournament (nom
        # RÉEL comparé/affiché dans un window.confirm) reste PURE.
        m = re.search(r"var TOURNAMENT_NAME = (\".*?\");", html)
        self.assertIsNotNone(m, "var TOURNAMENT_NAME introuvable dans la page")
        self.assertEqual(m.group(1), f'"{self.TOURNAMENT_NAME}"')
        self.assertNotIn(short_id, m.group(1))


# =====================================================================
# 4. Non-régression explicite : /lobbylist (plusieurs tournois).
# =====================================================================
class LobbyStillShowsIdentifierTest(_PhoneIdentifierHttpTestCase):
    def test_lobbylist_affiche_toujours_lidentifiant_plusieurs_tournois(self):
        """Non-régression du refactor de _handle_lobbylist (réutilise
        désormais _tournament_display_name au lieu de sa propre copie du
        calcul) : le comportement déjà couvert par tests/test_phone_
        identifier_lobby.py doit rester intact."""
        jar = authenticated_jar(self.base, owner_name="Admin Test")
        short_id = self._only_short_id()
        own_pid = os.getpid()
        live = [
            {"pid": own_pid, "port": self.port, "name": self.TOURNAMENT_NAME, "registered_at": 1},
            {"pid": own_pid + 1, "port": self.port + 1, "name": "Autre tournoi", "registered_at": 2},
        ]
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=live):
            html = _get_text(f"{self.base}/lobbylist", jar=jar)
        self.assertIn(f"{self.TOURNAMENT_NAME} — {short_id}", html)
        self.assertIn(f"Autre tournoi — {short_id}", html)
        self.assertIn("(celui-ci)", html)


# =====================================================================
# 5. Non-duplication de la logique (vérification structurelle).
# =====================================================================
class NoDuplicatedLogicTest(unittest.TestCase):
    def setUp(self):
        with open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "remote_control.py"),
            encoding="utf-8",
        ) as f:
            self.source = f.read()

    def test_get_remote_device_short_id_appele_une_seule_fois(self):
        """Un seul point d'appel à open_windows.get_remote_device_
        short_id dans tout le fichier — à l'intérieur de _own_phone_
        short_id, jamais recalculé ailleurs."""
        occurrences = self.source.count("open_windows.get_remote_device_short_id(")
        self.assertEqual(occurrences, 1, "get_remote_device_short_id appelé plus d'une fois")

    def test_toutes_les_pages_utilisent_tournament_display_name(self):
        """Les 6 routes qui affichent le nom du tournoi (page "aucun
        accès", page principale, Éliminations, Photos, Mouvements,
        Accueil) doivent toutes passer par _tournament_display_name —
        jamais une résolution séparée (_escape_html(get_name()) seul,
        sans l'identifiant, ne doit plus exister nulle part)."""
        self.assertEqual(
            self.source.count("tournament_name=self._tournament_display_name(get_name())"), 6,
        )
        self.assertNotIn("tournament_name=_escape_html(get_name())", self.source)

    def test_lobbylist_reutilise_aussi_tournament_display_name(self):
        self.assertIn("self._tournament_display_name(t[\"name\"])", self.source)


if __name__ == "__main__":
    unittest.main()
