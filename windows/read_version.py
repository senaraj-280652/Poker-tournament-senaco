# -*- coding: utf-8 -*-
"""Affiche APP_VERSION (version.py, source unique de vérité du numéro de
version — voir sa docstring) sur stdout, pour que build-msi.yml puisse
nommer le .msi et l'artifact d'après cette valeur sans jamais avoir à
re-saisir une expression régulière fragile dans un -c en ligne (les
guillemets du motif entraient en conflit avec le découpage de pwsh)."""
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
