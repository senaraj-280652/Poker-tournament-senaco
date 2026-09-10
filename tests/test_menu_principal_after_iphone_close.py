"""Test d'intégration ciblé du BUG 1 : "🏠 Menu principal" inopérant sur
le survivant après avoir fermé 2 tournois sur 3 depuis l'iPhone.

Contrairement à un test purement structurel (juger seulement la
présence d'un `try` dans le script AppleScript généré, jugé insuffisant
pour ce bug), ce test exécute la VRAIE chaîne avec de vrais processus :
3 vrais tournois (fichiers .tournoi jetables dans le répertoire
scratch/temp), fermés 2 sur 3 par de VRAIES requêtes HTTP /end_tournament
(exactement ce que fait le téléphone), puis appelle réellement
spawn_app_process() + open_windows.bring_pid_to_front() (le chemin exact
de App._open_new_window) sur le survivant.

Limite assumée et documentée : ce test ne peut PAS vérifier le résultat
VISUEL (la fenêtre est-elle réellement au premier plan à l'écran) —
aucun outil de capture d'écran/accessibilité n'est disponible dans cet
environnement de test (voir aussi test_bring_pid_to_front_script.py).
Il vérifie en revanche que TOUTE la chaîne mécanique (spawn réel,
process réellement vivant, appel à bring_pid_to_front, exécution réelle
d'osascript) se déroule sans lever d'exception ni retourner un code
d'erreur inattendu — exactement les étapes 1 à 9 demandées, tracées
avec de vrais PID et un vrai osascript, pas une hypothèse de lecture de
code.

Nettoie systématiquement tous les processus qu'il lance (y compris en
cas d'échec du test) et n'utilise que des fichiers .tournoi jetables
dans un répertoire temporaire dédié — jamais un fichier réel de
l'utilisateur, jamais ~/.poker_tournament/open_windows.json directement
(ce registre partagé réel EST utilisé en lecture/écriture par les vrais
sous-processus lancés ici, comme le ferait n'importe quelle vraie
fenêtre de l'application — c'est le sujet même de ce test — mais
uniquement via des entrées identifiées par leurs propres pid/chemins
jetables, jamais en écrasant une entrée existante d'un tournoi réel)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402

MAIN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def _post_json(url, payload, timeout=5, cookie=None):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    if cookie:
        req.add_header("Cookie", cookie)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def _select_tournament_cookie(any_entry_port, target_pid, auth_cookie=None, timeout=5):
    """Reproduit exactement ce que fait un téléphone qui vient de choisir
    `target_pid` dans le Lobby (voir _handle_select_tournament) : GET
    /select_tournament?pid=... — toujours traité localement quel que soit
    le port interrogé (voir do_GET) — puis renvoie le cookie
    "selected_pid=..." posé en réponse, à réutiliser sur les requêtes
    suivantes exactement comme le ferait le navigateur du téléphone.
    Sans cette sélection explicite, une requête sans cookie serait
    résolue via resolve_current_pid (le plus récemment ouvert, voir
    remote_control.py) — correct pour un vrai téléphone qui charge
    d'abord "/", mais ambigu pour ce test qui vise un pid précis
    directement.

    `auth_cookie` (voir _authenticate, demande du 2026-09-09) : requis
    depuis que /select_tournament exige un appareil authentifié/approuvé
    — sans lui, la requête est redirigée vers /login (302) SANS jamais
    poser de cookie "selected_pid"."""
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None  # ne PAS suivre le 302 : on veut son Set-Cookie à lui, pas "/"

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
        # Avec redirect_request -> None, urllib finit quand même par
        # traiter ce 302 comme une "erreur" HTTP (aucun handler ne l'a
        # transformé en réponse normale) — le Set-Cookie de CETTE
        # réponse reste lisible sur l'exception elle-même.
        cookie = e.headers.get("Set-Cookie")
        e.close()
        return cookie


def _authenticate(entry_port, register_cleanup, timeout=5):
    """Authentifie ce test comme un téléphone APPROUVÉ pour la session
    réelle en cours (demande du 2026-09-09, "sécurisation du contrôle à
    distance" puis "approbation des téléphones") — nécessaire depuis
    l'ajout du code à 6 chiffres + approbation par appareil : sans
    cookies rc_bid/rc_auth valides, /end_tournament (et toute autre
    route sensible) répond désormais 401. Reproduit le parcours RÉEL
    d'un téléphone (code correct -> "pending" -> approuvé -> code
    ressaisi -> jeton de session) — seule l'étape "Autoriser" (un clic
    sur le Mac en usage normal) est ici un appel direct à open_windows,
    exactement comme le ferait main.py. `register_cleanup` (typiquement
    self.addCleanup) révoque l'appareil de test à la fin, pour ne pas
    laisser une entrée "approuvée" dans le VRAI registre partagé de
    l'utilisateur (~/.poker_tournament/remote_control_devices.json,
    volontairement utilisé tel quel par ce fichier — voir sa docstring).
    Renvoie l'en-tête Cookie combiné (rc_bid + rc_auth) à fournir sur
    toute requête protégée suivante."""
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


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _wait_for_remote_port(path, timeout=15):
    """Attend que ce process ait enregistré son port de contrôle à
    distance dans le registre partagé réel (voir open_windows.
    update_remote_info) — polling, pas d'autre mécanisme de signal
    disponible entre processus indépendants."""
    deadline = time.time() + timeout
    abs_path = os.path.abspath(path)
    while time.time() < deadline:
        for t in open_windows.list_remote_tournaments():
            entry_path = _path_for_pid(t["pid"])
            if entry_path == abs_path:
                return t["port"]
        time.sleep(0.2)
    return None


def _path_for_pid(pid):
    return open_windows.find_path_for_pid(pid)


@unittest.skipUnless(
    os.environ.get("POKER_RUN_SLOW_INTEGRATION_TESTS") == "1",
    "Test d'intégration lent (vrais sous-processus + vrai osascript) : "
    "mettre POKER_RUN_SLOW_INTEGRATION_TESTS=1 pour l'exécuter explicitement.",
)
class MenuPrincipalAfterIphoneCloseTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="poker_repro_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.procs = []
        self.addCleanup(self._kill_all)

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
        path = os.path.join(self.tmpdir, f"{name}.tournoi")
        open(path, "a").close()  # fichier vide : Database() l'initialise au premier open
        proc = subprocess.Popen([sys.executable, MAIN_PY, path])
        self.procs.append(proc)
        return proc, path

    def test_survivant_peut_reellement_ouvrir_menu_principal_sans_exception(self):
        # 1) 3 vrais tournois, comme le scénario rapporté — lancés l'un
        # après l'autre en attendant chaque enregistrement avant le
        # suivant (comme un utilisateur cliquant réellement "Menu
        # principal" à trois reprises, jamais simultanément) : le
        # registre partagé (open_windows.json, simple fichier JSON sans
        # verrou) n'est pas conçu pour des écritures concurrentes
        # simultanées de plusieurs process au même instant — un détail
        # indépendant des deux bugs à corriger ici, pas la peine d'y
        # exposer ce test.
        procs_paths = []
        ports = {}
        for name in ("repro_x", "repro_y", "repro_z"):
            proc, path = self._spawn_tournament(name)
            procs_paths.append((proc, path))
            port = _wait_for_remote_port(path)
            self.assertIsNotNone(
                port, f"le contrôle à distance de {path} n'a jamais démarré (Paramètres > "
                      "Contrôle à distance doit être activé globalement pour ce test)",
            )
            ports[proc.pid] = port

        # 1bis) Authentification (demande du 2026-09-09, "sécurisation du
        # contrôle à distance") : UNE SEULE fois, contre n'importe lequel
        # des 3 ports — l'état d'authentification est partagé entre tous
        # les processus de la session via open_windows, indépendamment de
        # celui qui traite telle ou telle requête précise.
        auth_cookie = _authenticate(ports[procs_paths[0][0].pid], self.addCleanup)

        # 2) Ferme 2 des 3 par de VRAIES requêtes /end_tournament (exactement
        # ce que fait le téléphone) — la 3e (survivante) reste ouverte.
        # Sélectionne explicitement chaque cible via /select_tournament
        # avant de la fermer (comme un téléphone qui l'a choisie dans le
        # Lobby) : envoyer /end_tournament SANS cookie viserait "le plus
        # récemment ouvert" (voir resolve_current_pid, BUG 2) plutôt que
        # ce process précis-ci — correct pour un vrai téléphone qui a
        # d'abord chargé la page de CETTE cible, ambigu pour ce test qui
        # vise directement un pid.
        survivor_proc, survivor_path = procs_paths[2]
        for proc, _path in procs_paths[:2]:
            selection_cookie = _select_tournament_cookie(ports[proc.pid], proc.pid, auth_cookie=auth_cookie)
            status, body = _post_json(
                f"http://127.0.0.1:{ports[proc.pid]}/end_tournament", {"pid": proc.pid},
                cookie=f"{auth_cookie}; {selection_cookie}",
            )
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"], f"pid={proc.pid} port={ports[proc.pid]} body={body!r}")

        deadline = time.time() + 10
        for proc, _path in procs_paths[:2]:
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.2)
            self.assertIsNotNone(proc.poll(), f"le tournoi {proc.pid} aurait dû se fermer")
        self.assertTrue(_pid_alive(survivor_proc.pid), "le survivant ne devrait pas avoir été affecté")

        # 3-6) "Menu principal" du survivant : spawn_app_process() (import
        # tardif, une fois main.py chargeable sans lancer sa propre
        # mainloop) + vérification que le nouveau process existe et reste
        # vivant.
        import main  # noqa: PLC0415 (import tardif volontaire, voir ci-dessus)

        new_proc = main.spawn_app_process()
        self.procs.append(new_proc)  # nettoyé (terminate + wait) par _kill_all via addCleanup
        self.assertIsNotNone(new_proc.pid)
        time.sleep(1.5)
        self.assertIsNone(
            new_proc.poll(),
            "le nouveau process 'Menu principal' est mort immédiatement après son lancement "
            f"(code retour {new_proc.poll()}) — la chaîne casse dès l'étape 5/6 (spawn), "
            "avant même bring_pid_to_front.",
        )

        # 7-9) bring_pid_to_front sur ce process réellement vivant : ne doit
        # jamais lever d'exception Python, quel que soit le résultat réel
        # d'osascript (dépend de la permission Accessibilité accordée à
        # CET environnement, hors du contrôle de ce test — voir
        # test_bring_pid_to_front_script.py pour la vérification dédiée
        # au contenu du script lui-même).
        try:
            open_windows.bring_pid_to_front(new_proc.pid)
        except Exception as e:  # jamais censé arriver, voir docstring de bring_pid_to_front
            self.fail(f"bring_pid_to_front a levé une exception inattendue : {e!r}")

        # Cas "Menu principal existe mais est derrière les autres fenêtres" :
        # rappeler bring_pid_to_front sur un process DÉJÀ existant depuis un
        # moment (pas juste spawné) ne doit pas non plus lever d'exception.
        time.sleep(1)
        try:
            open_windows.bring_pid_to_front(new_proc.pid)
        except Exception as e:
            self.fail(f"bring_pid_to_front (2e appel, process déjà établi) a levé : {e!r}")

    def test_bring_pid_to_front_sur_pid_disparu_ne_leve_rien(self):
        """Cas "un ancien processus Menu principal a disparu" : cible un
        pid qui vient de se terminer — doit rester un no-op silencieux,
        jamais une exception."""
        proc, _path = self._spawn_tournament("repro_ephemere")
        proc.terminate()
        proc.wait(timeout=5)
        self.assertFalse(_pid_alive(proc.pid))
        try:
            open_windows.bring_pid_to_front(proc.pid)
        except Exception as e:
            self.fail(f"bring_pid_to_front sur un pid disparu a levé : {e!r}")


if __name__ == "__main__":
    unittest.main()
