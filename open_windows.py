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
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time


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


def _pid_is_running(pid):
    """Vrai si un processus portant cet identifiant existe encore sur
    cette machine — utilisé pour ignorer/retirer une entrée laissée par
    un processus disparu sans se désinscrire proprement (voir
    App._on_close / App._cleanup_for_close)."""
    if not isinstance(pid, int):
        return False
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
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
    pour "le plus récent" à sa place."""
    if not path:
        return
    data = _prune(_load())
    data[os.path.abspath(path)] = {"pid": os.getpid(), "registered_at": time.time()}
    _save(data)


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
    processus en cas de course) — voir App._cleanup_for_close."""
    if not path:
        return
    data = _prune(_load())
    abs_path = os.path.abspath(path)
    entry = data.get(abs_path)
    if entry and entry.get("pid") == os.getpid():
        del data[abs_path]
        _save(data)


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
