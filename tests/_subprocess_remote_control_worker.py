# -*- coding: utf-8 -*-
"""Petit script auxiliaire, PAS un fichier de test lui-même (nom
volontairement sans le préfixe test_, donc jamais collecté par
`unittest discover`) : reproduit de VRAIS processus séparés pour le
scénario multi-tournois du code/jeton de session à distance (demande
du 2026-09-09, "sécurisation du contrôle à distance" puis "approbation
des téléphones") — même principe et mêmes précautions que
_subprocess_primes_worker.py (voir sa docstring) : seul le disque
(HOME redirigé par l'appelant) fait le lien entre les processus, jamais
d'état Python partagé.

Usage : python3 _subprocess_remote_control_worker.py <action> [args...]"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


def _hold_open(path):
    """Représente UNE fenêtre de tournoi réellement ouverte, pour toute
    la durée où ce process reste vivant (voir _subprocess_primes_worker.
    py:_hold_open, même principe exact) : register() puis attente d'un
    "close" sur stdin, unregister() ensuite depuis CE MÊME process."""
    open_windows.register(path)
    print(open_windows.remote_session_code(), flush=True)
    print("ready", flush=True)
    for line in sys.stdin:
        if line.strip() == "close":
            break
    open_windows.unregister(path)
    print("closed", flush=True)


def main_cli():
    action = sys.argv[1]
    args = sys.argv[2:]

    if action == "hold_open":
        _hold_open(args[0])

    elif action == "read_code":
        print(open_windows.remote_session_code())

    elif action == "list_open":
        print(",".join(open_windows.list_open_paths()))

    elif action == "register_device_attempt":
        # args : browser_id ip
        print(open_windows.register_device_attempt(args[0], args[1]))

    elif action == "approve_device":
        print(open_windows.approve_remote_device(args[0], label=args[1] if len(args) > 1 else None))

    elif action == "revoke_device":
        print(open_windows.revoke_remote_device(args[0]))

    elif action == "verify_device_session":
        # args : browser_id token
        print("1" if open_windows.verify_device_session(args[0], args[1]) else "0")

    elif action == "mint_token":
        token = open_windows.get_or_mint_device_session_token(args[0])
        print(token or "")

    elif action == "list_approved":
        print(",".join(d["browser_id"] for d in open_windows.list_approved_remote_devices()))

    else:
        raise SystemExit(f"action inconnue : {action!r}")


if __name__ == "__main__":
    main_cli()
