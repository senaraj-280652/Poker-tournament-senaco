# -*- coding: utf-8 -*-
"""
Contrôle à distance depuis un téléphone (ou toute autre appareil sur le
même réseau Wifi) : un tout petit serveur web embarqué, sans aucune
dépendance en plus de la bibliothèque standard, sert :

- une page mobile avec plusieurs gros boutons — "Gérer les
  éliminations", "Plan des tables", "Mouvements", "Terminé",
  "Chronomètre" (+ petit bouton ON/OFF de pause juste à côté) et
  "Joueurs" — dont la plupart équivalent exactement aux raccourcis
  clavier (voir App._on_voice_word dans main.py) : "Plan des tables" et
  "Mouvements" ramènent respectivement l'onglet Tables/Mouvements de la
  fenêtre PRINCIPALE au premier plan (utile à distance, pas seulement
  l'écran projecteur) ; le bouton ON/OFF bascule directement pause/
  reprise du chrono, sans passer par l'écran projecteur ni l'onglet
  Joueurs (voir /clock_state, sondé toutes les 3s pour refléter l'état
  réel même s'il a changé par un autre moyen, ex. au clavier) ;
- une page "Éliminations" à deux colonnes (glisser un joueur éliminé, à
  gauche, sur son éliminateur, à droite, avec confirmation — cet ordre,
  éliminé puis éliminateur, correspond à l'usage en salle de poker) pour
  gérer les éliminations entièrement depuis le téléphone, sans repasser
  par le PC — pensée pour un responsable qui joue aussi à une table et
  ne peut pas se lever à chaque élimination ;
- une page "Photos" listant TOUT le répertoire de joueurs habituels (pas
  seulement ceux actifs dans le tournoi en cours — on peut vouloir
  photographier un joueur du club avant même qu'il ne soit inscrit ce
  soir-là), un bouton 📷 par joueur qui ouvre directement l'appareil
  photo du téléphone (simple <input type=file capture=environment>,
  aucune bibliothèque JS nécessaire), puis un écran de cadrage tactile
  (glisser/pincer) avant envoi ; la photo est associée par NOM au joueur
  dans le répertoire (player_photos.py — indépendant des données du
  tournoi, donc réutilisable pour ce joueur dans n'importe quel futur
  tournoi) ;
- un "Lobby" (bouton en haut de la page, visible seulement si plus
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
import version

DEFAULT_PORT = 8765

# Les 3 actions possibles, identiques à celles des raccourcis clavier
# (voir App._bind_voice_command_shortcuts) — un mot en dehors de cette
# liste est refusé.
_VALID_ACTIONS = {
    "elimination", "chronometre", "terminer",
    "tables", "mouvements", "toggle_pause", "niveau_suivant",
    "tables_zoom_moins", "tables_zoom_plus",
}

# Petit bouton 🔄 en haut à droite de chaque page (voir #btn-reload dans
# chacun des templates ci-dessous) : recharge la page avec un paramètre
# d'URL différent à chaque fois pour forcer le téléphone à re-télécharger
# le HTML/JS depuis le PC plutôt que de servir une version mise en cache
# — pratique en plein test, juste après avoir relancé l'appli avec du
# code changé côté serveur, sans avoir à fermer/rouvrir Safari.
_RELOAD_SCRIPT = (
    "function reloadApp() {\n"
    "  window.location.href = window.location.pathname + '?_r=' + Date.now();\n"
    "}"
)

_PAGE_TEMPLATE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Contrôle à distance</title>
<style>
  body {{
    margin: 0; padding: 14px 16px 20px;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }}
  h1 {{ font-size: 17px; color: #e8c468; margin: 0 0 2px; }}
  .tournoi {{ color: #b9ad8f; font-size: 13px; margin: 0 0 2px; }}
  .version {{ color: #6f6656; font-size: 10px; margin: 0 0 10px; }}
  /* Boutons volontairement compacts (pas les gros boutons d'origine,
     pensés pour 3-4 actions) : avec 8 boutons ou plus sur la page,
     l'objectif est que tout tienne sur un écran d'iPhone sans défiler. */
  button {{
    display: block; width: 100%; max-width: 420px; margin: 0 auto 8px;
    padding: 12px 10px; font-size: 17px; font-weight: 700;
    border: none; border-radius: 10px; color: #fff;
    -webkit-tap-highlight-color: transparent;
  }}
  button:active {{ transform: scale(0.97); }}
  #btn-lobby {{ background: #5a3a8c; }}
  #btn-elimination {{ background: #b5442e; }}
  #btn-chronometre {{ background: #1f6b3a; }}
  #btn-terminer {{ background: #8a6d1f; }}
  #btn-eliminations {{ background: #2c4a6e; }}
  #btn-tables {{ background: #1f6b6b; }}
  #btn-mouvements {{ background: #8a4a1f; }}
  #btn-niveau-suivant {{ background: #2c6e8a; }}
  #btn-photos {{ background: #6e2c6e; }}
  /* Ligne Chronomètre + petit bouton ON/OFF de pause à sa droite : les
     deux se partagent la largeur habituelle des boutons plutôt que
     chacun sa propre ligne pleine largeur. */
  .chrono-row {{
    display: flex; align-items: stretch; gap: 8px;
    max-width: 420px; margin: 0 auto 8px;
  }}
  .chrono-row button {{ margin: 0; max-width: none; }}
  #btn-chronometre {{ flex: 1; }}
  #btn-pause-toggle {{
    flex: 0 0 64px; background: #4a4a4a; font-size: 14px; padding: 0;
  }}
  /* Petits boutons Z-/Z+ de part et d'autre de "Plan des tables" —
     même principe que .chrono-row ci-dessus. */
  .zoom-row {{
    display: flex; align-items: stretch; gap: 8px;
    max-width: 420px; margin: 0 auto 8px;
  }}
  .zoom-row button {{ margin: 0; max-width: none; }}
  #btn-tables {{ flex: 1; }}
  #btn-tables-zoom-moins, #btn-tables-zoom-plus {{
    flex: 0 0 52px; background: #4a4a4a; font-size: 14px; padding: 0;
  }}
  #status {{
    max-width: 420px; margin: 20px auto 0; min-height: 22px;
    color: #b9ad8f; font-size: 15px;
  }}
  #btn-reload {{
    position: fixed; top: 14px; right: 14px; width: 40px; height: 40px;
    max-width: 40px; margin: 0; padding: 0; border-radius: 50%;
    background: #1c3d2c; font-size: 18px; line-height: 40px;
    box-shadow: 0 2px 6px rgba(0,0,0,.4);
  }}
</style>
</head>
<body>
  <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  <h1>🎙 Contrôle à distance</h1>
  <p class="tournoi">{tournament_name}</p>
  <p class="version">v{app_version}</p>

  {lobby_button}
  <button id="btn-eliminations" onclick="window.location.href='/eliminate'">🎯 Gérer les éliminations</button>
  <div class="zoom-row">
    <button id="btn-tables-zoom-moins" onclick="sendAction('tables_zoom_moins', this)" title="Rétrécir l'écran Tables sur le PC">Z−</button>
    <button id="btn-tables" onclick="sendAction('tables', this)">🗺 Plan des tables</button>
    <button id="btn-tables-zoom-plus" onclick="sendAction('tables_zoom_plus', this)" title="Agrandir l'écran Tables sur le PC">Z+</button>
  </div>
  <button id="btn-mouvements" onclick="sendAction('mouvements', this)">📋 Afficher Mouvements</button>
  <button id="btn-terminer" onclick="sendAction('terminer', this)">✅ Mouvements terminés</button>
  <div class="chrono-row">
    <button id="btn-chronometre" onclick="sendAction('chronometre', this)">▶ Chronomètre</button>
    <button id="btn-pause-toggle" onclick="togglePause()" title="Met en pause / relance le chrono">OFF</button>
  </div>
  <button id="btn-niveau-suivant" onclick="sendAction('niveau_suivant', this)">⏭ Niveau Suivant</button>
  <button id="btn-elimination" onclick="sendAction('elimination', this)">⏸ Joueurs</button>
  <button id="btn-photos" onclick="window.location.href='/photos'">📷 Photos des joueurs</button>

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

// Petit bouton ON/OFF à côté de "Chronomètre" : reflète et bascule
// directement l'état pause/lecture du chrono, sans passer par l'onglet
// Joueurs ni l'écran projecteur. "OFF" = chrono en marche (l'appui va le
// mettre en pause, d'où "ON" ensuite = pause activée) ; "ON" = chrono en
// pause (l'appui va le relancer, retour à "OFF").
function refreshClockState() {{
  fetch('/clock_state').then(function(r) {{ return r.json(); }}).then(function(data) {{
    document.getElementById('btn-pause-toggle').textContent = data.paused ? 'ON' : 'OFF';
  }}).catch(function() {{ /* réseau momentanément indisponible : le prochain sondage rattrapera */ }});
}}
function togglePause() {{
  var status = document.getElementById('status');
  status.textContent = 'Envoi...';
  fetch('/action/toggle_pause', {{ method: 'POST' }})
    .then(function(r) {{
      if (!r.ok) throw new Error('erreur ' + r.status);
      status.textContent = '';
      refreshClockState();
    }})
    .catch(function(e) {{
      status.textContent = 'Échec (' + e.message + ') — vérifiez le wifi.';
    }});
}}
refreshClockState();
setInterval(refreshClockState, 3000);
{reload_script}
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
  #btn-reload {{
    position: fixed; top: 14px; right: 14px; width: 40px; height: 40px;
    max-width: 40px; margin: 0; padding: 0; border-radius: 50%;
    background: #1c3d2c; font-size: 18px; line-height: 40px;
    box-shadow: 0 2px 6px rgba(0,0,0,.4);
  }}
</style>
</head>
<body>
  <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  <a class="back" href="/">← Retour</a>
  <h1>🏛 Choisir un tournoi</h1>
  {rows}
<script>
{reload_script}
</script>
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
  #btn-reload {{
    width: 32px; height: 32px; border: none; border-radius: 50%;
    background: #1c3d2c; color: #f5efe0; font-size: 15px; line-height: 32px;
    padding: 0; -webkit-tap-highlight-color: transparent;
  }}
  #columns {{ display: flex; height: calc(100% - 46px); min-height: 0; }}
  .col {{ flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }}
  .col-left {{ border-right: 2px solid #294235; }}
  .col h2 {{
    margin: 0; padding: 10px; font-size: 15px; text-align: center;
    background: #0b1c15; color: #e8c468; position: sticky; top: 0;
  }}
  /* min-height: 0 est essentiel ici : par défaut, un enfant flexible ne
     peut pas se réduire en dessous de la taille de son propre contenu
     (min-height: auto implicite), donc cette liste s'étirait pour
     contenir TOUS les joueurs au lieu de rester dans l'espace visible
     et défiler en interne — le débordement était alors coupé net par
     "overflow: hidden" sur <body>, sans le moindre ascenseur, rendant
     les derniers joueurs d'une table bien remplie inaccessibles. */
  .col .list {{ flex: 1; min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; padding: 8px; }}
  .player-item {{
    padding: 14px 8px; margin-bottom: 8px; border-radius: 10px;
    background: #1c3d2c; font-size: 15px; text-align: center;
    -webkit-user-select: none; -webkit-touch-callout: none;
    user-select: none; touch-action: pan-y; line-height: 1.3;
  }}
  .player-item .sub {{ display: block; font-size: 12px; color: #9fb8a8; margin-top: 2px; }}
  /* Retour visuel immédiat dès que le doigt se pose, avant même de
     savoir si ça va devenir un glissement ou un simple défilement (voir
     onTouchStart) — sans ça, rien ne montrait qu'un joueur était "pris"
     tant que le glissement n'était pas déjà engagé. Disparaît si le
     doigt est relâché sans glisser (voir cancelPending), ou remplacé
     par .dragging dès que le glissement est réellement engagé. */
  .player-item.pressed {{ background: #35624a; box-shadow: 0 0 0 2px #e8c468 inset; }}
  .player-item.dragging {{ opacity: 0.35; }}
  .player-item.drop-hover {{ background: #e8c468; color: #10241a; }}
  .player-item.drop-hover .sub {{ color: #4a3c10; }}
  /* Pendant un glissement, les joueurs d'une autre table que l'éliminé
     (au poker, on n'élimine que quelqu'un de sa propre table) — ainsi
     que l'éliminé lui-même, qui ne peut pas être son propre éliminateur
     — ne peuvent pas être ciblés. Complètement masqués (pas juste
     grisés) : ça libère de la place dans la colonne de droite pour les
     candidats valides, plutôt que de la gâcher avec des lignes qu'on ne
     peut de toute façon pas choisir. */
  .player-item.not-eligible {{ display: none; }}
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
    <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
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
function applyTableFilter(tableName, excludeId) {{
  // Masque les joueurs non éligibles (autre table, ou l'éliminé
  // lui-même — voir CSS .not-eligible) ET remonte les éligibles en tête
  // de liste : sur une table avec beaucoup de joueurs, un candidat
  // éligible pouvait se retrouver hors écran sans aucun moyen de
  // défiler jusqu'à lui — on ne peut pas glisser ET faire défiler avec
  // le même doigt en même temps. Masquer les non-éligibles (plutôt que
  // les griser en place) libère en plus de la place pour les candidats
  // valides, ce qui suffit à tous les afficher sans défiler dans le cas
  // courant (une table a rarement plus de 9-10 sièges).
  var right = document.getElementById('list-right');
  var children = Array.prototype.slice.call(right.children);
  var eligible = [], ineligible = [];
  children.forEach(function(item) {{
    if (item.dataset.table === tableName && item.dataset.id !== excludeId) {{
      eligible.push(item);
    }} else {{
      item.classList.add('not-eligible');
      ineligible.push(item);
    }}
  }});
  eligible.concat(ineligible).forEach(function(item) {{
    right.appendChild(item);
  }});
  right.scrollTop = 0;
}}
function clearTableFilter() {{
  // Reconstruit entièrement les deux colonnes (ordre alphabétique
  // normal) plutôt que de juste retirer la classe "not-eligible" :
  // remet aussi la colonne de droite dans son ordre habituel après le
  // remaniement temporaire fait par applyTableFilter ci-dessus.
  renderLists();
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
  // Retour visuel tout de suite, avant même de savoir si ça deviendra
  // un glissement ou un simple défilement (voir CSS .player-item.pressed).
  el.classList.add('pressed');
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
    pending.el.classList.remove('pressed');
    pending.el.classList.add('dragging');
    applyTableFilter(pending.table, pending.id);
    var ghost = document.getElementById('ghost');
    ghost.textContent = pending.label;
    ghost.style.display = 'block';
  }}

  e.preventDefault();
  moveGhost(t.clientX, t.clientY);
  autoScrollNearEdge(t.clientY);
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

// Un seul doigt tient le glissement : impossible de faire défiler la
// colonne de droite EN MEME TEMPS pour atteindre un candidat éligible
// resté hors écran (table bien remplie, 9-10 joueurs). On fait donc
// défiler automatiquement la colonne de droite dès que le doigt
// s'approche de son bord haut ou bas pendant le glissement — le même
// principe que le réordonnancement/repli des non-éligibles
// (applyTableFilter) : maximiser les chances de ne jamais avoir besoin
// de lâcher le glissement pour voir un candidat plus bas (ou plus haut).
var EDGE_SCROLL_ZONE = 50;   // px depuis le bord haut/bas de la colonne
var EDGE_SCROLL_STEP = 14;   // px de défilement à chaque évènement tactile proche du bord
function autoScrollNearEdge(clientY) {{
  var right = document.getElementById('list-right');
  var rect = right.getBoundingClientRect();
  if (clientY < rect.top + EDGE_SCROLL_ZONE) {{
    right.scrollTop -= EDGE_SCROLL_STEP;
  }} else if (clientY > rect.bottom - EDGE_SCROLL_ZONE) {{
    right.scrollTop += EDGE_SCROLL_STEP;
  }}
}}

function cancelPending() {{
  document.removeEventListener('touchmove', onTouchMove);
  document.removeEventListener('touchend', onTouchEnd);
  document.removeEventListener('touchcancel', onTouchEnd);
  if (pending && pending.el) {{
    pending.el.classList.remove('dragging');
    pending.el.classList.remove('pressed');
  }}
  if (hoverTarget) {{ hoverTarget.classList.remove('drop-hover'); hoverTarget = null; }}
  // Seulement si un glissement avait vraiment été engagé (donc si
  // applyTableFilter avait été appelé) : évite de reconstruire toute la
  // liste à chaque simple défilement annulé (le cas le plus fréquent).
  if (pending && pending.engaged) {{
    clearTableFilter();
  }}
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
{reload_script}
</script>
</body>
</html>
"""

# Page "Photos" : liste TOUT le répertoire de joueurs habituels via
# /roster_players (PAS /players, réservé aux joueurs actifs du tournoi
# en cours pour la page Éliminations — un joueur du club peut vouloir
# être pris en photo avant même d'être inscrit ce soir-là), un bouton 📷
# par joueur qui ouvre directement l'appareil photo du téléphone (input
# file avec capture="environment", un classique HTML pour ça, aucune
# bibliothèque JS nécessaire), suivi d'un écran de cadrage tactile
# (glisser/pincer) avant envoi. La photo est associée par NOM (pas par
# id — le répertoire n'en a pas) à ce joueur (voir player_photos.py,
# complètement indépendant des données du tournoi — une photo prise ici
# reste utilisable pour ce joueur dans n'importe quel futur tournoi).
_PHOTOS_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Photos</title>
<style>
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  body {{
    margin: 0; padding: 0 0 24px;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  }}
  #topbar {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #0b1c15; border-bottom: 1px solid #294235;
    position: sticky; top: 0; z-index: 10;
  }}
  #topbar a {{ color: #b9ad8f; text-decoration: none; font-size: 15px; }}
  #topbar .tournoi {{ color: #e8c468; font-size: 15px; font-weight: 700; }}
  #btn-reload {{
    width: 32px; height: 32px; border: none; border-radius: 50%;
    background: #1c3d2c; color: #f5efe0; font-size: 15px; line-height: 32px;
    padding: 0; -webkit-tap-highlight-color: transparent;
  }}
  #list {{ padding: 10px 14px; }}
  .player-row {{
    display: flex; align-items: center; gap: 10px;
    padding: 10px; margin-bottom: 8px; border-radius: 10px;
    background: #1c3d2c;
  }}
  .player-row .info {{ flex: 1; min-width: 0; text-align: left; }}
  .player-row .info .name {{ font-size: 16px; font-weight: 700; }}
  .player-row .info .sub {{ font-size: 12px; color: #9fb8a8; margin-top: 2px; }}
  .player-row .has-photo {{ color: #8fd694; }}
  .player-row button {{
    flex: none; border: none; border-radius: 10px; background: #2c4a6e;
    color: #fff; font-size: 22px; padding: 10px 14px;
    -webkit-tap-highlight-color: transparent;
  }}
  .player-row button:active {{ transform: scale(0.95); }}
  #empty {{ text-align: center; color: #b9ad8f; padding: 40px 16px; font-size: 15px; }}
  #status {{
    max-width: 420px; margin: 14px auto 0; min-height: 22px;
    color: #b9ad8f; font-size: 15px; text-align: center; padding: 0 16px;
  }}
  /* Écran de cadrage après la prise de vue : le nom du joueur est
     impossible à incruster sur la vue caméra NATIVE de l'iPhone
     elle-même (elle appartient à iOS, pas à cette page — une vraie
     caméra "maison" en direct demanderait du HTTPS, hors de propos
     pour un petit serveur local) ; il est donc affiché ici, bien en
     évidence, juste après la prise de vue et avant l'envoi — avec un
     recadrage tactile (glisser pour repositionner, pincer pour
     zoomer/dézoomer) pour corriger le cadrage avant confirmation. */
  #crop-view {{
    display: none; position: fixed; inset: 0; z-index: 200;
    background: #0b1c15; flex-direction: column;
    align-items: center; justify-content: center; padding: 20px 16px;
  }}
  #crop-name {{ color: #e8c468; font-size: 20px; font-weight: 700; margin-bottom: 14px; text-align: center; }}
  #crop-frame {{
    position: relative; overflow: hidden; background: #000;
    width: min(78vw, 320px); aspect-ratio: 3 / 4;
    border-radius: 12px; touch-action: none;
    box-shadow: 0 0 0 2px #294235;
  }}
  #crop-image {{ position: absolute; left: 0; top: 0; will-change: left, top, width, height; }}
  #crop-hint {{ color: #9fb8a8; font-size: 12px; margin-top: 10px; text-align: center; }}
  #crop-controls {{ margin-top: 18px; display: flex; gap: 12px; }}
  #crop-controls button {{
    padding: 12px 22px; border: none; border-radius: 10px;
    font-size: 16px; font-weight: 700; color: #fff;
    -webkit-tap-highlight-color: transparent;
  }}
  #btn-retake {{ background: #6e2c2c; }}
  #btn-confirm-send {{ background: #1f6b3a; }}
</style>
</head>
<body>
  <div id="topbar">
    <a href="/">← Retour</a>
    <span class="tournoi">{tournament_name}</span>
    <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  </div>
  <div id="list"></div>
  <p id="status"></p>
  <input type="file" accept="image/*" capture="environment" id="camera-input" style="display:none">

  <div id="crop-view">
    <div id="crop-name"></div>
    <div id="crop-frame"><img id="crop-image"></div>
    <p id="crop-hint">Glisser pour déplacer · pincer pour zoomer</p>
    <div id="crop-controls">
      <button id="btn-retake" type="button">↺ Reprendre</button>
      <button id="btn-confirm-send" type="button">✓ Envoyer</button>
    </div>
  </div>

<script>
var players = [];
var pendingPlayer = null;

function loadPlayers() {{
  // /roster_players (pas /players, réservé à la page Éliminations) :
  // TOUT le répertoire de joueurs habituels, pas seulement ceux inscrits
  // au tournoi en cours — on peut vouloir prendre en photo un joueur du
  // club avant même qu'il ne soit assis à une table ce soir-là.
  fetch('/roster_players').then(function(r) {{ return r.json(); }}).then(function(data) {{
    players = data;
    renderList();
  }}).catch(function() {{ /* réseau momentanément indisponible : on retentera */ }});
}}

function renderList() {{
  var list = document.getElementById('list');
  list.innerHTML = '';
  if (players.length === 0) {{
    list.innerHTML = '<div id="empty">Répertoire vide</div>';
    return;
  }}
  var sorted = players.slice().sort(function(a, b) {{
    return a.name.localeCompare(b.name, 'fr', {{sensitivity: 'base'}});
  }});
  sorted.forEach(function(p) {{
    var row = document.createElement('div');
    row.className = 'player-row';
    row.innerHTML =
      '<div class="info">' +
        '<div class="name">' + p.name + (p.has_photo ? ' <span class="has-photo">✓</span>' : '') + '</div>' +
        (p.club ? '<div class="sub">' + p.club + '</div>' : '') +
      '</div>' +
      '<button type="button">📷</button>';
    row.querySelector('button').addEventListener('click', function() {{
      pendingPlayer = p;
      // Rien à afficher DURANT la prise de vue elle-même (vue caméra
      // native, hors de portée de cette page) — mais on confirme ici,
      // juste avant qu'elle ne s'ouvre, pour qui la photo est prise.
      document.getElementById('status').textContent = '📷 Photo pour ' + p.name + '...';
      document.getElementById('camera-input').click();
    }});
    list.appendChild(row);
  }});
}}

// --- Cadrage tactile (glisser + pincer) après la prise de vue ---------
var natW = 0, natH = 0;       // taille naturelle de la photo prise
var baseDispW = 0, baseDispH = 0;  // taille affichée à zoom 1 (couvre le cadre, comme object-fit: cover)
var zoomFactor = 1;
var imgLeft = 0, imgTop = 0;  // position (px) du coin haut-gauche de l'image, relative au cadre
var touchState = null;

document.getElementById('camera-input').addEventListener('change', function(e) {{
  var file = e.target.files && e.target.files[0];
  e.target.value = '';  // permet de reprendre une photo pour le même joueur ensuite
  document.getElementById('status').textContent = '';
  if (!file || !pendingPlayer) return;
  var reader = new FileReader();
  reader.onload = function() {{ openCropView(reader.result); }};
  reader.readAsDataURL(file);
}});

function openCropView(dataUrl) {{
  document.getElementById('crop-name').textContent = pendingPlayer.name;
  var img = document.getElementById('crop-image');
  img.onload = function() {{
    natW = img.naturalWidth;
    natH = img.naturalHeight;
    var frame = document.getElementById('crop-frame');
    var frameW = frame.clientWidth, frameH = frame.clientHeight;
    // "object-fit: cover" calculé à la main (pas en CSS) : on a besoin
    // de connaître la taille affichée pour convertir ensuite la zone
    // visible en coordonnées de l'image d'origine au moment d'envoyer.
    var coverScale = Math.max(frameW / natW, frameH / natH);
    baseDispW = natW * coverScale;
    baseDispH = natH * coverScale;
    zoomFactor = 1;
    imgLeft = (frameW - baseDispW) / 2;
    imgTop = (frameH - baseDispH) / 2;
    applyImgTransform();
  }};
  img.src = dataUrl;
  document.getElementById('crop-view').style.display = 'flex';
}}

function closeCropView() {{
  document.getElementById('crop-view').style.display = 'none';
  pendingPlayer = null;
}}

function applyImgTransform() {{
  var img = document.getElementById('crop-image');
  img.style.width = (baseDispW * zoomFactor) + 'px';
  img.style.height = (baseDispH * zoomFactor) + 'px';
  img.style.left = imgLeft + 'px';
  img.style.top = imgTop + 'px';
}}

// Empêche l'image de laisser un vide dans le cadre, quel que soit le
// déplacement/zoom en cours.
function clampImgPosition() {{
  var frame = document.getElementById('crop-frame');
  var frameW = frame.clientWidth, frameH = frame.clientHeight;
  var dispW = baseDispW * zoomFactor, dispH = baseDispH * zoomFactor;
  imgLeft = Math.min(0, Math.max(frameW - dispW, imgLeft));
  imgTop = Math.min(0, Math.max(frameH - dispH, imgTop));
}}

document.getElementById('crop-frame').addEventListener('touchstart', function(e) {{
  e.preventDefault();
  if (e.touches.length === 1) {{
    touchState = {{
      mode: 'pan',
      startX: e.touches[0].clientX, startY: e.touches[0].clientY,
      left0: imgLeft, top0: imgTop,
    }};
  }} else if (e.touches.length === 2) {{
    var dx = e.touches[0].clientX - e.touches[1].clientX;
    var dy = e.touches[0].clientY - e.touches[1].clientY;
    var frame = document.getElementById('crop-frame');
    var rect = frame.getBoundingClientRect();
    touchState = {{
      mode: 'pinch',
      startDist: Math.hypot(dx, dy),
      zoom0: zoomFactor,
      left0: imgLeft, top0: imgTop,
      // Point médian des deux doigts, relatif au cadre : on zoome en
      // gardant CE point fixe à l'écran, comme un vrai pincer-zoomer.
      focalX: (e.touches[0].clientX + e.touches[1].clientX) / 2 - rect.left,
      focalY: (e.touches[0].clientY + e.touches[1].clientY) / 2 - rect.top,
    }};
  }}
}}, {{passive: false}});

document.getElementById('crop-frame').addEventListener('touchmove', function(e) {{
  e.preventDefault();
  if (!touchState) return;
  if (touchState.mode === 'pan' && e.touches.length === 1) {{
    imgLeft = touchState.left0 + (e.touches[0].clientX - touchState.startX);
    imgTop = touchState.top0 + (e.touches[0].clientY - touchState.startY);
    clampImgPosition();
    applyImgTransform();
  }} else if (touchState.mode === 'pinch' && e.touches.length === 2) {{
    var dx = e.touches[0].clientX - e.touches[1].clientX;
    var dy = e.touches[0].clientY - e.touches[1].clientY;
    var dist = Math.hypot(dx, dy);
    var factor = dist / touchState.startDist;
    zoomFactor = Math.max(1, Math.min(4, touchState.zoom0 * factor));
    var appliedFactor = zoomFactor / touchState.zoom0;
    imgLeft = touchState.focalX - (touchState.focalX - touchState.left0) * appliedFactor;
    imgTop = touchState.focalY - (touchState.focalY - touchState.top0) * appliedFactor;
    clampImgPosition();
    applyImgTransform();
  }}
}}, {{passive: false}});

document.getElementById('crop-frame').addEventListener('touchend', function() {{
  touchState = null;
}});

document.getElementById('btn-retake').addEventListener('click', function() {{
  var player = pendingPlayer;
  closeCropView();
  if (player) {{
    pendingPlayer = player;
    document.getElementById('status').textContent = '📷 Photo pour ' + player.name + '...';
    document.getElementById('camera-input').click();
  }}
}});

document.getElementById('btn-confirm-send').addEventListener('click', function() {{
  var player = pendingPlayer;
  var frame = document.getElementById('crop-frame');
  var frameW = frame.clientWidth, frameH = frame.clientHeight;
  var dispW = baseDispW * zoomFactor, dispH = baseDispH * zoomFactor;
  var scaleToNatural = natW / dispW;  // uniforme (rapport conservé)
  var cropX = (0 - imgLeft) * scaleToNatural;
  var cropY = (0 - imgTop) * scaleToNatural;
  var cropW = frameW * scaleToNatural;
  var cropH = frameH * scaleToNatural;

  var outW = 480, outH = Math.round(outW * frameH / frameW);
  var canvas = document.createElement('canvas');
  canvas.width = outW;
  canvas.height = outH;
  canvas.getContext('2d').drawImage(
    document.getElementById('crop-image'),
    cropX, cropY, cropW, cropH, 0, 0, outW, outH
  );
  closeCropView();

  var status = document.getElementById('status');
  status.textContent = 'Envoi de la photo...';
  // toDataURL (synchrone) plutôt que toBlob (asynchrone) : sur certains
  // Safari/iOS, toBlob peut ne JAMAIS rappeler sa fonction (encodage en
  // tâche de fond qui échoue silencieusement, notamment sous pression
  // mémoire) — l'écran resterait alors bloqué sur "Envoi de la photo..."
  // sans erreur ni recours. toDataURL est fiable de longue date sur iOS.
  var dataUrl = canvas.toDataURL('image/jpeg', 0.9);
  var blob = dataUrlToBlob(dataUrl);

  fetch('/upload_photo?player_name=' + encodeURIComponent(player.name), {{
    method: 'POST',
    headers: {{'Content-Type': 'image/jpeg'}},
    body: blob,
  }}).then(function(r) {{
    return r.json().then(function(data) {{ return {{ok: r.ok, data: data}}; }});
  }}).then(function(result) {{
    if (!result.ok || !result.data.ok) {{
      throw new Error(result.data.message || 'erreur');
    }}
    status.textContent = 'Photo enregistrée pour ' + result.data.message + '.';
    setTimeout(function() {{ status.textContent = ''; }}, 3000);
    loadPlayers();
  }}).catch(function(e) {{
    status.textContent = 'Échec (' + e.message + ') — vérifiez le wifi.';
  }});
}});

function dataUrlToBlob(dataUrl) {{
  var parts = dataUrl.split(',');
  var mime = parts[0].match(/:(.*?);/)[1];
  var binary = atob(parts[1]);
  var bytes = new Uint8Array(binary.length);
  for (var i = 0; i < binary.length; i++) {{
    bytes[i] = binary.charCodeAt(i);
  }}
  return new Blob([bytes], {{type: mime}});
}}

loadPlayers();
setInterval(loadPlayers, 4000);
{reload_script}
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
    utilisée pour joindre une adresse externe, sans nécessiter internet.

    Repli si ça échoue (OSError — ex : Wifi local sans accès internet du
    tout, comme un routeur de voyage sans connexion WAN : la tentative de
    route vers 8.8.8.8 peut alors échouer même si le réseau local
    lui-même fonctionne très bien entre le PC et le téléphone) : énumère
    les adresses IPv4 connues de cette machine via son propre nom d'hôte,
    et prend la première qui n'est ni loopback (127.x) ni lien-local sans
    DHCP (169.254.x) — un cas réel rencontré en v1.2.29, où la case
    Paramètres affichait 127.0.0.1 (inutilisable depuis le téléphone,
    qui désigne alors LUI-MÊME, pas le PC) alors que le Wifi local
    fonctionnait. Ne renvoie 127.0.0.1 qu'en tout dernier recours, si
    vraiment aucune adresse réseau n'a pu être trouvée."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        pass
    finally:
        s.close()
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = sockaddr[0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                return ip
    except OSError:
        pass
    return "127.0.0.1"


class RemoteControlServer:
    """Petit serveur HTTP embarqué (bibliothèque standard uniquement),
    tourne dans un thread dédié (un thread par requête, voir
    ThreadingHTTPServer). Chaque callback ci-dessous est donc appelé
    depuis CE thread, jamais celui de l'interface Tkinter — à l'appelant
    de ne jamais lire/écrire directement une ressource liée à ce dernier
    thread (ex : la connexion SQLite) depuis ces callbacks, et de repasser
    par un mécanisme thread-safe à la place (voir App._poll_voice_queue /
    _remote_players_cache dans main.py, qui font déjà ce travail).

    - `on_word(action)` : une des actions simples ("elimination" /
      "chronometre" / "terminer" / "tables" / "mouvements" /
      "toggle_pause" / "niveau_suivant" / "tables_zoom_moins" /
      "tables_zoom_plus").
    - `get_players()` : renvoie la liste des joueurs actifs à afficher sur
      la page Éliminations — liste de dicts {id, name, table, seat,
      has_photo}.
    - `get_roster_players()` : renvoie TOUT le répertoire de joueurs
      habituels (pas seulement ceux actifs dans le tournoi en cours), pour
      la page Photos — liste de dicts {name, club, has_photo}, sans id
      numérique (voir on_upload_photo ci-dessous).
    - `on_eliminate(eliminated_id, eliminator_id)` : élimination décidée
      depuis la page Éliminations (eliminator_id peut être None).
    - `get_clock_paused()` : True si le chrono est actuellement en pause
      — pour le petit bouton ON/OFF à côté de "Chronomètre".
    - `on_upload_photo(player_name, image_bytes)` : photo prise depuis la
      page Photos, à associer à ce joueur (identifié par NOM, pas par id
      — voir get_roster_players) dans le répertoire — renvoie
      (succès: bool, message: str) ; ce callback-ci, à la différence des
      autres, peut être appelé du thread du serveur SANS passer par une
      file d'attente thread-safe, s'il ne touche ni self.db (SQLite) ni
      Tkinter (voir App._remote_upload_photo, qui ne touche que de
      simples fichiers/JSON via player_photos.py)."""

    def __init__(self, on_word, get_tournament_name=None, get_players=None,
                 on_eliminate=None, get_clock_paused=None, on_upload_photo=None,
                 get_roster_players=None, port=DEFAULT_PORT):
        self.on_word = on_word
        self.get_tournament_name = get_tournament_name or (lambda: "Tournoi")
        self.get_players = get_players or (lambda: [])
        self.get_roster_players = get_roster_players or (lambda: [])
        self.on_eliminate = on_eliminate or (lambda eliminated_id, eliminator_id: None)
        self.get_clock_paused = get_clock_paused or (lambda: True)
        self.on_upload_photo = on_upload_photo or (lambda player_name, image_bytes: (False, "Non disponible"))
        self.port = port
        self._httpd = None
        self._thread = None

    def start(self):
        if self.is_running:
            return
        on_word = self.on_word
        get_name = self.get_tournament_name
        get_players = self.get_players
        get_roster_players = self.get_roster_players
        on_eliminate = self.on_eliminate
        get_clock_paused = self.get_clock_paused
        on_upload_photo = self.on_upload_photo
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
                self._send_html(_LOBBY_PAGE.format(rows=rows, reload_script=_RELOAD_SCRIPT))

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
                # self.path inclut la chaîne de requête ("?...") le cas
                # échéant (ex : "/?_r=173..." posé par le bouton 🔄 de
                # rechargement pour forcer le téléphone à ignorer son
                # cache — voir _RELOAD_SCRIPT) : la comparer telle quelle
                # à "/" échouait toujours (404), d'où un routage sur le
                # chemin SEUL, sans sa chaîne de requête, pour toutes les
                # pages ci-dessous (_proxy/_handle_select_tournament, qui
                # ont besoin de la chaîne de requête d'origine, continuent
                # eux d'utiliser self.path tel quel).
                path = self.path.split("?", 1)[0]

                # Toujours traitées ICI, jamais relayées vers un autre
                # tournoi : ce sont les pages qui permettent justement de
                # choisir/changer de tournoi.
                if path == "/lobbylist":
                    self._handle_lobbylist()
                    return
                if path == "/select_tournament":
                    self._handle_select_tournament()
                    return

                target_port = resolve_proxy_port(self)
                if target_port is not None:
                    self._proxy(target_port)
                    return

                if path in ("/", "/index.html"):
                    tournaments = open_windows.list_remote_tournaments()
                    lobby_button = (
                        '<button id="btn-lobby" onclick="window.location.href=\'/lobbylist\'">🏛 Lobby</button>'
                        if len(tournaments) > 1 else ""
                    )
                    self._send_html(_PAGE_TEMPLATE.format(
                        tournament_name=_escape_html(get_name()),
                        lobby_button=lobby_button,
                        app_version=version.APP_VERSION,
                        reload_script=_RELOAD_SCRIPT,
                    ))
                elif path in ("/eliminate", "/eliminate.html"):
                    self._send_html(_ELIMINATE_PAGE.format(
                        tournament_name=_escape_html(get_name()),
                        reload_script=_RELOAD_SCRIPT,
                    ))
                elif path in ("/photos", "/photos.html"):
                    self._send_html(_PHOTOS_PAGE.format(
                        tournament_name=_escape_html(get_name()),
                        reload_script=_RELOAD_SCRIPT,
                    ))
                elif path == "/players":
                    self._send_json(get_players())
                elif path == "/roster_players":
                    self._send_json(get_roster_players())
                elif path == "/clock_state":
                    self._send_json({"paused": bool(get_clock_paused())})
                else:
                    self.send_error(404)

            def do_POST(self):
                target_port = resolve_proxy_port(self)
                if target_port is not None:
                    self._proxy(target_port)
                    return

                path = self.path.split("?", 1)[0]
                if path.startswith("/action/"):
                    action = path[len("/action/"):]
                    if action not in _VALID_ACTIONS:
                        self.send_error(400, "Action inconnue")
                        return
                    on_word(action)
                    self._send_json({"ok": True})
                elif path == "/eliminate":
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
                elif path == "/upload_photo":
                    from urllib.parse import parse_qs, urlparse
                    query = parse_qs(urlparse(self.path).query)
                    # Identifié par NOM (pas par id) : la page Photos liste
                    # TOUT le répertoire de joueurs habituels (voir
                    # /roster_players), qui n'a pas d'id numérique comme les
                    # joueurs d'un tournoi en cours.
                    player_name_values = query.get("player_name")
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    # Limite large (20 Mo) mais réelle : une photo de
                    # téléphone moderne dépasse rarement quelques Mo, ça
                    # évite juste qu'une requête malformée ou abusive ne
                    # fasse lire un flux énorme en mémoire.
                    if not player_name_values or not player_name_values[0].strip() or length <= 0 or length > 20 * 1024 * 1024:
                        self.send_error(400, "Requête invalide")
                        return
                    player_name = player_name_values[0].strip()
                    image_bytes = self.rfile.read(length)
                    ok, message = on_upload_photo(player_name, image_bytes)
                    self._send_json({"ok": ok, "message": message})
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
