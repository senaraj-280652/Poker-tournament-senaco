# -*- coding: utf-8 -*-
"""
Derniers paramètres de tournoi utilisés (montants de buy-in, nombre de
sièges par table, etc.), mémorisés indépendamment de chaque fichier
.tournoi afin d'être proposés automatiquement à la création d'un nouveau
tournoi — sur le même principe que le répertoire de joueurs (roster.py).
Stocké dans ~/.poker_tournament/last_settings.json.

Le nom du tournoi n'est volontairement PAS mémorisé ici : chaque tournoi
doit garder son propre nom.
"""
import json
import os

# Réglages considérés comme des "préférences" réutilisables d'un tournoi à
# l'autre. Les réglages d'état du chrono (niveau en cours, pause...) et le
# nom du tournoi en sont volontairement exclus.
PERSISTED_KEYS = [
    "buyin_amount",
    "rebuy_amount",
    "addon_amount",
    "starting_chips",
    "rebuy_chips",
    "addon_chips",
    "max_seats_per_table",
    "min_players_per_table",
    "highlight_duration_minutes",
    "rake_percent",
    "start_small_blind",
    "start_big_blind",
    "ante_start_level",
    "start_ante",
    "break_duration_minutes",
    "movement_signal_duration_ms",
    "bounty_amount",
    "pko_mode",
    "pko_cash_percent",
    "tournament_day_folder",
    "tournament_days_of_week",
    "elimination_banner_seconds",
]


def _prefs_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d


def _prefs_path():
    return os.path.join(_prefs_dir(), "last_settings.json")


def load_last_settings():
    """Renvoie le dict des derniers paramètres mémorisés (sous-ensemble de
    PERSISTED_KEYS présent). Dict vide si rien n'a encore été enregistré."""
    path = _prefs_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return {k: v for k, v in data.items() if k in PERSISTED_KEYS}
    except (json.JSONDecodeError, OSError):
        return {}


def save_last_settings(values):
    """Mémorise le sous-ensemble pertinent de `values` (dict de réglages)
    pour les proposer par défaut au prochain nouveau tournoi. Fusionné
    avec ce qui est déjà enregistré (pas un remplacement complet) : un
    appel avec un sous-ensemble partiel de PERSISTED_KEYS (ex :
    tournament_day_folder seul, voir _save_day_folder) n'efface donc pas
    les autres préférences déjà mémorisées."""
    to_save = {k: v for k, v in values.items() if k in PERSISTED_KEYS}
    if not to_save:
        return
    existing = load_last_settings()
    existing.update(to_save)
    path = _prefs_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
