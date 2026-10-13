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
  voir ce commentaire — incident réel du 2026-10-09, recopie infinie
  d'une sauvegarde de sécurité dans elle-même) n'est jamais inclus dans
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
import hashlib
import json
import os
import platform
import shutil
import sys
from datetime import datetime

import backup_history
import open_windows
import tournament_prefs
import version

BACKUP_FOLDER_PREFIX = "PokerTournament_Backup_"
MANIFEST_FILENAME = "backup_manifest.json"
TOURNAMENTS_SUBDIR = "tournois"
POKER_DATA_SUBDIR = "poker_tournament_data"

# Sous-dossier des sauvegardes de sécurité PRÉ-RESTAURATION, créé par
# restore_backup À L'INTÉRIEUR MÊME de poker_data_dir (voir
# restore_backup, pre_restore_root). Incident réel du 2026-10-09
# ("WinError 206 : nom de fichier... trop long" sur le HP) : une
# sauvegarde de poker_data_dir qui ne l'exclut pas se recopie
# indéfiniment dans elle-même (sa propre destination vit À L'INTÉRIEUR
# de sa propre source), et ferait grossir chaque sauvegarde suivante de
# TOUTES les précédentes déjà présentes dans ce dossier. EXCLU de
# TOUTE sauvegarde de poker_data_dir, sans exception — manuelle (Menu
# principal), automatique (Purger Données), ou interne (sécurité
# pré-restauration elle-même) — voir create_backup/restore_backup, qui
# passent tous deux EXCLUDED_POKER_DATA_DIR_NAMES ci-dessous à
# _copy_tree_file_by_file/_verify_copied_tree. Les sauvegardes de
# sécurité déjà présentes sur le disque ne sont jamais ni supprimées ni
# modifiées par cette exclusion : elle ne change que ce qui est COPIÉ
# dans les FUTURES sauvegardes, jamais ce qui existe déjà.
PRE_RESTORE_BACKUPS_DIRNAME = "pre_restore_backups"
EXCLUDED_POKER_DATA_DIR_NAMES = frozenset({PRE_RESTORE_BACKUPS_DIRNAME})

# Fichiers d'état de session temporaires (voir open_windows.py) : jamais
# réinjectés lors d'une RESTAURATION (demande du 2026-09-07) — ils restent
# en revanche sauvegardés normalement, comme tout le reste de
# ~/.poker_tournament (aucune exception côté sauvegarde). license.json
# n'est volontairement PAS dans cette liste : il reste restauré comme
# avant.
#
# backup_history.HISTORY_FILENAME (demande du 2026-10-07, garde-fou avant
# "Purger Données") : ajouté pour la MÊME raison, mais inversée — ce
# fichier reste sauvegardé normalement (aucune exception côté sauvegarde,
# comme tout ~/.poker_tournament), mais ne doit JAMAIS être écrasé par une
# restauration : l'historique RÉEL des sauvegardes faites sur CETTE
# machine doit survivre à une restauration, jamais être remplacé par celui
# — potentiellement plus ancien ou d'une autre machine — capturé dans la
# sauvegarde restaurée (choix explicite de l'utilisateur).
RESTORE_EXCLUDED_DATA_FILENAMES = frozenset({
    "open_windows.json",
    "phone_selected_pid.json",
    backup_history.HISTORY_FILENAME,
})

_MANIFEST_REQUIRED_KEYS = (
    "created_at", "app_version", "tournament_files", "poker_tournament_data_included",
)

# "tournament_file_hashes" (demande du 2026-10-07, SHA-256 par fichier
# .tournoi pour les FUTURES sauvegardes) : délibérément ABSENTE de
# _MANIFEST_REQUIRED_KEYS ci-dessus — une sauvegarde faite avant ce
# chantier reste un manifeste valide, jamais invalidée rétroactivement
# (voir data_purge.py, qui traite son absence comme "vérification
# SHA-256 indisponible pour cette sauvegarde", jamais une erreur).
_HASH_CHUNK_SIZE = 1024 * 1024


def sha256_of_file(path):
    """Empreinte SHA-256 (hexadécimal) du fichier `path`, lue par blocs de
    1 Mo (jamais tout le fichier en mémoire d'un coup — un fichier .tournoi
    volumineux reste géré sans surcharge). Fonction PUBLIQUE : réutilisée
    par data_purge.py pour revérifier, au moment de la purge, qu'une copie
    sauvegardée n'a pas été modifiée/corrompue depuis sa création."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(_HASH_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


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


def _copy_tree_file_by_file(src_dir, dst_dir, exclude_relpaths=None, exclude_dir_names=None,
                             progress_callback=None):
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

    `exclude_dir_names` (incident réel du 2026-10-09, "WinError 206 :
    nom de fichier... trop long" — pre_restore_backups, À L'INTÉRIEUR
    de poker_data_dir, se recopiait indéfiniment dans lui-même) :
    ensemble de NOMS de sous-dossiers (pas des chemins, juste le nom —
    ex. {"pre_restore_backups"}) à ne JAMAIS descendre, à QUELQUE
    PROFONDEUR QUE CE SOIT sous `src_dir`. Élagué EN PLACE sur `dirs`
    (`dirs[:] = ...`), seule façon reconnue par os.walk pour l'empêcher
    de descendre dans un sous-dossier — jamais un simple filtre sur
    `files`, qui laisserait `os.walk` y descendre quand même.

    `progress_callback` (optionnel) : appelé après CHAQUE fichier
    copié, avec (nombre_copiés, total, nom_du_fichier) — `total` est
    obtenu par un pré-parcours dédié de `src_dir` (même logique
    d'exclusion), fait UNIQUEMENT si `progress_callback` est fourni —
    aucun coût ni changement de comportement sinon."""
    exclude_relpaths = exclude_relpaths or frozenset()
    exclude_dir_names = exclude_dir_names or frozenset()
    total = None
    if progress_callback is not None:
        total = 0
        for root, dirs, _files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in exclude_dir_names]
            rel = os.path.relpath(root, src_dir)
            for name in _files:
                rel_path = name if rel == "." else f"{rel.replace(os.sep, '/')}/{name}"
                if rel_path not in exclude_relpaths:
                    total += 1
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
            if progress_callback is not None:
                progress_callback(count, total, name)
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


def _is_strictly_under(path, folder):
    """True si `path` (un fichier) est bien SOUS `folder` (jamais égal à
    `folder` lui-même) — jamais une exception, y compris sur des
    lecteurs différents sous Windows (os.path.commonpath lève alors
    ValueError, traité ici comme "non, pas sous ce dossier")."""
    try:
        return os.path.commonpath([path, folder]) == os.path.normpath(folder) and path != folder
    except ValueError:
        return False


def sanitize_folder_label(folder):
    """Nom de dossier technique lisible pour le préfixe d'index (voir
    _group_tournament_files_by_source) — basename du dossier, repris
    "racine" si vide (dossier racine "/", ou lettre de lecteur seule
    sous Windows, ex. "C:\\"). PUBLIQUE (pas de "_") délibérément :
    data_purge._covering_backup_for doit reconstruire EXACTEMENT le
    même préfixe pour reconnaître les fichiers lors de la vérification
    de couverture — jamais une logique dupliquée qui pourrait diverger
    de celle utilisée à l'écriture."""
    name = os.path.basename(os.path.normpath(folder))
    return name if name else "racine"


def resolve_multi_folder_destination_labels(tournament_source_folders, tournament_folder):
    """Calcule, pour une RESTAURATION multi-dossiers, le nom de sous-
    dossier LISIBLE à utiliser pour CHAQUE dossier source d'origine
    (correctif du 2026-10-13, suite à l'apparition de dossiers
    techniques comme "4_mardi" dans le dossier de tournoi après une
    restauration — voir restore_backup, seule appelante). Renvoie
    {index: libellé}, un par position de `tournament_source_folders`
    (même ordre que le manifeste, voir create_backup).

    NE TOUCHE JAMAIS à la structure interne de l'ARCHIVE elle-même
    (le préfixe "<index>_<nom>" écrit par create_backup dans tournament_
    file_hashes/relative_tournament_files reste INCHANGÉ — c'est ce qui
    garantit la compatibilité avec data_purge._covering_backup_for, qui
    continue de reconstruire ce même préfixe pour vérifier la couverture
    d'une sauvegarde). Cette fonction ne sert qu'à décider où ÉCRIRE,
    côté restauration, jamais comment l'archive est structurée.

    Règle de calcul : le libellé de base est sanitize_folder_label
    (dossier), SANS le préfixe numérique technique. Si plusieurs
    dossiers SOURCES de CETTE restauration partagent le même libellé de
    base (deux chemins absolus différents tous deux nommés par ex.
    "mardi" — possible si la sauvegarde d'origine couvrait déjà
    plusieurs dossiers homonymes), seul le PREMIER (dans l'ordre des
    index) reçoit le libellé simple ; les suivants reçoivent un suffixe
    numéroté " (2)", " (3)"... — jamais fusionnés sous un même nom,
    pour qu'aucun tournoi ne soit perdu ni écrasé par un AUTRE dossier
    source de la même sauvegarde (demande explicite : "conserver tous
    les tournois, même si plusieurs portent le même nom").

    Le libellé simple (non numéroté) PEUT légitimement correspondre à un
    dossier DÉJÀ PRÉSENT dans `tournament_folder` — c'est le cas normal
    et voulu (fusion avec le dossier "mardi" habituel déjà utilisé par
    le club, par exemple : restore_backup écrase alors un éventuel
    fichier de même nom exactement comme pour une restauration à un seul
    dossier, protégé par la même sauvegarde de sécurité automatique,
    comportement INCHANGÉ). En revanche, un suffixe numéroté n'est
    JAMAIS choisi s'il correspond à un dossier déjà présent à la
    destination mais SANS RAPPORT avec cette restauration (demande
    explicite : vérifier aussi les collisions avec les dossiers déjà
    présents, pas seulement entre dossiers sources de la sauvegarde) —
    cela éviterait de fusionner par accident deux ensembles de fichiers
    totalement étrangers l'un à l'autre. Vérifié via un simple
    os.listdir, au mieux (silencieusement traité comme "rien d'existant"
    si `tournament_folder` n'existe pas encore ou n'est pas lisible —
    purement informatif ici ; restore_backup revalide de toute façon ce
    dossier séparément, AVANT d'écrire quoi que ce soit)."""
    try:
        existing_at_destination = {
            name for name in os.listdir(tournament_folder)
            if os.path.isdir(os.path.join(tournament_folder, name))
        }
    except OSError:
        existing_at_destination = set()

    claimed_this_restore = set()
    resolved = {}
    for index, folder in enumerate(tournament_source_folders):
        base_label = sanitize_folder_label(folder)
        if base_label not in claimed_this_restore:
            candidate = base_label
        else:
            n = 2
            candidate = f"{base_label} ({n})"
            while candidate in claimed_this_restore or candidate in existing_at_destination:
                n += 1
                candidate = f"{base_label} ({n})"
        claimed_this_restore.add(candidate)
        resolved[index] = candidate
    return resolved


def _group_tournament_files_by_source(tournament_files, candidate_folders):
    """Regroupe `tournament_files` (chemins ABSOLUS, déjà dédoublonnés
    par l'appelant) par le dossier de `candidate_folders` qui les
    contient RÉELLEMENT — le plus SPÉCIFIQUE en cas d'imbrication (ex.
    un dossier ajouté manuellement À L'INTÉRIEUR du dossier par défaut).
    Renvoie un dict ORDONNÉ (ordre de première apparition dans
    `tournament_files`) {dossier_absolu: [fichiers...]}.

    Sans `candidate_folders` (None/vide — appelant qui ne connaît pas
    ses dossiers candidats) : UN SEUL groupe, l'ancêtre commun de tous
    les fichiers (os.path.commonpath) — toujours correct (jamais de
    collision, voir create_backup), simplement moins fidèle à
    l'arborescence d'origine si les fichiers sont très éloignés les uns
    des autres. JAMAIS un repli sur le dossier parent direct de chaque
    fichier pris isolément : cela créerait artificiellement un groupe
    par sous-dossier (ex. un groupe "dimanche", un groupe "lundi"...)
    même quand tous ces sous-dossiers appartiennent en réalité à UNE
    seule racine déjà connue — voir DataPurgeDialog._on_backup_now, qui
    fournit toujours self._accessible_folders ici.

    Un fichier qui ne correspond à AUCUN dossier candidat connu (ne
    devrait pas arriver si `candidate_folders` est exhaustif) retombe
    sur son propre dossier parent direct — jamais une exception, jamais
    un fichier perdu."""
    if not tournament_files:
        return {}
    if not candidate_folders:
        return {os.path.commonpath(tournament_files): list(tournament_files)}
    folders_sorted = sorted(
        {os.path.abspath(f) for f in candidate_folders}, key=len, reverse=True,
    )
    groups = {}
    for f in tournament_files:
        owner = next((folder for folder in folders_sorted if _is_strictly_under(f, folder)), None)
        if owner is None:
            owner = os.path.dirname(f)
        groups.setdefault(owner, []).append(f)
    return groups


def create_backup(destination_parent_dir, tournament_folder=None, poker_data_dir=None,
                   computer_name=None, tournament_files=None, tournament_source_folders=None,
                   progress_callback=None, stage_prefix="backup"):
    """Crée une sauvegarde complète dans un nouveau sous-dossier horodaté
    de `destination_parent_dir` (la clé USB/dossier choisi par
    l'utilisateur, ou — voir restore_backup — le dossier fixe des
    sauvegardes de sécurité pré-restauration). `tournament_folder`/
    `poker_data_dir`/`computer_name` : injectables pour les tests
    (dossiers synthétiques) — None utilise les vrais réglages de
    l'application (default_tournament_folder()/default_poker_data_dir()/
    platform.node()).

    `tournament_files` (demande explicite du 2026-10-08, revue de
    sécurité "Purger Données" — "la sauvegarde lancée depuis Purger
    Données doit couvrir EXACTEMENT les fichiers sélectionnés pour la
    purge, jamais une redécouverte indépendante dans le seul dossier
    par défaut") : liste explicite de chemins ABSOLUS de fichiers
    .tournoi à sauvegarder. Quand fournie (y compris une liste VIDE),
    BYPASSE ENTIÈREMENT find_tournament_files()/tournament_folder pour
    la découverte — `None` (défaut, comportement HISTORIQUE strictement
    inchangé) conserve la découverte récursive dans tournament_folder/
    default_tournament_folder().

    `tournament_source_folders` (ignoré si `tournament_files` n'est pas
    fourni) : dossiers candidats connus de l'appelant (ex.
    DataPurgeDialog._accessible_folders) utilisés pour regrouper
    `tournament_files` par dossier d'origine RÉEL — voir
    _group_tournament_files_by_source. Si TOUS les fichiers de
    `tournament_files` appartiennent au MÊME dossier (cas normal, un
    seul "Dossier par défaut" ou un seul dossier ajouté) : manifeste
    STRICTEMENT IDENTIQUE à l'ancien schéma à un seul dossier (chemins
    relatifs SANS préfixe, "source_tournament_folder" seul renseigné).
    Seulement si PLUSIEURS dossiers DISTINCTS sont réellement
    représentés dans la sélection, chaque chemin relatif est préfixé
    par "<index>_<nom_du_dossier>/" (ex. "0_dimanche/To1.tournoi",
    "1_dimanche/To1.tournoi" pour deux dossiers différents partageant
    le même nom "dimanche") — l'index garantit l'ABSENCE DE COLLISION,
    prouvée par construction (os.path.relpath est injectif pour une
    base fixe : deux chemins absolus distincts, groupés sous le MÊME
    dossier, ne peuvent jamais produire le même chemin relatif ; des
    dossiers différents reçoivent toujours des index différents). Le
    manifeste gagne alors un nouveau champ ADDITIF, "tournament_source_
    folders" (liste ORDONNÉE des dossiers absolus, un par index) — voir
    data_purge._covering_backup_for pour la vérification de couverture
    correspondante, qui reste de son côté strictement inchangée pour
    tout manifeste SANS ce champ (sauvegardes existantes, sauvegarde
    classique du Menu principal — un seul dossier, jamais ce champ).
    "source_tournament_folder" (singulier) reste alors None : jamais
    une valeur qui ne représenterait qu'UN SEUL des dossiers réellement
    utilisés.

    `progress_callback` (optionnel) : appelé pendant la copie avec
    (stage_prefix, stage, nombre_copiés, total, nom_du_fichier) —
    `stage` vaut "tournament_files" ou "poker_data" selon la phase en
    cours. `stage_prefix` identifie l'APPELANT (ex. "backup" pour un
    appel autonome, "safety_backup" pour l'appel interne fait par
    restore_backup) — purement informatif, jamais utilisé ici pour une
    quelconque logique, seulement transmis tel quel pour que l'appelant
    (l'interface) puisse afficher un libellé différent selon le
    contexte sans dupliquer cette fonction.

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

    poker_data_dir = default_poker_data_dir() if poker_data_dir is None else poker_data_dir
    computer_name = platform.node() if computer_name is None else computer_name

    # Sélection EXPLICITE (demande du 2026-10-08) : bypass total de la
    # découverte par dossier unique — jamais une redécouverte
    # indépendante qui pourrait diverger de ce qui a été réellement
    # sélectionné/affiché à l'utilisateur.
    explicit_selection = tournament_files is not None
    if explicit_selection:
        tournament_files = [os.path.abspath(p) for p in tournament_files]
    else:
        tournament_folder = default_tournament_folder() if tournament_folder is None else tournament_folder
        tournament_files = find_tournament_files(tournament_folder)

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

        relative_tournament_files = []
        # SHA-256 par fichier .tournoi (demande du 2026-10-07) : calculée
        # sur la COPIE (dst_path), juste après l'écriture — détecte aussi
        # une éventuelle corruption pendant la copie elle-même, pas
        # seulement une modification ultérieure du fichier sauvegardé.
        tournament_file_hashes = {}
        source_tournament_folder_single = None
        tournament_source_folders_multi = None
        # Paires (src, dst) à revérifier explicitement APRÈS écriture du
        # manifeste, DANS TOUS LES CAS (sélection explicite OU dossier
        # unique "historique") — jamais _verify_copied_tree (marche-
        # arrière complète du DOSSIER SOURCE) pour les .tournoi : ce
        # dossier peut contenir des fichiers ANNEXES jamais copiés (PDF
        # d'exports, CSV, .DS_Store, Thumbs.db...) — un parcours libre
        # les signalerait à tort comme "manquants après la copie",
        # exactement l'incident réel du 2026-10-09 ("primes_
        # To170926.pdf"). Vérifier UNIQUEMENT les paires réellement
        # copiées élimine structurellement ce faux positif, quel que
        # soit le fichier étranger présent — jamais un traitement
        # particulier pour un nom de fichier donné.
        tournament_copy_pairs = []

        if tournament_files:
            tournaments_dst = os.path.join(backup_dir, TOURNAMENTS_SUBDIR)
            if explicit_selection:
                groups = _group_tournament_files_by_source(tournament_files, tournament_source_folders)
                if len(groups) <= 1:
                    only_folder = next(iter(groups))
                    source_tournament_folder_single = only_folder
                    for src_path in tournament_files:
                        rel = os.path.relpath(src_path, only_folder)
                        dst_path = os.path.join(tournaments_dst, rel)
                        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                        shutil.copy2(src_path, dst_path)
                        rel_slash = rel.replace(os.sep, "/")
                        relative_tournament_files.append(rel_slash)
                        tournament_file_hashes[rel_slash] = sha256_of_file(dst_path)
                        tournament_copy_pairs.append((src_path, dst_path))
                        if progress_callback is not None:
                            progress_callback(
                                stage_prefix, "tournament_files",
                                len(relative_tournament_files), len(tournament_files), rel_slash,
                            )
                else:
                    tournament_source_folders_multi = list(groups.keys())
                    for index, (folder, files_in_group) in enumerate(groups.items()):
                        label = f"{index}_{sanitize_folder_label(folder)}"
                        for src_path in files_in_group:
                            rel_within = os.path.relpath(src_path, folder).replace(os.sep, "/")
                            rel_slash = f"{label}/{rel_within}"
                            dst_path = os.path.join(tournaments_dst, *rel_slash.split("/"))
                            os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                            shutil.copy2(src_path, dst_path)
                            relative_tournament_files.append(rel_slash)
                            tournament_file_hashes[rel_slash] = sha256_of_file(dst_path)
                            tournament_copy_pairs.append((src_path, dst_path))
                            if progress_callback is not None:
                                progress_callback(
                                    stage_prefix, "tournament_files",
                                    len(relative_tournament_files), len(tournament_files), rel_slash,
                                )
            else:
                # Comportement HISTORIQUE de découverte/copie inchangé
                # (dossier unique, find_tournament_files) — seule la
                # VÉRIFICATION qui suit change (voir tournament_copy_
                # pairs ci-dessus), pour corriger l'asymétrie copie/
                # vérification.
                source_tournament_folder_single = tournament_folder
                for src_path in tournament_files:
                    rel = os.path.relpath(src_path, tournament_folder)
                    dst_path = os.path.join(tournaments_dst, rel)
                    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                    shutil.copy2(src_path, dst_path)
                    rel_slash = rel.replace(os.sep, "/")
                    relative_tournament_files.append(rel_slash)
                    tournament_file_hashes[rel_slash] = sha256_of_file(dst_path)
                    tournament_copy_pairs.append((src_path, dst_path))
                    if progress_callback is not None:
                        progress_callback(
                            stage_prefix, "tournament_files",
                            len(relative_tournament_files), len(tournament_files), rel_slash,
                        )

        poker_data_included = bool(poker_data_dir and os.path.isdir(poker_data_dir))
        if poker_data_included:
            _copy_tree_file_by_file(
                poker_data_dir, os.path.join(backup_dir, POKER_DATA_SUBDIR),
                exclude_dir_names=EXCLUDED_POKER_DATA_DIR_NAMES,
                progress_callback=(
                    (lambda current, total, name: progress_callback(
                        stage_prefix, "poker_data", current, total, name,
                    )) if progress_callback is not None else None
                ),
            )

        manifest = {
            "created_at": timestamp.isoformat(timespec="seconds"),
            "app_version": version.APP_VERSION,
            "computer_name": computer_name,
            "source_tournament_folder": source_tournament_folder_single,
            "source_poker_tournament_data": poker_data_dir,
            "tournament_files": relative_tournament_files,
            "poker_tournament_data_included": poker_data_included,
            "tournament_file_hashes": tournament_file_hashes,
        }
        if tournament_source_folders_multi is not None:
            manifest["tournament_source_folders"] = tournament_source_folders_multi
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


# Nom de fichier dupliqué délibérément depuis tournament_prefs.py (qui ne
# l'exporte pas comme constante publique) — voir _read_tournament_day_
# folder/_restore_tournament_day_folder ci-dessous : ces deux fonctions
# opèrent TOUJOURS sur un `poker_data_dir` fourni en paramètre, jamais
# via tournament_prefs.load_last_settings()/save_last_settings(), qui
# résolvent TOUJOURS le vrai ~/.poker_tournament — sans rapport avec le
# dossier (réel ou synthétique, pour les tests) réellement manipulé ici.
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
                    pre_restore_root=None, computer_name=None, open_tournament_paths=None,
                    progress_callback=None):
    """Restaure la sauvegarde `backup_dir`. `tournament_folder`/
    `poker_data_dir`/`pre_restore_root`/`computer_name`/
    `open_tournament_paths` : injectables pour les tests ; None utilise
    les vrais réglages de l'application (dont, pour ce dernier,
    open_windows.list_open_paths() — PID morts déjà ignorés par cette
    fonction, voir sa propre docstring).

    `progress_callback` (optionnel, même signature que pour
    create_backup : (stage_prefix, stage, nombre_copiés, total,
    nom_du_fichier)) : transmis à l'appel interne de create_backup pour
    la sauvegarde de sécurité de l'étape 2 avec stage_prefix=
    "safety_backup", puis réutilisé pour les étapes 3/4 ci-dessous
    (restauration proprement dite) avec stage_prefix="restore" — un
    seul callback suffit à l'appelant pour suivre l'ENSEMBLE de
    l'opération, sécurité comprise.

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
       inchangé), protégé par cette même sauvegarde de sécurité. Pour
       une sauvegarde MULTI-DOSSIERS (manifeste avec "tournament_
       source_folders"), le préfixe technique "<index>_<nom>" du
       manifeste (structure de l'ARCHIVE, inchangée) n'est PLUS recopié
       tel quel à la destination depuis le correctif du 2026-10-13 —
       voir resolve_multi_folder_destination_labels, qui choisit un nom
       de sous-dossier lisible (fusionné avec un dossier existant du
       même nom si pertinent, jamais entre deux dossiers sources
       DIFFÉRENTS de cette même restauration). Sauvegarde ANCIENNE ou à
       un seul dossier : comportement STRICTEMENT inchangé.
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
        progress_callback=progress_callback,
        stage_prefix="safety_backup",
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
            total_tournament_files = len(manifest["tournament_files"])
            # Correctif du 2026-10-13 ("dossiers techniques 4_mardi
            # après restauration") : pour une sauvegarde multi-dossiers
            # (manifeste portant "tournament_source_folders"), le
            # préfixe "<index>_<nom>" de CHAQUE chemin relatif — voir
            # create_backup, structure de L'ARCHIVE, INCHANGÉE — n'est
            # PLUS jamais recopié tel quel à la destination. Remplacé
            # par un libellé lisible, calculé UNE SEULE FOIS ici (voir
            # resolve_multi_folder_destination_labels). Sauvegarde
            # ANCIENNE ou à UN SEUL dossier (pas de champ "tournament_
            # source_folders" dans le manifeste) : resolved_labels reste
            # None, le bloc ci-dessous retombe sur `rel` tel quel —
            # comportement STRICTEMENT INCHANGÉ, jamais touché par ce
            # correctif (compatibilité totale avec les anciennes
            # sauvegardes).
            multi_source_folders = manifest.get("tournament_source_folders")
            resolved_labels = (
                resolve_multi_folder_destination_labels(multi_source_folders, tournament_folder)
                if multi_source_folders else None
            )
            for rel in manifest["tournament_files"]:
                src_path = os.path.join(tournaments_src, rel)
                if not os.path.isfile(src_path):
                    raise BackupError(f"Fichier manquant dans la sauvegarde : {rel}")
                if resolved_labels is not None:
                    # rel == "<index>_<nom>/<reste>" par construction
                    # (create_backup) — jamais sans "/" ici, un fichier a
                    # toujours au moins un nom après le libellé de groupe.
                    old_label, rest_within_group = rel.split("/", 1)
                    index = int(old_label.split("_", 1)[0])
                    rel_for_destination = f"{resolved_labels[index]}/{rest_within_group}"
                else:
                    rel_for_destination = rel
                dst_path = os.path.join(tournament_folder, *rel_for_destination.split("/"))
                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                shutil.copy2(src_path, dst_path)
                restored_tournament_files += 1
                if progress_callback is not None:
                    progress_callback(
                        "restore", "tournament_files",
                        restored_tournament_files, total_tournament_files, rel,
                    )

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
            # SOURCE (voir tournament_prefs.PERSISTED_KEYS : ce fichier
            # mélange ce chemin avec d'autres préférences génériques
            # portables — buy-in, structure de blindes... — que la
            # restauration doit, elle, bien mettre à jour normalement).
            #
            # Lu/écrit ICI directement dans poker_data_dir (le PARAMÈTRE
            # de cette fonction) — JAMAIS via tournament_prefs.
            # load_last_settings()/save_last_settings(), qui résolvent
            # TOUJOURS le VRAI ~/.poker_tournament, indépendamment de
            # poker_data_dir. Erreur réelle commise puis corrigée
            # pendant ce chantier : un premier essai via tournament_
            # prefs a réellement écrit dans le vrai ~/.poker_tournament
            # de la machine de développement pendant l'exécution des
            # tests (valeur réécrite identique, donc sans perte
            # constatée, mais une violation du principe "jamais toucher
            # aux vraies données en test" — voir _read_tournament_day_
            # folder/_restore_tournament_day_folder ci-dessous, qui
            # réparent cela en opérant exclusivement sur poker_data_dir).
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
                progress_callback=(
                    (lambda current, total, name: progress_callback(
                        "restore", "poker_data", current, total, name,
                    )) if progress_callback is not None else None
                ),
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
