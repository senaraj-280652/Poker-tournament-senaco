# -*- coding: utf-8 -*-
"""Correctif (demande du 2026-09-19, validée puis appliquée) du bug réel
constaté en validation manuelle Mac + iPhone : deux tournois ouverts (A
et B), un iPhone sélectionne B depuis le Lobby, B est ensuite fermé
normalement sur le Mac pendant que A reste ouvert — l'iPhone continuait
d'afficher "Connecté" (vert) et l'ancien affichage de B, sans jamais
revenir au Lobby ; PIRE, une action (élimination) envoyée dans cet état
était réellement appliquée sur A.

Cause : resolve_current_pid() (remote_control.py) ne distingue pas
"aucune sélection explicite encore faite" de "une sélection explicite
existe mais son tournoi a disparu" — dans les deux cas, elle retombait
silencieusement sur le tournoi le plus récemment ouvert PARMI CEUX
ENCORE VIVANTS. Comme resolve_proxy_port (donc /players ET /eliminate)
s'appuyait sur cette même fonction, ce repli silencieux permettait
qu'une action destinée à B soit exécutée sur A.

Correctif appliqué, localisé à resolve_proxy_port() UNIQUEMENT :
- resolve_current_pid() reste VOLONTAIREMENT inchangée (son unique
  usage restant est l'affichage "(celui-ci)" du Lobby — voir sa
  docstring mise à jour) ;
- resolve_proxy_port() vérifie maintenant EN PREMIER si le cookie
  "selected_pid" désigne une sélection EXPLICITE ; si ce tournoi n'est
  plus dans le registre, renvoie le sentinel SELECTION_VANISHED —
  JAMAIS un repli vers un autre tournoi vivant ;
- les deux points d'appel (do_GET/do_POST) répondent alors 409 avec
  {"tournament_gone": true}, journalisé (log_remote_event), sans jamais
  router la requête ailleurs ;
- le cas SANS sélection explicite (cookie absent/invalide) continue de
  suivre le comportement HISTORIQUE (repli sur le plus récemment
  ouvert) — voir NoRegressionOnFreshSelectionTest, toujours vert.

Ces tests étaient ROUGES avant le correctif (voir l'historique Git de
ce fichier) ; ils sont maintenant VERTS et couvrent la non-régression."""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import open_windows  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402


# =====================================================================
# 1. resolve_current_pid seule (pure, aucun Tk, aucun HTTP réel) :
#    documente qu'elle garde VOLONTAIREMENT son ancien comportement —
#    le correctif est localisé à resolve_proxy_port (section 2), jamais
#    ici. Cette fonction ne sert plus qu'à l'affichage "(celui-ci)" du
#    Lobby, où ce repli reste inoffensif (aucune conséquence de
#    sécurité, juste un surlignage visuel).
# =====================================================================
class ResolveCurrentPidUnchangedForDisplayOnlyTest(unittest.TestCase):
    def test_reste_utilisee_uniquement_pour_laffichage_repli_historique_conserve(self):
        """Non-régression EXPLICITE du correctif du 2026-09-19 : cette
        fonction n'a PAS été modifiée — elle retombe toujours sur le
        tournoi le plus récemment ouvert quand la sélection a disparu.
        C'est resolve_proxy_port (section 2 ci-dessous), pas cette
        fonction, qui porte désormais la protection réelle contre le
        routage silencieux."""
        pid_a = 11111
        pid_b_disparu = 22222
        live_tournaments = [{"pid": pid_a, "port": 9001, "name": "A", "registered_at": time.time()}]

        result = remote_control.resolve_current_pid(
            f"selected_pid={pid_b_disparu}", own_pid=pid_a, live_tournaments=live_tournaments,
        )

        self.assertEqual(result, pid_a)  # comportement historique conservé, volontairement

    def test_selection_disparue_alors_quaucun_tournoi_ne_survit(self):
        """Variante : B était le SEUL tournoi ouvert. Là, le repli sur
        own_pid est déjà le comportement actuel (if not live_tournaments:
        return own_pid) — ce cas particulier n'est pas concerné par le
        correctif (aucun autre tournoi vivant vers lequel router par
        erreur)."""
        pid_b_disparu = 22222
        own_pid = 33333  # ce process-ci, qui répond quand même à la requête
        result = remote_control.resolve_current_pid(
            f"selected_pid={pid_b_disparu}", own_pid=own_pid, live_tournaments=[],
        )
        self.assertEqual(result, own_pid)


class NoRegressionOnFreshSelectionTest(unittest.TestCase):
    """Non-régression EXPLICITEMENT demandée : le comportement de
    démarrage SANS sélection explicite (cookie absent) doit rester
    inchangé — repli sur le tournoi le plus récemment ouvert."""

    def test_sans_cookie_du_tout_repli_sur_le_plus_recent_inchange(self):
        pid_old = 111
        pid_recent = 222
        live_tournaments = [
            {"pid": pid_old, "port": 9001, "name": "Vieux", "registered_at": 100.0},
            {"pid": pid_recent, "port": 9002, "name": "Récent", "registered_at": 200.0},
        ]
        result = remote_control.resolve_current_pid("", own_pid=999, live_tournaments=live_tournaments)
        self.assertEqual(result, pid_recent)

    def test_cookie_invalide_syntaxiquement_traite_comme_absent(self):
        live_tournaments = [{"pid": 222, "port": 9002, "name": "Récent", "registered_at": 200.0}]
        result = remote_control.resolve_current_pid(
            "selected_pid=pas_un_entier", own_pid=999, live_tournaments=live_tournaments,
        )
        self.assertEqual(result, 222)


# =====================================================================
# 2. Bout en bout HTTP réel (VRAI RemoteControlServer, comme le reste du
#    chantier fiabilité) : reproduit EXACTEMENT le scénario demandé et
#    vérifie le point le plus grave (item 7) — une action /eliminate
#    envoyée avec le cookie de B disparu ne doit JAMAIS atteindre A.
# =====================================================================
class SelectedTournamentVanishedHttpTest(_RemoteControlHttpTestCase):
    """Réutilise le harnais de tests/test_remote_control_reliability.py
    (même registre temporaire, même serveur réel). CE process de test
    joue le rôle de "A" (own_pid = os.getpid(), déjà vivant et
    enregistré par setUp) ; "B" n'existe QUE comme entrée de registre
    avec un pid qui n'est PAS ce process — jamais un vrai second
    serveur nécessaire pour démontrer ce bug précis (la disparition de
    B du REGISTRE suffit, voir resolve_proxy_port)."""

    def setUp(self):
        super().setUp()
        self.eliminate_calls = []
        self.server.stop()
        # Recrée le serveur avec un on_eliminate qui enregistre CE qu'il
        # reçoit réellement — pour prouver qu'il n'est JAMAIS appelé
        # dans ce scénario (voir les assertions ci-dessous).
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            on_eliminate=lambda eliminated_id, eliminator_id, client_request_id=None: (
                self.eliminate_calls.append((eliminated_id, eliminator_id, client_request_id))
                or {"ok": True, "message": ""}
            ),
            get_players=lambda: [{"id": 1, "name": "JoueurDeA", "table": None, "seat": None, "has_photo": False}],
            port=self.port,
        )
        self.server.start()

    def test_players_repond_409_tournament_gone_jamais_les_donnees_de_a(self):
        """Vérifie précisément les questions 5/6 de la demande : le
        serveur ne doit JAMAIS répondre 200 avec les données d'un AUTRE
        tournoi (A) quand la sélection explicite (B) a disparu — 409 +
        tournament_gone: true, jamais de routage silencieux."""
        jar = authenticated_jar(self.base, owner_name="Test Admin")
        own_pid = os.getpid()
        pid_b_disparu = own_pid + 999999
        http_request(self.base, "GET", f"/select_tournament?pid={pid_b_disparu}", jar)

        with patch.object(
            open_windows, "list_remote_tournaments",
            return_value=[{"pid": own_pid, "port": self.port, "name": "A", "registered_at": time.time()}],
        ):
            status, body, _ = http_request(self.base, "GET", "/players", jar)

        self.assertEqual(status, 409)
        data = json.loads(body)
        self.assertEqual(data.get("tournament_gone"), True)
        self.assertFalse(data.get("ok"))
        content = self._read_log()
        self.assertIn("selected_tournament_vanished", content)

    def test_PRIORITAIRE_elimination_destinee_a_b_najamais_atteint_a(self):
        """Le point le plus grave de la demande (item 7) : une requête
        POST /eliminate envoyée par un téléphone dont le cookie
        sélectionne encore B (disparu) ne doit JAMAIS être exécutée par
        A — 409 + tournament_gone: true, et surtout AUCUN appel réel à
        on_eliminate."""
        jar = authenticated_jar(self.base, owner_name="Test Admin")
        own_pid = os.getpid()
        pid_b_disparu = own_pid + 999999
        http_request(self.base, "GET", f"/select_tournament?pid={pid_b_disparu}", jar)

        with patch.object(
            open_windows, "list_remote_tournaments",
            return_value=[{"pid": own_pid, "port": self.port, "name": "A", "registered_at": time.time()}],
        ):
            status, body, _ = http_request(
                self.base, "POST", "/eliminate", jar,
                body={"eliminated_id": 42, "eliminator_id": None, "request_id": "phone-thought-this-was-b"},
            )

        self.assertEqual(status, 409)
        data = json.loads(body)
        self.assertEqual(data.get("tournament_gone"), True)
        # Preuve définitive : A n'a JAMAIS exécuté cette élimination.
        self.assertEqual(
            self.eliminate_calls, [],
            f"une élimination destinée à B a atteint A : {self.eliminate_calls!r} — "
            "danger prioritaire de la demande, NE DOIT JAMAIS arriver.",
        )

    def test_sans_selection_explicite_le_repli_historique_fonctionne_encore(self):
        """Non-régression : SANS cookie selected_pid du tout (premier
        chargement de la page, aucune sélection jamais faite), le repli
        sur le tournoi le plus récemment ouvert doit continuer à
        fonctionner normalement — jamais de 409 dans ce cas, comportement
        de démarrage inchangé."""
        jar = authenticated_jar(self.base, owner_name="Test Admin")
        own_pid = os.getpid()
        with patch.object(
            open_windows, "list_remote_tournaments",
            return_value=[{"pid": own_pid, "port": self.port, "name": "A", "registered_at": time.time()}],
        ):
            status, body, _ = http_request(self.base, "GET", "/players", jar)
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(body),
            [{"id": 1, "name": "JoueurDeA", "table": None, "seat": None, "has_photo": False}],
        )


if __name__ == "__main__":
    unittest.main()
