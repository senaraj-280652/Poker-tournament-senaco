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
        with open(_registry_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
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
    une fois le tournoi effectivement ouvert (voir App.__init__)."""
    if not path:
        return
    data = _prune(_load())
    data[os.path.abspath(path)] = {"pid": os.getpid()}
    _save(data)


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


def bring_pid_to_front(pid):
    """Ramène au premier plan la fenêtre du processus `pid` (best-effort,
    silencieux en cas d'échec — ex : permission Accessibilité macOS non
    accordée, ou plateforme non gérée)."""
    if sys.platform == "darwin":
        script = (
            f'tell application "System Events" to set frontmost of '
            f'(first process whose unix id is {pid}) to true'
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
