# -*- coding: utf-8 -*-
"""Nom et numéro de version du logiciel — source unique de vérité pour
l'affichage dans l'application (titre de fenêtre, bandeau, menu Aide >
À propos, écran d'accueil, fenêtre d'activation de licence).

Le numéro de version n'est PAS lu automatiquement par les
installateurs : pensez à le reporter aussi, à chaque nouvelle version
distribuée, dans windows/app.wxs (attribut Version du <Package>) — voir
la section « Changer le numéro de version » de windows/README.md —
puisque WiX ne peut pas lire un fichier Python.
"""
APP_NAME = "Gestionnaire de Tournoi de Poker - Senaco"
APP_VERSION = "1.2.22"
