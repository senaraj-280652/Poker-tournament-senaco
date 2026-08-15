# -*- coding: utf-8 -*-
"""
Modèles de jeux de jetons réutilisables, indépendants de tout tournoi
(contrairement aux jetons d'un tournoi, qui vivent dans son fichier
.tournoi). Permet d'enregistrer un jeu de jetons une fois ("Enregistrer
Jetons sous...") et de le réappliquer ensuite à n'importe quel autre
tournoi/Sit & Go ("Récupérer Jetons...").

Stocké dans ~/.poker_tournament/chip_templates/<nom>.json (un fichier
par modèle), même principe que blind_templates.py / settings_templates.py.
Chaque fichier contient la liste de dénominations telle qu'attendue par
App._persist_chip_denominations (liste de dicts {name, color, value,
count}).
"""
import json
import os
import re


def _templates_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament", "chip_templates")
    os.makedirs(d, exist_ok=True)
    return d


def _safe_filename(name):
    """Nom de fichier sûr dérivé du nom du modèle (garde lettres/chiffres/
    espaces/tirets, remplace le reste) — le nom affiché lui-même n'est
    jamais tronqué, seul le nom de fichier sur disque l'est éventuellement."""
    safe = re.sub(r"[^\w \-]", "_", name, flags=re.UNICODE).strip() or "modele"
    return safe[:120]


def _template_path(name):
    return os.path.join(_templates_dir(), f"{_safe_filename(name)}.json")


def list_templates():
    """Noms des modèles enregistrés, triés (insensible à la casse)."""
    names = []
    for fname in os.listdir(_templates_dir()):
        if not fname.endswith(".json"):
            continue
        path = os.path.join(_templates_dir(), fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            names.append(data.get("name") or fname[:-5])
        except (json.JSONDecodeError, OSError):
            continue
    return sorted(names, key=str.casefold)


def save_template(name, denominations):
    """Enregistre `denominations` (liste de dicts {name, color, value,
    count}) sous le nom `name`. Écrase silencieusement un modèle existant
    du même nom (l'appelant doit demander confirmation en amont si besoin)."""
    path = _template_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "denominations": denominations}, f, ensure_ascii=False, indent=2)
    return path


def load_template(name):
    """Liste de dénominations enregistrée sous `name`, ou None si
    introuvable/illisible."""
    path = _template_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        denominations = data.get("denominations")
        return denominations if isinstance(denominations, list) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_template(name):
    path = _template_path(name)
    if os.path.exists(path):
        os.remove(path)
