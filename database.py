# -*- coding: utf-8 -*-
"""
Couche d'accès aux données pour le gestionnaire de tournoi de poker.
Toutes les données d'un tournoi sont stockées dans un seul fichier SQLite.
"""
import re
import sqlite3
import time
import math
import os
import glob
import json
import shutil
import random
import uuid
from fractions import Fraction

import export_prefs

# =====================================================================
# Export PDF : petit utilitaire partagé par tous les export_*_pdf
# ci-dessous (Joueurs, Primes, Résultats, Gains, Synthèse par période).
# Utilise fpdf2 (police cœur "Helvetica", jeu de caractères latin-1) :
# _pdf_text() remplace les quelques symboles hors de ce jeu (tiret
# cadratin, euro) qui apparaissent dans nos en-têtes de colonnes, pour
# éviter une erreur de rendu — les exports CSV/Excel restent en Unicode
# complet, seul le PDF est concerné par cette petite simplification.
# =====================================================================

def _pdf_text(value):
    if value is None:
        return ""
    return str(value).replace("—", "-").replace("€", "EUR")


# =====================================================================
# Format français des dates (JJ/MM/AAAA) : toutes les dates/heures sont
# stockées en interne au format ISO (AAAA-MM-JJ [HH:MM:SS]), pratique
# pour le tri et les comparaisons (voir get_tournament_date,
# _read_tournament_date_ro...). Ces deux fonctions ne servent qu'à
# l'AFFICHAGE (colonnes des onglets, exports) : ne jamais les utiliser
# pour stocker ou comparer des dates.
# =====================================================================

def format_date_fr(iso_date):
    """Convertit une date "AAAA-MM-JJ" en "JJ/MM/AAAA". Renvoie la valeur
    telle quelle si elle est vide ou ne correspond pas au format attendu."""
    if not iso_date:
        return iso_date
    try:
        y, m, d = iso_date.split("-")
        if len(y) == 4 and len(m) == 2 and len(d) == 2:
            return f"{d}/{m}/{y}"
    except (ValueError, AttributeError):
        pass
    return iso_date


def format_datetime_fr(iso_dt):
    """Convertit "AAAA-MM-JJ HH:MM:SS" en "JJ/MM/AAAA HH:MM:SS". Renvoie la
    valeur telle quelle si elle est vide ou ne correspond pas au format
    attendu."""
    if not iso_dt:
        return iso_dt
    try:
        date_part, time_part = iso_dt.split(" ", 1)
        formatted_date = format_date_fr(date_part)
        if formatted_date == date_part:
            return iso_dt
        return f"{formatted_date} {time_part}"
    except (ValueError, AttributeError):
        return iso_dt


def _pdf_fit_font_size(pdf, texts, col_width, bold=False, max_size=9, min_size=5):
    """Plus grande taille de police (entre `min_size` et `max_size`) à
    laquelle chacun de `texts` tient dans une colonne de largeur
    `col_width` (avec 2mm de marge) — évite que des en-têtes/valeurs longs
    ne débordent sur la colonne suivante quand il y a beaucoup de
    colonnes. Laisse la police active sur ce choix en sortie."""
    style = "B" if bold else ""
    for size in range(max_size, min_size - 1, -1):
        pdf.set_font("Helvetica", style, size)
        if all(pdf.get_string_width(t) <= col_width - 2 for t in texts):
            return size
    pdf.set_font("Helvetica", style, min_size)
    return min_size


def _write_pdf_table(path, title, subtitle_lines, headers, rows):
    """Génère un PDF simple (titre, sous-titres, puis un tableau) à partir
    de lignes déjà calculées (mêmes valeurs que pour les exports CSV/
    Excel). `subtitle_lines` : liste de lignes de texte optionnelles sous
    le titre (ex : entrées/prize pool). Paysage automatique au-delà de 6
    colonnes, pour laisser assez de place à chacune. La taille de police
    des en-têtes et des valeurs s'ajuste automatiquement (voir
    _pdf_fit_font_size) pour ne jamais déborder d'une colonne."""
    from fpdf import FPDF

    orientation = "L" if len(headers) > 6 else "P"
    pdf = FPDF(orientation=orientation, unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _pdf_text(title), border=0)
    pdf.ln(10)
    if subtitle_lines:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(90, 90, 90)
        for line in subtitle_lines:
            pdf.cell(0, 6, _pdf_text(line), border=0)
            pdf.ln(6)
        pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    avail_width = pdf.w - pdf.l_margin - pdf.r_margin
    col_width = avail_width / max(1, len(headers))
    row_height = 7

    header_texts = [_pdf_text(h) for h in headers]
    _pdf_fit_font_size(pdf, header_texts, col_width, bold=True)
    pdf.set_fill_color(31, 78, 36)
    pdf.set_text_color(255, 255, 255)
    for h in header_texts:
        pdf.cell(col_width, row_height + 1, h, border=1, align="C", fill=True)
    pdf.ln(row_height + 1)

    body_texts = [_pdf_text(c) for row in rows for c in row] or [""]
    _pdf_fit_font_size(pdf, body_texts, col_width, bold=False)
    pdf.set_text_color(0, 0, 0)
    fill_toggle = False
    for row in rows:
        if fill_toggle:
            pdf.set_fill_color(247, 241, 227)
        else:
            pdf.set_fill_color(255, 255, 255)
        for cell in row:
            pdf.cell(col_width, row_height, _pdf_text(cell), border=1, align="C", fill=True)
        pdf.ln(row_height)
        fill_toggle = not fill_toggle

    pdf.output(path)
    return path


SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tables_pk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    max_seats INTEGER NOT NULL DEFAULT 9,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    buyin_count INTEGER NOT NULL DEFAULT 1,
    rebuy_count INTEGER NOT NULL DEFAULT 0,
    addon_count INTEGER NOT NULL DEFAULT 0,
    chips INTEGER NOT NULL DEFAULT 0,
    table_id INTEGER,
    seat INTEGER,
    status TEXT NOT NULL DEFAULT 'active',   -- active | eliminated | withdrawn
    place INTEGER,
    elim_time TEXT,
    bounty INTEGER NOT NULL DEFAULT 0,       -- prime actuellement portée par ce joueur
    bounty_won INTEGER NOT NULL DEFAULT 0,   -- cumul des primes empochées (en cash)
    kills INTEGER NOT NULL DEFAULT 0,        -- nb de joueurs éliminés par ce joueur (prime de bounty en points)
    club TEXT NOT NULL DEFAULT '',           -- club du joueur POUR CE TOURNOI (copié du répertoire à l'ajout, voir roster.py)
    FOREIGN KEY(table_id) REFERENCES tables_pk(id)
);

CREATE TABLE IF NOT EXISTS blind_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level_order INTEGER NOT NULL,
    small_blind INTEGER NOT NULL,
    big_blind INTEGER NOT NULL,
    ante INTEGER NOT NULL DEFAULT 0,
    duration_minutes INTEGER NOT NULL,
    is_break INTEGER NOT NULL DEFAULT 0,
    break_label TEXT
);

CREATE TABLE IF NOT EXISTS payout_structure (
    place INTEGER PRIMARY KEY,
    percentage REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    old_table_name TEXT,
    old_seat INTEGER,
    new_table_name TEXT,
    new_seat INTEGER,
    moved_at TEXT NOT NULL,
    -- Raison du déplacement (demande du 2026-09-10, architecture de
    -- rééquilibrage) : voir MOVE_REASON_* plus bas — permet à l'onglet
    -- Mouvements d'expliquer POURQUOI un joueur a été déplacé
    -- (contrainte de capacité, fusion de table, équilibrage automatique,
    -- ou choix guidé par la grosse blinde). Chaîne vide pour tout
    -- mouvement archivé par une version antérieure à cette colonne (voir
    -- _migrate) : affiché comme "Équilibrage" générique, jamais une
    -- valeur inventée a posteriori.
    reason TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS bounty_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eliminated_name TEXT NOT NULL,
    eliminator_name TEXT,
    amount_won INTEGER NOT NULL,
    added_to_eliminator_bounty INTEGER NOT NULL DEFAULT 0,
    event_time TEXT NOT NULL,
    -- 'elimination' (défaut, une élimination normale) ou
    -- 'victory_collect' (clôture de la bounty finale du vainqueur en fin
    -- de tournoi PKO, voir Database._close_out_winner_bounty — jamais une
    -- élimination supplémentaire, eliminator_name reste NULL dans ce cas).
    event_type TEXT NOT NULL DEFAULT 'elimination'
);

-- Autorisations DIRTO du Contrôle à distance (Phase 3, "Sécurisation du
-- Contrôle à distance", 2026-09-20) : PAR TOURNOI (ce fichier .tournoi
-- précis), jamais globales — voir REMOTE_PERMISSION_LABELS plus bas
-- pour la liste des fonctions accordables et Database.set_dirto_
-- authorization/get_dirto_authorization/list_dirto_authorizations/
-- clear_dirto_authorization pour les opérations. dirto_name en clé
-- PRIMAIRE : au plus UNE autorisation par DIRTO pour CE tournoi (une
-- réattribution REMPLACE, ne s'ajoute jamais). CREATE TABLE IF NOT
-- EXISTS (comme le reste de ce schéma, exécuté à CHAQUE ouverture, voir
-- Database.__init__) : un ancien fichier .tournoi qui n'a jamais connu
-- cette table l'obtient automatiquement, vide, à la prochaine ouverture
-- — "aucune autorisation existante" en est donc la conséquence directe,
-- jamais un cas spécial à coder.
CREATE TABLE IF NOT EXISTS remote_authorizations (
    dirto_name TEXT PRIMARY KEY,
    admin_name TEXT NOT NULL,
    permissions TEXT NOT NULL DEFAULT '[]',  -- JSON, liste de clés REMOTE_PERMISSION_*
    granted_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "tournament_name": "Nouveau tournoi",
    "buyin_amount": "50",
    "rebuy_amount": "50",
    "addon_amount": "50",
    "starting_chips": "10000",
    "rebuy_chips": "10000",
    "addon_chips": "10000",
    "max_seats_per_table": "9",
    "min_players_per_table": "4",
    "break_duration_minutes": "15",
    "movement_signal_duration_ms": "300",
    "highlight_duration_minutes": "5",
    "rake_percent": "0",
    # Interrupteur général des primes (demande du 2026-09-09, voir
    # Database._primes_enabled) : "1" = calcule présence/assiduité/
    # classement/bounty classique/PKO normalement (comportement
    # historique, compatibilité des anciens fichiers sans cette clé —
    # voir _init_defaults/INSERT OR IGNORE) ; "0" = aucun calcul, aucune
    # mutation bounty/bounty_won/kills, tableau Primes vide — jamais
    # juste masqué. Valeur figée pour CE tournoi par main.py au moment
    # opportun (voir _sync_primes_enabled_pref/_clock_resume) ; ne
    # jamais modifier ce réglage directement en base sans passer par ce
    # mécanisme (verrouillage multi-tournois, voir main.py).
    "primes_enabled": "1",
    "bounty_amount": "0",
    "pko_mode": "0",
    "pko_cash_percent": "50",
    "current_level_order": "1",
    "level_start_epoch": "0",
    "is_paused": "1",
    "paused_accum_seconds": "0",
    "clock_started": "0",
    "tournament_date": "",  # AAAA-MM-JJ, fixée à la création (voir get_tournament_date)
    "tournament_start_epoch": "0",  # fixé au tout premier "Démarrer" (voir App._clock_resume)
    "tournament_end_epoch": "0",  # fixé quand il ne reste plus qu'1 joueur actif (voir eliminate_player)
}


# Système de points de classement (demande du 2026-09-10, remplace le
# champ "Montant de la prime de classement en points"/ranking_bonus_points
# — voir Database.resolve_ranking_formula pour la règle de compatibilité
# qui préserve exactement les anciens fichiers). Un seul réglage PAR
# TOURNOI (ranking_formula), jamais dans PERSISTED_KEYS (voir
# tournament_prefs.py) : chaque tournoi garde son propre choix, jamais
# hérité du précédent.
RANKING_FORMULA_NONE = "none"
RANKING_FORMULA_CURRENT = "current"
RANKING_FORMULA_PROGRESSIVE = "progressive"
RANKING_FORMULA_TOURNOIS_CPC = "tournois_cpc"
RANKING_FORMULA_SITNGO_CPC = "sitngo_cpc"

# Libellés affichés dans la liste déroulante de l'onglet Paramètres (voir
# main.py: _build_settings_tab) — ordre exact demandé (2026-09-20) : Aucun,
# Classique, Progressive, Tournois CPC, Sit & Go CPC. dict Python : ordre
# d'insertion préservé (garanti depuis Python 3.7), donc .values() respecte
# cet ordre — AUCUNE autre partie du code ne dépend d'une position
# numérique (Combobox readonly peuplé directement par .values(), résolution
# par comparaison de libellé, jamais par index).
RANKING_FORMULA_LABELS = {
    RANKING_FORMULA_NONE: "Aucun",
    RANKING_FORMULA_CURRENT: "Classique",
    RANKING_FORMULA_PROGRESSIVE: "Progressive",
    RANKING_FORMULA_TOURNOIS_CPC: "Tournois CPC",
    RANKING_FORMULA_SITNGO_CPC: "Sit & Go CPC",
}


# =====================================================================
# Permissions DIRTO du Contrôle à distance (Phase 3, "Sécurisation du
# Contrôle à distance", 2026-09-20) — voir la table remote_authorizations
# ci-dessus (PAR TOURNOI) et Database.set_dirto_authorization/get_dirto_
# authorization/list_dirto_authorizations/clear_dirto_authorization.
#
# Chaque clé représente une VRAIE fonction utilisateur du téléphone
# (jamais une route HTTP isolée) — inventaire exhaustif établi avant
# codage (voir l'échange du 2026-09-20) : plusieurs routes techniques
# sont regroupées sous UNE seule permission compréhensible, exactement
# comme un utilisateur les perçoit sur son téléphone :
#   - "eliminations" : page Éliminations (glisser-déposer) + liste des
#     joueurs qu'elle affiche (/eliminate, /players) ;
#   - "tables" : Plan de tables + ses boutons de zoom (/action/tables,
#     /action/tables_zoom_moins, /action/tables_zoom_plus) ;
#   - "moves" : page Mouvements, sa liste, la confirmation individuelle
#     et "Mouvements terminés" (/moves, /moves_pending, /confirm_move,
#     /action/terminer, /action/mouvements(_bas/_haut)) ;
#   - "clock" : pause/reprise du chronomètre (/action/chronometre,
#     /action/toggle_pause) ;
#   - "levels" : niveau précédent/suivant (/action/niveau_precedent,
#     /action/niveau_suivant) ;
#   - "photos" : page Photos, prise/import/suppression (/photos,
#     /upload_photo, /delete_photo, /photo_image, /roster_players) ;
#   - "rebalance" : réponse "quel siège est grosse blinde ?"
#     (/rebalance_pending, /rebalance_answer).
#
# "Terminer le tournoi" (/end_tournament) est DÉLIBÉRÉMENT ABSENT de ce
# dict — décision explicite et non négociable de l'utilisateur : cette
# fonction ne doit JAMAIS apparaître dans la liste des permissions
# accordables à un DIRTO, réservée aux ADMIN (accès total, hors de ce
# mécanisme de permissions). Ce n'est pas seulement une case décochée
# par défaut : c'est l'ABSENCE de toute clé "end_tournament" ici qui
# rend structurellement impossible de l'accorder — voir set_dirto_
# authorization, qui filtre toute clé absente de ce dict (donc aussi
# "end_tournament" si jamais présentée par erreur) avant d'écrire en
# base, en plus de l'UI qui ne la propose jamais.
#
# dict Python (ordre d'insertion préservé, comme RANKING_FORMULA_LABELS
# ci-dessus) : c'est cet ordre qui peuple la liste à cocher de l'onglet
# Paramètres (voir main.py).
REMOTE_PERMISSION_ELIMINATIONS = "eliminations"
REMOTE_PERMISSION_TABLES = "tables"
REMOTE_PERMISSION_MOVES = "moves"
REMOTE_PERMISSION_CLOCK = "clock"
REMOTE_PERMISSION_LEVELS = "levels"
REMOTE_PERMISSION_PHOTOS = "photos"
REMOTE_PERMISSION_REBALANCE = "rebalance"

REMOTE_PERMISSION_LABELS = {
    REMOTE_PERMISSION_ELIMINATIONS: "Gérer les éliminations",
    REMOTE_PERMISSION_TABLES: "Plan de tables",
    REMOTE_PERMISSION_MOVES: "Afficher Mouvements",
    REMOTE_PERMISSION_CLOCK: "Chronomètre (pause / reprise)",
    REMOTE_PERMISSION_LEVELS: "Changer de niveau (blindes)",
    REMOTE_PERMISSION_PHOTOS: "Photos des joueurs",
    REMOTE_PERMISSION_REBALANCE: "Répondre au rééquilibrage (UTG)",
}


def ranking_points(place, n_players, formula=RANKING_FORMULA_NONE):
    """Valeur en points de la prime de classement pour un rang `place`
    parmi `n_players` joueurs au total. `formula` : une des 5 valeurs de
    RANKING_FORMULA_LABELS —
    - "none" : aucun point (0), quel que soit le rang ;
    - "current" ("Classique") : 100×√N/P — favorise les premières places ;
    - "progressive" : 100×√N/√P — récompense davantage la régularité,
      écart entre les premières places plus faible que "Classique" ;
    - "tournois_cpc" : voir ranking_points_table (barème à somme fixe
      N×1000, méthode des plus grands restes — CE rang seul ne peut pas
      être calculé isolément, contrairement aux 3 formules ci-dessus :
      cette branche délègue donc à ranking_points_table, qui recalcule
      le barème COMPLET à chaque appel — acceptable pour un appel
      isolé/un test, jamais dans une boucle par joueur, voir
      get_ranking_bonuses qui appelle directement ranking_points_table
      une seule fois par tournoi) ;
    - "sitngo_cpc" : 1000 + 100×(N+1) - 200×P — chaque joueur apporte
      1000 points au total distribué, places espacées de 200 points
      (ex. N=7 : 1600/1400/1200/1000/800/600/400, somme = 7000 = N×1000).

    Remplace, depuis le 2026-09-10, l'ancien paramètre `flat_value`
    (valeur fixe manuelle, réglage ranking_bonus_points) — voir
    Database.resolve_ranking_formula pour la règle de compatibilité qui
    préserve EXACTEMENT le comportement des anciens fichiers utilisant
    encore ce réglage (jamais recalculé par une formule ici : la valeur
    fixe historique est appliquée directement par l'appelant, sans
    passer par cette fonction — voir get_ranking_bonuses)."""
    if not place or place <= 0 or n_players <= 0:
        return 0
    if formula == RANKING_FORMULA_CURRENT:
        return round(100 * math.sqrt(n_players) / place)
    if formula == RANKING_FORMULA_PROGRESSIVE:
        return round(100 * math.sqrt(n_players) / math.sqrt(place))
    if formula == RANKING_FORMULA_TOURNOIS_CPC:
        if place > n_players:
            return 0
        return ranking_points_table(n_players, formula)[place - 1]
    if formula == RANKING_FORMULA_SITNGO_CPC:
        # Arithmétique entière exacte (pas de round() nécessaire ni
        # souhaitable : 100×(N+1) et 200×P sont toujours des entiers).
        return 1000 + 100 * (n_players + 1) - 200 * place
    return 0  # "none", ou toute valeur inconnue/future : aucun point.


def ranking_points_table(n_players, formula=RANKING_FORMULA_NONE):
    """Barème COMPLET des points de classement d'un tournoi de
    `n_players` joueurs, sous forme de liste de `n_players` entiers
    (index 0 = place 1, ..., index n_players-1 = place n_players) — 0 si
    n_players <= 0.

    Pour "current"/"progressive"/"sitngo_cpc"/"none" : simple wrapper
    de ranking_points() pour chaque place — mathématiquement identique
    à des appels individuels, aucun changement de résultat (ces 3
    formules calculent chaque place indépendamment des autres).

    Pour "tournois_cpc" (demande du 2026-09-20, règle définitive du
    club — voir aussi la docstring de ranking_points), CALCULE le
    barème entier en une seule fois : contrairement aux formules
    ci-dessus, la méthode des plus grands restes exige de connaître la
    répartition COMPLÈTE des valeurs théoriques avant de savoir quelles
    places reçoivent le point de complément — un rang ne peut donc
    jamais être déterminé isolément pour cette formule.

    Formule théorique (avant arrondi), pour chaque place r de 1 à N :
        P(r,N) = 50 + 950×N × [0,12 × 0,88^(r-1)] / [1 - 0,88^N]
    Appliquée SANS EXCEPTION ni changement de formule, y compris hors
    du barème documenté 15-45 joueurs (demande explicite du club :
    "aucune limitation, aucun blocage et aucun changement de formule
    hors de la plage 15-45").

    Arrondi par la méthode des PLUS GRANDS RESTES (jamais un round()
    indépendant par place, qui ne garantirait pas une somme exacte) :
    1. partie entière de chaque valeur théorique ;
    2. points manquants pour atteindre EXACTEMENT N×1000 = somme des
       valeurs théoriques (démontré algébriquement : la série
       géométrique se simplifie exactement à 950N + 50N = 1000N,
       quel que soit N) ;
    3. ces points manquants sont distribués un par un aux places ayant
       les plus grands restes décimaux, départagées par la place r la
       plus petite en cas d'égalité stricte du reste (règle explicite
       du club).

    Utilise fractions.Fraction (0,88 = 22/25 est rationnel, donc toute
    la formule l'est) plutôt que float/Decimal : exactitude totale, ni
    imprécision flottante sur les restes, ni risque qu'un départage
    soit faussé par une erreur d'arrondi — la somme finale vaut donc
    PROUVABLEMENT exactement n_players × 1000, jamais approximativement
    (assertion de sécurité ci-dessous, qui ne devrait jamais se
    déclencher d'après la preuve algébrique ci-dessus)."""
    if n_players <= 0:
        return []
    if formula != RANKING_FORMULA_TOURNOIS_CPC:
        return [ranking_points(r, n_players, formula) for r in range(1, n_players + 1)]

    q = Fraction(88, 100)
    one_minus_q = 1 - q
    denom = 1 - q ** n_players
    raw_values = [
        50 + Fraction(950 * n_players) * (one_minus_q * q ** (r - 1)) / denom
        for r in range(1, n_players + 1)
    ]
    floors = [int(v) for v in raw_values]  # int() tronque vers 0 : = floor() pour une valeur positive
    remainders = [v - f for v, f in zip(raw_values, floors)]
    target = n_players * 1000
    missing = target - sum(floors)
    # Plus grands restes d'abord ; départage par r le plus petit
    # (index i = r-1, donc tri croissant sur i) en cas d'égalité
    # stricte du reste — règle explicite du club.
    order = sorted(range(n_players), key=lambda i: (-remainders[i], i))
    table = list(floors)
    for i in order[:missing]:
        table[i] += 1
    assert sum(table) == target, (
        f"Tournois CPC : somme des points de classement != N×1000 "
        f"pour N={n_players} ({sum(table)} != {target})"
    )
    return table


def bounty_unit_value(n_players, flat_value=0):
    """Valeur en points d'un bounty (un joueur éliminé) dans un tournoi de
    `n_players` joueurs au total. Si `flat_value` (réglage manuel, non nul)
    est fourni, il est utilisé tel quel pour tout le monde ; sinon on
    applique la formule 10×√N — plus le champ est grand, plus éliminer un
    adversaire y est statistiquement difficile, donc plus le bounty
    rapporte."""
    if flat_value:
        return flat_value
    if n_players <= 0:
        return 0
    return round(10 * math.sqrt(n_players))


# Convention "table finale" (voir rebalance_tables) : une fois qu'il ne
# reste plus que ce nombre de joueurs actifs ou moins, ils sont toujours
# regroupés sur UNE SEULE table, quitte à dépasser ponctuellement le
# réglage "Nombre de sièges par table" s'il est plus petit (ex. 8) — un
# vrai tournoi ne scinde jamais les tout derniers joueurs entre deux
# tables alors qu'ils tiendraient sur une seule table finale.
FINAL_TABLE_MAX_SEATS = 10

# Raisons possibles d'un déplacement de joueur (colonne seat_moves.reason,
# voir rebalance_tables/resolve_pending_rebalance) — architecture de
# rééquilibrage validée le 2026-09-10, qui distingue strictement trois
# notions : capacité normale (jamais négociable), exception de vraie
# table finale (voir FINAL_TABLE_MAX_SEATS ci-dessus, ne produit pas de
# déplacement en tant que telle), et équilibrage sportif (grosse blinde
# ou automatique). Chaque déplacement réellement effectué porte
# EXACTEMENT une de ces raisons, jamais plusieurs à la fois (voir
# rebalance_tables : la dernière raison qui s'applique à un joueur donné
# gagne si plusieurs mécanismes le déplacent au cours du même appel —
# c'est la raison de son emplacement FINAL qui est affichée, pas
# l'historique intermédiaire de cet appel).
MOVE_REASON_STRUCTURAL = "structurel"        # mise en conformité obligatoire de capacité (siège le plus élevé)
MOVE_REASON_TABLE_CLOSURE = "fermeture_table"  # fusion/fermeture de table (cassage, répartition aléatoire)
MOVE_REASON_AUTO_BALANCE = "equilibrage_auto"  # équilibrage historique automatique (guidage BB désactivé, ou Phase 1)
MOVE_REASON_BB_GUIDED = "equilibrage_bb"      # réponse "quel siège est la grosse blinde" reçue
MOVE_REASON_BB_SKIPPED = "continuer_sans_bb"  # "Continuer sans indiquer la BB" (téléphone ou bouton Mac)
# Mouvement de retour généré par une annulation d'élimination (demande du
# 2026-09-17, voir undo_last_elimination) : un joueur revient à sa table/
# siège d'avant l'élimination annulée — distingué des autres raisons pour
# rester compréhensible dans l'onglet Mouvements.
MOVE_REASON_ELIMINATION_UNDO = "annulation_elimination"

# Libellés humains (français), utilisés par main.py (onglet Mouvements) —
# regroupés ici plutôt que dans main.py pour rester à côté des constantes
# qu'ils décrivent. Chaîne vide (mouvement archivé avant l'ajout de cette
# colonne, voir _migrate) -> repli générique "Équilibrage".
MOVE_REASON_LABELS = {
    MOVE_REASON_STRUCTURAL: "Structurel (capacité)",
    MOVE_REASON_TABLE_CLOSURE: "Fusion/fermeture de table",
    MOVE_REASON_AUTO_BALANCE: "Équilibrage automatique",
    MOVE_REASON_BB_GUIDED: "UTG (guidé)",
    MOVE_REASON_BB_SKIPPED: "Continuer sans désigner le joueur",
    MOVE_REASON_ELIMINATION_UNDO: "Annulation d'élimination",
    "": "Équilibrage",
}


def _comparable_undo_state(state):
    """Sous-ensemble d'un état renvoyé par Database._player_undo_state,
    utilisé UNIQUEMENT pour la comparaison stricte "l'état a-t-il changé
    depuis cette élimination ?" de undo_last_elimination — exclut
    délibérément `elim_time` (demande du 2026-09-17, "Timeout pour
    Annuler Eliminer") : ce champ est désormais lu en LIVE pour calculer
    le délai écoulé (voir Database.undo_last_elimination_available), sa
    valeur exacte au moment de l'élimination n'ayant par ailleurs aucune
    incidence sur la sécurité de la restauration table/siège/primes
    elle-même. `elim_time` reste néanmoins conservé tel quel dans
    l'instantané et bien RESTAURÉ normalement (voir le bloc de
    restauration de undo_last_elimination, qui utilise l'état complet,
    jamais cette version filtrée)."""
    if state is None:
        return None
    return {k: v for k, v in state.items() if k != "elim_time"}


def _defensive_integrity_log_path():
    d = os.path.join(os.path.expanduser("~"), ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "crash.log")


def _log_defensive_relocation(message):
    """Trace, dans le même journal que les plantages (~/.poker_tournament/
    crash.log, voir main.py: _log_exception/_crash_log_path — jamais
    importé depuis ici pour éviter une dépendance circulaire database.py
    <-> main.py, d'où cette petite fonction autonome qui écrit au même
    endroit), qu'une routine défensive a dû intervenir alors qu'elle ne
    devrait normalement jamais avoir à le faire (voir rebalance_tables,
    passe défensive finale) — demande explicite du 2026-09-10 : "si elle
    doit intervenir alors qu'elle ne devrait normalement pas, prévoir au
    minimum une trace". Ne lève jamais d'exception elle-même (best-effort,
    comme _log_exception)."""
    try:
        with open(_defensive_integrity_log_path(), "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
            f.write(
                "[rebalance_tables] Passe défensive de capacité déclenchée "
                "(ne devrait normalement jamais arriver) : " + message + "\n"
            )
    except OSError:
        pass


def _log_skipped_tournament_file(path, exc):
    """Trace, dans le même journal que les plantages (voir _log_
    defensive_relocation ci-dessus pour le choix de ne pas importer
    main.py ici), qu'un fichier .tournoi a dû être ignoré par
    build_period_summary — soit à l'ouverture de la connexion, soit dès
    sa toute première lecture (ex. fichier tronqué/corrompu, disque
    plein interrompu, permissions) — demande du 2026-09-14 : un fichier
    illisible ne doit plus jamais faire échouer TOUTE la synthèse (voir
    build_period_summary), mais l'utilisateur doit pouvoir identifier
    PLUS TARD, dans ~/.poker_tournament/crash.log, lequel précisément a
    été ignoré, sans qu'une popup bloquante n'interrompe la génération
    de la synthèse pour autant. Ne lève jamais d'exception elle-même
    (best-effort, comme _log_defensive_relocation/_log_exception)."""
    try:
        with open(_defensive_integrity_log_path(), "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
            f.write(
                "[build_period_summary] Fichier .tournoi ignoré (illisible ou "
                f"corrompu) : {path}\n"
                f"  Cause : {type(exc).__name__}: {exc}\n"
            )
    except OSError:
        pass

# Préférence GLOBALE (voir export_prefs.py — même mécanisme que
# "remote_control_enabled", partagée par tous les tournois/Sit & Go de
# cette machine, pas une donnée du tournoi) qui active/désactive la
# question "quel siège est actuellement grosse blinde ?" lors d'un simple
# rééquilibrage (voir rebalance_tables, _bb_rebalance_prompt_enabled, et
# App._build_settings_tab / App._on_bb_rebalance_prompt_toggle dans
# main.py). Un seul et même nom de clé utilisé des deux côtés (importé
# dans main.py) pour ne jamais risquer une faute de frappe entre les deux.
# Activée par défaut (voir _bb_rebalance_prompt_enabled).
BB_REBALANCE_PROMPT_PREF_KEY = "bb_rebalance_prompt_enabled"


class Database:
    def __init__(self, path, read_only=False):
        """`read_only=True` : pour une simple consultation (Lobby SNG,
        synthèse par période...) d'un fichier .tournoi potentiellement
        déjà ouvert par une autre fenêtre/processus en ce moment même
        (voir _read_tournament_date_ro plus bas dans ce module, même
        principe). Ouvre en mode URI "ro" et saute la création/migration
        du schéma (executescript + _migrate + _init_defaults + commit,
        qui prennent chacun un verrou d'écriture) : un fichier .tournoi
        existant a forcément déjà tout ça en place, inutile de le refaire
        juste pour lire. Sous Windows en particulier, répéter ces
        écritures à chaque rafraîchissement du Lobby (toutes les 4s, sur
        chaque fichier du dossier) entrait en conflit avec les écritures
        de la fenêtre qui a ce même tournoi ouvert, et le fichier
        disparaissait alors silencieusement de la liste (exception
        avalée par l'appelant) le temps du conflit."""
        self.path = path
        # Version TEST "grosse blinde" (voir rebalance_tables /
        # resolve_pending_rebalance plus bas) : demande "quel siège est
        # actuellement grosse blinde ?" actuellement en attente de réponse,
        # ou None. État purement en mémoire (PAS en SQLite : ce n'est pas
        # une donnée du tournoi, seulement un état de session éphémère
        # côté interface), remis à None à chaque nouvelle instance de
        # Database — jamais lu ni écrit depuis le thread du serveur de
        # contrôle à distance (voir App._remote_pending_rebalance /
        # remote_control.py dans main.py, même principe que
        # _remote_clock_paused).
        self.pending_rebalance = None
        if read_only:
            self.conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
            self.conn.row_factory = sqlite3.Row
            return
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._init_defaults()
        self.conn.commit()

    def _migrate(self):
        """Ajoute les colonnes apparues après la création initiale du
        fichier .tournoi (les anciens fichiers n'ont pas 'bounty' /
        'bounty_won' / 'kills' sur la table players)."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(players)")}
        if "bounty" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN bounty INTEGER NOT NULL DEFAULT 0")
        if "bounty_won" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN bounty_won INTEGER NOT NULL DEFAULT 0")
        if "kills" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN kills INTEGER NOT NULL DEFAULT 0")
        if "elim_round" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN elim_round INTEGER")
        if "eliminated_by_name" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN eliminated_by_name TEXT")
        if "club" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN club TEXT NOT NULL DEFAULT ''")
        bounty_events_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(bounty_events)")}
        if "event_type" not in bounty_events_cols:
            self.conn.execute(
                "ALTER TABLE bounty_events ADD COLUMN event_type TEXT NOT NULL DEFAULT 'elimination'"
            )
        seat_moves_cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(seat_moves)")}
        if "reason" not in seat_moves_cols:
            self.conn.execute("ALTER TABLE seat_moves ADD COLUMN reason TEXT NOT NULL DEFAULT ''")

    # ---------- init ----------
    def _init_defaults(self):
        cur = self.conn.cursor()
        for k, v in DEFAULT_SETTINGS.items():
            cur.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
            )
        cur.execute("SELECT COUNT(*) c FROM blind_levels")
        if cur.fetchone()["c"] == 0:
            from structures import default_blind_structure
            self.set_blind_structure(default_blind_structure())
        cur.execute("SELECT COUNT(*) c FROM payout_structure")
        if cur.fetchone()["c"] == 0:
            self.set_payout_structure({1: 100.0})
        cur.execute("SELECT COUNT(*) c FROM tables_pk")
        if cur.fetchone()["c"] == 0:
            self.add_table("Table 1")

    # ---------- settings ----------
    def get_setting(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def get_setting_int(self, key, default=0):
        v = self.get_setting(key)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def get_setting_float(self, key, default=0.0):
        v = self.get_setting(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    # -- Autorisations DIRTO du Contrôle à distance (Phase 3, "Sécurisation
    # du Contrôle à distance", 2026-09-20) — table remote_authorizations,
    # voir SCHEMA et REMOTE_PERMISSION_LABELS plus haut. PAR TOURNOI : ce
    # fichier .tournoi précis uniquement, jamais partagé avec un autre. ---

    @staticmethod
    def _decode_permissions(raw_json):
        """Décodage défensif (jamais une exception, même sur une valeur
        corrompue) — et filtre systématiquement contre REMOTE_PERMISSION_
        LABELS : une clé qui existait dans une version antérieure de
        l'application mais plus aujourd'hui (ou, impossible en pratique
        mais par principe, "end_tournament" si jamais présente) disparaît
        silencieusement plutôt que d'être accordée par erreur."""
        try:
            values = json.loads(raw_json) if raw_json else []
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(values, list):
            return []
        return [v for v in dict.fromkeys(values) if v in REMOTE_PERMISSION_LABELS]

    @staticmethod
    def _row_to_dirto_authorization(row):
        return {
            "dirto_name": row["dirto_name"],
            "admin_name": row["admin_name"],
            "permissions": Database._decode_permissions(row["permissions"]),
            "granted_at": row["granted_at"],
            "updated_at": row["updated_at"],
        }

    def get_dirto_authorization(self, dirto_name):
        """dict {dirto_name, admin_name, permissions (liste de clés
        REMOTE_PERMISSION_*), granted_at, updated_at} pour CE DIRTO dans
        CE tournoi, ou None si aucune autorisation n'existe — "aucune
        autorisation existante = aucune permission DIRTO" (règle
        explicite) découle directement de ce None : voir son futur
        usage en Phase 4 (remote_control.py), jamais codé ici."""
        dirto_name = (dirto_name or "").strip()
        if not dirto_name:
            return None
        row = self.conn.execute(
            "SELECT * FROM remote_authorizations WHERE dirto_name = ?", (dirto_name,)
        ).fetchone()
        return self._row_to_dirto_authorization(row) if row is not None else None

    def list_dirto_authorizations(self):
        """Toutes les autorisations DIRTO de CE tournoi, triées par nom
        (insensible à la casse) — pour l'écran Paramètres (voir main.py),
        qui doit pouvoir afficher/modifier chacune, pas seulement celle
        du DIRTO actuellement sélectionné dans le formulaire."""
        rows = self.conn.execute(
            "SELECT * FROM remote_authorizations ORDER BY dirto_name COLLATE NOCASE"
        ).fetchall()
        return [self._row_to_dirto_authorization(row) for row in rows]

    def set_dirto_authorization(self, dirto_name, admin_name, permissions):
        """Crée (ou REMPLACE intégralement — jamais un ajout partiel,
        jamais deux lignes pour le même DIRTO, dirto_name est la clé
        PRIMAIRE de la table) l'autorisation de `dirto_name` pour CE
        tournoi, accordée/modifiée par `admin_name`. `admin_name` est
        toujours écrasé par la valeur donnée ici, y compris lors d'une
        simple MODIFICATION d'une autorisation déjà existante : reflète
        "changement d'ADMIN accordant les droits" (règle explicite) —
        c'est TOUJOURS le dernier ADMIN à avoir agi sur cette
        autorisation, jamais celui qui l'a créée à l'origine en premier
        (`granted_at`, lui, est préservé tel quel lors d'une
        modification — seul `updated_at` change).

        `permissions` : itérable de clés — filtré contre REMOTE_
        PERMISSION_LABELS (voir _decode_permissions) et dédoublonné
        avant stockage ; toute clé invalide/inconnue (donc aussi
        "end_tournament", qui n'existe pas dans ce dict) est
        silencieusement écartée — DERNIER filet de sécurité, en plus de
        l'interface qui ne la propose de toute façon jamais.

        Ne fait rien si `dirto_name`/`admin_name` est vide après
        nettoyage — jamais une ligne orpheline sans identité claire."""
        dirto_name = (dirto_name or "").strip()
        admin_name = (admin_name or "").strip()
        if not dirto_name or not admin_name:
            return
        clean_permissions = [p for p in dict.fromkeys(permissions or []) if p in REMOTE_PERMISSION_LABELS]
        now = time.time()
        existing = self.conn.execute(
            "SELECT granted_at FROM remote_authorizations WHERE dirto_name = ?", (dirto_name,)
        ).fetchone()
        granted_at = existing["granted_at"] if existing is not None else now
        self.conn.execute(
            "INSERT INTO remote_authorizations "
            "(dirto_name, admin_name, permissions, granted_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(dirto_name) DO UPDATE SET "
            "admin_name=excluded.admin_name, permissions=excluded.permissions, "
            "updated_at=excluded.updated_at",
            (dirto_name, admin_name, json.dumps(clean_permissions), granted_at, now),
        )
        self.conn.commit()

    def clear_dirto_authorization(self, dirto_name):
        """Retrait COMPLET de l'autorisation de `dirto_name` pour CE
        tournoi (règle explicite) — supprime la ligne, ne la vide/
        désactive pas : get_dirto_authorization renverra ensuite None,
        exactement comme si ce DIRTO n'avait jamais été autorisé.
        Silencieux si `dirto_name` n'a aucune autorisation (rien à
        retirer)."""
        dirto_name = (dirto_name or "").strip()
        if not dirto_name:
            return
        self.conn.execute("DELETE FROM remote_authorizations WHERE dirto_name = ?", (dirto_name,))
        self.conn.commit()

    def get_tournament_date(self):
        """Date du tournoi (AAAA-MM-JJ), utilisée pour les synthèses par
        période. C'est la date fixée à la création du tournoi si elle est
        connue ; sinon (fichiers créés avant l'existence de ce paramètre),
        on retombe sur la date de création du fichier .tournoi lui-même
        (st_birthtime si disponible - macOS/BSD -, sinon la date de
        dernière modification). On évite volontairement de se baser sur
        la date de modification quand la vraie date de création est
        connue : le simple fait d'ouvrir un ancien fichier (migrations de
        réglages) le "touche" et modifierait sinon sa date à chaque
        utilisation."""
        d = self.get_setting("tournament_date", "")
        if d:
            return d
        try:
            st = os.stat(self.path)
            ts = getattr(st, "st_birthtime", None)
            if ts is None:
                ts = st.st_mtime
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except OSError:
            return ""

    def set_settings(self, mapping):
        for k, v in mapping.items():
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )
        self.conn.commit()

    # ---------- tables ----------
    def set_all_tables_max_seats(self, new_max):
        """Applique un nouveau nombre de sièges par table à TOUTES les
        tables existantes (actives ou fermées), pour que le changement du
        paramètre prenne effet immédiatement, y compris en cours de
        tournoi. À appeler suivi d'un rebalance_tables().

        N'EST PAS elle-même responsable de refuser un changement laissant
        une table en surcapacité (voir tables_over_capacity juste en
        dessous, et App._collect_and_save_all_settings dans main.py qui
        décide en amont s'il faut appeler cette méthode) : cette fonction
        reste un simple UPDATE inconditionnel, comme avant."""
        new_max = max(2, int(new_max))
        self.conn.execute("UPDATE tables_pk SET max_seats=?", (new_max,))
        self.conn.commit()

    def tables_over_capacity(self, new_max):
        """Renvoie la liste des tables ACTIVES dont l'occupation réelle
        (joueurs actifs assis) dépasserait STRICTEMENT `new_max` — sans
        rien modifier. Utilisé par App._collect_and_save_all_settings
        (main.py) pour refuser un changement de "Nombre de sièges par
        table" en cours de tournoi (clock_started == 1) qui laisserait
        sinon une table durablement au-dessus de sa capacité (ex : 9
        joueurs pour max_seats=8) — PHASE 2 de l'architecture de
        rééquilibrage validée le 2026-09-10 : refus ciblé de l'application
        du réglage plutôt que déplacement automatique d'un joueur choisi
        arbitrairement (siège le plus haut) pour y faire de la place.
        Avant le premier démarrage (clock_started == 0), cette méthode
        n'est volontairement PAS consultée par main.py : la réorganisation
        automatique complète reste autorisée (voir rebalance_tables,
        PHASE 1).

        Exception de "table finale" (demande du 2026-09-14, suite à un
        cas réel : 1 table / 8 joueurs / capacité réglée à 7 affichait à
        tort ce refus) — réutilise EXACTEMENT la même convention que
        rebalance_tables (voir FINAL_TABLE_MAX_SEATS ci-dessus), jamais
        une règle indépendante : quand il ne reste qu'UNE seule table
        active et que son occupation totale tient dans la table finale
        (<= FINAL_TABLE_MAX_SEATS, actuellement 10), aucun refus n'est
        jamais opposé, quelle que soit la nouvelle capacité demandée —
        cette table est déjà, par construction (voir rebalance_tables),
        la table finale autorisée à dépasser "Nombre de sièges par
        table" ; il serait incohérent de refuser d'un côté ce que
        l'autre mécanisme autorise déjà explicitement. Dès qu'il y a
        plusieurs tables actives, ou que l'occupation dépasse
        FINAL_TABLE_MAX_SEATS (11 joueurs ou plus), cette exception ne
        s'applique JAMAIS : le contrôle de capacité normal s'applique
        alors sans changement.

        Chaque élément de la liste renvoyée : {"name": <nom de la
        table>, "count": <nombre de joueurs actifs qui y sont assis>}."""
        new_max = int(new_max)
        active_tables = list(self.list_tables())
        occupancies = {
            t["id"]: self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            for t in active_tables
        }
        if len(active_tables) == 1 and sum(occupancies.values()) <= FINAL_TABLE_MAX_SEATS:
            return []
        over = []
        for t in active_tables:
            occ = occupancies[t["id"]]
            if occ > new_max:
                over.append({"name": t["name"], "count": occ})
        return over

    def _table_display_number(self, table_row):
        """Numéro affiché d'une table (ex : 9 pour "Table 9"), utilisé pour
        décider quelle table fermer en premier lors d'une consolidation
        (voir rebalance_tables) — à préférer à l'id interne, qui peut
        diverger du numéro affiché sur un vieux fichier .tournoi (voir
        commentaire dans rebalance_tables). Retombe sur l'id si le nom ne
        se termine pas par un nombre (table renommée manuellement)."""
        m = re.search(r"(\d+)\s*$", table_row["name"] or "")
        return int(m.group(1)) if m else table_row["id"]

    def _open_or_reopen_table(self):
        """Fournit une table supplémentaire quand aucune table active n'a
        de place (voir _seat_player et rebalance_tables) : réactive
        d'abord la table FERMÉE dont le numéro affiché est le plus bas
        s'il en existe une, plutôt que d'en créer systématiquement une
        toute nouvelle avec un numéro plus haut. Sans ça, une phase de
        consolidation (tables fermées en fin de tournoi) suivie d'un
        besoin de place retrouvé (joueur réintégré, ou ajouté après coup)
        rouvrait toujours une table de numéro croissant, laissant des
        trous dans la numérotation affichée (ex : Table 1, 8, 9 restantes
        au lieu de Table 1, 2, 3 — un joueur avait fini par signaler cette
        numérotation en dents de scie comme un bug). Renvoie la ligne de
        la table (réactivée ou nouvellement créée)."""
        closed = [t for t in self.list_tables(active_only=False) if t["is_active"] == 0]
        if closed:
            closed.sort(key=self._table_display_number)
            t = closed[0]
            self.conn.execute("UPDATE tables_pk SET is_active=1 WHERE id=?", (t["id"],))
            self.conn.commit()
            return self.conn.execute("SELECT * FROM tables_pk WHERE id=?", (t["id"],)).fetchone()
        new_id = self.add_table()
        return self.conn.execute("SELECT * FROM tables_pk WHERE id=?", (new_id,)).fetchone()

    def add_table(self, name=None):
        max_seats = self.get_setting_int("max_seats_per_table", 9)
        if name is None:
            # Compte TOUTES les tables jamais créées (actives ou fermées) :
            # se baser uniquement sur les tables actives ferait repartir la
            # numérotation en arrière après une fermeture, et créerait des
            # doublons de nom (ex : "Table 2" utilisé deux fois).
            n = self.conn.execute("SELECT COUNT(*) c FROM tables_pk").fetchone()["c"]
            name = f"Table {n + 1}"
        cur = self.conn.execute(
            "INSERT INTO tables_pk(name, max_seats, is_active) VALUES (?, ?, 1)",
            (name, max_seats),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_tables(self, active_only=True):
        q = "SELECT * FROM tables_pk"
        if active_only:
            q += " WHERE is_active=1"
        q += " ORDER BY id"
        return self.conn.execute(q).fetchall()

    def close_table(self, table_id):
        self.conn.execute(
            "UPDATE tables_pk SET is_active=0 WHERE id=?", (table_id,)
        )
        self.conn.commit()

    # ---------- players ----------
    def list_players(self, status=None):
        q = "SELECT * FROM players"
        params = ()
        if status:
            q += " WHERE status=?"
            params = (status,)
        q += " ORDER BY table_id, seat"
        return self.conn.execute(q, params).fetchall()

    def get_player(self, player_id):
        return self.conn.execute(
            "SELECT * FROM players WHERE id=?", (player_id,)
        ).fetchone()

    def find_active_conflict(self, player_name):
        """Renvoie le chemin du fichier .tournoi (dans le même dossier que
        celui-ci, non récursif, et daté du même jour — voir plus bas) où
        `player_name` est actuellement un joueur actif, ou None s'il n'y
        en a pas. Sert à empêcher d'inscrire par erreur un même joueur sur
        deux tournois en cours à la fois (ex : deux Sit & Go simultanés
        dans la même salle). Les autres fichiers ne sont consultés qu'en
        lecture seule. Si ce tournoi n'a pas encore été sauvegardé sur
        disque, aucune vérification n'est possible (renvoie None).

        Seuls les tournois datés du même jour que celui-ci sont comparés :
        un ancien fichier abandonné (jamais terminé, un autre jour) ne
        doit pas bloquer indéfiniment un joueur qui n'est plus vraiment
        "en jeu ailleurs"."""
        if not self.path or not os.path.exists(self.path) or not player_name.strip():
            return None
        folder = os.path.dirname(os.path.abspath(self.path)) or "."
        today = self.get_tournament_date()
        name_lower = player_name.strip().lower()
        for path in find_tournament_files(folder, recursive=False):
            if os.path.abspath(path) == os.path.abspath(self.path):
                continue
            if today and _read_tournament_date_ro(path) != today:
                continue
            if _player_active_in_file(path, name_lower):
                return path
        return None

    def _primes_enabled(self):
        """Interrupteur général des primes pour CE tournoi (réglage
        `primes_enabled`, demande du 2026-09-09) : True = comportement
        historique inchangé ; False = aucun calcul de prime ne doit avoir
        lieu nulle part (présence/assiduité/classement/bounty classique/
        PKO), y compris à l'inscription (bounty jamais stampée) et à
        l'élimination (kills/bounty/bounty_won/bounty_events/clôture du
        vainqueur jamais touchés) — jamais un simple masquage à
        l'affichage. Absent d'un ancien fichier -> True (voir
        DEFAULT_SETTINGS, compatibilité historique)."""
        return self.get_setting_int("primes_enabled", 1) == 1

    def primes_enabled(self):
        """Équivalent public de `_primes_enabled` — à utiliser depuis
        l'extérieur de cette classe (main.py notamment, pour décider
        d'appliquer ou non la contrainte PKO d'éliminateur obligatoire
        AVANT même d'appeler eliminate_player, voir _eliminate_selected/
        _ask_eliminator/_remote_eliminate) plutôt que d'accéder
        directement à une méthode "privée"."""
        return self._primes_enabled()

    def add_player(self, name, club=""):
        """`club` : copié dans ce tournoi au moment de l'ajout (voir
        roster.get_club côté appelant) — n'est ensuite plus synchronisé
        avec le répertoire si celui-ci change, ce tournoi garde la photo
        du club tel qu'il était à l'inscription."""
        starting_chips = self.get_setting_int("starting_chips", 10000)
        # Primes désactivées (_primes_enabled) : jamais de bounty stampée
        # à l'inscription, quel que soit le réglage `bounty_amount`.
        bounty_amount = self.get_setting_int("bounty_amount", 0) if self._primes_enabled() else 0
        cur = self.conn.execute(
            "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
            "chips, status, bounty, club) VALUES (?, 1, 0, 0, ?, 'active', ?, ?)",
            (name, starting_chips, bounty_amount, (club or "").strip()),
        )
        player_id = cur.lastrowid
        self.conn.commit()
        self._seat_player(player_id)
        self.rebalance_tables(record_moves=False)
        return player_id

    def rebuy_player(self, player_id):
        chips = self.get_setting_int("rebuy_chips", 10000)
        # Voir add_player : jamais de bounty ajoutée si les primes sont
        # désactivées pour ce tournoi.
        bounty_amount = self.get_setting_int("bounty_amount", 0) if self._primes_enabled() else 0
        self.conn.execute(
            "UPDATE players SET rebuy_count = rebuy_count + 1, "
            "chips = chips + ?, bounty = bounty + ? WHERE id=?",
            (chips, bounty_amount, player_id),
        )
        self.conn.commit()

    def addon_player(self, player_id):
        chips = self.get_setting_int("addon_chips", 10000)
        self.conn.execute(
            "UPDATE players SET addon_count = addon_count + 1, "
            "chips = chips + ? WHERE id=?",
            (chips, player_id),
        )
        self.conn.commit()

    def rename_player(self, player_id, new_name):
        new_name = new_name.strip()
        if not new_name:
            return
        self.conn.execute(
            "UPDATE players SET name=? WHERE id=?", (new_name, player_id)
        )
        self.conn.commit()

    def set_player_club(self, player_id, club):
        """Corrige le club (POUR CE TOURNOI, voir add_player) d'un joueur
        déjà inscrit — ex. club mal renseigné/absent à l'ajout."""
        self.conn.execute(
            "UPDATE players SET club=? WHERE id=?", ((club or "").strip(), player_id)
        )
        self.conn.commit()

    def set_purchase_counts(self, player_id, buyin_count, rebuy_count, addon_count):
        """Corrige manuellement les compteurs buy-in / rebuy / add-on d'un
        joueur (utile en cas d'erreur de saisie), sans toucher aux chips."""
        self.conn.execute(
            "UPDATE players SET buyin_count=?, rebuy_count=?, addon_count=? WHERE id=?",
            (max(0, int(buyin_count)), max(0, int(rebuy_count)), max(0, int(addon_count)), player_id),
        )
        self.conn.commit()

    def set_chips(self, player_id, chips):
        self.conn.execute(
            "UPDATE players SET chips=? WHERE id=?", (max(0, int(chips)), player_id)
        )
        self.conn.commit()

    def eliminate_player(self, player_id, eliminated_by_id=None, orphan_bounty_ok=False):
        """Élimine un joueur. Si `eliminated_by_id` est fourni, l'éliminateur
        voit son compteur de bounty (kills, prime de bounty en points —
        voir get_bounty_bonuses) incrémenté de 1, quel que soit le mode de
        bounty. Si en plus le joueur éliminé portait une prime (bounty, en
        points), celle-ci est versée à l'éliminateur : intégralement en
        mode classique, ou selon le partage PKO (une partie en points
        immédiats, le reste ajouté à la prime de l'éliminateur) en mode
        progressif. Enregistre aussi, pour l'onglet Joueurs, le round et le
        nom de l'éliminateur.

        En mode PKO, un éliminateur est OBLIGATOIRE dès que le joueur
        éliminé porte une bounty > 0 : lève ValueError sans rien modifier
        si `eliminated_by_id` est absent ou invalide dans ce cas (demande
        du 2026-09-08 — une bounty PKO ne doit jamais devenir orpheline).
        Ne s'applique jamais hors PKO ni si la bounty du joueur est à 0 :
        toutes les autres possibilités existantes (élimination sans
        éliminateur) restent inchangées.

        `orphan_bounty_ok=True` (demande du 2026-09-09, Mode Test — voir
        App._eliminate_selected, élimination groupée) : lève CETTE
        contrainte précise pour ce seul appel, SANS créer de faux
        éliminateur ni transférer la bounty du joueur éliminé à qui que
        ce soit (aucun bounty_won ajouté, aucune ligne bounty_events,
        aucun partage PKO normal) — mais REMET SA BOUNTY À 0 (demande du
        2026-09-09, 3e relecture) plutôt que de la laisser telle quelle :
        un joueur désormais éliminé/inactif ne doit jamais rester porteur
        d'une bounty non nulle en base, considéré comme un état
        incohérent même à des fins de test. Cette bounty est donc
        simplement ABANDONNÉE (perdue pour tout le monde), jamais
        attribuée. RÉSERVÉ à un appelant qui a déjà vérifié lui-même que
        le Mode Test est actif : cette méthode ne connaît rien du Mode
        Test (concept purement main.py/UI, jamais persisté), elle se
        contente d'un simple paramètre d'appel explicite — jamais activé
        par défaut, jamais accessible autrement que par ce paramètre.
        Sans effet si `eliminated_by_id` est fourni (la contrainte ne
        s'applique de toute façon que si aucun éliminateur n'est désigné,
        et le bloc de transfert normal ci-dessous remet déjà la bounty à
        0 dans ce cas).

        Primes désactivées (`_primes_enabled`, demande du 2026-09-09) :
        AUCUN de ces mécanismes ne s'applique — ni la contrainte
        d'éliminateur obligatoire (elle n'a plus lieu d'être puisqu'aucune
        bounty n'est jamais assignée dans ce mode, voir add_player), ni
        le comptage de kills, ni le moindre transfert bounty/bounty_won/
        bounty_events, ni la clôture de la bounty du vainqueur. Seule
        l'inscription du round/nom de l'éliminateur (`eliminated_by_name`/
        `elim_round`) reste enregistrée dans tous les cas : elle sert au
        bandeau d'élimination, à l'onglet Classement/Joueurs et aux
        statistiques, indépendamment des primes — jamais supprimée ici.

        Annulation (demande du 2026-09-17, voir undo_last_elimination) :
        un instantané complet de l'état juste AVANT cette élimination
        (joueur éliminé, éventuel éliminateur, toutes les tables, fin de
        tournoi, dernière ligne bounty_events, question de rééquilibrage
        éventuellement déjà en attente) est capturé ICI, avant la moindre
        écriture, puis complété d'un second instantané "APRÈS" une fois
        l'élimination ET le rééquilibrage qui la suit terminés — le tout
        mémorisé dans settings["last_elimination_undo"]. Écrasé par la
        PROCHAINE élimination, quelle qu'elle soit : un seul niveau
        d'annulation possible, toujours le tout dernier joueur éliminé,
        jamais un historique remontant plus loin (règle absolue demandée
        par l'utilisateur)."""
        active = self.list_players(status="active")
        place = len(active)  # ce joueur prend la place n° (nb d'actifs restants)
        eliminated = self.get_player(player_id)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        current_round = self.get_current_round_number()
        eliminator_row = self.get_player(eliminated_by_id) if eliminated_by_id else None
        eliminator_name = eliminator_row["name"] if eliminator_row else None

        primes_enabled = self._primes_enabled()
        pko_mode = primes_enabled and self.get_setting_int("pko_mode", 0) == 1
        if (pko_mode and eliminated and eliminated["bounty"] > 0
                and eliminator_row is None and not orphan_bounty_ok):
            raise ValueError(
                f"{eliminated['name']} porte une prime PKO de "
                f"{eliminated['bounty']} pts : un éliminateur doit être "
                "désigné pour ne pas la rendre orpheline."
            )

        # Instantané "AVANT" (voir la docstring ci-dessus et undo_last_
        # elimination) : capturé maintenant, juste avant la toute première
        # écriture de cette méthode — jamais après.
        undo_tracked_ids = sorted(
            {p["id"] for p in active} | ({eliminated_by_id} if eliminated_by_id else set())
        )
        undo_tables_before = [dict(t) for t in self.list_tables(active_only=False)]
        undo_end_epoch_before = self.get_setting("tournament_end_epoch")
        undo_bounty_max_before = self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) m FROM bounty_events"
        ).fetchone()["m"]
        undo_pending_id_before = (
            self.pending_rebalance["request_id"] if self.pending_rebalance else None
        )
        undo_states_before = {
            str(pid): self._player_undo_state(pid) for pid in undo_tracked_ids
        }

        self.conn.execute(
            "UPDATE players SET status='eliminated', place=?, elim_time=?, "
            "elim_round=?, eliminated_by_name=?, table_id=NULL, seat=NULL WHERE id=?",
            (place, now, current_round, eliminator_name, player_id),
        )

        if orphan_bounty_ok and eliminated_by_id is None and eliminated and eliminated["bounty"] > 0:
            # Mode Test, élimination groupée sans éliminateur (demande du
            # 2026-09-09, 3e relecture) : la bounty de ce joueur n'est
            # transférée à personne (voir le bloc de transfert normal
            # ci-dessous, jamais atteint ici puisque eliminated_by_id est
            # None) — mais elle ne doit pas non plus rester non nulle sur
            # un joueur désormais éliminé/inactif (état incohérent, même
            # à des fins de test) : elle est donc ABANDONNÉE, remise à 0
            # sans être créditée nulle part (aucun bounty_won, aucune
            # ligne bounty_events, aucun partage PKO).
            self.conn.execute("UPDATE players SET bounty=0 WHERE id=?", (player_id,))

        if primes_enabled and eliminated_by_id:
            self.conn.execute(
                "UPDATE players SET kills = kills + 1 WHERE id=?", (eliminated_by_id,)
            )

        if primes_enabled and eliminated_by_id and eliminated and eliminated["bounty"] > 0:
            bounty = eliminated["bounty"]
            eliminator = self.get_player(eliminated_by_id)
            if pko_mode:
                cash_pct = self.get_setting_int("pko_cash_percent", 50)
                cash_part = round(bounty * cash_pct / 100)
                grow_part = bounty - cash_part
            else:
                cash_part = bounty
                grow_part = 0
            self.conn.execute(
                "UPDATE players SET bounty_won = bounty_won + ?, bounty = bounty + ? "
                "WHERE id=?",
                (cash_part, grow_part, eliminated_by_id),
            )
            self.conn.execute("UPDATE players SET bounty=0 WHERE id=?", (player_id,))
            self.conn.execute(
                "INSERT INTO bounty_events(eliminated_name, eliminator_name, "
                "amount_won, added_to_eliminator_bounty, event_time, event_type) "
                "VALUES (?,?,?,?,?,'elimination')",
                (eliminated["name"], eliminator["name"] if eliminator else "?",
                 cash_part, grow_part, now),
            )

        self.conn.commit()

        # Fige l'heure de fin dès qu'il ne reste plus qu'1 joueur actif
        # (le vainqueur) — sert à figer l'affichage "Durée" du chrono
        # projecteur au lieu de continuer à défiler après la fin de la
        # partie, et à y afficher "Partie terminée" (voir get_stats()).
        still_active = self.list_players(status="active")
        if len(still_active) <= 1:
            self.set_setting("tournament_end_epoch", int(time.time()))
            # Clôture de la bounty finale du vainqueur (mode PKO
            # uniquement, demande du 2026-09-08 ; jamais si les primes
            # sont désactivées, demande du 2026-09-09) : dès qu'il ne
            # reste plus qu'un seul joueur actif, sa bounty encore
            # portée lui est définitivement attribuée — voir
            # _close_out_winner_bounty.
            if pko_mode and len(still_active) == 1:
                self._close_out_winner_bounty(still_active[0]["id"], now)

        moves = self.rebalance_tables(record_moves=True)

        # Instantané "APRÈS" (voir la docstring plus haut) : maintenant
        # que l'élimination ET le rééquilibrage qui la suit sont
        # entièrement terminés — persisté seulement à ce stade, une fois
        # les deux moitiés (avant/après) disponibles.
        self._save_elimination_undo_snapshot(
            player_id=player_id,
            tracked_ids=undo_tracked_ids,
            tables_before=undo_tables_before,
            tournament_end_epoch_before=undo_end_epoch_before,
            bounty_events_max_id_before=undo_bounty_max_before,
            pending_rebalance_id_before=undo_pending_id_before,
            player_states_before=undo_states_before,
        )

        return moves

    def _close_out_winner_bounty(self, winner_id, now=None):
        """Clôture la bounty finale du VAINQUEUR d'un tournoi PKO (demande
        du 2026-09-08) : transfère sa bounty encore portée vers son propre
        bounty_won (elle est "définitivement gagnée", voir
        get_bounty_bonuses), remet sa bounty portée à 0, et enregistre un
        événement bounty_events distinct — event_type='victory_collect',
        eliminator_name=NULL — pour qu'on comprenne sans ambiguïté qu'il
        s'agit de la récupération de sa propre bounty finale, PAS d'une
        élimination supplémentaire (ne touche jamais `kills`).

        Idempotent : si la bounty portée est déjà à 0 (déjà clôturée, ou
        rien à clôturer), ne fait rien — sûr à appeler plusieurs fois.
        Appelé automatiquement par eliminate_player() dès qu'il ne reste
        plus qu'un joueur actif. Les tournois déjà terminés AVANT ce
        correctif ne sont PAS corrigés rétroactivement par cette méthode
        (aucune nouvelle élimination n'y déclenche plus jamais cet appel)
        — voir Database._pko_effective_bounty_won pour le filet de
        sécurité en LECTURE SEULE qui couvre ce cas à l'affichage/export."""
        winner = self.get_player(winner_id)
        if not winner or winner["bounty"] <= 0:
            return
        amount = winner["bounty"]
        now = now or time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute(
            "UPDATE players SET bounty_won = bounty_won + ?, bounty = 0 WHERE id=?",
            (amount, winner_id),
        )
        self.conn.execute(
            "INSERT INTO bounty_events(eliminated_name, eliminator_name, "
            "amount_won, added_to_eliminator_bounty, event_time, event_type) "
            "VALUES (?,NULL,?,0,?,'victory_collect')",
            (winner["name"], amount, now),
        )
        self.conn.commit()

    def _pko_effective_bounty_won(self, player):
        """bounty_won réel d'un joueur en mode PKO, EN LECTURE SEULE (ne
        modifie jamais la base — utilisable même sur une connexion
        read_only) : inclut la bounty encore portée par le VAINQUEUR si le
        tournoi est terminé et que cette clôture n'a pas encore été
        physiquement appliquée en base (voir _close_out_winner_bounty) —
        filet de sécurité pour les tournois déjà terminés AVANT ce
        correctif (aucune nouvelle élimination n'y déclenche plus jamais
        la clôture réelle). Sans effet (renvoie bounty_won tel quel) hors
        PKO, ou si la clôture a déjà eu lieu (bounty déjà à 0 -> somme
        inchangée, sans double comptage)."""
        won = player["bounty_won"]
        if self.get_setting_int("pko_mode", 0) != 1:
            return won
        active = self.list_players(status="active")
        if len(active) == 1 and active[0]["id"] == player["id"]:
            won += player["bounty"]
        return won

    # =====================================================================
    # Annulation de la dernière élimination (demande du 2026-09-17)
    # =====================================================================
    #
    # Fonctionnalité distincte de reinstate_player()/"Réinscrire" (bouton
    # existant, INCHANGÉ) : "Réinscrire" remet un joueur en jeu avec les
    # jetons de départ, réassis à la table la moins pleine, sans toucher
    # aux primes/kills/mouvements — pratique pour une remise en jeu
    # ordinaire, mais PAS une annulation. Ici, il s'agit au contraire de
    # faire comme si l'élimination n'avait jamais eu lieu : même table,
    # même siège, mêmes jetons (jamais touchés par une élimination de
    # toute façon), primes/kills/bounty_events et mouvements de tables
    # provoqués par CETTE élimination précise entièrement défaits.
    #
    # Approche retenue (validée avec l'utilisateur le 2026-09-17) :
    # INSTANTANÉ COMPLET avant/après (voir eliminate_player), jamais une
    # tentative d'inverser mouvement par mouvement — un cassage de table
    # répartit ALÉATOIREMENT les joueurs évincés (rebalance_tables),
    # opération non réversible étape par étape ; revenir directement à
    # l'état exact d'avant, lui, fonctionne quel que soit le mécanisme de
    # rééquilibrage qui s'est déclenché (simple équilibrage, fusion,
    # table finale...).
    #
    # SÉCURITÉ AVANT TOUT (demande explicite) : plutôt que de tenter une
    # reconstruction approximative, undo_last_elimination() REFUSE
    # entièrement (ValueError, aucune écriture) dès que l'état courant ne
    # correspond plus EXACTEMENT à l'instantané "après" mémorisé.

    def _player_undo_state(self, player_id):
        """Sous-ensemble des colonnes de `players` nécessaires à
        l'instantané d'annulation d'élimination (identité de la ligne au
        moment de la capture) : utilisé à la fois pour la capture avant/
        après (eliminate_player) et pour la revalidation stricte au
        moment de l'annulation (undo_last_elimination compare l'état
        courant à l'état "après" mémorisé, champ par champ). Ne couvre
        QUE ce qu'une élimination peut modifier — jamais chips/buyin/
        rebuy/addon/club/nom, volontairement : un rebuy, un renommage ou
        une correction de jetons entre-temps n'a aucun rapport avec
        l'élimination et ne doit jamais bloquer son annulation.

        Renvoie None si le joueur n'existe plus (supprimé) — une
        divergence par rapport à n'importe quel instantané préexistant,
        détectée telle quelle par la comparaison d'égalité de
        undo_last_elimination (None != un dict), jamais un cas particulier
        séparé."""
        row = self.get_player(player_id)
        if row is None:
            return None
        return {
            "status": row["status"], "table_id": row["table_id"], "seat": row["seat"],
            "bounty": row["bounty"], "bounty_won": row["bounty_won"], "kills": row["kills"],
            "place": row["place"], "elim_time": row["elim_time"], "elim_round": row["elim_round"],
            "eliminated_by_name": row["eliminated_by_name"],
        }

    def _save_elimination_undo_snapshot(self, player_id, tracked_ids, tables_before,
                                         tournament_end_epoch_before,
                                         bounty_events_max_id_before,
                                         pending_rebalance_id_before,
                                         player_states_before):
        """Termine et persiste l'instantané d'annulation démarré par
        eliminate_player (voir sa docstring pour le détail de chaque
        pièce "avant") : capture le pendant "APRÈS", une fois l'élimination
        ET le rééquilibrage qui la suit entièrement terminés, sur EXACTEMENT
        les mêmes joueurs/tables, puis écrit le tout dans settings["last_
        elimination_undo"] au format JSON — remplace systématiquement tout
        instantané précédent (RÈGLE ABSOLUE demandée : seule la toute
        dernière élimination reste annulable, jamais un historique)."""
        player_states_after = {
            str(pid): self._player_undo_state(pid) for pid in tracked_ids
        }
        tables_after = [dict(t) for t in self.list_tables(active_only=False)]
        tournament_end_epoch_after = self.get_setting("tournament_end_epoch")
        bounty_events_max_id_after = self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) m FROM bounty_events"
        ).fetchone()["m"]
        pending_rebalance_id_after = (
            self.pending_rebalance["request_id"] if self.pending_rebalance else None
        )
        snapshot = {
            "version": 1,
            "player_id": player_id,
            "before": {
                "tables": tables_before,
                "tournament_end_epoch": tournament_end_epoch_before,
                "bounty_events_max_id": bounty_events_max_id_before,
                "pending_rebalance_request_id": pending_rebalance_id_before,
                "player_states": player_states_before,
            },
            "after": {
                "tables": tables_after,
                "tournament_end_epoch": tournament_end_epoch_after,
                "bounty_events_max_id": bounty_events_max_id_after,
                "pending_rebalance_request_id": pending_rebalance_id_after,
                "player_states": player_states_after,
            },
        }
        self.set_setting("last_elimination_undo", json.dumps(snapshot))

    def get_last_eliminated_player(self):
        """Le joueur éliminé le plus RÉCEMMENT, ou None s'il n'y a
        actuellement aucun joueur éliminé. `place` est assigné de façon
        strictement décroissante à chaque élimination (nombre d'actifs
        restants à cet instant précis, voir eliminate_player) ; withdraw_
        player/reinstate_player ne font que décaler TOUS les `place` déjà
        attribués d'un même montant (+1/-1, voir leurs docstrings), ce qui
        préserve toujours l'ordre relatif entre éliminations. Le dernier
        éliminé est donc, en toute fiabilité, celui dont `place` est le
        plus PETIT parmi les joueurs status='eliminated' — jamais besoin
        d'un compteur ou d'un horodatage séparé."""
        return self.conn.execute(
            "SELECT * FROM players WHERE status='eliminated' AND place IS NOT NULL "
            "ORDER BY place ASC LIMIT 1"
        ).fetchone()

    def _undo_elimination_timeout_minutes(self):
        """Délai (en minutes) au-delà duquel "Annule Eliminer" n'est plus
        disponible pour la dernière élimination (Paramètres : "Timeout
        pour Annuler Eliminer (m)", demande du 2026-09-17) — 5 minutes
        par défaut. Même convention que "elimination_banner_seconds" :
        JAMAIS dans DEFAULT_SETTINGS, un simple repli Python ici (un
        ancien fichier .tournoi sans ce réglage retombe donc proprement
        sur 5, comme "Durée du bandeau d'élimination" juste au-dessus de
        lui dans Paramètres). RÈGLE ABSOLUE demandée : 0 DÉSACTIVE
        complètement la fonction, jamais interprété comme "illimité"."""
        return self.get_setting_int("undo_elimination_timeout_minutes", 5)

    def _elim_time_to_epoch(self, elim_time_str):
        """Convertit elim_time ("%Y-%m-%d %H:%M:%S", heure locale — même
        format que celui écrit par eliminate_player via time.strftime)
        en epoch (secondes), pour un calcul de délai ÉCOULÉ fiable —
        JAMAIS une comparaison de texte HH:MM affiché, qui casserait dès
        un changement de minute/heure/jour entre l'élimination et la
        tentative d'annulation (mise en garde explicite du 2026-09-17).
        Renvoie None si la valeur est vide ou illisible (défensif : ne
        devrait normalement jamais arriver pour un joueur réellement
        éliminé, elim_time étant toujours renseigné par eliminate_
        player)."""
        if not elim_time_str:
            return None
        try:
            return time.mktime(time.strptime(elim_time_str, "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError, OverflowError):
            return None

    def undo_last_elimination_available(self):
        """True si "Annule Eliminer" doit être proposé MAINTENANT — un
        dernier joueur éliminé existe, le timeout n'est pas à 0
        (désactivation complète du bouton comme du clic droit) et le
        temps écoulé depuis son elim_time reste STRICTEMENT inférieur au
        timeout configuré (donc déjà refusé PILE à l'expiration, pas
        seulement après — demande explicite). Utilisée par main.py pour
        l'état du bouton "Annule Eliminer" ET la condition du clic droit
        sur le dernier joueur éliminé — jamais une logique dupliquée :
        undo_last_elimination() revalide de toute façon indépendamment ce
        même délai (avec ses propres messages d'erreur précis) avant de
        restaurer quoi que ce soit, ce qui garantit qu'AUCUNE autre voie
        d'appel ne peut contourner ce délai, même en ignorant cette
        méthode-ci."""
        if self._undo_elimination_timeout_minutes() <= 0:
            return False
        last = self.get_last_eliminated_player()
        if last is None:
            return False
        elim_epoch = self._elim_time_to_epoch(last["elim_time"])
        if elim_epoch is None:
            return False
        return (time.time() - elim_epoch) < self._undo_elimination_timeout_minutes() * 60

    def undo_last_elimination(self):
        """Annule la DERNIÈRE élimination (demande du 2026-09-17) —
        fonction métier CENTRALE UNIQUE appelée aussi bien par le bouton
        "Annule Eliminer" que par le clic droit sur le dernier joueur
        éliminé (main.py: App._undo_last_elimination), jamais dupliquée.
        Ne prend AUCUN paramètre : la cible n'est jamais celle d'une
        sélection courante, toujours déterminée ici via get_last_
        eliminated_player() — RÈGLE ABSOLUE demandée par l'utilisateur :
        il ne doit jamais être possible d'annuler une élimination
        antérieure à la dernière.

        Voir la section "Annulation de la dernière élimination" plus haut
        pour le choix d'architecture (instantané complet avant/après,
        jamais une inversion mouvement par mouvement).

        SÉCURITÉ AVANT TOUT (demande explicite du 2026-09-17, "REFUSE
        plutôt que de tenter une reconstruction approximative") :
        revalide D'ABORD, un par un, que l'état ACTUEL correspond
        EXACTEMENT à l'état "APRÈS" mémorisé au moment de cette
        élimination — mêmes joueurs actifs, mêmes position/statut/bounty/
        bounty_won/kills/place pour chaque joueur concerné, mêmes tables
        (nom, capacité, active ou non), même fin de tournoi, même
        question de rééquilibrage en attente le cas échéant, aucune prime
        enregistrée depuis. La moindre divergence lève ValueError SANS
        AUCUNE écriture, plutôt que de deviner. Cette revalidation couvre
        notamment, sans code séparé pour chaque cas :
        - une élimination suivante a eu lieu depuis (get_last_eliminated_
          player() renvoie alors un autre joueur, refusé dès la première
          vérification) ;
        - un joueur a été ajouté/réintégré/retiré depuis (l'ensemble des
          joueurs actifs ne correspond plus) ;
        - une question de rééquilibrage guidé par la grosse blinde,
          encore ouverte au moment de cette élimination, a depuis reçu
          une réponse (son request_id a changé ou a disparu) ;
        - les tables ont été modifiées manuellement depuis.
        Chips/buyin/rebuy/addon/club/nom ne sont volontairement PAS
        vérifiés (voir _player_undo_state) : un rebuy ou un renommage
        entre-temps n'a aucun rapport avec l'élimination et ne bloque
        jamais son annulation.

        Si l'annulation aboutit : restaure atomiquement la ligne de
        chaque joueur concerné (y compris l'ancienne table/siège du
        joueur éliminé, ses primes, celles de l'éventuel éliminateur),
        l'état complet de tables_pk, la fin de tournoi le cas échéant,
        supprime les lignes bounty_events créées par cette élimination
        (élimination normale + éventuelle clôture de bounty du vainqueur
        PKO), annule proprement toute question de rééquilibrage que
        CETTE élimination avait créée ou modifiée (laisse intacte une
        question totalement étrangère, déjà présente avant elle et
        inchangée depuis), et efface l'instantané consommé. Renvoie la
        liste des mouvements de tables réellement nécessaires pour que
        chacun revienne à sa position d'avant (même format que
        rebalance_tables, réutilisable telle quelle par l'alerte de
        mouvement existante) — jamais un nouveau rebalance_tables() : on
        revient en arrière, on ne recalcule pas."""
        raw = self.get_setting("last_elimination_undo")
        if not raw:
            raise ValueError("Aucune élimination à annuler.")
        try:
            snapshot = json.loads(raw)
        except (TypeError, ValueError):
            raise ValueError(
                "Instantané d'annulation illisible : annulation impossible."
            )

        player_id = snapshot.get("player_id")
        last = self.get_last_eliminated_player()
        if last is None or last["id"] != player_id:
            raise ValueError(
                "Impossible d'annuler : ce n'est plus la dernière élimination "
                "(une autre élimination a eu lieu depuis, ou ce joueur n'est "
                "plus éliminé)."
            )

        # ---- Timeout (demande du 2026-09-17) : contrôlé ICI, dans la ----
        # logique métier elle-même, quelle que soit la voie d'appel (bouton
        # "Annule Eliminer", clic droit, ou tout futur appelant) — jamais
        # seulement côté interface (voir undo_last_elimination_available,
        # utilisée par main.py pour l'état du bouton/clic droit, mais qui
        # NE remplace PAS ce contrôle-ci). Calculé à partir d'elim_time
        # converti en epoch (voir _elim_time_to_epoch), jamais une
        # comparaison de texte HH:MM qui casserait au changement de
        # minute/heure/jour. RÈGLE ABSOLUE : 0 minute désactive
        # complètement la fonction (jamais "illimité") ; un délai déjà
        # écoulé (>=, pas seulement >) refuse — pile à l'expiration
        # incluse.
        timeout_minutes = self._undo_elimination_timeout_minutes()
        if timeout_minutes <= 0:
            raise ValueError(
                "Impossible d'annuler : « Annule Eliminer » est désactivé "
                "(Timeout pour Annuler Eliminer réglé à 0 minute dans "
                "Paramètres)."
            )
        elim_epoch = self._elim_time_to_epoch(last["elim_time"])
        if elim_epoch is None or (time.time() - elim_epoch) >= timeout_minutes * 60:
            raise ValueError(
                "Impossible d'annuler : le délai autorisé "
                f"({timeout_minutes} minute(s), voir Paramètres) pour "
                "annuler cette élimination est dépassé."
            )

        before = snapshot["before"]
        after = snapshot["after"]

        # ---- Revalidation stricte : refuse au moindre écart ------------
        tracked_ids = [int(pid) for pid in after["player_states"].keys()]
        expected_active_ids = {
            pid for pid in tracked_ids
            if (after["player_states"][str(pid)] or {}).get("status") == "active"
        }
        current_active_ids = {p["id"] for p in self.list_players(status="active")}
        if current_active_ids != expected_active_ids:
            raise ValueError(
                "Impossible d'annuler : la liste des joueurs actifs a changé "
                "depuis cette élimination (joueur ajouté, réintégré ou "
                "retiré entre-temps)."
            )
        for pid in tracked_ids:
            expected = after["player_states"][str(pid)]
            current = self._player_undo_state(pid)
            # elim_time exclu de CETTE comparaison (voir _comparable_
            # undo_state) : lu en LIVE pour le timeout (contrôlé
            # séparément juste après), sa valeur exacte au moment de
            # l'élimination n'a par ailleurs aucune incidence sur la
            # sécurité de la restauration table/siège/primes elle-même —
            # toujours restauré normalement plus bas (voir before[
            # "player_states"], qui, lui, conserve bien elim_time).
            if _comparable_undo_state(current) != _comparable_undo_state(expected):
                raise ValueError(
                    "Impossible d'annuler : l'état du tournoi a changé "
                    "depuis cette élimination — annulation refusée pour ne "
                    "pas produire une restauration incohérente."
                )

        current_tables = {t["id"]: dict(t) for t in self.list_tables(active_only=False)}
        expected_tables = {t["id"]: t for t in after["tables"]}
        if current_tables != expected_tables:
            raise ValueError(
                "Impossible d'annuler : la configuration des tables a "
                "changé depuis cette élimination."
            )

        if self.get_setting("tournament_end_epoch") != after["tournament_end_epoch"]:
            raise ValueError(
                "Impossible d'annuler : l'état de fin de tournoi a changé "
                "depuis cette élimination."
            )

        current_pending_id = (
            self.pending_rebalance["request_id"] if self.pending_rebalance else None
        )
        if current_pending_id != after["pending_rebalance_request_id"]:
            raise ValueError(
                "Impossible d'annuler : une question de rééquilibrage "
                "(grosse blinde) a été traitée depuis cette élimination."
            )

        current_bounty_max = self.conn.execute(
            "SELECT COALESCE(MAX(id), 0) m FROM bounty_events"
        ).fetchone()["m"]
        if current_bounty_max != after["bounty_events_max_id"]:
            raise ValueError(
                "Impossible d'annuler : d'autres primes ont été "
                "enregistrées depuis cette élimination."
            )

        # ---- Toutes les vérifications passent : restauration atomique --
        for pid_str, state in before["player_states"].items():
            if state is None:
                continue
            self.conn.execute(
                "UPDATE players SET status=?, table_id=?, seat=?, bounty=?, "
                "bounty_won=?, kills=?, place=?, elim_time=?, elim_round=?, "
                "eliminated_by_name=? WHERE id=?",
                (state["status"], state["table_id"], state["seat"], state["bounty"],
                 state["bounty_won"], state["kills"], state["place"], state["elim_time"],
                 state["elim_round"], state["eliminated_by_name"], int(pid_str)),
            )

        for t in before["tables"]:
            self.conn.execute(
                "UPDATE tables_pk SET name=?, max_seats=?, is_active=? WHERE id=?",
                (t["name"], t["max_seats"], t["is_active"], t["id"]),
            )

        self.conn.execute(
            "DELETE FROM bounty_events WHERE id > ?", (before["bounty_events_max_id"],)
        )

        if before["tournament_end_epoch"] is None:
            self.conn.execute("DELETE FROM settings WHERE key='tournament_end_epoch'")
        else:
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES ('tournament_end_epoch', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (before["tournament_end_epoch"],),
            )

        # La question de rééquilibrage actuellement affichée (déjà
        # revalidée identique à l'instantané "après" ci-dessus) n'a plus
        # lieu d'être si elle a été CRÉÉE ou MODIFIÉE par cette
        # élimination (before != after) — mais reste intacte si elle lui
        # est totalement étrangère (déjà présente, inchangée, avant même
        # cette élimination).
        if before["pending_rebalance_request_id"] != after["pending_rebalance_request_id"]:
            self.pending_rebalance = None

        self.conn.execute("DELETE FROM settings WHERE key='last_elimination_undo'")
        self.conn.commit()

        # Mouvements RÉELLEMENT nécessaires pour que chacun revienne à sa
        # position d'avant cette élimination (comparaison directe des
        # deux instantanés déjà capturés par eliminate_player) — jamais un
        # nouveau rebalance_tables() : on revient en arrière à l'identique,
        # on ne relance pas de calcul de rééquilibrage.
        table_names = {t["id"]: t["name"] for t in self.list_tables(active_only=False)}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        moves = []
        for pid_str, before_state in before["player_states"].items():
            after_state = after["player_states"].get(pid_str)
            if (before_state is None or after_state is None
                    or before_state["table_id"] == after_state["table_id"]):
                continue
            moves.append({
                "player_name": self.get_player(int(pid_str))["name"],
                "old_table_name": table_names.get(after_state["table_id"]),
                "old_seat": after_state["seat"],
                "new_table_name": table_names.get(before_state["table_id"]),
                "new_seat": before_state["seat"],
                "moved_at": now,
                "reason": MOVE_REASON_ELIMINATION_UNDO,
            })
        if moves:
            self.conn.execute("DELETE FROM seat_moves")
            for move in moves:
                self.conn.execute(
                    "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
                    "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
                    (move["player_name"], move["old_table_name"], move["old_seat"],
                     move["new_table_name"], move["new_seat"], move["moved_at"],
                     move["reason"]),
                )
            self.conn.commit()

        return moves

    def withdraw_player(self, player_id):
        """Retire un joueur de la liste active sans lui attribuer de place
        au classement (forfait / inscription annulée), contrairement à
        eliminate_player. Libère sa table/siège comme une élimination."""
        self.conn.execute(
            "UPDATE players SET status='withdrawn', place=NULL, elim_time=?, "
            "table_id=NULL, seat=NULL WHERE id=?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), player_id),
        )
        # Ce joueur était compté dans "active" (voir eliminate_player :
        # place = nb d'actifs restants au moment de l'élimination) pour
        # TOUT joueur déjà éliminé jusqu'ici, tant qu'il n'avait pas
        # encore forfait. Comme un forfait ne prend jamais de place au
        # classement (place reste NULL ci-dessus), il faut le retirer
        # rétroactivement du calcul : le nombre réel de participants
        # classés diminue d'une unité, donc chaque rang déjà attribué
        # doit être décalé d'une place vers le haut (ex : 3e devient 2e)
        # pour ne pas laisser de trou dans le classement final.
        self.conn.execute(
            "UPDATE players SET place = place - 1 WHERE status='eliminated' AND place IS NOT NULL"
        )
        self.conn.commit()
        return self.rebalance_tables(record_moves=False)

    def reinstate_player(self, player_id):
        starting_chips = self.get_setting_int("starting_chips", 10000)
        bounty_amount = self.get_setting_int("bounty_amount", 0)
        was_withdrawn = self.get_player(player_id)["status"] == "withdrawn"
        self.conn.execute(
            "UPDATE players SET status='active', place=NULL, elim_time=NULL, "
            "chips=?, bounty=? WHERE id=?",
            (starting_chips, bounty_amount, player_id),
        )
        if was_withdrawn:
            # Symétrique du décalage fait dans withdraw_player : ce joueur
            # réintègre le décompte des participants classés, chaque rang
            # déjà attribué redescend donc d'une place (ex : 2e redevient
            # 3e). Sans effet s'il était éliminé (pas forfait) : son
            # départ n'avait alors jamais touché aux rangs des autres.
            self.conn.execute(
                "UPDATE players SET place = place + 1 WHERE status='eliminated' AND place IS NOT NULL"
            )
        self.conn.commit()
        self._seat_player(player_id)
        # Réintégrer un joueur peut faire repasser le nombre d'actifs
        # au-dessus de 1 : la partie n'est alors plus terminée, on efface
        # l'heure de fin figée (voir eliminate_player) pour que "Durée"
        # se remette à compter sur le chrono projecteur.
        if len(self.list_players(status="active")) > 1:
            self.set_setting("tournament_end_epoch", 0)
        return self.rebalance_tables(record_moves=False)

    def delete_player(self, player_id):
        self.conn.execute("DELETE FROM players WHERE id=?", (player_id,))
        self.conn.commit()
        return self.rebalance_tables(record_moves=False)

    # ---------- seating / balancing ----------
    def _seat_player(self, player_id):
        """Assoit un joueur à la table la moins remplie, sur le premier siège libre."""
        tables = self.list_tables()
        if not tables:
            self._open_or_reopen_table()
            tables = self.list_tables()
        best_table = None
        best_count = None
        for t in tables:
            occ = self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            if occ < t["max_seats"] and (best_count is None or occ < best_count):
                best_table, best_count = t, occ
        if best_table is None:
            best_table = self._open_or_reopen_table()
        taken = {
            r["seat"]
            for r in self.conn.execute(
                "SELECT seat FROM players WHERE table_id=? AND status='active'",
                (best_table["id"],),
            )
        }
        seat = 1
        while seat in taken:
            seat += 1
        self.conn.execute(
            "UPDATE players SET table_id=?, seat=? WHERE id=?",
            (best_table["id"], seat, player_id),
        )
        self.conn.commit()

    def _relocate_excess_players(self, table_id, target_max_seats, move_reasons, reason):
        """Déplace, un par un, le joueur au SIÈGE LE PLUS ÉLEVÉ de
        `table_id` vers la table active la moins pleine ayant de la place
        (jamais `table_id` lui-même — voir _move_player_to_least_full_
        table), jusqu'à ce que son occupation soit ≤ `target_max_seats`.

        Cœur de la garantie "aucune table normale ne dépasse jamais sa
        capacité" (architecture validée le 2026-09-10) : appelée
        AVANT toute écriture qui abaisserait tables_pk.max_seats en
        dessous de l'occupation actuelle (voir rebalance_tables) — jamais
        l'inverse, pour qu'il n'existe jamais d'état, même transitoire,
        où une table est enregistrée à une capacité inférieure à son
        occupation réelle.

        Règle de choix déterministe, jamais liée au guidage par la grosse
        blinde (ce mouvement doit pouvoir s'exécuter immédiatement et de
        façon synchrone — une réponse de téléphone est par nature
        asynchrone, incompatible avec la garantie de validité permanente,
        voir la docstring de rebalance_tables) : toujours le siège le
        plus élevé en premier.

        `move_reasons` : dict {player_id: raison} partagé avec l'appelant
        (voir MOVE_REASON_* et rebalance_tables) — mis à jour ici pour
        que le mouvement soit correctement étiqueté dans l'historique.

        Filet de sécurité anti-boucle infinie : si un déplacement ne
        change rien (aucune autre table active n'a de place, ce qui ne
        devrait normalement jamais arriver puisque le nombre de tables
        nécessaires est calculé en amont pour accueillir tout le monde),
        journalise via _log_defensive_relocation et s'arrête plutôt que
        de boucler indéfiniment — jamais un `while True` sans garde-fou
        sur des données venant d'un fichier .tournoi potentiellement
        déjà incohérent (voir check_table_integrity)."""
        for _ in range(200):  # garde-fou anti boucle infinie, comme ailleurs dans ce fichier
            occupants = self.conn.execute(
                "SELECT id FROM players WHERE table_id=? AND status='active' ORDER BY seat DESC",
                (table_id,),
            ).fetchall()
            if len(occupants) <= target_max_seats:
                return
            mover_id = occupants[0]["id"]
            before_table_id = table_id
            self._move_player_to_least_full_table(mover_id, exclude_table_id=table_id)
            after_table_id = self.conn.execute(
                "SELECT table_id FROM players WHERE id=?", (mover_id,)
            ).fetchone()["table_id"]
            if after_table_id == before_table_id:
                # _move_player_to_least_full_table n'a rien pu faire
                # (aucune autre table avec de la place) : ne devrait
                # jamais arriver si n_tables_needed a été calculé
                # correctement en amont. On s'arrête plutôt que de
                # boucler indéfiniment sur le même joueur.
                _log_defensive_relocation(
                    f"table_id={table_id} reste à {len(occupants)} joueurs "
                    f"(cible {target_max_seats}) : aucune autre table active "
                    f"n'a de place pour le siège le plus élevé (player_id={mover_id})."
                )
                return
            move_reasons[mover_id] = reason

    def rebalance_tables(self, record_moves=False):
        """Rééquilibre les tables actives : comble les sièges vides en déplaçant
        des joueurs des tables les plus pleines, et ferme les tables devenues
        inutiles quand le nombre de joueurs restants tient sur moins de
        tables — toujours en partant du numéro de table le plus haut (les
        tables gardent leur numéro toute la partie, jamais renumérotées :
        voir plus bas). Si record_moves est vrai, archive chaque déplacement réel (ancienne
        table/siège -> nouvelle table/siège) dans l'historique des
        mouvements (onglet Mouvements) — ce n'est le cas que pour les
        rééquilibrages déclenchés par une élimination de joueur. Renvoie
        dans tous les cas la liste des mouvements effectués.

        Ne fait rien quand il reste 0 ou 1 joueur actif : à 1 seul joueur
        actif, le tournoi est terminé (il n'y a plus personne à équilibrer
        entre tables) — sans ce garde-fou, le vainqueur pouvait se
        retrouver déplacé vers une table "consolidée" au tout dernier
        rééquilibrage et apparaître à tort dans l'historique des
        mouvements alors que la partie est finie."""
        active_players = [
            dict(p) for p in self.list_players(status="active")
        ]
        n_active = len(active_players)
        if n_active <= 1:
            return []

        before_state = {p["id"]: (p["table_id"], p["seat"]) for p in active_players}
        # Raison du déplacement, par player_id (voir MOVE_REASON_*) —
        # renseignée par chaque mécanisme de cette fonction au fur et à
        # mesure qu'il déplace réellement quelqu'un ; utilisée à la toute
        # fin pour étiqueter chaque mouvement dans l'historique (onglet
        # Mouvements). Si un même joueur est déplacé plusieurs fois au
        # cours de CET appel (ex : mise en conformité PUIS équilibrage
        # sportif), seule la DERNIÈRE raison est conservée — c'est
        # l'explication de son emplacement final qui est affichée, pas
        # l'historique intermédiaire de cet appel.
        move_reasons = {}

        max_seats = self.get_setting_int("max_seats_per_table", 9)
        min_players = self.get_setting_int("min_players_per_table", 4)
        tables = list(self.list_tables())
        n_tables_needed = max(1, math.ceil(n_active / max_seats))

        # En dessous du seuil minimum de joueurs par table, on regroupe
        # davantage (une table de plus en moins) tant que c'est possible
        # sans dépasser le nombre de sièges disponibles par table.
        if min_players > 1:
            while (
                n_tables_needed > 1
                and n_active / n_tables_needed < min_players
                and n_active <= (n_tables_needed - 1) * max_seats
            ):
                n_tables_needed -= 1

        # Convention "table finale" (voir FINAL_TABLE_MAX_SEATS) : au
        # poker, la toute dernière table peut accueillir jusqu'à 10
        # joueurs même si "Nombre de sièges par table" est réglé plus bas
        # (ex. 8) — on ne scinde jamais les derniers joueurs entre deux
        # tables alors qu'ils tiendraient sur une seule table finale.
        if n_active <= FINAL_TABLE_MAX_SEATS:
            n_tables_needed = 1

        # Ouvre des tables supplémentaires si le nombre de sièges disponibles
        # ne suffit plus (ex : réduction du nombre de sièges par table en
        # cours de tournoi).
        if len(tables) < n_tables_needed:
            for _ in range(n_tables_needed - len(tables)):
                self._open_or_reopen_table()
            tables = list(self.list_tables())

        # Si consolider sur une seule table finale dépasse le nombre de
        # sièges normalement configuré (cas ci-dessus), relève
        # ponctuellement la capacité de CETTE table pour qu'elle puisse
        # réellement tous les accueillir — sinon _seat_player refuserait
        # de la remplir au-delà de son max_seats actuel et rouvrirait une
        # table à la place, annulant la fusion voulue.
        if n_tables_needed == 1 and n_active > max_seats:
            survivor = min(tables, key=self._table_display_number)
            if survivor["max_seats"] < n_active:
                self.conn.execute(
                    "UPDATE tables_pk SET max_seats=? WHERE id=?",
                    (n_active, survivor["id"]),
                )
                self.conn.commit()
                tables = list(self.list_tables())
        elif any(t["max_seats"] != max_seats for t in tables):
            # Repli : plusieurs tables sont de nouveau nécessaires (ex :
            # d'autres joueurs se sont inscrits depuis une fusion "table
            # finale" ci-dessus) — sans ça, la table déjà ponctuellement
            # élargie garderait pour toujours une capacité différente des
            # autres, sans raison apparente une fois la fusion plus
            # nécessaire.
            #
            # SÉQUENCE ATOMIQUE (architecture validée le 2026-09-10,
            # correctif du dépassement de capacité persistant — voir
            # diagnostic "Table 1 : 9/10 joueurs, max_seats=8") : on
            # déplace D'ABORD tout joueur excédentaire de chaque table
            # dont la capacité va être abaissée, et SEULEMENT ENSUITE on
            # écrit la nouvelle valeur de max_seats — jamais l'inverse.
            # Sans cet ordre, une table pouvait rester enregistrée à une
            # capacité inférieure à son occupation réelle (ex : 10
            # joueurs sur une table repassée à max_seats=8) de façon
            # PERMANENTE, la seule mécanique censée la résorber ensuite
            # (l'ancienne boucle "de correction de dépassement", plus
            # bas) ne sachant jamais déplacer quelqu'un vers une AUTRE
            # table. _relocate_excess_players() est déterministe (siège
            # le plus élevé, voir sa docstring) : ce premier mouvement de
            # mise en conformité ne peut jamais dépendre d'une réponse de
            # la grosse blinde, par nature asynchrone — incompatible avec
            # la garantie "jamais de surcapacité, même transitoire".
            for t in tables:
                if t["max_seats"] > max_seats:
                    self._relocate_excess_players(
                        t["id"], max_seats, move_reasons, MOVE_REASON_STRUCTURAL
                    )
            self.conn.execute("UPDATE tables_pk SET max_seats=?", (max_seats,))
            self.conn.commit()
            tables = list(self.list_tables())

        # Ferme les tables en trop — toujours en partant du numéro le PLUS
        # HAUT (comme dans un vrai tournoi : les tables sont pré-numérotées
        # à leur installation et gardent ce numéro toute la partie ; on
        # regroupe progressivement les joueurs vers les tables 1, 2, 3...
        # jusqu'à la table finale n°1, jamais l'inverse). Trier par nombre
        # de joueurs (vider la plus petite d'abord) déplacerait moins de
        # monde en moyenne, mais casserait cette convention : une table
        # basse pourrait fermer avant une table haute plus vide, ce qui ne
        # correspond à aucune pratique réelle de gestion de tournoi et
        # obligeait jusqu'ici à renuméroter les tables restantes (source
        # de confusion dans l'historique des mouvements — deux tables
        # différentes affichées sous le même nom à des moments différents).
        if len(tables) > n_tables_needed:
            occ_by_table = {}
            for p in active_players:
                occ_by_table.setdefault(p["table_id"], []).append(p)
            # Trié par NUMÉRO affiché (extrait du nom "Table N"), pas par id
            # interne : les deux coïncident normalement (add_table nomme
            # toujours la nouvelle table d'après le nombre total de tables
            # jamais créées), mais un fichier .tournoi hérité d'une
            # version antérieure à la suppression de l'ancienne
            # renumérotation pouvait avoir des id et des numéros affichés
            # décorrélés — d'où par exemple "Table 1, Table 8, Table 9"
            # restantes au lieu de "Table 1, Table 2, Table 3" quand on se
            # fie à l'id brut plutôt qu'au numéro réellement affiché.
            tables_sorted = sorted(tables, key=lambda t: -self._table_display_number(t))
            to_close = tables_sorted[: len(tables) - n_tables_needed]
            # Regroupe TOUS les joueurs évincés de TOUTES les tables fermées
            # dans CE MÊME passage (ex : fusion directe vers la table
            # finale, qui ferme souvent plusieurs tables d'un coup) en une
            # seule liste, plutôt que de les réasseoir table fermée par
            # table fermée : sans ça, les joueurs de la première table
            # fermée occuperaient systématiquement les places les plus
            # "précoces", un biais détectable même si chaque groupe était
            # mélangé séparément.
            players_to_move = []
            for t in to_close:
                players_to_move.extend(occ_by_table.get(t["id"], []))
                self.close_table(t["id"])
            for p in players_to_move:
                self.conn.execute(
                    "UPDATE players SET table_id=NULL, seat=NULL WHERE id=?",
                    (p["id"],),
                )
            self.conn.commit()
            # Cassage de table (contrairement au simple équilibrage
            # ci-dessous, laissé inchangé) : répartition ALÉATOIRE des
            # joueurs évincés sur les places disponibles des tables
            # restantes. _seat_player() choisit toujours la table la moins
            # remplie puis le premier siège libre — une séquence de places
            # entièrement déterminée par l'état d'occupation courant,
            # jamais par l'identité du joueur passé en argument. Mélanger
            # l'ORDRE des joueurs avant de les réasseoir un par un dans
            # cette même séquence de places (déjà équilibrée) équivaut donc
            # à une bijection aléatoire uniforme joueur -> place, sans
            # toucher à _seat_player() elle-même (qui doit continuer à
            # garantir des effectifs équilibrés).
            random.shuffle(players_to_move)
            for p in players_to_move:
                self._seat_player(p["id"])
                move_reasons[p["id"]] = MOVE_REASON_TABLE_CLOSURE

        # Ré-équilibre : si la table la plus pleine et la moins pleine ont
        # un écart >= 2, un joueur doit passer de l'une à l'autre.
        #
        # Version TEST "grosse blinde" (voir pending_rebalance, docstring
        # de Database.__init__, _detect_simple_rebalance_need et
        # resolve_pending_rebalance plus bas) : un rééquilibrage SIMPLE
        # comme celui-ci (PAS un cassage de table, traité plus haut —
        # random.shuffle des joueurs évincés, INCHANGÉ) ne choisit plus
        # lui-même qui bouge. Dès qu'un mouvement est nécessaire, on POSE
        # LA QUESTION ("quel siège est actuellement grosse blinde ?", voir
        # App._open_pending_rebalance_dialog et remote_control.py côté
        # téléphone) au lieu d'agir tout de suite, et on s'arrête là pour
        # CET appel : un seul mouvement simple est décidé par appel — la
        # suite ne reprendra qu'au prochain appel de rebalance_tables(),
        # déclenché par resolve_pending_rebalance() une fois la réponse
        # traitée (ou par tout autre événement entre-temps) — jamais en
        # rappelant cette méthode elle-même pendant qu'on attend, ce qui
        # bloquerait le thread principal Tkinter. Une seule question à la
        # fois : si une question posée par un appel précédent est toujours
        # sans réponse, on n'en pose pas une seconde — mais on la
        # REVALIDE D'ABORD sur l'état COURANT (au lieu de se contenter de
        # vérifier que sa table existe encore) : rebalance_tables() peut
        # être rappelée (par une élimination CONCURRENTE sur une AUTRE
        # table, un ajout de joueur...) pendant qu'une demande est encore
        # affichée sur le téléphone, sans attendre sa réponse — voir
        # eliminate_player, qui rappelle toujours rebalance_tables()
        # inconditionnellement. Sans cette revalidation ICI (au moment du
        # recalcul, PAS seulement à la réponse — resolve_pending_rebalance
        # le fait déjà, mais seulement quand l'utilisateur répond, ce qui
        # peut prendre un moment), une demande devenue obsolète entre-temps
        # (écart déjà résorbé, ou déplacé sur une autre table par un
        # cassage de table ci-dessus) restait affichée sur le téléphone
        # jusqu'à ce que l'utilisateur y réponde pour rien — symptôme
        # observé : une deuxième question de grosse blinde alors qu'un
        # seul déplacement était en réalité nécessaire.
        if self.pending_rebalance is not None:
            still_valid = any(
                t["id"] == self.pending_rebalance["table_id"] for t in self.list_tables()
            )
            if not still_valid:
                # Cassage de table (ci-dessus) : la table de cette demande
                # vient d'être fermée. Plus rien à répondre.
                self.pending_rebalance = None
            else:
                need = self._detect_simple_rebalance_need()
                if need is None:
                    # Écart déjà résorbé entre-temps (par le déplacement
                    # d'un autre joueur, une élimination ailleurs...) :
                    # cette demande n'a plus d'objet, retirée sans
                    # attendre une réponse qui ne déplacerait plus
                    # personne de toute façon (voir resolve_pending_
                    # rebalance, étape 2 — même logique, appliquée ici
                    # PLUS TÔT, dès ce recalcul plutôt qu'à la réponse).
                    self.pending_rebalance = None
                else:
                    source_table, occupied_seats = need
                    if source_table["id"] != self.pending_rebalance["table_id"]:
                        # Le besoin a basculé sur une AUTRE table entre-
                        # temps : l'ancienne demande ne correspond plus à
                        # rien de valide — remplacée par une nouvelle
                        # demande cohérente, avec un NOUVEL identifiant
                        # (l'ancien ne doit plus jamais pouvoir "gagner",
                        # même par coïncidence côté téléphone — même
                        # principe que l'étape 3 de resolve_pending_
                        # rebalance, appliqué ici au recalcul plutôt qu'à
                        # la réponse).
                        self.pending_rebalance = {
                            "request_id": uuid.uuid4().hex,
                            "table_id": source_table["id"],
                            "table_name": source_table["name"],
                            "seats": occupied_seats,
                            "record_moves": self.pending_rebalance["record_moves"],
                            "before_state": dict(before_state),
                            "created_at": time.time(),
                        }
                    else:
                        # Toujours la même table source : la demande reste
                        # valide TELLE QUELLE — même request_id, jamais
                        # recréée inutilement (une nouvelle demande à
                        # chaque appel casserait la réponse déjà envoyée
                        # par un téléphone entre-temps, voir la règle de
                        # consommation de resolve_pending_rebalance). Seuls
                        # les sièges affichés sont rafraîchis si
                        # l'occupation de CETTE table a changé (ex : un de
                        # ses propres joueurs éliminé entre-temps, sans que
                        # ça ne change QUELLE table doit donner un joueur).
                        self.pending_rebalance["seats"] = occupied_seats

        if self.pending_rebalance is None:
            # PHASE 1 (mise en place initiale, voir clock_started et
            # DEFAULT_SETTINGS) : avant le tout premier démarrage du
            # chronomètre, il n'y a encore aucune intervention possible
            # des téléphones (créations de tables/inscriptions
            # successives) — le guidage par la grosse blinde ne doit
            # JAMAIS être proposé à ce stade, même si la préférence
            # "Équilibrage guidé par la grosse blinde" est activée
            # (valeur par défaut). On force donc le mécanisme historique
            # automatique (branche `else` ci-dessous) tant que
            # clock_started vaut 0 — demande explicite du 2026-09-10.
            # clock_started ne repasse jamais de 1 à 0 pour un même
            # fichier (voir set_settings appelé par App au premier
            # démarrage), donc aucun pending_rebalance ne peut avoir été
            # créé avant que cette condition ne devienne vraie.
            guided = self._bb_rebalance_prompt_enabled() and self.get_setting_int("clock_started", 0) == 1
            if guided:
                need = self._detect_simple_rebalance_need()
                if need is not None:
                    source_table, occupied_seats = need
                    # La table de DESTINATION n'est volontairement pas
                    # mémorisée ici — elle sera recalculée à l'état courant
                    # au moment de la réponse (resolve_pending_rebalance ->
                    # _move_player_to_least_full_table), sans changer sa
                    # logique de choix actuelle (consigne explicite de
                    # cette version TEST). before_state (qui était où AVANT
                    # ce rééquilibrage-ci) est mémorisé tel quel dans la
                    # demande : c'est la référence historique nécessaire à
                    # _legacy_pick_mover si "Continuer sans indiquer la BB"
                    # est utilisé plus tard pour y répondre (voir
                    # resolve_pending_rebalance) — y compris si cette
                    # demande est ensuite recréée pour une autre table
                    # entre-temps.
                    self.pending_rebalance = {
                        "request_id": uuid.uuid4().hex,
                        "table_id": source_table["id"],
                        "table_name": source_table["name"],
                        "seats": occupied_seats,
                        "record_moves": record_moves,
                        "before_state": dict(before_state),
                        "created_at": time.time(),
                    }
            else:
                # Soit la préférence "Équilibrage guidé par la grosse
                # blinde" est désactivée (voir Paramètres), soit on est
                # encore en PHASE 1 (clock_started == 0, voir ci-dessus) :
                # dans les deux cas, jamais de question posée, jamais de
                # pending_rebalance créé — le mécanisme historique choisit
                # directement qui bouge (_legacy_pick_mover, EXACTEMENT
                # comme "Continuer sans indiquer la BB"), en boucle tant
                # qu'un écart persiste — repris ici du mécanisme d'origine
                # (avant cette version TEST) pour résoudre tous les
                # mouvements nécessaires en un seul appel, sans dépendre
                # d'un enchaînement de réponses.
                for _ in range(200):  # garde-fou anti boucle infinie
                    need = self._detect_simple_rebalance_need()
                    if need is None:
                        break
                    source_table, _occupied_seats = need
                    mover_id = self._legacy_pick_mover(source_table["id"], before_state)
                    if mover_id is None:
                        break
                    self._move_player_to_least_full_table(mover_id, exclude_table_id=source_table["id"])
                    move_reasons[mover_id] = MOVE_REASON_AUTO_BALANCE

        # Passe défensive finale (architecture validée le 2026-09-10) —
        # DEUX cas bien distincts, jamais confondus :
        #
        # 1. Occupation réellement au-dessus de max_seats (violation
        #    structurelle) : ne devrait JAMAIS arriver ici — la séquence
        #    atomique plus haut (voir MOVE_REASON_STRUCTURAL) est censée
        #    garantir cet invariant EN AMONT. Ce n'est donc qu'un filet
        #    de sécurité générique (voir sa docstring pour la raison de
        #    ne pas s'appuyer sur lui comme mécanisme principal) : s'il
        #    doit intervenir, c'est le signe d'un chemin non couvert
        #    ailleurs — jamais silencieux, toujours journalisé (voir
        #    _log_defensive_relocation).
        # 2. Occupation DÉJÀ dans la limite, mais un numéro de SIÈGE
        #    dépasse encore max_seats (ex : juste après une réduction du
        #    nombre de sièges par table, avec de la place ailleurs sur
        #    CETTE MÊME table) : cas normal et attendu, pas un bug — on
        #    recompacte simplement le numéro de siège au sein de la même
        #    table (comportement d'origine, inchangé). Ceci n'est jamais
        #    compté comme un "mouvement" (pas de changement de TABLE, voir
        #    plus bas) : au poker, personne ne se déplace juste pour
        #    combler un trou.
        for t in self.list_tables():
            occ_count = self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            if occ_count > t["max_seats"]:
                _log_defensive_relocation(
                    f"{t['name']} (id={t['id']}) a {occ_count} joueurs actifs pour "
                    f"max_seats={t['max_seats']} en fin de rebalance_tables() — "
                    f"la séquence atomique en amont aurait dû empêcher cet état."
                )
                self._relocate_excess_players(
                    t["id"], t["max_seats"], move_reasons, MOVE_REASON_STRUCTURAL
                )

            occupants = self.conn.execute(
                "SELECT id, seat FROM players WHERE table_id=? AND status='active' "
                "ORDER BY seat",
                (t["id"],),
            ).fetchall()
            overflow = [p for p in occupants if p["seat"] > t["max_seats"]]
            if not overflow:
                continue
            taken = {p["seat"] for p in occupants if p["seat"] <= t["max_seats"]}
            seat = 1
            for p in overflow:
                while seat in taken:
                    seat += 1
                self.conn.execute("UPDATE players SET seat=? WHERE id=?", (seat, p["id"]))
                taken.add(seat)
                seat += 1
        self.conn.commit()

        # Calcule les déplacements réels (avant -> après) et les archive.
        # Seul un changement de TABLE compte comme un "mouvement" (alerte,
        # pause du chrono, historique) : un simple recompactage de numéro
        # de siège au sein de la même table (ex : combler le siège laissé
        # vide par un joueur éliminé) ne demande à personne de se déplacer
        # physiquement, donc pas d'alerte pour ça — sur un SNG à une seule
        # table, ça évite une alerte à chaque élimination alors que
        # personne ne bouge réellement de table. Les tables ne sont plus
        # jamais renommées (voir plus haut : on ferme toujours la table la
        # plus haute, jamais de renumérotation), donc un même id de table a
        # forcément le même nom avant et après — un seul dictionnaire de
        # noms suffit, plus besoin de distinguer avant/après ni de filet de
        # sécurité contre une coïncidence de nom.
        after_players = [dict(p) for p in self.list_players(status="active")]
        table_names = {t["id"]: t["name"] for t in self.list_tables(active_only=False)}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        moves = []
        for p in after_players:
            old_table_id, old_seat = before_state.get(p["id"], (None, None))
            new_table_id, new_seat = p["table_id"], p["seat"]
            if old_table_id == new_table_id:
                continue
            old_table_name = table_names.get(old_table_id)
            new_table_name = table_names.get(new_table_id)
            # Raison affichée dans l'onglet Mouvements (voir MOVE_REASON_*
            # et move_reasons plus haut) : celle du mécanisme qui a
            # DÉTERMINÉ l'emplacement final de ce joueur pour cet appel —
            # repli sur l'équilibrage automatique si, pour une raison
            # inattendue, aucun mécanisme ne l'a explicitement étiqueté
            # (ne devrait pas arriver, chaque chemin de cette fonction
            # étiquette désormais ses propres mouvements).
            move = {
                "player_name": p["name"],
                "old_table_name": old_table_name,
                "old_seat": old_seat,
                "new_table_name": new_table_name,
                "new_seat": new_seat,
                "moved_at": now,
                "reason": move_reasons.get(p["id"], MOVE_REASON_AUTO_BALANCE),
            }
            moves.append(move)
        if moves and record_moves:
            # On efface les mouvements précédents : l'onglet Mouvements
            # n'affiche que le dernier lot de déplacements en date, pas un
            # historique cumulatif.
            self.conn.execute("DELETE FROM seat_moves")
            for move in moves:
                self.conn.execute(
                    "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
                    "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
                    (move["player_name"], move["old_table_name"], move["old_seat"],
                     move["new_table_name"], move["new_seat"], move["moved_at"],
                     move["reason"]),
                )
            self.conn.commit()
        return moves

    def check_table_integrity(self):
        """Détection SEULE, en lecture seule, d'un fichier .tournoi déjà
        incohérent au sens de la capacité des tables (architecture
        validée le 2026-09-10) — ne modifie RIEN, ne déplace personne :
        appelée une fois à l'ouverture du fichier (voir App.__init__
        dans main.py, juste après _align_primes_enabled_on_open) pour
        avertir le responsable plutôt que de réparer automatiquement un
        ancien fichier (demande explicite : "détection + avertissement
        seulement, aucune réparation automatique pour l'instant").

        Un seul invariant vérifié ici : occupation réelle d'une table
        active > tables_pk.max_seats de CETTE table (pas la valeur
        globale "Nombre de sièges par table" — une vraie table finale a
        légitimement son propre max_seats relevé jusqu'à 10, voir
        FINAL_TABLE_MAX_SEATS, ce qui n'est jamais un problème). Le cas
        d'un simple numéro de siège isolé dépassant max_seats SANS
        dépassement d'occupation n'est volontairement PAS remonté ici :
        inoffensif (voir la passe défensive de rebalance_tables, qui le
        recompacte silencieusement dès le prochain rééquilibrage), pas
        une incohérence à signaler à l'utilisateur.

        Renvoie une liste de dicts {table_name, occupation, max_seats} —
        vide si le fichier est cohérent."""
        problems = []
        for t in self.list_tables():
            occ = self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            if occ > t["max_seats"]:
                problems.append({
                    "table_name": t["name"], "occupation": occ, "max_seats": t["max_seats"],
                })
        return problems

    # ---------- rééquilibrage simple : question "grosse blinde" (TEST) ----------
    def _bb_rebalance_prompt_enabled(self):
        """Préférence globale (voir BB_REBALANCE_PROMPT_PREF_KEY) : True
        par défaut, désactivée seulement si explicitement enregistrée à
        False (case décochée dans Paramètres). Relue à CHAQUE appel
        (jamais mise en cache) : un changement de ce réglage prend ainsi
        effet immédiatement sur le prochain rééquilibrage, sans redémarrer
        l'application."""
        return export_prefs.load_value(BB_REBALANCE_PROMPT_PREF_KEY, True) is not False

    def _detect_simple_rebalance_need(self):
        """Détecte si un rééquilibrage SIMPLE (pas un cassage de table,
        qui reste géré séparément plus haut dans rebalance_tables) est
        nécessaire à l'état COURANT : renvoie (table, occupied_seats) où
        `table` est la ligne de la table qui doit donner un joueur (la
        plus pleine, si l'écart avec la moins pleine est >= 2 ET que
        celle-ci a encore de la place) et `occupied_seats` la liste triée
        de ses sièges actuellement occupés ; renvoie None si aucun
        mouvement de ce type n'est nécessaire.

        Factorise EXACTEMENT la détection utilisée à la fois par
        rebalance_tables() (pour savoir s'il faut poser une nouvelle
        question) et par resolve_pending_rebalance() (pour revalider une
        question existante quand l'état a changé depuis qu'elle a été
        posée — voir sa docstring) : les deux doivent s'accorder sur la
        même notion de "toujours nécessaire", sans quoi une réponse
        pourrait être acceptée ou refusée de façon incohérente selon qui
        appelle.

        GARDE DE SÉPARATION DES NOTIONS (architecture validée le
        2026-09-10, voir MOVE_REASON_*) : une table déjà EN SURCAPACITÉ
        par rapport à SA PROPRE tables_pk.max_seats n'est jamais
        considérée ici comme candidate à l'équilibrage SPORTIF — ce n'est
        pas un simple écart de remplissage, c'est une violation
        structurelle, qui ne doit jamais être proposée au guidage par la
        grosse blinde (jamais de question posée pour ça, jamais de
        dépendance à une réponse humaine). Sa résorption relève
        exclusivement de _relocate_excess_players(), déjà exécutée en
        amont dans rebalance_tables() ; si elle subsistait malgré tout
        (ne devrait jamais arriver), la passe défensive finale de
        rebalance_tables() la rattrape, jamais ce détecteur-ci."""
        tables = list(self.list_tables())
        if len(tables) < 2:
            return None
        counts = []
        for t in tables:
            occ = self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            if occ > t["max_seats"]:
                # Violation structurelle, pas un candidat à l'équilibrage
                # sportif (voir docstring ci-dessus) — ignorée ici.
                continue
            counts.append((occ, t))
        if len(counts) < 2:
            return None
        counts.sort(key=lambda x: x[0])
        smallest_count, smallest_table = counts[0]
        largest_count, largest_table = counts[-1]
        if largest_count - smallest_count < 2:
            return None
        if smallest_count >= smallest_table["max_seats"]:
            return None
        occupied_seats = sorted(
            r["seat"] for r in self.conn.execute(
                "SELECT seat FROM players WHERE table_id=? AND status='active'",
                (largest_table["id"],),
            )
        )
        if not occupied_seats:
            return None
        return largest_table, occupied_seats

    def _legacy_pick_mover(self, table_id, before_state):
        """Algorithme HISTORIQUE de choix du joueur à déplacer d'une table
        lors d'un simple rééquilibrage — EXACTEMENT celui qui existait
        avant la version TEST "grosse blinde" (voir l'historique git de
        rebalance_tables) : préfère un joueur déjà en mouvement PENDANT LE
        MÊME rééquilibrage (avant_state différent de table_id — ex : un
        joueur replacé ici par un cassage de table plus tôt dans le même
        appel de rebalance_tables) plutôt qu'un joueur assis à cette table
        depuis le début de ce rééquilibrage ; celui-ci compte déjà comme
        "déplacé" quoi qu'il arrive, le déranger ne coûte donc rien de
        plus — alors que déplacer quelqu'un de stable sans nécessité crée
        un mouvement évitable dans l'historique (onglet Mouvements). Ne
        change rien au résultat final (nombre de joueurs par table) :
        seulement LEQUEL bouge. Retombe sur le premier candidat trouvé
        (ordre naturel de la requête, sans tri) si personne n'est déjà en
        mouvement.

        `before_state` : dict {player_id: (table_id, seat)} capturé par
        l'appelant AVANT le début de ce rééquilibrage (voir
        rebalance_tables et pending_rebalance["before_state"]) ; un dict
        vide revient à toujours prendre le premier candidat trouvé (aucune
        préférence possible sans historique). Utilisé UNIQUEMENT par
        "Continuer sans désigner le joueur" (voir resolve_pending_
        rebalance) — une réponse UTG explicite (le téléphone désigne
        directement le joueur à déplacer, voir _player_still_at_table)
        n'a jamais besoin de cette préférence.
        Renvoie None si cette table n'a plus aucun joueur actif."""
        candidates = self.conn.execute(
            "SELECT id FROM players WHERE table_id=? AND status='active'",
            (table_id,),
        ).fetchall()
        if not candidates:
            return None
        mover = next(
            (c for c in candidates if before_state.get(c["id"], (None, None))[0] != table_id),
            candidates[0],
        )
        return mover["id"]

    def _player_still_at_table(self, table_id, player_id):
        """`player_id` si ce joueur est ENCORE actif ET assis à `table_id`
        MAINTENANT, sinon None (chantier "sélection directe du joueur
        UTG", 2026-09-24 — remplace _next_active_seat_player, qui
        dérivait le joueur à déplacer d'un siège "grosse blinde" indiqué :
        le téléphone désigne désormais directement le joueur, cette
        fonction se contente de revalider son identité EXACTE contre
        l'état COURANT, jamais un instantané mémorisé au moment de la
        question — même principe de fraîcheur que l'ancienne fonction,
        appliqué à une identité de joueur plutôt qu'à un numéro de
        siège). Couvre à la fois "joueur éliminé entre-temps" (status
        n'est plus 'active') et "joueur déplacé vers une autre table
        entre-temps" (table_id ne correspond plus) en une seule
        requête — l'appelant (resolve_pending_rebalance) traite un
        résultat None comme une réponse INVALIDE, exactement comme
        l'ancien siège introuvable/obsolète : aucune substitution par un
        autre joueur, jamais."""
        row = self.conn.execute(
            "SELECT id FROM players WHERE id=? AND table_id=? AND status='active'",
            (player_id, table_id),
        ).fetchone()
        return row["id"] if row else None

    def _move_player_to_least_full_table(self, player_id, exclude_table_id):
        """Déplace un joueur déjà désigné vers la table active la moins
        remplie (hors `exclude_table_id`, sa table actuelle) qui a encore
        de la place, au premier siège libre — exactement le même choix de
        destination que la boucle de rééquilibrage de rebalance_tables
        (INCHANGÉ, consigne de cette version TEST), mais recalculé à
        l'état courant plutôt que de réutiliser une table de destination
        évaluée au moment de la question, qui a pu changer entre-temps
        (voir resolve_pending_rebalance). Ne fait rien si aucune autre
        table n'a de place libre (ne devrait pas arriver : on vient
        justement d'en détecter une)."""
        counts = []
        for t in self.list_tables():
            if t["id"] == exclude_table_id:
                continue
            occ = self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            if occ < t["max_seats"]:
                counts.append((occ, t))
        if not counts:
            return
        counts.sort(key=lambda x: x[0])
        dest_table = counts[0][1]
        taken = {
            r["seat"] for r in self.conn.execute(
                "SELECT seat FROM players WHERE table_id=? AND status='active'",
                (dest_table["id"],),
            )
        }
        seat = 1
        while seat in taken:
            seat += 1
        self.conn.execute(
            "UPDATE players SET table_id=?, seat=? WHERE id=?",
            (dest_table["id"], seat, player_id),
        )
        self.conn.commit()

    def resolve_pending_rebalance(self, request_id, player_id):
        """Traite une réponse à la question "quel joueur est UTG ?" (voir
        rebalance_tables ci-dessus) — à appeler UNIQUEMENT depuis le
        thread principal Tkinter (voir App._resolve_pending_rebalance
        dans main.py) ; jamais directement depuis le thread du serveur de
        contrôle à distance (remote_control.py ne fait que déposer la
        réponse dans la file d'attente thread-safe existante, voir
        on_rebalance_answer/voice_command_queue).

        `player_id` : identifiant du joueur désigné comme UTG (celui à
        déplacer), ou None ("Continuer sans désigner le joueur"). Chantier
        "sélection directe du joueur UTG" (2026-09-24) : REMPLACE l'ancien
        paramètre `seat` (numéro de siège "grosse blinde", dont on
        déduisait le joueur suivant via _next_active_seat_player,
        supprimée — voir _player_still_at_table) — le téléphone désigne
        désormais directement le joueur, jamais un siège à interpréter.

        RÈGLE DE CONSOMMATION — "première réponse VALIDE traitée gagne" :
        pending_rebalance n'est mis à None (consommant définitivement la
        demande) QUE lorsque cette réponse est jugée VALIDE ET qu'un
        mouvement est réellement décidé. Une réponse INVALIDE (mauvais/
        vieux request_id, player_id vide/invalide/inexistant, joueur qui
        n'est plus actif ou plus à cette table, ou état devenu obsolète)
        NE consomme RIEN : la demande reste ouverte (avec le même
        request_id si elle concerne toujours la même table) pour qu'une
        réponse valide arrivant ensuite — d'un autre appareil ou du même —
        puisse encore déterminer le mouvement. Étapes, dans l'ordre :

        1. request_id ne correspond pas à la demande actuellement en
           attente (déjà traitée par une réponse VALIDE précédente, déjà
           remplacée par une nouvelle demande — voir 3 —, ou inconnue) :
           ignoré, sans aucun effet sur pending_rebalance.
        2. Le besoin de rééquilibrage est recalculé sur l'état COURANT
           (voir _detect_simple_rebalance_need), AVANT même de regarder ce
           que cette réponse précise contient : l'état a pu changer depuis
           que la question a été posée (élimination, cassage de table...).
           Si plus aucun mouvement n'est nécessaire : la demande est
           fermée SANS déplacer personne, quelle qu'ait été la réponse —
           jamais un déplacement "par défaut" alors qu'il n'y a plus rien
           à équilibrer.
        3. Si la table qui doit maintenant donner un joueur diffère de
           celle de la demande d'origine (la situation a basculé sur une
           autre table pendant l'attente) : la demande d'origine n'a plus
           de sens et n'est PAS utilisée pour décider quoi que ce soit —
           elle est remplacée par une nouvelle demande cohérente avec
           l'état courant (nouveau request_id ; l'ancien ne peut plus
           jamais gagner, y compris s'il semblait numériquement valide
           pour l'ancienne table).
        4. Toujours la même table : si un joueur est désigné mais n'est
           plus actif OU n'est plus assis à CETTE table (éliminé, ou
           déplacé ailleurs entre la question et la réponse — voir
           _player_still_at_table), la réponse est INVALIDE — voir règle
           de consommation ci-dessus : la demande reste ouverte (sièges
           réaffichés à jour), rien n'est déplacé, on ne choisit PAS un
           autre joueur à sa place (aucune substitution, jamais).
        5. "Continuer sans désigner le joueur" (player_id=None) est
           toujours traité comme une réponse VALIDE (choix explicite et
           délibéré), dès lors que l'étape 2 confirme qu'un mouvement
           reste nécessaire : utilise alors l'ancien mécanisme HISTORIQUE
           exact (voir _legacy_pick_mover), y compris sa préférence pour
           un joueur déjà déplacé pendant CE rééquilibrage (before_state
           mémorisé dans la demande au moment de sa création, voir
           rebalance_tables) — jamais une simplification approximative.

        Renvoie la liste des mouvements RÉELLEMENT effectués par cette
        résolution (le mouvement décidé ici, plus tout mouvement
        supplémentaire enchaîné par la suite du rééquilibrage), au même
        format que rebalance_tables() ; les archive dans l'historique
        (onglet Mouvements) si la demande d'origine le demandait (voir
        `record_moves` dans rebalance_tables)."""
        pending = self.pending_rebalance
        if pending is None or pending["request_id"] != request_id:
            # Étape 1 : requête inconnue, déjà traitée par une réponse
            # valide précédente, ou remplacée par une nouvelle demande
            # (étape 3) — ignorée SANS AUCUN EFFET.
            return []

        # Étape 2 : revalide D'ABORD le besoin sur l'état courant, avant de
        # regarder le contenu de cette réponse.
        need = self._detect_simple_rebalance_need()
        if need is None:
            self.pending_rebalance = None
            return []
        source_table, occupied_seats = need

        if source_table["id"] != pending["table_id"]:
            # Étape 3 : la situation a changé de table entre-temps. On ne
            # consomme PAS la réponse reçue (elle ne concerne plus la
            # bonne table) — on la remplace par une demande cohérente,
            # avec un NOUVEL identifiant.
            self.pending_rebalance = {
                "request_id": uuid.uuid4().hex,
                "table_id": source_table["id"],
                "table_name": source_table["name"],
                "seats": occupied_seats,
                "record_moves": pending["record_moves"],
                "before_state": {
                    p["id"]: (p["table_id"], p["seat"])
                    for p in self.list_players(status="active")
                },
                "created_at": time.time(),
            }
            return []

        # Toujours la même table : détermine le joueur à déplacer.
        if player_id is not None:
            mover_id = self._player_still_at_table(source_table["id"], player_id)
            if mover_id is None:
                # Étape 4 : réponse INVALIDE — ne consomme PAS la demande,
                # qui reste ouverte (même request_id) pour une réponse
                # valide ultérieure. Sièges réaffichés à jour uniquement —
                # AUCUNE substitution par un autre joueur.
                self.pending_rebalance["seats"] = occupied_seats
                return []
        else:
            # Étape 5 : "Continuer sans désigner le joueur" — toujours
            # valide ici (un mouvement est bien nécessaire, voir étape 2).
            mover_id = self._legacy_pick_mover(source_table["id"], pending.get("before_state", {}))

        # Réponse VALIDE : consommée SEULEMENT MAINTENANT (jamais avant ce
        # point) — elle gagne définitivement contre toute réponse
        # ultérieure à cette même demande (son request_id ne correspondra
        # plus à l'étape 1 dès l'instruction suivante).
        self.pending_rebalance = None

        my_move = None
        if mover_id is not None:
            mover_before = self.get_player(mover_id)
            old_table_id, old_seat = mover_before["table_id"], mover_before["seat"]
            self._move_player_to_least_full_table(mover_id, exclude_table_id=source_table["id"])
            mover_after = self.get_player(mover_id)
            if mover_after["table_id"] != old_table_id:
                table_names = {t["id"]: t["name"] for t in self.list_tables(active_only=False)}
                my_move = {
                    "player_name": mover_after["name"],
                    "old_table_name": table_names.get(old_table_id),
                    "old_seat": old_seat,
                    "new_table_name": table_names.get(mover_after["table_id"]),
                    "new_seat": mover_after["seat"],
                    "moved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    # Raison (voir MOVE_REASON_*) : joueur UTG désigné
                    # explicitement par le téléphone -> équilibrage guidé ;
                    # player_id=None ("Continuer sans désigner le joueur")
                    # -> choix explicite et délibéré du responsable,
                    # distingué textuellement dans l'historique de
                    # l'équilibrage automatique de la Phase 1/guidage
                    # désactivé, bien que le même _legacy_pick_mover soit
                    # réutilisé. Clés MOVE_REASON_BB_GUIDED/MOVE_REASON_BB_
                    # SKIPPED volontairement INCHANGÉES (compatibilité des
                    # mouvements déjà enregistrés dans d'anciens fichiers
                    # .tournoi) — seul leur libellé affiché change.
                    "reason": MOVE_REASON_BB_GUIDED if player_id is not None else MOVE_REASON_BB_SKIPPED,
                }

        # Reprend le rééquilibrage sur l'état courant (peut fermer
        # d'autres tables devenues inutiles, ou poser une NOUVELLE
        # question si un écart persiste ailleurs). record_moves=False ici
        # dans tous les cas : le mouvement décidé ci-dessus (my_move) a
        # déjà eu lieu et ne serait de toute façon plus visible dans le
        # diff avant/après de cet appel-là ; c'est ce résolveur-ci qui
        # archive l'ensemble (my_move + further_moves) plus bas, une seule
        # fois, selon le `record_moves` demandé par l'appel d'ORIGINE
        # (celui qui a posé la question).
        further_moves = self.rebalance_tables(record_moves=False)
        moves = ([my_move] if my_move else []) + further_moves

        if moves and pending["record_moves"]:
            self.conn.execute("DELETE FROM seat_moves")
            for move in moves:
                self.conn.execute(
                    "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
                    "new_table_name, new_seat, moved_at, reason) VALUES (?,?,?,?,?,?,?)",
                    (move["player_name"], move["old_table_name"], move["old_seat"],
                     move["new_table_name"], move["new_seat"], move["moved_at"],
                     move.get("reason", MOVE_REASON_AUTO_BALANCE)),
                )
            self.conn.commit()
        return moves

    def get_seat_moves(self, limit=500):
        """Historique des déplacements de joueurs entre tables/sièges (le
        plus récent en premier), pour l'onglet Mouvements."""
        return self.conn.execute(
            "SELECT * FROM seat_moves ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def count_seat_moves(self):
        return self.conn.execute("SELECT COUNT(*) c FROM seat_moves").fetchone()["c"]

    def clear_seat_moves(self):
        """Vide l'historique des déplacements (onglet Mouvements) — utilisé
        par le bouton "Terminé" une fois les joueurs déplacés installés à
        leur nouvelle table."""
        self.conn.execute("DELETE FROM seat_moves")
        self.conn.commit()

    def confirm_seat_move(self, move_id):
        """Confirmation INDIVIDUELLE d'un mouvement (demande du
        2026-09-19, bouton [OK] par ligne sur la page "Mouvements" du
        contrôle à distance) : retire uniquement CETTE ligne, les autres
        mouvements en attente restent inchangés.

        N'applique jamais rien à `players` : `table_id`/`seat` sont déjà,
        depuis le calcul même du rééquilibrage (rebalance_tables/
        resolve_pending_rebalance/undo_last_elimination), la position
        RÉELLE et définitive du joueur — bien avant que cette ligne
        seat_moves n'existe. Confirmer un mouvement ne fait donc que
        retirer son entrée du journal/affichage, exactement comme
        clear_seat_moves() pour le lot entier (voir App._finish_movement_
        alert, jamais modifiée par cette fonction).

        Idempotent : un id déjà confirmé (ligne déjà supprimée) ou
        totalement inconnu ne lève jamais d'exception et n'a aucun
        effet — un double-tap ou un retry réseau côté téléphone reste
        donc toujours sans risque."""
        self.conn.execute("DELETE FROM seat_moves WHERE id=?", (move_id,))
        self.conn.commit()

    # ---------- primes (bounty) ----------
    def get_bounty_events(self, limit=500):
        """Historique des primes gagnées (le plus récent en premier), pour
        l'onglet Primes."""
        return self.conn.execute(
            "SELECT * FROM bounty_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def get_active_bounties(self):
        """Joueurs actifs portant une prime, triés par prime décroissante."""
        return self.conn.execute(
            "SELECT * FROM players WHERE status='active' AND bounty > 0 "
            "ORDER BY bounty DESC"
        ).fetchall()

    def get_bounty_totals(self):
        """Classement des joueurs par cumul de primes empochées (cash),
        toutes primes gagnées, décroissant."""
        return self.conn.execute(
            "SELECT * FROM players WHERE bounty_won > 0 ORDER BY bounty_won DESC"
        ).fetchall()

    def get_presence_bonuses(self):
        """Prime de présence (en points) : chaque joueur du tournoi en
        cours reçoit `attendance_bonus_points` (réglage) pour le simple
        fait d'avoir participé à ce tournoi, 0 si le réglage est nul.
        Renvoie {nom: points}.

        Un forfait (status='withdrawn') est EXCLU depuis la règle métier
        validée le 2026-09-18 : "un joueur inscrit qui n'est jamais venu"
        ne doit pas recevoir de prime de présence — remplace le
        comportement antérieur (forfait compris, voir git history). Un
        joueur 'eliminated' (a réellement joué) reste, lui, pleinement
        éligible, exactement comme 'active' : seul le statut 'withdrawn'
        est exclu ici, jamais 'eliminated'. Get_primes_summary (seul
        appelant) lit ce dict via `.get(name, 0)` : un forfait absent
        d'ici y apparaît donc naturellement avec 0, sans autre
        changement nécessaire là-bas.

        Primes désactivées (`_primes_enabled`, demande du 2026-09-09) :
        aucun calcul, {} inconditionnellement — court-circuit à la
        source, pas un simple masquage à l'affichage."""
        if not self._primes_enabled():
            return {}
        points = self.get_setting_int("attendance_bonus_points", 0)
        return {
            p["name"]: points for p in self.list_players() if p["status"] != "withdrawn"
        }

    def get_assiduity_bonuses(self):
        """Calcule la prime d'assiduité (en points) de chaque joueur du
        tournoi en cours. Contrôlée par deux réglages :
        - `assiduity_bonus_points` : montant en points (0/nul = prime
          désactivée) ;
        - `assiduity_consecutive_days` : nombre de présences consécutives
          requises, ce tournoi inclus (0/nul = prime désactivée aussi). 2
          = présent ce tournoi-ci ET le précédent ; 3 = ce tournoi-ci et
          les 2 précédents ; etc.
        Un joueur est éligible s'il a été RÉELLEMENT présent (statut
        'active' ou 'eliminated', jamais 'withdrawn' — règle métier
        validée le 2026-09-18) dans CE tournoi ET dans TOUS les
        (`assiduity_consecutive_days` - 1) fichiers .tournoi précédents
        immédiats (même dossier, non récursif — recherche de fichiers
        elle-même INCHANGÉE, voir find_previous_tournament_files) —
        s'il manque ne serait-ce qu'un de ces tournois dans son
        historique, s'il y était forfait, s'il est lui-même forfait
        MAINTENANT, ou qu'il n'y a pas encore assez de tournois
        précédents, il n'est pas éligible. Un forfait, dans un tournoi
        antérieur OU dans celui-ci, casse donc la série exactement comme
        une absence pure — ni ne la maintient pour lui-même, ni pour les
        autres joueurs du même tournoi antérieur.

        Utilise _read_present_player_names_from_file (jamais
        read_player_names_from_file, réservée à un usage général sans
        rapport avec les primes — voir sa docstring) pour les fichiers
        précédents : cette fonction dédiée exclut déjà les forfaits à la
        source.

        Renvoie une liste de dicts {name, present_previous, points} triée
        par nom ; liste vide si la prime est désactivée (l'un des deux
        réglages à 0) ou si `_primes_enabled` est faux (interrupteur
        général, demande du 2026-09-09 — court-circuit à la source)."""
        if not self._primes_enabled():
            return []
        points = self.get_setting_int("assiduity_bonus_points", 0)
        consecutive_days = self.get_setting_int("assiduity_consecutive_days", 0)
        if points <= 0 or consecutive_days <= 0:
            return []

        # Joueurs réellement présents dans CE tournoi (jamais un forfait) —
        # seule cette liste peut prétendre au bonus, quelle que soit son
        # historique : un forfait AUJOURD'HUI n'en bénéficie jamais, même
        # s'il remplissait la condition les fois précédentes.
        present_now = {
            p["name"].strip().lower() for p in self.list_players() if p["status"] != "withdrawn"
        }

        needed_previous = consecutive_days - 1
        if needed_previous == 0:
            # 1 seule présence "consécutive" requise : ce tournoi-ci suffit.
            eligible_names = present_now
        else:
            prev_files = find_previous_tournament_files(
                self.path, self.get_tournament_date(), count=needed_previous
            )
            if len(prev_files) < needed_previous:
                eligible_names = set()  # pas encore assez d'historique
            else:
                name_sets = [_read_present_player_names_from_file(fp) for fp in prev_files]
                eligible_names = present_now.intersection(*name_sets)

        result = [
            {
                "name": p["name"],
                "present_previous": p["name"].strip().lower() in eligible_names,
                "points": points if p["name"].strip().lower() in eligible_names else 0,
            }
            for p in self.list_players()
        ]
        result.sort(key=lambda r: r["name"].casefold())
        return result

    def resolve_ranking_formula(self):
        """Résout le réglage EFFECTIF de formule de classement pour CE
        tournoi (voir RANKING_FORMULA_*) — règle de compatibilité avec
        l'ancien réglage ranking_bonus_points, validée le 2026-09-10
        (l'utilisateur a explicitement choisi de PRÉSERVER les résultats
        historiques réels plutôt que d'appliquer littéralement "vide/0 =
        Aucun", qui aurait rétroactivement mis à zéro les points de
        classement de tout ancien tournoi n'ayant jamais touché ce champ) :

        1. `ranking_formula` déjà présent (l'une des 5 valeurs de
           RANKING_FORMULA_LABELS, y compris "none") : utilisé tel quel.
           C'est le cas de tout NOUVEAU tournoi (stampé "none" dès sa
           création, voir App._choose_tournament_file) et de tout
           tournoi déjà explicitement configuré via la nouvelle liste
           déroulante.
        2. `ranking_formula` ABSENT et `ranking_bonus_points` > 0 :
           ancien réglage "valeur fixe" — préservé EXACTEMENT, pour
           toujours (2e élément du tuple renvoyé, non None) : c'était
           déjà le comportement historique de ranking_points() (`if
           flat_value: return flat_value`, prioritaire sur toute
           formule) — jamais recalculé, jamais remplacé silencieusement
           par une formule.
        3. `ranking_formula` ABSENT et `ranking_bonus_points` absent ou
           à 0 : c'est très précisément le comportement historique de
           TOUJOURS (0 est faux en Python — l'ancien `if flat_value:
           ...` ne s'exécutait jamais, la formule 100×√N/P s'appliquait
           déjà) — résolu à "current" (Classique), JAMAIS "none", pour
           ne rien changer rétroactivement.

        Renvoie (formula, legacy_flat_value) : `formula` est toujours
        l'une des 5 valeurs de RANKING_FORMULA_LABELS ; `legacy_flat_value`
        est None sauf dans le cas 2 ci-dessus, où il prime absolument sur
        `formula` (voir get_ranking_bonuses — jamais les deux appliqués
        à la fois)."""
        stored = self.get_setting("ranking_formula")
        if stored in RANKING_FORMULA_LABELS:
            return stored, None
        legacy_flat_value = self.get_setting_int("ranking_bonus_points", 0)
        if legacy_flat_value > 0:
            return RANKING_FORMULA_CURRENT, legacy_flat_value
        return RANKING_FORMULA_CURRENT, None

    def get_ranking_bonuses(self):
        """Calcule la prime de classement (en points) de chaque joueur dont
        le rang est déjà connu : un joueur éliminé (place déjà attribuée),
        ou le vainqueur une fois le tournoi terminé (même convention que le
        rang affiché dans l'onglet Joueurs / les exports). Les joueurs
        encore actifs en cours de tournoi (rang pas encore connu) n'ont pas
        de ligne. Renvoie une liste de dicts {name, place, nombre, valeur,
        montant} (montant = nombre × valeur), triée par rang croissant ;
        liste vide si `_primes_enabled` est faux (interrupteur général,
        demande du 2026-09-09 — court-circuit à la source).

        Utilise resolve_ranking_formula() (voir sa docstring pour la
        règle de compatibilité complète avec l'ancien ranking_bonus_points)
        — SEUL point d'appel de ranking_points_table() dans tout le
        fichier : onglet Primes, exports CSV/XLSX/PDF et synthèse
        multi-tournois en découlent tous automatiquement via
        get_primes_summary()."""
        if not self._primes_enabled():
            return []
        formula, legacy_flat_value = self.resolve_ranking_formula()
        n_players = self.get_stats()["total_players_ever"]
        all_players = self.list_players()
        active = [p for p in all_players if p["status"] == "active"]
        finished = len(active) == 1
        # Barème calculé UNE SEULE FOIS pour tout le tournoi (voir
        # ranking_points_table), jamais un appel par joueur : nécessaire
        # pour "tournois_cpc" (plus grands restes, exige la répartition
        # complète de tous les rangs) et neutre en résultat pour les
        # autres formules (simple wrapper, voir sa docstring). Inutile
        # si legacy_flat_value prime (ancien réglage "valeur fixe") :
        # on évite alors totalement ce calcul.
        points_table = None if legacy_flat_value is not None else ranking_points_table(n_players, formula)

        result = []
        for p in all_players:
            place = None
            if p["status"] == "eliminated":
                place = p["place"]
            elif p["status"] == "active" and finished:
                place = 1
            if place is None:
                continue
            # legacy_flat_value (ancien réglage "valeur fixe") prime
            # absolument sur la formule si présent — voir
            # resolve_ranking_formula, cas 2.
            if legacy_flat_value is not None:
                valeur = legacy_flat_value
            elif 0 < place <= len(points_table):
                valeur = points_table[place - 1]
            else:
                valeur = 0
            result.append({
                "name": p["name"], "place": place,
                "nombre": 1, "valeur": valeur, "montant": valeur,
            })
        result.sort(key=lambda r: r["place"])
        return result

    def get_bounty_bonuses(self):
        """Calcule la prime de bounty (en points) de chaque joueur du
        tournoi en cours. Nombre = nombre de joueurs qu'il a éliminés
        (players.kills, incrémenté sur toute élimination avec éliminateur
        désigné) — JAMAIS incrémenté par la clôture de la bounty finale du
        vainqueur (voir _close_out_winner_bounty), dans les deux modes.

        Mode CLASSIQUE (pko_mode=0, inchangé) :
          Valeur = réglage manuel `bounty_amount` s'il est non nul, sinon
          10×√N points par bounty (N = nombre total de joueurs du
          tournoi) ; Montant = Nombre × Valeur.

        Mode PKO (demande du 2026-09-08) : Valeur × Nombre ne représente
          plus les vrais gains (une bounty grandit/se transmet en chaîne,
          voir eliminate_player) — on utilise donc directement les vrais
          gains PKO définitivement acquis :
          Montant = bounty_won réel du joueur (y compris sa propre bounty
          finale s'il est le vainqueur — voir _pko_effective_bounty_won,
          qui couvre aussi les tournois déjà terminés avant ce correctif) ;
          Valeur = Montant ÷ Nombre, arrondi (moyenne par bounty gagnée),
          0 si Nombre = 0 (aucune division par zéro).

        Renvoie une liste de dicts {name, nombre, valeur, montant} pour
        tous les joueurs, triée par montant décroissant. Utilisé par
        get_primes_summary (dont le TOTAL, dans les deux modes) et son
        export dédié — jamais de double comptage : le calcul classique et
        le calcul PKO sont mutuellement exclusifs, jamais additionnés.

        Primes désactivées (`_primes_enabled`, demande du 2026-09-09) :
        liste vide inconditionnellement — court-circuit à la source (de
        toute façon `kills`/`bounty_won` ne sont jamais alimentés dans ce
        cas, voir eliminate_player, mais on ne dépend pas de cela ici)."""
        if not self._primes_enabled():
            return []
        pko_mode = self.get_setting_int("pko_mode", 0) == 1
        if not pko_mode:
            flat_value = self.get_setting_int("bounty_amount", 0)
            n_players = self.get_stats()["total_players_ever"]
            valeur = bounty_unit_value(n_players, flat_value)
            result = [
                {
                    "name": p["name"], "nombre": p["kills"],
                    "valeur": valeur, "montant": p["kills"] * valeur,
                }
                for p in self.list_players()
            ]
        else:
            result = []
            for p in self.list_players():
                nombre = p["kills"]
                montant = self._pko_effective_bounty_won(p)
                valeur = round(montant / nombre) if nombre else 0
                result.append({
                    "name": p["name"], "nombre": nombre,
                    "valeur": valeur, "montant": montant,
                })
        result.sort(key=lambda r: (-r["montant"], r["name"].casefold()))
        return result

    def primes_columns(self):
        """PRIMES_COLUMNS pour CE tournoi précis : l'en-tête de la colonne
        'bo_valeur' s'adapte au mode (demande du 2026-09-08) — "Val
        Bounty" en classique (valeur fixe par bounty), "Moy Bounty" en PKO
        (Mon Bounty ÷ Nb Bounty, arrondi — voir get_bounty_bonuses).
        Source UNIQUE de cette adaptation : utilisée à la fois par
        l'onglet Primes (main.py) et les exports dédiés
        (export_primes_csv/xlsx/pdf) pour qu'ils ne divergent jamais."""
        pko_mode = self.get_setting_int("pko_mode", 0) == 1
        if not pko_mode:
            return PRIMES_COLUMNS
        return [
            (key, "Moy Bounty", fn) if key == "bo_valeur" else (key, header, fn)
            for key, header, fn in PRIMES_COLUMNS
        ]

    def get_primes_summary(self, sort_column=None, ascending=True):
        """Construit, pour chaque joueur du tournoi en cours, la ligne
        récapitulative des primes en points affichée dans l'onglet Primes
        (présence, assiduité, rang, classement, bounty nombre/valeur/
        montant, TOTAL). Utilisé aussi pour son export dédié.

        `sort_column` : 'rang', 'bo_nombre' ou 'total' (autre valeur ou
        None -> tri par défaut, TOTAL décroissant). Les valeurs manquantes
        (ex : rang d'un joueur encore actif) sont toujours reléguées en
        fin de liste, quel que soit le sens du tri.

        Primes désactivées (`_primes_enabled`, demande du 2026-09-09) :
        liste vide inconditionnellement — l'onglet Primes reste
        simplement vide, aucun message de remplacement (les quatre
        get_*_bonuses renvoient déjà [] / {} chacun de leur côté, mais on
        court-circuite aussi ici pour ne dépendre d'aucun détail interne
        de ces sous-fonctions)."""
        if not self._primes_enabled():
            return []
        presence_by_name = self.get_presence_bonuses()
        assiduity_by_name = {r["name"]: r for r in self.get_assiduity_bonuses()}
        ranking_by_name = {r["name"]: r for r in self.get_ranking_bonuses()}
        bounty_by_name = {r["name"]: r for r in self.get_bounty_bonuses()}

        rows = []
        for p in self.list_players():
            name = p["name"]
            presence = presence_by_name.get(name, 0)
            assiduite = assiduity_by_name.get(name, {}).get("points", 0)
            rk = ranking_by_name.get(name)
            rang, cl_montant = (rk["place"], rk["montant"]) if rk else (None, 0)
            bt = bounty_by_name.get(name, {"nombre": 0, "valeur": 0, "montant": 0})
            total = presence + assiduite + cl_montant + bt["montant"]
            rows.append({
                "name": name, "presence": presence, "assiduite": assiduite,
                "rang": rang, "cl_montant": cl_montant,
                "bo_nombre": bt["nombre"], "bo_valeur": bt["valeur"], "bo_montant": bt["montant"],
                "total": total,
            })

        if sort_column in ("rang", "bo_nombre", "total"):
            def sort_key(r):
                v = r[sort_column]
                if v is None:
                    return (1, 0, r["name"].casefold())
                return (0, v if ascending else -v, r["name"].casefold())
            rows.sort(key=sort_key)
        else:
            rows.sort(key=lambda r: (-r["total"], r["name"].casefold()))
        return rows

    def export_primes_csv(self, path, columns=None, sort_column=None, ascending=True):
        """Exporte le tableau de l'onglet Primes tel qu'affiché, en CSV.
        `columns` : sous-ensemble de clés de PRIMES_COLUMNS (None =
        toutes). `sort_column`/`ascending` : voir get_primes_summary."""
        import csv

        cols = _selected_period_columns(self.primes_columns(), columns)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in self.get_primes_summary(sort_column=sort_column, ascending=ascending):
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_primes_xlsx(self, path, columns=None, sort_column=None, ascending=True, title=None):
        """Exporte le tableau de l'onglet Primes au format Excel (.xlsx).
        `columns`, `sort_column`, `ascending` : voir export_primes_csv.
        `title` : remplace le titre par défaut (nom du tournoi) si fourni.
        Nécessite 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(self.primes_columns(), columns)
        rows = self.get_primes_summary(sort_column=sort_column, ascending=ascending)

        wb = Workbook()
        ws = wb.active
        ws.title = "Primes"

        name = title or self.get_setting("tournament_name", "Tournoi")
        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append(["Primes en points : présence, assiduité, classement, bounty"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"name": 22, "presence": 12, "assiduite": 12, "rang": 10,
                  "cl_montant": 14, "bo_nombre": 12, "bo_valeur": 12,
                  "bo_montant": 12, "total": 12}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_movement_slips_pdf(self, path, n_slips=6):
        """Génère une page de coupons VIERGES (rien de préempli, pas même
        le nom du tournoi) à découper, un par joueur concerné par un
        changement de table — bouton "Imprimer" de l'onglet Mouvements.
        Le responsable écrit chaque coupon à la main au moment du
        mouvement réel (nom du tournoi, nom du joueur, ancienne table/
        siège, nouvelle table/siège) et le remet directement au joueur :
        plus rapide et plus discret que d'annoncer les mouvements à voix
        haute. `n_slips` : nombre de coupons identiques sur la page (une
        seule impression sert pour tout un mouvement de plusieurs
        joueurs à la fois). Nécessite 'fpdf2'."""
        from fpdf import FPDF

        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=False)
        pdf.add_page()

        margin = 12
        usable_width = pdf.w - 2 * margin
        slip_height = (pdf.h - 2 * margin) / n_slips
        line_h = (slip_height - 8) / 4
        half_width = (usable_width - 6) / 2

        pdf.set_font("Helvetica", "", 11)
        for i in range(n_slips):
            top = margin + i * slip_height
            if i > 0:
                pdf.dashed_line(margin, top, pdf.w - margin, top, dash_length=2, space_length=1.5)
            y = top + 5

            def field(label, x, y, w):
                pdf.set_xy(x, y)
                pdf.cell(w, line_h, _pdf_text(label), border="B")

            field("Nom du tournoi : ", margin, y, usable_width)
            field("Nom du joueur : ", margin, y + line_h, usable_width)
            field("Ancienne table : ", margin, y + 2 * line_h, half_width)
            field("Ancien siège : ", margin + half_width + 6, y + 2 * line_h, half_width)
            field("Nouvelle table : ", margin, y + 3 * line_h, half_width)
            field("Nouveau siège : ", margin + half_width + 6, y + 3 * line_h, half_width)

        pdf.output(path)
        return path

    def export_movement_slips_filled_pdf(self, path, n_per_page=6):
        """Comme export_movement_slips_pdf, mais un coupon par mouvement
        RÉELLEMENT en attente (voir get_seat_moves, même liste que le
        tableau de l'onglet Mouvements), déjà rempli avec les vraies
        valeurs — rien à écrire à la main, juste à découper et remettre.
        Pratique quand beaucoup de joueurs sont concernés à la fois (voir
        bouton "Imprimer" de l'onglet Mouvements, à distinguer du bouton
        "Imprimer Vierge" qui imprime des coupons vides). Une page par
        tranche de `n_per_page` mouvements. Lève ValueError si aucun
        mouvement n'est en attente. Nécessite 'fpdf2'."""
        moves = self.get_seat_moves()
        if not moves:
            raise ValueError("Aucun mouvement en attente à imprimer.")
        name = self.get_setting("tournament_name", "Tournoi")

        from fpdf import FPDF

        pdf = FPDF(orientation="P", unit="mm", format="A4")
        pdf.set_auto_page_break(auto=False)

        margin = 12
        usable_width = pdf.w - 2 * margin
        slip_height = (pdf.h - 2 * margin) / n_per_page
        line_h = (slip_height - 8) / 4
        half_width = (usable_width - 6) / 2

        def field(label, value, x, y, w):
            pdf.set_xy(x, y)
            pdf.cell(w, line_h, _pdf_text(f"{label}{value}"), border="B")

        for i, m in enumerate(moves):
            pos = i % n_per_page
            if pos == 0:
                pdf.add_page()
                pdf.set_font("Helvetica", "", 11)
            top = margin + pos * slip_height
            if pos > 0:
                pdf.dashed_line(margin, top, pdf.w - margin, top, dash_length=2, space_length=1.5)
            y = top + 5
            field("Nom du tournoi : ", name, margin, y, usable_width)
            field("Nom du joueur : ", m["player_name"], margin, y + line_h, usable_width)
            field("Ancienne table : ", m["old_table_name"] or "-", margin, y + 2 * line_h, half_width)
            field("Ancien siège : ", m["old_seat"] or "-", margin + half_width + 6, y + 2 * line_h, half_width)
            field("Nouvelle table : ", m["new_table_name"] or "-", margin, y + 3 * line_h, half_width)
            field("Nouveau siège : ", m["new_seat"] or "-", margin + half_width + 6, y + 3 * line_h, half_width)

        pdf.output(path)
        return path

    def export_primes_pdf(self, path, columns=None, sort_column=None, ascending=True, title=None):
        """Exporte le tableau de l'onglet Primes en PDF. `columns`,
        `sort_column`, `ascending` : voir export_primes_csv. `title` : voir
        export_primes_xlsx. Nécessite 'fpdf2'."""
        cols = _selected_period_columns(self.primes_columns(), columns)
        rows = self.get_primes_summary(sort_column=sort_column, ascending=ascending)
        name = title or self.get_setting("tournament_name", "Tournoi")
        return _write_pdf_table(
            path, name,
            ["Primes en points : présence, assiduité, classement, bounty"],
            [h for _, h, _ in cols],
            [[fn(r) for _, _, fn in cols] for r in rows],
        )

    def export_settings_pdf(self, path, club_name=None):
        """Exporte en PDF tous les réglages actuels de ce tournoi (onglet
        Paramètres, voir SETTINGS_PRINT_FIELDS), sous forme d'un tableau
        Réglage/Valeur — pour en garder une trace papier ou la partager.
        Reflète les valeurs déjà enregistrées dans ce fichier .tournoi
        (l'appelant, App._print_settings_pdf, enregistre d'abord le
        formulaire pour être sûr qu'elles soient à jour). Booléens
        (pko_mode) affichés "Oui"/"Non" plutôt que "1"/"0". `club_name` :
        à passer explicitement par l'appelant (ex : export_prefs.
        load_value("club_name", "")) — ce réglage, commun à tous les
        tournois/Sit & Go, n'est justement JAMAIS écrit dans ce fichier
        .tournoi (voir App._collect_and_save_all_settings), donc
        self.get_setting("club_name", ...) renverrait toujours vide ici.
        Nécessite 'fpdf2'."""
        name = self.get_setting("tournament_name", "Tournoi")
        rows = []
        for key, label in SETTINGS_PRINT_FIELDS:
            if key == "club_name":
                value = club_name or ""
            elif key == "ranking_formula":
                # Valeur RÉSOLUE (voir resolve_ranking_formula), pas la
                # ligne brute "settings" : un ancien fichier n'a souvent
                # aucune ligne "ranking_formula" du tout, alors qu'un
                # système de points s'applique bel et bien (compatibilité
                # avec ranking_bonus_points, voir sa docstring) — un
                # print qui afficherait "vide" ici serait trompeur.
                formula, legacy_flat_value = self.resolve_ranking_formula()
                if legacy_flat_value is not None:
                    value = f"Valeur fixe historique ({legacy_flat_value} points)"
                else:
                    value = RANKING_FORMULA_LABELS.get(formula, formula)
            else:
                value = self.get_setting(key, "")
                if key == "pko_mode":
                    value = "Oui" if value in ("1", 1, True) else "Non"
            rows.append((label, value))
        return _write_pdf_table(
            path, f"Paramètres — {name}",
            [f"Imprimé le {time.strftime('%d/%m/%Y %H:%M')}"],
            ["Réglage", "Valeur"],
            rows,
        )

    def export_bounty_history_csv(self, path, columns=None):
        """Exporte l'historique du bounty progressif (mécanisme PKO
        interne, 2e tableau de l'onglet Primes), en CSV. `columns` :
        sous-ensemble de clés de BOUNTY_HISTORY_COLUMNS (None = toutes)."""
        import csv

        cols = _selected_period_columns(BOUNTY_HISTORY_COLUMNS, columns)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in self.get_bounty_events(limit=10000):
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_bounty_history_xlsx(self, path, columns=None, title=None):
        """Exporte l'historique du bounty progressif au format Excel
        (.xlsx). `columns` : voir export_bounty_history_csv. `title` :
        remplace le titre par défaut (nom du tournoi) si fourni. Nécessite
        'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(BOUNTY_HISTORY_COLUMNS, columns)
        rows = self.get_bounty_events(limit=10000)

        wb = Workbook()
        ws = wb.active
        ws.title = "Historique bounty"

        name = title or self.get_setting("tournament_name", "Tournoi")
        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append(["Historique du bounty progressif (mécanisme PKO interne)"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"time": 18, "eliminated": 20, "eliminator": 20, "amount": 14, "grow": 16}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_bounty_history_pdf(self, path, columns=None, title=None):
        """Exporte l'historique du bounty progressif en PDF. `columns` :
        voir export_bounty_history_csv. `title` : voir
        export_bounty_history_xlsx. Nécessite 'fpdf2'."""
        cols = _selected_period_columns(BOUNTY_HISTORY_COLUMNS, columns)
        rows = self.get_bounty_events(limit=10000)
        name = title or self.get_setting("tournament_name", "Tournoi")
        return _write_pdf_table(
            path, name,
            ["Historique du bounty progressif (mécanisme PKO interne)"],
            [h for _, h, _ in cols],
            [[fn(r) for _, _, fn in cols] for r in rows],
        )

    # ---------- blind structure ----------
    def get_blind_structure(self):
        return self.conn.execute(
            "SELECT * FROM blind_levels ORDER BY level_order"
        ).fetchall()

    def set_blind_structure(self, levels):
        """levels: liste de dicts {small_blind, big_blind, ante, duration_minutes,
        is_break, break_label}"""
        self.conn.execute("DELETE FROM blind_levels")
        for i, lvl in enumerate(levels, start=1):
            self.conn.execute(
                "INSERT INTO blind_levels(level_order, small_blind, big_blind, "
                "ante, duration_minutes, is_break, break_label) VALUES (?,?,?,?,?,?,?)",
                (
                    i,
                    lvl.get("small_blind", 0),
                    lvl.get("big_blind", 0),
                    lvl.get("ante", 0),
                    lvl.get("duration_minutes", 15),
                    1 if lvl.get("is_break") else 0,
                    lvl.get("break_label", "Pause"),
                ),
            )
        self.conn.commit()

    def get_current_level(self):
        order = self.get_setting_int("current_level_order", 1)
        row = self.conn.execute(
            "SELECT * FROM blind_levels WHERE level_order=?", (order,)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM blind_levels ORDER BY level_order LIMIT 1"
            ).fetchone()
        return row

    def get_next_level(self):
        order = self.get_setting_int("current_level_order", 1)
        return self.conn.execute(
            "SELECT * FROM blind_levels WHERE level_order=?", (order + 1,)
        ).fetchone()

    def get_round_number(self, level_order):
        """Numéro de round au sens de l'onglet Blindes pour la ligne
        level_order donnée (une pause n'y compte pas comme un round à
        part entière, contrairement à level_order qui numérote toutes
        les lignes de la structure, pauses comprises — voir "Niveau" au
        Chronomètre/écran projecteur, qui utilisait jusqu'ici directement
        level_order et pouvait donc afficher un numéro différent de la
        colonne "Round" de l'onglet Blindes dès qu'une pause avait eu
        lieu). Renvoie None si level_order est vide/invalide."""
        if not level_order:
            return None
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM blind_levels WHERE is_break=0 AND level_order<=?",
            (level_order,),
        ).fetchone()
        return row["c"] or None

    def get_current_round_number(self):
        """Numéro de round (voir get_round_number) du niveau actuellement
        en cours — utilisé pour horodater les éliminations (colonne
        "Round" de l'onglet Joueurs)."""
        return self.get_round_number(self.get_setting_int("current_level_order", 0))

    # ---------- payout structure ----------
    def get_payout_structure(self):
        return self.conn.execute(
            "SELECT * FROM payout_structure ORDER BY place"
        ).fetchall()

    def set_payout_structure(self, place_to_pct):
        self.conn.execute("DELETE FROM payout_structure")
        for place, pct in place_to_pct.items():
            self.conn.execute(
                "INSERT INTO payout_structure(place, percentage) VALUES (?, ?)",
                (place, pct),
            )
        self.conn.commit()

    # ---------- stats / prize pool ----------
    def get_stats(self):
        all_players = self.list_players()
        active = [p for p in all_players if p["status"] == "active"]
        entries = sum(p["buyin_count"] for p in all_players)
        rebuys = sum(p["rebuy_count"] for p in all_players)
        addons = sum(p["addon_count"] for p in all_players)
        buyin_amount = self.get_setting_float("buyin_amount", 0)
        rebuy_amount = self.get_setting_float("rebuy_amount", 0)
        addon_amount = self.get_setting_float("addon_amount", 0)
        rake_pct = self.get_setting_float("rake_percent", 0)
        gross = entries * buyin_amount + rebuys * rebuy_amount + addons * addon_amount
        prize_pool = gross * (1 - rake_pct / 100.0)
        # Tapis moyen = total des chips en jeu / joueurs encore actifs. Le
        # total doit porter sur les actifs ET les éliminés (mais pas les
        # forfaits — voir plus bas) : les chips d'un joueur éliminé ne
        # disparaissent pas de la table, elles passent dans le tapis de
        # celui qui l'a éliminé — mais ce transfert n'est pas forcément
        # ressaisi manuellement (onglet Joueurs > Modifier les chips)
        # pour chaque main. Se limiter aux actifs ferait baisser le total
        # à chaque élimination et donnerait un tapis moyen sous-évalué.
        # Les forfaits (status='withdrawn') sont exclus : leurs chips ne
        # sont jamais entrées en jeu / en sont sorties avec eux, elles ne
        # doivent pas gonfler le total.
        total_chips = sum(p["chips"] for p in all_players if p["status"] != "withdrawn")
        avg_stack = total_chips / len(active) if active else 0

        # Durée du tournoi (temps réel écoulé depuis le tout premier
        # "Démarrer" — voir App._clock_resume) : continue de courir tant
        # que la partie n'est pas terminée (y compris pendant les pauses
        # du chrono de niveau, qui n'arrêtent pas le temps réel), puis se
        # fige à l'heure de fin dès qu'il ne reste plus qu'1 joueur actif
        # (voir eliminate_player) plutôt que de continuer à défiler alors
        # que tout le monde est déjà parti.
        start_epoch = self.get_setting_int("tournament_start_epoch", 0)
        if start_epoch == 0 and self.get_setting_int("clock_started", 0) == 1:
            # Tournoi déjà en cours avant l'ajout de ce réglage (le chrono
            # avait déjà été démarré) : l'heure du tout premier "Démarrer"
            # n'a jamais été enregistrée, donc "Durée" resterait bloquée à
            # 00:00:00 pour toujours sans ce rattrapage ponctuel. On
            # l'approxime une bonne fois pour toutes avec l'heure de la
            # première élimination déjà enregistrée si elle existe
            # (meilleure estimation disponible), sinon avec l'heure
            # actuelle — puis on la fige en réglage pour ne plus jamais y
            # revenir (sans quoi la durée repartirait de zéro à chaque
            # rafraîchissement).
            earliest = None
            for p in all_players:
                if p["elim_time"]:
                    try:
                        t = time.mktime(time.strptime(p["elim_time"], "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        continue
                    if earliest is None or t < earliest:
                        earliest = t
            start_epoch = int(earliest) if earliest else int(time.time())
            self.set_setting("tournament_start_epoch", start_epoch)
        end_epoch = self.get_setting_int("tournament_end_epoch", 0)
        if start_epoch:
            duration_seconds = max(0, (end_epoch or int(time.time())) - start_epoch)
        else:
            duration_seconds = 0

        return {
            "total_players_ever": len(all_players),
            "active_count": len(active),
            "entries": entries,
            "rebuys": rebuys,
            "addons": addons,
            "gross": gross,
            "prize_pool": prize_pool,
            "total_chips": total_chips,
            "avg_stack": avg_stack,
            "duration_seconds": duration_seconds,
            "tournament_finished": end_epoch != 0,
        }

    def get_live_status(self):
        """Résumé de l'état courant du tournoi, pour l'affichage dans le
        Lobby SNG (liste de plusieurs tournois à la fois) : nom, date,
        joueurs actifs/total, niveau de blindes courant (ou pause), temps
        restant dans ce niveau, chrono démarré/en pause, tournoi terminé.
        Ne modifie rien (ne fait pas avancer automatiquement de niveau,
        contrairement à l'onglet Chronomètre — une simple consultation ne
        doit pas altérer le déroulé du tournoi)."""
        stats = self.get_stats()
        level = self.get_current_level()
        clock_started = self.get_setting_int("clock_started", 0) == 1
        is_paused = self.get_setting_int("is_paused", 1) == 1
        remaining_seconds = None
        if level is not None:
            duration = level["duration_minutes"] * 60
            if not clock_started:
                elapsed = 0
            elif is_paused:
                elapsed = self.get_setting_int("paused_accum_seconds", 0)
            else:
                start = self.get_setting_int("level_start_epoch", int(time.time()))
                elapsed = int(time.time()) - start
            remaining_seconds = max(0, duration - elapsed)
        finished = stats["active_count"] <= 1 and stats["total_players_ever"] > 1
        return {
            "name": self.get_setting("tournament_name", "Tournoi"),
            "date": self.get_tournament_date(),
            "active_count": stats["active_count"],
            "total_players_ever": stats["total_players_ever"],
            "level": level,
            "remaining_seconds": remaining_seconds,
            "clock_started": clock_started,
            "is_paused": is_paused,
            "finished": finished,
        }

    def get_payouts_amounts(self):
        stats = self.get_stats()
        pool = stats["prize_pool"]
        result = []
        for row in self.get_payout_structure():
            result.append(
                {
                    "place": row["place"],
                    "percentage": row["percentage"],
                    "amount": pool * row["percentage"] / 100.0,
                }
            )
        return result

    def _results_rows(self):
        """Construit les lignes de résultats (rang, nom, statut, gain,
        buy-ins, rebuys, add-ons, prime gagnée) sous forme de dicts, triées
        meilleur rang en premier. Utilisé par les exports CSV et XLSX.

        Le rang suit exactement la même convention que l'onglet Joueurs :
        1 pour le vainqueur (seul joueur encore actif, tournoi terminé),
        None (affiché "-") pour les autres joueurs encore actifs tant que
        le tournoi est en cours, et le rang habituel pour les éliminés."""
        payouts_by_place = {p["place"]: p["amount"] for p in self.get_payouts_amounts()}
        eliminated = [p for p in self.list_players() if p["status"] == "eliminated"]
        eliminated.sort(key=lambda p: p["place"])
        withdrawn = [p for p in self.list_players() if p["status"] == "withdrawn"]
        active = [p for p in self.list_players() if p["status"] == "active"]
        finished = len(active) == 1

        rows = []
        for p in active:
            rows.append({
                "rang": 1 if finished else None,
                "name": p["name"], "status": "En cours" if not finished else "Terminé",
                "gain": payouts_by_place.get(1) if finished else None,
                "buyin": p["buyin_count"], "rebuy": p["rebuy_count"], "addon": p["addon_count"],
                # _pko_effective_bounty_won (pas p["bounty_won"] brut) :
                # inclut la bounty finale du vainqueur même sur un tournoi
                # déjà terminé avant le correctif du 2026-09-08 (filet de
                # sécurité en lecture seule, voir sa docstring).
                "bounty_won": self._pko_effective_bounty_won(p),
            })
        for p in eliminated:
            rows.append({
                "rang": p["place"],
                "name": p["name"], "status": "Éliminé",
                "gain": payouts_by_place.get(p["place"]),
                "buyin": p["buyin_count"], "rebuy": p["rebuy_count"], "addon": p["addon_count"],
                "bounty_won": p["bounty_won"],
            })
        for p in withdrawn:
            rows.append({
                "rang": None,
                "name": p["name"], "status": "Forfait", "gain": None,
                "buyin": p["buyin_count"], "rebuy": p["rebuy_count"], "addon": p["addon_count"],
                "bounty_won": p["bounty_won"],
            })
        return rows

    def players_rows(self, sort_column=None, ascending=True):
        """Construit les lignes du tableau de l'onglet Joueurs (nom, table,
        siège, chips, achats, prime en jeu, statut, rang), avec le même
        calcul de rang que cet onglet. Utilisé pour son export dédié.

        `sort_column`/`ascending` reproduisent exactement le tri appliqué
        dans l'onglet (voir App._sort_players_by côté interface) : sans
        eux, l'ordre d'affichage à l'écran (par ex. trié par Rang) et
        l'ordre du fichier exporté pouvaient diverger."""
        status_labels = {"active": "Actif", "withdrawn": "Forfait", "eliminated": "Éliminé"}
        tables = {t["id"]: t["name"] for t in self.list_tables(active_only=False)}
        players = [dict(p) for p in self.list_players()]
        n_active = sum(1 for p in players if p["status"] == "active")

        rows = []
        for p in players:
            if p["status"] == "active":
                rang = 1 if n_active == 1 else None
            elif p["status"] == "eliminated":
                rang = p["place"]
            else:
                rang = None
            rows.append({
                "name": p["name"],
                "club": p["club"] or "",
                "table": tables.get(p["table_id"], "-") if p["table_id"] else "-",
                "seat": p["seat"],
                "chips": p["chips"],
                "buyin": p["buyin_count"],
                "rebuy": p["rebuy_count"],
                "addon": p["addon_count"],
                "bounty": p["bounty"],
                "status": status_labels.get(p["status"], p["status"]),
                "rang": rang,
                "elim_time": p["elim_time"] or "",
                "elim_round": p["elim_round"],
                "eliminated_by": p["eliminated_by_name"] or "",
            })

        if sort_column == "name":
            rows.sort(key=lambda r: r["name"].lower())
        elif sort_column == "status":
            rows.sort(key=lambda r: r["status"].lower())
        elif sort_column == "table":
            rows.sort(key=lambda r: ((r["table"] or "").lower(), r["seat"] or 0))
        elif sort_column == "rang":
            rows.sort(key=lambda r: r["rang"] or 1)
        elif sort_column == "elim_time":
            rows.sort(key=lambda r: r["elim_time"] or "")
        elif sort_column == "eliminated_by":
            rows.sort(key=lambda r: r["eliminated_by"].lower())
        if sort_column and not ascending:
            rows.reverse()
        return rows

    def _bounty_in_use(self):
        if self.get_setting_int("bounty_amount", 0) > 0:
            return True
        return self.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE bounty_won > 0"
        ).fetchone()["c"] > 0

    def export_results_csv(self, path, columns=None):
        """Exporte le classement final (éliminés triés par rang, puis
        joueurs encore actifs) avec le gain correspondant, en CSV.
        `columns` : sous-ensemble de clés de RESULT_COLUMNS à inclure
        (None = toutes celles pertinentes, primes comprises seulement si
        le bounty est utilisé)."""
        import csv

        cols = _selected_period_columns(RESULT_COLUMNS, columns)
        if columns is None and not self._bounty_in_use():
            cols = [c for c in cols if c[0] != "bounty_won"]
        rows = self._results_rows()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in rows:
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_results_xlsx(self, path, columns=None):
        """Exporte le classement final au format Excel (.xlsx), avec
        mise en forme (en-têtes en gras, colonnes ajustées, ligne de
        synthèse du tournoi). `columns` : voir export_results_csv.
        Nécessite le paquet 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(RESULT_COLUMNS, columns)
        if columns is None and not self._bounty_in_use():
            cols = [c for c in cols if c[0] != "bounty_won"]
        rows = self._results_rows()

        wb = Workbook()
        ws = wb.active
        ws.title = "Résultats"

        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"rang": 10, "name": 26, "status": 12, "gain": 14,
                  "buyin": 10, "rebuy": 10, "addon": 10, "bounty_won": 16}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_results_pdf(self, path, columns=None):
        """Exporte le classement final en PDF. `columns` : voir
        export_results_csv. Nécessite le paquet 'fpdf2'."""
        cols = _selected_period_columns(RESULT_COLUMNS, columns)
        if columns is None and not self._bounty_in_use():
            cols = [c for c in cols if c[0] != "bounty_won"]
        rows = self._results_rows()
        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")
        return _write_pdf_table(
            path, name,
            [f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} EUR"],
            [h for _, h, _ in cols],
            [[fn(r) for _, _, fn in cols] for r in rows],
        )

    def export_payouts_csv(self, path, columns=None):
        """Exporte la grille de gains telle qu'affichée dans l'onglet
        Gains (place, pourcentage, montant — sans nom de joueur), en CSV.
        `columns` : sous-ensemble de clés de PAYOUT_COLUMNS (None = toutes)."""
        import csv

        cols = _selected_period_columns(PAYOUT_COLUMNS, columns)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in self.get_payouts_amounts():
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_payouts_xlsx(self, path, columns=None):
        """Exporte la grille de gains au format Excel (.xlsx). `columns` :
        voir export_payouts_csv. Nécessite le paquet 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(PAYOUT_COLUMNS, columns)
        rows = self.get_payouts_amounts()

        wb = Workbook()
        ws = wb.active
        ws.title = "Grille de gains"

        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"place": 10, "percentage": 16, "amount": 14}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_payouts_pdf(self, path, columns=None):
        """Exporte la grille de gains en PDF. `columns` : voir
        export_payouts_csv. Nécessite le paquet 'fpdf2'."""
        cols = _selected_period_columns(PAYOUT_COLUMNS, columns)
        rows = self.get_payouts_amounts()
        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")
        return _write_pdf_table(
            path, name,
            [f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} EUR"],
            [h for _, h, _ in cols],
            [[fn(r) for _, _, fn in cols] for r in rows],
        )

    def export_players_csv(self, path, columns=None, sort_column=None, ascending=True):
        """Exporte le tableau de l'onglet Joueurs tel qu'affiché (nom,
        table, siège, chips, achats, prime en jeu, statut, rang), en CSV.
        `columns` : sous-ensemble de clés de PLAYERS_TAB_COLUMNS (None =
        toutes). `sort_column`/`ascending` : voir players_rows — reprend
        le tri actuellement appliqué dans l'onglet."""
        import csv

        cols = _selected_period_columns(PLAYERS_TAB_COLUMNS, columns)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in self.players_rows(sort_column=sort_column, ascending=ascending):
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_players_xlsx(self, path, columns=None, sort_column=None, ascending=True,
                             title=None, show_prize_pool=True):
        """Exporte le tableau de l'onglet Joueurs au format Excel (.xlsx).
        `columns`, `sort_column`, `ascending` : voir export_players_csv.
        `title` : remplace le titre par défaut (nom du tournoi) si fourni —
        utilisé par exemple par l'export du Classement. `show_prize_pool` :
        si False, n'affiche pas la ligne "Entrées : ... Prize pool : ..."
        (également utilisé par l'export du Classement, où ça n'a pas de
        sens). Nécessite 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(PLAYERS_TAB_COLUMNS, columns)
        rows = self.players_rows(sort_column=sort_column, ascending=ascending)

        wb = Workbook()
        ws = wb.active
        ws.title = "Joueurs"

        stats = self.get_stats()
        name = title or self.get_setting("tournament_name", "Tournoi")

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        if show_prize_pool:
            ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
            ws[f"A{ws.max_row}"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"name": 22, "table": 14, "seat": 10, "chips": 12,
                  "buyin": 10, "rebuy": 10, "addon": 10, "bounty": 12,
                  "status": 12, "rang": 10, "elim_time": 18, "elim_round": 10,
                  "eliminated_by": 18}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_players_pdf(self, path, columns=None, sort_column=None, ascending=True,
                            title=None, show_prize_pool=True):
        """Exporte le tableau de l'onglet Joueurs en PDF. `columns`,
        `sort_column`, `ascending` : voir export_players_csv. `title`,
        `show_prize_pool` : voir export_players_xlsx. Nécessite le paquet
        'fpdf2'."""
        cols = _selected_period_columns(PLAYERS_TAB_COLUMNS, columns)
        rows = self.players_rows(sort_column=sort_column, ascending=ascending)
        stats = self.get_stats()
        name = title or self.get_setting("tournament_name", "Tournoi")
        subtitle_lines = [f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} EUR"] if show_prize_pool else []
        return _write_pdf_table(
            path, name,
            subtitle_lines,
            [h for _, h, _ in cols],
            [[fn(r) for _, _, fn in cols] for r in rows],
        )

    def close(self):
        self.conn.close()


def read_player_names_from_file(path):
    """Lit uniquement les noms des joueurs d'un fichier .tournoi existant,
    triés par ordre alphabétique, sans toucher au fichier ni reprendre
    leurs performances (chips, place, buy-ins...). Utilisé pour reprendre
    la liste des joueurs d'un tournoi précédent dans un nouveau tournoi.
    Ouvre la base en lecture seule (URI mode=ro) pour ne jamais créer ni
    modifier ce fichier, même par erreur. Lève une exception si le
    fichier n'est pas une base de tournoi valide."""
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT name FROM players ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [row[0] for row in rows if row[0] and row[0].strip()]
    finally:
        conn.close()


def _read_present_player_names_from_file(path):
    """Comme read_player_names_from_file ci-dessus, mais EXCLUT les
    forfaits (status='withdrawn') — usage RÉSERVÉ à Database.get_
    assiduity_bonuses (règle métier validée le 2026-09-18 : un forfait
    est un joueur inscrit qui n'est jamais venu, il ne doit ni bénéficier
    d'une série d'assiduité, ni la maintenir pour les autres joueurs
    d'un tournoi antérieur). Ne remplace PAS read_player_names_from_file
    pour son propre usage (reprise de la liste des joueurs d'un tournoi
    précédent dans un nouveau tournoi, voir main.py) — volontairement
    inchangée, fonction séparée pour ne jamais risquer d'effet de bord
    sur cet autre appelant. Renvoie un set de noms en minuscules/sans
    espaces superflus (déjà normalisés pour l'intersection faite par
    get_assiduity_bonuses, qui compare toujours sur cette forme)."""
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT name FROM players WHERE status != 'withdrawn' "
            "ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return {row[0].strip().lower() for row in rows if row[0] and row[0].strip()}
    finally:
        conn.close()


# =====================================================================
# Synthèse multi-tournois par période (parcourt plusieurs fichiers
# .tournoi d'un dossier). Contrairement au reste de ce module, ces
# fonctions ne portent pas sur un seul tournoi mais en agrègent
# plusieurs — elles sont donc au niveau module plutôt que sur la classe
# Database.
# =====================================================================

def find_tournament_files(folder, recursive=True):
    """Liste (triée) des fichiers .tournoi trouvés dans `folder`, et ses
    sous-dossiers si `recursive` est vrai."""
    pattern = os.path.join(folder, "**", "*.tournoi") if recursive else os.path.join(folder, "*.tournoi")
    return sorted(glob.glob(pattern, recursive=recursive))


def find_finished_tournament_files(folder):
    """Fichiers .tournoi terminés (get_live_status()['finished']) trouvés
    directement dans `folder` (non récursif, comme le Lobby SNG) — liste
    de dicts {path, name}, utilisée pour "Archiver les terminés..."."""
    results = []
    for path in find_tournament_files(folder, recursive=False):
        try:
            db = Database(path, read_only=True)
            status = db.get_live_status()
            db.close()
        except Exception:
            continue
        if status["finished"]:
            results.append({"path": path, "name": status["name"]})
    return results


def archive_tournament_files(paths):
    """Déplace chaque fichier .tournoi de `paths` dans un sous-dossier
    "archive" créé (si besoin) dans son propre dossier parent — celui où
    il a été créé, pas un emplacement d'archive centralisé. En cas de nom
    déjà présent dans ce sous-dossier, ajoute un suffixe numérique plutôt
    que d'écraser. Renvoie le nombre de fichiers effectivement déplacés ;
    une erreur sur un fichier (verrouillé, permissions...) n'interrompt
    pas le traitement des autres."""
    moved = 0
    for path in paths:
        folder = os.path.dirname(path)
        archive_dir = os.path.join(folder, "archive")
        try:
            os.makedirs(archive_dir, exist_ok=True)
            base, ext = os.path.splitext(os.path.basename(path))
            dest = os.path.join(archive_dir, base + ext)
            i = 2
            while os.path.exists(dest):
                dest = os.path.join(archive_dir, f"{base}_{i}{ext}")
                i += 1
            shutil.move(path, dest)
            moved += 1
        except OSError:
            continue
    return moved


def _read_tournament_date_ro(path):
    """Comme Database.get_tournament_date, mais en lecture seule (URI
    mode=ro) pour ne jamais créer ni modifier le fichier consulté — même
    précaution que read_player_names_from_file, appliquée ici pour pouvoir
    comparer les dates de plusieurs tournois (dossier entier) sans toucher
    aux fichiers des autres soirées."""
    try:
        uri = f"file:{os.path.abspath(path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return ""
    try:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='tournament_date'"
        ).fetchone()
        if row and row[0]:
            return row[0]
        st = os.stat(path)
        ts = getattr(st, "st_birthtime", None)
        if ts is None:
            ts = st.st_mtime
        return time.strftime("%Y-%m-%d", time.localtime(ts))
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()


def _player_active_in_file(path, name_lower):
    """Vrai si un joueur nommé `name_lower` (déjà en minuscules) est
    actuellement 'active' dans le fichier .tournoi `path`, consulté en
    lecture seule (même précaution que _read_tournament_date_ro)."""
    try:
        uri = f"file:{os.path.abspath(path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return False
    try:
        row = conn.execute(
            "SELECT 1 FROM players WHERE status='active' AND lower(name)=? LIMIT 1",
            (name_lower,),
        ).fetchone()
        return row is not None
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def _active_player_names_lower_in_file(path):
    """Ensemble des noms (en minuscules) actuellement 'active' dans le
    fichier .tournoi `path`, consulté en lecture seule."""
    try:
        uri = f"file:{os.path.abspath(path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return set()
    try:
        rows = conn.execute("SELECT lower(name) FROM players WHERE status='active'").fetchall()
        return {r[0] for r in rows}
    except sqlite3.OperationalError:
        return set()
    finally:
        conn.close()


def find_players_active_elsewhere(folder, names, exclude_path=None, date=None):
    """Parmi `names`, renvoie l'ensemble de ceux actuellement actifs dans
    un fichier .tournoi du dossier `folder` (recherche non récursive, hors
    `exclude_path`) — utilisé pour griser, dans la fenêtre "Joueurs
    participants", les joueurs déjà engagés dans un autre tournoi en cours
    du même dossier (ex : un autre Sit & Go). Ne modifie aucun fichier.

    Si `date` (AAAA-MM-JJ) est fourni, seuls les tournois datés du même
    jour sont comparés : un ancien fichier abandonné (jamais terminé, un
    autre jour) ne doit pas griser un joueur indéfiniment."""
    if not folder or not names:
        return set()
    files = [
        p for p in find_tournament_files(folder, recursive=False)
        if not exclude_path or os.path.abspath(p) != os.path.abspath(exclude_path)
    ]
    if date:
        files = [p for p in files if _read_tournament_date_ro(p) == date]
    if not files:
        return set()
    wanted_by_lower = {n.strip().lower(): n for n in names}
    conflicted = set()
    for path in files:
        for name_lower in _active_player_names_lower_in_file(path):
            original = wanted_by_lower.get(name_lower)
            if original:
                conflicted.add(original)
    return conflicted


def _active_players_and_name_in_file(path):
    """(nom_du_tournoi, [noms de joueurs actifs]) pour le fichier .tournoi
    `path`, consulté en lecture seule."""
    try:
        uri = f"file:{os.path.abspath(path)}?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.OperationalError:
        return ("", [])
    try:
        name_row = conn.execute(
            "SELECT value FROM settings WHERE key='tournament_name'"
        ).fetchone()
        tournament_name = (
            name_row[0] if name_row and name_row[0]
            else os.path.splitext(os.path.basename(path))[0]
        )
        rows = conn.execute("SELECT name FROM players WHERE status='active'").fetchall()
        return (tournament_name, [r[0] for r in rows])
    except sqlite3.OperationalError:
        return ("", [])
    finally:
        conn.close()


def find_stale_active_players(folder, before_date=None, recursive=True):
    """Parcourt les fichiers .tournoi de `folder` (et ses sous-dossiers si
    `recursive`), et renvoie ceux qui ont encore des joueurs 'active' —
    utilisé par le bouton "Tout réactiver" du répertoire pour repérer les
    joueurs restés coincés dans de vieux tournois abandonnés (jamais
    terminés). Si `before_date` (AAAA-MM-JJ) est fourni, seuls les
    tournois datés strictement avant sont pris en compte ; par défaut
    (None), TOUS les tournois du dossier sont vérifiés, quelle que soit
    leur date — la fenêtre de confirmation affichée avant application
    (voir RosterManagerDialog._reactivate_all) reste le seul garde-fou.
    Lecture seule (aucun fichier modifié ici). Renvoie une liste de dicts
    {path, tournament_name, players: [noms]}."""
    if not folder:
        return []
    results = []
    for path in find_tournament_files(folder, recursive=recursive):
        if before_date:
            date = _read_tournament_date_ro(path)
            if not date or date >= before_date:
                continue
        tournament_name, names = _active_players_and_name_in_file(path)
        if names:
            results.append({"path": path, "tournament_name": tournament_name, "players": names})
    return results


def withdraw_stale_active_players(stale_entries):
    """Applique la correction repérée par find_stale_active_players : pour
    chaque entrée, ouvre le fichier .tournoi en écriture et retire
    (forfait, via Database.withdraw_player) chacun de ses joueurs encore
    actifs. Renvoie le nombre total de joueurs libérés."""
    total = 0
    for entry in stale_entries:
        db = Database(entry["path"])
        try:
            for p in db.list_players(status="active"):
                db.withdraw_player(p["id"])
                total += 1
        finally:
            db.close()
    return total


def find_previous_tournament_files(current_path, current_date=None, count=1):
    """Renvoie jusqu'à `count` chemins de fichiers .tournoi datés avant
    `current_date`, parmi les fichiers .tournoi présents dans le même
    dossier que `current_path` (recherche non récursive : uniquement ce
    dossier, pas ses sous-dossiers), du plus récent au plus ancien. Les
    autres fichiers ne sont ouverts qu'en lecture seule (voir
    `_read_tournament_date_ro`). Renvoie une liste vide si `current_path`
    n'est pas encore sauvegardé sur disque, si `count` <= 0, ou si aucun
    tournoi antérieur n'est trouvé (dates égales ou manquantes ignorées)."""
    if not current_path or not os.path.exists(current_path) or count <= 0:
        return []
    folder = os.path.dirname(os.path.abspath(current_path)) or "."
    if current_date is None:
        current_date = _read_tournament_date_ro(current_path)
    if not current_date:
        return []

    candidates = []  # (date, path)
    for path in find_tournament_files(folder, recursive=False):
        if os.path.abspath(path) == os.path.abspath(current_path):
            continue
        d = _read_tournament_date_ro(path)
        if not d or d >= current_date:
            continue
        candidates.append((d, path))
    candidates.sort(key=lambda c: c[0], reverse=True)  # plus récent en premier
    return [path for _, path in candidates[:count]]


def find_previous_tournament_file(current_path, current_date=None):
    """Comme `find_previous_tournament_files` mais renvoie uniquement le
    plus récent (ou None) — pratique quand on ne veut vérifier qu'un seul
    tournoi précédent."""
    found = find_previous_tournament_files(current_path, current_date, count=1)
    return found[0] if found else None


# Valeurs internes acceptées par build_period_summary (paramètre
# `tournament_type`, demande du 2026-09-17 — onglet Statistiques,
# "Type de tournois") — jamais les libellés français affichés (voir
# main.py: STATS_TOURNAMENT_TYPE_LABELS pour la correspondance).
STATS_TOURNAMENT_TYPE_TOURNOIS = "tournois"
STATS_TOURNAMENT_TYPE_SITNGO = "sitngo"
STATS_TOURNAMENT_TYPE_ALL = "all"


def _tournament_type_matches(path, tournament_type):
    """True si le fichier .tournoi `path` correspond au filtre "Type de
    tournois" de l'onglet Statistiques (demande du 2026-09-17) — jamais
    appliqué à find_tournament_files elle-même (partagée par 5 autres
    appelants sans rapport avec Statistiques : Lobby, réactivation de
    joueurs bloqués, tournoi précédent, conflit de joueur actif
    ailleurs), uniquement ici, localement à build_period_summary.

    Convention de nommage établie par tournament_day_folder_proposal
    (main.py) : un tournoi normal commence par "To" (ex. "To270826"), un
    Sit & Go par "Sn" (ex. "Sn270826") — jamais l'inverse, jamais un
    autre préfixe pour ces deux types.

    - STATS_TOURNAMENT_TYPE_TOURNOIS : uniquement les fichiers dont le
      nom commence par "To".
    - STATS_TOURNAMENT_TYPE_SITNGO : uniquement ceux commençant par "Sn".
    - STATS_TOURNAMENT_TYPE_ALL (ou toute autre valeur, y compris None) :
      TOUS les fichiers, y COMPRIS ceux hors convention (ex. l'ancien
      repli "tournoi.tournoi", ou un renommage manuel quelconque) —
      OPTION A validée avec l'utilisateur le 2026-09-17 : "Tous" ne doit
      jamais faire disparaître un fichier qui apparaissait déjà dans
      Statistiques avant l'existence de ce filtre."""
    if tournament_type == STATS_TOURNAMENT_TYPE_TOURNOIS:
        return os.path.basename(path).startswith("To")
    if tournament_type == STATS_TOURNAMENT_TYPE_SITNGO:
        return os.path.basename(path).startswith("Sn")
    return True


# Sous-dossiers "jour" (demande du 2026-09-18, onglet Statistiques,
# 7 cases à cocher Lundi...Dimanche) — mêmes noms que WEEKDAY_NAMES_FR
# (main.py: tournament_day_folder_proposal), qui les CRÉE ; ce module ne
# doit rien importer de main.py (sens inverse des dépendances), donc une
# copie locale plutôt qu'un import croisé.
STATS_WEEKDAY_FOLDER_NAMES = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def _tournament_day_matches(path, folder, selected_days):
    """True si `path` correspond au filtre "jours" de l'onglet
    Statistiques (demande du 2026-09-18) — jamais appliqué à
    find_tournament_files elle-même (voir _tournament_type_matches
    ci-dessus, même principe, uniquement local à build_period_summary).

    Ne détermine JAMAIS le jour depuis la date enregistrée dans le
    tournoi : uniquement depuis les NOMS DE SOUS-DOSSIERS réels sur
    disque (organisation de l'utilisateur : dossier principal/Vendredi/
    To....tournoi), retrouvés via os.path.relpath(dirname(path), folder).

    `selected_days` : None, ou un itérable de noms de jours (sous-
    ensemble de STATS_WEEKDAY_FOLDER_NAMES) actuellement cochés.
    - None, ou une sélection couvrant les 7 jours : BYPASS TOTAL, jamais
      la moindre analyse de chemin — un fichier à la racine, dans
      Lundi...Dimanche, dans un dossier legacy/inconnu, ou dans un autre
      sous-dossier quelconque, est TOUJOURS retenu (comportement
      strictement identique à avant l'existence de ce filtre) — même
      principe que STATS_TOURNAMENT_TYPE_ALL pour le filtre Type.
    - Sélection partielle : retenu seulement si AU MOINS UN segment du
      chemin relatif entre `folder` et le fichier correspond (insensible
      à la casse) à l'un des jours sélectionnés — donc
      Vendredi/sous_dossier/x.tournoi appartient bien à "Vendredi" (pas
      seulement le dossier parent immédiat). Un fichier à la racine, ou
      dans un dossier dont AUCUN segment ne nomme un jour, n'appartient
      alors à aucun jour sélectionné : exclu.
    - Sélection vide (aucun jour coché) : aucun segment ne peut jamais
      correspondre à rien -> exclut tout, 0 résultat, sans cas spécial.

    Appelée uniquement quand `recursive` est vrai côté build_period_
    summary (voir sa docstring) : sans sous-dossiers, aucun fichier ne
    peut de toute façon se trouver "dans" un jour, le filtre serait sans
    objet."""
    if selected_days is None:
        return True
    selected_cf = {d.casefold() for d in selected_days}
    if selected_cf >= {d.casefold() for d in STATS_WEEKDAY_FOLDER_NAMES}:
        return True
    rel = os.path.relpath(os.path.dirname(path), folder)
    segments = [] if rel == os.curdir else rel.split(os.sep)
    return any(seg.casefold() in selected_cf for seg in segments)


def build_period_summary(folder, date_from=None, date_to=None, recursive=True,
                          tournament_type=STATS_TOURNAMENT_TYPE_ALL, selected_days=None):
    """Parcourt tous les fichiers .tournoi d'un dossier et construit une
    synthèse des résultats pour la période indiquée. `date_from` /
    `date_to` sont des chaînes 'AAAA-MM-JJ' (bornes incluses), ou None
    pour ne pas borner. `tournament_type` (demande du 2026-09-17, voir
    _tournament_type_matches ci-dessus) filtre en plus par CONVENTION DE
    NOMMAGE du fichier (To.../Sn.../tous). `selected_days` (demande du
    2026-09-18, voir _tournament_day_matches ci-dessus) filtre en plus
    par SOUS-DOSSIER "jour" (Lundi...Dimanche) — mais UNIQUEMENT si
    `recursive` est vrai : sans sous-dossiers explorés, aucun fichier ne
    peut se trouver "dans" un jour, ce filtre serait sans effet et
    DOIT rester totalement inopérant (demande explicite, cases grisées
    côté main.py) plutôt que de faire disparaître les fichiers de la
    racine. Les trois filtres (type, jour, période) se combinent : un
    fichier doit tous les passer pour être retenu, dans la même passe
    sur les fichiers, jamais des parcours distincts. Renvoie un dict :

      {
        "tournaments": [ {name, date, path, entries, prize_pool, status,
                           winner, bounty_distributed}, ... ],  # triés par date
        "players": [ {name, tournaments_played, wins, best_place,
                       total_presence_assiduity, total_ranking_points,
                       total_bounty_won, total_points}, ... ],  # triés par total_points décroissant
      }

    "total_bounty_won" (par joueur) et "bounty_distributed" (par tournoi)
    viennent de la MÊME source que l'onglet Primes de chaque tournoi (voir
    Database.get_primes_summary, colonne "Mon Bounty" -> Database.
    get_bounty_bonuses) : nombre d'éliminations (kills) × valeur fixe d'un
    bounty en mode CLASSIQUE, ou les vrais gains PKO définitivement acquis
    (`bounty_won`, bounty finale du vainqueur incluse) en mode PKO — les
    deux mécanismes ont été unifiés le 2026-09-08, il n'y a plus de champ
    "ancien"/"indépendant" ici : cette synthèse reflète toujours exactement
    ce qu'affiche l'onglet Primes du tournoi correspondant, quel que soit
    son mode.

    "total_points" par joueur = somme, sur toute la période, du TOTAL de
    l'onglet Primes de chaque tournoi joué (Présence + Assiduité +
    Classement + Bounty, en points).

    "total_presence_assiduity" (demande du 2026-09-18, remplace l'ancien
    "total_cost" en euros — voir plus bas) = somme, sur la période, de
    presence + assiduite de get_primes_summary() pour chaque tournoi :
    EXCLUSIVEMENT ces valeurs, jamais une formule dupliquée ici.
    "total_ranking_points" (remplace l'ancien "total_gain" en euros) =
    somme de cl_montant de get_primes_summary() — donc déjà correct pour
    les 5 formules de classement (Aucun/Classique/Progressive/Tournois
    CPC/Sit & Go CPC, voir resolve_ranking_formula/ranking_points,
    jamais recalculées ici) et pour l'ancien réglage "valeur fixe"
    hérité, naturellement à 0 pour un forfait (get_ranking_bonuses ne
    lui attribue jamais de place). "total_presence_assiduity" : depuis
    la règle métier validée le 2026-09-18 ("un forfait est un joueur
    inscrit qui n'est jamais venu"),
    get_presence_bonuses/get_assiduity_bonuses EXCLUENT
    désormais les forfaits (status='withdrawn') — un forfait ne contribue
    donc plus du tout à "total_presence_assiduity" pour ce tournoi
    (remplace le comportement antérieur, voir git history). Primes
    désactivées pour un tournoi (_primes_enabled faux) : get_primes_
    summary() renvoie [], donc aucune contribution de ce tournoi à ces
    deux totaux, exactement comme pour total_bounty_won/total_points.

    "tournaments_played" (règle métier validée le 2026-09-15) : un joueur
    déclaré forfait (status='withdrawn', voir Database.withdraw_player) a
    bien participé ADMINISTRATIVEMENT à ce tournoi (inscrit, blindes
    éventuellement prélevées avant sa déclaration de forfait ~1h après le
    début) — il reste donc dans "players" et continue de peser sur
    "total_bounty_won"/"total_points" (bounty : mécanique de kills,
    volontairement non touchée par la règle du 2026-09-18 ci-dessus — un
    forfait ayant éliminé un adversaire AVANT sa propre déclaration de
    forfait garde ce bounty) — mais ce tournoi ne doit PAS compter dans
    son nombre de tournois JOUÉS : "tournaments_played" n'est incrémenté
    que pour un joueur resté "active" (encore en jeu, y compris le
    vainqueur) ou "eliminated" (a réellement joué jusqu'à son
    élimination), jamais pour "withdrawn". "wins"/"best_place" restent,
    eux, déjà inatteignables pour un forfait de toute façon (aucune
    "place" ne lui est jamais attribuée, voir la boucle plus bas) —
    inchangé par cette règle.

    SUPPRIMÉ le 2026-09-18 (demande explicite, plus aucun usage ailleurs
    dans le projet — vérifié) : "total_cost"/"total_gain" (€, calculés à
    partir de buyin_amount/rebuy_amount/addon_amount/get_payouts_
    amounts) — remplacés ci-dessus par les deux totaux en points."""
    tournaments = []
    players = {}

    for path in find_tournament_files(folder, recursive=recursive):
        if not _tournament_type_matches(path, tournament_type):
            continue
        if recursive and not _tournament_day_matches(path, folder, selected_days):
            continue
        try:
            db = Database(path, read_only=True)
        except Exception as e:
            # Échec à l'OUVERTURE de la connexion (permissions, fichier
            # supprimé entre le glob et l'ouverture...) — voir le second
            # bloc try ci-dessous pour l'échec, plus fréquent en pratique,
            # sur la PREMIÈRE VRAIE REQUÊTE (sqlite3 ne valide le format
            # du fichier qu'à la première lecture, jamais à la connexion
            # elle-même, voir _log_skipped_tournament_file).
            _log_skipped_tournament_file(path, e)
            continue

        # Demande du 2026-09-14, suite à un fichier .tournoi corrompu
        # ayant fait échouer TOUTE la synthèse (pas seulement le fichier
        # en cause) : `tournament_entry`/`player_updates` sont calculés
        # dans des variables LOCALES, jamais directement ajoutés à
        # `tournaments`/`players` avant la fin du bloc ci-dessous — pour
        # qu'une erreur survenant n'importe où pendant le traitement de
        # CE fichier (première requête comme les suivantes, y compris au
        # milieu de la boucle sur les joueurs) ne laisse jamais un
        # tournoi à moitié construit ou une agrégation partielle
        # contaminer le résultat global. `finally: db.close()` garantit
        # que la connexion est TOUJOURS refermée proprement, que ce
        # fichier ait pu être traité ou non.
        tournament_entry = None
        player_updates = None
        try:
            date = db.get_tournament_date()
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue

            name = db.get_setting(
                "tournament_name", os.path.splitext(os.path.basename(path))[0]
            )
            all_players = db.list_players()
            active = [p for p in all_players if p["status"] == "active"]
            # "Terminé" (demande du 2026-09-14) : réutilise EXACTEMENT la
            # règle déjà appliquée par le Lobby (get_live_status, voir sa
            # docstring) — <= 1 joueur actif restant ET plus d'un joueur
            # au total — plutôt qu'une définition indépendante ("== 1"
            # actif) qui divergeait du Lobby dès que TOUS les joueurs
            # avaient fini par quitter le tournoi (forfaits compris,
            # 0 actif restant) sans qu'aucun vainqueur ne soit désigné.
            finished = db.get_live_status()["finished"]
            # "Vainqueur" reste distinct de "Terminé" : un tournoi peut
            # être "Terminé" au sens ci-dessus sans qu'il ne reste
            # personne pour être désigné vainqueur (0 actif) — dans ce
            # cas précis, "-" (comme "En cours") plutôt qu'une IndexError
            # sur `active[0]`.
            winner = active[0]["name"] if len(active) == 1 else "-"
            # Même source que l'onglet Primes de ce tournoi (voir
            # get_primes_summary) : "bo_montant" = bounty en points, "total"
            # = Présence + Assiduité + Classement + Bounty pour ce tournoi.
            primes_by_name = {r["name"]: r for r in db.get_primes_summary()}
            bounty_distributed = sum(r["bo_montant"] for r in primes_by_name.values())
            entries = sum(p["buyin_count"] for p in all_players)
            stats = db.get_stats()

            tournament_entry = {
                "name": name,
                "date": date,
                "path": path,
                "entries": entries,
                "prize_pool": stats["prize_pool"],
                "status": "Terminé" if finished else "En cours",
                "winner": winner,
                "bounty_distributed": bounty_distributed,
            }

            player_updates = []
            for p in all_players:
                place = None
                if p["status"] == "eliminated":
                    place = p["place"]
                elif p["status"] == "active" and finished:
                    place = 1

                # EXCLUSIVEMENT get_primes_summary() (source unique déjà
                # utilisée pour bounty_won/points ci-dessous, voir la
                # docstring de cette fonction) : jamais une formule
                # dupliquée pour presence/assiduite/cl_montant — ces
                # valeurs sont déjà correctes pour les 5 formules de
                # classement (Aucun/Classique/Progressive/Tournois CPC/
                # Sit & Go CPC)
                # et pour un tournoi "primes désactivées" (prime_row is
                # None -> 0 partout, comme bounty_won/points).
                prime_row = primes_by_name.get(p["name"])
                bounty_won = prime_row["bo_montant"] if prime_row else 0
                points = prime_row["total"] if prime_row else 0
                presence_assiduity = (
                    prime_row["presence"] + prime_row["assiduite"] if prime_row else 0
                )
                ranking_pts = prime_row["cl_montant"] if prime_row else 0
                # Règle métier du 2026-09-15 (voir la docstring de cette
                # fonction) : un forfait ("withdrawn") compte comme une
                # participation ADMINISTRATIVE, jamais comme un tournoi
                # JOUÉ — seul "played" distingue les deux ci-dessous, rien
                # d'autre n'est modifié pour ce joueur (primes...).
                played = p["status"] != "withdrawn"
                player_updates.append((
                    p["name"], place, bounty_won, points,
                    presence_assiduity, ranking_pts, played,
                ))
        except Exception as e:
            _log_skipped_tournament_file(path, e)
            continue
        finally:
            db.close()

        tournaments.append(tournament_entry)
        for p_name, place, bounty_won, points, presence_assiduity, ranking_pts, played in player_updates:
            agg = players.setdefault(p_name, {
                "name": p_name,
                "tournaments_played": 0,
                "wins": 0,
                "best_place": None,
                "total_presence_assiduity": 0,
                "total_ranking_points": 0,
                "total_bounty_won": 0,
                "total_points": 0,
            })
            if played:
                agg["tournaments_played"] += 1
            agg["total_presence_assiduity"] += presence_assiduity
            agg["total_ranking_points"] += ranking_pts
            agg["total_bounty_won"] += bounty_won
            agg["total_points"] += points
            if place == 1:
                agg["wins"] += 1
            if place is not None and (agg["best_place"] is None or place < agg["best_place"]):
                agg["best_place"] = place

    tournaments.sort(key=lambda t: t["date"])
    players_list = sorted(players.values(), key=lambda a: a["total_points"], reverse=True)
    return {"tournaments": tournaments, "players": players_list}


def _period_range_label(date_from, date_to):
    """Texte lisible de la période couverte par une synthèse
    (build_period_summary), à afficher dans les exports (CSV/Excel/PDF) —
    sans lui, un fichier exporté ne permet pas de savoir, une fois hors de
    l'application, sur quelle période (dates du/au choisies dans l'onglet
    Statistiques) portait son contenu."""
    if not date_from and not date_to:
        return "Période : toutes dates confondues"
    if date_from and date_to:
        return f"Période : du {format_date_fr(date_from)} au {format_date_fr(date_to)}"
    if date_from:
        return f"Période : à partir du {format_date_fr(date_from)}"
    return f"Période : jusqu'au {format_date_fr(date_to)}"


# Colonnes disponibles pour l'export de la synthèse par période, sous la
# forme (clé, en-tête, fonction d'extraction de la valeur à partir d'une
# ligne de tournoi/joueur). Définies une seule fois ici et réutilisées à
# la fois par l'export CSV, l'export Excel et la boîte de dialogue de
# sélection des colonnes (main.py) — ainsi les trois restent toujours en
# phase.
PERIOD_TOURNAMENT_COLUMNS = [
    ("date", "Date", lambda t: format_date_fr(t["date"])),
    ("name", "Tournoi", lambda t: t["name"]),
    ("status", "Statut", lambda t: t["status"]),
    ("entries", "Entrées", lambda t: t["entries"]),
    # "prize_pool" ("Prize pool (€)") retiré le 2026-09-18 (demande
    # explicite : ce club ne distribue pas de gains en argent réel, ce
    # champ ne sert plus dans l'export Statistiques) — UNIQUEMENT cette
    # entrée de colonne d'export : build_period_summary continue de
    # calculer et de porter tournament_entry["prize_pool"] (depuis
    # Database.get_stats(), lui-même utilisé ailleurs — exports
    # Résultats/Classement notamment, jamais touchés ici) ; rien dans
    # les fichiers .tournoi ni dans les calculs historiques n'est
    # modifié, seule cette colonne n'est plus PROPOSÉE à l'export.
    ("winner", "Vainqueur", lambda t: t["winner"]),
    ("bounty_distributed", "Primes distribuées (pts)", lambda t: t["bounty_distributed"]),
]

PERIOD_PLAYER_COLUMNS = [
    # "rang" (demande du 2026-09-18) : PAS calculé ici — build_period_
    # summary n'a aucune connaissance du filtre Club (appliqué en aval,
    # dans main.py), or le rang doit être recalculé sur la liste APRÈS
    # ce filtre (voir main.py: _stats_players_with_rank). Chaque entrée
    # `a` passée à ce lambda DOIT déjà porter "rang" — jamais un repli
    # a.get("rang", "") : un oubli d'appeler _stats_players_with_rank en
    # amont doit lever une KeyError bien visible, jamais être masqué
    # silencieusement (choix explicite, voir tests qui appellent les
    # fonctions d'export directement : elles préparent "rang" elles-
    # mêmes avant l'appel).
    ("rang", "Rang", lambda a: a["rang"]),
    ("name", "Joueur", lambda a: a["name"]),
    ("tournaments_played", "Tournois joués", lambda a: a["tournaments_played"]),
    ("wins", "Victoires", lambda a: a["wins"]),
    ("best_place", "Meilleur Rang", lambda a: a["best_place"]),
    # Remplacent depuis le 2026-09-18 les anciennes colonnes en euros
    # "Total investi (€)"/"Gains classement (€)" (plus aucun usage
    # ailleurs dans le projet, retirées de build_period_summary) : sommes
    # en POINTS, EXCLUSIVEMENT depuis Database.get_primes_summary (voir
    # sa docstring et celle de build_period_summary) — jamais une
    # formule dupliquée ici.
    ("total_presence_assiduity", "Pts Prés/Ass", lambda a: a["total_presence_assiduity"]),
    ("total_ranking_points", "Pts Gain Clsmt", lambda a: a["total_ranking_points"]),
    ("total_bounty_won", "Bounty", lambda a: a["total_bounty_won"]),
    ("total_points", "TOTAL Pts", lambda a: a["total_points"]),
]

# Colonnes disponibles pour l'export du classement final nominatif d'UN
# tournoi (menu Fichier > Exporter les résultats...). Le rang du vainqueur
# est un entier (1), pas une chaîne, pour éviter le souci d'alignement
# Excel entre texte et nombres dans la même colonne.
RESULT_COLUMNS = [
    ("rang", "Rang", lambda r: r["rang"]),
    ("name", "Nom", lambda r: r["name"]),
    ("status", "Statut", lambda r: r["status"]),
    ("gain", "Gain (€)", lambda r: round(r["gain"], 2) if r["gain"] else None),
    ("buyin", "Buy-ins", lambda r: r["buyin"]),
    ("rebuy", "Rebuys", lambda r: r["rebuy"]),
    ("addon", "Add-ons", lambda r: r["addon"]),
    ("bounty_won", "Prime gagnée (pts)", lambda r: r["bounty_won"]),
]

# Colonnes disponibles pour l'export de la grille de gains telle
# qu'affichée dans l'onglet Gains (place -> pourcentage -> montant, sans
# nom de joueur) — distinct du classement nominatif ci-dessus.
PAYOUT_COLUMNS = [
    ("place", "Place", lambda r: r["place"]),
    ("percentage", "Pourcentage (%)", lambda r: round(r["percentage"], 1)),
    ("amount", "Montant (€)", lambda r: round(r["amount"], 2)),
]

# Colonnes disponibles pour l'export de l'onglet Joueurs, dans le même
# ordre que son tableau (nom, table, siège, chips, achats, prime en jeu,
# statut, rang) — distinct du classement final nominatif (RESULT_COLUMNS,
# qui a le gain et la prime déjà empochée plutôt que la prime en jeu).
PLAYERS_TAB_COLUMNS = [
    ("name", "Nom", lambda p: p["name"]),
    ("table", "Table", lambda p: p["table"]),
    ("seat", "Siège", lambda p: p["seat"]),
    ("chips", "Chips", lambda p: p["chips"]),
    ("buyin", "Buy-in", lambda p: p["buyin"]),
    ("rebuy", "Rebuys", lambda p: p["rebuy"]),
    ("addon", "Add-ons", lambda p: p["addon"]),
    ("bounty", "Prime", lambda p: p["bounty"]),
    ("status", "Statut", lambda p: p["status"]),
    ("rang", "Rang", lambda p: p["rang"]),
    ("elim_time", "Éliminé le", lambda p: format_datetime_fr(p["elim_time"])),
    ("elim_round", "Round", lambda p: p["elim_round"]),
    ("eliminated_by", "Éliminé par", lambda p: p["eliminated_by"]),
]

# Colonnes disponibles pour l'export de l'onglet Primes, dans le même ordre
# que son tableau (nom, présence, assiduité, rang, classement, bounty
# nombre/valeur/montant, total). Voir Database.get_primes_summary.
PRIMES_COLUMNS = [
    ("name", "Joueur", lambda r: r["name"]),
    ("rang", "Rang", lambda r: r["rang"]),
    ("presence", "Présence", lambda r: r["presence"]),
    ("assiduite", "Assiduité", lambda r: r["assiduite"]),
    ("cl_montant", "Classement", lambda r: r["cl_montant"]),
    ("bo_nombre", "Nb Bounty", lambda r: r["bo_nombre"]),
    ("bo_valeur", "Val Bounty", lambda r: r["bo_valeur"]),
    ("bo_montant", "Mon Bounty", lambda r: r["bo_montant"]),
    ("total", "TOTAL", lambda r: r["total"]),
]

# Réglages imprimables de l'onglet Paramètres (bouton "Imprimer
# Paramètres...", voir App._print_settings_pdf) : (clé, libellé), dans le
# même ordre que le formulaire (colonne gauche puis colonne droite). Tenu
# à jour manuellement en phase avec main.py._build_settings_tab — pas de
# source unique automatique ici, ces libellés n'existent que côté widgets
# Tk (self.settings_vars), pas dans ce module.
SETTINGS_PRINT_FIELDS = [
    ("club_name", "Nom du Club"),
    ("tournament_name", "Nom du tournoi"),
    ("buyin_amount", "Montant du buy-in (€)"),
    ("rebuy_amount", "Montant d'un rebuy (€)"),
    ("addon_amount", "Montant d'un add-on (€)"),
    ("starting_chips", "Tapis de départ (chips)"),
    ("rebuy_chips", "Chips reçues pour un rebuy"),
    ("addon_chips", "Chips reçues pour un add-on"),
    ("max_seats_per_table", "Nombre de sièges par table"),
    ("min_players_per_table", "Nombre minimum de joueurs par table avant rééquilibrage"),
    ("highlight_duration_minutes", "Durée de surbrillance des derniers joueurs déplacés (minutes)"),
    ("rake_percent", "Rake / frais d'organisation (%)"),
    ("movement_signal_duration_ms", "Durée max. du signal de mouvement (ms)"),
    ("tournament_day_folder", "Chemin du dossier du tournoi du jour"),
    ("start_small_blind", "Small blind (niveau 1)"),
    ("start_big_blind", "Big blind (niveau 1)"),
    ("ante_start_level", "Niveau à partir duquel l'ante commence"),
    ("start_ante", "Valeur de l'ante de départ"),
    # round_duration_minutes/round_count_1/round_duration_minutes_2/
    # round_count_2/break_minutes_1/break_after_round_1/break_minutes_2/
    # break_after_round_2 (chantier "Paramètres > Structure des blindes",
    # 2026-09-24) : remplacent les anciennes entrées "Durée d'un Round"/
    # "Durée de la Pause" (1 seule valeur chacune) — break_duration_minutes
    # reste une clé valide en base (voir DEFAULT_SETTINGS/App._edit_break_
    # duration, onglet Chronomètre, chantier SANS RAPPORT) mais n'est plus
    # un réglage de CET onglet, donc retirée de cette liste.
    ("round_duration_minutes", "Durée d'un Round — ligne 1 (minutes)"),
    ("round_count_1", "Nb Rounds — ligne 1"),
    ("round_duration_minutes_2", "Durée d'un Round — ligne 2 (minutes)"),
    ("round_count_2", "Nb Rounds — ligne 2"),
    ("break_minutes_1", "Durée de la pause 1 (minutes)"),
    ("break_after_round_1", "Pause 1 — après round"),
    ("break_minutes_2", "Durée de la pause 2 (minutes)"),
    ("break_after_round_2", "Pause 2 — après round"),
    ("attendance_bonus_points", "Prime de présence (points)"),
    ("assiduity_bonus_points", "Prime d'assiduité (points)"),
    ("assiduity_consecutive_days", "Nombre de jours consécutifs (assiduité)"),
    ("ranking_formula", "Système de points distribués (classement)"),
    ("bounty_amount", "Montant du bounty (points)"),
    ("pko_mode", "Mode PKO (prime progressive)"),
    ("pko_cash_percent", "Part en Perso immédiat en PKO (%)"),
]

# Colonnes disponibles pour l'export de la 2e table de l'onglet Primes,
# l'historique du bounty progressif (mécanisme PKO interne, voir
# get_bounty_events) — distinct du récapitulatif ci-dessus.
BOUNTY_HISTORY_COLUMNS = [
    ("time", "Heure", lambda r: format_datetime_fr(r["event_time"])),
    ("eliminated", "Joueur éliminé", lambda r: r["eliminated_name"]),
    ("eliminator", "Éliminé par", lambda r: r["eliminator_name"] or "-"),
    ("amount", "Points gagnés", lambda r: r["amount_won"]),
    ("grow", "Ajouté à sa prime", lambda r: r["added_to_eliminator_bounty"] or 0),
]


def _selected_period_columns(columns, keys):
    """Sous-ensemble de `columns` (une des listes ci-dessus) correspondant
    à `keys`, dans l'ordre d'origine ; toutes les colonnes si `keys` est
    None."""
    if keys is None:
        return columns
    keys = set(keys)
    return [c for c in columns if c[0] in keys]


def export_period_summary_csv(
    summary, path, tournament_keys=None, player_keys=None, date_from=None, date_to=None,
):
    """Exporte une synthèse (issue de build_period_summary) en CSV : une
    section 'Tournois de la période', puis une section 'Classement des
    joueurs' incluant les primes (bounty) empochées. `tournament_keys` /
    `player_keys` permettent de ne garder qu'un sous-ensemble de colonnes
    (voir PERIOD_TOURNAMENT_COLUMNS / PERIOD_PLAYER_COLUMNS) ; None = toutes.
    `date_from`/`date_to` (AAAA-MM-JJ ou None) : bornes de la période
    choisies dans l'onglet Statistiques, affichées en clair sous chaque
    titre de section (voir _period_range_label)."""
    import csv

    t_cols = _selected_period_columns(PERIOD_TOURNAMENT_COLUMNS, tournament_keys)
    p_cols = _selected_period_columns(PERIOD_PLAYER_COLUMNS, player_keys)
    period_label = _period_range_label(date_from, date_to)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if t_cols:
            writer.writerow(["Tournois de la période"])
            writer.writerow([period_label])
            writer.writerow([h for _, h, _ in t_cols])
            for t in summary["tournaments"]:
                writer.writerow([fn(t) for _, _, fn in t_cols])
            writer.writerow([])
        if p_cols:
            writer.writerow(["Classement des joueurs sur la période"])
            writer.writerow([period_label])
            writer.writerow([h for _, h, _ in p_cols])
            for a in summary["players"]:
                writer.writerow([fn(a) for _, _, fn in p_cols])
    return path


def export_period_summary_xlsx(
    summary, path, tournament_keys=None, player_keys=None, date_from=None, date_to=None,
):
    """Exporte une synthèse en Excel (.xlsx) : une feuille 'Tournois', une
    feuille 'Joueurs', avec les mêmes options de sélection de colonnes que
    export_period_summary_csv (voir aussi date_from/date_to là-bas).
    Nécessite le paquet 'openpyxl'."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    t_cols = _selected_period_columns(PERIOD_TOURNAMENT_COLUMNS, tournament_keys)
    p_cols = _selected_period_columns(PERIOD_PLAYER_COLUMNS, player_keys)
    period_label = _period_range_label(date_from, date_to)
    header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")

    def _write_sheet(ws, cols, rows):
        # Ligne 1 : période couverte (italique, fusionnée sur toute la
        # largeur du tableau) ; ligne 2 : en-têtes de colonnes (décalés
        # d'une ligne par rapport à avant, d'où row=2 ci-dessous).
        ws.append([period_label])
        ws.cell(row=1, column=1).font = Font(italic=True, color="555555")
        if len(cols) > 1:
            ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(cols))
        ws.append([h for _, h, _ in cols])
        for col in range(1, len(cols) + 1):
            cell = ws.cell(row=2, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        for row in rows:
            ws.append([fn(row) for _, _, fn in cols])
        for i, _ in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = 20

    wb = Workbook()
    first = True
    if t_cols:
        ws_t = wb.active
        ws_t.title = "Tournois"
        first = False
        _write_sheet(ws_t, t_cols, summary["tournaments"])
    if p_cols:
        ws_p = wb.active if first else wb.create_sheet("Joueurs")
        ws_p.title = "Joueurs"
        _write_sheet(ws_p, p_cols, summary["players"])
    if not t_cols and not p_cols:
        wb.active.title = "Synthèse"

    wb.save(path)
    return path


def export_period_summary_pdf(
    summary, path, tournament_keys=None, player_keys=None, date_from=None, date_to=None,
):
    """Exporte une synthèse en PDF : une page 'Tournois de la période', une
    page 'Classement des joueurs sur la période' (chacune omise si sa
    sélection de colonnes est vide), avec les mêmes options que
    export_period_summary_csv (voir aussi date_from/date_to là-bas).
    Nécessite le paquet 'fpdf2'."""
    from fpdf import FPDF

    t_cols = _selected_period_columns(PERIOD_TOURNAMENT_COLUMNS, tournament_keys)
    p_cols = _selected_period_columns(PERIOD_PLAYER_COLUMNS, player_keys)
    period_label = _period_range_label(date_from, date_to)

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)

    def _draw_section(title, cols, rows):
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, _pdf_text(title), border=0)
        pdf.ln(9)
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(85, 85, 85)
        pdf.cell(0, 7, _pdf_text(period_label), border=0)
        pdf.set_text_color(0, 0, 0)
        pdf.ln(10)

        headers = [h for _, h, _ in cols]
        avail_width = pdf.w - pdf.l_margin - pdf.r_margin
        col_width = avail_width / max(1, len(headers))
        row_height = 7

        header_texts = [_pdf_text(h) for h in headers]
        _pdf_fit_font_size(pdf, header_texts, col_width, bold=True)
        pdf.set_fill_color(31, 78, 36)
        pdf.set_text_color(255, 255, 255)
        for h in header_texts:
            pdf.cell(col_width, row_height + 1, h, border=1, align="C", fill=True)
        pdf.ln(row_height + 1)

        body_texts = [_pdf_text(fn(row)) for row in rows for _, _, fn in cols] or [""]
        _pdf_fit_font_size(pdf, body_texts, col_width, bold=False)
        pdf.set_text_color(0, 0, 0)
        fill_toggle = False
        for row in rows:
            if fill_toggle:
                pdf.set_fill_color(247, 241, 227)
            else:
                pdf.set_fill_color(255, 255, 255)
            for _, _, fn in cols:
                pdf.cell(col_width, row_height, _pdf_text(fn(row)), border=1, align="C", fill=True)
            pdf.ln(row_height)
            fill_toggle = not fill_toggle

    if t_cols:
        _draw_section("Tournois de la période", t_cols, summary["tournaments"])
    if p_cols:
        _draw_section("Classement des joueurs sur la période", p_cols, summary["players"])
    if not t_cols and not p_cols:
        pdf.add_page()  # évite un PDF sans aucune page si tout est décoché

    pdf.output(path)
    return path
