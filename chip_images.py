# -*- coding: utf-8 -*-
"""
Images de jetons (jeu "Jetons" de l'onglet Blindes), stockées
indépendamment des tournois dans ~/.poker_tournament/chip_images/, sur le
même principe que les photos de joueurs (player_photos.py).

Contrairement aux photos (indexées par nom de joueur, une clé stable),
une dénomination de jeton n'a pas d'identifiant stable — son nom peut
être modifié ou vidé, les lignes réordonnées/supprimées. Pas d'index
séparé ici : chaque dénomination référence directement son fichier image
par son nom de fichier généré (champ "image" du JSON de la dénomination,
voir App._load_chip_denominations / _collect_chips_from_widgets).
"""
import os
import shutil
import uuid


def _chip_images_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament", "chip_images")
    os.makedirs(d, exist_ok=True)
    return d


def get_chip_image_path(filename):
    """Chemin de l'image `filename` (tel que stocké dans le champ "image"
    de la dénomination), ou None si absent/introuvable."""
    if not filename:
        return None
    path = os.path.join(_chip_images_dir(), filename)
    return path if os.path.exists(path) else None


def save_chip_image_from_file(source_path):
    """Copie le fichier image `source_path` dans le stockage des images de
    jetons, sous un nom généré unique (pour ne jamais entrer en conflit
    avec une autre dénomination). Renvoie ce nom de fichier, à conserver
    dans le champ "image" de la dénomination JSON."""
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif"):
        ext = ".png"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_chip_images_dir(), filename)
    shutil.copyfile(source_path, dest)
    return filename


def delete_chip_image(filename):
    """Supprime le fichier image `filename` du stockage, si présent
    (best-effort — ne lève jamais). À appeler quand une dénomination
    change d'image ou repasse en couleur unie, pour ne pas accumuler des
    fichiers orphelins au fil du temps."""
    if not filename:
        return
    path = os.path.join(_chip_images_dir(), filename)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
