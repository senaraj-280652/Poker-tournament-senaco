# -*- coding: utf-8 -*-
"""Petit script auxiliaire, PAS un fichier de test lui-même (nom
volontairement sans le préfixe test_, donc jamais collecté par
`unittest discover`) : exécute UNE action isolée liée à
open_windows.try_register, dans un VRAI process Python tout neuf à
chaque appel — même principe que tests/_subprocess_primes_worker.py
(HOME redirigé par l'appelant fait le lien entre les process, exactement
comme en conditions réelles).

Usage : python3 _subprocess_try_register_worker.py <action> [args...]
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


def main_cli():
    action = sys.argv[1]
    args = sys.argv[2:]

    if action == "try_register":
        # Court-vécu : une seule tentative, imprime le résultat puis quitte
        # aussitôt (représente une tentative d'ouverture qui échoue et
        # n'a jamais vraiment "ouvert" quoi que ce soit).
        result = open_windows.try_register(args[0])
        print("None" if result is None else str(result), flush=True)

    elif action == "hold_open":
        # Représente une VRAIE fenêtre de tournoi ouverte, pour toute la
        # durée où ce process reste vivant (voir _subprocess_primes_
        # worker.py:_hold_open pour la même remarque sur _prune/_pid_is_
        # running) : reste vivant jusqu'à recevoir "close" sur stdin (ou
        # EOF), désenregistre alors PROPREMENT avant de quitter.
        path = args[0]
        result = open_windows.try_register(path)
        print("None" if result is None else str(result), flush=True)
        for line in sys.stdin:
            if line.strip() == "close":
                break
        open_windows.unregister(path)
        print("closed", flush=True)

    elif action == "hold_open_no_cleanup":
        # Comme "hold_open", mais ne désenregistre JAMAIS avant de
        # quitter — simule un plantage/kill -9 (voir os._exit ci-dessous,
        # qui saute tout nettoyage Python, y compris finally/atexit) pour
        # tester la récupération automatique (_prune/_pid_is_running).
        path = args[0]
        result = open_windows.try_register(path)
        print("None" if result is None else str(result), flush=True)
        for line in sys.stdin:
            if line.strip() == "die":
                break
        os._exit(1)

    else:
        raise SystemExit(f"action inconnue : {action!r}")


if __name__ == "__main__":
    main_cli()
