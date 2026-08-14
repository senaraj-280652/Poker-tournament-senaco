# -*- coding: utf-8 -*-
"""
Mémorise les dernières préférences d'export (colonnes cochées, format
CSV/Excel) choisies par l'utilisateur dans les fenêtres d'export
(résultats d'un tournoi, synthèse par période), indépendamment de chaque
tournoi, pour les reproposer par défaut au prochain export. Stocké dans
~/.poker_tournament/export_prefs.json (même principe que roster.py /
tournament_prefs.py).
"""
import json
import os


def _prefs_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d


def _prefs_path():
    return os.path.join(_prefs_dir(), "export_prefs.json")


def _load_all():
    path = _prefs_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data):
    with open(_prefs_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_columns(key, all_keys):
    """Colonnes cochées mémorisées pour `key` (identifiant de la fenêtre
    d'export concernée), filtrées pour ne garder que celles qui existent
    toujours parmi `all_keys`. Si rien n'a encore été mémorisé, renvoie
    `all_keys` (tout coché par défaut, comportement d'origine)."""
    saved = _load_all().get(key)
    if not isinstance(saved, list):
        return list(all_keys)
    return [k for k in saved if k in all_keys]


def save_columns(key, keys):
    data = _load_all()
    data[key] = list(keys)
    _save_all(data)


def load_format(key, default="csv"):
    val = _load_all().get(key + "_format")
    return val if val in ("csv", "xlsx", "pdf") else default


def save_format(key, fmt):
    data = _load_all()
    data[key + "_format"] = fmt
    _save_all(data)


def load_value(key, default=None):
    """Valeur simple mémorisée sous `key` (ex : dernier dossier utilisé
    dans le Lobby SNG), ou `default` si rien n'a encore été enregistré."""
    return _load_all().get(key, default)


def save_value(key, value):
    data = _load_all()
    data[key] = value
    _save_all(data)
