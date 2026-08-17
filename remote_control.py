# -*- coding: utf-8 -*-
"""
Contrôle à distance depuis un téléphone (ou toute autre appareil sur le
même réseau Wifi) : un tout petit serveur web embarqué, sans aucune
dépendance en plus de la bibliothèque standard, sert une page mobile avec
3 gros boutons — "Élimination", "Chronomètre", "Terminé" — équivalents
exacts des commandes vocales/raccourcis clavier (voir voice_command.py et
App._on_voice_word dans main.py). Rien n'est installé sur le téléphone :
juste ouvrir une adresse dans son navigateur, sur le wifi du club.

Volontairement sans mot de passe ni compte : l'accès est limité à qui est
déjà sur le même réseau Wifi local (comme le reste de l'application, qui
n'a pas non plus de système d'authentification), et l'action déclenchée
est de toute façon la même que dire "Terminé" à voix haute ou appuyer sur
Ctrl+Maj+T — rien de destructeur, rien qui touche aux données du tournoi
directement.
"""
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 8765

# Les 3 actions possibles, identiques à celles de voice_command.py
# (voir _TRIGGER_WORDS) — un mot en dehors de cette liste est refusé.
_VALID_ACTIONS = {"elimination", "chronometre", "terminer"}

_PAGE_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Contrôle à distance</title>
<style>
  body {{
    margin: 0; padding: 24px 16px 40px;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }}
  h1 {{ font-size: 20px; color: #e8c468; margin: 0 0 4px; }}
  .tournoi {{ color: #b9ad8f; font-size: 14px; margin: 0 0 28px; }}
  button {{
    display: block; width: 100%; max-width: 420px; margin: 0 auto 18px;
    padding: 26px 10px; font-size: 22px; font-weight: 700;
    border: none; border-radius: 14px; color: #fff;
    -webkit-tap-highlight-color: transparent;
  }}
  button:active {{ transform: scale(0.97); }}
  #btn-elimination {{ background: #b5442e; }}
  #btn-chronometre {{ background: #1f6b3a; }}
  #btn-terminer {{ background: #8a6d1f; }}
  #status {{
    max-width: 420px; margin: 20px auto 0; min-height: 22px;
    color: #b9ad8f; font-size: 15px;
  }}
</style>
</head>
<body>
  <h1>🎙 Contrôle à distance</h1>
  <p class="tournoi">{tournament_name}</p>

  <button id="btn-elimination" onclick="sendAction('elimination', this)">⏸ Élimination</button>
  <button id="btn-chronometre" onclick="sendAction('chronometre', this)">▶ Chronomètre</button>
  <button id="btn-terminer" onclick="sendAction('terminer', this)">✅ Terminé</button>

  <p id="status"></p>

<script>
function sendAction(action, btn) {{
  var status = document.getElementById('status');
  status.textContent = 'Envoi...';
  fetch('/action/' + action, {{ method: 'POST' }})
    .then(function(r) {{
      if (!r.ok) throw new Error('erreur ' + r.status);
      status.textContent = 'Envoyé : ' + btn.textContent;
      setTimeout(function() {{ status.textContent = ''; }}, 2000);
    }})
    .catch(function(e) {{
      status.textContent = 'Échec (' + e.message + ') — vérifiez le wifi.';
    }});
}}
</script>
</body>
</html>
"""


def local_ip():
    """Adresse IP locale de cette machine sur le réseau Wifi/Ethernet
    actuel (pas 127.0.0.1) — pour l'afficher à l'utilisateur, à taper dans
    le navigateur du téléphone. N'envoie en fait aucune donnée (le socket
    UDP n'est jamais réellement utilisé pour émettre) : c'est une astuce
    standard pour demander au système quelle interface réseau serait
    utilisée pour joindre une adresse externe, sans nécessiter internet."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class RemoteControlServer:
    """Petit serveur HTTP embarqué (bibliothèque standard uniquement),
    tourne dans un thread dédié. `on_word(action)` est appelé depuis ce
    thread à chaque bouton pressé sur le téléphone — à l'appelant de
    repasser sur le thread Tkinter si besoin (voir App._on_voice_word,
    déjà pensé pour être appelé depuis un thread d'arrière-plan via une
    file — voir main.py)."""

    def __init__(self, on_word, get_tournament_name=None, port=DEFAULT_PORT):
        self.on_word = on_word
        self.get_tournament_name = get_tournament_name or (lambda: "Tournoi")
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self):
        if self.is_running:
            return
        on_word = self.on_word
        get_name = self.get_tournament_name

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # pas de log console à chaque requête (bruyant)

            def do_GET(self):
                if self.path not in ("/", "/index.html"):
                    self.send_error(404)
                    return
                body = _PAGE_TEMPLATE.format(
                    tournament_name=_escape_html(get_name())
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if not self.path.startswith("/action/"):
                    self.send_error(404)
                    return
                action = self.path[len("/action/"):]
                if action not in _VALID_ACTIONS:
                    self.send_error(400, "Action inconnue")
                    return
                on_word(action)
                body = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def url(self):
        return f"http://{local_ip()}:{self.port}"


def _escape_html(text):
    return (
        str(text).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
