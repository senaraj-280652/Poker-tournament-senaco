# -*- coding: utf-8 -*-
"""
Contrôle à distance depuis un téléphone (ou toute autre appareil sur le
même réseau Wifi) : un tout petit serveur web embarqué, sans aucune
dépendance en plus de la bibliothèque standard, sert :

- une page mobile avec 3 gros boutons — "Joueurs", "Chronomètre",
  "Terminé" — équivalents exacts des raccourcis clavier (voir
  App._on_voice_word dans main.py) ;
- une page "Éliminations" à deux colonnes (glisser un joueur éliminé, à
  gauche, sur son éliminateur, à droite, avec confirmation — cet ordre,
  éliminé puis éliminateur, correspond à l'usage en salle de poker) pour
  gérer les éliminations entièrement depuis le téléphone, sans repasser
  par le PC — pensée pour un responsable qui joue aussi à une table et
  ne peut pas se lever à chaque élimination ;
- un "Lobby" (bouton au-dessus de "Joueurs", visible seulement si plus
  d'un tournoi tourne en même temps — voir open_windows.py) pour choisir
  QUEL tournoi gérer depuis le téléphone. Chaque tournoi/Sit & Go est un
  processus indépendant (voir spawn_app_process dans main.py) qui essaie
  de démarrer son PROPRE RemoteControlServer sur le port 8765 ; seul le
  premier y arrive, les suivants retombent automatiquement sur un port
  libre quelconque (voir RemoteControlServer.start) — invisible pour le
  téléphone, qui ne parle jamais qu'au port 8765. Le Lobby, servi par ce
  processus-là, lit le registre partagé (open_windows.list_remote_
  tournaments) pour lister tous les tournois joignables, et une fois un
  tournoi choisi (cookie "selected_pid"), RELAIE en interne (127.0.0.1)
  chaque requête suivante vers le port réel de ce tournoi précis (voir
  Handler._proxy_target/_proxy) — le téléphone ne voit jamais qu'une
  seule adresse, tout le raccordement entre processus se fait côté PC.

Rien n'est installé sur le téléphone : juste ouvrir une adresse dans son
navigateur, sur le wifi du club.

Volontairement sans mot de passe ni compte : l'accès est limité à qui est
déjà sur le même réseau Wifi local (comme le reste de l'application, qui
n'a pas non plus de système d'authentification), et les actions
déclenchées sont les mêmes que celles déjà disponibles au clavier
(Ctrl+Maj+J/C/T) ou dans l'onglet Joueurs — rien de destructeur, rien qui
touche aux données du tournoi autrement que par une élimination normale.
"""
import json
import os
import socket
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import open_windows

DEFAULT_PORT = 8765

# Les 3 actions possibles, identiques à celles des raccourcis clavier
# (voir App._bind_voice_command_shortcuts) — un mot en dehors de cette
# liste est refusé.
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
  #btn-lobby {{ background: #5a3a8c; }}
  #btn-elimination {{ background: #b5442e; }}
  #btn-chronometre {{ background: #1f6b3a; }}
  #btn-terminer {{ background: #8a6d1f; }}
  #btn-eliminations {{ background: #2c4a6e; }}
  #status {{
    max-width: 420px; margin: 20px auto 0; min-height: 22px;
    color: #b9ad8f; font-size: 15px;
  }}
</style>
</head>
<body>
  <h1>🎙 Contrôle à distance</h1>
  <p class="tournoi">{tournament_name}</p>

  {lobby_button}
  <button id="btn-elimination" onclick="sendAction('elimination', this)">⏸ Joueurs</button>
  <button id="btn-chronometre" onclick="sendAction('chronometre', this)">▶ Chronomètre</button>
  <button id="btn-terminer" onclick="sendAction('terminer', this)">✅ Terminé</button>
  <button id="btn-eliminations" onclick="window.location.href='/eliminate'">🎯 Gérer les éliminations</button>

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

# Page "Lobby" : liste des tournois actuellement joignables (registre
# partagé open_windows.py — voir docstring du module), un bouton par
# tournoi. Choisir un tournoi POSTe... en fait un simple lien GET vers
# /select_tournament?pid=N, qui pose un cookie et redirige vers "/" —
# toutes les requêtes suivantes de CE téléphone sont alors relayées vers
# le port de ce tournoi précis (voir Handler._proxy_target/_proxy).
# N'est jamais accédée directement par un lien visible si un seul
# tournoi est ouvert (voir do_GET, bouton "Lobby" masqué dans ce cas).
_LOBBY_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Lobby</title>
<style>
  body {{
    margin: 0; padding: 24px 16px 40px;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }}
  h1 {{ font-size: 20px; color: #e8c468; margin: 0 0 20px; }}
  a.back {{ display: block; color: #b9ad8f; text-decoration: none; margin-bottom: 22px; font-size: 15px; }}
  button {{
    display: block; width: 100%; max-width: 420px; margin: 0 auto 14px;
    padding: 22px 10px; font-size: 19px; font-weight: 700;
    border: none; border-radius: 14px; color: #fff; background: #3a5a8c;
    -webkit-tap-highlight-color: transparent;
  }}
  button:active {{ transform: scale(0.97); }}
  button.current {{ background: #e8c468; color: #10241a; }}
  p.empty {{ color: #b9ad8f; font-size: 15px; }}
</style>
</head>
<body>
  <a class="back" href="/">← Retour</a>
  <h1>🏛 Choisir un tournoi</h1>
  {rows}
</body>
</html>
"""

# Page "Éliminations" : deux colonnes tactiles (éliminé à gauche,
# éliminateur à droite — cet ordre correspond à l'usage en salle de
# poker, où l'on note d'abord le joueur éliminé puis son éliminateur),
# chacune avec son propre ascenseur vertical. Glisser un nom de gauche
# sur un nom de droite (doigt ou stylet tactile) déclenche une demande de
# confirmation puis POST /eliminate. Sans bibliothèque externe
# (glisser-déposer géré à la main via les événements tactiles, pas
# HTML5 drag-and-drop — peu fiable au toucher).
_ELIMINATE_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>Éliminations</title>
<style>
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  html, body {{
    margin: 0; padding: 0; height: 100%; overflow: hidden;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  }}
  #topbar {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #0b1c15; border-bottom: 1px solid #294235;
  }}
  #topbar a {{ color: #b9ad8f; text-decoration: none; font-size: 15px; }}
  #topbar .tournoi {{ color: #e8c468; font-size: 15px; font-weight: 700; }}
  #columns {{ display: flex; height: calc(100% - 46px); }}
  .col {{ flex: 1; display: flex; flex-direction: column; min-width: 0; }}
  .col-left {{ border-right: 2px solid #294235; }}
  .col h2 {{
    margin: 0; padding: 10px; font-size: 15px; text-align: center;
    background: #0b1c15; color: #e8c468; position: sticky; top: 0;
  }}
  .col .list {{ flex: 1; overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 8px; }}
  .player-item {{
    padding: 14px 8px; margin-bottom: 8px; border-radius: 10px;
    background: #1c3d2c; font-size: 15px; text-align: center;
    -webkit-user-select: none; -webkit-touch-callout: none;
    user-select: none; touch-action: pan-y; line-height: 1.3;
  }}
  .player-item .sub {{ display: block; font-size: 12px; color: #9fb8a8; margin-top: 2px; }}
  .player-item.dragging {{ opacity: 0.35; }}
  .player-item.drop-hover {{ background: #e8c468; color: #10241a; }}
  .player-item.drop-hover .sub {{ color: #4a3c10; }}
  /* Pendant un glissement, les joueurs d'une autre table que l'éliminé
     ne peuvent pas être l'éliminateur (au poker, on n'élimine que
     quelqu'un de sa propre table) — grisés et non ciblables. */
  .player-item.not-eligible {{ opacity: 0.25; pointer-events: none; }}
  #empty {{
    text-align: center; color: #b9ad8f; padding: 40px 16px; font-size: 15px;
  }}
  #ghost {{
    position: fixed; pointer-events: none; z-index: 1000; display: none;
    padding: 14px 18px; border-radius: 10px; background: #e8c468; color: #10241a;
    font-weight: 700; font-size: 15px; box-shadow: 0 4px 14px rgba(0,0,0,.4);
    max-width: 70vw; text-align: center;
  }}
</style>
</head>
<body>
  <div id="topbar">
    <a href="/">← Retour</a>
    <span class="tournoi">{tournament_name}</span>
    <span style="width:50px"></span>
  </div>
  <div id="columns">
    <div class="col col-left">
      <h2>Éliminé — glisser vers →</h2>
      <div class="list" id="list-left"></div>
    </div>
    <div class="col col-right">
      <h2>← déposer ici — Éliminateur</h2>
      <div class="list" id="list-right"></div>
    </div>
  </div>
  <div id="ghost"></div>

<script>
var players = [];
var lastSignature = null;
var pending = null;    // candidat de glissement pas encore confirmé : {{id, label, sub, el, startX, startY, engaged}}
var hoverTarget = null;
var refreshTimer = null;
// Distance (px) à partir de laquelle on tranche entre "c'est un défilement"
// (mouvement surtout vertical) et "c'est un glissement" (mouvement surtout
// horizontal, colonne de gauche vers colonne de droite) — voir onTouchMove.
var DRAG_THRESHOLD = 10;

function fmtLabel(p) {{
  return p.name;
}}
function fmtSub(p) {{
  if (p.table) {{
    return p.table + (p.seat ? (' — Siège ' + p.seat) : '');
  }}
  return '';
}}

function loadPlayers() {{
  // Ne pas rafraîchir pendant un glissement en cours : ça décrocherait
  // l'élément suivi sous le doigt. Le prochain tic (4s après) rattrapera.
  if (pending && pending.engaged) return;
  fetch('/players').then(function(r) {{ return r.json(); }}).then(function(data) {{
    var sig = JSON.stringify(data);
    if (sig === lastSignature) return;  // rien n'a changé : pas de re-rendu (évite le clignotement et perd le défilement en cours)
    lastSignature = sig;
    players = data;
    renderLists();
  }}).catch(function() {{ /* réseau momentanément indisponible : on retentera */ }});
}}

function renderLists() {{
  var left = document.getElementById('list-left');
  var right = document.getElementById('list-right');
  left.innerHTML = '';
  right.innerHTML = '';
  if (players.length === 0) {{
    left.innerHTML = '<div id="empty">Aucun joueur actif</div>';
    right.innerHTML = '<div id="empty">Aucun joueur actif</div>';
    return;
  }}
  // Ordre alphabétique (plutôt que l'ordre table/siège renvoyé par le PC)
  // pour retrouver un joueur plus facilement dans une longue liste.
  var sorted = players.slice().sort(function(a, b) {{
    return a.name.localeCompare(b.name, 'fr', {{sensitivity: 'base'}});
  }});
  sorted.forEach(function(p) {{
    left.appendChild(makeItem(p, true));   // gauche = Éliminé (glissable)
    right.appendChild(makeItem(p, false)); // droite = Éliminateur (cible)
  }});
}}

function makeItem(p, isSource) {{
  var div = document.createElement('div');
  div.className = 'player-item';
  div.dataset.id = p.id;
  div.dataset.table = p.table || '';
  var sub = fmtSub(p);
  div.dataset.name = fmtLabel(p);
  div.dataset.sub = sub;
  div.innerHTML = fmtLabel(p) + (sub ? '<span class="sub">' + sub + '</span>' : '');
  if (isSource) {{
    div.addEventListener('touchstart', onTouchStart, {{passive: true}});
  }} else {{
    div.dataset.target = 'true';
  }}
  return div;
}}

// Un joueur ne peut éliminer que quelqu'un de SA PROPRE table (au
// poker, on n'élimine jamais quelqu'un assis à une autre table) :
// pendant un glissement engagé, grise/désactive dans la colonne de
// droite (Éliminateur) tous les joueurs qui ne sont pas à la même table
// que l'éliminé en cours de glissement, pour ne laisser sélectionnable
// que les candidats valides.
function applyTableFilter(tableName) {{
  var right = document.getElementById('list-right');
  Array.prototype.forEach.call(right.children, function(item) {{
    if (item.dataset.table !== tableName) {{
      item.classList.add('not-eligible');
    }}
  }});
}}
function clearTableFilter() {{
  var right = document.getElementById('list-right');
  Array.prototype.forEach.call(right.children, function(item) {{
    item.classList.remove('not-eligible');
  }});
}}

function moveGhost(x, y) {{
  var ghost = document.getElementById('ghost');
  ghost.style.left = x + 'px';
  ghost.style.top = (y - 60) + 'px';
  ghost.style.transform = 'translate(-50%, -50%)';
}}

// Glissement tactile pensé pour cohabiter avec le défilement natif de la
// liste (voir touch-action: pan-y en CSS) : on ne décide PAS dès
// touchstart qu'il s'agit d'un glissement — sinon un simple geste vers le
// bas pour voir les joueurs plus loin dans la liste serait toujours
// capturé comme un début de glissement, empêchant tout défilement. On
// attend un déplacement suffisant (DRAG_THRESHOLD) puis on regarde sa
// direction dominante : plutôt vertical => c'est un défilement, on se
// retire et on laisse le navigateur faire son travail ; plutôt horizontal
// (de la colonne de gauche vers celle de droite) => c'est un glissement,
// on l'engage réellement (ghost, preventDefault) à partir de là.
function onTouchStart(e) {{
  var el = e.currentTarget;
  var t = e.touches[0];
  pending = {{
    id: el.dataset.id, label: el.dataset.name, sub: el.dataset.sub,
    table: el.dataset.table, el: el,
    startX: t.clientX, startY: t.clientY, engaged: false,
  }};
  document.addEventListener('touchmove', onTouchMove, {{passive: false}});
  document.addEventListener('touchend', onTouchEnd);
  document.addEventListener('touchcancel', onTouchEnd);
}}

function onTouchMove(e) {{
  if (!pending) return;
  var t = e.touches[0];
  var dx = t.clientX - pending.startX;
  var dy = t.clientY - pending.startY;

  if (!pending.engaged) {{
    if (Math.abs(dx) < DRAG_THRESHOLD && Math.abs(dy) < DRAG_THRESHOLD) {{
      return;  // pas encore assez de mouvement pour trancher
    }}
    if (Math.abs(dy) >= Math.abs(dx)) {{
      // Mouvement surtout vertical : défilement, pas glissement. On se
      // retire complètement et on laisse le navigateur défiler nativement.
      cancelPending();
      return;
    }}
    pending.engaged = true;
    pending.el.classList.add('dragging');
    applyTableFilter(pending.table);
    var ghost = document.getElementById('ghost');
    ghost.textContent = pending.label;
    ghost.style.display = 'block';
  }}

  e.preventDefault();
  moveGhost(t.clientX, t.clientY);
  var el = document.elementFromPoint(t.clientX, t.clientY);
  var target = el ? el.closest('[data-target="true"]') : null;
  if (hoverTarget && hoverTarget !== target) {{
    hoverTarget.classList.remove('drop-hover');
  }}
  if (target) {{
    target.classList.add('drop-hover');
  }}
  hoverTarget = target;
}}

function cancelPending() {{
  document.removeEventListener('touchmove', onTouchMove);
  document.removeEventListener('touchend', onTouchEnd);
  document.removeEventListener('touchcancel', onTouchEnd);
  if (pending && pending.el) pending.el.classList.remove('dragging');
  if (hoverTarget) {{ hoverTarget.classList.remove('drop-hover'); hoverTarget = null; }}
  clearTableFilter();
  document.getElementById('ghost').style.display = 'none';
  pending = null;
}}

function onTouchEnd(e) {{
  var wasEngaged = pending && pending.engaged;
  var drag = pending;
  var target = hoverTarget;
  cancelPending();

  if (wasEngaged && target && target.dataset.id !== drag.id) {{
    // Colonne de gauche (glissée) = Éliminé, colonne de droite (déposée
    // dessus) = Éliminateur — voir confirmElimination pour l'ordre inverse
    // historique (avant l'inversion demandée par un joueur : en salle, on
    // note d'abord l'éliminé, puis son éliminateur).
    confirmElimination(
      target.dataset.id, target.dataset.name, target.dataset.sub,
      drag.id, drag.label, drag.sub
    );
  }}
}}

function confirmElimination(eliminatorId, eliminatorLabel, eliminatorSub, eliminatedId, eliminatedLabel, eliminatedSub) {{
  var elimText = eliminatedLabel + (eliminatedSub ? ' ' + eliminatedSub : '');
  var elorText = eliminatorLabel + (eliminatorSub ? ' ' + eliminatorSub : '');
  // Éliminé d'abord, éliminateur ensuite : dans le même ordre que le
  // geste (on part de la colonne de gauche, l'éliminé) et que l'usage en
  // salle de poker (on note d'abord qui est éliminé, puis par qui).
  var msg = elimText + '\\nest éliminé par\\n' + elorText + ' ?';
  if (!window.confirm(msg)) return;
  fetch('/eliminate', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{eliminated_id: eliminatedId, eliminator_id: eliminatorId}})
  }}).then(function(r) {{
    if (!r.ok) throw new Error('erreur ' + r.status);
    lastSignature = null;  // forcer le prochain rendu même si la liste redevient identique entre-temps
    loadPlayers();
  }}).catch(function(e) {{
    window.alert('Échec : ' + e.message + ' — vérifiez le wifi.');
  }});
}}

loadPlayers();
refreshTimer = setInterval(loadPlayers, 4000);
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
    tourne dans un thread dédié (un thread par requête, voir
    ThreadingHTTPServer). Chaque callback ci-dessous est donc appelé
    depuis CE thread, jamais celui de l'interface Tkinter — à l'appelant
    de ne jamais lire/écrire directement une ressource liée à ce dernier
    thread (ex : la connexion SQLite) depuis ces callbacks, et de repasser
    par un mécanisme thread-safe à la place (voir App._poll_voice_queue /
    _remote_players_cache dans main.py, qui font déjà ce travail).

    - `on_word(action)` : une des 3 actions simples ("elimination" /
      "chronometre" / "terminer").
    - `get_players()` : renvoie la liste des joueurs actifs à afficher sur
      la page Éliminations — liste de dicts {id, name, table, seat}.
    - `on_eliminate(eliminated_id, eliminator_id)` : élimination décidée
      depuis la page Éliminations (eliminator_id peut être None)."""

    def __init__(self, on_word, get_tournament_name=None, get_players=None,
                 on_eliminate=None, port=DEFAULT_PORT):
        self.on_word = on_word
        self.get_tournament_name = get_tournament_name or (lambda: "Tournoi")
        self.get_players = get_players or (lambda: [])
        self.on_eliminate = on_eliminate or (lambda eliminated_id, eliminator_id: None)
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self):
        if self.is_running:
            return
        on_word = self.on_word
        get_name = self.get_tournament_name
        get_players = self.get_players
        on_eliminate = self.on_eliminate
        own_pid = os.getpid()

        def resolve_proxy_port(handler):
            """Port du tournoi actuellement choisi par CE téléphone (cookie
            "selected_pid", posé par /select_tournament — voir
            _LOBBY_PAGE), s'il diffère de ce tournoi-ci. None si aucune
            sélection, sélection = ce tournoi-ci, ou tournoi sélectionné
            disparu depuis (fenêtre fermée entre-temps) — dans tous ces
            cas, la requête est traitée localement, sur ce tournoi-ci."""
            cookie_header = handler.headers.get("Cookie", "")
            selected_pid = None
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith("selected_pid="):
                    try:
                        selected_pid = int(part.split("=", 1)[1])
                    except ValueError:
                        selected_pid = None
            if selected_pid is None or selected_pid == own_pid:
                return None
            for t in open_windows.list_remote_tournaments():
                if t["pid"] == selected_pid:
                    return t["port"]
            return None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # pas de log console à chaque requête (bruyant)

            def _send_html(self, html):
                body = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, obj, status=200):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _proxy(self, target_port):
                """Relaie telle quelle la requête en cours vers le VRAI
                port du tournoi choisi via le Lobby (127.0.0.1 — toujours
                la même machine, jamais le réseau externe) et renvoie au
                téléphone la réponse obtenue, sans qu'il n'ait jamais eu
                à connaître ce port lui-même."""
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else None
                url = f"http://127.0.0.1:{target_port}{self.path}"
                req = urllib.request.Request(url, data=body, method=self.command)
                ctype = self.headers.get("Content-Type")
                if ctype:
                    req.add_header("Content-Type", ctype)
                try:
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        data = resp.read()
                        self.send_response(resp.status)
                        self.send_header(
                            "Content-Type", resp.headers.get("Content-Type", "text/html; charset=utf-8")
                        )
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                except (OSError, urllib.error.URLError):
                    self.send_error(502, "Ce tournoi n'est momentanément plus joignable")

            def _handle_lobbylist(self):
                tournaments = open_windows.list_remote_tournaments()
                if not tournaments:
                    rows = "<p class=\"empty\">Aucun tournoi joignable pour l'instant.</p>"
                else:
                    parts = []
                    for t in sorted(tournaments, key=lambda x: x["name"].lower()):
                        is_self = t["pid"] == own_pid
                        css_class = " class=\"current\"" if is_self else ""
                        label = _escape_html(t["name"]) + (" (celui-ci)" if is_self else "")
                        parts.append(
                            f'<button{css_class} '
                            f'onclick="window.location.href=\'/select_tournament?pid={t["pid"]}\'">{label}</button>'
                        )
                    rows = "\n".join(parts)
                self._send_html(_LOBBY_PAGE.format(rows=rows))

            def _handle_select_tournament(self):
                from urllib.parse import parse_qs, urlparse
                query = parse_qs(urlparse(self.path).query)
                pid_values = query.get("pid")
                self.send_response(302)
                if pid_values:
                    try:
                        pid = int(pid_values[0])
                        self.send_header("Set-Cookie", f"selected_pid={pid}; Path=/")
                    except ValueError:
                        pass
                self.send_header("Location", "/")
                self.end_headers()

            def do_GET(self):
                # Toujours traitées ICI, jamais relayées vers un autre
                # tournoi : ce sont les pages qui permettent justement de
                # choisir/changer de tournoi.
                if self.path == "/lobbylist":
                    self._handle_lobbylist()
                    return
                if self.path.startswith("/select_tournament"):
                    self._handle_select_tournament()
                    return

                target_port = resolve_proxy_port(self)
                if target_port is not None:
                    self._proxy(target_port)
                    return

                if self.path in ("/", "/index.html"):
                    tournaments = open_windows.list_remote_tournaments()
                    lobby_button = (
                        '<button id="btn-lobby" onclick="window.location.href=\'/lobbylist\'">🏛 Lobby</button>'
                        if len(tournaments) > 1 else ""
                    )
                    self._send_html(_PAGE_TEMPLATE.format(
                        tournament_name=_escape_html(get_name()),
                        lobby_button=lobby_button,
                    ))
                elif self.path in ("/eliminate", "/eliminate.html"):
                    self._send_html(_ELIMINATE_PAGE.format(
                        tournament_name=_escape_html(get_name())
                    ))
                elif self.path == "/players":
                    self._send_json(get_players())
                else:
                    self.send_error(404)

            def do_POST(self):
                target_port = resolve_proxy_port(self)
                if target_port is not None:
                    self._proxy(target_port)
                    return

                if self.path.startswith("/action/"):
                    action = self.path[len("/action/"):]
                    if action not in _VALID_ACTIONS:
                        self.send_error(400, "Action inconnue")
                        return
                    on_word(action)
                    self._send_json({"ok": True})
                elif self.path == "/eliminate":
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        data = json.loads(raw.decode("utf-8"))
                        eliminated_id = int(data["eliminated_id"])
                        eliminator_raw = data.get("eliminator_id")
                        eliminator_id = int(eliminator_raw) if eliminator_raw not in (None, "") else None
                    except (ValueError, KeyError, TypeError):
                        self.send_error(400, "Requête invalide")
                        return
                    on_eliminate(eliminated_id, eliminator_id)
                    self._send_json({"ok": True})
                else:
                    self.send_error(404)

        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError:
            # Port déjà pris par un autre tournoi/processus (voir docstring
            # de la classe) : on prend un port libre quelconque à la place
            # plutôt que d'abandonner — ce tournoi reste joignable depuis
            # le téléphone via le Lobby (voir open_windows.
            # update_remote_info, appelé par l'appelant juste après ce
            # start()), même s'il n'est pas celui que le téléphone
            # contacte directement.
            self._httpd = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self._httpd.server_address[1]
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
