#!/bin/bash
# Double-cliquez sur ce fichier pour lancer le Gestionnaire de Tournoi de Poker.
# (La première fois, macOS peut demander une confirmation : voir LISEZMOI.md)

cd "$(dirname "$0")"

if ! command -v python3 &> /dev/null; then
    echo "----------------------------------------------------------------"
    echo "Python 3 n'est pas installé sur cet ordinateur."
    echo "Installez-le depuis https://www.python.org/downloads/"
    echo "puis redémarrez ce fichier."
    echo "----------------------------------------------------------------"
    read -p "Appuyez sur Entrée pour fermer cette fenêtre..."
    exit 1
fi

python3 main.py

status=$?
if [ $status -ne 0 ]; then
    echo ""
    echo "----------------------------------------------------------------"
    echo "L'application s'est arrêtée avec une erreur (message ci-dessus)."
    echo "----------------------------------------------------------------"
    read -p "Appuyez sur Entrée pour fermer cette fenêtre..."
fi
