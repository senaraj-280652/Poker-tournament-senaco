# -*- coding: utf-8 -*-
"""
Répertoire de joueurs habituels, indépendant des tournois eux-mêmes.
Stocké dans le dossier personnel de l'utilisateur (~/.poker_tournament/roster.json)
afin d'être disponible quel que soit l'endroit où l'application est lancée
et quel que soit le fichier .tournoi ouvert.

Chaque entrée est un dict {"name": ..., "club": ...} (le club peut être
une chaîne vide si inconnu). Les anciens fichiers roster.json (simple
liste de noms) sont migrés automatiquement à la lecture, sans club.
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

def load_roster_entries():
    """Liste de dicts {"name", "club"}, triée par nom (insensible à la
    casse). Migre automatiquement l'ancien format (liste de chaînes)."""
    path = _roster_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []

    by_name = {}
    for item in data:
        if isinstance(item, str):
            name, club = item.strip(), ""
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            club = str(item.get("club", "") or "").strip()
        else:
            continue
        if not name:
            continue
        # En cas de doublon dans le fichier, garde le premier club non vide rencontré.
        if name in by_name:
            if not by_name[name]["club"] and club:
                by_name[name]["club"] = club
        else:
            by_name[name] = {"name": name, "club": club}
    return sorted(by_name.values(), key=lambda e: e["name"].lower())

def save_roster_entries(entries):
    path = _roster_path()
    by_name = {}
    for e in entries:
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        club = str(e.get("club", "") or "").strip()
        by_name[name] = {"name": name, "club": club}
    cleaned = sorted(by_name.values(), key=lambda e: e["name"].lower())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def load_roster():
    """Liste des noms seuls (triée), pour les usages qui n'ont besoin que
    du nom (sélection de joueurs, etc.)."""
    return [e["name"] for e in load_roster_entries()]

def save_roster(names):
    """Compatibilité : remplace la liste des noms en conservant le club
    déjà connu de chacun (ceux qui disparaissent de `names` sont retirés)."""
    existing = {e["name"]: e["club"] for e in load_roster_entries()}
    cleaned = sorted({str(n).strip() for n in names if str(n).strip()}, key=str.lower)
    save_roster_entries([{"name": n, "club": existing.get(n, "")} for n in cleaned])

def list_clubs():
    """Clubs connus (non vides), triés, pour proposer un menu déroulant."""
    clubs = {e["club"] for e in load_roster_entries() if e["club"]}
    return sorted(clubs, key=str.lower)

def get_club(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["club"]
    return ""

def set_club(name, club):
    """Définit (ou efface, si club vide) le club d'un joueur du répertoire,
    en le créant si besoin."""
    name = name.strip()
    if not name:
        return
    club = (club or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["club"] = club
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": club})
    save_roster_entries(entries)

def add_to_roster(name, club=None):
    """Ajoute `name` au répertoire s'il n'y est pas déjà. Si `club` est
    fourni (non None), le club est enregistré/mis à jour même si le
    joueur existait déjà ; sinon le club existant est conservé tel quel."""
    name = name.strip()
    if not name:
        return
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            if club is not None and club.strip() and e["club"] != club.strip():
                e["club"] = club.strip()
                save_roster_entries(entries)
            return
    entries.append({"name": name, "club": (club or "").strip()})
    save_roster_entries(entries)

def remove_from_roster(name):
    entries = load_roster_entries()
    filtered = [e for e in entries if e["name"] != name]
    if len(filtered) != len(entries):
        save_roster_entries(filtered)

def rename_in_roster(old_name, new_name):
    """Renomme une entrée en conservant son club. Si `new_name` existe
    déjà, ses deux entrées fusionnent (le club non vide l'emporte)."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name:
        return
    entries = load_roster_entries()
    old_entry = next((e for e in entries if e["name"] == old_name), None)
    old_club = old_entry["club"] if old_entry else ""
    entries = [e for e in entries if e["name"] not in (old_name, new_name)]
    existing_new = next((e for e in load_roster_entries() if e["name"] == new_name), None)
    club = old_club or (existing_new["club"] if existing_new else "")
    entries.append({"name": new_name, "club": club})
    save_roster_entries(entries)
