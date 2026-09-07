"""Test ciblé du correctif du 2026-09-07 (deuxième passe) : après "Fin de
la partie" sur le TOUT DERNIER tournoi, la page principale du contrôle à
distance (_PAGE_TEMPLATE, restée ouverte sur l'iPhone) devait rester
affichée, montrer clairement qu'elle attend un nouveau tournoi, et ne
JAMAIS naviguer avant qu'un tournoi VIVANT et DIFFÉRENT de celui qu'on
vient de fermer ne soit réellement détecté.

Deux bugs corrigés ici :

1. L'en-tête (<p id="tournoi-name">) gardait le nom du tournoi fermé
   affiché indéfiniment pendant l'attente — jamais remis à un état
   neutre.

2. Race la plus grave : l'ancien mécanisme (goToLobbyWhenReady) navigait
   dès qu'UNE réponse HTTP quelconque arrivait de /lobbylist (r.ok),
   sans vérifier QUI avait répondu. Or le tournoi qu'on vient de fermer
   reste souvent joignable un court instant après avoir répondu à
   /end_tournament (fermeture réelle différée via la file d'attente
   vocale Tkinter, voir _poll_voice_queue côté Python) : une navigation
   pouvait donc être déclenchée sur la foi d'une DERNIÈRE réponse de ce
   même process en train de mourir, et échouer si son port se refermait
   entre cette vérification et la navigation réelle -> écran natif
   Safari "ERR_CONNECTION_FAILED".

   Correctif : /lobbylist expose maintenant l'en-tête HTTP réel
   "X-Own-Pid" (le pid du process qui répond VRAIMENT à CETTE requête —
   jamais relayée vers un autre process, voir do_GET). Le client compare
   ce pid à OWN_PID (celui qu'on vient de fermer, capturé au chargement
   de la page) : navigation UNIQUEMENT si différent.

Deux familles de tests :
- Des tests SERVEUR réels (RemoteControlServer réel, vraie requête HTTP)
  pour la partie qui peut être exécutée : l'en-tête X-Own-Pid.
- Des assertions structurelles sur le JS embarqué généré (même principe
  que tests/test_eliminate_page_back_button.py) pour la partie qui ne
  peut pas l'être dans cet environnement (aucun moteur JS disponible,
  voir la tentative documentée dans cette session) : elles pincent
  précisément la logique de garde (le SEUL appel de navigation de tout
  le fichier, et la condition exacte qui le protège), pas seulement la
  présence de texte."""
import json
import os
import socket
import sys
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_control  # noqa: E402


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _render_main_page():
    return remote_control._PAGE_TEMPLATE.format(
        tournament_name="Tournoi Test",
        tournament_name_json='"Tournoi Test"',
        lobby_button="",
        app_version="0.0.0-test",
        reload_script="",
        rebalance_widget="",
        own_pid=4242,
    )


# ---------------------------------------------------------------------
# Partie serveur (réelle, exécutable) : /lobbylist expose bien le pid du
# process qui répond.
# ---------------------------------------------------------------------
class LobbylistOwnPidHeaderTest(unittest.TestCase):
    def setUp(self):
        self.test_port = _free_port()
        patcher = patch.object(remote_control, "DEFAULT_PORT", self.test_port)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.server = remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_tournament_name=lambda: "Tournoi Test",
            port=self.test_port,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_lobbylist_expose_le_pid_du_process_qui_repond_vraiment(self):
        with patch.object(remote_control.open_windows, "list_remote_tournaments", return_value=[]):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.test_port}/lobbylist", timeout=2
            ) as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.headers.get("X-Own-Pid"), str(os.getpid()))

    def test_en_tete_toujours_present_meme_avec_des_tournois_listes(self):
        """Non-régression : l'ajout de l'en-tête ne dépend pas du contenu
        de la liste (rows vide ou non)."""
        fake_tournaments = [{"pid": 999999, "port": 1234, "name": "Autre", "registered_at": 1}]
        with patch.object(
            remote_control.open_windows, "list_remote_tournaments", return_value=fake_tournaments
        ):
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.test_port}/lobbylist", timeout=2
            ) as r:
                self.assertEqual(r.headers.get("X-Own-Pid"), str(os.getpid()))


# ---------------------------------------------------------------------
# Partie client (structurelle) : la page principale ne navigue jamais
# sans confirmation d'un pid différent.
# ---------------------------------------------------------------------
class ReconnectAfterLastTournamentClosedTest(unittest.TestCase):
    def test_1_nom_du_tournoi_ferme_disparait_de_l_affichage_d_attente(self):
        html = _render_main_page()
        self.assertIn('id="tournoi-name"', html)
        self.assertIn(
            "document.getElementById('tournoi-name').textContent = 'En attente d\\'un tournoi';",
            html,
        )

    def test_2_dernier_tournoi_ferme_ne_navigue_jamais_immediatement(self):
        """confirmEndTournament lui-même ne doit contenir AUCUNE
        instruction de navigation — seulement le déclenchement du
        sondage, qui seul décide plus tard si naviguer ou non."""
        html = _render_main_page()
        confirm_body = html.split("function confirmEndTournament() {")[1].split(
            "\n// Après \"Fin de la partie\""
        )[0]
        self.assertNotIn("window.location", confirm_body)
        self.assertNotIn("location.href", confirm_body)
        self.assertNotIn(".reload(", confirm_body)
        self.assertIn("waitForDifferentTournamentThenReload(OWN_PID);", confirm_body)

    def test_3_fetch_en_erreur_reste_en_attente_sans_navigation(self):
        html = _render_main_page()
        catch_body = html.split("waitForDifferentTournamentThenReload(closedPid) {")[1].split(
            ".catch(function() {"
        )[1].split("});\n}")[0]
        self.assertNotIn("window.location", catch_body)
        self.assertIn("setTimeout(function() { waitForDifferentTournamentThenReload(closedPid); }, RECONNECT_POLL_MS);", catch_body)

    def test_4_reponse_du_tournoi_qui_ferme_lui_meme_ne_declenche_pas_de_navigation(self):
        """Le coeur du correctif : la seule navigation de tout le
        mécanisme est gardée par "responderPid !== closedPid" — une
        réponse dont le pid est identique (le tournoi qu'on vient de
        fermer lui-même, encore brièvement joignable) tombe dans le
        fallthrough (setTimeout), jamais dans la navigation."""
        html = _render_main_page()
        self.assertIn(
            "var responderPid = r.ok ? Number(r.headers.get('X-Own-Pid')) : NaN;\n"
            "    if (r.ok && responderPid && responderPid !== closedPid) {\n"
            "      window.location.href = '/lobbylist';\n"
            "      return;\n"
            "    }\n"
            "    setTimeout(function() { waitForDifferentTournamentThenReload(closedPid); }, RECONNECT_POLL_MS);",
            html,
        )

    def test_5_pid_different_et_vivant_est_la_seule_condition_de_navigation(self):
        """Il n'existe qu'UN SEUL appel de navigation dans tout le
        mécanisme de reconnexion, et il est protégé par la vérification
        de pid — jamais un "else" ou un chemin alternatif qui naviguerait
        sans elle."""
        html = _render_main_page()
        mechanism = html.split("function waitForDifferentTournamentThenReload(closedPid) {")[1].split(
            "function sendAction"
        )[0]
        self.assertEqual(mechanism.count("window.location.href = '/lobbylist';"), 1)
        self.assertEqual(mechanism.count("window.location"), 1)

    def test_6_cas_multi_tournoi_existant_reste_gere_par_le_meme_mecanisme(self):
        """Non-régression : quand un autre tournoi existe déjà,
        confirmEndTournament affiche toujours le même message "Retour au
        Lobby..." et déclenche le MÊME sondage (pas de chemin spécial
        qui naviguerait directement sans vérifier le pid)."""
        html = _render_main_page()
        self.assertIn(
            "status.textContent = data.other_tournaments_remain\n"
            "      ? 'Partie terminée. Retour au Lobby...'\n"
            "      : 'Partie terminée. En attente d\\'un nouveau tournoi...';\n"
            "    waitForDifferentTournamentThenReload(OWN_PID);",
            html,
        )

    def test_ancien_mecanisme_non_securise_entierement_retire(self):
        """L'ancien mécanisme (qui navigue sur la seule réussite HTTP,
        sans vérifier qui a répondu) a bien disparu, pas seulement
        contourné."""
        html = _render_main_page()
        for old_name in (
            "goToLobbyWhenReady",
            "retryGoToLobbyOrGiveUp",
            "retryGoToLobbyOrKeepWaiting",
            "LOBBY_RETRY_DELAYS_MS",
            "LOBBY_SLOW_RETRY_MS",
        ):
            self.assertNotIn(old_name, html)

    def test_pas_de_popup_ni_d_erreur_repetitive_pendant_l_attente(self):
        html = _render_main_page()
        mechanism = html.split("function waitForDifferentTournamentThenReload(closedPid) {")[1].split(
            "function sendAction"
        )[0]
        self.assertNotIn("alert(", mechanism)
        self.assertNotIn("confirm(", mechanism)
        self.assertNotIn("Échec", mechanism)


if __name__ == "__main__":
    unittest.main()
