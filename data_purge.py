# -*- coding: utf-8 -*-
"""
"Purger Données" (demande du 2026-10-07) — nettoyage volontaire de vieux
fichiers .tournoi, par plage de dates et type(s) de tournoi, avec garde-
fous stricts avant toute suppression définitive : couverture vérifiée par
une sauvegarde réellement accessible MAINTENANT (jamais une confiance
aveugle dans backup_history.py), aucun tournoi actuellement ouvert, et
une purge qui ne peut jamais être partielle (déplacement en corbeille
temporaire, vérification finale exhaustive, rollback complet sur toute
erreur).

Complètement SÉPARÉ de la purge du LOG (action_log.py:purge_actions,
onglet LOG, inchangée) : "Purger Données" ne touche jamais à
actions_log.sqlite3, et "Purger LOG" ne touche jamais à un fichier
.tournoi.

Ce module ne modifie JAMAIS : roster.json, les photos (player_photos.py),
export_prefs.json, tournament_prefs.json/last_settings.json, le LOG, ni
les sauvegardes elles-mêmes — uniquement des fichiers .tournoi désignés
explicitement par l'appelant.
"""
import os
import shutil
import sqlite3
import time
import uuid
from collections import deque

import backup_restore
import open_windows
from database import find_tournament_files, _read_tournament_date_ro

# Types réels d'un tournoi (demande du 2026-10-07) — reprend EXACTEMENT la
# convention de nommage déjà établie et validée pour l'onglet Statistiques
# (database.py: _tournament_type_matches, STATS_TOURNAMENT_TYPE_*) : un
# tournoi normal commence par "To", un Sit & Go par "Sn". Fonction
# ENTIÈREMENT SÉPARÉE de celle des Statistiques (jamais importée ni
# modifiée) pour ne courir aucun risque de régression sur cette
# fonctionnalité déjà validée — seule la 3ᵉ catégorie differe : les
# Statistiques traitent tout fichier hors convention comme inclus dans
# "Tous" (ne jamais faire disparaître un ancien fichier d'un filtre déjà
# existant) ; "Purger Données" en fait au contraire une catégorie à part,
# désactivée par défaut, que l'utilisateur peut cocher explicitement
# (décision validée : "je préfère qu'ils puissent être purgés
# volontairement plutôt que de devenir impossibles à nettoyer").
PURGE_TYPE_TOURNOIS = "tournois"
PURGE_TYPE_SITNGO = "sitngo"
PURGE_TYPE_OTHER = "other"
PURGE_TYPES = (PURGE_TYPE_TOURNOIS, PURGE_TYPE_SITNGO, PURGE_TYPE_OTHER)
# Décoché par défaut dans l'interface (voir main.py: DataPurgeDialog) —
# seule PURGE_TYPE_OTHER est concernée (décision explicite de
# l'utilisateur) ; Tournois/Sit & Go peuvent eux être cochés par défaut.
PURGE_TYPE_DEFAULT_CHECKED = {PURGE_TYPE_TOURNOIS, PURGE_TYPE_SITNGO}

# Marqueur d'un fichier .tournoi qui n'a PAS pu être ouvert/lu comme une
# base Senaco valide (corrompu, pas un fichier SQLite, table "settings"
# absente...) — demande du 2026-10-08, diagnostic "classification
# fiable des tournois". DÉLIBÉRÉMENT ABSENT de PURGE_TYPES : aucune case
# à cocher de l'interface n'y correspond jamais, donc un fichier
# illisible ne peut JAMAIS devenir éligible à une purge, quels que
# soient les types cochés (y compris "Autres / non classés") — voir
# _tournament_matches_criteria, qui compare toujours contre l'ensemble
# `types` choisi par l'utilisateur, qui ne contient jamais cette valeur.
PURGE_TYPE_UNREADABLE = "unreadable"


class PurgeError(Exception):
    """Erreur de purge — message déjà rédigé pour être affiché tel quel à
    l'utilisateur. Levée uniquement AVANT toute suppression définitive,
    ou après un rollback complet réussi (voir purge_tournaments) : ne
    signifie donc jamais une perte de données."""


class TournamentUnreadableError(Exception):
    """Levée UNIQUEMENT en interne par _read_is_sng_strict (jamais
    propagée hors de ce module — voir classify_tournament_for_purge, qui
    la capture systématiquement) quand un fichier .tournoi ne peut pas
    être ouvert/lu comme une base Senaco valide : fichier corrompu, pas
    un fichier SQLite du tout, ou table "settings" absente. Le message
    (déjà lisible) est conservé pour l'affichage ("Fichiers illisibles",
    voir main.py: DataPurgeDialog)."""


def tournament_purge_type(path):
    """Type de `path` au sens de "Purger Données" — voir PURGE_TYPE_*
    ci-dessus. Déduit UNIQUEMENT du préfixe du nom de fichier, jamais
    d'une donnée interne au tournoi (même convention que les
    Statistiques, voir database._tournament_type_matches).

    N'est plus utilisée QUE comme repli (voir classify_tournament_for_
    purge) : le diagnostic du 2026-10-08 a montré qu'un tournoi créé via
    "Sit & Go rapide" peut être enregistré sous n'importe quel nom (la
    boîte de sauvegarde macOS/Windows permet de tout renommer) — le
    préfixe seul faisait alors passer de vrais Sit & Go pour des
    "Autres / non classés". Reste INCHANGÉE (jamais modifiée) : utilisée
    telle quelle par database._tournament_type_matches (Statistiques,
    jamais touchée par ce chantier) et par le repli lui-même ici."""
    name = os.path.basename(path)
    if name.startswith("To"):
        return PURGE_TYPE_TOURNOIS
    if name.startswith("Sn"):
        return PURGE_TYPE_SITNGO
    return PURGE_TYPE_OTHER


def _read_is_sng_strict(path):
    """Lit le réglage "is_sng" DANS le fichier .tournoi — ouverture
    SQLite STRICTEMENT en lecture seule (URI mode=ro, même précaution
    que database._read_tournament_date_ro) : jamais une écriture, jamais
    une migration de schéma (contrairement à database.Database(path),
    qui exécute SCHEMA/._migrate()/._init_defaults() même pour une
    simple lecture) — ce fichier n'est JAMAIS modifié par cette
    fonction, quel que soit son contenu.

    Renvoie :
    - True / False si la clé "is_sng" est présente et interprétable
      comme un entier (0 ou toute autre valeur) ;
    - None si la clé est ABSENTE mais que le fichier a pu être ouvert et
      interrogé normalement (base Senaco valide, simplement plus
      ancienne ou jamais créée via "Sit & Go rapide") — ne signifie
      JAMAIS "corrompu" ;
    - None aussi si la valeur existe mais n'est pas interprétable comme
      un entier (jamais une exception pour une valeur inattendue).

    Lève TournamentUnreadableError si `path` ne peut pas être ouvert
    comme une base SQLite valide, ou n'a pas la table "settings"
    attendue d'un fichier .tournoi Senaco — jamais une autre
    exception : OSError est aussi capturée (ex. permission refusée)."""
    try:
        conn = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    except (sqlite3.Error, OSError) as e:
        raise TournamentUnreadableError(str(e)) from e
    try:
        try:
            row = conn.execute("SELECT value FROM settings WHERE key='is_sng'").fetchone()
        except (sqlite3.Error, OSError) as e:
            raise TournamentUnreadableError(str(e)) from e
    finally:
        conn.close()
    if row is None:
        return None
    try:
        return bool(int(row[0]))
    except (TypeError, ValueError):
        return None


def classify_tournament_for_purge(path):
    """Classification RÉELLE d'un tournoi pour "Purger Données" (demande
    du 2026-10-08, diagnostic "0 Sit & Go trouvé" malgré de vrais Sit &
    Go sur le disque — leur nom ne commençait pas par "Sn", car la boîte
    de sauvegarde permet de tout renommer à la création). Priorité
    STRICTE, jamais l'inverse :

    1) "is_sng" vrai dans le fichier (lecture seule) -> PURGE_TYPE_SITNGO,
       quel que soit le nom du fichier.
    2) "is_sng" EXPLICITEMENT faux -> PURGE_TYPE_TOURNOIS, quel que soit
       le nom.
    3) "is_sng" ABSENT (fichier par ailleurs lisible) -> repli sur le
       préfixe du nom (tournament_purge_type, "To"/"Sn") — JAMAIS
       "Autres" automatiquement ici : l'absence de "is_sng" ne prouve
       RIEN à elle seule (ancien fichier valide, jamais créé via "Sit &
       Go rapide" ni via "Nouveau tournoi" avec le réglage explicite).
    4) Ni "is_sng" ni préfixe reconnu -> PURGE_TYPE_OTHER (décoché par
       défaut, voir PURGE_TYPE_DEFAULT_CHECKED) — catégorie "non
       classé", jamais supprimée sans un choix explicite.
    5) Fichier illisible/corrompu/pas une base Senaco valide ->
       PURGE_TYPE_UNREADABLE — jamais dans PURGE_TYPES, donc jamais
       sélectionnable ni éligible à une purge (voir sa docstring).

    Renvoie (purge_type, error) — `error` est toujours None sauf pour
    PURGE_TYPE_UNREADABLE, où il contient un message lisible sur la
    cause (jamais une exception propagée hors de cette fonction)."""
    try:
        is_sng = _read_is_sng_strict(path)
    except TournamentUnreadableError as e:
        return PURGE_TYPE_UNREADABLE, str(e)
    if is_sng is True:
        return PURGE_TYPE_SITNGO, None
    if is_sng is False:
        return PURGE_TYPE_TOURNOIS, None
    return tournament_purge_type(path), None


def _dedup_abspath(paths):
    """Chemins ABSOLUS, dédoublonnés, dans leur ordre d'arrivée (premier
    vu gagne) — utilitaire partagé par collect_candidate_folders et
    find_purgeable_tournaments_multi : le MÊME fichier/dossier découvert
    deux fois (sources qui se recoupent, dossiers imbriqués...) ne doit
    jamais apparaître deux fois."""
    seen = set()
    result = []
    for p in paths:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            result.append(ap)
    return result


def collect_candidate_folders(tournament_day_folder=None, last_tournament_dir=None,
                               open_tournament_paths=None, action_log_tournament_paths=None,
                               extra_folders=None):
    """Construit l'ensemble dédoublonné des dossiers candidats pour
    "Purger Données" (demande du 2026-10-07, "ne pas obliger
    artificiellement l'utilisateur à renseigner Dossier par défaut si
    Senaco sait déjà où se trouvent ses tournois") à partir des
    emplacements RÉELLEMENT connus de Senaco — JAMAIS une remontée
    automatique au dossier parent d'un dossier déjà connu, JAMAIS un
    balayage du disque : seules les sources ci-dessous, chacune
    FOURNIE PAR L'APPELANT (ce module ne lit jamais lui-même
    tournament_prefs/export_prefs/open_windows/action_log, pour rester
    testable avec des valeurs synthétiques — voir main.py:
    DataPurgeDialog, seul appelant réel).

    - `tournament_day_folder` : "Dossier par défaut" (tournament_prefs.
      tournament_day_folder) s'il est renseigné — ajouté TEL QUEL
      (recherche récursive, couvre déjà les sous-dossiers "jour de la
      semaine" créés par tournament_day_folder_proposal).
    - `last_tournament_dir` : dernier dossier utilisé pour CRÉER un
      tournoi (export_prefs "last_tournament_dir") — ajouté TEL QUEL,
      jamais son parent : rien ne garantit qu'un dossier voisin
      contienne lui aussi des tournois.
    - `open_tournament_paths` : chemins des tournois actuellement
      ouverts (voir open_windows.list_open_paths) — seul le dossier
      PARENT DIRECT de chaque fichier est ajouté, jamais un niveau
      au-dessus.
    - `action_log_tournament_paths` : tournament_path connus du journal
      des actions à distance (action_log.list_tournaments, remarque :
      ne couvre que les tournois ayant déjà utilisé le contrôle à
      distance) — seul le dossier PARENT DIRECT de chaque fichier est
      ajouté.
    - `extra_folders` : dossiers ajoutés MANUELLEMENT par l'utilisateur
      pour CETTE session de purge uniquement (voir DataPurgeDialog,
      "Ajouter un dossier à vérifier...") — ajoutés TELS QUELS, jamais
      persistés, jamais remontés au parent.

    Renvoie une liste de chemins ABSOLUS, dédoublonnés, dans un ordre
    stable — AUCUNE vérification d'existence ici (voir
    split_accessible_folders, qui distingue explicitement "accessible"
    de "connu mais actuellement inaccessible", ex. clé USB/disque
    externe débranché — jamais une erreur, jamais une recherche
    silencieusement incomplète sans le signaler à l'appelant)."""
    candidates = []
    if tournament_day_folder:
        candidates.append(tournament_day_folder)
    if last_tournament_dir:
        candidates.append(last_tournament_dir)
    for p in sorted(open_tournament_paths or ()):
        candidates.append(os.path.dirname(p))
    for p in sorted(action_log_tournament_paths or ()):
        candidates.append(os.path.dirname(p))
    for f in (extra_folders or ()):
        candidates.append(f)
    return _dedup_abspath(candidates)


def split_accessible_folders(folders):
    """(accessible, inaccessible) — `folders` déjà dédoublonnés/absolus
    (voir collect_candidate_folders). Un dossier "inaccessible" (clé
    USB/disque externe débranché, dossier supprimé/renommé depuis...)
    n'est JAMAIS une erreur ici : seulement réparti dans la liste
    séparée, à l'appelant (DataPurgeDialog) de le signaler EXPLICITEMENT
    à l'utilisateur — ne jamais prétendre silencieusement à une
    recherche exhaustive quand un emplacement connu n'a pas pu être
    examiné."""
    accessible = [f for f in folders if os.path.isdir(f)]
    inaccessible = [f for f in folders if not os.path.isdir(f)]
    return accessible, inaccessible


def _tournament_matches_criteria(purge_type, path, date_from, date_to, types):
    """`purge_type` est TOUJOURS fourni par l'appelant (déjà déterminé
    via classify_tournament_for_purge, jamais recalculé ici) — permet à
    scan_candidate_folders_for_purge de classer chaque fichier UNE SEULE
    fois (ouverture SQLite en lecture seule) et de réutiliser ce même
    résultat ici pour le filtre Date, plutôt que de rouvrir le fichier
    une seconde fois. `purge_type` peut valoir PURGE_TYPE_UNREADABLE :
    `types` (toujours un sous-ensemble de PURGE_TYPES) ne le contient
    jamais, donc cette comparaison suffit À ELLE SEULE à exclure
    définitivement un fichier illisible, quels que soient les types
    cochés."""
    if purge_type not in types:
        return False
    date = _read_tournament_date_ro(path)
    if date_from and date < date_from:
        return False
    if date_to and date > date_to:
        return False
    return True


def filter_tournament_files(paths, date_from=None, date_to=None, types=None):
    """Filtre une liste de chemins .tournoi DÉJÀ TROUVÉS (par n'importe
    quel mécanisme de recherche — database.find_tournament_files, ou le
    scan BORNÉ, voir scan_candidate_folders_for_purge) selon les critères
    Date + Type de "Purger Données" — logique PARTAGÉE entre
    find_purgeable_tournaments (dossier unique, NON bornée) et le scan
    multi-dossiers borné, pour ne jamais dupliquer/diverger cette
    interprétation. Le TYPE de chaque fichier est déterminé par
    classify_tournament_for_purge (métadonnées internes "is_sng" EN
    PRIORITÉ, repli sur le préfixe du nom seulement si absent — voir sa
    docstring pour le diagnostic complet du 2026-10-08) — jamais le seul
    nom de fichier.

    `date_from`/`date_to` : chaînes 'AAAA-MM-JJ' (bornes incluses) ou
    None (borne ouverte). `types` : sous-ensemble de PURGE_TYPES ; vide/
    None -> liste VIDE (jamais "tous les types par défaut" — un type
    doit toujours être explicitement choisi par l'utilisateur). Un
    fichier illisible/corrompu (voir PURGE_TYPE_UNREADABLE) n'est JAMAIS
    inclus, quels que soient les types choisis.

    Renvoie une liste ABSOLUE, triée."""
    if not types:
        return []
    types = set(types)
    result = []
    for p in paths:
        purge_type, _error = classify_tournament_for_purge(p)
        if _tournament_matches_criteria(purge_type, p, date_from, date_to, types):
            result.append(os.path.abspath(p))
    return sorted(result)


def find_purgeable_tournaments(folder, date_from=None, date_to=None, types=None):
    """Fichiers .tournoi de `folder` (parcours récursif NON BORNÉ, voir
    database.find_tournament_files) correspondant aux critères Date +
    Type — LES SEULS critères utilisés par "Purger Données" (tout autre
    filtre éventuel de la purge LOG ne s'applique jamais ici).

    ATTENTION (incident réel du 2026-10-08) : cette fonction n'impose
    AUCUNE limite de temps ni de nombre de fichiers — un `folder` vaste
    (ex. un dossier personnel entier) peut la faire tourner indéfiniment.
    "Purger Données" (voir main.py: DataPurgeDialog) ne l'utilise donc
    PLUS pour son interface interactive : elle appelle exclusivement
    scan_candidate_folders_for_purge (BORNÉE, ANNULABLE, exécutée hors du
    thread Tk). Cette fonction-ci reste disponible telle quelle pour un
    usage simple à dossier unique (et ses propres tests), mais ne doit
    JAMAIS être rebranchée directement sur un dossier choisi par
    l'utilisateur sans borne.

    Renvoie une liste de chemins ABSOLUS, triée. `folder` manquant/
    inexistant (ex : aucun "Dossier par défaut" configuré) -> liste
    vide, jamais une exception — même convention défensive que
    backup_restore.find_tournament_files."""
    if not folder or not os.path.isdir(folder) or not types:
        return []
    return filter_tournament_files(find_tournament_files(folder, recursive=True), date_from, date_to, types)


def find_purgeable_tournaments_multi(folders, date_from=None, date_to=None, types=None):
    """Comme find_purgeable_tournaments, mais sur PLUSIEURS dossiers
    candidats à la fois — NON BORNÉE elle non plus (voir find_purgeable_
    tournaments : même avertissement, même raison). Conservée pour un
    usage simple/les tests ; l'interface interactive utilise exclusivement
    scan_candidate_folders_for_purge.

    `folders` doit déjà être la liste des dossiers ACCESSIBLES (voir
    collect_candidate_folders/split_accessible_folders). Le MÊME fichier
    .tournoi découvert depuis plusieurs dossiers candidats (chevauchement
    de dossiers) n'apparaît qu'UNE SEULE fois dans le résultat, jamais en
    double — voir _dedup_abspath.

    Renvoie une liste de chemins ABSOLUS, triée, dédoublonnée."""
    all_files = []
    for folder in folders:
        if folder and os.path.isdir(folder):
            all_files.extend(find_tournament_files(folder, recursive=True))
    return filter_tournament_files(_dedup_abspath(all_files), date_from, date_to, types)


# ---------------------------------------------------------------------
# Scan BORNÉ, ANNULABLE, sans Tkinter (incident macOS du 2026-10-08) :
# ajouter "/Users/raj" (dossier personnel) comme dossier à vérifier a
# bloqué Senaco indéfiniment (curseur arc-en-ciel, forçage de fermeture)
# — database.find_tournament_files/glob.glob("**", recursive=True)
# n'impose AUCUNE limite et tourne sur le thread principal de Tkinter.
# Tout ce qui suit est un mécanisme ENTIÈREMENT SÉPARÉ, dédié à "Purger
# Données" (jamais utilisé par database.py/backup_restore.py, qui
# restent inchangées pour leurs propres usages, sans rapport) :
# - os.scandir, jamais glob/os.walk — contrôle précis, entrée par
#   entrée, de la traversée (limites, liens symboliques, annulation).
# - Ce module reste STRICTEMENT indépendant de Tkinter : aucune des
#   fonctions ci-dessous n'importe ni n'appelle jamais tkinter. C'est à
#   l'appelant (main.py: DataPurgeDialog) d'exécuter ce scan sur un
#   thread séparé et de ne communiquer avec lui que par une structure
#   thread-safe (queue.Queue) — jamais un appel Tk depuis ce thread.
# ---------------------------------------------------------------------

# Dossiers techniques/système JAMAIS descendus pendant un scan récursif
# (en plus de tout dossier dont le nom commence par "." — convention
# large mais sûre : un tournoi n'est jamais rangé dans un dossier
# caché). Liste délibérément explicite plutôt qu'une heuristique floue,
# pour rester auditable et testable. Ne contient AUCUN dossier métier
# (ex. "Vendredi", "samedi"...) : seuls des dossiers reconnus comme
# techniques/système, quel que soit l'endroit où ils apparaissent.
SKIP_DIR_NAMES = frozenset({
    "Library", "Applications", ".Trash", ".cache", ".git", "node_modules",
    "__pycache__", "venv", ".venv", "DerivedData", ".npm", ".cargo",
    ".rustup", ".Spotlight-V100", ".fseventsd", ".DocumentRevisions-V100",
    ".TemporaryItems", ".dropbox.cache",
})

# Limites par défaut (une PREMIÈRE limite atteinte arrête PROPREMENT le
# dossier candidat EN COURS, jamais une exception, jamais les autres
# dossiers candidats déjà listés) — modifiables par l'appelant (les
# tests, notamment, utilisent des valeurs très petites pour forcer une
# troncature de façon déterministe sans dépendre d'un vrai dossier
# volumineux).
DEFAULT_SCAN_MAX_SECONDS = 5.0
DEFAULT_SCAN_MAX_ENTRIES = 20000

SCAN_REASON_MAX_SECONDS = "max_seconds"
SCAN_REASON_MAX_ENTRIES = "max_entries"
SCAN_REASON_CANCELLED = "cancelled"


def scan_folder_for_tournaments(folder, recursive=True, max_seconds=DEFAULT_SCAN_MAX_SECONDS,
                                 max_entries=DEFAULT_SCAN_MAX_ENTRIES, cancel_event=None):
    """Recherche BORNÉE et ANNULABLE des fichiers .tournoi sous `folder`
    (voir la bannière de section ci-dessus pour le contexte complet de
    l'incident du 2026-10-08) — implémentée avec os.scandir, JAMAIS
    glob/os.walk, pour un contrôle précis entrée par entrée.

    - Ne suit JAMAIS un lien symbolique, fichier ou dossier (voir
      DirEntry.is_symlink()) — toujours comptabilisé dans `skipped_dirs`
      quand `recursive` est vrai (sinon ignoré sans even être classifié :
      voir plus bas).
    - En mode récursif, ne descend JAMAIS dans un dossier caché (nom
      commençant par ".") ni dans un dossier listé dans SKIP_DIR_NAMES —
      ajouté à `skipped_dirs`. Les dossiers MÉTIER (ex. "Vendredi",
      "samedi"...) ne sont JAMAIS exclus, y compris ceux du projet
      poker_tournament lui-même (décision explicite du 2026-10-08: il
      contient actuellement de vrais tournois de test) — seuls ses
      éventuels sous-dossiers techniques (ex. ".git") le seraient.
    - `recursive=False` ("Ce dossier uniquement", choix PAR DÉFAUT pour
      un dossier ajouté manuellement — demande explicite du 2026-10-08) :
      n'examine QUE les fichiers directement dans `folder`, ne regarde
      même pas la nature de ses sous-dossiers (jamais classifiés comme
      "ignorés" dans ce mode — ils ne sont simplement pas concernés).
    - S'arrête dès que `max_seconds` (temps écoulé) OU `max_entries`
      (entrées de répertoire visitées) est atteint — la PREMIÈRE limite
      franchie — en conservant les fichiers déjà trouvés, jamais une
      exception. S'arrête aussi immédiatement si `cancel_event.is_set()`
      (vérifié à chaque entrée visitée) — AUCUN appel Tkinter ici, cette
      fonction reste entièrement indépendante de l'interface.

    Renvoie {"files": [...chemins absolus, triés, dédoublonnés...],
             "truncated": bool,  # une limite a été atteinte
             "reason": None | SCAN_REASON_MAX_SECONDS |
                       SCAN_REASON_MAX_ENTRIES | SCAN_REASON_CANCELLED,
             "skipped_dirs": [...chemins absolus, dédoublonnés...]}."""
    if not folder or not os.path.isdir(folder):
        return {"files": [], "truncated": False, "reason": None, "skipped_dirs": []}

    files = []
    skipped_dirs = []
    start = time.monotonic()
    entries_visited = 0
    reason = None

    def _is_technical(name):
        # name.startswith(backup_restore.BACKUP_FOLDER_PREFIX) (demande
        # du 2026-10-08) : un dossier de sauvegarde ("PokerTournament_
        # Backup_...") ne doit JAMAIS être descendu, à AUCUNE profondeur
        # — ses .tournoi sont des COPIES, jamais des tournois actifs à
        # proposer à la purge. Vérifié ici, au même titre que les
        # dossiers techniques/système : s'applique donc à chaque niveau
        # de la récursion, pas seulement au premier.
        return (
            name.startswith(".")
            or name in SKIP_DIR_NAMES
            or name.startswith(backup_restore.BACKUP_FOLDER_PREFIX)
        )

    def _scan_one_dir(path):
        nonlocal entries_visited, reason
        subdirs = []
        try:
            scandir_iter = os.scandir(path)
        except OSError:
            return subdirs
        try:
            for entry in scandir_iter:
                if cancel_event is not None and cancel_event.is_set():
                    reason = SCAN_REASON_CANCELLED
                    break
                entries_visited += 1
                if entries_visited > max_entries:
                    reason = SCAN_REASON_MAX_ENTRIES
                    break
                if time.monotonic() - start > max_seconds:
                    reason = SCAN_REASON_MAX_SECONDS
                    break
                try:
                    is_symlink = entry.is_symlink()
                except OSError:
                    continue
                if is_symlink:
                    if recursive:
                        skipped_dirs.append(entry.path)
                    continue
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if is_dir:
                    if not recursive:
                        continue
                    if _is_technical(entry.name):
                        skipped_dirs.append(entry.path)
                        continue
                    subdirs.append(entry.path)
                    continue
                try:
                    is_file = entry.is_file(follow_symlinks=False)
                except OSError:
                    continue
                if is_file and entry.name.lower().endswith(".tournoi"):
                    files.append(entry.path)
        finally:
            scandir_iter.close()
        return subdirs

    pending = deque([folder])
    while pending and reason is None:
        current = pending.popleft()
        subdirs = _scan_one_dir(current)
        if reason is not None:
            break
        if recursive:
            pending.extend(subdirs)

    return {
        "files": sorted(_dedup_abspath(files)),
        "truncated": reason is not None,
        "reason": reason,
        "skipped_dirs": _dedup_abspath(skipped_dirs),
    }


def scan_candidate_folders(folders, recursive_folders=None, max_seconds=DEFAULT_SCAN_MAX_SECONDS,
                            max_entries=DEFAULT_SCAN_MAX_ENTRIES, cancel_event=None):
    """Scanne CHAQUE dossier de `folders` INDÉPENDAMMENT — chacun avec
    SES PROPRES limites de temps/nombre d'entrées (jamais un budget
    global partagé qui pénaliserait les dossiers suivants si le premier
    est volumineux). `recursive_folders` : ensemble de chemins ABSOLUS
    parmi `folders` à explorer récursivement — tout dossier de `folders`
    absent de cet ensemble est examiné en mode "ce dossier uniquement"
    (voir scan_folder_for_tournaments). None -> tous récursifs (repli
    pratique pour les appels internes/tests qui ne distinguent pas).

    Vérifie `cancel_event` ENTRE deux dossiers (en plus de la
    vérification déjà faite par scan_folder_for_tournaments à
    l'intérieur de chacun) — une annulation pendant le dossier N
    n'entame jamais le scan du dossier N+1.

    Renvoie {"files": [...dédoublonnés, triés...],
             "truncated_folders": {dossier: raison, ...},
             "skipped_dirs": [...dédoublonnés...],
             "cancelled": bool}."""
    recursive_folders = set(folders) if recursive_folders is None else set(recursive_folders)
    all_files = []
    truncated_folders = {}
    all_skipped = []
    cancelled = False
    for folder in folders:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        result = scan_folder_for_tournaments(
            folder, recursive=folder in recursive_folders,
            max_seconds=max_seconds, max_entries=max_entries, cancel_event=cancel_event,
        )
        all_files.extend(result["files"])
        all_skipped.extend(result["skipped_dirs"])
        if result["reason"] == SCAN_REASON_CANCELLED:
            cancelled = True
            break
        if result["truncated"]:
            truncated_folders[folder] = result["reason"]
    return {
        "files": sorted(_dedup_abspath(all_files)),
        "truncated_folders": truncated_folders,
        "skipped_dirs": _dedup_abspath(all_skipped),
        "cancelled": cancelled,
    }


def scan_candidate_folders_for_purge(folders, recursive_folders=None, date_from=None, date_to=None,
                                      types=None, max_seconds=DEFAULT_SCAN_MAX_SECONDS,
                                      max_entries=DEFAULT_SCAN_MAX_ENTRIES, cancel_event=None,
                                      require_date_range=False):
    """Point d'entrée BORNÉ ET ANNULABLE utilisé par l'interface
    interactive de "Purger Données" (main.py: DataPurgeDialog) — JAMAIS
    find_purgeable_tournaments_multi, non bornée (voir l'incident
    macOS du 2026-10-08 : un dossier volumineux comme ~/ bloquait
    indéfiniment l'interface). Combine scan_candidate_folders (recherche
    brute, bornée) et filter_tournament_files (critères Date + Type).

    À exécuter HORS du thread Tk (voir main.py: DataPurgeDialog, qui
    orchestre le threading) — cette fonction elle-même ne touche jamais
    à Tkinter, directement ou indirectement.

    Chaque fichier trouvé est classé UNE SEULE fois (voir
    classify_tournament_for_purge : métadonnées internes "is_sng" EN
    PRIORITÉ, repli sur le préfixe du nom seulement si absent, jamais le
    nom seul — diagnostic complet du 2026-10-08) pour construire à la
    fois `purge_paths` et les deux listes de transparence ci-dessous.

    `require_date_range` (demande explicite du 2026-10-08, "les deux
    dates Du/Au doivent être obligatoires et valides avant toute purge,
    jamais une date vide interprétée comme une période illimitée") :
    quand True, `purge_paths` est FORCÉ vide si `date_from` ou
    `date_to` est None, OU si `date_from > date_to` (période inversée)
    — cette garantie vit ICI, dans la logique métier, pas seulement
    dans la validation de l'interface (main.py: DataPurgeDialog._parse_
    dates) : même appelée directement avec des dates manquantes/
    inversées, cette fonction ne peut JAMAIS renvoyer de fichier
    sélectionné pour suppression. Par défaut (False, INCHANGÉ) :
    comportement historique, None = borne ouverte — préserve
    filter_tournament_files/find_purgeable_tournaments et tous les
    appels existants qui s'appuient délibérément sur une période non
    bornée. La classification/transparence (other_files/
    unreadable_files/skipped_dirs) continue d'être calculée normalement
    même quand require_date_range vide purge_paths — ce n'est qu'une
    sélection vide, jamais un scan sauté.

    Renvoie {"purge_paths": [...après filtre Date/Type...],
             "all_files": [...bruts, avant filtre, dédoublonnés...],
             "other_files": [...classés "Autres / non classés", qu'ils
                              soient cochés ou non — pour que
                              l'utilisateur puisse toujours les
                              identifier (nom/chemin) sans les
                              purger...],
             "unreadable_files": [{"path", "error"}, ...] — fichiers
                              corrompus/illisibles, JAMAIS inclus dans
                              purge_paths quels que soient les types
                              cochés (voir PURGE_TYPE_UNREADABLE),
             "truncated_folders": {...}, "skipped_dirs": [...],
             "cancelled": bool,
             "dates_required": bool — True seulement si
                              require_date_range=True ET que la période
                              fournie est absente ou invalide (inversée) ;
                              sert de preuve testable, indépendante de
                              l'interface, que purge_paths est resté
                              vide pour cette raison précise} — à
    l'appelant de décider si le résultat est exploitable : une
    recherche TRONQUÉE ou ANNULÉE ne doit JAMAIS autoriser la
    vérification de sauvegarde ni la purge, même si des tournois ont
    déjà été trouvés (voir DataPurgeDialog._on_scan_finished)."""
    scan = scan_candidate_folders(
        folders, recursive_folders=recursive_folders, max_seconds=max_seconds,
        max_entries=max_entries, cancel_event=cancel_event,
    )
    dates_required = bool(require_date_range) and (
        date_from is None or date_to is None or date_from > date_to
    )
    types_set = set(types or ())
    purge_paths = []
    other_files = []
    unreadable_files = []
    for path in scan["files"]:
        purge_type, error = classify_tournament_for_purge(path)
        if purge_type == PURGE_TYPE_UNREADABLE:
            unreadable_files.append({"path": path, "error": error})
            continue
        if purge_type == PURGE_TYPE_OTHER:
            other_files.append(os.path.abspath(path))
        if not dates_required and _tournament_matches_criteria(purge_type, path, date_from, date_to, types_set):
            purge_paths.append(os.path.abspath(path))
    return {
        "purge_paths": sorted(_dedup_abspath(purge_paths)),
        "all_files": scan["files"],
        "other_files": sorted(_dedup_abspath(other_files)),
        "unreadable_files": unreadable_files,
        "truncated_folders": scan["truncated_folders"],
        "skipped_dirs": scan["skipped_dirs"],
        "cancelled": scan["cancelled"],
        "dates_required": dates_required,
    }


def _check_candidate_relpath(rel_path, manifest, backup_dir):
    """Si `rel_path` figure dans manifest["tournament_files"] ET que le
    fichier correspondant est physiquement présent dans `backup_dir` :
    renvoie le résultat de couverture (dict), en vérifiant le hash
    SHA-256 si disponible — sinon None (jamais une exception). Factorisé
    hors de _covering_backup_for pour être appelé soit une fois (ancien
    schéma à un seul dossier), soit une fois par dossier candidat
    (nouveau schéma multi-dossiers, voir ci-dessous)."""
    if rel_path not in manifest.get("tournament_files", []):
        return None
    backed_up_path = os.path.join(
        backup_dir, backup_restore.TOURNAMENTS_SUBDIR, *rel_path.split("/")
    )
    if not os.path.isfile(backed_up_path):
        return None
    hashes = manifest.get("tournament_file_hashes") or {}
    expected_hash = hashes.get(rel_path)
    if expected_hash:
        try:
            actual_hash = backup_restore.sha256_of_file(backed_up_path)
        except OSError:
            return None
        if actual_hash != expected_hash:
            return None  # corrompu/modifié : jamais cette sauvegarde pour ce fichier
        return {
            "covered": True, "backup_dir": backup_dir,
            "created_at": manifest.get("created_at"), "hash_verified": True,
        }
    return {
        "covered": True, "backup_dir": backup_dir,
        "created_at": manifest.get("created_at"), "hash_verified": False,
    }


def _covering_backup_for(tournament_path, history_entries):
    """Cherche, parmi `history_entries` (voir backup_history.load_history,
    PAS consultée directement ici — fournie par l'appelant pour rester
    testable sans toucher au vrai ~/.poker_tournament), une sauvegarde
    ENCORE accessible et vérifiable MAINTENANT qui couvre `tournament_path`
    (chemin ABSOLU du fichier .tournoi).

    Le chemin relatif utilisé pour chercher dans le manifeste d'une
    sauvegarde est recalculé ICI, SÉPARÉMENT POUR CHAQUE sauvegarde
    examinée, à partir de SON PROPRE "source_tournament_folder" (celui
    réellement utilisé par backup_restore.create_backup AU MOMENT de
    cette sauvegarde-là, enregistré dans son manifeste) — jamais à
    partir d'un unique dossier de référence global. Deux raisons : (1)
    "Dossier par défaut" peut changer entre deux sauvegardes, un ancien
    manifeste resterait alors interprété avec le dossier ACTUEL, faux ;
    (2) depuis la découverte multi-dossiers (2026-10-07),
    `tournament_path` peut provenir de n'importe lequel des dossiers
    candidats (voir collect_candidate_folders) — pas forcément celui
    réellement sauvegardé. Un manifeste SANS "source_tournament_folder"
    (ne devrait pas arriver pour ce schéma : ce champ est toujours
    écrit par create_backup pour une sauvegarde à un seul dossier) est
    ignoré pour CETTE recherche, jamais une exception.

    ÉTENDU le 2026-10-08 (revue de sécurité "Purger Données" — la
    sauvegarde lancée depuis ce dialogue peut désormais couvrir
    PLUSIEURS dossiers distincts, voir backup_restore.create_backup) :
    si le manifeste porte le nouveau champ ADDITIF "tournament_source_
    folders" (liste ordonnée de dossiers absolus, un par index), on
    essaie CHAQUE dossier de cette liste comme base possible pour
    `tournament_path` — en reconstruisant EXACTEMENT le même préfixe
    "<index>_<nom>/" que celui utilisé à l'écriture (voir backup_
    restore.sanitize_folder_label, PARTAGÉE entre écriture et lecture
    pour qu'elle ne puisse jamais diverger). Un manifeste SANS ce champ
    (toute sauvegarde déjà existante, et la sauvegarde classique du
    Menu principal, toujours à un seul dossier) suit la logique
    HISTORIQUE strictement inchangée ci-dessous — AUCUNE régression
    pour ces sauvegardes.

    Examine les entrées de la plus RÉCENTE à la plus ancienne. Pour
    chacune : le dossier doit encore exister, son manifeste doit rester
    valide (backup_restore.validate_backup_folder), le chemin relatif
    ainsi obtenu doit figurer dans son "tournament_files", et le fichier
    sauvegardé correspondant doit toujours être physiquement présent. Si
    un hash SHA-256 est disponible pour ce fichier dans le manifeste
    (sauvegardes FUTURES à partir de ce chantier), il est RECALCULÉ sur
    la copie sauvegardée et comparé — une différence (corruption/
    modification) REJETTE cette sauvegarde pour ce fichier précis
    (jamais une erreur bloquant tout : on continue de chercher une
    sauvegarde plus ancienne qui pourrait, elle, rester valide). Sans
    hash disponible (ancienne sauvegarde) : acceptée, mais signalée
    comme telle (hash_verified=False) — jamais transformée
    artificiellement en sauvegarde vérifiée par hash.

    Renvoie un dict {"covered", "backup_dir", "created_at",
    "hash_verified"} — "covered"=False et le reste None si aucune
    sauvegarde ne convient."""
    for entry in reversed(history_entries):
        backup_dir = entry.get("backup_dir")
        if not backup_dir or not os.path.isdir(backup_dir):
            continue
        try:
            manifest = backup_restore.validate_backup_folder(backup_dir)
        except backup_restore.BackupError:
            continue

        multi_folders = manifest.get("tournament_source_folders")
        if multi_folders:
            result = None
            for index, folder in enumerate(multi_folders):
                try:
                    rel_within = os.path.relpath(tournament_path, folder).replace(os.sep, "/")
                except ValueError:
                    continue  # ex. lecteurs différents sous Windows -> jamais une exception
                if rel_within.startswith(".."):
                    continue  # tournament_path n'est pas réellement sous ce dossier-là
                label = f"{index}_{backup_restore.sanitize_folder_label(folder)}"
                result = _check_candidate_relpath(f"{label}/{rel_within}", manifest, backup_dir)
                if result is not None:
                    break
            if result is not None:
                return result
            continue

        source_folder = manifest.get("source_tournament_folder")
        if not source_folder:
            continue
        try:
            rel_path = os.path.relpath(tournament_path, source_folder).replace(os.sep, "/")
        except ValueError:
            continue  # ex. lecteurs différents sous Windows -> jamais une exception
        result = _check_candidate_relpath(rel_path, manifest, backup_dir)
        if result is not None:
            return result
    return {"covered": False, "backup_dir": None, "created_at": None, "hash_verified": None}


def check_purge_coverage(tournament_paths, history_entries):
    """Vérifie, pour CHAQUE chemin de `tournament_paths`, qu'il est
    couvert par une sauvegarde encore accessible et vérifiable
    MAINTENANT (voir _covering_backup_for) — jamais une déduction par
    plage de dates globale : une présence fichier par fichier, décidable
    sans ambiguïté (voir le diagnostic : une sauvegarde de juin ne
    "couvre" rien par simple proximité de date, seule la présence RÉELLE
    du fichier compte).

    Ne prend PLUS de paramètre "tournament_folder" unique (retiré le
    2026-10-07, découverte multi-dossiers) : `tournament_paths` peut
    désormais provenir de plusieurs dossiers candidats hétérogènes (voir
    collect_candidate_folders) — _covering_backup_for détermine lui-même
    le dossier de référence pertinent, SÉPARÉMENT POUR CHAQUE sauvegarde
    examinée, à partir du manifeste de CETTE sauvegarde.

    Renvoie {"all_covered": bool, "per_tournament": {chemin: résultat},
    "backups_used": [résultats uniques, les plus récents en tête],
    "uncovered": [chemins non couverts]} — jamais une exception : à
    l'appelant (interface) de décider quoi afficher/bloquer."""
    per_tournament = {}
    for path in tournament_paths:
        per_tournament[path] = _covering_backup_for(path, history_entries)

    uncovered = [p for p, r in per_tournament.items() if not r["covered"]]
    backups_used = []
    seen_dirs = set()
    for r in per_tournament.values():
        if r["covered"] and r["backup_dir"] not in seen_dirs:
            seen_dirs.add(r["backup_dir"])
            backups_used.append(r)
    return {
        "all_covered": not uncovered,
        "per_tournament": per_tournament,
        "backups_used": backups_used,
        "uncovered": uncovered,
    }


def find_open_tournaments(tournament_paths):
    """Sous-ensemble de `tournament_paths` actuellement OUVERT (voir
    open_windows.list_open_paths, même mécanisme déjà utilisé par
    backup_restore.restore_backup) — jamais purgé, voir purge_tournaments
    qui refuse systématiquement si cette liste n'est pas vide."""
    open_paths = {os.path.abspath(p) for p in open_windows.list_open_paths()}
    return [p for p in tournament_paths if os.path.abspath(p) in open_paths]


def purge_tournaments(paths, poker_data_dir=None):
    """Supprime DÉFINITIVEMENT les fichiers .tournoi de `paths` — jamais
    appelée directement par l'interface sans vérification préalable de
    check_purge_coverage()/find_open_tournaments() (voir main.py:
    DataPurgeDialog), mais revérifie elle-même l'absence de tournoi
    ouvert par sécurité (défense en profondeur, jamais un coût réel : un
    aller-retour déjà fait par l'appelant).

    Mécanisme (demande explicite, "jamais une purge partielle") :
    1) Déplace (shutil.move, jamais une copie+suppression séparée) CHAQUE
       fichier vers un dossier de corbeille temporaire dédié à CET appel
       (~/.poker_tournament/pending_purge_<id>/) — un déplacement qui
       échoue laisse le fichier à sa place d'origine, jamais à moitié
       déplacé.
    2) GARDE-FOU FINAL explicite (demande du 2026-10-07) : avant toute
       suppression définitive, vérifie que TOUS les fichiers prévus sont
       bien dans la corbeille ET qu'AUCUN ne subsiste à son emplacement
       d'origine — si cette vérification échoue, RIEN n'est supprimé.
    3) Seulement si cette vérification passe : supprime la corbeille
       (shutil.rmtree) — la SEULE étape réellement irréversible, atteinte
       uniquement après un déplacement intégral et vérifié.
    4) À la moindre erreur (étape 1 ou 2) : ROLLBACK complet — chaque
       fichier déjà déplacé est remis à sa place d'origine — puis
       PurgeError, listant toute erreur de rollback elle-même (rare, mais
       jamais passée sous silence : nécessiterait une intervention
       manuelle).

    Renvoie le nombre de fichiers réellement purgés. Lève PurgeError si
    la purge n'a pas pu être menée à bien — dans tous les cas, AUCUNE
    suppression définitive n'a alors eu lieu (hors cas, signalé dans le
    message, où le rollback lui-même aurait partiellement échoué)."""
    if not paths:
        return 0

    still_open = find_open_tournaments(paths)
    if still_open:
        raise PurgeError(
            "Purge refusée : au moins un tournoi sélectionné est actuellement "
            "ouvert :\n" + "\n".join(still_open) + "\n\nFermez-le avant de purger."
        )

    data_dir = poker_data_dir or backup_restore.default_poker_data_dir()
    os.makedirs(data_dir, exist_ok=True)
    trash_dir = os.path.join(data_dir, f"pending_purge_{uuid.uuid4().hex}")
    os.makedirs(trash_dir)

    moved = {}  # chemin d'origine -> chemin dans la corbeille
    try:
        for index, path in enumerate(paths):
            if not os.path.isfile(path):
                raise PurgeError(f"Fichier introuvable, purge annulée : {path}")
            trash_path = os.path.join(trash_dir, f"{index}_{os.path.basename(path)}")
            shutil.move(path, trash_path)
            moved[path] = trash_path

        # Garde-fou final (demande explicite du 2026-10-07) : AVANT toute
        # suppression définitive de la corbeille, revérifie
        # exhaustivement qu'aucun fichier prévu ne subsiste à son
        # emplacement d'origine et que tous sont bien dans la corbeille.
        for original_path, trash_path in moved.items():
            if os.path.exists(original_path):
                raise PurgeError(
                    f"Incohérence détectée avant suppression définitive : "
                    f"{original_path} existe encore à son emplacement d'origine."
                )
            if not os.path.isfile(trash_path):
                raise PurgeError(
                    f"Incohérence détectée avant suppression définitive : "
                    f"{trash_path} manquant dans la corbeille temporaire."
                )

        shutil.rmtree(trash_dir)
        return len(moved)
    except Exception as e:
        rollback_errors = []
        for original_path, trash_path in moved.items():
            try:
                if os.path.isfile(trash_path) and not os.path.exists(original_path):
                    shutil.move(trash_path, original_path)
            except OSError as rollback_exc:
                rollback_errors.append(f"{trash_path} -> {original_path} : {rollback_exc}")
        try:
            if os.path.isdir(trash_dir) and not os.listdir(trash_dir):
                os.rmdir(trash_dir)
        except OSError:
            pass
        message = f"La purge a échoué, aucune suppression définitive n'a eu lieu : {e}"
        if rollback_errors:
            message += (
                "\n\nATTENTION — erreur(s) pendant la restauration automatique "
                "(intervention manuelle nécessaire) :\n" + "\n".join(rollback_errors)
            )
        raise PurgeError(message) from e
