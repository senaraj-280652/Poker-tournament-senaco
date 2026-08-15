# -*- coding: utf-8 -*-
"""Verrou anti-copie du logiciel : licence hors-ligne, liée à la machine.

Principe
--------
Une fois compilé en exécutable (voir windows/build.ps1 ou
macos/build_dmg.sh), le logiciel exige une licence valide pour démarrer
sur un poste qui n'a jamais été activé. La licence est un code délivré
manuellement par l'éditeur (vous, via generate_license.py) à partir de
l'identifiant de la machine du club, saisi une seule fois par
l'utilisateur puis mémorisé dans
~/.poker_tournament/license.json — aucune connexion Internet requise,
ni au moment de l'activation ni ensuite.

Important — secret et dépôt public
-----------------------------------
La clé secrète servant à signer/vérifier les licences ne se trouve PAS
dans ce fichier ni ailleurs dans le dépôt (celui-ci est public sur
GitHub : tout secret qui y serait commité serait lisible par
n'importe qui, et permettrait de fabriquer de fausses licences).
Elle est injectée uniquement au moment de la compilation, dans un
fichier local `_license_secret.py` jamais versionné (voir .gitignore) :

    python3 -c "import secrets; print('SECRET = ' + repr(secrets.token_hex(32)))" > _license_secret.py

Générez ce fichier une seule fois, gardez-en une copie de sûreté privée
(hors dépôt Git), et réutilisez-le pour toutes les compilations futures
— en changer invaliderait toutes les licences déjà délivrées. Pour les
builds Windows via GitHub Actions, son contenu est stocké comme secret
du dépôt (voir .github/workflows/build-msi.yml et windows/README.md).

Sans ce fichier (ex. : lancement depuis les sources en développement
avec `python3 main.py`), le logiciel démarre normalement sans jamais
demander de licence — le verrou ne s'applique qu'aux exécutables
compilés destinés à être distribués.
"""
import hashlib
import hmac
import json
import os
import platform
import uuid

try:
    from _license_secret import SECRET
except ImportError:
    SECRET = None

LICENSE_FILE = os.path.join(os.path.expanduser("~"), ".poker_tournament", "license.json")

_CODE_LENGTH = 16  # caractères hexadécimaux (avant mise en forme avec tirets)


def format_code(raw_hex):
    """Découpe un code en blocs de 4 caractères, plus lisible/saisissable :
    'A1B2C3D4E5F60789' -> 'A1B2-C3D4-E5F6-0789'."""
    raw_hex = raw_hex.upper()
    return "-".join(raw_hex[i:i + 4] for i in range(0, len(raw_hex), 4))


def _strip(code):
    return (code or "").replace("-", "").replace(" ", "").strip().upper()


def raw_machine_id():
    """Identifiant (approximatif) de cette machine, hexadécimal, sans
    tirets. Basé sur l'adresse MAC et le nom de la machine : stable d'un
    lancement à l'autre sur un même poste, différent d'un poste à
    l'autre. Ce n'est pas infalsifiable (un utilisateur averti peut le
    reproduire), mais suffit à dissuader la copie ordinaire du logiciel
    d'un poste à un autre."""
    raw = f"{uuid.getnode()}-{platform.node()}-{platform.system()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_CODE_LENGTH].upper()


def machine_id_display():
    """Identifiant machine mis en forme, à communiquer à l'éditeur pour
    obtenir une clé de licence."""
    return format_code(raw_machine_id())


def compute_key(mach_id, club_name, secret=None):
    """Calcule la clé de licence attendue pour (identifiant machine, nom
    de club) donnés. `mach_id` peut être fourni avec ou sans tirets."""
    secret = secret or SECRET
    if not secret:
        raise RuntimeError(
            "Aucune clé secrète disponible (_license_secret.py absent) : "
            "impossible de générer ou vérifier une licence dans cet environnement."
        )
    mach_id = _strip(mach_id)
    club_key = (club_name or "").strip().upper()
    msg = f"{mach_id}|{club_key}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return format_code(digest[:_CODE_LENGTH])


def check_key(club_name, key, mach_id=None):
    """Vrai si `key` est la licence valide pour ce club sur cette machine."""
    mach_id = mach_id or raw_machine_id()
    try:
        expected = compute_key(mach_id, club_name)
    except RuntimeError:
        return False
    return hmac.compare_digest(_strip(expected), _strip(key))


def is_licensed():
    """Vrai si le logiciel peut démarrer sans (re)demander d'activation :
    soit aucun secret n'est présent (build de développement, non
    distribuée), soit une licence valide pour CETTE machine est déjà
    enregistrée localement."""
    if SECRET is None:
        return True
    if not os.path.exists(LICENSE_FILE):
        return False
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return False
    if _strip(data.get("machine_id", "")) != raw_machine_id():
        return False
    return check_key(data.get("club_name", ""), data.get("key", ""))


def save_license(club_name, key):
    os.makedirs(os.path.dirname(LICENSE_FILE), exist_ok=True)
    with open(LICENSE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "machine_id": raw_machine_id(),
                "club_name": club_name.strip(),
                "key": _strip(key),
            },
            f, ensure_ascii=False, indent=2,
        )


def license_info():
    """Infos d'activation à afficher (menu Aide > À propos), ou None si
    non applicable (build de développement) / non activé."""
    if SECRET is None:
        return None
    if not os.path.exists(LICENSE_FILE):
        return None
    try:
        with open(LICENSE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return {
        "club_name": data.get("club_name", ""),
        "machine_id": format_code(_strip(data.get("machine_id", ""))),
    }
