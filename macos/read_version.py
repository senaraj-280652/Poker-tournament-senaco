# -*- coding: utf-8 -*-
"""Affiche APP_VERSION (version.py, source unique de vérité du numéro de
version — voir sa docstring) sur stdout, pour que build_dmg.sh puisse
nommer le .dmg d'après cette valeur sans jamais avoir à re-saisir une
expression régulière fragile dans la ligne de commande (déjà vu côté
Windows : les guillemets d'un motif en ligne entraient en conflit avec
le découpage du shell — voir windows/read_version.py, dont ce fichier
reprend le principe, dupliqué ici pour ne pas introduire de dépendance
du dossier macos/ vers windows/)."""
import os
import re

_VERSION_PY = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "version.py")


def main():
    with open(_VERSION_PY, encoding="utf-8") as f:
        content = f.read()
    match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', content)
    if not match:
        raise SystemExit("APP_VERSION introuvable dans version.py")
    print(match.group(1))


if __name__ == "__main__":
    main()
