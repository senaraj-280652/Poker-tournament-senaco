# -*- coding: utf-8 -*-
"""
Modèles de réglages de tournoi réutilisables (buy-in, tapis de départ,
structure de blindes de départ, primes, signal de mouvements...),
indépendants de tout tournoi précis. Permet d'enregistrer une
configuration complète une fois ("Enregistrer Paramètres sous..." dans
l'onglet Paramètres) et de la réappliquer ensuite à n'importe quel autre
tournoi/Sit & Go ("Récupérer Paramètres...").

Stocké dans ~/.poker_tournament/settings_templates/<nom>.json (un
fichier par modèle), même principe que blind_templates.py / roster.py.
"""
import json
import os
import re

# Le nom du tournoi est volontairement exclu de tout modèle : chaque
# tournoi doit garder le sien, ce n'est pas un réglage réutilisable
# (même principe que tournament_prefs.py pour les derniers réglages).
EXCLUDED_KEYS = {"tournament_name"}


def _templates_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament", "settings_templates")
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


def save_template(name, values):
    """Enregistre `values` (dict de réglages, ex : App.settings_vars
    résolus en chaînes) sous le nom `name`, en excluant EXCLUDED_KEYS.
    Écrase silencieusement un modèle existant du même nom (l'appelant
    doit demander confirmation en amont si besoin)."""
    filtered = {k: v for k, v in values.items() if k not in EXCLUDED_KEYS}
    path = _template_path(name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"name": name, "values": filtered}, f, ensure_ascii=False, indent=2)
    return path


def load_template(name):
    """Dict de réglages enregistré sous `name`, ou None si
    introuvable/illisible."""
    path = _template_path(name)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        values = data.get("values")
        return values if isinstance(values, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def delete_template(name):
    path = _template_path(name)
    if os.path.exists(path):
        os.remove(path)
