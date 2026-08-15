# -*- coding: utf-8 -*-
"""Outil réservé à l'éditeur (vous) : génère une clé de licence pour un
club, à partir de l'identifiant machine qu'il vous a communiqué.

Ce script n'est PAS distribué aux clubs et n'est pas embarqué dans les
exécutables compilés (voir license.py) — gardez-le pour votre usage
personnel.

Prérequis : le fichier local `_license_secret.py` doit exister (jamais
versionné, voir .gitignore et l'en-tête de license.py) :

    python3 -c "import secrets; print('SECRET = ' + repr(secrets.token_hex(32)))" > _license_secret.py

Conservez-en une copie de sûreté privée : le perdre revient à ne plus
pouvoir délivrer de nouvelles licences ni valider les anciennes, et en
changer invaliderait toutes les licences déjà délivrées.

Utilisation
-----------
    python3 generate_license.py "<identifiant machine>" "<nom du club>"

Exemple :
    python3 generate_license.py A1B2-C3D4-E5F6-0789 "Club de Poker de Senaco"
"""
import sys

from license import compute_key


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    mach_id_display, club_name = sys.argv[1], sys.argv[2]

    try:
        from _license_secret import SECRET  # noqa: F401
    except ImportError:
        print(
            "Erreur : fichier _license_secret.py introuvable.\n"
            "Voir l'en-tête de ce script pour le créer (une seule fois)."
        )
        sys.exit(1)

    key = compute_key(mach_id_display, club_name)
    print(f"Club           : {club_name}")
    print(f"Identifiant    : {mach_id_display}")
    print(f"Clé de licence : {key}")
    print()
    print("Communiquez cette clé au club : elle est à saisir une seule fois,")
    print("avec le nom du club EXACTEMENT comme ci-dessus, dans la fenêtre")
    print("d'activation affichée au premier lancement de l'application.")


if __name__ == "__main__":
    main()
