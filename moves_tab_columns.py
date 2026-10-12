# -*- coding: utf-8 -*-
"""Largeurs des 5 colonnes du "mode joueurs" de l'onglet Mouvements
(demande du 2026-10-05, lisibilité à distance quand "Afficher Mouvements"
est déclenché depuis le téléphone — voir main.py : App._apply_moves_tab_
display_mode). Mémorisées indépendamment de l'affichage normal de cet
onglet (ses propres largeurs, réservées à l'organisateur, ne passent
jamais par ce module) et indépendamment de tournament_prefs.py (réservé
aux réglages DE TOURNOI hérités d'un tournoi à l'autre, pas à une
géométrie d'interface) — même principe qu'un fichier dédié unique que
structures.py (structure standard) ou roster.py (répertoire), chacun pour
un seul réglage précis.

Stocké dans ~/.poker_tournament/moves_tab_player_columns.json.
"""
import json
import os

PLAYER_COLUMNS = ("player", "old_table", "old_seat", "new_table", "new_seat")


def _path():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "moves_tab_player_columns.json")


def load_player_column_widths():
    """Renvoie le dict {colonne: largeur en pixels} mémorisé pour les 5
    colonnes du mode joueurs, ou None si rien n'a encore été enregistré
    ou si le contenu est invalide/incomplet — jamais une exception."""
    path = _path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    widths = data.get("widths")
    if not isinstance(widths, dict) or not all(c in widths for c in PLAYER_COLUMNS):
        return None
    try:
        return {c: int(widths[c]) for c in PLAYER_COLUMNS}
    except (TypeError, ValueError):
        return None


def save_player_column_widths(widths):
    """Enregistre les 5 largeurs (dict {colonne: largeur}) — remplace
    entièrement l'enregistrement précédent (un seul jeu de largeurs
    mémorisé pour le mode joueurs, pas un historique)."""
    path = _path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"widths": {c: int(widths[c]) for c in PLAYER_COLUMNS}},
            f, ensure_ascii=False, indent=2,
        )
