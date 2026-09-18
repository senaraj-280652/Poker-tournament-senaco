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
import os
import subprocess
import sys

APP_NAME = "Gestionnaire de Tournoi de Poker - Senaco"
APP_VERSION = "1.2.41"

# Identification du code en cours d'exécution pendant le développement
# (demande du 2026-09-09) : APP_VERSION ci-dessus reste un numéro figé,
# modifié à la main uniquement pour une VRAIE version distribuée — pour
# ne JAMAIS avoir à y toucher après chaque petite correction, dev_suffix()
# calcule un complément à l'exécution, à partir de Git, jamais stocké ici
# en dur. Voir dev_suffix() plus bas pour le détail du format.
_DEV_SUFFIX_CACHE = None  # None = pas encore calculé (voir dev_suffix) ; "" est un résultat valide (build figée)


def _repo_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _short_git_hash(repo_dir=None):
    """Hash Git court du commit HEAD du dépôt contenant ce fichier, ou
    None si indisponible (pas un dépôt Git, `git` absent du PATH,
    dépôt corrompu...) — jamais bloquant, jamais une exception qui
    remonte à l'appelant. `repo_dir` : uniquement pour les tests (permet
    de pointer vers un dépôt Git temporaire plutôt que le vrai projet)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_dir or _repo_dir(),
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    h = result.stdout.strip()
    return h or None


def _working_tree_dirty(repo_dir=None):
    """True si le dépôt contient des modifications non commitées par
    rapport à HEAD (fichiers suivis modifiés OU nouveaux fichiers pas
    encore ajoutés — `git status --porcelain` couvre les deux) —
    demande du 2026-09-09 : deux fenêtres lancées sur le même commit ne
    doivent pas afficher exactement le même repère si l'une tourne avec
    des modifications locales pas encore commitées. Faux par défaut si
    indéterminable (`git` absent, pas un dépôt...) : au pire l'étoile
    n'apparaît pas alors qu'elle aurait dû, jamais l'inverse trompeur.
    `repo_dir` : voir _short_git_hash, même usage réservé aux tests."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir or _repo_dir(),
            capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def dev_suffix():
    """Complément de version affiché uniquement en développement (voir
    App.__init__/_update_window_title dans main.py — jamais APP_VERSION
    lui-même, qui reste inchangé) : demande du 2026-09-09, "je ne veux
    pas devoir modifier version.py après chaque petite correction".

    - Build empaquetée (PyInstaller, voir sys.frozen — même détection
      déjà utilisée ailleurs dans ce projet pour distinguer un
      exécutable distribué d'un lancement depuis les sources) : chaîne
      VIDE, titre propre inchangé ("Poker Tournament v1.2.38").
    - Lancement depuis les sources, Git disponible : "-dev [HASH]" si le
      dépôt est propre, "-dev [HASH*]" s'il contient des modifications
      non commitées (voir _working_tree_dirty) — l'étoile permet de
      distinguer deux fenêtres lancées sur LE MÊME commit dont l'une
      aurait des changements locaux pas encore commitées, ce que le
      hash seul ne peut pas montrer.
    - Lancement depuis les sources, Git indisponible/dépôt introuvable :
      simple "-dev" (sans hash), pour rester visuellement distinct d'une
      build officielle même sans pouvoir identifier le commit précis.

    Calculé UNE SEULE FOIS par process (mis en cache) : un hash Git ne
    change jamais en cours d'exécution, inutile de relancer `git` à
    chaque rafraîchissement de titre (App._update_window_title est
    appelée à chaque changement de paramètre)."""
    global _DEV_SUFFIX_CACHE
    if _DEV_SUFFIX_CACHE is not None:
        return _DEV_SUFFIX_CACHE
    if getattr(sys, "frozen", False):
        _DEV_SUFFIX_CACHE = ""
        return _DEV_SUFFIX_CACHE
    commit_hash = _short_git_hash()
    if not commit_hash:
        _DEV_SUFFIX_CACHE = "-dev"
    elif _working_tree_dirty():
        _DEV_SUFFIX_CACHE = f"-dev [{commit_hash}*]"
    else:
        _DEV_SUFFIX_CACHE = f"-dev [{commit_hash}]"
    return _DEV_SUFFIX_CACHE
