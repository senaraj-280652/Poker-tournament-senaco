# -*- coding: utf-8 -*-
"""Utilitaires PARTAGÉS (PAS un fichier de test lui-même, nom
volontairement sans le préfixe test_, donc jamais collecté par
`unittest discover`) pour les tests HTTP de remote_control.py qui
doivent franchir la nouvelle authentification (code à 6 chiffres PUIS
approbation par appareil, demande du 2026-09-09) sans être eux-mêmes
des tests DE cette authentification (voir tests/test_remote_control_
device_approval.py pour ça) : fournit un jar de cookies déjà
authentifié ET approuvé, prêt à requêter n'importe quelle route
protégée.

L'appelant reste responsable de rediriger open_windows vers un dossier
temporaire (`_registry_path`, `_remote_control_dir`) et d'avoir au
moins un chemin "ouvert" (open_windows.register) AVANT d'utiliser ces
fonctions — sans quoi verify_device_session échouerait toujours
(aucune session active), et sans la redirection ceci toucherait le vrai
~/.poker_tournament de l'utilisateur."""
import http.cookiejar
import json
import urllib.error
import urllib.request

import open_windows


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Empêche urllib de suivre automatiquement un 302 (vers /login) —
    les appelants qui veulent vérifier CE code de statut précis (voir
    tests/test_remote_control_device_approval.py) ont besoin de le
    recevoir tel quel, jamais masqué par une redirection déjà suivie."""

    def redirect_request(self, *args, **kwargs):
        return None


def http_request(base_url, method, path, jar=None, body=None, headers=None):
    """(status, body_bytes, headers_dict) — petite enveloppe autour
    d'urllib avec gestion optionnelle d'un jar de cookies (http.cookiejar.
    CookieJar), commune à tous les tests HTTP de remote_control.py."""
    handlers = [NoRedirect]
    if jar is not None:
        handlers.insert(0, urllib.request.HTTPCookieProcessor(jar))
    opener = urllib.request.build_opener(*handlers)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(base_url + path, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with opener.open(req, timeout=5) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def authenticated_jar(base_url, code=None, label="Test"):
    """Jar de cookies AUTHENTIFIÉ ET APPROUVÉ (rc_bid + rc_auth), prêt à
    requêter n'importe quelle route protégée de `base_url` — reproduit
    le parcours complet (code correct -> "pending" -> approbation
    "PC" via open_windows directement, comme le ferait main.py -> code
    ressaisi -> "approved") plutôt qu'une entrée par un raccourci
    interne, pour rester représentatif d'un VRAI téléphone."""
    jar = http.cookiejar.CookieJar()
    http_request(base_url, "GET", "/login", jar)
    code = code or open_windows.remote_session_code()
    status, body, _ = http_request(base_url, "POST", "/authenticate", jar, body={"code": code})
    data = json.loads(body)
    assert data == {"ok": True, "status": "pending"}, data
    pending = open_windows.list_pending_remote_devices()
    assert pending, "aucune demande en attente trouvée après /authenticate"
    browser_id = pending[-1]["browser_id"]
    ok = open_windows.approve_remote_device(browser_id, label=label)
    assert ok, "approve_remote_device a échoué de façon inattendue"
    status, body, _ = http_request(base_url, "POST", "/authenticate", jar, body={"code": code})
    data = json.loads(body)
    assert data == {"ok": True, "status": "approved"}, data
    return jar
