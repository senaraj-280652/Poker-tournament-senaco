# -*- coding: utf-8 -*-
"""Application RÉELLE des permissions DIRTO côté serveur (Phase 4,
"Sécurisation du Contrôle à distance", 2026-09-20) : ADMIN a accès total,
un DIRTO n'a accès qu'aux fonctions explicitement accordées POUR CE
TOURNOI (voir database.py: REMOTE_PERMISSION_LABELS/remote_authorizations,
Phase 3), un appareil sans propriétaire (ou un DIRTO reclassé/retiré du
Répertoire) n'a accès à rien — et ce contrôle est refait CÔTÉ SERVEUR pour
chaque route, jamais seulement par le masquage HTML de la page d'accueil
(voir remote_control.py: resolve_role/_send_permission_denied).

VRAI RemoteControlServer (ThreadingHTTPServer réel sur 127.0.0.1, comme
tests/test_remote_control_device_approval_http.py) + VRAIE Database (un
fichier .tournoi temporaire par test) + VRAI roster.py (redirigé vers un
fichier temporaire, jamais ~/.poker_tournament) : ce fichier teste la
résolution du RÔLE elle-même (roster.get_group), contrairement aux tests
plus anciens (test_remote_control_reliability.py et consorts) qui
patchent roster.get_group en ADMIN pour rester concentrés sur ce qu'ils
testaient avant l'existence de ce chantier.

get_dirto_permissions est alimenté par un cache local rafraîchi
manuellement après chaque set_dirto_authorization/clear_dirto_authorization
— jamais une lecture directe de self.db depuis le thread HTTP (même
principe que App._refresh_remote_dirto_permissions_cache dans main.py,
obligatoire : les connexions sqlite3 ne sont pas partageables entre
threads)."""
import json
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


class _DirtoEnforcementTestCase(unittest.TestCase):
    """Un seul "tournoi" (une Database, un RemoteControlServer). Voir
    EtancheiteMultiTournoisEnforcementTest plus bas pour le cas à deux
    tournois."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dirto_enforcement_test_")
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
        self.on_end_tournament_calls = []

        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.server = self._make_server(self.db, self.port)
        self.server.start()
        self.addCleanup(self.server.stop)
        self.own_pid = os.getpid()

    def _make_server(self, db, port):
        """Un serveur lié à `db`, avec un cache de permissions DIRTO
        propre à CETTE base — jamais une lecture directe de self.db
        depuis le thread HTTP (voir la docstring du module)."""
        cache = {}

        def refresh():
            cache.clear()
            cache.update({
                auth["dirto_name"]: frozenset(auth["permissions"])
                for auth in db.list_dirto_authorizations()
            })

        server = remote_control.RemoteControlServer(
            on_word=lambda word: self.on_word_calls.append(word),
            on_eliminate=self._on_eliminate,
            on_end_tournament=lambda: self.on_end_tournament_calls.append(True),
            get_dirto_permissions=lambda name: cache.get((name or "").strip(), frozenset()),
            port=port,
        )
        server._test_refresh_dirto_cache = refresh  # exposé pour _grant/_revoke ci-dessous
        return server

    def _on_eliminate(self, eliminated_id, eliminator_id, client_request_id=None):
        self.on_eliminate_calls.append((eliminated_id, eliminator_id, client_request_id))
        return {"ok": True, "message": ""}

    def _grant(self, db, server, dirto_name, admin_name, permissions):
        db.set_dirto_authorization(dirto_name, admin_name, permissions)
        server._test_refresh_dirto_cache()

    def _revoke(self, db, server, dirto_name):
        db.clear_dirto_authorization(dirto_name)
        server._test_refresh_dirto_cache()


class AdminFullAccessTest(_DirtoEnforcementTestCase):
    """ADMIN : accès complet."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.jar = authenticated_jar(self.base, owner_name="Raj")

    def test_page_accueil_complete_avec_fin_de_partie(self):
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        text = body.decode("utf-8")
        self.assertEqual(status, 200)
        for btn_id in ("btn-eliminations", "btn-tables", "btn-mouvements",
                       "btn-chronometre", "btn-niveau-precedent", "btn-photos",
                       "btn-end-tournament", "btn-elimination"):
            self.assertIn(f'id="{btn_id}"', text, btn_id)

    def test_toutes_les_actions_fonctionnent(self):
        for action in sorted(remote_control._VALID_ACTIONS):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar)
            self.assertEqual(status, 200, f"{action} : {body}")
        self.assertEqual(sorted(self.on_word_calls), sorted(remote_control._VALID_ACTIONS))

    def test_eliminate_fonctionne(self):
        status, body, _ = http_request(
            self.base, "POST", "/eliminate", self.jar,
            body={"eliminated_id": 1, "eliminator_id": None},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.on_eliminate_calls), 1)

    def test_terminer_le_tournoi_fonctionne(self):
        with patch.object(open_windows, "list_remote_tournaments", return_value=[]):
            status, body, _ = http_request(
                self.base, "POST", "/end_tournament", self.jar, body={"pid": self.own_pid},
            )
        self.assertEqual(status, 200, body)
        self.assertEqual(self.on_end_tournament_calls, [True])


class DirtoOnePermissionTest(_DirtoEnforcementTestCase):
    """DIRTO avec 1 permission."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", ["tables"])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_action_autorisee_fonctionne(self):
        for action in ("tables", "tables_zoom_moins", "tables_zoom_plus"):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar)
            self.assertEqual(status, 200, f"{action} : {body}")

    def test_toute_autre_action_refusee(self):
        for action in sorted(remote_control._VALID_ACTIONS - {"tables", "tables_zoom_moins", "tables_zoom_plus"}):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar)
            self.assertEqual(status, 403, f"{action} aurait dû être refusée : {body}")
        # Aucune de ces actions refusées n'a atteint on_word (setUp propre
        # à CE test, jamais partagé avec test_action_autorisee_fonctionne).
        self.assertEqual(self.on_word_calls, [])

    def test_page_daccueil_ne_montre_que_tables(self):
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        text = body.decode("utf-8")
        self.assertIn('id="btn-tables"', text)
        for absent_id in ("btn-eliminations", "btn-mouvements", "btn-chronometre",
                          "btn-niveau-precedent", "btn-photos", "btn-end-tournament"):
            self.assertNotIn(f'id="{absent_id}"', text, absent_id)


class DirtoMultiplePermissionsTest(_DirtoEnforcementTestCase):
    """DIRTO avec plusieurs permissions."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", ["tables", "moves", "clock"])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_les_trois_permissions_accordees_fonctionnent(self):
        for action in ("tables", "mouvements", "chronometre", "toggle_pause"):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar)
            self.assertEqual(status, 200, f"{action} : {body}")
        status, body, _ = http_request(self.base, "GET", "/moves_pending", self.jar)
        self.assertEqual(status, 200, body)

    def test_les_permissions_non_accordees_restent_refusees(self):
        for action in ("elimination", "niveau_precedent", "niveau_suivant"):
            status, body, _ = http_request(self.base, "POST", f"/action/{action}", self.jar)
            self.assertEqual(status, 403, f"{action} : {body}")
        status, body, _ = http_request(self.base, "GET", "/players", self.jar)
        self.assertEqual(status, 403, body)
        status, body, _ = http_request(self.base, "GET", "/rebalance_pending", self.jar)
        self.assertEqual(status, 403, body)


class DirtoNoAuthorizationForThisTournamentTest(_DirtoEnforcementTestCase):
    """DIRTO classé au Répertoire mais JAMAIS autorisé sur ce tournoi
    précis — "aucune autorisation existante = aucune permission" (règle
    explicite de Phase 3)."""

    def setUp(self):
        super().setUp()
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_page_accueil_affiche_le_message_aucune_fonction(self):
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        self.assertEqual(status, 200)
        self.assertIn(remote_control._NO_PERMISSION_MESSAGE, body.decode("utf-8"))

    def test_toute_action_refusee(self):
        status, body, _ = http_request(self.base, "POST", "/action/tables", self.jar)
        self.assertEqual(status, 403, body)
        self.assertEqual(self.on_word_calls, [])


class DeviceApprovedWithoutOwnerTest(_DirtoEnforcementTestCase):
    """Appareil approuvé (niveau 1 acquis) mais SANS propriétaire du
    tout — jamais lié via l'onglet Paramètres."""

    def setUp(self):
        super().setUp()
        self.jar = authenticated_jar(self.base)  # pas de owner_name

    def test_page_accueil_affiche_le_message_aucune_fonction(self):
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        self.assertEqual(status, 200)
        self.assertIn(remote_control._NO_PERMISSION_MESSAGE, body.decode("utf-8"))

    def test_toute_route_fonctionnelle_refusee(self):
        cases = [
            ("GET", "/players", None),
            ("GET", "/roster_players", None),
            ("GET", "/eliminate", None),
            ("GET", "/photos", None),
            ("GET", "/moves", None),
            ("POST", "/action/tables", None),
            ("POST", "/eliminate", {"eliminated_id": 1, "eliminator_id": None}),
            ("POST", "/end_tournament", {"pid": self.own_pid}),
        ]
        for method, path, body in cases:
            status, resp_body, _ = http_request(self.base, method, path, self.jar, body=body)
            self.assertEqual(status, 403, f"{method} {path} : {resp_body}")
        self.assertEqual(self.on_word_calls, [])
        self.assertEqual(self.on_eliminate_calls, [])
        self.assertEqual(self.on_end_tournament_calls, [])

    def test_proprietaire_reclasse_hors_admin_dirto_traite_comme_non_lie(self):
        """Filet de sécurité supplémentaire (non explicitement demandé,
        mais direct conséquence de "roster.get_group résolu EN DIRECT" —
        voir resolve_role) : un propriétaire retiré du Répertoire (ou
        dont le groupe a été effacé) APRÈS avoir été lié à l'appareil ne
        doit jamais garder l'accès qu'il avait avant, ni hériter d'un
        rôle par défaut dangereux."""
        open_windows.set_remote_device_owner(
            self._browser_id_of(self.jar), "Fantome",
        )  # jamais classé ADMIN/DIRTO au Répertoire
        status, body, _ = http_request(self.base, "POST", "/action/tables", self.jar)
        self.assertEqual(status, 403, body)

    def _browser_id_of(self, jar):
        return {c.name: c.value for c in jar}["rc_bid"]


class DirectHttpCallForbiddenActionTest(_DirtoEnforcementTestCase):
    """Appel HTTP DIRECT d'une action interdite (jamais depuis un bouton
    affiché) → refus — preuve que le masquage HTML n'est pas la sécurité
    réelle (requirement 6)."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        # "eliminations" volontairement ABSENTE : Marie ne devrait jamais
        # pouvoir éliminer qui que ce soit sur ce tournoi.
        self._grant(self.db, self.server, "Marie", "Raj", ["tables", "photos"])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_appel_direct_a_eliminate_refuse_et_jamais_execute(self):
        status, body, _ = http_request(
            self.base, "POST", "/eliminate", self.jar,
            body={"eliminated_id": 1, "eliminator_id": None},
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(self.on_eliminate_calls, [])

    def test_appel_direct_a_laction_elimination_refuse(self):
        status, body, _ = http_request(self.base, "POST", "/action/elimination", self.jar)
        self.assertEqual(status, 403, body)
        self.assertEqual(self.on_word_calls, [])

    def test_lecture_de_players_refusee(self):
        status, body, _ = http_request(self.base, "GET", "/players", self.jar)
        self.assertEqual(status, 403, body)


class EndTournamentByDirtoRefusedTest(_DirtoEnforcementTestCase):
    """"Terminer le tournoi" par un DIRTO → refus, MÊME avec les 7
    permissions accordées (elle n'existe structurellement pas parmi
    elles) — et MÊME par appel HTTP direct."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", [
            "eliminations", "tables", "moves", "clock", "levels", "photos", "rebalance",
        ])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_end_tournament_refuse_malgre_toutes_les_permissions(self):
        status, body, _ = http_request(
            self.base, "POST", "/end_tournament", self.jar, body={"pid": self.own_pid},
        )
        self.assertEqual(status, 403, body)
        self.assertEqual(self.on_end_tournament_calls, [])

    def test_bouton_absent_de_la_page_meme_avec_toutes_les_permissions(self):
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        self.assertNotIn('id="btn-end-tournament"', body.decode("utf-8"))


class JoueursCommandNeverGrantedToDirtoTest(_DirtoEnforcementTestCase):
    """Anomalie constatée en test réel iPhone le 2026-09-22 : le bouton
    "⏸ Joueurs" (POST /action/elimination — bascule l'onglet Joueurs du
    Mac, SANS rapport avec la page "Gérer les éliminations" du
    téléphone) apparaissait à tort dès que la permission "eliminations"
    était accordée, alors qu'aucune des 7 permissions ne couvre cette
    commande (voir _ADMIN_ONLY_ACTIONS dans remote_control.py — même
    traitement que "Terminer le tournoi"). Non-régression : réservée à
    l'ADMIN, MÊME avec les 7 permissions accordées, et SANS jamais
    affecter GET /players ni POST /eliminate (nécessaires à la page
    "Gérer les éliminations", qui ne déclenche jamais cette action)."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", [
            "eliminations", "tables", "moves", "clock", "levels", "photos", "rebalance",
        ])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_action_elimination_refusee_malgre_la_permission_eliminations(self):
        status, body, _ = http_request(self.base, "POST", "/action/elimination", self.jar)
        self.assertEqual(status, 403, body)
        self.assertEqual(self.on_word_calls, [])

    def test_bouton_joueurs_absent_de_la_page_meme_avec_eliminations_accordee(self):
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        self.assertNotIn('id="btn-elimination"', body.decode("utf-8"))
        # "Gérer les éliminations" (page distincte, /eliminate) doit
        # rester visible : seule la commande "Joueurs" est concernée.
        self.assertIn('id="btn-eliminations"', body.decode("utf-8"))

    def test_get_players_et_post_eliminate_restent_fonctionnels(self):
        """Coeur de la demande : la permission "eliminations" doit
        continuer à couvrir GET /players et POST /eliminate (nécessaires
        à la page "Gérer les éliminations"), même si la commande
        "Joueurs" leur est désormais totalement indépendante."""
        status, body, _ = http_request(self.base, "GET", "/players", self.jar)
        self.assertEqual(status, 200, body)
        status, body, _ = http_request(self.base, "GET", "/eliminate", self.jar)
        self.assertEqual(status, 200, body)
        status, body, _ = http_request(
            self.base, "POST", "/eliminate", self.jar,
            body={"eliminated_id": 1, "eliminator_id": None},
        )
        self.assertEqual(status, 200, body)
        self.assertEqual(len(self.on_eliminate_calls), 1)


class PermissionRevokedWhileConnectedTest(_DirtoEnforcementTestCase):
    """Retrait d'une permission (ou de l'autorisation entière) PENDANT
    que le téléphone reste connecté — même jar du début à la fin, jamais
    de ré-authentification."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", ["tables", "moves"])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_retrait_complet_coupe_immediatement_sans_nouvelle_auth(self):
        status, _, _ = http_request(self.base, "POST", "/action/tables", self.jar)
        self.assertEqual(status, 200)

        self._revoke(self.db, self.server, "Marie")  # ADMIN retire tout, côté Mac

        status, body, _ = http_request(self.base, "POST", "/action/tables", self.jar)
        self.assertEqual(status, 403, body)
        status, body, _ = http_request(self.base, "GET", "/", self.jar)
        self.assertIn(remote_control._NO_PERMISSION_MESSAGE, body.decode("utf-8"))

    def test_changement_de_permissions_sans_nouvelle_authentification(self):
        """Remplace ["tables","moves"] par ["clock"] uniquement — bascule
        complète, même jar, aucun nouvel /authenticate."""
        status, _, _ = http_request(self.base, "POST", "/action/tables", self.jar)
        self.assertEqual(status, 200)
        status, _, _ = http_request(self.base, "POST", "/action/chronometre", self.jar)
        self.assertEqual(status, 403)

        self._grant(self.db, self.server, "Marie", "Raj", ["clock"])

        status, body, _ = http_request(self.base, "POST", "/action/tables", self.jar)
        self.assertEqual(status, 403, body)
        status, body, _ = http_request(self.base, "POST", "/action/chronometre", self.jar)
        self.assertEqual(status, 200, body)


class ReconnectionWifiTest(_DirtoEnforcementTestCase):
    """"Reconnexion Wi-Fi" : chaque requête HTTP de ce serveur est déjà
    une connexion TCP indépendante (urllib, comme un vrai téléphone qui
    reperd puis retrouve son réseau) — la résolution du rôle/permissions
    ne doit dépendre d'aucun état tenu EN MÉMOIRE d'une requête à
    l'autre (uniquement des cookies + du registre partagé + du cache de
    permissions), donc rester identique après une coupure simulée."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", ["moves"])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_role_et_permissions_identiques_apres_plusieurs_connexions_separees(self):
        for _ in range(3):  # simule plusieurs cycles connexion/déconnexion
            status, body, _ = http_request(self.base, "POST", "/action/mouvements", self.jar)
            self.assertEqual(status, 200, body)
            status, body, _ = http_request(self.base, "POST", "/action/tables", self.jar)
            self.assertEqual(status, 403, body)


class SelectionVanishedStillTakesPriorityTest(_DirtoEnforcementTestCase):
    """SELECTION_VANISHED (chantier antérieur, protégé) : un tournoi B
    explicitement sélectionné puis disparu du registre doit renvoyer 409
    AVANT toute résolution de rôle/permission — jamais un repli
    silencieux vers le tournoi A qui traite la requête, même si le DIRTO
    n'a AUCUNE permission sur A (sinon on ne saurait jamais si le 403
    obtenu vient du bon contrôle)."""

    def setUp(self):
        super().setUp()
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        # Aucune autorisation pour Marie sur A : si SELECTION_VANISHED
        # n'était pas vérifié EN PREMIER, la requête tomberait sur A et
        # renverrait 403 (permission) au lieu de 409 (tournoi disparu) —
        # ce test distingue explicitement les deux.
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_tournoi_selectionne_disparu_renvoie_409_pas_403(self):
        vanished_pid = self.own_pid + 999999
        http_request(self.base, "GET", f"/select_tournament?pid={vanished_pid}", self.jar)
        # Registre NON vide (CE process lui-même, "A", toujours listé) :
        # un registre totalement vide retomberait sur le repli "servi
        # localement" (voir resolve_proxy_port), ce n'est PAS le
        # scénario testé ici — B a disparu alors qu'A, lui, existe
        # toujours bel et bien.
        with patch.object(
            open_windows, "list_remote_tournaments",
            return_value=[{"pid": self.own_pid, "port": self.port, "name": "A"}],
        ):
            status, body, _ = http_request(self.base, "GET", "/players", self.jar)
        self.assertEqual(status, 409, body)
        self.assertTrue(json.loads(body).get("tournament_gone") is True)


class EliminateIdempotencePreservedTest(_DirtoEnforcementTestCase):
    """Le contrôle de permission ne doit rien changer à l'idempotence de
    /eliminate déjà garantie par main.py (voir tests/test_remote_control_
    reliability.py pour la couverture complète de CETTE logique) : ici,
    on vérifie seulement que le NOUVEAU filtre de permission est
    transparent — un DIRTO autorisé peut rejouer la même requête
    normalement, la même callback est appelée à chaque fois (le
    dédoublonnage réel est la responsabilité de on_eliminate lui-même,
    jamais du filtre de permission)."""

    def setUp(self):
        super().setUp()
        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._grant(self.db, self.server, "Marie", "Raj", ["eliminations"])
        self.jar = authenticated_jar(self.base, owner_name="Marie")

    def test_meme_requete_rejouee_deux_fois_toutes_deux_autorisees(self):
        body = {"eliminated_id": 1, "eliminator_id": 2, "request_id": "req-42"}
        for _ in range(2):
            status, resp_body, _ = http_request(self.base, "POST", "/eliminate", self.jar, body=body)
            self.assertEqual(status, 200, resp_body)
        self.assertEqual(
            self.on_eliminate_calls,
            [(1, 2, "req-42"), (1, 2, "req-42")],
        )


class EtancheiteMultiTournoisEnforcementTest(unittest.TestCase):
    """Tournoi A autorisé / tournoi B non autorisé : le MÊME appareil
    (même browser_id/jar — le registre open_windows est partagé entre
    tournois, exactement comme lors d'un relais _proxy réel) ne doit
    JAMAIS voir les permissions accordées sur A s'appliquer à B."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dirto_enforcement_multi_tournoi_")
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

        roster.set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Marie", roster.ROSTER_GROUP_DIRTO)

        self.db_a = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db_a.conn.close)
        self.db_b = database.Database(os.path.join(self._tmp.name, "B.tournoi"))
        self.addCleanup(self.db_b.conn.close)

        self.port_a = _free_port()
        self.port_b = _free_port()
        self.server_a = self._make_server(self.db_a, self.port_a)
        self.server_b = self._make_server(self.db_b, self.port_b)
        self.server_a.start()
        self.addCleanup(self.server_a.stop)
        self.server_b.start()
        self.addCleanup(self.server_b.stop)

        # Marie autorisée sur A ("tables") UNIQUEMENT — jamais sur B.
        self.db_a.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.server_a._test_refresh_dirto_cache()

        # Un seul appareil/jar, authentifié via A (le registre est
        # partagé — même browser_id/token valides sur B, exactement
        # comme après un relais _proxy réel).
        self.jar = authenticated_jar(f"http://127.0.0.1:{self.port_a}", owner_name="Marie")

    def _make_server(self, db, port):
        cache = {}

        def refresh():
            cache.clear()
            cache.update({
                auth["dirto_name"]: frozenset(auth["permissions"])
                for auth in db.list_dirto_authorizations()
            })

        server = remote_control.RemoteControlServer(
            on_word=lambda word: None, get_dirto_permissions=lambda name: cache.get((name or "").strip(), frozenset()),
            port=port,
        )
        server._test_refresh_dirto_cache = refresh
        return server

    def test_tables_autorise_sur_a_refuse_sur_b(self):
        status, body, _ = http_request(
            f"http://127.0.0.1:{self.port_a}", "POST", "/action/tables", self.jar,
        )
        self.assertEqual(status, 200, body)

        status, body, _ = http_request(
            f"http://127.0.0.1:{self.port_b}", "POST", "/action/tables", self.jar,
        )
        self.assertEqual(status, 403, body)


if __name__ == "__main__":
    unittest.main()
