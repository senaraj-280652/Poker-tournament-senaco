# -*- coding: utf-8 -*-
"""Numéro de version du logiciel — source unique de vérité pour
l'affichage dans l'application (titre de fenêtre, menu Aide > À propos,
fenêtre d'activation de licence).

Ce numéro n'est PAS lu automatiquement par les installateurs : pensez à
le reporter aussi, à chaque nouvelle version distribuée, dans
windows/app.wxs (attribut Version du <Package>) — voir la section
« Changer le numéro de version » de windows/README.md — puisque WiX ne
peut pas lire un fichier Python.
"""
APP_VERSION = "1.2.3"
