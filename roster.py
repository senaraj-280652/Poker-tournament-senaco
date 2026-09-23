# -*- coding: utf-8 -*-
"""
Répertoire de joueurs habituels, indépendant des tournois eux-mêmes.
Stocké dans le dossier personnel de l'utilisateur (~/.poker_tournament/roster.json)
afin d'être disponible quel que soit l'endroit où l'application est lancée
et quel que soit le fichier .tournoi ouvert.

Chaque entrée est un dict {"name", "club", "group", "phone", "mail"} (tous
les champs sauf "name" peuvent être des chaînes vides si inconnus/non
renseignés). Les anciens fichiers roster.json (simple liste de chaînes,
ou dicts {"name", "club"} d'avant le 2026-09-20) sont migrés
automatiquement à la lecture, sans club/groupe/téléphone/mail.

"group" (demande du 2026-09-20, chantier "Sécurisation du Contrôle à
distance") : identifie qui, parmi les personnes du Répertoire, peut être
ADMIN (accès total au Contrôle à distance, peut accorder/retirer des
droits) ou DIRTO (accès limité aux fonctions explicitement accordées par
un ADMIN, pour un tournoi donné) — voir ROSTER_GROUP_ADMIN/
ROSTER_GROUP_DIRTO ci-dessous. Une personne appartient à UN SEUL groupe
à la fois, JAMAIS les deux simultanément (décision explicite de
l'utilisateur) — la chaîne vide "" signifie "non classé" (comportement
de TOUTE fiche existante avant ce chantier, jamais une erreur). Ce champ
ne donne AUCUN droit par lui-même : il identifie seulement qui PEUT être
choisi comme ADMIN/DIRTO ailleurs dans l'application (chantiers
suivants) — la logique d'autorisation elle-même vit ailleurs (base du
tournoi, registre des appareils), jamais ici."""
import json
import os

# Valeurs valides de "group" (voir la docstring du module). Toute autre
# valeur lue depuis le disque (fichier corrompu, future valeur non
# encore supportée, etc.) est silencieusement ramenée à "" par
# _normalize_group ci-dessous — jamais une exception, même principe
# défensif que le reste de ce module vis-à-vis d'un fichier existant.
ROSTER_GROUP_ADMIN = "ADMIN"
ROSTER_GROUP_DIRTO = "DIRTO"
ROSTER_GROUPS = (ROSTER_GROUP_ADMIN, ROSTER_GROUP_DIRTO)

def _roster_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d

def _roster_path():
    return os.path.join(_roster_dir(), "roster.json")

def _normalize_group(value):
    """"" (non classé) si `value` n'est pas exactement l'une des deux
    valeurs valides — jamais une exception, y compris pour une chaîne
    vide, None, un type inattendu, ou une future valeur non encore
    supportée par cette version de l'application."""
    value = str(value or "").strip().upper()
    return value if value in ROSTER_GROUPS else ""

def load_roster_entries():
    """Liste de dicts {"name", "club", "group", "phone", "mail"}, triée
    par nom (insensible à la casse). Migre automatiquement l'ancien
    format (liste de chaînes, ou dicts sans group/phone/mail)."""
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
            name, club, group, phone, mail = item.strip(), "", "", "", ""
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            club = str(item.get("club", "") or "").strip()
            group = _normalize_group(item.get("group", ""))
            phone = str(item.get("phone", "") or "").strip()
            mail = str(item.get("mail", "") or "").strip()
        else:
            continue
        if not name:
            continue
        # En cas de doublon dans le fichier, garde la première valeur
        # non vide rencontrée pour chaque champ (même principe déjà en
        # place pour "club" avant ce chantier).
        if name in by_name:
            existing = by_name[name]
            if not existing["club"] and club:
                existing["club"] = club
            if not existing["group"] and group:
                existing["group"] = group
            if not existing["phone"] and phone:
                existing["phone"] = phone
            if not existing["mail"] and mail:
                existing["mail"] = mail
        else:
            by_name[name] = {
                "name": name, "club": club, "group": group,
                "phone": phone, "mail": mail,
            }
    return sorted(by_name.values(), key=lambda e: e["name"].lower())

def save_roster_entries(entries):
    path = _roster_path()
    by_name = {}
    for e in entries:
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        by_name[name] = {
            "name": name,
            "club": str(e.get("club", "") or "").strip(),
            "group": _normalize_group(e.get("group", "")),
            "phone": str(e.get("phone", "") or "").strip(),
            "mail": str(e.get("mail", "") or "").strip(),
        }
    cleaned = sorted(by_name.values(), key=lambda e: e["name"].lower())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def load_roster():
    """Liste des noms seuls (triée), pour les usages qui n'ont besoin que
    du nom (sélection de joueurs, etc.)."""
    return [e["name"] for e in load_roster_entries()]

def save_roster(names):
    """Compatibilité : remplace la liste des noms en conservant le club/
    groupe/téléphone/mail déjà connus de chacun (ceux qui disparaissent
    de `names` sont retirés, avec toutes leurs autres informations)."""
    existing = {e["name"]: e for e in load_roster_entries()}
    cleaned = sorted({str(n).strip() for n in names if str(n).strip()}, key=str.lower)
    save_roster_entries([
        existing.get(n, {"name": n, "club": "", "group": "", "phone": "", "mail": ""})
        for n in cleaned
    ])

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
    entries.append({"name": name, "club": club, "group": "", "phone": "", "mail": ""})
    save_roster_entries(entries)

def get_group(name):
    """"ADMIN", "DIRTO" ou "" (non classé, y compris pour un nom absent
    du Répertoire)."""
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["group"]
    return ""

def set_group(name, group):
    """Définit (ou efface, si `group` invalide/vide) le groupe ADMIN/
    DIRTO d'une personne du répertoire, en la créant si besoin. Une
    valeur invalide (ni "ADMIN" ni "DIRTO") est silencieusement ramenée
    à "" plutôt que de lever une exception — voir _normalize_group."""
    name = name.strip()
    if not name:
        return
    group = _normalize_group(group)
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["group"] = group
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": group, "phone": "", "mail": ""})
    save_roster_entries(entries)

def get_phone(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["phone"]
    return ""

def set_phone(name, phone):
    """Définit (ou efface, si phone vide) le téléphone d'une personne du
    répertoire, en la créant si besoin."""
    name = name.strip()
    if not name:
        return
    phone = (phone or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["phone"] = phone
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": "", "phone": phone, "mail": ""})
    save_roster_entries(entries)

def get_mail(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["mail"]
    return ""

def set_mail(name, mail):
    """Définit (ou efface, si mail vide) le mail d'une personne du
    répertoire, en la créant si besoin."""
    name = name.strip()
    if not name:
        return
    mail = (mail or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["mail"] = mail
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": "", "phone": "", "mail": mail})
    save_roster_entries(entries)

def list_by_group(group):
    """Entrées complètes ({"name", "club", "group", "phone", "mail"})
    dont le groupe est exactement `group` ("ADMIN" ou "DIRTO"), triées
    par nom — pour peupler les futurs sélecteurs "AUTORISÉ PAR"/
    "UTILISATEUR AUTORISÉ" du Contrôle à distance (chantiers suivants).
    Liste vide si `group` n'est pas une valeur valide (jamais une
    exception)."""
    group = _normalize_group(group)
    if not group:
        return []
    return [e for e in load_roster_entries() if e["group"] == group]

def add_to_roster(name, club=None):
    """Ajoute `name` au répertoire s'il n'y est pas déjà. Si `club` est
    fourni (non None), le club est enregistré/mis à jour même si le
    joueur existait déjà ; sinon le club existant est conservé tel quel.
    Ne touche jamais group/phone/mail (voir set_group/set_phone/
    set_mail dédiés) — une personne nouvellement créée ici démarre donc
    toujours "non classée", exactement comme une ancienne fiche."""
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
    entries.append({"name": name, "club": (club or "").strip(), "group": "", "phone": "", "mail": ""})
    save_roster_entries(entries)

def remove_from_roster(name):
    entries = load_roster_entries()
    filtered = [e for e in entries if e["name"] != name]
    if len(filtered) != len(entries):
        save_roster_entries(filtered)

def rename_in_roster(old_name, new_name):
    """Renomme une entrée en conservant son club/groupe/téléphone/mail.
    Si `new_name` existe déjà, ses deux entrées fusionnent (pour chaque
    champ, la valeur non vide de l'ancien nom l'emporte, sinon celle du
    nom cible est conservée)."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name:
        return
    entries = load_roster_entries()
    old_entry = next((e for e in entries if e["name"] == old_name), None)
    old_entry = old_entry or {"club": "", "group": "", "phone": "", "mail": ""}
    entries = [e for e in entries if e["name"] not in (old_name, new_name)]
    existing_new = next((e for e in load_roster_entries() if e["name"] == new_name), None)
    existing_new = existing_new or {"club": "", "group": "", "phone": "", "mail": ""}
    merged = {
        "name": new_name,
        "club": old_entry["club"] or existing_new["club"],
        "group": old_entry["group"] or existing_new["group"],
        "phone": old_entry["phone"] or existing_new["phone"],
        "mail": old_entry["mail"] or existing_new["mail"],
    }
    entries.append(merged)
    save_roster_entries(entries)
