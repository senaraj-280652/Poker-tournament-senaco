# -*- coding: utf-8 -*-
"""
Registre des fenêtres de tournoi actuellement ouvertes (une par
processus indépendant — voir spawn_app_process dans main.py, chaque
fenêtre/Sit & Go tourne dans son propre processus) : permet au Lobby SNG
de détecter qu'un tournoi de la liste est déjà affiché ailleurs, pour le
ramener au premier plan plutôt que d'en ouvrir une deuxième fenêtre sur
le même fichier .tournoi (ce qui ferait écrire deux processus en même
temps dans le même fichier — source de conflits, surtout sous Windows
où le verrouillage de fichier est plus strict, voir database.py).

Stocké dans ~/.poker_tournament/open_windows.json : {chemin absolu du
.tournoi: {"pid": <pid>}}, un par processus encore vivant. Les entrées
dont le PID ne correspond plus à un processus en cours sont
automatiquement retirées à chaque lecture (_prune) : pas besoin qu'un
processus se ferme proprement pour que son entrée disparaisse (plantage,
"Forcer à quitter"...).

Contient aussi (fichiers séparés, voir leurs docstrings) : la sélection
téléphone du Lobby (set_phone_selected_pid), le verrouillage de session
de "Calculer les primes" (mark_primes_session_started /
primes_session_started, demande du 2026-09-09), l'unicité du "Menu
principal" par ligne prod/test (register_menu_principal/
unregister_menu_principal/menu_principal_pid, demande du 2026-09-14),
et la sécurisation du contrôle à distance (code de session,
anti-bruteforce, approbation persistante des appareils — voir le grand
bloc de commentaires au-dessus de _REMOTE_TEST_CODE, plus bas dans ce
fichier).
"""
import contextlib
import ctypes
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time

# Verrou inter-processus (demande du 2026-09-09, durcissement du
# contrôle à distance — voir _remote_control_lock) : fcntl (POSIX) ou
# msvcrt (Windows), tous deux dans la bibliothèque standard — aucune
# nouvelle dépendance.
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

import export_prefs

# Clé export_prefs de la valeur "proposée" pour "Calculer les primes" —
# voir primes_enabled_proposed/set_primes_enabled_proposed ci-dessous,
# désormais LA source de vérité pour main.py (_primes_enabled_proposed/
# _set_primes_enabled_proposed délèguent ici, ne touchent plus
# export_prefs directement) : centralisée dans CE module pour que la
# vérification "session déjà éteinte -> ignorer/nettoyer" ci-dessous
# s'applique invariablement, quel que soit l'appelant.
PRIMES_ENABLED_PROPOSED_KEY = "primes_enabled_session_proposed"


def primes_enabled_proposed():
    """Valeur "proposée" pour "Calculer les primes" (voir main.py:
    _primes_enabled_proposed, qui délègue ici) — True par défaut.

    AUTO-RÉINITIALISATION (demande du 2026-09-09, point 2 : "nouvelle
    session = primes ON par défaut", CORRIGÉE après un vrai bug détecté
    par tests/test_primes_multi_process_real_subprocess.py — deux VRAIS
    process séparés, pas seulement deux objets en mémoire) : si
    list_open_paths() est actuellement VIDE, la valeur stockée ne peut
    provenir que d'une session déjà éteinte -> ignorée, True renvoyé
    inconditionnellement (et la valeur stockée nettoyée au passage) —
    exactement le même principe que primes_session_started().

    Point CRITIQUE qui justifie cette vérification ICI, à la LECTURE,
    plutôt que de compter uniquement sur le nettoyage fait dans
    register()/unregister() (conservé plus bas comme filet de sécurité
    supplémentaire, jamais LA garantie) : main.py:_choose_tournament_
    file lit cette valeur pour "stamper" un tournoi flambant neuf AVANT
    d'appeler open_windows.register() pour lui (ce nouveau tournoi n'est
    donc pas encore dans le registre au moment de cette lecture). Sans
    la vérification ci-dessous, un tournoi créé comme PREMIÈRE fenêtre
    d'une session neuve lisait à tort l'ancienne valeur de la session
    précédente — le nettoyage de register() n'intervenant, lui, qu'un
    instant trop tard (juste après cette lecture, pas avant)."""
    if not list_open_paths():
        _clear_primes_enabled_proposed()
        return True
    return export_prefs.load_value(PRIMES_ENABLED_PROPOSED_KEY, True) is not False


def set_primes_enabled_proposed(value):
    """Modifie la valeur "proposée" (voir main.py:
    _set_primes_enabled_proposed, qui délègue ici) — à n'appeler que si
    la session n'est pas verrouillée (voir primes_session_started)."""
    try:
        export_prefs.save_value(PRIMES_ENABLED_PROPOSED_KEY, bool(value))
    except OSError:
        pass


def _clear_primes_enabled_proposed():
    """Remet la valeur "proposée" à ON (cochée) — filet de sécurité
    supplémentaire (voir register/unregister ci-dessous, mêmes points
    d'appel que _clear_primes_session_started), en plus de la
    vérification à la lecture ci-dessus qui est LA garantie réelle :
    ne touche jamais une session encore active."""
    try:
        export_prefs.save_value(PRIMES_ENABLED_PROPOSED_KEY, True)
    except OSError:
        pass


def _atomic_write_json(path, data):
    """Écrit `data` (JSON) dans `path` de façon atomique : écrit d'abord
    dans un fichier temporaire distinct (créé dans LE MÊME dossier que
    `path`, pour que le remplacement final reste sur le même système de
    fichiers — un rename entre deux volumes différents échouerait), le
    referme explicitement AVANT le remplacement (nécessaire sous
    Windows : un fichier encore ouvert ne peut pas toujours être
    renommé/remplacé), puis os.replace() — un remplacement atomique
    aussi bien sous macOS/Linux (rename()) que Windows (MoveFileEx),
    contrairement à une écriture directe sur `path` : un crash pendant
    celle-ci pouvait laisser un JSON tronqué/corrompu (voir _load, qui
    devait alors retomber sur {} — perdant du même coup tout le registre
    partagé, y compris les entrées d'AUTRES tournois déjà ouverts).

    Nom de fichier temporaire propre à CET appel (tempfile.mkstemp, pid +
    suffixe aléatoire inclus) : deux processus qui écriraient au même
    instant n'utilisent jamais le même fichier temporaire, seul le
    remplacement final de `path` peut se chevaucher — et reste alors
    "dernier arrivé gagne" de façon propre (jamais un mélange des deux
    écritures), exactement comme avant, jamais pire.

    Ne masque PAS les erreurs (contrairement aux appelants, qui les
    avalent volontairement comme avant ce correctif) : nettoie le
    fichier temporaire si quoi que ce soit échoue avant le remplacement,
    puis relève, pour laisser l'appelant décider."""
    directory = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _registry_path():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "open_windows.json")


def _registry_lock_path():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "open_windows.lock")


@contextlib.contextmanager
def _registry_lock(timeout=5.0):
    """Verrou inter-processus (demande du 2026-09-14, suite à un cas réel
    observé sur un poste Windows : primes_session_started.json disparu
    alors qu'un tournoi restait vivant dans open_windows.json) autour de
    TOUTE opération de lecture-décision-écriture portant sur open_
    windows.json ET/OU primes_session_started.json ensemble — les deux
    doivent être considérés comme UNE SEULE section critique, puisque la
    décision "plus aucun tournoi vivant -> effacer primes_session_
    started.json" dépend d'une lecture de open_windows.json : sans
    verrou commun, un process pouvait lire ce registre à un instant où
    un AUTRE process était au milieu de sa propre mise à jour (ou
    inversement), lire une image transitoirement incohérente, et en
    tirer à tort la conclusion "plus rien n'est ouvert".

    Réutilise le MÊME mécanisme déjà éprouvé pour le contrôle à distance
    (voir _file_lock, extrait de l'ancien corps de _remote_control_lock
    lors de ce même correctif — jamais une architecture différente) :
    verrou de fichier natif de l'OS (fcntl.flock/msvcrt.locking),
    automatiquement relâché par le système même après un plantage brutal
    — fichier de verrou DISTINCT de celui du contrôle à distance
    (open_windows.lock, jamais remote_control.lock) : ce sont deux
    sections critiques indépendantes, inutile de les faire attendre
    l'une l'autre.

    Voir les variantes "_locked" (register/unregister ci-dessous,
    _read_primes_session_data_locked, mark_primes_session_started) pour
    enchaîner plusieurs opérations SANS ré-acquérir ce verrou (fcntl.
    flock/msvcrt.locking ne sont PAS ré-entrants — un second appel
    imbriqué bloquerait sur son propre verrou jusqu'au timeout, voir la
    même précaution déjà documentée pour _remote_control_lock/_ensure_
    remote_session_auth_locked)."""
    with _file_lock(_registry_lock_path(), timeout=timeout):
        yield


def _win32_open_process_handle(pid):
    """Encapsule l'appel Windows brut (ctypes.windll.kernel32.
    OpenProcess/CloseHandle) dans sa propre fonction MODULE-LEVEL
    (demande du 2026-09-14, durcissement suite à un cas réel où
    primes_session_started.json a disparu alors qu'un tournoi restait
    vivant) — pour deux raisons :

    1. Rester substituable dans les tests même sur une machine non-
       Windows : ctypes.windll n'existe tout simplement pas hors
       Windows, impossible d'y patcher quoi que ce soit directement ;
       cette fonction, elle, existe sur toutes les plateformes et peut
       être remplacée par une doublure (voir tests/test_pid_is_running_
       windows.py) pour exercer la logique de _pid_is_running ci-dessous
       sans jamais réellement appeler l'API Windows.
    2. Isoler l'appel brut de toute interprétation : renvoie le handle
       tel quel (int non nul si le process existe, 0/None si OpenProcess
       échoue proprement), ou laisse remonter TELLE QUELLE toute
       exception inattendue — jamais convertie ici en "process mort".
       C'est _pid_is_running, l'appelant, qui décide quoi faire d'une
       exception (voir sa docstring : jamais assimilée à une preuve de
       mort du process)."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
    return handle


def _pid_is_running(pid):
    """Vrai si un processus portant cet identifiant existe encore sur
    cette machine — utilisé pour ignorer/retirer une entrée laissée par
    un processus disparu sans se désinscrire proprement (voir
    App._on_close / App._cleanup_for_close).

    CORRECTION du 2026-09-14 (cas réel observé sur un poste Windows :
    primes_session_started.json disparu alors qu'un tournoi restait
    vivant dans open_windows.json — diagnostic du même jour) : la
    branche Windows n'avait AUCUN `try/except` autour de l'appel API
    brut, contrairement à la branche POSIX juste en dessous (qui, elle,
    distingue explicitement "process introuvable" de "erreur qui ne
    prouve rien" — voir `except PermissionError: return True`). Toute
    erreur Windows autre qu'un OpenProcess proprement refusé (handle nul)
    — panne transitoire, antivirus, etc. — remonte maintenant comme une
    exception plutôt que d'être silencieusement interprétée comme "mort".

    RÈGLE DÉLIBÉRÉE, la même des deux côtés (Windows et POSIX) : en cas
    de doute (exception/erreur qui ne prouve RIEN), on considère le
    process VIVANT, jamais mort. Une entrée fantôme qui traîne un peu
    plus longtemps qu'idéal est sans conséquence (elle disparaîtra au
    prochain contrôle réussi) ; à l'inverse, déclarer à tort un
    process mort peut faire croire à tort que "plus aucun tournoi n'est
    ouvert" et effacer primes_session_started.json/open_windows.json
    alors qu'un tournoi tourne toujours — l'incident qui a motivé ce
    correctif. Mieux vaut échouer du côté prudent."""
    if not isinstance(pid, int):
        return False
    if sys.platform == "win32":
        try:
            handle = _win32_open_process_handle(pid)
        except Exception:
            return True
        return bool(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # existe bien, appartient à un autre utilisateur
    except OSError:
        return False
    return True


def _load():
    path = _registry_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data):
    try:
        _atomic_write_json(_registry_path(), data)
    except OSError:
        pass


def _prune(data):
    """Retire du dict (en place) les entrées dont le processus n'existe
    plus. Renvoie `data` pour chaînage."""
    for key in list(data.keys()):
        entry = data.get(key) or {}
        if not _pid_is_running(entry.get("pid")):
            del data[key]
    return data


def register(path):
    """Enregistre le processus courant comme affichant `path` — appelé
    une fois le tournoi effectivement ouvert (voir App.__init__).

    `registered_at` (epoch) : horodatage de CETTE ouverture, indépendant
    de tout ce qui touche au contrôle à distance — sert uniquement à
    déterminer le tournoi le plus RÉCEMMENT ouvert parmi ceux joignables
    depuis un téléphone (voir remote_control.py: resolve_current_pid),
    jamais modifié ensuite par update_remote_info (simple merge, voir sa
    docstring) : reprendre plus tard le port 8765 (voir App._maybe_
    reclaim_default_remote_port) ne doit jamais faire passer un tournoi
    pour "le plus récent" à sa place.

    Nettoyage robuste du verrou de session "Calculer les primes" (demande
    du 2026-09-09) : si, une fois les PID morts purgés (_prune ci-dessus),
    il ne restait PLUS AUCUNE fenêtre vivante de l'éventuelle ancienne
    session, tout drapeau `primes_session_started.json` résiduel ne peut
    appartenir qu'à cette session déjà éteinte (ex. dernier processus
    disparu par plantage plutôt que par unregister propre, voir la même
    précaution symétrique dans unregister ci-dessous) — nettoyé ICI, AVANT
    d'ajouter cette nouvelle fenêtre, pour qu'un "started=true" périmé ne
    puisse jamais survivre jusqu'à la nouvelle session qui commence avec
    cet enregistrement. `primes_session_started()` fait déjà ce même
    contrôle paresseusement à chaque lecture (filet de sécurité), mais on
    ne veut pas dépendre du hasard d'un futur appel.

    Même précaution pour la valeur "proposée" de "Calculer les primes"
    (demande du 2026-09-09, point 2) : si elle avait été laissée à OFF
    par une session précédente entièrement close SANS que unregister()
    n'ait pu la remettre à ON lui-même (même scénario de plantage
    ci-dessus), remise à ON ICI aussi, avant d'ajouter cette nouvelle
    fenêtre — voir _clear_primes_enabled_proposed.

    Même précaution pour le code/jeton de session du contrôle à distance
    ET les compteurs anti-bruteforce associés (demande du 2026-09-09,
    durcissement du contrôle à distance) : nettoyés ICI aussi, avant
    d'ajouter cette nouvelle fenêtre — voir _clear_remote_control_
    session_files, qui protège cette opération avec le même verrou
    inter-processus que la génération/consultation de ces fichiers.

    Tout le corps ci-dessous tourne désormais sous _registry_lock()
    (demande du 2026-09-14) : la lecture de _load(), la décision "data
    est-il vide ?", et l'écriture finale forment UNE SEULE section
    critique — sans ce verrou, un autre process au même instant (son
    propre register()/unregister()) pouvait lire la même image
    "d'avant", chacun décidant indépendamment, la seconde écriture
    écrasant silencieusement la première (perte de mise à jour
    confirmée expérimentalement lors du diagnostic de ce correctif)."""
    if not path:
        return
    with _registry_lock():
        data = _prune(_load())
        if not data:
            _clear_primes_session_started()
            _clear_primes_enabled_proposed()
            _clear_remote_control_session_files()
        data[os.path.abspath(path)] = {"pid": os.getpid(), "registered_at": time.time()}
        _save(data)


def try_register(path):
    """Réservation ATOMIQUE d'un fichier .tournoi AVANT même de l'ouvrir
    (demande du 2026-09-16, "interdire l'ouverture simultanée du même
    .tournoi") — à appeler à la place de register() PAR-DESSUS `Database
    (path)`, jamais après : contrairement à register() (qui écrase
    toujours inconditionnellement, pensé pour "je viens d'ouvrir ce
    fichier, enregistre-moi"), try_register() REFUSE si `path` est déjà
    présent dans le registre, quel que soit le PID propriétaire — MÊME
    LE SIEN. Aucune exception "même processus" : dans ce logiciel,
    chaque fenêtre de tournoi tourne dans son propre processus, et le
    seul cas où un même processus rouvrirait un chemin (App.
    _new_tournament) libère déjà explicitement l'ancien via unregister()
    AVANT de recommencer — un chemin encore présent au moment de
    try_register() signifie donc toujours "réellement déjà ouvert
    ailleurs", jamais une re-confirmation légitime.

    Renvoie :
    - None si la réservation a réussi (l'appelant peut ouvrir Database()
      en toute sécurité, PUIS ne doit PAS rappeler register() ensuite :
      cette fonction a déjà tout fait, y compris le nettoyage de session
      "premier tournoi du registre" — voir register() ci-dessus, dont
      cette fonction reprend exactement les mêmes précautions) ;
    - le PID (int) du processus qui détient déjà `path` sinon — l'appelant
      doit alors renoncer à ouvrir ce fichier (voir open_windows.
      bring_pid_to_front) et JAMAIS appeler Database(path).

    Atomique par construction : lecture (_prune/_load), décision (déjà
    pris ?) et écriture forment UNE SEULE section critique sous
    _registry_lock() (verrou de fichier natif de l'OS, inter-processus,
    voir sa docstring) — deux processus qui appellent try_register() sur
    LE MÊME chemin quasi simultanément se sérialisent sur ce verrou :
    le second relit alors une image à jour incluant la réservation du
    premier, et se voit refuser. Jamais de fenêtre de course entre "lire
    l'état" et "écrire ma réservation", contrairement à l'ancien schéma
    (find_open_pid() dans un process, register() bien plus tard dans un
    AUTRE — voir LobbyDialog._open_selected, qui garde son propre
    find_open_pid() comme confort UX — "basculer vers" au lieu d'un
    message d'erreur — mais n'est plus la seule protection réelle).

    Récupération après un crash : entièrement héritée de _prune()/
    _pid_is_running (aucun code supplémentaire nécessaire) — un
    processus mort libère automatiquement son entrée dès le PROCHAIN
    appel (à ce chemin ou À N'IMPORTE QUEL AUTRE, puisque _prune()
    nettoie tout le dict à chaque lecture)."""
    if not path:
        return None
    with _registry_lock():
        data = _prune(_load())
        abs_path = os.path.abspath(path)
        existing = data.get(abs_path)
        if existing:
            return existing.get("pid")
        if not data:
            _clear_primes_session_started()
            _clear_primes_enabled_proposed()
            _clear_remote_control_session_files()
        data[abs_path] = {"pid": os.getpid(), "registered_at": time.time()}
        _save(data)
        return None


def update_remote_info(path, port, name):
    """Complète l'entrée de ce processus avec les infos nécessaires au
    Lobby de la page "Contrôle à distance" du téléphone (voir
    remote_control.py) : le port effectivement utilisé par SON PROPRE
    serveur de contrôle à distance (peut différer du port par défaut
    8765 si un autre processus/tournoi l'a pris en premier — voir
    RemoteControlServer.start) et le nom affiché sur le bouton du
    Lobby. Appelé à chaque démarrage du contrôle à distance (voir
    App._start_remote_control_if_enabled). Sans effet si `path` n'est
    pas (ou plus) enregistré par ce processus."""
    if not path:
        return
    data = _prune(_load())
    abs_path = os.path.abspath(path)
    entry = data.get(abs_path)
    if entry and entry.get("pid") == os.getpid():
        entry["remote_port"] = port
        entry["tournament_name"] = name
        _save(data)


def list_remote_tournaments():
    """Tournois actuellement ouverts (tous processus vivants confondus)
    dont le contrôle à distance est actif — pour le Lobby de la page
    "Contrôle à distance" (voir remote_control.py) : ce module n'a
    besoin de rien d'autre que ce simple registre partagé sur disque
    pour savoir joindre (en local, sur 127.0.0.1) le serveur de
    n'importe quel autre tournoi ouvert, même depuis le port d'un
    tournoi différent. Chaque entrée : {"pid", "port", "name",
    "registered_at"} — ce dernier sert à remote_control.py:
    resolve_current_pid à déterminer le tournoi le plus récemment
    ouvert (0 par défaut pour une entrée héritée d'avant ce champ, donc
    jamais considérée "la plus récente" face à une vraie ouverture)."""
    data = _prune(_load())
    result = []
    for entry in data.values():
        port = entry.get("remote_port")
        if port:
            result.append({
                "pid": entry.get("pid"),
                "port": port,
                "name": entry.get("tournament_name") or "Tournoi",
                "registered_at": entry.get("registered_at", 0),
            })
    return result


def unregister(path):
    """Retire l'entrée de `path` si elle appartient au processus courant
    (par précaution, pour ne jamais effacer par erreur celle d'un autre
    processus en cas de course) — voir App._cleanup_for_close.

    Nettoyage robuste du verrou de session "Calculer les primes" (demande
    du 2026-09-09) : si ce tournoi était le DERNIER de la session
    (registre vide une fois cette entrée retirée), le drapeau
    `primes_session_started.json` n'a plus lieu d'exister — nettoyé
    immédiatement ici, sans attendre un éventuel futur appel à
    primes_session_started() qui pourrait ne jamais avoir lieu si aucune
    autre fenêtre ne se rouvre avant longtemps (voir la même précaution
    symétrique dans register ci-dessus, et le filet de sécurité
    paresseux déjà présent dans primes_session_started elle-même).

    Même chose pour la valeur "proposée" (demande du 2026-09-09, point 2,
    "nouvelle session = primes ON par défaut") : remise à ON dès la
    fermeture du DERNIER tournoi de la session, sans affecter en rien la
    session en cours tant qu'il en reste au moins un ouvert — voir
    _clear_primes_enabled_proposed.

    Même chose pour le code/jeton de session du contrôle à distance et
    les compteurs anti-bruteforce (demande du 2026-09-09) : voir
    _clear_remote_control_session_files.

    Sous _registry_lock() (demande du 2026-09-14) — même raison que
    register() ci-dessus : lecture, retrait, décision "reste-t-il
    quelque chose ?" et nettoyage éventuel forment UNE SEULE section
    critique, jamais entrelacée avec le register()/unregister() d'un
    autre process."""
    if not path:
        return
    with _registry_lock():
        data = _prune(_load())
        abs_path = os.path.abspath(path)
        entry = data.get(abs_path)
        if entry and entry.get("pid") == os.getpid():
            del data[abs_path]
            _save(data)
            if not data:
                _clear_primes_session_started()
                _clear_primes_enabled_proposed()
                _clear_remote_control_session_files()


def find_open_pid(path):
    """PID du processus qui a actuellement `path` ouvert, ou None si
    aucun (fichier pas ouvert ailleurs, ou son processus a disparu sans
    se désinscrire proprement) — utilisé par le Lobby SNG pour éviter
    d'ouvrir une deuxième fenêtre sur le même tournoi."""
    if not path:
        return None
    data = _prune(_load())
    entry = data.get(os.path.abspath(path))
    return entry.get("pid") if entry else None


def list_open_paths():
    """Chemins absolus de tous les fichiers .tournoi actuellement ouverts
    (un par processus vivant), quel que soit leur dossier — utilisé par
    le Lobby SNG pour toujours lister les tournois en cours même s'ils
    ne sont pas dans le dossier actuellement affiché (voir
    LobbyDialog._refresh)."""
    data = _prune(_load())
    return list(data.keys())


def find_path_for_pid(pid):
    """Chemin .tournoi actuellement ouvert par ce pid, ou None (pid
    inconnu ou son processus a disparu) — utilisé par LobbyDialog pour
    retrouver quel tournoi sélectionner à l'écran à partir d'un pid choisi
    depuis un téléphone (voir get_phone_selected_pid)."""
    if not isinstance(pid, int):
        return None
    data = _prune(_load())
    for path, entry in data.items():
        if entry.get("pid") == pid:
            return path
    return None


def _phone_selection_path():
    return os.path.join(os.path.expanduser("~"), ".poker_tournament", "phone_selected_pid.json")


def set_phone_selected_pid(pid):
    """Mémorise le pid du tournoi actuellement choisi par UN téléphone
    dans la page "Lobby" du contrôle à distance (voir /select_tournament
    dans remote_control.py) — pour que toute fenêtre "Lobby" Mac
    (LobbyDialog, main.py) ouverte à ce moment-là, quel que soit le
    processus qui l'héberge, puisse aligner sa sélection visuelle sur ce
    même tournoi (voir get_phone_selected_pid / LobbyDialog._refresh).

    Fichier SÉPARÉ du registre principal (open_windows.json) : ce
    dernier associe à chaque clé (un chemin de fichier .tournoi) un
    dict {"pid": ...} que _prune() relit systématiquement en boucle — y
    mélanger une clé spéciale non-chemin casserait cette hypothèse.

    Peut être appelée directement depuis le thread du serveur de
    contrôle à distance (comme on_upload_photo/get_photo_image dans
    remote_control.py) : simple écriture de fichier, ne touche ni
    self.db (SQLite) ni Tkinter."""
    try:
        _atomic_write_json(_phone_selection_path(), {"pid": pid})
    except OSError:
        pass


def get_phone_selected_pid():
    """Dernier pid choisi par un téléphone (voir set_phone_selected_pid),
    ou None si jamais renseigné ou fichier illisible/absent. Ne vérifie
    PAS que ce pid correspond encore à un processus vivant : l'appelant
    (LobbyDialog) le fait déjà lui-même via find_path_for_pid, en gardant
    simplement la sélection existante si ce pid ne correspond plus à
    rien — jamais de sélection "par défaut" à sa place."""
    path = _phone_selection_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None
    pid = data.get("pid") if isinstance(data, dict) else None
    return pid if isinstance(pid, int) else None


def _primes_session_lock_path():
    return os.path.join(os.path.expanduser("~"), ".poker_tournament", "primes_session_started.json")


def mark_primes_session_started(primes_enabled=True):
    """Verrouille "Calculer les primes" pour TOUTE la session actuelle,
    à la valeur `primes_enabled` (bool) — appelée UNE SEULE FOIS pour de
    bon par session (voir main.py:_clock_resume, appelée à chaque
    transition clock_started 0->1) : dès que la session est déjà
    verrouillée (primes_session_started() vrai), un appel ultérieur
    (ex. un DEUXIÈME tournoi de la session qui démarre à son tour)
    N'A AUCUN EFFET — la valeur du tout premier tournoi démarré reste
    l'unique valeur AUTHORITATIVE pour toute la session, jamais
    réécrite par un second démarrage.

    CORRECTION du 2026-09-09 (4e relecture utilisateur, "ouvrir un
    tournoi EXISTANT après verrouillage garde à tort son ancienne
    valeur") : mémorise désormais la valeur elle-même, pas seulement le
    booléen "un tournoi a démarré" — voir locked_primes_enabled(), qui
    la restitue. Sans cette valeur explicite et persistée, aligner un
    tournoi (nouveau ou existant) rejoignant une session déjà verrouillée
    devait se rabattre sur la valeur "proposée" (export_prefs), qui peut
    être remise à tort à ON par erreur si le registre passe par un état
    temporairement vide (ex. App._new_tournament/_open_tournament :
    fenêtre unique fermée puis immédiatement remplacée par une autre,
    dans le MÊME process — voir open_windows.register/unregister) —
    cette valeur-ci, elle, ne peut plus jamais être perdue une fois
    posée, tant que la session reste active (voir primes_session_
    started, qui la nettoie exactement au même moment que le drapeau).

    Fichier SÉPARÉ du registre principal (open_windows.json) — même
    raison que _phone_selection_path : celui-ci associe à chaque clé (un
    chemin de fichier .tournoi) un dict {"pid": ...} que _prune() relit
    systématiquement en boucle, y mélanger une clé non-chemin le
    casserait.

    Sous _registry_lock() (demande du 2026-09-14) : le contrôle "déjà
    verrouillée ?" et l'écriture qui suit forment une seule section
    critique — via _read_primes_session_data_locked (PAS la fonction
    publique primes_session_started(), qui ré-acquerrait ce même verrou
    non ré-entrant, voir sa docstring)."""
    with _registry_lock():
        if _read_primes_session_data_locked().get("started"):
            return  # déjà verrouillée : la valeur du tout premier reste seule autoritative
        try:
            _atomic_write_json(
                _primes_session_lock_path(), {"started": True, "primes_enabled": bool(primes_enabled)}
            )
        except OSError:
            pass


def _clear_primes_session_started():
    try:
        os.remove(_primes_session_lock_path())
    except OSError:
        pass


def _read_primes_session_data_locked():
    """Variante SANS verrou de _read_primes_session_data — le verrou
    (_registry_lock) doit déjà être détenu par l'appelant (voir mark_
    primes_session_started ci-dessus, qui a besoin d'enchaîner cette
    lecture avec une écriture conditionnelle dans LA MÊME section
    critique, sans jamais ré-acquérir le verrou : fcntl.flock/msvcrt.
    locking ne sont PAS ré-entrants — un second appel imbriqué de
    _registry_lock() depuis le même thread bloquerait sur son propre
    verrou jusqu'au timeout, même précaution que _ensure_remote_
    session_auth_locked plus bas dans ce fichier). N'appeler qu'à
    l'intérieur d'un `with _registry_lock():` déjà ouvert.

    {} si absent/illisible/session terminée (registre vide)."""
    if not list_open_paths():
        _clear_primes_session_started()
        return {}
    path = _primes_session_lock_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_primes_session_data():
    """Point d'entrée PUBLIC (acquiert lui-même _registry_lock) — voir
    _read_primes_session_data_locked pour la variante réutilisable à
    l'intérieur d'une section déjà verrouillée, et sa docstring pour le
    détail du contenu renvoyé."""
    with _registry_lock():
        return _read_primes_session_data_locked()


def primes_session_started():
    """True si un tournoi de la session ACTUELLE a déjà démarré son
    chronomètre à un moment donné, ET qu'il reste encore au moins un
    tournoi ouvert depuis (n'importe lequel — pas forcément celui qui a
    démarré : voir la demande du 2026-09-09, exemple OPEN+Sit&Go où
    l'OPEN démarre puis se ferme alors que le Sit&Go, jamais démarré,
    reste ouvert -> doit rester verrouillé).

    Dès que list_open_paths() (déjà nettoyé des PID morts par _prune,
    donc robuste à un plantage — voir aussi _pid_is_running, durci le
    2026-09-14 pour ne jamais confondre "process réellement mort" et
    "erreur de détection transitoire") devient complètement vide, la
    session est considérée TERMINÉE : ce drapeau est alors ignoré (et le
    fichier supprimé) pour la session SUIVANTE. En pratique, ce
    nettoyage a déjà eu lieu ACTIVEMENT, dès l'instant précis où le
    dernier tournoi s'est fermé (voir unregister) ou dès l'enregistrement
    du premier tournoi d'une session suivante si un résidu avait survécu
    (voir register, ex. dernier processus disparu par plantage plutôt
    que par unregister propre) — cette vérification ici n'est qu'un
    filet de sécurité supplémentaire (défense en profondeur), jamais le
    seul mécanisme de nettoyage : aucune valeur `started=true` périmée
    ne peut donc survivre jusqu'à une session suivante, peu importe
    qui/quand relit ce drapeau en premier."""
    return bool(_read_primes_session_data().get("started"))


def locked_primes_enabled(default=True):
    """Valeur de "Calculer les primes" VERROUILLÉE pour la session
    actuelle (demande du 2026-09-09, 4e relecture) — `default` si la
    session n'est PAS verrouillée (primes_session_started() faux) : cet
    appel n'a alors aucun sens, l'appelant doit toujours vérifier
    primes_session_started() lui-même avant de s'y fier (voir main.py:
    _align_primes_enabled_on_open, seul appelant prévu)."""
    data = _read_primes_session_data()
    if not data.get("started"):
        return default
    return bool(data.get("primes_enabled", default))


# =======================================================================
# Unicité du "Menu principal" (demande du 2026-09-14) : un double-clic
# accidentel sur le raccourci Windows/macOS lançait un second processus
# Senaco totalement indépendant (nouvel écran "Bienvenue", nouvelle
# session), même si un premier tournoi était déjà affiché — au lieu de
# simplement ramener au premier plan la fenêtre déjà ouverte.
#
# Portée volontairement ÉTROITE (voir la docstring de main.py:
# App.__init__ pour le câblage complet) : ce verrou ne concerne QUE les
# lancements SANS argument de fichier .tournoi ET sans le marqueur
# POKER_TOURNAMENT_INTERNAL_LAUNCH (voir main.py: spawn_app_process) —
# donc jamais les tournois ouverts depuis le Lobby (spawn_app_process(
# [path])), ni les fenêtres supplémentaires ouvertes depuis "🏠 Menu
# principal" (spawn_app_process(internal_menu_child=True)), qui doivent
# rester illimités. AUCUN rapport avec open_windows.json (registre des
# tournois) ni primes_session_started.json ci-dessus : fichier séparé,
# clé de verrouillage différente.
#
# Une clé par "produit" (`key`, ex. "prod"/"test" — voir main.py: _is_
# test_build) : Poker Senaco et Poker Senaco TEST doivent pouvoir
# cohabiter, chacun avec sa PROPRE instance unique de Menu principal,
# sans jamais se bloquer l'un l'autre — un fichier distinct par clé.
#
# Même idiome que primes_session_started.json ci-dessus : le PID
# enregistré est revérifié À LA LECTURE (menu_principal_pid, via
# _pid_is_running — déjà cross-plateforme Windows/macOS/POSIX) plutôt
# que de faire confiance à un drapeau statique — un ancien verrou laissé
# par un plantage est donc automatiquement ignoré et nettoyé dès la
# prochaine tentative de lancement, sans jamais bloquer durablement un
# redémarrage.

def _menu_principal_lock_path(key):
    return os.path.join(
        os.path.expanduser("~"), ".poker_tournament", f"menu_principal_{key}.json",
    )


def register_menu_principal(pid, key):
    """Enregistre `pid` comme détenant l'unique "Menu principal" de la
    ligne `key` (ex. "prod"/"test") — appelé UNE FOIS, au tout début
    d'App.__init__, uniquement pour un lancement SANS fichier .tournoi
    ET sans le marqueur d'appel interne (voir le bloc de commentaires
    ci-dessus). À libérer explicitement via unregister_menu_principal
    (voir App._cleanup_for_close) une fois ce processus fermé — jamais
    remplacé silencieusement tant que ce PID reste vivant (voir
    menu_principal_pid, seul lecteur)."""
    try:
        _atomic_write_json(_menu_principal_lock_path(key), {"pid": pid})
    except OSError:
        pass


def unregister_menu_principal(pid, key):
    """Retire le verrou de la ligne `key` SEULEMENT s'il appartient
    encore à `pid` (même précaution que unregister() pour open_windows.
    json : ne jamais effacer par erreur celui d'un processus PLUS
    RÉCENT que soi, en cas de course improbable)."""
    path = _menu_principal_lock_path(key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and data.get("pid") == pid:
        try:
            os.remove(path)
        except OSError:
            pass


def menu_principal_pid(key):
    """PID détenant actuellement l'unique "Menu principal" de la ligne
    `key`, ou None si aucun (jamais lancé, déjà fermé proprement, ou
    verrou périmé). Auto-nettoyant : si le PID enregistré n'est PLUS
    vivant (_pid_is_running, voir sa docstring pour le détail Windows/
    POSIX), le fichier est supprimé ici même — un plantage ne peut donc
    jamais empêcher durablement un futur lancement de reprendre la
    main, exactement comme primes_session_started() ci-dessus."""
    path = _menu_principal_lock_path(key)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int):
        return None
    if not _pid_is_running(pid):
        try:
            os.remove(path)
        except OSError:
            pass
        return None
    return pid


def bring_pid_to_front(pid):
    """Ramène au premier plan la fenêtre du processus `pid` (best-effort,
    silencieux en cas d'échec — ex : permission Accessibilité macOS non
    accordée, ou plateforme non gérée).

    Sous macOS, "frontmost of process" ne suffit pas toujours à faire
    remonter visuellement la fenêtre Tk au-dessus des autres (le
    processus devient actif mais sa fenêtre peut rester derrière) : on
    tente en plus un AXRaise (Accessibilité) sur sa fenêtre principale
    visible, qui remonte explicitement la fenêtre elle-même. L'activation
    du process reste faite en premier et inconditionnellement, donc si
    AXRaise échoue (permission Accessibilité absente, élément introuvable,
    etc. — voir échec initial osascript -1719), le comportement retombe
    silencieusement sur l'activation seule (comportement actuel).

    La résolution du process cible se fait par une boucle manuelle sur
    `every process` plutôt que par `first process whose unix id is
    {pid}` : ce filtre "whose" de System Events s'est avéré peu fiable
    quand plusieurs process partagent le même nom/bundle (ex. deux
    fenêtres de CETTE appli, toutes deux "Python"/org.python.python) —
    vérifié en le voyant renvoyer le mauvais process (celui d'un AUTRE
    tournoi déjà ouvert) alors qu'une comparaison manuelle de `unix id`
    élément par élément trouve, elle, systématiquement le bon. C'est ce
    qui provoquait le "Menu principal" (fenêtre "Bienvenue", nouveau
    process lancé depuis un tournoi déjà ouvert) ramené derrière
    l'ancien tournoi presque aussitôt après son apparition : les
    tentatives successives de raise_process_when_ready ciblaient en
    réalité, à cause de ce bug, l'ancien tournoi plutôt que la nouvelle
    fenêtre.

    `try` autour de CHAQUE comparaison `unix id of p` (pas seulement
    autour du bloc AXRaise plus bas) : juste après qu'un ou plusieurs
    tournois se soient fermés (notamment via "Fin de la partie" depuis
    le téléphone — voir remote_control.py), leur process peut rester
    listé par System Events pendant un court instant sous une forme
    "fantôme" dont certaines propriétés (dont `unix id`) lèvent une
    erreur AppleScript à la lecture. Sans ce `try`, cette seule erreur
    interrompait TOUT le `repeat` (AppleScript ne continue pas après une
    erreur non rattrapée) avant même d'avoir atteint le vrai process
    cible plus loin dans la liste — symptôme observé : "Menu principal"
    lance bien une nouvelle fenêtre "Bienvenue" (le process existe,
    voir raise_process_when_ready) mais ne parvient plus jamais à la
    ramener au premier plan, reproductible juste après avoir fermé
    plusieurs tournois depuis le téléphone. Un process qui lève ainsi
    est simplement ignoré (ni lui ni personne ne "gagne" cette
    itération), la recherche continue normalement avec le suivant."""
    if sys.platform == "darwin":
        script = (
            'tell application "System Events"\n'
            '    set targetProc to missing value\n'
            '    repeat with p in (every process)\n'
            '        try\n'
            f'            if unix id of p is {pid} then\n'
            '                set targetProc to p\n'
            '                exit repeat\n'
            '            end if\n'
            '        end try\n'
            '    end repeat\n'
            '    if targetProc is not missing value then\n'
            '        set frontmost of targetProc to true\n'
            '        try\n'
            '            set visibleWindows to (windows of targetProc whose visible is true)\n'
            '            if (count of visibleWindows) > 0 then\n'
            '                perform action "AXRaise" of item 1 of visibleWindows\n'
            '            end if\n'
            '        end try\n'
            '    end if\n'
            'end tell'
        )
        try:
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except OSError:
            pass
    elif sys.platform == "win32":
        _bring_to_front_windows(pid)


def _bring_to_front_windows(pid):
    """Équivalent Windows de bring_pid_to_front, via l'API user32 (ctypes
    — pas de dépendance pywin32 supplémentaire nécessaire) : retrouve
    la/les fenêtres visibles appartenant à ce PID puis les restaure et
    les met au premier plan."""
    try:
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        target_hwnds = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum_handler(hwnd, _lparam):
            pid_out = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid_out))
            if pid_out.value == pid and user32.IsWindowVisible(hwnd):
                target_hwnds.append(hwnd)
            return True

        user32.EnumWindows(_enum_handler, 0)
        for hwnd in target_hwnds:
            user32.ShowWindow(hwnd, SW_RESTORE)
            user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


# =======================================================================
# Contrôle à distance : code de session, approbation persistante des
# appareils, anti-bruteforce (demande du 2026-09-09, "sécurisation du
# contrôle à distance", étendue le même jour par "approbation des
# téléphones").
#
# TROIS fichiers séparés, chacun avec sa propre durée de vie — ne
# jamais les confondre :
#
# 1. remote_control_devices.json — registre PERSISTANT (survit aux
#    sessions/redémarrages, JAMAIS effacé par _clear_remote_control_
#    session_files ci-dessous) des appareils déjà vus : uniquement des
#    données qui doivent survivre d'une session à l'autre (status
#    pending/approved/revoked, short_id, label, ip_last_seen,
#    horodatages) — AUCUN secret de session dedans (voir point 1 de la
#    demande du 2026-09-09 : "je ne veux pas conserver les session_
#    token dans remote_control_devices.json").
#
# 2. remote_control_auth.json — code à 6 chiffres de la session
#    ACTUELLE (`remote_session_code`) affiché dans Paramètres, plus les
#    jetons de session déjà délivrés à des appareils APPROUVÉS pour
#    CETTE session (sous-clé "devices", voir get_or_mint_device_
#    session_token) — entièrement effacé à la fin de session (registre
#    des tournois ouverts qui devient vide, mêmes points d'accroche
#    register/unregister que "Calculer les primes") : la disparition du
#    fichier fait disparaître TOUS les jetons de session d'un coup,
#    sans avoir à les énumérer un par un — l'approbation elle-même
#    (fichier 1) n'est pas affectée, une nouvelle session en repartira
#    juste avec un nouveau code et devra délivrer de nouveaux jetons
#    aux appareils déjà approuvés.
#
# 3. remote_control_ratelimit.json — compteurs anti-bruteforce, à PART
#    car modifiés à CHAQUE tentative de code (contrairement aux deux
#    fichiers ci-dessus, écrits beaucoup plus rarement) ; scope
#    session comme le fichier 2 (effacé aux mêmes points d'accroche).
#
# Une requête distante est donc autorisée seulement si DEUX conditions
# tiennent TOUTES LES DEUX, vérifiées côté serveur à chaque requête
# (voir verify_device_session, jamais une seule des deux) :
#   a. l'appareil est "approved" dans le fichier 1 (persistant) ;
#   b. un jeton de session valide pour CETTE session existe pour cet
#      appareil dans le fichier 2 (éphémère).
# Une révocation (revoke_remote_device) agit sur LES DEUX fichiers dans
# la même section verrouillée : bascule l'appareil en "revoked" dans le
# fichier 1 ET retire immédiatement son entrée du fichier 2 — un jeton
# déjà en circulation cesse donc de fonctionner dès l'écriture faite,
# sans attendre la fin de session.
# =======================================================================

# Code de test permanent (demande du 2026-09-09, point 5) : accepté en
# PLUS du code aléatoire de session, jamais À LA PLACE — voir
# verify_remote_code, seul endroit qui le connaît. JAMAIS affiché dans
# l'interface (voir remote_session_code, qui ne renvoie jamais celui-ci)
# ni journalisé nulle part.
_REMOTE_TEST_CODE = "131261"

# Anti-bruteforce (demande du 2026-09-09) : seuils validés par
# l'utilisateur. Navigateur = clé la plus précise (un identifiant par
# appareil, voir remote_control.py) ; IP = clé de secours plus large
# (survit à un cookie effacé/navigation privée), seuil volontairement
# plus élevé pour ne pas bloquer plusieurs téléphones légitimes sur le
# même wifi à cause d'un seul fautif.
_REMOTE_BROWSER_FAIL_THRESHOLD = 5
_REMOTE_IP_FAIL_THRESHOLD = 10
_REMOTE_RATE_LIMIT_WINDOW_SECONDS = 60
# Escalade demandée : 1 min, puis 5 min, puis 15 min — plafond, jamais
# de blocage permanent (index au-delà de la fin -> dernière valeur).
_REMOTE_RATE_LIMIT_DURATIONS = (60, 300, 900)

# Une demande d'approbation "pending" non traitée par le responsable
# expire après 10 minutes (demande du 2026-09-09, point 4) — filtrée à
# la LECTURE (list_pending_remote_devices, register_device_attempt),
# jamais purgée activement : simple, cohérent avec le reste du module
# (aucune tâche de fond nécessaire). Un appareil dont la demande a
# expiré doit ressaisir le code pour en redéposer une neuve.
_REMOTE_DEVICE_PENDING_TTL_SECONDS = 600


def _remote_control_dir():
    d = os.path.join(os.path.expanduser("~"), ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d


def _remote_lock_path():
    return os.path.join(_remote_control_dir(), "remote_control.lock")


def _remote_auth_path():
    return os.path.join(_remote_control_dir(), "remote_control_auth.json")


def _remote_ratelimit_path():
    return os.path.join(_remote_control_dir(), "remote_control_ratelimit.json")


def _remote_devices_path():
    # Registre PERSISTANT (voir le commentaire d'en-tête ci-dessus) —
    # jamais listé dans _clear_remote_control_session_files.
    return os.path.join(_remote_control_dir(), "remote_control_devices.json")


def _acquire_lock(fd, timeout):
    """Tentatives NON bloquantes en boucle (jamais un vrai blocage
    indéfini du thread appelant — voir _remote_control_lock) : ni
    fcntl.flock ni msvcrt.locking n'offrent nativement un "essaie
    pendant X secondes puis abandonne" identique sur les deux
    plateformes, d'où cette boucle unique, symétrique, au lieu de
    compter sur le comportement (différent) de chacun en mode
    bloquant."""
    deadline = time.monotonic() + timeout
    if sys.platform == "win32":
        # msvcrt.locking verrouille une RÉGION d'octets à partir de la
        # position courante, jamais "le fichier" dans l'absolu : un seul
        # octet suffit comme pur jeton de mutex (son contenu n'est
        # jamais lu) — écrit une fois si le fichier est encore vide.
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("verrou remote_control indisponible (timeout)")
                time.sleep(0.02)
    else:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("verrou remote_control indisponible (timeout)")
                time.sleep(0.02)


def _release_lock(fd):
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


@contextlib.contextmanager
def _file_lock(path, timeout=5.0):
    """Verrou de FICHIER natif de l'OS (fcntl.flock sur macOS/Linux,
    msvcrt.locking sur Windows) sur `path` — aucune nouvelle dépendance,
    tous deux dans la bibliothèque standard. Extrait le 2026-09-14 du
    corps de _remote_control_lock (demande du 2026-09-09) pour être
    réutilisé tel quel par _registry_lock (open_windows.json/primes_
    session_started.json) — même mécanisme partout dans ce fichier
    plutôt qu'une seconde implémentation parallèle.

    Choisi précisément pour sa robustesse à un plantage : contrairement
    à un fichier ".lock" contenant un PID (qui exigerait de détecter
    soi-même un verrou "périmé" si son propriétaire meurt sans le
    relâcher), un verrou de fichier au niveau OS est automatiquement
    relâché par le système dès que le processus qui le détient se
    termine — même brutalement ("Forcer à quitter", plantage) — sans le
    moindre code de nettoyage à écrire.

    `fd` ouvert/fermé à CHAQUE utilisation (jamais gardé entre deux
    appels) : le verrou lui-même est associé à cette "description de
    fichier ouvert" précise, aussi bien entre process qu'entre threads
    d'un même process. Contexte appelant VOLONTAIREMENT très court (une
    lecture JSON, un calcul en mémoire, une écriture atomique — jamais
    d'E/S réseau ni d'attente utilisateur à l'intérieur) ; `timeout` est
    un filet de sécurité seulement (ne devrait jamais être atteint vu ce
    qui précède) — remonte TimeoutError plutôt que de bloquer
    indéfiniment le thread appelant."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR)
    try:
        _acquire_lock(fd, timeout)
        try:
            yield
        finally:
            _release_lock(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def _remote_control_lock(timeout=5.0):
    """Verrou inter-processus COURT (demande du 2026-09-09, durcissement
    du contrôle à distance) autour de toute opération de lecture-
    modification-écriture sur remote_control_auth.json/remote_control_
    ratelimit.json — contrairement au reste de ce module (écriture
    atomique tempfile+os.replace SEULE, jamais de verrou, suffisante
    pour une valeur qui ne change qu'à la frontière d'une session), ces
    deux fichiers peuvent être modifiés par PLUSIEURS process à des
    instants très rapprochés (deux téléphones qui tapent un code
    presque simultanément, ou deux tournois qui démarrent leur serveur
    au même instant) — une simple écriture atomique sans rien d'autour
    peut alors perdre la mise à jour de l'un des deux (l'un lit, l'autre
    lit LA MÊME version, chacun calcule sa propre nouvelle valeur,
    chacun écrit : la seconde écriture écrase silencieusement la
    première).

    Délègue à _file_lock (voir sa docstring pour le détail du mécanisme,
    factorisé le 2026-09-14 — auparavant dupliqué ici) sur remote_
    control.lock, jamais open_windows.lock (voir _registry_lock) : deux
    sections critiques indépendantes, inutile de les faire attendre
    l'une l'autre."""
    with _file_lock(_remote_lock_path(), timeout=timeout):
        yield


def _read_json_or_empty(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _clear_remote_control_session_files():
    """Supprime le code/jeton de session ET tous les compteurs anti-
    bruteforce — appelée aux MÊMES points de bascule de session que les
    primes (register/unregister, registre qui devient vide) : une
    nouvelle session repart avec un code, un jeton et des compteurs
    entièrement neufs (demande du 2026-09-09, point 8 : le blocage
    anti-bruteforce appartient à la session, jamais reporté à la
    suivante). Protégée par le même verrou que les lectures/écritures
    normales, pour ne jamais courir avec une génération/un enregistrement
    d'échec en cours dans un autre process."""
    with _remote_control_lock():
        for path in (_remote_auth_path(), _remote_ratelimit_path()):
            try:
                os.remove(path)
            except OSError:
                pass


def _ensure_remote_session_auth_locked():
    """Variante SANS verrou de _ensure_remote_session_auth — le
    verrou doit déjà être détenu par l'appelant (voir get_or_mint_
    device_session_token, qui a besoin d'enchaîner cette lecture/
    création avec une écriture supplémentaire dans LA MÊME section
    critique, sans jamais ré-acquérir le verrou : fcntl.flock/msvcrt.
    locking ne sont PAS ré-entrants — un second appel imbriqué de
    _remote_control_lock() depuis le même thread bloquerait sur son
    propre verrou jusqu'au timeout). N'appeler qu'à l'intérieur d'un
    `with _remote_control_lock():` déjà ouvert."""
    data = _read_json_or_empty(_remote_auth_path())
    if data.get("code") and data.get("session_id"):
        data.setdefault("devices", {})
        return data
    data = {
        "code": f"{secrets.randbelow(1_000_000):06d}",
        "session_id": secrets.token_hex(16),
        "devices": {},
    }
    try:
        _atomic_write_json(_remote_auth_path(), data)
    except OSError:
        pass
    return data


def _ensure_remote_session_auth():
    """Lit remote_control_auth.json, le génère s'il manque ET qu'une
    session est active (list_open_paths non vide), renvoie le contenu
    qui fait foi — {"code", "session_id", "devices": {...}}. Le verrou
    rend inutile toute relecture de vérification après coup : aucun
    autre process ne peut écrire pendant que celui-ci le détient, donc
    jamais deux codes générés pour la même session (contrairement à un
    simple "écrire puis relire" sans verrou, qui resterait vulnérable
    si les deux opérations portent sur des CLÉS DIFFÉRENTES d'un même
    fichier — non applicable ici, mais le verrou règle aussi ce cas
    plus général pour le fichier de compteurs, voir plus bas). Point
    d'entrée PUBLIC (acquiert lui-même le verrou) — voir _ensure_
    remote_session_auth_locked pour la variante réutilisable à
    l'intérieur d'une section déjà verrouillée."""
    if not list_open_paths():
        return {}
    with _remote_control_lock():
        return _ensure_remote_session_auth_locked()


def remote_session_code():
    """Code à 6 chiffres de la session ACTUELLE, pour AFFICHAGE
    uniquement (Paramètres, voir main.py) — JAMAIS le code de test
    _REMOTE_TEST_CODE, qui ne doit jamais apparaître dans l'interface
    (demande du 2026-09-09, point 5). None si aucune session active."""
    return _ensure_remote_session_auth().get("code")


def remote_session_token():
    """Jeton long de la session actuelle (jamais affiché/saisi par
    l'utilisateur — voir remote_control.py, posé en cookie après
    validation du code). None si aucune session active."""
    return _ensure_remote_session_auth().get("token")


def verify_remote_code(entered_code):
    """True si `entered_code` (chaîne quelconque venant du téléphone)
    correspond au code de la session actuelle OU au code de test
    permanent (demande du 2026-09-09, point 5 : accepté EN PLUS, jamais
    À LA PLACE du code aléatoire — qui continue d'être généré à chaque
    session normalement) — validation stricte : exactement 6 chiffres,
    rien d'autre accepté même par coïncidence de formatage.
    Comparaison en temps constant (secrets.compare_digest) : évite
    qu'une différence de timing ne laisse deviner combien de chiffres de
    tête sont déjà corrects."""
    if not isinstance(entered_code, str) or not re.fullmatch(r"\d{6}", entered_code):
        return False
    if secrets.compare_digest(entered_code, _REMOTE_TEST_CODE):
        return True
    session_code = remote_session_code()
    return session_code is not None and secrets.compare_digest(entered_code, session_code)


def _new_short_id(existing_short_ids):
    """6 caractères hexadécimaux (demande du 2026-09-09, "approbation
    des téléphones", point 5) — PUREMENT visuel (affiché sur le PC pour
    distinguer les demandes en attente à l'écran), ne sert JAMAIS de
    secret ni n'entre dans aucune décision d'authentification (voir
    verify_device_session, qui ne s'appuie que sur browser_id/token).
    Tiré à part de browser_id (jamais un simple préfixe de celui-ci) et
    vérifié unique parmi les appareils déjà enregistrés — collision
    quasi impossible dès la 1ère tentative (16.7M de valeurs), boucle
    de secours au cas où."""
    for _ in range(20):
        candidate = secrets.token_hex(3).upper()
        if candidate not in existing_short_ids:
            return candidate
    return secrets.token_hex(4).upper()  # repli : espace encore plus large


def register_device_attempt(browser_id, ip):
    """Appelée depuis remote_control.py APRÈS une vérification de CODE
    réussie (voir /authenticate) mais AVANT toute décision d'accès : ne
    délivre JAMAIS de jeton elle-même (voir get_or_mint_device_session_
    token, appelé séparément une fois "approved" constaté) — se
    contente de créer/rafraîchir l'entrée de ce browser_id dans le
    registre PERSISTANT (remote_control_devices.json), et renvoie son
    status ("approved" ou "pending") pour que l'appelant sache s'il
    doit délivrer un accès immédiat ou renvoyer "en attente
    d'autorisation" (demande du 2026-09-09, "approbation des
    téléphones").

    - Appareil déjà "approved" : ip_last_seen rafraîchi, status
      inchangé — AUCUNE nouvelle demande créée, un appareil approuvé
      qui ressaisit le code (même 131261) ne redevient JAMAIS
      "pending" (point 9 : "appareil déjà approuvé + nouveau code
      correct = accès immédiat à la nouvelle session").
    - Appareil "pending" NON expiré (voir _REMOTE_DEVICE_PENDING_TTL_
      SECONDS) : ip_last_seen rafraîchi seulement — une seule demande
      par browser_id, jamais multipliée (point 4) ; un simple
      rechargement de /login, qui n'appelle jamais cette fonction (lui
      seul soumet un CODE), ne crée de toute façon rien.
    - Appareil "pending" EXPIRÉ, "revoked", ou totalement inconnu :
      (re)devient "pending", horodatage remis à l'instant présent — un
      appareil révoqué qui revient avec le bon code (ou 131261) NE
      RETROUVE JAMAIS l'accès automatiquement (point 8 du message
      précédent / point 9 de "approbation des téléphones") : il ne
      fait que redéposer une demande neuve, label/short_id existants
      conservés par simple confort visuel pour le responsable ("tiens,
      encore ce téléphone-là")."""
    now = time.time()
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
        entry = devices.get(browser_id)
        if entry and entry.get("status") == "approved":
            entry["ip_last_seen"] = ip
            devices[browser_id] = entry
            try:
                _atomic_write_json(_remote_devices_path(), devices)
            except OSError:
                pass
            return "approved"
        if (
            entry and entry.get("status") == "pending"
            and now - entry.get("requested_at", 0) <= _REMOTE_DEVICE_PENDING_TTL_SECONDS
        ):
            entry["ip_last_seen"] = ip
            devices[browser_id] = entry
            try:
                _atomic_write_json(_remote_devices_path(), devices)
            except OSError:
                pass
            return "pending"
        short_id = (entry or {}).get("short_id")
        if not short_id:
            existing_short_ids = {d.get("short_id") for d in devices.values() if d.get("short_id")}
            short_id = _new_short_id(existing_short_ids)
        devices[browser_id] = {
            "status": "pending",
            "short_id": short_id,
            "label": (entry or {}).get("label"),
            "ip_last_seen": ip,
            "requested_at": now,
            "approved_at": None,
            "revoked_at": (entry or {}).get("revoked_at"),
        }
        try:
            _atomic_write_json(_remote_devices_path(), devices)
        except OSError:
            pass
        return "pending"


def get_or_mint_device_session_token(browser_id):
    """Jeton de session (256 bits) pour cet appareil, pour LA session
    ACTUELLE — délivré UNIQUEMENT si l'appareil est "approved" dans le
    registre persistant ; idempotent (un appareil déjà servi pour cette
    session reçoit le MÊME jeton à chaque appel, jamais un nouveau à
    chaque sondage — voir /authenticate et /auth_status côté
    remote_control.py, tous deux appelants). Stocké dans le fichier
    ÉPHÉMÈRE (remote_control_auth.json, sous-clé "devices"), jamais
    dans le registre persistant (demande du 2026-09-09, point 1) : il
    disparaît de lui-même à la prochaine session, sans rien à nettoyer
    ici. None si l'appareil n'est pas (ou plus) approuvé, ou si aucune
    session n'est active."""
    if not browser_id or not list_open_paths():
        return None
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
        entry = devices.get(browser_id)
        if not entry or entry.get("status") != "approved":
            return None
        auth = _ensure_remote_session_auth_locked()
        session_devices = auth.setdefault("devices", {})
        existing = session_devices.get(browser_id)
        if existing and existing.get("session_token"):
            return existing["session_token"]
        token = secrets.token_hex(32)
        session_devices[browser_id] = {"session_token": token, "issued_at": time.time()}
        try:
            _atomic_write_json(_remote_auth_path(), auth)
        except OSError:
            pass
        return token


def verify_device_session(browser_id, token):
    """LA vérification faite sur CHAQUE requête distante sensible (voir
    remote_control.py, Handler._is_authenticated) — les DEUX conditions
    ci-dessous doivent tenir, aucune ne suffit seule (demande du
    2026-09-09, "approbation des téléphones", point 9) :
      1. l'appareil est TOUJOURS "approved" dans le registre PERSISTANT
         (remote_control_devices.json) — une révocation le fait échouer
         immédiatement, dès l'écriture faite par revoke_remote_device ;
      2. un jeton de session valide pour CETTE session existe pour ce
         browser_id dans le fichier ÉPHÉMÈRE (remote_control_auth.json)
         et correspond exactement (comparaison en temps constant) —
         comme ce fichier est entièrement remplacé à chaque nouvelle
         session (voir _ensure_remote_session_auth_locked) et que
         revoke_remote_device y retire aussi l'entrée immédiatement, un
         ancien jeton (session précédente OU appareil révoqué depuis)
         ne correspond simplement plus à rien — aucune liste de
         révocation séparée à consulter."""
    if not browser_id or not token or not list_open_paths():
        return False
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
        entry = devices.get(browser_id)
        if not entry or entry.get("status") != "approved":
            return False
        auth = _read_json_or_empty(_remote_auth_path())
    session_entry = (auth.get("devices") or {}).get(browser_id)
    if not session_entry or not session_entry.get("session_token"):
        return False
    return secrets.compare_digest(token, session_entry["session_token"])


def get_device_auth_status(browser_id):
    """"approved" / "pending" / "refused" — UNIQUEMENT ces trois
    valeurs (demande du 2026-09-09, "approbation des téléphones",
    point 2 : /auth_status ne doit renvoyer STRICTEMENT rien d'autre —
    ni liste, ni nom, ni IP, ni jeton, ni session_id). Un browser_id
    absent/inconnu du registre (jamais encore soumis de code, ou
    cookie manquant) est traité comme "pending" : ne révèle rien de
    plus qu'un vrai "pending", et évite un 4e statut inutile côté
    client. "revoked" (registre persistant) devient "refused" pour le
    téléphone — vocabulaire volontairement différent de celui du
    fichier interne, jamais exposé tel quel."""
    if not browser_id:
        return "pending"
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
    entry = devices.get(browser_id)
    if not entry:
        return "pending"
    status = entry.get("status")
    if status == "approved":
        return "approved"
    if status == "revoked":
        return "refused"
    return "pending"


def approve_remote_device(browser_id, label=None):
    """Opération LOCALE Tkinter UNIQUEMENT (voir main.py, bouton
    "Autoriser") — demande du 2026-09-09, "approbation des téléphones",
    point 3 : AUCUNE route HTTP distante n'appelle jamais cette
    fonction, un téléphone ne peut jamais s'auto-approuver. Ne délivre
    PAS de jeton de session ici (voir get_or_mint_device_session_token,
    appelé paresseusement par /authenticate ou /auth_status la
    prochaine fois que ce téléphone se manifeste) : approuver un
    appareil qui ne s'est jamais reconnecté depuis ne doit rien écrire
    dans le fichier de session éphémère. Renvoie False si ce browser_id
    est inconnu du registre (rien à approuver)."""
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
        entry = devices.get(browser_id)
        if not entry:
            return False
        entry["status"] = "approved"
        entry["approved_at"] = time.time()
        entry["revoked_at"] = None
        if label:
            entry["label"] = label
        devices[browser_id] = entry
        try:
            _atomic_write_json(_remote_devices_path(), devices)
        except OSError:
            pass
    return True


def revoke_remote_device(browser_id):
    """Opération LOCALE Tkinter UNIQUEMENT (voir main.py, boutons
    "Refuser" sur une demande en attente et "Révoquer" sur un appareil
    déjà approuvé — mêmes conséquences, même fonction) — demande du
    2026-09-09, "approbation des téléphones", points 3 et 7 : bascule
    l'appareil en "revoked" dans le registre PERSISTANT ET retire
    IMMÉDIATEMENT son éventuelle entrée du fichier de session ÉPHÉMÈRE,
    dans LA MÊME section verrouillée — un jeton déjà en circulation
    cesse de fonctionner dès cette écriture faite (voir verify_device_
    session, condition 1), sans attendre une expiration ni la fin de
    session. Un appareil ainsi révoqué qui reviendrait avec le bon code
    (même 131261) ne redevient JAMAIS approuvé automatiquement : voir
    register_device_attempt, qui le traite comme une demande neuve.
    Renvoie False si ce browser_id est inconnu du registre."""
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
        entry = devices.get(browser_id)
        if entry:
            entry["status"] = "revoked"
            entry["revoked_at"] = time.time()
            devices[browser_id] = entry
            try:
                _atomic_write_json(_remote_devices_path(), devices)
            except OSError:
                pass
        auth = _read_json_or_empty(_remote_auth_path())
        session_devices = auth.get("devices") or {}
        if session_devices.pop(browser_id, None) is not None:
            auth["devices"] = session_devices
            try:
                _atomic_write_json(_remote_auth_path(), auth)
            except OSError:
                pass
    return bool(entry)


def list_pending_remote_devices():
    """Demandes "pending" NON expirées (voir _REMOTE_DEVICE_PENDING_TTL_
    SECONDS), triées par ancienneté de demande — pour l'écran Paramètres
    (voir main.py) et la notification de nouvelle demande (App._tick).
    Ne renvoie que ce qui est nécessaire à l'affichage local ; browser_id
    inclus (nécessaire pour que les boutons Autoriser/Refuser sachent
    quel appareil cibler) mais jamais transmis au réseau."""
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
    now = time.time()
    result = []
    for browser_id, entry in devices.items():
        if entry.get("status") != "pending":
            continue
        if now - entry.get("requested_at", 0) > _REMOTE_DEVICE_PENDING_TTL_SECONDS:
            continue
        result.append({
            "browser_id": browser_id,
            "short_id": entry.get("short_id", ""),
            "label": entry.get("label"),
            "ip_last_seen": entry.get("ip_last_seen", ""),
            "requested_at": entry.get("requested_at", 0),
        })
    result.sort(key=lambda d: d["requested_at"])
    return result


def list_approved_remote_devices():
    """Appareils "approved", triés par nom affiché — pour la liste
    "Téléphones autorisés" de Paramètres (voir main.py), avec le
    bouton "Révoquer" de chacun."""
    with _remote_control_lock():
        devices = _read_json_or_empty(_remote_devices_path())
    result = []
    for browser_id, entry in devices.items():
        if entry.get("status") != "approved":
            continue
        result.append({
            "browser_id": browser_id,
            "short_id": entry.get("short_id", ""),
            "label": entry.get("label") or entry.get("short_id", ""),
            "ip_last_seen": entry.get("ip_last_seen", ""),
            "approved_at": entry.get("approved_at", 0),
        })
    result.sort(key=lambda d: (d["label"] or "").lower())
    return result


def _fresh_remote_rate_limit_entry():
    return {"window_start": 0.0, "fail_count": 0, "level": 0, "block_until": 0.0}


def remote_auth_rate_limit_status(browser_id, ip):
    """(bloqué: bool, secondes_restantes: float) — pire des deux clés
    (navigateur ET IP, demande du 2026-09-09 point 3 : ni l'une ni
    l'autre n'est une identité certaine, on retient la plus stricte des
    deux). Lecture seule (sous verrou, pour ne jamais lire un état à
    moitié écrit par un record_remote_auth_failure/success concurrent) —
    à appeler AVANT toute comparaison du code saisi, pour ne même pas
    évaluer un code pendant un blocage actif."""
    now = time.time()
    with _remote_control_lock():
        data = _read_json_or_empty(_remote_ratelimit_path())
    worst_remaining = 0.0
    for key in (f"browser:{browser_id}", f"ip:{ip}"):
        entry = data.get(key) or _fresh_remote_rate_limit_entry()
        remaining = entry.get("block_until", 0.0) - now
        if remaining > worst_remaining:
            worst_remaining = remaining
    return (worst_remaining > 0), max(worst_remaining, 0.0)


def _record_remote_auth_attempt(browser_id, ip, success):
    """Sous verrou : met à jour LES DEUX clés (navigateur, IP) pour une
    tentative d'authentification. `success=True` réinitialise
    entièrement les deux (compteur ET niveau d'escalade, demande du
    2026-09-09 : une authentification correcte "pardonne" totalement,
    la prochaine série repartira à 1 minute, jamais une pénalité qui
    s'accumule après un succès). `success=False` incrémente le
    compteur de fenêtre glissante (60s) de chacune des deux clés et,
    dès que le SEUIL de CETTE clé est atteint (5 pour le navigateur, 10
    pour l'IP — jamais "toléré une fois de plus puis bloqué au coup
    suivant"), déclenche IMMÉDIATEMENT le blocage à ce même échec,
    avec la durée d'escalade correspondante (1 min / 5 min / 15 min
    plafond)."""
    now = time.time()
    with _remote_control_lock():
        data = _read_json_or_empty(_remote_ratelimit_path())
        keys_and_thresholds = (
            (f"browser:{browser_id}", _REMOTE_BROWSER_FAIL_THRESHOLD),
            (f"ip:{ip}", _REMOTE_IP_FAIL_THRESHOLD),
        )
        for key, threshold in keys_and_thresholds:
            if success:
                data[key] = _fresh_remote_rate_limit_entry()
                continue
            entry = dict(data.get(key) or _fresh_remote_rate_limit_entry())
            # Déjà bloqué : ne prolonge/ne recompte pas par-dessus (filet
            # de sécurité seulement — remote_auth_rate_limit_status doit
            # déjà avoir refusé la requête AVANT d'appeler cette
            # fonction, voir remote_control.py).
            if entry.get("block_until", 0.0) > now:
                data[key] = entry
                continue
            if now - entry.get("window_start", 0.0) > _REMOTE_RATE_LIMIT_WINDOW_SECONDS:
                entry["window_start"] = now
                entry["fail_count"] = 0
            entry["fail_count"] = entry.get("fail_count", 0) + 1
            if entry["fail_count"] >= threshold:
                level = entry.get("level", 0)
                duration = _REMOTE_RATE_LIMIT_DURATIONS[min(level, len(_REMOTE_RATE_LIMIT_DURATIONS) - 1)]
                entry["block_until"] = now + duration
                entry["level"] = level + 1
                entry["fail_count"] = 0
                entry["window_start"] = now
            data[key] = entry
        try:
            _atomic_write_json(_remote_ratelimit_path(), data)
        except OSError:
            pass


def record_remote_auth_failure(browser_id, ip):
    _record_remote_auth_attempt(browser_id, ip, success=False)


def record_remote_auth_success(browser_id, ip):
    _record_remote_auth_attempt(browser_id, ip, success=True)
