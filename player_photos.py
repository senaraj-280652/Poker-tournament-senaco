# -*- coding: utf-8 -*-
"""
Photos de joueurs, mémorisées indépendamment des tournois dans
~/.poker_tournament/photos/, sur le même principe que le répertoire de
joueurs (roster.py). Un index JSON associe le nom du joueur à son fichier
image. Ce module ne dépend que de la bibliothèque standard : la
copie/suppression de fichiers ne nécessite ni Pillow ni OpenCV (seuls
l'aperçu et la capture caméra en ont besoin, gérés dans main.py).
"""
import json
import os
import shutil
import uuid


def _photos_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament", "photos")
    os.makedirs(d, exist_ok=True)
    return d


def _index_path():
    return os.path.join(_photos_dir(), "index.json")


def _load_index():
    path = _index_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_index(index):
    with open(_index_path(), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)


def get_photo_path(name):
    """Chemin de la photo du joueur `name`, ou None s'il n'en a pas."""
    filename = _load_index().get(name)
    if not filename:
        return None
    path = os.path.join(_photos_dir(), filename)
    return path if os.path.exists(path) else None


def save_photo_from_file(name, source_path):
    """Copie le fichier image `source_path` dans le stockage des photos,
    associé au joueur `name` (remplace une éventuelle photo existante)."""
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_photos_dir(), filename)
    shutil.copyfile(source_path, dest)

    index = _load_index()
    old_filename = index.get(name)
    index[name] = filename
    _save_index(index)

    if old_filename and old_filename != filename:
        old_path = os.path.join(_photos_dir(), old_filename)
        if os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass
    return dest


def delete_photo(name):
    index = _load_index()
    filename = index.pop(name, None)
    if filename:
        path = os.path.join(_photos_dir(), filename)
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
        _save_index(index)


def rename_photo(old_name, new_name):
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name or old_name == new_name:
        return
    index = _load_index()
    if old_name in index:
        index[new_name] = index.pop(old_name)
        _save_index(index)
