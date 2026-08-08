# -*- coding: utf-8 -*-
"""
Répertoire de joueurs habituels, indépendant des tournois eux-mêmes.
Stocké dans le dossier personnel de l'utilisateur (~/.poker_tournament/roster.json)
afin d'être disponible quel que soit l'endroit où l'application est lancée
et quel que soit le fichier .tournoi ouvert.
"""
import json
import os

def _roster_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d

def _roster_path():
    return os.path.join(_roster_dir(), "roster.json")

def load_roster():
    path = _roster_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return sorted({str(n).strip() for n in data if str(n).strip()}, key=str.lower)
    except (json.JSONDecodeError, OSError):
        return []

def save_roster(names):
    path = _roster_path()
    cleaned = sorted({str(n).strip() for n in names if str(n).strip()}, key=str.lower)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def add_to_roster(name):
    name = name.strip()
    if not name:
        return
    names = load_roster()
    if name not in names:
        names.append(name)
        save_roster(names)

def remove_from_roster(name):
    names = load_roster()
    if name in names:
        names.remove(name)
        save_roster(names)

def rename_in_roster(old_name, new_name):
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name:
        return
    names = load_roster()
    changed = False
    if old_name in names:
        names.remove(old_name)
        changed = True
    if new_name not in names:
        names.append(new_name)
        changed = True
    if changed:
        save_roster(names)
