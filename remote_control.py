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
  réel même s'il a changé par un autre moyen, ex. au clavier) ; ce même
  sondage fait aussi clignoter "Mouvements" tant que l'onglet Mouvements
  du Mac contient au moins un déplacement en attente ;
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

Protégé par un code à 6 chiffres PUIS une approbation par appareil
(demande du 2026-09-09) : avant ce correctif, l'accès reposait
uniquement sur le fait d'être déjà sur le même réseau Wifi local.
Désormais, toute connexion doit d'abord passer par /login puis
/authenticate (voir Handler._handle_login/_handle_authenticate) et
indiquer le code de la session en cours (voir open_windows.
remote_session_code, affiché dans Paramètres à côté de la case "Activer
le contrôle à distance"), IDENTIQUE pour tous les tournois ouverts
pendant une même session de l'application. Un code de test permanent
(131261) est accepté EN PLUS du code de session pour les besoins de
test — jamais affiché dans l'interface, soumis à la même protection
anti-force-brute que le code réel (open_windows.record_remote_auth_
failure/_success), aucune route ni aucun contournement spécifique pour
lui.

Le code seul ne suffit PAS : un appareil qui ne s'est jamais connecté
doit en plus être explicitement APPROUVÉ depuis le PC/Mac (Paramètres,
voir main.py) avant d'accéder à la moindre commande — voir open_windows.
register_device_attempt/approve_remote_device/verify_device_session.
Un appareil déjà approuvé lors d'une session précédente retrouve un
accès immédiat en resaisissant le nouveau code de la session en cours,
sans réapprobation (voir open_windows.get_or_mint_device_session_
token) ; une révocation (bouton "Révoquer" dans Paramètres) invalide
l'accès de cet appareil IMMÉDIATEMENT, même en cours de session.

Une fois authentifié ET approuvé, le téléphone reste autorisé pour
toute la session (cookies "rc_bid"/"rc_auth", HttpOnly — jamais lus par
le JavaScript de la page) : il n'a PAS à ressaisir le code à chaque
page ni en changeant de tournoi depuis le Lobby, mais DOIT s'authentifier
à nouveau à la session suivante (code et jetons de session régénérés,
voir open_windows._ensure_remote_session_auth_locked).

Vérification faite CÔTÉ SERVEUR sur CHAQUE route sensible (voir
_AUTH_EXEMPT_PATHS/_AUTH_PAGE_PATHS et le tout début de do_GET/do_POST
ci-dessous) — pas seulement sur les pages HTML ou les boutons JS :
connaître directement l'URL d'une action (ex. /end_tournament) sans
authentification valide ne suffit pas à l'exécuter. Aucune route HTTP
ne permet à un téléphone de s'auto-approuver, se révoquer ou modifier
la liste des appareils : Autoriser/Refuser/Révoquer sont exclusivement
des actions de l'interface Tkinter locale (voir main.py).

Limite connue et assumée : ce serveur tourne en HTTP simple (pas de
TLS) sur le Wifi local — code, cookies et jetons y circulent donc EN
CLAIR sur le réseau. La protection mise en place vise un accès non
autorisé qui ne serait ni sur le Wifi club ni approuvé, un ancien
responsable dont le téléphone a été révoqué, et le brute-force du code
— pas une interception réseau active sur ce même Wifi (HTTPS pourrait
être traité séparément plus tard). Les actions déclenchées restent les
mêmes que celles déjà disponibles au clavier (Ctrl+Maj+J/C/T) ou dans
l'onglet Joueurs — rien de destructeur, rien qui touche aux données du
tournoi autrement que par une élimination normale.
"""
import json
import logging
import logging.handlers
import os
import re
import secrets
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import open_windows
import roster
import version

DEFAULT_PORT = 8765

# =====================================================================
# Journal DIAGNOSTIQUE du contrôle à distance (demande du 2026-09-19,
# "bétonner la communication téléphone <-> PC", suite à un incident réel
# en club où la page Joueurs/Éliminations avait cessé de communiquer
# sans qu'aucune trace n'existe nulle part pour le diagnostiquer après
# coup). Volontairement SOBRE : uniquement les événements ANORMAUX (401,
# 502/proxy injoignable, requête dupliquée ignorée) — jamais une ligne
# par requête réussie (voir Handler.log_message plus bas, délibérément
# silencieux depuis l'origine de ce module, pour rester bruyant à ce
# seul endroit et nulle part ailleurs).
#
# Emplacement (identique Mac ET Windows, même convention que crash.log/
# menu_principal_child.log dans main.py) :
#   ~/.poker_tournament/remote_control.log
# soit concrètement :
#   Mac     : /Users/<compte>/.poker_tournament/remote_control.log
#   Windows : %USERPROFILE%\.poker_tournament\remote_control.log
#
# Rotation automatique (RotatingFileHandler, bibliothèque standard) :
# 1 Mo par fichier, 2 fichiers de sauvegarde conservés au maximum
# (remote_control.log, .log.1, .log.2) — 3 Mo au total au pire, jamais
# une croissance illimitée sur des mois d'utilisation du club.
#
# JAMAIS journalisé ici : mot de passe, code d'accès à 6 chiffres,
# cookie (rc_bid/rc_auth/selected_pid), ou tout autre secret — les
# appelants ne passent en `fields` que method/path/status/port/pid/type
# d'erreur, jamais self.headers ni une valeur de cookie brute (voir
# chaque site d'appel ci-dessous).
_LOG_DIR = os.path.join(os.path.expanduser("~"), ".poker_tournament")
_LOG_PATH = os.path.join(_LOG_DIR, "remote_control.log")
_LOG_MAX_BYTES = 1_000_000
_LOG_BACKUP_COUNT = 2
_logger = None


def _get_remote_logger():
    global _logger
    if _logger is not None:
        return _logger
    os.makedirs(_LOG_DIR, exist_ok=True)
    logger = logging.getLogger("poker_tournament.remote_control")
    logger.setLevel(logging.INFO)
    for old_handler in list(logger.handlers):
        old_handler.close()
        logger.removeHandler(old_handler)
    handler = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=_LOG_MAX_BYTES, backupCount=_LOG_BACKUP_COUNT, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    _logger = logger
    return _logger


def log_remote_event(event, **fields):
    """Ajoute une ligne au journal diagnostique ci-dessus pour un
    événement ANORMAL du contrôle à distance — jamais pour une requête
    réussie. `fields` : uniquement des valeurs déjà sûres à écrire en
    clair (method, path, status, port, pid, type d'erreur...) — QUE
    l'appelant est responsable de choisir : cette fonction ne filtre
    rien elle-même, elle fait confiance à ses appelants (tous internes à
    ce fichier ou à main.py) pour ne jamais lui passer un cookie ou un
    code d'accès. N'échoue JAMAIS : un souci d'écriture du journal
    (disque plein, permissions...) ne doit jamais faire échouer la
    requête HTTP réelle qui a déclenché cet appel."""
    try:
        parts = " ".join(f"{k}={v}" for k, v in fields.items())
        _get_remote_logger().info("%s %s", event, parts)
    except Exception:
        pass

# Les 3 actions possibles, identiques à celles des raccourcis clavier
# (voir App._bind_voice_command_shortcuts) — un mot en dehors de cette
# liste est refusé.
_VALID_ACTIONS = {
    "elimination", "chronometre", "terminer",
    "tables", "mouvements", "toggle_pause", "niveau_precedent", "niveau_suivant",
    "tables_zoom_moins", "tables_zoom_plus",
    "mouvements_bas", "mouvements_haut",
}

# =====================================================================
# Permissions DIRTO (Phase 4, "Sécurisation du Contrôle à distance",
# 2026-09-20) — application RÉELLE des autorisations accordées par
# Phase 3 (voir database.py: REMOTE_PERMISSION_LABELS/remote_
# authorizations). Les 7 chaînes ci-dessous sont dupliquées ici en
# LITTÉRAUX plutôt qu'importées de database.py : remote_control.py reste
# volontairement sans dépendance vers ce module (voir la docstring de
# RemoteControlServer — chaque callback isole ce fichier de SQLite/
# Tkinter) ; elles forment un contrat stable, déjà gravé dans le JSON
# persisté par Database.set_dirto_authorization.
_PERM_ELIMINATIONS = "eliminations"
_PERM_TABLES = "tables"
_PERM_MOVES = "moves"
_PERM_CLOCK = "clock"
_PERM_LEVELS = "levels"
_PERM_PHOTOS = "photos"
_PERM_REBALANCE = "rebalance"

# Accès total (rôle ADMIN) : un objet distinct de "toutes les clés
# valides actuellement" n'aurait aucun sens ici — un ADMIN n'est jamais
# limité par cette liste, elle sert uniquement à satisfaire les mêmes
# tests d'appartenance ("permission in permissions") que pour un DIRTO,
# sans dupliquer la logique de vérification par rôle.
_ALL_PERMISSIONS = frozenset({
    _PERM_ELIMINATIONS, _PERM_TABLES, _PERM_MOVES, _PERM_CLOCK,
    _PERM_LEVELS, _PERM_PHOTOS, _PERM_REBALANCE,
})

# "elimination" (bouton "⏸ Joueurs" de la page principale, PAS la page
# "Gérer les éliminations") : bascule l'onglet Joueurs du Mac au premier
# plan et met le chrono en pause (voir App._voice_start_elimination) —
# corrigé le 2026-09-22 (anomalie constatée en test réel iPhone) : cette
# commande n'a JAMAIS fait partie de l'inventaire des 7 permissions
# établi en Phase 3 (voir database.py: REMOTE_PERMISSION_LABELS —
# "eliminations" n'y couvre QUE /eliminate et /players, jamais cette
# action) et reste totalement INDÉPENDANTE de GET /players / POST
# /eliminate (la page "Gérer les éliminations" du téléphone ne déclenche
# jamais /action/elimination, voir _ELIMINATE_PAGE). Comme "Terminer le
# tournoi", elle est donc réservée à l'ADMIN, structurellement absente de
# _ACTION_PERMISSION ci-dessous — vérifiée séparément par rôle (voir
# do_POST), jamais par appartenance à `permissions`.
_ADMIN_ONLY_ACTIONS = frozenset({"elimination"})

# Mapping EXACT mot-clé (/action/<mot>) -> permission requise. Couvre les
# 11 clés de _VALID_ACTIONS RESTANTES (12 moins "elimination" ci-dessus,
# voir App._on_voice_word dans main.py pour ce que fait réellement
# chaque mot — c'est cette implémentation, jamais son seul nom, qui fixe
# le regroupement) : "terminer"/"mouvements"/"mouvements_bas"/
# "mouvements_haut" agissent tous sur le bandeau/la file des mouvements ;
# "chronometre"/"toggle_pause" sur le chrono ; "tables"/"tables_zoom_
# moins"/"tables_zoom_plus" sur le Plan des tables ; "niveau_precedent"/
# "niveau_suivant" sur la structure de blindes.
_ACTION_PERMISSION = {
    "chronometre": _PERM_CLOCK,
    "terminer": _PERM_MOVES,
    "tables": _PERM_TABLES,
    "mouvements": _PERM_MOVES,
    "toggle_pause": _PERM_CLOCK,
    "niveau_precedent": _PERM_LEVELS,
    "niveau_suivant": _PERM_LEVELS,
    "tables_zoom_moins": _PERM_TABLES,
    "tables_zoom_plus": _PERM_TABLES,
    "mouvements_bas": _PERM_MOVES,
    "mouvements_haut": _PERM_MOVES,
}

# Mapping route GET -> permission requise (pages ET données JSON qu'elles
# consomment — voir l'inventaire de database.py: REMOTE_PERMISSION_LABELS,
# établi avant codage en Phase 3). "/", "/index.html", "/lobbylist",
# "/select_tournament", "/login", "/auth_status", "/authenticate" sont
# volontairement ABSENTES : traitées avant résolution du rôle (voir
# do_GET/do_POST) ou par un rendu propre au rôle (page d'accueil).
_GET_ROUTE_PERMISSION = {
    "/eliminate": _PERM_ELIMINATIONS, "/eliminate.html": _PERM_ELIMINATIONS,
    "/players": _PERM_ELIMINATIONS,
    "/photos": _PERM_PHOTOS, "/photos.html": _PERM_PHOTOS,
    "/roster_players": _PERM_PHOTOS, "/photo_image": _PERM_PHOTOS,
    "/moves": _PERM_MOVES, "/moves.html": _PERM_MOVES,
    "/moves_pending": _PERM_MOVES,
    "/rebalance_pending": _PERM_REBALANCE,
}

# "/clock_state" : cas particulier volontairement absent de _GET_ROUTE_
# PERMISSION — alimente à la fois l'indicateur pause/lecture (clock) ET
# le clignotement "Afficher Mouvements" (moves) sur TOUTES les pages ;
# accessible dès que l'une des deux est accordée, jamais une 8e
# permission séparée (le cahier des charges en exige exactement 7).
_CLOCK_STATE_PERMISSIONS = frozenset({_PERM_CLOCK, _PERM_MOVES})

# Mapping route POST -> permission requise. "/action/<mot>" est traité à
# part (voir _ACTION_PERMISSION) ; "/authenticate" et "/end_tournament"
# sont volontairement absentes (la première est pré-niveau-1, la seconde
# est réservée ADMIN par construction — voir do_POST).
_POST_ROUTE_PERMISSION = {
    "/eliminate": _PERM_ELIMINATIONS,
    "/confirm_move": _PERM_MOVES,
    "/upload_photo": _PERM_PHOTOS,
    "/delete_photo": _PERM_PHOTOS,
    "/rebalance_answer": _PERM_REBALANCE,
}

# Message affiché/renvoyé tel quel (page d'accueil ET réponses JSON de
# refus) pour un appareil SANS propriétaire ou un DIRTO sans la moindre
# autorisation pour CE tournoi (règle Phase 3 : "aucune autorisation
# existante" = zéro permission, jamais distingué ici d'un DIRTO
# explicitement autorisé mais dont les 7 cases ont été décochées — même
# traitement, même message, décision explicite de simplicité/sûreté).
_NO_PERMISSION_MESSAGE = "Aucune fonction autorisée — contactez un ADMIN."


def _strip_permission_sections(html, permissions):
    """Retire de `html` chaque bloc <!--PERM:X-->...<!--/PERM:X--> dont
    X n'est pas dans `permissions` ; les blocs conservés perdent
    uniquement leurs marqueurs (jamais leur contenu). Simple recherche
    de sous-chaînes (jamais une regex sur du HTML arbitraire) : les
    marqueurs sont des littéraux fixes posés à la main dans
    _PAGE_TEMPLATE, pas du contenu utilisateur. N'est JAMAIS LA
    protection réelle (voir la docstring de _resolve_role plus bas) —
    seulement l'ergonomie "n'afficher que les fonctions autorisées" de
    la Phase 4 ; le contrôle qui compte est refait côté serveur pour
    chaque route, que ce bloc ait été retiré ou non de ce qui a été
    affiché."""
    for key in (
        _PERM_ELIMINATIONS, _PERM_TABLES, _PERM_MOVES, _PERM_CLOCK,
        _PERM_LEVELS, _PERM_PHOTOS, _PERM_REBALANCE,
    ):
        open_tag = f"<!--PERM:{key}-->"
        close_tag = f"<!--/PERM:{key}-->"
        if key in permissions:
            html = html.replace(open_tag, "").replace(close_tag, "")
        else:
            result = []
            i = 0
            while True:
                start = html.find(open_tag, i)
                if start == -1:
                    result.append(html[i:])
                    break
                result.append(html[i:start])
                end = html.index(close_tag, start) + len(close_tag)
                i = end
            html = "".join(result)
    return html


def _strip_admin_only_sections(html, is_admin):
    """Retire de `html` chaque bloc <!--ADMIN_ONLY-->...<!--/ADMIN_ONLY-->
    si `is_admin` est faux (bloc entier omis) ; sinon les deux
    marqueurs sont simplement effacés, le contenu reste. Utilisé pour
    la section "Fin de la partie" : jamais accordable à un DIRTO, quel
    que soit l'état de ses permissions (voir _resolve_role)."""
    open_tag = "<!--ADMIN_ONLY-->"
    close_tag = "<!--/ADMIN_ONLY-->"
    if is_admin:
        return html.replace(open_tag, "").replace(close_tag, "")
    result = []
    i = 0
    while True:
        start = html.find(open_tag, i)
        if start == -1:
            result.append(html[i:])
            break
        result.append(html[i:start])
        end = html.index(close_tag, start) + len(close_tag)
        i = end
    html = "".join(result)
    return html


# Petite page affichée à la place de l'accueil normal pour un appareil
# SANS propriétaire ou un DIRTO à zéro permission pour ce tournoi — voir
# _NO_PERMISSION_MESSAGE. Pas de bouton, pas de sondage périodique :
# rien à afficher tant que ce statut n'a pas changé sur le PC (voir
# _refresh_remote_dirto_permissions_cache dans main.py). Le bouton 🔄
# reste présent (voir _RELOAD_SCRIPT) pour recharger après qu'un ADMIN a
# accordé une autorisation, sans devoir couper/rouvrir Safari.
_NO_ACCESS_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Contrôle à distance</title>
<style>
  body {{
    margin: 0; padding: 40px 20px; min-height: 100vh; box-sizing: border-box;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }}
  #btn-reload {{
    position: fixed; top: 14px; right: 14px; width: 40px; height: 40px;
    max-width: 40px; margin: 0; padding: 0; border-radius: 50%;
    background: #1c3d2c; font-size: 18px; line-height: 40px; border: none;
    color: #fff; box-shadow: 0 2px 6px rgba(0,0,0,.4);
  }}
  h1 {{ font-size: 17px; color: #e8c468; margin: 0 0 2px; }}
  .tournoi {{ color: #b9ad8f; font-size: 13px; margin: 0 0 26px; }}
  p.msg {{ color: #d98a5f; font-size: 16px; max-width: 320px; margin: 0 auto; }}
</style>
</head>
<body>
  <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  <h1>🎙 Contrôle à distance</h1>
  <p class="tournoi">{tournament_name}</p>
  <p class="msg">{message}</p>
<script>
{auth_redirect_script}
{reload_script}
</script>
</body>
</html>
"""

# Page de refus générique pour une PAGE (GET /eliminate, /photos, /moves)
# demandée sans la permission requise — jamais utilisée pour l'accueil
# lui-même (voir _NO_ACCESS_PAGE ci-dessus) ni pour une route de données
# JSON (voir _send_permission_denied, qui renvoie du JSON dans ce cas).
_FORBIDDEN_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Contrôle à distance</title>
<style>
  body {{
    margin: 0; padding: 40px 20px; min-height: 100vh; box-sizing: border-box;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }}
  h1 {{ font-size: 17px; color: #e8c468; margin: 0 0 26px; }}
  p.msg {{ color: #d98a5f; font-size: 16px; max-width: 320px; margin: 0 auto 26px; }}
  a {{ color: #8fc4d6; }}
</style>
</head>
<body>
  <h1>🎙 Contrôle à distance</h1>
  <p class="msg">{message}</p>
  <p><a href="/">← Retour à l'accueil</a></p>
</body>
</html>
"""

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

# ---------------------------------------------------------------------
# Authentification (demande du 2026-09-09) : code à 6 chiffres puis
# approbation par appareil — voir la docstring de ce module et celle,
# bien plus détaillée, de la section correspondante dans open_windows.py
# (registre persistant des appareils / fichier éphémère de session).
# ---------------------------------------------------------------------

# Cookie HttpOnly, jamais lu ni écrit par le JavaScript de la page —
# identifiant de navigateur (rc_bid, 128 bits, non sensible : ne sert
# qu'à retrouver un appareil déjà approuvé, voir open_windows.
# register_device_attempt) et jeton de session par appareil (rc_auth,
# 256 bits, voir open_windows.get_or_mint_device_session_token).
_BROWSER_ID_COOKIE_NAME = "rc_bid"
_AUTH_COOKIE_NAME = "rc_auth"

# Un an : rc_bid identifie l'APPAREIL, pas la session — doit survivre à
# la fermeture de Safari pour qu'un responsable déjà approuvé n'ait
# jamais à re-demander une approbation d'une soirée à l'autre (voir
# open_windows, section "approbation persistante").
_BROWSER_ID_COOKIE_MAX_AGE = 365 * 24 * 3600
# 24h : largement plus qu'une soirée de tournoi, mais borné plutôt
# qu'un cookie de session pur — un responsable qui ferme et rouvre
# Safari en cours de soirée garde son accès sans ressaisir le code.
_AUTH_COOKIE_MAX_AGE = 24 * 3600

# Routes accessibles SANS authentification (demande du 2026-09-09) :
# strictement celles nécessaires pour saisir/vérifier le code et
# sonder l'état d'une demande d'approbation en attente — TOUT le reste
# (pages ET actions) exige une session valide ET un appareil approuvé,
# vérifiés ICI côté serveur (voir do_GET/do_POST), jamais seulement
# côté page/JS.
_AUTH_EXEMPT_PATHS = {"/login", "/authenticate", "/auth_status"}

# Routes GET qui correspondent à une VRAIE navigation plein écran
# (typiquement suivies d'un rendu HTML plein écran par le téléphone) :
# une requête non authentifiée y répond par une redirection 302 vers
# /login, plutôt que par un 401 JSON — sans ça, /login recevrait un
# fetch() en boucle plutôt qu'un affichage utilisable. Tout le reste
# (endpoints JSON, tout do_POST) répond 401 en JSON — voir
# _AUTH_REDIRECT_SCRIPT côté client, qui intercepte ce 401 pour
# rediriger lui-même vers /login.
_AUTH_PAGE_PATHS = {
    "/", "/index.html", "/eliminate", "/eliminate.html",
    "/photos", "/photos.html", "/moves", "/moves.html",
    "/lobbylist", "/select_tournament",
}


def _parse_cookie(cookie_header, name):
    """Valeur du cookie `name` dans l'en-tête Cookie brut, ou None —
    petit utilitaire partagé (identifiant de navigateur, jeton
    d'authentification...) ; le cookie "selected_pid" garde son
    analyse propre dans resolve_current_pid, laissée telle quelle pour
    ne rien changer à un comportement déjà en place et testé."""
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if part.startswith(name + "="):
            return part.split("=", 1)[1]
    return None


_BROWSER_ID_RE = re.compile(r"[0-9a-f]{32}")

# Injecté en tout début du <script> de chaque page accessible une fois
# authentifié (voir _PAGE_TEMPLATE/_ELIMINATE_PAGE/_PHOTOS_PAGE/
# _MOVES_PAGE/_NO_ACCESS_PAGE) : dès qu'une session serveur devient
# invalide (nouvelle session côté PC, donc nouveau code/jetons — voir
# open_windows._ensure_remote_session_auth_locked — OU appareil révoqué
# entre-temps), toute réponse 401 d'un fetch() quelconque de la page
# renvoie directement au formulaire de code plutôt que de laisser la
# page continuer à afficher des données obsolètes/vides silencieusement.
# Une seule interception centrale plutôt que de modifier individuel-
# lement chaque .then() de chaque page (il y en a une bonne dizaine,
# réparties sur plusieurs templates).
#
# Sondage automatique du rôle/des permissions (demande du 2026-09-24,
# "synchronisation automatique permissions" — incident réel : MARIE,
# DIRTO, restait bloquée sur "Aucune fonction autorisée" après que RAJ
# lui a accordé "Gérer les éliminations", sans qu'aucun mécanisme ne
# revérifie jamais l'autorisation d'une page déjà ouverte) : interroge
# GET /permission_state toutes les 3s (même cadence que /clock_state,
# déjà établie dans cette page) — accessible à TOUT appareil authentifié
# niveau 1, quel que soit son rôle/ses permissions actuels (voir do_GET,
# jamais gardée par une permission : sinon un appareil qui vient de
# tout perdre ne pourrait justement plus détecter qu'il a tout perdu).
# Ne fait QUE comparer une signature (rôle + permissions triées) à celle
# du sondage précédent — jamais mise en cache comme autorité (voir la
# docstring de resolve_role côté serveur, seule source de vérité réelle,
# revérifiée à CHAQUE requête) : un écart déclenche un simple
# rechargement complet de la page (reloadApp(), avec le même paramètre
# anti-cache que le bouton 🔄 manuel), qui redemande alors un rendu
# entièrement neuf au serveur — jamais une modification du DOM en place
# à partir de données côté client. Le tout premier sondage ne fait
# qu'enregistrer la référence (jamais de rechargement au chargement de
# la page elle-même) ; un échec réseau ponctuel est ignoré en silence,
# retenté au sondage suivant.
_AUTH_REDIRECT_SCRIPT = (
    "(function() {\n"
    "  var _origFetch = window.fetch;\n"
    "  window.fetch = function() {\n"
    "    return _origFetch.apply(this, arguments).then(function(response) {\n"
    "      if (response.status === 401) {\n"
    "        window.location.href = '/login';\n"
    "        throw new Error('auth_required');\n"
    "      }\n"
    "      return response;\n"
    "    });\n"
    "  };\n"
    "})();\n"
    "(function() {\n"
    "  var lastPermissionSignature = null;\n"
    "  function pollPermissionState() {\n"
    "    fetch('/permission_state').then(function(r) {\n"
    "      if (!r.ok) throw new Error('permission_state_unavailable');\n"
    "      return r.json();\n"
    "    }).then(function(data) {\n"
    "      var perms = (data.permissions || []).slice().sort();\n"
    "      var signature = data.role + '|' + perms.join(',');\n"
    "      if (lastPermissionSignature === null) {\n"
    "        lastPermissionSignature = signature;\n"
    "        return;\n"
    "      }\n"
    "      if (signature !== lastPermissionSignature) {\n"
    "        reloadApp();\n"
    "      }\n"
    "    }).catch(function() { /* réseau/tournoi momentanément indisponible : retenté au prochain sondage */ });\n"
    "  }\n"
    "  setInterval(pollPermissionState, 3000);\n"
    "})();"
)

_LOGIN_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Code d'accès</title>
<style>
  body {
    margin: 0; padding: 40px 20px; min-height: 100vh; box-sizing: border-box;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
    text-align: center;
  }
  h1 { font-size: 19px; color: #e8c468; margin: 0 0 6px; }
  p.hint { color: #b9ad8f; font-size: 14px; margin: 0 0 26px; }
  input {
    display: block; width: 100%; max-width: 280px; margin: 0 auto 16px;
    padding: 16px 10px; font-size: 28px; letter-spacing: 6px; text-align: center;
    border: none; border-radius: 10px; box-sizing: border-box;
    -webkit-appearance: none;
  }
  button {
    display: block; width: 100%; max-width: 280px; margin: 0 auto;
    padding: 14px 10px; font-size: 17px; font-weight: 700;
    border: none; border-radius: 10px; color: #fff; background: #2c6e8a;
    -webkit-tap-highlight-color: transparent;
  }
  button:active { transform: scale(0.97); }
  button:disabled { opacity: 0.55; }
  #msg { min-height: 40px; margin: 18px auto 0; max-width: 280px; color: #d98a5f; font-size: 14px; }
  #msg.pending { color: #e8c468; }
</style>
</head>
<body>
  <h1>🔒 Contrôle à distance</h1>
  <p class="hint" id="hint">Code affiché dans Paramètres, sur le PC</p>
  <input id="code" type="tel" inputmode="numeric" pattern="[0-9]*" maxlength="6" autocomplete="off" autofocus>
  <button id="btn-submit" onclick="submitCode()">Valider</button>
  <p id="msg"></p>
<script>
var pollTimer = null;

function stopPolling() {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
}

// Sondage de l'état d'une demande en attente (demande du 2026-09-09) :
// toutes les 2 secondes seulement, jamais plus fréquent — l'iPhone
// détecte ainsi tout seul le moment où le responsable clique
// "Autoriser" sur le PC, sans que l'utilisateur ait à recharger la
// page ou ressaisir le code.
function startPolling() {
  stopPolling();
  pollTimer = setInterval(function() {
    fetch('/auth_status').then(function(r) { return r.json(); }).then(function(data) {
      if (data.status === 'approved') {
        stopPolling();
        window.location.href = '/';
      } else if (data.status === 'refused') {
        stopPolling();
        var msg = document.getElementById('msg');
        msg.className = '';
        msg.textContent = "Accès refusé par le responsable du tournoi.";
      }
    }).catch(function() { /* réseau momentanément indisponible : on retentera */ });
  }, 2000);
}

function submitCode() {
  var input = document.getElementById('code');
  var btn = document.getElementById('btn-submit');
  var msg = document.getElementById('msg');
  var code = input.value.trim();
  if (!/^[0-9]{6}$/.test(code)) {
    msg.className = '';
    msg.textContent = 'Entrez les 6 chiffres du code.';
    return;
  }
  btn.disabled = true;
  msg.className = '';
  msg.textContent = '';
  fetch('/authenticate', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({code: code}),
  }).then(function(r) {
    if (r.status === 429) {
      return r.json().then(function(data) { throw {rateLimited: true, message: data.message}; });
    }
    return r.json();
  }).then(function(data) {
    btn.disabled = false;
    if (!data.ok) {
      msg.className = '';
      msg.textContent = 'Code incorrect.';
      input.value = '';
      input.focus();
      return;
    }
    if (data.status === 'approved') {
      window.location.href = '/';
    } else {
      msg.className = 'pending';
      msg.textContent = "Code correct. En attente d'autorisation par le responsable du tournoi.";
      startPolling();
    }
  }).catch(function(err) {
    btn.disabled = false;
    msg.className = '';
    if (err && err.rateLimited) {
      msg.textContent = err.message || 'Trop de tentatives. Réessayez plus tard.';
    } else {
      msg.textContent = 'Connexion impossible. Réessayez.';
    }
  });
}
document.getElementById('code').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') submitCode();
});
</script>
</body>
</html>
"""

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
  /* Clignotement doux tant qu'un mouvement est en attente (voir
     refreshClockState/has_pending_moves) : alternance d'opacité, pas de
     couleur criarde ni de changement de taille — le texte "📋 Afficher
     Mouvements" et la mise en page du bouton restent identiques, seule
     son apparence pulse légèrement. Portée à CE bouton uniquement
     (jamais toute la page). */
  @keyframes btn-mouvements-blink {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.55; }}
  }}
  #btn-mouvements.blink {{
    animation: btn-mouvements-blink 1.4s ease-in-out infinite;
  }}
  #btn-niveau-precedent, #btn-niveau-suivant {{ background: #2c6e8a; }}
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
  /* Petites flèches ↓/↑ de part et d'autre de "Afficher Mouvements"
     (demande du 2026-09-20) — même principe que .zoom-row ci-dessus,
     réutilisée telle quelle (voir le <div class="zoom-row"> du bouton
     #btn-mouvements plus bas) : ne déplacent QUE le bandeau du Chrono
     Projo, ne confirment/suppriment jamais un mouvement. */
  #btn-mouvements {{ flex: 1; }}
  #btn-mouvements-bas, #btn-mouvements-haut {{
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
  /* "Fin de la partie" : nettement séparée des autres boutons (ligne de
     séparation + marge au-dessus) et d'une couleur plus sombre/alarmante
     que "Éliminations" (#b5442e) pour qu'un geste distrait ne suffise
     pas à la confondre avec une action courante — une confirmation
     (window.confirm) est de toute façon exigée avant tout envoi. */
  #end-tournament-section {{
    max-width: 420px; margin: 28px auto 0; padding-top: 16px;
    border-top: 1px solid #294235;
  }}
  #btn-end-tournament {{ background: #6e1f1f; }}
</style>
</head>
<body>
  <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  <h1>🎙 Contrôle à distance</h1>
  <p class="tournoi" id="tournoi-name">{tournament_name}</p>
  <p class="version">v{app_version}</p>

  {lobby_button}
  <!--PERM:eliminations-->
  <button id="btn-eliminations" onclick="window.location.href='/eliminate'">🎯 Gérer les éliminations</button>
  <!--/PERM:eliminations-->
  <!--PERM:tables-->
  <div class="zoom-row">
    <button id="btn-tables-zoom-moins" onclick="sendAction('tables_zoom_moins', this)" title="Rétrécir l'écran Tables sur le PC">Z−</button>
    <button id="btn-tables" onclick="sendAction('tables', this)">🗺 Plan des tables</button>
    <button id="btn-tables-zoom-plus" onclick="sendAction('tables_zoom_plus', this)" title="Agrandir l'écran Tables sur le PC">Z+</button>
  </div>
  <!--/PERM:tables-->
  <!--PERM:moves-->
  <div class="zoom-row">
    <button id="btn-mouvements-bas" onclick="sendAction('mouvements_bas', this)" title="Descendre le bandeau Mouvements sur le PC">↓</button>
    <button id="btn-mouvements" onclick="showMoves()">📋 Afficher Mouvements</button>
    <button id="btn-mouvements-haut" onclick="sendAction('mouvements_haut', this)" title="Monter le bandeau Mouvements sur le PC">↑</button>
  </div>
  <button id="btn-terminer" onclick="sendAction('terminer', this)">✅ Mouvements terminés</button>
  <!--/PERM:moves-->
  <!--PERM:clock-->
  <div class="chrono-row">
    <button id="btn-chronometre" onclick="sendAction('chronometre', this)">▶ Chronomètre</button>
    <button id="btn-pause-toggle" onclick="togglePause()" title="Met en pause / relance le chrono">OFF</button>
  </div>
  <!--/PERM:clock-->
  <!--PERM:levels-->
  <button id="btn-niveau-precedent" onclick="sendAction('niveau_precedent', this)">⏮ Niveau Précédent</button>
  <button id="btn-niveau-suivant" onclick="sendAction('niveau_suivant', this)">⏭ Niveau Suivant</button>
  <!--/PERM:levels-->
  <!--ADMIN_ONLY-->
  <button id="btn-elimination" onclick="sendAction('elimination', this)">⏸ Joueurs</button>
  <!--/ADMIN_ONLY-->
  <!--PERM:photos-->
  <button id="btn-photos" onclick="window.location.href='/photos'">📷 Photos des joueurs</button>
  <!--/PERM:photos-->

  <p id="status"></p>

  <!--ADMIN_ONLY-->
  <div id="end-tournament-section">
    <button id="btn-end-tournament" onclick="confirmEndTournament()">⛔ Fin de la partie</button>
  </div>
  <!--/ADMIN_ONLY-->

<script>
{auth_redirect_script}
var OWN_PID = {own_pid};  // capturé au chargement de cette page — voir /end_tournament et sa docstring côté serveur
// Nom du tournoi RÉELLEMENT affiché sur CETTE page (voir tournament_name_json
// côté serveur) — celui du tournoi sélectionné/servi ici, jamais forcément
// celui qui héberge le port 8765 (voir le correctif Cookie de _proxy).
var TOURNAMENT_NAME = {tournament_name_json};
function confirmEndTournament() {{
  if (!window.confirm('Confirmer la Fin de partie pour le ' + TOURNAMENT_NAME + ' ?')) return;
  var status = document.getElementById('status');
  status.textContent = 'Fermeture en cours...';
  fetch('/end_tournament', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{pid: OWN_PID}})
  }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
    if (!data.ok) {{
      status.textContent = data.message || 'Échec.';
      return;
    }}
    // Ce tournoi n'existe plus : garder sa sélection ferait, au prochain
    // appel, retomber EN SILENCE sur un autre tournoi que celui affiché
    // ici (voir resolve_proxy_port côté serveur — un pid disparu du
    // registre est traité comme "aucune sélection", jamais signalé comme
    // une erreur) — mieux vaut repartir sans aucune sélection.
    document.cookie = 'selected_pid=; Max-Age=0; Path=/';
    // En-tête immédiatement neutre : ce process va fermer (via la file
    // d'attente vocale côté Python — voir _poll_voice_queue — donc pas
    // forcément instantané), plus question d'afficher encore le nom de
    // ce tournoi pendant l'attente qui suit.
    document.getElementById('tournoi-name').textContent = 'En attente d\\'un tournoi';
    status.textContent = data.other_tournaments_remain
      ? 'Partie terminée. Retour au Lobby...'
      : 'Partie terminée. En attente d\\'un nouveau tournoi...';
    waitForDifferentTournamentThenReload(OWN_PID);
  }}).catch(function(e) {{
    status.textContent = 'Échec (' + e.message + ') — vérifiez le wifi.';
  }});
}}
// Après "Fin de la partie" : sonde périodiquement le point d'entrée
// normal (port 8765 — toujours l'origine de CETTE page déjà chargée,
// jamais un port/pid figé en dur) jusqu'à ce qu'un tournoi VIVANT et
// DIFFÉRENT de celui qu'on vient de fermer (closedPid) réponde
// réellement. Ne navigue JAMAIS (ni window.location, ni location.href,
// ni reload) avant cette confirmation.
//
// Pourquoi pas simplement "la requête a réussi" (r.ok) : le tournoi
// qu'on vient de fermer reste souvent joignable un court instant après
// avoir répondu à /end_tournament — la fermeture réelle passe par la
// file d'attente vocale de Tkinter (_poll_voice_queue côté Python,
// scrutée toutes les 150 ms), pendant laquelle son propre serveur HTTP
// continue de tourner normalement. Une redirection déclenchée sur la
// seule foi d'un "r.ok" pouvait donc atterrir en fait sur CE MÊME
// process en train de mourir, et échouer (le port pouvant se refermer
// entre cette vérification et la navigation réelle) : c'est exactement
// ce qui produisait l'écran natif Safari "ERR_CONNECTION_FAILED".
//
// /lobbylist expose maintenant l'en-tête X-Own-Pid (voir
// _handle_lobbylist côté serveur) : le pid de QUI répond VRAIMENT à
// CETTE requête précise (/lobbylist n'est jamais relayée vers un autre
// process — voir do_GET, elle est toujours traitée localement). Un
// tournoi n'est considéré "détecté" que si ce pid diffère de closedPid
// — jamais sur la seule réussite de la requête. Un échec réseau (port
// encore libre, personne n'écoute sur 8765 pour l'instant) est
// silencieux et simplement retenté, comme une réponse dont le pid ne
// diffère pas encore (le tournoi qu'on ferme lui-même, ou une réponse
// sans cet en-tête) : dans les deux cas, on continue d'attendre, sans
// jamais afficher d'erreur ni de popup.
var RECONNECT_POLL_MS = 1500;
function waitForDifferentTournamentThenReload(closedPid) {{
  fetch('/lobbylist', {{ cache: 'no-store' }}).then(function(r) {{
    var responderPid = r.ok ? Number(r.headers.get('X-Own-Pid')) : NaN;
    if (r.ok && responderPid && responderPid !== closedPid) {{
      window.location.href = '/lobbylist';
      return;
    }}
    setTimeout(function() {{ waitForDifferentTournamentThenReload(closedPid); }}, RECONNECT_POLL_MS);
  }}).catch(function() {{
    setTimeout(function() {{ waitForDifferentTournamentThenReload(closedPid); }}, RECONNECT_POLL_MS);
  }});
}}
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
// "Afficher Mouvements" (demande du 2026-09-19) : continue de ramener
// l'onglet Mouvements au premier plan sur le Mac (comportement
// historique inchangé, voir on_word('mouvements')) ET navigue en plus
// le téléphone vers la nouvelle page listant chaque mouvement avec son
// propre bouton [OK]. keepalive:true (au lieu de sendAction/fetch
// classique) : garantit que cette requête POST est bien envoyée même si
// la navigation qui suit l'interrompt avant qu'elle n'ait eu le temps de
// se terminer normalement — un fetch() ordinaire risquerait d'être
// annoncé par le navigateur au moment du changement de page.
function showMoves() {{
  fetch('/action/mouvements', {{ method: 'POST', keepalive: true }}).catch(function() {{}});
  window.location.href = '/moves';
}}

// Petit bouton ON/OFF à côté de "Chronomètre" : reflète et bascule
// directement l'état pause/lecture du chrono, sans passer par l'onglet
// Joueurs ni l'écran projecteur. "OFF" = chrono en marche (l'appui va le
// mettre en pause, d'où "ON" ensuite = pause activée) ; "ON" = chrono en
// pause (l'appui va le relancer, retour à "OFF").
function refreshClockState() {{
  fetch('/clock_state').then(function(r) {{ return r.json(); }}).then(function(data) {{
    document.getElementById('btn-pause-toggle').textContent = data.paused ? 'ON' : 'OFF';
    // Clignotement de "📋 Afficher Mouvements" tant qu'un mouvement est en
    // attente (voir has_pending_moves ci-dessus, même sondage 3s que le
    // bouton ON/OFF juste au-dessus — aucun sondage supplémentaire créé) :
    // ajoute/retire uniquement la classe CSS .blink, jamais le texte du
    // bouton ni sa position.
    document.getElementById('btn-mouvements').classList.toggle('blink', !!data.has_pending_moves);
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
{rebalance_widget}
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
  /* "← Retour" : un vrai bouton tactile (comme sur la page Éliminations),
     pas un simple lien texte discret — mène toujours à "/", résolu à
     CHAQUE clic par la même logique de routage que le reste (voir
     resolve_current_pid côté serveur) : jamais un pid figé dans ce
     bouton, donc toujours valide même après changement de sélection,
     fermeture d'un tournoi, ou reprise du port 8765 par un autre. */
  #btn-back {{
    display: block; width: 100%; max-width: 420px; margin: 0 auto 22px;
    border: none; border-radius: 10px; background: #2c4a6e; color: #f5efe0;
    font-size: 15px; font-weight: 700; padding: 12px 16px;
    -webkit-tap-highlight-color: transparent;
  }}
  #btn-back:active {{ transform: scale(0.97); }}
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
  <button id="btn-back" onclick="window.location.href='/'">← Retour</button>
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
  #topbar .tournoi {{ color: #e8c468; font-size: 15px; font-weight: 700; }}
  /* Indicateur de communication (demande du 2026-09-19, "bétonner la
     communication téléphone <-> PC") : discret (petit point + texte
     court), jamais un popup — l'utilisateur doit pouvoir d'un coup
     d'œil savoir si la liste affichée est fraîche ou potentiellement
     périmée, sans qu'un message ne s'impose à chaque sondage raté. */
  #conn-indicator {{
    display: flex; align-items: center; gap: 5px; font-size: 11px;
    color: #9fb8a8; padding: 2px 0;
  }}
  #conn-indicator .dot {{
    width: 7px; height: 7px; border-radius: 50%; background: #4caf6d; flex: none;
  }}
  #conn-indicator.conn-reconnecting .dot {{ background: #e8c468; }}
  #conn-indicator.conn-lost .dot {{ background: #d9534f; }}
  #conn-indicator.conn-reconnecting {{ color: #e8c468; }}
  #conn-indicator.conn-lost {{ color: #d9534f; }}
  /* "← Retour" : un vrai bouton tactile (fond plein, coins arrondis,
     zone de frappe confortable), pas un simple lien texte discret —
     pour qu'il soit immédiatement identifiable comme actionnable sur un
     écran d'iPhone tenu à bout de bras/au-dessus de la table. */
  #btn-back {{
    border: none; border-radius: 8px; background: #2c4a6e; color: #f5efe0;
    font-size: 15px; font-weight: 700; padding: 9px 16px; line-height: 1.2;
    -webkit-tap-highlight-color: transparent;
  }}
  #btn-back:active {{ transform: scale(0.97); }}
  #btn-reload {{
    width: 32px; height: 32px; border: none; border-radius: 50%;
    background: #1c3d2c; color: #f5efe0; font-size: 15px; line-height: 32px;
    padding: 0; -webkit-tap-highlight-color: transparent;
  }}
  /* 46px de #topbar + ~18px de #conn-indicator (ajouté le 2026-09-19) :
     évite que #columns ne déborde sous overflow:hidden. */
  #columns {{ display: flex; height: calc(100% - 64px); min-height: 0; }}
  .col {{ flex: 1; display: flex; flex-direction: column; min-width: 0; min-height: 0; }}
  .col-left {{ border-right: 2px solid #294235; }}
  .col h2 {{
    margin: 0; padding: 10px; font-size: 15px; text-align: center;
    background: #0b1c15; color: #e8c468; position: sticky; top: 0;
  }}
  /* "ÉLIMINÉ"/"ÉLIMINATEUR" en majuscules et en couleur distincte de
     part et d'autre (demande du 2026-09-20) : text-transform plutôt que
     retaper le texte en dur, pour ne jamais risquer une faute d'accent
     sur la version majuscule. Teintes dérivées de la palette rouge/bleu
     déjà utilisée ailleurs dans cette page (#b5442e "Éliminations" /
     #2c6e8a "Niveau"), mais éclaircies : ces deux couleurs d'origine
     sont pensées comme fond de bouton (texte blanc par-dessus), pas
     comme texte directement sur le fond très sombre #0b1c15 de ce
     bandeau — contraste vérifié ≥ 4,5:1 (WCAG AA) avec les teintes
     choisies ici, très en dessous avec les couleurs d'origine (~3.2:1),
     important pour une lecture rapide en pleine partie. */
  .col-left h2 {{ color: #d9614a; text-transform: uppercase; }}
  .col-right h2 {{ color: #5fa8cc; text-transform: uppercase; }}
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
    <button id="btn-back" onclick="window.location.href='/'">← Retour</button>
    <span class="tournoi">{tournament_name}</span>
    <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  </div>
  <div id="conn-indicator" class="conn-ok"><span class="dot"></span><span id="conn-text">Connecté</span></div>
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
{auth_redirect_script}
var players = [];
var lastSignature = null;
var pending = null;    // candidat de glissement pas encore confirmé : {{id, label, sub, el, startX, startY, engaged}}
var hoverTarget = null;
var pollTimer = null;
// Distance (px) à partir de laquelle on tranche entre "c'est un défilement"
// (mouvement surtout vertical) et "c'est un glissement" (mouvement surtout
// horizontal, colonne de gauche vers colonne de droite) — voir onTouchMove.
var DRAG_THRESHOLD = 10;

// ===================================================================
// Fiabilité de la communication (demande du 2026-09-19, suite à un
// incident réel en club où cette page était devenue muette sans le
// moindre signe visible) : indicateur d'état, timeout explicite,
// backoff raisonnable, distinction 401/502/réseau, jamais de catch vide.
// ===================================================================
var pollDelayMs = 4000;
var POLL_DELAY_MIN_MS = 4000;
var POLL_DELAY_MAX_MS = 20000;
var consecutiveFailures = 0;
var connState = 'ok';  // 'ok' | 'reconnecting' | 'lost'

function setConnState(state) {{
  if (state === connState) return;
  connState = state;
  var el = document.getElementById('conn-indicator');
  var txt = document.getElementById('conn-text');
  if (!el || !txt) return;
  el.className = state === 'ok' ? 'conn-ok' : (state === 'reconnecting' ? 'conn-reconnecting' : 'conn-lost');
  txt.textContent = state === 'ok' ? 'Connecté' : (state === 'reconnecting' ? 'Reconnexion…' : 'Connexion perdue');
}}

// AbortController : disponible sur Safari iOS depuis longtemps (iOS
// 11.3+), donc sur toute cible réelle de ce contrôle à distance — un
// timeout EXPLICITE évite qu'une connexion dégradée ne laisse un bouton
// "en l'air" indéfiniment (sans lui, on dépendrait du timeout par
// défaut du navigateur, très long et variable).
function fetchWithTimeout(url, options, timeoutMs) {{
  var opts = options || {{}};
  var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  if (controller) opts.signal = controller.signal;
  var timer = controller ? setTimeout(function() {{ controller.abort(); }}, timeoutMs) : null;
  var clear = function() {{ if (timer) clearTimeout(timer); }};
  return fetch(url, opts).then(function(r) {{ clear(); return r; }}, function(e) {{ clear(); throw e; }});
}}

function scheduleNextPoll(ok) {{
  if (pollTimer) clearTimeout(pollTimer);
  // Backoff raisonnable sur échecs successifs (jamais de boucle
  // agressive) : repart immédiatement à l'intervalle normal dès le
  // premier succès retrouvé.
  pollDelayMs = ok ? POLL_DELAY_MIN_MS : Math.min(pollDelayMs * 1.5, POLL_DELAY_MAX_MS);
  pollTimer = setTimeout(loadPlayers, pollDelayMs);
}}

function tryRecoverViaLobby() {{
  // 502/proxy injoignable de façon PERSISTANTE (demande du 2026-09-19,
  // "si le tournoi n'existe réellement plus, revenir proprement au
  // Lobby") : /lobbylist est TOUJOURS traité localement par ce serveur
  // (jamais relayé, voir do_GET) — encore joignable même si le tournoi
  // SÉLECTIONNÉ ne l'est plus. Réutilise l'architecture existante telle
  // quelle (aucune deuxième route/mécanisme créé) : une simple
  // navigation, exactement ce que fait déjà le bouton "🏛 Lobby".
  window.location.href = '/lobbylist';
}}

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
  // l'élément suivi sous le doigt. Le prochain sondage rattrapera.
  if (pending && pending.engaged) {{ scheduleNextPoll(true); return; }}
  fetchWithTimeout('/players', {{}}, 6000).then(function(r) {{
    // 401 : le script d'interception globale de fetch() injecté en tête
    // de cette page (voir _AUTH_REDIRECT_SCRIPT côté serveur) a DÉJÀ
    // intercepté cette réponse avant que ce .then() ne s'exécute et
    // déclenché la redirection vers /login — jamais traité comme une
    // liste de joueurs ici, voir son propre code.
    // 409 + tournament_gone (demande du 2026-09-19, correctif du
    // routage silencieux vers un AUTRE tournoi) : le tournoi
    // EXPLICITEMENT sélectionné par ce téléphone a disparu — signal
    // DÉFINITIF, jamais une panne passagère, donc retour au Lobby
    // IMMÉDIAT, sans attendre le seuil d'échecs consécutifs prévu pour
    // un 502 transitoire (voir SELECTION_VANISHED côté serveur).
    if (r.status === 409) {{
      return r.json().then(function(data) {{
        if (data && data.tournament_gone) {{
          setConnState('reconnecting');
          tryRecoverViaLobby();
        }}
        throw new Error('tournament_gone');
      }});
    }}
    // 502 (_proxy, tournoi cible injoignable) : jamais du JSON valide
    // (page d'erreur HTML de send_error) — distingué explicitement,
    // jamais transmis tel quel à .json().
    if (r.status === 502) {{
      consecutiveFailures++;
      setConnState('reconnecting');
      if (consecutiveFailures >= 5) {{ tryRecoverViaLobby(); }}
      throw new Error('proxy_unreachable');
    }}
    if (!r.ok) {{ throw new Error('http_' + r.status); }}
    return r.json();
  }}).then(function(data) {{
    if (!Array.isArray(data)) {{ throw new Error('format_inattendu'); }}
    consecutiveFailures = 0;
    setConnState('ok');
    var sig = JSON.stringify(data);
    if (sig !== lastSignature) {{  // rien n'a changé : pas de re-rendu (évite le clignotement et perd le défilement en cours)
      lastSignature = sig;
      players = data;
      renderLists();
    }}
    scheduleNextPoll(true);
  }}).catch(function(e) {{
    if (e && e.message === 'auth_required') {{
      // Redirection déjà engagée par auth_redirect_script : rien de
      // plus à faire ici qu'exposer l'état visuel le temps qu'elle
      // prenne effet.
      setConnState('lost');
      return;
    }}
    if (e && e.message === 'tournament_gone') {{
      // Navigation vers /lobbylist déjà engagée (voir ci-dessus) : rien
      // de plus à faire, surtout pas reprogrammer un sondage sur cette
      // page qu'on est en train de quitter.
      return;
    }}
    consecutiveFailures++;
    if (connState !== 'lost') {{
      setConnState(consecutiveFailures >= 2 ? 'lost' : 'reconnecting');
    }}
    scheduleNextPoll(false);
  }});
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

// Identifiant de requête réutilisé pour un RETRY de la MÊME action
// (demande du 2026-09-19) : évite qu'une élimination ne soit appliquée
// deux fois côté serveur (voir main.py: App._remote_eliminate_request,
// SEULE garantie réelle — jamais uniquement ce JavaScript) simplement
// parce que l'utilisateur retente après un échec réseau. Une paire
// (éliminé, éliminateur) DIFFÉRENTE obtient toujours un identifiant
// différent ; la MÊME paire, retentée dans les 30s suivant un échec,
// réutilise le même identifiant — passé cette fenêtre, une nouvelle
// tentative est traitée comme une action neuve (rétention côté serveur
// elle-même bornée à 5 minutes, voir _REMOTE_ACTION_DEDUP_TTL_SECONDS).
var lastRequestIdByPair = {{}};
var REQUEST_ID_REUSE_WINDOW_MS = 30000;

function genRequestId() {{
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {{
    return window.crypto.randomUUID();
  }}
  // Repli pour un Safari plus ancien sans crypto.randomUUID (iOS <
  // 15.4) : suffisamment unique pour ce seul usage (dédoublonnage
  // court terme côté serveur), jamais un secret.
  return 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2);
}}

function requestIdFor(eliminatedId, eliminatorId) {{
  var key = eliminatedId + '|' + eliminatorId;
  var now = Date.now();
  var existing = lastRequestIdByPair[key];
  if (existing && (now - existing.ts) < REQUEST_ID_REUSE_WINDOW_MS) {{
    return existing.id;
  }}
  var id = genRequestId();
  lastRequestIdByPair[key] = {{id: id, ts: now}};
  return id;
}}

function confirmElimination(eliminatorId, eliminatorLabel, eliminatorSub, eliminatedId, eliminatedLabel, eliminatedSub) {{
  var elimText = eliminatedLabel + (eliminatedSub ? ' ' + eliminatedSub : '');
  var elorText = eliminatorLabel + (eliminatorSub ? ' ' + eliminatorSub : '');
  // Éliminé d'abord, éliminateur ensuite : dans le même ordre que le
  // geste (on part de la colonne de gauche, l'éliminé) et que l'usage en
  // salle de poker (on note d'abord qui est éliminé, puis par qui).
  var msg = elimText + '\\nest éliminé par\\n' + elorText + ' ?';
  if (!window.confirm(msg)) return;
  var requestId = requestIdFor(eliminatedId, eliminatorId);
  fetchWithTimeout('/eliminate', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{eliminated_id: eliminatedId, eliminator_id: eliminatorId, request_id: requestId}})
  }}, 8000).then(function(r) {{
    // 409 + tournament_gone (demande du 2026-09-19) : ce tournoi a
    // disparu — cette élimination n'a JAMAIS pu être appliquée nulle
    // part (voir SELECTION_VANISHED côté serveur, vérifié AVANT tout
    // traitement métier). Message explicite, jamais confondu avec un
    // simple problème réseau, puis retour immédiat au Lobby.
    if (r.status === 409) {{
      return r.json().then(function(data) {{
        if (data && data.tournament_gone) {{ throw new Error('tournament_gone'); }}
        throw new Error('erreur ' + r.status);
      }});
    }}
    if (!r.ok) throw new Error('erreur ' + r.status);
    return r.json();
  }}).then(function(data) {{
    // data.ok peut être faux même avec une réponse HTTP 200 (demande du
    // 2026-09-08) : refus explicite (ex. bounty PKO sans éliminateur
    // désigné), jamais un échec silencieux — data.message est affiché tel
    // quel, jamais rien n'est modifié côté serveur dans ce cas.
    if (!data.ok) {{
      window.alert(data.message || 'Élimination refusée.');
      return;
    }}
    setConnState('ok');
    consecutiveFailures = 0;
    lastSignature = null;  // forcer le prochain rendu même si la liste redevient identique entre-temps
    loadPlayers();
  }}).catch(function(e) {{
    if (e && e.message === 'auth_required') {{ setConnState('lost'); return; }}
    if (e && e.message === 'tournament_gone') {{
      window.alert("Ce tournoi n'est plus disponible — retour au Lobby. Cette élimination n'a été appliquée nulle part.");
      tryRecoverViaLobby();
      return;
    }}
    // Message volontairement rassurant sur la sécurité d'un nouvel essai
    // (demande du 2026-09-19) : le mécanisme d'idempotence ci-dessus
    // garantit qu'un retry de CETTE même action ne l'appliquera jamais
    // deux fois, même si la première tentative a en réalité réussi côté
    // serveur et que seule sa réponse a été perdue.
    window.alert(
      'Échec : ' + (e && e.message ? e.message : 'réseau') + ' — vérifiez le wifi.\\n'
      + 'Vous pouvez retenter sans risque : cette action ne sera jamais appliquée deux fois.'
    );
  }});
}}

loadPlayers();
{reload_script}
</script>
{rebalance_widget}
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
  #topbar .tournoi {{ color: #e8c468; font-size: 15px; font-weight: 700; }}
  /* "← Retour" : un vrai bouton tactile (même principe et même
     présentation que sur la page Éliminations), pas un simple lien
     texte discret — mène toujours à "/", résolu à CHAQUE clic par la
     même logique de routage que le reste (voir resolve_current_pid
     côté serveur) : jamais un pid figé dans ce bouton, donc toujours
     valide même après changement de sélection, fermeture d'un
     tournoi, ou reprise du port 8765 par un autre. */
  #btn-back {{
    border: none; border-radius: 8px; background: #2c4a6e; color: #f5efe0;
    font-size: 15px; font-weight: 700; padding: 9px 16px; line-height: 1.2;
    -webkit-tap-highlight-color: transparent;
  }}
  #btn-back:active {{ transform: scale(0.97); }}
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
  .player-row .thumb {{
    flex: none; width: 44px; height: 44px; border-radius: 8px;
    object-fit: cover; background: #0b1c15;
  }}
  .player-row .info {{ flex: 1; min-width: 0; text-align: left; }}
  .player-row .info .name {{ font-size: 16px; font-weight: 700; }}
  .player-row .info .sub {{ font-size: 12px; color: #9fb8a8; margin-top: 2px; }}
  .player-row button {{
    flex: none; border: none; border-radius: 10px; background: #2c4a6e;
    color: #fff; font-size: 22px; padding: 10px 14px;
    -webkit-tap-highlight-color: transparent;
  }}
  .player-row button:active {{ transform: scale(0.95); }}
  .player-row .btn-delete {{ background: #6e2c2c; font-size: 18px; padding: 10px 12px; }}
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
    <button id="btn-back" onclick="window.location.href='/'">← Retour</button>
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
{auth_redirect_script}
var players = [];
var pendingPlayer = null;
var lastPlayersJSON = null;

function loadPlayers() {{
  // /roster_players (pas /players, réservé à la page Éliminations) :
  // TOUT le répertoire de joueurs habituels, pas seulement ceux inscrits
  // au tournoi en cours — on peut vouloir prendre en photo un joueur du
  // club avant même qu'il ne soit assis à une table ce soir-là.
  fetch('/roster_players').then(function(r) {{ return r.json(); }}).then(function(data) {{
    // Ne redessine que si quelque chose a vraiment changé : sinon,
    // reconstruire la liste (et donc chaque <img> de miniature) toutes
    // les 4 secondes pour rien faisait clignoter les photos à l'écran.
    var json = JSON.stringify(data);
    if (json === lastPlayersJSON) return;
    lastPlayersJSON = json;
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
    var thumb = p.has_photo
      ? '<img class="thumb" src="/photo_image?name=' + encodeURIComponent(p.name) + '&_r=' + Date.now() + '">'
      : '';
    row.innerHTML =
      thumb +
      '<div class="info">' +
        '<div class="name">' + p.name + '</div>' +
        (p.club ? '<div class="sub">' + p.club + '</div>' : '') +
      '</div>' +
      '<button type="button" class="btn-camera">📷</button>' +
      (p.has_photo ? '<button type="button" class="btn-delete">🗑</button>' : '');
    row.querySelector('.btn-camera').addEventListener('click', function() {{
      pendingPlayer = p;
      // Rien à afficher DURANT la prise de vue elle-même (vue caméra
      // native, hors de portée de cette page) — mais on confirme ici,
      // juste avant qu'elle ne s'ouvre, pour qui la photo est prise.
      document.getElementById('status').textContent = '📷 Photo pour ' + p.name + '...';
      document.getElementById('camera-input').click();
    }});
    var delBtn = row.querySelector('.btn-delete');
    if (delBtn) {{
      delBtn.addEventListener('click', function() {{
        if (!confirm('Supprimer la photo de ' + p.name + ' ?')) return;
        var status = document.getElementById('status');
        status.textContent = 'Suppression...';
        fetch('/delete_photo?player_name=' + encodeURIComponent(p.name), {{ method: 'POST' }})
          .then(function(r) {{ return r.json(); }})
          .then(function(data) {{
            if (!data.ok) throw new Error(data.message || 'erreur');
            status.textContent = 'Photo supprimée pour ' + p.name + '.';
            setTimeout(function() {{ status.textContent = ''; }}, 3000);
            loadPlayers();
          }})
          .catch(function(e) {{
            status.textContent = 'Échec (' + e.message + ').';
          }});
      }});
    }}
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
{rebalance_widget}
</body>
</html>
"""

# Page "Mouvements" (demande du 2026-09-19, "ergonomie iPhone" — bouton
# [OK] par ligne, en complément du bouton "Mouvements terminés" conservé
# tel quel) : liste, une ligne par mouvement ENCORE en attente (via
# /moves_pending, même source que get_has_pending_moves/count_seat_moves,
# aucune logique séparée), triée par table de départ. Un OK confirme
# UNIQUEMENT cette ligne (POST /confirm_move) — elle disparaît
# immédiatement, les autres restent affichées. Le bouton "Mouvements
# terminés" (bas de page, fixe) reste le MÊME /action/terminer que sur la
# page principale, inchangé. Dès que la liste devient vide (dernier OK
# individuel, ou "Mouvements terminés"), message bref puis retour
# automatique à l'accueil — le même mécanisme normal de fin
# (_finish_movement_alert) a déjà tout arrêté côté serveur à ce moment-là
# (voir App._remote_confirm_move), jamais réimplémenté ici.
_MOVES_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Mouvements</title>
<style>
  * {{ box-sizing: border-box; -webkit-tap-highlight-color: transparent; }}
  html, body {{
    margin: 0; padding: 0; min-height: 100%;
    background: #10241a; color: #f5efe0;
    font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", Arial, sans-serif;
  }}
  #topbar {{
    display: flex; align-items: center; justify-content: space-between;
    padding: 10px 14px; background: #0b1c15; border-bottom: 1px solid #294235;
  }}
  #topbar .tournoi {{ color: #e8c468; font-size: 15px; font-weight: 700; }}
  #conn-indicator {{
    display: flex; align-items: center; gap: 5px; font-size: 11px;
    color: #9fb8a8; padding: 2px 14px;
  }}
  #conn-indicator .dot {{
    width: 7px; height: 7px; border-radius: 50%; background: #4caf6d; flex: none;
  }}
  #conn-indicator.conn-reconnecting .dot {{ background: #e8c468; }}
  #conn-indicator.conn-lost .dot {{ background: #d9534f; }}
  #conn-indicator.conn-reconnecting {{ color: #e8c468; }}
  #conn-indicator.conn-lost {{ color: #d9534f; }}
  #btn-back {{
    border: none; border-radius: 8px; background: #2c4a6e; color: #f5efe0;
    font-size: 15px; font-weight: 700; padding: 9px 16px; line-height: 1.2;
    -webkit-tap-highlight-color: transparent;
  }}
  #btn-back:active {{ transform: scale(0.97); }}
  #btn-reload {{
    width: 32px; height: 32px; border: none; border-radius: 50%;
    background: #1c3d2c; color: #f5efe0; font-size: 15px; line-height: 32px;
    padding: 0; -webkit-tap-highlight-color: transparent;
  }}
  /* 90px de marge basse : dégage la place du bouton "Mouvements
     terminés", fixe en bas d'écran (voir #footer), pour que la dernière
     ligne de la liste ne se retrouve jamais cachée derrière lui. */
  #moves-list {{ padding: 10px 14px 90px; overflow-y: auto; -webkit-overflow-scrolling: touch; }}
  .move-row {{
    display: flex; align-items: center; justify-content: space-between; gap: 10px;
    padding: 12px 14px; margin-bottom: 8px; border-radius: 10px;
    background: #1c3d2c; font-size: 15px; line-height: 1.35;
  }}
  .move-row .move-text {{ flex: 1; }}
  .move-row button.btn-ok {{
    flex: none; border: none; border-radius: 8px; background: #1f6b3a; color: #fff;
    font-size: 15px; font-weight: 700; padding: 10px 18px;
  }}
  .move-row button.btn-ok:active {{ transform: scale(0.95); }}
  #empty {{ text-align: center; color: #b9ad8f; padding: 40px 16px; font-size: 15px; }}
  #footer {{
    position: fixed; left: 0; right: 0; bottom: 0; padding: 10px 14px;
    background: #0b1c15; border-top: 1px solid #294235;
  }}
  #btn-terminer {{
    display: block; width: 100%; border: none; border-radius: 10px;
    background: #8a6d1f; color: #fff; font-size: 16px; font-weight: 700; padding: 12px 10px;
    -webkit-tap-highlight-color: transparent;
  }}
  #btn-terminer:active {{ transform: scale(0.98); }}
</style>
</head>
<body>
  <div id="topbar">
    <button id="btn-back" onclick="window.location.href='/'">← Retour</button>
    <span class="tournoi">{tournament_name}</span>
    <button id="btn-reload" onclick="reloadApp()" title="Recharger la dernière version">🔄</button>
  </div>
  <div id="conn-indicator" class="conn-ok"><span class="dot"></span><span id="conn-text">Connecté</span></div>
  <div id="moves-list"></div>
  <div id="footer">
    <button id="btn-terminer" onclick="confirmAllMoves()">✅ Mouvements terminés</button>
  </div>

<script>
{auth_redirect_script}
var moves = [];
var lastSignature = null;
var pollTimer = null;
var pollDelayMs = 4000;
var POLL_DELAY_MIN_MS = 4000;
var POLL_DELAY_MAX_MS = 20000;
var consecutiveFailures = 0;
var connState = 'ok';
var leavingPage = false;  // liste vidée : plus aucun sondage/rendu après ça

function setConnState(state) {{
  if (state === connState) return;
  connState = state;
  var el = document.getElementById('conn-indicator');
  var txt = document.getElementById('conn-text');
  if (!el || !txt) return;
  el.className = state === 'ok' ? 'conn-ok' : (state === 'reconnecting' ? 'conn-reconnecting' : 'conn-lost');
  txt.textContent = state === 'ok' ? 'Connecté' : (state === 'reconnecting' ? 'Reconnexion…' : 'Connexion perdue');
}}

function fetchWithTimeout(url, options, timeoutMs) {{
  var opts = options || {{}};
  var controller = (typeof AbortController !== 'undefined') ? new AbortController() : null;
  if (controller) opts.signal = controller.signal;
  var timer = controller ? setTimeout(function() {{ controller.abort(); }}, timeoutMs) : null;
  var clear = function() {{ if (timer) clearTimeout(timer); }};
  return fetch(url, opts).then(function(r) {{ clear(); return r; }}, function(e) {{ clear(); throw e; }});
}}

function scheduleNextPoll(ok) {{
  if (pollTimer) clearTimeout(pollTimer);
  pollDelayMs = ok ? POLL_DELAY_MIN_MS : Math.min(pollDelayMs * 1.5, POLL_DELAY_MAX_MS);
  pollTimer = setTimeout(loadMoves, pollDelayMs);
}}

function tryRecoverViaLobby() {{
  window.location.href = '/lobbylist';
}}

// Tri "naturel" par table de départ (demande du 2026-09-19) : un simple
// tri alphabétique mettrait "Table 10" avant "Table 2" — on extrait le
// nombre final du nom de table pour trier dans l'ordre attendu par le
// responsable qui parcourt la salle table par table.
function tableSortKey(name) {{
  if (!name) return [1, '', 0];
  var m = /^(.*?)(\\d+)\\s*$/.exec(name);
  if (m) return [0, m[1], parseInt(m[2], 10)];
  return [0, name, 0];
}}
function compareMoves(a, b) {{
  var ka = tableSortKey(a.old_table_name), kb = tableSortKey(b.old_table_name);
  if (ka[0] !== kb[0]) return ka[0] - kb[0];
  if (ka[1] !== kb[1]) return ka[1] < kb[1] ? -1 : 1;
  if (ka[2] !== kb[2]) return ka[2] - kb[2];
  return (a.old_seat || 0) - (b.old_seat || 0);
}}

// Tous les mouvements confirmés (dernier OK individuel, ou "Mouvements
// terminés" depuis cette page) : message bref puis retour automatique à
// l'accueil (demande du 2026-09-19) — le mécanisme normal de fin a déjà
// tout arrêté côté serveur à ce moment-là (alerte, clignotement,
// affichage Projo, chrono repris).
function showAllConfirmedThenGoHome() {{
  leavingPage = true;
  if (pollTimer) clearTimeout(pollTimer);
  document.getElementById('moves-list').innerHTML = '<div id="empty">Tous les mouvements sont confirmés.</div>';
  document.getElementById('footer').style.display = 'none';
  setTimeout(function() {{ window.location.href = '/'; }}, 1500);
}}

function loadMoves() {{
  if (leavingPage) return;
  fetchWithTimeout('/moves_pending', {{}}, 6000).then(function(r) {{
    if (r.status === 409) {{
      return r.json().then(function(data) {{
        if (data && data.tournament_gone) {{ setConnState('reconnecting'); tryRecoverViaLobby(); }}
        throw new Error('tournament_gone');
      }});
    }}
    if (r.status === 502) {{
      consecutiveFailures++;
      setConnState('reconnecting');
      if (consecutiveFailures >= 5) {{ tryRecoverViaLobby(); }}
      throw new Error('proxy_unreachable');
    }}
    if (!r.ok) {{ throw new Error('http_' + r.status); }}
    return r.json();
  }}).then(function(data) {{
    if (!Array.isArray(data)) {{ throw new Error('format_inattendu'); }}
    consecutiveFailures = 0;
    setConnState('ok');
    var sig = JSON.stringify(data);
    if (sig !== lastSignature) {{
      lastSignature = sig;
      moves = data;
      renderMoves();
    }}
    if (moves.length === 0) {{ showAllConfirmedThenGoHome(); return; }}
    scheduleNextPoll(true);
  }}).catch(function(e) {{
    if (e && e.message === 'auth_required') {{ setConnState('lost'); return; }}
    if (e && e.message === 'tournament_gone') {{ return; }}
    consecutiveFailures++;
    if (connState !== 'lost') {{
      setConnState(consecutiveFailures >= 2 ? 'lost' : 'reconnecting');
    }}
    scheduleNextPoll(false);
  }});
}}

function renderMoves() {{
  var list = document.getElementById('moves-list');
  list.innerHTML = '';
  if (moves.length === 0) {{
    list.innerHTML = '<div id="empty">Aucun mouvement en attente.</div>';
    return;
  }}
  moves.slice().sort(compareMoves).forEach(function(m) {{
    var row = document.createElement('div');
    row.className = 'move-row';
    var text = document.createElement('span');
    text.className = 'move-text';
    text.textContent = m.player_name + ' — ' + (m.old_table_name || '—') + ', siège ' + (m.old_seat || '—')
      + ' → ' + (m.new_table_name || '—') + ', siège ' + (m.new_seat || '—');
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn-ok';
    btn.textContent = 'OK';
    btn.addEventListener('click', function() {{ confirmMove(m.id, row); }});
    row.appendChild(text);
    row.appendChild(btn);
    list.appendChild(row);
  }});
}}

function confirmMove(moveId, row) {{
  fetchWithTimeout('/confirm_move', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{move_id: moveId}}),
  }}, 8000).then(function(r) {{
    if (r.status === 409) {{
      return r.json().then(function(data) {{
        if (data && data.tournament_gone) {{ setConnState('reconnecting'); tryRecoverViaLobby(); }}
        throw new Error('tournament_gone');
      }});
    }}
    if (!r.ok) throw new Error('erreur ' + r.status);
    return r.json();
  }}).then(function(data) {{
    if (!data.ok) {{
      window.alert(data.message || 'Confirmation refusée.');
      return;
    }}
    // Disparition IMMÉDIATE de cette ligne (demande explicite), sans
    // attendre le prochain sondage — les autres restent affichées.
    if (row && row.parentNode) {{ row.parentNode.removeChild(row); }}
    moves = moves.filter(function(m) {{ return m.id !== moveId; }});
    lastSignature = JSON.stringify(moves);
    if (data.all_done || moves.length === 0) {{
      showAllConfirmedThenGoHome();
      return;
    }}
    if (document.getElementById('moves-list').children.length === 0) {{ renderMoves(); }}
  }}).catch(function(e) {{
    if (e && e.message === 'auth_required') {{ setConnState('lost'); return; }}
    if (e && e.message === 'tournament_gone') {{ return; }}
    window.alert(
      'Échec : ' + (e && e.message ? e.message : 'réseau') + ' — vérifiez le wifi.\\n'
      + 'Vous pouvez retenter sans risque : cette confirmation n\\'a aucun effet en double.'
    );
  }});
}}

function confirmAllMoves() {{
  if (!window.confirm('Confirmer tous les mouvements encore affichés ?')) return;
  fetchWithTimeout('/action/terminer', {{ method: 'POST' }}, 8000).then(function(r) {{
    if (r.status === 409) {{
      return r.json().then(function(data) {{
        if (data && data.tournament_gone) {{ setConnState('reconnecting'); tryRecoverViaLobby(); }}
        throw new Error('tournament_gone');
      }});
    }}
    if (!r.ok) throw new Error('erreur ' + r.status);
    return r.json();
  }}).then(function() {{
    showAllConfirmedThenGoHome();
  }}).catch(function(e) {{
    if (e && e.message === 'auth_required') {{ setConnState('lost'); return; }}
    if (e && e.message === 'tournament_gone') {{ return; }}
    window.alert('Échec : ' + (e && e.message ? e.message : 'réseau') + ' — vérifiez le wifi.');
  }});
}}

loadMoves();
{reload_script}
</script>
{rebalance_widget}
</body>
</html>
"""


# Widget "Équilibrage des tables" (question "quel siège est actuellement
# grosse blinde ?" — version TEST, voir database.py: rebalance_tables /
# resolve_pending_rebalance) : inséré tel quel (voir {rebalance_widget})
# dans les TROIS pages du contrôle à distance (index, Éliminations,
# Photos), pas seulement celle des Éliminations — un rééquilibrage peut
# survenir à tout instant, quelle que soit la page ouverte sur le
# téléphone à ce moment-là. Sondé toutes les 2s via /rebalance_pending ;
# répondre poste sur /rebalance_answer. Pas d'association téléphone/table
# ni de mot de passe (comme le reste du contrôle à distance, voir
# docstring du module) : la PREMIÈRE RÉPONSE VALIDE traitée gagne (voir
# database.py: resolve_pending_rebalance) — une réponse invalide (siège
# obsolète, demande déjà remplacée...) ne consomme rien et ne l'emporte
# jamais sur une réponse valide arrivant ensuite. Les autres appareils
# voient simplement leur superposition disparaître au sondage suivant
# (voir pollRebalance), sans jamais afficher d'erreur pour ça.
_REBALANCE_WIDGET = """
<div id="rebalance-overlay" style="display:none; position:fixed; inset:0; z-index:900; background:rgba(0,0,0,.72); align-items:center; justify-content:center; padding:20px;">
  <div style="background:#10241a; border:2px solid #e8c468; border-radius:14px; padding:22px 20px; max-width:360px; width:100%; min-width:0; box-sizing:border-box; text-align:center; box-shadow:0 6px 24px rgba(0,0,0,.5);">
    <h2 style="color:#e8c468; font-size:16px; margin:0 0 10px; letter-spacing:.02em;">ÉQUILIBRAGE DES TABLES</h2>
    <p id="rebalance-table-msg" style="color:#f5efe0; font-size:15px; font-weight:700; margin:0 0 10px;"></p>
    <p style="color:#b9ad8f; font-size:14px; margin:0 0 4px;">Quel siège est actuellement grosse blinde ?</p>
    <p style="color:#8a7f66; font-size:12px; margin:0 0 14px;">Le joueur juste après changera de table.</p>
    <div id="rebalance-seats" style="display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:8px 10px; margin-bottom:16px;"></div>
    <button id="rebalance-skip" type="button" style="width:100%; padding:12px; border:none; border-radius:10px; background:#4a4a4a; color:#fff; font-size:14px; font-weight:700; -webkit-tap-highlight-color:transparent;">Continuer sans indiquer la BB</button>
  </div>
</div>
<script>
(function() {
  var shownId = null;
  function pollRebalance() {
    fetch('/rebalance_pending').then(function(r) { return r.json(); }).then(function(data) {
      var overlay = document.getElementById('rebalance-overlay');
      if (!data) {
        if (shownId !== null) { overlay.style.display = 'none'; shownId = null; }
        return;
      }
      if (data.request_id === shownId) return;
      shownId = data.request_id;
      document.getElementById('rebalance-table-msg').textContent = data.table_name + ' doit donner un joueur';
      var seatsDiv = document.getElementById('rebalance-seats');
      seatsDiv.innerHTML = '';
      var seatPlayers = data.seat_players || {};
      data.seats.forEach(function(s) {
        var b = document.createElement('button');
        b.type = 'button';
        // Nom du joueur assis à ce siège quand connu (voir seat_players,
        // /rebalance_pending) — sinon repli sur le seul numéro de siège,
        // comme avant cet ajout.
        var playerName = seatPlayers[s];
        b.textContent = playerName ? (playerName + ' [' + s + ']') : ('[' + s + ']');
        // 2 boutons par ligne (voir grid-template-columns du conteneur
        // ci-dessus) : width:100% + box-sizing:border-box pour que
        // chaque bouton remplisse exactement sa colonne sans jamais
        // déborder horizontalement, même avec un nom de joueur long
        // (white-space:normal + word-break autorisent alors un
        // retour à la ligne DANS le bouton plutôt qu'un débordement —
        // min-height, pas height, pour que le bouton grandisse tout
        // seul si le texte prend 2 lignes, tout en gardant une bonne
        // zone de clic même pour un texte court sur 1 seule ligne).
        b.style.cssText = 'width:100%; min-width:0; box-sizing:border-box; padding:12px 8px; border:none; border-radius:10px; background:#1f6b6b; color:#fff; font-size:15px; font-weight:700; line-height:1.25; min-height:48px; white-space:normal; word-break:break-word; text-align:center; -webkit-tap-highlight-color:transparent;';
        b.addEventListener('click', function() { answerRebalance(data.request_id, s); });
        seatsDiv.appendChild(b);
      });
      overlay.style.display = 'flex';
    }).catch(function() { /* réseau momentanément indisponible : le prochain sondage rattrapera */ });
  }
  function answerRebalance(requestId, seat) {
    // Masquée tout de suite (optimiste), sans attendre la réponse du
    // serveur : si la requête échoue (wifi), le prochain sondage la
    // réaffichera automatiquement tant que la demande est toujours en
    // attente côté PC — inutile de bloquer l'interface pour ça.
    document.getElementById('rebalance-overlay').style.display = 'none';
    shownId = null;
    fetch('/rebalance_answer', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({request_id: requestId, seat: seat})
    }).catch(function() { /* le prochain sondage rattrapera si besoin */ });
  }
  document.getElementById('rebalance-skip').addEventListener('click', function() {
    if (shownId !== null) answerRebalance(shownId, null);
  });
  pollRebalance();
  setInterval(pollRebalance, 2000);
})();
</script>
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


def _extract_selected_pid(cookie_header):
    """Valeur entière du cookie "selected_pid" dans `cookie_header`, ou
    None si absent/invalide — dernier "selected_pid=" du header
    l'emporte en cas de doublon (comportement HISTORIQUE, inchangé au
    caractère près : c'était l'analyse inline de resolve_current_pid
    avant le 2026-09-19, simplement extraite ici pour être réutilisée
    par resolve_proxy_port ci-dessous SANS dupliquer cette analyse —
    voir sa docstring pour pourquoi il en a besoin séparément)."""
    selected_pid = None
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if part.startswith("selected_pid="):
            try:
                selected_pid = int(part.split("=", 1)[1])
            except ValueError:
                selected_pid = None
    return selected_pid


def resolve_current_pid(cookie_header, own_pid, live_tournaments):
    """Pid du tournoi actuellement COURANT pour un téléphone donné —
    celui que /lobbylist doit marquer "(celui-ci)" (voir
    RemoteControlServer.start: _handle_lobbylist). Notion délibérément
    INDÉPENDANTE de `own_pid` (le tournoi qui héberge physiquement le
    port 8765, un simple relais réseau pour les autres — voir la
    docstring de RemoteControlServer) : avant ce correctif, une
    sélection absente ou invalide retombait silencieusement sur
    `own_pid`, confondant "détient le routeur" et "est le tournoi
    courant" — symptôme observé : un tournoi ouvert en premier (et
    devenu routeur) restait affiché "(celui-ci)" même après l'ouverture
    de tournois plus récents jamais sélectionnés depuis un téléphone.

    Priorité :
    1. le cookie "selected_pid" de CE téléphone (posé par
       /select_tournament), REVALIDÉ ici contre `live_tournaments` à
       CHAQUE appel (jamais un pid conservé sans vérifier qu'il
       correspond encore à un tournoi vivant et joignable — un
       processus fermé, y compris l'ancien routeur, est ainsi
       automatiquement écarté sans action explicite) ;
    2. à défaut (cookie absent, ou pid mort/invalide/inconnu), le
       tournoi le plus RÉCEMMENT ouvert parmi `live_tournaments` (voir
       open_windows.register: "registered_at") — jamais `own_pid` par
       défaut.
    `live_tournaments` : liste au format de open_windows.
    list_remote_tournaments(), passée par l'appelant plutôt que relue
    ici, pour ne jamais la relire deux fois inutilement dans le même
    traitement de requête.

    ATTENTION (demande du 2026-09-19) : cette fonction reste, à dessein,
    UNIQUEMENT un affichage ("(celui-ci)" dans le Lobby) — depuis ce
    correctif, elle n'est PLUS utilisée pour décider où router/relayer
    une requête de CONTENU (voir resolve_proxy_port ci-dessous, qui a sa
    propre logique dédiée pour cette décision-là, avec des conséquences
    de sécurité que ce simple affichage n'a pas)."""
    selected_pid = _extract_selected_pid(cookie_header)
    if selected_pid is not None and any(t["pid"] == selected_pid for t in live_tournaments):
        return selected_pid
    if not live_tournaments:
        return own_pid
    return max(live_tournaments, key=lambda t: t.get("registered_at", 0))["pid"]


class _SelectionVanished:
    """Sentinel renvoyé par resolve_proxy_port (demande du 2026-09-19,
    diagnostic du 2026-09-19 : une action destinée à un tournoi B fermé
    pouvait être exécutée sur un tournoi A resté ouvert) quand le
    téléphone a explicitement sélectionné un tournoi (cookie
    "selected_pid" présent) qui n'est PLUS dans le registre partagé.
    Distinct de None (qui signifie "sers/traite localement, cette
    requête EST pour ce tournoi-ci") et de tout port entier (relais vers
    un AUTRE tournoi encore vivant) : une classe dédiée plutôt qu'une
    chaîne ou -1 pour qu'aucune comparaison accidentelle (`==`) ne
    puisse jamais le confondre avec une vraie valeur de retour."""

    def __repr__(self):
        return "SELECTION_VANISHED"


SELECTION_VANISHED = _SelectionVanished()


# Budget TOTAL (jamais par thread) laissé à RemoteControlServer.stop()/
# try_reclaim_default_port() pour attendre la fin des threads de requêtes
# encore en cours avant de considérer l'arrêt terminé (demande du
# 2026-09-19, suite au crash natif reproductible de la suite complète —
# voir _ExclusiveThreadingHTTPServer.join_request_threads pour le
# mécanisme complet). Largement au-dessus des délais internes déjà
# bornés à 3s de App._remote_eliminate_request/_remote_confirm_move_
# request : une requête légitime en cours a tout le temps de se terminer
# normalement. Jamais indéfini : un client réellement figé (ex. corps de
# requête jamais envoyé en entier) ne peut donc jamais bloquer stop()
# au-delà de ce budget — voir tests/test_remote_control_server_stop_
# joins_threads.py::ServerStopNeverBlocksIndefinitelyTest.
_STOP_REQUEST_THREADS_TIMEOUT_SECONDS = 5.0


class _ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer utilisé PAR CE MODULE UNIQUEMENT (jamais un
    changement global de http.server) — correctif du 502 multi-tournois
    reproduit sur Windows le 2026-09-15 (voir tests/test_remote_control_
    windows_port_collision.py pour le diagnostic complet et sa
    reproduction).

    Suit aussi elle-même ses threads de requêtes (demande du 2026-09-19,
    correctif d'une course diagnostiquée par un crash natif reproductible
    de la suite complète — voir join_request_threads) : http.server.
    ThreadingHTTPServer pose `daemon_threads = True`, ce qui fait que
    socketserver._Threads.append() (utilisée par le join interne de
    ThreadingMixIn.server_close(), block_on_close=True jamais modifié)
    IGNORE SILENCIEUSEMENT chaque thread créé — `if thread.daemon: return`
    avant tout ajout. Le join que server_close() appelle bien s'exécute
    donc sur une liste qui n'a jamais rien contenu : RemoteControlServer.
    stop() pouvait ainsi rendre la main alors qu'un thread de requête
    tournait encore, capable d'exécuter du code (y compris appeler des
    fonctions dépatchées entre-temps par un test suivant) bien après.

    daemon_threads N'EST PAS modifié ici (resterait `True`, hérité de
    ThreadingHTTPServer) : le passer à `False` ferait qu'un thread
    réellement bloqué (client lent/figé) empêcherait le PROCESS entier de
    quitter — un risque strictement pire que la course corrigée. Le
    suivi ci-dessous est donc entièrement SÉPARÉ du mécanisme _Threads/
    daemon_threads de la bibliothèque standard, jamais mélangé avec lui.

    http.server.HTTPServer (dont hérite ThreadingHTTPServer) pose
    `allow_reuse_address = True`, donc SO_REUSEADDR avant chaque bind()
    (voir socketserver.TCPServer.server_bind). Sur POSIX (macOS/Linux),
    SO_REUSEADDR ne fait sauter QUE la contrainte TIME_WAIT : un bind()
    sur un port où un AUTRE socket écoute déjà ACTIVEMENT échoue
    toujours avec OSError — RemoteControlServer.start()/try_reclaim_
    default_port() (inchangés ci-dessous, toujours leur seule branche
    `except OSError` déjà existante) s'appuient entièrement sur cette
    garantie. C'EST AUSSI ce qui permet à try_reclaim_default_port() de
    refonctionner immédiatement après la fermeture d'un routeur ayant
    réellement servi des requêtes (connexions HTTP passées en TIME_WAIT
    sur ce même port local) : retirer purement et simplement SO_REUSEADDR
    sur POSIX réintroduirait ce vieux problème (bind() refusé pendant
    jusqu'à quelques minutes) — d'où l'importance de ne JAMAIS toucher à
    ce comportement sur POSIX (demande explicite du 2026-09-15).

    Sur Windows, en revanche, SO_REUSEADDR a un comportement radicalement
    différent (documenté par Microsoft) : il autorise PLUSIEURS sockets à
    bind() ET écouter SIMULTANÉMENT sur le MÊME port, sans jamais lever
    la moindre erreur — c'est la cause démontrée du 502 ("Ce tournoi
    n'est momentanément plus joignable") : deux RemoteControlServer
    distincts croyaient tous deux avoir obtenu le port 8765, et open_
    windows.update_remote_info (jamais modifié ici) enregistrait alors
    fidèlement cette valeur pour les deux.

    Correctif MINIMAL, sans toucher à l'architecture (routeur/relais/
    Lobby inchangés) : UNIQUEMENT sous Windows, remplace SO_REUSEADDR
    par SO_EXCLUSIVEADDRUSE (option Windows dédiée à exactement ce
    problème), qui restaure sous Windows la même garantie qu'en POSIX
    SANS reproduire son défaut (SO_EXCLUSIVEADDRUSE reste compatible
    avec un rebind légitime une fois le socket précédent réellement
    fermé — seul un bind() concurrent avec un socket encore actif est
    refusé). server_bind() est donc entièrement réécrit pour Windows
    (jamais un appel à super().server_bind(), qui poserait SO_REUSEADDR)
    — POSIX continue, lui, d'appeler super().server_bind() SANS AUCUNE
    modification, exactement comme avant ce correctif (voir tests/
    test_remote_control_windows_port_collision.py:
    DeuxServeursReelsPosixTest, qui passe sans changement). Les deux
    options ne doivent JAMAIS être posées ensemble sur le même socket
    (documentation Microsoft) : les deux branches ci-dessous sont donc
    mutuellement exclusives, jamais combinées."""

    def server_bind(self):
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            self.socket.bind(self.server_address)
            self.server_address = self.socket.getsockname()
        else:
            super().server_bind()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_threads = []

    def process_request(self, request, client_address):
        """Identique à ThreadingMixIn.process_request (classe mère),
        SAUF que le thread créé est TOUJOURS suivi ici — contrairement à
        self._threads (stdlib), qui ignore silencieusement les threads
        daemon (voir la docstring de cette classe). `reap()` retire au
        passage les threads déjà terminés, pour ne jamais laisser cette
        liste grossir indéfiniment sur une longue session."""
        self._request_threads = [t for t in self._request_threads if t.is_alive()]
        t = threading.Thread(target=self.process_request_thread, args=(request, client_address))
        t.daemon = self.daemon_threads
        self._request_threads.append(t)
        t.start()

    def join_request_threads(self, timeout):
        """Attend la fin des threads de requêtes actuellement suivis,
        avec un budget TOTAL borné (jamais par thread, jamais indéfini)
        — voir RemoteControlServer.stop()/try_reclaim_default_port(),
        appelés entre shutdown() (plus aucune NOUVELLE requête acceptée)
        et server_close() (ferme le socket d'écoute) : au moment précis
        où la liste des threads suivis est déjà définitive. Un thread qui
        n'a pas fini dans le budget imparti reste simplement suivi (et
        continue de tourner, daemon — voir la docstring de la classe) ;
        ce budget écoulé, cette méthode rend la main sans jamais attendre
        davantage."""
        deadline = time.monotonic() + timeout
        for t in list(self._request_threads):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            t.join(timeout=remaining)
        self._request_threads = [t for t in self._request_threads if t.is_alive()]


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
      "toggle_pause" / "niveau_precedent" / "niveau_suivant" /
      "tables_zoom_moins" / "tables_zoom_plus").
    - `get_players()` : renvoie la liste des joueurs actifs à afficher sur
      la page Éliminations — liste de dicts {id, name, table, seat,
      has_photo}.
    - `get_roster_players()` : renvoie TOUT le répertoire de joueurs
      habituels (pas seulement ceux actifs dans le tournoi en cours), pour
      la page Photos — liste de dicts {name, club, has_photo}, sans id
      numérique (voir on_upload_photo ci-dessous).
    - `on_eliminate(eliminated_id, eliminator_id, client_request_id)` :
      élimination décidée depuis la page Éliminations (eliminator_id peut
      être None). Appelé DIRECTEMENT sur le thread HTTP de la requête
      (voir ThreadingHTTPServer plus bas) — PAS le thread Tk — et doit
      renvoyer {"ok": bool, "message": str} : "ok" indique si
      l'élimination a bien eu lieu, "message" est affiché tel quel sur le
      téléphone si "ok" est faux (ex. refus PKO sans éliminateur désigné,
      demande du 2026-09-08) — jamais un échec silencieux.
      `client_request_id` (demande du 2026-09-19, "bétonner la
      communication téléphone <-> PC") : identifiant généré côté
      téléphone pour CETTE tentative, réutilisé tel quel en cas de
      retry — None si absent (ancien client, ou appelant qui n'en fournit
      pas, comportement historique inchangé). L'implémentation DOIT
      garantir l'idempotence pour un même identifiant (voir App._remote_
      eliminate_request dans main.py, seule implémentation réelle) :
      jamais un second effet métier pour deux appels avec le même
      client_request_id.
    - `get_clock_paused()` : True si le chrono est actuellement en pause
      — pour le petit bouton ON/OFF à côté de "Chronomètre".
    - `get_has_pending_moves()` : True s'il existe au moins un mouvement en
      attente dans l'onglet Mouvements de CE tournoi (voir App._tick,
      self.db.count_seat_moves() — même source que l'onglet lui-même,
      aucune logique séparée) — fait clignoter le bouton "📋 Afficher
      Mouvements" tant que c'est vrai.
    - `on_upload_photo(player_name, image_bytes)` : photo prise depuis la
      page Photos, à associer à ce joueur (identifié par NOM, pas par id
      — voir get_roster_players) dans le répertoire — renvoie
      (succès: bool, message: str) ; ce callback-ci, à la différence des
      autres, peut être appelé du thread du serveur SANS passer par une
      file d'attente thread-safe, s'il ne touche ni self.db (SQLite) ni
      Tkinter (voir App._remote_upload_photo, qui ne touche que de
      simples fichiers/JSON via player_photos.py).
    - `get_photo_image(player_name)` : renvoie (bytes, mime) pour la
      miniature affichée à côté du nom sur la page Photos, ou (None,
      None) si ce joueur n'a pas de photo — même remarque thread-safe que
      on_upload_photo (simple lecture de fichier).
    - `on_delete_photo(player_name)` : supprime la photo de ce joueur
      (bouton 🗑 de la page Photos) — renvoie (succès: bool, message:
      str), même remarque thread-safe.
    - `on_end_tournament()` : bouton "Fin de la partie" (tout en bas de la
      page principale) — appelé UNIQUEMENT après que ce module a déjà
      vérifié que le pid envoyé par le téléphone correspond à CE
      processus-ci (voir /end_tournament dans start()) ; ne prend aucun
      argument, ne fait que déposer la demande dans la file d'attente
      thread-safe existante (voir App._start_remote_control_if_enabled).
    - `get_pending_moves()` : renvoie la liste des mouvements ACTUELLEMENT
      en attente (demande du 2026-09-19, page "Mouvements" du contrôle à
      distance) — liste de dicts {id, player_name, old_table_name,
      old_seat, new_table_name, new_seat}, même source que get_has_
      pending_moves (self.db.get_seat_moves(), aucune logique séparée),
      tenue à jour depuis le thread principal (voir App._refresh_remote_
      moves_cache), jamais lue/écrite depuis ce thread-ci.
    - `on_confirm_move(move_id)` : confirmation INDIVIDUELLE d'un
      mouvement (bouton [OK] d'une ligne sur la page "Mouvements") —
      comme on_eliminate, ne fait que déposer la demande dans la file
      d'attente thread-safe (voir App._remote_confirm_move_request, seule
      implémentation réelle) : ce thread ne touche JAMAIS self.db ni
      Tkinter directement. Renvoie {"ok": bool, "all_done": bool} :
      "all_done" indique si c'était le DERNIER mouvement en attente (le
      téléphone affiche alors "Tous les mouvements sont confirmés" puis
      revient à l'accueil, voir _MOVES_PAGE) — dans ce cas, App._remote_
      confirm_move a déjà déclenché le même mécanisme de fin que le
      bouton "Mouvements terminés" (_finish_movement_alert), jamais une
      fin réimplémentée à part.
    - `get_dirto_permissions(dirto_name)` (Phase 4, "Sécurisation du
      Contrôle à distance", 2026-09-20) : renvoie l'ensemble (frozenset)
      des clés REMOTE_PERMISSION_* accordées à `dirto_name` POUR CE
      TOURNOI — frozenset() vide si aucune autorisation n'existe (voir
      Database.get_dirto_authorization, "aucune autorisation existante =
      aucune permission DIRTO"). Appelé UNIQUEMENT quand le propriétaire
      résolu de l'appareil (voir open_windows.get_remote_device_owner)
      est classé "DIRTO" au Répertoire (jamais pour un ADMIN, qui a
      accès total par construction) — même remarque thread-safe que
      get_players/get_pending_moves : DOIT provenir d'un cache tenu à
      jour par le thread principal (voir App._refresh_remote_dirto_
      permissions_cache dans main.py), jamais d'une lecture directe de
      self.db depuis ce thread-ci."""

    def __init__(self, on_word, get_tournament_name=None, get_players=None,
                 on_eliminate=None, get_clock_paused=None, on_upload_photo=None,
                 get_roster_players=None, get_photo_image=None, on_delete_photo=None,
                 get_pending_rebalance=None, on_rebalance_answer=None,
                 on_end_tournament=None, get_has_pending_moves=None,
                 get_pending_moves=None, on_confirm_move=None,
                 get_dirto_permissions=None,
                 port=DEFAULT_PORT):
        self.on_word = on_word
        self.get_tournament_name = get_tournament_name or (lambda: "Tournoi")
        self.get_players = get_players or (lambda: [])
        self.get_roster_players = get_roster_players or (lambda: [])
        self.on_eliminate = on_eliminate or (
            lambda eliminated_id, eliminator_id, client_request_id=None: {"ok": True, "message": ""}
        )
        self.get_clock_paused = get_clock_paused or (lambda: True)
        self.get_has_pending_moves = get_has_pending_moves or (lambda: False)
        self.on_upload_photo = on_upload_photo or (lambda player_name, image_bytes: (False, "Non disponible"))
        self.get_photo_image = get_photo_image or (lambda player_name: (None, None))
        self.on_delete_photo = on_delete_photo or (lambda player_name: (False, "Non disponible"))
        # Rééquilibrage simple : question "quel siège est grosse blinde ?"
        # (version TEST, voir database.py: rebalance_tables /
        # resolve_pending_rebalance). get_pending_rebalance() renvoie
        # None ou {request_id, table_name, seats} — sondé par TOUS les
        # téléphones (voir _REBALANCE_WIDGET) ; on_rebalance_answer(
        # request_id, seat) est appelé quand l'un d'eux répond (seat=None
        # pour "Continuer sans indiquer la BB").
        self.get_pending_rebalance = get_pending_rebalance or (lambda: None)
        self.on_rebalance_answer = on_rebalance_answer or (lambda request_id, seat: None)
        # Bouton "Fin de la partie" (tout en bas de la page principale) :
        # on_end_tournament() est appelé UNIQUEMENT après vérification, ici
        # même (voir /end_tournament dans start()), que le pid envoyé par
        # le téléphone correspond bien à CE processus-ci (os.getpid()) —
        # jamais transmis tel quel à l'appelant, pour qu'il n'ait besoin
        # d'aucune logique d'identification supplémentaire (voir
        # App._start_remote_control_if_enabled dans main.py).
        self.on_end_tournament = on_end_tournament or (lambda: None)
        self.get_pending_moves = get_pending_moves or (lambda: [])
        self.on_confirm_move = on_confirm_move or (lambda move_id: {"ok": True, "all_done": False})
        # Permissions DIRTO (Phase 4, "Sécurisation du Contrôle à
        # distance", 2026-09-20) : dirto_name -> frozenset de clés
        # REMOTE_PERMISSION_* accordées à CE DIRTO pour CE tournoi,
        # frozenset() vide si aucune autorisation n'existe (voir
        # Database.get_dirto_authorization — "aucune autorisation
        # existante = aucune permission DIRTO"). Comme get_players/
        # get_pending_moves, DOIT être alimenté depuis un cache tenu à
        # jour par le thread principal (voir App._refresh_remote_dirto_
        # permissions_cache dans main.py) — jamais une lecture directe
        # de self.db depuis ce thread-ci.
        self.get_dirto_permissions = get_dirto_permissions or (lambda dirto_name: frozenset())
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
        get_has_pending_moves = self.get_has_pending_moves
        on_upload_photo = self.on_upload_photo
        get_photo_image = self.get_photo_image
        on_delete_photo = self.on_delete_photo
        get_pending_rebalance = self.get_pending_rebalance
        on_rebalance_answer = self.on_rebalance_answer
        on_end_tournament = self.on_end_tournament
        get_pending_moves = self.get_pending_moves
        on_confirm_move = self.on_confirm_move
        get_dirto_permissions = self.get_dirto_permissions
        own_pid = os.getpid()

        def resolve_role(handler):
            """(role, permissions) pour la requête `handler` en cours —
            appelée UNIQUEMENT après authentification niveau 1 (garantie
            par l'appelant) ET après relais éventuel vers le tournoi
            réellement visé (voir resolve_proxy_port/_proxy juste
            au-dessus dans do_GET/do_POST) : une permission n'est donc
            jamais vérifiée contre un autre tournoi que celui qui va
            RÉELLEMENT traiter cette requête — si `_proxy` a relayé la
            requête, C'EST le processus cible qui exécute cette même
            fonction, avec SON PROPRE get_dirto_permissions (donc SA
            PROPRE base), jamais celui-ci (Phase 4, "Sécurisation du
            Contrôle à distance", 2026-09-20).

            role : "ADMIN" (accès total, y compris "Terminer le
            tournoi"), "DIRTO" (accès limité à `permissions`) ou "NONE"
            (appareil non lié à un propriétaire, OU propriétaire qui
            n'est plus classé ADMIN/DIRTO au Répertoire depuis l'octroi
            — traité comme non lié, JAMAIS comme son ancien rôle :
            roster.get_group est résolu EN DIRECT ici, à chaque requête,
            jamais mis en cache, même principe que le panneau
            "Propriétaire" de main.py). permissions : _ALL_PERMISSIONS
            pour ADMIN, l'ensemble accordé à ce DIRTO POUR CE TOURNOI
            pour DIRTO (frozenset vide si jamais autorisé ici), toujours
            frozenset() pour NONE."""
            browser_id = _parse_cookie(handler.headers.get("Cookie", ""), _BROWSER_ID_COOKIE_NAME)
            owner_name = open_windows.get_remote_device_owner(browser_id)
            if not owner_name:
                return "NONE", frozenset()
            group = roster.get_group(owner_name)
            if group == roster.ROSTER_GROUP_ADMIN:
                return "ADMIN", _ALL_PERMISSIONS
            if group == roster.ROSTER_GROUP_DIRTO:
                return "DIRTO", frozenset(get_dirto_permissions(owner_name) or ())
            return "NONE", frozenset()

        def resolve_proxy_port(handler):
            """Port du tournoi actuellement COURANT pour CE téléphone, s'il
            diffère de ce tournoi-ci. None si le tournoi courant EST ce
            tournoi-ci (traité localement) — y compris quand aucune
            sélection valide n'existe et que CE tournoi-ci s'avère être
            lui-même le plus récemment ouvert. SELECTION_VANISHED (demande
            du 2026-09-19) si CE téléphone a explicitement sélectionné un
            tournoi (cookie "selected_pid") qui n'est PLUS dans le
            registre partagé — voir la docstring de SELECTION_VANISHED.

            Priorité ABSOLUE à une sélection EXPLICITE encore présente
            dans le cookie, revalidée ICI (pas seulement dans resolve_
            current_pid, dont l'usage se limite désormais à l'affichage
            "(celui-ci)" du Lobby — voir sa docstring) : si son tournoi a
            disparu ET qu'un AUTRE tournoi est encore vivant, retourne
            SELECTION_VANISHED SANS JAMAIS consulter le repli "plus
            récemment ouvert" — c'est précisément ce repli silencieux
            qui permettait qu'une action destinée à un tournoi B fermé
            soit exécutée sur un tournoi A resté ouvert (diagnostic du
            2026-09-19, tests/test_remote_selected_tournament_vanished.
            py). Si en revanche le registre est totalement VIDE (aucun
            AUTRE tournoi vers lequel on aurait pu router par erreur),
            le repli historique "servi localement par ce process-ci"
            reste inoffensif et est conservé tel quel (non-régression
            explicite : tests/test_remote_control_reliability.py::
            ProxyUnreachableTest::test_tournoi_disparu_du_registre_ne_
            declenche_aucun_proxy). Le cas SANS sélection explicite
            (cookie absent/invalide) reste, lui, entièrement délégué à
            resolve_current_pid — comportement HISTORIQUE inchangé."""
            cookie_header = handler.headers.get("Cookie", "")
            live_tournaments = open_windows.list_remote_tournaments()

            selected_pid = _extract_selected_pid(cookie_header)
            if selected_pid is not None:
                if not any(t["pid"] == selected_pid for t in live_tournaments):
                    if live_tournaments:
                        return SELECTION_VANISHED
                    return None  # aucun autre survivant : servi localement, comme avant
                if selected_pid == own_pid:
                    return None
                for t in live_tournaments:
                    if t["pid"] == selected_pid:
                        return t["port"]
                return None  # inatteignable : déjà vérifié par le any() ci-dessus

            current_pid = resolve_current_pid(cookie_header, own_pid, live_tournaments)
            if current_pid == own_pid:
                return None
            for t in live_tournaments:
                if t["pid"] == current_pid:
                    return t["port"]
            return None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass  # pas de log console à chaque requête (bruyant)

            def _send_html(self, html, extra_headers=None, status=200):
                # extra_headers : liste de (nom, valeur) — PAS un dict,
                # justement pour pouvoir poser PLUSIEURS "Set-Cookie"
                # dans la même réponse (ex. rc_bid + rc_auth à la fois,
                # voir _handle_authenticate) : un dict ne peut porter
                # qu'une seule valeur par nom d'en-tête. status=200 par
                # défaut (comportement historique inchangé pour tous les
                # appelants existants) — 403 utilisé par Phase 4 pour une
                # page HTML de refus de permission (voir _FORBIDDEN_PAGE/
                # _NO_ACCESS_PAGE). Cache-Control: no-store (demande du
                # 2026-09-24, "synchronisation automatique permissions") :
                # sans cet en-tête, Safari iOS peut servir une copie mise
                # en cache de "/" même sur une actualisation MANUELLE —
                # symptôme observé en club ("le bon écran n'apparaît pas
                # systématiquement" après recharger la page) — cette page
                # reflète un rôle/des permissions qui peuvent changer à
                # tout moment côté Mac, jamais un contenu statique.
                body = html.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if extra_headers:
                    for name, value in extra_headers:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _send_json(self, obj, status=200, extra_headers=None):
                # extra_headers : voir _send_html — même convention
                # (liste de tuples), nécessaire ici aussi pour poser un
                # cookie sur une réponse JSON (ex. /authenticate,
                # /auth_status).
                body = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                if extra_headers:
                    for name, value in extra_headers:
                        self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def _send_permission_denied(self, method, path, role, is_page=False):
                """Refus explicite d'une route/action pour laquelle le
                rôle résolu (voir resolve_role, appelé par l'appelant)
                n'a pas la permission requise (Phase 4, "Sécurisation
                du Contrôle à distance", 2026-09-20) — JAMAIS un simple
                masquage HTML : c'est CE contrôle, et lui seul, qui
                empêche réellement l'action, que le bouton/lien
                correspondant ait été affiché ou non au téléphone (voir
                _strip_permission_sections, qui n'est qu'ergonomie).
                Journalisé comme les refus d'authentification niveau 1
                (voir _is_authenticated ci-dessous, même mécanisme
                log_remote_event déjà existant — pas un nouveau système
                de journalisation, la Phase 5 LOG n'est pas commencée
                ici). `is_page` : True pour une page HTML destinée à
                être NAVIGUÉE (GET /eliminate, /moves, /photos) — répond
                alors avec _FORBIDDEN_PAGE plutôt que du JSON, plus
                lisible en cas d'ouverture directe/marque-page périmé."""
                log_remote_event(
                    "permission_denied", method=method, path=path, role=role,
                    port=self.server.server_port,
                )
                if is_page:
                    self._send_html(
                        _FORBIDDEN_PAGE.format(message=_NO_PERMISSION_MESSAGE),
                        status=403,
                    )
                else:
                    self._send_json({"ok": False, "message": _NO_PERMISSION_MESSAGE}, status=403)

            def _client_ip(self):
                return self.client_address[0]

            def _browser_id(self):
                """(browser_id, is_new) — identifiant de navigateur
                (demande du 2026-09-09) : 128 bits aléatoires
                (secrets.token_hex), SANS aucune donnée personnelle, non
                dérivé de l'IP, non prévisible — sert uniquement à
                retrouver un appareil déjà (dés)approuvé (voir
                open_windows.register_device_attempt) et à clé
                l'anti-force-brute. `is_new` indique si CETTE réponse
                doit poser le cookie (absent, ou valeur qui ne
                ressemble pas à ce que ce serveur génère lui-même — un
                cookie forgé/tronqué est alors simplement remplacé, pas
                fait confiance)."""
                existing = _parse_cookie(self.headers.get("Cookie", ""), _BROWSER_ID_COOKIE_NAME)
                if existing and _BROWSER_ID_RE.fullmatch(existing):
                    return existing, False
                return secrets.token_hex(16), True

            def _is_authenticated(self):
                """LA vérification faite avant toute route sensible
                (voir do_GET/do_POST) : les deux cookies HttpOnly
                (rc_bid, rc_auth) doivent correspondre à un appareil
                TOUJOURS approuvé, avec un jeton valide pour la session
                EN COURS (voir open_windows.verify_device_session, qui
                vérifie les deux conditions requises)."""
                browser_id = _parse_cookie(self.headers.get("Cookie", ""), _BROWSER_ID_COOKIE_NAME)
                token = _parse_cookie(self.headers.get("Cookie", ""), _AUTH_COOKIE_NAME)
                return open_windows.verify_device_session(browser_id, token)

            def _handle_login(self):
                browser_id, is_new = self._browser_id()
                extra_headers = []
                if is_new:
                    extra_headers.append((
                        "Set-Cookie",
                        f"{_BROWSER_ID_COOKIE_NAME}={browser_id}; Path=/; HttpOnly; "
                        f"SameSite=Lax; Max-Age={_BROWSER_ID_COOKIE_MAX_AGE}",
                    ))
                self._send_html(_LOGIN_PAGE, extra_headers=extra_headers or None)

            def _handle_authenticate(self):
                """POST /authenticate — SEULE route qui compare un code
                saisi (demande du 2026-09-09 : 131261 passe forcément
                par ICI, comme le code réel, jamais de route ni de
                bypass séparé). Ordre STRICT, chacun avant le suivant :
                1) anti-bruteforce (avant même de lire le code, pour ne
                   jamais évaluer un code pendant un blocage actif) ;
                2) validité du code (échec -> compteur incrémenté,
                   réponse {"ok": false}, RIEN d'autre) ;
                3) succès -> compteurs remis à zéro, PUIS seulement :
                   approbation de l'appareil (voir open_windows.
                   register_device_attempt) — "approved" délivre
                   immédiatement un jeton de session (cookie rc_auth),
                   tout le reste (pending/nouveau/révoqué-redevenu-
                   pending) renvoie {"ok": true, "status": "pending"}
                   SANS aucun cookie rc_auth : le code était correct,
                   mais ça ne suffit plus à donner accès."""
                browser_id, browser_is_new = self._browser_id()
                ip = self._client_ip()
                extra_headers = []
                if browser_is_new:
                    extra_headers.append((
                        "Set-Cookie",
                        f"{_BROWSER_ID_COOKIE_NAME}={browser_id}; Path=/; HttpOnly; "
                        f"SameSite=Lax; Max-Age={_BROWSER_ID_COOKIE_MAX_AGE}",
                    ))
                blocked, remaining = open_windows.remote_auth_rate_limit_status(browser_id, ip)
                if blocked:
                    minutes = int(remaining // 60) + (1 if remaining % 60 else 0)
                    minutes = max(1, minutes)
                    self._send_json(
                        {"ok": False, "message": f"Trop de tentatives. Réessayez dans {minutes} min."},
                        status=429,
                        extra_headers=extra_headers or None,
                    )
                    return
                length = int(self.headers.get("Content-Length", 0) or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    data = json.loads(raw.decode("utf-8"))
                    code = data.get("code")
                except (ValueError, TypeError):
                    code = None
                if not isinstance(code, str) or not open_windows.verify_remote_code(code):
                    open_windows.record_remote_auth_failure(browser_id, ip)
                    self._send_json({"ok": False}, extra_headers=extra_headers or None)
                    return
                open_windows.record_remote_auth_success(browser_id, ip)
                device_status = open_windows.register_device_attempt(browser_id, ip)
                if device_status == "approved":
                    token = open_windows.get_or_mint_device_session_token(browser_id)
                    if token:
                        extra_headers.append((
                            "Set-Cookie",
                            f"{_AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; "
                            f"SameSite=Lax; Max-Age={_AUTH_COOKIE_MAX_AGE}",
                        ))
                    self._send_json({"ok": True, "status": "approved"}, extra_headers=extra_headers or None)
                else:
                    self._send_json({"ok": True, "status": "pending"}, extra_headers=extra_headers or None)

            def _handle_auth_status(self):
                """GET /auth_status — sondé toutes les 2s par /login tant
                qu'une demande est "pending" (voir _LOGIN_PAGE). Réponse
                STRICTEMENT limitée à {"status": "..."} (demande du
                2026-09-09, point 2 : aucune liste, aucun nom, aucune
                IP, aucun jeton, aucun session_id) — basée uniquement
                sur le browser_id du cookie courant, jamais sur un
                paramètre fourni par la requête. Pose le cookie rc_auth
                (via Set-Cookie, jamais dans le corps JSON) dès que
                l'approbation est détectée, pour que la redirection vers
                "/" qui suit trouve déjà une session valide."""
                browser_id = _parse_cookie(self.headers.get("Cookie", ""), _BROWSER_ID_COOKIE_NAME)
                status = open_windows.get_device_auth_status(browser_id)
                extra_headers = []
                if status == "approved" and browser_id:
                    token = open_windows.get_or_mint_device_session_token(browser_id)
                    if token:
                        extra_headers.append((
                            "Set-Cookie",
                            f"{_AUTH_COOKIE_NAME}={token}; Path=/; HttpOnly; "
                            f"SameSite=Lax; Max-Age={_AUTH_COOKIE_MAX_AGE}",
                        ))
                self._send_json({"status": status}, extra_headers=extra_headers or None)

            def _proxy(self, target_port):
                """Relaie telle quelle la requête en cours vers le VRAI
                port du tournoi choisi via le Lobby (127.0.0.1 — toujours
                la même machine, jamais le réseau externe) et renvoie au
                téléphone la réponse obtenue, sans qu'il n'ait jamais eu
                à connaître ce port lui-même.

                Le Cookie ("selected_pid", voir _handle_select_tournament)
                DOIT être relayé au process cible : celui-ci exécute le
                MÊME do_GET/do_POST, donc le MÊME resolve_proxy_port sur
                cette requête relayée — sans son cookie d'origine, il la
                voit comme "sans sélection" et retombe sur resolve_
                current_pid('', ...), qui peut désigner un troisième
                tournoi (celui le plus récemment ouvert) au lieu de
                conclure "c'est moi, sers localement" ; le tournoi
                sélectionné pouvait alors afficher (nom, état...) celui
                d'un AUTRE tournoi, et ce à chaque requête (donc y compris
                juste après 🔄 Actualiser, sans rapport avec un cache) —
                symptôme observé : le nom affiché sur l'écran principal ne
                reflétait pas le tournoi réellement sélectionné dès que
                celui-ci n'était ni le routeur (8765) ni, par coïncidence,
                déjà celui que resolve_current_pid aurait choisi par
                défaut sans cookie."""
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else None
                url = f"http://127.0.0.1:{target_port}{self.path}"
                req = urllib.request.Request(url, data=body, method=self.command)
                ctype = self.headers.get("Content-Type")
                if ctype:
                    req.add_header("Content-Type", ctype)
                cookie = self.headers.get("Cookie")
                if cookie:
                    req.add_header("Cookie", cookie)
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
                except (OSError, urllib.error.URLError) as e:
                    # Journalisé AVANT send_error (demande du 2026-09-19) :
                    # target_port suffit à savoir QUEL tournoi était visé
                    # sans avoir besoin de relire own_pid/le registre ici
                    # (déjà fait par resolve_proxy_port juste avant l'appel
                    # à _proxy — pas la peine de le refaire pour un simple
                    # log). Jamais de cookie/en-tête journalisé, juste le
                    # chemin, le port cible et le type d'erreur réseau.
                    log_remote_event(
                        "proxy_unreachable", method=self.command,
                        path=self.path.split("?", 1)[0], target_port=target_port,
                        error=type(e).__name__,
                    )
                    self.send_error(502, "Ce tournoi n'est momentanément plus joignable")

            def _send_tournament_gone(self, method, path):
                """Réponse pour resolve_proxy_port() is SELECTION_VANISHED
                (demande du 2026-09-19) : le tournoi EXPLICITEMENT
                sélectionné par CE téléphone (cookie "selected_pid") n'est
                plus dans le registre partagé — jamais un routage
                silencieux vers un AUTRE tournoi encore vivant (voir la
                docstring de resolve_proxy_port pour le risque exact que
                ceci corrige : une action destinée à ce tournoi disparu
                appliquée par erreur à un autre).

                409 (Conflict) plutôt que 404/410 : le CHEMIN demandé
                existe bel et bien quelque part, seule la ressource visée
                par LA SÉLECTION de ce téléphone a disparu — sémantique la
                plus proche disponible en HTTP standard pour "l'état côté
                client ne correspond plus à l'état réel côté serveur".
                `tournament_gone: true` : champ explicite pour que le
                JavaScript n'ait pas à interpréter le code HTTP lui-même
                (voir loadPlayers/confirmElimination, qui appellent
                tryRecoverViaLobby() immédiatement dans ce cas précis,
                sans attendre les échecs consécutifs prévus pour un 502
                transitoire — ce signal-ci est définitif, jamais une
                panne passagère)."""
                log_remote_event("selected_tournament_vanished", method=method, path=path)
                self._send_json(
                    {"ok": False, "message": "Ce tournoi n'est plus disponible.", "tournament_gone": True},
                    status=409,
                )

            def _handle_lobbylist(self):
                # X-Own-Pid (voir confirmEndTournament/waitForDifferent
                # TournamentThenReload côté client, dans _PAGE_TEMPLATE) :
                # le pid de CE process, celui qui répond VRAIMENT à cette
                # requête précise — jamais relayée vers un autre process
                # (voir do_GET, "/lobbylist" est toujours traitée
                # localement, avant même resolve_proxy_port). Permet au
                # téléphone de distinguer, après "Fin de la partie", une
                # réponse venant encore du tournoi en train de fermer
                # (même pid) d'une réponse venant d'un tournoi VRAIMENT
                # différent — sans quoi une navigation pouvait être
                # déclenchée sur la foi d'une dernière réponse du process
                # mourant, puis échouer si son port se refermait entre
                # temps (écran natif Safari "ERR_CONNECTION_FAILED").
                tournaments = open_windows.list_remote_tournaments()
                if not tournaments:
                    rows = "<p class=\"empty\">Aucun tournoi joignable pour l'instant.</p>"
                else:
                    # current_pid (voir resolve_current_pid) : PAS own_pid —
                    # "(celui-ci)" doit refléter le tournoi COURANT de CE
                    # téléphone (sa sélection, ou à défaut le plus
                    # récemment ouvert), jamais automatiquement celui qui
                    # héberge le routeur réseau (port 8765). Recalculé à
                    # chaque appel (donc à chaque 🔄 Actualiser), jamais
                    # mis en cache : reflète toujours l'état réellement à
                    # jour du registre partagé.
                    current_pid = resolve_current_pid(
                        self.headers.get("Cookie", ""), own_pid, tournaments
                    )
                    parts = []
                    for t in sorted(tournaments, key=lambda x: x["name"].lower()):
                        is_self = t["pid"] == current_pid
                        css_class = " class=\"current\"" if is_self else ""
                        label = _escape_html(t["name"]) + (" (celui-ci)" if is_self else "")
                        parts.append(
                            f'<button{css_class} '
                            f'onclick="window.location.href=\'/select_tournament?pid={t["pid"]}\'">{label}</button>'
                        )
                    rows = "\n".join(parts)
                self._send_html(
                    _LOBBY_PAGE.format(rows=rows, reload_script=_RELOAD_SCRIPT),
                    extra_headers=[("X-Own-Pid", str(own_pid))],
                )

            def _handle_select_tournament(self):
                from urllib.parse import parse_qs, urlparse
                query = parse_qs(urlparse(self.path).query)
                pid_values = query.get("pid")
                self.send_response(302)
                if pid_values:
                    try:
                        pid = int(pid_values[0])
                        self.send_header("Set-Cookie", f"selected_pid={pid}; Path=/")
                        # Permet à une éventuelle fenêtre "Lobby" ouverte
                        # sur le Mac (LobbyDialog, main.py) d'aligner sa
                        # sélection visuelle sur ce même tournoi (voir
                        # open_windows.get_phone_selected_pid) — simple
                        # écriture de fichier, ne touche ni self.db ni
                        # Tkinter, donc sans danger depuis ce thread (même
                        # remarque que on_upload_photo/get_photo_image).
                        # Ne change RIEN au mécanisme de proxy/routage
                        # existant ci-dessus (cookie selected_pid) : purement
                        # informatif pour ce seul usage visuel.
                        open_windows.set_phone_selected_pid(pid)
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

                # Authentification (demande du 2026-09-09) : traitée
                # avant TOUT le reste, y compris /lobbylist et le relais
                # vers un autre tournoi — voir _AUTH_EXEMPT_PATHS/_AUTH_
                # PAGE_PATHS et la docstring de ce module. /login et
                # /auth_status doivent rester joignables SANS être
                # authentifié (c'est justement leur rôle) ; tout le
                # reste exige une session valide ET un appareil
                # approuvé (voir Handler._is_authenticated), vérifié ICI
                # côté serveur, jamais seulement côté page/JS.
                if path == "/login":
                    self._handle_login()
                    return
                if path == "/auth_status":
                    self._handle_auth_status()
                    return
                if path not in _AUTH_EXEMPT_PATHS and not self._is_authenticated():
                    if path in _AUTH_PAGE_PATHS:
                        self.send_response(302)
                        self.send_header("Location", "/login")
                        self.end_headers()
                    else:
                        log_remote_event("auth_required", method="GET", path=path, port=self.server.server_port)
                        self._send_json({"ok": False, "message": "Authentification requise."}, status=401)
                    return

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
                if target_port is SELECTION_VANISHED:
                    self._send_tournament_gone("GET", path)
                    return
                if target_port is not None:
                    self._proxy(target_port)
                    return

                # Permissions DIRTO (Phase 4, "Sécurisation du Contrôle à
                # distance", 2026-09-20) — résolues ICI, jamais avant :
                # voir resolve_role, qui garantit que c'est TOUJOURS le
                # tournoi réellement servi par CE processus qui est
                # vérifié (le relais ci-dessus est déjà passé).
                role, permissions = resolve_role(self)

                if path in ("/", "/index.html"):
                    # "aucune autorisation existante pour ce tournoi =
                    # aucune permission" (règle Phase 3) reçoit ICI le
                    # même traitement qu'un appareil non lié : les deux
                    # cas signifient concrètement la même chose pour le
                    # responsable ("rien n'est utilisable, voyez un
                    # ADMIN"), un seul message clair plutôt que deux
                    # présentations différentes pour un même résultat.
                    if role == "NONE" or (role == "DIRTO" and not permissions):
                        self._send_html(_NO_ACCESS_PAGE.format(
                            tournament_name=_escape_html(get_name()),
                            message=_NO_PERMISSION_MESSAGE,
                            reload_script=_RELOAD_SCRIPT,
                            auth_redirect_script=_AUTH_REDIRECT_SCRIPT,
                        ))
                        return
                    tournaments = open_windows.list_remote_tournaments()
                    lobby_button = (
                        '<button id="btn-lobby" onclick="window.location.href=\'/lobbylist\'">🏛 Lobby</button>'
                        if len(tournaments) > 1 else ""
                    )
                    page_html = _PAGE_TEMPLATE.format(
                        tournament_name=_escape_html(get_name()),
                        # tournament_name_json : même nom que ci-dessus,
                        # mais échappé pour être injecté tel quel comme
                        # littéral JS (voir confirmEndTournament) — celui
                        # du tournoi RÉELLEMENT servi ici (voir le
                        # correctif Cookie de _proxy : sans lui, un
                        # tournoi sélectionné via un relais pouvait
                        # afficher/confirmer par erreur le nom d'un
                        # AUTRE tournoi).
                        tournament_name_json=json.dumps(get_name()),
                        lobby_button=lobby_button,
                        app_version=version.APP_VERSION,
                        reload_script=_RELOAD_SCRIPT,
                        rebalance_widget=_REBALANCE_WIDGET,
                        auth_redirect_script=_AUTH_REDIRECT_SCRIPT,
                        own_pid=own_pid,
                    )
                    # "Fin de la partie" : jamais pour un DIRTO, quelles
                    # que soient ses permissions (voir requirement 8) —
                    # retiré ici pour tout rôle qui n'est pas ADMIN.
                    # Ergonomie uniquement (voir _strip_admin_only_
                    # sections) : /end_tournament refuse de toute façon
                    # tout appelant non-ADMIN, y compris un appel direct.
                    page_html = _strip_admin_only_sections(page_html, is_admin=(role == "ADMIN"))
                    if role == "DIRTO":
                        page_html = _strip_permission_sections(page_html, permissions)
                    self._send_html(page_html)
                    return

                if path == "/photo_image":
                    # Réponse de refus SPÉCIFIQUE (403 nu, pas de corps) :
                    # cette route renvoie normalement des octets d'image,
                    # jamais du JSON/HTML (voir _send_permission_denied,
                    # pensée pour les deux autres cas).
                    if _GET_ROUTE_PERMISSION["/photo_image"] not in permissions:
                        self.send_error(403)
                        return
                elif path in _GET_ROUTE_PERMISSION:
                    if _GET_ROUTE_PERMISSION[path] not in permissions:
                        is_page = path in ("/eliminate", "/eliminate.html", "/photos", "/photos.html", "/moves", "/moves.html")
                        self._send_permission_denied("GET", path, role, is_page=is_page)
                        return
                elif path == "/clock_state":
                    if not (permissions & _CLOCK_STATE_PERMISSIONS):
                        self._send_permission_denied("GET", path, role)
                        return

                if path in ("/eliminate", "/eliminate.html"):
                    self._send_html(_ELIMINATE_PAGE.format(
                        tournament_name=_escape_html(get_name()),
                        reload_script=_RELOAD_SCRIPT,
                        rebalance_widget=_REBALANCE_WIDGET,
                        auth_redirect_script=_AUTH_REDIRECT_SCRIPT,
                    ))
                elif path in ("/photos", "/photos.html"):
                    self._send_html(_PHOTOS_PAGE.format(
                        tournament_name=_escape_html(get_name()),
                        reload_script=_RELOAD_SCRIPT,
                        rebalance_widget=_REBALANCE_WIDGET,
                        auth_redirect_script=_AUTH_REDIRECT_SCRIPT,
                    ))
                elif path in ("/moves", "/moves.html"):
                    self._send_html(_MOVES_PAGE.format(
                        tournament_name=_escape_html(get_name()),
                        reload_script=_RELOAD_SCRIPT,
                        rebalance_widget=_REBALANCE_WIDGET,
                        auth_redirect_script=_AUTH_REDIRECT_SCRIPT,
                    ))
                elif path == "/players":
                    self._send_json(get_players())
                elif path == "/moves_pending":
                    # Mouvements ACTUELLEMENT en attente (demande du
                    # 2026-09-19) — même principe que /players : simple
                    # lecture d'un cache tenu à jour par le thread
                    # principal (voir get_pending_moves), jamais self.db
                    # touché depuis ce thread-ci.
                    self._send_json(get_pending_moves())
                elif path == "/roster_players":
                    self._send_json(get_roster_players())
                elif path == "/photo_image":
                    # Permission déjà vérifiée ci-dessus (send_error(403)
                    # si "photos" manquante) — la permission requise est
                    # dans _GET_ROUTE_PERMISSION comme les autres routes
                    # "photos", seule la FORME de la réponse de refus
                    # diffère (403 nu plutôt que JSON/HTML, cette route
                    # renvoyant normalement des octets d'image).
                    #
                    # Miniature affichée à côté du nom sur la page Photos
                    # (voir _PHOTOS_PAGE) : identifié par NOM, comme
                    # /upload_photo et /delete_photo — le répertoire n'a
                    # pas d'id numérique.
                    from urllib.parse import parse_qs, urlparse
                    query = parse_qs(urlparse(self.path).query)
                    name_values = query.get("name")
                    if not name_values or not name_values[0].strip():
                        self.send_error(404)
                        return
                    image_bytes, mime = get_photo_image(name_values[0].strip())
                    if image_bytes is None:
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", mime)
                    self.send_header("Content-Length", str(len(image_bytes)))
                    self.end_headers()
                    self.wfile.write(image_bytes)
                elif path == "/clock_state":
                    # has_pending_moves inséré ici (pas un endpoint séparé) :
                    # réutilise le sondage toutes les 3s déjà en place pour
                    # le bouton ON/OFF du chrono, voir refreshClockState()
                    # côté page — clignotement du bouton "Afficher
                    # Mouvements" (voir _PAGE_TEMPLATE), propre à CE
                    # tournoi/processus (get_has_pending_moves, jamais
                    # partagé entre tournois différents).
                    self._send_json({
                        "paused": bool(get_clock_paused()),
                        "has_pending_moves": bool(get_has_pending_moves()),
                    })
                elif path == "/permission_state":
                    # Sondage léger (Phase 4bis, "synchronisation
                    # automatique permissions", 2026-09-24 — voir
                    # _AUTH_REDIRECT_SCRIPT côté client, sondé toutes les
                    # 3s) : accessible à TOUT appareil authentifié
                    # niveau 1, JAMAIS gardée par une permission (`role`/
                    # `permissions` déjà résolus plus haut par resolve_
                    # role, contre CE tournoi précisément — voir sa
                    # docstring) — c'est justement le rôle NONE, ou un
                    # DIRTO qui vient de tout perdre, qui doit pouvoir
                    # détecter son propre état ici. Ne fait jamais rien
                    # d'autre que RENVOYER l'état actuel : aucune
                    # mutation, aucun effet de bord.
                    self._send_json({
                        "role": role,
                        "permissions": sorted(permissions),
                    })
                elif path == "/rebalance_pending":
                    # Sondé toutes les 2s par TOUS les téléphones, sur
                    # toutes les pages (voir _REBALANCE_WIDGET) : renvoie
                    # null s'il n'y a aucune question "grosse blinde" en
                    # attente en ce moment.
                    pending = get_pending_rebalance()
                    if pending is None:
                        self._send_json(None)
                    else:
                        # seat_players : {siège: nom du joueur actuellement
                        # assis là} pour LA SEULE table concernée (pending
                        # ["table_name"]) — enrichit l'affichage (le
                        # téléphone montre "Alice [3]" plutôt qu'un simple
                        # numéro de siège), sans rien changer au calcul :
                        # simple lecture de get_players() (déjà exposé pour
                        # la page Éliminations), jointe ici uniquement pour
                        # cette réponse JSON. Absent de la requête si le nom
                        # n'a pas pu être retrouvé (siège occupé mais
                        # joueur introuvable dans le cache, cas limite) —
                        # le téléphone affiche alors juste le numéro de
                        # siège, comme avant cet ajout.
                        seat_players = {
                            p["seat"]: p["name"] for p in get_players()
                            if p.get("table") == pending["table_name"] and p.get("seat") in pending["seats"]
                        }
                        self._send_json({
                            "request_id": pending["request_id"],
                            "table_name": pending["table_name"],
                            "seats": pending["seats"],
                            "seat_players": seat_players,
                        })
                else:
                    self.send_error(404)

            def do_POST(self):
                path = self.path.split("?", 1)[0]

                # /authenticate est la SEULE route POST accessible sans
                # authentification (voir _AUTH_EXEMPT_PATHS et la
                # docstring de _handle_authenticate) — jamais relayée
                # vers un autre tournoi non plus : l'état d'authenti-
                # fication est de toute façon partagé entre tous les
                # tournois de la session via open_windows, peu importe
                # quel processus traite cette requête précise.
                if path == "/authenticate":
                    self._handle_authenticate()
                    return
                if not self._is_authenticated():
                    log_remote_event("auth_required", method="POST", path=path, port=self.server.server_port)
                    self._send_json({"ok": False, "message": "Authentification requise."}, status=401)
                    return

                target_port = resolve_proxy_port(self)
                if target_port is SELECTION_VANISHED:
                    self._send_tournament_gone("POST", path)
                    return
                if target_port is not None:
                    self._proxy(target_port)
                    return

                # Permissions DIRTO (Phase 4, "Sécurisation du Contrôle à
                # distance", 2026-09-20) — voir resolve_role et son
                # équivalent dans do_GET : résolues ICI, après le relais
                # éventuel ci-dessus, donc toujours contre le tournoi
                # réellement visé par CE processus.
                role, permissions = resolve_role(self)

                if path.startswith("/action/"):
                    action = path[len("/action/"):]
                    if action not in _VALID_ACTIONS:
                        self.send_error(400, "Action inconnue")
                        return
                    # "elimination" (bouton "⏸ Joueurs") : ADMIN uniquement,
                    # jamais par appartenance à `permissions` — voir
                    # _ADMIN_ONLY_ACTIONS (même traitement que /end_tournament,
                    # corrigé le 2026-09-22).
                    if action in _ADMIN_ONLY_ACTIONS:
                        if role != "ADMIN":
                            self._send_permission_denied("POST", path, role)
                            return
                    elif _ACTION_PERMISSION[action] not in permissions:
                        self._send_permission_denied("POST", path, role)
                        return
                    on_word(action)
                    self._send_json({"ok": True})
                elif path == "/eliminate":
                    if _PERM_ELIMINATIONS not in permissions:
                        self._send_permission_denied("POST", path, role)
                        return
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        data = json.loads(raw.decode("utf-8"))
                        eliminated_id = int(data["eliminated_id"])
                        eliminator_raw = data.get("eliminator_id")
                        eliminator_id = int(eliminator_raw) if eliminator_raw not in (None, "") else None
                        # request_id (demande du 2026-09-19) : identifiant
                        # généré côté téléphone pour CETTE tentative
                        # d'élimination, réutilisé tel quel par lui en cas
                        # de retry — voir App._remote_eliminate_request
                        # (main.py), seul endroit qui garantit réellement
                        # l'idempotence. Optionnel : un ancien client (ou
                        # un appel de test) qui n'en fournit pas garde le
                        # comportement historique, jamais de dédoublonnage
                        # imposé. Chaîne brève et bornée (jamais confiée
                        # telle quelle à une requête SQL ou un chemin de
                        # fichier) : simple clé de dict côté Python.
                        client_request_id = data.get("request_id")
                        if client_request_id is not None:
                            client_request_id = str(client_request_id)[:64] or None
                    except (ValueError, KeyError, TypeError):
                        self.send_error(400, "Requête invalide")
                        return
                    # on_eliminate renvoie {"ok": bool, "message": str}
                    # (demande du 2026-09-08) : renvoyé tel quel au
                    # téléphone, qui affiche "message" si "ok" est faux
                    # (ex. refus PKO sans éliminateur désigné) — jamais un
                    # échec silencieux, voir sa docstring plus haut.
                    result = on_eliminate(eliminated_id, eliminator_id, client_request_id)
                    self._send_json(result)
                elif path == "/confirm_move":
                    # Confirmation INDIVIDUELLE d'un mouvement (bouton
                    # [OK] d'une ligne, voir _MOVES_PAGE) — comme
                    # /eliminate, ne fait que déposer la demande dans la
                    # file d'attente thread-safe (voir on_confirm_move/
                    # App._remote_confirm_move_request) : ce thread ne
                    # touche jamais self.db. Idempotent par nature
                    # (DELETE d'une ligne déjà supprimée ne fait rien) :
                    # jamais besoin d'un request_id de dédoublonnage
                    # comme pour /eliminate.
                    if _PERM_MOVES not in permissions:
                        self._send_permission_denied("POST", path, role)
                        return
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        data = json.loads(raw.decode("utf-8"))
                        move_id = int(data["move_id"])
                    except (ValueError, KeyError, TypeError):
                        self.send_error(400, "Requête invalide")
                        return
                    result = on_confirm_move(move_id)
                    self._send_json(result)
                elif path == "/upload_photo":
                    if _PERM_PHOTOS not in permissions:
                        self._send_permission_denied("POST", path, role)
                        return
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
                elif path == "/delete_photo":
                    if _PERM_PHOTOS not in permissions:
                        self._send_permission_denied("POST", path, role)
                        return
                    from urllib.parse import parse_qs, urlparse
                    query = parse_qs(urlparse(self.path).query)
                    player_name_values = query.get("player_name")
                    if not player_name_values or not player_name_values[0].strip():
                        self.send_error(400, "Requête invalide")
                        return
                    ok, message = on_delete_photo(player_name_values[0].strip())
                    self._send_json({"ok": ok, "message": message})
                elif path == "/rebalance_answer":
                    # Réponse à la question "quel siège est grosse
                    # blinde ?" — comme /eliminate, ne fait que déposer la
                    # réponse dans la file d'attente thread-safe côté
                    # appelant (voir on_rebalance_answer/voice_command_
                    # queue) : ce thread ne touche JAMAIS self.db ni
                    # Tkinter directement. `seat` absent/null/vide =
                    # "Continuer sans indiquer la BB". La PREMIÈRE RÉPONSE
                    # VALIDE traitée (PC ou téléphone) à une demande donnée
                    # gagne ; une réponse invalide (siège obsolète, demande
                    # déjà remplacée...) ne consomme rien, et toute réponse
                    # supplémentaire à la MÊME demande (même request_id)
                    # une fois celle-ci résolue est ignorée proprement —
                    # cette validation/consommation a lieu côté thread
                    # principal (voir database.py: resolve_pending_
                    # rebalance) — jamais ici, pour rester sans accès à
                    # self.db.
                    if _PERM_REBALANCE not in permissions:
                        self._send_permission_denied("POST", path, role)
                        return
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        data = json.loads(raw.decode("utf-8"))
                        request_id = str(data["request_id"])
                        seat_raw = data.get("seat")
                        seat = int(seat_raw) if seat_raw not in (None, "") else None
                    except (ValueError, KeyError, TypeError):
                        self.send_error(400, "Requête invalide")
                        return
                    on_rebalance_answer(request_id, seat)
                    self._send_json({"ok": True})
                elif path == "/end_tournament":
                    # Bouton "Fin de la partie" (tout en bas de la page
                    # principale) : ferme proprement CE tournoi-ci
                    # UNIQUEMENT — jamais un autre tournoi ouvert sur cette
                    # même machine, jamais brutal (voir App._remote_end_
                    # tournament dans main.py, qui réutilise _on_close()).
                    #
                    # Vérification cruciale, faite ICI (avant même de
                    # déposer quoi que ce soit dans la file d'attente) :
                    # `pid` envoyé par le téléphone doit correspondre à
                    # own_pid (le processus qui traite RÉELLEMENT cette
                    # requête, une fois le relais Lobby éventuel déjà
                    # résolu — voir resolve_proxy_port/_proxy plus haut).
                    # Ce `pid` a été capturé côté téléphone au chargement
                    # de la page (voir OWN_PID dans _PAGE_TEMPLATE), donc
                    # par le processus qui l'a alors RENDUE — le même que
                    # own_pid ici tant que ce tournoi est resté ouvert et
                    # sélectionné entre-temps. S'il ne correspond plus
                    # (ex : le tournoi visé a déjà été fermé par un autre
                    # moyen entre le chargement de la page et ce clic, et
                    # cette requête retombe donc, faute de relais possible,
                    # sur un AUTRE tournoi resté ouvert), on refuse net —
                    # jamais fermer ce tournoi-ci à la place d'un autre
                    # sur la seule foi d'un identifiant devenu obsolète.
                    #
                    # ADMIN uniquement (requirement 8, Phase 4) : JAMAIS
                    # accordable à un DIRTO, quelles que soient ses
                    # permissions — "end_tournament" n'existe même pas
                    # dans REMOTE_PERMISSION_LABELS (voir database.py),
                    # donc `permissions` ne peut structurellement jamais
                    # le contenir ; ce contrôle sur `role` est un second
                    # filet explicite, jamais retiré même si un futur bug
                    # ailleurs venait à peupler `permissions` par erreur.
                    if role != "ADMIN":
                        self._send_permission_denied("POST", path, role)
                        return
                    length = int(self.headers.get("Content-Length", 0) or 0)
                    raw = self.rfile.read(length) if length else b"{}"
                    try:
                        data = json.loads(raw.decode("utf-8"))
                        requested_pid = int(data["pid"])
                    except (ValueError, KeyError, TypeError):
                        self.send_error(400, "Requête invalide")
                        return
                    if requested_pid != own_pid:
                        self._send_json({
                            "ok": False,
                            "message": "Ce tournoi n'est plus disponible ici. Rechargez la page.",
                        })
                        return
                    # other_tournaments_remain : calculé AVANT on_end_tournament()
                    # (qui ne fait que déposer la demande dans la file — la
                    # fermeture réelle est asynchrone, ce process reste donc
                    # inscrit au registre le temps de ce calcul) — dit à la
                    # page cliente (voir confirmEndTournament) si elle doit
                    # tenter de revenir au Lobby (un autre tournoi existe
                    # encore, potentiellement joignable dans l'instant qui
                    # suit via _maybe_reclaim_default_remote_port si CE
                    # tournoi-ci tenait le port 8765 — voir main.py) ou
                    # afficher directement un message de fin propre (plus
                    # aucun tournoi nulle part).
                    other_remain = any(
                        t["pid"] != own_pid for t in open_windows.list_remote_tournaments()
                    )
                    on_end_tournament()
                    self._send_json({"ok": True, "other_tournaments_remain": other_remain})
                else:
                    self.send_error(404)

        try:
            self._httpd = _ExclusiveThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        except OSError:
            # Port déjà pris par un autre tournoi/processus (voir docstring
            # de la classe) : on prend un port libre quelconque à la place
            # plutôt que d'abandonner — ce tournoi reste joignable depuis
            # le téléphone via le Lobby (voir open_windows.
            # update_remote_info, appelé par l'appelant juste après ce
            # start()), même s'il n'est pas celui que le téléphone
            # contacte directement.
            self._httpd = _ExclusiveThreadingHTTPServer(("0.0.0.0", 0), Handler)
        self.port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        if self._httpd is not None:
            self._httpd.shutdown()
            # Attend (budget borné, voir _STOP_REQUEST_THREADS_TIMEOUT_
            # SECONDS) la fin des threads de requêtes déjà en cours AVANT
            # server_close() — demande du 2026-09-19, correctif de la
            # course qui laissait de tels threads survivre indéfiniment
            # au retour de stop() (voir _ExclusiveThreadingHTTPServer.
            # join_request_threads pour le mécanisme complet).
            self._httpd.join_request_threads(_STOP_REQUEST_THREADS_TIMEOUT_SECONDS)
            self._httpd.server_close()
            self._httpd = None
        self._thread = None

    def try_reclaim_default_port(self):
        """Bascule ce serveur (déjà démarré sur un port quelconque, voir
        start() : c'est arrivé parce que DEFAULT_PORT était pris par un
        AUTRE tournoi au démarrage) sur DEFAULT_PORT (8765) si ce port
        est redevenu libre entre-temps — typiquement parce que le
        tournoi qui l'occupait vient de se fermer via "Fin de la
        partie" (voir App._maybe_reclaim_default_remote_port, appelé
        depuis _tick sur TOUS les tournois encore ouverts : celui qui
        s'en aperçoit et réussit le premier le récupère, sans protocole
        de négociation entre processus — juste une nouvelle tentative de
        bind()).

        Ne fait RIEN si déjà sur DEFAULT_PORT, ou si un autre processus
        a gagné la course entre-temps (bind() échoue alors avec
        OSError, silencieusement — un prochain appel réessaiera). Ne
        risque JAMAIS de se retrouver sans aucun port : le nouveau
        serveur (sur 8765) n'est adopté qu'une fois son bind() réussi,
        l'ancien (sur l'ancien port) n'est arrêté qu'ENSUITE — jamais
        l'inverse. Le téléphone (qui ne parle jamais qu'à 8765) peut
        ainsi retrouver directement CE tournoi, sans passer par le
        Lobby d'un tournoi qui n'existe plus. Renvoie True si la
        bascule a eu lieu."""
        if not self.is_running or self.port == DEFAULT_PORT:
            return False
        try:
            new_httpd = _ExclusiveThreadingHTTPServer(("0.0.0.0", DEFAULT_PORT), self._httpd.RequestHandlerClass)
        except OSError:
            return False  # toujours pris (par un autre tournoi, ou perdu la course) : on garde notre port actuel
        old_httpd = self._httpd
        old_httpd.shutdown()
        # Même correctif que stop() ci-dessus (demande du 2026-09-19) :
        # attend la fin des threads de requêtes de l'ANCIEN serveur avant
        # de fermer son socket, budget borné identique.
        old_httpd.join_request_threads(_STOP_REQUEST_THREADS_TIMEOUT_SECONDS)
        old_httpd.server_close()
        self._httpd = new_httpd
        self.port = DEFAULT_PORT
        self._thread = threading.Thread(target=new_httpd.serve_forever, daemon=True)
        self._thread.start()
        return True

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
