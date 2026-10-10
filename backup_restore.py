# -*- coding: utf-8 -*-
"""Sauvegarde/restauration complète des données de l'utilisateur (fichiers
.tournoi + ~/.poker_tournament) sur une clé USB ou tout dossier de
destination — voir la zone "Sauvegarde des données" du Menu principal
(main.py: _choose_tournament_file).

Principes de sécurité (demande explicite du 2026-09-07) :
- JAMAIS de déplacement ni de suppression des données d'ORIGINE :
  uniquement des copies (shutil.copy2/copytree maison), jamais un
  DELETE ni un rmtree sur une source.
- Une restauration commence TOUJOURS par sauvegarder l'état actuel
  (voir restore_backup) avant d'écraser quoi que ce soit — jamais
  d'écrasement silencieux.
- Une restauration est REFUSÉE si un tournoi est actuellement ouvert
  (ici ou dans une autre fenêtre/process — voir open_windows.
  list_open_paths, déjà utilisé ailleurs dans l'appli pour connaître
  les tournois réellement vivants) : écraser un fichier .tournoi
  pendant qu'une connexion SQLite y est encore ouverte risquerait un
  échec ou une incohérence. Aucune copie ni sauvegarde de sécurité
  n'est tentée tant que cette condition n'est pas remplie. La
  sauvegarde (jamais la restauration), elle, reste possible même
  tournoi(s) ouvert(s) : elle ne fait que LIRE les fichiers sources,
  jamais les modifier.
- La restauration copie fichier par fichier dans le dossier cible
  actuel (jamais un rmtree préalable de la cible) : un échec en cours
  de route laisse un mélange d'anciens/nouveaux fichiers, jamais un
  dossier vidé.
- Toute fonction ici prend ses dossiers sources en PARAMÈTRE (jamais
  résolus en dur à l'intérieur des fonctions de copie elles-mêmes) —
  seules default_tournament_folder()/default_poker_data_dir() lisent
  la config réelle ; le reste est testable avec des dossiers
  synthétiques sans toucher aux vraies données.
- La SAUVEGARDE copie l'intégralité de ~/.poker_tournament, à une seule
  exception près : PRE_RESTORE_BACKUPS_DIRNAME ("pre_restore_backups",
  voir ce commentaire — correctif du 2026-10-09, recopie infinie d'une
  sauvegarde de sécurité dans elle-même) n'est jamais inclus dans
  AUCUNE sauvegarde, manuelle ou automatique. La RESTAURATION, elle,
  n'y réinjecte jamais open_windows.json ni phone_selected_pid.json
  (voir RESTORE_EXCLUDED_DATA_FILENAMES) : ce sont des états de session
  temporaires liés aux processus en cours sur LA machine d'origine au
  moment de la sauvegarde, sans rapport avec la machine/le moment de la
  restauration — les réinjecter pourrait laisser croire à tort qu'un
  tournoi est ouvert (ou par qui). license.json n'est PAS concerné par
  cette exclusion : il reste sauvegardé et restauré normalement, sans
  aucune modification du système de licence lui-même (voir license.py,
  inchangé)."""
import json
import os
import platform
import shutil
import sys
from datetime import datetime

import open_windows
import tournament_prefs
import version

BACKUP_FOLDER_PREFIX = "PokerTournament_Backup_"
MANIFEST_FILENAME = "backup_manifest.json"
TOURNAMENTS_SUBDIR = "tournois"
POKER_DATA_SUBDIR = "poker_tournament_data"

# Sous-dossier des sauvegardes de sécurité PRÉ-RESTAURATION, créé par
# restore_backup À L'INTÉRIEUR MÊME de poker_data_dir (voir
# restore_backup, pre_restore_root). Correctif du 2026-10-09 ("WinError
# 206 : nom de fichier... trop long" sur un poste réel) : une sauvegarde
# de poker_data_dir qui ne l'exclut pas se recopie indéfiniment dans
# elle-même (sa propre destination vit À L'INTÉRIEUR de sa propre
# source), et ferait grossir chaque sauvegarde suivante de TOUTES les
# précédentes déjà présentes dans ce dossier. EXCLU de TOUTE sauvegarde
# de poker_data_dir, sans exception — manuelle (Menu principal) ou
# interne (sécurité pré-restauration elle-même) — voir create_backup/
# restore_backup, qui passent tous deux EXCLUDED_POKER_DATA_DIR_NAMES
# ci-dessous à _copy_tree_file_by_file/_verify_copied_tree. Les
# sauvegardes de sécurité déjà présentes sur le disque ne sont jamais
# ni supprimées ni modifiées par cette exclusion : elle ne change que
# ce qui est COPIÉ dans les FUTURES sauvegardes, jamais ce qui existe
# déjà.
PRE_RESTORE_BACKUPS_DIRNAME = "pre_restore_backups"
EXCLUDED_POKER_DATA_DIR_NAMES = frozenset({PRE_RESTORE_BACKUPS_DIRNAME})

# Fichiers d'état de session temporaires (voir open_windows.py) : jamais
# réinjectés lors d'une RESTAURATION (demande du 2026-09-07) — ils restent
# en revanche sauvegardés normalement, comme tout le reste de
# ~/.poker_tournament (aucune exception côté sauvegarde). license.json
# n'est volontairement PAS dans cette liste : il reste restauré comme
# avant.
RESTORE_EXCLUDED_DATA_FILENAMES = frozenset({
    "open_windows.json",
    "phone_selected_pid.json",
})

_MANIFEST_REQUIRED_KEYS = (
    "created_at", "app_version", "tournament_files", "poker_tournament_data_included",
)


class BackupError(Exception):
    """Erreur de sauvegarde/restauration — le message est déjà rédigé
    pour être affiché tel quel à l'utilisateur (messagebox)."""


def default_poker_data_dir():
    """~/.poker_tournament (ou %USERPROFILE%\\.poker_tournament sous
    Windows, os.path.expanduser gère les deux) — même chemin que TOUS
    les autres modules de préférences (export_prefs.py, roster.py,
    open_windows.py, license.py...)."""
    return os.path.join(os.path.expanduser("~"), ".poker_tournament")


def default_tournament_folder():
    """Dossier de tournoi par défaut configuré dans l'application (voir
    Paramètres > "Dossier par défaut" — tournament_prefs.
    tournament_day_folder, même source que main.py:
    tournament_day_folder_proposal), ou le repli standard club sous
    Windows (C:\\poker\\senaco) si rien n'est configuré. None ailleurs
    (Mac de développement/test) si rien n'est configuré non plus — à
    l'appelant de gérer ce cas (aucun fichier .tournoi à sauvegarder,
    pas une erreur en soi : les données ~/.poker_tournament restent
    sauvegardées quand même)."""
    base = (tournament_prefs.load_last_settings().get("tournament_day_folder", "") or "").strip()
    if base:
        return base
    if sys.platform.startswith("win"):
        return r"C:\poker\senaco"
    return None


def find_tournament_files(base_dir):
    """Chemins ABSOLUS de tous les fichiers .tournoi sous `base_dir`,
    recherche récursive (les tournois du club sont organisés en
    sous-dossiers par jour de semaine, voir main.py:
    tournament_day_folder_proposal) — liste vide si `base_dir` est
    None/inexistant, jamais une erreur."""
    found = []
    if not base_dir or not os.path.isdir(base_dir):
        return found
    for root, _dirs, files in os.walk(base_dir):
        for name in files:
            if name.lower().endswith(".tournoi"):
                found.append(os.path.join(root, name))
    return found


def _copy_tree_file_by_file(src_dir, dst_dir, exclude_relpaths=None, exclude_dir_names=None):
    """Copie récursive fichier par fichier de `src_dir` vers `dst_dir` —
    jamais un rmtree/DELETE préalable sur `dst_dir` (qui peut déjà
    contenir des données, voir restore_backup) : chaque fichier existant
    dans `dst_dir` mais absent de `src_dir` est laissé tel quel, chaque
    fichier présent dans les deux est écrasé (shutil.copy2, qui
    remplace le contenu sans jamais passer par une suppression). Crée
    les sous-dossiers nécessaires. Renvoie le nombre de fichiers copiés.

    `exclude_relpaths` : ensemble de chemins relatifs (séparateur "/",
    ex. {"open_windows.json"}) à ne JAMAIS copier — utilisé uniquement
    par restore_backup (voir RESTORE_EXCLUDED_DATA_FILENAMES).

    `exclude_dir_names` (correctif du 2026-10-09, "WinError 206 : nom
    de fichier... trop long" — pre_restore_backups, À L'INTÉRIEUR de
    poker_data_dir, se recopiait indéfiniment dans lui-même) : ensemble
    de NOMS de sous-dossiers (pas des chemins, juste le nom — ex.
    {"pre_restore_backups"}) à ne JAMAIS descendre, à QUELQUE
    PROFONDEUR QUE CE SOIT sous `src_dir`. Élagué EN PLACE sur `dirs`
    (`dirs[:] = ...`), seule façon reconnue par os.walk pour l'empêcher
    de descendre dans un sous-dossier — jamais un simple filtre sur
    `files`, qui laisserait `os.walk` y descendre quand même."""
    exclude_relpaths = exclude_relpaths or frozenset()
    exclude_dir_names = exclude_dir_names or frozenset()
    count = 0
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dir_names]
        rel = os.path.relpath(root, src_dir)
        target_root = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        os.makedirs(target_root, exist_ok=True)
        for name in files:
            rel_path = name if rel == "." else f"{rel.replace(os.sep, '/')}/{name}"
            if rel_path in exclude_relpaths:
                continue
            shutil.copy2(os.path.join(root, name), os.path.join(target_root, name))
            count += 1
    return count


def _verify_copied_tree(src_dir, dst_dir, exclude_dir_names=None):
    """Vérifie que chaque fichier de `src_dir` a bien son pendant dans
    `dst_dir`, de même taille. Lève BackupError (message prêt pour
    l'utilisateur) au premier écart trouvé.

    `exclude_dir_names` : MÊME ensemble que celui passé à
    _copy_tree_file_by_file pour CETTE copie — indispensable pour
    rester symétrique : un sous-dossier délibérément jamais copié (voir
    ci-dessus) ne doit jamais être signalé comme "manquant" ici."""
    exclude_dir_names = exclude_dir_names or frozenset()
    for root, dirs, files in os.walk(src_dir):
        dirs[:] = [d for d in dirs if d not in exclude_dir_names]
        rel = os.path.relpath(root, src_dir)
        target_root = dst_dir if rel == "." else os.path.join(dst_dir, rel)
        for name in files:
            src_path = os.path.join(root, name)
            dst_path = os.path.join(target_root, name)
            if not os.path.isfile(dst_path):
                raise BackupError(f"Fichier manquant après la copie : {name}")
            if os.path.getsize(dst_path) != os.path.getsize(src_path):
                raise BackupError(f"Taille différente après la copie : {name}")


def create_backup(destination_parent_dir, tournament_folder=None, poker_data_dir=None,
                   computer_name=None):
    """Crée une sauvegarde complète dans un nouveau sous-dossier horodaté
    de `destination_parent_dir` (la clé USB/dossier choisi par
    l'utilisateur, ou — voir restore_backup — le dossier fixe des
    sauvegardes de sécurité pré-restauration). `tournament_folder`/
    `poker_data_dir`/`computer_name` : injectables pour les tests
    (dossiers synthétiques) — None utilise les vrais réglages de
    l'application (default_tournament_folder()/default_poker_data_dir()/
    platform.node()).

    Ne modifie/déplace/supprime JAMAIS les données d'origine : uniquement
    des copies. Renvoie un dict {"backup_dir", "tournament_file_count",
    "poker_tournament_data_included"}. Lève BackupError (message déjà
    prêt pour l'utilisateur) en cas d'échec — le dossier de sauvegarde
    partiel est alors supprimé au mieux (jamais les fichiers d'origine,
    qui ne sont de toute façon jamais que LUS ici, jamais écrits)."""
    if not destination_parent_dir or not os.path.isdir(destination_parent_dir):
        raise BackupError(
            "Le dossier de destination choisi n'existe pas ou n'est plus "
            f"accessible :\n{destination_parent_dir}"
        )

    tournament_folder = default_tournament_folder() if tournament_folder is None else tournament_folder
    poker_data_dir = default_poker_data_dir() if poker_data_dir is None else poker_data_dir
    computer_name = platform.node() if computer_name is None else computer_name

    timestamp = datetime.now()
    backup_name = f"{BACKUP_FOLDER_PREFIX}{timestamp:%Y-%m-%d_%H%M}"
    backup_dir = os.path.join(destination_parent_dir, backup_name)
    # Ne devrait arriver qu'en cas de deux sauvegardes dans la même
    # minute (ou en test) : jamais d'écrasement d'une sauvegarde
    # existante, un suffixe numérique est ajouté à la place.
    suffix = 1
    while os.path.exists(backup_dir):
        suffix += 1
        backup_dir = os.path.join(destination_parent_dir, f"{backup_name}-{suffix}")

    try:
        os.makedirs(backup_dir)

        tournament_files = find_tournament_files(tournament_folder)
        relative_tournament_files = []
        # Paires (src, dst) réellement copiées — revérifiées explicitement
        # plus bas, PAIRE PAR PAIRE (correctif du 2026-10-09, incident réel
        # "Fichier manquant après la copie : primes_To170926.pdf") : jamais
        # un parcours libre de tournament_folder (_verify_copied_tree),
        # qui peut contenir des fichiers ANNEXES (PDF d'export, CSV,
        # fichiers techniques OS) jamais copiés — un tel parcours les
        # signalerait à tort comme "manquants".
        tournament_copy_pairs = []
        if tournament_files:
            tournaments_dst = os.path.join(backup_dir, TOURNAMENTS_SUBDIR)
            for src_path in tournament_files:
                rel = os.path.relpath(src_path, tournament_folder)
                dst_path = os.path.join(tournaments_dst, rel)
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                relative_tournament_files.append(rel.replace(os.sep, "/"))
                tournament_copy_pairs.append((src_path, dst_path))

        poker_data_included = bool(poker_data_dir and os.path.isdir(poker_data_dir))
        if poker_data_included:
            _copy_tree_file_by_file(
                poker_data_dir, os.path.join(backup_dir, POKER_DATA_SUBDIR),
                exclude_dir_names=EXCLUDED_POKER_DATA_DIR_NAMES,
            )

        manifest = {
            "created_at": timestamp.isoformat(timespec="seconds"),
            "app_version": version.APP_VERSION,
            "computer_name": computer_name,
            "source_tournament_folder": tournament_folder,
            "source_poker_tournament_data": poker_data_dir,
            "tournament_files": relative_tournament_files,
            "poker_tournament_data_included": poker_data_included,
        }
        with open(os.path.join(backup_dir, MANIFEST_FILENAME), "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

        # Vérification post-copie (demande explicite) : compare chaque
        # fichier RÉELLEMENT copié (tournament_copy_pairs) à sa copie —
        # jamais un parcours libre du dossier source (voir le
        # commentaire sur tournament_copy_pairs plus haut).
        for src_path, dst_path in tournament_copy_pairs:
            if not os.path.isfile(dst_path):
                raise BackupError(f"Fichier manquant après la copie : {os.path.basename(src_path)}")
            if os.path.getsize(dst_path) != os.path.getsize(src_path):
                raise BackupError(f"Taille différente après la copie : {os.path.basename(src_path)}")
        if poker_data_included:
            _verify_copied_tree(
                poker_data_dir, os.path.join(backup_dir, POKER_DATA_SUBDIR),
                exclude_dir_names=EXCLUDED_POKER_DATA_DIR_NAMES,
            )
    except Exception as e:
        try:
            shutil.rmtree(backup_dir, ignore_errors=True)
        except OSError:
            pass
        raise BackupError(
            f"La sauvegarde a échoué :\n{e}\n\nAucune donnée d'origine n'a été modifiée."
        ) from e

    return {
        "backup_dir": backup_dir,
        "tournament_file_count": len(relative_tournament_files),
        "poker_tournament_data_included": poker_data_included,
    }


def validate_backup_folder(backup_dir):
    """Lit et renvoie le manifeste de `backup_dir`. Lève BackupError
    (message prêt pour l'utilisateur) si ce dossier ne contient pas un
    manifeste de sauvegarde lisible et complet — jamais une simple
    vérification du NOM du dossier, qui pourrait avoir été renommé."""
    manifest_path = os.path.join(backup_dir, MANIFEST_FILENAME)
    if not os.path.isfile(manifest_path):
        raise BackupError(
            "Ce dossier ne semble pas être une sauvegarde valide de Poker "
            f"Tournament (fichier manifeste introuvable) :\n{backup_dir}"
        )
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise BackupError(
            "Ce dossier ne semble pas être une sauvegarde valide de Poker "
            f"Tournament (manifeste illisible) :\n{e}"
        ) from e
    if not isinstance(manifest, dict) or not all(k in manifest for k in _MANIFEST_REQUIRED_KEYS):
        raise BackupError(
            "Le manifeste de cette sauvegarde est incomplet ou corrompu :\n"
            f"{manifest_path}"
        )
    return manifest


# Clé "tournament_day_folder" de last_settings.json (seule clé
# machine-spécifique de ce fichier — voir tournament_prefs.
# PERSISTED_KEYS, qui ne l'exporte pas comme constante publique) —
# voir _read_tournament_day_folder/_restore_tournament_day_folder
# ci-dessous : ces deux fonctions opèrent TOUJOURS sur un
# `poker_data_dir` fourni en paramètre, jamais via tournament_prefs.
# load_last_settings()/save_last_settings(), qui résolvent TOUJOURS le
# vrai ~/.poker_tournament — sans rapport avec le dossier (réel ou
# synthétique, pour les tests) réellement manipulé ici.
_LAST_SETTINGS_FILENAME = "last_settings.json"


def _read_tournament_day_folder(last_settings_path):
    """Lit UNIQUEMENT la clé "tournament_day_folder" du fichier
    `last_settings_path` — jamais via tournament_prefs (voir le
    commentaire ci-dessus). None si le fichier est absent, illisible,
    ou sans cette clé — jamais une exception."""
    if not os.path.isfile(last_settings_path):
        return None
    try:
        with open(last_settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("tournament_day_folder")


def _restore_tournament_day_folder(last_settings_path, value):
    """Réécrit UNIQUEMENT la clé "tournament_day_folder" dans
    `last_settings_path` (déjà restauré par la copie intégrale de
    poker_data_dir au moment où cette fonction est appelée) — fusionne
    avec le contenu déjà présent (ne touche à aucune autre préférence),
    ou retire la clé si `value` est None (aucune valeur locale avant la
    restauration — jamais en introduire une venue de la machine
    source). Jamais une exception : si le fichier restauré est
    illisible, abandonne silencieusement plutôt que de faire échouer
    toute la restauration pour ce seul détail — le reste a déjà été
    restauré avec succès à ce stade.

    N'écrit RIEN si l'état souhaité est déjà celui présent (y compris
    quand `last_settings_path` n'existe pas encore et qu'il n'y a rien
    à y retirer) — sans cette garde, un restore vers un poker_data_dir
    qui n'avait jamais eu de last_settings.json créerait un fichier
    vide pour rien, perturbant tout calcul qui compte les fichiers de
    ce dossier (ex. la non-croissance des sauvegardes de sécurité
    successives)."""
    try:
        with open(last_settings_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, OSError):
        data = {}
    had_key = "tournament_day_folder" in data
    if value is None:
        if not had_key:
            return
        data.pop("tournament_day_folder", None)
    else:
        if had_key and data.get("tournament_day_folder") == value:
            return
        data["tournament_day_folder"] = value
    try:
        with open(last_settings_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def restore_backup(backup_dir, tournament_folder=None, poker_data_dir=None,
                    pre_restore_root=None, computer_name=None, open_tournament_paths=None):
    """Restaure la sauvegarde `backup_dir`. `tournament_folder`/
    `poker_data_dir`/`pre_restore_root`/`computer_name`/
    `open_tournament_paths` : injectables pour les tests ; None utilise
    les vrais réglages de l'application (dont, pour ce dernier,
    open_windows.list_open_paths() — PID morts déjà ignorés par cette
    fonction, voir sa propre docstring).

    Ordre STRICT (demande explicite) :
    0) Refuse tout de suite si au moins un tournoi est actuellement
       ouvert (ici ou ailleurs) — voir open_windows.list_open_paths.
       Aucune copie ni sauvegarde de sécurité n'est tentée dans ce cas.
    1) Valide le manifeste (voir validate_backup_folder) — rien n'est
       encore touché à ce stade.
    1bis) Si cette sauvegarde contient des .tournoi à restaurer, valide
       le DOSSIER DE TOURNOI DE DESTINATION (demande explicite du
       2026-10-10) : doit être le "Dossier par défaut" RÉELLEMENT
       configuré sur CETTE machine (tournament_prefs, jamais le repli
       "C:\\poker\\senaco" de default_tournament_folder(), jamais le
       chemin de la machine SOURCE), existant, et accessible en
       écriture (os.access, SANS créer ni modifier aucun fichier —
       limite documentée dans le code : pas une garantie absolue sur
       tous les systèmes). Refuse avec un message explicite sinon —
       TOUJOURS avant la moindre écriture, y compris la sauvegarde de
       sécurité de l'étape 2.
    2) Crée une sauvegarde de sécurité de l'état ACTUEL (via
       create_backup, même mécanisme, vers `pre_restore_root` —
       ~/.poker_tournament/pre_restore_backups par défaut) — toujours
       AVANT le moindre écrasement. Si cette étape échoue, la
       restauration s'arrête ici : aucune donnée réelle n'a encore été
       touchée.
    3) Restaure les .tournoi et les données ~/.poker_tournament fichier
       par fichier (jamais un rmtree préalable de la cible, voir
       _copy_tree_file_by_file) — un échec en cours de route laisse un
       mélange d'anciens/nouveaux fichiers, jamais un dossier vidé, et
       la sauvegarde de sécurité de l'étape 2 reste disponible pour une
       récupération manuelle. Un .tournoi de même nom déjà présent à
       destination est ÉCRASÉ (restauration voulue, comportement
       inchangé), protégé par cette même sauvegarde de sécurité.
       open_windows.json et phone_selected_pid.json ne sont
       volontairement JAMAIS restaurés (voir RESTORE_EXCLUDED_DATA_
       FILENAMES) ; license.json, lui, est restauré normalement, sans
       toucher au système de licence lui-même. tournament_day_folder
       (dans last_settings.json) fait exception au sein même de la
       copie de poker_data_dir : la valeur LOCALE est préservée (ou
       effacée si elle était absente), jamais remplacée par celle de
       la machine source — voir _read_tournament_day_folder/_restore_
       tournament_day_folder, qui opèrent sur poker_data_dir lui-même,
       jamais via tournament_prefs (qui résoudrait toujours le vrai
       ~/.poker_tournament).

    Renvoie {"restored_tournament_files", "restored_data",
    "safety_backup_dir"}. Lève BackupError (message prêt pour
    l'utilisateur, mentionnant la sauvegarde de sécurité) en cas
    d'échec."""
    open_tournament_paths = (
        open_windows.list_open_paths() if open_tournament_paths is None else open_tournament_paths
    )
    if open_tournament_paths:
        raise BackupError(
            "Au moins un tournoi est actuellement ouvert (ici ou dans une "
            "autre fenêtre). Fermez tous les tournois ouverts avant de "
            "restaurer une sauvegarde."
        )

    manifest = validate_backup_folder(backup_dir)

    # Validation du DOSSIER DE TOURNOI DE DESTINATION — demande
    # explicite du 2026-10-10, AVANT toute écriture (y compris la
    # sauvegarde de sécurité ci-dessous) : la restauration doit
    # utiliser EXCLUSIVEMENT le "Dossier par défaut" RÉELLEMENT
    # configuré sur CETTE machine — jamais le repli "C:\poker\senaco"
    # de default_tournament_folder() (qui conviendrait pour un usage
    # normal, mais choisirait ici SILENCIEUSEMENT un autre dossier),
    # jamais une création automatique d'un dossier manquant, et jamais
    # le chemin de la machine SOURCE (manifest["source_tournament_
    # folder"], jamais lu ici). Seulement si cette sauvegarde contient
    # effectivement des .tournoi à restaurer — sinon aucune exigence
    # sur ce réglage.
    if tournament_folder is None and manifest["tournament_files"]:
        raw = (tournament_prefs.load_last_settings().get("tournament_day_folder", "") or "").strip()
        if not raw:
            raise BackupError(
                "Aucun « Dossier par défaut » n'est configuré sur cet "
                "ordinateur. Renseignez-le dans Paramètres avant de "
                "restaurer — la restauration n'utilise jamais un autre "
                "emplacement."
            )
        tournament_folder = raw
    if manifest["tournament_files"]:
        if not os.path.isdir(tournament_folder):
            raise BackupError(
                "Le « Dossier par défaut » configuré n'existe pas ou n'est "
                f"plus accessible :\n{tournament_folder}\n\nVérifiez ce "
                "réglage dans Paramètres avant de restaurer."
            )
        # Accessibilité en ÉCRITURE — demande explicite : vérifiée SANS
        # créer ni modifier aucun fichier (os.access, jamais un fichier-
        # sonde créé puis supprimé). Limite connue, documentée plutôt
        # que cachée : os.access n'est pas une garantie absolue sur
        # tous les systèmes (certains partages réseau/configurations
        # Windows particulières peuvent la tromper dans un sens ou
        # l'autre — limite de l'OS/de Python, pas de cette application).
        # Toute écriture qui échouerait malgré tout PLUS TARD reste de
        # toute façon interceptée par la gestion d'erreur déjà en place
        # (voir le except plus bas) — jamais une donnée corrompue ni une
        # restauration silencieusement incomplète.
        if not os.access(tournament_folder, os.W_OK):
            raise BackupError(
                "Le « Dossier par défaut » configuré n'est pas accessible "
                f"en écriture sur cette machine :\n{tournament_folder}\n\n"
                "Vérifiez les permissions de ce dossier (ou reconnectez le "
                "support concerné) avant de restaurer."
            )

    poker_data_dir = default_poker_data_dir() if poker_data_dir is None else poker_data_dir
    if pre_restore_root is None:
        pre_restore_root = os.path.join(default_poker_data_dir(), PRE_RESTORE_BACKUPS_DIRNAME)

    # 1) Sauvegarde de sécurité de l'état ACTUEL, avant tout écrasement.
    os.makedirs(pre_restore_root, exist_ok=True)
    safety_backup = create_backup(
        pre_restore_root,
        tournament_folder=tournament_folder,
        poker_data_dir=poker_data_dir,
        computer_name=computer_name,
    )

    try:
        # 2) Restauration des .tournoi — copie fichier par fichier dans
        # le dossier de tournoi ACTUEL (jamais l'ancien chemin absolu du
        # manifeste, qui peut venir d'une autre machine). tournament_
        # folder est déjà validé (existant, accessible en écriture)
        # ci-dessus — jamais recréé ici (os.makedirs retiré : un
        # dossier manquant doit être REFUSÉ, jamais créé silencieusement).
        # Un fichier .tournoi de même nom déjà présent à destination est
        # ÉCRASÉ (shutil.copy2) — comportement de restauration voulu et
        # inchangé, protégé par la sauvegarde de sécurité de l'étape 1
        # (récupération manuelle possible) et par la gestion d'erreur
        # ci-dessous (jamais un rollback automatique des fichiers déjà
        # copiés, voir le docstring).
        restored_tournament_files = 0
        if manifest["tournament_files"]:
            tournaments_src = os.path.join(backup_dir, TOURNAMENTS_SUBDIR)
            for rel in manifest["tournament_files"]:
                src_path = os.path.join(tournaments_src, rel)
                if not os.path.isfile(src_path):
                    raise BackupError(f"Fichier manquant dans la sauvegarde : {rel}")
                dst_path = os.path.join(tournament_folder, *rel.split("/"))
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                restored_tournament_files += 1

        # 3) Restauration de ~/.poker_tournament — copie fichier par
        # fichier dans le dossier ACTUEL.
        restored_data = False
        if manifest["poker_tournament_data_included"]:
            data_src = os.path.join(backup_dir, POKER_DATA_SUBDIR)
            os.makedirs(poker_data_dir, exist_ok=True)
            # Préserve le "Dossier par défaut" LOCAL (demande explicite
            # du 2026-10-10) : capturé AVANT la copie, car last_
            # settings.json fait partie de la copie intégrale ci-dessous
            # et contient aussi tournament_day_folder — SPÉCIFIQUE à
            # CETTE machine, jamais à remplacer par celui de la machine
            # SOURCE. Lu/écrit ICI directement dans poker_data_dir (le
            # PARAMÈTRE de cette fonction) — JAMAIS via tournament_prefs.
            # load_last_settings()/save_last_settings(), qui résolvent
            # TOUJOURS le VRAI ~/.poker_tournament, indépendamment de
            # poker_data_dir.
            local_settings_path = os.path.join(poker_data_dir, "last_settings.json")
            local_day_folder = _read_tournament_day_folder(local_settings_path)
            # open_windows.json / phone_selected_pid.json : jamais
            # réinjectés (états de session temporaires d'une autre
            # machine/d'un autre moment, voir RESTORE_EXCLUDED_DATA_FILENAMES
            # et le docstring du module). license.json n'est PAS exclu.
            # pre_restore_backups (EXCLUDED_POKER_DATA_DIR_NAMES) : défense
            # en profondeur contre une ANCIENNE sauvegarde créée avant ce
            # correctif, qui pourrait déjà contenir une version gonflée de
            # ce dossier — jamais réinjectée ici non plus.
            _copy_tree_file_by_file(
                data_src, poker_data_dir, exclude_relpaths=RESTORE_EXCLUDED_DATA_FILENAMES,
                exclude_dir_names=EXCLUDED_POKER_DATA_DIR_NAMES,
            )
            _restore_tournament_day_folder(local_settings_path, local_day_folder)
            restored_data = True
    except Exception as e:
        raise BackupError(
            f"La restauration a échoué en cours de route :\n{e}\n\n"
            "Certaines données ont pu être partiellement remplacées, mais "
            "aucune suppression n'a eu lieu. Une sauvegarde de sécurité de "
            "l'état d'avant restauration reste disponible ici :\n"
            f"{safety_backup['backup_dir']}"
        ) from e

    return {
        "restored_tournament_files": restored_tournament_files,
        "restored_data": restored_data,
        "safety_backup_dir": safety_backup["backup_dir"],
    }
