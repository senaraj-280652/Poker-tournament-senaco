# -*- coding: utf-8 -*-
"""
Photos de joueurs, mémorisées indépendamment des tournois dans
~/.poker_tournament/photos/, sur le même principe que le répertoire de
joueurs (roster.py). Un index JSON associe le nom du joueur à son fichier
image. Ce module ne dépend que de la bibliothèque standard : la
copie/suppression de fichiers ne nécessite ni Pillow ni OpenCV (seuls
l'aperçu et la capture caméra en ont besoin, gérés dans main.py).

Appelé depuis plusieurs threads en pratique (le contrôle à distance sert
un thread HTTP par téléphone connecté, voir remote_control.py — deux
photos envoyées presque en même temps depuis deux téléphones différents
appellent donc _register_photo/delete_photo en parallèle). _index_lock
protège le cycle complet lire→modifier→réécrire de index.json contre ces
accès concurrents (sans lui, deux threads pouvaient lire le même index
avant que l'un des deux n'ait écrit le sien, et le second écrasait alors
la mise à jour du premier — une "lost update" silencieuse, vérifiée en
pratique). Une seule instance du serveur tourne dans ce processus : un
verrou en mémoire (pas un verrou de fichier inter-processus) suffit.
"""
import json
import os
import shutil
import threading
import uuid

# Protège tout le cycle lire/modifier/réécrire de index.json contre les
# accès concurrents entre threads (voir docstring du module) — jamais
# recroisé (aucune fonction protégée n'en appelle une autre), un simple
# Lock suffit, pas besoin de RLock.
_index_lock = threading.Lock()


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
    """Écriture atomique : le JSON complet est d'abord écrit dans un
    fichier temporaire du MÊME dossier (indispensable pour qu'os.replace
    reste atomique — un remplacement inter-systèmes de fichiers ne l'est
    pas), puis os.replace() bascule d'un coup vers index.json. Un lecteur
    (get_photo_path, appelé sans _index_lock) ne peut ainsi jamais tomber
    sur un fichier tronqué en cours d'écriture — mais ça ne suffit PAS à
    empêcher une lost update entre deux écrivains : c'est le rôle de
    _index_lock, pas du remplacement atomique seul (voir docstring du
    module)."""
    path = _index_path()
    tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_path, path)


def get_photo_path(name):
    """Chemin de la photo du joueur `name`, ou None s'il n'en a pas."""
    filename = _load_index().get(name)
    if not filename:
        return None
    path = os.path.join(_photos_dir(), filename)
    return path if os.path.exists(path) else None


def _register_photo(name, filename):
    """Associe `filename` (déjà présent dans le dossier photos) au joueur
    `name` dans l'index, et supprime l'ancien fichier s'il est remplacé.
    Le cycle lire/modifier/réécrire l'index tient entièrement sous
    _index_lock (voir docstring du module) : un autre thread qui
    modifierait l'index au même moment (autre joueur, ou même joueur)
    attend son tour au lieu d'écraser cette mise à jour."""
    with _index_lock:
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


def save_photo_from_file(name, source_path):
    """Copie le fichier image `source_path` dans le stockage des photos,
    associé au joueur `name` (remplace une éventuelle photo existante)."""
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    dest = os.path.join(_photos_dir(), filename)
    shutil.copyfile(source_path, dest)
    _register_photo(name, filename)
    return dest


def save_photo_from_image(name, pil_image):
    """Enregistre une image PIL déjà en mémoire (typiquement après
    recadrage) comme photo du joueur `name`. Nécessite Pillow — à
    l'appelant de vérifier sa disponibilité au préalable."""
    filename = f"{uuid.uuid4().hex}.jpg"
    dest = os.path.join(_photos_dir(), filename)
    pil_image.convert("RGB").save(dest, "JPEG", quality=90)
    _register_photo(name, filename)
    return dest


def delete_photo(name):
    # Même remarque que _register_photo : cycle lire/modifier/réécrire
    # entièrement protégé par _index_lock.
    with _index_lock:
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
    # Même remarque que _register_photo : cycle lire/modifier/réécrire
    # entièrement protégé par _index_lock.
    with _index_lock:
        index = _load_index()
        if old_name in index:
            index[new_name] = index.pop(old_name)
            _save_index(index)
