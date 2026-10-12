# -*- coding: utf-8 -*-
"""
Historique des sauvegardes RÉUSSIES (demande du 2026-10-07, garde-fou
avant le bouton "Purger Données" de l'onglet LOG) — stocké dans
~/.poker_tournament/backup_history.json, totalement indépendant de tout
fichier .tournoi et du LOG utilisateur (actions_log.sqlite3, action_log.py).

Principes de sécurité (demande explicite) :
- Une entrée n'est ajoutée ICI que pour une sauvegarde RÉELLEMENT
  terminée avec succès (voir main.py: backup_now, qui appelle
  record_backup() seulement après que backup_restore.create_backup() ait
  renvoyé sans lever BackupError) — jamais pour une sauvegarde annulée ou
  échouée, jamais pour les sauvegardes de sécurité internes de
  backup_restore.restore_backup (celles-ci ne sont PAS enregistrées ici :
  seul backup_now, déclenché par l'utilisateur, appelle record_backup()).
- Écriture ATOMIQUE (fichier temporaire + os.replace, même précaution que
  player_photos.py) : ce fichier protège une décision de sécurité (purge
  de données), jamais une simple préférence d'interface — jamais de
  risque de fichier tronqué en cas d'interruption au mauvais moment.
- Ce fichier est volontairement INCLUS dans les sauvegardes futures (il
  vit dans ~/.poker_tournament, déjà copié intégralement par
  backup_restore.create_backup, sans exception), mais JAMAIS écrasé par
  une RESTAURATION (voir backup_restore.RESTORE_EXCLUDED_DATA_FILENAMES) :
  l'historique réel des sauvegardes faites sur CETTE machine doit
  survivre à une restauration.
- La présence d'une sauvegarde ICI ne prouve JAMAIS, à elle seule,
  qu'elle est toujours utilisable pour autoriser une purge : voir
  data_purge.py, qui revérifie systématiquement l'accès réel au dossier
  de sauvegarde (existence, manifeste valide, fichier présent, hash si
  disponible) au moment même de la purge — jamais une confiance aveugle
  dans cet historique déclaratif.
"""
import json
import os
import platform
import uuid

HISTORY_FILENAME = "backup_history.json"

# Valeurs possibles de "destination_kind" (voir _detect_destination_kind) —
# PUREMENT informatif pour l'affichage ("nature de la destination",
# demande explicite) : JAMAIS la base d'une décision de sécurité, qui
# repose entièrement sur la vérification réelle faite par data_purge.py.
DESTINATION_REMOVABLE = "removable"
DESTINATION_FIXED = "fixed"
DESTINATION_UNKNOWN = "unknown"


def _history_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d


def _history_path():
    return os.path.join(_history_dir(), HISTORY_FILENAME)


def _load_all():
    path = _history_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _save_all(entries):
    """Écriture ATOMIQUE : le JSON complet est d'abord écrit dans un
    fichier temporaire du MÊME dossier (indispensable pour qu'os.replace
    reste atomique — un remplacement inter-systèmes de fichiers ne l'est
    pas), puis os.replace() bascule d'un coup — même mécanisme que
    player_photos._save_index, choisi délibérément ici plutôt que
    l'écriture directe plus simple d'export_prefs.py : ce fichier protège
    une décision de sécurité, pas une préférence d'interface anodine."""
    path = _history_path()
    tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def _detect_destination_kind(path):
    """Best-effort, purement informatif (voir la docstring du module) —
    jamais une garantie. macOS : un chemin sous /Volumes/ est traité
    comme externe (heuristique simple, peut inclure un disque externe
    non-USB, ou un partage réseau monté — assumé et documenté comme tel,
    jamais affiné par un appel à diskutil pour rester simple et robuste).
    Windows : GetDriveTypeW (API standard déjà dans ctypes, aucune
    dépendance nouvelle) — DRIVE_REMOVABLE (2) = externe, DRIVE_FIXED (3)
    = interne, toute autre valeur = indéterminé. Toute erreur -> inconnu,
    jamais une exception propagée (purement informatif, ne doit jamais
    faire échouer une sauvegarde par ailleurs réussie)."""
    try:
        system = platform.system()
        if system == "Darwin":
            abs_path = os.path.abspath(path)
            return DESTINATION_REMOVABLE if abs_path.startswith("/Volumes/") else DESTINATION_FIXED
        if system == "Windows":
            import ctypes
            drive = os.path.splitdrive(os.path.abspath(path))[0]
            if not drive:
                return DESTINATION_UNKNOWN
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(drive + "\\")
            if drive_type == 2:
                return DESTINATION_REMOVABLE
            if drive_type == 3:
                return DESTINATION_FIXED
            return DESTINATION_UNKNOWN
    except Exception:
        return DESTINATION_UNKNOWN
    return DESTINATION_UNKNOWN


def record_backup(backup_dir, manifest):
    """Ajoute une entrée à l'historique — appelée UNIQUEMENT après une
    sauvegarde réellement terminée avec succès (voir la docstring du
    module). `manifest` : celui renvoyé par backup_restore.create_backup
    et déjà écrit sur le disque de destination — jamais reconstruit
    indépendamment ici, pour garantir que cet historique reflète
    exactement ce qui a été réellement sauvegardé (jamais une
    source de vérité séparée/potentiellement divergente)."""
    entries = _load_all()
    entries.append({
        "created_at": manifest.get("created_at"),
        "backup_dir": backup_dir,
        "tournament_files": list(manifest.get("tournament_files", [])),
        "poker_tournament_data_included": bool(manifest.get("poker_tournament_data_included", False)),
        "destination_kind": _detect_destination_kind(backup_dir),
    })
    _save_all(entries)


def load_history():
    """Liste des sauvegardes enregistrées, de la plus ancienne à la plus
    récente — PUREMENT déclaratif : ne vérifie JAMAIS que le dossier
    existe encore, qu'il est lisible, ni qu'il correspond toujours au
    contenu annoncé. Voir data_purge.py pour la vérification réelle,
    effectuée séparément et systématiquement au moment de la purge."""
    return _load_all()
