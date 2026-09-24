# -*- coding: utf-8 -*-
"""
Gestionnaire de tournoi de poker — application de bureau.
Lancement : python main.py
Nécessite uniquement Python 3.8+ (Tkinter est inclus dans la distribution
standard de Python sous Windows/macOS ; sous Linux, installez le paquet
python3-tk si besoin).
"""
import os
import sys
import subprocess
import threading
import queue
import collections
import time
import json
import csv
import io
import shutil
import tempfile
import uuid
import calendar
import re
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog, colorchooser

from database import (
    Database, build_period_summary, export_period_summary_csv,
    export_period_summary_xlsx, export_period_summary_pdf,
    read_player_names_from_file, bounty_unit_value, find_players_active_elsewhere,
    find_stale_active_players, withdraw_stale_active_players,
    format_date_fr, format_datetime_fr,
    PERIOD_TOURNAMENT_COLUMNS, PERIOD_PLAYER_COLUMNS,
    RESULT_COLUMNS, PAYOUT_COLUMNS, PLAYERS_TAB_COLUMNS, PRIMES_COLUMNS,
    BOUNTY_HISTORY_COLUMNS, BB_REBALANCE_PROMPT_PREF_KEY, MOVE_REASON_LABELS,
    RANKING_FORMULA_NONE, RANKING_FORMULA_CURRENT, RANKING_FORMULA_PROGRESSIVE,
    RANKING_FORMULA_TOURNOIS_CPC, RANKING_FORMULA_SITNGO_CPC, RANKING_FORMULA_LABELS,
    REMOTE_PERMISSION_LABELS,
    STATS_TOURNAMENT_TYPE_TOURNOIS, STATS_TOURNAMENT_TYPE_SITNGO, STATS_TOURNAMENT_TYPE_ALL,
    STATS_WEEKDAY_FOLDER_NAMES,
)
from structures import (
    default_blind_structure, standard_payout_structure, generate_blind_structure,
    GENERATED_ROUNDS_COUNT,
)
from clock_window import ClockWindow
import roster
import tournament_prefs
import export_prefs
import blind_templates
import settings_templates
import chip_templates
import chip_images
import player_photos
import sound_signal
import remote_control
import open_windows
import action_log
import backup_restore
from help_browser import HelpBrowser, TAB_TO_CHAPTER
import license as licensing
from version import APP_NAME, APP_VERSION, dev_suffix


# Nom du fichier-marqueur qui identifie un exécutable compilé pour la
# ligne « TEST » (cohabitation avec une installation de production sur le
# même poste, demande du 2026-09-12 — voir windows/README.md, section
# "Version de TEST"). Embarqué UNIQUEMENT par windows/poker_tournament-
# test.spec (datas), JAMAIS par windows/poker_tournament.spec (le spec de
# production ne le référence nulle part) : un build de production normal
# ne peut donc jamais l'embarquer, même par erreur de fusion de branche,
# sans une modification explicite de CE spec-ci. Ne touche ni version.py
# (APP_VERSION reste inchangé) ni license.py (aucun rapport avec la
# licence) — seul l'AFFICHAGE (titre de fenêtre, "À propos") en tient
# compte, voir _is_test_build ci-dessous.
_TEST_BUILD_MARKER_FILENAME = "TEST_BUILD_MARKER"


def _is_test_build():
    """Vrai uniquement pour un exécutable compilé à partir de windows/
    poker_tournament-test.spec (voir _TEST_BUILD_MARKER_FILENAME
    ci-dessus) : ce spec embarque un petit fichier marqueur
    (windows/assets/TEST_BUILD_MARKER) à la racine du bundle, absent du
    spec de production. Même repli sys._MEIPASS / dossier du fichier
    source que help_browser._data_dir() pour fonctionner identiquement en
    build compilée (onedir, _MEIPASS pointe vers _internal/) et en
    lancement depuis les sources.

    En lancement depuis les sources (python main.py), le dossier vérifié
    est celui de main.py lui-même (racine du dépôt) — PAS windows/assets/
    — donc ce marqueur n'y est jamais trouvé : aucun effet sur le
    développement courant, uniquement sur un exécutable réellement
    compilé avec ce spec dédié. Fonction MODULE-LEVEL (pas une méthode),
    volontairement peu coûteuse (un seul appel os.path.exists) : appelée
    à chaque rafraîchissement de titre, jamais mise en cache (le marqueur
    ne peut de toute façon pas changer en cours d'exécution)."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.exists(os.path.join(base, _TEST_BUILD_MARKER_FILENAME))


# Nom du fichier optionnel portant le numéro identifiant CE build TEST
# précis (demande du 2026-09-14, "pouvoir distinguer immédiatement les
# différents MSI TEST" — ex. "[TEST 3]") — voir _test_build_number.
# Volontairement DISTINCT de _TEST_BUILD_MARKER_FILENAME ci-dessus (qui,
# lui, reste un simple booléen "est-ce un build TEST ?", jamais retiré) :
# ce numéro-ci est un simple COMPLÉMENT d'affichage, absent tant qu'un
# build TEST n'a pas été produit par le workflow GitHub Actions dédié
# (voir .github/workflows/build-msi-test.yml) — un build TEST local
# (windows/build.ps1-style, sans ce fichier généré au préalable) reste
# parfaitement fonctionnel, simplement affiché "[TEST]" sans numéro,
# comme avant cette demande.
_TEST_BUILD_NUMBER_FILENAME = "TEST_BUILD_NUMBER"


def _test_build_number():
    """Numéro identifiant CE build TEST précis (demande du 2026-09-14),
    ou None si absent — jamais codé en dur : le workflow GitHub Actions
    dédié (.github/workflows/build-msi-test.yml) écrit windows/assets/
    TEST_BUILD_NUMBER avec ${{ github.run_number }} — le numéro de run
    de CE workflow précis, qui s'incrémente TOUT SEUL à chaque
    déclenchement (1, 2, 3...) — juste avant d'appeler PyInstaller, sans
    jamais avoir à modifier le code pour un nouveau build TEST.

    Renvoie None (jamais une exception, jamais une valeur trompeuse) si
    : pas un build TEST du tout (voir _is_test_build, vérifié en
    premier) ; fichier absent (build local sans cette étape, ou ancien
    build TEST antérieur à cette demande) ; ou contenu illisible/vide.
    Voir _app_title_prefix/App._show_about pour l'affichage résultant
    ("[TEST N]" si un numéro est disponible, simplement "[TEST]" sinon
    — comportement strictement inchangé pour tout build TEST déjà
    existant, jamais une régression pour lui)."""
    if not _is_test_build():
        return None
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(base, _TEST_BUILD_NUMBER_FILENAME), "r", encoding="utf-8") as f:
            number = f.read().strip()
    except OSError:
        return None
    return number or None


def _menu_principal_key():
    """Clé de verrouillage du "Menu principal" (demande du 2026-09-14,
    voir open_windows.register_menu_principal/menu_principal_pid) —
    "test" ou "prod" selon _is_test_build() : Poker Senaco et Poker
    Senaco TEST doivent chacun garder leur propre instance unique, sans
    jamais se bloquer l'un l'autre (installations volontairement
    coexistantes sur un même poste, voir windows/README.md). Fonction
    MODULE-LEVEL (pas une méthode) : réutilisable telle quelle, sans
    construire de fenêtre, y compris dans les tests."""
    return "test" if _is_test_build() else "prod"


def _test_build_label():
    """"[TEST N]" si ce build TEST porte un numéro (voir
    _test_build_number — le numéro de run du workflow GitHub Actions qui
    l'a produit, demande du 2026-09-14), sinon simplement "[TEST]"
    (build TEST local, ou antérieur à cette demande), ou "" si ce n'est
    pas un build TEST du tout. Factorisé ici pour que _app_title_prefix
    (titre de fenêtre) et App._show_about ("À propos") affichent
    exactement le même texte, sans dupliquer cette logique à deux
    endroits."""
    if not _is_test_build():
        return ""
    number = _test_build_number()
    return f"[TEST {number}]" if number else "[TEST]"


def _app_title_prefix():
    """"[TEST N] {APP_NAME} v{APP_VERSION}[complément dev]" — préfixe
    commun à TOUS les titres de fenêtre de premier niveau (demande du
    2026-09-09) : Menu principal (voir App.__init__) ET fenêtre de
    tournoi (voir App._update_window_title), pour identifier
    immédiatement, PENDANT LE DÉVELOPPEMENT, quel commit (et si des
    modifications locales non commitées s'y ajoutent, voir version.
    dev_suffix) une fenêtre déjà ouverte fait réellement tourner — sans
    jamais avoir à toucher APP_VERSION à la main pour ça. En build
    officielle (PyInstaller) de PRODUCTION, dev_suffix() est vide : le
    titre reste strictement "{APP_NAME} v{APP_VERSION}", inchangé par ce
    correctif. Le préfixe "[TEST]"/"[TEST N]" (voir _test_build_label,
    demandes du 2026-09-12 et du 2026-09-14) s'ajoute, lui, uniquement
    pour un exécutable compilé avec windows/poker_tournament-test.spec —
    cohabite sans conflit avec dev_suffix() (les deux peuvent apparaître
    ensemble en théorie, mais en pratique un build "-test.spec" est
    toujours une build PyInstaller figée, donc dev_suffix() y est de
    toute façon vide). Fonction MODULE-LEVEL (pas une méthode) :
    réutilisable telle quelle, sans construire de fenêtre, y compris
    dans les tests."""
    prefix = f"{APP_NAME} v{APP_VERSION}{dev_suffix()}"
    label = _test_build_label()
    if label:
        prefix = f"{label} {prefix}"
    return prefix


def _format_players_count(n):
    """"1 joueur" au singulier, "N joueurs" au pluriel (y compris pour 0)
    — utilisé par l'onglet Tables (voir App._refresh_tables_tab, demande
    du 2026-09-10 : nombre de joueurs affiché dans le titre de chaque
    table et en total). Fonction MODULE-LEVEL (pas une méthode) :
    réutilisable telle quelle, sans construire de fenêtre, y compris
    dans les tests."""
    return f"{n} joueur" if n == 1 else f"{n} joueurs"


# Encodages essayés dans l'ordre pour décoder un CSV importé (Répertoire >
# Importer CSV, voir RosterManagerDialog._import_csv et _decode_csv_bytes
# ci-dessous) — demande du 2026-09-12. "utf-8-sig" couvre à la fois l'UTF-8
# simple et l'UTF-8 avec BOM ; "cp1252" (Windows-1252/ANSI) couvre les
# fichiers exportés par Excel sous Windows sans passer par UTF-8 (ex.
# "Jérome M" avec un é encodé en 0xE9, qui faisait échouer l'ouverture
# forcée en "utf-8-sig" d'avant ce correctif). Liste MODULE-LEVEL : reste
# patchable isolément dans les tests (voir tests/test_import_csv_encoding.
# py) pour simuler un fichier dans un troisième encodage non supporté —
# impossible à obtenir avec de vrais octets, puisque cp1252 associe un
# caractère à CHAQUE octet possible et ne peut donc jamais lui-même
# échouer.
IMPORT_CSV_ENCODINGS = ("utf-8-sig", "cp1252")


def _decode_csv_bytes(raw):
    """Décode `raw` (bytes lus depuis un fichier CSV importé) en essayant
    successivement IMPORT_CSV_ENCODINGS. Lève l'UnicodeDecodeError du
    DERNIER essai si aucun ne convient — voir RosterManagerDialog.
    _import_csv pour l'affichage du message à l'utilisateur dans ce cas.
    Fonction MODULE-LEVEL (pas une méthode) : testable isolément, sans
    construire de fenêtre."""
    last_error = None
    for encoding in IMPORT_CSV_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError as e:
            last_error = e
    raise last_error


# Textes COURTS affichés en permanence sous la Combobox "Système de
# points distribués" (onglet Paramètres), mis à jour immédiatement à
# chaque changement de sélection — voir _on_ranking_formula_display_
# changed dans _build_ranking_formula_widget. Contenu exact demandé.
# Les explications complètes de chaque formule (autrefois dans un
# popup d'aide "ⓘ", retiré le 2026-09-12 — voir la docstring de
# _build_ranking_formula_widget) vivent désormais dans le manuel
# utilisateur (MANUEL_UTILISATEUR_TOURNOI_CPC.docx, chapitre "Onglet
# Paramètres" / section Primes en détail).
RANKING_FORMULA_SHORT_TEXTS = {
    RANKING_FORMULA_NONE: "Aucun point attribué selon le classement.",
    RANKING_FORMULA_CURRENT: "100 × √N / P",
    RANKING_FORMULA_PROGRESSIVE: "100 × √N / √P",
    RANKING_FORMULA_TOURNOIS_CPC: "Barème total N × 1000 pts (plus grands restes)",
    RANKING_FORMULA_SITNGO_CPC: "1000 + 100(N+1) - 200P",
}
# Placeholder affiché (jamais une des 4 vraies valeurs) quand ce tournoi
# utilise encore l'ancien réglage "valeur fixe" (ranking_bonus_points,
# voir Database.resolve_ranking_formula) sans qu'aucun choix explicite
# n'ait encore été fait dans la nouvelle liste : tant que ce placeholder
# reste affiché, _collect_and_save_all_settings n'écrit RIEN dans
# ranking_formula (voir plus bas), pour ne jamais remplacer
# silencieusement la valeur fixe historique par une formule.
RANKING_FORMULA_LEGACY_PLACEHOLDER = "(valeur fixe historique — voir ci-dessous)"


# Écran de démarrage ("Chargement en cours...", voir
# windows/poker_tournament.spec) : le module pyi_splash n'existe que
# dans l'exécutable Windows compilé avec un écran de démarrage — absent
# en lancement depuis les sources ou sur macOS, d'où ce try/except.
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

# Journal de plantage (~/.poker_tournament/crash.log) : jusqu'ici, aucune
# exception non gérée n'était conservée nulle part — seulement affichée
# dans le terminal (perdue dès qu'il est fermé, ou jamais vue si l'appli
# tournait en arrière-plan). Utile pour diagnostiquer un plantage
# "silencieux" (l'appli disparaît sans qu'on ait pu voir pourquoi) :
# couvre le thread principal (sys.excepthook), les threads secondaires
# (threading.excepthook — ex : celui du serveur de contrôle à distance)
# et les exceptions survenant dans les callbacks Tkinter (clics de
# bouton, etc. — normalement juste affichées dans le terminal et
# avalées sans arrêter l'appli, voir App.report_callback_exception).
def _crash_log_path():
    d = os.path.join(os.path.expanduser("~"), ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "crash.log")


def _spawned_child_log_path():
    """Fichier de sortie standard/erreurs des process lancés par
    spawn_app_process (voir ci-dessous) — même répertoire que crash.log,
    créé au besoin."""
    d = os.path.join(os.path.expanduser("~"), ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "menu_principal_child.log")


def _log_exception(exc_type, exc_value, exc_tb):
    import traceback
    try:
        with open(_crash_log_path(), "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 70 + "\n")
            f.write(datetime.now().isoformat(timespec="seconds") + "\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=f)
    except OSError:
        pass
    # Conserve aussi l'affichage terminal habituel.
    traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)


def _install_crash_logging():
    sys.excepthook = _log_exception
    if hasattr(threading, "excepthook"):
        def _thread_hook(args):
            _log_exception(args.exc_type, args.exc_value, args.exc_traceback)
        threading.excepthook = _thread_hook

# Photos de joueurs (aperçu + capture caméra) : dépendances optionnelles.
# La copie/suppression des fichiers photo (player_photos.py) ne nécessite
# rien de plus que la bibliothèque standard ; seuls l'AFFICHAGE d'un
# aperçu et la capture webcam nécessitent Pillow (et OpenCV pour la
# caméra). Si absents, l'appli le signale avec des instructions
# d'installation plutôt que de planter.
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import cv2
    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

PLAYER_THUMB_SIZE = 28  # taille des vignettes dans le tableau des joueurs
ROSTER_PREVIEW_SIZE = 160  # taille de l'aperçu dans le répertoire
ROSTER_ROW_THUMB_SIZE = 28  # taille des vignettes dans le tableau du répertoire (colonne Photo)


def open_file_with_default_app(path):
    """Ouvre un fichier exporté avec l'application par défaut du système
    (Excel/LibreOffice pour .xlsx, l'application associée aux .csv...),
    pour éviter d'avoir à aller le rechercher manuellement après un
    export. Best-effort : une erreur ici n'annule pas l'export lui-même,
    déjà réussi à ce stade."""
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def show_missing_export_module(fmt):
    """Message d'erreur affiché quand la bibliothèque optionnelle requise
    par un format d'export (Excel -> openpyxl, PDF -> fpdf2) n'est pas
    installée — utilisé par toutes les fenêtres d'export (Joueurs, Primes,
    Résultats, Gains, Synthèse par période)."""
    module, pip_pkg, fmt_label = {
        "xlsx": ("openpyxl", "openpyxl", "Excel (.xlsx)"),
        "pdf": ("fpdf2", "fpdf2", "PDF"),
    }[fmt]
    messagebox.showerror(
        "Module manquant",
        f"L'export {fmt_label} nécessite le paquet '{module}', qui n'est "
        "pas installé.\n\nOuvrez un terminal et tapez :\n\n"
        f"    pip3 install {pip_pkg}\n\n"
        "puis relancez l'export. (Vous pouvez aussi choisir le format CSV, "
        "qui ne nécessite rien de plus.)",
    )


def load_thumbnail(path, size):
    """Charge une image en vignette carrée (recadrée) de `size` pixels.
    Renvoie None si le fichier est absent ou si Pillow n'est pas installé."""
    if not path or not PIL_AVAILABLE:
        return None
    try:
        img = Image.open(path)
        img = img.convert("RGB")
        # recadrage carré centré, puis redimensionnement
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize(
            (size, size), Image.LANCZOS
        )
        return ImageTk.PhotoImage(img)
    except Exception:
        return None

# Palette "table de poker" (feutre vert / doré / crème), utilisée dans
# toute l'application pour un rendu cohérent avec l'écran chronomètre.
FELT_DARK = "#0b241a"
FELT = "#123a29"
FELT_LIGHT = "#1c5940"
GOLD = "#e8c05c"
GOLD_DARK = "#c9a13e"
CREAM = "#f7f1e3"
CREAM_ALT = "#ece2c8"
TEXT_DARK = "#17281f"
MUTED = "#b9c9bd"  # texte discret, lisible sur fond foncé
DANGER_RED = "#8a1f1f"
DANGER_RED_ACTIVE = "#a92c2c"
ELIMINATION_BLUE = "#1f4e8a"  # bandeau d'élimination (écran projecteur + onglet Chronomètre)
# Variante plus claire pour l'état "survolé/pressé" du bouton "Annule
# Eliminer" (demande du 2026-09-17) — même principe que DANGER_RED_ACTIVE
# pour "Éliminer".
ELIMINATION_BLUE_ACTIVE = "#2f63a8"


def default_tournament_dir():
    """Dossier de départ proposé par les sélecteurs "Créer un nouveau
    tournoi" / "Créer un nouveau Sit & Go" : le dernier dossier utilisé
    pour créer un tournoi, ou le dossier personnel de l'utilisateur à
    défaut. Sans ça, le sélecteur macOS peut s'ouvrir sans dossier de
    départ précis (ex : la racine du disque, "/"), où un utilisateur
    normal n'a pas le droit d'écrire — ce qui faisait planter la création
    d'un tournoi avec "unable to open database file"."""
    last = export_prefs.load_value("last_tournament_dir")
    if last and os.path.isdir(last):
        return last
    return os.path.expanduser("~")


# Noms de sous-dossiers "jour de tournoi" (voir tournament_day_folder_proposal) :
# l'index correspond à datetime.weekday() (lundi=0 ... dimanche=6).
WEEKDAY_NAMES_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

# Jours cochés par défaut dans "Jours de tournoi / Sit & Go" (voir
# _build_settings_tab) avant tout réglage explicite du club : usage actuel
# du CPC (tournois le vendredi, Sit & Go le dimanche) — un point de départ
# raisonnable, pas une contrainte : un autre club peut cocher n'importe
# quel autre jour (ex. jeudi).
DEFAULT_TOURNAMENT_DAYS = "4,6"


def tournament_day_folder_proposal(is_sng=False):
    """Renvoie (dossier_proposé, nom_de_fichier_proposé) pour "Nouveau
    tournoi" / "Sit & Go rapide", à partir de "Dossier par défaut" (voir
    _build_settings_tab), mémorisé globalement dans tournament_prefs
    (dernier tournoi en date, retrouvable même avant l'ouverture d'un
    fichier .tournoi) : sous-dossier nommé d'après le jour de la semaine
    du jour (lundi/mardi/.../dimanche — purement organisationnel, voir
    WEEKDAY_NAMES_FR), créé s'il n'existe pas encore, et nom de fichier
    To/Sn + date du jour (JJMMAA), ex. "To270826". Le préfixe dépend de
    `is_sng` (donc du bouton effectivement cliqué — "Nouveau tournoi" ou
    "Sit & Go rapide"), PAS du jour de la semaine : créer un tournoi
    normal un dimanche doit donner "To...", pas "Sn..." (qui laisserait
    croire à tort que c'était un Sit & Go).
    Si "Dossier par défaut" n'est pas configuré : sous Windows (poste
    club réel), repli sur C:\\poker\\senaco plutôt que d'abandonner —
    l'installation standard Senaco ; ailleurs (Mac de développement/test,
    où ce chemin Windows n'aurait aucun sens), renvoie (None, None) et
    laisse les repos de secours habituels s'appliquer
    (default_tournament_dir(), "tournoi.tournoi"...)."""
    base = tournament_prefs.load_last_settings().get("tournament_day_folder", "")
    base = (base or "").strip()
    if not base:
        if sys.platform.startswith("win"):
            base = r"C:\poker\senaco"
        else:
            return None, None
    weekday = datetime.now().weekday()  # lundi=0 ... dimanche=6
    subfolder = WEEKDAY_NAMES_FR[weekday]
    prefix = "Sn" if is_sng else "To"
    folder = os.path.join(base, subfolder)
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        return None, None
    filename = f"{prefix}{datetime.now().strftime('%d%m%y')}.tournoi"
    return folder, filename


# Marqueur d'environnement (demande du 2026-09-14, unicité du Menu
# principal) posé UNIQUEMENT par spawn_app_process(internal_menu_child=
# True) — voir App._open_new_window, seul appelant à le passer — pour
# distinguer de façon fiable un processus enfant VOLONTAIRE (bouton "🏠
# Menu principal", au même titre que le Lobby) d'un vrai second
# lancement externe accidentel (raccourci Windows/macOS re-double-
# cliqué) : un double-clic sur le raccourci ne peut, par construction,
# jamais porter cette variable (l'OS ne la connaît pas), contrairement à
# `open_path` qui, lui, ne suffit pas à distinguer ces deux cas (les
# deux valent None). Voir App.__init__ pour la vérification, et
# open_windows.py (section "Unicité du Menu principal") pour le verrou
# lui-même.
POKER_TOURNAMENT_INTERNAL_LAUNCH = "POKER_TOURNAMENT_INTERNAL_LAUNCH"


def spawn_app_process(extra_args=None, internal_menu_child=False):
    """Lance une nouvelle instance indépendante de l'application (autre
    processus). `extra_args` : arguments supplémentaires passés au
    programme — notamment le chemin d'un fichier .tournoi à ouvrir
    directement, sans passer par l'écran d'accueil (voir
    App.__init__/open_path, et LobbyDialog qui l'utilise pour "Ouvrir"
    un tournoi de la liste dans sa propre fenêtre). Renvoie l'objet
    Popen. Lève OSError si le lancement échoue (à l'appelant de gérer).

    `internal_menu_child=True` (demande du 2026-09-14, UNIQUEMENT passé
    par App._open_new_window) : marque le nouveau processus comme un
    "Menu principal" supplémentaire VOLONTAIRE (voir
    POKER_TOURNAMENT_INTERNAL_LAUNCH ci-dessus), exempté du verrou
    d'unicité — sans ce marqueur, `open_path=None` seul ne permettrait
    pas de le distinguer d'un vrai second lancement externe accidentel.

    `stdin=subprocess.DEVNULL` : le nouveau process reçoit un stdin neuf
    et toujours valide, plutôt que d'hériter du descripteur de fichier 0
    de SON parent — qui peut déjà être invalide après plusieurs
    fermetures/ouvertures de fenêtres en chaîne (notamment via "Fin de
    la partie" depuis le téléphone). Sans ça, l'initialisation de CPython
    lui-même pouvait échouer dès le tout premier démarrage du nouveau
    process, AVANT tout code Python : "Fatal Python error:
    init_sys_streams: can't initialize sys standard streams / OSError:
    [Errno 9] Bad file descriptor" — le process mourait sans jamais rien
    afficher, symptôme "Menu principal" qui ne "faisait rien" observé
    après plusieurs tournois fermés depuis le téléphone (diagnostiqué en
    capturant réellement stdout/stderr du process mort, voir l'historique
    git de ce fichier).

    stdout/stderr : même raisonnement, étendu le 2026-09-18 (diagnostic
    "Menu principal" -> "le nouveau processus s'est arrêté immédiatement
    (code 1)" sur une instance restée ouverte tout un après-midi de
    tests). Constaté via `lsof` sur le vrai process parent au moment du
    bug : ses fd 1/2 étaient "(revoked)" par macOS (terminal/session
    d'origine disparu depuis) — hérités tels quels par le nouveau
    process (stdout/stderr n'étaient PAS redirigés jusqu'ici, seul stdin
    l'était), provoquant exactement le même "Fatal Python error:
    init_sys_streams" que ci-dessus, mais sur fd 1/2 cette fois : trop
    tôt pour que _install_crash_logging() (ou le try/except de
    `if __name__ == "__main__":`) puisse l'intercepter, et de toute
    façon imprimé sur un stderr lui-même invalide — d'où crash.log
    resté muet malgré deux reproductions réelles. Rediriger stdout ET
    stderr vers un fichier neuf (_spawned_child_log_path(), ouvert par
    CE process donc jamais revoked) élimine cette dépendance à l'état
    des descripteurs du parent, quelle que soit son ancienneté."""
    extra_args = list(extra_args or [])
    env = None
    if internal_menu_child:
        env = os.environ.copy()
        env[POKER_TOURNAMENT_INTERNAL_LAUNCH] = "1"
    with open(_spawned_child_log_path(), "a", encoding="utf-8") as log_file:
        # Popen duplique ce descripteur pour le nouveau process (voir la
        # doc standard de subprocess) : ce fichier peut être refermé ici
        # (fin du `with`, juste après la création du process) sans que le
        # nouveau process perde son propre accès, déjà indépendant.
        if getattr(sys, "frozen", False):
            # Application empaquetée (PyInstaller) : sys.executable est déjà
            # le programme lui-même, pas besoin de lui repasser main.py.
            proc = subprocess.Popen(
                [sys.executable, *extra_args],
                stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, env=env,
            )
        else:
            proc = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), *extra_args],
                stdin=subprocess.DEVNULL, stdout=log_file, stderr=log_file, env=env,
            )
    return proc


# Rétention des identifiants de requête déduplication (demande du
# 2026-09-19, "bétonner la communication téléphone <-> PC") — voir
# App._remote_eliminate_request/_prune_remote_action_dedup. 5 minutes :
# largement suffisant pour couvrir un retry réseau réaliste (coupure
# Wi-Fi, timeout), jamais une fuite mémoire sur une longue soirée.
_REMOTE_ACTION_DEDUP_TTL_SECONDS = 300

SINGLE_TOURNAMENT_PREF_KEY = "single_tournament_at_a_time"

# Onglet LOG (chantier "LOG", Phase 2, 2026-09-24) : format de saisie
# strict JJ/MM/AAAA pour "Du"/"Au" — validé AVANT strptime, jamais
# seulement délégué à lui : "%Y" de strptime accepte aussi une année à
# 1-3 chiffres ("01/01/26" serait alors interprété comme l'an 26), ce
# que cette regex exclut d'abord.
_LOG_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

# Ancien plafond de 500 lignes affichées/exportées par une recherche LOG
# (décision du 2026-09-24) — ABANDONNÉ le 2026-09-25 ("CE QUI CORRESPOND
# AUX FILTRES = CE QUI EST AFFICHÉ = CE QUI EST EXPORTÉ = CE QUI PEUT
# ÊTRE PURGÉ") : _refresh_log_tab appelle désormais action_log.
# search_actions avec limit=None (aucune troncature). Voir _refresh_log_
# tab/_log_count_label_text pour le compteur "N opération(s) affichée(s)"
# qui remplace l'ancien message de troncature.


def _single_tournament_pref_enabled():
    """Préférence globale "Un seul tournoi à la fois" (onglet Paramètres,
    juste sous "Équilibrage guidé par UTG") — cochée par
    défaut, mémorisée indépendamment de chaque tournoi (voir
    export_prefs, déjà utilisé pour d'autres préférences globales
    comparables, ex : le délai de "Son prochain changement Blindes").
    N'affecte que les DEMANDES de lancement d'un nouveau process (voir
    _other_tournament_is_open/_block_second_tournament_if_needed) —
    lire cette valeur ne touche à aucun tournoi déjà ouvert."""
    return export_prefs.load_value(SINGLE_TOURNAMENT_PREF_KEY, True) is not False


def _other_tournament_is_open():
    """True si au moins un tournoi est actuellement ouvert (n'importe
    lequel — voir plus bas pourquoi jamais d'exclusion de "soi-même"),
    d'après le registre partagé (open_windows.list_open_paths(), qui
    ignore déjà proprement les PID morts via _prune — voir sa
    docstring).

    AUCUNE exclusion d'un chemin "own_path" ici (une version antérieure
    en prenait un et excluait le tournoi appelant de la comparaison) :
    chaque appelant (_open_new_window, LobbyDialog._open_selected,
    new_tournament/new_sng/open_tournament) s'apprête à ouvrir un
    tournoi SUPPLÉMENTAIRE — la question à se poser est toujours "en
    existe-t-il déjà au moins un", jamais "en existe-t-il un AUTRE que
    moi". Exclure sa propre fenêtre était correct pour une tout autre
    question (voir find_open_pid : "CE chemin précis est-il déjà
    ouvert ailleurs", utilisé pour basculer vers un tournoi déjà actif),
    mais faux ici : depuis le DERNIER tournoi restant après la fermeture
    d'un autre, "Menu principal" avec cette exclusion ignorait ce
    tournoi restant lui-même et autorisait à tort d'en ouvrir un
    second — repéré lors d'un test manuel (option cochée APRÈS avoir
    ouvert plusieurs tournois, puis fermeture de tous sauf un)."""
    return bool(open_windows.list_open_paths())


def _block_second_tournament_if_needed(parent):
    """Point de contrôle central de la préférence "Un seul tournoi à la
    fois" : à appeler AVANT tout spawn_app_process() qui lancerait un
    NOUVEAU tournoi/Sit&Go (jamais avant de basculer vers un tournoi
    déjà ouvert, voir LobbyDialog._open_selected — ce cas garde son
    comportement actuel de premier plan, jamais bloqué), ainsi qu'en
    filet de sécurité au tout début de new_tournament/new_sng/
    open_tournament (voir _choose_tournament_file) — au cas où un
    lancement passerait malgré l'état grisé de ces boutons (voir
    _refresh_launch_buttons_state), ex. une fenêtre restée ouverte
    depuis avant que la préférence ne soit activée. Renvoie True (et
    affiche le message d'explication) si le lancement doit être refusé ;
    False sinon, sans aucun effet de bord. Ne ferme ni ne modifie jamais
    le tournoi déjà ouvert : une simple vérification avant de créer un
    nouveau process, rien de plus."""
    if not _single_tournament_pref_enabled():
        return False
    if not _other_tournament_is_open():
        return False
    messagebox.showinfo(
        "Un seul tournoi à la fois",
        "Un tournoi est déjà ouvert.\n\n"
        "Désactivez « Un seul tournoi à la fois » dans Paramètres pour "
        "autoriser plusieurs tournois simultanés.",
        parent=parent,
    )
    return True


def _refresh_launch_buttons_state(win, buttons):
    """Active/désactive `buttons` (les commandes "Nouveau tournoi"/
    "Sit & Go rapide"/"Ouvrir un tournoi existant" de _choose_tournament_
    file — jamais "Lobby", qui doit rester accessible pour basculer vers
    un tournoi DÉJÀ ouvert) selon "Un seul tournoi à la fois" et l'état
    RÉEL du registre partagé (_other_tournament_is_open — toujours
    recalculé ici, jamais mémorisé). Se reprogramme elle-même tant que
    `win` existe (win.after, 1 seconde — assez réactif sans sonder trop
    souvent) : détecte donc aussi bien la fermeture du dernier tournoi
    ailleurs (réactive) que l'ouverture d'un premier tournoi (grise), et
    un changement de la préférence elle-même pendant que cet écran
    reste affiché, sans dépendre d'un quelconque tick déjà existant
    (aucun ici : cette fenêtre s'affiche AVANT que _tick ne soit jamais
    lancé, voir App.__init__). Ne remplace pas _block_second_tournament_
    if_needed, déjà présent en filet de sécurité au tout début de chaque
    commande — une simple couche d'interface en plus."""
    if not win.winfo_exists():
        return
    disabled = _single_tournament_pref_enabled() and _other_tournament_is_open()
    state = "disabled" if disabled else "normal"
    for btn in buttons:
        btn.configure(state=state)
    win.after(1000, lambda: _refresh_launch_buttons_state(win, buttons))


# --- Interrupteur général "Calculer les primes" (demande du 2026-09-09) ---
#
# Objectif : un seul réglage, valable pour TOUTE la session de
# l'application (tous les tournois déjà ouverts, pas encore démarrés, ou
# créés plus tard dans la même session), modifiable depuis les Paramètres
# de N'IMPORTE LEQUEL de ces tournois tant qu'AUCUN d'entre eux n'a
# encore démarré son chronomètre, puis définitivement figé pour tous dès
# que le PREMIER démarre (voir _clock_resume) — et qui se déverrouille de
# nouveau uniquement une fois TOUS fermés (plus aucune entrée dans
# open_windows.list_open_paths()).
#
# Architecture (2 niveaux, cf. discussion avec l'utilisateur) :
#   1. Une valeur "proposée" globale, dans export_prefs.json (même
#      mécanisme, mêmes garanties, que SINGLE_TOURNAMENT_PREF_KEY
#      ci-dessus) : PRIMES_ENABLED_PROPOSED_KEY. Simple reflet de "ce que
#      l'utilisateur a coché en dernier" avant tout verrouillage.
#   2. La valeur réellement utilisée par chaque calcul est TOUJOURS la
#      copie locale en base SQLite de CE tournoi (`primes_enabled`, voir
#      Database._primes_enabled) — jamais lue directement depuis
#      export_prefs par database.py, qui n'a connaissance d'aucune notion
#      de session.
# Tant que la session n'est pas verrouillée, main.py maintient ces deux
# valeurs synchronisées activement (voir _sync_primes_enabled_pref,
# appelée sans condition à chaque tick de CHAQUE fenêtre ouverte, et une
# dernière fois juste avant que _clock_resume ne démarre le chrono) :
# ainsi, AUCUNE copie locale figée à la création d'un tournoi ne peut
# rester en retard sur un changement fait depuis une autre fenêtre — la
# convergence est garantie en un peu moins d'une seconde, bien avant
# qu'aucun tournoi n'ait eu la moindre chance de démarrer entre-temps
# (démarrer un chronomètre est une action utilisateur explicite, jamais
# automatique). Le verrouillage lui-même n'est JAMAIS un drapeau
# persisté à part : il est entièrement DÉRIVÉ, à la demande, de l'état
# réel (clock_started) des tournois actuellement ouverts d'après le
# registre partagé — voir _primes_session_locked. Ce choix évite tout
# risque de drapeau de verrouillage resté bloqué après un plantage/une
# fermeture brutale (même robustesse que _other_tournament_is_open, qui
# repose déjà sur ce même registre auto-nettoyé).

PRIMES_ENABLED_PROPOSED_KEY = open_windows.PRIMES_ENABLED_PROPOSED_KEY


def _primes_enabled_proposed():
    """Valeur globale "proposée" pour "Calculer les primes" (voir
    bloc de commentaires ci-dessus) — cochée par défaut, comme
    Database.DEFAULT_SETTINGS["primes_enabled"]="1" pour rester
    cohérent avec la compatibilité des anciens tournois. Ne représente
    PAS forcément la valeur en vigueur si la session est verrouillée sur
    une valeur différente d'une session précédente non nettoyée — c'est
    toujours la copie SQLite de chaque tournoi qui fait foi pour les
    calculs, jamais cette valeur directement.

    Simple délégation à open_windows.primes_enabled_proposed() (demande
    du 2026-09-09, point 2, CORRIGÉE après un vrai bug détecté par
    tests/test_primes_multi_process_real_subprocess.py) : c'est CE
    module qui porte l'auto-réinitialisation "nouvelle session = ON par
    défaut" (vérifiée à chaque lecture, pas seulement à l'écriture),
    car c'est lui qui connaît déjà list_open_paths()."""
    return open_windows.primes_enabled_proposed()


def _set_primes_enabled_proposed(value):
    """Modifie la valeur "proposée" (case à cocher des Paramètres) — à
    n'appeler que si `_primes_session_locked()` est faux (voir la case
    elle-même, désactivée sinon). N'a par elle-même aucun effet sur les
    tournois déjà ouverts : c'est `_sync_primes_enabled_pref`, rappelée
    par chaque fenêtre à chaque tick, qui répercute ce changement dans
    leur copie SQLite locale respective. Délègue à open_windows.
    set_primes_enabled_proposed (même raison que ci-dessus)."""
    open_windows.set_primes_enabled_proposed(value)


def _primes_session_locked():
    """True si le PREMIER tournoi de la session ACTUELLE a déjà démarré
    son chronomètre, tant qu'il reste au moins un tournoi de cette
    session encore ouvert — voir open_windows.mark_primes_session_
    started/primes_session_started, qui portent le drapeau réel.

    CORRECTION du 2026-09-09 (relecture utilisateur) : la version
    précédente ici re-scannait à chaque appel `clock_started` sur
    chaque tournoi ACTUELLEMENT ouvert, et considérait la session
    déverrouillée dès qu'AUCUN d'eux n'avait `clock_started=1` — ce qui
    déverrouillait à tort dès la fermeture du tournoi qui avait démarré,
    même si un AUTRE tournoi de la même session (jamais démarré,
    ex. un Sit & Go créé en attendant) restait ouvert. Voir
    open_windows.primes_session_started : le drapeau "un tournoi de
    cette session a démarré" est maintenant mémorisé séparément (posé
    une fois pour toutes par _clock_resume) et ne redevient faux que
    lorsque list_open_paths() est complètement VIDE (tous les tournois
    de la session fermés — PID morts déjà exclus par _prune, donc
    robuste à un plantage), jamais simplement "plus aucun DÉMARRÉ
    actuellement ouvert"."""
    return open_windows.primes_session_started()


def _sync_primes_enabled_pref(db):
    """Fait converger la copie SQLite locale de `db` (CE tournoi) vers la
    valeur globale proposée (`_primes_enabled_proposed`), UNIQUEMENT si
    ce tournoi précis n'a pas encore démarré son propre chronomètre —
    une fois démarré, sa valeur est définitivement la sienne (figée,
    voir le bloc de commentaires plus haut) et ne doit plus jamais être
    réécrite, y compris si la session reste "non verrouillée" du point
    de vue d'un AUTRE tournoi pas encore démarré (cas normal : un
    tournoi démarré verrouille toute la session, mais tant qu'il reste
    ouvert son propre réglage ne doit évidemment plus bouger).
    À appeler sans aucune condition préalable (pas seulement si l'onglet
    Paramètres est affiché) à chaque tick de chaque fenêtre de tournoi,
    et une dernière fois juste avant que _clock_resume ne démarre
    effectivement le chrono (resynchronisation défensive de dernière
    minute, referme toute fenêtre de course résiduelle)."""
    if db.get_setting_int("clock_started", 0) == 1:
        return
    wanted = "1" if _primes_enabled_proposed() else "0"
    if db.get_setting("primes_enabled", "1") != wanted:
        db.set_setting("primes_enabled", wanted)


def _align_primes_enabled_on_open(db):
    """Aligne `db.primes_enabled` sur l'état VERROUILLÉ de la session
    AVANT que ce tournoi ne puisse être utilisé (demande du 2026-09-09,
    4e relecture utilisateur) : si la session est DÉJÀ verrouillée
    (open_windows.primes_session_started), ce tournoi — NOUVEAU ou
    EXISTANT, peu importe sa propre valeur antérieure — doit
    immédiatement adopter la valeur verrouillée, authoritative pour
    TOUS les tournois qui rejoignent la session après ce verrouillage.
    Seule l'activation/désactivation (ce réglage précis) est concernée :
    les montants propres à CE tournoi (présence, assiduité, classement,
    bounty, PKO...) ne sont jamais touchés ici, ni son historique.

    À appeler UNE FOIS, juste après open_windows.register(self.db.path)
    dans App.__init__ (voir plus bas) — donc AVANT _build_tabs()/
    _build_settings_tab(), pour que la case et le grisement de la
    section reflètent le bon état dès la toute première image affichée
    (jamais l'ancien état qui apparaîtrait puis changerait au tick
    suivant). Cas d'usage typique découvert le 2026-09-09 : App.
    _new_tournament/_open_tournament ferme la fenêtre actuelle (donc la
    désenregistre) puis en ouvre une autre DANS LE MÊME PROCESS — si
    cette fenêtre était la seule ouverte, le registre passe par un état
    temporairement vide entre les deux, ce qui pouvait faire lire à tort
    l'ancienne valeur "proposée" (déjà réinitialisée entre-temps, voir
    _primes_enabled_proposed) au lieu de la valeur RÉELLEMENT verrouillée
    de la session en cours — cette fonction s'appuie plutôt sur la
    valeur verrouillée elle-même (open_windows.locked_primes_enabled),
    mémorisée une seule fois pour de bon au moment du verrouillage
    (voir App._clock_resume), jamais perdue tant que la session reste
    active.

    Ne touche jamais un tournoi déjà démarré (sa valeur lui appartient
    définitivement, comme _sync_primes_enabled_pref) — cas normalement
    déjà couvert (un tournoi qu'on rouvre alors qu'il a déjà démarré
    verrouille de toute façon la session sur SA PROPRE valeur, voir
    App._clock_resume), simple garde de cohérence supplémentaire ici."""
    if db.get_setting_int("clock_started", 0) == 1:
        return
    if not open_windows.primes_session_started():
        return
    wanted = "1" if open_windows.locked_primes_enabled() else "0"
    if db.get_setting("primes_enabled", "1") != wanted:
        db.set_setting("primes_enabled", wanted)


def raise_process_when_ready(widget, pid, attempt=0):
    """Tente de faire passer au premier plan le processus `pid` tout
    juste lancé par spawn_app_process (macOS et Windows, voir
    open_windows.bring_pid_to_front — no-op silencieux ailleurs).
    `widget` : n'importe quel widget Tk vivant, utilisé seulement pour
    planifier les tentatives (.after) — pas besoin que ce soit la
    fenêtre App elle-même. Plusieurs essais espacés de 700 ms : le temps
    que Tk démarre et affiche sa fenêtre dans le nouveau processus varie,
    et sa fenêtre n'existe pas encore lors des tout premiers essais.
    Échoue silencieusement si l'accès Accessibilité n'est pas accordé
    (macOS) à l'application qui lance ceci (Terminal, IDE...) — la
    fenêtre reste alors ouverte, juste pas mise en avant automatiquement.

    Si le process a disparu ENTRE deux tentatives (déjà vivant à l'appel
    précédent, mort depuis — le cas "mort immédiate" est, lui, détecté
    plus tôt par App._open_new_window), n'appelle plus bring_pid_to_front
    sur un pid mort et surtout ne reprogramme PAS d'autre tentative :
    plus jamais "essayer dans le vide puis abandonner sans rien dire"."""
    try:
        os.kill(pid, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    except OSError:
        alive = True  # existe, appartient à un autre utilisateur, etc.
    if not alive:
        return
    open_windows.bring_pid_to_front(pid)
    if attempt < 5:
        widget.after(700, lambda: raise_process_when_ready(widget, pid, attempt + 1))


class Tooltip:
    """Petite bulle d'aide qui apparaît au survol d'un widget (après un
    court délai) et disparaît dès que la souris le quitte ou qu'on clique.
    Usage : Tooltip(mon_widget, "texte d'aide")."""

    def __init__(self, widget, text, delay=500, wraplength=320):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, event=None):
        self._unschedule()
        self._after_id = self.widget.after(self.delay, self._show)

    def _unschedule(self):
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self):
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        try:
            self._tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            self._tip, text=self.text, justify="left",
            background=CREAM, foreground=TEXT_DARK,
            relief="solid", borderwidth=1,
            wraplength=self.wraplength, padx=8, pady=6,
            font=("Helvetica", 9),
        ).pack()

    def _hide(self, event=None):
        self._unschedule()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


CLUB_FILTER_ALL_LABEL = "Tous"  # pseudo-entrée du filtre "Club" (voir get_selected_clubs)


def _populate_club_filter_listbox(listbox):
    """Remplit `listbox` avec une entrée "Tous" (voir get_selected_clubs),
    suivie des clubs connus du répertoire (roster.py) et de celui réglé
    comme "Nom du Club" dans Paramètres même s'il n'a encore aucun joueur
    — puis présélectionne ce dernier ("Tous" à défaut). Utilisé par le
    filtre "Club" du Classement et de Statistiques (voir
    build_club_filter_widget)."""
    default_club = export_prefs.load_value("club_name", "").strip()
    clubs = set(roster.list_clubs())
    if default_club:
        clubs.add(default_club)
    clubs = sorted(clubs, key=str.lower)
    items = [CLUB_FILTER_ALL_LABEL] + clubs
    listbox.delete(0, "end")
    for c in items:
        listbox.insert("end", c)
    if default_club and default_club in clubs:
        listbox.selection_set(items.index(default_club))
    else:
        listbox.selection_set(0)
    return items


def build_club_filter_widget(parent, on_change, padx=(20, 0)):
    """Construit le filtre "Club" (libellé + liste à sélection multiple)
    partagé par les onglets Classement et Statistiques : ne garder dans
    le tableau que les joueurs des clubs cochés (Ctrl/Shift + clic pour
    en cocher plusieurs). Par défaut, seul le club réglé dans Paramètres
    ("Nom du Club") est coché. Cocher "Tous" (ou ne rien cocher) désactive
    le filtre : tous les clubs sont affichés (voir get_selected_clubs).
    Un simple clic (sans Ctrl) sur "Tous" ou sur un club désélectionne
    automatiquement le reste (comportement natif du Listbox), ce qui
    suffit à basculer proprement entre "un club précis" et "tous".
    `on_change` est appelé (sans argument) à chaque changement de
    sélection. Empaquette lui-même le widget dans `parent` (côté gauche,
    `padx` réglable pour l'espacement avec ce qui précède) et renvoie le
    Listbox (à repasser à get_selected_clubs)."""
    frame = ttk.Frame(parent)
    frame.pack(side="left", padx=padx)
    ttk.Label(frame, text="Club :").pack(side="left", anchor="n", pady=2)
    list_wrap = ttk.Frame(frame)
    list_wrap.pack(side="left", padx=(6, 0))
    listbox = tk.Listbox(
        list_wrap, selectmode="extended", exportselection=False,
        height=4, width=20,
    )
    scrollbar = ttk.Scrollbar(list_wrap, orient="vertical", command=listbox.yview)
    listbox.configure(yscrollcommand=scrollbar.set)
    listbox.pack(side="left")
    scrollbar.pack(side="left", fill="y")
    _populate_club_filter_listbox(listbox)
    listbox.bind("<<ListboxSelect>>", lambda e: on_change())
    Tooltip(
        frame,
        "Sélectionnez un ou plusieurs clubs (Ctrl/Shift + clic) pour ne\n"
        "garder que leurs joueurs dans le tableau, ou « Tous » pour ne\n"
        "filtrer sur aucun club. Par défaut : le club réglé dans\n"
        "Paramètres.",
    )
    return listbox


def get_selected_clubs(listbox):
    """Clubs actuellement cochés dans `listbox` (voir
    build_club_filter_widget) ; liste vide si aucun coché, ou si "Tous"
    en fait partie (= pas de filtre, quels que soient les autres clubs
    cochés en même temps)."""
    selected = [listbox.get(i) for i in listbox.curselection()]
    if CLUB_FILTER_ALL_LABEL in selected:
        return []
    return selected


class TreeHeadingTooltip:
    """Variante de Tooltip pour les en-têtes de colonnes d'un
    ttk.Treeview (qui ne sont pas des widgets individuels) : `column_texts`
    est un dict {identifiant_de_colonne: texte} — les vraies clés de
    colonnes (celles passées à Treeview(columns=...) / .heading()), pas des
    index "#N" : ceux-ci dépendent de l'ordre/visibilité des colonnes
    affichées (displaycolumns), qui peut changer (colonnes masquées,
    triées...), donc on les retraduit systématiquement en identifiant réel
    via `_resolve_column`. Le texte s'affiche quand la souris survole
    l'en-tête concerné."""

    def __init__(self, tree, column_texts, delay=500, wraplength=320):
        self.tree = tree
        self.column_texts = column_texts
        self.delay = delay
        self.wraplength = wraplength
        self._after_id = None
        self._tip = None
        self._current_col = None
        tree.bind("<Motion>", self._on_motion, add="+")
        tree.bind("<Leave>", self._hide, add="+")
        tree.bind("<ButtonPress>", self._hide, add="+")

    def _resolve_column(self, col_num):
        """Traduit un index Tk ('#0', '#1', ...) en identifiant de colonne
        réel, en tenant compte des colonnes actuellement masquées/réordonnées
        (displaycolumns). '#0' est la colonne arbre elle-même."""
        if col_num == "#0":
            return "#0"
        try:
            idx = int(col_num.lstrip("#")) - 1
        except ValueError:
            return None
        display_cols = self.tree.cget("displaycolumns")
        if not display_cols or display_cols == "#all":
            display_cols = self.tree.cget("columns")
        try:
            return display_cols[idx]
        except (IndexError, TypeError):
            return None

    def _on_motion(self, event):
        if self.tree.identify_region(event.x, event.y) != "heading":
            self._hide()
            return
        col = self._resolve_column(self.tree.identify_column(event.x))
        if col != self._current_col:
            self._hide()
            self._current_col = col
            if col and self.column_texts.get(col):
                self._after_id = self.tree.after(self.delay, lambda: self._show(event))

    def _show(self, event):
        if self._tip is not None:
            return
        text = self.column_texts.get(self._current_col)
        if not text:
            return
        x = self.tree.winfo_rootx() + event.x + 12
        y = self.tree.winfo_rooty() + event.y + 18
        self._tip = tk.Toplevel(self.tree)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        try:
            self._tip.attributes("-topmost", True)
        except tk.TclError:
            pass
        tk.Label(
            self._tip, text=text, justify="left",
            background=CREAM, foreground=TEXT_DARK,
            relief="solid", borderwidth=1,
            wraplength=self.wraplength, padx=8, pady=6,
            font=("Helvetica", 9),
        ).pack()

    def _hide(self, event=None):
        if self._after_id is not None:
            self.tree.after_cancel(self._after_id)
            self._after_id = None
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
        self._current_col = None


class PlayerSelectionDialog(tk.Toplevel):
    """Fenêtre permettant de cocher/décocher, parmi le répertoire de joueurs
    habituels, ceux qui participent au nouveau tournoi. Permet aussi
    d'ajouter un nouveau nom au répertoire à la volée."""

    def __init__(self, master, title="Joueurs participants",
                 confirm_text="Créer le tournoi", cancel_text="Annuler",
                 exclude_names=None, conflict_folder=None, conflict_exclude_path=None,
                 conflict_date=None):
        super().__init__(master)
        self.title(title)
        self.geometry("500x560")
        # transient() lie cette fenêtre à la fenêtre principale : sans ça,
        # un clic sur la fenêtre principale pouvait la faire passer devant
        # cette boîte de dialogue (grab_set() bloque bien les clics, mais
        # ne force pas l'ordre d'affichage) — donnant l'impression qu'elle
        # « disparaissait » et perdait le focus alors qu'elle était juste
        # masquée derrière.
        self.transient(master)
        self.grab_set()
        self.selected_names = []
        self.check_vars = {}
        self.exclude_names = exclude_names or set()
        # Contrairement à avant, on ne retire plus purement et simplement
        # les joueurs déjà inscrits à CE tournoi (self.exclude_names) de la
        # liste : ça les faisait disparaître sans explication (ex : un
        # joueur tout juste ajouté depuis l'onglet Joueurs restait
        # introuvable ici, alors qu'il était bien enregistré au répertoire
        # — visible seulement depuis "Gérer le répertoire"). Ils sont
        # maintenant affichés grisés/non cochables, comme les joueurs
        # "déjà actifs ailleurs" juste en dessous — _check_all/_check_club/
        # _confirm continuent de les ignorer pour ne jamais les ajouter en
        # double.
        self.roster_names = list(roster.load_roster())
        self.sort_state = {"column": "name", "ascending": True}
        self.header_labels = {}
        # Joueurs déjà actifs dans un autre tournoi en cours du même dossier
        # ET daté du même jour (ex : un autre Sit & Go ce soir) : grisés et
        # non cochables ci-dessous — voir find_players_active_elsewhere
        # (database.py). conflict_folder est None pour un usage sans
        # contexte de dossier connu (aucun grisage dans ce cas). Idem si
        # "Éviter qu'un joueur joue à deux tables à la fois" est décoché
        # dans Paramètres (préférence partagée, voir _build_settings_tab) :
        # personne n'est grisé.
        if export_prefs.load_value("check_multi_table_conflict", True):
            self.active_elsewhere = find_players_active_elsewhere(
                conflict_folder, self.roster_names,
                exclude_path=conflict_exclude_path, date=conflict_date,
            )
        else:
            self.active_elsewhere = set()

        ttk.Label(
            self, text="Cochez les joueurs concernés :",
            font=("Helvetica", 11, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 4))

        search_frame = ttk.Frame(self)
        search_frame.pack(fill="x", padx=12)
        ttk.Label(search_frame, text="Rechercher :").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=5)
        self.search_var.trace_add("write", lambda *a: self._filter())

        btns_top = ttk.Frame(self)
        btns_top.pack(fill="x", padx=12, pady=6)
        ttk.Button(btns_top, text="Tout cocher", command=self._check_all).pack(side="left", padx=3)
        ttk.Button(btns_top, text="Tout décocher", command=self._uncheck_all).pack(side="left", padx=3)
        ttk.Button(
            btns_top, text="Importer d'un tournoi précédent...",
            command=self._import_from_previous_tournament,
        ).pack(side="left", padx=3)

        # Cocher/décocher d'un coup tous les joueurs du répertoire
        # rattachés à un même club (voir roster.get_club / list_clubs),
        # pratique quand un club entier participe au tournoi plutôt que
        # de cocher chaque joueur un par un.
        club_check_frame = ttk.Frame(self)
        club_check_frame.pack(fill="x", padx=12, pady=(0, 6))
        ttk.Label(club_check_frame, text="Club :").pack(side="left")
        self.club_filter_var = tk.StringVar()
        self.club_filter_combo = ttk.Combobox(
            club_check_frame, textvariable=self.club_filter_var, width=16,
            values=roster.list_clubs(), state="readonly",
        )
        self.club_filter_combo.pack(side="left", padx=5)
        ttk.Button(
            club_check_frame, text="Cocher ce club", command=self._check_club,
        ).pack(side="left", padx=3)
        ttk.Button(
            club_check_frame, text="Décocher ce club", command=self._uncheck_club,
        ).pack(side="left", padx=3)

        container = ttk.Frame(self)
        container.pack(fill="both", expand=True, padx=12, pady=5)
        canvas = tk.Canvas(container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        self.list_frame = ttk.Frame(canvas)
        self.list_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        if not self.roster_names:
            ttk.Label(
                self, foreground=MUTED,
                text="(Répertoire vide pour l'instant — ajoutez des joueurs ci-dessous.\n"
                     "Ils seront proposés automatiquement pour vos prochains tournois.)",
            ).pack(padx=12, pady=(0, 5), anchor="w")

        # Liste actuellement affichée (recherche en cours comprise) — tenue
        # à jour par _filter ; initialisée ici pour que "Tout cocher"/"Tout
        # décocher" aient une valeur valide même avant toute frappe dans le
        # champ Rechercher (voir _check_all/_uncheck_all).
        self.visible_names = self.roster_names
        self._build_list(self.roster_names)

        add_frame = ttk.LabelFrame(self, text="Ajouter un joueur au répertoire")
        add_frame.pack(fill="x", padx=12, pady=8)
        self.new_name_var = tk.StringVar()
        entry = ttk.Entry(add_frame, textvariable=self.new_name_var, width=16)
        entry.pack(side="left", fill="x", expand=True, padx=(8, 4), pady=8)
        entry.bind("<Return>", lambda e: self._add_new_name())
        ttk.Label(add_frame, text="Club :").pack(side="left", padx=(4, 0))
        # Préempli avec le "Nom du Club" réglé dans Paramètres (commun à
        # tous les tournois/Sit & Go, voir _build_settings_tab) plutôt
        # que vide — la plupart des joueurs ajoutés ici viennent du même
        # club que celui qui organise le tournoi.
        self.new_name_club_var = tk.StringVar(value=export_prefs.load_value("club_name", ""))
        self.new_name_club_combo = ttk.Combobox(
            add_frame, textvariable=self.new_name_club_var, width=14,
            values=roster.list_clubs(),
        )
        self.new_name_club_combo.pack(side="left", padx=4, pady=8)
        self.new_name_club_combo.bind("<Return>", lambda e: self._add_new_name())
        ttk.Button(add_frame, text="Ajouter", command=self._add_new_name).pack(side="left", padx=8)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=12, pady=12)
        ttk.Button(bottom, text=cancel_text, command=self._skip).pack(side="left")
        ttk.Button(bottom, text=confirm_text, command=self._confirm).pack(side="right")

    def _build_list(self, names):
        for w in self.list_frame.winfo_children():
            w.destroy()
        self.header_labels = {}
        if names:
            for col, key, label in ((0, "name", "Joueur"), (1, "club", "Club")):
                hdr = ttk.Label(
                    self.list_frame, font=("Helvetica", 9, "bold"),
                    foreground=GOLD_DARK, cursor="hand2",
                )
                hdr.grid(row=0, column=col, sticky="w", padx=(0, 20) if col == 0 else 0)
                hdr.bind("<Button-1>", lambda e, k=key: self._sort_by(k))
                self.header_labels[key] = hdr
            self._update_sort_headers(label_texts={"name": "Joueur", "club": "Club"})
        for idx, name in enumerate(self._sorted(names), start=1):
            var = self.check_vars.get(name)
            if var is None:
                var = tk.BooleanVar(value=False)
                self.check_vars[name] = var
            conflicted = name in self.active_elsewhere
            already_here = name in self.exclude_names
            disabled = conflicted or already_here
            if disabled:
                var.set(False)
            if already_here:
                suffix = "  (déjà dans ce tournoi)"
            elif conflicted:
                suffix = "  (déjà actif ailleurs)"
            else:
                suffix = ""
            check = ttk.Checkbutton(
                self.list_frame,
                text=name + suffix,
                variable=var, state="disabled" if disabled else "normal",
            )
            check.grid(row=idx, column=0, sticky="w", pady=1, padx=(0, 20))
            if already_here:
                Tooltip(
                    check,
                    "Déjà inscrit à ce tournoi (ajouté depuis l'onglet\n"
                    "Joueurs, ou depuis cette fenêtre plus tôt) — non\n"
                    "sélectionnable ici pour éviter de l'ajouter en double.",
                )
            elif conflicted:
                Tooltip(
                    check,
                    "Ce joueur est actuellement actif dans un autre tournoi\n"
                    "du même dossier (ex : un autre Sit & Go en cours) —\n"
                    "non sélectionnable ici pour éviter de l'inscrire à deux\n"
                    "endroits à la fois.",
                )
            club = roster.get_club(name)
            club_lbl = ttk.Label(
                self.list_frame, text=club or "+ ajouter un club",
                foreground=MUTED if club else GOLD_DARK,
                font=("Helvetica", 9, "italic") if not club else ("Helvetica", 9),
                cursor="hand2",
            )
            club_lbl.grid(row=idx, column=1, sticky="w", pady=1)
            # Cliquer sur le club (ou sur l'invite s'il n'y en a pas encore)
            # ouvre la même boîte de dialogue que "Modifier le club..." dans
            # la fenêtre Répertoire, pour le gérer directement depuis ici.
            club_lbl.bind("<Button-1>", lambda e, n=name: self._edit_club_for(n))

    def _sorted(self, names):
        col = self.sort_state["column"]
        if col == "club":
            key = lambda n: (roster.get_club(n) or "").lower()
        else:
            key = lambda n: n.lower()
        result = sorted(names, key=key)
        if not self.sort_state["ascending"]:
            result.reverse()
        return result

    def _update_sort_headers(self, label_texts):
        for key, hdr in self.header_labels.items():
            text = label_texts[key]
            if self.sort_state["column"] == key:
                text += " ▲" if self.sort_state["ascending"] else " ▼"
            hdr.configure(text=text)

    def _sort_by(self, column):
        """Clic sur l'en-tête Joueur/Club : trie la liste, ré-appuyer
        inverse l'ordre (croissant <-> décroissant)."""
        if self.sort_state["column"] == column:
            self.sort_state["ascending"] = not self.sort_state["ascending"]
        else:
            self.sort_state["column"] = column
            self.sort_state["ascending"] = True
        self._filter()

    def _filter(self):
        term = self.search_var.get().strip().lower()
        filtered = [n for n in self.roster_names if term in n.lower()] if term else self.roster_names
        self.visible_names = filtered
        self._build_list(filtered)

    def _check_all(self):
        # Sur les seuls joueurs actuellement AFFICHÉS (recherche en cours
        # comprise) — pas tout le répertoire : sinon, taper "an" dans
        # Rechercher puis "Tout cocher" cochait bien les 2 joueurs visibles
        # mais AUSSI, en silence, tous les autres joueurs du répertoire
        # (jamais réaffichés avant de cliquer "Ajouter les joueurs
        # sélectionnés", d'où la confirmation avec 20 joueurs au lieu de 2).
        for name in self.visible_names:
            if name in self.active_elsewhere or name in self.exclude_names:
                continue  # déjà actif ailleurs / déjà dans ce tournoi : jamais coché, même par "Tout cocher"
            self.check_vars.setdefault(name, tk.BooleanVar()).set(True)
        self._filter()

    def _uncheck_all(self):
        for name in self.visible_names:
            self.check_vars.setdefault(name, tk.BooleanVar()).set(False)
        self._filter()

    def _check_club(self):
        club = self.club_filter_var.get().strip()
        if not club:
            return
        for name in self.roster_names:
            if (roster.get_club(name) == club
                    and name not in self.active_elsewhere and name not in self.exclude_names):
                self.check_vars.setdefault(name, tk.BooleanVar()).set(True)
        self._filter()

    def _uncheck_club(self):
        club = self.club_filter_var.get().strip()
        if not club:
            return
        for name in self.roster_names:
            if roster.get_club(name) == club:
                self.check_vars.setdefault(name, tk.BooleanVar()).set(False)
        self._filter()

    def _add_new_name(self):
        name = self.new_name_var.get().strip()
        if not name:
            return
        club = self.new_name_club_var.get().strip()
        if name not in self.roster_names:
            roster.add_to_roster(name, club=club or None)
            self.roster_names = roster.load_roster()
            self.new_name_club_combo.configure(values=roster.list_clubs())
            self.club_filter_combo.configure(values=roster.list_clubs())
        elif club:
            roster.set_club(name, club)
        self.check_vars.setdefault(name, tk.BooleanVar()).set(True)
        self.new_name_var.set("")
        # Revient au club par défaut (Paramètres), pas à vide : la plupart
        # des ajouts suivants viendront probablement du même club.
        self.new_name_club_var.set(export_prefs.load_value("club_name", ""))
        # Si un texte de recherche encore actif ne correspond pas au nom
        # qu'on vient d'ajouter (ex : on avait tapé "Bob" dans Rechercher
        # pour vérifier qu'il n'existait pas, avant d'ajouter "Chloé
        # Nouvelle"), le nouveau joueur resterait coché mais invisible
        # dans la liste ci-dessus, sans confirmation visuelle — on vide
        # donc la recherche dans ce cas précis pour qu'il apparaisse.
        term = self.search_var.get().strip().lower()
        if term and term not in name.lower():
            self.search_var.set("")
        self._filter()

    def _import_from_previous_tournament(self):
        """Reprend uniquement la liste des noms de joueurs d'un fichier
        .tournoi précédent (pas leurs chips, place, buy-ins...) : les
        coche ici, et les ajoute au répertoire s'ils n'y figurent pas
        déjà, pour que le nouveau tournoi reparte de zéro pour chacun."""
        path = filedialog.askopenfilename(
            title="Importer les joueurs d'un tournoi précédent",
            filetypes=[("Fichier de tournoi", "*.tournoi"), ("Tous les fichiers", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            names = read_player_names_from_file(path)
        except Exception as e:
            messagebox.showerror(
                "Erreur", f"Impossible de lire les joueurs de ce fichier :\n{e}",
                parent=self,
            )
            return
        if not names:
            messagebox.showinfo(
                "Aucun joueur", "Ce tournoi ne contient aucun joueur.", parent=self,
            )
            return
        already_present = [n for n in names if n in self.exclude_names]
        names = [n for n in names if n not in self.exclude_names]
        added_to_roster = 0
        for name in names:
            if name not in self.roster_names:
                roster.add_to_roster(name)
                added_to_roster += 1
            self.check_vars.setdefault(name, tk.BooleanVar()).set(True)
        if added_to_roster:
            self.roster_names = roster.load_roster()
        self._filter()
        msg = (
            f"{len(names)} joueur(s) importé(s) et coché(s) "
            f"(sans leurs performances du tournoi précédent)."
        )
        if already_present:
            msg += f"\n{len(already_present)} joueur(s) déjà présent(s) ignoré(s)."
        messagebox.showinfo("Import terminé", msg, parent=self)

    def _edit_club_for(self, name):
        club = ask_club_dialog(self, title=f"Club de {name}", current_club=roster.get_club(name))
        if club is not None:
            roster.set_club(name, club)
            self.new_name_club_combo.configure(values=roster.list_clubs())
            self.club_filter_combo.configure(values=roster.list_clubs())
            self._filter()

    def _skip(self):
        self.selected_names = []
        self.destroy()

    def _confirm(self):
        self.selected_names = [
            n for n, v in self.check_vars.items()
            if v.get() and n not in self.active_elsewhere and n not in self.exclude_names
        ]
        self.destroy()


class CameraCaptureDialog(tk.Toplevel):
    """Fenêtre de capture photo via la caméra de l'ordinateur (aperçu en
    direct + bouton pour figer/valider l'image). Nécessite les paquets
    optionnels opencv-python et Pillow (voir vérification à l'appel)."""

    def __init__(self, master, player_name, on_saved=None):
        super().__init__(master)
        self.player_name = player_name
        self.on_saved = on_saved
        self.title(f"Prendre une photo — {player_name}")
        self.geometry("520x480")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.configure(bg=FELT_DARK)

        self._cap = None
        self._live = True
        self._frozen_frame = None  # image PIL figée en attente de validation

        ttk.Label(
            self, text=f"Photo de {player_name}",
            font=("Helvetica", 12, "bold"),
        ).pack(pady=(12, 6))

        self.video_lbl = tk.Label(self, bg="black", width=480, height=360)
        self.video_lbl.pack(padx=12, pady=6)

        btns = ttk.Frame(self)
        btns.pack(pady=10)
        self.capture_btn = ttk.Button(btns, text="📸  Capturer", command=self._capture)
        self.capture_btn.pack(side="left", padx=5)
        self.retake_btn = ttk.Button(btns, text="↺  Reprendre", command=self._retake, state="disabled")
        self.retake_btn.pack(side="left", padx=5)
        self.save_btn = ttk.Button(btns, text="✓  Valider", command=self._save, state="disabled")
        self.save_btn.pack(side="left", padx=5)
        ttk.Button(btns, text="Annuler", command=self._on_close).pack(side="left", padx=5)

        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self._cap = cv2.VideoCapture(0)
        if not self._cap.isOpened():
            messagebox.showerror(
                "Caméra indisponible",
                "Impossible d'accéder à la caméra. Vérifiez qu'elle n'est pas "
                "utilisée par une autre application et que l'accès à la "
                "caméra est autorisé pour cette application dans les "
                "réglages système.",
            )
            self._on_close()
            return
        self._update_frame()

    def _update_frame(self):
        if not self._live or self._cap is None:
            return
        ok, frame = self._cap.read()
        if ok:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb).resize((480, 360))
            photo = ImageTk.PhotoImage(img)
            self.video_lbl.configure(image=photo)
            self.video_lbl.image = photo  # garder une référence
        self.after(30, self._update_frame)

    def _capture(self):
        if self._cap is None:
            return
        ok, frame = self._cap.read()
        if not ok:
            return
        self._live = False
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        self._frozen_frame = Image.fromarray(rgb)
        photo = ImageTk.PhotoImage(self._frozen_frame.resize((480, 360)))
        self.video_lbl.configure(image=photo)
        self.video_lbl.image = photo
        self.capture_btn.config(state="disabled")
        self.retake_btn.config(state="normal")
        self.save_btn.config(state="normal")

    def _retake(self):
        self._frozen_frame = None
        self._live = True
        self.capture_btn.config(state="normal")
        self.retake_btn.config(state="disabled")
        self.save_btn.config(state="disabled")
        self._update_frame()

    def _save(self):
        if self._frozen_frame is None:
            return
        crop_dlg = CropDialog(self, self._frozen_frame)
        self.wait_window(crop_dlg)
        if crop_dlg.result is None:
            # L'utilisateur a annulé le cadrage : on reste sur la photo
            # figée, prête à être recadrée à nouveau ou reprise.
            return
        try:
            player_photos.save_photo_from_image(self.player_name, crop_dlg.result)
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'enregistrer la photo :\n{e}")
            return
        if self.on_saved:
            self.on_saved()
        self._on_close()

    def _on_close(self):
        self._live = False
        if self._cap is not None:
            self._cap.release()
        self.destroy()


class CropDialog(tk.Toplevel):
    """Fenêtre pour cadrer (recadrer) une photo avant de l'enregistrer :
    zone de sélection carrée sur l'image, qu'on déplace (glisser à
    l'intérieur) ou redimensionne (glisser un coin) à la souris. Nécessite
    Pillow — à l'appelant de vérifier PIL_AVAILABLE avant d'ouvrir cette
    fenêtre. `self.result` contient l'image PIL recadrée après fermeture,
    ou None si l'utilisateur a annulé."""

    MAX_DISPLAY = 440
    HANDLE_HIT = 14  # rayon (px, à l'écran) de détection d'un coin
    MIN_BOX = 30  # taille minimale (px, à l'écran) du cadre

    def __init__(self, master, pil_image, title="Cadrer la photo"):
        super().__init__(master)
        self.title(title)
        self.configure(bg=FELT_DARK)
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.original = pil_image.convert("RGB")
        self.result = None

        ow, oh = self.original.size
        self.scale = min(self.MAX_DISPLAY / ow, self.MAX_DISPLAY / oh, 1.0)
        self.disp_w = max(1, round(ow * self.scale))
        self.disp_h = max(1, round(oh * self.scale))

        tk.Label(
            self, bg=FELT_DARK, fg=CREAM,
            text="Glissez à l'intérieur du cadre pour le déplacer, ou un coin pour le redimensionner :",
            wraplength=self.disp_w,
        ).pack(padx=12, pady=(12, 6))

        self.canvas = tk.Canvas(
            self, width=self.disp_w, height=self.disp_h,
            highlightthickness=0, cursor="crosshair",
        )
        self.canvas.pack(padx=12, pady=(0, 6))
        self._tk_img = ImageTk.PhotoImage(self.original.resize((self.disp_w, self.disp_h)))
        self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img)

        side = min(self.disp_w, self.disp_h)
        x0 = (self.disp_w - side) / 2
        y0 = (self.disp_h - side) / 2
        self.box = [x0, y0, x0 + side, y0 + side]
        self.rect_id = self.canvas.create_rectangle(*self.box, outline=GOLD, width=2)
        self._handle_ids = {
            corner: self.canvas.create_rectangle(0, 0, 0, 0, fill=GOLD, outline="")
            for corner in ("nw", "ne", "sw", "se")
        }
        self._redraw_handles()

        self._drag_mode = None  # None | "move" | "nw"/"ne"/"sw"/"se" | "new"
        self._drag_anchor = None  # coin fixe lors d'un resize, ou point de clic lors d'un move/new
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)

        btns = ttk.Frame(self)
        btns.pack(pady=(4, 12))
        ttk.Button(btns, text="Valider", command=self._confirm).pack(side="left", padx=5)
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="left", padx=5)

    def _redraw_handles(self):
        x0, y0, x1, y1 = self.box
        h = self.HANDLE_HIT / 2
        corners = {"nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1)}
        for name, (cx, cy) in corners.items():
            self.canvas.coords(self._handle_ids[name], cx - h, cy - h, cx + h, cy + h)

    def _redraw(self):
        self.canvas.coords(self.rect_id, *self.box)
        self._redraw_handles()

    def _corner_at(self, x, y):
        x0, y0, x1, y1 = self.box
        for name, (cx, cy) in {"nw": (x0, y0), "ne": (x1, y0), "sw": (x0, y1), "se": (x1, y1)}.items():
            if abs(x - cx) <= self.HANDLE_HIT and abs(y - cy) <= self.HANDLE_HIT:
                return name
        return None

    def _on_press(self, event):
        x, y = event.x, event.y
        corner = self._corner_at(x, y)
        x0, y0, x1, y1 = self.box
        if corner:
            self._drag_mode = corner
            opposite = {"nw": (x1, y1), "ne": (x0, y1), "sw": (x1, y0), "se": (x0, y0)}
            self._drag_anchor = opposite[corner]
        elif x0 <= x <= x1 and y0 <= y <= y1:
            self._drag_mode = "move"
            self._drag_anchor = (x - x0, y - y0)  # décalage clic <-> coin nw
        else:
            self._drag_mode = "new"
            self._drag_anchor = (x, y)

    def _on_drag(self, event):
        x = max(0, min(self.disp_w, event.x))
        y = max(0, min(self.disp_h, event.y))
        if self._drag_mode == "move":
            offx, offy = self._drag_anchor
            side = self.box[2] - self.box[0]
            nx0 = max(0, min(self.disp_w - side, x - offx))
            ny0 = max(0, min(self.disp_h - side, y - offy))
            self.box = [nx0, ny0, nx0 + side, ny0 + side]
        elif self._drag_mode in ("nw", "ne", "sw", "se", "new"):
            ax, ay = self._drag_anchor
            # Taille carrée souhaitée d'après le mouvement de la souris,
            # puis bornée pour que le cadre reste dans le canevas quelle
            # que soit la direction du glissement depuis le coin fixe `ax,ay`.
            size = max(self.MIN_BOX, max(abs(x - ax), abs(y - ay)))
            max_size_x = (self.disp_w - ax) if x >= ax else ax
            max_size_y = (self.disp_h - ay) if y >= ay else ay
            size = min(size, max_size_x, max_size_y)
            sx = ax + size if x >= ax else ax - size
            sy = ay + size if y >= ay else ay - size
            self.box = [min(ax, sx), min(ay, sy), max(ax, sx), max(ay, sy)]
        self._redraw()

    def _confirm(self):
        x0, y0, x1, y1 = self.box
        ox0, oy0 = x0 / self.scale, y0 / self.scale
        ox1, oy1 = x1 / self.scale, y1 / self.scale
        self.result = self.original.crop((round(ox0), round(oy0), round(ox1), round(oy1)))
        self.destroy()


# Mémorisation de position de "Club de XXX" (ask_club_dialog, demande du
# 2026-09-16) : mêmes principes que App._ask_eliminator_position/_save_
# ask_eliminator_position/_on_ask_eliminator_window_configure (export_
# prefs, préférence PERSISTANTE ; repli sur None — Tk choisit alors sa
# position par défaut — si jamais mémorisée ou devenue hors écran, voir
# App._is_position_onscreen, réutilisé tel quel). En fonctions MODULE-
# LEVEL, jamais des méthodes d'App : ask_club_dialog est une fonction
# libre, appelée aussi bien depuis RosterManagerDialog que depuis
# PlayerSelectionDialog — une seule position mémorisée, PARTAGÉE par
# tous ses appelants (déplacer la fenêtre une fois depuis n'importe
# lequel la mémorise pour tous les suivants, quel que soit l'appelant).
def _ask_club_dialog_position(master):
    x = export_prefs.load_value("ask_club_window_x", None)
    y = export_prefs.load_value("ask_club_window_y", None)
    if (
        isinstance(x, int) and isinstance(y, int)
        and App._is_position_onscreen(x, y, master.winfo_screenwidth(), master.winfo_screenheight())
    ):
        return x, y
    return None


def _save_ask_club_dialog_position(x, y):
    export_prefs.save_value("ask_club_window_x", x)
    export_prefs.save_value("ask_club_window_y", y)


def _on_ask_club_dialog_configure(event, win):
    """CORRECTIF du 2026-09-17 (même diagnostic, même cause que App._on_
    ask_eliminator_window_configure — voir sa docstring pour le détail
    complet) : `win` est passé explicitement (capturé par la lambda de
    ask_club_dialog) plutôt que de faire confiance à `event.widget`, qui
    peut être n'importe quel enfant de `win` (Label, Combobox, Bouton —
    leur <Configure> remonte jusqu'à ce gestionnaire via les bindtags de
    `win`) et dont winfo_x()/winfo_y() renverraient alors une position
    relative à SON PARENT, pas la position écran de la fenêtre."""
    if event.widget is not win:
        return
    try:
        _save_ask_club_dialog_position(win.winfo_x(), win.winfo_y())
    except tk.TclError:
        pass


def ask_club_dialog(master, title="Club", current_club=""):
    """Petite fenêtre pour choisir un club dans une liste déroulante des
    clubs déjà connus du répertoire, ou en saisir un nouveau. Renvoie le
    club choisi/saisi (chaîne, éventuellement vide), ou None si annulé.

    Position flottante mémorisée (demande du 2026-09-16) : voir
    _ask_club_dialog_position ci-dessus."""
    win = tk.Toplevel(master)
    win.title(title)
    win.configure(bg=FELT_DARK)
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()
    position = _ask_club_dialog_position(master)
    if position is not None:
        win.geometry(f"+{position[0]}+{position[1]}")
    win.bind("<Configure>", lambda e: _on_ask_club_dialog_configure(e, win))
    result = {"club": None}

    tk.Label(
        win, bg=FELT_DARK, fg=CREAM, text="Club (choisir dans la liste, ou saisir un nouveau) :",
    ).pack(padx=16, pady=(16, 6))

    var = tk.StringVar(value=current_club)
    combo = ttk.Combobox(win, textvariable=var, values=roster.list_clubs(), width=30)
    combo.pack(padx=16, pady=(0, 16))
    combo.focus_set()

    def confirm():
        result["club"] = var.get().strip()
        win.destroy()

    def cancel():
        win.destroy()

    combo.bind("<Return>", lambda e: confirm())
    btns = ttk.Frame(win)
    btns.pack(pady=(0, 16))
    ttk.Button(btns, text="Annuler", command=cancel).pack(side="left", padx=5)
    ttk.Button(btns, text="Valider", command=confirm).pack(side="left", padx=5)

    win.wait_window(win)
    return result["club"]


_ROSTER_GROUP_DISPLAY_NONE = "(Non classé)"
_ROSTER_GROUP_DISPLAY_VALUES = [_ROSTER_GROUP_DISPLAY_NONE, roster.ROSTER_GROUP_ADMIN, roster.ROSTER_GROUP_DIRTO]


def ask_group_dialog(master, title="Groupe", current_group=""):
    """Petite fenêtre pour choisir le groupe ADMIN/DIRTO d'une personne
    du répertoire, ou "non classé" (demande du 2026-09-20, chantier
    "Sécurisation du Contrôle à distance"). Liste READONLY (jamais de
    saisie libre, contrairement à ask_club_dialog ci-dessus) : "group"
    est une valeur stricte parmi roster.ROSTER_GROUPS, jamais un texte
    arbitraire — une personne appartient à UN SEUL groupe à la fois,
    jamais les deux simultanément (décision explicite de l'utilisateur).
    Renvoie "ADMIN", "DIRTO", "" (non classé), ou None si annulé."""
    win = tk.Toplevel(master)
    win.title(title)
    win.configure(bg=FELT_DARK)
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()
    result = {"group": None}

    tk.Label(
        win, bg=FELT_DARK, fg=CREAM,
        text="Groupe (ADMIN ou DIRTO — jamais les deux) :",
    ).pack(padx=16, pady=(16, 6))

    display_current = current_group if current_group in roster.ROSTER_GROUPS else _ROSTER_GROUP_DISPLAY_NONE
    var = tk.StringVar(value=display_current)
    combo = ttk.Combobox(
        win, textvariable=var, values=_ROSTER_GROUP_DISPLAY_VALUES,
        width=20, state="readonly",
    )
    combo.pack(padx=16, pady=(0, 16))
    combo.focus_set()

    def confirm():
        chosen = var.get()
        result["group"] = "" if chosen == _ROSTER_GROUP_DISPLAY_NONE else chosen
        win.destroy()

    def cancel():
        win.destroy()

    combo_btns = ttk.Frame(win)
    combo_btns.pack(pady=(0, 16))
    ttk.Button(combo_btns, text="Annuler", command=cancel).pack(side="left", padx=5)
    ttk.Button(combo_btns, text="Valider", command=confirm).pack(side="left", padx=5)

    win.wait_window(win)
    return result["group"]


_DEVICE_OWNER_DISPLAY_NONE = "(Aucun — appareil non lié)"


def ask_device_owner_dialog(master, title="Propriétaire de l'appareil", current_owner=""):
    """Petite fenêtre pour attribuer (ou retirer, via "(Aucun...)") le
    propriétaire d'un appareil approuvé du Contrôle à distance — Phase 2
    du chantier "Sécurisation du Contrôle à distance" (2026-09-20).
    Liste READONLY (comme ask_group_dialog) de toutes les personnes du
    Répertoire appartenant au groupe ADMIN ou DIRTO (roster.list_by_
    group), chacune annotée de son groupe pour lever toute ambiguïté à
    l'écran ("Alice — ADMIN", "Bob — DIRTO") — jamais une saisie libre :
    seule une personne RÉELLEMENT classée ADMIN/DIRTO dans le Répertoire
    au moment de l'ouverture de cette fenêtre peut être choisie
    (garantit, dès la source, qu'aucun nom hors Répertoire ni aucune
    personne "non classée" ne puisse jamais devenir propriétaire d'un
    appareil — la validation n'a donc pas besoin d'être refaite côté
    open_windows.set_remote_device_owner, simple magasin de données).

    Opération LOCALE Tkinter UNIQUEMENT (voir RosterManagerDialog/la
    section Contrôle à distance de _build_settings_tab) : le DIRTO ne
    choisit JAMAIS lui-même son identité depuis le téléphone.

    Renvoie le NOM choisi (chaîne), "" (option "(Aucun...)" — retire la
    liaison), ou None si annulé/fermé."""
    win = tk.Toplevel(master)
    win.title(title)
    win.configure(bg=FELT_DARK)
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()
    result = {"owner": None}

    tk.Label(
        win, bg=FELT_DARK, fg=CREAM,
        text="Propriétaire (une personne ADMIN ou DIRTO du Répertoire) :",
    ).pack(padx=16, pady=(16, 6))

    admins = roster.list_by_group(roster.ROSTER_GROUP_ADMIN)
    dirtos = roster.list_by_group(roster.ROSTER_GROUP_DIRTO)
    # {libellé affiché -> nom brut} : le Combobox n'expose QUE le
    # libellé annoté, jamais le nom seul (qui pourrait ambiguïser deux
    # personnes de groupes différents partageant par hasard le même nom
    # à l'écran) — la valeur RENVOYÉE par cette fonction reste bien le
    # nom brut, via ce dict inverse.
    display_to_name = {}
    display_values = [_DEVICE_OWNER_DISPLAY_NONE]
    for e in admins + dirtos:
        display = f"{e['name']} — {e['group']}"
        display_to_name[display] = e["name"]
        display_values.append(display)

    # Pré-remplissage : si le propriétaire actuel n'est plus ADMIN/DIRTO
    # (reclassé, supprimé du Répertoire depuis l'attribution), retombe
    # sur "(Aucun...)" plutôt que d'afficher un libellé qui ne
    # correspondrait à aucune entrée de la liste.
    display_current = _DEVICE_OWNER_DISPLAY_NONE
    for display, name in display_to_name.items():
        if name == current_owner:
            display_current = display
            break

    var = tk.StringVar(value=display_current)
    combo = ttk.Combobox(
        win, textvariable=var, values=display_values, width=32, state="readonly",
    )
    combo.pack(padx=16, pady=(0, 16))
    combo.focus_set()

    def confirm():
        chosen = var.get()
        result["owner"] = "" if chosen == _DEVICE_OWNER_DISPLAY_NONE else display_to_name.get(chosen, "")
        win.destroy()

    def cancel():
        win.destroy()

    combo_btns = ttk.Frame(win)
    combo_btns.pack(pady=(0, 16))
    ttk.Button(combo_btns, text="Annuler", command=cancel).pack(side="left", padx=5)
    ttk.Button(combo_btns, text="Valider", command=confirm).pack(side="left", padx=5)

    win.wait_window(win)
    return result["owner"]


class RosterManagerDialog(ttk.Frame):
    """Onglet "Répertoire" de la fenêtre principale — gestion du
    répertoire de joueurs habituels, indépendante de tout tournoi en
    cours. Anciennement une fenêtre à part (tk.Toplevel) ouverte depuis
    le menu Répertoire ; devenue un onglet du notebook (voir
    App._build_tabs), d'où ce ttk.Frame comme classe de base — le
    contenu (construction des widgets ci-dessous, méthodes d'action)
    n'a pas changé, seul le contenant a changé de nature. Le F1
    contextuel est géré globalement (voir TAB_TO_CHAPTER,
    help_browser.py), plus besoin d'un bind dédié ici."""

    def __init__(self, master, app):
        super().__init__(master)
        # master = le notebook (parent Tk réel du widget, voir
        # App._build_tabs) ; app = l'instance App elle-même — distincts
        # depuis que cette fenêtre est devenue un onglet plutôt qu'un
        # Toplevel ouvert avec App comme master direct (voir la même
        # distinction dans PeriodSummaryDialog, juste à côté).
        self.app = app
        self._preview_photo = None  # référence gardée pour éviter le garbage collect
        self.roster_sort = {"column": "name", "ascending": True}

        # Titre en flux normal (pack), compact — plus dans une rangée
        # partagée avec le tableau "Joueurs par club" : les deux forçaient
        # sinon la rangée entière à la hauteur du plus grand des deux
        # (le tableau), laissant un vide sous ce titre bien plus court.
        ttk.Label(
            self, text="Joueurs habituels (proposés à la création d'un tournoi)",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        # Tableau "Joueurs par club" : posé par-dessus (place(), pas pack())
        # en haut à droite de l'onglet, plutôt qu'inséré dans le flux
        # normal des widgets empilés verticalement — c'est justement pour
        # ne pas influencer la hauteur du titre ci-dessus (ni celle
        # d'aucune autre rangée) que ce widget est sorti du flux. "Sans"
        # regroupe les joueurs sans club, toujours affiché en premier ;
        # recalculé à chaque _refresh() (donc à chaque ajout/modification/
        # suppression d'un joueur).
        #
        # Hauteur bloquée avec ascenseur : le nombre de clubs distincts
        # n'est pas borné (variantes/fautes de frappe incluses, ex. "CPC"
        # et "CPC&") et grossirait ce tableau sans limite. Un Treeview
        # (plutôt qu'un Canvas + fenêtre intégrée + widgets détruits/
        # recréés à chaque rafraîchissement, essayé d'abord) : ce dernier
        # provoquait un affichage tronqué/superposé (artefact de rendu
        # Tk connu avec ce genre de construction) — un Treeview gère son
        # défilement nativement, sans ce risque, et height=N lignes suffit
        # à borner sa hauteur sans le moindre Canvas ni scrollregion à
        # gérer à la main. Largeur des colonnes calée sur celle du cadre
        # de prise de photo (ROSTER_PREVIEW_SIZE), pour un alignement
        # visuel cohérent avec lui.
        # Gardé sur self : _refresh() le remet au premier plan à chaque
        # rafraîchissement (voir plus bas et self.summary_box.lift() en
        # fin de __init__) — sans ça, les widgets créés APRÈS lui (la
        # liste du répertoire, le cadre photo...) passent devant dans
        # l'ordre d'empilement Tk par défaut (le plus récent créé au-
        # dessus), d'où les "stries" : un mélange visuel des deux.
        self.summary_box = ttk.LabelFrame(self, text="Joueurs par club")
        summary_box = self.summary_box
        summary_box.place(relx=1.0, x=-15, y=12, anchor="ne")

        self.club_summary_tree = ttk.Treeview(
            summary_box, columns=("club", "count"), show="headings",
            height=4, selectmode="none",
        )
        self.club_summary_tree.heading("club", text="Club")
        self.club_summary_tree.heading("count", text="Nombre")
        self.club_summary_tree.column("club", width=int(ROSTER_PREVIEW_SIZE * 0.62), anchor="w", stretch=False)
        self.club_summary_tree.column("count", width=int(ROSTER_PREVIEW_SIZE * 0.38), anchor="e", stretch=False)
        summary_vscroll = ttk.Scrollbar(summary_box, orient="vertical", command=self.club_summary_tree.yview)
        self.club_summary_tree.configure(yscrollcommand=summary_vscroll.set)
        # vscroll empaqueté AVANT le tree : sinon le tree capterait tout
        # l'espace restant et la scrollbar n'aurait plus de place (même
        # raison qu'ailleurs dans ce fichier).
        summary_vscroll.pack(side="right", fill="y")
        self.club_summary_tree.pack(side="left", padx=6, pady=6)

        # Tous les boutons d'action tout en haut de la fenêtre (avant la
        # zone de liste, extensible) : ils restent ainsi toujours visibles
        # en premier, quelle que soit la hauteur prise par la liste sur un
        # écran donné.
        add_frame = ttk.Frame(self)
        add_frame.pack(fill="x", padx=12, pady=(0, 8))
        self.new_name_var = tk.StringVar()
        # width=24 : triple de la largeur précédente (8), elle-même
        # environ le quart des ~32 caractères qu'occupait ce champ à
        # l'origine (fill="x", expand=True — il s'étirait sur toute la
        # largeur disponible dans add_frame).
        entry = ttk.Entry(add_frame, textvariable=self.new_name_var, width=24)
        entry.pack(side="left")
        entry.bind("<Return>", lambda e: self._add())
        ttk.Button(add_frame, text="Ajouter", command=self._add).pack(side="left", padx=5)
        ttk.Button(
            add_frame, text="Importer les joueurs d'un tournoi existant...",
            command=self._import_from_tournament,
        ).pack(side="left", padx=(8, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Button(btns, text="Renommer...", command=self._rename).pack(side="left", padx=3)
        ttk.Button(btns, text="Modifier le club...", command=self._edit_club).pack(side="left", padx=3)
        # Groupe/Téléphone/Mail (demande du 2026-09-20, chantier
        # "Sécurisation du Contrôle à distance") : mêmes principes que
        # "Modifier le club..." ci-dessus — un bouton dédié par champ,
        # jamais un formulaire multi-champs (aucun n'existe dans cet
        # onglet, style déjà établi champ par champ).
        ttk.Button(btns, text="Modifier le groupe...", command=self._edit_group).pack(side="left", padx=3)
        ttk.Button(btns, text="Modifier le téléphone...", command=self._edit_phone).pack(side="left", padx=3)
        ttk.Button(btns, text="Modifier le mail...", command=self._edit_mail).pack(side="left", padx=3)
        fill_missing_btn = ttk.Button(
            btns, text="Modifier clubs pour tous...", command=self._apply_default_club_to_missing,
        )
        fill_missing_btn.pack(side="left", padx=3)
        Tooltip(
            fill_missing_btn,
            "Attribue le club par défaut (« Nom du Club », réglé dans\n"
            "Paramètres) à tous les joueurs du répertoire qui n'ont\n"
            "encore aucun club — ne touche jamais à un club déjà\n"
            "renseigné.",
        )
        ttk.Button(btns, text="Supprimer", command=self._delete).pack(side="left", padx=3)
        ttk.Button(
            btns, text="Tout supprimer", command=self._delete_all, style="Danger.TButton",
        ).pack(side="left", padx=3)

        btns_csv = ttk.Frame(self)
        btns_csv.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Button(
            btns_csv, text="Importer (CSV)...", command=self._import_csv,
        ).pack(side="left")
        ttk.Button(
            btns_csv, text="Exporter (CSV)...", command=self._export_csv,
        ).pack(side="left", padx=(6, 0))
        reactivate_btn = ttk.Button(
            btns_csv, text="Tout réactiver...", command=self._reactivate_all,
        )
        reactivate_btn.pack(side="left", padx=(6, 0))
        Tooltip(
            reactivate_btn,
            "Cherche, dans un dossier au choix, TOUS les tournois où des\n"
            "joueurs sont restés coincés « actifs » sans que la partie ait\n"
            "été terminée — ce qui les grise à tort dans la fenêtre\n"
            "Joueurs participants. Les marque Forfait pour les libérer.\n"
            "⚠️ Systématique, y compris les tournois d'aujourd'hui : à\n"
            "n'utiliser que si aucun d'eux n'est réellement en cours.",
        )

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        list_frame = ttk.Frame(body)
        list_frame.pack(side="left", fill="both", expand=True)
        # show="tree headings" plutôt que "headings" seul : la photo (une
        # image par ligne) ne peut s'afficher que dans la toute première
        # colonne d'un Treeview ("#0", la colonne "arbre") — jamais dans
        # une colonne de données ordinaire comme "club", même en pratique
        # positionnée après elle. Même contrainte, même solution que la
        # colonne Photo de l'onglet Joueurs (voir _refresh_players_tab).
        # "group"/"phone"/"mail" (demande du 2026-09-20, chantier
        # "Sécurisation du Contrôle à distance") : ajoutées à la suite de
        # "club", sans rien retirer — une ancienne fiche sans ces champs
        # (roster.load_roster_entries les garantit toujours présents,
        # "" par défaut) s'affiche simplement avec ces 3 colonnes vides.
        self.roster_tree = ttk.Treeview(
            list_frame, columns=("name", "club", "group", "phone", "mail"),
            show="tree headings", selectmode="browse",
        )
        self.roster_tree.heading("#0", text="Photo")
        # +32 (pas +16) : le libellé "Photo" de l'en-tête ne tenait pas
        # dans une colonne calée juste sur la largeur de la vignette,
        # et se retrouvait tronqué en "Phot".
        self.roster_tree.column("#0", width=ROSTER_ROW_THUMB_SIZE + 32, stretch=False, anchor="center")
        self.roster_tree.heading("name", text="Nom", command=lambda: self._sort_roster_by("name"))
        self.roster_tree.heading("club", text="Club", command=lambda: self._sort_roster_by("club"))
        self.roster_tree.heading("group", text="Groupe", command=lambda: self._sort_roster_by("group"))
        self.roster_tree.heading("phone", text="Téléphone", command=lambda: self._sort_roster_by("phone"))
        self.roster_tree.heading("mail", text="Mail", command=lambda: self._sort_roster_by("mail"))
        self.roster_tree.column("name", width=180, anchor="w")
        self.roster_tree.column("club", width=140, anchor="w")
        self.roster_tree.column("group", width=80, anchor="center")
        self.roster_tree.column("phone", width=120, anchor="w")
        self.roster_tree.column("mail", width=180, anchor="w")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.roster_tree.yview)
        self.roster_tree.configure(yscrollcommand=scrollbar.set)
        self.roster_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.roster_tree.bind("<<TreeviewSelect>>", lambda e: self._refresh_preview())

        photo_frame = ttk.Frame(body)
        photo_frame.pack(side="left", fill="y", padx=(12, 0))

        preview_container = tk.Frame(
            photo_frame, width=ROSTER_PREVIEW_SIZE, height=ROSTER_PREVIEW_SIZE, bg=CREAM
        )
        preview_container.pack_propagate(False)  # taille fixe en pixels, quel que soit le contenu
        # pady(top) plus généreux (60, avant 24) : le tableau "Joueurs par
        # club" (place(), donc hors du flux normal — voir summary_box)
        # peut chevaucher le haut de ce cadre selon le nombre de lignes
        # qu'il affiche, sans cette marge.
        preview_container.pack(pady=(60, 0))
        self.preview_lbl = tk.Label(
            preview_container, bg=CREAM, text="Aucune photo", fg="#888888",
        )
        self.preview_lbl.pack(fill="both", expand=True)
        photo_btns = ttk.Frame(photo_frame)
        photo_btns.pack(pady=8, fill="x")
        ttk.Button(photo_btns, text="📷  Prendre une photo...", command=self._take_photo).pack(fill="x", pady=2)
        ttk.Button(photo_btns, text="🖼  Importer une photo...", command=self._import_photo).pack(fill="x", pady=2)
        ttk.Button(photo_btns, text="🗑  Supprimer la photo", command=self._delete_photo).pack(fill="x", pady=2)

        self._refresh()

    def _sort_roster_by(self, column):
        """Tri par clic sur un en-tête (Nom / Club) : ré-appuyer sur le
        même en-tête inverse l'ordre (croissant <-> décroissant)."""
        if self.roster_sort["column"] == column:
            self.roster_sort["ascending"] = not self.roster_sort["ascending"]
        else:
            self.roster_sort["column"] = column
            self.roster_sort["ascending"] = True
        self._refresh()

    def _update_roster_sort_headings(self):
        labels = {"name": "Nom", "club": "Club", "group": "Groupe", "phone": "Téléphone", "mail": "Mail"}
        for col, label in labels.items():
            if self.roster_sort["column"] == col:
                arrow = " ▲" if self.roster_sort["ascending"] else " ▼"
                self.roster_tree.heading(col, text=label + arrow)
            else:
                self.roster_tree.heading(col, text=label)

    def _refresh(self):
        selected = self._selected_name()
        for row in self.roster_tree.get_children():
            self.roster_tree.delete(row)
        entries = roster.load_roster_entries()
        col = self.roster_sort["column"]
        entries.sort(key=lambda e: (e[col] or "").lower())
        if not self.roster_sort["ascending"]:
            entries.reverse()
        self._update_roster_sort_headings()
        self.roster_row_photo_images = {}  # {nom: PhotoImage} — évite le garbage collect
        for e in entries:
            photo_path = player_photos.get_photo_path(e["name"])
            photo = load_thumbnail(photo_path, ROSTER_ROW_THUMB_SIZE) if photo_path else None
            if photo is not None:
                self.roster_row_photo_images[e["name"]] = photo
            self.roster_tree.insert(
                "", "end", iid=e["name"], image=photo if photo is not None else "",
                values=(e["name"], e["club"], e["group"], e["phone"], e["mail"]),
            )
        if selected and self.roster_tree.exists(selected):
            self.roster_tree.selection_set(selected)
        self._refresh_club_summary(entries)
        self._refresh_preview()

    def _refresh_club_summary(self, entries):
        """Reconstruit le petit tableau 'Club / Nombre' au-dessus de
        l'aperçu photo — appelé par _refresh(), donc à chaque
        ajout/modification/suppression d'un joueur du répertoire. 'Sans'
        regroupe les joueurs sans club, toujours affiché en premier ;
        les vrais clubs suivent par ordre alphabétique."""
        for row in self.club_summary_tree.get_children():
            self.club_summary_tree.delete(row)
        counts = {}
        for e in entries:
            key = e["club"] or "Sans"
            counts[key] = counts.get(key, 0) + 1
        ordered = ([("Sans", counts["Sans"])] if "Sans" in counts else []) + sorted(
            (item for item in counts.items() if item[0] != "Sans"), key=lambda item: item[0].lower()
        )
        for club, count in ordered:
            self.club_summary_tree.insert("", "end", values=(club, count))
        # Total à droite du titre : somme de tous les joueurs du
        # répertoire, tous clubs (et "Sans") confondus.
        self.summary_box.configure(text=f"Joueurs par club  (total={len(entries)})")
        # Remis au premier plan à CHAQUE rafraîchissement (pas seulement
        # à la construction initiale) : voir le commentaire à la création
        # de self.summary_box — un autre widget (re-)construit/repacké
        # entre-temps pourrait sinon repasser devant.
        self.summary_box.lift()

    def _refresh_preview(self):
        name = self._selected_name()
        path = player_photos.get_photo_path(name) if name else None
        photo = load_thumbnail(path, ROSTER_PREVIEW_SIZE) if path else None
        if photo is not None:
            self.preview_lbl.configure(image=photo, text="")
            self._preview_photo = photo
        else:
            if path and not PIL_AVAILABLE:
                placeholder = "Photo enregistrée,\naperçu indisponible\n(Pillow non installé)"
            elif name:
                placeholder = "Aucune photo"
            else:
                placeholder = ""
            self.preview_lbl.configure(image="", text=placeholder)
            self._preview_photo = None

    def _selected_name(self):
        sel = self.roster_tree.selection()
        if not sel:
            return None
        return sel[0]

    def _add(self):
        name = self.new_name_var.get().strip()
        if not name:
            return
        # Créé SANS club pour l'instant (demande du 2026-09-16) : la
        # fenêtre "Club de {name}" ci-dessous propose immédiatement le
        # club par défaut réglé dans Paramètres ("Nom du Club", commun à
        # tous les tournois/Sit & Go — voir aussi PlayerSelectionDialog.
        # _add_new_name, qui l'applique directement sans le proposer, un
        # contexte différent) — mais ne l'attribue QUE si l'utilisateur
        # valide, jamais en douce. Fermer/annuler cette fenêtre laisse
        # donc le membre sans club, jamais un club arbitraire imposé —
        # la création elle-même (juste au-dessus) n'est jamais remise en
        # cause par ce qui se passe ensuite dans la fenêtre.
        roster.add_to_roster(name)
        self.new_name_var.set("")
        self._refresh()
        default_club = export_prefs.load_value("club_name", "") or ""
        club = ask_club_dialog(self, title=f"Club de {name}", current_club=default_club)
        if club is not None:
            roster.set_club(name, club)
            self._refresh()
        # Pointe/sélectionne le nouveau membre dans la liste (demande du
        # 2026-09-16) — que "Club de {name}" ait été validée ou annulée/
        # fermée : le membre est créé dans les DEUX cas (voir plus haut),
        # donc toujours à faire apparaître sélectionné ensuite. APRÈS le
        # (ou les) _refresh() ci-dessus, qui reconstruit entièrement les
        # lignes du Treeview (roster_tree.insert avec iid=nom, voir
        # _refresh) — jamais avant, sinon la ligne n'existerait pas
        # encore.
        self._select_and_reveal(name)

    def _select_and_reveal(self, name):
        """Sélectionne la ligne `name` dans roster_tree (iid = le nom du
        joueur, voir _refresh) et la fait défiler à l'écran si besoin —
        réutilise directement selection_set()/see(), déjà le mécanisme
        de sélection existant de ce Treeview (voir _refresh, qui
        préserve la sélection courante de la même façon). Ne modifie
        jamais le tri actuel (roster_sort, inchangé ici) ni aucun autre
        comportement de sélection : simple sélection/défilement
        ponctuel, sans effet de bord sur _selected_name()/_refresh()
        pour la suite."""
        if self.roster_tree.exists(name):
            self.roster_tree.selection_set(name)
            self.roster_tree.see(name)

    def _rename(self):
        name = self._selected_name()
        if not name:
            return
        new_name = simpledialog.askstring("Renommer", "Nouveau nom :", initialvalue=name)
        if new_name and new_name.strip():
            roster.rename_in_roster(name, new_name.strip())
            player_photos.rename_photo(name, new_name.strip())
            self._refresh()

    def _edit_club(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Info", "Sélectionnez d'abord un joueur dans la liste.")
            return
        club = ask_club_dialog(self, title=f"Club de {name}", current_club=roster.get_club(name))
        if club is not None:
            roster.set_club(name, club)
            self._refresh()

    def _edit_group(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Info", "Sélectionnez d'abord un joueur dans la liste.")
            return
        group = ask_group_dialog(self, title=f"Groupe de {name}", current_group=roster.get_group(name))
        if group is not None:
            roster.set_group(name, group)
            self._refresh()

    def _edit_phone(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Info", "Sélectionnez d'abord un joueur dans la liste.")
            return
        phone = simpledialog.askstring(
            "Téléphone", f"Téléphone de {name} :", initialvalue=roster.get_phone(name),
        )
        if phone is not None:
            roster.set_phone(name, phone.strip())
            self._refresh()

    def _edit_mail(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Info", "Sélectionnez d'abord un joueur dans la liste.")
            return
        mail = simpledialog.askstring(
            "Mail", f"Mail de {name} :", initialvalue=roster.get_mail(name),
        )
        if mail is not None:
            roster.set_mail(name, mail.strip())
            self._refresh()

    def _apply_default_club_to_missing(self):
        """Attribue le 'Nom du Club' réglé dans Paramètres à tous les
        joueurs du répertoire sans club — n'écrase jamais un club déjà
        renseigné (voir la même logique côté ajout, _add/_add_new_name)."""
        default_club = export_prefs.load_value("club_name", "").strip()
        if not default_club:
            messagebox.showinfo(
                "Info",
                "Aucun club par défaut réglé pour l'instant — renseignez "
                "d'abord « Nom du Club » dans Paramètres.",
                parent=self,
            )
            return
        entries = roster.load_roster_entries()
        missing = [e["name"] for e in entries if not e["club"]]
        if not missing:
            messagebox.showinfo(
                "Info", "Tous les joueurs du répertoire ont déjà un club.", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Confirmer",
            f"Attribuer le club « {default_club} » aux {len(missing)} joueur(s) "
            "du répertoire qui n'ont encore aucun club ?",
            parent=self,
        ):
            return
        for name in missing:
            roster.set_club(name, default_club)
        self._refresh()
        messagebox.showinfo(
            "Terminé", f"{len(missing)} joueur(s) mis à jour.", parent=self,
        )

    def _delete(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno("Confirmer", f"Retirer {name} du répertoire ?\n"
                                "(Cela ne touche à aucun tournoi déjà créé.)"):
            roster.remove_from_roster(name)
            player_photos.delete_photo(name)
            self._refresh()

    def _delete_all(self):
        names = roster.load_roster()
        if not names:
            messagebox.showinfo("Info", "Le répertoire est déjà vide.")
            return
        if messagebox.askyesno(
            "Confirmer",
            f"Vider entièrement le répertoire ({len(names)} joueur(s)) ?\n\n"
            "Une sauvegarde du répertoire et des photos sera d'abord enregistrée "
            "dans ~/.poker_tournament/backups/, pour pouvoir être restaurée "
            "manuellement en cas d'erreur.\n(Cela ne touche à aucun tournoi déjà créé.)",
        ):
            backup_dir = self._backup_roster_before_wipe()
            for name in names:
                roster.remove_from_roster(name)
                player_photos.delete_photo(name)
            self._refresh()
            messagebox.showinfo(
                "Répertoire vidé",
                f"Le répertoire a été vidé.\n\nUne sauvegarde a été enregistrée ici :\n{backup_dir}",
            )

    def _backup_roster_before_wipe(self):
        """Enregistre un instantané horodaté du répertoire (roster.json) et
        des photos associées avant une suppression totale, sous
        ~/.poker_tournament/backups/repertoire_<horodatage>/. Ne modifie ni
        ne supprime rien : purement une copie, à restaurer manuellement en
        cas de besoin (recopier roster.json et les photos à la main)."""
        entries = roster.load_roster_entries()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        backup_dir = os.path.join(
            os.path.expanduser("~"), ".poker_tournament", "backups", f"repertoire_{timestamp}"
        )
        os.makedirs(backup_dir, exist_ok=True)

        with open(os.path.join(backup_dir, "roster.json"), "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)

        photo_index = {}
        for entry in entries:
            name = entry["name"]
            path = player_photos.get_photo_path(name)
            if not path or not os.path.exists(path):
                continue
            photos_dir = os.path.join(backup_dir, "photos")
            os.makedirs(photos_dir, exist_ok=True)
            filename = os.path.basename(path)
            try:
                shutil.copyfile(path, os.path.join(photos_dir, filename))
                photo_index[name] = filename
            except OSError:
                pass
        if photo_index:
            with open(os.path.join(backup_dir, "photos", "index.json"), "w", encoding="utf-8") as f:
                json.dump(photo_index, f, ensure_ascii=False, indent=2)

        return backup_dir

    def _take_photo(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Info", "Sélectionnez d'abord un joueur dans la liste.")
            return
        if not CV2_AVAILABLE or not PIL_AVAILABLE:
            messagebox.showerror(
                "Fonctionnalité indisponible",
                "La prise de photo par caméra nécessite les paquets "
                "'opencv-python' et 'Pillow', qui ne sont pas installés.\n\n"
                "Ouvrez un terminal et tapez :\n\n"
                "    pip3 install opencv-python pillow\n\n"
                "Vous pouvez en attendant importer une photo depuis un "
                "fichier existant.",
            )
            return
        CameraCaptureDialog(self, name, on_saved=self._refresh_preview)

    def _import_photo(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo("Info", "Sélectionnez d'abord un joueur dans la liste.")
            return
        path = filedialog.askopenfilename(
            title=f"Choisir une photo pour {name}",
            filetypes=[("Images", "*.jpg *.jpeg *.png"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        if PIL_AVAILABLE:
            try:
                source_image = Image.open(path)
                source_image.load()
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible d'ouvrir cette image :\n{e}")
                return
            crop_dlg = CropDialog(self, source_image)
            self.wait_window(crop_dlg)
            if crop_dlg.result is None:
                return
            try:
                player_photos.save_photo_from_image(name, crop_dlg.result)
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible d'importer cette photo :\n{e}")
                return
        else:
            try:
                player_photos.save_photo_from_file(name, path)
            except OSError as e:
                messagebox.showerror("Erreur", f"Impossible d'importer cette photo :\n{e}")
                return
        self._refresh_preview()
        if not PIL_AVAILABLE:
            messagebox.showinfo(
                "Photo enregistrée",
                f"La photo de {name} a bien été enregistrée, mais l'aperçu et "
                "les vignettes ne peuvent pas s'afficher sans le paquet "
                "'Pillow', qui n'est pas installé.\n\n"
                "Ouvrez un terminal et tapez :\n\n"
                "    pip3 install pillow\n\n"
                "puis rouvrez cette fenêtre.",
            )

    def _delete_photo(self):
        name = self._selected_name()
        if not name:
            return
        if player_photos.get_photo_path(name) is None:
            return
        if messagebox.askyesno("Confirmer", f"Supprimer la photo de {name} ?"):
            player_photos.delete_photo(name)
            self._refresh_preview()

    def _import_from_tournament(self):
        path = filedialog.askopenfilename(
            title="Importer les joueurs d'un tournoi existant",
            filetypes=[("Fichier de tournoi", "*.tournoi"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        try:
            src_db = Database(path)
            names = sorted({p["name"] for p in src_db.list_players()})
            src_db.close()
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de lire ce fichier :\n{e}")
            return
        if not names:
            messagebox.showinfo(
                "Import terminé", "Aucun joueur trouvé dans ce fichier.", parent=self,
            )
            return

        # Un fichier .tournoi ne mémorise jamais de club par joueur (voir
        # _import_csv, même principe pour un CSV sans colonne CLUB) :
        # propose un club unique à attribuer à tous les joueurs importés,
        # plutôt que de les laisser sans club.
        club_setting = export_prefs.load_value("club_name", "")
        default_club = simpledialog.askstring(
            "Club des joueurs importés",
            "Un fichier .tournoi ne mémorise pas le club de ses joueurs.\n"
            "Club à attribuer à tous les joueurs importés :",
            initialvalue=club_setting or "CPC", parent=self,
        )
        if default_club is None:
            return  # import annulé
        default_club = default_club.strip() or None

        for name in names:
            roster.add_to_roster(name, default_club)
        self._refresh()
        messagebox.showinfo(
            "Import terminé",
            f"{len(names)} joueur(s) ajouté(s) au répertoire depuis :\n{os.path.basename(path)}",
        )

    def _import_csv(self):
        path = filedialog.askopenfilename(
            title="Importer le répertoire depuis un CSV",
            filetypes=[("Fichier CSV", "*.csv"), ("Tous les fichiers", "*.*")],
            parent=self,
        )
        if not path:
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except OSError as e:
            messagebox.showerror("Erreur", f"Impossible de lire ce fichier :\n{e}", parent=self)
            return

        # Décodage du texte via IMPORT_CSV_ENCODINGS (UTF-8/UTF-8 avec BOM,
        # puis repli Windows-1252/ANSI — voir _decode_csv_bytes ci-dessus
        # pour le détail). Avant ce correctif du 2026-09-12, l'ouverture
        # forçait "utf-8-sig" sans repli et UnicodeDecodeError (sous-classe
        # de ValueError, PAS de OSError) n'était pas rattrapée ici : elle
        # remontait jusqu'à Tkinter, qui l'avalait silencieusement (voir
        # App.report_callback_exception) — le bouton semblait "ne rien
        # faire" sur un CSV non-UTF-8, sans aucun message.
        try:
            text = _decode_csv_bytes(raw)
        except UnicodeDecodeError as e:
            messagebox.showerror(
                "Erreur",
                "Encodage de fichier non reconnu (ni UTF-8, ni Windows-1252/"
                f"ANSI) :\n{e}",
                parent=self,
            )
            return

        try:
            sample = text[:4096]
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=";,")
            except csv.Error:
                dialect = csv.excel
                dialect.delimiter = ";"
            # newline="" (comme pour un fichier ouvert normalement, voir la
            # doc du module csv) : préserve les fins de ligne telles quelles,
            # pour un découpage des lignes identique à l'ancien code qui
            # lisait directement depuis le fichier.
            rows = list(csv.reader(io.StringIO(text, newline=""), dialect))
        except csv.Error as e:
            messagebox.showerror(
                "Erreur", f"Ce fichier ne semble pas être un CSV valide :\n{e}", parent=self,
            )
            return

        # Ignore une éventuelle ligne d'en-tête (NOM / CLUB, ou variantes).
        if rows and rows[0] and rows[0][0].strip().lower() in ("nom", "name", "joueur"):
            rows = rows[1:]

        cleaned_rows = []
        for row in rows:
            cells = [c.strip() for c in row]
            if not cells or not cells[0]:
                continue  # ligne vide, ou sans nom -> rien à importer
            cleaned_rows.append(cells)

        if not cleaned_rows:
            messagebox.showinfo(
                "Import terminé", "Aucun joueur trouvé dans ce fichier.", parent=self,
            )
            return

        # Fichier à une seule colonne (aucun club renseigné, sur aucune
        # ligne) -> propose un club unique à attribuer à tous les joueurs
        # importés, plutôt que de les laisser sans club.
        single_column = all(len(cells) < 2 or not cells[1] for cells in cleaned_rows)
        default_club = None
        if single_column:
            # Reprend le "Nom du Club" réglé dans Paramètres (commun à tous
            # les tournois/Sit & Go, voir _build_settings_tab) ; "CPC" en
            # dernier recours si rien n'y est encore renseigné.
            club_setting = export_prefs.load_value("club_name", "")
            default_club = simpledialog.askstring(
                "Club des joueurs importés",
                "Ce fichier ne contient que des noms (pas de club).\n"
                "Club à attribuer à tous les joueurs importés :",
                initialvalue=club_setting or "CPC", parent=self,
            )
            if default_club is None:
                return  # import annulé
            default_club = default_club.strip() or None

        added = 0
        for cells in cleaned_rows:
            name = cells[0]
            club = cells[1] if len(cells) >= 2 and cells[1] else default_club
            roster.add_to_roster(name, club)
            # GROUPE/TELEPHONE/MAIL (demande du 2026-09-20, complément
            # CSV) : même principe déjà en place ci-dessus pour "club"
            # (une cellule absente/vide -> `default_club`, qui vaut None
            # hors mode "fichier à une seule colonne" -> add_to_roster
            # conserve le club déjà connu, ne l'efface jamais) — étendu
            # ici aux 3 nouveaux champs. Une colonne absente (ancien CSV
            # à 1-2 colonnes) ou une cellule vide (nouveau CSV, valeur
            # volontairement non renseignée pour cette ligne) ne touche
            # donc JAMAIS une valeur déjà connue d'une personne déjà
            # présente dans le répertoire, qu'il s'agisse d'un ancien
            # CSV ou d'un nouveau : aucune perte accidentelle possible à
            # l'import, dans aucun des deux formats.
            #
            # Groupe : uniquement "ADMIN"/"DIRTO" (insensible à la
            # casse) déclenche une mise à jour — une cellule non vide
            # mais INVALIDE (faute de frappe, colonne décalée...) est
            # ignorée EXACTEMENT comme une cellule vide, jamais
            # normalisée en "" à la place d'une classification déjà
            # connue et valide (roster.set_group le ferait sinon
            # silencieusement, voir sa docstring) : "Groupe doit rester
            # strictement ADMIN, DIRTO ou vide" ne doit jamais devenir
            # une façon accidentelle d'effacer un ADMIN/DIRTO existant.
            group_cell = cells[2].upper() if len(cells) >= 3 and cells[2] else ""
            if group_cell in roster.ROSTER_GROUPS:
                roster.set_group(name, group_cell)
            # Téléphone/mail : jamais reconvertis en nombre ni altérés
            # (roster.set_phone/set_mail traitent déjà tout en chaîne,
            # voir roster.py) — un zéro initial ("0102030405") survit
            # donc intact à un aller-retour export -> import.
            phone_cell = cells[3] if len(cells) >= 4 else ""
            if phone_cell:
                roster.set_phone(name, phone_cell)
            mail_cell = cells[4] if len(cells) >= 5 else ""
            if mail_cell:
                roster.set_mail(name, mail_cell)
            added += 1

        self._refresh()
        messagebox.showinfo(
            "Import terminé",
            f"{added} joueur(s) importé(s) depuis :\n{os.path.basename(path)}",
            parent=self,
        )

    def _export_csv(self):
        entries = roster.load_roster_entries()
        if not entries:
            messagebox.showinfo("Info", "Le répertoire est vide, rien à exporter.", parent=self)
            return
        path = filedialog.asksaveasfilename(
            title="Exporter le répertoire en CSV",
            defaultextension=".csv",
            filetypes=[("Fichier CSV", "*.csv")],
            initialfile="repertoire_joueurs.csv",
            parent=self,
        )
        if not path:
            return
        # GROUPE/TELEPHONE/MAIL (demande du 2026-09-20, complément CSV du
        # chantier "Sécurisation du Contrôle à distance") : 3 colonnes
        # ajoutées à la suite de NOM;CLUB, jamais insérées au milieu — un
        # ancien import CSV (2 colonnes) reste ainsi structurellement
        # compatible avec ce nouveau format en lecture (voir _import_csv,
        # qui lit chaque colonne indépendamment selon ce qui est présent
        # sur la ligne). csv.writer écrit chaque valeur comme du texte
        # brut, jamais interprétée/convertie : un téléphone à zéro
        # initial ("0102030405") est donc préservé tel quel dans le
        # fichier — voir aussi _import_csv, qui ne le reconvertit jamais
        # en nombre.
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["NOM", "CLUB", "GROUPE", "TELEPHONE", "MAIL"])
                for e in entries:
                    writer.writerow([e["name"], e["club"], e["group"], e["phone"], e["mail"]])
        except OSError as e:
            messagebox.showerror("Erreur", f"Impossible d'écrire ce fichier :\n{e}", parent=self)
            return
        messagebox.showinfo(
            "Export terminé",
            f"{len(entries)} joueur(s) exporté(s) vers :\n{os.path.basename(path)}",
            parent=self,
        )

    def _reactivate_all(self):
        folder = filedialog.askdirectory(
            title="Choisir le dossier à vérifier", parent=self,
        )
        if not folder:
            return
        # Systématique : tous les tournois du dossier sont vérifiés, quelle
        # que soit leur date (y compris ceux d'aujourd'hui) — la fenêtre de
        # confirmation ci-dessous reste le seul garde-fou avant d'agir.
        stale = find_stale_active_players(folder, before_date=None, recursive=True)
        if not stale:
            messagebox.showinfo(
                "Tout réactiver",
                "Aucun joueur coincé « actif » trouvé dans les tournois de "
                "ce dossier.",
                parent=self,
            )
            return
        total_players = sum(len(e["players"]) for e in stale)
        lines = "\n".join(
            f"- {e['tournament_name']} ({os.path.basename(e['path'])}) : "
            f"{', '.join(e['players'])}"
            for e in stale
        )
        if not messagebox.askyesno(
            "Confirmer",
            f"{total_players} joueur(s), coincé(s) « actif(s) » dans "
            f"{len(stale)} tournoi(s) de ce dossier (y compris ceux "
            f"d'aujourd'hui), seront marqués Forfait :\n\n{lines}\n\n"
            "⚠️ Si l'un de ces tournois est réellement en train d'être joué "
            "en ce moment, ses joueurs actifs seront quand même retirés.\n\n"
            "Continuer ?",
            parent=self,
        ):
            return
        freed = withdraw_stale_active_players(stale)
        messagebox.showinfo(
            "Tout réactiver", f"{freed} joueur(s) libéré(s).", parent=self,
        )


class LobbyDialog(tk.Toplevel):
    """Vue d'ensemble de plusieurs tournois/Sit & Go à la fois : liste les
    fichiers .tournoi d'un dossier au choix avec leur état en direct
    (joueurs actifs, niveau de blindes, temps restant, en pause/en cours/
    terminé), et permet d'en ouvrir un dans une nouvelle fenêtre en un
    clic — pratique pour basculer d'un SNG à l'autre sans se souvenir de
    quelle fenêtre macOS contient lequel. Se rafraîchit automatiquement
    toutes les quelques secondes tant qu'elle reste ouverte. Ne modifie
    aucun fichier (consultation seule)."""

    REFRESH_MS = 4000

    def __init__(self, master):
        super().__init__(master)
        self.title("Lobby — Sit & Go / Tournois")
        self.geometry("860x420")
        # Empêche de réduire la fenêtre au point de cacher la barre de
        # boutons du bas ("🔀 Basculer vers"/"Fermer") — repéré sur une
        # capture où la fenêtre, réduite trop petit, ne montrait plus que
        # le tableau, sans aucun moyen visible d'agir dessus.
        self.minsize(560, 320)
        self.bind(
            "<F1>",
            lambda e: HelpBrowser.open_at(
                master, chapter_title="14. Sit & Go et gestion de plusieurs tournois", section_title="Lobby"
            ),
        )
        self._after_id = None
        self._paths_by_iid = {}
        # Synchronisation iPhone -> Lobby Mac (voir open_windows.py:
        # get_phone_selected_pid) : dernier pid déjà appliqué à LA
        # sélection de CETTE fenêtre, pour ne réagir qu'à un changement
        # (jamais réimposer la même valeur en boucle à chaque
        # rafraîchissement — voir _refresh) et ne jamais écraser un choix
        # manuel du Mac tant que le téléphone n'a pas sélectionné autre
        # chose.
        #
        # Initialisé avec la valeur DÉJÀ mémorisée (et non None) : sans
        # ça, un phone_selected_pid laissé par un choix téléphone
        # ANTÉRIEUR (avant même l'ouverture de CETTE fenêtre) est
        # interprété au tout premier _refresh() comme un nouveau choix
        # tout juste reçu, et bring_pid_to_front() ramène alors aussitôt
        # l'ancien tournoi au premier plan — devant ce Lobby qu'on vient
        # littéralement d'ouvrir (repéré avec AXRaise : le Lobby apparaît
        # puis disparaît quasi immédiatement, remplacé par le tournoi
        # déjà ouvert). En partant de la valeur actuelle, ce pid déjà
        # connu n'est plus vu comme un changement ; un choix téléphone
        # réellement nouveau (pid différent) continue, lui, à être
        # détecté et appliqué normalement (voir _refresh).
        self._last_synced_phone_pid = open_windows.get_phone_selected_pid()
        # Garde anti-double-ouverture (voir _open_selected) : chemins pour
        # lesquels un lancement est en cours depuis CETTE fenêtre, ni
        # encore enregistré (open_windows.register), ni confirmé mort.
        self._launching_paths = set()

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=10)

        self.hide_finished_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            top, text="Masquer les tournois terminés",
            variable=self.hide_finished_var, command=self._refresh,
        ).pack(side="right", padx=8)

        cols = ("name", "date", "players", "level", "remaining", "status")
        headers = ["Tournoi", "Date", "Joueurs actifs", "Niveau", "Temps restant", "État"]
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=130, anchor="center")
        self.tree.column("name", width=220, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.tree.bind("<Double-Button-1>", lambda e: self._open_selected())
        # Clic droit : bascule directement vers le tournoi sous le curseur
        # (Button-3 la plupart des plateformes, Button-2 sur Mac avec
        # certains trackpads/souris — les deux sont liés par précaution,
        # même principe qu'ailleurs dans ce fichier).
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Button-2>", self._on_tree_right_click)

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        switch_btn = ttk.Button(
            bottom, text="🔀 Basculer vers", command=self._open_selected,
        )
        switch_btn.pack(side="left")
        Tooltip(
            switch_btn,
            "Si ce tournoi est déjà ouvert dans une autre fenêtre, la\n"
            "ramène au premier plan. Sinon, l'ouvre dans une nouvelle\n"
            "fenêtre (double-clic sur la ligne fait la même chose).",
        )
        ttk.Button(bottom, text="Fermer", command=self._on_close).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._refresh()
        self._schedule_refresh()

    def _refresh(self):
        selected_path = self._selected_path()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._paths_by_iid = {}

        # UNIQUEMENT les tournois actuellement ouverts (voir
        # open_windows.py), peu importe leur dossier — plus le contenu
        # d'un dossier scanné : un tournoi juste présent sur disque (pas
        # ouvert dans une fenêtre en ce moment) n'a pas sa place ici.
        # "Masquer les tournois terminés" (coché par défaut) filtre
        # encore ensuite : un tournoi ouvert mais déjà terminé (fenêtre
        # pas encore refermée) ne doit pas non plus y figurer.
        paths = []
        seen = set()
        for path in open_windows.list_open_paths():
            if path not in seen and os.path.exists(path):
                seen.add(path)
                paths.append(path)

        for idx, path in enumerate(paths):
            try:
                db = Database(path, read_only=True)
                status = db.get_live_status()
                db.close()
            except Exception:
                continue  # fichier illisible/corrompu : ignoré plutôt que planter le Lobby

            level = status["level"]
            if level is not None and level["is_break"]:
                level_txt = level["break_label"] or "Pause"
            elif level is not None:
                level_txt = f"{level['small_blind']} / {level['big_blind']}"
            else:
                level_txt = "-"

            if status["remaining_seconds"] is None:
                remaining_txt = "-"
            else:
                m, s = divmod(status["remaining_seconds"], 60)
                remaining_txt = f"{m:02d}:{s:02d}"

            if status["finished"] and self.hide_finished_var.get():
                continue

            if status["finished"]:
                state_txt = "Terminé"
            elif not status["clock_started"]:
                state_txt = "Pas démarré"
            elif status["is_paused"]:
                state_txt = "En pause"
            else:
                state_txt = "En cours"

            iid = f"row{idx}"
            self.tree.insert(
                "", "end", iid=iid,
                values=(
                    status["name"], format_date_fr(status["date"]),
                    f"{status['active_count']} / {status['total_players_ever']}",
                    level_txt, remaining_txt, state_txt,
                ),
            )
            self._paths_by_iid[iid] = path

        # Synchronisation iPhone -> Lobby Mac : si un téléphone a
        # sélectionné un tournoi (voir open_windows.set_phone_selected_pid,
        # appelé par /select_tournament) depuis la dernière fois qu'on l'a
        # appliqué ICI, aligne la sélection de cette fenêtre dessus —
        # exactement l'effet d'un clic manuel (tree.selection_set), sans
        # ouvrir ni fermer quoi que ce soit. Prioritaire sur la ré-
        # application de l'ancienne sélection ci-dessous. Ne déclenche
        # jamais de boucle : _last_synced_phone_pid n'est mis à jour que
        # lorsque ce pid change réellement, donc un choix manuel ultérieur
        # du Mac n'est jamais écrasé par un rafraîchissement suivant tant
        # que le téléphone n'a pas sélectionné autre chose. Si le pid ne
        # correspond plus à aucun tournoi ouvert (déjà fermé entre-temps),
        # ne sélectionne rien à sa place ni ne ramène rien au premier
        # plan : la sélection existante ci-dessous reste inchangée.
        #
        # Ramène aussi CETTE fenêtre de tournoi au premier plan sur le
        # Mac (open_windows.bring_pid_to_front — EXACTEMENT la même
        # fonction, déjà "best-effort"/silencieuse en cas d'échec, que
        # _open_selected utilise pour "🔀 Basculer vers" un double-clic
        # manuel) — mais UNE SEULE FOIS au moment où ce nouveau choix est
        # détecté (même condition que la sélection ci-dessus), jamais à
        # chaque rafraîchissement (toutes les 4s).
        applied_from_phone = False
        phone_pid = open_windows.get_phone_selected_pid()
        if phone_pid is not None and phone_pid != self._last_synced_phone_pid:
            self._last_synced_phone_pid = phone_pid
            target_path = open_windows.find_path_for_pid(phone_pid)
            if target_path is not None:
                for iid, p in self._paths_by_iid.items():
                    if p == target_path:
                        self.tree.selection_set(iid)
                        applied_from_phone = True
                        break
                if applied_from_phone:
                    open_windows.bring_pid_to_front(phone_pid)

        if not applied_from_phone and selected_path:
            for iid, p in self._paths_by_iid.items():
                if p == selected_path:
                    self.tree.selection_set(iid)
                    break

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._paths_by_iid.get(sel[0])

    def _on_tree_right_click(self, event):
        """Clic droit sur une ligne : la sélectionne (si pas déjà) puis
        propose "Basculer vers" dans un petit menu contextuel, plutôt que
        de devoir cliquer la ligne PUIS aller chercher le bouton du bas."""
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="🔀 Basculer vers ce tournoi", command=self._open_selected)
        menu.tk_popup(event.x_root, event.y_root)

    def _open_selected(self):
        path = self._selected_path()
        if not path:
            messagebox.showinfo(
                "Lobby", "Sélectionnez d'abord un tournoi dans la liste.", parent=self,
            )
            return
        # Déjà ouvert dans une autre fenêtre (voir open_windows.py) : la
        # ramène au premier plan plutôt que d'en ouvrir une deuxième sur
        # le même fichier (deux fenêtres qui écrivent en même temps dans
        # le même .tournoi, à éviter) — utile pour basculer d'un Sit & Go
        # à l'autre sans se souvenir de quelle fenêtre contient lequel.
        existing_pid = open_windows.find_open_pid(path)
        if existing_pid:
            open_windows.bring_pid_to_front(existing_pid)
            return
        # "Un seul tournoi à la fois" (Paramètres) : AVANT le lancement
        # d'un nouveau process, jamais avant le "bring to front" ci-dessus
        # (qui doit rester possible pour un tournoi déjà ouvert, quel que
        # soit ce réglage). Compte TOUT tournoi actuellement ouvert, y
        # compris celui de LA fenêtre qui a ouvert ce Lobby (self.master,
        # voir App._open_lobby) : on s'apprête ici à en ouvrir un
        # SUPPLÉMENTAIRE (chemin `path`, pas encore ouvert), donc ce
        # tournoi-là compte bel et bien déjà comme "un tournoi ouvert" —
        # voir _other_tournament_is_open.
        if _block_second_tournament_if_needed(self):
            return
        # Deux clics rapprochés sur "🔀 Basculer vers" (ou un double-clic
        # suivi d'un second) pour un chemin pas encore ouvert : chacun
        # verrait `existing_pid` à None ci-dessus tant que le process tout
        # juste lancé par le premier clic n'a pas eu le temps de
        # s'enregistrer (open_windows.register, voir App.__init__) — sans
        # garde, le second lancerait SA PROPRE fenêtre sur le même fichier
        # .tournoi, soit deux process écrivant en même temps dedans (voir
        # la tooltip de ce bouton). Garde purement logique, pas de délai
        # arbitraire : tant qu'un lancement pour CE chemin précis est en
        # cours, les clics suivants sur la même ligne sont ignorés
        # silencieusement — un "Basculer vers" ultérieur bascule
        # normalement vers la fenêtre dès qu'elle existe (existing_pid
        # ci-dessus la retrouve alors).
        if path in self._launching_paths:
            return
        self._launching_paths.add(path)
        try:
            proc = spawn_app_process([path])
        except OSError as e:
            self._launching_paths.discard(path)
            messagebox.showerror(
                "Erreur", f"Impossible d'ouvrir ce tournoi :\n{e}", parent=self,
            )
            return
        raise_process_when_ready(self, proc.pid)
        self._clear_launch_guard_when_resolved(path, proc)

    def _clear_launch_guard_when_resolved(self, path, proc, attempt=0):
        """Lève la garde posée par _open_selected sur `path` dès que ce
        lancement est résolu : soit le nouveau process s'est bien
        enregistré (auquel cas une tentative suivante sur la même ligne
        le retrouvera normalement via existing_pid), soit il est mort
        entre-temps (une nouvelle tentative doit alors pouvoir relancer).
        Mêmes intervalle et nombre d'essais que raise_process_when_ready
        ci-dessus (700 ms, 5 essais) : le temps qu'un process démarre est
        le même dans les deux cas. Filet de sécurité au-delà de ces
        essais (plutôt qu'une attente indéfinie) : ne bloque jamais
        durablement une ligne, même dans un cas non prévu."""
        if not self.winfo_exists():
            return
        if open_windows.find_open_pid(path) is not None or proc.poll() is not None or attempt >= 5:
            self._launching_paths.discard(path)
            return
        self.after(700, lambda: self._clear_launch_guard_when_resolved(path, proc, attempt + 1))

    def _schedule_refresh(self):
        self._after_id = self.after(self.REFRESH_MS, self._auto_refresh)

    def _auto_refresh(self):
        if not self.winfo_exists():
            return
        self._refresh()
        self._schedule_refresh()

    def _on_close(self):
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except Exception:
                pass
        self.destroy()


class BlindTemplatesDialog(tk.Toplevel):
    """Liste les structures de blindes enregistrées comme modèles
    réutilisables (voir blind_templates.py, bouton "Enregistrer Blindes
    sous..." de l'onglet Blindes) : sélectionner un modèle et cliquer
    "Charger sur ce tournoi" remplace la structure de blindes du tournoi
    actuellement ouvert par celle du modèle choisi."""

    def __init__(self, master):
        super().__init__(master)
        self.app = master
        self.title("Récupérer Blindes")
        self.geometry("420x420")
        self.transient(master)
        self.grab_set()

        ttk.Label(
            self, text="Modèles de structures de blindes enregistrés :",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=12)
        self.listbox = tk.Listbox(list_frame, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self._load_selected())

        if not blind_templates.list_templates():
            ttk.Label(
                self, foreground=MUTED,
                text="(Aucun modèle enregistré pour l'instant — utilisez\n"
                     "\"Enregistrer Blindes sous...\" dans l'onglet Blindes.)",
                justify="left",
            ).pack(anchor="w", padx=12, pady=(6, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=12)
        ttk.Button(btns, text="Charger sur ce tournoi", command=self._load_selected).pack(side="left")
        ttk.Button(
            btns, text="Supprimer", command=self._delete_selected, style="Danger.TButton",
        ).pack(side="left", padx=6)
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right")

        self._refresh()

    def _refresh(self):
        self.listbox.delete(0, "end")
        for name in blind_templates.list_templates():
            self.listbox.insert("end", name)

    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.listbox.get(sel[0])

    def _load_selected(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo(
                "Récupérer Blindes", "Sélectionnez d'abord un modèle.", parent=self,
            )
            return
        levels = blind_templates.load_template(name)
        if levels is None:
            messagebox.showerror(
                "Erreur", f"Impossible de lire le modèle « {name} ».", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Confirmer",
            f"Remplacer la structure de blindes actuelle par « {name} » ?",
            parent=self,
        ):
            return
        self.app.db.set_blind_structure(levels)
        self.app._refresh_blinds_tab()
        if hasattr(self.app, "blinds_tree"):
            self.app._refresh_clock_tab()
        messagebox.showinfo(
            "Récupérer Blindes", f"Structure « {name} » appliquée à ce tournoi.", parent=self,
        )
        self.destroy()

    def _delete_selected(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno(
            "Confirmer", f"Supprimer définitivement le modèle « {name} » ?", parent=self,
        ):
            blind_templates.delete_template(name)
            self._refresh()


class SaveTemplateAsDialog(tk.Toplevel):
    """Dialogue générique pour les boutons "Enregistrer ... sous..." :
    affiche les modèles déjà enregistrés (cliquer sur l'un d'eux reprend
    son nom dans le champ, pour l'écraser en confirmant) et un champ de
    saisie libre pour créer un nouveau modèle. `self.result` contient le
    nom choisi (str) après fermeture, ou None si annulé."""

    def __init__(self, master, title, prompt, existing_names):
        super().__init__(master)
        self.title(title)
        self.geometry("380x400")
        self.transient(master)
        self.grab_set()
        self.result = None

        ttk.Label(self, text=prompt, wraplength=350, justify="left").pack(
            anchor="w", padx=12, pady=(12, 6)
        )

        ttk.Label(
            self, text="Modèles déjà enregistrés (cliquer pour écraser) :",
            foreground=MUTED,
        ).pack(anchor="w", padx=12)

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=12, pady=(4, 8))
        self.listbox = tk.Listbox(list_frame, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for name in existing_names:
            self.listbox.insert("end", name)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", lambda e: self._confirm())

        if not existing_names:
            ttk.Label(
                self, foreground=MUTED,
                text="(Aucun modèle enregistré pour l'instant.)",
            ).pack(anchor="w", padx=12)

        entry_frame = ttk.Frame(self)
        entry_frame.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Label(entry_frame, text="Nom :").pack(side="left")
        self.name_var = tk.StringVar()
        entry = ttk.Entry(entry_frame, textvariable=self.name_var)
        entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        entry.focus_set()
        entry.bind("<Return>", lambda e: self._confirm())

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Enregistrer", command=self._confirm).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

        self.wait_window(self)

    def _on_select(self, event):
        sel = self.listbox.curselection()
        if sel:
            self.name_var.set(self.listbox.get(sel[0]))

    def _confirm(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showinfo("Nom requis", "Entrez un nom pour ce modèle.", parent=self)
            return
        self.result = name
        self.destroy()


class SettingsTemplatesDialog(tk.Toplevel):
    """Liste les modèles de réglages enregistrés (voir
    settings_templates.py, bouton "Enregistrer Paramètres sous..." de
    l'onglet Paramètres) : sélectionner un modèle et cliquer "Charger sur
    ce tournoi" remplace tous les réglages (hors nom du tournoi) du
    tournoi actuellement ouvert par ceux du modèle choisi."""

    def __init__(self, master):
        super().__init__(master)
        self.app = master
        self.title("Récupérer Paramètres")
        self.geometry("420x420")
        self.transient(master)
        self.grab_set()

        ttk.Label(
            self, text="Modèles de réglages enregistrés :",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=12)
        self.listbox = tk.Listbox(list_frame, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self._load_selected())

        if not settings_templates.list_templates():
            ttk.Label(
                self, foreground=MUTED,
                text="(Aucun modèle enregistré pour l'instant — utilisez\n"
                     "\"Enregistrer Paramètres sous...\" dans l'onglet\n"
                     "Paramètres.)",
                justify="left",
            ).pack(anchor="w", padx=12, pady=(6, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=12)
        ttk.Button(btns, text="Charger sur ce tournoi", command=self._load_selected).pack(side="left")
        ttk.Button(
            btns, text="Supprimer", command=self._delete_selected, style="Danger.TButton",
        ).pack(side="left", padx=6)
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right")

        self._refresh()

    def _refresh(self):
        self.listbox.delete(0, "end")
        for name in settings_templates.list_templates():
            self.listbox.insert("end", name)

    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.listbox.get(sel[0])

    def _load_selected(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo(
                "Récupérer Paramètres", "Sélectionnez d'abord un modèle.", parent=self,
            )
            return
        values = settings_templates.load_template(name)
        if values is None:
            messagebox.showerror(
                "Erreur", f"Impossible de lire le modèle « {name} ».", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Confirmer",
            f"Remplacer les réglages actuels de ce tournoi par le modèle "
            f"« {name} » ?\n(Le nom du tournoi n'est pas concerné.)",
            parent=self,
        ):
            return
        for k, v in values.items():
            var = self.app.settings_vars.get(k)
            if var is None:
                continue
            if isinstance(var, tk.BooleanVar):
                var.set(v in ("1", "True", "true", True))
            else:
                var.set(v)
        self.app._collect_and_save_all_settings()
        self.app._refresh_all()
        messagebox.showinfo(
            "Récupérer Paramètres", f"Réglages « {name} » appliqués à ce tournoi.", parent=self,
        )
        self.destroy()

    def _delete_selected(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno(
            "Confirmer", f"Supprimer définitivement le modèle « {name} » ?", parent=self,
        ):
            settings_templates.delete_template(name)
            self._refresh()


class ChipTemplatesDialog(tk.Toplevel):
    """Liste les jeux de jetons enregistrés (voir chip_templates.py,
    bouton "Enregistrer Jetons sous..." de l'onglet Blindes) : sélectionner
    un modèle et cliquer "Charger sur ce tournoi" remplace les jetons du
    tournoi actuellement ouvert par ceux du modèle choisi."""

    def __init__(self, master):
        super().__init__(master)
        self.app = master
        self.title("Récupérer Jetons")
        self.geometry("420x420")
        self.transient(master)
        self.grab_set()

        ttk.Label(
            self, text="Jeux de jetons enregistrés :",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        list_frame = ttk.Frame(self)
        list_frame.pack(fill="both", expand=True, padx=12)
        self.listbox = tk.Listbox(list_frame, exportselection=False)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=scrollbar.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", lambda e: self._load_selected())

        if not chip_templates.list_templates():
            ttk.Label(
                self, foreground=MUTED,
                text="(Aucun modèle enregistré pour l'instant — utilisez\n"
                     "\"Enregistrer Jetons sous...\" dans l'onglet Blindes.)",
                justify="left",
            ).pack(anchor="w", padx=12, pady=(6, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=12)
        ttk.Button(btns, text="Charger sur ce tournoi", command=self._load_selected).pack(side="left")
        ttk.Button(
            btns, text="Supprimer", command=self._delete_selected, style="Danger.TButton",
        ).pack(side="left", padx=6)
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right")

        self._refresh()

    def _refresh(self):
        self.listbox.delete(0, "end")
        for name in chip_templates.list_templates():
            self.listbox.insert("end", name)

    def _selected_name(self):
        sel = self.listbox.curselection()
        if not sel:
            return None
        return self.listbox.get(sel[0])

    def _load_selected(self):
        name = self._selected_name()
        if not name:
            messagebox.showinfo(
                "Récupérer Jetons", "Sélectionnez d'abord un modèle.", parent=self,
            )
            return
        denominations = chip_templates.load_template(name)
        if denominations is None:
            messagebox.showerror(
                "Erreur", f"Impossible de lire le modèle « {name} ».", parent=self,
            )
            return
        if not messagebox.askyesno(
            "Confirmer",
            f"Remplacer les jetons actuels de ce tournoi par le modèle « {name} » ?",
            parent=self,
        ):
            return
        self.app._persist_chip_denominations(denominations)
        self.app._refresh_chips_tab()
        messagebox.showinfo(
            "Récupérer Jetons", f"Jetons « {name} » appliqués à ce tournoi.", parent=self,
        )
        self.destroy()

    def _delete_selected(self):
        name = self._selected_name()
        if not name:
            return
        if messagebox.askyesno(
            "Confirmer", f"Supprimer définitivement le modèle « {name} » ?", parent=self,
        ):
            chip_templates.delete_template(name)
            self._refresh()


# =====================================================================
# Onglet Statistiques — "Type de tournois" (demande du 2026-09-17)
# =====================================================================
# Libellés français affichés dans la Combobox <-> valeurs internes
# transmises à Database.build_period_summary (voir STATS_TOURNAMENT_
# TYPE_* importées de database.py) — même principe que RANKING_FORMULA_
# LABELS : dict Python, ordre d'insertion préservé (garanti depuis
# Python 3.7), donc .values() respecte l'ordre voulu dans la liste
# déroulante (Tournois, SitnGo, Tous).
STATS_TOURNAMENT_TYPE_LABELS = {
    STATS_TOURNAMENT_TYPE_TOURNOIS: "Tournois",
    STATS_TOURNAMENT_TYPE_SITNGO: "SitnGo",
    STATS_TOURNAMENT_TYPE_ALL: "Tous",
}
STATS_TOURNAMENT_TYPE_VALUES_BY_LABEL = {v: k for k, v in STATS_TOURNAMENT_TYPE_LABELS.items()}


# =====================================================================
# Onglet Statistiques — tri par en-tête cliquable (demande du 2026-09-17)
# =====================================================================
# Mécanisme FACTORISÉ pour les deux nouveaux tableaux de PeriodSummary
# Dialog ("Tournois de la période" / "Classement des joueurs") — jamais
# une refonte des 4 mécanismes de tri déjà existants ailleurs dans ce
# fichier (_sort_roster_by/_sort_players_by/_sort_primes_by/_sort_
# classement_by, hors périmètre, chacun propre à son propre onglet et
# déjà validé) : ceux-ci restent strictement inchangés. Fonctions PURES
# (aucun accès à self/aux widgets) : trient une COPIE de la liste
# fournie, ne modifient jamais la liste d'origine (donc jamais self.
# summary) ni aucun fichier .tournoi — un tri est purement un choix
# d'affichage.
def _sorted_rows(rows, sort_state, key_funcs):
    """Copie de `rows`, triée selon `sort_state` ({"column", "ascending"})
    si sa colonne a une fonction de clé dans `key_funcs` ({colonne:
    fonction(ligne) -> clé "naturelle", CROISSANTE}) — sinon renvoie une
    copie de `rows` INCHANGÉE (ordre par défaut déjà décidé par
    l'appelant, ex. Database.build_period_summary : tournois par date
    croissante, joueurs par total_points décroissant). `reverse=` de
    sorted() gère le sens : correct pour toute colonne "symétrique"
    (une chaîne ou un nombre, où inverser le sens inverse simplement
    l'ordre) — PAS pour "Meilleur Rang", qui a son propre traitement
    dédié (voir _sort_stats_players) car l'absence de classement (None)
    doit rester en dernière position quel que soit le sens, jamais
    suivre ce mécanisme générique symétrique."""
    col = sort_state.get("column")
    if not col or col not in key_funcs:
        return list(rows)
    return sorted(rows, key=key_funcs[col], reverse=not sort_state.get("ascending", True))


# Colonnes triables de "Tournois de la période" — clé de tri sur la
# valeur BRUTE (jamais le texte déjà formaté affiché dans le Treeview,
# voir _refresh_display) : "date" sur la chaîne ISO 'AAAA-MM-JJ' (jamais
# format_date_fr, qui donnerait un tri sur le JOUR d'abord) ; "name"/
# "winner" insensibles à la casse ; "bounty" numérique (clé du dict
# summary : "bounty_distributed", mais nom de colonne Treeview "bounty").
STATS_TOURNAMENTS_SORT_KEYS = {
    "date": lambda t: t["date"],
    "name": lambda t: t["name"].lower(),
    "winner": lambda t: t["winner"].lower(),
    "bounty": lambda t: t["bounty_distributed"],
}
STATS_TOURNAMENTS_SORT_HEADERS = {
    "date": "Date", "name": "Tournoi", "winner": "Vainqueur",
    "bounty": "Primes distribuées (pts)",
}

# Colonnes triables de "Classement des joueurs" (Treeview VISIBLE
# uniquement — voir cols_p dans PeriodSummaryDialog.__init__). Demande
# du 2026-09-18 (2e ajustement) : "wins" (Victoires) et "best" (Meilleur
# Rang) ne sont PLUS des colonnes de CE Treeview, remplacées à l'écran
# par "total_presence_assiduity" (Pts Prés/Ass) et "total_ranking_
# points" (Pts Gain Clsmt) — toutes deux TOUJOURS des entiers simples
# (jamais None, contrairement à best_place), donc un tri numérique
# ordinaire suffit, sans cas particulier. "wins"/"best_place" restent
# entièrement intacts dans build_period_summary et PERIOD_PLAYER_
# COLUMNS (disponibles à l'export, voir Colonnes — Classement des
# joueurs) — seule leur commande de tri sur CE Treeview disparaît, faute
# de colonne pour la porter ; _sort_stats_players garde son traitement
# "best" (inchangé, voir sa docstring) au cas où un appelant futur y
# aurait recours, mais plus aucune en-tête de ce Treeview ne le
# déclenche plus (voir STATS_PLAYERS_SORT_HEADERS ci-dessous, qui ne
# porte plus "best").
STATS_PLAYERS_SORT_KEYS = {
    # "rang" (demande du 2026-09-18) : trie l'AFFICHAGE selon le rang déjà
    # figé par _stats_players_with_rank (appelée AVANT ce tri, voir
    # _refresh_display) — ne recalcule jamais rien, un entier simple comme
    # les autres colonnes numériques ci-dessous, jamais None.
    "rang": lambda a: a["rang"],
    "name": lambda a: a["name"].lower(),
    "played": lambda a: a["tournaments_played"],
    "total_presence_assiduity": lambda a: a["total_presence_assiduity"],
    "total_ranking_points": lambda a: a["total_ranking_points"],
    "total_points": lambda a: a["total_points"],
}
STATS_PLAYERS_SORT_HEADERS = {
    "rang": "Rang", "name": "Joueur", "played": "Tournois joués",
    "total_presence_assiduity": "Pts Prés/Ass", "total_ranking_points": "Pts Gain Clsmt",
    "total_points": "TOTAL Pts",
}


def _sort_stats_players(players, sort_state):
    """Comme _sorted_rows, mais gère en plus "best" (Meilleur Rang) —
    cas particulier demandé le 2026-09-18 : un joueur sans classement
    (best_place=None, affiché "-") doit TOUJOURS rester en DERNIÈRE
    position, aussi bien en tri croissant qu'en tri décroissant (il
    représente une absence de classement, jamais un rang numérique à
    inverser comme les autres). Implémenté via une clé à deux niveaux —
    (a_un_classement, valeur signée) — le premier niveau (False < True)
    sépare définitivement les deux groupes sans jamais dépendre du sens,
    le second n'inverse QUE l'ordre à l'intérieur du groupe classé."""
    if sort_state.get("column") != "best":
        return _sorted_rows(players, sort_state, STATS_PLAYERS_SORT_KEYS)
    sign = 1 if sort_state.get("ascending", True) else -1
    return sorted(
        players,
        key=lambda a: (a["best_place"] is None, sign * (a["best_place"] or 0)),
    )


def _stats_players_with_rank(players):
    """Ajoute un "rang" (colonne "Rang", demande du 2026-09-18) à chaque
    joueur de `players` — classement SPORTIF avec égalités (1, 2, 2, 4 —
    jamais 1, 2, 3, 4), calculé EXCLUSIVEMENT depuis total_points
    décroissant, jamais départagé par le nom en cas d'égalité stricte.

    Identification des joueurs : `players` vient toujours de
    Database.build_period_summary, qui agrège par NOM (dict Python
    `players.setdefault(p_name, {...})`, voir sa docstring/son code) —
    il n'existe aucun identifiant de joueur qui survive au-delà d'un
    seul fichier .tournoi (l'id SQLite de chaque tournoi lui est propre,
    inutilisable pour recouper plusieurs fichiers). `summary["players"]`
    ne peut donc structurellement JAMAIS contenir deux entrées du même
    nom — c'est cette même garantie, déjà celle dont dépend build_
    period_summary lui-même, qui rend le nom sûr comme clé ici (voir
    tests/test_stats_player_rank.py : NomsUniquesDansSummaryTest, qui le
    démontre plutôt que de le supposer).

    Calcule le rang sur une COPIE triée à part (jamais en réordonnant le
    résultat) : la liste renvoyée est une NOUVELLE liste de NOUVEAUX
    dicts (dict(a, rang=...), jamais une mutation de `players`/self.
    summary), dans le MÊME ORDRE que `players` en entrée — appelée par
    l'appelant AVANT tout tri visuel (voir _sort_stats_players) pour que
    le rang reste fixe quel que soit l'ordre d'affichage choisi ensuite,
    et APRÈS le filtre Club (voir PeriodSummaryDialog._club_filtered_
    players) pour que le rang reflète bien la liste réellement affichée."""
    ordered = sorted(players, key=lambda a: -a["total_points"])
    rank_by_name = {}
    rank = 0
    prev_points = None
    for idx, a in enumerate(ordered, start=1):
        if a["total_points"] != prev_points:
            rank = idx
            prev_points = a["total_points"]
        rank_by_name[a["name"]] = rank
    return [dict(a, rang=rank_by_name[a["name"]]) for a in players]


def _apply_stats_sort_arrows(tree, sort_state, base_headers):
    """Réécrit le texte des en-têtes triables de `tree` avec ▲/▼ sur la
    colonne active (sort_state["column"]), texte nu sinon — factorisé
    pour les deux tableaux de Statistiques (voir le grand commentaire
    plus haut)."""
    for col, label in base_headers.items():
        if sort_state.get("column") == col:
            arrow = " ▲" if sort_state.get("ascending", True) else " ▼"
            tree.heading(col, text=label + arrow)
        else:
            tree.heading(col, text=label)


# =====================================================================
# Onglet Statistiques — calendrier pour les dates de période (demande du
# 2026-09-17/18), sans aucune dépendance externe : bibliothèque standard
# `calendar` (grille semaines/jours) + Tkinter, cohérent avec le reste
# du projet ("l'application elle-même ne requiert que la bibliothèque
# standard", voir windows/requirements.txt).
# =====================================================================
CALENDAR_WEEKDAY_LETTERS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
CALENDAR_MONTH_NAMES_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
]


def _shift_month(year, month, delta):
    """(année, mois) après avoir avancé/reculé de `delta` mois (positif
    ou négatif) — gère le passage d'année (ex. décembre + 1 -> janvier
    de l'année suivante ; janvier - 1 -> décembre de l'année
    précédente). Fonction PURE, testable indépendamment de tout widget
    Tk (voir tests/test_stats_period_calendar.py)."""
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


def _build_calendar_grid(parent, year, month, on_pick):
    """Construit, DANS `parent` (un widget déjà existant — jamais créé
    ni détruit par cette fonction elle-même, seul son CONTENU est
    reconstruit), la grille d'un mois : une ligne d'en-têtes Lun..Dim,
    puis un bouton par jour du mois (calendar.monthcalendar renvoie 0
    pour les cases vides des semaines incomplètes en début/fin de mois —
    laissées sans bouton). Chaque bouton appelle `on_pick(jour_iso)` à
    son clic — ne détruit JAMAIS la fenêtre elle-même ici (voir
    pick_date_dialog, seul appelant à connaître `win`) : reste ainsi
    testable directement (construire un Frame ordinaire, appeler cette
    fonction, invoquer le command() d'un bouton) sans jamais ouvrir de
    vraie fenêtre modale (grab_set/wait_window), risque de segfault déjà
    documenté ailleurs dans cette suite pour ce genre de fenêtre."""
    for w in parent.winfo_children():
        w.destroy()
    for col, wd in enumerate(CALENDAR_WEEKDAY_LETTERS_FR):
        ttk.Label(parent, text=wd, foreground=MUTED).grid(row=0, column=col, padx=2, pady=(0, 4))
    for r, week in enumerate(calendar.monthcalendar(year, month), start=1):
        for c, day in enumerate(week):
            if day == 0:
                continue
            day_iso = f"{year:04d}-{month:02d}-{day:02d}"
            ttk.Button(
                parent, text=str(day), width=3,
                command=lambda d=day_iso: on_pick(d),
            ).grid(row=r, column=c, padx=1, pady=1)


def pick_date_dialog(master, title="Choisir une date", initial_iso=None):
    """Petite fenêtre de calendrier (demande du 2026-09-17/18) pour
    choisir une date 'AAAA-MM-JJ' — sans aucune dépendance externe (voir
    le grand commentaire plus haut). Ouvre sur le mois d'`initial_iso`
    si fourni et valide, sinon le mois courant. Navigation mois
    précédent/suivant, "Aujourd'hui", "Effacer" (vide explicitement le
    champ — préserve "laisser vide = pas de borne", voir PeriodSummary
    Dialog._pick_period_date) et "Annuler" (ferme sans rien changer,
    y compris via la croix de fermeture native).

    Renvoie :
    - une chaîne 'AAAA-MM-JJ' si un jour (ou "Aujourd'hui") a été choisi ;
    - '' (chaîne vide) si "Effacer" a été cliqué — DISTINCT de None,
      l'appelant doit alors vider le champ explicitement ;
    - None si annulé/fermé — l'appelant ne doit RIEN changer au champ
      dans ce cas.

    Fonction MODULE-LEVEL, jamais une méthode d'App/PeriodSummaryDialog :
    générale, réutilisable pour tout futur champ de date de l'appli
    (seul appelant pour l'instant : l'onglet Statistiques)."""
    try:
        initial_dt = datetime.strptime(initial_iso, "%Y-%m-%d") if initial_iso else datetime.now()
    except (ValueError, TypeError):
        initial_dt = datetime.now()
    state = {"year": initial_dt.year, "month": initial_dt.month, "result": None}

    win = tk.Toplevel(master)
    win.title(title)
    win.configure(bg=FELT_DARK)
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()

    header = ttk.Frame(win)
    header.pack(fill="x", padx=10, pady=(10, 4))

    def go(delta):
        state["year"], state["month"] = _shift_month(state["year"], state["month"], delta)
        render()

    ttk.Button(header, text="◀", width=3, command=lambda: go(-1)).pack(side="left")
    ttk.Button(header, text="▶", width=3, command=lambda: go(1)).pack(side="right")
    month_lbl = ttk.Label(header, font=("Helvetica", 11, "bold"), anchor="center")
    month_lbl.pack(side="left", fill="x", expand=True)

    grid_frame = ttk.Frame(win)
    grid_frame.pack(padx=10, pady=(0, 6))

    def on_pick(day_iso):
        state["result"] = day_iso
        win.destroy()

    def render():
        month_lbl.config(text=f"{CALENDAR_MONTH_NAMES_FR[state['month'] - 1]} {state['year']}")
        _build_calendar_grid(grid_frame, state["year"], state["month"], on_pick)

    render()

    bottom = ttk.Frame(win)
    bottom.pack(fill="x", padx=10, pady=(0, 10))

    def choose_today():
        state["result"] = datetime.now().strftime("%Y-%m-%d")
        win.destroy()

    def choose_clear():
        state["result"] = ""
        win.destroy()

    def choose_cancel():
        state["result"] = None
        win.destroy()

    ttk.Button(bottom, text="Aujourd'hui", command=choose_today).pack(side="left")
    ttk.Button(bottom, text="Effacer", command=choose_clear).pack(side="left", padx=(6, 0))
    ttk.Button(bottom, text="Annuler", command=choose_cancel).pack(side="right")
    win.protocol("WM_DELETE_WINDOW", choose_cancel)

    master.wait_window(win)
    return state["result"]


class PeriodSummaryDialog(ttk.Frame):
    """Onglet "Statistiques" de la fenêtre principale — synthèse des
    résultats de tous les tournois (.tournoi) trouvés dans un dossier,
    pour une période donnée (dates de début/fin), en mentionnant les
    primes (bounty) empochées par chaque joueur. Anciennement une
    fenêtre à part (tk.Toplevel) ouverte depuis le menu Statistiques ;
    devenue un onglet du notebook (voir App._build_tabs), d'où ce
    ttk.Frame comme classe de base — le contenu n'a pas changé. Le F1
    contextuel est géré globalement (voir TAB_TO_CHAPTER,
    help_browser.py), plus besoin d'un bind dédié ici."""

    def __init__(self, master, app):
        super().__init__(master)
        # master = le notebook (parent Tk réel du widget, voir
        # App._build_tabs) ; app = l'instance App elle-même, dont ce
        # tournoi (self.app.db) dépend pour préremplir le dossier par
        # défaut ci-dessous — distincts depuis que cette fenêtre est
        # devenue un onglet plutôt qu'un Toplevel ouvert avec App comme
        # master direct.
        self.app = app
        self.summary = None

        default_folder = ""
        if getattr(app, "db", None) is not None:
            # "Dossier par défaut" (voir _build_settings_tab), tel que
            # configuré pour CE tournoi, sinon repli sur le dossier
            # contenant son fichier .tournoi.
            default_folder = (app.db.get_setting("tournament_day_folder", "") or "").strip()
            if not default_folder:
                default_folder = os.path.dirname(os.path.abspath(app.db.path))
        self.folder_var = tk.StringVar(value=default_folder)
        self.recursive_var = tk.BooleanVar(value=True)
        # Filtre "jours" (demande du 2026-09-18, 7 cases Lundi...Dimanche
        # à droite de "Tournois de la période") : cochées par défaut (voir
        # Database._tournament_day_matches, bypass total tant que les 7
        # restent cochées — comportement historique inchangé). Un
        # BooleanVar par jour (jamais une seule variable composite) pour
        # rester posé sur CETTE instance, comme recursive_var/
        # tournament_type_var ci-dessus : survit à _generate()/tout
        # rafraîchissement tant que cet onglet reste ouvert, jamais
        # persisté entre deux lancements de Senaco (pas demandé). Les
        # widgets Checkbutton eux-mêmes (grisés/réactivés selon
        # recursive_var, voir _update_stats_day_checkboxes_state) sont
        # construits plus bas ; ne JAMAIS réinitialiser ces BooleanVar en
        # grisant/dégrisant — c'est justement ce qui garantit que l'état
        # précédent est retrouvé telle quelle à la réactivation de
        # "Inclure les sous-dossiers", sans code de sauvegarde/restauration
        # séparé.
        self.stats_day_vars = {day: tk.BooleanVar(value=True) for day in STATS_WEEKDAY_FOLDER_NAMES}
        self._stats_day_checkbuttons = []
        # "Type de tournois" (demande du 2026-09-17) : valeur par défaut
        # "Tous" — comportement STRICTEMENT identique à avant l'existence
        # de ce filtre pour qui ne le touche pas (voir Database.build_
        # period_summary/_tournament_type_matches, OPTION A validée).
        # Jamais persisté entre sessions (comme folder_var/recursive_var/
        # les dates ci-dessous : aucun de ces réglages ne l'est
        # aujourd'hui, pas de raison d'en faire une exception isolée).
        self.tournament_type_var = tk.StringVar(value=STATS_TOURNAMENT_TYPE_LABELS[STATS_TOURNAMENT_TYPE_ALL])
        today = datetime.now()
        self.date_from_var = tk.StringVar(value=f"{today.year}-01-01")
        self.date_to_var = tk.StringVar(value=today.strftime("%Y-%m-%d"))
        # État de tri des deux tableaux (demande du 2026-09-17/18) —
        # aucune colonne active par défaut (comportement de Database.
        # build_period_summary inchangé : "Tournois de la période" par
        # date croissante, "Classement des joueurs" par TOTAL Pts
        # décroissant, voir _sorted_rows/_sort_stats_players). Posé sur
        # l'INSTANCE (cet onglet), donc survit à _generate()/_refresh_
        # display() tant que l'onglet reste ouvert — demande explicite :
        # le dernier tri choisi reste actif après Générer, changement de
        # période, de Type de tournois, du filtre Club, ou tout
        # rafraîchissement de l'affichage.
        self.tournaments_sort = {"column": None, "ascending": True}
        self.stats_players_sort = {"column": None, "ascending": True}

        params = ttk.Frame(self)
        params.pack(fill="x", padx=14, pady=(10, 4))

        row1 = ttk.Frame(params)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="Dossier des tournois :").pack(side="left")
        # Largeur réduite de moitié (55 -> 27) : la case "Inclure les
        # sous-dossiers" (déplacée ici, à droite de "Parcourir...") a
        # besoin de la place ainsi libérée sur la même ligne.
        ttk.Entry(row1, textvariable=self.folder_var, width=27).pack(
            side="left", padx=6
        )
        ttk.Button(row1, text="Parcourir...", command=self._browse_folder).pack(side="left")
        ttk.Checkbutton(
            row1, text="Inclure les sous-dossiers", variable=self.recursive_var,
        ).pack(side="left", padx=(16, 0))
        # Grise/dégrise les 7 cases Lundi...Dimanche (demande du
        # 2026-09-18) selon recursive_var — jamais leur VALEUR (voir
        # _refresh_stats_day_checkboxes_state) : la sélection reste donc
        # intacte, retrouvée telle quelle si "Inclure les sous-dossiers"
        # est réactivé. trace_add plutôt qu'un command= sur CE
        # Checkbutton : réagit aussi si recursive_var change par un autre
        # moyen (aucun aujourd'hui, mais plus robuste que le lier à un
        # seul widget précis).
        self.recursive_var.trace_add("write", lambda *_a: self._refresh_stats_day_checkboxes_state())
        # "Type de tournois" (demande du 2026-09-17) : à droite de
        # "Inclure les sous-dossiers", même ligne — aucune nouvelle ligne
        # créée, disposition existante préservée. Combobox non
        # modifiable (state="readonly") : seules les 3 valeurs proposées
        # sont valides, jamais de saisie libre.
        ttk.Label(row1, text="Type de tournois :").pack(side="left", padx=(16, 0))
        ttk.Combobox(
            row1, textvariable=self.tournament_type_var,
            values=list(STATS_TOURNAMENT_TYPE_LABELS.values()),
            state="readonly", width=10,
        ).pack(side="left", padx=(6, 0))

        # Ligne 2 : filtre Club à gauche (haut sur 4 lignes) ; à droite,
        # empilées sur sa hauteur, "Période..." puis, juste en dessous,
        # le compte-rendu et les boutons Générer/Exporter — une colonne à
        # part (right_col), pas la ligne "row2" elle-même, sinon ces deux
        # dernières se retrouveraient sous TOUTE la hauteur du filtre Club
        # (4 lignes) plutôt que juste sous "Période...".
        row2 = ttk.Frame(params)
        row2.pack(fill="x", pady=3)
        # Ne re-filtre que l'affichage déjà généré (voir _refresh_display),
        # pas besoin de reparcourir les fichiers .tournoi à chaque
        # coche/décoche.
        self.stats_club_listbox = build_club_filter_widget(row2, on_change=self._refresh_display)

        right_col = ttk.Frame(row2)
        right_col.pack(side="left", anchor="n", fill="x", expand=True, padx=(20, 0))

        period_frame = ttk.Frame(right_col)
        period_frame.pack(fill="x", anchor="w")
        ttk.Label(period_frame, text="Période — du (AAAA-MM-JJ) :").pack(side="left")
        ttk.Entry(period_frame, textvariable=self.date_from_var, width=12).pack(
            side="left", padx=(4, 2)
        )
        # Bouton calendrier (demande du 2026-09-17/18) : raccourci
        # facultatif — la saisie manuelle du champ ci-dessus reste
        # entièrement fonctionnelle, inchangée (voir _pick_period_date).
        ttk.Button(
            period_frame, text="📅", width=3,
            command=lambda: self._pick_period_date(self.date_from_var),
        ).pack(side="left", padx=(0, 14))
        ttk.Label(period_frame, text="au (AAAA-MM-JJ) :").pack(side="left")
        ttk.Entry(period_frame, textvariable=self.date_to_var, width=12).pack(side="left", padx=(4, 2))
        ttk.Button(
            period_frame, text="📅", width=3,
            command=lambda: self._pick_period_date(self.date_to_var),
        ).pack(side="left")
        ttk.Label(
            period_frame, text="(laisser vide = pas de borne)", foreground=MUTED,
        ).pack(side="left", padx=10)

        info_row = ttk.Frame(right_col)
        info_row.pack(fill="x", pady=(8, 0))
        self.info_lbl = ttk.Label(info_row, text="", font=("Helvetica", 10, "bold"))
        self.info_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(info_row, text="Exporter...", command=self._open_export_dialog).pack(side="right")
        ttk.Button(
            info_row, text="Générer la synthèse", command=self._generate,
        ).pack(side="right", padx=(0, 6))

        panes = ttk.Frame(self)
        panes.pack(fill="both", expand=True, padx=14, pady=(0, 6))
        # grid + poids égaux plutôt que pack(expand=True) sur les deux
        # LabelFrame ci-dessous : pack ne les répartissait pas forcément à
        # parts égales (le premier packé pouvait grossir bien plus que le
        # second) ; grid avec deux lignes de même poids garantit un
        # partage 50/50 de la hauteur disponible entre les deux tableaux.
        panes.grid_rowconfigure(0, weight=1)
        panes.grid_rowconfigure(1, weight=1)
        panes.grid_columnconfigure(0, weight=1)

        # "Tournois de la période" + 7 cases Lundi...Dimanche À DROITE du
        # titre (demande du 2026-09-18) : un ttk.LabelFrame(text=...)
        # intègre son titre à sa propre bordure — impossible d'empaqueter
        # un widget juste à côté avec un simple pack()/grid(). Passer par
        # labelwidget= (un ttk.Frame construit à la place du texte,
        # contenant le Label du titre PUIS les 7 Checkbutton) est la
        # façon standard ttk de placer des widgets interactifs dans la
        # zone de titre elle-même, sans toucher au reste du layout de
        # top_pane (toujours plein largeur, hauteur inchangée).
        top_pane = ttk.LabelFrame(panes)
        top_pane_header = ttk.Frame(top_pane)
        ttk.Label(top_pane_header, text="Tournois de la période").pack(side="left")
        for day in STATS_WEEKDAY_FOLDER_NAMES:
            cb = ttk.Checkbutton(top_pane_header, text=day, variable=self.stats_day_vars[day])
            cb.pack(side="left", padx=(10, 0))
            self._stats_day_checkbuttons.append(cb)
        top_pane.configure(labelwidget=top_pane_header)
        self._refresh_stats_day_checkboxes_state()
        top_pane.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        # Pas de colonne "Prize pool (€)" ici : ce club ne distribue pas de
        # gains en argent réel (voir Classement, colonnes Total investi/
        # Gains classement retirées pour la même raison) — la donnée reste
        # calculée normalement (build_period_summary), juste pas affichée.
        cols_t = ("date", "name", "status", "entries", "winner", "bounty")
        headers_t = ["Date", "Tournoi", "Statut", "Entrées", "Vainqueur", "Primes distribuées (pts)"]
        # height=13 : même hauteur que "Classement des joueurs" ci-dessous
        # (voir players_tree), pour que les deux tableaux soient alignés.
        self.tournaments_tree = ttk.Treeview(top_pane, columns=cols_t, show="headings", height=13)
        for c, h in zip(cols_t, headers_t):
            self.tournaments_tree.heading(c, text=h)
            self.tournaments_tree.column(c, width=120, anchor="center")
        self.tournaments_tree.column("name", width=180, anchor="w")
        self.tournaments_tree.pack(fill="both", expand=True, padx=6, pady=6)
        # Tri par en-tête cliquable (demande du 2026-09-17/18) : Date,
        # Tournoi, Vainqueur, Primes uniquement — "Statut"/"Entrées"
        # restent sans command=, comme avant (non demandées). Voir
        # STATS_TOURNAMENTS_SORT_KEYS/_sorted_rows/_apply_stats_sort_
        # arrows, factorisés avec "Classement des joueurs" ci-dessous —
        # la ligne TOTAL (voir _refresh_display) n'est JAMAIS concernée
        # par ce tri, toujours insérée en premier, hors de la liste
        # triée.
        for col in STATS_TOURNAMENTS_SORT_HEADERS:
            self.tournaments_tree.heading(
                col, command=lambda c=col: self._on_stats_sort_click(self.tournaments_sort, c)
            )

        bottom_pane = ttk.LabelFrame(panes, text="Classement des joueurs sur la période (primes incluses)")
        bottom_pane.grid(row=1, column=0, sticky="nsew")
        # Pas de "Total investi (€)" ni "Gains classement (€)" : voir la
        # remarque équivalente ci-dessus pour "Tournois de la période".
        # "club" en première colonne : club actuel du joueur dans le
        # répertoire (voir _refresh_display), pas de tri dédié dessus.
        # "Victoires"/"Meilleur Rang" remplacées le 2026-09-18 (2e
        # ajustement) par "Pts Prés/Ass"/"Pts Gain Clsmt" (total_
        # presence_assiduity/total_ranking_points, déjà calculés dans
        # build_period_summary) — UNIQUEMENT dans ce Treeview VISIBLE :
        # "wins"/"best_place" restent entièrement intacts et disponibles
        # à l'export (voir PERIOD_PLAYER_COLUMNS, jamais modifiée pour
        # cette demande).
        # "rang" (demande du 2026-09-18, 3e ajustement) : immédiatement à
        # gauche de "name"/"Joueur" — valeur déjà calculée par _stats_
        # players_with_rank (voir _refresh_display), jamais recalculée ici
        # ni par le tri de cette colonne (voir STATS_PLAYERS_SORT_KEYS).
        cols_p = ("club", "rang", "name", "played", "total_presence_assiduity", "total_ranking_points", "bounty", "total_points")
        headers_p = [
            "Club", "Rang", "Joueur", "Tournois joués", "Pts Prés/Ass", "Pts Gain Clsmt",
            "Bounty", "TOTAL Pts",
        ]
        self.players_tree = ttk.Treeview(bottom_pane, columns=cols_p, show="headings", height=13)
        for c, h in zip(cols_p, headers_p):
            self.players_tree.heading(c, text=h)
            self.players_tree.column(c, width=115, anchor="center")
        self.players_tree.column("club", width=110, anchor="w")
        self.players_tree.column("rang", width=55, anchor="center")
        self.players_tree.column("name", width=170, anchor="w")
        self.players_tree.pack(fill="both", expand=True, padx=6, pady=6)
        # Tri par en-tête cliquable (demande du 2026-09-17/18, colonnes
        # ajustées le 2026-09-18) : Joueur, Tournois joués, Pts Prés/Ass,
        # Pts Gain Clsmt, TOTAL Pts uniquement — "Club"/"Bounty" restent
        # sans command=, comme avant (non demandées). Voir STATS_PLAYERS_
        # SORT_KEYS/_sort_stats_players/_apply_stats_sort_arrows — la
        # ligne TOTAL (voir _refresh_display) n'est jamais concernée,
        # toujours insérée en premier.
        for col in STATS_PLAYERS_SORT_HEADERS:
            self.players_tree.heading(
                col, command=lambda c=col: self._on_stats_sort_click(self.stats_players_sort, c)
            )

    def _browse_folder(self):
        path = filedialog.askdirectory(
            title="Choisir le dossier contenant les fichiers .tournoi",
            initialdir=self.folder_var.get() or os.path.expanduser("~"),
        )
        if path:
            self.folder_var.set(path)

    def _refresh_stats_day_checkboxes_state(self):
        """Grise/dégrise les 7 cases Lundi...Dimanche selon recursive_var
        (demande du 2026-09-18) — ne touche JAMAIS aux BooleanVar elles-
        mêmes : une sélection partielle faite pendant que "Inclure les
        sous-dossiers" était coché reste donc mémorisée telle quelle
        pendant qu'il est décoché (cases grisées, filtre sans effet, voir
        Database.build_period_summary), et retrouvée automatiquement à la
        réactivation — rien à sauvegarder/restaurer explicitement."""
        state = "!disabled" if self.recursive_var.get() else "disabled"
        for cb in self._stats_day_checkbuttons:
            cb.state([state])

    def _pick_period_date(self, var):
        """Ouvre le calendrier (pick_date_dialog) pour le champ de
        période `var` (date_from_var ou date_to_var) — bouton 📅 à côté
        de chaque champ (demande du 2026-09-17/18). None (annulé/fermé,
        y compris via la croix) laisse le champ inchangé ; '' (Effacer)
        le vide explicitement, préservant "laisser vide = pas de
        borne" ; toute autre valeur est la date choisie, au même format
        AAAA-MM-JJ que la saisie manuelle déjà existante — jamais un
        format différent. La saisie manuelle du champ reste par
        ailleurs entièrement fonctionnelle, ce bouton n'est qu'un
        raccourci facultatif."""
        ok, current = self._parse_date(var.get())
        chosen = pick_date_dialog(self, initial_iso=current if ok else None)
        if chosen is not None:
            var.set(chosen)

    @staticmethod
    def _parse_date(text):
        """Renvoie (ok, valeur) : valeur = chaîne 'AAAA-MM-JJ' ou None si
        vide ; ok = False si le texte n'est ni vide ni une date valide."""
        text = text.strip()
        if not text:
            return True, None
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return False, None
        return True, text

    def _generate(self):
        folder = self.folder_var.get().strip()
        if not folder or not os.path.isdir(folder):
            messagebox.showerror("Erreur", "Choisissez d'abord un dossier valide.")
            return
        ok_from, date_from = self._parse_date(self.date_from_var.get())
        ok_to, date_to = self._parse_date(self.date_to_var.get())
        if not ok_from or not ok_to:
            messagebox.showerror(
                "Erreur", "Les dates doivent être au format AAAA-MM-JJ (ex : 2026-01-31),\n"
                "ou laissées vides."
            )
            return
        if date_from and date_to and date_from > date_to:
            messagebox.showerror("Erreur", "La date de début doit précéder la date de fin.")
            return

        # "Type de tournois" (demande du 2026-09-17) : se combine avec
        # date_from/date_to, dans le MÊME appel à build_period_summary —
        # un fichier doit passer les deux filtres pour être retenu (voir
        # Database._tournament_type_matches).
        tournament_type = STATS_TOURNAMENT_TYPE_VALUES_BY_LABEL.get(
            self.tournament_type_var.get(), STATS_TOURNAMENT_TYPE_ALL
        )
        # Jours cochés (demande du 2026-09-18) : toujours transmis tel
        # quel, même si "Inclure les sous-dossiers" est décoché — c'est
        # build_period_summary/_tournament_day_matches qui rend ce filtre
        # sans effet dans ce cas (jamais recalculé/ignoré ici), pour
        # qu'une réactivation ultérieure retrouve la sélection réellement
        # cochée à l'écran, jamais une valeur reconstruite à côté.
        selected_days = [day for day, var in self.stats_day_vars.items() if var.get()]
        self.summary = build_period_summary(
            folder, date_from=date_from, date_to=date_to,
            recursive=self.recursive_var.get(),
            tournament_type=tournament_type,
            selected_days=selected_days,
        )
        self._refresh_display()

    def _club_filtered_players(self, players):
        """Sous-ensemble de `players` (une liste comme summary["players"])
        qui passe le filtre "Club" actuellement sélectionné à l'écran —
        extrait de _refresh_display (demande du 2026-09-14) pour que
        _open_export_dialog puisse transmettre à l'export EXACTEMENT les
        mêmes joueurs que ceux affichés, au lieu de dupliquer cette
        logique (et de risquer qu'elle diverge un jour). Renvoie `players`
        tel quel si aucun club n'est coché (voir get_selected_clubs).

        Filtre "Club" (voir build_club_filter_widget) : club actuel du
        joueur dans le répertoire (roster.py), pas celui, potentiellement
        différent d'un tournoi à l'autre, enregistré au moment de son
        inscription à chacun — un joueur ayant changé de club entre-temps
        ne doit compter que pour son club actuel. Aucune coche = tous les
        clubs (voir get_selected_clubs). Un joueur sans club renseigné
        dans le répertoire (le cas le plus courant pour les joueurs du
        club organisateur, jamais explicitement tagués) compte pour le
        club réglé dans Paramètres, pas pour "aucun club"."""
        home_club = export_prefs.load_value("club_name", "").strip()
        selected_clubs = get_selected_clubs(self.stats_club_listbox)
        if not selected_clubs:
            return players
        return [
            a for a in players
            if (roster.get_club(a["name"]) or home_club) in selected_clubs
        ]

    def _on_stats_sort_click(self, sort_state, column):
        """Clic sur un en-tête triable de l'un des deux tableaux de
        Statistiques (demande du 2026-09-17/18) — factorisé, `sort_state`
        est soit self.tournaments_sort, soit self.stats_players_sort.
        Même colonne re-cliquée -> inverse `ascending` ; sinon nouvelle
        colonne + `ascending=True` (repart dans son ordre initial) —
        exactement le même principe que les 4 mécanismes de tri déjà
        existants ailleurs dans ce fichier (roster/players/primes/
        classement), jamais dupliqué en détail ici : voir _sorted_rows/
        _sort_stats_players pour le tri effectif, appliqué dans _refresh_
        display, seule méthode qui reconstruit ces deux Treeview."""
        if sort_state["column"] == column:
            sort_state["ascending"] = not sort_state["ascending"]
        else:
            sort_state["column"] = column
            sort_state["ascending"] = True
        self._refresh_display()

    def _refresh_display(self):
        for row in self.tournaments_tree.get_children():
            self.tournaments_tree.delete(row)
        for row in self.players_tree.get_children():
            self.players_tree.delete(row)

        if self.summary is None:
            return

        # Tri (demande du 2026-09-17/18) : toujours sur une COPIE (voir
        # _sorted_rows/_sort_stats_players), jamais self.summary lui-même
        # — un changement de tri n'est qu'un choix d'affichage, jamais
        # une écriture. Aucune colonne active (self.X_sort["column"] is
        # None, valeur initiale) -> ordre par défaut de build_period_
        # summary INCHANGÉ (date croissante / TOTAL Pts décroissant).
        tournaments = _sorted_rows(
            self.summary["tournaments"], self.tournaments_sort, STATS_TOURNAMENTS_SORT_KEYS
        )
        # "rang" (demande du 2026-09-18) : calculé APRÈS le filtre Club
        # (players réellement affichés) mais AVANT le tri visuel — voir
        # _stats_players_with_rank, jamais recalculé par _sort_stats_
        # players, qui ne fait que réordonner l'affichage.
        players = _sort_stats_players(
            _stats_players_with_rank(self._club_filtered_players(self.summary["players"])),
            self.stats_players_sort,
        )
        _apply_stats_sort_arrows(
            self.tournaments_tree, self.tournaments_sort, STATS_TOURNAMENTS_SORT_HEADERS
        )
        _apply_stats_sort_arrows(
            self.players_tree, self.stats_players_sort, STATS_PLAYERS_SORT_HEADERS
        )
        # Repris ici (pas seulement dans _club_filtered_players ci-dessus,
        # qui a son propre usage interne du même réglage) : sert aussi à
        # la colonne "Club" affichée plus bas, pour chaque ligne restante
        # après filtrage.
        home_club = export_prefs.load_value("club_name", "").strip()

        # Ligne de total (en gras, voir tag "totalcol" plus bas), tout en
        # haut du tableau, insérée avant la boucle pour y rester quel que
        # soit l'ordre d'affichage — même principe que l'onglet Primes
        # (_refresh_bounty_tab). Reflète les tournois actuellement
        # affichés (donc déjà filtrés par la période choisie).
        self.tournaments_tree.insert(
            "", "end",
            values=(
                "", "TOTAL", "", "",
                "",
                f"{sum(t['bounty_distributed'] for t in tournaments):,}".replace(",", " "),
            ),
            tags=("totalcol",),
        )
        for idx, t in enumerate(tournaments):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.tournaments_tree.insert(
                "", "end",
                values=(
                    format_date_fr(t["date"]), t["name"], t["status"], t["entries"],
                    t["winner"],
                    f"{t['bounty_distributed']:,}".replace(",", " ") if t["bounty_distributed"] else "-",
                ),
                tags=(tag,),
            )
        self.tournaments_tree.tag_configure("evenrow", background=CREAM)
        self.tournaments_tree.tag_configure("oddrow", background=CREAM_ALT)
        self.tournaments_tree.tag_configure(
            "totalcol", font=("Helvetica", 9, "bold"), background=GOLD, foreground=TEXT_DARK,
        )

        # Ligne de total (voir la même chose ci-dessus pour "Tournois de la
        # période") : reflète les joueurs actuellement affichés (donc déjà
        # filtrés par club le cas échéant, voir selected_clubs plus haut).
        # Demande du 2026-09-18 (2e ajustement) : cumule désormais aussi
        # Pts Prés/Ass et Pts Gain Clsmt (total_presence_assiduity/
        # total_ranking_points), MÊME principe que Bounty/TOTAL Pts déjà
        # cumulés ici — sur `players`, donc déjà filtré par Club à ce
        # stade, jamais sur self.summary["players"] brut.
        self.players_tree.insert(
            "", "end",
            values=(
                "", "", "TOTAL", "",
                f"{sum(a['total_presence_assiduity'] for a in players):,}".replace(",", " "),
                f"{sum(a['total_ranking_points'] for a in players):,}".replace(",", " "),
                f"{sum(a['total_bounty_won'] for a in players):,}".replace(",", " "),
                f"{sum(a['total_points'] for a in players):,}".replace(",", " "),
            ),
            tags=("totalcol",),
        )
        for idx, a in enumerate(players):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.players_tree.insert(
                "", "end",
                values=(
                    roster.get_club(a["name"]) or home_club or "-",
                    a["rang"],
                    a["name"], a["tournaments_played"],
                    f"{a['total_presence_assiduity']:,}".replace(",", " "),
                    f"{a['total_ranking_points']:,}".replace(",", " "),
                    f"{a['total_bounty_won']:,}".replace(",", " ") if a["total_bounty_won"] else "-",
                    f"{a['total_points']:,}".replace(",", " "),
                ),
                tags=(tag,),
            )
        self.players_tree.tag_configure("evenrow", background=CREAM)
        self.players_tree.tag_configure("oddrow", background=CREAM_ALT)
        self.players_tree.tag_configure(
            "totalcol", font=("Helvetica", 9, "bold"), background=GOLD, foreground=TEXT_DARK,
        )

        self.info_lbl.config(
            text=f"{len(tournaments)} tournoi(s) trouvé(s) sur la période — {len(players)} joueur(s) distinct(s)."
        )

    def _open_export_dialog(self):
        if not self.summary or not (self.summary["tournaments"] or self.summary["players"]):
            messagebox.showinfo("Info", "Générez d'abord une synthèse non vide.")
            return
        # Les bornes de dates saisies (voir _generate) sont repassées telles
        # quelles à l'export, pour qu'il puisse indiquer la période couverte
        # en clair (voir _period_range_label côté database.py) — sans ça,
        # un fichier exporté ne permettait pas de savoir sur quelle période
        # portait son contenu une fois hors de l'application.
        _, date_from = self._parse_date(self.date_from_var.get())
        _, date_to = self._parse_date(self.date_to_var.get())
        # Demande du 2026-09-14 : l'export doit refléter EXACTEMENT le
        # filtre "Club" actuellement sélectionné à l'écran (voir
        # _club_filtered_players, même logique que _refresh_display,
        # jamais dupliquée) — auparavant self.summary (donc TOUS les
        # clubs) était transmis tel quel, quel que soit le filtre affiché.
        # "tournaments" n'est, lui, jamais filtré par club (ni à l'écran
        # ni ici) : seul "players" l'est, comme dans _refresh_display.
        # Un nouveau dict (jamais une mutation de self.summary) : un
        # "Générer" ultérieur ou un changement de filtre ne doit jamais
        # dépendre de l'état d'un export précédent.
        #
        # "rang" (demande du 2026-09-18) : calculé ici, APRÈS le filtre
        # Club, comme à l'écran — mais _stats_players_with_rank ne trie
        # JAMAIS son résultat (voir sa docstring) : l'ordre exporté reste
        # donc celui de _club_filtered_players, lui-même celui de build_
        # period_summary (total_points décroissant), jamais un éventuel
        # tri visuel temporaire du Treeview (self.stats_players_sort,
        # volontairement non consulté ici).
        export_summary = {
            "tournaments": self.summary["tournaments"],
            "players": _stats_players_with_rank(self._club_filtered_players(self.summary["players"])),
        }
        PeriodExportDialog(self, export_summary, date_from=date_from, date_to=date_to)


class PeriodExportDialog(tk.Toplevel):
    """Choix du format (CSV / Excel) et des colonnes à exporter pour une
    synthèse par période déjà générée (voir PeriodSummaryDialog)."""

    def __init__(self, master, summary, date_from=None, date_to=None):
        super().__init__(master)
        self.summary = summary
        self.date_from = date_from
        self.date_to = date_to
        self.title("Exporter la synthèse")
        self.configure(bg=FELT_DARK)
        self.geometry("480x560")
        self.transient(master)
        self.grab_set()

        # Reprend le format et les colonnes cochées/décochées lors du
        # dernier export de ce type, plutôt que de repartir de zéro à
        # chaque fois (tout coché, CSV).
        self.format_var = tk.StringVar(value=export_prefs.load_format("period"))
        saved_t = export_prefs.load_columns(
            "period_tournament", [k for k, _, _ in PERIOD_TOURNAMENT_COLUMNS]
        )
        saved_p = export_prefs.load_columns(
            "period_player", [k for k, _, _ in PERIOD_PLAYER_COLUMNS]
        )
        self.tournament_vars = {
            key: tk.BooleanVar(value=key in saved_t) for key, _, _ in PERIOD_TOURNAMENT_COLUMNS
        }
        self.player_vars = {
            key: tk.BooleanVar(value=key in saved_p) for key, _, _ in PERIOD_PLAYER_COLUMNS
        }

        # Barre du haut : juste le format et un bouton pour fermer sans
        # exporter. Il n'y a volontairement plus de bouton "Exporter..."
        # unique ici : chaque tableau ci-dessous a le sien (voir
        # _build_column_checks), pour qu'il soit toujours sans ambiguïté
        # de savoir lequel des deux tableaux on est en train d'exporter.
        top_bar = ttk.Frame(self)
        top_bar.pack(side="top", fill="x", padx=14, pady=(14, 0))
        ttk.Button(top_bar, text="Fermer", command=self.destroy).pack(side="right")

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=(8, 8))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.format_var, value="pdf").pack(
            side="left", padx=10, pady=6
        )

        t_frame = ttk.LabelFrame(self, text="Colonnes — Tournois de la période")
        t_frame.pack(fill="x", padx=14, pady=8)
        self._build_column_checks(
            t_frame, PERIOD_TOURNAMENT_COLUMNS, self.tournament_vars,
            kind="tournament", prefs_key="period_tournament",
            title="Exporter les tournois de la période", filename_prefix="synthese_tournois",
        )

        p_frame = ttk.LabelFrame(self, text="Colonnes — Classement des joueurs")
        p_frame.pack(fill="both", expand=True, padx=14, pady=8)
        self._build_column_checks(
            p_frame, PERIOD_PLAYER_COLUMNS, self.player_vars,
            kind="player", prefs_key="period_player",
            title="Exporter le classement des joueurs", filename_prefix="synthese_joueurs",
        )

    def _build_column_checks(self, parent, columns, var_map, kind, prefs_key, title, filename_prefix):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(
            bar, text="Tout cocher",
            command=lambda: [v.set(True) for v in var_map.values()],
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Tout décocher",
            command=lambda: [v.set(False) for v in var_map.values()],
        ).pack(side="left")
        # Bouton d'export propre à CE tableau, dans la barre du haut de sa
        # propre section (donc toujours visible, quelle que soit la
        # hauteur prise par ses cases à cocher en dessous).
        ttk.Button(
            bar, text="Exporter ce tableau...",
            command=lambda: self._do_export_section(kind, var_map, prefs_key, title, filename_prefix),
        ).pack(side="right")
        grid = ttk.Frame(parent)
        grid.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for idx, (key, header, _fn) in enumerate(columns):
            ttk.Checkbutton(grid, text=header, variable=var_map[key]).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2
            )

    def _do_export_section(self, kind, var_map, prefs_key, title, filename_prefix):
        keys = [k for k, v in var_map.items() if v.get()]
        if not keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        # Mémorise ce choix (colonnes de CE tableau + format), pour le
        # proposer par défaut au prochain export.
        export_prefs.save_columns(prefs_key, keys)
        export_prefs.save_format("period", self.format_var.get())

        fmt = self.format_var.get()
        ext = {"csv": ".csv", "xlsx": ".xlsx", "pdf": ".pdf"}[fmt]
        filetypes = {
            "csv": [("Fichier CSV", "*.csv")],
            "xlsx": [("Fichier Excel", "*.xlsx")],
            "pdf": [("Fichier PDF", "*.pdf")],
        }[fmt]
        path = filedialog.asksaveasfilename(
            title=title,
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"{filename_prefix}{ext}",
        )
        if not path:
            return

        # N'exporte QUE ce tableau : l'autre reçoit une liste de colonnes
        # vide, ce qui lui fait sauter entièrement sa section (voir
        # build_period_summary / export_period_summary_csv|xlsx|pdf).
        t_keys = keys if kind == "tournament" else []
        p_keys = keys if kind == "player" else []
        try:
            if fmt == "xlsx":
                export_period_summary_xlsx(
                    self.summary, path, tournament_keys=t_keys, player_keys=p_keys,
                    date_from=self.date_from, date_to=self.date_to,
                )
            elif fmt == "pdf":
                export_period_summary_pdf(
                    self.summary, path, tournament_keys=t_keys, player_keys=p_keys,
                    date_from=self.date_from, date_to=self.date_to,
                )
            else:
                export_period_summary_csv(
                    self.summary, path, tournament_keys=t_keys, player_keys=p_keys,
                    date_from=self.date_from, date_to=self.date_to,
                )
        except ImportError:
            show_missing_export_module(fmt)
            return
        self.destroy()
        # Ouvre directement le fichier généré (Excel/LibreOffice ou
        # l'application associée aux .csv), sans avoir à aller le chercher.
        open_file_with_default_app(path)


class ResultsExportDialog(tk.Toplevel):
    """Choix du format (CSV / Excel) et des colonnes à exporter pour le
    classement final du tournoi en cours (Fichier > Exporter les
    résultats..., ou depuis l'onglet Gains)."""

    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.title("Exporter les résultats")
        self.configure(bg=FELT_DARK)
        self.geometry("420x420")
        self.transient(master)
        self.grab_set()

        # Reprend le format et les colonnes cochées/décochées lors du
        # dernier export de ce type, plutôt que de repartir de zéro.
        self.format_var = tk.StringVar(value=export_prefs.load_format("results"))
        saved_cols = export_prefs.load_columns("results", [k for k, _, _ in RESULT_COLUMNS])
        self.col_vars = {
            key: tk.BooleanVar(value=key in saved_cols) for key, _, _ in RESULT_COLUMNS
        }

        # Barre de boutons tout en haut de la fenêtre, toujours visible en
        # premier quelle que soit la hauteur du reste du contenu.
        btns = ttk.Frame(self)
        btns.pack(side="top", fill="x", padx=14, pady=(14, 8))
        ttk.Button(btns, text="Exporter...", command=self._do_export).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.format_var, value="pdf").pack(
            side="left", padx=10, pady=6
        )

        cols_frame = ttk.LabelFrame(self, text="Colonnes à exporter")
        cols_frame.pack(fill="both", expand=True, padx=14, pady=8)
        bar = ttk.Frame(cols_frame)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(
            bar, text="Tout cocher",
            command=lambda: [v.set(True) for v in self.col_vars.values()],
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Tout décocher",
            command=lambda: [v.set(False) for v in self.col_vars.values()],
        ).pack(side="left")
        grid = ttk.Frame(cols_frame)
        grid.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for idx, (key, header, _fn) in enumerate(RESULT_COLUMNS):
            ttk.Checkbutton(grid, text=header, variable=self.col_vars[key]).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2
            )

    def _do_export(self):
        keys = [k for k, v in self.col_vars.items() if v.get()]
        if not keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        export_prefs.save_columns("results", keys)
        export_prefs.save_format("results", self.format_var.get())

        name = self.db.get_setting("tournament_name", "tournoi")
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "tournoi"
        fmt = self.format_var.get()
        ext = {"csv": ".csv", "xlsx": ".xlsx", "pdf": ".pdf"}[fmt]
        filetypes = {
            "csv": [("Fichier CSV", "*.csv")],
            "xlsx": [("Fichier Excel", "*.xlsx")],
            "pdf": [("Fichier PDF", "*.pdf")],
        }[fmt]
        path = filedialog.asksaveasfilename(
            title="Exporter les résultats",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"resultats_{safe_name}{ext}",
        )
        if not path:
            return

        try:
            if fmt == "xlsx":
                self.db.export_results_xlsx(path, columns=keys)
            elif fmt == "pdf":
                self.db.export_results_pdf(path, columns=keys)
            else:
                self.db.export_results_csv(path, columns=keys)
        except ImportError:
            show_missing_export_module(fmt)
            return
        self.destroy()
        open_file_with_default_app(path)


# Colonnes fixes de l'onglet Classement (Nom, Rang, Éliminé le, Round,
# Éliminé par) — un sous-ensemble de PLAYERS_TAB_COLUMNS, réutilisé tel
# quel pour l'export (voir ClassementExportDialog).
CLASSEMENT_EXPORT_COLUMNS = ["name", "rang", "elim_time", "elim_round", "eliminated_by"]


def _compute_tournament_export_title(db, tab_name):
    """Titre standard "<Onglet> du SitnGo du <club> <tournoi> du <date>"
    (Sit & Go) ou "<Onglet> du Tournoi du <club> <tournoi> du <date>"
    (tournoi classique), utilisé par les exports Classement, Primes et
    Joueurs — `tab_name` : "Classement", "Primes" ou "Joueurs". Voir
    Database._apply_sng_defaults pour le réglage "is_sng"."""
    club = export_prefs.load_value("club_name", "")
    tournament_name = db.get_setting("tournament_name", "Tournoi")
    date = format_date_fr(db.get_tournament_date())
    prefix = "SitnGo" if db.get_setting_int("is_sng", 0) else "Tournoi"
    parts = [p for p in [club, tournament_name] if p]
    base = f"{prefix} du " + " ".join(parts) + f" du {date}"
    return f"{tab_name} du {base}"


class ClassementExportDialog(tk.Toplevel):
    """Choix du format (CSV / Excel / PDF) pour l'export de l'onglet
    Classement (Nom, Rang, Éliminé le, Round, Éliminé par) — colonnes
    fixes, pas de sélection possible (contrairement aux autres exports).
    Réutilise les fonctions d'export de l'onglet Joueurs (mêmes colonnes,
    voir CLASSEMENT_EXPORT_COLUMNS), triées par Rang."""

    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.title("Exporter le classement")
        self.configure(bg=FELT_DARK)
        self.geometry("340x260")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.format_var = tk.StringVar(value=export_prefs.load_format("classement"))

        btns = ttk.Frame(self)
        btns.pack(side="top", fill="x", padx=14, pady=(14, 8))
        ttk.Button(btns, text="Exporter...", command=self._do_export).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=8)
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            anchor="w", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            anchor="w", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.format_var, value="pdf").pack(
            anchor="w", padx=10, pady=6
        )

    def _do_export(self):
        export_prefs.save_format("classement", self.format_var.get())

        name = self.db.get_setting("tournament_name", "tournoi")
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "tournoi"
        fmt = self.format_var.get()
        ext = {"csv": ".csv", "xlsx": ".xlsx", "pdf": ".pdf"}[fmt]
        filetypes = {
            "csv": [("Fichier CSV", "*.csv")],
            "xlsx": [("Fichier Excel", "*.xlsx")],
            "pdf": [("Fichier PDF", "*.pdf")],
        }[fmt]
        path = filedialog.asksaveasfilename(
            title="Exporter le classement",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"classement_{safe_name}{ext}",
        )
        if not path:
            return

        title = _compute_tournament_export_title(self.db, "Classement")
        try:
            if fmt == "xlsx":
                self.db.export_players_xlsx(
                    path, columns=CLASSEMENT_EXPORT_COLUMNS, sort_column="rang", ascending=True,
                    title=title, show_prize_pool=False,
                )
            elif fmt == "pdf":
                self.db.export_players_pdf(
                    path, columns=CLASSEMENT_EXPORT_COLUMNS, sort_column="rang", ascending=True,
                    title=title, show_prize_pool=False,
                )
            else:
                self.db.export_players_csv(path, columns=CLASSEMENT_EXPORT_COLUMNS, sort_column="rang", ascending=True)
        except ImportError:
            show_missing_export_module(fmt)
            return
        self.destroy()
        open_file_with_default_app(path)


class PlayersExportDialog(tk.Toplevel):
    """Choix du format (CSV / Excel) et des colonnes à exporter pour le
    tableau de l'onglet Joueurs tel qu'affiché (nom, table, siège, chips,
    achats, prime en jeu, statut, rang). Distinct de "Exporter les
    résultats..." (menu Fichier, classement nominatif avec gains)."""

    def __init__(self, master, db, sort_state=None):
        super().__init__(master)
        self.db = db
        # Tri actuellement appliqué dans l'onglet Joueurs (colonne cliquée
        # + sens) : repris tel quel à l'export, pour que l'ordre du
        # fichier corresponde à ce qui est affiché à l'écran.
        self.sort_state = sort_state or {"column": None, "ascending": True}
        self.title("Exporter les joueurs")
        self.configure(bg=FELT_DARK)
        self.geometry("420x420")
        self.transient(master)
        self.grab_set()

        self.format_var = tk.StringVar(value=export_prefs.load_format("players"))
        saved_cols = export_prefs.load_columns("players", [k for k, _, _ in PLAYERS_TAB_COLUMNS])
        self.col_vars = {
            key: tk.BooleanVar(value=key in saved_cols) for key, _, _ in PLAYERS_TAB_COLUMNS
        }

        btns = ttk.Frame(self)
        btns.pack(side="top", fill="x", padx=14, pady=(14, 8))
        ttk.Button(btns, text="Exporter...", command=self._do_export).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

        sort_col = self.sort_state.get("column")
        if sort_col:
            headers_by_key = {k: h for k, h, _ in PLAYERS_TAB_COLUMNS}
            sort_label = headers_by_key.get(sort_col, sort_col)
            direction = "croissant" if self.sort_state.get("ascending", True) else "décroissant"
            ttk.Label(
                self, foreground=MUTED,
                text=f"Tri actuel repris à l'export : {sort_label} ({direction}).",
            ).pack(fill="x", padx=14, pady=(0, 4))

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.format_var, value="pdf").pack(
            side="left", padx=10, pady=6
        )

        cols_frame = ttk.LabelFrame(self, text="Colonnes à exporter")
        cols_frame.pack(fill="both", expand=True, padx=14, pady=8)
        bar = ttk.Frame(cols_frame)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(
            bar, text="Tout cocher",
            command=lambda: [v.set(True) for v in self.col_vars.values()],
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Tout décocher",
            command=lambda: [v.set(False) for v in self.col_vars.values()],
        ).pack(side="left")
        grid = ttk.Frame(cols_frame)
        grid.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for idx, (key, header, _fn) in enumerate(PLAYERS_TAB_COLUMNS):
            ttk.Checkbutton(grid, text=header, variable=self.col_vars[key]).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2
            )

    def _do_export(self):
        keys = [k for k, v in self.col_vars.items() if v.get()]
        if not keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        export_prefs.save_columns("players", keys)
        export_prefs.save_format("players", self.format_var.get())

        name = self.db.get_setting("tournament_name", "tournoi")
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "tournoi"
        fmt = self.format_var.get()
        ext = {"csv": ".csv", "xlsx": ".xlsx", "pdf": ".pdf"}[fmt]
        filetypes = {
            "csv": [("Fichier CSV", "*.csv")],
            "xlsx": [("Fichier Excel", "*.xlsx")],
            "pdf": [("Fichier PDF", "*.pdf")],
        }[fmt]
        path = filedialog.asksaveasfilename(
            title="Exporter les joueurs",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"joueurs_{safe_name}{ext}",
        )
        if not path:
            return

        sort_column = self.sort_state.get("column")
        ascending = self.sort_state.get("ascending", True)
        title = _compute_tournament_export_title(self.db, "Joueurs")
        try:
            if fmt == "xlsx":
                self.db.export_players_xlsx(
                    path, columns=keys, sort_column=sort_column, ascending=ascending,
                    title=title, show_prize_pool=False,
                )
            elif fmt == "pdf":
                self.db.export_players_pdf(
                    path, columns=keys, sort_column=sort_column, ascending=ascending,
                    title=title, show_prize_pool=False,
                )
            else:
                self.db.export_players_csv(
                    path, columns=keys, sort_column=sort_column, ascending=ascending
                )
        except ImportError:
            show_missing_export_module(fmt)
            return
        self.destroy()
        open_file_with_default_app(path)


class PrimesExportDialog(tk.Toplevel):
    """Choix du tableau (Récapitulatif des primes / Historique du bounty
    progressif — les 2 tableaux de l'onglet Primes), du format (CSV /
    Excel / PDF) et des colonnes à exporter."""

    def __init__(self, master, db, sort_state=None):
        super().__init__(master)
        self.db = db
        # Tri actuellement appliqué dans l'onglet Primes (colonne cliquée
        # + sens) : repris tel quel à l'export, uniquement pertinent pour
        # le Récapitulatif (l'Historique est toujours du plus récent au
        # plus ancien).
        self.sort_state = sort_state or {"column": "total", "ascending": False}
        self.title("Exporter les primes")
        self.configure(bg=FELT_DARK)
        self.geometry("420x480")
        self.transient(master)
        self.grab_set()

        self.kind_var = tk.StringVar(value="summary")
        self.format_var = tk.StringVar(value=export_prefs.load_format("primes"))

        saved_summary_cols = export_prefs.load_columns("primes", [k for k, _, _ in PRIMES_COLUMNS])
        saved_history_cols = export_prefs.load_columns(
            "primes_history", [k for k, _, _ in BOUNTY_HISTORY_COLUMNS]
        )
        self.col_vars_summary = {
            key: tk.BooleanVar(value=key in saved_summary_cols) for key, _, _ in PRIMES_COLUMNS
        }
        self.col_vars_history = {
            key: tk.BooleanVar(value=key in saved_history_cols) for key, _, _ in BOUNTY_HISTORY_COLUMNS
        }

        btns = ttk.Frame(self)
        btns.pack(side="top", fill="x", padx=14, pady=(14, 8))
        ttk.Button(btns, text="Exporter...", command=self._do_export).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

        kind_frame = ttk.LabelFrame(self, text="Tableau à exporter")
        kind_frame.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Radiobutton(
            kind_frame, text="Récapitulatif", variable=self.kind_var, value="summary",
            command=self._refresh_columns_grid,
        ).pack(side="left", padx=10, pady=6)
        ttk.Radiobutton(
            kind_frame, text="Historique", variable=self.kind_var, value="history",
            command=self._refresh_columns_grid,
        ).pack(side="left", padx=10, pady=6)

        self.sort_info_lbl = ttk.Label(self, foreground=MUTED)
        self.sort_info_lbl.pack(fill="x", padx=14, pady=(0, 4))

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.format_var, value="pdf").pack(
            side="left", padx=10, pady=6
        )

        self.cols_frame = ttk.LabelFrame(self, text="Colonnes à exporter")
        self.cols_frame.pack(fill="both", expand=True, padx=14, pady=8)
        self._refresh_columns_grid()

    def _refresh_columns_grid(self):
        """Reconstruit la liste de cases à cocher pour le tableau
        actuellement sélectionné (Récapitulatif ou Historique) — chacun a
        ses propres colonnes et son propre état coché/décoché mémorisé."""
        for w in self.cols_frame.winfo_children():
            w.destroy()

        if self.kind_var.get() == "summary":
            # db.primes_columns() (pas PRIMES_COLUMNS directement) : en-tête
            # "bo_valeur" adapté au mode classique/PKO de CE tournoi, même
            # source que l'onglet Primes et les exports (voir sa docstring).
            columns, var_map = self.db.primes_columns(), self.col_vars_summary
            sort_col = self.sort_state.get("column")
            if sort_col:
                headers_by_key = {k: h for k, h, _ in columns}
                sort_label = headers_by_key.get(sort_col, sort_col)
                direction = "croissant" if self.sort_state.get("ascending", True) else "décroissant"
                self.sort_info_lbl.config(text=f"Tri actuel repris à l'export : {sort_label} ({direction}).")
            else:
                self.sort_info_lbl.config(text="")
        else:
            columns, var_map = BOUNTY_HISTORY_COLUMNS, self.col_vars_history
            self.sort_info_lbl.config(text="Toujours du plus récent au plus ancien.")

        bar = ttk.Frame(self.cols_frame)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(
            bar, text="Tout cocher",
            command=lambda: [v.set(True) for v in var_map.values()],
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Tout décocher",
            command=lambda: [v.set(False) for v in var_map.values()],
        ).pack(side="left")
        grid = ttk.Frame(self.cols_frame)
        grid.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for idx, (key, header, _fn) in enumerate(columns):
            ttk.Checkbutton(grid, text=header, variable=var_map[key]).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2
            )

    def _do_export(self):
        kind = self.kind_var.get()
        var_map = self.col_vars_summary if kind == "summary" else self.col_vars_history
        keys = [k for k, v in var_map.items() if v.get()]
        if not keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        export_prefs.save_columns("primes" if kind == "summary" else "primes_history", keys)
        export_prefs.save_format("primes", self.format_var.get())

        name = self.db.get_setting("tournament_name", "tournoi")
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "tournoi"
        fmt = self.format_var.get()
        ext = {"csv": ".csv", "xlsx": ".xlsx", "pdf": ".pdf"}[fmt]
        filetypes = {
            "csv": [("Fichier CSV", "*.csv")],
            "xlsx": [("Fichier Excel", "*.xlsx")],
            "pdf": [("Fichier PDF", "*.pdf")],
        }[fmt]
        prefix = "primes" if kind == "summary" else "historique_bounty"
        path = filedialog.asksaveasfilename(
            title="Exporter les primes" if kind == "summary" else "Exporter l'historique du bounty",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"{prefix}_{safe_name}{ext}",
        )
        if not path:
            return

        title = _compute_tournament_export_title(self.db, "Primes")
        try:
            if kind == "summary":
                sort_column = self.sort_state.get("column")
                ascending = self.sort_state.get("ascending", True)
                if fmt == "xlsx":
                    self.db.export_primes_xlsx(
                        path, columns=keys, sort_column=sort_column, ascending=ascending,
                        title=title,
                    )
                elif fmt == "pdf":
                    self.db.export_primes_pdf(
                        path, columns=keys, sort_column=sort_column, ascending=ascending,
                        title=title,
                    )
                else:
                    self.db.export_primes_csv(
                        path, columns=keys, sort_column=sort_column, ascending=ascending
                    )
            else:
                if fmt == "xlsx":
                    self.db.export_bounty_history_xlsx(path, columns=keys, title=title)
                elif fmt == "pdf":
                    self.db.export_bounty_history_pdf(path, columns=keys, title=title)
                else:
                    self.db.export_bounty_history_csv(path, columns=keys)
        except ImportError:
            show_missing_export_module(fmt)
            return
        self.destroy()
        open_file_with_default_app(path)


class LogExportDialog(tk.Toplevel):
    """Export du Journal des actions (onglet LOG, correction du
    2026-09-24 — remplace l'export CSV direct implémenté un peu plus tôt
    le même jour) : même principe visuel que PrimesExportDialog ci-dessus
    (Format + Colonnes à exporter + Exporter.../Annuler), réutilisant
    autant que possible ses mêmes mécanismes (export_prefs.load_format/
    save_format/load_columns/save_columns, show_missing_export_module,
    open_file_with_default_app) — SANS section "Tableau à exporter" :
    Primes en a deux (Récapitulatif/Historique), LOG n'a qu'UN seul
    tableau, cette section n'a donc pas de sens ici.

    `rows` : instantané FIGÉ des lignes actuellement affichées dans
    self.log_tree, construit par App._on_log_export AVANT l'ouverture de
    cette fenêtre — jamais relu ni requêté à nouveau ici, ni même à
    l'export ("Exporter...") : "ce que je vois dans le tableau LOG = ce
    qui peut être exporté" (demande explicite), donc aussi les mêmes
    libellés français déjà affichés (NONE -> Non lié, SUCCESS -> Réussi,
    etc. — voir App._populate_log_tree) et jamais device_id/
    tournament_path, qui ne font structurellement pas partie des
    colonnes exportables (voir action_log.LOG_EXPORT_COLUMNS).

    `criteria_line` (ajouté le 2026-09-24 pour le PDF uniquement, étendu
    à CSV/Excel le 2026-09-25 — "les 3 formats doivent afficher les
    mêmes critères et le même nombre d'opérations") : ligne
    "Critères : ..." déjà construite par App._on_log_export (voir
    action_log.format_log_export_criteria) à partir des MÊMES widgets de
    filtre que `rows`, donc forcément cohérente avec eux. Transmise
    désormais aux 3 fonctions action_log.export_csv/export_xlsx/
    export_pdf (voir _do_export) ; chacune y ajoute aussi "Nombre
    d'opérations : N", calculé depuis `rows` DANS la fonction d'export
    elle-même — jamais un compte séparé qui pourrait diverger du
    contenu réel du fichier."""

    def __init__(self, master, rows, criteria_line=None):
        super().__init__(master)
        self.rows = rows
        self.criteria_line = criteria_line
        self.title("Exporter le LOG")
        self.configure(bg=FELT_DARK)
        self.geometry("420x420")
        self.transient(master)
        self.grab_set()

        self.format_var = tk.StringVar(value=export_prefs.load_format("log"))

        saved_cols = export_prefs.load_columns("log", [k for k, _ in action_log.LOG_EXPORT_COLUMNS])
        self.col_vars = {
            key: tk.BooleanVar(value=key in saved_cols) for key, _ in action_log.LOG_EXPORT_COLUMNS
        }

        btns = ttk.Frame(self)
        btns.pack(side="top", fill="x", padx=14, pady=(14, 8))
        ttk.Button(btns, text="Exporter...", command=self._do_export).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=(0, 8))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="PDF", variable=self.format_var, value="pdf").pack(
            side="left", padx=10, pady=6
        )

        cols_frame = ttk.LabelFrame(self, text="Colonnes à exporter")
        cols_frame.pack(fill="both", expand=True, padx=14, pady=8)
        bar = ttk.Frame(cols_frame)
        bar.pack(fill="x", padx=8, pady=(4, 2))
        ttk.Button(
            bar, text="Tout cocher",
            command=lambda: [v.set(True) for v in self.col_vars.values()],
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            bar, text="Tout décocher",
            command=lambda: [v.set(False) for v in self.col_vars.values()],
        ).pack(side="left")
        grid = ttk.Frame(cols_frame)
        grid.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for idx, (key, header) in enumerate(action_log.LOG_EXPORT_COLUMNS):
            ttk.Checkbutton(grid, text=header, variable=self.col_vars[key]).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2
            )

    def _do_export(self):
        keys = [k for k, v in self.col_vars.items() if v.get()]
        if not keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        export_prefs.save_columns("log", keys)
        export_prefs.save_format("log", self.format_var.get())

        columns = action_log.selected_log_export_columns(keys)
        fmt = self.format_var.get()
        ext = {"csv": ".csv", "xlsx": ".xlsx", "pdf": ".pdf"}[fmt]
        filetypes = {
            "csv": [("Fichier CSV", "*.csv")],
            "xlsx": [("Fichier Excel", "*.xlsx")],
            "pdf": [("Fichier PDF", "*.pdf")],
        }[fmt]
        path = filedialog.asksaveasfilename(
            title="Exporter le LOG",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"journal_actions_{datetime.now():%Y%m%d_%H%M%S}{ext}",
        )
        if not path:
            return

        try:
            if fmt == "xlsx":
                action_log.export_xlsx(
                    path, columns, self.rows, title="Journal des actions",
                    criteria_line=self.criteria_line,
                )
            elif fmt == "pdf":
                action_log.export_pdf(
                    path, columns, self.rows, title="Journal des actions",
                    criteria_line=self.criteria_line,
                )
            else:
                action_log.export_csv(
                    path, columns, self.rows, title="Journal des actions",
                    criteria_line=self.criteria_line,
                )
        except ImportError:
            show_missing_export_module(fmt)
            return
        except Exception as e:
            # Diagnostic du 2026-09-24 : une exception D'EXPORT autre
            # qu'ImportError (ex. FPDFUnicodeEncodingException, avant le
            # correctif général de action_log._pdf_text) remontait
            # jusqu'à Tkinter et se retrouvait AVALÉE par le gestionnaire
            # par défaut — aucun fichier créé, aucun message, la fenêtre
            # restait juste plantée là. En l'interceptant nous-mêmes ici,
            # elle n'atteint plus JAMAIS App.report_callback_exception
            # (voir sa docstring) : on appelle donc _log_exception()
            # explicitement pour ne rien perdre du journal de plantage
            # habituel (~/.poker_tournament/crash.log), EN PLUS d'un
            # message clair affiché à l'utilisateur. Fenêtre gardée
            # OUVERTE (pas de self.destroy()) et fichier JAMAIS ouvert
            # (pas d'open_file_with_default_app) : l'export a échoué,
            # rien ne doit laisser croire le contraire.
            _log_exception(type(e), e, e.__traceback__)
            messagebox.showerror(
                "Erreur d'export",
                f"L'export {fmt.upper()} a échoué :\n{e}",
            )
            return
        self.destroy()
        open_file_with_default_app(path)


class ActivationDialog(tk.Toplevel):
    """Fenêtre d'activation de licence, affichée au démarrage tant que ce
    poste n'a pas encore été activé (voir license.py). Bloque le
    lancement de l'application tant qu'elle est ouverte ; `self.activated`
    indique si une licence valide a été enregistrée avant sa fermeture."""

    def __init__(self, master):
        super().__init__(master)
        self.activated = False
        self.title("Activation requise")
        self.geometry("480x360")
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", self._quit)

        ttk.Label(
            self, text="Activation du logiciel", font=("Helvetica", 13, "bold"),
        ).pack(anchor="w", padx=16, pady=(16, 4))
        ttk.Label(
            self,
            text="Ce poste n'est pas encore activé. Communiquez l'identifiant "
                 "ci-dessous à l'éditeur pour recevoir votre clé de licence, "
                 "puis saisissez-la ci-dessous (à faire une seule fois).",
            wraplength=440, justify="left",
        ).pack(anchor="w", padx=16, pady=(0, 12))

        id_frame = ttk.Frame(self)
        id_frame.pack(fill="x", padx=16, pady=(0, 12))
        ttk.Label(id_frame, text="Identifiant de cette machine :").pack(anchor="w")
        row = ttk.Frame(id_frame)
        row.pack(fill="x", pady=(4, 0))
        self.id_entry = ttk.Entry(row, font=("Courier", 11))
        self.id_entry.insert(0, licensing.machine_id_display())
        self.id_entry.configure(state="readonly")
        self.id_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Copier", command=self._copy_id).pack(side="left", padx=(6, 0))

        form = ttk.Frame(self)
        form.pack(fill="x", padx=16, pady=(0, 6))
        ttk.Label(form, text="Nom du club :").pack(anchor="w")
        self.club_entry = ttk.Entry(form)
        self.club_entry.pack(fill="x", pady=(2, 10))
        ttk.Label(form, text="Clé de licence :").pack(anchor="w")
        self.key_entry = ttk.Entry(form, font=("Courier", 11))
        self.key_entry.pack(fill="x", pady=(2, 0))

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=16, pady=16, side="bottom")
        ttk.Button(btns, text="Quitter", command=self._quit).pack(side="right")
        ttk.Button(btns, text="Activer", command=self._activate).pack(side="right", padx=(0, 6))

        self.transient(master)
        self.grab_set()
        self.club_entry.focus_set()
        self.wait_window(self)

    def _copy_id(self):
        self.clipboard_clear()
        self.clipboard_append(licensing.machine_id_display())

    def _activate(self):
        club = self.club_entry.get().strip()
        key = self.key_entry.get().strip()
        if not club or not key:
            messagebox.showerror(
                "Activation", "Renseignez le nom du club et la clé de licence.", parent=self,
            )
            return
        if not licensing.check_key(club, key):
            messagebox.showerror(
                "Activation",
                "Clé invalide pour ce club sur cette machine.\n"
                "Vérifiez l'orthographe exacte du club (celle communiquée à "
                "l'éditeur) et la clé reçue.",
                parent=self,
            )
            return
        licensing.save_license(club, key)
        self.activated = True
        self.destroy()

    def _quit(self):
        self.activated = False
        self.destroy()


class RemoteDeviceRequestWindow(tk.Toplevel):
    """Petite fenêtre flottante Tkinter (demande du 2026-09-09) :
    présente UNE demande d'accès au contrôle à distance à la fois
    (Identifiant/IP, champ Nom, boutons Autoriser/Refuser/Plus tard) —
    disposition définitivement retenue après plusieurs essais
    d'intégration dans la grille de Paramètres, tous abandonnés (le
    dernier ayant élargi la colonne au point de repousser la colonne
    droite hors écran). Totalement indépendante de la mise en page de
    Paramètres : ne touche à aucune de ses dimensions, se déplace
    librement à la souris.

    Une SEULE instance à la fois (voir App._remote_device_popup) : pour
    passer d'une demande à la suivante, App._refresh_remote_device_popup
    repeuple CETTE MÊME fenêtre (voir show_request) plutôt que d'en
    détruire/recréer une — elle reste ainsi exactement à la même
    position, sans le moindre scintillement. Ne connaît elle-même AUCUNE
    règle métier (approbation/révocation/anti-bruteforce/tokens...) :
    se contente d'appeler les callbacks fournis par App, qui seule
    orchestre open_windows (source de vérité partagée)."""

    def __init__(self, master, on_approve, on_refuse, on_later, on_geometry_changed):
        super().__init__(master)
        self._on_approve = on_approve
        self._on_refuse = on_refuse
        self._on_later = on_later
        self._on_geometry_changed = on_geometry_changed
        self.title("Contrôle à distance")
        self.resizable(False, False)
        try:
            self.attributes("-topmost", True)
        except tk.TclError:
            pass
        self.transient(master)
        # "Plus tard" ET la croix de fermeture native ont EXACTEMENT le
        # même effet (voir App._on_remote_device_popup_later, appelé par
        # les deux) : fermer cette fenêtre ne doit jamais, par un autre
        # chemin que le bouton "Refuser" explicite, être interprété comme
        # un refus de la demande.
        self.protocol("WM_DELETE_WINDOW", self._handle_later)
        self.bind("<Configure>", self._on_configure)

        ttk.Label(
            self, text="📱 Nouveau téléphone demande l'accès\nau contrôle à distance",
            font=("Helvetica", 11, "bold"), justify="left",
        ).pack(padx=16, pady=(14, 6), anchor="w")
        self._info_lbl = ttk.Label(self, foreground=MUTED)
        self._info_lbl.pack(padx=16, anchor="w")

        label_row = ttk.Frame(self)
        label_row.pack(padx=16, pady=(10, 4), fill="x")
        ttk.Label(label_row, text="Nom (optionnel) :").pack(side="left")
        self._label_var = tk.StringVar()
        ttk.Entry(label_row, textvariable=self._label_var, width=22).pack(side="left", padx=(6, 0))

        btn_row = ttk.Frame(self)
        btn_row.pack(padx=16, pady=(8, 14), fill="x")
        ttk.Button(btn_row, text="✓ Autoriser", command=self._handle_approve).pack(side="left")
        ttk.Button(btn_row, text="✗ Refuser", command=self._handle_refuse).pack(side="left", padx=(8, 0))
        ttk.Button(btn_row, text="Plus tard", command=self._handle_later).pack(side="right")

    def show_request(self, short_id, ip, label_default):
        """(Re)peuple la fenêtre pour une demande précise — appelée
        aussi bien à la création qu'à chaque passage à la demande
        suivante (voir App._refresh_remote_device_popup) : jamais de
        nouvelle fenêtre créée pour ça, seul le contenu change."""
        self._info_lbl.config(text=f"Identifiant : {short_id}     Adresse IP : {ip}")
        self._label_var.set(label_default)

    def get_label(self):
        return self._label_var.get().strip() or None

    def _handle_approve(self):
        self._on_approve()

    def _handle_refuse(self):
        self._on_refuse()

    def _handle_later(self):
        self._on_later()

    def _on_configure(self, event):
        # <Configure> se déclenche aussi pour un simple redessin interne
        # (pas seulement un déplacement) — inoffensif ici : réécrire la
        # même position ne coûte presque rien, plus simple et robuste
        # que de tenter de distinguer "vrai déplacement" vs "redessin".
        if event.widget is self:
            try:
                self._on_geometry_changed(self.winfo_x(), self.winfo_y())
            except tk.TclError:
                pass


class App(tk.Tk):
    def report_callback_exception(self, exc, val, tb):
        """Tkinter appelle ceci pour toute exception levée dans un callback
        (clic de bouton, etc.) — par défaut, juste affiché dans le
        terminal, avalé sans arrêter l'appli. On le consigne en plus dans
        le journal de plantage (voir _log_exception) pour pouvoir
        diagnostiquer après coup, notamment quand l'appli tourne sans
        terminal visible (contrôle à distance, tests)."""
        _log_exception(exc, val, tb)

    def __init__(self, open_path=None):
        super().__init__()
        self.withdraw()
        # Titre initial (Menu principal, avant tout choix de tournoi —
        # voir _app_title_prefix, demande du 2026-09-09) : identique au
        # préfixe utilisé ensuite par _update_window_title pour une
        # fenêtre de tournoi, pour repérer immédiatement, EN
        # DÉVELOPPEMENT, quel commit une fenêtre déjà ouverte fait
        # tourner. Écrasé par _update_window_title dès qu'un tournoi est
        # choisi (voir _build_header, appelée après _choose_tournament_
        # file) — ce titre-ci n'est donc visible QUE pendant l'écran
        # d'accueil "Bienvenue".
        self.title(_app_title_prefix())
        self.geometry("1200x750")

        self.db = None
        self.clock_window = None
        # "Son prochain changement Blindes" (voir _open_clock_sounds_dialog
        # / _maybe_play_next_blinds_sound) : level_order du dernier round
        # pour lequel ce son a déjà été joué, pour ne le jouer qu'une
        # seule fois par round tant que le compte à rebours reste sous le
        # délai configuré (comparé à level["level_order"] à chaque appel,
        # donc redevient pertinent dès que le niveau change).
        self._next_blinds_sound_played_for_order = None
        # Actions "Élimination"/"Terminé"/"Chronomètre" (raccourcis clavier
        # et contrôle à distance depuis un téléphone, voir
        # _bind_voice_command_shortcuts / remote_control.py) : le thread du
        # petit serveur web ne doit jamais appeler de méthode Tkinter
        # directement (pas thread-safe), il dépose un mot dans cette file,
        # relevée régulièrement par _poll_voice_queue sur le thread
        # principal (voir _tick pour le même principe périodique).
        # voice_awaiting_resume : True entre "Élimination" et soit
        # "Chronomètre" (aucun mouvement causé), soit "Terminé" (un
        # mouvement a eu lieu).
        self.voice_command_queue = queue.Queue()
        self.voice_awaiting_resume = False
        # Bandeau d'élimination (écran projecteur + onglet Chronomètre,
        # voir _queue_elimination_banner / _advance_elimination_banner) :
        # file FIFO sans plafond des éliminations en attente d'affichage
        # (aucune n'est jamais perdue), et bandeau actuellement affiché
        # (dict avec "until", l'échéance epoch) ou None. Recalculé à
        # chaque _refresh_clock_tab (comme movement_alert_active), pas
        # via un self.after() séparé : rien à annuler si le bandeau est
        # écourté manuellement ("Chronomètre" pendant qu'il est affiché,
        # voir _on_voice_word).
        self._elimination_banner_queue = collections.deque()
        self._elimination_banner_current = None
        # "Menu principal" (voir _open_new_window) : Popen du process déjà
        # lancé DEPUIS CETTE fenêtre par un clic précédent, tant qu'il est
        # encore vivant — None si aucun n'a jamais été lancé, ou si le
        # dernier lancé s'est depuis terminé. Sert uniquement à empêcher
        # d'en accumuler plusieurs par des clics répétés ; ne concerne que
        # CE process-ci (chaque tournoi garde sa propre référence).
        self._menu_principal_proc = None
        # Unicité GLOBALE du "Menu principal" (demande du 2026-09-14,
        # distincte de self._menu_principal_proc ci-dessus, qui n'est
        # qu'une optimisation locale à cette fenêtre) : True si CE
        # process a lui-même posé le verrou open_windows.
        # register_menu_principal (voir plus bas dans __init__) — permet
        # à _cleanup_for_close de ne libérer QUE le verrou qu'il détient
        # réellement, jamais celui d'un autre processus. Initialisé ICI,
        # avant même tout risque de sortie anticipée (licence non
        # activée, etc.), pour que _cleanup_for_close (appelée par
        # certains chemins de fermeture) trouve toujours cet attribut.
        self._holds_menu_principal_lock = False
        # Mode Test (demande du 2026-09-09) : outil de test/développement,
        # PROPRE À CE PROCESS, jamais mémorisé nulle part (ni export_prefs,
        # ni réglage de tournoi) — décoché à chaque lancement du logiciel,
        # voir _build_settings_tab (case "Mode Test") et _test_mode_
        # enabled. Défini ici, avant même le choix d'un fichier de
        # tournoi, pour que _update_window_title (appelée dès l'écran
        # d'accueil) puisse toujours le consulter sans crainte d'un
        # attribut manquant.
        self.test_mode_var = tk.BooleanVar(value=False)
        # Contrôle à distance depuis un téléphone (voir remote_control.py).
        self.remote_control_server = None
        self._remote_control_tournament_name = "Tournoi"
        # Identifiant STABLE du tournoi pour le journal LOG (action_log.py,
        # chantier "LOG", 2026-09-24) — même principe/même prudence que
        # _remote_control_tournament_name juste au-dessus (simple attribut
        # tenu à jour depuis le thread principal, jamais self.db lu
        # directement depuis le thread du serveur web) : le NOM affiché
        # d'un tournoi peut changer ou se répéter d'un fichier à l'autre,
        # jamais le chemin de son fichier .tournoi.
        self._remote_control_tournament_path = None
        # Approbation des téléphones (demande du 2026-09-09 ; disposition
        # définitivement fixée le même jour, après plusieurs essais
        # d'intégration dans la grille de Paramètres tous abandonnés :
        # une fenêtre flottante indépendante, voir RemoteDeviceRequest
        # Window/_refresh_remote_device_popup) — état PROPRE à ce
        # process, jamais partagé entre fenêtres (chacune gère sa propre
        # fenêtre/file, ce qui reste correct : n'importe quel responsable
        # présent peut traiter la demande depuis N'IMPORTE QUELLE
        # fenêtre, voir open_windows, la source de vérité partagée).
        #
        # _remote_device_snoozed_keys : clés (browser_id, requested_at)
        # explicitement "Plus tard"-ées — MASQUÉES (fenêtre fermée) tant
        # que Paramètres n'a pas été quitté puis rouvert (voir _is_
        # settings_tab_active/_check_remote_device_requests), jamais
        # définitivement ignorées : la demande reste "pending" côté
        # serveur, le badge 🔔 de l'onglet reste affiché, et revenir sur
        # Paramètres la fait réapparaître SANS attendre une nouvelle
        # tentative du téléphone.
        self._remote_device_snoozed_keys = set()
        # Fenêtre flottante actuellement ouverte (RemoteDeviceRequestWindow)
        # ou None — UNE SEULE à la fois (voir _refresh_remote_device_
        # popup) : repeuplée en place pour la demande suivante plutôt que
        # détruite/recréée, pour rester à la même position sans
        # scintillement.
        self._remote_device_popup = None
        # (browser_id, requested_at) actuellement affichée dans cette
        # fenêtre, ou None — évite de la repeupler pour rien à chaque
        # sondage si rien n'a changé (même principe que _last_remote_
        # devices_panel_signature ci-dessous, appliqué ici à une seule
        # demande plutôt qu'à toute une liste).
        self._remote_device_popup_current_key = None
        # browser_id correspondant à _remote_device_popup_current_key —
        # tenu à jour séparément (jamais reconstruit depuis la clé) pour
        # que les boutons Autoriser/Refuser de la fenêtre (voir
        # _on_remote_device_popup_approve/_refuse) sachent quel appareil
        # cibler sans avoir à rouvrir le registre partagé.
        self._remote_device_popup_current_browser_id = None
        # Dernier état connu (onglet "CA" actif ou non — voir
        # _is_ca_tab_active) — détecte la TRANSITION vers CA (pas
        # seulement "est actif maintenant") pour ne vider _remote_device_
        # snoozed_keys qu'au moment où l'utilisateur y REVIENT, jamais en
        # continu tant qu'il y reste. Renommé le 2026-09-24 (chantier
        # "séparation CA/LOG en CA + LOG") — s'appelait _last_ca_log_tab_
        # active tant que CA et LOG partageaient le même onglet ; même
        # mécanisme, seul l'onglet surveillé (CA seul, jamais LOG) a
        # changé.
        self._last_ca_tab_active = False
        # Dernier "instantané" affiché de la liste "Téléphones" de
        # l'onglet CA (appareils APPROUVÉS uniquement désormais, voir
        # _remote_devices_signature/_refresh_remote_devices_panel) :
        # None tant qu'elle n'a jamais été construite — toute valeur (y
        # compris un instantané "vide") est traitée comme différente
        # d'un premier sondage, pour garantir un premier rendu correct.
        self._last_remote_devices_panel_signature = None
        # Liste des joueurs actifs pour la page "Éliminations" du contrôle
        # à distance — même principe que _remote_control_tournament_name :
        # tenue à jour depuis le thread principal (voir _tick), jamais lue
        # ni écrite depuis le thread du serveur web.
        self._remote_players_cache = []
        # État pause/lecture du chrono, pour le petit bouton ON/OFF à côté
        # de "Chronomètre" sur la page du contrôle à distance — même
        # principe (tenu à jour depuis _tick, jamais lu/écrit depuis le
        # thread du serveur web).
        self._remote_clock_paused = True
        # True s'il existe au moins un mouvement en attente dans l'onglet
        # Mouvements (voir _tick : self.db.count_seat_moves() — EXACTEMENT
        # la même source que _refresh_moves_tab/self.db.get_seat_moves(),
        # aucune logique parallèle) — fait clignoter "📋 Afficher
        # Mouvements" sur le téléphone tant que c'est vrai (voir
        # remote_control.py, sondé via /clock_state comme _remote_clock_
        # paused ci-dessus, même principe thread-safe).
        self._remote_has_pending_moves = False
        # Mouvements ACTUELLEMENT en attente (demande du 2026-09-19, page
        # "Mouvements" du contrôle à distance) — liste de dicts {id,
        # player_name, old_table_name, old_seat, new_table_name,
        # new_seat}, même principe que _remote_players_cache ci-dessus :
        # tenue à jour depuis le thread principal (voir _tick/_refresh_
        # remote_moves_cache), jamais lue ni écrite depuis le thread du
        # serveur web.
        self._remote_moves_cache = []
        # Permissions DIRTO accordées POUR CE TOURNOI (Phase 4,
        # "Sécurisation du Contrôle à distance", 2026-09-20) — {dirto_name
        # -> frozenset des clés REMOTE_PERMISSION_*}, même principe que
        # _remote_players_cache ci-dessus : tenue à jour depuis le thread
        # principal (voir _tick/_refresh_remote_dirto_permissions_cache),
        # jamais lue ni écrite depuis le thread du serveur web.
        self._remote_dirto_permissions_cache = {}
        # Positionné (côté thread du serveur web, voir _remote_upload_photo)
        # dès qu'une photo vient d'être envoyée depuis le téléphone, pour
        # que _tick rafraîchisse la colonne Photo (Répertoire/Joueurs) sans
        # attendre un changement d'onglet — même principe que les deux
        # attributs juste au-dessus.
        self._remote_photo_uploaded = False
        # Bouton "Fin de la partie" du contrôle à distance (voir
        # _remote_end_tournament) : True dès que cette fermeture a été
        # déclenchée une première fois (par ce téléphone ou un autre,
        # confirmations quasi simultanées comprises) — empêche tout appel
        # ultérieur à _on_close()/self.destroy() sur une fenêtre déjà
        # détruite.
        self._remote_end_tournament_triggered = False
        # Rééquilibrage simple : question "quel siège est actuellement
        # grosse blinde ?" (version TEST, voir database.py:
        # rebalance_tables / resolve_pending_rebalance) — affichée
        # uniquement sur les téléphones du contrôle à distance, plus
        # aucune fenêtre Mac (voir _check_pending_rebalance).
        # _remote_pending_rebalance : même principe que _remote_clock_
        # paused ci-dessus — copie de self.db.pending_rebalance tenue à
        # jour depuis le thread principal (_tick / _check_pending_
        # rebalance), jamais lue ni écrite depuis le thread du serveur de
        # contrôle à distance.
        self._remote_pending_rebalance = None
        # Résultat de chaque élimination demandée depuis le téléphone
        # (demande du 2026-09-08) : {request_id: {"ok": bool, "message":
        # str}}, écrit par _remote_eliminate (thread Tk, via la file
        # voice_command_queue comme d'habitude) et lu/consommé par
        # _remote_eliminate_request (thread HTTP du téléphone concerné,
        # voir sa docstring) — permet au téléphone d'afficher un message
        # explicite (ex. refus PKO sans éliminateur) au lieu d'un simple
        # "ok" silencieux. Dict simple, pas de verrou : lecture/écriture
        # d'une clé à la fois, déjà sûr sous le GIL (même principe que
        # _remote_pending_rebalance ci-dessus, jamais verrouillé non plus).
        self._remote_elimination_results = {}
        # Idempotence des actions distantes sensibles (demande du
        # 2026-09-19, voir _remote_eliminate_request) : {client_request_
        # id: {"ts": float, "result": dict|None}} — même principe (dict
        # nu, jamais verrouillé) que _remote_elimination_results ci-
        # dessus, purgé au fil de l'eau (voir _prune_remote_action_dedup),
        # jamais persisté (repart à vide à chaque lancement, sans
        # incidence : un identifiant n'a de sens que pendant la fenêtre
        # d'un retry réseau, jamais au-delà).
        self._remote_action_dedup = {}
        # Résultat de chaque confirmation individuelle de mouvement
        # demandée depuis le téléphone (demande du 2026-09-19) — même
        # principe que _remote_elimination_results ci-dessus : {request_
        # id: {"ok": bool, "all_done": bool}}, écrit par _remote_confirm_
        # move (thread Tk) et lu/consommé par _remote_confirm_move_request
        # (thread HTTP). Pas de dédoublonnage par client_request_id ici
        # (contrairement à _remote_action_dedup) : confirm_seat_move est
        # idempotent par nature (DELETE d'une ligne déjà supprimée ne fait
        # rien), un double-tap ou un retry réseau ne présente donc aucun
        # risque de double effet métier.
        self._remote_move_confirm_results = {}
        # Synchronisation iPhone -> Mac SANS dépendre d'un Lobby ouvert
        # (voir _check_phone_selected_pid, appelé depuis _tick) : dernier
        # pid de tournoi déjà traité PAR CETTE fenêtre, même principe et
        # même nom que LobbyDialog._last_synced_phone_pid (voir son
        # commentaire) — initialisé avec la valeur déjà mémorisée pour ne
        # pas se ramener soi-même au premier plan sur un choix téléphone
        # antérieur au démarrage de cette fenêtre.
        self._last_synced_phone_pid = open_windows.get_phone_selected_pid()
        self._apply_theme()

        # Ferme l'écran de démarrage ("Chargement en cours...") : Tkinter
        # est maintenant prêt à afficher une fenêtre (activation de
        # licence ou choix du fichier .tournoi, juste en dessous) — inutile
        # de laisser le splash devant plus longtemps. Sans effet si l'appli
        # n'a pas été lancée avec ce splash (voir import pyi_splash).
        if pyi_splash is not None:
            pyi_splash.close()

        # Verrou anti-copie (voir license.py) : sans effet tant que le
        # logiciel tourne depuis les sources (aucun secret injecté) ;
        # actif uniquement sur un exécutable compilé pour distribution.
        if not licensing.is_licensed():
            if not ActivationDialog(self).activated:
                self.destroy()
                return

        # `open_path` : ouvre directement ce fichier .tournoi, sans passer
        # par l'écran d'accueil — utilisé quand une nouvelle fenêtre est
        # lancée depuis le Lobby SNG pour un tournoi précis (voir
        # spawn_app_process / LobbyDialog._open_selected).
        if open_path and os.path.exists(open_path):
            # Réservation ATOMIQUE inter-processus AVANT toute ouverture
            # réelle du fichier (demande du 2026-09-16, "interdire
            # l'ouverture simultanée du même .tournoi") — voir
            # open_windows.try_register, qui refuse si ce chemin est déjà
            # détenu par un autre processus vivant, quel qu'il soit.
            # Lancement interne (Lobby, association de fichier) : personne
            # n'est devant un écran d'accueil à qui expliquer quoi que ce
            # soit, on ramène juste la fenêtre déjà ouverte au premier
            # plan et on abandonne silencieusement CE lancement-ci — même
            # principe que _acquire_menu_principal_lock_if_needed.
            existing_pid = open_windows.try_register(open_path)
            if existing_pid is not None:
                open_windows.bring_pid_to_front(existing_pid)
                self.destroy()
                return
            try:
                self.db = Database(open_path)
            except Exception as e:
                # Réservation prise mais ouverture réelle échouée : la
                # libérer immédiatement, sinon elle resterait bloquée
                # (sans conséquence durable — _prune()/_pid_is_running la
                # nettoierait de toute façon dès que ce process se
                # termine — mais autant le faire proprement tout de
                # suite plutôt que d'attendre).
                open_windows.unregister(open_path)
                messagebox.showerror(
                    "Erreur", f"Impossible d'ouvrir ce fichier :\n{e}"
                )
                self.destroy()
                return
        else:
            if not self._acquire_menu_principal_lock_if_needed(open_path):
                # Une autre instance (MÊME ligne prod/test) existe déjà
                # et a été ramenée au premier plan — voir la docstring de
                # _acquire_menu_principal_lock_if_needed : self.after
                # (destroy différé) déjà programmé, rien d'autre à faire.
                return
            if not self._choose_tournament_file():
                # _cleanup_for_close (pas juste self.destroy()) : libère
                # aussi le verrou de Menu principal éventuellement posé
                # juste au-dessus (voir self._holds_menu_principal_lock)
                # — sans quoi annuler l'écran "Bienvenue" (Sit & Go NON
                # créé) laisserait le verrou posé indéfiniment, bloquant
                # à tort tout futur lancement externe. self.db est
                # encore None ici : _cleanup_for_close n'y touche pas.
                self._cleanup_for_close()
                self.destroy()
                return

        # Ce tournoi est déjà enregistré à ce stade (voir open_windows.
        # try_register, appelé plus haut — branche `open_path`, ou dans
        # _choose_tournament_file pour l'écran "Bienvenue" — AVANT
        # l'ouverture réelle de Database) : jamais un second appel à
        # register() ici, qui referait exactement le même travail en
        # double (voir la docstring de try_register).
        # Aligne ce tournoi (nouveau OU existant) sur "Calculer les
        # primes" VERROUILLÉ de la session, s'il y a lieu (demande du
        # 2026-09-09, 4e relecture) — AVANT _build_tabs()/_build_
        # settings_tab() ci-dessous, pour que la case et le grisement de
        # la section reflètent le bon état dès la toute première image,
        # jamais l'ancien état affiché puis corrigé au tick suivant.
        _align_primes_enabled_on_open(self.db)
        # Détection SEULE (aucune réparation automatique, demande
        # explicite du 2026-09-10) d'une incohérence de capacité déjà
        # présente dans le fichier ouvert — ex. un ancien fichier créé
        # avant le correctif de l'architecture de rééquilibrage. Après
        # deiconify() pour que la fenêtre soit déjà visible derrière
        # l'avertissement, plutôt qu'un dialogue sans fenêtre parente
        # affichée.
        self.deiconify()
        self._warn_if_table_integrity_issue()
        self._build_header()
        self._build_menu()
        self._build_tabs()
        # Sans cet appel, l'onglet affiché au tout premier lancement (Joueurs)
        # restait vide tant qu'on n'avait pas changé d'onglet au moins une
        # fois : seul <<NotebookTabChanged>> déclenchait un rafraîchissement,
        # jamais la construction initiale — invisible la plupart du temps,
        # mais flagrant pour un tournoi créé avec des joueurs déjà choisis.
        self._refresh_all()
        self._tick()
        self._poll_voice_queue()
        # silent=True : au lancement automatique d'une fenêtre, le port
        # peut déjà être pris par une autre fenêtre de l'appli ouverte en
        # parallèle (Sit & Go multiples) — cas normal, pas la peine
        # d'interrompre l'ouverture avec une fenêtre d'erreur pour ça.
        self._start_remote_control_if_enabled(silent=True)
        self._bind_voice_command_shortcuts()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------
    # Thème visuel ("table de poker" : feutre vert / doré / crème)
    # ---------------------------------------------------------------
    def _apply_theme(self):
        self.configure(bg=FELT_DARK)
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        base_font = ("Helvetica", 11)
        bold_font = ("Helvetica", 11, "bold")

        style.configure(".", font=base_font)
        style.configure("TFrame", background=FELT)
        style.configure("TLabel", background=FELT, foreground=CREAM)
        style.configure(
            "TLabelframe", background=FELT, bordercolor=GOLD_DARK, relief="groove"
        )
        style.configure(
            "TLabelframe.Label", background=FELT, foreground=GOLD, font=bold_font
        )
        style.configure(
            "TButton", background=FELT_LIGHT, foreground=GOLD, padding=(10, 6),
            borderwidth=0, focuscolor=FELT_LIGHT, font=bold_font,
        )
        style.map(
            "TButton",
            background=[("active", GOLD), ("pressed", GOLD_DARK), ("disabled", FELT)],
            foreground=[("active", TEXT_DARK), ("pressed", TEXT_DARK), ("disabled", MUTED)],
        )
        # Variante rouge pour les actions destructrices/à risque (ex :
        # "Éliminer"), pour bien les distinguer visuellement du reste.
        style.configure(
            "Danger.TButton", background=DANGER_RED, foreground=CREAM, padding=(10, 6),
            borderwidth=0, focuscolor=DANGER_RED, font=bold_font,
        )
        style.map(
            "Danger.TButton",
            background=[("active", DANGER_RED_ACTIVE), ("pressed", DANGER_RED), ("disabled", FELT)],
            foreground=[("active", CREAM), ("pressed", CREAM), ("disabled", MUTED)],
        )
        # Variante bleue pour "Annule Eliminer" (demande du 2026-09-17) —
        # même couleur que le bandeau d'élimination (ELIMINATION_BLUE),
        # pour la distinguer à la fois du rouge "Éliminer" et du reste des
        # boutons neutres.
        style.configure(
            "Undo.TButton", background=ELIMINATION_BLUE, foreground=CREAM, padding=(10, 6),
            borderwidth=0, focuscolor=ELIMINATION_BLUE, font=bold_font,
        )
        style.map(
            "Undo.TButton",
            background=[("active", ELIMINATION_BLUE_ACTIVE), ("pressed", ELIMINATION_BLUE), ("disabled", FELT)],
            foreground=[("active", CREAM), ("pressed", CREAM), ("disabled", MUTED)],
        )
        style.configure("TCheckbutton", background=FELT, foreground=CREAM)
        style.map("TCheckbutton", background=[("active", FELT)])
        style.configure(
            "TEntry", fieldbackground=CREAM, foreground=TEXT_DARK, insertcolor=TEXT_DARK,
            bordercolor=GOLD_DARK,
        )
        style.configure(
            "TSpinbox", fieldbackground=CREAM, foreground=TEXT_DARK, arrowcolor=TEXT_DARK,
        )
        style.configure("TNotebook", background=FELT_DARK, borderwidth=0)
        style.configure(
            "TNotebook.Tab", background=FELT, foreground=CREAM, padding=(18, 9),
            font=bold_font,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", GOLD)],
            foreground=[("selected", TEXT_DARK)],
        )
        style.configure(
            "Treeview", background=CREAM, fieldbackground=CREAM, foreground=TEXT_DARK,
            rowheight=27, borderwidth=0,
        )
        style.configure(
            "Treeview.Heading", background=FELT_DARK, foreground=GOLD, font=bold_font,
            relief="flat",
        )
        style.map("Treeview.Heading", background=[("active", FELT)])
        style.map(
            "Treeview",
            background=[("selected", GOLD)],
            foreground=[("selected", TEXT_DARK)],
        )
        # Style dédié au tableau des joueurs : lignes/police plus grandes
        # pour que les cases à cocher (☐/☑) soient plus faciles à voir et
        # à cliquer (ttk ne permet pas d'agrandir une seule colonne).
        style.configure(
            "Players.Treeview", background=CREAM, fieldbackground=CREAM,
            foreground=TEXT_DARK, rowheight=38, borderwidth=0,
            font=("Helvetica", 13),
        )
        style.map(
            "Players.Treeview",
            background=[("selected", GOLD)],
            foreground=[("selected", TEXT_DARK)],
        )
        style.configure(
            "Players.Treeview.Heading", background=FELT_DARK, foreground=GOLD,
            font=bold_font, relief="flat",
        )
        style.map("Players.Treeview.Heading", background=[("active", FELT)])
        style.configure(
            "Vertical.TScrollbar", background=FELT_LIGHT, troughcolor=FELT_DARK,
            arrowcolor=GOLD, bordercolor=FELT_DARK,
        )

    def _build_header(self):
        header = tk.Frame(self, bg=FELT_DARK)
        header.pack(fill="x", side="top")
        inner = tk.Frame(header, bg=FELT_DARK)
        inner.pack(fill="x", padx=18)
        # Réorganisation : l'ancien "🏠 Menu principal" (fermait le tournoi
        # en cours pour revenir à l'écran Bienvenue DANS cette même
        # fenêtre, voir l'ex-_back_to_main_menu) cède sa place à "📋
        # Lobby" — accès direct plus utile au quotidien. Son rôle
        # d'origine ("afficher l'écran Bienvenue") est repris par
        # l'ancien "🚀 Nouveau SitnGO", renommé "🏠 Menu principal" : il
        # ouvrait déjà une fenêtre indépendante avec cet écran
        # (_open_new_window, comportement inchangé), il l'affiche donc
        # bien lui aussi, juste dans une nouvelle fenêtre plutôt qu'en
        # remplaçant celle-ci. Ordre affiché : Menu principal puis Lobby
        # (interverti par rapport à un tout premier essai).
        new_window_btn = ttk.Button(
            inner, text="🏠  Menu principal", command=self._open_new_window,
        )
        new_window_btn.pack(side="left", pady=14)
        Tooltip(
            new_window_btn,
            "Ouvre une nouvelle fenêtre indépendante de l'application, sans\n"
            "fermer celle-ci — pratique pour gérer plusieurs Sit & Go (ou\n"
            "tournois) en même temps, chacun dans sa propre fenêtre. Vous\n"
            "y retrouverez l'écran d'accueil habituel (Nouveau tournoi /\n"
            "Sit & Go rapide / Ouvrir).",
        )
        lobby_header_btn = ttk.Button(
            inner, text="📋  Lobby", command=self._open_lobby,
        )
        lobby_header_btn.pack(side="left", padx=(8, 0), pady=14)
        Tooltip(
            lobby_header_btn,
            "Vue d'ensemble de tous les tournois/Sit & Go actuellement\n"
            "ouverts (dans n'importe quelle fenêtre) et pas encore\n"
            "terminés — double-cliquez pour basculer vers l'un d'eux.",
        )
        self.header_title_lbl = tk.Label(
            inner, text=f"♠ ♥  {APP_NAME}  ♦ ♣",
            bg=FELT_DARK, fg=GOLD, font=("Helvetica", 18, "bold"),
        )
        self.header_title_lbl.pack(side="left", padx=(24, 0), pady=(14, 12))
        self._update_window_title()

    def _update_window_title(self):
        """Met à jour le titre de la fenêtre et le bandeau avec le nom du
        tournoi en cours (appelé à l'ouverture et après chaque changement
        de paramètres). Format "Tournoi : <nom> du <date du tournoi>" —
        la date affichée est celle de CE tournoi (voir Database.
        get_tournament_date : date fixée à sa création, ou date de
        création du fichier .tournoi à défaut pour d'anciens fichiers),
        pas celle du jour où la fenêtre est ouverte/affichée. Pour un
        tournoi tout juste créé, les deux coïncident (tournament_date est
        justement fixée à aujourd'hui à ce moment-là) — seule l'ouverture
        d'un tournoi plus ancien fait une vraie différence."""
        name = self.db.get_setting("tournament_name", "Tournoi") if self.db else ""
        if self.db and name in ("", "Nouveau tournoi", "Tournoi"):
            fallback = os.path.splitext(os.path.basename(self.db.path))[0]
            if fallback:
                name = fallback
        tournament_date = format_date_fr(self.db.get_tournament_date()) if self.db else ""
        # Préfixe "{APP_NAME} v{APP_VERSION}[complément dev]" (demande du
        # 2026-09-09, voir _app_title_prefix) : identique à celui du
        # Menu principal (App.__init__), pour repérer immédiatement, EN
        # DÉVELOPPEMENT, quel commit (et si des modifications locales
        # non commitées s'y ajoutent) une fenêtre de tournoi déjà ouverte
        # fait tourner — jamais besoin de toucher APP_VERSION pour ça.
        # Sans effet en build officielle (dev_suffix() y est vide).
        app_prefix = _app_title_prefix()
        title = f"{app_prefix} — Tournoi : {name} du {tournament_date}" if name else app_prefix
        # Mode Test (demande du 2026-09-09) : préfixé bien en évidence,
        # aussi bien dans le titre de la fenêtre (barre de titre/Dock, visible
        # même onglet Paramètres fermé) que dans le bandeau interne
        # (header_title_lbl, coloré différemment pour sauter aux yeux) —
        # pour ne jamais l'oublier activé par erreur avant un vrai tournoi.
        # Reste le préfixe le PLUS à gauche (donc le plus visible même si
        # le titre est tronqué), devant même l'identification de version.
        test_mode_on = self.test_mode_var.get() if hasattr(self, "test_mode_var") else False
        if test_mode_on:
            title = f"🧪 MODE TEST — {title}"
        self.title(title)
        if hasattr(self, "header_title_lbl"):
            header_text = (
                f"♠ ♥  Tournoi : {name} du {tournament_date}  ♦ ♣" if name
                else f"♠ ♥  {APP_NAME}  ♦ ♣"
            )
            if test_mode_on:
                header_text = f"🧪 MODE TEST — {header_text}"
            self.header_title_lbl.config(
                text=header_text, fg="#ff6b4a" if test_mode_on else GOLD,
            )

    def _open_new_window(self):
        """Lance une nouvelle instance indépendante de l'application (autre
        processus, avec son propre écran d'accueil), sans toucher à celle
        déjà ouverte — pour gérer plusieurs tournois/Sit & Go à la fois,
        chacun dans sa propre fenêtre. Si ce nouveau process meurt
        immédiatement (voir spawn_app_process : stdin=subprocess.DEVNULL
        en corrige la cause la plus courante), le détecte au lieu de
        continuer vers raise_process_when_ready (qui essaierait de
        ramener au premier plan un pid déjà mort, puis abandonnerait sans
        rien dire) — affiche une vraie erreur à la place.

        TOUJOURS lancée, quelle que soit "Un seul tournoi à la fois"
        (Paramètres) : "Menu principal" n'est PAS lui-même un second
        tournoi (juste un écran d'accueil, voir _choose_tournament_file)
        — la préférence ne doit jamais empêcher de l'afficher, seulement
        ce qu'on peut y faire ensuite. C'est cet écran "Bienvenue" qui
        grise ses propres boutons "Nouveau tournoi"/"Sit & Go rapide"/
        "Ouvrir..." selon ce réglage (voir _refresh_launch_buttons_state)
        et vérifie le garde backend à leur tout début (_block_second_
        tournament_if_needed, filet de sécurité si jamais un appel
        contournait cet état grisé) — c'est LÀ, jamais ici, que la
        protection doit intervenir.

        UNICITÉ locale (self._menu_principal_proc) : n'en relance PAS un
        second tant que celui déjà lancé DEPUIS CETTE fenêtre est encore
        vivant — le ramène simplement au premier plan à la place. Sans
        cette garde, chaque clic répété créait un nouveau process
        indépendant, aussi longtemps que l'utilisateur cliquait. Pas de
        vraie modalité possible ici (grab_set/transient de Tkinter ne
        s'appliquent qu'à l'intérieur d'un même process — "Menu
        principal" en est un second, voir spawn_app_process) : la
        fenêtre tournoi reste techniquement cliquable derrière, seule
        l'unicité est garantie.

        `internal_menu_child=True` (demande du 2026-09-14) : ce nouveau
        process est un "Menu principal" supplémentaire VOLONTAIRE
        (comme le Lobby pour un tournoi précis) — voir
        POKER_TOURNAMENT_INTERNAL_LAUNCH et open_windows.
        menu_principal_pid : ce marqueur l'exempte du verrou global
        d'unicité "un seul Menu principal externe par PC" posé dans
        App.__init__, qui ne concerne QUE le raccourci Windows/macOS
        re-double-cliqué. Reste par ailleurs limité par self.
        _menu_principal_proc ci-dessus (un seul par fenêtre déjà
        ouverte) — les deux mécanismes sont indépendants, jamais
        redondants : celui-ci évite de spammer des process depuis LA
        MÊME fenêtre, l'autre (global) évite un second lancement
        EXTERNE accidentel."""
        if self._menu_principal_proc is not None and self._menu_principal_proc.poll() is None:
            open_windows.bring_pid_to_front(self._menu_principal_proc.pid)
            return
        try:
            proc = spawn_app_process(internal_menu_child=True)
        except OSError as e:
            messagebox.showerror(
                "Erreur", f"Impossible d'ouvrir une nouvelle fenêtre :\n{e}"
            )
            return

        # Laisse un court instant à un process qui planterait dès son tout
        # premier démarrage (avant même Tkinter) le temps de réellement
        # quitter, plutôt que de foncer vers raise_process_when_ready.
        time.sleep(0.3)
        returncode = proc.poll()
        if returncode is not None:
            messagebox.showerror(
                "Erreur",
                "La nouvelle fenêtre n'a pas pu s'ouvrir : le nouveau "
                f"processus s'est arrêté immédiatement (code {returncode}).",
            )
            return

        self._menu_principal_proc = proc
        raise_process_when_ready(self, proc.pid)

    def _acquire_menu_principal_lock_if_needed(self, open_path):
        """Pose (si nécessaire) le verrou d'unicité du "Menu principal"
        pour CE lancement (demande du 2026-09-14) — extrait d'App.
        __init__ dans sa propre méthode pour rester testable isolément
        (voir tests/test_menu_principal_singleton.py), même principe que
        _build_ranking_formula_widget.

        Ne s'applique QUE si `open_path` vaut None (jamais pour "chemin
        fourni mais fichier introuvable", qui retombe sur le même écran
        d'accueil par un chemin différent, déjà existant, inchangé —
        seul appelant : App.__init__) ET sans le marqueur
        POKER_TOURNAMENT_INTERNAL_LAUNCH (posé UNIQUEMENT par
        spawn_app_process(internal_menu_child=True), voir
        _open_new_window ci-dessus) : le Lobby (chemin réel) et "🏠 Menu
        principal" (marqueur présent) sont ainsi TOUJOURS exemptés,
        quel que soit l'état du verrou — jamais de vérification pour eux
        du tout.

        `_menu_principal_key()` sépare Poker Senaco de Poker Senaco
        TEST : chacun garde sa propre instance unique, sans jamais se
        bloquer l'un l'autre.

        Renvoie True si l'appelant doit continuer normalement vers
        _choose_tournament_file() (cas B — open_path fourni ou marqueur
        interne présent — ou cas A où aucune autre instance n'existait,
        verrou fraîchement posé par CE process, self.
        _holds_menu_principal_lock passé à True). Renvoie False si une
        autre instance de la MÊME ligne existe déjà : elle a été ramenée
        au premier plan (best-effort, jamais de message d'erreur) et
        self.after(destroy) déjà programmé — l'appelant ne doit alors
        RIEN faire de plus, ni construire la moindre fenêtre.

        Le verrou est posé AVANT _choose_tournament_file() et gardé
        pendant TOUTE la vie de cette fenêtre (libéré uniquement dans
        _cleanup_for_close, jamais ici) — jamais relâché dès qu'un
        tournoi est choisi : sans ça, un second double-clic sur le
        raccourci PENDANT qu'un tournoi est déjà affiché ouvrirait quand
        même un second écran Bienvenue, exactement le cas que cette
        demande doit empêcher."""
        if open_path is not None or os.environ.get(POKER_TOURNAMENT_INTERNAL_LAUNCH):
            return True
        menu_key = _menu_principal_key()
        existing_pid = open_windows.menu_principal_pid(menu_key)
        if existing_pid is not None:
            # Une autre instance du Menu principal (de la MÊME ligne
            # prod/test) tourne déjà sur ce poste : on la ramène au
            # premier plan (fonctionne qu'elle affiche encore
            # "Bienvenue" ou déjà un tournoi, bring_pid_to_front visant
            # le PID, pas une fenêtre précise) et on quitte
            # SILENCIEUSEMENT — jamais de fenêtre affichée, jamais de
            # message d'erreur. self.after (pas un appel synchrone) :
            # laisse mainloop() traiter les tentatives successives de
            # raise_process_when_ready avant de se détruire pour de bon.
            raise_process_when_ready(self, existing_pid)
            self.after(3500, self.destroy)
            return False
        open_windows.register_menu_principal(os.getpid(), menu_key)
        self._holds_menu_principal_lock = True
        return True

    def _open_lobby(self):
        LobbyDialog(self)

    # ---------------------------------------------------------------
    # Ouverture / création du fichier de tournoi
    # ---------------------------------------------------------------
    def _choose_tournament_file(self):
        win = tk.Toplevel(self)
        win.title("Bienvenue")
        win.configure(bg=FELT_DARK)
        # 480x527 (480x460 avant l'ajout de la zone "Sauvegarde des
        # données" ; 620 avec séparateur + texte explicatif sous le
        # titre ; 570 avec seulement le titre, sans séparateur/texte ;
        # le titre lui-même a été retiré le 2026-09-08 (plus que les 2
        # boutons, qui remontent naturellement) — 43px de moins, mesuré
        # via winfo_reqheight() sur les mêmes widgets/polices/paddings)
        # : sans cet ajustement, ces éléments dépasseraient hors de la
        # fenêtre (toujours non redimensionnable), ou inversement
        # laisseraient un espace vide en bas.
        win.geometry("480x527")
        win.resizable(False, False)
        # PAS de win.transient(self) ici, volontairement : à ce stade du
        # démarrage, self (la fenêtre racine) est encore self.withdraw()
        # (voir App.__init__, avant self.deiconify()) — lier une fenêtre
        # transient() à un parent pas encore affiché l'empêche elle-même de
        # s'afficher (testé : aucune erreur, mais rien à l'écran, y compris
        # sous le Dock). Même précaution déjà prise pour ActivationDialog,
        # affiché encore plus tôt dans ce même état. grab_set() seul suffit
        # à rendre la fenêtre modale ici ; les fenêtres ouvertes plus tard
        # (une fois l'appli affichée) doivent, elles, garder transient().
        win.grab_set()
        result = {"path": None}

        tk.Label(
            win, text="♠ ♥ ♦ ♣",
            bg=FELT_DARK, fg=GOLD, font=("Helvetica", 22, "bold"),
        ).pack(pady=(28, 4))
        tk.Label(
            win, text=APP_NAME,
            bg=FELT_DARK, fg=CREAM, font=("Helvetica", 16, "bold"),
        ).pack(pady=(0, 6))
        tk.Label(
            win, text="Créez un nouveau tournoi ou reprenez-en un en cours",
            bg=FELT_DARK, fg=MUTED, font=("Helvetica", 10),
        ).pack(pady=(0, 24))

        def new_tournament():
            # Filet de sécurité (voir _block_second_tournament_if_needed) :
            # ce bouton est normalement déjà grisé dans ce cas (voir
            # _refresh_launch_buttons_state), ce garde ne devrait donc
            # jamais se déclencher en usage normal.
            if _block_second_tournament_if_needed(win):
                return
            day_folder, day_filename = tournament_day_folder_proposal()
            path = filedialog.asksaveasfilename(
                title="Créer un nouveau tournoi",
                defaultextension=".tournoi",
                filetypes=[("Fichier de tournoi", "*.tournoi")],
                initialfile=day_filename or "tournoi.tournoi",
                initialdir=day_folder or default_tournament_dir(),
            )
            if not path:
                return
            export_prefs.save_value("last_tournament_dir", os.path.dirname(os.path.abspath(path)))
            if os.path.exists(path):
                # La fenêtre "Save" de macOS a déjà demandé une confirmation
                # générique ("remplacer ce fichier ?"), qui ne dit pas que ça
                # efface tout le tournoi existant — on le précise noir sur
                # blanc ici, avec "Ouvrir un tournoi existant" en rappel,
                # avant de repartir d'une base complètement vierge (joueurs,
                # tables, mouvements, blindes, jetons...).
                if not messagebox.askyesno(
                    "Remplacer ce tournoi ?",
                    f"« {os.path.basename(path)} » existe déjà et contient un "
                    "tournoi.\n\nContinuer va TOUT effacer (joueurs, blindes, "
                    "jetons, gains...) et repartir d'une base entièrement "
                    "vierge sous ce même nom.\n\n"
                    "Pour reprendre ce tournoi tel quel, annulez et utilisez "
                    "plutôt « 📂 Ouvrir un tournoi existant » depuis l'écran "
                    "d'accueil.\n\nEffacer et repartir de zéro ?",
                    icon="warning", default="no",
                ):
                    return
                try:
                    os.remove(path)
                except OSError as e:
                    messagebox.showerror(
                        "Erreur", f"Impossible de remplacer ce fichier :\n{e}"
                    )
                    return
            selector = PlayerSelectionDialog(
                win, title="Joueurs participants",
                confirm_text="Créer le tournoi", cancel_text="Créer sans joueurs",
                conflict_folder=os.path.dirname(os.path.abspath(path)),
                conflict_exclude_path=path,
                conflict_date=time.strftime("%Y-%m-%d"),
            )
            win.wait_window(selector)
            result["path"] = path
            result["is_new"] = True
            result["selected_players"] = selector.selected_names
            win.destroy()

        def new_sng():
            # Voir le commentaire équivalent dans new_tournament() ci-dessus.
            if _block_second_tournament_if_needed(win):
                return
            day_folder, day_filename = tournament_day_folder_proposal(is_sng=True)
            path = filedialog.asksaveasfilename(
                title="Créer un nouveau Sit & Go",
                defaultextension=".tournoi",
                filetypes=[("Fichier de tournoi", "*.tournoi")],
                initialfile=day_filename or "sitngo.tournoi",
                initialdir=day_folder or default_tournament_dir(),
            )
            if not path:
                return
            export_prefs.save_value("last_tournament_dir", os.path.dirname(os.path.abspath(path)))
            if os.path.exists(path):
                # Voir le commentaire équivalent dans new_tournament() ci-dessus.
                if not messagebox.askyesno(
                    "Remplacer ce tournoi ?",
                    f"« {os.path.basename(path)} » existe déjà et contient un "
                    "tournoi.\n\nContinuer va TOUT effacer (joueurs, blindes, "
                    "jetons, gains...) et repartir d'une base entièrement "
                    "vierge sous ce même nom.\n\n"
                    "Pour reprendre ce tournoi tel quel, annulez et utilisez "
                    "plutôt « 📂 Ouvrir un tournoi existant » depuis l'écran "
                    "d'accueil.\n\nEffacer et repartir de zéro ?",
                    icon="warning", default="no",
                ):
                    return
                try:
                    os.remove(path)
                except OSError as e:
                    messagebox.showerror(
                        "Erreur", f"Impossible de remplacer ce fichier :\n{e}"
                    )
                    return
            selector = PlayerSelectionDialog(
                win, title="Joueurs participants",
                confirm_text="Créer le Sit & Go", cancel_text="Créer sans joueurs",
                conflict_folder=os.path.dirname(os.path.abspath(path)),
                conflict_exclude_path=path,
                conflict_date=time.strftime("%Y-%m-%d"),
            )
            win.wait_window(selector)
            result["path"] = path
            result["is_new"] = True
            result["is_sng"] = True
            result["selected_players"] = selector.selected_names
            win.destroy()

        def open_tournament():
            # Voir le commentaire équivalent dans new_tournament() ci-dessus.
            if _block_second_tournament_if_needed(win):
                return
            day_folder = (
                tournament_prefs.load_last_settings().get("tournament_day_folder", "") or ""
            ).strip()
            path = filedialog.askopenfilename(
                title="Ouvrir un tournoi existant",
                filetypes=[("Fichier de tournoi", "*.tournoi"), ("Tous les fichiers", "*.*")],
                initialdir=day_folder if day_folder and os.path.isdir(day_folder) else None,
            )
            if path:
                result["path"] = path
                result["is_new"] = False
                win.destroy()

        def backup_now():
            """"💾 Sauvegarder sur clé USB" : copie les .tournoi du
            dossier de tournoi par défaut + tout ~/.poker_tournament
            vers un sous-dossier horodaté du dossier choisi (voir
            backup_restore.create_backup — jamais de modification des
            fichiers d'origine, uniquement des lectures/copies).
            Disponible même si un tournoi est ouvert ailleurs : ne fait
            que LIRE ses fichiers, jamais les modifier."""
            dest = filedialog.askdirectory(
                title="Choisir la clé USB ou le dossier de destination", parent=win,
            )
            if not dest:
                return
            try:
                result_info = backup_restore.create_backup(dest)
            except backup_restore.BackupError as e:
                messagebox.showerror("Erreur de sauvegarde", str(e), parent=win)
                return
            data_note = (
                "Données de l'application (réglages, joueurs, photos...) incluses."
                if result_info["poker_tournament_data_included"]
                else "Aucune donnée d'application (~/.poker_tournament) trouvée à sauvegarder."
            )
            messagebox.showinfo(
                "Sauvegarde terminée avec succès",
                "Sauvegarde terminée avec succès.\n\n"
                f"Emplacement : {result_info['backup_dir']}\n"
                f"Tournois sauvegardés : {result_info['tournament_file_count']}\n"
                f"{data_note}",
                parent=win,
            )

        def restore_now():
            """"♻️ Restaurer depuis une clé USB" : voir backup_restore.
            restore_backup — refuse d'abord si un tournoi est ouvert
            (open_windows.list_open_paths), affiche le contenu du
            manifeste et exige une confirmation explicite AVANT toute
            écriture, puis crée automatiquement une sauvegarde de
            sécurité de l'état actuel avant d'écraser quoi que ce soit."""
            src = filedialog.askdirectory(
                title="Sélectionner un dossier de sauvegarde PokerTournament_Backup_...",
                parent=win,
            )
            if not src:
                return
            if open_windows.list_open_paths():
                messagebox.showerror(
                    "Tournoi(s) actuellement ouvert(s)",
                    "Au moins un tournoi est actuellement ouvert (ici ou dans "
                    "une autre fenêtre).\n\nFermez tous les tournois ouverts "
                    "avant de restaurer une sauvegarde.",
                    parent=win,
                )
                return
            try:
                manifest = backup_restore.validate_backup_folder(src)
            except backup_restore.BackupError as e:
                messagebox.showerror("Sauvegarde invalide", str(e), parent=win)
                return
            count = len(manifest.get("tournament_files", []))
            has_data = manifest.get("poker_tournament_data_included", False)
            if not messagebox.askyesno(
                "Confirmer la restauration",
                f"Sauvegarde du {manifest.get('created_at', '?')}\n"
                f"Version : {manifest.get('app_version', '?')}\n"
                f"Ordinateur d'origine : {manifest.get('computer_name', '?')}\n"
                f"Tournois : {count}\n"
                "Données de l'application (réglages, joueurs, photos...) : "
                f"{'oui' if has_data else 'non'}\n\n"
                "Une sauvegarde de sécurité des données actuelles sera créée "
                "automatiquement avant toute modification.\n\n"
                "Confirmer la restauration ?",
                icon="warning", default="no", parent=win,
            ):
                return
            try:
                restore_info = backup_restore.restore_backup(src)
            except backup_restore.BackupError as e:
                messagebox.showerror("Erreur de restauration", str(e), parent=win)
                return
            messagebox.showinfo(
                "Restauration terminée",
                "Restauration terminée avec succès.\n\n"
                f"Tournois restaurés : {restore_info['restored_tournament_files']}\n"
                f"Données de l'application restaurées : "
                f"{'oui' if restore_info['restored_data'] else 'non'}\n\n"
                "Sauvegarde de sécurité de l'état précédent :\n"
                f"{restore_info['safety_backup_dir']}",
                parent=win,
            )

        btn_frame = tk.Frame(win, bg=FELT_DARK)
        btn_frame.pack(pady=4)
        new_tournament_btn = ttk.Button(
            btn_frame, text="🆕  Nouveau tournoi", command=new_tournament, width=28,
        )
        new_tournament_btn.pack(pady=6)
        sng_btn = ttk.Button(
            btn_frame, text="🚀  Sit & Go rapide", command=new_sng, width=28,
        )
        sng_btn.pack(pady=6)
        Tooltip(
            sng_btn,
            "Comme \"Nouveau tournoi\", mais préremplit tout de suite une\n"
            "structure de blindes rapide (10 min/niveau, antes dès le\n"
            "niveau 3) et une grille de gains standard selon le nombre de\n"
            "joueurs choisis ci-après — à ajuster ensuite si besoin dans\n"
            "Paramètres/Gains, comme pour n'importe quel tournoi normal.",
        )
        open_tournament_btn = ttk.Button(
            btn_frame, text="📂  Ouvrir un tournoi existant", command=open_tournament, width=28,
        )
        open_tournament_btn.pack(pady=6)
        lobby_btn = ttk.Button(
            btn_frame, text="📋  Lobby (plusieurs tournois)",
            command=lambda: LobbyDialog(win), width=28,
        )
        lobby_btn.pack(pady=6)
        Tooltip(
            lobby_btn,
            "Vue d'ensemble de tous les tournois/Sit & Go actuellement\n"
            "ouverts (dans n'importe quelle fenêtre) et pas encore\n"
            "terminés : joueurs actifs, niveau, temps restant, en un\n"
            "coup d'œil — double-cliquez pour basculer vers l'un d'eux.\n"
            "N'ouvre ni ne ferme celle-ci.",
        )

        # -- Sauvegarde des données (clé USB) : jamais liée à "Un seul
        # tournoi à la fois" (aucun rapport avec le lancement d'un
        # tournoi) — toujours active, jamais grisée. Voir backup_now/
        # restore_now ci-dessus et backup_restore.py pour la logique.
        # Packée dans btn_frame (PAS win) : l'ordre d'affichage suit
        # l'ordre d'empilement des ENFANTS de btn_frame, indépendant de
        # quand btn_frame lui-même a été empilé dans win — la packer
        # dans win la ferait apparaître après TOUT btn_frame (donc après
        # "À propos"), pas entre "Lobby" et les boutons ci-dessous.
        # Ni séparateur, ni texte explicatif, ni titre (demandes du
        # 2026-09-08) : uniquement les 2 boutons, avec leurs explications
        # dans les tooltips ci-dessous — aucun espace réservé à la place
        # d'un titre, les boutons remontent juste après le bouton Lobby.
        backup_btn = ttk.Button(
            btn_frame, text="💾  Sauvegarder sur clé USB", command=backup_now, width=28,
        )
        backup_btn.pack(pady=6)
        Tooltip(
            backup_btn,
            "Permet de sauvegarder les tournois, joueurs, photos et\n"
            "réglages sur une clé USB.",
        )
        restore_btn = ttk.Button(
            btn_frame, text="♻️  Restaurer depuis une clé USB", command=restore_now, width=28,
        )
        restore_btn.pack(pady=6)
        Tooltip(
            restore_btn,
            "Permet de restaurer les tournois, joueurs, photos et\n"
            "réglages depuis une clé USB.",
        )

        ttk.Button(
            btn_frame, text="ℹ️  À propos", command=lambda: self._show_about(win), width=28,
        ).pack(pady=6)

        # État initial correct dès le premier affichage (pas seulement au
        # bout d'une seconde) : voir _refresh_launch_buttons_state, qui
        # se reprogramme ensuite elle-même tant que `win` reste affichée.
        _refresh_launch_buttons_state(win, [new_tournament_btn, sng_btn, open_tournament_btn])

        self.wait_window(win)
        if not result["path"]:
            return False
        # Réservation ATOMIQUE inter-processus AVANT toute ouverture réelle
        # (demande du 2026-09-16, "interdire l'ouverture simultanée du
        # même .tournoi") — voir open_windows.try_register. Ici, action
        # DÉLIBÉRÉE de l'utilisateur ("Ouvrir un tournoi existant" vient
        # justement de choisir CE fichier précis) : contrairement à la
        # branche `open_path` d'App.__init__ (lancement interne, personne
        # devant l'écran), un message explicite est justifié en plus de
        # ramener l'autre fenêtre au premier plan.
        existing_pid = open_windows.try_register(result["path"])
        if existing_pid is not None:
            # ORDRE VOLONTAIRE (demande du 2026-09-16, diagnostic du focus
            # qui repart ~2s après) : la messagebox D'ABORD, bring_pid_to_
            # front ENSUITE, en tout dernier — jamais l'inverse. À ce
            # stade, `self` (la racine de CE processus, qui vient
            # d'échouer) est encore withdraw()ée (jamais deiconify()ée,
            # voir plus bas) : la SEULE fenêtre réellement visible de ce
            # processus est cette messagebox elle-même. L'appeler AVANT
            # bring_pid_to_front garantit qu'elle a fini de capter le
            # focus (et d'être refermée par l'utilisateur) AVANT même que
            # le tournoi déjà ouvert ne soit activé — jamais la messagebox
            # de CE processus mourant ne peut plus revenir devant lui
            # après coup. Il ne reste alors plus qu'à sortir vite (return
            # False -> _cleanup_for_close()+self.destroy() dans l'appelant) :
            # une fois ce processus réellement terminé, raise_process_
            # when_ready (lancé par la fenêtre d'origine du "🏠 Menu
            # principal" ayant créé CE processus, voir _open_new_window)
            # cesse déjà de lui-même de le ramener au premier plan dès
            # qu'il constate ce PID mort (voir sa docstring) — rien à
            # changer de ce côté, seulement ne plus lui laisser une longue
            # fenêtre "vivant avec sa messagebox affichée" pendant laquelle
            # il pourrait être reramené au premier plan À LA PLACE du
            # tournoi déjà ouvert.
            messagebox.showinfo(
                "Tournoi déjà ouvert",
                "Ce tournoi est déjà ouvert dans une autre fenêtre — elle "
                "va être ramenée au premier plan.",
            )
            open_windows.bring_pid_to_front(existing_pid)
            return False
        # Le dossier cible peut ne pas exister (dossier tout juste créé via
        # le sélecteur, ou choisi par erreur) : on le crée si besoin plutôt
        # que de laisser sqlite3 échouer plus loin, et on attrape toute
        # erreur d'ouverture pour afficher un message clair au lieu de
        # faire planter toute l'application avec une trace Python brute.
        try:
            target_dir = os.path.dirname(os.path.abspath(result["path"]))
            if target_dir:
                os.makedirs(target_dir, exist_ok=True)
            self.db = Database(result["path"])
        except Exception as e:
            # Réservation prise mais ouverture réelle échouée : la libérer
            # immédiatement (voir la même remarque dans la branche
            # `open_path` ci-dessus).
            open_windows.unregister(result["path"])
            messagebox.showerror(
                "Erreur",
                f"Impossible de créer/ouvrir le fichier de tournoi :\n"
                f"{result['path']}\n\n{e}",
            )
            return False
        if result.get("is_new"):
            # Interrupteur général "Calculer les primes" (demande du
            # 2026-09-09) : un tournoi flambant neuf part de la valeur
            # GLOBALE proposée pour la session en cours (voir
            # _primes_enabled_proposed), immédiatement — pas seulement
            # au prochain tick (voir _sync_primes_enabled_pref, qui
            # prendra ensuite le relais tant que ce tournoi n'a pas
            # démarré, garantissant qu'il ne puisse jamais rester sur une
            # valeur devenue périmée entre-temps). Ne fait jamais partie
            # de `last_settings` ci-dessous (tournament_prefs ne mémorise
            # que self.settings_vars, qui n'inclut volontairement pas
            # "primes_enabled" — ce réglage suit une règle de session,
            # pas la règle habituelle "dernier tournoi utilisé").
            self.db.set_setting(
                "primes_enabled", "1" if _primes_enabled_proposed() else "0"
            )
            last_settings = tournament_prefs.load_last_settings()
            if last_settings:
                self.db.set_settings(last_settings)
                # Une "Table 1" a déjà été créée par le constructeur avec
                # l'ancien nombre de sièges par défaut (9) avant qu'on
                # applique les préférences mémorisées ci-dessus : on la
                # met donc à jour rétroactivement.
                try:
                    new_max_seats = int(last_settings.get("max_seats_per_table", ""))
                except ValueError:
                    new_max_seats = None
                if new_max_seats and new_max_seats >= 2:
                    self.db.set_all_tables_max_seats(new_max_seats)
            guessed_name = os.path.splitext(os.path.basename(result["path"]))[0]
            if guessed_name:
                self.db.set_settings({"tournament_name": guessed_name})
            self.db.set_settings({"tournament_date": time.strftime("%Y-%m-%d")})
            # Système de points distribués (demande du 2026-09-10, étendu
            # le 2026-09-18 : "reprendre automatiquement les paramètres
            # Primes précédemment utilisés") : hérite du dernier choix
            # EXPLICITE mémorisé dans last_settings s'il existe (voir
            # tournament_prefs.PERSISTED_KEYS et le filtre "" appliqué à
            # l'écriture dans _collect_and_save_all_settings — un
            # placeholder légataire, "" jamais mémorisé, ne peut donc
            # jamais apparaître ici). Sinon (premier lancement, ou dernier
            # tournoi encore sur le placeholder légataire) : comportement
            # sûr inchangé, stampé "none" ("Aucun") explicitement — PAS
            # via DEFAULT_SETTINGS/_init_defaults (voir sa docstring),
            # justement pour qu'un tournoi flambant neuf soit TOUJOURS
            # distinguable sans ambiguïté d'un ancien fichier antérieur à
            # cette fonctionnalité (qui, lui, n'aura jamais cette clé) —
            # voir Database.resolve_ranking_formula, volontairement NON
            # modifiée par cette demande : l'interprétation des anciens
            # fichiers .tournoi reste strictement inchangée.
            inherited_formula = last_settings.get("ranking_formula")
            if inherited_formula not in RANKING_FORMULA_LABELS:
                self.db.set_settings({"ranking_formula": RANKING_FORMULA_NONE})
        self._update_window_title()
        if result.get("is_new") and result.get("selected_players"):
            for name in self._filter_active_conflicts(result["selected_players"]):
                self.db.add_player(name, roster.get_club(name))
        if result.get("is_sng"):
            self._apply_sng_defaults(n_players=len(result.get("selected_players") or []))
        return True

    def _apply_sng_defaults(self, n_players):
        """Préremplit un tournoi flambant neuf avec des réglages adaptés à
        un Sit & Go (structure de blindes rapide, grille de gains standard
        selon le nombre de joueurs déjà choisis) — appelé uniquement par
        le bouton "Sit & Go rapide" du menu d'accueil. Le tournoi reste un
        tournoi normal en tout point ensuite (mêmes onglets, mêmes
        réglages modifiables) : rien n'est verrouillé ni spécifique."""
        structure = generate_blind_structure(
            start_small_blind=25, start_big_blind=50, ante_start_level=3,
            start_ante=25, duration_minutes=10, break_duration_minutes=10,
            break_every=6,
        )
        self.db.set_blind_structure(structure)
        self.db.set_settings({
            "round_duration_minutes": 10, "ante_start_level": 3,
            "break_duration_minutes": 10,
            # Mémorisé pour de bon (contrairement à result["is_sng"], qui
            # n'existe que le temps de la création) : sert par exemple au
            # titre des exports Classement et Primes (voir
            # _compute_tournament_export_title).
            "is_sng": 1,
        })
        if n_players > 0:
            self.db.set_payout_structure(standard_payout_structure(n_players))

    def _cleanup_for_close(self):
        """Nettoyage commun avant de fermer ce tournoi dans ce processus,
        que ce soit pour de bon (_on_close), pour revenir au menu
        principal et en ouvrir un autre dans la même fenêtre
        (_new_tournament), ou en annulant l'écran "Bienvenue" avant
        même d'avoir choisi un tournoi (voir App.__init__) : arrête le
        contrôle à distance, désinscrit ce tournoi du registre des
        fenêtres ouvertes (voir open_windows.py — sinon il resterait
        signalé "ouvert ici" alors que ce n'est plus vrai), ferme la
        base, et libère le verrou d'unicité du Menu principal (demande
        du 2026-09-14) SI ce process le détient encore.

        self._holds_menu_principal_lock : posé UNIQUEMENT par
        App.__init__ pour un lancement externe (open_path=None, sans le
        marqueur POKER_TOURNAMENT_INTERNAL_LAUNCH) — jamais pour un
        tournoi ouvert avec un chemin (Lobby) ni pour un "Menu
        principal" supplémentaire volontaire (bouton 🏠, voir
        _open_new_window/spawn_app_process(internal_menu_child=True)),
        qui n'ont donc jamais rien à libérer ici (le `getattr` par
        défaut à False couvre aussi un appel avant que __init__ n'ait
        atteint cette étape). Le verrou est gardé pendant TOUTE la vie
        de cette fenêtre, pas seulement le temps de l'écran Bienvenue :
        c'est précisément ce qui empêche un second lancement externe
        pendant qu'un tournoi est déjà affiché — voir App.__init__."""
        self._stop_remote_control()
        self._cancel_tables_blink()
        if self.db:
            open_windows.unregister(self.db.path)
            self.db.close()
        if getattr(self, "_holds_menu_principal_lock", False):
            open_windows.unregister_menu_principal(os.getpid(), _menu_principal_key())
            self._holds_menu_principal_lock = False

    def _on_close(self):
        self._cleanup_for_close()
        self.destroy()

    # ---------------------------------------------------------------
    # Menu
    # ---------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        # Voir le commentaire équivalent dans _build_header : mêmes deux
        # entrées, mêmes nouveaux libellés/rôles — la troisième
        # ("Lobby (plusieurs tournois)...") a disparu, son rôle étant
        # repris par la première.
        filemenu.add_command(label="🏠 Menu principal (nouvelle fenêtre)...", command=self._open_new_window)
        filemenu.add_command(label="📋 Lobby...", command=self._open_lobby)
        filemenu.add_separator()
        filemenu.add_command(label="Nouveau tournoi...", command=self._new_tournament)
        filemenu.add_command(label="Ouvrir...", command=self._open_tournament)
        filemenu.add_separator()
        filemenu.add_command(label="Exporter les résultats (Excel/CSV)...", command=self._export_results)
        filemenu.add_separator()
        filemenu.add_command(label="Quitter", command=self._on_close)
        menubar.add_cascade(label="Fichier", menu=filemenu)

        viewmenu = tk.Menu(menubar, tearoff=0)
        viewmenu.add_command(label="Ouvrir l'écran chronomètre", command=self._open_clock_window)
        menubar.add_cascade(label="Affichage", menu=viewmenu)

        rostermenu = tk.Menu(menubar, tearoff=0)
        rostermenu.add_command(label="Gérer le répertoire de joueurs...", command=self._manage_roster)
        menubar.add_cascade(label="Répertoire", menu=rostermenu)

        statsmenu = tk.Menu(menubar, tearoff=0)
        statsmenu.add_command(label="Synthèse par période...", command=self._open_period_summary)
        menubar.add_cascade(label="Statistiques", menu=statsmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="Manuel (F1)...", command=self._show_context_help)
        helpmenu.add_command(label="À propos...", command=self._show_about)
        menubar.add_cascade(label="Aide", menu=helpmenu)

        self.config(menu=menubar)

        # bind_all (et non bind) : capte F1 depuis n'importe quel widget de
        # l'application, y compris les fenêtres séparées (Toplevel) comme
        # l'écran projecteur — une fenêtre qui a besoin d'un chapitre
        # différent (Lobby, Répertoire, Synthèse par période...) redéfinit
        # sa propre touche F1 directement sur elle-même, ce qui prend le
        # pas sur ce binding global tant qu'elle a le focus.
        self.bind_all("<F1>", lambda e: self._show_context_help())

    def _show_context_help(self):
        """Ouvre l'aide (menu Aide, ou touche F1) directement sur le
        chapitre du manuel correspondant à l'endroit où se trouve
        l'utilisateur dans le logiciel : l'écran chronomètre projecteur
        s'il a le focus, sinon l'onglet actuellement affiché (voir
        TAB_TO_CHAPTER) — sur la première page du manuel si aucune
        correspondance n'est trouvée."""
        focused = self.focus_get()
        if (
            self.clock_window is not None
            and self.clock_window.winfo_exists()
            and focused is not None
            and focused.winfo_toplevel() is self.clock_window
        ):
            HelpBrowser.open_at(self, chapter_title="9. Onglet Chronomètre", section_title="Écran projecteur")
            return
        try:
            current_tab = self.notebook.tab(self.notebook.select(), "text")
        except tk.TclError:
            current_tab = None
        chapter = TAB_TO_CHAPTER.get(current_tab)
        HelpBrowser.open_at(self, chapter_title=chapter)

    def _show_about(self, parent=None):
        # "[TEST]"/"[TEST N]" (demandes du 2026-09-12 et du 2026-09-14,
        # voir _test_build_label) : visible dès la première ligne de "À
        # propos", en plus du titre de fenêtre (_app_title_prefix) — pour
        # qu'une fenêtre de la version de TEST ne puisse jamais être
        # confondue avec la v1.2.38 installée à côté, même une fois "À
        # propos" ouvert en plein écran sans le reste de la fenêtre
        # visible ; le numéro (quand disponible) distingue en plus
        # immédiatement plusieurs builds TEST entre eux (ex. "[TEST 3]").
        label = _test_build_label()
        name_line = f"{label} {APP_NAME}" if label else APP_NAME
        lines = [
            name_line,
            f"Version {APP_VERSION}",
            "",
            "Développé par Sena Raj Juganaikloo, membre de Chemillé Poker Club",
        ]
        info = licensing.license_info()
        if info is not None:
            lines += ["", f"Club activé : {info['club_name']}", f"Machine : {info['machine_id']}"]
        messagebox.showinfo("À propos", "\n".join(lines), parent=parent or self)

    def _open_period_summary(self):
        # Bascule vers l'onglet "Statistiques" (voir App._build_tabs) —
        # avant cette réorganisation, ouvrait une fenêtre séparée
        # (PeriodSummaryDialog). Le menu reste, en simple raccourci.
        self.notebook.select(self.stats_tab)

    def _manage_roster(self):
        # Bascule vers l'onglet "Répertoire" (voir App._build_tabs) —
        # avant cette réorganisation, ouvrait une fenêtre séparée
        # (RosterManagerDialog). Le menu reste, en simple raccourci.
        self.notebook.select(self.roster_tab)

    def _new_tournament(self):
        self._cleanup_for_close()
        self.destroy()
        app = App()
        app.mainloop()

    def _open_tournament(self):
        self._new_tournament()

    def _export_results(self):
        if not self.db:
            return
        ResultsExportDialog(self, self.db)

    def _export_classement(self):
        if not self.db:
            return
        ClassementExportDialog(self, self.db)

    def _export_players(self):
        if not self.db:
            return
        PlayersExportDialog(self, self.db, sort_state=self.players_sort)

    # ---------------------------------------------------------------
    # Construction des onglets
    # ---------------------------------------------------------------
    def _build_tabs(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True)

        self.players_tab = ttk.Frame(self.notebook)
        self.tables_tab = ttk.Frame(self.notebook)
        self.moves_tab = ttk.Frame(self.notebook)
        self.bounty_tab = ttk.Frame(self.notebook)
        self.clock_tab = ttk.Frame(self.notebook)
        self.blinds_tab = ttk.Frame(self.notebook)
        self.payouts_tab = ttk.Frame(self.notebook)
        # "Répertoire" et "Statistiques" : anciennement des fenêtres à
        # part ouvertes depuis les menus Répertoire/Statistiques
        # (RosterManagerDialog/PeriodSummaryDialog, voir leurs
        # docstrings) — construisent tout leur contenu dans __init__,
        # contrairement aux autres onglets ci-dessus (pas de
        # _build_xxx_tab séparée à appeler juste en dessous).
        self.roster_tab = RosterManagerDialog(self.notebook, self)
        self.stats_tab = PeriodSummaryDialog(self.notebook, self)
        self.settings_tab = ttk.Frame(self.notebook)
        # "CA" (chantier "séparation CA/LOG en CA + LOG", 2026-09-24 —
        # s'appelait "CA/LOG" et hébergeait aussi les critères LOG tant
        # que les deux partageaient le même onglet, voir l'historique du
        # 2026-09-22) : emplacement UNIQUE du bloc Contrôle à distance/
        # Téléphones/Permissions DIRTO, retiré de Paramètres (voir
        # _build_ca_tab) — présentation/positions/logique STRICTEMENT
        # inchangées, seuls les critères de recherche LOG en sont
        # retirés (déplacés vers leur propre onglet "LOG" ci-dessous).
        self.ca_tab = ttk.Frame(self.notebook)
        # "LOG" (nouveau, même chantier du 2026-09-24) : reçoit les
        # critères de recherche préparatoires déjà existants (voir
        # _build_log_tab/_build_log_search_criteria_placeholder,
        # RÉUTILISÉE telle quelle, jamais dupliquée) — aucune logique
        # fonctionnelle pour l'instant (Phase 2 LOG, PAS commencée ici).
        self.log_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.players_tab, text="Joueurs")
        self.notebook.add(self.tables_tab, text="Tables")
        self.notebook.add(self.moves_tab, text="Mouvements")
        self.notebook.add(self.bounty_tab, text="Primes")
        self.notebook.add(self.blinds_tab, text="Blindes")
        self.notebook.add(self.clock_tab, text="Chronomètre")
        self.notebook.add(self.payouts_tab, text="Classement")
        self.notebook.add(self.roster_tab, text="Répertoire")
        self.notebook.add(self.stats_tab, text="Statistiques")
        # Ordre demandé le 2026-09-22 (CA/LOG immédiatement après
        # Statistiques, avant Paramètres), affiné le 2026-09-24 avec la
        # séparation en 2 onglets distincts, dans le MÊME emplacement :
        # Statistiques -> CA -> LOG -> Paramètres — uniquement l'ordre
        # d'ajout au Notebook, aucun changement de contenu/logique.
        self.notebook.add(self.ca_tab, text="CA")
        self.notebook.add(self.log_tab, text="LOG")
        self.notebook.add(self.settings_tab, text="Paramètres")

        self._build_players_tab()
        self._build_tables_tab()
        self._build_moves_tab()
        self._build_bounty_tab()
        self._build_clock_tab()
        self._build_blinds_tab()
        self._build_payouts_tab()
        self._build_ca_tab()
        self._build_log_tab()
        self._build_settings_tab()

        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

    # ---------------------------------------------------------------
    # Onglet Joueurs
    # ---------------------------------------------------------------
    CHECKBOX_UNCHECKED = "\u2610"  # ☐
    CHECKBOX_CHECKED = "\u2611"    # ☑

    # Touches à ignorer dans l'auto-complétion du champ "Nom du joueur" :
    # navigation dans le menu déroulant et touches de modification, qui ne
    # doivent pas déclencher un recalcul des suggestions.
    _AUTOCOMPLETE_IGNORED_KEYS = {
        "Up", "Down", "Return", "Escape", "Tab", "ISO_Left_Tab",
        "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
        "Caps_Lock", "Meta_L", "Meta_R", "Super_L", "Super_R",
    }

    def _build_players_tab(self):
        # ids des joueurs actuellement cochés (cases à cocher, pour les
        # actions groupées) — indépendant de la sélection classique du
        # Treeview.
        self.checked_player_ids = set()
        self.players_sort = {"column": None, "ascending": True}

        top = ttk.Frame(self.players_tab)
        top.pack(fill="x", padx=10, pady=10)

        ttk.Label(top, text="Nom du joueur :").pack(side="left")
        self.new_player_var = tk.StringVar()
        self.new_player_entry = ttk.Entry(top, textvariable=self.new_player_var, width=24)
        self.new_player_entry.pack(side="left", padx=5)
        self.new_player_entry.bind("<Return>", lambda e: self._add_player())
        self.new_player_entry.bind("<Escape>", lambda e: self._hide_autocomplete())
        # Un seul gestionnaire pour FocusOut : pré-remplit le club, puis
        # referme (avec un léger délai) le menu déroulant d'auto-complétion
        # — le délai laisse le temps à un clic sur une suggestion de bien
        # être traité avant que la liste ne disparaisse.
        self.new_player_entry.bind("<FocusOut>", self._on_player_name_focus_out)
        # Auto-complétion : à chaque lettre tapée, propose dans un menu
        # déroulant (maison, sous forme de petite fenêtre) les joueurs du
        # répertoire dont le nom commence par ce qui a été saisi ; cliquer
        # un nom l'inscrit directement au tournoi, sans passer par le
        # bouton "Ajouter".
        self.new_player_entry.bind("<KeyRelease>", self._on_player_name_keyrelease)
        self._autocomplete_popup = None
        self._autocomplete_listbox = None

        ttk.Label(top, text="Club :").pack(side="left", padx=(8, 0))
        self.new_player_club_var = tk.StringVar()
        self.new_player_club_combo = ttk.Combobox(
            top, textvariable=self.new_player_club_var, width=16,
            values=roster.list_clubs(),
        )
        self.new_player_club_combo.pack(side="left", padx=5)
        self.new_player_club_combo.bind("<Return>", lambda e: self._add_player())

        ttk.Button(top, text="Ajouter", command=self._add_player).pack(side="left", padx=5)
        ttk.Button(top, text="Ajouter depuis le répertoire...", command=self._add_from_roster).pack(side="left", padx=5)

        self.temp_player_var = tk.BooleanVar(value=False)
        temp_check = ttk.Checkbutton(
            top, text="Temp (ne pas ajouter au répertoire)", variable=self.temp_player_var,
        )
        temp_check.pack(side="left", padx=(10, 5))
        Tooltip(
            temp_check,
            "Coché : ce joueur est inscrit à ce tournoi uniquement, sans\n"
            "être enregistré dans le répertoire de joueurs habituels\n"
            "(utile pour un invité ponctuel).",
        )

        # Sur sa propre ligne, sous la case "Temp" (plutôt que serré à
        # droite de la barre du haut, déjà chargée) : plus lisible,
        # surtout sur une fenêtre pas très large.
        stats_row = ttk.Frame(self.players_tab)
        stats_row.pack(fill="x", padx=10, pady=(0, 6))
        self.stats_lbl = ttk.Label(stats_row, text="", font=("Helvetica", 10, "bold"))
        self.stats_lbl.pack(side="left")

        check_bar = ttk.Frame(self.players_tab)
        check_bar.pack(fill="x", padx=10)
        ttk.Button(check_bar, text="Tout cocher", command=self._check_all_players).pack(side="left", padx=3)
        ttk.Button(check_bar, text="Tout décocher", command=self._uncheck_all_players).pack(side="left", padx=3)
        self.eliminate_player_btn = ttk.Button(
            check_bar, text="Éliminer", command=self._eliminate_selected, style="Danger.TButton",
        )
        self.eliminate_player_btn.pack(side="left", padx=3)
        # Grisé TANT QUE le tournoi n'a pas démarré — voir
        # _update_tournament_started_buttons, appelée à chaque
        # rafraîchissement (comportement inverse de "Supprimer" ci-dessous :
        # rien à éliminer avant que la partie ait commencé). Texte de
        # l'info-bulle basculé entre les deux ci-dessous plutôt que
        # d'ajouter un second Tooltip sur le même bouton.
        self._eliminate_btn_tooltip_normal_text = (
            "Élimine tous les joueurs cochés ci-dessus. Pour un seul\n"
            "joueur, demande qui l'a éliminé (calcule bounty et prime\n"
            "de classement) ; pour plusieurs à la fois, aucun éliminateur\n"
            "n'est demandé."
        )
        self.eliminate_player_btn_tooltip = Tooltip(
            self.eliminate_player_btn, self._eliminate_btn_tooltip_normal_text,
        )
        ttk.Button(
            check_bar, text="Exporter les joueurs (Excel/CSV)...", command=self._export_players,
        ).pack(side="left", padx=3)
        columns_btn = ttk.Button(check_bar, text="Bou/Col...", command=self._manage_player_columns)
        columns_btn.pack(side="left", padx=3)
        Tooltip(
            columns_btn,
            "Choisir quelles colonnes du tableau, et quels boutons\n"
            "(Rebuy, Add-on, Modifier chips, Modifier achats) afficher.",
        )
        self.checked_count_lbl = ttk.Label(check_bar, text="", foreground=GOLD)
        self.checked_count_lbl.pack(side="left", padx=10)

        actions = ttk.Frame(self.players_tab)
        actions.pack(fill="x", padx=10, pady=(6, 10))
        ttk.Button(actions, text="Renommer...", command=self._rename_selected).pack(side="left", padx=3)

        # Boutons réductibles à la souris (comme les colonnes du tableau) :
        # glisser la poignée dorée à droite d'un bouton le rétrécit ; le
        # relâcher en dessous d'un seuil le masque complètement. "Bou/Col..."
        # (ci-dessus) les réaffiche, seul moyen de les récupérer une fois
        # masqués. Voir _add_shrinkable_button.
        self._PLAYER_BUTTON_MIN_WIDTH = 20  # px : à/en dessous, le bouton se masque
        self._player_button_restore_widths = {
            "rebuy": 110, "addon": 110, "edit_chips": 150, "edit_purchases": 165,
        }
        visible_buttons_saved = export_prefs.load_columns(
            "players_tab_buttons_visible", list(self._player_button_restore_widths)
        )
        self.hidden_player_buttons = {
            k for k in self._player_button_restore_widths if k not in visible_buttons_saved
        }
        self._player_button_slots = {}

        self._add_shrinkable_button(
            actions, "rebuy", "Rebuy (+)", self._rebuy_selected,
            "Recave : remet le joueur en jeu avec le nombre de\njetons réglé dans Paramètres, incrémente son compteur de rebuys.",
        )
        self._add_shrinkable_button(
            actions, "addon", "Add-on (+)", self._addon_selected,
            "Recharge (add-on) : ajoute des jetons au joueur (réglage\nParamètres), incrémente son compteur d'add-ons.",
        )
        self._add_shrinkable_button(actions, "edit_chips", "Modifier chips...", self._edit_chips_selected)
        self._add_shrinkable_button(actions, "edit_purchases", "Modifier achats...", self._edit_purchases_selected)

        withdraw_btn = ttk.Button(actions, text="Désactiver (forfait)", command=self._withdraw_selected)
        # Repère pour réinsérer un bouton réduit à sa bonne place (avant les
        # boutons suivants) quand "Bou/Col..." le réaffiche.
        self._player_button_slots_anchor = withdraw_btn
        withdraw_btn.pack(side="left", padx=3)
        Tooltip(
            withdraw_btn,
            "Retire le joueur du tournoi sans lui attribuer de rang\n"
            "(contrairement à Éliminer) : à utiliser pour un forfait/départ\n"
            "volontaire plutôt qu'une élimination au jeu.",
        )
        reinstate_btn = ttk.Button(actions, text="Réinscrire", command=self._reinstate_selected)
        reinstate_btn.pack(side="left", padx=3)
        Tooltip(reinstate_btn, "Remet en jeu un joueur désactivé (forfait) ou éliminé par erreur.")
        self.delete_player_btn = ttk.Button(actions, text="Supprimer", command=self._delete_selected)
        self.delete_player_btn.pack(side="left", padx=3)
        # Grisé une fois le tournoi commencé (chronomètre déjà démarré au
        # moins une fois, voir clock_started) — état et texte de l'info-
        # bulle tenus à jour par _update_tournament_started_buttons,
        # appelée à chaque rafraîchissement de cet onglet : supprimer un joueur en
        # cours de partie fausserait l'historique (mouvements, élimination
        # par...) sans qu'on puisse revenir en arrière, mieux vaut
        # Désactiver (forfait), qui garde une trace. Se réactive de
        # lui-même pour un nouveau tournoi (clock_started y repart à 0).
        self.delete_player_btn_tooltip = Tooltip(self.delete_player_btn, "")

        # "Annule Eliminer" (demande du 2026-09-17) — sert à corriger
        # rapidement une erreur d'élimination faite depuis le téléphone :
        # annule TOUJOURS la toute dernière élimination, quelle que soit
        # la sélection/le cochage courant dans ce tableau (voir
        # App._undo_last_elimination, fonction métier centrale UNIQUE
        # partagée avec le clic droit sur le dernier joueur éliminé —
        # voir _on_players_tree_right_click). Grisé s'il n'y a
        # actuellement aucun joueur éliminé (voir _update_undo_
        # elimination_button_state) — reste néanmoins possible que le
        # clic soit refusé si l'état a changé depuis (voir Database.
        # undo_last_elimination), auquel cas un message clair l'explique,
        # sans jamais rien modifier.
        self.undo_elimination_btn = ttk.Button(
            actions, text="Annule Eliminer", command=self._undo_last_elimination,
            style="Undo.TButton",
        )
        self.undo_elimination_btn.pack(side="left", padx=3)
        Tooltip(self.undo_elimination_btn, "Annule la dernière élimination")

        columns = ("sel", "id", "name", "club", "table", "seat", "chips", "buyin", "rebuy", "addon", "bounty", "status", "rang",
                   "elim_time", "elim_round", "eliminated_by")
        headers = ["", "ID", "Nom", "Club", "Table", "Siège", "Chips", "Buy-in", "Rebuys", "Add-ons", "Prime", "Statut", "Rang",
                   "Éliminé le", "Round", "Éliminé par"]
        self.players_columns = columns
        self.players_headers = headers
        # Colonnes qu'on a réduites à presque rien (voir
        # _collapse_tiny_player_columns) et qui sont donc masquées ; "sel"
        # et "name" restent toujours affichées. Mémorisé entre deux
        # lancements de l'appli (indépendamment de chaque tournoi, comme
        # les préférences d'export).
        visible_saved = export_prefs.load_columns("players_tab_visible", list(columns))
        self.hidden_player_columns = {
            c for c in columns if c not in visible_saved and c not in ("sel", "name")
        }
        # Conteneur + ascenseur vertical : un tournoi Open peut avoir
        # beaucoup plus de joueurs que ce qui tient à l'écran (contrairement
        # à un Sit & Go), sans ça les derniers de la liste n'étaient
        # atteignables qu'à la molette, sans repère de position.
        players_tree_frame = ttk.Frame(self.players_tab)
        players_tree_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.players_tree = ttk.Treeview(
            players_tree_frame, columns=columns, show="tree headings", height=20,
            style="Players.Treeview",
        )
        self.players_tree.heading("#0", text="Photo")
        self.players_tree.column("#0", width=PLAYER_THUMB_SIZE + 16, stretch=False, anchor="center")
        for c, h in zip(columns, headers):
            self.players_tree.heading(c, text=h)
            # stretch=False : sans ça, ttk réétire automatiquement les
            # colonnes pour combler l'espace disponible dès que la
            # fenêtre se redessine, ce qui annulait silencieusement tout
            # rétrécissement manuel à la souris avant même que le
            # masquage automatique (_collapse_tiny_player_columns) ait pu
            # s'en apercevoir.
            self.players_tree.column(c, width=90, anchor="center", stretch=False)
        # Colonne des cases à cocher ("sel") : tri par en-tête réservé au
        # Mode Test (demande du 2026-09-14) — voir _on_players_checkbox_
        # header_click, qui filtre lui-même sur _test_mode_enabled() ;
        # la commande reste toujours attachée (Mode Test peut être
        # coché/décoché en cours de session sans reconstruire cet
        # onglet), elle ne fait simplement rien hors Mode Test.
        self.players_tree.heading("sel", command=self._on_players_checkbox_header_click)
        self.players_tree.heading("name", command=lambda: self._sort_players_by("name"))
        self.players_tree.heading("club", command=lambda: self._sort_players_by("club"))
        self.players_tree.heading("status", command=lambda: self._sort_players_by("status"))
        self.players_tree.heading("table", command=lambda: self._sort_players_by("table"))
        # "Rang" : classement final du joueur (100 pour le 1er éliminé d'un
        # champ de 100, 99 pour le 2e, etc. — voir _sort_players_by), triable.
        self.players_tree.heading("rang", command=lambda: self._sort_players_by("rang"))
        self.players_tree.heading("elim_time", command=lambda: self._sort_players_by("elim_time"))
        self.players_tree.heading("eliminated_by", command=lambda: self._sort_players_by("eliminated_by"))
        self.players_tree.column("sel", width=56, anchor="center", stretch=False)
        self.players_tree.column("name", width=180, anchor="w")
        self.players_tree.column("club", width=110, anchor="w")
        self.players_tree.column("elim_time", width=130, anchor="center")
        TreeHeadingTooltip(self.players_tree, {
            "sel": "Cocher pour inclure ce joueur dans les actions groupées\n(Éliminer, etc.).",
            "rang": "Place finale du joueur : 1 = vainqueur, un chiffre plus élevé\n= éliminé plus tôt. Vide tant que le joueur est encore en jeu.",
            "bounty": "Prime (bounty) actuellement portée par ce joueur, en points\n(mécanisme interne PKO — voir Paramètres > Primes) —\nà ne pas confondre avec le tableau de l'onglet Primes.",
            "buyin": "Nombre de buy-ins (entrées) de ce joueur dans ce tournoi.",
            "rebuy": "Nombre de recaves (rebuys).",
            "addon": "Nombre de recharges (add-ons).",
            "elim_time": "Date et heure d'élimination de ce joueur, triable.",
            "elim_round": "Round de la structure de blindes (onglet Blindes) où ce\njoueur a été éliminé.",
            "eliminated_by": "Nom du joueur qui l'a éliminé, triable.",
        })
        players_scrollbar = ttk.Scrollbar(
            players_tree_frame, orient="vertical", command=self.players_tree.yview
        )
        self.players_tree.configure(yscrollcommand=players_scrollbar.set)
        self.players_tree.pack(side="left", fill="both", expand=True)
        players_scrollbar.pack(side="right", fill="y")
        # Molette de souris, en complément de l'ascenseur (même formule que
        # payouts_tree/moves_tree, cohérent d'un onglet à l'autre).
        self.players_tree.bind(
            "<MouseWheel>",
            lambda e: self.players_tree.yview_scroll(int(-e.delta / 120 * 3) or (-3 if e.delta > 0 else 3), "units"),
        )
        self.players_tree.bind("<Button-1>", self._on_players_tree_click)
        # Clic droit sur une ligne : raccourci "Éliminer" sans confirmation
        # (demande du 2026-09-16) — même principe que LobbyDialog.
        # _on_tree_right_click (Button-3 la plupart des plateformes,
        # Button-2 sur Mac avec certains trackpads/souris — les deux liés
        # par précaution).
        self.players_tree.bind("<Button-3>", self._on_players_tree_right_click)
        self.players_tree.bind("<Button-2>", self._on_players_tree_right_click)
        # Après tout relâchement de clic dans l'en-tête (typiquement la fin
        # d'un redimensionnement de colonne à la souris), masque
        # automatiquement les colonnes réduites à presque rien plutôt que
        # de laisser un filet de quelques pixels affiché pour rien.
        self.players_tree.bind("<ButtonRelease-1>", self._on_players_header_release, add="+")
        # Applique dès l'ouverture les colonnes masquées mémorisées d'une
        # session précédente (voir visible_saved ci-dessus).
        self._apply_players_displaycolumns()
        self.player_photo_images = {}  # {player_id: PhotoImage} — évite le garbage collect

    # -- Gestion des cases à cocher --------------------------------
    def _on_players_tree_click(self, event):
        region = self.players_tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.players_tree.identify_column(event.x)
        row_iid = self.players_tree.identify_row(event.y)
        if not row_iid or col != "#1":  # "#1" = première colonne affichée ("sel")
            return
        pid = int(row_iid)
        if pid in self.checked_player_ids:
            self.checked_player_ids.discard(pid)
        else:
            self.checked_player_ids.add(pid)
        self._apply_checkbox_display(row_iid)
        self._update_checked_count_label()
        return "break"  # évite que le clic ne change aussi la sélection classique

    def _on_players_tree_right_click(self, event):
        """Clic droit sur une ligne du tableau Joueurs — deux
        comportements distincts selon le statut du joueur visé (demande
        du 2026-09-16, complétée le 2026-09-17) :

        - joueur ACTIF : raccourci d'élimination rapide qui ouvre
          DIRECTEMENT "Qui a éliminé ce joueur ?" (_ask_eliminator), sans
          le moindre menu ni la confirmation "Voulez-vous éliminer ce
          joueur ?" habituelle. Sélectionne d'abord la ligne réellement
          sous le curseur (même principe que LobbyDialog._on_tree_right_
          click), puis appelle _eliminate_selected avec :
          - skip_confirmation=True : saute la boîte "Confirmer" ;
          - ids=[pid] : agit TOUJOURS sur CE joueur précis, jamais sur une
            case cochée ou une sélection multiple précédente — un clic
            droit sur B après un clic sur A agit bien sur B ;
          - force_mandatory_eliminator=True : "Qui a éliminé ce joueur ?"
            devient elle-même le garde-fou (bouton "Annuler l'élimination"
            plutôt que "Ignorer (pas de prime)") — tant qu'elle n'est pas
            validée par "Valider", AUCUNE élimination n'est enregistrée
            (fermer la fenêtre ou cliquer "Annuler l'élimination"
            produisent tous deux eliminator_id=None, voir _ask_eliminator,
            qui abandonne alors l'élimination — déjà le mécanisme
            existant, jamais dupliqué ici).
        - joueur ÉLIMINÉ, qu'il s'agit bien du DERNIER éliminé (demande
          du 2026-09-17, voir Database.get_last_eliminated_player) ET que
          le timeout ("Timeout pour Annuler Eliminer", Paramètres,
          ajouté le 2026-09-17) n'est pas dépassé (voir Database.undo_
          last_elimination_available) : propose son annulation via
          _undo_last_elimination (même fonction métier centrale que le
          bouton "Annule Eliminer", jamais dupliquée — confirmation
          obligatoire incluse).
        - joueur éliminé plus ANCIEN que le dernier, OU délai dépassé (OU
          "Timeout pour Annuler Eliminer" réglé à 0, désactivation
          complète) : RÈGLE ABSOLUE demandée par l'utilisateur, aucune
          action.
        - forfait (withdrawn), zone vide (aucune ligne sous le curseur),
          ou en-tête : aucune action, inchangé."""
        row_iid = self.players_tree.identify_row(event.y)
        if not row_iid:
            return
        self.players_tree.selection_set(row_iid)
        pid = int(row_iid)
        player = self.db.get_player(pid)
        if player is None:
            return
        if player["status"] == "active":
            self._eliminate_selected(skip_confirmation=True, ids=[pid], force_mandatory_eliminator=True)
            return
        if player["status"] == "eliminated":
            last = self.db.get_last_eliminated_player()
            # undo_last_elimination_available() couvre à la fois "c'est
            # bien le dernier éliminé" (implicitement, via son propre
            # get_last_eliminated_player()) ET le timeout (demande du
            # 2026-09-17) — mais on vérifie explicitement `last["id"] ==
            # pid` en plus : cette ligne précise doit être celle du
            # dernier éliminé, jamais une autre même si, par impossible,
            # la disponibilité globale était vraie pour un autre id.
            if (last is not None and last["id"] == pid
                    and self.db.undo_last_elimination_available()):
                self._undo_last_elimination()
            # Sinon (éliminé plus ancien, ou délai dépassé) : aucune
            # action, RÈGLE ABSOLUE.

    def _apply_checkbox_display(self, row_iid):
        pid = int(row_iid)
        mark = self.CHECKBOX_CHECKED if pid in self.checked_player_ids else self.CHECKBOX_UNCHECKED
        self.players_tree.set(row_iid, "sel", mark)

    def _update_checked_count_label(self):
        n = len(self.checked_player_ids)
        self.checked_count_lbl.config(text=f"{n} joueur(s) coché(s)" if n else "")

    def _sort_players_by(self, column):
        """Tri par clic sur un en-tête (Nom / Statut) : ré-appuyer sur le
        même en-tête inverse l'ordre (croissant <-> décroissant). Utilisé
        aussi pour la colonne "sel" (voir _on_players_checkbox_header_
        click) : `ascending` y prend le sens "cochés d'abord" (True) /
        "décochés d'abord" (False) plutôt que croissant/décroissant, mais
        le même bascule à chaque clic répété fonctionne à l'identique."""
        if self.players_sort["column"] == column:
            self.players_sort["ascending"] = not self.players_sort["ascending"]
        else:
            self.players_sort["column"] = column
            self.players_sort["ascending"] = True
        self._refresh_players_tab()

    def _on_players_checkbox_header_click(self):
        """Tri par l'en-tête de la colonne des cases à cocher (demande du
        2026-09-14) — réservé au Mode Test (voir _test_mode_enabled) :
        premier clic -> joueurs COCHÉS en haut (triés par nom, insensible
        à la casse), décochés en dessous (triés par nom) ; second clic ->
        l'inverse (décochés en haut) ; clics suivants, alternance — voir
        _sort_players_by et le tri effectif dans _refresh_players_tab
        (sort_col == "sel").

        Ne modifie JAMAIS self.checked_player_ids : uniquement l'ORDRE
        d'affichage (comme les tris existants Nom/Club/... de cet
        onglet), aucune écriture en base pour ce tri.

        Hors Mode Test, ce clic ne fait rien — la commande reste
        attachée à l'en-tête en permanence (voir _build_players_tab) car
        Mode Test peut être coché/décoché en cours de session sans que
        cet onglet soit reconstruit ; c'est cette méthode qui vérifie
        l'état actuel à chaque clic, jamais un état figé à la
        construction de l'onglet."""
        if not self._test_mode_enabled():
            return
        self._sort_players_by("sel")

    def _update_sort_headings(self):
        base_headers = {
            "name": "Nom", "club": "Club", "status": "Statut", "table": "Table", "rang": "Rang",
            "elim_time": "Éliminé le", "eliminated_by": "Éliminé par",
        }
        for col, label in base_headers.items():
            if self.players_sort["column"] == col:
                arrow = " ▲" if self.players_sort["ascending"] else " ▼"
                self.players_tree.heading(col, text=label + arrow)
            else:
                self.players_tree.heading(col, text=label)
        # Colonne "sel" (Mode Test, voir _on_players_checkbox_header_
        # click) : reprend les glyphes ☑/☐ déjà utilisés pour les cases
        # elles-mêmes plutôt qu'un texte, l'en-tête étant trop étroit
        # pour un libellé ; vide (comme avant cette fonction) dans tous
        # les autres cas, y compris hors Mode Test.
        if self.players_sort["column"] == "sel":
            checked_first = self.players_sort["ascending"]
            self.players_tree.heading(
                "sel", text=self.CHECKBOX_CHECKED if checked_first else self.CHECKBOX_UNCHECKED,
            )
        else:
            self.players_tree.heading("sel", text="")

    # -- Colonnes affichées (masquage auto au redimensionnement minimal) --
    # ttk impose une largeur minimale de colonne de 20px par défaut :
    # impossible de descendre en dessous en glissant la bordure à la
    # souris. Le seuil est donc fixé à 20 (et non plus bas), sans quoi le
    # masquage automatique ne se déclencherait jamais.
    _PLAYER_COLUMN_MIN_WIDTH = 20  # px : à/en dessous, on masque la colonne
    _PLAYER_COLUMN_RESTORE_WIDTH = 90  # largeur redonnée quand on la réaffiche

    def _on_players_header_release(self, event):
        # Laisse ttk terminer d'appliquer le redimensionnement avant de
        # relire les largeurs, sinon on peut lire une valeur pas encore à
        # jour au tout premier relâchement du clic.
        self.after(1, self._collapse_tiny_player_columns)

    def _collapse_tiny_player_columns(self):
        if not self.players_tree.winfo_exists():
            return
        changed = False
        for c in self.players_columns:
            if c in ("sel", "name") or c in self.hidden_player_columns:
                continue
            try:
                width = self.players_tree.column(c, "width")
            except tk.TclError:
                continue
            if width <= self._PLAYER_COLUMN_MIN_WIDTH:
                self.hidden_player_columns.add(c)
                changed = True
        if changed:
            self._apply_players_displaycolumns()

    def _apply_players_displaycolumns(self):
        visible = [c for c in self.players_columns if c not in self.hidden_player_columns]
        self.players_tree["displaycolumns"] = tuple(visible)
        # Mémorise ce choix pour le reprendre au prochain lancement de
        # l'appli (indépendamment du tournoi ouvert).
        export_prefs.save_columns("players_tab_visible", visible)

    def _add_shrinkable_button(self, parent, key, text, command, tooltip=None):
        """Bouton d'action (Rebuy, Add-on, Modifier chips/achats) placé dans
        un cadre de largeur fixe avec une poignée dorée sur son bord droit :
        la glisser réduit le bouton, comme une colonne du tableau ; le
        relâcher sous _PLAYER_BUTTON_MIN_WIDTH le masque complètement
        (seul "Bou/Col..." le réaffiche — voir _manage_player_columns)."""
        restore_w = self._player_button_restore_widths.get(key, 130)
        slot = tk.Frame(parent, width=restore_w, height=30, bg=FELT)
        slot.pack_propagate(False)
        slot.pack(side="left", padx=3)

        btn = ttk.Button(slot, text=text, command=command)
        btn.pack(fill="both", expand=True)
        if tooltip:
            Tooltip(btn, tooltip)

        grip = tk.Frame(slot, width=5, bg=GOLD_DARK, cursor="sb_h_double_arrow")
        grip.place(relx=1.0, rely=0, relheight=1.0, anchor="ne")
        Tooltip(
            grip,
            "Glisser pour réduire ce bouton (jusqu'à le masquer) —\n"
            "\"Bou/Col...\" le réaffiche ensuite.",
        )

        self._player_button_slots[key] = slot
        drag = {"start_x": 0, "start_w": restore_w}

        def on_press(event):
            drag["start_x"] = event.x_root
            drag["start_w"] = slot.winfo_width()

        def on_motion(event):
            new_w = max(2, drag["start_w"] + (event.x_root - drag["start_x"]))
            slot.configure(width=new_w)

        def on_release(event):
            if slot.winfo_width() <= self._PLAYER_BUTTON_MIN_WIDTH:
                slot.pack_forget()
                self.hidden_player_buttons.add(key)
                self._save_hidden_player_buttons()

        grip.bind("<ButtonPress-1>", on_press)
        grip.bind("<B1-Motion>", on_motion)
        grip.bind("<ButtonRelease-1>", on_release)

        if key in self.hidden_player_buttons:
            slot.pack_forget()
        return slot

    def _save_hidden_player_buttons(self):
        visible = [
            k for k in self._player_button_restore_widths if k not in self.hidden_player_buttons
        ]
        export_prefs.save_columns("players_tab_buttons_visible", visible)

    def _manage_player_columns(self):
        """Fenêtre pour réafficher (ou masquer manuellement) les colonnes
        et les boutons réductibles de l'onglet Joueurs — seul moyen de les
        récupérer une fois réduits à rien, puisqu'ils n'ont alors plus de
        bordure/poignée à ressaisir."""
        win = tk.Toplevel(self)
        win.title("Boutons et colonnes affichés")
        win.configure(bg=FELT_DARK)
        win.transient(self)
        win.grab_set()

        button_labels = {
            "rebuy": "Rebuy (+)", "addon": "Add-on (+)",
            "edit_chips": "Modifier chips...", "edit_purchases": "Modifier achats...",
        }
        btn_vars = {
            k: tk.BooleanVar(value=k not in self.hidden_player_buttons) for k in button_labels
        }
        tk.Label(
            win, text="Boutons affichés :",
            bg=FELT_DARK, fg=CREAM, font=("Helvetica", 10, "bold"),
        ).pack(padx=16, pady=(16, 8), anchor="w")
        for k, label in button_labels.items():
            ttk.Checkbutton(win, text=label, variable=btn_vars[k]).pack(
                anchor="w", padx=16, pady=2
            )

        tk.Label(
            win, text="Colonnes affichées dans le tableau :",
            bg=FELT_DARK, fg=CREAM, font=("Helvetica", 10, "bold"),
        ).pack(padx=16, pady=(16, 8), anchor="w")

        headers_by_key = dict(zip(self.players_columns, self.players_headers))
        hideable = [c for c in self.players_columns if c not in ("sel", "name")]
        col_vars = {c: tk.BooleanVar(value=c not in self.hidden_player_columns) for c in hideable}
        for c in hideable:
            ttk.Checkbutton(win, text=headers_by_key.get(c, c), variable=col_vars[c]).pack(
                anchor="w", padx=16, pady=2
            )

        def apply_and_close():
            for k, var in btn_vars.items():
                if var.get():
                    self.hidden_player_buttons.discard(k)
                    slot = self._player_button_slots.get(k)
                    if slot is not None and not slot.winfo_ismapped():
                        slot.configure(width=self._player_button_restore_widths.get(k, 130))
                        slot.pack(side="left", padx=3, before=self._player_button_slots_anchor)
                else:
                    self.hidden_player_buttons.add(k)
                    slot = self._player_button_slots.get(k)
                    if slot is not None:
                        slot.pack_forget()
            self._save_hidden_player_buttons()

            for c, var in col_vars.items():
                if var.get():
                    self.hidden_player_columns.discard(c)
                    if self.players_tree.column(c, "width") <= self._PLAYER_COLUMN_MIN_WIDTH:
                        self.players_tree.column(c, width=self._PLAYER_COLUMN_RESTORE_WIDTH)
                else:
                    self.hidden_player_columns.add(c)
            self._apply_players_displaycolumns()
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(8, 16))
        ttk.Button(btns, text="Fermer", command=apply_and_close).pack()

    def _check_all_players(self):
        for row_iid in self.players_tree.get_children():
            self.checked_player_ids.add(int(row_iid))
            self._apply_checkbox_display(row_iid)
        self._update_checked_count_label()

    def _uncheck_all_players(self):
        self.checked_player_ids.clear()
        for row_iid in self.players_tree.get_children():
            self._apply_checkbox_display(row_iid)
        self._update_checked_count_label()

    def _checked_or_selected_ids(self):
        """IDs à utiliser pour une action : les joueurs cochés en priorité,
        sinon la sélection classique du tableau (clic/ctrl/shift-clic)."""
        if self.checked_player_ids:
            return [pid for pid in self.checked_player_ids
                    if self.players_tree.exists(str(pid))]
        return [int(s) for s in self.players_tree.selection()]

    def _clear_checked(self):
        self.checked_player_ids.clear()

    def _on_player_name_keyrelease(self, event):
        """Recalcule, à chaque lettre tapée, les suggestions (joueurs du
        répertoire dont le nom commence par le texte saisi, déjà inscrits
        au tournoi exclus) et affiche/masque le menu déroulant en
        conséquence."""
        if event.keysym in self._AUTOCOMPLETE_IGNORED_KEYS:
            return
        text = self.new_player_var.get().strip()
        if not text:
            self._hide_autocomplete()
            return
        already_in_tournament = {p["name"] for p in self.db.list_players()}
        matches = sorted(
            (n for n in roster.load_roster()
             if n.lower().startswith(text.lower()) and n not in already_in_tournament),
            key=str.lower,
        )
        if matches:
            self._show_autocomplete(matches)
        else:
            self._hide_autocomplete()

    def _show_autocomplete(self, matches):
        """Affiche (en la créant si besoin) une petite liste cliquable
        juste sous le champ Nom du joueur, avec les suggestions."""
        if self._autocomplete_popup is None or not self._autocomplete_popup.winfo_exists():
            popup = tk.Toplevel(self)
            popup.withdraw()
            popup.overrideredirect(True)
            # 'transient' associe explicitement la fenêtre à la fenêtre
            # principale : sans ça, sous macOS, l'appli entière peut perdre
            # le focus après un clic sur cette petite fenêtre sans style
            # (overrideredirect) et devenir insensible aux clics jusqu'à ce
            # qu'on la réactive manuellement (bascule vers une autre appli
            # puis retour, par ex.) — c'est ce "gel" apparent des champs
            # Nom/Club/Ajouter qui a été signalé.
            popup.transient(self)
            try:
                popup.attributes("-topmost", True)
            except tk.TclError:
                pass
            listbox = tk.Listbox(
                popup, bg=CREAM, fg=TEXT_DARK, selectbackground=GOLD,
                selectforeground=TEXT_DARK, font=("Helvetica", 11),
                exportselection=False, activestyle="none",
                highlightthickness=1, highlightbackground=GOLD_DARK, borderwidth=0,
                takefocus=0,
            )
            listbox.pack(fill="both", expand=True)
            # ButtonRelease (et non Button-1) : la sélection du Listbox est
            # déjà à jour au relâchement du clic, ce qui évite de lire
            # l'ancienne sélection.
            listbox.bind("<ButtonRelease-1>", self._on_autocomplete_click)
            self._autocomplete_popup = popup
            self._autocomplete_listbox = listbox

        listbox = self._autocomplete_listbox
        listbox.delete(0, "end")
        for name in matches:
            listbox.insert("end", name)
        height = min(6, len(matches))
        listbox.configure(height=height)

        entry = self.new_player_entry
        x = entry.winfo_rootx()
        y = entry.winfo_rooty() + entry.winfo_height()
        width = max(entry.winfo_width(), 160)
        self._autocomplete_popup.geometry(f"{width}x{height * 20}+{x}+{y}")
        self._autocomplete_popup.deiconify()
        self._autocomplete_popup.lift()

    def _hide_autocomplete(self):
        if self._autocomplete_popup is not None and self._autocomplete_popup.winfo_exists():
            self._autocomplete_popup.withdraw()
            # Le correctif ci-dessous (lift + focus_force sur la fenêtre
            # racine) sert seulement à débloquer l'appli si elle est
            # restée "collée" sans AUCUN widget focalisé — ce qui peut
            # arriver sous macOS après un clic sur cette fenêtre sans
            # style (overrideredirect), voir _show_autocomplete, sinon
            # l'appli entière reste insensible aux clics. Ne s'applique
            # QUE dans ce cas précis (focus_get() vaut None) : sinon, il
            # volait le focus clavier au champ Nom du joueur en cours de
            # saisie à CHAQUE lettre tapée ne donnant aucune suggestion
            # (ex : un nom tout nouveau, absent du répertoire) — signalé
            # comme une perte de focus continue pendant la frappe.
            if self.focus_get() is None:
                self.lift()
                try:
                    self.focus_force()
                except tk.TclError:
                    pass

    def _on_player_name_focus_out(self, event=None):
        self._prefill_club_from_roster()
        # Léger délai : laisse le temps au clic sur une suggestion
        # (<ButtonRelease-1> sur la liste) d'être traité avant de la
        # masquer — sinon FocusOut la ferme avant que le clic ne compte.
        self.after(150, self._hide_autocomplete)

    def _on_autocomplete_click(self, event):
        if self._autocomplete_listbox is None:
            return
        sel = self._autocomplete_listbox.curselection()
        if not sel:
            return
        name = self._autocomplete_listbox.get(sel[0])
        self._hide_autocomplete()
        self.new_player_var.set(name)
        self._prefill_club_from_roster()
        self._add_player()

    def _prefill_club_from_roster(self):
        name = self.new_player_var.get().strip()
        if not name or self.new_player_club_var.get().strip():
            return  # ne pas écraser une saisie de club déjà en cours
        club = roster.get_club(name)
        if club:
            self.new_player_club_var.set(club)

    def _warn_active_conflict(self, name):
        """Si `name` est déjà actif dans un autre tournoi .tournoi du même
        dossier (ex : un autre Sit & Go en cours), prévient et demande
        confirmation avant de l'ajouter quand même. Renvoie True s'il faut
        continuer l'ajout (pas de conflit, ou confirmé malgré tout).
        Ne vérifie rien du tout si "Éviter qu'un joueur joue à deux tables
        à la fois" est décoché dans Paramètres (voir _build_settings_tab)."""
        if not export_prefs.load_value("check_multi_table_conflict", True):
            return True
        conflict = self.db.find_active_conflict(name)
        if not conflict:
            return True
        other_name = os.path.splitext(os.path.basename(conflict))[0]
        return messagebox.askyesno(
            "Joueur déjà en jeu ailleurs",
            f"{name} est actuellement actif dans un autre tournoi du même "
            f"dossier : « {other_name} ».\n\n"
            "L'ajouter quand même à celui-ci ?",
        )

    def _add_player(self):
        name = self.new_player_var.get().strip()
        if not name:
            return
        if not self._warn_active_conflict(name):
            return
        club = self.new_player_club_var.get().strip()
        self.db.add_player(name, club)
        if not self.temp_player_var.get():
            roster.add_to_roster(name, club=club or None)
            # Rafraîchit la liste de clubs proposée dans le menu déroulant
            # si un club inédit vient d'être saisi.
            self.new_player_club_combo.configure(values=roster.list_clubs())
        self.new_player_var.set("")
        self.new_player_club_var.set("")
        self._hide_autocomplete()
        self._refresh_all()
        # Prêt à saisir le joueur suivant.
        self.new_player_entry.focus_set()

    def _add_from_roster(self):
        existing_names = {p["name"] for p in self.db.list_players()}
        dialog = PlayerSelectionDialog(
            self, title="Ajouter des joueurs depuis le répertoire",
            confirm_text="Ajouter les joueurs sélectionnés", cancel_text="Annuler",
            exclude_names=existing_names,
            conflict_folder=os.path.dirname(os.path.abspath(self.db.path)),
            conflict_exclude_path=self.db.path,
            conflict_date=self.db.get_tournament_date(),
        )
        self.wait_window(dialog)
        to_add = self._filter_active_conflicts(dialog.selected_names)
        for name in to_add:
            self.db.add_player(name, roster.get_club(name))
        if to_add:
            self._refresh_all()

    def _filter_active_conflicts(self, names):
        """Pour une liste de noms à ajouter en une fois : sépare ceux déjà
        actifs dans un autre tournoi du même dossier, prévient en un seul
        message groupé et demande confirmation pour eux uniquement. Renvoie
        la liste finale des noms à ajouter (sans conflit + confirmés).
        Ne vérifie rien du tout si "Éviter qu'un joueur joue à deux tables
        à la fois" est décoché dans Paramètres (voir _build_settings_tab)."""
        if not export_prefs.load_value("check_multi_table_conflict", True):
            return list(names)
        no_conflict, conflicts = [], []
        for name in names:
            other = self.db.find_active_conflict(name)
            if other:
                conflicts.append((name, os.path.splitext(os.path.basename(other))[0]))
            else:
                no_conflict.append(name)
        if conflicts:
            lines = "\n".join(f"- {n} (actif dans « {t} »)" for n, t in conflicts)
            if messagebox.askyesno(
                "Joueurs déjà en jeu ailleurs",
                f"{len(conflicts)} joueur(s) sont actuellement actifs dans un "
                f"autre tournoi du même dossier :\n\n{lines}\n\n"
                "Les ajouter quand même ?",
            ):
                no_conflict.extend(n for n, _ in conflicts)
        return no_conflict

    def _selected_player_id(self, action_label="cette action"):
        """Retourne l'ID d'un unique joueur ciblé (case cochée ou ligne
        sélectionnée). Affiche un avertissement si plusieurs joueurs sont
        cochés, car ces actions ne s'appliquent qu'à un seul joueur à la
        fois."""
        ids = self._checked_or_selected_ids()
        if not ids:
            return None
        if len(ids) > 1:
            messagebox.showinfo(
                "Sélection multiple",
                f"Veuillez ne cocher/sélectionner qu'un seul joueur pour {action_label}.",
            )
            return None
        return ids[0]

    def _rebuy_selected(self):
        ids = self._checked_or_selected_ids()
        if not ids:
            return
        for pid in ids:
            self.db.rebuy_player(pid)
        self._clear_checked()
        self._refresh_all()

    def _addon_selected(self):
        ids = self._checked_or_selected_ids()
        if not ids:
            return
        for pid in ids:
            self.db.addon_player(pid)
        self._clear_checked()
        self._refresh_all()

    def _rename_selected(self):
        pid = self._selected_player_id("renommer")
        if not pid:
            return
        p = self.db.get_player(pid)
        new_name = simpledialog.askstring(
            "Renommer le joueur", "Nouveau nom :", initialvalue=p["name"]
        )
        if new_name and new_name.strip():
            self.db.rename_player(pid, new_name.strip())
            roster.rename_in_roster(p["name"], new_name.strip())
            self._refresh_all()

    def _edit_purchases_selected(self):
        pid = self._selected_player_id("modifier les achats")
        if not pid:
            return
        p = self.db.get_player(pid)

        win = tk.Toplevel(self)
        win.title(f"Modifier les achats — {p['name']}")
        win.transient(self)
        win.grab_set()

        vars_ = {}
        fields = [
            ("buyin_count", "Nombre de buy-ins"),
            ("rebuy_count", "Nombre de rebuys"),
            ("addon_count", "Nombre d'add-ons"),
        ]
        for i, (key, label) in enumerate(fields):
            ttk.Label(win, text=label + " :").grid(row=i, column=0, sticky="w", padx=10, pady=6)
            var = tk.IntVar(value=p[key])
            ttk.Spinbox(win, from_=0, to=999, textvariable=var, width=8).grid(
                row=i, column=1, padx=10, pady=6
            )
            vars_[key] = var

        def save():
            self.db.set_purchase_counts(
                pid, vars_["buyin_count"].get(), vars_["rebuy_count"].get(), vars_["addon_count"].get()
            )
            win.destroy()
            self._refresh_all()

        btns = ttk.Frame(win)
        btns.grid(row=len(fields), column=0, columnspan=2, pady=10)
        ttk.Button(btns, text="Enregistrer", command=save).pack(side="left", padx=5)
        ttk.Button(btns, text="Annuler", command=win.destroy).pack(side="left", padx=5)

    def _edit_chips_selected(self):
        pid = self._selected_player_id("modifier les chips")
        if not pid:
            return
        p = self.db.get_player(pid)
        val = simpledialog.askinteger(
            "Modifier les chips", f"Nouveau montant de chips pour {p['name']} :",
            initialvalue=p["chips"], minvalue=0,
        )
        if val is not None:
            self.db.set_chips(pid, val)
            self._refresh_all()

    def _eliminate_selected(self, skip_confirmation=False, ids=None,
                             force_mandatory_eliminator=False):
        """`ids` : liste explicite de joueurs à éliminer, prioritaire sur
        les cases cochées/la sélection du tableau (voir _checked_or_
        selected_ids) — utilisé UNIQUEMENT par le raccourci clic droit
        (_on_players_tree_right_click), qui vise sciemment CE joueur
        précis, quels que soient les cases cochées ou la sélection en
        cours au moment du clic. None (par défaut, bouton rouge
        "Éliminer" existant) : comportement inchangé, _checked_or_
        selected_ids() comme avant.

        `skip_confirmation=True` (raccourci clic droit UNIQUEMENT, et
        seulement pour une élimination d'UN SEUL joueur) : saute la boîte
        de dialogue "Confirmer" ci-dessous — un raccourci volontairement
        plus rapide, qui rejoint ensuite exactement le même mécanisme
        métier que le bouton rouge (enregistrement, classement, Primes/
        PKO, rééquilibrage, mouvements, bandeau, contrôle à distance :
        rien de tout ça n'est dupliqué ni contourné). Ignoré (jamais
        appliqué) pour une élimination groupée : le bouton rouge reste
        alors la seule voie, avec sa confirmation habituelle.

        `force_mandatory_eliminator=True` (raccourci clic droit
        UNIQUEMENT) : force `mandatory=True` pour l'appel à _ask_eliminator
        ci-dessous, quel que soit le contexte de bounty — la fenêtre "Qui
        a éliminé ce joueur ?" devient alors ELLE-MÊME le garde-fou
        remplaçant la confirmation sautée juste au-dessus (bouton "Annuler
        l'élimination" plutôt que "Ignorer (pas de prime)" ; la fermer ou
        cliquer ce bouton renvoie eliminator_id=None, et le garde-fou
        DÉJÀ EXISTANT `if mandatory and eliminator_id is None: return`
        abandonne alors l'élimination — aucune nouvelle logique, seulement
        un mécanisme déjà là appliqué plus largement)."""
        if ids is None:
            ids = self._checked_or_selected_ids()
        if not ids:
            return
        # Un joueur doit toujours rester en jeu : c'est le vainqueur. On
        # bloque donc toute élimination qui viderait la table (élimination
        # du tout dernier actif, seul ou en groupe).
        n_active = len(self.db.list_players(status="active"))
        n_eliminable = sum(
            1 for pid in ids
            if (p := self.db.get_player(pid)) is not None and p["status"] == "active"
        )
        if n_eliminable and n_eliminable >= n_active:
            messagebox.showerror(
                "Impossible",
                "Impossible d'éliminer le dernier joueur encore actif : il "
                "doit toujours en rester au moins un — c'est le vainqueur.",
            )
            return
        # Primes désactivées (demande du 2026-09-09, précisée le même
        # jour après relecture utilisateur) : `primes_matter` conditionne
        # TOUTES les contraintes d'éliminateur liées aux primes/PKO
        # ci-dessous (élimination groupée bloquée, éliminateur individuel
        # obligatoire) — si les primes sont désactivées, aucun mécanisme
        # bounty/PKO n'existe, donc aucun garde ne doit imposer quoi que
        # ce soit "pour une raison de prime" : ni la case à cocher
        # groupée, ni le choix individuel d'un éliminateur (l'information
        # eliminated_by_name reste saisissable normalement si l'utilisateur
        # choisit d'indiquer un éliminateur, simplement plus jamais
        # rendue obligatoire par la logique des primes dans ce cas).
        primes_matter = self.db.primes_enabled()
        pko_mode = primes_matter and self.db.get_setting_int("pko_mode", 0) == 1
        test_mode = self._test_mode_enabled()
        if len(ids) == 1:
            p = self.db.get_player(ids[0])
            question = f"Éliminer {p['name']} du tournoi ?"
        else:
            # Élimination GROUPÉE : jamais d'éliminateur désigné
            # individuellement (voir eliminator_id=None plus bas), donc un
            # moyen de contourner l'attribution d'une bounty — bloquée en
            # fonctionnement normal SEULEMENT si les primes sont activées
            # (rien à contourner sinon, voir primes_matter ci-dessus) et
            # que le Mode Test n'est pas actif (demande du 2026-09-09,
            # facilité d'essai explicitement demandée) : chaque joueur
            # doit alors être éliminé un par un, avec un éliminateur
            # obligatoire (voir plus bas).
            if primes_matter and not test_mode:
                messagebox.showerror(
                    "Élimination groupée indisponible",
                    "Un éliminateur doit obligatoirement être désigné pour "
                    "chaque élimination (une prime est en jeu) : éliminez "
                    "ces joueurs un par un.\n\n"
                    "(Activez « Mode Test » dans Paramètres pour une "
                    "élimination groupée sans éliminateur, réservée aux "
                    "essais — ou décochez « Calculer les primes » si les "
                    "primes ne doivent plus du tout intervenir.)",
                )
                return
            # Mode Test + PKO (demande du 2026-09-09) : la protection
            # anti-bounty-orpheline de la base (Database.eliminate_player)
            # serait normalement bloquante ici puisqu'aucun éliminateur
            # n'est désigné en groupe — c'est exactement la facilité de
            # test demandée : éliminer en vrac même des joueurs porteurs
            # d'une bounty PKO, SANS créer de faux éliminateur ni
            # transférer leur bounty nulle part (elle reste simplement sur
            # ces joueurs désormais éliminés, jamais relue ailleurs — voir
            # orphan_bounty_ok plus bas et sa docstring). Simple note
            # informative ici, plus un blocage : le contournement est le
            # but recherché en Mode Test.
            with_bounty = [
                p["name"] for pid in ids
                if (p := self.db.get_player(pid)) and p["status"] == "active" and p["bounty"] > 0
            ] if pko_mode else []
            bounty_note = (
                "\n\n⚠️ Mode Test : " + ", ".join(with_bounty) + " porte(nt) une "
                "bounty PKO qui sera perdue (non attribuée), aucun "
                "éliminateur n'étant désigné en élimination groupée."
            ) if with_bounty else ""
            question = (
                f"Éliminer ces {len(ids)} joueurs du tournoi ?"
                "\n\n(Élimination groupée : personne ne sera désigné comme "
                "éliminateur, donc aucun bounty (points) ne sera attribué "
                "ici. Éliminez ces joueurs un par un si vous voulez "
                "enregistrer qui élimine qui.)" + bounty_note
            )
        # skip_confirmation : voir la docstring ci-dessus — jamais honoré
        # pour une élimination groupée (len(ids) > 1), seulement pour le
        # raccourci clic droit sur un seul joueur.
        if not (skip_confirmation and len(ids) == 1):
            if not messagebox.askyesno("Confirmer", question):
                return

        eliminator_id = None
        if len(ids) == 1:
            # Éliminateur obligatoire pour une élimination individuelle
            # UNIQUEMENT si une attribution de bounty/PKO en dépend
            # réellement (demande du 2026-09-09, précisée après relecture
            # utilisateur) : primes activées ET ce joueur précis porte une
            # bounty > 0 — jamais "toujours obligatoire", et INDÉPENDANT
            # du Mode Test (qui ne facilite que l'élimination GROUPÉE,
            # jamais la règle individuelle). Le bouton "Ignorer (pas de
            # prime)" de _ask_eliminator disparaît de lui-même dès que
            # mandatory=True (il devient "Annuler l'élimination") ; il
            # reste disponible normalement si aucune bounty n'est en jeu
            # (primes désactivées, bounty nulle, ou classique/PKO à 0).
            mandatory = force_mandatory_eliminator or (primes_matter and p["bounty"] > 0)
            eliminator_id = self._ask_eliminator(exclude_id=ids[0], mandatory=mandatory)
            if mandatory and eliminator_id is None:
                return  # élimination abandonnée : rien n'est modifié

        # orphan_bounty_ok=True UNIQUEMENT pour l'élimination groupée
        # (voir Database.eliminate_player) : ce chemin n'est atteignable
        # que si primes_matter est faux OU si le Mode Test est actif (déjà
        # vérifié ci-dessus) — jamais pour une élimination individuelle.
        orphan_bounty_ok = len(ids) > 1
        moved_count = 0
        for pid in ids:
            try:
                moved_count += len(self.db.eliminate_player(
                    pid, eliminated_by_id=eliminator_id, orphan_bounty_ok=orphan_bounty_ok
                ))
            except ValueError as e:
                # Filet de sécurité (garde-fou équivalent, plus fort, côté
                # Database.eliminate_player) : ne devrait normalement plus
                # se produire grâce aux vérifications ci-dessus, mais ne
                # doit jamais planter ni laisser un état à moitié traité.
                messagebox.showerror("Élimination refusée", str(e))
                break
            self._queue_elimination_banner(pid, eliminator_id)
        self._clear_checked()
        # _trigger_movement_alert/_finish_movement_alert AVANT _refresh_all
        # (voir la même remarque dans _remote_eliminate) : positionne
        # movement_alert_active avant que l'onglet Joueurs ne se
        # rafraîchisse, pour qu'il affiche tout de suite l'ancienne
        # table/siège plutôt que la nouvelle (voir _refresh_players_tab).
        if len(self.db.list_players(status="active")) <= 1:
            # Tournoi terminé (0 ou 1 joueur encore actif) : un éventuel
            # rééquilibrage resté "en attente" (alerte non fermée via
            # "Terminé" avant cette dernière élimination) n'a plus lieu
            # d'être affiché — sans ça, d'anciens mouvements traînaient
            # dans l'onglet Mouvements après la fin de la partie.
            if (self.db.get_setting_int("movement_alert_active", 0) == 1
                    or self.db.count_seat_moves() > 0):
                self._finish_movement_alert()
        elif moved_count:
            self._trigger_movement_alert()
        self._refresh_all()
        self._check_pending_rebalance()

    def _ask_eliminator_position(self):
        """Dernière position mémorisée pour la fenêtre "Qui a éliminé ce
        joueur ?" (demande du 2026-09-16), ou None si aucune n'est encore
        connue, ou si elle est devenue hors écran (changement de
        résolution/moniteur — voir _is_position_onscreen, même garde-fou
        déjà utilisé pour la fenêtre flottante de demande de téléphone).
        None laisse Tk choisir sa position par défaut, EXACTEMENT le
        comportement d'avant cette demande — jamais de nouvelle position
        par défaut inventée ici, seule la RESTAURATION est ajoutée.

        export_prefs (préférence PERSISTANTE, comme remote_device_popup_
        x/y) plutôt qu'un mécanisme séparé : survit à un redémarrage
        complet de Senaco, réutilise tel quel le mécanisme déjà éprouvé
        (voir _remote_device_popup_position/_save_remote_device_popup_
        position, plus haut)."""
        x = export_prefs.load_value("ask_eliminator_window_x", None)
        y = export_prefs.load_value("ask_eliminator_window_y", None)
        if (
            isinstance(x, int) and isinstance(y, int)
            and self._is_position_onscreen(x, y, self.winfo_screenwidth(), self.winfo_screenheight())
        ):
            return x, y
        return None

    def _save_ask_eliminator_position(self, x, y):
        """Mémorise la position de "Qui a éliminé ce joueur ?" à chaque
        déplacement (voir le bind <Configure> dans _ask_eliminator) —
        même mécanisme que _save_remote_device_popup_position."""
        export_prefs.save_value("ask_eliminator_window_x", x)
        export_prefs.save_value("ask_eliminator_window_y", y)

    def _on_ask_eliminator_window_configure(self, event, win):
        """Gestionnaire lié à <Configure> pour la fenêtre "Qui a éliminé
        ce joueur ?" (voir _ask_eliminator) — méthode LIÉE (App), pas une
        fonction imbriquée : directement testable avec un faux événement
        (voir tests/test_ask_eliminator_window_position.py), comme
        RemoteDeviceRequestWindow._on_configure.

        CORRECTIF du 2026-09-17 (diagnostic confirmé : la position ne se
        conservait plus correctement) : `win.bind("<Configure>", ...)`
        est posé sur la fenêtre, mais un <Configure> émis par N'IMPORTE
        LEQUEL de ses enfants (Label, Combobox, Frame, Bouton — chacun
        empaqueté juste après ce bind, voir _ask_eliminator) REMONTE
        aussi jusqu'à ce gestionnaire : la fenêtre fait partie des
        bindtags de chacun de ses enfants. `event.widget` n'est alors
        PAS `win` mais cet enfant, dont winfo_x()/winfo_y() renvoient une
        position relative à SON PARENT (souvent de petites valeurs, ex.
        16/190), pas la position écran de la fenêtre — ce qui écrasait
        silencieusement la position réellement mémorisée dès la
        construction de la fenêtre (et à chaque redessin d'un enfant),
        indépendamment de tout vrai déplacement par l'utilisateur.

        `win` est désormais passé explicitement (capturé par la lambda
        de _ask_eliminator, jamais un nouvel attribut persistant sur
        self) : seul un `<Configure>` dont `event.widget is win` est
        pris en compte — reproduit la garde déjà correcte de
        RemoteDeviceRequestWindow._on_configure (`if event.widget is
        self`), qui n'avait pas été reprise ici à l'origine. <Configure>
        se déclenche aussi pour un simple redessin interne DE `win`
        elle-même (pas seulement un déplacement) — inoffensif, réécrire
        la même position ne coûte presque rien."""
        if event.widget is not win:
            return
        try:
            self._save_ask_eliminator_position(win.winfo_x(), win.winfo_y())
        except tk.TclError:
            pass

    def _ask_eliminator(self, exclude_id, mandatory=False):
        """Petite fenêtre pour choisir qui a éliminé le joueur — sert à
        compter ses bounties (kills, onglet Primes) et, si une bounty est
        configurée, à la lui attribuer. Ne propose que les joueurs de la
        MÊME TABLE que l'éliminé : au poker, on ne peut éliminer que
        quelqu'un assis à sa propre table. Renvoie l'id du joueur choisi,
        ou None si ignoré/annulé.

        `mandatory=True` (demande du 2026-09-08, PKO + bounty > 0) : le
        bouton "Ignorer (pas de prime)" devient "Annuler l'élimination" —
        un None renvoyé doit alors être compris par l'APPELANT comme
        "abandonner l'élimination elle-même" (elle ne doit PAS être
        exécutée sans éliminateur), jamais comme "l'effectuer quand même
        sans bounty attribuée", pour ne jamais laisser une bounty PKO
        orpheline (voir aussi le garde-fou équivalent, plus fort, dans
        Database.eliminate_player)."""
        eliminated = self.db.get_player(exclude_id)
        # list_players() trie par table/siège (pratique pour l'affichage du
        # tableau Joueurs, pas pour retrouver un nom ici) — trié par nom
        # pour ce menu, plus facile à parcourir.
        same_table = [
            p for p in self.db.list_players(status="active")
            if p["id"] != exclude_id and p["table_id"] == eliminated["table_id"]
        ]
        # Filet de sécurité si l'éliminé n'a plus de table (ne devrait pas
        # arriver pour un joueur encore actif) : autant proposer tout le
        # monde que de bloquer la désignation d'un éliminateur.
        pool = same_table if eliminated["table_id"] else [
            p for p in self.db.list_players(status="active") if p["id"] != exclude_id
        ]
        candidates = sorted(pool, key=lambda p: p["name"].lower())
        if not candidates:
            return None

        win = tk.Toplevel(self)
        win.title("Qui a éliminé ce joueur ?")
        win.configure(bg=FELT_DARK)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        # Mémorisation de position (demande du 2026-09-16) : si aucune
        # position valide n'est encore connue, `position` vaut None et
        # Tk choisit lui-même où l'afficher — comportement STRICTEMENT
        # inchangé pour la toute première ouverture. Voir _ask_eliminator_
        # position/_save_ask_eliminator_position ci-dessus.
        position = self._ask_eliminator_position()
        if position is not None:
            win.geometry(f"+{position[0]}+{position[1]}")
        win.bind("<Configure>", lambda e: self._on_ask_eliminator_window_configure(e, win))
        result = {"id": None}
        header_text = f"Qui a éliminé {eliminated['name']} ?"
        if eliminated["bounty"] > 0:
            header_text = (
                f"💰  {eliminated['name']} portait une prime de "
                f"{eliminated['bounty']:,} pts".replace(",", " ")
            )
        tk.Label(
            win, bg=FELT_DARK, fg=GOLD, font=("Helvetica", 12, "bold"),
            text=header_text,
        ).pack(padx=16, pady=(16, 4))
        subtitle = "Qui l'a éliminé(e) ?" if eliminated["bounty"] > 0 else \
            "(Compte pour son bounty en points, onglet Primes.)"
        if mandatory:
            subtitle += "\nObligatoire en mode PKO : une bounty ne doit jamais rester orpheline."
        tk.Label(win, bg=FELT_DARK, fg=CREAM, text=subtitle, justify="center").pack(padx=16, pady=(0, 10))

        names = [p["name"] for p in candidates]
        name_to_id = {p["name"]: p["id"] for p in candidates}
        var = tk.StringVar(value=names[0])
        combo = ttk.Combobox(win, textvariable=var, values=names, state="readonly", width=28)
        combo.pack(padx=16, pady=(0, 16))

        def confirm():
            result["id"] = name_to_id.get(var.get())
            win.destroy()

        def skip():
            result["id"] = None
            win.destroy()

        btns = ttk.Frame(win)
        btns.pack(pady=(0, 16))
        skip_label = "Annuler l'élimination" if mandatory else "Ignorer (pas de prime)"
        ttk.Button(btns, text=skip_label, command=skip).pack(side="left", padx=5)
        ttk.Button(btns, text="Valider", command=confirm).pack(side="left", padx=5)

        self.wait_window(win)
        return result["id"]

    def _withdraw_selected(self):
        """Désactive un ou plusieurs joueurs sans leur attribuer de place
        au classement (forfait / inscription annulée)."""
        ids = self._checked_or_selected_ids()
        if not ids:
            return
        if len(ids) == 1:
            p = self.db.get_player(ids[0])
            question = (
                f"Désactiver {p['name']} ?\n\n"
                "Il/elle sera retiré(e) de la liste active, sans place au "
                "classement (forfait). Différent d'une élimination."
            )
        else:
            question = (
                f"Désactiver ces {len(ids)} joueurs ?\n\n"
                "Ils seront retirés de la liste active, sans place au "
                "classement (forfait). Différent d'une élimination."
            )
        if messagebox.askyesno("Confirmer", question):
            for pid in ids:
                self.db.withdraw_player(pid)
            self._clear_checked()
            self._refresh_all()

    def _reinstate_selected(self):
        ids = self._checked_or_selected_ids()
        if not ids:
            return
        for pid in ids:
            self.db.reinstate_player(pid)
        self._clear_checked()
        self._refresh_all()

    def _undo_last_elimination(self):
        """Fonction métier CENTRALE UNIQUE pour "Annule Eliminer" (demande
        du 2026-09-17) — appelée aussi bien par le bouton de l'onglet
        Joueurs que par le clic droit sur le dernier joueur éliminé (voir
        _on_players_tree_right_click), jamais dupliquée. Vise TOUJOURS le
        dernier joueur éliminé (Database.undo_last_elimination ne prend
        d'ailleurs aucun paramètre) — indépendant de toute sélection/
        cochage courant dans le tableau.

        Distincte de "Réinscrire" (_reinstate_selected, INCHANGÉE) : ici,
        une vraie annulation — même table/siège, primes/kills/bounty_
        events et mouvements de tables provoqués par cette élimination
        précise entièrement défaits (voir Database.undo_last_elimination
        pour le détail).

        Confirmation obligatoire AVANT toute modification : fermer ou
        refuser la confirmation ne modifie strictement rien (on ne
        touche à la base qu'après le "Oui"). Si l'état a changé depuis
        cette élimination au point de ne plus garantir une restauration
        exacte — y compris le "Timeout pour Annuler Eliminer" (Paramètres,
        demande du 2026-09-17) désormais dépassé ou réglé à 0 — Database.
        undo_last_elimination lève ValueError — affichée telle quelle,
        sans rien modifier (principe de sécurité demandé : refuser
        plutôt que reconstruire approximativement). Ce contrôle de délai
        est fait dans undo_last_elimination lui-même (pas seulement ici
        ni dans l'état grisé du bouton/le clic droit) : même appelée
        directement, en contournant entièrement l'interface, cette
        méthode ne peut jamais restaurer une élimination hors délai."""
        last = self.db.get_last_eliminated_player()
        if last is None:
            return
        question = (
            f"Annuler l'élimination de {last['name']} ?\n\n"
            "Il/elle redevient actif(ve), reprend sa table, son siège et "
            "son classement d'avant cette élimination. Tout ce qu'elle "
            "avait provoqué (primes, mouvements de tables...) est annulé."
        )
        if not messagebox.askyesno("Confirmer", question):
            return
        try:
            moves = self.db.undo_last_elimination()
        except ValueError as e:
            messagebox.showerror("Annulation impossible", str(e))
            return
        # _trigger_movement_alert/_finish_movement_alert AVANT _refresh_all
        # (même remarque que _eliminate_selected/_remote_eliminate) :
        # positionne movement_alert_active avant que l'onglet Joueurs ne
        # se rafraîchisse.
        if len(self.db.list_players(status="active")) <= 1:
            if (self.db.get_setting_int("movement_alert_active", 0) == 1
                    or self.db.count_seat_moves() > 0):
                self._finish_movement_alert()
        elif moves:
            self._trigger_movement_alert()
        elif self.db.get_setting_int("movement_alert_active", 0) == 1:
            # L'annulation elle-même n'a provoqué aucun mouvement (cas
            # simple), mais une alerte de l'élimination désormais annulée
            # était encore affichée sans avoir été fermée via "Terminé" :
            # elle n'a plus lieu d'être, plus rien à confirmer.
            self._finish_movement_alert()
        self._clear_checked()
        self._refresh_all()
        self._check_pending_rebalance()

    def _delete_selected(self):
        ids = self._checked_or_selected_ids()
        if not ids:
            return
        if len(ids) == 1:
            p = self.db.get_player(ids[0])
            question = f"Supprimer définitivement {p['name']} ?"
        else:
            question = f"Supprimer définitivement ces {len(ids)} joueurs ?"
        if messagebox.askyesno("Confirmer", question):
            for pid in ids:
                self.db.delete_player(pid)
            self._clear_checked()
            self._refresh_all()

    def _play_movement_signal(self):
        """Émet le signal de mouvements : le fichier .wav choisi dans
        Paramètres (commun à tous les tournois, voir _choose_clock_sound),
        tronqué à "Durée max. du signal" s'il est plus long qu'elle ; à
        défaut, un bip généré automatiquement de cette même durée."""
        duration = self.db.get_setting_int("movement_signal_duration_ms", 300)
        wav_path = export_prefs.load_value("movement_signal_wav_path", "")
        if wav_path and sound_signal.play_file(wav_path, max_duration_ms=duration):
            return
        if not sound_signal.play_tone(880, duration):
            self.bell()  # repli si la lecture audio n'a pas pu être lancée

    def _trigger_movement_alert(self, from_remote=False):
        """Appelé dès qu'un rééquilibrage a réellement déplacé des joueurs
        (élimination ou bouton "Rééquilibrer les tables") : joue le signal
        sonore et active le bandeau "Changement de tables en cours"
        (onglets Chronomètre + écran projecteur — ce dernier via
        _refresh_clock_tab, qui pousse l'état à self.clock_window.refresh
        indépendamment de la fenêtre principale). Ne met PLUS le
        chronomètre en pause depuis qu'un club a jugé, à l'usage, que le
        temps continue de s'écouler normalement pendant qu'un mouvement
        de tables est en cours (les joueurs se déplacent pendant que la
        partie continue ailleurs, comme dans un vrai tournoi) — le
        responsable ferme juste le bandeau avec "Terminé" une fois tout
        le monde réinstallé, sans que le chrono n'ait jamais été
        interrompu. Bascule aussi
        automatiquement l'onglet Mouvements au premier plan (au-dessus du
        Chronomètre ou de tout autre onglet affiché) et ramène la fenêtre
        principale au premier plan, SAUF si `from_remote=True` (élimination
        décidée depuis le téléphone, voir _remote_eliminate) : dans ce cas
        personne n'est devant le PC, et faire remonter la fenêtre
        principale ne ferait que recouvrir l'écran projecteur (donnant
        l'impression que le mode projecteur s'arrête). Le bouton "Terminé"
        de l'onglet Mouvements (_finish_movement_alert) referme le bandeau
        et relance le chronomètre — comme le raccourci clavier Ctrl+Maj+T
        ou le bouton "Terminé" du contrôle à distance (voir
        _on_voice_word). Annule aussi une éventuelle "élimination en
        attente" (voir _voice_start_elimination) : si l'élimination qui
        vient de se produire a justement causé ce rééquilibrage, c'est
        "Terminé" qui clôt le tout maintenant, plus "Chronomètre"."""
        self._play_movement_signal()
        self.db.set_settings({"movement_alert_active": 1})
        self.voice_awaiting_resume = False
        self._refresh_moves_tab()
        self._refresh_clock_tab()
        if not from_remote:
            self.notebook.select(self.moves_tab)
            # Ramène la fenêtre principale au premier plan (devant l'écran
            # projecteur, potentiellement plein écran) : changer d'onglet
            # ne suffit pas à rendre le bandeau visible si une autre
            # fenêtre le recouvre encore (voir aussi _voice_start_elimination).
            self.lift()
            self.focus_force()

    def _finish_movement_alert(self, switch_to_clock=True):
        """Bouton "Terminé" de l'onglet Mouvements (ou raccourci clavier/
        contrôle à distance, voir _on_voice_word) : referme le bandeau
        d'alerte, vide la liste des mouvements affichée (le prochain
        rééquilibrage la repeuplera avec son propre lot), rebascule
        l'onglet Chronomètre au premier plan dans la fenêtre principale
        (symétrique du passage automatique sur Mouvements fait par
        _trigger_movement_alert) et ramène aussi la fenêtre séparée
        "écran projecteur" au premier plan si elle est ouverte — sans ça
        elle peut rester cachée derrière la fenêtre principale une fois
        l'alerte terminée. Appelle aussi _clock_resume() par précaution
        (relance le chronomètre s'il était encore en pause pour une autre
        raison, ex : "Élimination" en attente de désignation via
        Ctrl+Maj+J/le contrôle à distance — voir _voice_start_elimination)
        : sans effet si le chrono tournait déjà, ce qui est désormais
        toujours le cas pendant un simple mouvement de tables.

        `switch_to_clock=False` (utilisé UNIQUEMENT par le bouton "Terminé"
        de l'onglet Tables, voir _build_tables_tab) : conserve tout le
        même mécanisme de validation ci-dessus (aucune logique dupliquée),
        seulement sans faire passer l'onglet Chronomètre ni l'écran
        projecteur au premier plan — l'utilisateur qui clique depuis
        Tables veut voir IMMÉDIATEMENT les positions réelles sur CET
        onglet, pas être redirigé ailleurs. Tous les autres appelants
        (onglet Mouvements, raccourci clavier, contrôle à distance)
        laissent ce paramètre à sa valeur par défaut : comportement
        strictement inchangé pour eux."""
        self.db.set_settings({"movement_alert_active": 0})
        self.voice_awaiting_resume = False
        self._clock_resume()
        self.db.clear_seat_moves()
        self._refresh_moves_tab()
        if switch_to_clock:
            self.notebook.select(self.clock_tab)
        self._refresh_clock_tab()
        if switch_to_clock and self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()

    # ---------------------------------------------------------------
    # Rééquilibrage simple : question "quel siège est grosse blinde ?"
    # (version TEST, voir database.py: rebalance_tables /
    # resolve_pending_rebalance). Lors d'un simple rééquilibrage entre
    # tables (pas un cassage de table, inchangé), le choix du joueur à
    # déplacer n'est plus automatique : on demande quel siège est
    # actuellement grosse blinde — UNIQUEMENT sur les téléphones du
    # contrôle à distance (voir remote_control.py, _REBALANCE_WIDGET), le
    # premier qui répond gagne. Affichée sur le Mac jusqu'à v1.2.37 (petite
    # fenêtre Tkinter non bloquante) : retirée à la demande explicite de
    # l'utilisateur (trop envahissante sur les écrans du Mac/projecteur),
    # le calcul et le stockage de la proposition (self.db.pending_
    # rebalance) restant, eux, entièrement inchangés — voir
    # _check_pending_rebalance ci-dessous. Le chronomètre et le reste de
    # l'appli continuent de tourner normalement tant qu'aucune réponse
    # n'arrive (jamais de sleep()/wait_window()/boucle d'attente ici, voir
    # consigne).
    # ---------------------------------------------------------------
    def _check_pending_rebalance(self):
        """Tient à jour _remote_pending_rebalance (copie de self.db.
        pending_rebalance lue par le thread du serveur de contrôle à
        distance, voir _start_remote_control_if_enabled et
        remote_control.py: /rebalance_pending) à partir de l'état courant
        de self.db.pending_rebalance, ET l'indication discrète de l'onglet
        Tables (voir _update_pending_rebalance_badge — PHASE 3 de
        l'architecture validée le 2026-09-10 : toujours aucune fenêtre
        intrusive sur le Mac, mais plus le silence total d'avant cette
        version, qui ne laissait voir l'attente que sur les téléphones).
        Appelée juste après chaque action qui peut déclencher un
        rééquilibrage (élimination locale ou distante, bouton
        "Rééquilibrer les tables", changement de "Nombre de sièges par
        table" dans Paramètres) pour une réaction immédiate, et par
        sécurité à chaque tick (_tick) : une réponse arrivée par une autre
        voie doit y être reflétée même si elle n'a pas causé la demande
        suivante."""
        if not self.db:
            return
        self._remote_pending_rebalance = self.db.pending_rebalance
        self._update_pending_rebalance_badge()

    def _update_pending_rebalance_badge(self):
        """Affiche/masque, dans le bandeau du haut de l'onglet Tables,
        l'indication discrète qu'un rééquilibrage attend qu'on désigne le
        joueur UTG à déplacer (voir _build_tables_tab et database.py:
        pending_rebalance) — disparaît dès que la demande est résolue,
        d'où qu'elle le soit (téléphone ou le bouton "Continuer sans
        désigner le joueur" ci-dessous). Ne fait rien si l'onglet Tables
        n'est pas encore construit (tout début de App.__init__, avant
        _build_tabs)."""
        frame = getattr(self, "_pending_rebalance_frame", None)
        if frame is None or not frame.winfo_exists():
            return
        pending = self.db.pending_rebalance if self.db else None
        if pending is None:
            frame.pack_forget()
            return
        self._pending_rebalance_label.configure(
            text=f"⏳ {pending['table_name']} : rééquilibrage en attente du joueur à déplacer"
        )
        # winfo_manager() (pas winfo_ismapped()) : reflète si ce frame est
        # actuellement sous gestion pack, indépendamment de la visibilité
        # réelle à l'écran (fenêtre minimisée/pas encore déiconifiée) —
        # winfo_ismapped() renverrait toujours faux dans ces cas-là et
        # provoquerait un pack() répété à chaque appel (harmless en
        # pratique, mais inutile).
        if frame.winfo_manager() != "pack":
            frame.pack(side="left", padx=(15, 3))

    def _continue_pending_rebalance_without_bb(self):
        """Bouton "Continuer sans désigner le joueur" de l'onglet Tables
        (voir _build_tables_tab/_update_pending_rebalance_badge) — permet
        au responsable de débloquer CE rééquilibrage précis depuis le
        Mac, sans téléphone, en utilisant exactement le même mécanisme
        que la réponse équivalente envoyée depuis un téléphone
        (player_id=None, voir database.py: resolve_pending_rebalance,
        règle 5) — donc le même traitement que _on_bb_rebalance_prompt_
        toggle, à une différence près et volontaire : ce bouton ne touche
        PAS à la préférence globale "Équilibrage guidé par UTG" (self.
        bb_rebalance_prompt_var reste inchangée), donc le PROCHAIN
        rééquilibrage reposera de nouveau la question normalement — ce
        bouton ne résout QUE la demande actuellement affichée. Nom de
        méthode historique (chantier "grosse blinde") volontairement
        inchangé : usage interne uniquement, jamais affiché."""
        if not self.db or self.db.pending_rebalance is None:
            return
        self._resolve_pending_rebalance(
            self.db.pending_rebalance["request_id"], None, from_remote=False
        )

    def _resolve_pending_rebalance(self, request_id, player_id, from_remote):
        """Traite une réponse à la question "quel joueur est UTG ?" —
        reçue d'un téléphone (POST /rebalance_answer, relayé ici par
        _poll_voice_queue, from_remote=True), ou déclenchée localement
        quand la préférence "Équilibrage guidé par UTG" (Paramètres) est
        décochée alors qu'une demande est en attente (voir _on_bb_
        rebalance_prompt_toggle, from_remote=False — aucune fenêtre Mac
        n'est impliquée dans ce second cas, seulement ce même traitement
        de réponse). La mutation réelle passe par database.py:resolve_
        pending_rebalance, qui revalide tout avant d'agir (request_id
        encore valide, table source toujours active, joueur toujours
        actif à cette table — voir sa docstring et _player_still_at_
        table) : ici, on se contente d'enchaîner les mêmes suites qu'une
        élimination normale (alerte de mouvement, rafraîchissements) sur
        le résultat qu'elle renvoie."""
        if not self.db:
            return
        moves = self.db.resolve_pending_rebalance(request_id, player_id)
        if moves:
            if len(self.db.list_players(status="active")) <= 1:
                if (self.db.get_setting_int("movement_alert_active", 0) == 1
                        or self.db.count_seat_moves() > 0):
                    self._finish_movement_alert()
            else:
                self._trigger_movement_alert(from_remote=from_remote)
        self._refresh_all()
        self._refresh_remote_players_cache()
        self._refresh_remote_moves_cache()
        # Le rééquilibrage relancé par resolve_pending_rebalance a pu
        # poser une NOUVELLE question (écart encore présent ailleurs, ou
        # table suivante à son tour trop pleine) : l'affiche tout de suite
        # plutôt que d'attendre le prochain tick.
        self._check_pending_rebalance()

    # ---------------------------------------------------------------
    # Bandeau d'élimination (écran projecteur + onglet Chronomètre) :
    # affiche "XXX est sorti par YYY / Merci d'avoir participé" (ou "XXX
    # est éliminé" sans éliminateur) pendant une durée réglable, pour
    # CHAQUE élimination réussie (contrairement au bandeau de mouvement,
    # qui ne se déclenche que si des joueurs changent réellement de
    # table). Complètement indépendant de movement_alert_active : les
    # deux peuvent être actifs en même temps (voir ClockWindow.refresh,
    # qui empile alors les deux sans jamais les superposer).
    # ---------------------------------------------------------------
    def _queue_elimination_banner(self, eliminated_id, eliminator_id):
        """Ajoute une élimination à la file d'affichage (voir
        _advance_elimination_banner) — appelé juste après un
        eliminate_player réussi, depuis _eliminate_selected ET
        _remote_eliminate : seul point d'entrée de la file, pour ne pas
        dupliquer cette logique entre les deux chemins d'élimination.
        Sans plafond : aucune élimination n'est jamais perdue, chacune
        attend son tour. Ne lève jamais d'exception (un souci ici — nom
        introuvable, etc. — ne doit jamais remettre en cause l'élimination
        déjà enregistrée en base)."""
        try:
            eliminated = self.db.get_player(eliminated_id)
            eliminator = self.db.get_player(eliminator_id) if eliminator_id else None
            self._elimination_banner_queue.append({
                "eliminated_name": eliminated["name"] if eliminated else "?",
                "eliminator_name": eliminator["name"] if eliminator else None,
            })
        except Exception:
            pass

    def _advance_elimination_banner(self):
        """Termine le bandeau d'élimination actuellement affiché (s'il y
        en a un) et, s'il reste un message dans la file, le fait devenir
        le nouveau bandeau courant avec sa PROPRE échéance complète à
        partir de MAINTENANT (durée réglée dans Paramètres — "Durée du
        bandeau d'élimination (secondes)", self.db.get_setting_int
        "elimination_banner_seconds" — INDÉPENDANTE de la "Durée (ms)" du
        son "Son sortie d'un joueur", voir _play_elimination_sound : deux
        réglages séparés, l'un pour l'AFFICHAGE, l'autre pour le SON,
        même si aucun son n'est configuré) et joue le son configuré une
        seule fois. Seul point qui fait avancer la file — appelé (1)
        depuis _refresh_clock_tab quand l'échéance du bandeau courant est
        dépassée, (2) depuis _on_voice_word("chronometre") pour
        l'écourter manuellement à la demande (bouton "Chronomètre" du
        téléphone ou Ctrl+Maj+C) : cette seconde utilisation ne vide
        JAMAIS le reste de la file (elle ne retire qu'UN SEUL élément,
        le bandeau en cours, jamais rappelé ensuite), les messages
        suivants restent strictement dans leur ordre FIFO. Sans effet
        (et sans son) s'il n'y a ni bandeau courant ni file en attente.

        Chaque bandeau mémorise sa PROPRE échéance ("until", calculée ICI
        une seule fois à sa création) dans son propre dict, plutôt qu'un
        minuteur global partagé : si le joueur A est éliminé puis B peu
        après pendant que le bandeau de A est encore affiché, le job de B
        est simplement ajouté à la file (_queue_elimination_banner) et ne
        devient "courant" (avec sa propre échéance à partir de CE
        moment-là) qu'ici, quand celui de A expire — jamais recalculée
        après coup ni partagée entre deux bandeaux. Aucun risque qu'une
        échéance déjà expirée (celle de A) n'efface prématurément B.

        "Durée du bandeau d'élimination" à 0 (voir Paramètres/tooltip) :
        DÉSACTIVE uniquement l'AFFICHAGE — _elimination_banner_current
        n'est alors jamais renseigné (seul point lu par _refresh_clock_
        tab et ClockWindow.refresh pour décider d'afficher quoi que ce
        soit, voir leurs commentaires), donc aucun bandeau nulle part
        (onglet Chronomètre ni écran projecteur). Le son (_play_
        elimination_sound), réglage totalement indépendant, continue lui
        de jouer normalement à chaque élimination — comportement
        volontairement inchangé."""
        self._elimination_banner_current = None
        if self._elimination_banner_queue:
            job = self._elimination_banner_queue.popleft()
            seconds = max(0, min(30, self.db.get_setting_int("elimination_banner_seconds", 5)))
            try:
                self._play_elimination_sound()
            except Exception:
                pass
            if seconds > 0:
                job["until"] = time.time() + seconds
                self._elimination_banner_current = job

    def _play_elimination_sound(self):
        """Petit son d'attention joué une seule fois, exactement au début
        de l'affichage d'un bandeau d'élimination (voir
        _advance_elimination_banner) — même mécanisme de lecture que
        _play_movement_signal/_play_clock_sound (aucune dépendance
        nouvelle) : fichier .wav choisi par l'utilisateur ("Son sortie
        d'un joueur", voir _open_clock_sounds_dialog), tronqué à sa
        "Durée (ms)" s'il est plus long ; à défaut (aucun fichier choisi
        — y compris sur une installation qui n'a pas encore cette
        préférence, ou après un retrait par clic droit), un bip généré
        automatiquement (sound_signal.play_tone, comme le repli du
        Signal de mouvements) pour que le bandeau ait un son dès
        l'installation, sans configuration obligatoire. Jamais en boucle
        (play_tone/play_file ne jouent qu'une fois). Un échec de lecture
        (périphérique audio absent, fichier corrompu...) est avalé en
        silence par sound_signal — le bandeau doit s'afficher normalement
        même sans aucun son."""
        duration = self._clock_sound_duration_ms("sound_elimination_path") or 250
        wav_path = export_prefs.load_value("sound_elimination_path", "")
        if wav_path and sound_signal.play_file(wav_path, max_duration_ms=duration):
            return
        sound_signal.play_tone(660, duration)

    # ---------------------------------------------------------------
    # Raccourcis clavier "Élimination" / "Terminé" / "Chronomètre" — même
    # dispatch que le contrôle à distance (_on_voice_word) : chaque mot
    # n'est traité que s'il a un sens dans l'état courant du tournoi.
    # ---------------------------------------------------------------
    def _bind_voice_command_shortcuts(self):
        """Raccourcis clavier Ctrl+Maj+J / Ctrl+Maj+C / Ctrl+Maj+T pour les
        3 actions "Joueurs"/"Chronomètre"/"Terminé", TOUJOURS actifs (J
        comme "Joueurs" — anciennement E comme "Élimination", renommé
        avec le bouton correspondant du contrôle à distance : ce module
        gère maintenant plus largement les joueurs, pas seulement les
        éliminations). Ctrl+Alt a été écarté (Ctrl+Maj utilisé à la
        place) : sur Mac, Option("Alt")+J/C/T compose des caractères
        spéciaux au niveau du système avant même que l'application ne
        voie la touche, rendant Ctrl+Alt peu fiable là-bas — Maj ne
        compose jamais de caractère spécial, donc fiable sur Windows et
        Mac. Passent par _on_voice_word, exactement comme le contrôle à
        distance : mêmes conditions (ex : "Ctrl+Maj+C" ne fait rien tant
        qu'aucune élimination n'est en attente)."""
        # Chaque raccourci est lié en MAJUSCULE et en minuscule : avec
        # Control enfoncé, Tk ne met pas toujours le keysym en majuscule
        # comme il le ferait pour Maj+lettre seule (constaté sous Windows,
        # où <Control-Shift-J> ne se déclenchait jamais alors que
        # <Control-Shift-j> fonctionne) — lier les deux couvre tous les
        # cas sans dépendre de ce détail d'implémentation par plateforme.
        for key in ("J", "j"):
            self.bind_all(f"<Control-Shift-{key}>", lambda e: self._on_voice_word("elimination"))
        for key in ("C", "c"):
            self.bind_all(f"<Control-Shift-{key}>", lambda e: self._on_voice_word("chronometre"))
        for key in ("T", "t"):
            self.bind_all(f"<Control-Shift-{key}>", lambda e: self._on_voice_word("terminer"))

    # ---------------------------------------------------------------
    # Contrôle à distance depuis un téléphone (voir remote_control.py) —
    # une petite page web avec 3 boutons (Élimination/Chronomètre/
    # Terminé), servie par un serveur local sur le wifi du club. Même
    # dispatch que les raccourcis clavier (_on_voice_word), réglage
    # indépendant.
    # ---------------------------------------------------------------
    def _start_remote_control_if_enabled(self, silent=False):
        """Démarre le petit serveur web de contrôle à distance si le
        réglage correspondant est activé (Paramètres). Silencieux si déjà
        démarré. Si plusieurs tournois/Sit & Go tournent en parallèle
        (voir "Menu principal", chapitre 14), seul le premier obtient le
        port habituel (8765, voir remote_control.DEFAULT_PORT) — les
        suivants démarrent quand même, chacun sur un port libre
        quelconque (voir RemoteControlServer.start), et restent
        joignables depuis le téléphone via le bouton "Lobby" de la page
        du premier (voir open_windows.list_remote_tournaments et
        update_remote_info juste en dessous) : `server.start()` ne lève
        donc plus d'OSError pour ce cas précis, seulement pour un échec
        réellement bloquant (garde défensive conservée ci-dessous).
        `silent=True` (utilisé au lancement automatique d'une fenêtre)
        n'affiche alors aucune fenêtre d'erreur — seulement un message
        dans la console ; `silent=False` (case à cocher cliquée
        explicitement dans Paramètres) affiche l'erreur normalement,
        l'utilisateur a alors besoin du retour."""
        if export_prefs.load_value("remote_control_enabled", False) is not True:
            return
        if self.remote_control_server is not None and self.remote_control_server.is_running:
            return
        # get_tournament_name est appelé depuis le thread du serveur web
        # (ThreadingHTTPServer démarre un thread par requête) : lire
        # self.db directement depuis là plante (sqlite3 refuse qu'une
        # connexion soit utilisée hors du thread qui l'a ouverte —
        # ProgrammingError, silencieusement avalée par http.server, d'où
        # une réponse vide côté téléphone/navigateur). On lit donc plutôt
        # self._remote_control_tournament_name, un simple attribut tenu à
        # jour depuis le thread principal (voir _tick), jamais écrit ni lu
        # ailleurs que par une lecture atomique (le GIL suffit ici).
        self._remote_control_tournament_name = (
            self.db.get_setting("tournament_name", "Tournoi") if self.db else "Tournoi"
        )
        self._remote_control_tournament_path = self.db.path if self.db else None
        self._refresh_remote_players_cache()
        self._refresh_remote_moves_cache()
        self._refresh_remote_dirto_permissions_cache()
        self._remote_clock_paused = self.db.get_setting_int("is_paused", 1) == 1 if self.db else True
        self._remote_has_pending_moves = self.db.count_seat_moves() > 0 if self.db else False
        server = remote_control.RemoteControlServer(
            on_word=lambda word: self.voice_command_queue.put(word),
            get_tournament_name=lambda: self._remote_control_tournament_name,
            get_tournament_path=lambda: self._remote_control_tournament_path,
            get_players=lambda: self._remote_players_cache,
            get_clock_paused=lambda: self._remote_clock_paused,
            get_has_pending_moves=lambda: self._remote_has_pending_moves,
            get_pending_moves=lambda: self._remote_moves_cache,
            on_confirm_move=self._remote_confirm_move_request,
            on_eliminate=self._remote_eliminate_request,
            on_upload_photo=self._remote_upload_photo,
            get_roster_players=self._remote_get_roster_players,
            get_photo_image=self._remote_get_photo_image,
            on_delete_photo=self._remote_delete_photo,
            get_pending_rebalance=lambda: self._remote_pending_rebalance,
            on_rebalance_answer=lambda request_id, player_id: self.voice_command_queue.put(
                ("rebalance_answer", request_id, player_id)
            ),
            on_end_tournament=lambda: self.voice_command_queue.put(("end_tournament",)),
            # Phase 4, "Sécurisation du Contrôle à distance", 2026-09-20 :
            # voir _refresh_remote_dirto_permissions_cache — .strip() ici
            # aussi (même normalisation que Database.set_dirto_
            # authorization/get_remote_device_owner) pour ne jamais rater
            # une correspondance à cause d'un espace superflu.
            get_dirto_permissions=lambda dirto_name: self._remote_dirto_permissions_cache.get(
                (dirto_name or "").strip(), frozenset()
            ),
        )
        try:
            server.start()
        except OSError as exc:
            if silent:
                print(
                    f"[contrôle à distance] port {remote_control.DEFAULT_PORT} déjà utilisé "
                    f"(probablement par une autre fenêtre de l'appli déjà ouverte) : {exc}",
                    file=sys.stderr,
                )
            else:
                messagebox.showerror(
                    "Contrôle à distance",
                    f"Impossible de démarrer le serveur (port {remote_control.DEFAULT_PORT} "
                    f"déjà utilisé ?) :\n{exc}",
                )
            return
        self.remote_control_server = server
        # Fait connaître le port RÉEL de ce tournoi (peut différer de
        # 8765, voir docstring ci-dessus) au registre partagé entre
        # processus, pour que le Lobby de la page du téléphone (servie
        # par quel que soit le tournoi qui a eu le port 8765) puisse le
        # lister et y relayer les requêtes.
        if self.db:
            open_windows.update_remote_info(
                self.db.path, server.port, self._remote_control_tournament_name
            )
        self._refresh_remote_control_status()

    def _stop_remote_control(self):
        if self.remote_control_server is not None:
            self.remote_control_server.stop()
            self.remote_control_server = None
        self._refresh_remote_control_status()

    def _on_remote_control_toggle(self):
        """Case à cocher "Activer le contrôle à distance" (Paramètres) :
        mémorise le choix (réglage commun à tous les tournois/Sit & Go,
        comme la commande vocale) et démarre/arrête le serveur tout de
        suite, sans redémarrer l'appli."""
        enabled = self.remote_control_enabled_var.get()
        export_prefs.save_value("remote_control_enabled", enabled)
        if enabled:
            self._start_remote_control_if_enabled()
        else:
            self._stop_remote_control()

    def _refresh_remote_control_status(self):
        """Met à jour le libellé affichant l'adresse à ouvrir sur le
        téléphone (ou son absence si le serveur n'est pas démarré). Si ce
        tournoi n'a pas eu le port habituel 8765 (un autre tournoi
        tournait déjà, voir _start_remote_control_if_enabled), indique
        plutôt de passer par le Lobby de la page du premier tournoi —
        donner directement l'adresse de ce port-ci ne servirait à rien,
        le pare-feu Windows du club n'autorise en général que 8765."""
        if not hasattr(self, "remote_control_status_lbl"):
            return
        if self.remote_control_server is not None and self.remote_control_server.is_running:
            if self.remote_control_server.port == remote_control.DEFAULT_PORT:
                url = self.remote_control_server.url
                if url.startswith("http://127.0.0.1"):
                    # local_ip() n'a trouvé aucune vraie adresse réseau
                    # (voir son docstring) : donner cette adresse telle
                    # quelle serait inutile, 127.0.0.1 désigne le
                    # téléphone lui-même depuis son propre navigateur,
                    # jamais ce PC — mieux vaut le dire clairement que
                    # laisser croire que l'adresse affichée devrait
                    # marcher.
                    text = (
                        "⚠️ Impossible de déterminer l'adresse réseau de ce PC "
                        "(pas de connexion Wifi/Ethernet active, ou réseau sans "
                        "aucune passerelle) : vérifiez que ce PC est bien "
                        "connecté au même réseau que le téléphone."
                    )
                else:
                    text = f"📱 Sur votre téléphone (même wifi que cet ordinateur), ouvrez :\n{url}"
            else:
                # Même si ce n'est pas CE tournoi-ci qui répond sur le port
                # habituel, l'adresse à taper sur le téléphone reste
                # prévisible (même machine, même port 8765) : autant la
                # donner directement plutôt que de laisser deviner "la
                # page de l'autre tournoi" sans dire laquelle.
                primary_url = f"http://{remote_control.local_ip()}:{remote_control.DEFAULT_PORT}"
                text = (
                    f"📱 Un autre tournoi occupe déjà le port habituel — ouvrez "
                    f"plutôt :\n{primary_url}\nsur votre téléphone, puis "
                    f"choisissez celui-ci via le bouton \"Lobby\"."
                )
            self.remote_control_status_lbl.config(text=text)
        else:
            self.remote_control_status_lbl.config(text="")

    @staticmethod
    def _remote_devices_signature(approved):
        """Petit "instantané" de la liste "Téléphones" de Paramètres
        (appareils APPROUVÉS uniquement — les demandes en attente ont
        leur propre fenêtre flottante, voir RemoteDeviceRequestWindow/
        _refresh_remote_device_popup, jamais affichées ici pour éviter
        un doublon) — comparé à chaque sondage périodique (voir
        _check_remote_device_requests) pour ne reconstruire les widgets
        QUE si quelque chose a RÉELLEMENT changé depuis le dernier
        sondage. Correctif du 2026-09-09 (bug signalé après test réel
        sur Huawei) : appeler _refresh_remote_devices_panel() SANS
        CONDITION à chaque sondage (~2s) détruisait et recréait TOUS les
        widgets de la section, un scintillement permanent, y compris
        LONGTEMPS après qu'une demande ait été traitée, puisque rien
        n'était jamais lié à un changement réel.

        owner_name inclus (Phase 2, 2026-09-20) : une attribution/
        réaffectation/retrait faite depuis UN AUTRE process (un autre
        tournoi ouvert simultanément, même registre partagé — voir
        open_windows.py) doit être détectée comme un changement par ce
        sondage périodique, exactement comme label/ip_last_seen déjà
        présents ici."""
        return tuple(
            (d["browser_id"], d.get("label"), d["ip_last_seen"], d.get("owner_name"))
            for d in approved
        )

    def _refresh_remote_devices_panel(self, approved=None):
        """Reconstruit la liste "Téléphones" de Paramètres — UNIQUEMENT
        les appareils déjà APPROUVÉS (Révoquer/renommer) : les demandes
        en attente ont leur propre fenêtre flottante (demande du
        2026-09-09, abandon définitif de toute intégration dans la
        grille de Paramètres — voir RemoteDeviceRequestWindow), pour ne
        jamais présenter la même demande à deux endroits. Appelée après
        chaque action (Révoquer/renommer, ET depuis _on_remote_device_
        popup_approve : un nouvel appareil approuvé doit apparaître ici
        immédiatement — TOUJOURS sans argument dans ce cas, relit alors
        l'état à jour) et depuis _check_remote_device_requests (avec
        `approved` déjà lu, pour ne pas le relire deux fois pour la même
        vérification). Ne fait rien si le conteneur n'existe pas encore
        (onglet Paramètres pas encore construit) ou plus (fenêtre en
        cours de fermeture) — jamais une exception qui remonterait
        jusqu'à _tick. Met à jour _last_remote_devices_panel_signature
        dans tous les cas (y compris ces appels directs), pour que le
        sondage périodique suivant ne reconstruise pas une seconde fois
        pour rien juste après."""
        container = getattr(self, "remote_devices_container", None)
        if container is None or not container.winfo_exists():
            return
        if approved is None:
            try:
                approved = open_windows.list_approved_remote_devices()
            except Exception:
                return
        self._last_remote_devices_panel_signature = self._remote_devices_signature(approved)

        for child in container.winfo_children():
            child.destroy()

        if not approved:
            ttk.Label(container, text="Aucun téléphone approuvé pour l'instant.", foreground=MUTED).pack(anchor="w")
            return

        for device in approved:
            # Cadre englobant les 2 lignes de CE téléphone (appareil +
            # propriétaire) — pady plus généreux qu'avant (2 -> (2,6))
            # pour bien séparer visuellement un appareil du suivant
            # maintenant que chacun occupe 2 lignes.
            device_frame = ttk.Frame(container)
            device_frame.pack(fill="x", pady=(2, 6), anchor="w")

            row = ttk.Frame(device_frame)
            row.pack(fill="x", anchor="w")
            ttk.Label(row, text="✓", foreground="#1f6b3a").pack(side="left")
            label_var = tk.StringVar(value=device.get("label") or device["short_id"])
            entry = ttk.Entry(row, textvariable=label_var, width=20)
            entry.pack(side="left", padx=(4, 4))
            entry.bind(
                "<FocusOut>",
                lambda e, bid=device["browser_id"], var=label_var: self._on_rename_remote_device(bid, var),
            )
            entry.bind(
                "<Return>",
                lambda e, bid=device["browser_id"], var=label_var: self._on_rename_remote_device(bid, var),
            )
            ttk.Label(row, text=f"({device['ip_last_seen']})", foreground=MUTED).pack(side="left")
            ttk.Button(
                row, text="Révoquer", width=10,
                command=lambda bid=device["browser_id"]: self._on_revoke_remote_device(bid),
            ).pack(side="left", padx=(8, 0))

            # Propriétaire (Phase 2, "Sécurisation du Contrôle à
            # distance", 2026-09-20) : owner_name stocké tel quel dans
            # le registre (voir open_windows.list_approved_remote_
            # devices) — le GROUPE, lui, est résolu EN DIRECT via
            # roster.get_group() à CHAQUE affichage, jamais mis en
            # cache : si le Répertoire a changé depuis l'attribution
            # (personne reclassée ou supprimée), la conséquence est
            # immédiate ici plutôt qu'un libellé figé qui pourrait
            # devenir faux silencieusement.
            owner_row = ttk.Frame(device_frame)
            owner_row.pack(fill="x", anchor="w", padx=(20, 0), pady=(2, 0))
            owner_name = device.get("owner_name")
            owner_group = roster.get_group(owner_name) if owner_name else ""
            if owner_name and owner_group:
                owner_text = f"Propriétaire : {owner_name} ({owner_group})"
            elif owner_name:
                # Nom présent dans le registre mais qui n'est PLUS
                # ADMIN/DIRTO du Répertoire au moment de l'affichage :
                # jamais une exception, traité comme "à corriger" plutôt
                # que silencieusement comme "non lié" pur (l'ADMIN doit
                # voir qu'une action reste nécessaire).
                owner_text = f"Propriétaire : {owner_name} (⚠ absent du Répertoire ADMIN/DIRTO)"
            else:
                owner_text = "Aucune fonction autorisée — contactez un ADMIN"
            ttk.Label(
                owner_row, text=owner_text, foreground=(GOLD if owner_name else MUTED),
            ).pack(side="left")
            ttk.Button(
                owner_row, text=("Changer..." if owner_name else "Attribuer..."), width=12,
                command=lambda bid=device["browser_id"], cur=owner_name or "":
                    self._on_assign_remote_device_owner(bid, cur),
            ).pack(side="left", padx=(8, 0))
            if owner_name:
                ttk.Button(
                    owner_row, text="Retirer la liaison", width=16,
                    command=lambda bid=device["browser_id"]: self._on_clear_remote_device_owner(bid),
                ).pack(side="left", padx=(4, 0))

    def _on_assign_remote_device_owner(self, browser_id, current_owner=""):
        """Bouton "Attribuer.../Changer..." d'un appareil déjà approuvé
        (voir _refresh_remote_devices_panel) — Phase 2, 2026-09-20.
        Ouvre ask_device_owner_dialog (liste ADMIN/DIRTO du Répertoire
        uniquement) : None (annulé/fermé) ne change rien ; "" (option
        "(Aucun...)") retire la liaison, exactement comme _on_clear_
        remote_device_owner ci-dessous — même action, deux chemins pour
        y arriver (ce bouton, ou "Retirer la liaison" directement)."""
        owner = ask_device_owner_dialog(self, current_owner=current_owner)
        if owner is None:
            return
        if owner:
            open_windows.set_remote_device_owner(browser_id, owner)
        else:
            open_windows.clear_remote_device_owner(browser_id)
        self._refresh_remote_devices_panel()

    def _on_clear_remote_device_owner(self, browser_id):
        """Bouton "Retirer la liaison" : retire le propriétaire SANS
        révoquer l'appareil (voir open_windows.clear_remote_device_
        owner) — l'appareil reste "approved", continue de passer le
        niveau 1 d'authentification normalement."""
        open_windows.clear_remote_device_owner(browser_id)
        self._refresh_remote_devices_panel()

    # -- Permissions DIRTO PAR TOURNOI (Phase 3, "Sécurisation du
    # Contrôle à distance", 2026-09-20) — voir database.py :
    # remote_authorizations/REMOTE_PERMISSION_LABELS. Stockage dans LE
    # TOURNOI COURANT (self.db) UNIQUEMENT : deux tournois ouverts
    # simultanément ont chacun leur propre fichier .tournoi, donc
    # structurellement leurs propres autorisations, sans le moindre code
    # d'étanchéité à écrire ici — c'est l'architecture même de Database
    # qui la garantit. -----------------------------------------------

    def _build_remote_dirto_permissions_widgets(self, container):
        """Construit UNE FOIS le formulaire "Permissions DIRTO" — les
        Combobox ADMIN/DIRTO et la liste des autorisations déjà
        accordées sont ensuite tenues à jour par le sondage périodique
        déjà existant (~2s, voir _check_remote_device_requests, qui
        appelle _refresh_remote_dirto_permissions_panel) plutôt que
        reconstruites ici à chaque tick : voir sa docstring pour le
        détail (values Combobox rafraîchies sans jamais perdre la
        sélection/saisie en cours, liste des autorisations reconstruite
        seulement si son contenu a RÉELLEMENT changé)."""
        selector_row = ttk.Frame(container)
        selector_row.pack(fill="x", anchor="w")

        ttk.Label(selector_row, text="Autorisé par (ADMIN) :").grid(
            row=0, column=0, sticky="w", padx=(0, 4), pady=2
        )
        self.remote_dirto_admin_var = tk.StringVar(value="")
        self.remote_dirto_admin_combo = ttk.Combobox(
            selector_row, textvariable=self.remote_dirto_admin_var, state="readonly", width=18,
        )
        self.remote_dirto_admin_combo.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=2)

        ttk.Label(selector_row, text="Utilisateur autorisé (DIRTO) :").grid(
            row=1, column=0, sticky="w", padx=(0, 4), pady=2
        )
        self.remote_dirto_user_var = tk.StringVar(value="")
        self.remote_dirto_user_combo = ttk.Combobox(
            selector_row, textvariable=self.remote_dirto_user_var, state="readonly", width=18,
        )
        self.remote_dirto_user_combo.grid(row=1, column=1, sticky="w", pady=2)
        # Précharge les permissions DÉJÀ accordées à ce DIRTO pour ce
        # tournoi (s'il y en a) dès qu'il est choisi — "Accorder /
        # Modifier" modifie ainsi réellement l'existant plutôt que de
        # toujours repartir d'un formulaire vide (voir "règle explicite :
        # modification d'une autorisation existante").
        self.remote_dirto_user_combo.bind(
            "<<ComboboxSelected>>", lambda e: self._on_remote_dirto_user_selected(),
        )

        checks_frame = ttk.Frame(container)
        checks_frame.pack(fill="x", anchor="w", pady=(8, 4))
        # Une VRAIE fonction utilisateur par case (jamais une route
        # technique isolée) : REMOTE_PERMISSION_LABELS regroupe déjà les
        # routes en fonctions compréhensibles (voir database.py) —
        # "Terminer le tournoi" n'y figure structurellement JAMAIS
        # (absente du dict lui-même, pas seulement décochée), donc ne
        # peut littéralement pas apparaître ici.
        #
        # Case et libellé dans DEUX widgets/colonnes de grille séparés
        # (demande du 2026-09-22, "réorganisation visuelle du contrôle à
        # distance") — plutôt qu'un unique ttk.Checkbutton portant son
        # propre texte : garantit que les cases d'une même sous-colonne
        # tombent TOUTES exactement sur la même coordonnée X, quelle que
        # soit la longueur du libellé juste à côté. Cliquer le LIBELLÉ
        # coche/décoche aussi la case (bind manuel), pour ne pas réduire
        # la zone cliquable.
        #
        # DEUX colonnes de paires (case, libellé) — demande du
        # 2026-09-24, "réorganisation Permissions DIRTO" : réduit
        # fortement la hauteur occupée (7 lignes -> 4). Répartition
        # "en colonne" (PAS 2 par 2 dans l'ordre de lecture) :
        # les ceil(7/2)=4 premières permissions (dans l'ordre de
        # REMOTE_PERMISSION_LABELS — eliminations/tables/moves/clock)
        # descendent la colonne de GAUCHE, les 3 restantes (levels/
        # photos/rebalance) descendent la colonne de DROITE — c'est bien
        # ce qui place "Changer de niveau (blindes)" en tête de la
        # colonne de droite, à côté de "Gérer les éliminations", comme
        # demandé explicitement. self.remote_dirto_permission_vars
        # reste un dict PLAT avec les 7 mêmes clés qu'avant (eliminations,
        # tables, moves, clock, levels, photos, rebalance) — aucune
        # variable ni clé renommée, seul leur PLACEMENT dans la grille
        # change.
        # Deux sous-frames INDÉPENDANTES (chacune sa propre grille à 2
        # colonnes case/libellé) plutôt qu'une seule grille à 4 colonnes
        # partagée (ajustement du 2026-09-24, "décaler la colonne de
        # droite d'environ 1 cm vers la gauche", affiné le 2026-09-25
        # pour un alignement plus précis avec les Combobox "Autorisé
        # par"/"Utilisateur autorisé" juste au-dessus) : la position de
        # la sous-colonne de droite devient ainsi un SEUL réglage
        # explicite (padx=0 ci-dessous — la valeur la plus resserrée
        # possible sans chevauchement), plutôt que de dépendre aussi, de
        # façon peu prévisible, de la largeur naturelle du libellé le
        # plus long de la colonne de gauche ("Chronomètre (pause /
        # reprise)") au sein d'une grille partagée. Chaque sous-frame
        # garde son alignement vertical PROPRE (row 0..3), inchangé —
        # seule la POSITION HORIZONTALE de la sous-frame de droite, dans
        # son ensemble, se décale.
        #
        # IMPORTANT (tenté puis retiré le 2026-09-25) : un alignement
        # PIXEL-EXACT mesuré dynamiquement (winfo_x/winfo_reqwidth après
        # update_idletasks()) a été essayé ici pour caler EXACTEMENT ce
        # bord sur celui des Combobox — retiré aussitôt : update_
        # idletasks(), sur CETTE combinaison macOS/Python/Tk, segfault
        # de façon déterministe dès qu'un tk.Tk() a été créé PUIS détruit
        # plus tôt dans le MÊME process — exactement le schéma de la
        # sonde _TK_AVAILABLE présente dans TOUS les fichiers de tests
        # Tk de cette suite (voir tests/test_clock_projo_movement_alert_
        # drag.py pour le diagnostic complet de ce bug pré-existant,
        # sans rapport avec ce chantier). Reproduit et confirmé ici même
        # (tests/test_dirto_permissions_widget.py crashait tout le
        # process dès qu'il construisait ce formulaire) — jamais un
        # `update_idletasks()` ajouté à un chemin de code construit par
        # cette suite, même si l'usage réel (une seule racine, jamais
        # détruite puis recréée) serait probablement sûr : le risque
        # pour la testabilité l'emporte sur la précision au pixel près.
        #
        # Correction finale du 2026-09-25 : la valeur exacte (-40 px) a
        # été obtenue via un diagnostic TEMPORAIRE sûr (impression de
        # winfo_rootx() différée par self.after() depuis un vrai
        # évènement <<NotebookTabChanged>>, donc après mappage naturel
        # par Tk — jamais un update_idletasks() forcé), lancé une seule
        # fois dans l'app réelle puis entièrement retiré. Mesures
        # relevées : combo_admin_x = 949, combo_dirto_x = 949,
        # right_checkbox_x = 989 (avec padx=0) — d'où un décalage grid
        # column=1 trop large de 40 px par rapport aux deux Combobox.
        # grid() ne permet pas de padx négatif (chevauchement de
        # colonnes interdit) : la sous-frame de droite est donc
        # positionnée avec place(), relativement à left_checks
        # (relx=1.0 = juste après son bord droit, x=-40 = 40 px plus à
        # gauche que cette position), ce qui exprime directement "40 px
        # à gauche de la position actuelle" sans aucune valeur absolue
        # ni mesure dynamique.
        self.remote_dirto_permission_vars = {}
        items = list(REMOTE_PERMISSION_LABELS.items())
        half = (len(items) + 1) // 2
        left_checks = ttk.Frame(checks_frame)
        left_checks.grid(row=0, column=0, sticky="nw")
        right_checks = ttk.Frame(checks_frame)
        right_checks.place(in_=left_checks, relx=1.0, x=-40, y=0, anchor="nw")
        for group_frame, group_items in ((left_checks, items[:half]), (right_checks, items[half:])):
            for row_idx, (key, label) in enumerate(group_items):
                var = tk.BooleanVar(value=False)
                self.remote_dirto_permission_vars[key] = var
                cb = ttk.Checkbutton(group_frame, variable=var)
                cb.grid(row=row_idx, column=0, sticky="w", padx=(0, 8), pady=3)
                label_lbl = ttk.Label(group_frame, text=label)
                label_lbl.grid(row=row_idx, column=1, sticky="w", pady=3)
                label_lbl.bind("<Button-1>", lambda e, v=var: v.set(not v.get()))

        btns_row = ttk.Frame(container)
        btns_row.pack(fill="x", anchor="w", pady=(4, 8))
        ttk.Button(
            btns_row, text="Accorder / Modifier", command=self._on_grant_remote_dirto_permissions,
        ).pack(side="left")
        ttk.Button(
            btns_row, text="Retirer l'autorisation",
            command=lambda: self._on_revoke_remote_dirto_permissions(),
        ).pack(side="left", padx=(8, 0))

        ttk.Label(
            container, text="Autorisations déjà accordées pour ce tournoi :", foreground=MUTED,
        ).pack(anchor="w", pady=(4, 2))
        self.remote_dirto_list_container = ttk.Frame(container)
        self.remote_dirto_list_container.pack(fill="x", anchor="w")

        self._last_remote_dirto_signature = None
        self._refresh_remote_dirto_permissions_panel()

    @staticmethod
    def _remote_dirto_signature(authorizations):
        """Même principe que _remote_devices_signature : "instantané"
        comparé à chaque sondage périodique pour ne reconstruire la
        liste des autorisations affichées QUE si quelque chose a
        RÉELLEMENT changé (jamais de scintillement pour rien, même bug
        déjà corrigé une fois pour le panneau "Téléphones")."""
        return tuple(
            (a["dirto_name"], a["admin_name"], tuple(sorted(a["permissions"])))
            for a in authorizations
        )

    def _refresh_remote_dirto_permissions_panel(self):
        """Rafraîchit le formulaire "Permissions DIRTO" — appelée à la
        construction ET à chaque sondage périodique (~2s, voir _check_
        remote_device_requests), pour suivre les changements du
        Répertoire (nouveaux ADMIN/DIRTO, personnes reclassées/
        supprimées) SANS jamais perturber une saisie en cours :

        - seule la liste `values` des deux Combobox est mise à jour
          (opération non destructive : ne touche JAMAIS la sélection
          déjà faite par l'ADMIN, contrairement à une reconstruction
          complète des widgets) ;
        - la LISTE des autorisations déjà accordées, elle, n'est
          reconstruite que si son contenu a RÉELLEMENT changé (voir
          _remote_dirto_signature) — jamais à chaque tick pour rien.

        Ne fait rien si le conteneur n'existe pas encore/plus (onglet
        Paramètres pas encore construit, ou fenêtre en cours de
        fermeture) — jamais une exception qui remonterait jusqu'à
        _tick, même principe que _refresh_remote_devices_panel."""
        admin_combo = getattr(self, "remote_dirto_admin_combo", None)
        if admin_combo is None or not admin_combo.winfo_exists():
            return
        admin_combo["values"] = [e["name"] for e in roster.list_by_group(roster.ROSTER_GROUP_ADMIN)]
        self.remote_dirto_user_combo["values"] = [
            e["name"] for e in roster.list_by_group(roster.ROSTER_GROUP_DIRTO)
        ]

        authorizations = self.db.list_dirto_authorizations() if self.db is not None else []
        signature = self._remote_dirto_signature(authorizations)
        if signature == self._last_remote_dirto_signature:
            return
        self._last_remote_dirto_signature = signature

        container = self.remote_dirto_list_container
        for child in container.winfo_children():
            child.destroy()
        if not authorizations:
            ttk.Label(
                container, text="Aucune autorisation DIRTO pour ce tournoi.", foreground=MUTED,
            ).pack(anchor="w")
            return
        # Présentation par bloc (demande du 2026-09-24, "réorganisation
        # Permissions DIRTO") : ligne d'identité "{dirto} — accordé par
        # {admin}" + bouton "Retirer" (même fonction qu'avant, toujours
        # lié à CE dirto_name précis — jamais à la sélection courante du
        # Combobox, voir tests/test_dirto_permissions_widget.py::
        # RetraitTest.test_retirer_via_bouton_dune_ligne_precise) sur la
        # MÊME ligne, immédiatement à droite — plus "au bout" d'une
        # longue ligne à virgules, dont la position dépendait du nombre
        # de fonctions accordées. Permissions accordées affichées EN
        # DESSOUS, groupées deux par ligne (jamais une chaîne à
        # virgules) — dans l'ordre canonique de REMOTE_PERMISSION_LABELS
        # (pas l'ordre de stockage, potentiellement différent), et
        # UNIQUEMENT celles réellement accordées à ce DIRTO.
        for auth in authorizations:
            header_row = ttk.Frame(container)
            header_row.pack(fill="x", anchor="w", pady=(6, 0))
            ttk.Label(
                header_row, text=f"{auth['dirto_name']} — accordé par {auth['admin_name']}",
            ).pack(side="left")
            ttk.Button(
                header_row, text="Retirer", width=8,
                command=lambda name=auth["dirto_name"]: self._on_revoke_remote_dirto_permissions(name),
            ).pack(side="left", padx=(8, 0))

            granted_labels = [
                REMOTE_PERMISSION_LABELS[key] for key in REMOTE_PERMISSION_LABELS
                if key in auth["permissions"]
            ]
            if not granted_labels:
                ttk.Label(
                    container, text="(aucune fonction cochée)", foreground=MUTED,
                ).pack(anchor="w", padx=(14, 0), pady=(0, 4))
                continue
            perms_grid = ttk.Frame(container)
            perms_grid.pack(fill="x", anchor="w", padx=(14, 0), pady=(0, 4))
            for idx, perm_label in enumerate(granted_labels):
                perm_row, perm_col = divmod(idx, 2)
                ttk.Label(perms_grid, text=perm_label).grid(
                    row=perm_row, column=perm_col, sticky="w", padx=(0, 20), pady=1
                )

    def _on_remote_dirto_user_selected(self):
        """Quand l'ADMIN choisit un DIRTO dans « Utilisateur autorisé » :
        précharge SES permissions déjà accordées pour CE tournoi (s'il y
        en a). « Autorisé par » n'est JAMAIS préchargé ici, volontaire :
        identifie qui effectue CETTE action précise, pas qui l'a
        accordée la dernière fois — voir Database.set_dirto_
        authorization, "changement d'ADMIN accordant les droits"."""
        name = self.remote_dirto_user_var.get().strip()
        auth = self.db.get_dirto_authorization(name) if (name and self.db is not None) else None
        granted = set(auth["permissions"]) if auth else set()
        for key, var in self.remote_dirto_permission_vars.items():
            var.set(key in granted)

    def _on_grant_remote_dirto_permissions(self):
        """Bouton "Accorder / Modifier" : crée l'autorisation si elle
        n'existait pas encore, ou la REMPLACE intégralement sinon (voir
        Database.set_dirto_authorization) — les cases NON cochées sont
        donc bien RETIRÉES si elles l'étaient avant, pas seulement les
        nouvelles cases cochées AJOUTÉES."""
        admin_name = self.remote_dirto_admin_var.get().strip()
        dirto_name = self.remote_dirto_user_var.get().strip()
        if not admin_name:
            messagebox.showinfo("Info", "Choisissez d'abord un ADMIN dans « Autorisé par ».", parent=self)
            return
        if not dirto_name:
            messagebox.showinfo(
                "Info", "Choisissez d'abord un DIRTO dans « Utilisateur autorisé ».", parent=self,
            )
            return
        permissions = [key for key, var in self.remote_dirto_permission_vars.items() if var.get()]
        self.db.set_dirto_authorization(dirto_name, admin_name, permissions)
        # Effet IMMÉDIAT côté téléphone, sans attendre le prochain tick
        # (Phase 4, "Sécurisation du Contrôle à distance", 2026-09-20) —
        # voir _refresh_remote_dirto_permissions_cache, également
        # rafraîchi chaque seconde par _tick comme filet de sécurité.
        self._refresh_remote_dirto_permissions_cache()
        self._last_remote_dirto_signature = None  # force la reconstruction de la liste ci-dessous
        self._refresh_remote_dirto_permissions_panel()

    def _on_revoke_remote_dirto_permissions(self, dirto_name=None):
        """Bouton "Retirer l'autorisation" (formulaire) ou "Retirer" (une
        ligne précise de la liste, `dirto_name` alors fourni directement)
        — retrait COMPLET (voir Database.clear_dirto_authorization),
        jamais un simple décochage de cases laissé en base."""
        dirto_name = dirto_name or self.remote_dirto_user_var.get().strip()
        if not dirto_name:
            messagebox.showinfo(
                "Info", "Choisissez d'abord un DIRTO dans « Utilisateur autorisé ».", parent=self,
            )
            return
        if self.db is not None:
            self.db.clear_dirto_authorization(dirto_name)
        # Effet IMMÉDIAT côté téléphone (voir la remarque équivalente
        # dans _on_grant_remote_dirto_permissions ci-dessus).
        self._refresh_remote_dirto_permissions_cache()
        if self.remote_dirto_user_var.get().strip() == dirto_name:
            for var in self.remote_dirto_permission_vars.values():
                var.set(False)
        self._last_remote_dirto_signature = None
        self._refresh_remote_dirto_permissions_panel()

    def _on_revoke_remote_device(self, browser_id):
        """Bouton "Révoquer" d'un appareil déjà approuvé (voir _refresh_
        remote_devices_panel) : bascule en "revoked" côté open_windows
        et invalide IMMÉDIATEMENT tout jeton de session en cours pour
        cet appareil."""
        open_windows.revoke_remote_device(browser_id)
        self._refresh_remote_devices_panel()

    def _on_rename_remote_device(self, browser_id, label_var):
        label = label_var.get().strip()
        if label:
            open_windows.approve_remote_device(browser_id, label=label)
        self._refresh_remote_devices_panel()

    def _is_ca_tab_active(self):
        """True si l'onglet "CA" est actuellement affiché — testé via
        son libellé RÉEL (voir _update_ca_tab_badge : peut porter le
        suffixe "🔔", d'où startswith plutôt qu'une égalité stricte).
        TclError (fenêtre en cours de fermeture) traitée comme "non
        actif", jamais une exception qui remonterait à _tick.

        Renommée le 2026-09-24 (chantier "séparation CA/LOG en CA +
        LOG") — s'appelait _is_ca_log_tab_active tant que CA et LOG
        partageaient le même onglet (elle-même issue de _is_settings_
        tab_active, voir l'historique du 2026-09-22) ; comportement/
        mécanisme inchangés, seul l'onglet surveillé a changé. L'onglet
        "LOG", lui, ne doit JAMAIS être considéré comme "CA actif" —
        son libellé ne commence pas par "CA", ce startswith l'exclut
        donc déjà naturellement."""
        try:
            return self.notebook.tab(self.notebook.select(), "text").startswith("CA")
        except tk.TclError:
            return False

    def _update_ca_tab_badge(self, has_pending):
        """🔔 sur l'onglet "CA" tant qu'AU MOINS UNE demande de téléphone
        est en attente (demande du 2026-09-09, "avertir le responsable
        même si CA n'est pas ouvert") — reflète TOUJOURS l'ensemble des
        demandes pending, y compris celles actuellement masquées par
        "Plus tard" (voir _remote_device_snoozed_keys) : "Plus tard" ne
        doit JAMAIS faire disparaître ce signal, seulement fermer la
        fenêtre flottante elle-même.

        Renommée le 2026-09-24 (voir _is_ca_tab_active) — s'appelait
        _update_ca_log_tab_badge tant que CA et LOG partageaient le même
        onglet (elle-même issue de _update_settings_tab_badge)."""
        try:
            current_label = self.notebook.tab(self.ca_tab, "text")
        except tk.TclError:
            return
        base = current_label[:-2] if current_label.endswith(" 🔔") else current_label
        new_label = base + (" 🔔" if has_pending else "")
        if new_label != current_label:
            try:
                self.notebook.tab(self.ca_tab, text=new_label)
            except tk.TclError:
                pass

    @staticmethod
    def _is_position_onscreen(x, y, screen_w, screen_h, margin=40):
        """True si (x, y) — coin haut-gauche mémorisé de la fenêtre
        flottante — laisse au moins `margin` pixels de cette fenêtre
        accessibles sur UN écran de résolution (screen_w, screen_h).
        Volontairement approximatif (Tkinter n'expose pas nativement la
        géométrie de plusieurs écrans distincts) : le but n'est que
        d'éviter le cas grossier "fenêtre entièrement hors champ" après
        un changement d'écran/résolution (demande du 2026-09-09), pas de
        valider un multi-écran précis — un repli sur la position par
        défaut est de toute façon totalement inoffensif si ce test est
        trop prudent."""
        return -margin <= x <= screen_w - margin and -margin <= y <= screen_h - margin

    def _remote_device_popup_position(self, win):
        """(x, y) où placer la fenêtre flottante de demande de téléphone
        (demande du 2026-09-09) : la dernière position mémorisée par
        l'utilisateur (export_prefs — préférence PERSISTANTE, JAMAIS le
        fichier de session éphémère remote_control_auth.json, et aucun
        secret) si elle reste raisonnablement visible à l'écran actuel
        (voir _is_position_onscreen — se prémunit d'une ancienne
        position devenue hors écran après un changement de résolution/
        moniteur), sinon une position par défaut SOUS le bloc "Téléphones
        autorisés" de l'onglet "CA" (Changer.../Retirer la liaison/
        Révoquer — demande explicite du 2026-09-22, "réorganisation
        visuelle du contrôle à distance") plutôt qu'un coin fixe de la
        fenêtre principale."""
        x = export_prefs.load_value("remote_device_popup_x", None)
        y = export_prefs.load_value("remote_device_popup_y", None)
        if (
            isinstance(x, int) and isinstance(y, int)
            and self._is_position_onscreen(x, y, win.winfo_screenwidth(), win.winfo_screenheight())
        ):
            return x, y
        container = getattr(self, "remote_devices_container", None)
        if container is not None:
            try:
                if container.winfo_exists():
                    return (
                        container.winfo_rootx(),
                        container.winfo_rooty() + container.winfo_height() + 10,
                    )
            except tk.TclError:
                pass
        # Repli (conteneur pas encore construit, ou fenêtre en cours de
        # fermeture) : ancien calcul, jamais une exception.
        return (
            self.winfo_rootx() + max(self.winfo_width() - 300, 20),
            self.winfo_rooty() + 60,
        )

    def _save_remote_device_popup_position(self, x, y):
        """Mémorise la position de la fenêtre flottante (demande du
        2026-09-09) — export_prefs (préférence PERSISTANTE, survit à un
        redémarrage complet du logiciel), JAMAIS remote_control_auth.
        json (fichier de session ÉPHÉMÈRE, effacé à chaque nouvelle
        session — voir open_windows.py) : rien à voir avec le code/les
        jetons de la session, uniquement une coordonnée d'écran, aucun
        secret. Appelée à chaque déplacement (voir RemoteDeviceRequest
        Window._on_configure) et à la fermeture de la fenêtre."""
        export_prefs.save_value("remote_device_popup_x", x)
        export_prefs.save_value("remote_device_popup_y", y)

    def _refresh_remote_device_popup(self, pending=None):
        """Ouvre/repeuple/masque/ferme la fenêtre flottante de demande de
        téléphone (demande du 2026-09-09, abandon définitif de toute
        intégration dans une grille de réglages ; déplacée dans l'onglet
        "CA/LOG" le 2026-09-22, puis dans l'onglet "CA" seul le
        2026-09-24 lors de la séparation CA/LOG en CA + LOG — voir
        _build_ca_tab) : présente la PLUS ANCIENNE demande NON masquée
        par "Plus tard" (voir _remote_device_snoozed_keys) — jamais deux
        à la fois, jamais la même demande que la section "Téléphones"
        (voir _refresh_remote_devices_panel, approuvés uniquement).

        VISIBILITÉ (demande du 2026-09-09, correction : "la fenêtre ne
        doit apparaître au-dessus d'AUCUN autre onglet que celui qui
        héberge le contrôle à distance" — CA seul depuis le 2026-09-24,
        LOG n'est JAMAIS considéré comme cet onglet même si les deux
        étaient encore réunis au moment de cette demande initiale) —
        règle appliquée à CHAQUE appel, qu'il vienne du sondage
        périodique (~2s, voir App._tick/_check_remote_device_requests)
        ou du changement d'onglet lui-même (voir _on_notebook_tab_
        changed, <<NotebookTabChanged>>, pour un affichage/masquage
        IMMÉDIAT au clic, sans attendre le prochain sondage) : VISIBLE
        SI ET SEULEMENT SI l'onglet "CA" est actuellement sélectionné ET
        qu'il existe une telle demande. Sur tout AUTRE onglet (y compris
        "LOG"), la fenêtre déjà créée est seulement MASQUÉE (`withdraw`,
        jamais détruite ni recréée) — elle retrouve donc sa position
        EXACTE, sans le moindre recalcul, dès que CA redevient actif
        (`deiconify`) ; la demande elle-même n'est ni approuvée, ni
        révoquée, ni "Plus tard"-ée par ce simple changement d'onglet —
        seul le badge 🔔 (voir _update_ca_tab_badge, appelé séparément
        par l'appelant) reste alors le signal visible.

        Une fenêtre déjà EXISTANTE (visible ou masquée) est REPEUPLÉE en
        place pour la demande suivante plutôt que détruite/recréée
        (jamais de scintillement, jamais de repositionnement inutile) ;
        entièrement fermée seulement quand plus aucune demande n'est à
        montrer."""
        if pending is None:
            try:
                pending = open_windows.list_pending_remote_devices()
            except Exception:
                return
        candidates = sorted(
            (d for d in pending if (d["browser_id"], d["requested_at"]) not in self._remote_device_snoozed_keys),
            key=lambda d: d["requested_at"],
        )
        current = candidates[0] if candidates else None
        current_key = (current["browser_id"], current["requested_at"]) if current else None

        if current is None:
            self._remote_device_popup_current_key = None
            self._close_remote_device_popup()
            return

        ca_active = self._is_ca_tab_active()
        content_changed = current_key != self._remote_device_popup_current_key
        self._remote_device_popup_current_key = current_key
        self._remote_device_popup_current_browser_id = current["browser_id"]

        popup = self._remote_device_popup
        if not ca_active:
            # Jamais créer ni réafficher la fenêtre sur un autre onglet
            # que CA (y compris LOG) — seule une fenêtre déjà existante
            # peut avoir besoin d'être masquée ici (ex. l'utilisateur
            # vient de quitter CA pendant qu'elle était affichée).
            if popup is not None and popup.winfo_exists():
                try:
                    popup.withdraw()
                except tk.TclError:
                    pass
            return

        if popup is None or not popup.winfo_exists():
            popup = RemoteDeviceRequestWindow(
                self,
                on_approve=self._on_remote_device_popup_approve,
                on_refuse=self._on_remote_device_popup_refuse,
                on_later=self._on_remote_device_popup_later,
                on_geometry_changed=self._save_remote_device_popup_position,
            )
            self._remote_device_popup = popup
            x, y = self._remote_device_popup_position(popup)
            popup.geometry(f"+{x}+{y}")
            content_changed = True  # fenêtre neuve : il faut la peupler, même si `current_key` n'a en fait pas changé
        if content_changed:
            popup.show_request(
                short_id=current["short_id"],
                ip=current["ip_last_seen"],
                label_default=current.get("label") or current["short_id"],
            )
        try:
            popup.deiconify()
        except tk.TclError:
            pass

    def _close_remote_device_popup(self):
        popup = self._remote_device_popup
        self._remote_device_popup = None
        if popup is not None and popup.winfo_exists():
            try:
                self._save_remote_device_popup_position(popup.winfo_x(), popup.winfo_y())
            except tk.TclError:
                pass
            popup.destroy()

    def _on_remote_device_popup_approve(self):
        label = self._remote_device_popup.get_label() if self._remote_device_popup else None
        open_windows.approve_remote_device(self._remote_device_popup_current_browser_id, label=label)
        # Le nouvel appareil approuvé doit apparaître IMMÉDIATEMENT dans
        # "Téléphones" (voir _refresh_remote_devices_panel), pas
        # seulement au prochain sondage périodique.
        self._refresh_remote_devices_panel()
        self._refresh_remote_device_popup()

    def _on_remote_device_popup_refuse(self):
        open_windows.revoke_remote_device(self._remote_device_popup_current_browser_id)
        self._refresh_remote_device_popup()

    def _on_remote_device_popup_later(self):
        """"Plus tard" (bouton ET fermeture de la fenêtre via sa croix —
        voir RemoteDeviceRequestWindow — traitées IDENTIQUEMENT) : ferme
        UNIQUEMENT la fenêtre flottante pour CETTE demande — ne l'approuve
        ni ne la révoque (le téléphone reste "pending" côté serveur), et
        ne fait PAS disparaître le badge 🔔 de l'onglet (voir _update_
        ca_tab_badge, basé sur la liste "pending" complète, jamais
        filtrée par ce masquage). Redevient visible dès que CA
        est quitté PUIS rouvert (voir _is_ca_tab_active/_check_
        remote_device_requests, qui vide _remote_device_snoozed_keys à
        ce moment précis) — sans avoir besoin d'attendre une nouvelle
        tentative du téléphone (même browser_id/requested_at)."""
        if self._remote_device_popup_current_key is not None:
            self._remote_device_snoozed_keys.add(self._remote_device_popup_current_key)
        self._close_remote_device_popup()
        self._remote_device_popup_current_key = None
        self._refresh_remote_device_popup()

    def _check_remote_device_requests(self):
        """Sondage périodique (voir App._tick, appelé toutes les
        quelques secondes seulement, pas à chaque tick) du registre
        partagé des appareils (open_windows.list_pending_remote_devices/
        list_approved_remote_devices) — orchestre les trois éléments
        d'interface concernés par une demande de téléphone (demande du
        2026-09-09, retour définitif à une fenêtre flottante après
        abandon de toute intégration dans une grille de réglages ;
        bloc déplacé dans l'onglet "CA/LOG" le 2026-09-22, puis dans
        l'onglet "CA" seul le 2026-09-24 lors de la séparation CA/LOG en
        CA + LOG — l'onglet "LOG" n'est concerné par RIEN de ce qui
        suit) :

        1. la fenêtre flottante (voir _refresh_remote_device_popup), qui
           présente la plus ancienne demande NON masquée par "Plus
           tard" ;
        2. le badge 🔔 de l'onglet "CA" (voir _update_ca_tab_badge),
           reflet FIDÈLE de la liste "pending" complète — jamais affecté
           par "Plus tard" ;
        3. la liste "Téléphones" (appareils APPROUVÉS, voir _refresh_
           remote_devices_panel), reconstruite seulement si son contenu
           a changé (voir _remote_devices_signature).

        Détecte aussi la TRANSITION vers l'onglet "CA" (il était affiché
        autre chose au sondage précédent, il affiche CA maintenant) pour
        vider _remote_device_snoozed_keys À CE moment précis — jamais en
        continu tant que l'utilisateur y reste (voir _on_remote_device_
        popup_later) : revenir sur CA fait ainsi réapparaître une
        demande "Plus tard"-ée plus tôt, sans attendre une nouvelle
        tentative du téléphone."""
        ca_active_now = self._is_ca_tab_active()
        if ca_active_now and not self._last_ca_tab_active:
            self._remote_device_snoozed_keys.clear()
        self._last_ca_tab_active = ca_active_now

        try:
            pending = open_windows.list_pending_remote_devices()
            approved = open_windows.list_approved_remote_devices()
        except Exception:
            return

        self._update_ca_tab_badge(bool(pending))
        self._refresh_remote_device_popup(pending=pending)

        signature = self._remote_devices_signature(approved)
        if signature != getattr(self, "_last_remote_devices_panel_signature", None):
            self._refresh_remote_devices_panel(approved=approved)

        # Permissions DIRTO (Phase 3, 2026-09-20) : même sondage
        # périodique, pour suivre les changements du Répertoire (nouveaux
        # ADMIN/DIRTO...) — voir _refresh_remote_dirto_permissions_panel,
        # qui ne perturbe jamais une saisie en cours (values Combobox
        # rafraîchies sans toucher à la sélection, liste reconstruite
        # seulement si son contenu a réellement changé).
        self._refresh_remote_dirto_permissions_panel()

    def _poll_voice_queue(self):
        """Relève régulièrement les mots-clés (et éliminations décidées
        depuis la page "Éliminations" du contrôle à distance) déposés par
        remote_control.py, et les traite ici, sur le thread Tkinter — même
        principe périodique que _tick. S'arrête silencieusement de se
        reprogrammer si la fenêtre a été détruite entre-temps (fenêtre
        secondaire fermée, appli quittée)."""
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self.voice_command_queue.get_nowait()
                if isinstance(item, tuple) and item and item[0] == "eliminate":
                    _, eliminated_id, eliminator_id, request_id = item
                    self._remote_eliminate(eliminated_id, eliminator_id, request_id=request_id)
                elif isinstance(item, tuple) and item and item[0] == "rebalance_answer":
                    _, request_id, player_id = item
                    self._resolve_pending_rebalance(request_id, player_id, from_remote=True)
                elif isinstance(item, tuple) and item and item[0] == "confirm_move":
                    _, move_id, request_id = item
                    self._remote_confirm_move(move_id, request_id=request_id)
                elif isinstance(item, tuple) and item and item[0] == "end_tournament":
                    self._remote_end_tournament()
                    # La fenêtre vient d'être détruite (ou l'a déjà été,
                    # voir _remote_end_tournament_triggered) : n'importe
                    # quel élément suivant resterait dans la file, JAMAIS
                    # traité ici — self.db est fermé mais pas remis à
                    # None, donc les gardes "if not self.db" des autres
                    # gestionnaires (_on_voice_word, _remote_eliminate...)
                    # ne les arrêteraient pas et lèveraient
                    # sqlite3.ProgrammingError. Ne reprogramme pas non
                    # plus de nouveau passage : inutile, ce process se
                    # termine de toute façon.
                    return
                else:
                    self._on_voice_word(item)
        except queue.Empty:
            pass
        self.after(150, self._poll_voice_queue)

    def _remote_eliminate_request(self, eliminated_id, eliminator_id, client_request_id=None):
        """Point d'entrée appelé DIRECTEMENT depuis le thread HTTP du
        contrôle à distance (voir remote_control.py: on_eliminate, passé
        tel quel au serveur) — PAS depuis le thread Tk. Génère un
        request_id INTERNE (corrélation avec _remote_elimination_results,
        inchangé), dépose la demande dans voice_command_queue (traitée
        plus tard par _remote_eliminate, sur le thread Tk, comme toujours
        pour tout accès à self.db) puis ATTEND (sondage borné, ~150 ms de
        délai habituel de _poll_voice_queue) le résultat écrit dans
        self._remote_elimination_results par _remote_eliminate, pour le
        renvoyer tel quel au téléphone (demande du 2026-09-08 : un refus
        — ex. bounty PKO orpheline — doit être explicite côté téléphone,
        jamais silencieux). Renvoie toujours {"ok": bool, "message": str}
        ; un dépassement du délai (Tk anormalement bloqué) renvoie un
        message d'échec plutôt que de bloquer indéfiniment le thread HTTP
        (chaque requête téléphone a son propre thread, voir
        ThreadingHTTPServer : un dépassement ici n'affecte ni l'UI ni les
        autres téléphones).

        `client_request_id` (demande du 2026-09-19, "bétonner la
        communication téléphone <-> PC" suite à l'incident réel en club) :
        identifiant GÉNÉRÉ CÔTÉ TÉLÉPHONE pour CETTE tentative d'action
        (voir remote_control.py: _ELIMINATE_PAGE, confirmElimination),
        réutilisé TEL QUEL par le téléphone s'il rejoue la même requête
        après avoir perdu la réponse HTTP (timeout, coupure Wi-Fi...).
        Garantit l'IDEMPOTENCE côté SERVEUR (jamais seulement côté
        JavaScript, demande explicite) via self._remote_action_dedup
        ({client_request_id: {"ts": float, "result": dict|None}}) :
        - premier appel avec cet identifiant : traité normalement,
          résultat mémorisé une fois connu ;
        - appel(s) suivant(s) avec le MÊME identifiant, résultat déjà
          connu : renvoyé TEL QUEL, sans jamais réenfiler la moindre
          action dans voice_command_queue (donc sans second appel à
          Database.eliminate_player -> aucun second kill, aucun second
          transfert de bounty, aucun second mouvement de table) ;
        - appel(s) suivant(s) avec le MÊME identifiant mais résultat PAS
          ENCORE connu (la première tentative est encore en cours de
          traitement, cas d'un double-tap ou d'un retry très rapide) :
          attend ce même résultat au lieu d'en déclencher un second
          traitement — jamais deux passages dans voice_command_queue
          pour un seul identifiant.
        `client_request_id` absent (None) : comportement HISTORIQUE
        strictement inchangé, aucun dédoublonnage (jamais imposé à un
        appelant qui n'en fournit pas). Rétention limitée (voir
        _REMOTE_ACTION_DEDUP_TTL_SECONDS et _prune_remote_action_dedup) :
        un identifiant oublié après quelques minutes, largement suffisant
        pour couvrir un retry réseau réaliste, jamais une fuite mémoire
        sur une longue soirée."""
        entry = None
        if client_request_id:
            self._prune_remote_action_dedup()
            entry = self._remote_action_dedup.get(client_request_id)
            if entry is not None:
                if entry["result"] is not None:
                    return entry["result"]
                # Une tentative avec ce même identifiant est déjà en cours
                # de traitement ailleurs : attend SON résultat, ne déclenche
                # surtout pas un second passage dans voice_command_queue.
                try:
                    import remote_control as _remote_control_module
                    _remote_control_module.log_remote_event(
                        "duplicate_action_ignored", action="eliminate",
                        client_request_id=client_request_id,
                    )
                except Exception:
                    pass
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    if entry["result"] is not None:
                        return entry["result"]
                    time.sleep(0.03)
                return {"ok": False, "message": "Délai dépassé, réessayez."}
            entry = {"ts": time.time(), "result": None}
            self._remote_action_dedup[client_request_id] = entry

        request_id = uuid.uuid4().hex
        self.voice_command_queue.put(("eliminate", eliminated_id, eliminator_id, request_id))
        deadline = time.monotonic() + 3.0
        result = None
        while time.monotonic() < deadline:
            result = self._remote_elimination_results.pop(request_id, None)
            if result is not None:
                break
            time.sleep(0.03)
        if result is None:
            result = {"ok": False, "message": "Délai dépassé, réessayez."}
        if entry is not None:
            entry["result"] = result
            entry["ts"] = time.time()
        return result

    def _prune_remote_action_dedup(self):
        """Retire de self._remote_action_dedup les identifiants plus
        vieux que _REMOTE_ACTION_DEDUP_TTL_SECONDS — rétention limitée
        demandée explicitement : un retry réseau réaliste se produit en
        quelques secondes, jamais plusieurs minutes après coup ; purge à
        chaque nouvel appel plutôt qu'un minuteur séparé, pour rester
        aussi simple que le mécanisme déjà en place pour _remote_
        elimination_results (dict nu, jamais verrouillé, voir sa
        docstring)."""
        now = time.time()
        expired = [
            rid for rid, v in self._remote_action_dedup.items()
            if now - v["ts"] > _REMOTE_ACTION_DEDUP_TTL_SECONDS
        ]
        for rid in expired:
            del self._remote_action_dedup[rid]

    def _remote_confirm_move_request(self, move_id):
        """Point d'entrée appelé DIRECTEMENT depuis le thread HTTP du
        contrôle à distance (voir remote_control.py: on_confirm_move) —
        PAS depuis le thread Tk. Même principe que _remote_eliminate_
        request (demande du 2026-09-19) : dépose la demande dans voice_
        command_queue (traitée par _remote_confirm_move, sur le thread
        Tk), puis ATTEND (sondage borné, ~150 ms de délai habituel de
        _poll_voice_queue) le résultat écrit dans self._remote_move_
        confirm_results, pour le renvoyer tel quel au téléphone.

        Aucun dédoublonnage par client_request_id ici (contrairement à
        _remote_eliminate_request) : confirm_seat_move est idempotent PAR
        NATURE (un DELETE sur une ligne déjà supprimée ne fait rien) —
        rejouer la même confirmation ne peut jamais produire un second
        effet métier, donc rien à protéger de plus.

        Renvoie toujours {"ok": bool, "all_done": bool} : "all_done" est
        vrai si CE mouvement était le dernier en attente — dans ce cas,
        _remote_confirm_move a déjà déclenché _finish_movement_alert
        (exactement le mécanisme du bouton "Mouvements terminés", jamais
        une fin réimplémentée à part). Un dépassement du délai (Tk
        anormalement bloqué) renvoie un résultat d'échec plutôt que de
        bloquer indéfiniment le thread HTTP."""
        request_id = uuid.uuid4().hex
        self.voice_command_queue.put(("confirm_move", move_id, request_id))
        deadline = time.monotonic() + 3.0
        result = None
        while time.monotonic() < deadline:
            result = self._remote_move_confirm_results.pop(request_id, None)
            if result is not None:
                break
            time.sleep(0.03)
        if result is None:
            result = {"ok": False, "message": "Délai dépassé, réessayez.", "all_done": False}
        return result

    def _remote_confirm_move(self, move_id, request_id=None):
        """Traitement RÉEL d'une confirmation individuelle de mouvement
        (demande du 2026-09-19) — appelé UNIQUEMENT depuis _poll_voice_
        queue (thread Tk), jamais directement depuis le thread HTTP.

        confirm_seat_move ne fait que retirer la ligne visée de seat_
        moves : `players.table_id`/`seat` sont déjà, depuis le calcul du
        rééquilibrage, la position réelle et définitive du joueur — rien
        à "appliquer" ici (voir la docstring de Database.confirm_seat_
        move pour le diagnostic complet).

        Rafraîchissements IMMÉDIATS (jamais besoin d'un changement
        d'onglet ni d'attendre le tick suivant, demande explicite) :
        Joueurs et Plan des tables sont TOUJOURS rafraîchis explicitement
        ici (ni _refresh_all — qui ne rafraîchit que l'onglet ACTUELLEMENT
        affiché — ni _finish_movement_alert, plus bas, ne le font). S'il
        restait au moins un mouvement, Mouvements et Chronomètre sont
        aussi rafraîchis directement. S'il n'en restait aucun, c'est le
        mécanisme NORMAL de fin (_finish_movement_alert, identique au
        bouton "Mouvements terminés") qui s'en charge — jamais une fin
        réimplémentée à part — et qui rafraîchit déjà Mouvements/
        Chronomètre lui-même."""
        if not self.db:
            result = {"ok": False, "message": "Aucun tournoi ouvert.", "all_done": False}
        else:
            self.db.confirm_seat_move(move_id)
            remaining = self.db.count_seat_moves()
            self._refresh_remote_moves_cache()
            self._remote_has_pending_moves = remaining > 0
            if remaining == 0:
                self._finish_movement_alert()
            else:
                self._refresh_moves_tab()
                self._refresh_clock_tab()
            self._refresh_players_tab()
            self._refresh_tables_tab()
            result = {"ok": True, "all_done": remaining == 0}
        if request_id is not None:
            self._remote_move_confirm_results[request_id] = result

    def _remote_eliminate(self, eliminated_id, eliminator_id, request_id=None):
        """Élimination décidée depuis la page "Éliminations" du contrôle
        à distance (glisser un joueur éliminé sur son éliminateur, voir
        remote_control.py) : mêmes garde-fous et suites que
        _eliminate_selected (bounty, rééquilibrage, bandeau de mouvement,
        fin de partie), pour un résultat identique à une élimination faite
        directement dans l'onglet Joueurs. Appelé sur le thread Tk (voir
        _poll_voice_queue), jamais directement depuis le thread HTTP —
        voir _remote_eliminate_request, qui dépose la demande et attend
        le résultat écrit ici (self._remote_elimination_results) pour le
        renvoyer au téléphone."""
        def refuse(message):
            if request_id is not None:
                self._remote_elimination_results[request_id] = {"ok": False, "message": message}

        if not self.db:
            return refuse("Tournoi non disponible.")
        active = self.db.list_players(status="active")
        active_ids = {p["id"] for p in active}
        if eliminated_id not in active_ids or eliminated_id == eliminator_id:
            # état déjà changé entre-temps (ex : élimination concurrente) ou requête absurde
            return refuse("Ce joueur n'est plus disponible (état déjà modifié).")
        if len(active_ids) <= 1:
            return refuse("Impossible d'éliminer le dernier joueur actif (le vainqueur).")
        if eliminator_id is not None and eliminator_id not in active_ids:
            eliminator_id = None
        if eliminator_id is not None:
            # Un joueur ne peut éliminer que quelqu'un de SA PROPRE table
            # (voir aussi _ask_eliminator, même règle côté onglet
            # Joueurs) : la page "Gérer les éliminations" du téléphone ne
            # propose déjà que ça, mais on revalide ici au cas où (état
            # changé entre-temps, ex. mouvement de table concurrent).
            table_by_id = {p["id"]: p["table_id"] for p in active}
            if table_by_id.get(eliminator_id) != table_by_id.get(eliminated_id):
                eliminator_id = None
        if eliminator_id is None:
            # Éliminateur obligatoire UNIQUEMENT si une attribution de
            # bounty/PKO en dépend réellement (demande du 2026-09-09,
            # précisée après relecture utilisateur : primes activées ET
            # ce joueur précis porte une bounty > 0) — jamais "toujours
            # obligatoire", et INDÉPENDANT du Mode Test (qui ne facilite
            # que l'élimination GROUPÉE depuis l'onglet Joueurs, un
            # concept qui n'existe pas ici : le téléphone élimine toujours
            # UN seul joueur à la fois par glisser-déposer). Primes
            # désactivées (self.db.primes_enabled() coupe cette
            # contrainte) : aucune bounty n'étant de toute façon jamais
            # assignée dans ce cas (voir Database.add_player), rien ne
            # bloque ici. Dans l'usage normal du téléphone (glisser-
            # déposer, voir remote_control.py: confirmElimination), ce
            # cas (bounty en jeu mais sans éliminateur) ne se présente
            # qu'après une invalidation tardive (joueur/table qui ont
            # changé entre-temps, voir plus haut) : on refuse et on
            # invite à réessayer, plutôt que de procéder sans éliminateur
            # et de rendre la bounty orpheline.
            eliminated = self.db.get_player(eliminated_id)
            if self.db.primes_enabled() and eliminated and eliminated["bounty"] > 0:
                pko_mode = self.db.get_setting_int("pko_mode", 0) == 1
                message = (
                    "Élimination impossible : en mode PKO, vous devez "
                    "désigner le joueur qui a éliminé ce joueur."
                ) if pko_mode else (
                    "Élimination impossible : ce joueur porte une prime, "
                    "vous devez désigner qui l'a éliminé pour l'attribuer."
                )
                return refuse(message)
        moves = self.db.eliminate_player(eliminated_id, eliminated_by_id=eliminator_id)
        self._queue_elimination_banner(eliminated_id, eliminator_id)
        # _trigger_movement_alert/_finish_movement_alert AVANT _refresh_all
        # (et pas après, comme on pourrait s'y attendre) : c'est le premier
        # qui positionne movement_alert_active, dont dépend l'affichage
        # figé de l'onglet Joueurs pendant qu'un mouvement est en attente
        # (voir _refresh_players_tab) — dans l'autre ordre, ce premier
        # rafraîchissement afficherait déjà la nouvelle table/siège avant
        # même que l'alerte existe, et plus aucun rafraîchissement
        # ultérieur ne viendrait corriger l'affichage tant que l'onglet
        # Joueurs n'est pas quitté puis réaffiché.
        if len(self.db.list_players(status="active")) <= 1:
            if (self.db.get_setting_int("movement_alert_active", 0) == 1
                    or self.db.count_seat_moves() > 0):
                self._finish_movement_alert()
        elif moves:
            self._trigger_movement_alert(from_remote=True)
        self._refresh_all()
        self._refresh_remote_players_cache()
        self._refresh_remote_moves_cache()
        if request_id is not None:
            self._remote_elimination_results[request_id] = {"ok": True, "message": ""}
        # Contrairement à une élimination faite directement dans l'onglet
        # Joueurs (_eliminate_selected) : pas de self.lift()/focus_force()
        # sur la fenêtre PRINCIPALE ici. Une élimination décidée depuis le
        # téléphone veut justement dire que personne n'est devant le PC —
        # faire remonter la fenêtre principale ne servirait à rien et,
        # pire, viendrait recouvrir l'écran projecteur (potentiellement
        # plein écran) juste en dessous.
        #
        # On fait l'inverse : si l'écran projecteur est ouvert, on le
        # relève lui, explicitement, à chaque élimination à distance —
        # plutôt que de simplement "ne pas toucher à la pile de fenêtres"
        # (ce qui suppose qu'il était déjà au premier plan, hypothèse
        # fragile : une seule fenêtre peut avoir le focus à la fois sur un
        # Mac à un seul écran, et un rafraîchissement de la fenêtre
        # principale ailleurs entre-temps a pu la faire passer devant sans
        # qu'aucun code à nous ne le demande explicitement).
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()
        self._check_pending_rebalance()

    def _remote_get_roster_players(self):
        """TOUT le répertoire de joueurs habituels (roster.py), pas
        seulement les joueurs actifs dans le tournoi en cours — pour la
        page Photos du contrôle à distance (voir remote_control.py) :
        contrairement à l'onglet Joueurs, on peut vouloir photographier un
        joueur du club avant même qu'il ne soit inscrit ce soir-là.
        roster.py et player_photos.py ne touchent ni à self.db (SQLite) ni
        à Tkinter (comme _remote_upload_photo juste en dessous) : appelable
        sans danger directement depuis le thread du serveur web, sans
        passer par un cache tenu à jour depuis le thread principal."""
        return [
            {
                "name": e["name"],
                "club": e["club"],
                "has_photo": player_photos.get_photo_path(e["name"]) is not None,
            }
            for e in roster.load_roster_entries()
        ]

    def _remote_upload_photo(self, player_name, image_bytes):
        """Photo prise avec l'appareil photo du téléphone (page /photos du
        contrôle à distance, voir remote_control.py) et associée à un
        joueur du répertoire, identifié par NOM (le répertoire n'a pas
        d'id numérique comme les joueurs d'un tournoi — voir
        _remote_get_roster_players). Contrairement à _remote_eliminate :
        appelé directement depuis le thread du serveur web, SANS passer
        par voice_command_queue — sans danger ici, `player_photos` ne
        touche ni à self.db (SQLite) ni à Tkinter, seulement à de simples
        fichiers/JSON dans ~/.poker_tournament/photos/ (déjà conçu pour
        être utilisé indépendamment de l'appli, voir son docstring).
        Renvoie (succès: bool, message: str)."""
        name = (player_name or "").strip()
        if not name:
            return False, "Nom de joueur manquant."
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
                tmp.write(image_bytes)
                tmp_path = tmp.name
            player_photos.save_photo_from_file(name, tmp_path)
        except OSError as e:
            return False, f"Échec de l'enregistrement : {e}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        # Simple écriture d'attribut (thread-safe en pratique, voir
        # commentaire sur self._remote_clock_paused) : _tick, sur le
        # thread principal, la consomme pour rafraîchir la colonne Photo
        # sans attendre un changement d'onglet.
        self._remote_photo_uploaded = True
        return True, name

    def _remote_get_photo_image(self, player_name):
        """Miniature affichée à côté du nom sur la page Photos du contrôle
        à distance (voir remote_control.py) — simple lecture de fichier
        via player_photos, sans toucher à self.db ni Tkinter : appelable
        sans danger directement depuis le thread du serveur web, comme
        _remote_upload_photo. Renvoie (bytes, mime) ou (None, None) si ce
        joueur n'a pas de photo."""
        name = (player_name or "").strip()
        path = player_photos.get_photo_path(name) if name else None
        if not path:
            return None, None
        ext = os.path.splitext(path)[1].lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        try:
            with open(path, "rb") as f:
                return f.read(), mime
        except OSError:
            return None, None

    def _remote_delete_photo(self, player_name):
        """Supprime la photo de ce joueur (bouton 🗑 de la page Photos du
        contrôle à distance) — même remarque thread-safe que
        _remote_upload_photo. Renvoie (succès: bool, message: str)."""
        name = (player_name or "").strip()
        if not name:
            return False, "Nom de joueur manquant."
        player_photos.delete_photo(name)
        self._remote_photo_uploaded = True
        return True, name

    def _remote_end_tournament(self):
        """Bouton "Fin de la partie" (tout en bas de la page principale du
        contrôle à distance, voir remote_control.py) : ferme proprement
        CE tournoi-ci — ce processus n'en gère jamais qu'un seul à la
        fois — en réutilisant EXACTEMENT le même chemin que fermer la
        fenêtre ou "Fichier > Quitter" (_on_close : arrête le contrôle à
        distance, désinscrit ce tournoi du registre open_windows.py,
        ferme la base, puis détruit la fenêtre — jamais de
        os._exit()/kill() ni d'autre raccourci brutal). Ne peut jamais
        fermer un AUTRE tournoi : remote_control.py a déjà vérifié, avant
        même de déposer cette demande dans la file d'attente, que le pid
        envoyé par le téléphone correspond à CE processus précis (voir
        RemoteControlServer.start, route /end_tournament) — ce module-ci
        n'a donc besoin d'aucune vérification supplémentaire d'identité.

        Idempotent (voir _remote_end_tournament_triggered, initialisé à
        False dans __init__) : si cette fermeture a déjà été déclenchée
        — deux téléphones ayant confirmé presque simultanément, ou une
        confirmation en double du même téléphone, tous deux déjà déposés
        dans la file avant que le premier ne soit traité — les appels
        suivants sont ignorés sans effet, pour ne jamais appeler
        _on_close()/self.destroy() une seconde fois sur une fenêtre déjà
        détruite (ce qui lèverait une exception Tcl)."""
        if self._remote_end_tournament_triggered:
            return
        self._remote_end_tournament_triggered = True
        self._on_close()

    def _on_voice_word(self, word):
        """Dispatché pour chaque mot-clé reçu ("elimination"/"terminer"/
        "chronometre", depuis un raccourci clavier ou le contrôle à
        distance) : n'agit que si ce mot a un sens dans l'état courant du
        tournoi (ex : "chronomètre" ne relance pas le chrono tant qu'un
        mouvement de table est en attente — mais affiche quand même l'écran
        projecteur, ce n'est pas la même chose)."""
        if not self.db:
            return
        alert_active = self.db.get_setting_int("movement_alert_active", 0) == 1
        if word == "terminer":
            if alert_active:
                self._finish_movement_alert()
        elif word == "elimination":
            if not alert_active and not self.voice_awaiting_resume:
                self._voice_start_elimination()
        elif word == "chronometre":
            # Écourte un bandeau d'élimination actuellement affiché (voir
            # _advance_elimination_banner) : ne fait rien de plus si aucun
            # bandeau n'est affiché et que la file est vide (fonction
            # normale de "Chronomètre" ci-dessous inchangée dans tous les
            # cas) ; s'il en reste un dans la file, il devient le nouveau
            # bandeau courant avec sa propre durée complète et son son —
            # jamais toute la file d'un coup, un seul message à la fois.
            # AVANT le if/else ci-dessous (pas après) : ce dernier appelle
            # dans tous les cas _refresh_clock_tab() en aval
            # (_voice_resume_clock/_voice_show_clock), qui affichera donc
            # tout de suite le nouvel état, sans attendre le tick d'1
            # seconde — le tout depuis le même passage de
            # _poll_voice_queue (~150 ms), pour une réaction rapide.
            self._advance_elimination_banner()
            if self.voice_awaiting_resume:
                # Reprendre le chrono après une élimination : à éviter tant
                # qu'un mouvement de table est en attente (le responsable
                # doit d'abord fermer l'alerte avec "Terminé") — sinon le
                # chrono repartirait alors que des joueurs n'ont pas encore
                # changé de table. Même précaution tant qu'une question
                # "quel siège est grosse blinde ?" est encore SANS RÉPONSE
                # (voir database.py: pending_rebalance — source de vérité
                # unique, pas de second booléen redondant) : entre le
                # moment où l'élimination a révélé un besoin de
                # rééquilibrage et celui où quelqu'un y répond, le
                # mouvement lui-même n'a pas encore eu lieu — un mouvement
                # qui, une fois décidé, redeviendra visible via
                # movement_alert_active (voir _trigger_movement_alert,
                # appelé par _resolve_pending_rebalance) et bloquera alors
                # la reprise par le chemin habituel ci-dessous. Si la
                # question disparaît sans qu'aucun mouvement n'ait eu lieu
                # (écart de rééquilibrage résorbé entretemps), plus rien ne
                # bloque : pending_rebalance redevient None et ce même
                # raccourci relance normalement, exactement comme avant
                # l'apparition de cette question.
                if not alert_active and self.db.pending_rebalance is None:
                    self._voice_resume_clock()
            else:
                # Hors du contexte "reprendre après une élimination" :
                # sert simplement à ramener l'écran projecteur au premier
                # plan (utile s'il a été rapetissé/réduit pour faire autre
                # chose dans le logiciel entre-temps, ou pour le rouvrir à
                # distance après une élimination faite depuis le téléphone,
                # qui ne passe jamais par voice_awaiting_resume). Il n'y a
                # aucune raison de bloquer ça même si une alerte de
                # mouvement est active : au contraire, c'est justement là
                # que le responsable a besoin de voir l'écran projecteur.
                self._voice_show_clock()
        elif word == "tables":
            # Contrairement à "chronometre" : ici c'est bien la fenêtre
            # PRINCIPALE qu'on veut voir (l'onglet Tables n'existe que
            # là, pas sur l'écran projecteur), donc self.lift() a tout
            # son sens — pas de conflit avec un écran projecteur à
            # préserver au premier plan.
            self.notebook.select(self.tables_tab)
            self._refresh_tables_tab()
            self.lift()
            self.focus_force()
        elif word == "mouvements":
            self.notebook.select(self.moves_tab)
            self._refresh_moves_tab()
            self.lift()
            self.focus_force()
        elif word == "tables_zoom_moins":
            # Petits boutons Z-/Z+ de part et d'autre de "Plan des
            # tables" sur le téléphone : ajustent le même réglage de
            # zoom que les boutons "🔍− Zoom"/"🔍+ Zoom" de l'onglet
            # Tables (voir _tables_zoom_by) — discret, comme
            # "toggle_pause"/"niveau_suivant" (ne remonte aucune
            # fenêtre : on ajuste typiquement après avoir déjà affiché
            # "Plan des tables").
            self._tables_zoom_by(-self.TABLES_ZOOM_STEP)
        elif word == "tables_zoom_plus":
            self._tables_zoom_by(self.TABLES_ZOOM_STEP)
        elif word == "mouvements_bas":
            # Petites flèches ↓/↑ de part et d'autre de "Afficher
            # Mouvements" sur le téléphone (demande du 2026-09-20) :
            # déplacent UNIQUEMENT la position d'affichage du bandeau
            # "Changement de tables en cours" sur le Chrono Projo — même
            # mécanisme que le glisser souris (voir ClockWindow.nudge_
            # movement_alert_position/_movement_alert_manual_y), jamais
            # un second système de positionnement. Ne confirment, ne
            # suppriment et ne modifient JAMAIS un mouvement lui-même :
            # aucun accès à self.db ici, uniquement la fenêtre Chrono.
            # y augmente vers le bas dans Tk : "bas" = pas positif.
            if self.clock_window is not None and self.clock_window.winfo_exists():
                self.clock_window.nudge_movement_alert_position(
                    self.clock_window.MOVEMENT_ALERT_NUDGE_STEP_PX
                )
        elif word == "mouvements_haut":
            # Voir "mouvements_bas" ci-dessus — "haut" = pas négatif.
            if self.clock_window is not None and self.clock_window.winfo_exists():
                self.clock_window.nudge_movement_alert_position(
                    -self.clock_window.MOVEMENT_ALERT_NUDGE_STEP_PX
                )
        elif word == "toggle_pause":
            # Petit bouton ON/OFF à côté de "Chronomètre" sur le
            # téléphone : bascule directement pause/reprise, sans passer
            # par le contexte "reprendre après une élimination" (qui a sa
            # propre logique de garde-fous, voir plus haut) — un simple
            # aller-retour, jamais bloqué par une alerte de mouvement en
            # cours, ni ne remonte aucune fenêtre (pensé pour rester
            # discret).
            if self.db.get_setting_int("is_paused", 1) == 1:
                self._clock_resume()
            else:
                self._clock_pause()
            self._remote_clock_paused = self.db.get_setting_int("is_paused", 1) == 1
        elif word == "niveau_precedent":
            # Bouton "Niveau Précédent" du téléphone (demande du
            # 2026-09-11 : présent côté Mac depuis un moment, jamais
            # câblé côté contrôle à distance) : équivalent exact du
            # bouton du même nom dans l'onglet Chronomètre —
            # _clock_prev_level() réutilise _go_to_level(), donc la
            # MÊME logique centrale que "Niveau Suivant"/le tableau de
            # structure (bornage au niveau 1 inclus, redémarrage du
            # chrono à la durée pleine du niveau ciblé — pause ou pas —
            # et rafraîchissement immédiat de l'affichage Mac déjà
            # garantis par cette fonction, aucune logique dupliquée
            # ici). Discret, comme "toggle_pause"/"niveau_suivant" (ne
            # remonte aucune fenêtre).
            self._clock_prev_level()
        elif word == "niveau_suivant":
            # Bouton "Niveau Suivant" du téléphone : équivalent exact du
            # bouton du même nom dans l'onglet Chronomètre — discret,
            # comme "toggle_pause" (ne remonte aucune fenêtre).
            self._clock_next_level()

    def _voice_start_elimination(self):
        """Commande "Élimination" (raccourci clavier ou contrôle à
        distance) : met le chronomètre en pause (comme _clock_pause) et
        bascule l'onglet Joueurs au premier plan pour que le responsable
        élimine un joueur sans les mains. Si l'élimination déclenche un
        rééquilibrage, _trigger_movement_alert prend normalement le relais
        (bandeau + "Terminé") ; sinon, "Chronomètre" relance le chrono
        (voir _voice_resume_clock). Ramène aussi la fenêtre principale au
        premier plan (devant l'écran projecteur, potentiellement plein
        écran sur son propre moniteur) : sans ça, changer d'onglet ne
        suffit pas à le rendre visible si une autre fenêtre le recouvre
        encore."""
        if (self.db.get_setting_int("clock_started", 0) == 1
                and self.db.get_setting_int("is_paused", 1) == 0):
            start = self.db.get_setting_int("level_start_epoch", int(time.time()))
            elapsed = int(time.time()) - start
            self.db.set_settings({"is_paused": 1, "paused_accum_seconds": elapsed})
        self.voice_awaiting_resume = True
        self.notebook.select(self.players_tab)
        self._refresh_players_tab()
        self._refresh_clock_tab()
        self.lift()
        self.focus_force()

    def _voice_resume_clock(self):
        """Commande "Chronomètre" (raccourci clavier ou contrôle à
        distance) : relance le chrono après une élimination (déclenchée
        par "Élimination") qui n'a causé aucun mouvement de table (sinon
        c'est "Terminé" qui s'en charge, voir _finish_movement_alert), et
        ramène l'onglet Chronomètre + l'écran projecteur au premier plan
        (l'ouvre s'il n'existait pas encore — voir _open_clock_window)."""
        self.voice_awaiting_resume = False
        self._clock_resume()
        self.notebook.select(self.clock_tab)
        self._refresh_clock_tab()
        self._open_clock_window(fullscreen=True)

    def _voice_show_clock(self):
        """Commande "Chronomètre" (raccourci clavier Ctrl+Maj+C ou contrôle
        à distance) hors du contexte "reprendre après une élimination"
        (voir _on_voice_word) : ramène simplement l'onglet Chronomètre et
        l'écran projecteur au premier plan (l'ouvre s'il n'existait pas
        encore — voir _open_clock_window), en restaurant son plein écran
        s'il l'était avant d'être rapetissé pour faire autre chose dans le
        logiciel — sans toucher à l'état pause/lecture du chrono ni à quoi
        que ce soit d'autre.

        PAS de self.lift() ici sur la fenêtre PRINCIPALE (contrairement à
        avant) : ça annulait aussitôt le travail de _open_clock_window
        juste au-dessus en faisant repasser la fenêtre principale devant
        l'écran projecteur qu'on vient tout juste de ramener au premier
        plan — d'où le symptôme rapporté "le chrono apparaît une fraction
        de seconde puis disparaît" en utilisant le bouton "Chronomètre"
        du contrôle à distance."""
        self.notebook.select(self.clock_tab)
        self._refresh_clock_tab()
        self._open_clock_window(fullscreen=True)

    def _update_tournament_started_buttons(self):
        """Ajuste 'Supprimer' et 'Éliminer' (onglet Joueurs) selon que le
        tournoi a démarré ou non (chronomètre déjà lancé au moins une
        fois, voir clock_started) — sens opposés :

        - 'Supprimer' se grise UNE FOIS le tournoi démarré : l'effacer en
          cours de partie fausserait l'historique (mouvements, 'éliminé
          par'...) sans recours possible — 'Désactiver (forfait)' garde
          une trace, à utiliser à la place.
        - 'Éliminer' se grise TANT QUE le tournoi n'a pas démarré : rien
          à éliminer avant que la partie ait commencé.

        Lu à chaque rafraîchissement (pas mis en cache) : les deux se
        remettent d'eux-mêmes dans leur état initial pour un nouveau
        tournoi, où clock_started repart à 0."""
        started = self.db.get_setting_int("clock_started", 0) == 1

        self.delete_player_btn.configure(state="disabled" if started else "normal")
        self.delete_player_btn_tooltip.text = (
            "Suppression désactivée : le tournoi a déjà démarré. Utilisez\n"
            "plutôt « Désactiver (forfait) », qui garde une trace du\n"
            "joueur au lieu de l'effacer."
        ) if started else ""

        self.eliminate_player_btn.configure(state="normal" if started else "disabled")
        self.eliminate_player_btn_tooltip.text = (
            self._eliminate_btn_tooltip_normal_text if started else
            "Élimination désactivée : le tournoi n'a pas encore démarré\n"
            "(cliquez « Démarrer » dans l'onglet Chronomètre)."
        )

    def _update_undo_elimination_button_state(self):
        """Grise "Annule Eliminer" (demande du 2026-09-16, timeout ajouté
        le 2026-09-17) s'il n'y a actuellement AUCUN joueur éliminé, ou
        si le délai configuré ("Timeout pour Annuler Eliminer", onglet
        Paramètres) est dépassé — ou réglé à 0 (désactivation complète,
        jamais "illimité") — voir Database.undo_last_elimination_
        available, SEULE source de vérité pour cette condition (jamais
        dupliquée ici). Reste actif si un joueur éliminé existe et que le
        délai n'est pas dépassé, MÊME si l'instantané mémorisé s'avère
        ensuite incompatible pour une autre raison (voir Database.undo_
        last_elimination) : ce cas, plus rare, est signalé par un message
        clair au clic plutôt que par un bouton grisé de façon préventive.

        Jamais mis en cache : relu à chaque rafraîchissement de l'onglet
        Joueurs, à chaque changement du réglage Timeout (voir _save_undo_
        elimination_timeout_minutes), ET une fois par seconde tant que
        l'onglet Joueurs est affiché (voir _tick) — pour que le bouton se
        grise de lui-même dans la seconde qui suit l'expiration du délai,
        sans attendre un rafraîchissement déclenché par autre chose."""
        available = self.db.undo_last_elimination_available()
        self.undo_elimination_btn.configure(state="normal" if available else "disabled")

    def _pending_old_seat_by_name(self):
        """Tant qu'un mouvement de tables est en attente (bandeau
        "Changement de tables en cours" affiché, avant que le responsable
        confirme avec "Terminé"), renvoie {nom_joueur: (ancienne_table,
        ancien_siège)} pour les joueurs concernés — la base contient déjà
        la nouvelle affectation (calculée par rebalance_tables, utilisée
        pour dire où installer les joueurs une fois qu'ils se sont
        levés), mais les onglets Joueurs ET Tables doivent continuer
        d'afficher l'ancien emplacement le temps de l'alerte, sans quoi
        on donnerait l'impression que le joueur a déjà changé de place
        alors qu'il ne s'est pas encore levé. Voir seat_moves (onglet
        Mouvements), qui garde justement l'ancienne et la nouvelle valeur
        le temps de l'alerte — _finish_movement_alert ("Terminé") vide
        seat_moves, ce qui fait alors réapparaître partout la valeur
        réelle (déjà en base depuis le début, donc rien à changer côté
        données). Renvoie un dict vide si aucune alerte n'est active."""
        if self.db.get_setting_int("movement_alert_active", 0) != 1:
            return {}
        return {
            m["player_name"]: (m["old_table_name"], m["old_seat"])
            for m in self.db.get_seat_moves()
        }

    def _refresh_players_tab(self):
        # Garde la liste déroulante des clubs à jour (un club a pu être
        # ajouté/modifié entre-temps depuis le répertoire de joueurs).
        self.new_player_club_combo.configure(values=roster.list_clubs())
        self._update_tournament_started_buttons()
        self._update_undo_elimination_button_state()
        for row in self.players_tree.get_children():
            self.players_tree.delete(row)
        tables = {t["id"]: t["name"] for t in self.db.list_tables(active_only=False)}
        present_ids = set()

        pending_old_by_name = self._pending_old_seat_by_name()

        status_labels = {"active": "Actif", "withdrawn": "Forfait", "eliminated": "Éliminé"}
        players = [dict(p) for p in self.db.list_players()]
        n_active = sum(1 for p in players if p["status"] == "active")
        for p in players:
            p["status_label"] = status_labels.get(p["status"], p["status"])
            p["table_name"] = tables.get(p["table_id"], "-") if p["table_id"] else "-"
            if p["name"] in pending_old_by_name:
                old_table_name, old_seat = pending_old_by_name[p["name"]]
                p["table_name"] = old_table_name or "-"
                p["seat"] = old_seat
            # Rang final : celui d'un joueur éliminé (voir eliminate_player),
            # 1 pour le vainqueur (seul joueur encore actif, tournoi
            # terminé), et non déterminé (« - ») pour les autres joueurs
            # encore actifs tant que le tournoi est en cours.
            if p["status"] == "active":
                p["rang"] = 1 if n_active == 1 else None
            else:
                p["rang"] = p["place"]

        sort_col = self.players_sort["column"]
        if sort_col == "name":
            players.sort(key=lambda p: p["name"].lower())
        elif sort_col == "club":
            players.sort(key=lambda p: (p["club"] or "").lower())
        elif sort_col == "status":
            players.sort(key=lambda p: p["status_label"].lower())
        elif sort_col == "table":
            players.sort(key=lambda p: (p["table_name"].lower(), p["seat"] or 0))
        elif sort_col == "rang":
            # Le vainqueur (rang 1) et les joueurs encore actifs en cours de
            # tournoi (rang indéterminé) sont classés en tête, cohérent avec
            # un classement "du meilleur au moins bon".
            players.sort(key=lambda p: p["rang"] or 1)
        elif sort_col == "elim_time":
            players.sort(key=lambda p: p["elim_time"] or "")
        elif sort_col == "eliminated_by":
            players.sort(key=lambda p: (p["eliminated_by_name"] or "").lower())
        elif sort_col == "sel":
            # Tri par la colonne des cases à cocher (Mode Test, demande du
            # 2026-09-14, voir _on_players_checkbox_header_click) :
            # `ascending` vaut ici "cochés d'abord" (True) / "décochés
            # d'abord" (False), jamais un sens croissant/décroissant
            # classique. Deux tris successifs, en s'appuyant sur la
            # stabilité garantie de list.sort() (Python) : le tri
            # alphabétique posé en premier reste l'ordre interne de
            # chaque groupe une fois le second tri (par groupe
            # coché/décoché) appliqué par-dessus — sans lui, un simple
            # sort() par groupe seul laisserait les joueurs d'un même
            # groupe dans un ordre non déterminé. N'écrit jamais dans
            # self.checked_player_ids : seul l'ORDRE d'affichage change.
            players.sort(key=lambda p: p["name"].lower())
            checked_first = self.players_sort["ascending"]
            players.sort(
                key=lambda p: (p["id"] not in self.checked_player_ids) if checked_first
                else (p["id"] in self.checked_player_ids)
            )
        # La colonne "sel" gère elle-même son sens ci-dessus (groupe
        # coché/décoché en premier) : lui appliquer EN PLUS ce reverse()
        # global inverserait aussi l'ordre alphabétique interne de
        # chaque groupe (Z->A), ce qui n'est pas le comportement demandé.
        if sort_col and sort_col != "sel" and not self.players_sort["ascending"]:
            players.reverse()
        self._update_sort_headings()

        self.player_photo_images = {}
        for idx, p in enumerate(players):
            present_ids.add(p["id"])
            table_name = p["table_name"]
            status = p["status_label"]
            mark = self.CHECKBOX_CHECKED if p["id"] in self.checked_player_ids else self.CHECKBOX_UNCHECKED
            row_tag = "evenrow" if idx % 2 == 0 else "oddrow"
            if p["status"] == "active":
                tags = (row_tag,)
            elif p["status"] == "withdrawn":
                tags = (row_tag, "withdrawn")
            else:
                tags = (row_tag, "eliminated")
            photo_path = player_photos.get_photo_path(p["name"])
            photo = load_thumbnail(photo_path, PLAYER_THUMB_SIZE) if photo_path else None
            if photo is not None:
                self.player_photo_images[p["id"]] = photo  # garde une référence
            bounty_txt = f"{p['bounty']:,} pts".replace(",", " ") if p["bounty"] else "-"
            self.players_tree.insert(
                "", "end", iid=str(p["id"]),
                image=photo if photo is not None else "",
                values=(
                    mark, p["id"], p["name"], p["club"] or "-", table_name, p["seat"] or "-",
                    f"{p['chips']:,}".replace(",", " "),
                    p["buyin_count"], p["rebuy_count"], p["addon_count"],
                    bounty_txt, status, p["rang"] or "-",
                    format_datetime_fr(p["elim_time"]) or "-", p["elim_round"] or "-", p["eliminated_by_name"] or "-",
                ),
                tags=tags,
            )
        self.players_tree.tag_configure("evenrow", background=CREAM)
        self.players_tree.tag_configure("oddrow", background=CREAM_ALT)
        self.players_tree.tag_configure("eliminated", foreground="#8a7d63")
        self.players_tree.tag_configure("withdrawn", foreground="#b05c2e")
        # on oublie les joueurs cochés qui n'existent plus (ex : supprimés)
        self.checked_player_ids &= present_ids
        self._update_checked_count_label()
        stats = self.db.get_stats()
        # Entrées affichées ici = buy-ins des joueurs encore réellement
        # dans le tournoi (actifs + éliminés), désistements exclus — pas
        # stats["entries"] (tous les buy-ins jamais encaissés, y compris
        # les désistés : sert au calcul du prize pool ailleurs dans
        # l'appli, où un forfait ne doit pas faire baisser la cagnotte
        # déjà collectée). Ici, dans l'onglet Joueurs, on veut plutôt
        # "combien de joueurs sont dans ce tournoi en ce moment" : un
        # joueur désactivé (forfait) n'en fait plus partie.
        entries_current = sum(p["buyin_count"] for p in players if p["status"] != "withdrawn")
        self.stats_lbl.config(
            text=f"Actifs : {stats['active_count']}  |  Entrées : {entries_current}"
        )

    # ---------------------------------------------------------------
    # Onglet Tables
    # ---------------------------------------------------------------
    # Vitesse du défilement automatique de l'onglet Tables (utilisé
    # seulement quand le contenu dépasse la hauteur visible) : 2 pixels
    # toutes les 45 ms, soit environ 44 px/s — assez lent pour rester
    # lisible sur un écran de vidéoprojecteur.
    TABLES_AUTOSCROLL_STEP_PX = 2
    TABLES_AUTOSCROLL_INTERVAL_MS = 45
    TABLES_AUTOSCROLL_PAUSE_MS = 2500  # pause en haut et en bas avant de reboucler

    # Zoom des cartes de table (taille du texte affiché) : mémorisé comme
    # préférence partagée (comme Nom du Club), pas propre à un tournoi —
    # pratique pour ajuster une bonne fois la lisibilité selon l'écran
    # utilisé (ex : projeté pour les joueurs) sans avoir à recommencer à
    # chaque tournoi.
    TABLES_ZOOM_MIN = 0.8
    TABLES_ZOOM_MAX = 3.0
    TABLES_ZOOM_STEP = 0.2

    def _warn_if_table_integrity_issue(self):
        """Avertit (une fois, à l'ouverture) si ce fichier .tournoi
        contient déjà une table en surcapacité (voir Database.
        check_table_integrity — lecture seule) — typiquement un ancien
        fichier créé avant le correctif de l'architecture de
        rééquilibrage du 2026-09-10. Ne modifie RIEN, ne déplace
        personne : demande explicite "détection + avertissement
        seulement, aucune réparation automatique pour l'instant"."""
        if not self.db:
            return
        problems = self.db.check_table_integrity()
        if not problems:
            return
        lines = [
            f"{p['table_name']} : {p['occupation']} joueurs pour {p['max_seats']} sièges"
            for p in problems
        ]
        messagebox.showwarning(
            "Tables en surcapacité détectées",
            "Ce fichier de tournoi contient au moins une table dont "
            "l'occupation dépasse sa capacité configurée :\n\n"
            + "\n".join(lines) + "\n\n"
            "Cet état n'a PAS été corrigé automatiquement. Un "
            "rééquilibrage manuel (bouton \"Rééquilibrer les tables\", "
            "onglet Tables) peut être nécessaire.",
        )

    def _build_tables_tab(self):
        self._tables_zoom = export_prefs.load_value("tables_zoom", 1.0)

        top = ttk.Frame(self.tables_tab)
        top.pack(fill="x", padx=10, pady=10)
        rebalance_btn = ttk.Button(top, text="Rééquilibrer les tables", command=self._rebalance)
        rebalance_btn.pack(side="left", padx=3)
        Tooltip(
            rebalance_btn,
            "Redistribue les joueurs actifs pour équilibrer le nombre de\n"
            "joueurs par table (utile après des éliminations). Se fait\n"
            "aussi automatiquement à chaque élimination.",
        )

        ttk.Button(
            top, text="🔍− Zoom", width=9, command=lambda: self._tables_zoom_by(-self.TABLES_ZOOM_STEP),
        ).pack(side="left", padx=(15, 3))
        ttk.Button(
            top, text="🔍+ Zoom", width=9, command=lambda: self._tables_zoom_by(self.TABLES_ZOOM_STEP),
        ).pack(side="left", padx=3)

        # -- Indication discrète "rééquilibrage en attente du joueur à
        # déplacer" (PHASE 3 de l'architecture validée le 2026-09-10, voir
        # database.py: pending_rebalance) : SEUL affichage Mac de cette
        # attente (aucune fenêtre intrusive, voir _check_pending_
        # rebalance) — un simple libellé + un bouton "Continuer sans
        # désigner le joueur" équivalent au choix déjà disponible sur les
        # téléphones (remote_control.py: _REBALANCE_WIDGET), pour que le
        # responsable puisse trancher CE mouvement précis depuis le Mac
        # sans devoir décocher la préférence globale d'équilibrage guidé
        # (contrairement à _on_bb_rebalance_prompt_toggle). Caché par
        # défaut ; affiché/masqué par _update_pending_rebalance_badge,
        # appelée depuis _check_pending_rebalance (donc à chaque tick et
        # après toute action de rééquilibrage).
        self._pending_rebalance_frame = ttk.Frame(top)
        self._pending_rebalance_label = ttk.Label(
            self._pending_rebalance_frame, text="", foreground="#8a6d00",
        )
        self._pending_rebalance_label.pack(side="left", padx=(0, 6))
        pending_rebalance_continue_btn = ttk.Button(
            self._pending_rebalance_frame, text="Continuer sans désigner le joueur",
            command=self._continue_pending_rebalance_without_bb,
        )
        pending_rebalance_continue_btn.pack(side="left")
        Tooltip(
            pending_rebalance_continue_btn,
            "Résout ce rééquilibrage précis sans attendre de réponse d'un\n"
            "téléphone, exactement comme si \"Continuer sans désigner le\n"
            "joueur\" avait été répondu sur un téléphone du contrôle à\n"
            "distance. La préférence \"Équilibrage guidé par UTG\" n'est\n"
            "pas modifiée : le prochain rééquilibrage posera de nouveau\n"
            "la question normalement.",
        )
        self._pending_rebalance_frame.pack_forget()

        # -- Total de joueurs actuellement répartis dans les tables
        # (demande du 2026-09-10, amélioration d'affichage uniquement —
        # aucun changement de la logique des tables/du rééquilibrage) :
        # discret, à l'opposé (droite) des boutons d'action de ce même
        # bandeau. Mis à jour par _refresh_tables_tab, à partir du même
        # décompte que celui utilisé pour le titre de chaque table (voir
        # plus bas) — garantit que ce total correspond toujours
        # exactement à la somme des nombres affichés par table.
        # Demande du 2026-09-16 (amélioration de visibilité uniquement,
        # observée lors du test réel sur le HP) : libellé blanc + nombre
        # en gros caractères, à la place du petit texte discret d'avant
        # ("Total : ..." en ttk.Label, taille de police par défaut du
        # thème — non personnalisable par instance, voir la remarque
        # équivalente plus bas sur tk.LabelFrame/tk.Label pour le zoom
        # des tables). AUCUN changement du calcul ni de la logique :
        # toujours `total_players`/_format_players_count, alimenté par
        # _refresh_tables_tab exactement comme avant (voir plus bas,
        # `if hasattr(self, "_tables_total_label")`). bg=FELT : même
        # couleur que le fond réel de `top` (ttk.Frame, style "TFrame"
        # réglé sur FELT — voir _setup_style), pour éviter tout patch de
        # couleur visible autour d'un tk.Label brut. Position INCHANGÉE
        # (toujours à droite de ce même bandeau, voir top.pack ci-dessus)
        # : la disposition de l'onglet Tables n'est pas bouleversée.
        self._tables_total_label = tk.Label(
            top, text="", font=("Helvetica", 16, "bold"), bg=FELT, fg=GOLD,
        )
        self._tables_total_label.pack(side="right", padx=(3, 0))
        tk.Label(
            top, text="Nombre de joueurs :", font=("Helvetica", 14, "bold"),
            bg=FELT, fg="white",
        ).pack(side="right", padx=(6, 0))

        # -- Avertissement "mouvements en attente" (demande du 2026-09-16,
        # suite au diagnostic confirmé sur le HP du club — voir
        # _pending_old_seat_by_name) : tant que movement_alert_active=1,
        # l'onglet Tables affiche VOLONTAIREMENT les anciennes positions
        # des joueurs concernés par un mouvement pas encore confirmé
        # (comportement métier conservé, voir _refresh_tables_tab) — sans
        # ce bandeau, rien ne l'indiquait sur CET onglet précis (seuls
        # Chronomètre/l'écran projecteur montraient "Changement de tables
        # en cours"), ce qui donnait à tort l'impression d'une base de
        # données désynchronisée. Construit une seule fois ici ; montré/
        # masqué par _refresh_tables_tab (jamais reconstruit, contrairement
        # aux cadres de table eux-mêmes). `before=scroll_container` (pas
        # un simple .pack() qui l'ajouterait à la fin de l'ordre
        # d'empilement actuel) garantit sa position juste au-dessus de la
        # grille de tables à CHAQUE réaffichage, quel que soit l'historique
        # pack()/pack_forget() précédent.
        self._movement_pending_frame = ttk.Frame(self.tables_tab)
        self._movement_pending_label = ttk.Label(
            self._movement_pending_frame,
            text="⚠ Mouvements de tables en attente — positions avant déplacement affichées",
            font=("Helvetica", 11, "bold"), foreground=DANGER_RED,
        )
        self._movement_pending_label.pack(side="left", padx=(0, 10))
        movement_pending_finish_btn = ttk.Button(
            self._movement_pending_frame, text="Terminé",
            # switch_to_clock=False (voir _finish_movement_alert) : reste
            # sur l'onglet Tables pour y montrer tout de suite les
            # positions réelles, plutôt que de basculer sur Chronomètre
            # comme le bouton "Terminé" de l'onglet Mouvements — même
            # mécanisme de validation sous-jacent, aucune logique dupliquée.
            command=self._finish_movement_alert_from_tables,
            style="Danger.TButton",
        )
        movement_pending_finish_btn.pack(side="left")
        Tooltip(
            movement_pending_finish_btn,
            "Exactement le même bouton que dans l'onglet Mouvements : à\n"
            "cliquer une fois que tous les joueurs déplacés ont rejoint\n"
            "leur nouvelle table. Referme cet avertissement et affiche\n"
            "aussitôt les positions réelles ci-dessous.",
        )

        # Clignotement des tables concernées par un mouvement en attente
        # (voir _tables_blink_tick) : UN SEUL callback after() à la fois,
        # quel que soit le nombre de rafraîchissements successifs de cet
        # onglet (_refresh_tables_tab annule systématiquement l'ancien
        # avant d'en programmer un nouveau).
        self._tables_blink_after_id = None
        self._tables_blink_phase = False
        self._tables_blink_ids = set()

        scroll_container = ttk.Frame(self.tables_tab)
        scroll_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._tables_scroll_container = scroll_container

        # Zone défilante : un Canvas (seul widget dont on peut piloter le
        # défilement par programme) contenant un Frame avec les tables en
        # grille. Sans cette zone, les tables au-delà de la capacité
        # d'affichage de l'écran seraient simplement invisibles.
        self.tables_canvas = tk.Canvas(scroll_container, bg=FELT, highlightthickness=0)
        tables_scrollbar = ttk.Scrollbar(
            scroll_container, orient="vertical", command=self.tables_canvas.yview
        )
        # yscrollincrement=1 : chaque "unit" de yview_scroll vaut 1 pixel,
        # ce qui permet un défilement automatique fluide (voir
        # _tables_autoscroll_tick) plutôt que par grands blocs.
        self.tables_canvas.configure(yscrollcommand=tables_scrollbar.set, yscrollincrement=1)
        self.tables_canvas.pack(side="left", fill="both", expand=True)
        tables_scrollbar.pack(side="right", fill="y")

        self.tables_inner = ttk.Frame(self.tables_canvas)
        self._tables_inner_window = self.tables_canvas.create_window(
            (0, 0), window=self.tables_inner, anchor="nw"
        )
        self.tables_inner.bind(
            "<Configure>",
            lambda e: self.tables_canvas.configure(scrollregion=self.tables_canvas.bbox("all")),
        )
        self.tables_canvas.bind(
            "<Configure>",
            lambda e: self.tables_canvas.itemconfigure(self._tables_inner_window, width=e.width),
        )
        # Molette de souris, pour un défilement manuel en complément du
        # défilement automatique (macOS/Windows puis Linux).
        self.tables_canvas.bind("<MouseWheel>", self._on_tables_mousewheel)
        self.tables_canvas.bind("<Button-4>", lambda e: self.tables_canvas.yview_scroll(-40, "units"))
        self.tables_canvas.bind("<Button-5>", lambda e: self.tables_canvas.yview_scroll(40, "units"))

        self._tables_scroll_after_id = None
        self._tables_scroll_paused = False
        self._tables_autoscroll_tick()

    def _tables_zoom_by(self, delta):
        new_zoom = round(min(self.TABLES_ZOOM_MAX, max(self.TABLES_ZOOM_MIN, self._tables_zoom + delta)), 2)
        if new_zoom == self._tables_zoom:
            return
        self._tables_zoom = new_zoom
        export_prefs.save_value("tables_zoom", new_zoom)
        self._refresh_tables_tab()

    def _on_tables_mousewheel(self, event):
        # event.delta : multiples de 120 sous Windows, valeur brute (~1-3)
        # sous macOS — dans les deux cas, on veut quelques dizaines de
        # pixels par cran de molette.
        self.tables_canvas.yview_scroll(int(-event.delta / 120 * 40) or (-40 if event.delta > 0 else 40), "units")

    def _rebalance(self):
        moves = self.db.rebalance_tables()
        # _trigger_movement_alert avant _refresh_all : voir la remarque
        # dans _eliminate_selected/_remote_eliminate — sinon l'onglet
        # Joueurs, si c'est lui l'onglet affiché, montrerait la nouvelle
        # table/siège avant même que l'alerte n'existe.
        if moves:
            self._trigger_movement_alert()
        self._refresh_all()
        self._check_pending_rebalance()

    def _refresh_tables_tab(self):
        # Annule tout cycle de clignotement en cours AVANT de détruire les
        # cadres qu'il visait (voir _tables_blink_tick) — sans ça, un
        # rafraîchissement de plus en plus rapproché (éliminations
        # successives) programmerait un nouveau callback after() à chaque
        # appel sans jamais annuler le précédent, les faisant s'accumuler.
        self._cancel_tables_blink()

        for w in self.tables_inner.winfo_children():
            w.destroy()

        # Comme l'onglet Joueurs (voir _pending_old_seat_by_name) : tant
        # qu'un mouvement de tables est en attente, un joueur concerné
        # reste affiché à son ANCIENNE table/siège ici aussi, pas à la
        # nouvelle déjà enregistrée en base.
        pending_old_by_name = self._pending_old_seat_by_name()
        all_tables_by_id = {t["id"]: t for t in self.db.list_tables(active_only=False)}
        name_to_id = {t["name"]: t["id"] for t in all_tables_by_id.values()}
        active_ids = {t["id"] for t in self.db.list_tables()}

        # Avertissement + tables à faire clignoter (voir _build_tables_tab
        # et _tables_blink_tick) : déterminés à partir des mouvements
        # RÉELLEMENT en attente (seat_moves), pas de `pending_old_by_name`
        # (qui ne liste que les JOUEURS encore actifs concernés — une
        # table de départ peut être concernée par le mouvement même si
        # tous les joueurs qui l'occupaient ont depuis été éliminés).
        movement_alert = self.db.get_setting_int("movement_alert_active", 0) == 1
        blinking_ids = set()
        if movement_alert:
            for mv in self.db.get_seat_moves():
                old_id = name_to_id.get(mv["old_table_name"])
                new_id = name_to_id.get(mv["new_table_name"])
                if old_id is not None:
                    blinking_ids.add(old_id)
                if new_id is not None:
                    blinking_ids.add(new_id)
        if movement_alert:
            self._movement_pending_frame.pack(
                fill="x", padx=10, pady=(0, 8), before=self._tables_scroll_container,
            )
        else:
            self._movement_pending_frame.pack_forget()

        players_by_table = {}
        for p in self.db.list_players(status="active"):
            p = dict(p)
            table_id, seat = p["table_id"], p["seat"]
            if p["name"] in pending_old_by_name:
                old_table_name, old_seat = pending_old_by_name[p["name"]]
                old_table_id = name_to_id.get(old_table_name)
                if old_table_id is not None:
                    table_id, seat = old_table_id, old_seat
            p["_display_seat"] = seat
            players_by_table.setdefault(table_id, []).append(p)

        # Tables à afficher : les actives, plus — pendant l'alerte
        # seulement — celles (même déjà fermées par la consolidation qui a
        # causé ce mouvement) où un joueur reste affiché "gelé" à son
        # ancienne place ; sans ça, sa table d'avant, si elle vient de
        # fermer dans ce même rééquilibrage, n'aurait plus de cadre où
        # l'afficher tant que "Terminé" n'a pas été cliqué.
        table_ids_to_show = active_ids | set(players_by_table.keys())
        tables = sorted(
            (all_tables_by_id[tid] for tid in table_ids_to_show if tid in all_tables_by_id),
            key=lambda t: t["id"],
        )

        # Tailles/espacements proportionnels au zoom (voir Zoom+/Zoom-
        # ci-dessus) — tk.LabelFrame/tk.Label plutôt que leurs équivalents
        # ttk : ces derniers ne permettent pas de changer la taille de
        # police par instance (seulement via un style global partagé par
        # tous les widgets du thème).
        zoom = self._tables_zoom
        title_font = ("Helvetica", round(11 * zoom), "bold")
        row_font = ("Helvetica", round(11 * zoom))
        grid_pad = max(4, round(8 * zoom))
        row_padx = max(6, round(10 * zoom))
        row_pady = max(1, round(2 * zoom))

        cols = 3
        total_players = 0
        for idx, t in enumerate(tables):
            plist = sorted(players_by_table.get(t["id"], []), key=lambda p: p["_display_seat"] or 0)
            # Nombre de joueurs affiché dans le titre (demande du
            # 2026-09-10, amélioration d'affichage uniquement) : compté
            # sur `plist`, PAS sur une nouvelle requête à la base — c'est
            # exactement la liste déjà utilisée ci-dessous pour peupler
            # cette table (y compris le "gel" sur l'ancienne table/siège
            # pendant une alerte de mouvement en attente, voir
            # pending_old_by_name plus haut), donc ce nombre correspond
            # TOUJOURS exactement aux sièges effectivement listés dans le
            # cadre, jamais en avance ou en retard d'un rafraîchissement.
            total_players += len(plist)
            frame = tk.LabelFrame(
                self.tables_inner, text=f"{t['name']} — {_format_players_count(len(plist))}",
                font=title_font,
                bg=FELT, fg=GOLD, bd=1, relief="groove", highlightbackground=GOLD_DARK,
            )
            # Attribut Python ordinaire posé sur le widget (pas une option
            # Tk) : simple étiquette relue par _tables_blink_tick pour
            # savoir, à CHAQUE bascule, quels cadres actuellement affichés
            # doivent clignoter — jamais de référence directe au widget
            # gardée ailleurs, qui deviendrait invalide au prochain
            # _refresh_tables_tab (destruction/reconstruction complète).
            frame._blink_table_id = t["id"]
            frame.grid(row=idx // cols, column=idx % cols, padx=grid_pad, pady=grid_pad, sticky="n")
            if not plist:
                tk.Label(frame, text="(vide)", font=row_font, bg=FELT, fg=CREAM).pack(
                    padx=row_padx, pady=row_pady + 4
                )
            for p in plist:
                tk.Label(
                    frame, text=f"Siège {p['_display_seat']} — {p['name']}",
                    font=row_font, bg=FELT, fg=CREAM,
                ).pack(anchor="w", padx=row_padx, pady=row_pady)

        if hasattr(self, "_tables_total_label"):
            # Le libellé "Nombre de joueurs :" (voir sa construction plus
            # haut) précède déjà ce nombre — jamais répété ici, seul le
            # compte lui-même (inchangé : _format_players_count(total_
            # players), exactement comme avant cette demande).
            self._tables_total_label.configure(text=_format_players_count(total_players))

        # Repart du haut à chaque rafraîchissement (rééquilibrage,
        # élimination...) plutôt que de rester sur une position de
        # défilement qui ne correspond plus forcément au même contenu.
        self.tables_canvas.update_idletasks()
        self.tables_canvas.configure(scrollregion=self.tables_canvas.bbox("all"))
        self.tables_canvas.yview_moveto(0.0)
        self._tables_scroll_paused = False

        # (Re)démarre le clignotement des tables concernées, le cas
        # échéant — toujours EN DERNIER, une fois les nouveaux cadres
        # construits et tagués ci-dessus (voir frame._blink_table_id).
        # Repart d'une phase "éteinte" à chaque reconstruction plutôt que
        # de garder l'ancienne : un rafraîchissement enchaîné ne doit
        # jamais laisser un cadre bloqué en phase "allumée" par accident.
        self._tables_blink_ids = blinking_ids
        self._tables_blink_phase = False
        if movement_alert and blinking_ids:
            self._tables_blink_tick()

    def _cancel_tables_blink(self):
        """Annule proprement le cycle de clignotement de l'onglet Tables
        (voir _tables_blink_tick), s'il y en a un en cours — appelé avant
        toute reconstruction des cadres (_refresh_tables_tab) et à la
        fermeture du tournoi (_cleanup_for_close), pour ne jamais laisser
        un callback after() orphelin viser des widgets déjà détruits."""
        after_id = getattr(self, "_tables_blink_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
            self._tables_blink_after_id = None

    def _tables_blink_tick(self):
        """Fait alterner l'apparence (fond/en-tête, JAMAIS les noms des
        joueurs — toujours affichés sur leur propre fond FELT distinct,
        voir _refresh_tables_tab) des cadres de table concernés par un
        mouvement en attente, entre la couleur d'alerte (DANGER_RED) et
        l'apparence normale (FELT/GOLD), toutes les 600 ms. UN SEUL
        cycle actif à la fois (voir _tables_blink_after_id/
        _cancel_tables_blink, systématiquement annulé avant d'en
        programmer un nouveau). S'arrête de lui-même (sans se
        reprogrammer) dès que la fenêtre/les widgets concernés n'existent
        plus, ou que plus aucun mouvement n'est en attente — jamais
        d'erreur Tkinter après destruction, jamais de boucle qui persiste
        inutilement une fois "Terminé" cliqué."""
        self._tables_blink_after_id = None
        if not self.winfo_exists() or not self.tables_inner.winfo_exists():
            return
        if self.db is None or self.db.get_setting_int("movement_alert_active", 0) != 1:
            return
        ids = getattr(self, "_tables_blink_ids", set())
        if not ids:
            return
        self._tables_blink_phase = not self._tables_blink_phase
        on = self._tables_blink_phase
        for frame in self.tables_inner.winfo_children():
            if getattr(frame, "_blink_table_id", None) not in ids:
                continue
            try:
                if on:
                    frame.configure(
                        bg=DANGER_RED, fg=CREAM,
                        highlightthickness=3, highlightbackground=DANGER_RED_ACTIVE,
                    )
                else:
                    frame.configure(
                        bg=FELT, fg=GOLD,
                        highlightthickness=3, highlightbackground=DANGER_RED_ACTIVE,
                    )
            except tk.TclError:
                pass
        self._tables_blink_after_id = self.after(600, self._tables_blink_tick)

    def _finish_movement_alert_from_tables(self):
        """Bouton "Terminé" de l'onglet Tables (voir _build_tables_tab) :
        délègue entièrement à _finish_movement_alert (switch_to_clock=
        False, voir sa docstring — aucune logique de validation dupliquée
        ici), puis rafraîchit immédiatement CET onglet pour y montrer sans
        attendre les positions réelles (arrêt du clignotement compris,
        via le _cancel_tables_blink en tête de _refresh_tables_tab)."""
        self._finish_movement_alert(switch_to_clock=False)
        self._refresh_tables_tab()

    def _tables_autoscroll_tick(self):
        """Boucle de défilement automatique et lent de l'onglet Tables,
        active seulement quand le nombre de tables dépasse la capacité
        d'affichage à l'écran (sinon rien ne défile)."""
        if not self.winfo_exists() or not self.tables_canvas.winfo_exists():
            return

        # N'anime que si l'onglet Tables est actuellement affiché, pour ne
        # pas défiler inutilement en arrière-plan pendant que l'utilisateur
        # est sur un autre onglet.
        if self.notebook.tab(self.notebook.select(), "text") != "Tables":
            self._tables_scroll_after_id = self.after(500, self._tables_autoscroll_tick)
            return

        bbox = self.tables_canvas.bbox("all")
        visible_height = self.tables_canvas.winfo_height()
        content_height = (bbox[3] - bbox[1]) if bbox else 0

        if not self._tables_scroll_paused and content_height > visible_height:
            top_frac, bottom_frac = self.tables_canvas.yview()
            if bottom_frac >= 1.0:
                # Arrivé en bas : pause de lecture puis retour en boucle en haut.
                self._tables_scroll_paused = True
                self.tables_canvas.yview_moveto(0.0)
                self.after(self.TABLES_AUTOSCROLL_PAUSE_MS, self._tables_resume_autoscroll)
            else:
                self.tables_canvas.yview_scroll(self.TABLES_AUTOSCROLL_STEP_PX, "units")

        self._tables_scroll_after_id = self.after(
            self.TABLES_AUTOSCROLL_INTERVAL_MS, self._tables_autoscroll_tick
        )

    def _tables_resume_autoscroll(self):
        self._tables_scroll_paused = False

    # ---------------------------------------------------------------
    # Onglet Mouvements (historique des changements de table/siège)
    # ---------------------------------------------------------------
    def _build_moves_tab(self):
        top = ttk.Frame(self.moves_tab)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Label(
            top,
            text="Historique des déplacements de joueurs suite aux rééquilibrages de tables.",
        ).pack(side="left")
        finish_btn = ttk.Button(
            top, text="Terminé", command=self._finish_movement_alert, style="Danger.TButton",
        )
        finish_btn.pack(side="right", padx=3)
        Tooltip(
            finish_btn,
            "À cliquer une fois que tous les joueurs déplacés ont rejoint\n"
            "leur nouvelle table : referme le bandeau \"Changement de tables\n"
            "en cours\" et relance le chronomètre.",
        )
        print_filled_btn = ttk.Button(
            top, text="🖨️ Imprimer", command=self._print_movement_slips_filled,
        )
        print_filled_btn.pack(side="right", padx=3)
        Tooltip(
            print_filled_btn,
            "Imprime un coupon par joueur pour les mouvements réellement\n"
            "affichés ci-dessous (nom du tournoi, nom du joueur, ancienne/\n"
            "nouvelle table et siège déjà remplis) — à découper et remettre\n"
            "directement, sans rien écrire à la main. Pratique quand\n"
            "beaucoup de joueurs sont concernés à la fois.",
        )
        print_blank_btn = ttk.Button(
            top, text="🖨️ Imprimer Vierge", command=self._print_movement_slips,
        )
        print_blank_btn.pack(side="right", padx=3)
        Tooltip(
            print_blank_btn,
            "Imprime une feuille de coupons vierges (nom du tournoi, nom\n"
            "du joueur, ancienne/nouvelle table et siège) à remplir à la\n"
            "main, découper et remettre directement au joueur concerné —\n"
            "plus rapide et plus discret que d'annoncer les mouvements à\n"
            "voix haute.",
        )

        # Colonne "Raison" (demande du 2026-09-10, architecture de
        # rééquilibrage) : l'utilisateur doit pouvoir comprendre POURQUOI
        # un joueur a été déplacé (contrainte de capacité, fusion de
        # table, équilibrage automatique, ou choix guidé par la grosse
        # blinde) — voir database.py: MOVE_REASON_LABELS.
        cols = ("time", "player", "old_table", "old_seat", "new_table", "new_seat", "reason")
        headers = [
            "Heure", "Joueur", "Ancienne table", "Ancien siège", "Nouvelle table",
            "Nouveau siège", "Raison",
        ]
        self.moves_tree = ttk.Treeview(self.moves_tab, columns=cols, show="headings", height=20)
        for c, h in zip(cols, headers):
            self.moves_tree.heading(c, text=h)
            self.moves_tree.column(c, width=130, anchor="center")
        self.moves_tree.column("player", width=180, anchor="w")
        self.moves_tree.column("reason", width=170, anchor="w")
        self.moves_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _refresh_moves_tab(self):
        for row in self.moves_tree.get_children():
            self.moves_tree.delete(row)
        highlight_minutes = self.db.get_setting_int("highlight_duration_minutes", 5)
        cutoff = datetime.now() - timedelta(minutes=highlight_minutes)
        for idx, m in enumerate(self.db.get_seat_moves()):
            recent = False
            if highlight_minutes > 0:
                try:
                    moved_at_dt = datetime.strptime(m["moved_at"], "%Y-%m-%d %H:%M:%S")
                    recent = moved_at_dt >= cutoff
                except (ValueError, TypeError):
                    recent = False
            if recent:
                row_tag = "recent"
            else:
                row_tag = "evenrow" if idx % 2 == 0 else "oddrow"
            # m["reason"] : absent ("" via ALTER TABLE ... DEFAULT '')
            # pour tout mouvement archivé avant l'ajout de cette colonne
            # (voir database.py: _migrate) -> repli générique "Équilibrage"
            # via MOVE_REASON_LABELS.get, jamais une raison inventée.
            reason_label = MOVE_REASON_LABELS.get(m["reason"] or "", MOVE_REASON_LABELS[""])
            self.moves_tree.insert(
                "", "end",
                values=(
                    format_datetime_fr(m["moved_at"]),
                    m["player_name"],
                    m["old_table_name"] or "—",
                    m["old_seat"] or "—",
                    m["new_table_name"] or "—",
                    m["new_seat"] or "—",
                    reason_label,
                ),
                tags=(row_tag,),
            )
        self.moves_tree.tag_configure("evenrow", background=CREAM)
        self.moves_tree.tag_configure("oddrow", background=CREAM_ALT)
        self.moves_tree.tag_configure("recent", background=GOLD, foreground=TEXT_DARK)

    # ---------------------------------------------------------------
    # Onglet Primes (bounty / PKO)
    # ---------------------------------------------------------------
    def _build_bounty_tab(self):
        self.primes_sort = {"column": "total", "ascending": False}

        top = ttk.Frame(self.bounty_tab)
        top.pack(fill="x", padx=10, pady=10)
        self.bounty_info_lbl = ttk.Label(top, text="", font=("Helvetica", 10, "bold"))
        self.bounty_info_lbl.pack(side="left")
        ttk.Button(
            top, text="Exporter les primes (Excel/CSV)...", command=self._export_primes,
        ).pack(side="right", padx=3)

        # Récapitulatif et Historique dans un PanedWindow (au lieu de deux
        # blocs empilés de taille fixe) : une poignée entre les deux permet
        # de faire glisser la frontière pour agrandir l'un ou l'autre.
        panes = ttk.PanedWindow(self.bounty_tab, orient="vertical")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        summary = ttk.LabelFrame(panes, text="Récapitulatif des primes (en points)")
        panes.add(summary, weight=2)
        cols1 = ("name", "rang", "presence", "assiduite", "cl_montant",
                  "bo_nombre", "bo_valeur", "bo_montant", "total")
        headers1 = ["Joueur", "Rang", "Présence", "Assiduité", "Classement",
                    "Nb Bounty", "Val Bounty", "Mon Bounty", "TOTAL"]
        self.primes_tree = ttk.Treeview(summary, columns=cols1, show="headings", height=14)
        for c, h in zip(cols1, headers1):
            self.primes_tree.heading(c, text=h)
            width = 150 if c == "name" else 95
            self.primes_tree.column(c, width=width, anchor="w" if c == "name" else "center")
        # Tri par clic sur en-tête : Rang, Nb Bounty, TOTAL (les autres
        # colonnes ne sont pas des critères de tri pertinents à eux seuls).
        for c in ("rang", "bo_nombre", "total"):
            self.primes_tree.heading(c, command=lambda col=c: self._sort_primes_by(col))
        self.primes_tree.pack(fill="both", expand=True, padx=6, pady=6)
        self.primes_tree.tag_configure(
            "totalcol", font=("Helvetica", 9, "bold"), background=GOLD, foreground=TEXT_DARK,
        )
        # bo_valeur (en-tête ET tooltip) est mutable selon le mode
        # classique/PKO — voir _refresh_bounty_tab, qui met à jour ces 2
        # textes dynamiquement à chaque rafraîchissement (self.primes_tree
        # .heading + self.primes_heading_tooltip.column_texts["bo_valeur"]).
        # Les valeurs ci-dessous sont celles du mode CLASSIQUE (par défaut).
        self.primes_heading_tooltip = TreeHeadingTooltip(self.primes_tree, {
            "name": "Nom du joueur.",
            "presence": "Prime de présence : points pour avoir participé à ce\ntournoi (réglage Paramètres, 0 = désactivée).",
            "assiduite": "Prime d'assiduité : points si le joueur était déjà présent\naux N derniers tournois consécutifs (réglages Paramètres).",
            "rang": "Place finale du joueur (1 = vainqueur, un chiffre plus élevé\n= éliminé plus tôt). Vide tant que le joueur est encore en jeu.",
            "cl_montant": "Prime de classement : réglage manuel (Paramètres) s'il est\nnon nul, sinon 100×√N / P (N = nb de joueurs, P = Rang).",
            "bo_nombre": "Nombre : nombre de joueurs qu'il a éliminés (kills) — jamais\nmodifié par la récupération de sa propre bounty finale (PKO).",
            "bo_valeur": "Valeur : points par bounty — réglage manuel s'il est\nnon nul, sinon 10×√N (N = nombre de joueurs du tournoi).",
            "bo_montant": "Montant = Nombre × Valeur.",
            "total": "Somme de toutes les primes du joueur pour ce tournoi\n(Présence + Assiduité + Classement + Montant Bounty).",
        })

        history = ttk.LabelFrame(
            panes, text="Historique du bounty progressif (mécanisme PKO interne)"
        )
        panes.add(history, weight=1)
        cols3 = ("time", "eliminated", "eliminator", "amount", "grow")
        headers3 = ["Heure", "Joueur éliminé", "Éliminé par", "Points gagnés", "Ajouté à sa prime"]
        self.bounty_history_tree = ttk.Treeview(history, columns=cols3, show="headings", height=8)
        for c, h in zip(cols3, headers3):
            self.bounty_history_tree.heading(c, text=h)
            self.bounty_history_tree.column(c, width=130, anchor="center")
        self.bounty_history_tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _sort_primes_by(self, column):
        """Tri par clic sur un en-tête (Rang / Nb Bounty / TOTAL) : ré-
        appuyer sur le même en-tête inverse l'ordre."""
        if self.primes_sort["column"] == column:
            self.primes_sort["ascending"] = not self.primes_sort["ascending"]
        else:
            self.primes_sort["column"] = column
            self.primes_sort["ascending"] = True
        self._refresh_bounty_tab()

    def _update_primes_sort_headings(self):
        base_headers = {"rang": "Rang", "bo_nombre": "Nb Bounty", "total": "TOTAL"}
        for col, label in base_headers.items():
            if self.primes_sort["column"] == col:
                arrow = " ▲" if self.primes_sort["ascending"] else " ▼"
                self.primes_tree.heading(col, text=label + arrow)
            else:
                self.primes_tree.heading(col, text=label)

    def _export_primes(self):
        if not self.db:
            return
        PrimesExportDialog(self, self.db, sort_state=self.primes_sort)

    def _refresh_bounty_tab(self):
        n_players = self.db.get_stats()["total_players_ever"]
        pko_mode = self.db.get_setting_int("pko_mode", 0) == 1
        bounty_flat = self.db.get_setting_int("bounty_amount", 0)
        bounty_val = bounty_unit_value(n_players, bounty_flat)
        mode_txt = "PKO (prime progressive)" if pko_mode else "Bounty classique"
        self.bounty_info_lbl.config(
            text=(f"{n_players} joueur(s)  |  {mode_txt}  |  Valeur d'un bounty : "
                  f"{bounty_val:,} pts".replace(",", " "))
        )

        # bo_valeur : "Val Bounty" (classique, valeur fixe) devient
        # "Moy Bounty" en PKO (Mon Bounty ÷ Nb Bounty, arrondi — voir
        # Database.get_bounty_bonuses/primes_columns, même bascule que
        # dans les exports) — en-tête ET tooltip mis à jour ensemble.
        self.primes_tree.heading("bo_valeur", text="Moy Bounty" if pko_mode else "Val Bounty")
        self.primes_heading_tooltip.column_texts["bo_valeur"] = (
            "Moyenne : Mon Bounty ÷ Nb Bounty, arrondie (mode PKO) —\n"
            "'-' si Nb Bounty = 0 (rien à diviser)."
            if pko_mode else
            "Valeur : points par bounty — réglage manuel s'il est\n"
            "non nul, sinon 10×√N (N = nombre de joueurs du tournoi)."
        )

        self._update_primes_sort_headings()
        for row in self.primes_tree.get_children():
            self.primes_tree.delete(row)
        primes_rows = self.db.get_primes_summary(
            sort_column=self.primes_sort["column"], ascending=self.primes_sort["ascending"]
        )

        # Ligne de total (en gras, voir tag "totalcol" ci-dessous), tout en
        # haut du tableau — insérée AVANT la boucle des joueurs pour rester
        # au-dessus quel que soit le tri en cours (celui-ci ne s'applique
        # qu'à primes_rows, jamais à cette ligne). Seules "Mon Bounty" et
        # "TOTAL" ont un total demandé ; les autres colonnes restent vides
        # sur cette ligne (Nb/Val Bounty n'ont pas de somme pertinente,
        # Rang encore moins).
        #
        # Primes désactivées (demande du 2026-09-09, précisée le même
        # jour) : le tableau doit être RÉELLEMENT vide, pas seulement
        # dépourvu de lignes joueurs — cette ligne TOTAL (même à 0 pts)
        # ne doit donc plus apparaître du tout dans ce cas. primes_rows
        # est déjà [] ici (voir Database.get_primes_summary), mais on ne
        # s'appuie pas sur "primes_rows vide" pour décider (un tournoi
        # sans aucun joueur inscrit, primes activées, aurait aussi
        # primes_rows == [] et doit, lui, continuer à afficher la ligne
        # TOTAL à 0 pts comme avant) : c'est bien l'état de la case à
        # cocher qui décide, jamais une conséquence indirecte.
        if self.db.primes_enabled():
            self.primes_tree.insert(
                "", "end",
                values=(
                    "TOTAL", "", "", "", "", "", "",
                    f"{sum(r['bo_montant'] for r in primes_rows):,} pts".replace(",", " "),
                    f"{sum(r['total'] for r in primes_rows):,} pts".replace(",", " "),
                ),
                tags=("totalcol",),
            )

        for idx, r in enumerate(primes_rows):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.primes_tree.insert(
                "", "end",
                values=(
                    r["name"],
                    r["rang"] if r["rang"] is not None else "-",
                    f"{r['presence']:,} pts".replace(",", " ") if r["presence"] else "-",
                    f"{r['assiduite']:,} pts".replace(",", " ") if r["assiduite"] else "-",
                    f"{r['cl_montant']:,} pts".replace(",", " ") if r["cl_montant"] else "-",
                    r["bo_nombre"] if r["bo_nombre"] else "-",
                    f"{r['bo_valeur']:,} pts".replace(",", " ") if r["bo_valeur"] else "-",
                    f"{r['bo_montant']:,} pts".replace(",", " ") if r["bo_montant"] else "-",
                    f"{r['total']:,} pts".replace(",", " "),
                ),
                tags=(tag,),
            )
        self.primes_tree.tag_configure("evenrow", background=CREAM)
        self.primes_tree.tag_configure("oddrow", background=CREAM_ALT)

        for row in self.bounty_history_tree.get_children():
            self.bounty_history_tree.delete(row)
        for idx, e in enumerate(self.db.get_bounty_events()):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            # event_type='victory_collect' (voir Database.
            # _close_out_winner_bounty) : la récupération de sa propre
            # bounty finale par le vainqueur, PAS une élimination
            # supplémentaire — marquée distinctement (🏆) pour ne jamais
            # être confondue avec une vraie élimination dans cet
            # historique. eliminator_name reste NULL pour ces lignes,
            # donc déjà affiché "—" ci-dessous, sans traitement à part.
            eliminated_display = (
                f"🏆 {e['eliminated_name']} (bounty finale)"
                if e["event_type"] == "victory_collect" else e["eliminated_name"]
            )
            self.bounty_history_tree.insert(
                "", "end",
                values=(
                    format_datetime_fr(e["event_time"]), eliminated_display, e["eliminator_name"] or "—",
                    f"{e['amount_won']:,} pts".replace(",", " "),
                    f"{e['added_to_eliminator_bounty']:,} pts".replace(",", " ")
                    if e["added_to_eliminator_bounty"] else "—",
                ),
                tags=(tag,),
            )
        self.bounty_history_tree.tag_configure("evenrow", background=CREAM)
        self.bounty_history_tree.tag_configure("oddrow", background=CREAM_ALT)

    # ---------------------------------------------------------------
    # Onglet Chronomètre
    # ---------------------------------------------------------------
    def _build_clock_tab(self):
        frame = self.clock_tab
        # Bandeau d'alerte "Changement de tables en cours" : positionné en
        # overlay (place(), pas pack()) par-dessus le reste de l'onglet
        # (directement sur `frame`, pas dans le conteneur défilable
        # ci-dessous, pour rester visible quelle que soit la position de
        # défilement), affiché/masqué en clignotant depuis
        # _refresh_clock_tab tant que movement_alert_active est actif
        # (voir _trigger_movement_alert / _finish_movement_alert, onglet
        # Mouvements).
        self.movement_alert_lbl = tk.Label(
            frame, text="⚠  Changement de tables en cours  ⚠",
            font=("Helvetica", 20, "bold"), bg=DANGER_RED, fg="white",
            relief="solid", borderwidth=3, padx=24, pady=16,
        )

        # Bandeau d'élimination — version simplifiée (texte seul, sans les
        # photos : pas assez de place dans cet onglet) de celui de l'écran
        # projecteur (voir ClockWindow, "elimination_banner_frame") : même
        # état déjà calculé une seule fois dans _refresh_clock_tab
        # (self._elimination_banner_current), affiché/masqué ici en plus,
        # pas une deuxième file/logique. Positionné plus haut (rely=0.3)
        # que le bandeau de mouvement ci-dessus (rely=0.42) pour ne
        # jamais se superposer si les deux sont actifs en même temps.
        self.elimination_banner_lbl = tk.Label(
            frame, text="", font=("Helvetica", 18, "bold"),
            bg=ELIMINATION_BLUE, fg="white",
            relief="solid", borderwidth=3, padx=24, pady=14, justify="center",
        )

        # Conteneur défilable : les 5 boutons + la structure de blindes +
        # les sons peuvent dépasser la hauteur visible sur un petit écran.
        canvas = tk.Canvas(frame, bg=FELT, highlightthickness=0)
        vscroll = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        vscroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width)
        )

        is_mac = self.tk.call("tk", "windowingsystem") == "aqua"

        def _on_mousewheel(event):
            if is_mac:
                canvas.yview_scroll(int(-1 * event.delta), "units")
            else:
                canvas.yview_scroll(int(-1 * event.delta / 120), "units")

        # La molette ne fait défiler cet onglet que lorsque le curseur est
        # dessus, pour ne pas perturber le défilement des autres onglets.
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Ligne du haut : les 5 boutons d'action en haut à droite (empilés),
        # les infos du niveau en cours (niveau/minuteur/blindes/suivant) à
        # gauche dans l'espace restant. "controls" est empaqueté AVANT
        # "info_col" (qui a expand=True) pour que les boutons réservent
        # bien leur place à droite en premier — sinon info_col capterait
        # tout l'espace et ne laisserait rien pour eux.
        top_row = ttk.Frame(inner)
        top_row.pack(fill="x")

        # Espacements resserrés dans toute cette rangée du haut (boutons +
        # infos de niveau) : sur un écran Windows pas très haut, ce bloc
        # pouvait à lui seul remplir tout l'espace visible, obligeant à
        # défiler beaucoup (même avec l'ascenseur) pour seulement
        # apercevoir "Structure de blindes" en dessous. Les tailles de
        # police, elles, restent grandes exprès (lisibles depuis loin,
        # écran projecteur) — seuls les espaces autour sont réduits.
        controls = ttk.Frame(top_row)
        controls.pack(side="right", anchor="ne", padx=20, pady=8)
        ttk.Button(controls, text="Démarrer / Reprendre", command=self._clock_resume).pack(fill="x", pady=2)
        ttk.Button(controls, text="Pause", command=self._clock_pause).pack(fill="x", pady=2)
        ttk.Button(controls, text="Niveau précédent", command=self._clock_prev_level).pack(fill="x", pady=2)
        ttk.Button(controls, text="Niveau suivant", command=self._clock_next_level).pack(fill="x", pady=2)
        ttk.Button(controls, text="Ouvrir l'écran projecteur", command=self._open_clock_window).pack(fill="x", pady=2)

        info_col = ttk.Frame(top_row)
        info_col.pack(side="left", fill="both", expand=True)

        # "Niveau" (numéro brut, pauses comprises — cohérent avec le
        # tableau de structure et les boutons Niveau précédent/suivant
        # ci-dessus) et "Round" côte à côte : le round, lui, n'avance pas
        # pendant une pause (voir Database.get_round_number et
        # _refresh_clock_tab) — les afficher tous les deux évite toute
        # ambiguïté entre les deux numérotations.
        level_row_frame = ttk.Frame(info_col)
        level_row_frame.pack(pady=(8, 2))

        self.level_display = ttk.Label(level_row_frame, text="", font=("Helvetica", 20, "bold"))
        self.level_display.pack(side="left")

        self.round_display = ttk.Label(
            level_row_frame, text="", font=("Helvetica", 20), foreground=MUTED,
        )
        self.round_display.pack(side="left", padx=(16, 0))

        self.timer_display = ttk.Label(info_col, text="00:00", font=("Helvetica", 60, "bold"))
        self.timer_display.pack(pady=4)

        self.blinds_display = ttk.Label(info_col, text="", font=("Helvetica", 28))
        self.blinds_display.pack()

        self.next_display = ttk.Label(info_col, text="", font=("Helvetica", 12))
        self.next_display.pack(pady=(5, 6))

        # "Structure de blindes" juste sous top_row (donc sous "Niveau
        # suivant"), et prend tout l'espace restant en dessous.
        struct_frame = ttk.LabelFrame(inner, text="Structure de blindes")
        struct_frame.pack(fill="both", expand=True, padx=15, pady=(4, 10))

        ttk.Label(
            struct_frame, foreground=MUTED,
            text="Astuce : double-cliquez sur un niveau ci-dessous pour y aller directement.",
        ).pack(anchor="w", padx=5, pady=(5, 0))

        tree_frame = ttk.Frame(struct_frame)
        tree_frame.pack(fill="both", expand=True, side="left", padx=5, pady=5)

        cols = ("order", "round", "sb", "bb", "ante", "duration", "break")
        headers = ["Niveau", "Round", "SB", "BB", "Ante", "Durée (min)", "Pause"]
        self.blinds_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        for c, h in zip(cols, headers):
            self.blinds_tree.heading(c, text=h)
            self.blinds_tree.column(c, width=100, anchor="center")
        TreeHeadingTooltip(self.blinds_tree, {
            "order": "Numéro de niveau, dans l'ordre de jeu (les pauses comptent\naussi comme une ligne).",
            "round": "Numéro de round (voir colonne Round de l'onglet Blindes) :\nne compte pas les pauses comme une ligne à part entière,\ncontrairement à Niveau — n'avance donc pas pendant une pause.",
            "sb": "Petite blinde (small blind).",
            "bb": "Grosse blinde (big blind).",
            "ante": "Mise obligatoire de chaque joueur en début de main, en plus\ndes blindes (0 = pas d'ante à ce niveau).",
        })
        self.blinds_tree.pack(fill="both", expand=True)
        self.blinds_tree.bind("<Double-Button-1>", self._on_blinds_tree_double_click)

        struct_btns = ttk.Frame(struct_frame)
        struct_btns.pack(side="left", fill="y", padx=5)
        ttk.Button(struct_btns, text="Aller à ce niveau", command=self._go_to_selected_level).pack(pady=3, fill="x")
        standard_btn = ttk.Button(struct_btns, text="Structure standard", command=self._reset_blind_structure)
        standard_btn.pack(pady=3, fill="x")
        Tooltip(
            standard_btn,
            "Remplace toute la structure actuelle par la structure par\n"
            "défaut (25/50, ante dès le niveau 4, paliers de 15 min).",
        )
        edit_blinds_btn = ttk.Button(
            struct_btns, text="Modifier SB/BB/Ante pour un round sélectionné",
            command=self._edit_selected_level_blinds,
        )
        edit_blinds_btn.pack(pady=3, fill="x")
        Tooltip(
            edit_blinds_btn,
            "Sélectionnez d'abord une ligne dans le tableau ci-contre, puis\n"
            "cliquez ici pour changer juste ses blindes (petite blinde,\n"
            "grosse blinde, ante) sans toucher au reste de la structure.",
        )
        ttk.Button(struct_btns, text="Modifier durée (tous niveaux)", command=self._edit_level_duration).pack(pady=3, fill="x")
        ttk.Button(struct_btns, text="Modifier durée de la Pause", command=self._edit_break_duration).pack(pady=3, fill="x")

        # Réglage des 3 sons (début Pause / Fin Pause / fin Round) déplacé
        # dans sa propre petite fenêtre (voir _open_clock_sounds_dialog) :
        # affiché en ligne ici, ce bloc (3 boutons + 3 lignes durée/Test)
        # rendait l'onglet trop haut sur un écran pas très grand, obligeant
        # à beaucoup défiler pour voir le tableau des blindes en dessous.
        ttk.Button(
            struct_btns, text="🔊 Sons de fin de Round/Pause...",
            command=self._open_clock_sounds_dialog,
        ).pack(pady=(10, 3), fill="x")

    def _open_clock_sounds_dialog(self):
        win = tk.Toplevel(self)
        win.title("Sons de fin de Round/Pause")
        win.transient(self)
        win.grab_set()
        ttk.Label(
            win, foreground=MUTED,
            text="Sons joués automatiquement sur l'écran projecteur\n"
                 "(clic droit sur un bouton pour retirer le son configuré) :",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 6))

        self._clock_sound_buttons = {}
        for key, label in (
            ("sound_break_start_path", "Son début Pause"),
            ("sound_next_blinds_path", "Son prochain changement Blindes"),
            ("sound_break_end_path", "Son Fin Pause"),
            ("sound_round_end_path", "Son fin Round"),
            ("sound_elimination_path", "Son sortie d'un joueur"),
        ):
            row = ttk.Frame(win)
            row.pack(fill="x", padx=12, pady=4)
            btn = ttk.Button(row, text=self._clock_sound_button_text(key, label), width=26)
            btn.pack(side="left")
            btn.config(command=lambda k=key, l=label, b=btn: self._choose_clock_sound(k, l, b))
            btn.bind("<Button-2>", lambda e, k=key, l=label, b=btn: self._clear_clock_sound(k, l, b))
            btn.bind("<Button-3>", lambda e, k=key, l=label, b=btn: self._clear_clock_sound(k, l, b))
            if key == "sound_elimination_path":
                Tooltip(
                    btn,
                    "Fichier .wav joué une seule fois au tout début du bandeau\n"
                    "« XXX est sorti par YYY » sur l'écran projecteur (voir\n"
                    "onglet Chronomètre). Un bip généré automatiquement est\n"
                    "joué à défaut, pour que ça fonctionne sans réglage.\n"
                    "Clic gauche : choisir/remplacer le fichier.\n"
                    "Clic droit : retirer le fichier (revient au bip par défaut).",
                )
            elif key == "sound_next_blinds_path":
                Tooltip(
                    btn,
                    "Fichier .wav joué une seule fois, au délai configuré\n"
                    "ci-contre AVANT qu'un changement RÉEL de blindes (SB/BB\n"
                    "différentes) ne survienne. Jamais joué pendant une\n"
                    "pause, ni si le round suivant est une pause, ni si ses\n"
                    "blindes sont identiques à celles du round en cours.\n"
                    "Clic gauche : choisir/remplacer le fichier.\n"
                    "Clic droit : retirer le son configuré.",
                )
            else:
                Tooltip(
                    btn,
                    f"Fichier .wav joué automatiquement à chaque « {label.lower()} ».\n"
                    "Clic gauche : choisir/remplacer le fichier.\n"
                    "Clic droit : retirer le son configuré.",
                )
            self._clock_sound_buttons[key] = btn

            # Durée max. (tronque le fichier s'il est plus long, comme pour
            # le Signal de mouvements) + Test, sur la même ligne.
            ttk.Label(row, text="  Durée (ms) :").pack(side="left")
            dur_var = tk.StringVar(value=export_prefs.load_value(f"{key}_duration_ms", ""))
            dur_entry = ttk.Entry(row, textvariable=dur_var, width=6)
            dur_entry.pack(side="left", padx=(4, 4))
            Tooltip(
                dur_entry,
                "Tronque ce fichier s'il dure plus longtemps que ça\n"
                "(en millisecondes). Laisser vide pour le jouer en entier.",
            )
            dur_var.trace_add(
                "write", lambda *a, k=key, v=dur_var: self._save_clock_sound_duration(k, v)
            )

            if key == "sound_next_blinds_path":
                # Propre à ce son (voir _maybe_play_next_blinds_sound) :
                # combien de secondes avant le changement de blindes il
                # doit se déclencher — 60 par défaut, réglable ici, sans
                # équivalent sur les 3 autres sons (tous déclenchés à un
                # instant fixe, jamais "avant" quoi que ce soit).
                ttk.Label(row, text="  Délai avant fin (s) :").pack(side="left")
                lead_var = tk.StringVar(
                    value=export_prefs.load_value(f"{key}_lead_seconds", "60")
                )
                lead_entry = ttk.Entry(row, textvariable=lead_var, width=5)
                lead_entry.pack(side="left", padx=(4, 4))
                Tooltip(
                    lead_entry,
                    "Nombre de secondes avant le changement de blindes\n"
                    "auquel jouer ce son (60 par défaut). Entier positif.",
                )
                lead_var.trace_add(
                    "write", lambda *a, k=key, v=lead_var: self._save_clock_sound_lead_seconds(k, v)
                )

            ttk.Button(
                row, text="Test", width=5, command=lambda k=key: self._test_clock_sound(k),
            ).pack(side="left")

        ttk.Button(win, text="Fermer", command=win.destroy).pack(pady=(6, 12))

    def _clock_resume(self):
        if self.db.get_setting_int("clock_started", 0) == 0:
            # Resynchronisation défensive de dernière minute (demande du
            # 2026-09-09, voir le grand bloc de commentaires près de
            # _sync_primes_enabled_pref) : ce tournoi est sur le point de
            # devenir celui qui verrouille "Calculer les primes" pour
            # TOUTE la session — on s'assure qu'il porte bien la toute
            # dernière valeur globale proposée avant que ça n'arrive,
            # pour fermer toute fenêtre de course résiduelle avec le tick
            # normal (jusqu'à ~1s de délai autrement).
            _sync_primes_enabled_pref(self.db)
            self.db.set_settings({
                "clock_started": 1,
                "level_start_epoch": int(time.time()),
                "is_paused": 0,
                "paused_accum_seconds": 0,
                # Fixé une seule fois, au tout premier démarrage : sert de
                # référence pour la "Durée" affichée sur le chrono
                # projecteur (voir Database.get_stats).
                "tournament_start_epoch": int(time.time()),
            })
            # Verrouille "Calculer les primes" pour TOUTE la session,
            # À LA VALEUR de CE tournoi précis (demande du 2026-09-09,
            # voir open_windows.mark_primes_session_started/
            # primes_session_started/locked_primes_enabled et
            # _primes_session_locked) : posé ici, au moment exact où CE
            # tournoi démarre — que ce soit le tout premier de la session
            # ou un suivant (sans effet si la session est déjà
            # verrouillée : la valeur du tout premier reste seule
            # autoritative). Reste vrai (et la valeur mémorisée) même
            # après la fermeture de CE tournoi, tant qu'il reste au moins
            # un autre tournoi de la session ouvert (voir la docstring de
            # primes_session_started) — et sert désormais aussi à aligner
            # tout tournoi (nouveau OU existant) qui rejoindrait la
            # session après ce verrouillage, voir _align_primes_enabled_
            # on_open.
            open_windows.mark_primes_session_started(self.db.get_setting_int("primes_enabled", 1) == 1)
        elif self.db.get_setting_int("is_paused", 1) == 1:
            # reprise : on décale level_start_epoch du temps passé en pause
            self.db.set_settings({"is_paused": 0, "level_start_epoch": int(time.time()) - self._elapsed_before_pause()})
            # RÉPARATION AUTOMATIQUE (demande du 2026-09-14, suite à un cas
            # réel observé sur un poste Windows) : ce tournoi a déjà
            # démarré (clock_started=1) — normalement, mark_primes_session_
            # started a donc déjà été posé plus haut, à SON tout premier
            # démarrage. Si primes_session_started.json a depuis disparu
            # pour une raison quelconque (diagnostic du 2026-09-14 : cause
            # initiale non démontrée, mais _pid_is_running durci et
            # _registry_lock ajoutés le même jour pour la traiter à la
            # racine) alors qu'un tournoi reste bel et bien vivant, une
            # simple pause/reprise ne le recréait JUSQU'ICI JAMAIS (seule
            # la transition clock_started 0->1, quelques lignes plus haut,
            # appelait mark_primes_session_started) — laissant la section
            # Primes déverrouillée à tort, indéfiniment, hors Mode Test.
            # mark_primes_session_started est idempotente (aucun effet si
            # déjà posée, voir sa docstring) : cet appel est donc sans
            # risque même quand tout va bien, et répare le cas contraire.
            open_windows.mark_primes_session_started(self.db.get_setting_int("primes_enabled", 1) == 1)
        self._refresh_clock_tab()

    def _elapsed_before_pause(self):
        # temps déjà écoulé dans le niveau au moment de la mise en pause
        return self.db.get_setting_int("paused_accum_seconds", 0)

    def _clock_pause(self):
        if self.db.get_setting_int("is_paused", 1) == 0:
            start = self.db.get_setting_int("level_start_epoch", int(time.time()))
            elapsed = int(time.time()) - start
            self.db.set_settings({"is_paused": 1, "paused_accum_seconds": elapsed})
        self._refresh_clock_tab()

    def _go_to_level(self, order):
        levels = self.db.get_blind_structure()
        if not levels:
            return
        order = max(1, min(order, len(levels)))
        self.db.set_settings({
            "current_level_order": order,
            "level_start_epoch": int(time.time()),
            "paused_accum_seconds": 0,
        })
        self._refresh_clock_tab()

    def _on_blinds_tree_double_click(self, event):
        row_iid = self.blinds_tree.identify_row(event.y)
        if not row_iid:
            return
        order = int(row_iid)
        self._go_to_level(order)

    def _go_to_selected_level(self):
        sel = self.blinds_tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Sélectionnez d'abord un niveau dans le tableau.")
            return
        self._go_to_level(int(sel[0]))

    def _edit_selected_level_blinds(self):
        """Bouton "Modifier SB/BB/Ante pour un round sélectionné" : change
        juste les blindes de la ligne choisie dans le tableau (contrairement
        à "Structure standard", qui remplace toute la structure, ou à
        "Modifier durée", qui s'applique à tous les niveaux)."""
        sel = self.blinds_tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Sélectionnez d'abord un niveau dans le tableau.")
            return
        level_order = int(sel[0])
        row = self.db.conn.execute(
            "SELECT * FROM blind_levels WHERE level_order=?", (level_order,)
        ).fetchone()
        if row is None:
            return
        if row["is_break"]:
            messagebox.showinfo(
                "Info", "Cette ligne est une pause : elle n'a pas de blindes à modifier."
            )
            return

        sb = simpledialog.askinteger(
            "Petite blinde (SB)", "Nouvelle petite blinde :",
            initialvalue=row["small_blind"], minvalue=1,
        )
        if sb is None:
            return
        bb = simpledialog.askinteger(
            "Grosse blinde (BB)", "Nouvelle grosse blinde :",
            initialvalue=row["big_blind"], minvalue=1,
        )
        if bb is None:
            return
        ante = simpledialog.askinteger(
            "Ante", "Nouvel ante (0 = pas d'ante à ce round) :",
            initialvalue=row["ante"], minvalue=0,
        )
        if ante is None:
            return

        self.db.conn.execute(
            "UPDATE blind_levels SET small_blind=?, big_blind=?, ante=? WHERE level_order=?",
            (sb, bb, ante, level_order),
        )
        self.db.conn.commit()
        self._refresh_clock_tab()

    def _clock_next_level(self):
        order = self.db.get_setting_int("current_level_order", 1)
        self._go_to_level(order + 1)

    def _clock_prev_level(self):
        order = self.db.get_setting_int("current_level_order", 1)
        self._go_to_level(order - 1)

    def _reset_blind_structure(self):
        if messagebox.askyesno("Confirmer", "Remplacer la structure actuelle par la structure standard ?"):
            self.db.set_blind_structure(default_blind_structure())
            self._go_to_level(1)

    def _edit_level_duration(self):
        levels = [lvl for lvl in self.db.get_blind_structure() if not lvl["is_break"]]
        current_val = levels[0]["duration_minutes"] if levels else 15
        val = simpledialog.askinteger(
            "Durée des niveaux",
            "Nouvelle durée (en minutes) pour TOUS les niveaux de blindes\n"
            "(hors pauses — voir 'Durée de la Pause' séparément) :",
            initialvalue=current_val, minvalue=1,
        )
        if val is not None:
            self.db.conn.execute(
                "UPDATE blind_levels SET duration_minutes=? WHERE is_break=0", (val,)
            )
            self.db.conn.commit()
            self._refresh_clock_tab()

    def _edit_break_duration(self):
        levels = [lvl for lvl in self.db.get_blind_structure() if lvl["is_break"]]
        current_val = levels[0]["duration_minutes"] if levels else self.db.get_setting_int(
            "break_duration_minutes", 15
        )
        val = simpledialog.askinteger(
            "Durée de la Pause",
            "Nouvelle durée (en minutes) pour TOUTES les pauses\n"
            "(y compris pendant un tournoi en cours) :",
            initialvalue=current_val, minvalue=1,
        )
        if val is not None:
            self.db.conn.execute(
                "UPDATE blind_levels SET duration_minutes=? WHERE is_break=1", (val,)
            )
            self.db.conn.commit()
            self.db.set_settings({"break_duration_minutes": val})
            tournament_prefs.save_last_settings({"break_duration_minutes": val})
            self._refresh_clock_tab()

    def _remaining_seconds(self):
        level = self.db.get_current_level()
        if level is None:
            return 0, None, None
        duration = level["duration_minutes"] * 60
        if self.db.get_setting_int("clock_started", 0) == 0:
            elapsed = 0
        elif self.db.get_setting_int("is_paused", 1) == 1:
            elapsed = self.db.get_setting_int("paused_accum_seconds", 0)
        else:
            start = self.db.get_setting_int("level_start_epoch", int(time.time()))
            elapsed = int(time.time()) - start
        remaining = duration - elapsed
        if (remaining > 0 and not level["is_break"]
                and self.db.get_setting_int("clock_started", 0) == 1
                and self.db.get_setting_int("is_paused", 1) == 0):
            self._maybe_play_next_blinds_sound(level, remaining)
        if remaining <= 0 and self.db.get_setting_int("clock_started", 0) == 1 and self.db.get_setting_int("is_paused", 1) == 0:
            # niveau terminé -> passe automatiquement au suivant
            next_row = self.db.get_next_level()
            if next_row is not None:
                self._play_level_transition_sounds(level, next_row)
                self.db.set_settings({
                    "current_level_order": next_row["level_order"],
                    "level_start_epoch": int(time.time()),
                    "paused_accum_seconds": 0,
                })
                return self._remaining_seconds()
            else:
                remaining = 0
        next_level = self.db.get_next_level()
        return remaining, level, next_level

    def _maybe_play_next_blinds_sound(self, level, remaining):
        """« Son prochain changement Blindes » (voir _open_clock_sounds_
        dialog) : joué une seule fois par round, au délai configuré (voir
        _next_blinds_sound_lead_seconds, 60s par défaut) avant la fin de
        CE round — mais UNIQUEMENT si un changement RÉEL de blindes est
        imminent, à la demande explicite de l'utilisateur (à la
        différence de "Son fin Round", qui se joue quel que soit ce qui
        suit) :
        - jamais pendant une pause (l'appelant, _remaining_seconds, exclut
          déjà level["is_break"] avant même d'appeler ceci) ;
        - jamais si le round suivant est lui-même une pause (les blindes
          ne changent pas tout de suite) ;
        - jamais si les blindes (SB/BB) du round suivant sont identiques
          à celles du round courant (palier répété dans la structure).
        self._next_blinds_sound_played_for_order (comparé à
        level["level_order"]) évite de rejouer à chaque tick tant que
        `remaining` reste sous le délai — redevient naturellement
        pertinent dès que le niveau change (nouvel ordre)."""
        next_level = self.db.get_next_level()
        if next_level is None or next_level["is_break"]:
            return
        if (next_level["small_blind"], next_level["big_blind"]) == (
                level["small_blind"], level["big_blind"]):
            return
        lead = self._next_blinds_sound_lead_seconds()
        if lead <= 0 or remaining > lead:
            return
        if self._next_blinds_sound_played_for_order == level["level_order"]:
            return
        self._next_blinds_sound_played_for_order = level["level_order"]
        self._play_clock_sound("sound_next_blinds_path")

    def _next_blinds_sound_lead_seconds(self):
        """Délai (s) réglé pour "Son prochain changement Blindes" (champ
        "Délai avant fin (s)" de _open_clock_sounds_dialog) — 60 par
        défaut, y compris si le champ est vide/invalide ou à 0 (jamais
        désactivé silencieusement par une saisie incorrecte)."""
        raw = export_prefs.load_value("sound_next_blinds_path_lead_seconds", "60")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 60
        return value if value > 0 else 60

    def _save_clock_sound_lead_seconds(self, setting_key, var):
        export_prefs.save_value(f"{setting_key}_lead_seconds", var.get())

    def _play_level_transition_sounds(self, old_level, new_level):
        """Joue les sons configurés (boutons "Son début Pause"/"Son Fin
        Pause"/"Son fin Round" de l'onglet Chronomètre) au moment précis
        où un niveau se termine automatiquement et cède la place au
        suivant. "Son fin Round" concerne la fin de tout niveau de
        blindes normal (pause ou non juste après) ; "Son début/Fin
        Pause" concernent spécifiquement l'entrée/la sortie d'une pause —
        les deux peuvent donc se jouer l'un après l'autre (ex. : un
        niveau de blindes qui débouche sur une pause)."""
        if not old_level["is_break"]:
            self._play_clock_sound("sound_round_end_path")
        if new_level["is_break"] and not old_level["is_break"]:
            self._play_clock_sound("sound_break_start_path")
        elif old_level["is_break"] and not new_level["is_break"]:
            self._play_clock_sound("sound_break_end_path")

    def _play_clock_sound(self, setting_key):
        path = export_prefs.load_value(setting_key, "")
        if path:
            sound_signal.play_file(path, max_duration_ms=self._clock_sound_duration_ms(setting_key))

    def _clock_sound_duration_ms(self, setting_key):
        """Durée max. (ms) configurée pour ce son (voir le champ "Durée"
        à côté de chaque bouton), ou None si vide/invalide (joue le
        fichier en entier)."""
        raw = export_prefs.load_value(f"{setting_key}_duration_ms", "")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    def _save_clock_sound_duration(self, setting_key, var):
        export_prefs.save_value(f"{setting_key}_duration_ms", var.get())

    def _test_clock_sound(self, setting_key):
        if setting_key == "sound_elimination_path":
            # Contrairement aux 3 sons Round/Pause (vraiment rien à jouer
            # tant qu'aucun fichier n'est choisi, d'où le message
            # ci-dessous) : celui-ci a toujours quelque chose à jouer, ne
            # serait-ce que le bip par défaut (voir
            # _play_elimination_sound) — "Aucun fichier son choisi" y
            # serait trompeur, puisqu'un son sera bel et bien joué à la
            # prochaine élimination. Le bouton doit UNIQUEMENT tester le
            # son (voir _play_elimination_sound : aucune donnée touchée,
            # aucun bandeau affiché).
            self._play_elimination_sound()
            return
        path = export_prefs.load_value(setting_key, "")
        if not path:
            messagebox.showinfo("Test", "Aucun fichier son choisi pour l'instant.")
            return
        if not sound_signal.play_file(path, max_duration_ms=self._clock_sound_duration_ms(setting_key)):
            self.bell()

    def _clock_sound_button_text(self, setting_key, label):
        path = export_prefs.load_value(setting_key, "")
        if path:
            return f"{label} : {os.path.basename(path)}"
        if setting_key == "sound_elimination_path":
            # Contrairement aux 3 sons Round/Pause (vraiment silencieux
            # tant qu'aucun fichier n'est choisi) : celui-ci a un repli
            # (bip généré, voir _play_elimination_sound) pour fonctionner
            # dès l'installation — "(aucun)" laisserait croire à tort
            # qu'aucun son ne sera joué.
            return f"{label} : (son par défaut)"
        return f"{label} : (aucun)"

    def _choose_clock_sound(self, setting_key, label, btn):
        initial = export_prefs.load_value("sound_folder") or os.path.expanduser("~")
        path = filedialog.askopenfilename(
            title=f"Choisir le fichier son pour « {label} »",
            initialdir=initial,
            filetypes=[("Fichier son WAV", "*.wav"), ("Tous les fichiers", "*.*")],
        )
        if not path:
            return
        # Réglage commun à tous les tournois/Sit & Go (pas propre à celui-ci) :
        # mémorisé dans les préférences partagées, comme "sound_folder".
        export_prefs.save_value(setting_key, path)
        export_prefs.save_value("sound_folder", os.path.dirname(path))
        btn.config(text=self._clock_sound_button_text(setting_key, label))

    def _clear_clock_sound(self, setting_key, label, btn):
        if not export_prefs.load_value(setting_key, ""):
            return
        if messagebox.askyesno("Confirmer", f"Retirer le son « {label} » ?"):
            export_prefs.save_value(setting_key, "")
            btn.config(text=self._clock_sound_button_text(setting_key, label))

    def _next_break_eta_text(self, remaining=None, level=None, structure=None):
        """Texte 'Prochaine pause à HH:MM', calculé à partir du temps
        restant du niveau en cours puis des durées des niveaux suivants
        jusqu'à la prochaine pause de la structure. Si le niveau en cours
        est lui-même une pause, cherche la suivante (pas celle-ci).
        `remaining`/`level`/`structure` : à passer si déjà disponibles
        chez l'appelant (ex. _refresh_clock_tab, qui tourne chaque
        seconde) pour éviter de refaire les mêmes requêtes DB ; sinon,
        recalculés ici."""
        if level is None and remaining is None:
            remaining, level, _ = self._remaining_seconds()
        if level is None:
            return "Aucune structure définie"
        if structure is None:
            structure = self.db.get_blind_structure()
        total_seconds = max(0, remaining)
        for lvl in structure:
            if lvl["level_order"] <= level["level_order"]:
                continue
            if lvl["is_break"]:
                eta = datetime.now() + timedelta(seconds=total_seconds)
                return f"Prochaine pause à {eta.strftime('%H:%M')}"
            total_seconds += lvl["duration_minutes"] * 60
        return "Pas de pause prévue"

    def _refresh_clock_tab(self):
        remaining, level, next_level = self._remaining_seconds()
        mins, secs = divmod(max(0, remaining), 60)
        paused = self.db.get_setting_int("is_paused", 1) == 1
        suffix = "  (en pause)" if paused else ""
        self.timer_display.config(text=f"{mins:02d}:{secs:02d}{suffix}")

        # round_number (voir Database.get_round_number) : même numéro que
        # la colonne "Round" de l'onglet Blindes, contrairement à
        # level_order brut ("Niveau") qui compte aussi les pauses comme
        # une ligne à part entière — les deux sont affichés côte à côte
        # (round_display reste inchangé pendant une pause, justement pour
        # montrer qu'il n'avance pas).
        # Un seul SELECT ici (au lieu d'une requête Database.get_round_number
        # par niveau, N+1 coûteux vu que ce rafraîchissement tourne chaque
        # seconde tout au long du tournoi) : la structure est chargée une
        # fois, et les numéros de round sont comptés en Python au passage.
        structure = self.db.get_blind_structure()
        round_by_order = {}
        round_counter = 0
        for lvl in structure:
            if not lvl["is_break"]:
                round_counter += 1
            round_by_order[lvl["level_order"]] = round_counter
        round_number = round_by_order.get(level["level_order"]) if level is not None else None
        self.round_display.config(text=f"Round {round_number}" if round_number is not None else "")
        if level is not None and level["is_break"]:
            self.level_display.config(text=level["break_label"] or "Pause")
            self.blinds_display.config(text="")
        elif level is not None:
            self.level_display.config(text=f"Niveau {level['level_order']}")
            ante_txt = f"   Ante {level['ante']}" if level["ante"] else ""
            self.blinds_display.config(text=f"{level['small_blind']} / {level['big_blind']}{ante_txt}")
        else:
            self.level_display.config(text="Aucune structure définie")
            self.blinds_display.config(text="")

        if next_level is not None:
            label = (next_level["break_label"] or "Pause") if next_level["is_break"] else \
                f"{next_level['small_blind']} / {next_level['big_blind']}"
            self.next_display.config(text=f"Niveau suivant : {label}")
        else:
            self.next_display.config(text="Dernier niveau de la structure")

        current_order = level["level_order"] if level is not None else -1

        # Signature légère du contenu affiché : si elle n'a pas changé
        # depuis le tick précédent (cas normal — la structure de blindes
        # ne change quasiment jamais en cours de tournoi), on évite de
        # vider/reconstruire tout le tableau chaque seconde ; seule la
        # ligne "en cours" (surlignage doré) peut changer d'un tick à
        # l'autre, et se met à jour sans reconstruction.
        sig = tuple(
            (lvl["level_order"], lvl["small_blind"], lvl["big_blind"], lvl["ante"],
             lvl["duration_minutes"], lvl["is_break"], lvl["break_label"],
             round_by_order[lvl["level_order"]])
            for lvl in structure
        )
        if sig != getattr(self, "_blinds_tab_sig", None):
            selected = self.blinds_tree.selection()
            for row in self.blinds_tree.get_children():
                self.blinds_tree.delete(row)
            for lvl in structure:
                label = "Oui" if lvl["is_break"] else "Non"
                tag = "current" if lvl["level_order"] == current_order else ""
                # Une pause n'a pas de blindes propres — le "small_blind"/
                # "big_blind"/"ante" stockés en base pour cette ligne ne
                # sont que des valeurs de remplissage internes, jamais
                # utilisées en jeu. Les afficher ("1000 / 2000 / 0", par
                # exemple) donnait l'illusion d'un round normal éditable —
                # d'où des joueurs cliquant "Modifier SB/BB/Ante" sur une
                # pause sans le vouloir et tombant sur le message de refus.
                # Un tiret rend la distinction visible d'un coup d'œil.
                sb_txt = "—" if lvl["is_break"] else lvl["small_blind"]
                bb_txt = "—" if lvl["is_break"] else lvl["big_blind"]
                ante_txt = "—" if lvl["is_break"] else lvl["ante"]
                self.blinds_tree.insert(
                    "", "end", iid=str(lvl["level_order"]),
                    values=(lvl["level_order"], round_by_order[lvl["level_order"]],
                            sb_txt, bb_txt, ante_txt, lvl["duration_minutes"], label),
                    tags=(tag,),
                )
            self.blinds_tree.tag_configure("current", background=GOLD, foreground=TEXT_DARK)
            if selected and self.blinds_tree.exists(selected[0]):
                self.blinds_tree.selection_set(selected)
            self._blinds_tab_sig = sig
            self._blinds_tab_current_order = current_order
        elif current_order != getattr(self, "_blinds_tab_current_order", None):
            prev_order = getattr(self, "_blinds_tab_current_order", None)
            if prev_order is not None and self.blinds_tree.exists(str(prev_order)):
                self.blinds_tree.item(str(prev_order), tags=())
            if self.blinds_tree.exists(str(current_order)):
                self.blinds_tree.item(str(current_order), tags=("current",))
            self._blinds_tab_current_order = current_order

        # Bandeau d'élimination : l'échéance ("until", epoch absolue) est
        # désormais vérifiée/avancée dans _tick() (voir plus bas),
        # INCONDITIONNELLEMENT à chaque seconde — pas ici, puisque cette
        # méthode-ci ne tourne que si l'onglet Chronomètre est affiché ou
        # l'écran projecteur est ouvert (voir _tick). self._elimination_
        # banner_current est donc déjà à jour au moment où on l'affiche
        # ci-dessous, une seule source de vérité pour l'avancer.

        movement_alert = self.db.get_setting_int("movement_alert_active", 0) == 1
        if movement_alert and self.db.count_seat_moves() == 0:
            # Bandeau actif mais plus aucun mouvement à afficher : état
            # incohérent (ex : résidu d'un ancien test resté bloqué,
            # fichier .tournoi réutilisé de nombreuses fois) — un bandeau
            # "Changement de tables en cours" vide n'aide personne et
            # bloque en plus la reprise du chrono après une élimination
            # (voir _on_voice_word) sans qu'il n'y ait plus rien à
            # confirmer avec "Terminé". On se contente ici de désactiver
            # le drapeau (pas besoin de tout le reste de
            # _finish_movement_alert : pas de mouvement réel en cours,
            # donc rien à figer ou à débloquer côté chrono).
            self.db.set_settings({"movement_alert_active": 0})
            movement_alert = False
        blink_on = movement_alert and int(time.time()) % 2 == 0
        if blink_on:
            self.movement_alert_lbl.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.movement_alert_lbl.place_forget()

        # stats (dont stats["tournament_finished"]) calculée ici, avant le
        # bandeau simplifié ci-dessous, plutôt que seulement plus bas dans
        # le bloc "écran projecteur" : sert désormais aux deux usages (masquer
        # ce bandeau une fois la partie terminée, ET alimenter l'écran
        # projecteur), un seul appel à get_stats() par tick au lieu de deux.
        stats = self.db.get_stats()

        # Version simplifiée (texte seul, sans les photos) du bandeau
        # d'élimination pour cet onglet Chronomètre de la fenêtre
        # principale — même état déjà calculé ci-dessus
        # (_elimination_banner_current), pas de deuxième file/logique :
        # seul le rendu diffère de l'écran projecteur (voir
        # ClockWindow.refresh), pas assez de place ici pour les photos.
        # Masqué une fois la partie terminée (stats["tournament_finished"]),
        # exactement comme sur l'écran projecteur (qui affiche alors
        # "Partie terminée" à la place, voir ClockWindow.refresh) — sans
        # quoi les deux affichages se contredisaient. Ne touche ni
        # _elimination_banner_current ni _elimination_banner_queue : seul
        # l'AFFICHAGE de ce label change, l'état reste intact (utile si la
        # partie devait reprendre, ex. un joueur réintégré).
        banner = self._elimination_banner_current
        if banner is not None and not stats.get("tournament_finished"):
            if banner["eliminator_name"]:
                text = f"{banner['eliminated_name']} est sorti par {banner['eliminator_name']}\nMerci d'avoir participé"
            else:
                text = f"{banner['eliminated_name']} est éliminé\nMerci d'avoir participé"
            self.elimination_banner_lbl.config(text=text)
            self.elimination_banner_lbl.place(relx=0.5, rely=0.3, anchor="center")
        else:
            self.elimination_banner_lbl.place_forget()

        if self.clock_window is not None and self.clock_window.winfo_exists():
            name = self.db.get_setting("tournament_name", "Tournoi")
            # La liste des joueurs concernés n'est utile à l'écran
            # projecteur que pendant l'alerte elle-même (voir
            # ClockWindow._update_movement_moves_table) : inutile
            # d'interroger la base à chaque tick le reste du temps.
            moves = self.db.get_seat_moves() if movement_alert else []
            self.clock_window.refresh(
                remaining, level, next_level, stats, name, paused,
                self._next_break_eta_text(remaining, level, structure), movement_alert,
                self._load_chip_denominations(), moves, round_number,
                self._elimination_banner_current,
            )

    def _open_clock_window(self, fullscreen=False):
        """Ouvre l'écran projecteur, ou le ramène au premier plan s'il est
        déjà ouvert. `fullscreen=True` (utilisé par le bouton "Chronomètre"
        du contrôle à distance, voir _voice_show_clock/_voice_resume_clock)
        force le plein écran même si la fenêtre vient d'être créée à
        l'instant — sinon elle s'ouvrirait en petite fenêtre par défaut,
        ce qui n'a rien de "mode écran projecteur" pour un responsable qui
        n'est pas physiquement devant le PC pour appuyer sur F11.
        Dans les deux cas (déjà ouverte ou tout juste créée), passe par
        bring_to_front() : une fenêtre fraîchement créée peut, elle
        aussi, ne pas obtenir le premier plan sous Windows si ce
        processus n'a pas déjà la main au moment de l'appel (voir le
        commentaire de ClockWindow.bring_to_front)."""
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()
            return
        self.clock_window = ClockWindow(self, self)
        if fullscreen:
            self.clock_window.enter_fullscreen()
        self.clock_window.bring_to_front()

    # ---------------------------------------------------------------
    # Onglet Blindes
    # ---------------------------------------------------------------
    # Saisie manuelle de la structure, ligne par ligne : Round / Durée /
    # Petite Blind / Grosse Blind / Ante / Durée Pause. En base, une pause
    # reste une ligne à part (is_break=1) juste après le niveau concerné
    # (voir database.set_blind_structure) ; cet onglet la présente à
    # l'utilisateur comme une simple colonne "Durée Pause" du round
    # précédent, plus parlant pour un responsable de tournoi.
    def _build_blinds_tab(self):
        ttk.Label(
            self.blinds_tab, foreground=MUTED,
            text="Saisissez manuellement chaque round (durée, blindes, ante) et, "
                 "si besoin, la durée d'une pause juste après. Laissez \"Durée "
                 "Pause\" à 0 pour un round sans pause. N'oubliez pas d'enregistrer.",
            wraplength=760, justify="left",
        ).pack(anchor="w", padx=15, pady=(12, 6))

        top_btns = ttk.Frame(self.blinds_tab)
        top_btns.pack(fill="x", padx=15, pady=(0, 10))
        ttk.Button(top_btns, text="➕ Ajouter un round", command=lambda: self._add_blind_round()).pack(side="left", padx=3)
        ttk.Button(top_btns, text="Structure standard", command=self._reset_blind_structure_from_tab).pack(side="left", padx=3)
        ttk.Button(top_btns, text="Appliquer", command=self._apply_blinds_from_tab).pack(side="left", padx=3)

        # Largeur (en caractères) des champs Durée/Petite Blind/Grosse
        # Blind/Ante/Durée Pause du tableau ci-dessous — réglable par
        # l'utilisateur (et mémorisée d'une session à l'autre) plutôt que
        # fixée en dur, pour que les valeurs saisies restent toujours
        # entièrement visibles quelle que soit la résolution d'écran.
        self.blinds_field_width_var = tk.IntVar(
            value=export_prefs.load_value("blinds_field_width", 10)
        )
        width_frame = ttk.Frame(top_btns)
        width_frame.pack(side="left", padx=(16, 3))
        ttk.Label(width_frame, text="Largeur des champs :").pack(side="left")
        width_spin = ttk.Spinbox(
            width_frame, from_=5, to=25, width=3,
            textvariable=self.blinds_field_width_var,
            command=self._on_blinds_field_width_change,
        )
        width_spin.pack(side="left", padx=(4, 0))
        width_spin.bind("<Return>", lambda e: self._on_blinds_field_width_change())
        width_spin.bind("<FocusOut>", lambda e: self._on_blinds_field_width_change())
        Tooltip(
            width_spin,
            "Largeur des champs Durée/Petite Blind/Grosse Blind/Ante/\n"
            "Durée Pause ci-dessous (en caractères) — pour tout voir à\n"
            "l'écran quelle que soit la résolution. Mémorisée pour la\n"
            "prochaine fois.",
        )
        load_btn = ttk.Button(
            top_btns, text="📂 Récupérer Blindes...", command=self._open_blind_templates,
        )
        load_btn.pack(side="right", padx=3)
        Tooltip(
            load_btn,
            "Ouvre la liste des structures de blindes déjà enregistrées\n"
            "(via \"Enregistrer Blindes sous...\") pour en appliquer une à\n"
            "ce tournoi.",
        )
        save_as_btn = ttk.Button(
            top_btns, text="💾 Enregistrer Blindes sous...", command=self._save_blinds_as_template,
        )
        save_as_btn.pack(side="right", padx=3)
        Tooltip(
            save_as_btn,
            "Applique les modifications de ce tableau à ce tournoi ET les\n"
            "enregistre sous un nom au choix, pour les réutiliser plus\n"
            "tard sur d'autres tournois/Sit & Go (voir \"Récupérer\n"
            "Blindes\" juste à côté).",
        )

        # Panneau "Jetons" (à droite) : empaqueté AVANT le conteneur du
        # tableau de rounds ci-dessous, pour qu'il réserve sa place à
        # droite en premier — sinon le tableau de rounds (expand=True)
        # capterait tout l'espace disponible et ne laisserait rien pour
        # ce panneau. Voir _build_chips_panel.
        self._build_chips_panel()

        # Conteneur défilable : le nombre de rounds peut largement dépasser
        # la hauteur de l'écran.
        canvas = tk.Canvas(self.blinds_tab, bg=FELT, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.blinds_tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        # vscroll empaqueté AVANT canvas (expand=True) pour la même raison
        # que le panneau Jetons ci-dessus : sinon canvas capterait tout
        # l'espace restant et la scrollbar n'aurait plus de place.
        vscroll.pack(side="right", fill="y", pady=10)
        canvas.pack(side="left", fill="both", expand=True, padx=(15, 0), pady=10)

        self.blinds_rows_frame = ttk.Frame(canvas)
        outer_id = canvas.create_window((0, 0), window=self.blinds_rows_frame, anchor="nw")
        self.blinds_rows_frame.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(outer_id, width=e.width))

        is_mac = self.tk.call("tk", "windowingsystem") == "aqua"

        def _on_mousewheel(event):
            if is_mac:
                canvas.yview_scroll(int(-1 * event.delta), "units")
            else:
                canvas.yview_scroll(int(-1 * event.delta / 120), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Bascule de l'en-tête "Hr de Début" (voir _toggle_blinds_start_
        # time / _refresh_blinds_tab) : None = colonne affichée depuis
        # 00:00 (comportement d'origine, purement relatif au début du
        # tournoi) ; sinon, minutes depuis minuit capturées lors du
        # dernier clic sur "Hr de Début" en mode "heure réelle" — sert de
        # base à toute la colonne jusqu'au prochain clic. Volontairement
        # non persisté (comme blinds_field_width_var l'est, lui) : simple
        # aperçu de planification, remis à 00:00 à chaque redémarrage.
        self._blinds_start_now_minutes = None
        self._blind_row_vars = []
        self._refresh_blinds_tab()

    # ---------------------------------------------------------------
    # Panneau "Jetons" (onglet Blindes, à droite du tableau des rounds)
    # ---------------------------------------------------------------
    def _build_chips_panel(self):
        chips_frame = ttk.LabelFrame(self.blinds_tab, text="Jetons")
        chips_frame.pack(side="right", fill="y", padx=(10, 15), pady=10)

        # Rangée du haut : à gauche, une colonne compacte avec le total
        # jetons/joueur (nombre + valeur) SUIVI directement des boutons
        # Enregistrer/Récupérer, l'un sous l'autre ; à droite, le texte
        # d'aide (wraplength réduit, 220 -> 110) qui s'enroule sur
        # plusieurs lignes et donne donc à cette rangée toute sa hauteur.
        # Mettre les boutons dans cette même colonne de gauche (plutôt
        # qu'en rangée séparée sous top_row) comble l'espace resté vide
        # sous "Valeur des jetons" pendant que le texte de droite
        # s'enroule — et garde la section Jetons plus compacte, plus de
        # place pour le tableau des blindes.
        top_row = ttk.Frame(chips_frame)
        top_row.pack(fill="x", padx=8, pady=(8, 6))

        left_col = ttk.Frame(top_row)
        left_col.pack(side="left", anchor="n")
        self.chips_count_total_lbl = ttk.Label(
            left_col, text="", font=("Helvetica", 10, "bold"), foreground=CREAM,
        )
        self.chips_count_total_lbl.pack(anchor="w")
        self.chips_total_lbl = ttk.Label(
            left_col, text="", font=("Helvetica", 10, "bold"), foreground=GOLD,
        )
        self.chips_total_lbl.pack(anchor="w", pady=(0, 8))

        # "Enregistrer" avant "Récupérer" : c'est l'action la plus
        # fréquente une fois le tableau de jetons rempli.
        save_chips_btn = ttk.Button(
            left_col, text="💾 Enregistrer Jetons sous...", command=self._save_chips_as_template,
        )
        save_chips_btn.pack(fill="x", pady=(0, 4))
        Tooltip(
            save_chips_btn,
            "Applique les jetons de ce tableau à ce tournoi ET les\n"
            "enregistre sous un nom au choix, pour les réutiliser plus\n"
            "tard sur d'autres tournois/Sit & Go.",
        )
        load_chips_btn = ttk.Button(
            left_col, text="📂 Récupérer Jetons...", command=self._open_chip_templates,
        )
        load_chips_btn.pack(fill="x")
        Tooltip(
            load_chips_btn,
            "Ouvre la liste des jeux de jetons déjà enregistrés (via\n"
            "\"Enregistrer Jetons sous...\") pour en appliquer un à ce\n"
            "tournoi.",
        )

        ttk.Label(
            top_row, foreground=MUTED,
            text="Jetons utilisés pour ce tournoi (facultatif). Cliquer sur "
                 "la pastille pour choisir une couleur ou une image de jeton.",
            wraplength=110, justify="left",
        ).pack(side="left", anchor="n", padx=(10, 0))

        ttk.Separator(chips_frame, orient="horizontal").pack(fill="x", padx=8, pady=(4, 6))

        ttk.Button(
            chips_frame, text="➕ Ajouter une couleur", command=self._add_chip_row,
        ).pack(fill="x", padx=8, pady=(0, 6))

        # Conteneur défilable : le nombre de couleurs de jetons peut
        # dépasser la hauteur disponible (même principe que le tableau
        # des rounds ci-dessus, voir _build_blinds_tab) — sans ça, les
        # lignes en trop et les boutons/totaux sous le tableau devenaient
        # inaccessibles.
        chips_canvas = tk.Canvas(chips_frame, bg=FELT, highlightthickness=0)
        chips_vscroll = ttk.Scrollbar(chips_frame, orient="vertical", command=chips_canvas.yview)
        chips_canvas.configure(yscrollcommand=chips_vscroll.set)
        # vscroll empaqueté AVANT canvas (expand=True) : sinon canvas
        # capterait tout l'espace restant et la scrollbar n'aurait plus
        # de place (même raison qu'ailleurs dans ce fichier).
        chips_vscroll.pack(side="right", fill="y")
        chips_canvas.pack(side="left", fill="both", expand=True, padx=8, pady=(2, 4))

        self.chips_rows_frame = ttk.Frame(chips_canvas)
        chips_canvas.create_window((0, 0), window=self.chips_rows_frame, anchor="nw")
        self.chips_rows_frame.bind(
            "<Configure>",
            lambda e: (
                chips_canvas.configure(scrollregion=chips_canvas.bbox("all")),
                # Largeur du tableau fixée à celle de son contenu (les
                # colonnes ont une largeur fixe, indépendante du nombre de
                # lignes) : contrairement au tableau des rounds, il n'a
                # pas à s'étirer sur l'espace restant, seule la hauteur
                # doit défiler.
                chips_canvas.configure(width=self.chips_rows_frame.winfo_reqwidth()),
            ),
        )

        is_mac = self.tk.call("tk", "windowingsystem") == "aqua"

        def _on_chips_mousewheel(event):
            if is_mac:
                chips_canvas.yview_scroll(int(-1 * event.delta), "units")
            else:
                chips_canvas.yview_scroll(int(-1 * event.delta / 120), "units")

        chips_canvas.bind("<Enter>", lambda e: chips_canvas.bind_all("<MouseWheel>", _on_chips_mousewheel))
        chips_canvas.bind("<Leave>", lambda e: chips_canvas.unbind_all("<MouseWheel>"))

        self._chip_row_vars = []
        self._refresh_chips_tab()

    def _load_chip_denominations(self):
        raw = self.db.get_setting("chip_denominations_json", "")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        cleaned = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                value = int(item.get("value", 0))
            except (TypeError, ValueError):
                value = 0
            try:
                count = int(item.get("count", 0))
            except (TypeError, ValueError):
                count = 0
            cleaned.append({
                "name": str(item.get("name", "") or "").strip(),
                "color": str(item.get("color", "") or "#000000"),
                # Nom de fichier dans ~/.poker_tournament/chip_images/ (voir
                # chip_images.py) si cette dénomination utilise une image
                # de jeton plutôt qu'une pastille de couleur ; vide sinon.
                "image": str(item.get("image", "") or "").strip(),
                "value": value,
                "count": count,
            })
        return cleaned

    def _refresh_chips_tab(self):
        for w in self.chips_rows_frame.winfo_children():
            w.destroy()
        self._chip_row_vars = []

        for col, h in enumerate(["Jeton", "", "Valeur", "Nb/joueur", "Total", ""]):
            ttk.Label(
                self.chips_rows_frame, text=h, font=("Helvetica", 9, "bold"), foreground=GOLD_DARK,
            ).grid(row=0, column=col, padx=3, pady=(0, 4), sticky="w")

        for i, d in enumerate(self._load_chip_denominations()):
            self._add_chip_widget_row(i, d)
        self._update_chips_total()

    def _add_chip_widget_row(self, row_index, data):
        grid_row = row_index + 1  # ligne 0 = en-têtes (voir _refresh_chips_tab)
        row_vars = {
            "name": tk.StringVar(value=data.get("name", "")),
            "color": tk.StringVar(value=data.get("color", "#000000")),
            "value": tk.StringVar(value=str(data.get("value", 0))),
            "count": tk.StringVar(value=str(data.get("count", 0))),
            # Pas une tk.Var : seulement modifié depuis _pick_color/_pick_image
            # ci-dessous (jamais tapé directement), lu par
            # _collect_chips_from_widgets.
            "image": data.get("image", "") or "",
        }
        name_entry = ttk.Entry(self.chips_rows_frame, textvariable=row_vars["name"], width=10)
        name_entry.grid(row=grid_row, column=0, padx=3, pady=2)

        swatch = tk.Canvas(
            self.chips_rows_frame, width=20, height=20, highlightthickness=0, bg=FELT,
        )
        swatch.grid(row=grid_row, column=1, padx=3, pady=2)
        row_vars["_swatch"] = swatch

        def _render_swatch(rv=row_vars, sw=swatch):
            """Dessine la pastille : l'image de jeton choisie si elle est
            renseignée et son fichier existe encore, sinon la couleur
            unie — jamais les deux à la fois."""
            sw.delete("all")
            path = chip_images.get_chip_image_path(rv["image"]) if rv["image"] else None
            photo = load_thumbnail(path, 20) if path else None
            if photo is not None:
                sw.create_image(10, 10, image=photo)
                sw.image = photo  # garder une référence (sinon GC par Tk)
            else:
                sw.create_oval(2, 2, 18, 18, fill=rv["color"].get(), outline=GOLD_DARK)

        _render_swatch()

        def _pick_color(rv=row_vars):
            _, hex_color = colorchooser.askcolor(
                color=rv["color"].get(), title="Choisir une couleur", parent=self,
            )
            if hex_color:
                rv["color"].set(hex_color)
                if rv["image"]:
                    chip_images.delete_chip_image(rv["image"])
                    rv["image"] = ""
                _render_swatch()
                self._autosave_chips()

        def _pick_image(rv=row_vars):
            path = filedialog.askopenfilename(
                title="Choisir une image de jeton",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.gif"), ("Tous les fichiers", "*.*")],
                parent=self,
            )
            if not path:
                return
            if not PIL_AVAILABLE:
                messagebox.showerror(
                    "Module manquant",
                    "L'aperçu des images de jetons nécessite le paquet 'Pillow', "
                    "qui n'est pas installé.\n\nOuvrez un terminal et tapez :\n\n"
                    "    pip3 install Pillow",
                    parent=self,
                )
                return
            old_image = rv["image"]
            rv["image"] = chip_images.save_chip_image_from_file(path)
            if old_image:
                chip_images.delete_chip_image(old_image)
            _render_swatch()
            self._autosave_chips()

        def _remove_image(rv=row_vars):
            if rv["image"]:
                chip_images.delete_chip_image(rv["image"])
                rv["image"] = ""
                _render_swatch()
                self._autosave_chips()

        def _show_swatch_menu(event, rv=row_vars):
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Choisir une couleur...", command=_pick_color)
            menu.add_command(label="Choisir une image de jeton...", command=_pick_image)
            if rv["image"]:
                menu.add_command(label="Retirer l'image (revenir à la couleur)", command=_remove_image)
            menu.tk_popup(event.x_root, event.y_root)

        swatch.bind("<Button-1>", _show_swatch_menu)
        Tooltip(swatch, "Cliquer pour choisir une couleur ou une image de jeton.")

        value_entry = ttk.Entry(self.chips_rows_frame, textvariable=row_vars["value"], width=8)
        value_entry.grid(row=grid_row, column=2, padx=3, pady=2)
        count_entry = ttk.Entry(self.chips_rows_frame, textvariable=row_vars["count"], width=8)
        count_entry.grid(row=grid_row, column=3, padx=3, pady=2)

        total_lbl = ttk.Label(self.chips_rows_frame, text="0", width=8)
        total_lbl.grid(row=grid_row, column=4, padx=3, pady=2, sticky="w")
        row_vars["_total_lbl"] = total_lbl

        # Sauvegarde automatique dès qu'un champ est modifié (nom, valeur,
        # nombre) : évite de perdre la saisie si l'utilisateur quitte sans
        # avoir pensé à cliquer "Enregistrer les jetons" — ce dernier reste
        # utile pour signaler une valeur invalide (texte au lieu d'un
        # nombre, valeur négative...).
        for entry in (name_entry, value_entry, count_entry):
            entry.bind("<KeyRelease>", lambda e: self._autosave_chips())

        del_btn = ttk.Button(
            self.chips_rows_frame, text="🗑", width=3,
            command=lambda idx=row_index: self._delete_chip_row(idx),
        )
        del_btn.grid(row=grid_row, column=5, padx=3, pady=2)

        self._chip_row_vars.append(row_vars)

    def _collect_chips_from_widgets(self, strict=True):
        """Lit les champs actuellement affichés. `strict=True` (utilisé
        pour "Enregistrer les jetons") signale les valeurs invalides ;
        `strict=False` (utilisé pour ajouter/supprimer une ligne) les
        remplace silencieusement par 0 plutôt que de bloquer l'action."""
        result = []
        for i, rv in enumerate(self._chip_row_vars, start=1):
            name = rv["name"].get().strip()
            color = rv["color"].get().strip() or "#000000"
            try:
                value = int(rv["value"].get())
            except (ValueError, tk.TclError):
                if strict:
                    messagebox.showerror(
                        "Erreur", f"Ligne {i} : « Valeur » doit être un nombre entier.",
                    )
                    return None
                value = 0
            try:
                count = int(rv["count"].get())
            except (ValueError, tk.TclError):
                if strict:
                    messagebox.showerror(
                        "Erreur", f"Ligne {i} : « Nombre/joueur » doit être un nombre entier.",
                    )
                    return None
                count = 0
            if strict and (value < 0 or count < 0):
                messagebox.showerror("Erreur", f"Ligne {i} : les valeurs ne peuvent pas être négatives.")
                return None
            result.append({
                "name": name, "color": color, "image": rv.get("image", ""),
                "value": max(0, value), "count": max(0, count),
            })
        return result

    def _update_chips_total(self):
        rows = self._collect_chips_from_widgets(strict=False) or []
        for rv, r in zip(self._chip_row_vars, rows):
            rv["_total_lbl"].config(text=f"{r['value'] * r['count']:,}".replace(",", " "))
        total_count = sum(r["count"] for r in rows)
        total_value = sum(r["value"] * r["count"] for r in rows)
        self.chips_count_total_lbl.config(
            text=f"Nombre des jetons / joueur : {total_count:,}".replace(",", " ")
        )
        self.chips_total_lbl.config(
            text=f"Valeur des jetons / joueur : {total_value:,}".replace(",", " ")
        )

    def _persist_chip_denominations(self, denominations):
        self.db.set_settings({"chip_denominations_json": json.dumps(denominations, ensure_ascii=False)})

    def _autosave_chips(self):
        """Enregistre en continu ce qui est actuellement saisi (valeurs
        invalides remplacées par 0, sans bloquer ni avertir — voir
        "Enregistrer Jetons sous..." pour une sauvegarde avec validation
        stricte, en plus nommée pour être réutilisée sur d'autres
        tournois), et met à jour les totaux affichés."""
        denominations = self._collect_chips_from_widgets(strict=False) or []
        self._persist_chip_denominations(denominations)
        self._update_chips_total()

    def _add_chip_row(self):
        denominations = self._collect_chips_from_widgets(strict=False) or []
        denominations.append({"name": "", "color": "#000000", "image": "", "value": 0, "count": 0})
        self._persist_chip_denominations(denominations)
        self._refresh_chips_tab()

    def _delete_chip_row(self, index):
        denominations = self._collect_chips_from_widgets(strict=False) or []
        if 0 <= index < len(denominations):
            removed = denominations.pop(index)
            # Ne pas laisser un fichier image orphelin dans le stockage
            # (~/.poker_tournament/chip_images/) une fois sa dénomination
            # supprimée.
            if removed.get("image"):
                chip_images.delete_chip_image(removed["image"])
        self._persist_chip_denominations(denominations)
        self._refresh_chips_tab()

    def _save_chips_as_template(self):
        """Applique les modifications du tableau à ce tournoi (comme
        l'ancien bouton "Enregistrer les jetons"), ET enregistre le jeu de
        jetons sous un nom choisi par l'utilisateur, pour pouvoir le
        réappliquer plus tard à d'autres tournois via "Récupérer
        Jetons..." (voir chip_templates.py)."""
        denominations = self._collect_chips_from_widgets(strict=True)
        if denominations is None:
            return
        self._persist_chip_denominations(denominations)
        self._update_chips_total()

        dlg = SaveTemplateAsDialog(
            self,
            "Enregistrer Jetons sous",
            "Nom de ce jeu de jetons (cliquez un modèle existant "
            "ci-dessous pour l'écraser, ou entrez un nouveau nom) :",
            chip_templates.list_templates(),
        )
        name = dlg.result
        if not name:
            return
        if name in chip_templates.list_templates():
            if not messagebox.askyesno(
                "Confirmer",
                f"Un modèle nommé « {name} » existe déjà. Le remplacer ?",
                parent=self,
            ):
                return
        chip_templates.save_template(name, denominations)
        messagebox.showinfo(
            "Jetons enregistrés",
            f"Les jetons ont été mis à jour pour ce tournoi, et enregistrés "
            f"sous « {name} » pour une réutilisation future.",
            parent=self,
        )

    def _open_chip_templates(self):
        ChipTemplatesDialog(self)

    def _blind_rounds_from_db(self):
        """Regroupe la liste plate de la base (niveaux + pauses) en rounds
        {duration, sb, bb, ante, pause}, un round par niveau de blindes."""
        rounds = []
        for lvl in self.db.get_blind_structure():
            if lvl["is_break"]:
                if rounds:
                    rounds[-1]["pause"] += lvl["duration_minutes"]
                # une pause sans round précédent (structure mal formée) est
                # ignorée : cas qui ne devrait pas se produire en pratique.
                continue
            rounds.append({
                "duration": lvl["duration_minutes"],
                "sb": lvl["small_blind"],
                "bb": lvl["big_blind"],
                "ante": lvl["ante"],
                "pause": 0,
            })
        return rounds

    def _on_blinds_field_width_change(self):
        """Valide et mémorise la largeur choisie (voir le Spinbox "Largeur
        des champs" de _build_blinds_tab), puis réaffiche le tableau des
        rounds avec cette nouvelle largeur."""
        try:
            width = int(self.blinds_field_width_var.get())
        except (tk.TclError, ValueError):
            width = 10
        width = max(5, min(25, width))
        self.blinds_field_width_var.set(width)
        export_prefs.save_value("blinds_field_width", width)
        self._refresh_blinds_tab()

    def _toggle_blinds_start_time(self):
        """Clic sur l'en-tête "Hr de Début" (voir _refresh_blinds_tab) :
        bascule toute la colonne entre l'heure réelle de l'ordinateur (au
        moment de CE clic, figée jusqu'au prochain clic — pas une horloge
        qui continue d'avancer) et 00:00. Purement un aperçu de
        planification : ne touche à rien en base de données, ne modifie
        aucune donnée du tournoi ni du chronomètre déjà en cours."""
        if self._blinds_start_now_minutes is None:
            now = datetime.now()
            self._blinds_start_now_minutes = now.hour * 60 + now.minute
        else:
            self._blinds_start_now_minutes = None
        self._refresh_blinds_tab()

    def _refresh_blinds_tab(self):
        for w in self.blinds_rows_frame.winfo_children():
            w.destroy()
        self._blind_row_vars = []

        headers = ["Round", "Hr de Début", "Durée (min)", "Petite Blind", "Grosse Blind",
                   "Ante", "Durée Pause (min)", ""]
        for col, h in enumerate(headers):
            header_lbl = ttk.Label(self.blinds_rows_frame, text=h, font=("Helvetica", 9, "bold"),
                                    foreground=GOLD_DARK)
            header_lbl.grid(row=0, column=col, padx=6, pady=(0, 6), sticky="w")
            if col == 1:
                # "Hr de Début" bascule heure réelle <-> 00:00 au clic
                # (voir _toggle_blinds_start_time) — seul en-tête cliquable
                # de ce tableau, d'où le curseur main + l'astuce ci-dessous
                # pour le rendre repérable (rien d'autre ne change dans
                # cette colonne quand on ne clique pas).
                header_lbl.configure(cursor="hand2")
                header_lbl.bind("<Button-1>", lambda e: self._toggle_blinds_start_time())
                Tooltip(
                    header_lbl,
                    "Cliquer pour basculer entre l'heure réelle de\n"
                    "l'ordinateur (au moment du clic) et 00:00 comme\n"
                    "point de départ de toute la colonne.",
                )

        rounds = self._blind_rounds_from_db()
        if not rounds:
            rounds = [{"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0}]

        # Heure de début de chaque round : soit le temps écoulé depuis le
        # début du tournoi (base 00:00, comportement d'origine), soit
        # l'heure réelle au moment du dernier clic sur "Hr de Début" +
        # ce même temps écoulé (voir _blinds_start_now_minutes /
        # _toggle_blinds_start_time) — dans les deux cas, chaque round
        # suivant démarre à la fin du round précédent + sa pause
        # éventuelle (Durée Pause). Modulo 24h : un tournoi qui dépasse
        # minuit affiche "01:15" plutôt que "25:15".
        base_minutes = self._blinds_start_now_minutes or 0
        elapsed_minutes = 0
        for i, rnd in enumerate(rounds, start=1):
            row_vars = {
                "duration": tk.StringVar(value=str(rnd["duration"])),
                "sb": tk.StringVar(value=str(rnd["sb"])),
                "bb": tk.StringVar(value=str(rnd["bb"])),
                "ante": tk.StringVar(value=str(rnd["ante"])),
                "pause": tk.StringVar(value=str(rnd["pause"])),
            }
            self._blind_row_vars.append(row_vars)

            start_h, start_m = divmod((base_minutes + elapsed_minutes) % (24 * 60), 60)
            ttk.Label(self.blinds_rows_frame, text=str(i)).grid(row=i, column=0, padx=6, pady=2)
            ttk.Label(self.blinds_rows_frame, text=f"{start_h:02d}:{start_m:02d}").grid(
                row=i, column=1, padx=6, pady=2
            )
            field_width = self.blinds_field_width_var.get()
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["duration"], width=field_width).grid(row=i, column=2, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["sb"], width=field_width).grid(row=i, column=3, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["bb"], width=field_width).grid(row=i, column=4, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["ante"], width=field_width).grid(row=i, column=5, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["pause"], width=field_width).grid(row=i, column=6, padx=6, pady=2)
            elapsed_minutes += rnd["duration"] + rnd["pause"]

            actions = ttk.Frame(self.blinds_rows_frame)
            actions.grid(row=i, column=7, padx=6, pady=2)
            add_btn = ttk.Button(actions, text="➕", width=3,
                                  command=lambda idx=i: self._add_blind_round(idx))
            add_btn.pack(side="left", padx=1)
            Tooltip(add_btn, "Insère un nouveau round juste après celui-ci\n(copie ses blindes/ante).")
            del_btn = ttk.Button(actions, text="🗑", width=3,
                                  command=lambda idx=i: self._delete_blind_round(idx))
            del_btn.pack(side="left", padx=1)
            Tooltip(del_btn, "Supprime ce round.")

    def _collect_blinds_from_widgets(self):
        """Lit les champs actuellement affichés (y compris non enregistrés)
        et renvoie la liste de rounds {duration, sb, bb, ante, pause}, ou
        None (avec un message d'erreur) si une valeur saisie est invalide."""
        rounds = []
        for i, row_vars in enumerate(self._blind_row_vars, start=1):
            try:
                duration = int(row_vars["duration"].get())
                sb = int(row_vars["sb"].get())
                bb = int(row_vars["bb"].get())
                ante = int(row_vars["ante"].get())
                pause = int(row_vars["pause"].get())
            except (ValueError, tk.TclError):
                messagebox.showerror(
                    "Erreur", f"Round {i} : toutes les valeurs doivent être des nombres entiers."
                )
                return None
            if duration < 1:
                messagebox.showerror("Erreur", f"Round {i} : la durée doit être d'au moins 1 minute.")
                return None
            if sb < 0 or bb < 0 or ante < 0 or pause < 0:
                messagebox.showerror("Erreur", f"Round {i} : les valeurs ne peuvent pas être négatives.")
                return None
            rounds.append({"duration": duration, "sb": sb, "bb": bb, "ante": ante, "pause": pause})
        return rounds

    def _rounds_to_flat_structure(self, rounds):
        """Convertit les rounds édités (une ligne = un niveau + sa pause
        éventuelle) vers la liste plate attendue par
        database.set_blind_structure (niveaux et pauses en lignes
        distinctes, comme le reste de l'application le suppose)."""
        flat = []
        for rnd in rounds:
            flat.append({
                "small_blind": rnd["sb"], "big_blind": rnd["bb"], "ante": rnd["ante"],
                "duration_minutes": rnd["duration"], "is_break": False,
            })
            if rnd["pause"] > 0:
                flat.append({
                    "small_blind": 0, "big_blind": 0, "ante": 0,
                    "duration_minutes": rnd["pause"], "is_break": True, "break_label": "Pause",
                })
        return flat

    def _save_blinds_as_template(self):
        """Applique les modifications du tableau à ce tournoi (comme
        l'ancien bouton "Enregistrer les modifications"), ET enregistre la
        structure sous un nom choisi par l'utilisateur, pour pouvoir la
        réappliquer plus tard à d'autres tournois via "Récupérer
        Blindes..." (voir blind_templates.py)."""
        rounds = self._collect_blinds_from_widgets()
        if rounds is None:
            return
        flat = self._rounds_to_flat_structure(rounds)

        dlg = SaveTemplateAsDialog(
            self,
            "Enregistrer Blindes sous",
            "Nom de ce modèle de structure de blindes (cliquez un modèle "
            "existant ci-dessous pour l'écraser, ou entrez un nouveau nom) :",
            blind_templates.list_templates(),
        )
        name = dlg.result
        if not name:
            return
        if name in blind_templates.list_templates():
            if not messagebox.askyesno(
                "Confirmer",
                f"Un modèle nommé « {name} » existe déjà. Le remplacer ?",
            ):
                return
        blind_templates.save_template(name, flat)

        self.db.set_blind_structure(flat)
        self._refresh_blinds_tab()
        if hasattr(self, "blinds_tree"):
            self._refresh_clock_tab()
        messagebox.showinfo(
            "Structure enregistrée",
            f"La structure de blindes a été mise à jour pour ce tournoi, "
            f"et enregistrée sous « {name} » pour une réutilisation future.",
        )

    def _open_blind_templates(self):
        BlindTemplatesDialog(self)

    def _add_blind_round(self, after_index=None):
        rounds = self._collect_blinds_from_widgets()
        if rounds is None:
            return
        if after_index is None or not rounds:
            new_round = dict(rounds[-1]) if rounds else {"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0}
            new_round["pause"] = 0
            rounds.append(new_round)
        else:
            new_round = dict(rounds[after_index - 1])
            new_round["pause"] = 0
            rounds.insert(after_index, new_round)
        self.db.set_blind_structure(self._rounds_to_flat_structure(rounds))
        self._refresh_blinds_tab()

    def _delete_blind_round(self, index):
        rounds = self._collect_blinds_from_widgets()
        if rounds is None:
            return
        if len(rounds) <= 1:
            messagebox.showerror("Erreur", "La structure doit contenir au moins un round.")
            return
        if not messagebox.askyesno("Confirmer", f"Supprimer le round {index} ?"):
            return
        del rounds[index - 1]
        self.db.set_blind_structure(self._rounds_to_flat_structure(rounds))
        self._refresh_blinds_tab()

    def _reset_blind_structure_from_tab(self):
        if messagebox.askyesno("Confirmer", "Remplacer la structure actuelle par la structure standard ?"):
            self.db.set_blind_structure(default_blind_structure())
            self._refresh_blinds_tab()
            if hasattr(self, "blinds_tree"):
                self._go_to_level(1)

    def _apply_blinds_from_tab(self):
        """Bouton "Appliquer" (juste à droite de "Structure standard",
        demande du 2026-09-24) : applique à ce tournoi les valeurs
        ACTUELLEMENT visibles dans le tableau, sans les enregistrer comme
        modèle réutilisable (voir _save_blinds_as_template pour ça) et
        SANS confirmation (décision explicite : ce bouton ne fait que
        committer ce que l'utilisateur voit déjà à l'écran, contrairement
        à "Structure standard" ci-dessus, qui remplace tout par une
        structure par défaut invisible du tableau).

        Réutilise EXACTEMENT la même chaîne de validation/conversion que
        _add_blind_round/_delete_blind_round/_save_blinds_as_template :
        _collect_blinds_from_widgets() gère déjà la validation (montre
        son propre message d'erreur précis et renvoie None SANS toucher
        self.db en cas de valeur invalide, voir sa docstring) — rien à
        dupliquer ici, la structure précédente et le niveau courant
        restent alors intégralement intacts.

        Tournoi en cours (demande explicite du 2026-09-24) : ne
        réinitialise JAMAIS current_level_order à 1 (contrairement à
        "Structure standard" ci-dessus, qui appelle _go_to_level(1)) — ni
        level_start_epoch, ni paused_accum_seconds, ni l'état pause/
        démarrage du chrono, tous laissés intacts. Seule exception,
        reprise TELLE QUELLE de la règle déjà validée et déjà en
        production dans _generate_custom_blind_structure (bouton
        "Régénérer la structure de blindes..." de Paramètres, qui
        fonctionne lui aussi en plein milieu d'un tournoi) : si la
        nouvelle structure compte désormais MOINS de lignes que le
        niveau courant, ce dernier est ramené au dernier niveau valide —
        jamais au niveau 1. Si l'utilisateur raccourcit la durée du
        niveau EN COURS sous le temps déjà écoulé, le mécanisme normal
        du Chronomètre (_remaining_seconds) peut faire passer
        automatiquement au niveau suivant dès le prochain tick — c'est le
        comportement déjà existant pour tout changement de durée du
        niveau courant, jamais un cas spécial ajouté ici.

        _refresh_clock_tab() (si l'onglet/écran existe déjà, comme pour
        _save_blinds_as_template) recharge le Chronomètre ET l'écran
        projecteur d'un seul coup (voir sa fin, self.clock_window.
        refresh(...)) — aucun appel séparé nécessaire pour le Chrono
        Projo."""
        rounds = self._collect_blinds_from_widgets()
        if rounds is None:
            return
        flat = self._rounds_to_flat_structure(rounds)
        self.db.set_blind_structure(flat)
        current_order = self.db.get_setting_int("current_level_order", 1)
        if current_order > len(flat):
            self.db.set_settings({"current_level_order": len(flat)})
        self._refresh_blinds_tab()
        if hasattr(self, "blinds_tree"):
            self._refresh_clock_tab()

    # ---------------------------------------------------------------
    # Onglet Gains
    # ---------------------------------------------------------------
    # Vitesse du défilement automatique de l'onglet Classement (utilisé
    # seulement quand le nombre de lignes dépasse la hauteur visible) :
    # ttk.Treeview ne défile qu'en lignes entières (pas en pixels comme
    # le Canvas de l'onglet Tables), donc une ligne toutes les 700 ms —
    # assez lent pour rester lisible sur un écran de vidéoprojecteur.
    CLASSEMENT_AUTOSCROLL_INTERVAL_MS = 700
    CLASSEMENT_AUTOSCROLL_PAUSE_MS = 2500  # pause en haut et en bas avant de reboucler

    def _build_payouts_tab(self):
        top = ttk.Frame(self.payouts_tab)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Button(
            top, text="Exporter le classement (Excel/CSV)...", command=self._export_classement,
        ).pack(side="left", padx=3)
        self.classement_club_listbox = build_club_filter_widget(
            top, on_change=self._refresh_payouts_tab
        )
        # Réglage global (comme le zoom des tables ou le contrôle à
        # distance) : mémorisé d'un tournoi/lancement à l'autre, pas
        # propre à ce fichier .tournoi précis.
        self.classement_scroll_var = tk.BooleanVar(
            value=export_prefs.load_value("classement_autoscroll_enabled", True)
        )
        ttk.Checkbutton(
            top, text="Défilement", variable=self.classement_scroll_var,
            command=self._on_classement_scroll_toggle,
        ).pack(side="right", padx=8)

        self.payout_summary_lbl = ttk.Label(self.payouts_tab, text="", font=("Helvetica", 11, "bold"))
        self.payout_summary_lbl.pack(padx=10, anchor="w")

        tree_frame = ttk.Frame(self.payouts_tab)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=10)
        cols = ("name", "rang", "elim_time", "elim_round", "eliminated_by")
        headers = ["Nom", "Rang", "Éliminé le", "Round", "Éliminé par"]
        self.payouts_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=15)
        for c, h in zip(cols, headers):
            self.payouts_tree.heading(c, text=h)
            self.payouts_tree.column(c, width=150, anchor="center")
        self.payouts_tree.column("name", anchor="w")
        # Tri au clic, seulement sur Nom et Rang (voir _sort_classement_by
        # / _classement_rows) ; ré-appuyer sur le même en-tête inverse
        # l'ordre.
        self.classement_sort = {"column": None, "ascending": True}
        self.payouts_tree.heading("name", command=lambda: self._sort_classement_by("name"))
        self.payouts_tree.heading("rang", command=lambda: self._sort_classement_by("rang"))
        self._update_classement_sort_headings()
        payouts_scrollbar = ttk.Scrollbar(
            tree_frame, orient="vertical", command=self.payouts_tree.yview
        )
        self.payouts_tree.configure(yscrollcommand=payouts_scrollbar.set)
        self.payouts_tree.pack(side="left", fill="both", expand=True)
        payouts_scrollbar.pack(side="right", fill="y")
        # Molette de souris, en complément du défilement automatique.
        self.payouts_tree.bind(
            "<MouseWheel>",
            lambda e: self.payouts_tree.yview_scroll(int(-e.delta / 120 * 3) or (-3 if e.delta > 0 else 3), "units"),
        )

        self._classement_scroll_after_id = None
        self._classement_scroll_paused = False
        self._classement_autoscroll_tick()

    def _sort_classement_by(self, column):
        if self.classement_sort["column"] == column:
            self.classement_sort["ascending"] = not self.classement_sort["ascending"]
        else:
            self.classement_sort["column"] = column
            self.classement_sort["ascending"] = True
        self._refresh_payouts_tab()

    def _update_classement_sort_headings(self):
        labels = {"name": "Nom", "rang": "Rang"}
        for col, label in labels.items():
            if self.classement_sort["column"] == col:
                arrow = " ▲" if self.classement_sort["ascending"] else " ▼"
                self.payouts_tree.heading(col, text=label + arrow)
            else:
                self.payouts_tree.heading(col, text=label)

    def _classement_rows(self):
        """Lignes du Classement : uniquement les joueurs déjà classés (un
        rang déterminé — vainqueur ou éliminé), pour rester simple. Un
        joueur encore actif en cours de tournoi n'a pas encore de rang
        (voir Database.players_rows) et n'apparaît donc jamais ici — le
        compteur "En cours" du résumé suffit à savoir combien il en
        reste. Par défaut : du plus récemment classé (rang le plus bas)
        au plus ancien ; cliquer Nom/Rang trie autrement. Filtré en plus
        par le club coché dans le filtre "Club" (voir _build_payouts_tab
        / get_selected_clubs) — aucune coche = tous les clubs. Le club
        pris en compte est celui, actuel, du répertoire (roster.py), pas
        l'éventuel club figé dans ce tournoi au moment de l'inscription
        (souvent resté vide sur d'anciens tournois, avant que le club du
        joueur ne soit renseigné dans le répertoire) — comme pour le
        filtre équivalent de l'onglet Statistiques. Un joueur toujours
        sans club dans le répertoire compte pour le club réglé dans
        Paramètres, pas pour "aucun club"."""
        rows = [r for r in self.db.players_rows() if r["rang"] is not None]
        selected_clubs = get_selected_clubs(self.classement_club_listbox)
        if selected_clubs:
            home_club = export_prefs.load_value("club_name", "").strip()
            rows = [
                r for r in rows
                if (roster.get_club(r["name"]) or home_club) in selected_clubs
            ]
        column = self.classement_sort["column"]
        ascending = self.classement_sort["ascending"]

        if column == "name":
            rows.sort(key=lambda r: r["name"].lower())
        else:
            rows.sort(key=lambda r: r["rang"])
        if not ascending:
            rows.reverse()
        return rows

    def _refresh_payouts_tab(self):
        players = self.db.list_players()
        eliminated_count = sum(1 for p in players if p["status"] == "eliminated")
        active_count = sum(1 for p in players if p["status"] == "active")
        self.payout_summary_lbl.config(
            text=f"Éliminés : {eliminated_count}   |   En cours : {active_count}   |   "
                 f"Total : {eliminated_count + active_count}"
        )

        self._update_classement_sort_headings()
        for row in self.payouts_tree.get_children():
            self.payouts_tree.delete(row)
        for r in self._classement_rows():
            self.payouts_tree.insert(
                "", "end",
                values=(
                    r["name"], r["rang"] or "-", format_datetime_fr(r["elim_time"]) or "-",
                    r["elim_round"] or "-", r["eliminated_by"] or "-",
                ),
            )
        # Repart du haut à chaque rafraîchissement plutôt que de rester sur
        # une position de défilement qui ne correspond plus forcément au
        # même contenu (comme l'onglet Tables).
        self.payouts_tree.yview_moveto(0.0)
        self._classement_scroll_paused = False

    def _classement_autoscroll_tick(self):
        """Boucle de défilement automatique et lent de l'onglet Classement,
        active seulement quand le nombre de lignes dépasse la capacité
        d'affichage à l'écran (sinon rien ne défile) et que la case
        "Défilement" est cochée. Boucle indéfiniment : une fois la
        dernière ligne intégralement visible, pause pour laisser le
        temps de lire, retour en haut, nouvelle pause, puis ça redéfile
        (voir _classement_reset_to_top / _classement_resume_autoscroll)."""
        if not self.winfo_exists() or not self.payouts_tree.winfo_exists():
            return

        # N'anime que si l'onglet Classement est actuellement affiché, pour
        # ne pas défiler inutilement en arrière-plan.
        if self.notebook.tab(self.notebook.select(), "text") != "Classement":
            self._classement_scroll_after_id = self.after(500, self._classement_autoscroll_tick)
            return

        if self.classement_scroll_var.get() and not self._classement_scroll_paused:
            top_frac, bottom_frac = self.payouts_tree.yview()
            # On se fie uniquement à la fraction yview() (fiable, atteint
            # bien 1.0 une fois tout défilé) — PAS à bbox() de la dernière
            # ligne : bbox() renvoie déjà une boîte non vide dès qu'UN
            # SEUL pixel de la ligne est visible (ligne coupée en bas de
            # l'écran), ce qui arrêtait le défilement trop tôt et
            # empêchait de jamais voir la dernière ligne entièrement.
            if bottom_frac < 1.0:
                self.payouts_tree.yview_scroll(1, "units")
            elif top_frac > 0.0:
                # Dernière ligne désormais intégralement visible (tout
                # défilé) : pause ici pour laisser le temps de la lire,
                # puis retour en haut (voir _classement_reset_to_top).
                self._classement_scroll_paused = True
                self.after(self.CLASSEMENT_AUTOSCROLL_PAUSE_MS, self._classement_reset_to_top)

        self._classement_scroll_after_id = self.after(
            self.CLASSEMENT_AUTOSCROLL_INTERVAL_MS, self._classement_autoscroll_tick
        )

    def _classement_reset_to_top(self):
        """Après la pause en bas (voir _classement_autoscroll_tick) :
        retour en haut, puis nouvelle pause avant de reboucler à
        l'infini — tant que la case "Défilement" reste cochée."""
        if not self.payouts_tree.winfo_exists():
            return
        self.payouts_tree.yview_moveto(0.0)
        self.after(self.CLASSEMENT_AUTOSCROLL_PAUSE_MS, self._classement_resume_autoscroll)

    def _classement_resume_autoscroll(self):
        self._classement_scroll_paused = False

    def _on_classement_scroll_toggle(self):
        export_prefs.save_value("classement_autoscroll_enabled", self.classement_scroll_var.get())

    def _build_ranking_formula_widget(self, parent, row):
        """Construit le bloc "Système de points distribués" de l'onglet
        Paramètres (demande du 2026-09-10) — remplace l'ancien champ
        "Montant de la prime de classement en points" (réglage
        ranking_bonus_points, désormais conservé uniquement pour la
        compatibilité des anciens fichiers, voir Database.resolve_
        ranking_formula). Extrait de _build_settings_tab dans sa propre
        méthode pour rester testable isolément (voir tests/test_ranking_
        formula_widget.py).

        Comportement :
        - Combobox en lecture seule (state="readonly" : empêche toute
          saisie libre, donc toute valeur interne inconnue) ;
        - texte COURT discret sous la Combobox (RANKING_FORMULA_SHORT_
          TEXTS), mis à jour IMMÉDIATEMENT à chaque changement de
          sélection, y compris la toute première fois affichée.

        Un bouton d'aide "ⓘ" a été essayé le 2026-09-11 (popup complet
        au clic) puis DÉFINITIVEMENT retiré le 2026-09-12 : positionné
        via place() à gauche du Combobox pour ne pas déplacer ce
        dernier, il s'est avéré invisible dans l'interface réelle
        (mélanger place() avec les widgets pack()/grid() voisins, dans
        une zone qui se redessine — le Canvas défilable de cet onglet,
        voir _build_settings_tab — est un cas connu de fragilité Tk).
        Les explications détaillées des 5 formules vivent aussi dans le
        manuel utilisateur (chapitre "Onglet Paramètres", section Primes
        en détail) — et, depuis le 2026-09-14, dans un Tooltip ordinaire
        sur `ranking_lbl` lui-même (même mécanisme que TOUS les autres
        libellés de cet onglet, ex. `pko_check` ci-dessous — PAS un
        nouveau bouton/popup séparé, jamais de retour au bouton "ⓘ"
        retiré ci-dessus). `Tooltip.__init__` se contente de `widget.
        bind("<Enter>"/"<Leave>", ...)` : ces liaisons déclenchent
        normalement même si `ranking_lbl` est grisé par
        _update_primes_section_state (l'état ttk "disabled" bloque
        l'interaction — clic, saisie — pas les événements de survol de
        la souris), donc ce Tooltip fonctionne aussi section Primes
        désactivée — voir tests/test_ranking_formula_widget.py.

        Texte court sous la Combobox (RANKING_FORMULA_SHORT_TEXTS)
        inchangé : les deux se complètent (aperçu immédiat + détail au
        survol du libellé).

        Renvoie (label, row_frame, combo, short_lbl, legacy_note_ou_
        None). L'appelant ajoute à self._primes_section_widgets les
        widgets INTERACTIFS eux-mêmes (label, combo, short_lbl, et
        legacy_note s'il existe) — PAS row_frame (un ttk.Frame n'a pas
        d'option "state" ; l'y mettre faisait échouer silencieusement
        configure(state=...) sur toute la ligne, voir le diagnostic du
        2026-09-11 : le Combobox ne réagissait alors JAMAIS à "Calculer
        les primes"). _update_primes_section_state traite `combo` à
        part (jamais state="normal", voir sa docstring)."""
        ranking_lbl = ttk.Label(parent, text="Système de points distribués :")
        ranking_lbl.grid(row=row, column=0, sticky="w", pady=4)
        # Tooltip sur le libellé (demande du 2026-09-14) : détail complet
        # des 5 formules, au survol — jamais un bouton/popup séparé (voir
        # la docstring ci-dessus sur le bouton "ⓘ" définitivement retiré).
        # Contenu EXACT demandé, texte identique à celui du manuel
        # utilisateur (chapitre "Onglet Paramètres", section Primes en
        # détail) pour qu'aucune des deux sources ne diverge de l'autre.
        Tooltip(
            ranking_lbl,
            "Aucun — aucun point n'est attribué en fonction du\n"
            "classement final.\n"
            "\n"
            "Classique — formule 100 × √N / P\n"
            "(N = nombre de joueurs du tournoi, P = place finale du\n"
            "joueur). Favorise davantage les premières places.\n"
            "\n"
            "Progressive — formule 100 × √N / √P\n"
            "(N = nombre de joueurs du tournoi, P = place finale du\n"
            "joueur). Réduit l'écart entre les premières places et\n"
            "récompense davantage la régularité.\n"
            "\n"
            "Tournois CPC — formule P(r,N) = 50 + 950×N ×\n"
            "0,12×0,88^(r-1) / (1 − 0,88^N) (N = nombre de joueurs\n"
            "du tournoi, r = place finale du joueur). Chaque joueur\n"
            "apporte 1000 points au total distribué ; répartition\n"
            "décroissante, appliquée identiquement quel que soit N.\n"
            "Arrondi par la méthode des plus grands restes (jamais\n"
            "indépendant par place) pour que la somme distribuée\n"
            "reste toujours exactement N × 1000.\n"
            "\n"
            "Sit & Go CPC — formule 1000 + 100(N+1) − 200×P\n"
            "(N = nombre de joueurs du Sit & Go, P = place finale du\n"
            "joueur). Chaque joueur apporte 1000 points au total\n"
            "distribué ; l'écart entre deux places successives est de\n"
            "200 points.",
        )

        ranking_row = ttk.Frame(parent)
        ranking_row.grid(row=row, column=1, pady=4, padx=10, sticky="w")

        formula, legacy_flat_value = self.db.resolve_ranking_formula()
        if legacy_flat_value is not None:
            # Ancien tournoi utilisant encore la valeur fixe historique
            # (ranking_bonus_points > 0) : aucune présélection parmi les
            # 4 nouveaux choix tant que le responsable n'a pas choisi
            # explicitement — voir RANKING_FORMULA_LEGACY_PLACEHOLDER et
            # _collect_and_save_all_settings (n'écrit ranking_formula que
            # si un vrai choix a été fait, jamais ce placeholder).
            initial_display = RANKING_FORMULA_LEGACY_PLACEHOLDER
            initial_internal = ""
        else:
            initial_display = RANKING_FORMULA_LABELS.get(formula, RANKING_FORMULA_LABELS[RANKING_FORMULA_CURRENT])
            initial_internal = formula

        self.ranking_formula_display_var = tk.StringVar(value=initial_display)
        ranking_formula_var = tk.StringVar(value=initial_internal)
        self.settings_vars["ranking_formula"] = ranking_formula_var

        def _on_ranking_formula_display_changed(*_a):
            # Traduit le libellé affiché (Aucun/Classique/...) vers la
            # valeur interne réellement enregistrée (none/current/...) —
            # ne se déclenche QUE sur une vraie sélection utilisateur
            # (trace ajoutée après la construction ci-dessus, jamais sur
            # la valeur initiale).
            label = self.ranking_formula_display_var.get()
            for internal, display in RANKING_FORMULA_LABELS.items():
                if display == label:
                    ranking_formula_var.set(internal)
                    break
            # Texte court sous la Combobox (demande du 2026-09-11 :
            # l'aide du bouton "ⓘ" seule n'était pas assez visible) —
            # mis à jour immédiatement à CHAQUE changement de sélection,
            # y compris la toute première fois (appelé une fois "à la
            # main" juste après la construction du widget, plus bas).
            internal = ranking_formula_var.get()
            self.ranking_formula_short_lbl.configure(
                text=RANKING_FORMULA_SHORT_TEXTS.get(internal, "")
            )

        self.ranking_formula_display_var.trace_add("write", _on_ranking_formula_display_changed)

        # combo_line : sous-frame pour la liste déroulante — ranking_row
        # (la cellule de grille) peut ainsi empiler cette ligne, le texte
        # court, ET la note "valeur fixe historique" ci-dessous SANS
        # ajouter de nouvelle ligne de grille (ce qui aurait décalé tous
        # les réglages suivants — bounty, PKO...). Ne contient QUE le
        # Combobox (le bouton d'aide "ⓘ" envisagé le 2026-09-11 a été
        # retiré le 2026-09-12, voir la docstring de cette méthode).
        combo_line = ttk.Frame(ranking_row)
        combo_line.pack(side="top", anchor="w")

        ranking_combo = ttk.Combobox(
            combo_line, textvariable=self.ranking_formula_display_var,
            values=list(RANKING_FORMULA_LABELS.values()), state="readonly", width=16,
        )
        ranking_combo.pack(side="left")

        # Texte court, discret, sous la Combobox
        # — mis à jour à chaque sélection par _on_ranking_formula_display_
        # changed ci-dessus ; initialisé explicitement ici juste après sa
        # création (la trace ne s'est pas déclenchée pour la valeur
        # affichée à la construction du widget, posée avant l'ajout de
        # la trace — même principe que ranking_formula_var/
        # initial_internal plus haut).
        self.ranking_formula_short_lbl = ttk.Label(ranking_row, text="", foreground=MUTED)
        self.ranking_formula_short_lbl.pack(side="top", anchor="w", pady=(2, 0))
        _on_ranking_formula_display_changed()

        ranking_legacy_note = None
        if legacy_flat_value is not None:
            ranking_legacy_note = ttk.Label(
                ranking_row,
                text=(
                    f"Valeur fixe historique conservée : {legacy_flat_value} points.\n"
                    "Choisissez une formule ci-dessus pour la remplacer."
                ),
                foreground=MUTED, justify="left",
            )
            ranking_legacy_note.pack(side="top", anchor="w", pady=(2, 0))

        return (
            ranking_lbl, ranking_row, ranking_combo,
            self.ranking_formula_short_lbl, ranking_legacy_note,
        )

    # ---------------------------------------------------------------
    # Onglet Paramètres
    # ---------------------------------------------------------------
    def _build_settings_tab(self):
        # Conteneur défilable : le contenu de cet onglet a grandi avec les
        # ajouts successifs, et ne tenait plus entièrement dans la fenêtre
        # sur certains écrans. Molette de souris prise en charge.
        canvas = tk.Canvas(self.settings_tab, bg=FELT, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.settings_tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        outer = ttk.Frame(canvas)
        outer_id = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind(
            "<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.bind(
            "<Configure>", lambda e: canvas.itemconfigure(outer_id, width=e.width)
        )

        is_mac = self.tk.call("tk", "windowingsystem") == "aqua"

        def _on_mousewheel(event):
            if is_mac:
                canvas.yview_scroll(int(-1 * event.delta), "units")
            else:
                canvas.yview_scroll(int(-1 * event.delta / 120), "units")

        # La molette ne fait défiler cet onglet que lorsque le curseur est
        # dessus (bind/unbind local), pour ne pas perturber le défilement
        # des autres onglets (tableaux, listes...) du reste de l'appli.
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        # Deux colonnes côte à côte : réglages généraux à gauche, structure
        # de blindes + signal de mouvements à droite.
        columns = ttk.Frame(outer)
        columns.pack(padx=20, pady=20, anchor="nw", fill="both")
        left = ttk.Frame(columns)
        left.pack(side="left", anchor="n", padx=(0, 40))
        right = ttk.Frame(columns)
        right.pack(side="left", anchor="n")

        self.settings_vars = {}

        # -- Colonne gauche : réglages généraux --------------------
        fields = [
            ("club_name", "Nom du Club"),
            ("tournament_name", "Nom du tournoi"),
            ("buyin_amount", "Montant du buy-in (€)"),
            ("rebuy_amount", "Montant d'un rebuy (€)"),
            ("addon_amount", "Montant d'un add-on (€)"),
            ("starting_chips", "Tapis de départ (chips)"),
            ("rebuy_chips", "Chips reçues pour un rebuy"),
            ("addon_chips", "Chips reçues pour un add-on"),
            ("max_seats_per_table", "Nombre de sièges par table"),
            ("min_players_per_table", "Nombre minimum de joueurs par table avant rééquilibrage"),
            ("highlight_duration_minutes", "Durée de surbrillance des derniers joueurs déplacés (minutes)"),
            ("rake_percent", "Rake / frais d'organisation (%)"),
        ]
        field_tips = {
            "max_seats_per_table": "Une fois modifié et enregistré, s'applique\nimmédiatement à toutes les tables existantes.",
            "min_players_per_table": "En dessous de ce seuil sur une table, le\nrééquilibrage la vide en priorité vers les autres.",
            "highlight_duration_minutes": "Durée pendant laquelle un joueur\ndéplacé reste surligné dans l'onglet Mouvements.",
            "rake_percent": "Prélevé sur le prize pool avant répartition des\ngains (0 = tout le prize pool est reversé aux joueurs).",
        }
        for i, (key, label) in enumerate(fields):
            lbl = ttk.Label(left, text=label + " :")
            lbl.grid(row=i, column=0, sticky="w", pady=4)
            if key in field_tips:
                Tooltip(lbl, field_tips[key])
            if key == "club_name":
                # Commun à tous les tournois/Sit & Go (voir _save_club_name) :
                # mémorisé dans les préférences partagées, pas dans ce
                # fichier .tournoi, contrairement aux autres réglages
                # ci-dessous.
                initial = export_prefs.load_value("club_name", "")
                var = tk.StringVar(value=initial)
                var.trace_add("write", lambda *a: self._save_club_name())
            elif key == "tournament_name":
                # Auto-enregistré à la frappe (voir _save_tournament_name) :
                # un champ purement cosmétique, sans effet de bord sur les
                # tables, contrairement aux autres réglages ci-dessous qui
                # eux ne s'appliquent qu'au clic sur "Enregistrer
                # Paramètres sous...".
                current = self.db.get_setting(key, "")
                if current in ("", "Nouveau tournoi", "Tournoi"):
                    # Nom encore générique (jamais renseigné) : reprend le
                    # même repli que _update_window_title (nom de fichier)
                    # et l'enregistre pour de bon, pour que ce champ, le
                    # titre de la fenêtre et les exports affichent tous la
                    # même valeur.
                    fallback = os.path.splitext(os.path.basename(self.db.path))[0]
                    if fallback:
                        current = fallback
                        self.db.set_settings({"tournament_name": current})
                var = tk.StringVar(value=current)
                var.trace_add("write", lambda *a: self._save_tournament_name())
            else:
                var = tk.StringVar(value=self.db.get_setting(key, ""))
            ttk.Entry(left, textvariable=var, width=25).grid(row=i, column=1, pady=4, padx=10)
            self.settings_vars[key] = var

        # Les quatre actions sur le formulaire, en 2 rangées de 2 boutons
        # superposées (pas les 4 côte à côte sur une seule ligne, trop
        # large — obligeait à agrandir la fenêtre Paramètres) : rangée du
        # haut = appliquer/enregistrer les réglages, rangée du bas =
        # récupérer/imprimer. Repousse les champs suivants vers le bas,
        # sans incidence (de la place en dessous) ; largeur de la fenêtre
        # inchangée.
        settings_btns_row = ttk.Frame(left)
        settings_btns_row.grid(row=len(fields), column=0, columnspan=2, pady=(15, 15))
        settings_btns_row_top = ttk.Frame(settings_btns_row)
        settings_btns_row_top.pack(side="top")
        settings_btns_row_bottom = ttk.Frame(settings_btns_row)
        settings_btns_row_bottom.pack(side="top", pady=(6, 0))

        apply_settings_btn = ttk.Button(
            settings_btns_row_top, text="✅ Appliquer à ce tournoi",
            command=self._apply_settings_button,
        )
        apply_settings_btn.pack(side="left", padx=3)
        Tooltip(
            apply_settings_btn,
            "Applique tout de suite tous les réglages ci-dessus à CE\n"
            "tournoi (ex : un nouveau \"Nombre de sièges par table\") —\n"
            "sans quoi taper une nouvelle valeur dans un champ ne change\n"
            "rien tant qu'elle n'a pas été appliquée. Ne demande rien\n"
            "d'autre (contrairement à \"Enregistrer Paramètres sous...\",\n"
            "à droite, qui sert à en garder un modèle réutilisable).",
        )
        save_as_settings_btn = ttk.Button(
            settings_btns_row_top, text="💾 Enregistrer Paramètres sous...",
            command=self._save_settings_as_template,
        )
        save_as_settings_btn.pack(side="left", padx=3)
        Tooltip(
            save_as_settings_btn,
            "Applique tous les réglages ci-dessus à ce tournoi — dans\n"
            "tous les cas, même en annulant la fenêtre de nom qui suit.\n"
            "Cette fenêtre sert seulement, EN PLUS, à enregistrer ces\n"
            "réglages sous un nom au choix pour les réutiliser plus tard\n"
            "sur d'autres tournois/Sit & Go (le nom du tournoi et la\n"
            "structure de blindes elle-même ne sont pas inclus — voir\n"
            "\"Récupérer Blindes\" pour ça séparément).",
        )
        load_settings_btn = ttk.Button(
            settings_btns_row_bottom, text="📂 Récupérer Paramètres...", command=self._open_settings_templates,
        )
        load_settings_btn.pack(side="left", padx=3)
        Tooltip(
            load_settings_btn,
            "Ouvre la liste des réglages déjà enregistrés (via\n"
            "\"Enregistrer Paramètres sous...\") pour en appliquer un à\n"
            "ce tournoi.",
        )
        print_settings_btn = ttk.Button(
            settings_btns_row_bottom, text="🖨️ Imprimer Paramètres...", command=self._print_settings_pdf,
        )
        print_settings_btn.pack(side="left", padx=3)
        Tooltip(
            print_settings_btn,
            "Enregistre tous les réglages ci-dessus (comme \"Enregistrer\n"
            "Paramètres sous...\", mais sans leur donner de nom\n"
            "réutilisable) PUIS génère un PDF récapitulatif — pour en\n"
            "garder une trace papier ou la partager.",
        )

        ttk.Separator(left, orient="horizontal").grid(
            row=len(fields) + 2, column=0, columnspan=2, sticky="ew", pady=(5, 15)
        )
        signal_title = ttk.Label(
            left, text="Signal de mouvements",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        signal_title.grid(row=len(fields) + 3, column=0, columnspan=2, sticky="w", pady=(0, 8))
        Tooltip(
            signal_title,
            "Bip sonore joué sur l'écran projecteur quand un joueur vient\n"
            "d'être déplacé de table (voir Mouvements) — permet de le\n"
            "repérer sans regarder l'écran en continu.",
        )

        signal_row = len(fields) + 4

        # Fichier son (commun à tous les tournois/Sit & Go, comme les sons
        # du Chronomètre — voir _choose_clock_sound), et "Tester le son"
        # côte à côte sur la même ligne.
        movement_sound_btn = ttk.Button(
            left, text=self._clock_sound_button_text("movement_signal_wav_path", "Fichier Wav"),
        )
        movement_sound_btn.grid(row=signal_row, column=0, sticky="ew", pady=4, padx=(0, 5))
        movement_sound_btn.config(
            command=lambda b=movement_sound_btn: self._choose_clock_sound(
                "movement_signal_wav_path", "Fichier Wav", b
            )
        )
        movement_sound_btn.bind(
            "<Button-2>",
            lambda e, b=movement_sound_btn: self._clear_clock_sound(
                "movement_signal_wav_path", "Fichier Wav", b
            ),
        )
        movement_sound_btn.bind(
            "<Button-3>",
            lambda e, b=movement_sound_btn: self._clear_clock_sound(
                "movement_signal_wav_path", "Fichier Wav", b
            ),
        )
        Tooltip(
            movement_sound_btn,
            "Fichier .wav joué à chaque déplacement de joueur entre\n"
            "tables. Commun à tous les tournois/Sit & Go.\n"
            "Clic gauche : choisir/remplacer le fichier.\n"
            "Clic droit : retirer (repli sur un bip généré automatiquement).",
        )

        ttk.Button(
            left, text="🔊  Tester le son",
            command=self._test_movement_signal,
        ).grid(row=signal_row, column=1, sticky="ew", pady=4, padx=(5, 0))

        duration_row = signal_row + 1
        duration_lbl = ttk.Label(left, text="Durée max. du signal (millisecondes, ex : 300) :")
        duration_lbl.grid(row=duration_row, column=0, sticky="w", pady=4)
        Tooltip(
            duration_lbl,
            "Tronque le fichier Wav choisi ci-dessus s'il dure plus\n"
            "longtemps que ça ; sert aussi de durée pour le bip de\n"
            "repli si aucun fichier n'est choisi.",
        )
        duration_var = tk.StringVar(value=self.db.get_setting("movement_signal_duration_ms", "300"))
        ttk.Entry(left, textvariable=duration_var, width=25).grid(
            row=duration_row, column=1, pady=4, padx=10
        )
        self.settings_vars["movement_signal_duration_ms"] = duration_var

        ttk.Label(
            left,
            text=("Astuce : le fichier de tournoi (.tournoi) contient toutes les données\n"
                  "et se sauvegarde automatiquement à chaque action. Vous pouvez le copier\n"
                  "pour en garder une sauvegarde."),
            foreground=MUTED,
        ).grid(row=duration_row + 1, column=0, columnspan=2, sticky="w", pady=10)

        folder_row = duration_row + 2
        folder_lbl = ttk.Label(left, text="Dossier par défaut :")
        folder_lbl.grid(row=folder_row, column=0, columnspan=2, sticky="w", pady=(0, 4))
        Tooltip(
            folder_lbl,
            "Dossier choisi par vous où se trouve le tournoi du jour.\n"
            "Cliquez « Parcourir... » pour le choisir — proposé ensuite\n"
            "automatiquement (avec un sous-dossier nommé d'après le jour\n"
            "de la semaine en cours, ex. vendredi) pour créer un nouveau\n"
            "tournoi, l'ouvrir, ou dans l'onglet Statistiques.",
        )
        day_folder_var = tk.StringVar(value=self.db.get_setting("tournament_day_folder", ""))
        # Auto-enregistré à la frappe comme au choix (voir _save_day_folder),
        # à la fois dans CE tournoi et dans les préférences globales
        # (tournament_prefs) pour être proposé dès l'écran d'accueil, avant
        # même l'ouverture d'un fichier .tournoi.
        day_folder_var.trace_add("write", lambda *a: self._save_day_folder())
        self.settings_vars["tournament_day_folder"] = day_folder_var
        ttk.Entry(left, textvariable=day_folder_var, width=25).grid(
            row=folder_row + 1, column=0, sticky="ew", pady=4, padx=(0, 5)
        )
        ttk.Button(
            left, text="📂 Parcourir...",
            command=lambda: self._choose_day_folder(day_folder_var),
        ).grid(row=folder_row + 1, column=1, sticky="ew", pady=4, padx=(5, 0))

        # -- Mode Test (demande du 2026-09-09) : outil de test/développement,
        # PAS un paramètre de tournoi ni une préférence permanente — jamais
        # lu ni écrit via export_prefs/settings_vars, une simple BooleanVar
        # en mémoire (voir __init__), décochée à CHAQUE lancement, propre à
        # CE process (pas partagée entre tournois, contrairement à
        # "Calculer les primes" ci-dessus : "à chaque lancement du
        # logiciel", pas "à chaque session"). Assouplit UNIQUEMENT les
        # facilités d'élimination lors des essais (voir _eliminate_selected/
        # _remote_eliminate/_test_mode_enabled) — ne change RIEN d'autre au
        # fonctionnement normal du logiciel. Indicateur "MODE TEST" affiché
        # dans le titre de la fenêtre tant qu'actif (voir
        # _update_window_title) pour ne jamais l'oublier activé par erreur.
        #
        # (La demande de téléphone en attente n'a plus de panneau intégré
        # ici : voir RemoteDeviceRequestWindow, une petite fenêtre
        # flottante totalement indépendante de cette grille — plusieurs
        # tentatives d'intégration ont toutes été abandonnées, la
        # dernière ayant élargi cette colonne au point de repousser la
        # colonne droite hors écran.)
        test_mode_row = folder_row + 2
        test_mode_check = ttk.Checkbutton(
            left, text="Mode Test", variable=self.test_mode_var,
            command=self._on_test_mode_toggle,
        )
        test_mode_check.grid(row=test_mode_row, column=0, columnspan=2, sticky="w", pady=(8, 0))
        Tooltip(
            test_mode_check,
            "Outil de test/développement — décoché à chaque lancement,\n"
            "jamais mémorisé. Assouplit uniquement l'élimination (permet\n"
            "l'élimination groupée sans désigner d'éliminateur) pour\n"
            "faciliter les essais ; ne change rien d'autre. « MODE TEST »\n"
            "s'affiche alors dans le titre de la fenêtre.",
        )

        # Préférence partagée (comme Nom du Club ci-dessus), pas propre à ce
        # tournoi : désactive dans toute l'appli les vérifications "joueur
        # déjà actif dans un autre tournoi du même dossier" (fenêtre
        # "Joueurs participants" à la création, "Inscrire un joueur" et
        # "Ajouter depuis le répertoire" en cours de tournoi — voir
        # _warn_active_conflict / _filter_active_conflicts / PlayerSelectionDialog).
        # Coché par défaut (comportement historique inchangé) ; en pratique
        # ce conflit reste rare.
        multi_table_var = tk.BooleanVar(
            value=export_prefs.load_value("check_multi_table_conflict", True)
        )
        multi_table_check = ttk.Checkbutton(
            left, text="Éviter qu'un joueur joue à deux tables à la fois",
            variable=multi_table_var,
            command=lambda: export_prefs.save_value(
                "check_multi_table_conflict", multi_table_var.get()
            ),
        )
        multi_table_check.grid(
            row=test_mode_row + 1, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        Tooltip(
            multi_table_check,
            "Si décoché, le logiciel ne vérifie plus qu'un joueur est déjà\n"
            "actif dans un autre tournoi/Sit & Go du même dossier avant de\n"
            "l'ajouter ici — en pratique, ce cas reste rare.",
        )

        # -- Jours de tournoi / Sit & Go : purement une référence pour le
        # club (utile si un autre club joue un autre jour, ex. le jeudi),
        # sans effet sur la création des dossiers — le sous-dossier
        # automatique (voir "Dossier par défaut" ci-dessus) porte de toute
        # façon le nom du jour en cours, quel qu'il soit (voir
        # tournament_day_folder_proposal / WEEKDAY_NAMES_FR). Mémorisé
        # globalement (tournament_prefs), comme "Dossier par défaut".
        days_row = test_mode_row + 2
        days_lbl = ttk.Label(
            left, text="Jours de tournoi / Sit & Go",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        days_lbl.grid(row=days_row, column=0, columnspan=2, sticky="w", pady=(14, 4))
        Tooltip(
            days_lbl,
            "Pour référence : les jours de la semaine où votre club\n"
            "organise des tournois ou des Sit & Go (ex. vendredi pour les\n"
            "tournois, dimanche pour les Sit & Go). N'affecte pas la\n"
            "création des dossiers : le sous-dossier automatique (voir\n"
            "« Dossier par défaut » ci-dessus) porte de toute façon le nom\n"
            "du jour en cours, quel qu'il soit.",
        )
        saved_days_raw = self.db.get_setting("tournament_days_of_week", DEFAULT_TOURNAMENT_DAYS)
        try:
            saved_days = {int(x) for x in saved_days_raw.split(",") if x.strip() != ""}
        except ValueError:
            saved_days = {4, 6}
        self.tournament_day_vars = {}
        days_grid = ttk.Frame(left)
        days_grid.grid(row=days_row + 1, column=0, columnspan=2, sticky="w")
        for idx, day_name in enumerate(WEEKDAY_NAMES_FR):
            var = tk.BooleanVar(value=idx in saved_days)
            var.trace_add("write", lambda *a: self._save_tournament_days())
            self.tournament_day_vars[idx] = var
            ttk.Checkbutton(
                days_grid, text=day_name.capitalize(), variable=var,
            ).grid(row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2)

        # "Durée du bandeau d'élimination"/"Timeout pour Annuler Eliminer" :
        # déplacées le 2026-09-22 (correction de présentation) sous "Un
        # seul tournoi à la fois", colonne droite — voir la fin de cette
        # méthode. Ni valeur, ni variable, ni validation, ni tooltip,
        # ni logique changée : uniquement leur position dans la grille.

        # -- Colonne droite : structure de blindes + primes --
        ttk.Label(
            right, text="Structure de blindes — niveau 1 et antes",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        blind_fields = [
            ("start_small_blind", "Small blind (niveau 1)"),
            ("start_big_blind", "Big blind (niveau 1)"),
            ("ante_start_level", "Niveau à partir duquel l'ante commence"),
            ("start_ante", "Valeur de l'ante de départ (à ce niveau)"),
        ]
        blind_field_tips = {
            "ante_start_level": "Compte uniquement les niveaux de blindes\n(les pauses ne sont pas comptées comme un niveau).",
            "start_ante": "Ante au niveau de départ choisi ci-dessus ; elle grandit\nensuite proportionnellement au big blind sur les niveaux suivants.",
        }
        for j, (key, label) in enumerate(blind_fields, start=1):
            default = {
                "start_small_blind": 25, "start_big_blind": 50,
                "ante_start_level": 4, "start_ante": 25,
            }[key]
            lbl = ttk.Label(right, text=label + " :")
            lbl.grid(row=j, column=0, sticky="w", pady=4)
            if key in blind_field_tips:
                Tooltip(lbl, blind_field_tips[key])
            var = tk.StringVar(value=self.db.get_setting(key, str(default)))
            ttk.Entry(right, textvariable=var, width=25).grid(row=j, column=1, pady=4, padx=10)
            self.settings_vars[key] = var

        # -- Durées de round + pauses programmables (chantier "Paramètres
        # > Structure des blindes — durées variables + 2 pauses
        # programmables", 2026-09-24) : 2 lignes "Durée / Nb Rounds" puis
        # 2 lignes "Durée de la pause / Après Round", chacune une sous-
        # frame packée en columnspan=2 (même technique que primes_header
        # plus bas dans cette méthode) — jamais un partage de la grille à
        # 2 colonnes partagée avec les champs simples ci-dessus, qui
        # imposerait une largeur de colonne 1 incohérente avec ces
        # champs plus étroits.
        #
        # round_duration_minutes (ligne 1) : clé HISTORIQUE conservée
        # telle quelle (compatibilité totale : un ancien tournoi qui n'a
        # que cette clé se comporte exactement comme avant — round_
        # count_1 vide = "s'applique à tous les rounds", voir structures.
        # generate_blind_structure/_duration_for_round). round_count_1/
        # round_duration_minutes_2/round_count_2 : nouvelles clés,
        # absentes de DEFAULT_SETTINGS (comme les 4 champs simples
        # ci-dessus) — Database.get_setting renvoie alors simplement le
        # repli "" fourni ici, jamais une exception, pour tout fichier
        # .tournoi antérieur à ce chantier.
        def _build_dual_field_row(row, label1, key1, default1, label2, key2, default2, width1=8, width2=6):
            line = ttk.Frame(right)
            line.grid(row=row, column=0, columnspan=2, sticky="w", pady=4)
            ttk.Label(line, text=label1 + " :").pack(side="left")
            var1 = tk.StringVar(value=self.db.get_setting(key1, default1))
            ttk.Entry(line, textvariable=var1, width=width1).pack(side="left", padx=(6, 16))
            self.settings_vars[key1] = var1
            ttk.Label(line, text=label2 + " :").pack(side="left")
            var2 = tk.StringVar(value=self.db.get_setting(key2, default2))
            ttk.Entry(line, textvariable=var2, width=width2).pack(side="left", padx=(6, 0))
            self.settings_vars[key2] = var2
            return line

        duration_row1 = _build_dual_field_row(
            len(blind_fields) + 1, "Durée d'un Round en min", "round_duration_minutes", "15",
            "Nb Rounds", "round_count_1", "",
        )
        Tooltip(
            duration_row1,
            "Durée (en minutes) des premiers rounds de jeu lors de la\n"
            "régénération ci-dessous — pas les pauses (réglages séparés\n"
            "ci-dessous). \"Nb Rounds\" vide : cette durée s'applique à\n"
            "TOUS les rounds du tournoi, la ligne suivante n'intervient\n"
            "alors jamais.",
        )
        duration_row2 = _build_dual_field_row(
            len(blind_fields) + 2, "Durée d'un Round en min", "round_duration_minutes_2", "",
            "Nb Rounds", "round_count_2", "",
        )
        Tooltip(
            duration_row2,
            "Durée des rounds suivants, une fois les \"Nb Rounds\" de la\n"
            "ligne du dessus écoulés. \"Nb Rounds\" vide ici : s'applique à\n"
            "tous les rounds restants jusqu'à la fin du tournoi. Si cette\n"
            "ligne est entièrement vide, la durée de la première ligne se\n"
            "prolonge pour le reste du tournoi.",
        )
        break_row1 = _build_dual_field_row(
            len(blind_fields) + 3, "Durée de la pause (m)", "break_minutes_1", "15",
            "Après Round", "break_after_round_1", "4",
        )
        Tooltip(
            break_row1,
            "Insère une pause de cette durée juste après le round de jeu\n"
            "numéro \"Après Round\" (jamais compté parmi les rounds : voir\n"
            "\"Nb Rounds\" ci-dessus). Laisser les deux champs vides pour\n"
            "ne définir aucune pause automatique ici.",
        )
        break_row2 = _build_dual_field_row(
            len(blind_fields) + 4, "Durée de la pause (m)", "break_minutes_2", "",
            "Après Round", "break_after_round_2", "",
        )
        Tooltip(
            break_row2,
            "Deuxième pause automatique, optionnelle — mêmes règles que\n"
            "la ligne au-dessus. Toujours après la première (round plus\n"
            "grand). Pour une 3e pause exceptionnelle, ajoutez-la\n"
            "directement dans l'onglet Blindes après régénération.",
        )

        blind_next_row = len(blind_fields) + 5
        regen_btn = ttk.Button(
            right, text="🎲  Régénérer la structure de blindes avec ces valeurs",
            command=self._generate_custom_blind_structure,
        )
        regen_btn.grid(row=blind_next_row, column=0, columnspan=2, pady=(8, 15))
        Tooltip(
            regen_btn,
            "Remplace toute la structure de blindes par une nouvelle,\n"
            "calculée à partir des valeurs ci-dessus (fonctionne aussi en\n"
            "plein milieu d'un tournoi, sans jamais revenir au niveau 1 :\n"
            "voir le bouton \"Appliquer\" de l'onglet Blindes pour la même\n"
            "garantie). Enregistre aussi TOUS les paramètres en même\n"
            "temps, comme le bouton \"Enregistrer les paramètres\" — pas\n"
            "besoin de cliquer les deux.",
        )

        bounty_start_row = blind_next_row + 1
        ttk.Separator(right, orient="horizontal").grid(
            row=bounty_start_row, column=0, columnspan=2, sticky="ew", pady=(0, 15)
        )
        # Titre "Primes" + case "Calculer les primes" côte à côte, avec un
        # simple petit espacement (demande du 2026-09-09) : regroupés
        # dans une SOUS-FRAME dédiée (pack, pas grid) plutôt que placés
        # chacun dans une colonne différente de la grille de `right` —
        # sinon la case se serait retrouvée à l'aplomb de la colonne 1
        # PARTAGÉE avec les champs larges (Entry(width=25)) de tout le
        # reste de l'onglet, créant un grand espace vide entre le titre
        # et la case au lieu d'un petit. Cette sous-frame est ensuite
        # placée en `columnspan=2` (une seule "cellule" du point de vue
        # de la grille) : elle ne force donc JAMAIS la colonne 1 à
        # s'élargir pour elle, et ne décale rien du reste de la section
        # (montants, PKO...) toujours alignée sur les colonnes 0/1
        # habituelles juste en dessous.
        primes_header = ttk.Frame(right)
        primes_header.grid(
            row=bounty_start_row + 1, column=0, columnspan=2, sticky="w", pady=(0, 8)
        )
        primes_title = ttk.Label(
            primes_header, text="Primes",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        primes_title.pack(side="left")
        Tooltip(
            primes_title,
            "4 primes en points, cumulées par joueur dans l'onglet Primes :\n"
            "Présence, Assiduité, Classement et Bounty. Leur somme donne\n"
            "le TOTAL de chaque joueur pour ce tournoi.",
        )

        # -- Interrupteur général "Calculer les primes" (demande du
        # 2026-09-09, voir le grand bloc de commentaires près de
        # _sync_primes_enabled_pref, module-level, plus haut dans ce
        # fichier). Cochée par défaut (comportement historique inchangé,
        # compatibilité des anciens tournois — voir Database.
        # DEFAULT_SETTINGS["primes_enabled"]). Décochée : plus AUCUN
        # calcul de prime/bounty/PKO nulle part (voir Database.
        # _primes_enabled et tous ses appelants), le tableau Primes reste
        # simplement vide (jamais de message à la place), et le reste de
        # la section Primes ci-dessous est grisé (valeurs déjà saisies
        # conservées intactes pour une réactivation future — voir
        # _update_primes_section_state). Réglage GLOBAL À LA SESSION (pas
        # propre à ce tournoi, voir _primes_enabled_proposed) : modifiable
        # depuis N'IMPORTE LEQUEL des tournois de la session tant
        # qu'AUCUN d'entre eux n'a démarré son chronomètre, puis
        # définitivement grisée/verrouillée pour tous dès que le premier
        # démarre (voir _sync_primes_enabled_checkbox, _primes_session_
        # locked), jusqu'à ce que tous soient refermés.
        self.primes_enabled_var = tk.BooleanVar(
            value=self.db.get_setting_int("primes_enabled", 1) == 1
        )
        self.primes_enabled_check = ttk.Checkbutton(
            primes_header, text="Calculer les primes",
            variable=self.primes_enabled_var, command=self._on_primes_enabled_toggle,
        )
        # padx=(8, 0) : le "seulement un petit espace" demandé, entre le
        # titre et la case — jamais un grand espace lié à une colonne de
        # grille partagée (voir le commentaire de primes_header ci-dessus).
        self.primes_enabled_check.pack(side="left", padx=(8, 0))
        Tooltip(
            self.primes_enabled_check,
            "Cochée (par défaut) : comportement inchangé. Décochée :\n"
            "désactive complètement le calcul des primes/bounty/PKO pour\n"
            "TOUS les tournois de la session en cours (déjà ouverts ou\n"
            "créés plus tard) — le tableau Primes reste vide, rien n'est\n"
            "calculé ni stampé nulle part ; les montants ci-dessous sont\n"
            "conservés pour une réactivation ultérieure. Modifiable\n"
            "uniquement tant qu'aucun tournoi de la session n'a démarré\n"
            "son chronomètre ; verrouillée ensuite jusqu'à ce que tous\n"
            "soient refermés.",
        )
        # État initial de la case (avant même le premier tick) : reflète
        # le verrouillage EFFECTIF (exception Mode Test comprise, voir
        # _primes_section_effectively_locked, demande du 2026-09-14) —
        # pas seulement le verrouillage réel, pour qu'un onglet Paramètres
        # ouvert alors que Mode Test est DÉJÀ coché s'affiche correctement
        # déverrouillé dès la toute première image.
        if self._primes_section_effectively_locked():
            self.primes_enabled_check.configure(state="disabled")

        # Widgets de réglage des montants/PKO ci-dessous : grisés/
        # réactivés ensemble selon self.primes_enabled_var (voir
        # _update_primes_section_state), jamais leurs VALEURS effacées.
        self._primes_section_widgets = []

        presence_lbl = ttk.Label(right, text="Montant de la prime de présence (en points) :")
        presence_lbl.grid(row=bounty_start_row + 2, column=0, sticky="w", pady=4)
        Tooltip(
            presence_lbl,
            "Points attribués à tout joueur inscrit à ce tournoi, quel que\n"
            "soit son résultat. 0 = prime désactivée.",
        )
        attendance_var = tk.StringVar(value=self.db.get_setting("attendance_bonus_points", "0"))
        attendance_entry = ttk.Entry(right, textvariable=attendance_var, width=25)
        attendance_entry.grid(row=bounty_start_row + 2, column=1, pady=4, padx=10)
        self.settings_vars["attendance_bonus_points"] = attendance_var
        self._primes_section_widgets += [presence_lbl, attendance_entry]

        assiduity_lbl = ttk.Label(right, text="Montant de la prime d'assiduité en points :")
        assiduity_lbl.grid(row=bounty_start_row + 3, column=0, sticky="w", pady=4)
        Tooltip(
            assiduity_lbl,
            "Points attribués si le joueur a été présent lors des\n"
            "N derniers tournois consécutifs (voir réglage juste en dessous),\n"
            "ce tournoi-ci inclus. 0 = prime désactivée.",
        )
        assiduity_var = tk.StringVar(value=self.db.get_setting("assiduity_bonus_points", "0"))
        assiduity_entry = ttk.Entry(right, textvariable=assiduity_var, width=25)
        assiduity_entry.grid(row=bounty_start_row + 3, column=1, pady=4, padx=10)
        self.settings_vars["assiduity_bonus_points"] = assiduity_var
        self._primes_section_widgets += [assiduity_lbl, assiduity_entry]

        consecutive_lbl = ttk.Label(right, text="Nombre de jours consécutifs :")
        consecutive_lbl.grid(row=bounty_start_row + 4, column=0, sticky="w", pady=4)
        Tooltip(
            consecutive_lbl,
            "0 = pas de prime d'assiduité. 2 = il faut être présent à ce\n"
            "tournoi ET au précédent. 3 = ce tournoi + les 2 précédents,\n"
            "etc. Une seule absence dans la chaîne annule l'éligibilité,\n"
            "et il faut assez d'historique (fichiers .tournoi du même\n"
            "dossier) pour vérifier la chaîne complète.",
        )
        consecutive_var = tk.StringVar(value=self.db.get_setting("assiduity_consecutive_days", "2"))
        consecutive_entry = ttk.Entry(right, textvariable=consecutive_var, width=25)
        consecutive_entry.grid(row=bounty_start_row + 4, column=1, pady=4, padx=10)
        self.settings_vars["assiduity_consecutive_days"] = consecutive_var
        consecutive_note = ttk.Label(
            right,
            text=("0 = pas de prime d'assiduité ; 2 = ce tournoi + le précédent ;\n"
                  "3 = ce tournoi + les 2 précédents ; etc."),
            foreground=MUTED,
        )
        consecutive_note.grid(row=bounty_start_row + 5, column=0, columnspan=2, sticky="w", pady=(0, 4))
        self._primes_section_widgets += [consecutive_lbl, consecutive_entry, consecutive_note]

        (
            ranking_lbl, _ranking_row, ranking_combo,
            ranking_short_lbl, ranking_legacy_note,
        ) = self._build_ranking_formula_widget(right, bounty_start_row + 6)
        # ranking_row (le ttk.Frame conteneur) n'est PAS ajouté ici — voir
        # la docstring de _build_ranking_formula_widget et le diagnostic
        # du 2026-09-11 : un Frame n'a pas d'option "state", configure()
        # y échouait silencieusement et ne touchait donc jamais les
        # widgets qu'il contient. On ajoute directement les widgets
        # interactifs eux-mêmes.
        self._primes_section_widgets += [ranking_lbl, ranking_combo, ranking_short_lbl]
        if ranking_legacy_note is not None:
            self._primes_section_widgets.append(ranking_legacy_note)

        bounty_lbl = ttk.Label(right, text="Montant du bounty en points :")
        bounty_lbl.grid(row=bounty_start_row + 7, column=0, sticky="w", pady=4)
        Tooltip(
            bounty_lbl,
            "Valeur fixe (en points) de chaque joueur éliminé. Si ce champ\n"
            "est à 0, la valeur est calculée automatiquement avec la\n"
            "formule 10×√N (N = nombre de joueurs du tournoi) : plus le\n"
            "champ est grand, plus éliminer un adversaire rapporte.\n"
            "Le Nombre de bounties d'un joueur = son nombre total\n"
            "d'éliminations ce tournoi-ci.",
        )
        bounty_var = tk.StringVar(value=self.db.get_setting("bounty_amount", "0"))
        bounty_entry = ttk.Entry(right, textvariable=bounty_var, width=25)
        bounty_entry.grid(row=bounty_start_row + 7, column=1, pady=4, padx=10)
        self.settings_vars["bounty_amount"] = bounty_var
        self._primes_section_widgets += [bounty_lbl, bounty_entry]

        pko_var = tk.BooleanVar(value=self.db.get_setting_int("pko_mode", 0) == 1)
        pko_check = ttk.Checkbutton(right, text="Mode PKO (prime progressive)", variable=pko_var)
        pko_check.grid(row=bounty_start_row + 8, column=0, columnspan=2, sticky="w", pady=4)
        Tooltip(
            pko_check,
            "Mécanisme interne \"Perso\" (indépendant du nouveau tableau\n"
            "de primes ci-dessus) : quand un joueur qui porte un bounty\n"
            "est éliminé, son éliminateur en garde une partie en Perso\n"
            "immédiat (réglage ci-dessous) et le reste grossit sur sa\n"
            "propre tête pour la suite du tournoi.",
        )
        self.settings_vars["pko_mode"] = pko_var
        self._primes_section_widgets.append(pko_check)

        pko_pct_lbl = ttk.Label(right, text="Part en Perso immédiat en PKO (%) :")
        pko_pct_lbl.grid(row=bounty_start_row + 9, column=0, sticky="w", pady=4)
        Tooltip(
            pko_pct_lbl,
            "% du bounty que l'éliminateur empoche immédiatement en\n"
            "Perso ; le reste (100% - ce pourcentage) s'ajoute à son\n"
            "propre bounty, à remporter par qui l'éliminera à son tour.",
        )
        pko_pct_var = tk.StringVar(value=self.db.get_setting("pko_cash_percent", "50"))
        pko_pct_entry = ttk.Entry(right, textvariable=pko_pct_var, width=25)
        pko_pct_entry.grid(row=bounty_start_row + 9, column=1, pady=4, padx=10)
        self.settings_vars["pko_cash_percent"] = pko_pct_var
        self._primes_section_widgets += [pko_pct_lbl, pko_pct_entry]

        primes_note = ttk.Label(
            right,
            text=("Le bounty s'applique aux nouvelles inscriptions/rebuys après "
                  "avoir enregistré. En mode classique, l'éliminateur empoche toute "
                  "la prime en Perso ; en PKO, une partie s'ajoute à sa propre prime."),
            foreground=MUTED, wraplength=340, justify="left",
        )
        primes_note.grid(row=bounty_start_row + 10, column=0, columnspan=2, sticky="w", pady=(4, 10))
        self._primes_section_widgets.append(primes_note)

        # État initial (grisé si la case est déjà décochée pour ce
        # tournoi, ex. réouverture d'un tournoi créé alors que "Calculer
        # les primes" était décochée, ET/OU si la session est déjà
        # verrouillée — voir _update_primes_section_state, `locked=None`
        # recalcule l'état de verrouillage ici automatiquement).
        self._update_primes_section_state(self.primes_enabled_var.get())

        # -- Raccourcis clavier "Élimination"/"Terminé"/"Chronomètre" :
        # toujours actifs, rien à activer. Voir aussi le contrôle à
        # distance ci-dessous (mêmes 3 actions, depuis un téléphone).
        voice_start_row = bounty_start_row + 11
        ttk.Separator(right, orient="horizontal").grid(
            row=voice_start_row, column=0, columnspan=2, sticky="ew", pady=(0, 15)
        )
        shortcuts_title = ttk.Label(
            right, text="Raccourcis clavier",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        shortcuts_title.grid(row=voice_start_row + 1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        Tooltip(
            shortcuts_title,
            "Ctrl+Maj+J met le chrono en pause et bascule sur l'onglet\n"
            "Joueurs ; Ctrl+Maj+C le relance (si aucun mouvement n'a eu\n"
            "lieu) ; Ctrl+Maj+T referme l'alerte de mouvement. Toujours\n"
            "actifs, rien à activer dans les réglages.",
        )
        ttk.Label(
            right,
            text=("Ctrl+Maj+J Joueurs   |   Ctrl+Maj+C Chronomètre   |   Ctrl+Maj+T Terminé"),
            foreground=MUTED, wraplength=340, justify="left",
        ).grid(row=voice_start_row + 2, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # -- Contrôle à distance depuis un téléphone : déplacé dans son
        # propre onglet "CA/LOG" (demande du 2026-09-22, "réorganisation
        # visuelle du contrôle à distance"), puis dans l'onglet "CA" seul
        # le 2026-09-24 (séparation CA/LOG en CA + LOG) — voir
        # _build_ca_tab. Emplacement UNIQUE désormais : plus aucun widget
        # de ce bloc (activation, code, Téléphones autorisés, Permissions
        # DIRTO) n'est construit ici, pour ne jamais créer de doublon ni
        # un second état indépendant.

        # -- Rééquilibrage simple guidé par UTG (voir database.py:
        # rebalance_tables/_bb_rebalance_prompt_enabled) : préférence
        # GLOBALE (comme le bloc contrôle à distance, déplacé dans
        # l'onglet CA ci-dessus). Fait partie du CONTENU DÉFILANT de
        # l'onglet, comme le reste des réglages de "right" (grid, même
        # colonne 0, même sticky="w").
        #
        # Libellé/tooltip mis à jour (clé de stockage BB_REBALANCE_PROMPT_
        # PREF_KEY et comportement INCHANGÉS, pour rester compatible avec
        # une préférence déjà enregistrée par une version antérieure) :
        # cette case ne pilote plus l'affichage d'une fenêtre Mac (retirée,
        # voir _check_pending_rebalance) mais reste le seul interrupteur
        # entre le mode "guidé" (demande quel joueur est UTG, affichée sur
        # les téléphones) et le mode automatique historique
        # (_legacy_pick_mover, voir database.py: rebalance_tables) — elle
        # a donc toujours une utilité propre, indépendante de tout
        # affichage Mac.
        # Reprend directement la ligne libérée par le bloc contrôle à
        # distance (déplacé dans l'onglet CA) — plus aucun décalage
        # +4/+5/+6/+7 à ajouter ici.
        bb_prompt_row = voice_start_row + 3
        self.bb_rebalance_prompt_var = tk.BooleanVar(
            value=export_prefs.load_value(BB_REBALANCE_PROMPT_PREF_KEY, True) is not False
        )
        bb_prompt_check = ttk.Checkbutton(
            right, text="Équilibrage guidé par UTG (demande sur le téléphone)",
            variable=self.bb_rebalance_prompt_var, command=self._on_bb_rebalance_prompt_toggle,
        )
        bb_prompt_check.grid(row=bb_prompt_row, column=0, columnspan=2, sticky="w", pady=(0, 6))
        Tooltip(
            bb_prompt_check,
            "Activée (par défaut) : lors d'un simple rééquilibrage entre\n"
            "tables (pas un cassage de table), demande directement quel\n"
            "joueur est UTG — sur tous les téléphones du contrôle à\n"
            "distance — ce joueur est alors déplacé. Rien ne s'affiche sur\n"
            "ce Mac, seul le calcul/résultat y est appliqué.\n"
            "Désactivée : aucune demande, l'ancien mécanisme historique\n"
            "choisit directement, comme \"Continuer sans désigner le\n"
            "joueur\".",
        )

        # -- "Un seul tournoi à la fois" : préférence GLOBALE (comme la
        # case juste au-dessus), cochée par défaut. N'empêche jamais de
        # basculer vers un tournoi déjà ouvert (voir LobbyDialog.
        # _open_selected) ni ne touche à quoi que ce soit de déjà ouvert
        # — bloque uniquement la CRÉATION/l'OUVERTURE d'un tournoi ou
        # Sit&Go supplémentaire (voir _block_second_tournament_if_needed,
        # appelé depuis App._open_new_window et LobbyDialog._open_
        # selected — les deux seuls endroits de tout le fichier qui
        # lancent un nouveau process de tournoi, voir spawn_app_process).
        single_tournament_row = bb_prompt_row + 1
        self.single_tournament_var = tk.BooleanVar(
            value=_single_tournament_pref_enabled()
        )
        single_tournament_check = ttk.Checkbutton(
            right, text="Un seul tournoi à la fois",
            variable=self.single_tournament_var, command=self._on_single_tournament_toggle,
        )
        single_tournament_check.grid(
            row=single_tournament_row, column=0, columnspan=2, sticky="w", pady=(0, 6)
        )
        Tooltip(
            single_tournament_check,
            "Activée (par défaut) : interdit d'ouvrir un deuxième tournoi\n"
            "ou Sit&Go tant qu'un tournoi est déjà ouvert (« Menu\n"
            "principal », Lobby...) — ne ferme ni ne modifie jamais celui\n"
            "déjà ouvert, ne fait qu'empêcher d'en lancer un second par\n"
            "erreur. Désactivée : comportement multi-tournoi habituel,\n"
            "inchangé.",
        )

        # -- Durée du bandeau d'élimination (écran projecteur + onglet
        # Chronomètre, voir _advance_elimination_banner) — déplacée le
        # 2026-09-22 (correction de présentation, demande explicite)
        # immédiatement sous "Un seul tournoi à la fois" : valeur,
        # variable, validation, tooltip et logique INCHANGÉS, seule la
        # position dans la grille change (colonne "right" désormais,
        # plus "left"). Propre à CE tournoi (self.db) mais reprise par
        # défaut pour le prochain (tournament_prefs). Un ancien fichier
        # .tournoi sans ce réglage retombe proprement sur 5 (voir
        # get_setting_int ci-dessous et dans _advance_elimination_
        # banner). INDÉPENDANTE de la "Durée (ms)" du son "Son sortie
        # d'un joueur" (fenêtre "Sons de fin de Round/Pause...") : deux
        # réglages séparés, l'un pour la durée d'AFFICHAGE du bandeau,
        # l'autre pour la durée du SON — y compris si aucun son n'est
        # configuré du tout.
        #
        # Alignement demandé explicitement (libellés à gauche, Spinbox
        # alignées à droite sur la MÊME coordonnée X, même largeur) :
        # déjà garanti structurellement par la grille — les deux
        # libellés en column=0/sticky="w", les deux Spinbox en
        # column=1/sticky="w"/width=5 identique, comme tous les autres
        # champs de cette colonne (voir blind_fields plus haut, même
        # principe) — la longueur du texte du libellé ne peut donc
        # jamais déplacer la Spinbox, chaque colonne de grille étant
        # indépendante de la largeur du contenu des autres lignes.
        elim_row = single_tournament_row + 1
        elim_lbl = ttk.Label(right, text="Durée du bandeau d'élimination (secondes) :")
        elim_lbl.grid(row=elim_row, column=0, sticky="w", pady=(14, 4))
        Tooltip(
            elim_lbl,
            "Durée d'affichage du bandeau « XXX est sorti par YYY » sur\n"
            "l'écran projecteur (voir onglet Chronomètre) après chaque\n"
            "élimination — de 0 à 30 secondes, 5 par défaut. Indépendante\n"
            "de la « Durée (ms) » du son « Son sortie d'un joueur »\n"
            "(fenêtre « Sons de fin de Round/Pause... ») : deux réglages\n"
            "séparés, même si aucun son n'est configuré. Pris en compte\n"
            "dès la prochaine élimination, sans redémarrer.\n"
            "Mettre 0 seconde pour désactiver l'affichage du bandeau\n"
            "d'élimination (le son, réglage séparé, continue de jouer).",
        )
        elim_seconds_var = tk.IntVar(
            value=max(0, min(30, self.db.get_setting_int("elimination_banner_seconds", 5)))
        )
        elim_spin = ttk.Spinbox(
            right, from_=0, to=30, width=5, textvariable=elim_seconds_var,
            command=lambda: self._save_elimination_banner_seconds(elim_seconds_var),
        )
        elim_spin.grid(row=elim_row, column=1, sticky="w", padx=10, pady=(14, 4))
        elim_spin.bind(
            "<Return>", lambda e: self._save_elimination_banner_seconds(elim_seconds_var)
        )
        elim_spin.bind(
            "<FocusOut>", lambda e: self._save_elimination_banner_seconds(elim_seconds_var)
        )
        # Tooltip propre à la Spinbox elle-même (demande du 2026-09-08) :
        # jusqu'ici seul le libellé à sa gauche (elim_lbl ci-dessus) avait
        # un tooltip — un survol direct de la Spinbox (zone de saisie ou
        # flèches haut/bas, un seul widget ttk.Spinbox donc une seule
        # zone de survol pour Tkinter) n'affichait rien. Texte volontai-
        # rement plus court que celui du libellé, qui reste inchangé.
        Tooltip(
            elim_spin,
            "Durée d'affichage du bandeau d'élimination, en secondes. "
            "Si 0, le bandeau n'est pas affiché.",
        )

        # -- Timeout pour "Annule Eliminer" (demande du 2026-09-17) : sous
        # "Durée du bandeau d'élimination" ci-dessus, même principe (CE
        # tournoi + repris par défaut pour le prochain via tournament_
        # prefs). Le contrôle réel du délai se fait dans Database.undo_
        # last_elimination() (voir sa docstring) : ce réglage-ci ne pilote
        # que l'AFFICHAGE (bouton grisé/clic droit sans effet une fois
        # dépassé) — aucune voie d'appel ne peut donc jamais le contourner
        # simplement parce que l'interface n'aurait pas encore été
        # rafraîchie.
        undo_timeout_row = elim_row + 1
        undo_timeout_lbl = ttk.Label(right, text="Timeout pour Annuler Eliminer (m) :")
        undo_timeout_lbl.grid(row=undo_timeout_row, column=0, sticky="w", pady=4)
        Tooltip(
            undo_timeout_lbl,
            "Délai, en minutes, pendant lequel « Annule Eliminer » (onglet\n"
            "Joueurs) reste disponible pour annuler la DERNIÈRE élimination\n"
            "— décompté depuis l'heure exacte de cette élimination, jamais\n"
            "seulement l'heure affichée (fiable même à cheval sur un\n"
            "changement de minute/heure). Passé ce délai, le bouton se\n"
            "grise et le clic droit sur ce joueur ne propose plus rien —\n"
            "5 minutes par défaut. Mettre 0 minute pour DÉSACTIVER "
            "complètement\n« Annule Eliminer » (0 ne signifie jamais "
            "« illimité »).",
        )
        undo_timeout_var = tk.IntVar(
            value=max(0, self.db.get_setting_int("undo_elimination_timeout_minutes", 5))
        )
        undo_timeout_spin = ttk.Spinbox(
            right, from_=0, to=180, width=5, textvariable=undo_timeout_var,
            command=lambda: self._save_undo_elimination_timeout_minutes(undo_timeout_var),
        )
        undo_timeout_spin.grid(row=undo_timeout_row, column=1, sticky="w", padx=10, pady=4)
        undo_timeout_spin.bind(
            "<Return>", lambda e: self._save_undo_elimination_timeout_minutes(undo_timeout_var)
        )
        undo_timeout_spin.bind(
            "<FocusOut>", lambda e: self._save_undo_elimination_timeout_minutes(undo_timeout_var)
        )
        # Tooltip propre à la Spinbox elle-même (même principe que
        # elim_spin ci-dessus) — rappelle explicitement le rôle de 0,
        # demande complémentaire du 2026-09-17.
        Tooltip(
            undo_timeout_spin,
            "Délai en minutes avant que « Annule Eliminer » ne soit plus "
            "disponible.\nMettre 0 minute pour désactiver complètement "
            "cette possibilité\n(0 ne signifie pas « illimité »).",
        )

    # -----------------------------------------------------------------
    # Onglet "CA" (demande du 2026-09-22, "réorganisation visuelle du
    # contrôle à distance", séparée en CA + LOG le 2026-09-24 — voir
    # _build_log_tab pour le nouvel onglet "LOG") : emplacement UNIQUE du
    # bloc Contrôle à distance/Téléphones autorisés/Permissions DIRTO —
    # retiré de Paramètres (voir _build_settings_tab, qui ne construit
    # plus rien de ce bloc) pour ne jamais créer de doublon ni un second
    # état indépendant. Fonctionnement STRICTEMENT préservé (aucune
    # logique réécrite, seulement son ancrage/sa position) : mêmes noms
    # d'attributs qu'avant (remote_control_enabled_var, remote_control_
    # code_lbl, remote_control_status_lbl, remote_devices_container,
    # remote_dirto_container...), lus/écrits par les mêmes méthodes
    # déjà existantes (_on_remote_control_toggle, _refresh_remote_
    # control_status, _refresh_remote_devices_panel, _refresh_remote_
    # control_code_label, _build_remote_dirto_permissions_widgets...).
    # -----------------------------------------------------------------
    def _build_ca_tab(self):
        """Deux zones CÔTE À CÔTE occupant toute la largeur disponible
        (gauche : Contrôle à distance/Téléphones ; droite : Permissions
        DIRTO), au même niveau vertical. Les critères de recherche du
        Journal des actions vivent désormais dans leur propre onglet
        (voir _build_log_tab, chantier "séparation CA/LOG en CA + LOG",
        2026-09-24) — cet onglet-ci ne s'occupe plus que du contrôle à
        distance.

        La fenêtre flottante d'autorisation (RemoteDeviceRequestWindow)
        n'est plus visible que sur CET onglet (voir _is_ca_tab_active,
        _refresh_remote_device_popup) et se positionne par défaut SOUS
        remote_devices_container (voir _remote_device_popup_position) —
        jamais recréée/réécrite, seulement son ancrage adapté."""
        top = ttk.Frame(self.ca_tab)
        top.pack(padx=20, pady=20, anchor="nw", fill="x")
        left = ttk.Frame(top)
        left.pack(side="left", anchor="n", padx=(0, 50))
        right = ttk.Frame(top)
        right.pack(side="left", anchor="n")

        # ================================================================
        # ZONE GAUCHE — Contrôle à distance / Téléphones
        # ================================================================
        remote_title = ttk.Label(
            left, text="Contrôle à distance (téléphone)",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        remote_title.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        Tooltip(
            remote_title,
            "Sert une petite page web (Éliminations, Plan des tables,\n"
            "Mouvements, Chronomètre, Niveaux, Photos, rééquilibrage)\n"
            "consultable depuis n'importe quel téléphone connecté au\n"
            "même réseau Wifi que cet ordinateur — rien à installer,\n"
            "juste ouvrir l'adresse affichée dans un navigateur.",
        )

        # Case + "Code : XXXXXX" côte à côte (sous-frame pack, pas grid) :
        # évite un écart disgracieux entre les deux.
        remote_check_row = ttk.Frame(left)
        remote_check_row.grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.remote_control_enabled_var = tk.BooleanVar(
            value=export_prefs.load_value("remote_control_enabled", False) is True
        )
        remote_check = ttk.Checkbutton(
            remote_check_row, text="Activer le contrôle à distance",
            variable=self.remote_control_enabled_var, command=self._on_remote_control_toggle,
        )
        remote_check.pack(side="left")

        # Code à 6 chiffres de la session en cours (demande du
        # 2026-09-09) : IDENTIQUE pour tous les tournois de cette
        # session (voir open_windows.remote_session_code) — à
        # communiquer de vive voix aux responsables dont le téléphone
        # doit être approuvé ci-dessous. JAMAIS le code de test permanent
        # 131261, qui ne doit jamais apparaître ici (voir open_windows.
        # verify_remote_code). Déjà disponible dès la construction de cet
        # onglet : App.__init__ a déjà enregistré ce tournoi (open_
        # windows.register) avant d'arriver ici, la session existe donc
        # forcément. Valeur initiale seulement : si le fichier partagé
        # est un jour régénéré (incident du 2026-09-19) pendant que ce
        # tournoi reste ouvert, ce libellé est tenu à jour par
        # _refresh_remote_control_code_label, appelée depuis _tick tant
        # que cet onglet est affiché (voir _is_ca_tab_active) —
        # jamais à chaque tick de chaque fenêtre, pour rester négligeable.
        code = open_windows.remote_session_code()
        self.remote_control_code_lbl = ttk.Label(
            remote_check_row, text=(f"Code : {code}" if code else ""),
            foreground=MUTED, font=("Helvetica", 10, "bold"),
        )
        self.remote_control_code_lbl.pack(side="left", padx=(14, 0))
        Tooltip(
            self.remote_control_code_lbl,
            "Code à saisir sur le téléphone à la première connexion —\n"
            "identique pour tous les tournois/Sit & Go ouverts en même\n"
            "temps que celui-ci, change à chaque nouvelle session (tous\n"
            "les tournois refermés puis l'application relancée). Le\n"
            "téléphone devra ensuite être approuvé ci-dessous avant de\n"
            "pouvoir contrôler quoi que ce soit.",
        )

        self.remote_control_status_lbl = ttk.Label(
            left, foreground=MUTED, justify="left", wraplength=340,
        )
        self.remote_control_status_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 10))
        self._refresh_remote_control_status()

        # -- Téléphones APPROUVÉS (Révoquer/renommer) : les demandes EN
        # ATTENTE ont leur propre fenêtre flottante (voir RemoteDevice
        # RequestWindow, positionnée SOUS ce conteneur), jamais affichées
        # ici, pour ne jamais présenter la même demande à deux endroits.
        remote_devices_title = ttk.Label(
            left, text="Téléphones autorisés", font=("Helvetica", 10, "bold"), foreground=GOLD,
        )
        remote_devices_title.grid(row=3, column=0, columnspan=2, sticky="w", pady=(2, 4))
        Tooltip(
            remote_devices_title,
            "Appareils déjà approuvés — l'approbation reste valable aux\n"
            "prochaines sessions, contrairement au code, qui change à\n"
            "chaque fois. Une NOUVELLE demande de téléphone apparaît\n"
            "dans une fenêtre flottante sous cette liste, avec un badge\n"
            "🔔 sur cet onglet tant qu'elle n'a pas été traitée.",
        )
        self.remote_devices_container = ttk.Frame(left)
        self.remote_devices_container.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        self._refresh_remote_devices_panel()

        # ================================================================
        # ZONE DROITE — Permissions DIRTO (pour ce tournoi)
        # ================================================================
        remote_dirto_title = ttk.Label(
            right, text="Permissions DIRTO (pour ce tournoi)", font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        remote_dirto_title.grid(row=0, column=0, sticky="w", pady=(0, 8))
        Tooltip(
            remote_dirto_title,
            "Un DIRTO ne dispose, sur son téléphone, QUE des fonctions\n"
            "cochées ici — propres à CE tournoi (un autre tournoi ouvert\n"
            "simultanément a ses propres permissions, indépendantes).\n"
            "« Terminer le tournoi » n'apparaît jamais dans cette liste :\n"
            "réservée aux ADMIN, jamais accordable à un DIRTO.\n"
            "Un ADMIN garde de toute façon un accès total, sans avoir\n"
            "besoin d'être également DIRTO.",
        )
        self.remote_dirto_container = ttk.Frame(right)
        self.remote_dirto_container.grid(row=1, column=0, sticky="new")
        self._build_remote_dirto_permissions_widgets(self.remote_dirto_container)

    # -----------------------------------------------------------------
    # Onglet "LOG" (créé lors du chantier "séparation CA/LOG en CA +
    # LOG", 2026-09-24 ; rendu FONCTIONNEL lors du chantier "LOG, Phase
    # 2", même jour) : filtres de recherche du Journal des actions
    # (_build_log_search_criteria_placeholder, RÉUTILISÉE telle quelle —
    # mêmes variables log_date_from_var/log_date_to_var/log_tournament_
    # var/log_user_var/log_function_var/log_player_var/log_reset_btn/
    # log_search_btn/log_export_btn qu'avant la Phase 2, désormais
    # RÉELLEMENT câblées) en haut, grand tableau de résultats
    # (self.log_tree) en dessous. Toute la lecture SQL passe par
    # action_log.py (search_actions/list_tournaments/list_users/
    # list_players/list_categories) — jamais de SQL direct ici.
    # -----------------------------------------------------------------
    def _build_log_tab(self):
        """Filtres de recherche du Journal des actions en haut (voir
        _build_log_search_criteria_placeholder), tableau de résultats
        (self.log_tree, 9 colonnes) sur toute la largeur restante en
        dessous. Chargement initial des opérations récentes (aucun
        filtre) effectué ICI, à la construction — voir _refresh_log_tab.

        self.log_count_lbl (renommé le 2026-09-25 — s'appelait log_
        truncated_lbl tant qu'il ne servait qu'à signaler un dépassement
        du plafond de 500 lignes, désormais supprimé) : affiche en
        permanence "N opération(s) affichée(s)", voir _log_count_label_
        text/_refresh_log_tab."""
        top = ttk.Frame(self.log_tab)
        top.pack(padx=20, pady=(20, 8), anchor="nw", fill="x")
        self._build_log_search_criteria_placeholder(top, row=0)

        self.log_count_lbl = ttk.Label(self.log_tab, foreground=MUTED)
        self.log_count_lbl.pack(padx=20, anchor="w")

        results = ttk.Frame(self.log_tab)
        results.pack(fill="both", expand=True, padx=20, pady=(6, 20))

        cols = ("ts", "tournament", "user", "role", "category", "action", "player", "result", "message")
        headers = [
            "Date/Heure", "Tournoi", "Utilisateur", "Rôle", "Fonction",
            "Action", "Joueur", "Résultat", "Message",
        ]
        self.log_tree = ttk.Treeview(results, columns=cols, show="headings")
        widths = {
            "ts": 140, "tournament": 150, "user": 110, "role": 70,
            "category": 110, "action": 150, "player": 130, "result": 90,
        }
        left_aligned = {"tournament", "user", "action", "player", "message"}
        for c, h in zip(cols, headers):
            self.log_tree.heading(c, text=h)
            anchor = "w" if c in left_aligned else "center"
            if c == "message":
                self.log_tree.column(c, width=220, anchor=anchor, stretch=True)
            else:
                self.log_tree.column(c, width=widths[c], anchor=anchor, stretch=False)
        log_scrollbar = ttk.Scrollbar(results, orient="vertical", command=self.log_tree.yview)
        self.log_tree.configure(yscrollcommand=log_scrollbar.set)
        self.log_tree.pack(side="left", fill="both", expand=True)
        log_scrollbar.pack(side="right", fill="y")
        # Détail complet d'une ligne (§14, chantier "LOG", Phase 2) : le
        # tableau reste compact (Message peut être tronqué visuellement),
        # un double-clic ouvre une fenêtre non modale avec tout le
        # contenu — voir _on_log_row_double_click/_show_log_detail_window.
        self.log_tree.bind("<Double-1>", self._on_log_row_double_click)

        self._log_rows_by_item = {}
        self._log_tournament_label_to_path = {}
        self._log_category_label_to_key = {}

        # Chargement initial (§11 de l'analyse validée) : opérations
        # récentes affichées dès la construction de l'onglet, sans
        # attendre un clic sur Rechercher — une seule requête indexée
        # (ts DESC LIMIT 501), négligeable même sur un journal de
        # plusieurs années.
        self._refresh_log_tab()

    def _build_log_search_criteria_placeholder(self, parent, row):
        """Critères de recherche du Journal des actions — RÉELLEMENT
        câblés depuis le chantier "LOG", Phase 2 (2026-09-24) : Du/Au/
        Tournoi/Utilisateur/Fonction/Joueur alimentent action_log.
        search_actions (voir _refresh_log_tab). 4 boutons : Réinitialiser/
        Rechercher/Exporter (_on_log_reset/_on_log_search/_on_log_export)
        et, ajouté par la correction du même jour, Purger (_on_log_purge)
        — Du/Au portent aussi un petit bouton calendrier 📅 (voir
        _show_log_date_picker), sans dépendance externe.

        Purger réutilise désormais EXACTEMENT les mêmes 6 filtres que
        Rechercher, avec EXACTEMENT la même interprétation (revu le
        2026-09-25, "Purgeons ce qui est dans les filtres en cours,
        comme ça on voit bien ce qui va être effacé", puis à nouveau le
        même jour pour Du/Au — voir _current_log_filter_values/
        _on_log_purge) : les 6 champs sont TOUS facultatifs — Du vide =
        depuis le tout premier enregistrement, Au vide = jusqu'au tout
        dernier, Tournoi/Utilisateur/Fonction/Joueur sur "Tous"/"Toutes"
        = aucune restriction sur ce critère.

        Une seule grille PARTAGÉE par les 4 lignes (jamais une grille
        par ligne) : "Du"/"Utilisateur"/"Joueur" tombent tous en
        colonne 0, "Au"/"Fonction" en colonne 2 — même mécanisme
        d'alignement par colonne que "Permissions DIRTO" juste à droite
        (voir _build_remote_dirto_permissions_widgets)."""
        criteria = ttk.Frame(parent)
        criteria.grid(row=row, column=0, columnspan=2, sticky="w")

        # Ligne 1 : Du / Au / Tournoi
        # Du/Au (+ bouton calendrier 📅, chantier "LOG", Phase 2 —
        # correction "Purger" du 2026-09-24) : Entry + bouton empaquetés
        # dans une petite sous-frame (jamais posés directement dans la
        # grille partagée) pour ne pas perturber les colonnes 0-5
        # existantes — chaque sous-frame occupe exactement la même
        # cellule de grille que l'Entry seul occupait avant.
        ttk.Label(criteria, text="Du :").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=(0, 6))
        date_from_row = ttk.Frame(criteria)
        date_from_row.grid(row=0, column=1, sticky="w", padx=(0, 16), pady=(0, 6))
        self.log_date_from_var = tk.StringVar(value="")
        ttk.Entry(date_from_row, textvariable=self.log_date_from_var, width=12).pack(side="left")
        date_from_cal_btn = ttk.Button(date_from_row, text="📅", width=3)
        date_from_cal_btn.pack(side="left", padx=(2, 0))
        date_from_cal_btn.config(
            command=lambda: self._show_log_date_picker(self.log_date_from_var, date_from_cal_btn)
        )

        ttk.Label(criteria, text="Au :").grid(row=0, column=2, sticky="w", padx=(0, 4), pady=(0, 6))
        date_to_row = ttk.Frame(criteria)
        date_to_row.grid(row=0, column=3, sticky="w", padx=(0, 16), pady=(0, 6))
        self.log_date_to_var = tk.StringVar(value="")
        ttk.Entry(date_to_row, textvariable=self.log_date_to_var, width=12).pack(side="left")
        date_to_cal_btn = ttk.Button(date_to_row, text="📅", width=3)
        date_to_cal_btn.pack(side="left", padx=(2, 0))
        date_to_cal_btn.config(
            command=lambda: self._show_log_date_picker(self.log_date_to_var, date_to_cal_btn)
        )
        ttk.Label(criteria, text="Tournoi :").grid(row=0, column=4, sticky="w", padx=(0, 4), pady=(0, 6))
        self.log_tournament_var = tk.StringVar(value="Tous")
        self.log_tournament_combo = ttk.Combobox(
            criteria, textvariable=self.log_tournament_var, state="readonly", width=14,
            values=("Tous",),
        )
        self.log_tournament_combo.grid(row=0, column=5, sticky="w", pady=(0, 6))

        # Ligne 2 : Utilisateur / Fonction
        ttk.Label(criteria, text="Utilisateur :").grid(row=1, column=0, sticky="w", padx=(0, 4), pady=(0, 6))
        self.log_user_var = tk.StringVar(value="Tous")
        self.log_user_combo = ttk.Combobox(
            criteria, textvariable=self.log_user_var, state="readonly", width=14,
            values=("Tous",),
        )
        self.log_user_combo.grid(row=1, column=1, sticky="w", padx=(0, 16), pady=(0, 6))
        ttk.Label(criteria, text="Fonction :").grid(row=1, column=2, sticky="w", padx=(0, 4), pady=(0, 6))
        self.log_function_var = tk.StringVar(value="Toutes")
        self.log_function_combo = ttk.Combobox(
            criteria, textvariable=self.log_function_var, state="readonly", width=14,
            values=("Toutes",),
        )
        self.log_function_combo.grid(row=1, column=3, sticky="w", pady=(0, 6))

        # Ligne 3 : Joueur, puis Réinitialiser / Rechercher / Exporter
        # SUR LA MÊME LIGNE (demande du 2026-09-24, précision apportée
        # le même jour) — sous-frame packée (jamais une grille dédiée)
        # pour aligner les 3 boutons côte à côte sans avoir à gérer
        # 3 colonnes de grille supplémentaires.
        ttk.Label(criteria, text="Joueur :").grid(row=2, column=0, sticky="w", padx=(0, 4), pady=(0, 10))
        self.log_player_var = tk.StringVar(value="Tous")
        self.log_player_combo = ttk.Combobox(
            criteria, textvariable=self.log_player_var, state="readonly", width=14,
            values=("Tous",),
        )
        self.log_player_combo.grid(row=2, column=1, sticky="w", padx=(0, 16), pady=(0, 10))

        log_actions_row = ttk.Frame(criteria)
        log_actions_row.grid(row=2, column=2, columnspan=4, sticky="w", pady=(0, 10))
        # Réellement câblés depuis le chantier "LOG", Phase 2
        # (2026-09-24) — voir _on_log_reset/_on_log_search/_on_log_export.
        # "Purger" (revu le 2026-09-25) : utilise désormais les 6 mêmes
        # filtres que Rechercher — voir _on_log_purge. Placé à droite
        # d'un petit séparateur vertical pour le distinguer visuellement
        # des 3 actions non destructives qui précèdent.
        self.log_reset_btn = ttk.Button(log_actions_row, text="Réinitialiser", command=self._on_log_reset)
        self.log_reset_btn.pack(side="left")
        self.log_search_btn = ttk.Button(log_actions_row, text="🔍 Rechercher", command=self._on_log_search)
        self.log_search_btn.pack(side="left", padx=(8, 0))
        self.log_export_btn = ttk.Button(log_actions_row, text="Exporter", command=self._on_log_export)
        self.log_export_btn.pack(side="left", padx=(8, 0))
        ttk.Separator(log_actions_row, orient="vertical").pack(side="left", fill="y", padx=(12, 12))
        self.log_purge_btn = ttk.Button(log_actions_row, text="🗑 Purger", command=self._on_log_purge)
        self.log_purge_btn.pack(side="left")

    # -----------------------------------------------------------------
    # Onglet LOG — logique fonctionnelle (chantier "LOG", Phase 2,
    # 2026-09-24). Tout le SQL passe par action_log.py (search_actions/
    # list_tournaments/list_users/list_players/list_categories) — ces
    # méthodes-ci ne font jamais de requête directe.
    # -----------------------------------------------------------------

    @staticmethod
    def _build_log_tournament_label_maps(entries):
        """(path_to_label, label_to_path) à partir de action_log.
        list_tournaments() (déjà triée par nom puis chemin, donc un
        ordre stable ici). Désambiguïsation UNIQUEMENT lorsqu'au moins
        deux tournament_path différents partagent le même
        tournament_name (décision explicite du 2026-09-24) : le libellé
        reste alors le nom simple pour tout tournoi non ambigu, et
        gagne le nom du dossier parent pour les tournois en doublon — un
        numéro (" #2", " #3"...) est ajouté en tout dernier recours, si
        jamais deux tournois partagent EXACTEMENT le même nom et le même
        dossier parent. tournament_path lui-même n'est JAMAIS affiché."""
        by_name_count = {}
        for e in entries:
            by_name_count[e["tournament_name"]] = by_name_count.get(e["tournament_name"], 0) + 1

        raw_labels = []
        for e in entries:
            name, path = e["tournament_name"], e["tournament_path"]
            if by_name_count[name] == 1:
                label = name
            else:
                parent = os.path.basename(os.path.dirname(path)) or name
                label = f"{name} ({parent})"
            raw_labels.append((path, label))

        seen = {}
        path_to_label = {}
        label_to_path = {}
        for path, label in raw_labels:
            seen[label] = seen.get(label, 0) + 1
            final_label = label if seen[label] == 1 else f"{label} #{seen[label]}"
            path_to_label[path] = final_label
            label_to_path[final_label] = path
        return path_to_label, label_to_path

    def _refresh_log_filter_choices(self):
        """Repeuple les 4 combobox (Tournoi/Utilisateur/Fonction/Joueur)
        depuis les données RÉELLEMENT présentes dans le journal — ne
        touche PAS la sélection courante de l'utilisateur tant qu'elle
        reste valide (seule la liste `values` change, jamais `.set()`
        dans ce cas), pour ne jamais lui faire perdre un filtre déjà
        choisi en cours de saisie.

        Repli sur "Tous"/"Toutes" UNIQUEMENT si la sélection courante a
        cessé d'exister dans les données réelles (ajouté par la
        correction "Purger" du 2026-09-24) : en usage normal (Rechercher/
        Réinitialiser/retour sur l'onglet), les valeurs ne font que
        s'ACCUMULER, la sélection courante reste donc toujours présente
        et ce repli est un pur no-op — il ne joue un rôle réel qu'après
        une purge ayant fait disparaître la dernière trace d'un
        tournoi/utilisateur/catégorie/joueur donné."""
        tournaments = action_log.list_tournaments()
        path_to_label, label_to_path = self._build_log_tournament_label_maps(tournaments)
        self._log_tournament_label_to_path = label_to_path
        tournament_values = ("Tous",) + tuple(
            path_to_label[e["tournament_path"]] for e in tournaments
        )
        self.log_tournament_combo["values"] = tournament_values
        if self.log_tournament_var.get() not in tournament_values:
            self.log_tournament_var.set("Tous")

        user_values = ("Tous",) + tuple(action_log.list_users())
        self.log_user_combo["values"] = user_values
        if self.log_user_var.get() not in user_values:
            self.log_user_var.set("Tous")

        categories = action_log.list_categories()
        self._log_category_label_to_key = {
            action_log.category_label(c): c for c in categories
        }
        function_values = ("Toutes",) + tuple(action_log.category_label(c) for c in categories)
        self.log_function_combo["values"] = function_values
        if self.log_function_var.get() not in function_values:
            self.log_function_var.set("Toutes")

        player_values = ("Tous",) + tuple(action_log.list_players())
        self.log_player_combo["values"] = player_values
        if self.log_player_var.get() not in player_values:
            self.log_player_var.set("Tous")

    def _parse_log_date_field(self, raw_value, field_label):
        """Valide/parse un champ Du/Au (JJ/MM/AAAA) — renvoie (date,
        True) si vide (aucune borne) ou valide, (None, False) si invalide
        (un message d'erreur a alors déjà été affiché à l'utilisateur,
        rien d'autre à faire côté appelant qu'annuler la recherche)."""
        raw = (raw_value or "").strip()
        if not raw:
            return None, True
        if not _LOG_DATE_RE.match(raw):
            messagebox.showerror(
                "Date invalide",
                f"« {field_label} » doit être au format JJ/MM/AAAA (ex. 24/09/2026).",
                parent=self,
            )
            return None, False
        try:
            parsed = datetime.strptime(raw, "%d/%m/%Y")
        except ValueError:
            messagebox.showerror("Date invalide", f"« {field_label} » n'est pas une date valide.", parent=self)
            return None, False
        return parsed, True

    def _current_log_filter_values(self):
        """Lit et valide les 6 filtres ACTUELLEMENT affichés dans l'onglet
        LOG, sans rien interroger — source UNIQUE partagée par
        _refresh_log_tab (Rechercher) ET _on_log_purge (revu le
        2026-09-25, "Purgeons ce qui est dans les filtres en cours" :
        les deux doivent toujours lire les filtres EXACTEMENT de la même
        façon, jamais une conversion dupliquée qui pourrait diverger).

        Renvoie (date_from, date_to, ok, tournament_path, category,
        user_name, player_name). `ok=False` si une date saisie est
        invalide ou si Du > Au (message d'erreur déjà affiché à
        l'utilisateur) — l'appelant doit alors arrêter immédiatement,
        les 6 autres valeurs valant None dans ce cas.

        `date_from`/`date_to` valent None si le champ correspondant est
        VIDE — ce n'est PAS une erreur en soi ici (Rechercher l'autorise
        pour une recherche non bornée) : c'est à CHAQUE appelant de
        décider s'il exige ou non des dates renseignées (Purger les
        exige, voir _on_log_purge)."""
        date_from, ok_from = self._parse_log_date_field(self.log_date_from_var.get(), "Du")
        if not ok_from:
            return None, None, False, None, None, None, None
        date_to, ok_to = self._parse_log_date_field(self.log_date_to_var.get(), "Au")
        if not ok_to:
            return None, None, False, None, None, None, None
        if date_from is not None and date_to is not None and date_from > date_to:
            messagebox.showerror(
                "Dates invalides", "« Du » doit être antérieur ou égal à « Au ».", parent=self,
            )
            return None, None, False, None, None, None, None

        tournament_label = self.log_tournament_var.get()
        tournament_path = self._log_tournament_label_to_path.get(tournament_label) if tournament_label != "Tous" else None

        category_label = self.log_function_var.get()
        category = self._log_category_label_to_key.get(category_label) if category_label != "Toutes" else None

        user_name = self.log_user_var.get()
        user_name = None if user_name in ("", "Tous") else user_name

        player_name = self.log_player_var.get()
        player_name = None if player_name in ("", "Tous") else player_name

        return date_from, date_to, True, tournament_path, category, user_name, player_name

    @staticmethod
    def _log_count_label_text(n):
        """"N opération(s) affichée(s)" — accord EXACTEMENT comme
        demandé (0 et 1 au singulier, 2 et plus au pluriel) : "0
        opération affichée", "1 opération affichée", "4 opérations
        affichées", "750 opérations affichées". Utilisée par
        _refresh_log_tab (source unique de l'affichage du tableau LOG),
        donc aussi bien après Rechercher/Réinitialiser/un retour sur
        l'onglet qu'après une Purge (qui rappelle _refresh_log_tab)."""
        mot = "opération" if n <= 1 else "opérations"
        accord = "affichée" if n <= 1 else "affichées"
        return f"{n} {mot} {accord}"

    def _refresh_log_tab(self):
        """Coeur de l'onglet LOG : relit les filtres ACTUELLEMENT
        affichés (jamais réinitialisés ici — voir _on_log_reset pour la
        remise à zéro explicite) via _current_log_filter_values, interroge
        action_log.search_actions et repeuple self.log_tree. Appelée à la
        construction de l'onglet, par Rechercher, par Réinitialiser
        (après remise à zéro des variables), et à chaque retour de
        l'utilisateur sur l'onglet LOG (voir _refresh_all) — dans ce
        dernier cas, les filtres en cours sont CONSERVÉS et réutilisés
        tels quels (demande explicite du 2026-09-24 : ne jamais les
        effacer silencieusement).

        Revu le 2026-09-25 ("CE QUI CORRESPOND AUX FILTRES = CE QUI EST
        AFFICHÉ = CE QUI EST EXPORTÉ = CE QUI PEUT ÊTRE PURGÉ") :
        search_actions est appelée avec limit=None — plus aucun plafond
        à 500 lignes, plus aucune troncature. self.log_count_lbl affiche
        désormais en permanence le nombre EXACT de lignes du tableau
        (voir _log_count_label_text), calculé sur `rows` lui-même —
        jamais un compte séparé qui pourrait diverger de ce qui est
        réellement affiché."""
        self._refresh_log_filter_choices()

        date_from, date_to, ok, tournament_path, category, user_name, player_name = (
            self._current_log_filter_values()
        )
        if not ok:
            return
        ts_from = date_from.strftime("%Y-%m-%d 00:00:00") if date_from is not None else None
        ts_to = date_to.strftime("%Y-%m-%d 23:59:59") if date_to is not None else None

        rows, _truncated = action_log.search_actions(
            ts_from=ts_from, ts_to=ts_to, tournament_path=tournament_path,
            user_name=user_name, category=category, player_name=player_name,
            limit=None,
        )
        self._populate_log_tree(rows)
        self.log_count_lbl.config(text=self._log_count_label_text(len(rows)))

    @staticmethod
    def _format_log_ts(ts):
        """"AAAA-MM-JJ HH:MM:SS" (stockage) -> "JJ/MM/AAAA HH:MM:SS"
        (affichage) — présentation uniquement, ne modifie jamais le
        format de stockage (voir action_log.log_action)."""
        try:
            return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y %H:%M:%S")
        except (ValueError, TypeError):
            return ts or ""

    def _populate_log_tree(self, rows):
        for item in self.log_tree.get_children():
            self.log_tree.delete(item)
        self._log_rows_by_item = {}
        for row in rows:
            values = (
                self._format_log_ts(row["ts"]),
                row["tournament_name"] or "",
                row["user_name"] or "",
                action_log.role_label(row["role"]),
                action_log.category_label(row["category"]),
                action_log.action_label(row["action"]),
                row["player_name"] or "",
                action_log.result_label(row["result"]),
                row["message"] or "",
            )
            item_id = self.log_tree.insert("", "end", values=values)
            self._log_rows_by_item[item_id] = row

    def _on_log_search(self):
        self._refresh_log_tab()

    def _on_log_reset(self):
        """Réinitialiser (option B validée le 2026-09-24) : vide Du/Au,
        remet les 4 combobox sur "Tous"/"Toutes", PUIS relance
        immédiatement une recherche sans filtre — jamais un tableau vidé
        en attente d'un second clic sur Rechercher."""
        self.log_date_from_var.set("")
        self.log_date_to_var.set("")
        self.log_tournament_var.set("Tous")
        self.log_user_var.set("Tous")
        self.log_function_var.set("Toutes")
        self.log_player_var.set("Tous")
        self._refresh_log_tab()

    def _on_log_row_double_click(self, event):
        item_id = self.log_tree.identify_row(event.y)
        if not item_id:
            return
        row = self._log_rows_by_item.get(item_id)
        if not row:
            return
        self._show_log_detail_window(row)

    def _show_log_detail_window(self, row):
        """Détail complet d'une ligne du LOG (§14, chantier "LOG", Phase
        2) — fenêtre NON MODALE (aucun grab_set/wait_window : l'utilisateur
        doit pouvoir continuer à consulter le tableau derrière). Affiche
        device_label ("Appareil") mais JAMAIS device_id ni
        tournament_path — voir la demande explicite de confidentialité."""
        win = tk.Toplevel(self)
        win.title("Détail de l'action")
        win.transient(self)

        frame = ttk.Frame(win)
        frame.pack(padx=16, pady=16, fill="both", expand=True)
        fields = [
            ("Date/Heure", self._format_log_ts(row["ts"])),
            ("Tournoi", row["tournament_name"] or ""),
            ("Utilisateur", row["user_name"] or ""),
            ("Rôle", action_log.role_label(row["role"])),
            ("Appareil", row["device_label"] or ""),
            ("Fonction", action_log.category_label(row["category"])),
            ("Action", action_log.action_label(row["action"])),
            ("Joueur", row["player_name"] or ""),
            ("Résultat", action_log.result_label(row["result"])),
        ]
        for i, (label, value) in enumerate(fields):
            ttk.Label(frame, text=f"{label} :", font=("Helvetica", 10, "bold")).grid(
                row=i, column=0, sticky="ne", padx=(0, 8), pady=2,
            )
            ttk.Label(frame, text=value, wraplength=360, justify="left").grid(
                row=i, column=1, sticky="nw", pady=2,
            )

        msg_row = len(fields)
        ttk.Label(frame, text="Message :", font=("Helvetica", 10, "bold")).grid(
            row=msg_row, column=0, sticky="ne", padx=(0, 8), pady=(8, 2),
        )
        # Texte lisible ENTIÈREMENT (demande explicite) : tk.Text plutôt
        # qu'un Label, pour rester lisible même si le message dépasse
        # largement la largeur des autres champs — lecture seule
        # (disabled) : cette fenêtre affiche, elle ne modifie rien.
        msg_text = tk.Text(frame, width=44, height=6, wrap="word")
        msg_text.insert("1.0", row["message"] or "")
        msg_text.config(state="disabled")
        msg_text.grid(row=msg_row, column=1, sticky="nw", pady=(8, 2))

        ttk.Button(frame, text="Fermer", command=win.destroy).grid(
            row=msg_row + 1, column=0, columnspan=2, pady=(12, 0),
        )

    def _on_log_export(self):
        """Ouvre "Exporter le LOG" (LogExportDialog — correction du
        2026-09-24, remplace l'export CSV direct) — construit d'abord un
        instantané FIGÉ des lignes actuellement affichées dans
        self.log_tree (colonnes/ordre/libellés EXACTEMENT ceux de
        _populate_log_tree), avant même d'ouvrir la fenêtre : "ce que je
        vois dans le tableau LOG = ce qui peut être exporté", jamais une
        nouvelle recherche relancée en coulisses. Aucun résultat affiché
        -> refusé ici, la fenêtre ne s'ouvre même pas.

        La ligne "Critères : ..." (ajoutée le 2026-09-24, uniquement
        utilisée par l'export PDF — voir action_log.format_log_export_
        criteria) est construite ICI, à partir des MÊMES widgets de
        filtre actuellement affichés que ceux ayant produit `rows` —
        jamais recalculée après coup, donc forcément cohérente avec le
        tableau exporté."""
        items = self.log_tree.get_children()
        if not items:
            messagebox.showinfo("Info", "Aucun résultat à exporter.", parent=self)
            return
        cols = ("ts", "tournament", "user", "role", "category", "action", "player", "result", "message")
        rows = [dict(zip(cols, self.log_tree.item(item_id, "values"))) for item_id in items]
        criteria_line = action_log.format_log_export_criteria(
            date_from=self.log_date_from_var.get().strip() or None,
            date_to=self.log_date_to_var.get().strip() or None,
            tournament_label=self.log_tournament_var.get(),
            user_label=self.log_user_var.get(),
            function_label=self.log_function_var.get(),
            player_label=self.log_player_var.get(),
        )
        LogExportDialog(self, rows, criteria_line)

    # Noms de mois FRANÇAIS pour l'en-tête du calendrier — jamais les
    # locale.setlocale() du système (source classique de plantages
    # inter-plateforme quand la locale n'est pas installée) : une liste
    # statique de 12 entrées, mêmes principe que MOVE_REASON_LABELS/
    # REMOTE_PERMISSION_LABELS (database.py), toujours fiable Mac/
    # Windows sans dépendance ni configuration système.
    _LOG_CALENDAR_MONTH_NAMES_FR = [
        "", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
        "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre",
    ]

    def _show_log_date_picker(self, target_var, anchor_widget):
        """Petit calendrier POPUP (chantier "LOG", correction "Purger" du
        2026-09-24) — pur Tkinter + module `calendar` de la bibliothèque
        standard (déjà importé en tête de fichier), AUCUNE dépendance
        externe, fonctionne identiquement Mac/Windows. Remplit
        `target_var` au format JJ/MM/AAAA au clic sur un jour ; la saisie
        manuelle reste par ailleurs toujours possible (ce popup ne
        remplace rien, il complète). Ouvre sur la date déjà saisie si
        elle est valide, sinon sur aujourd'hui — jamais sur une date par
        défaut arbitraire."""
        raw = (target_var.get() or "").strip()
        try:
            start = datetime.strptime(raw, "%d/%m/%Y") if raw else datetime.now()
        except ValueError:
            start = datetime.now()

        picker = tk.Toplevel(self)
        picker.title("Choisir une date")
        picker.transient(self)
        picker.resizable(False, False)
        x = anchor_widget.winfo_rootx()
        y = anchor_widget.winfo_rooty() + anchor_widget.winfo_height()
        picker.geometry(f"+{x}+{y}")

        state = {"year": start.year, "month": start.month}

        header = ttk.Frame(picker)
        header.pack(fill="x", padx=6, pady=(6, 2))
        prev_btn = ttk.Button(header, text="◀", width=3)
        prev_btn.pack(side="left")
        month_lbl = ttk.Label(header, anchor="center", font=("Helvetica", 10, "bold"))
        month_lbl.pack(side="left", expand=True, fill="x")
        next_btn = ttk.Button(header, text="▶", width=3)
        next_btn.pack(side="right")

        grid_frame = ttk.Frame(picker)
        grid_frame.pack(padx=6, pady=(0, 6))

        def _select(day):
            target_var.set(f"{day:02d}/{state['month']:02d}/{state['year']:04d}")
            picker.destroy()

        def _render():
            for w in grid_frame.winfo_children():
                w.destroy()
            month_lbl.config(text=f"{self._LOG_CALENDAR_MONTH_NAMES_FR[state['month']]} {state['year']}")
            for col, weekday_letter in enumerate(("L", "M", "M", "J", "V", "S", "D")):
                ttk.Label(grid_frame, text=weekday_letter, width=3, anchor="center", foreground=MUTED).grid(
                    row=0, column=col,
                )
            for row_idx, week in enumerate(calendar.Calendar(firstweekday=0).monthdayscalendar(
                state["year"], state["month"],
            ), start=1):
                for col, day in enumerate(week):
                    if day == 0:
                        ttk.Label(grid_frame, text="", width=3).grid(row=row_idx, column=col)
                    else:
                        ttk.Button(
                            grid_frame, text=str(day), width=3, command=lambda d=day: _select(d),
                        ).grid(row=row_idx, column=col)

        def _prev_month():
            if state["month"] == 1:
                state["month"] = 12
                state["year"] -= 1
            else:
                state["month"] -= 1
            _render()

        def _next_month():
            if state["month"] == 12:
                state["month"] = 1
                state["year"] += 1
            else:
                state["month"] += 1
            _render()

        prev_btn.config(command=_prev_month)
        next_btn.config(command=_next_month)
        _render()
        picker.focus_set()

    def _log_purge_filter_summary(self, tournament_path, category, user_name, player_name):
        """Résumé lisible des filtres Tournoi/Utilisateur/Fonction/Joueur
        ACTUELLEMENT actifs (revu le 2026-09-25, voir _on_log_purge) —
        affiché dans la confirmation pour qu'on "voit bien ce qui va
        être effacé" (demande explicite), en plus des deux dates déjà
        affichées séparément. Reprend directement les LIBELLÉS déjà
        sélectionnés dans les combobox (jamais recalculés depuis les
        clés internes tournament_path/category) : c'est exactement ce
        que l'utilisateur voit à l'écran. Une valeur à None (filtre resté
        sur "Tous"/"Toutes") n'apparaît pas dans le résumé ; si les 4
        sont à None, renvoie une phrase explicite plutôt qu'une chaîne
        vide, pour ne jamais laisser croire par omission qu'un filtre
        était actif."""
        parts = []
        if tournament_path:
            parts.append(f"Tournoi = {self.log_tournament_var.get()}")
        if category:
            parts.append(f"Fonction = {self.log_function_var.get()}")
        if user_name:
            parts.append(f"Utilisateur = {user_name}")
        if player_name:
            parts.append(f"Joueur = {player_name}")
        return " ; ".join(parts) if parts else "aucun (tous les tournois/utilisateurs/fonctions/joueurs)"

    def _confirm_log_purge(self, date_from, date_to, count, filter_summary):
        """Confirmation OBLIGATOIRE avant toute purge (demande explicite
        du 2026-09-24) — Toplevel modal DÉDIÉ plutôt que messagebox.
        askyesno : les libellés de boutons demandés ("Annuler"/"Purger")
        ne sont pas ceux, fixes et dépendants de la langue système,
        qu'impose askyesno ("Oui"/"Non") — même pattern Toplevel+grab_set
        que les nombreuses autres fenêtres modales de ce fichier (ex. la
        fenêtre "Modifier les achats").

        `count` : nombre EXACT d'opérations que la purge va réellement
        supprimer — calculé par App._on_log_purge via action_log.
        count_actions AVANT d'ouvrir cette confirmation, avec EXACTEMENT
        les mêmes arguments (dates + les 4 autres filtres) que la purge
        elle-même. `filter_summary` (revu le 2026-09-25, "Purgeons ce
        qui est dans les filtres en cours, comme ça on voit bien ce qui
        va être effacé" — remplace l'ancien avertissement "les filtres
        affichés ne sont pas pris en compte", qui décrivait le
        comportement INVERSE) : résumé lisible de Tournoi/Utilisateur/
        Fonction/Joueur actuellement actifs, voir _log_purge_filter_
        summary — affiché explicitement pour qu'il n'y ait plus jamais
        d'écart entre "ce qui est affiché à l'écran" et "ce qui va être
        supprimé".

        `date_from`/`date_to` peuvent désormais valoir None (revu le
        2026-09-25, "je veux exactement la même interprétation que pour
        l'affichage/recherche" : Du/Au ne sont plus obligatoires pour
        purger) — le texte de période ci-dessous couvre les 4
        combinaisons (aucune, une seule, ou les deux bornes renseignées),
        avec une formulation française explicite pour chaque borne
        ouverte ("depuis le début du journal"/"jusqu'à la fin du
        journal") plutôt que de fabriquer une date artificielle.

        Renvoie True SEULEMENT si l'utilisateur clique explicitement sur
        "Purger" — Annuler ET la fermeture de la fenêtre (croix, Échap)
        renvoient tous les deux False, sans AUCUNE suppression."""
        result = {"confirmed": False}
        win = tk.Toplevel(self)
        win.title("Confirmer la purge")
        win.transient(self)
        win.grab_set()

        if date_from is not None and date_to is not None:
            period_text = (
                f"entre le {date_from.strftime('%d/%m/%Y')} "
                f"et le {date_to.strftime('%d/%m/%Y')} inclus"
            )
        elif date_from is not None:
            period_text = f"à partir du {date_from.strftime('%d/%m/%Y')} (jusqu'à la fin du journal)"
        elif date_to is not None:
            period_text = f"jusqu'au {date_to.strftime('%d/%m/%Y')} inclus (depuis le début du journal)"
        else:
            period_text = "sur toute la période disponible dans le journal"

        op_word = "opération" if count == 1 else "opérations"
        message = (
            f"Supprimer définitivement {count} {op_word} du LOG,\n"
            f"{period_text},\n"
            f"avec les filtres actuellement affichés :\n{filter_summary}\n\n"
            "Cette opération est irréversible."
        )
        ttk.Label(win, text=message, justify="left", wraplength=360).grid(
            row=0, column=0, columnspan=2, padx=16, pady=16,
        )

        def _cancel():
            result["confirmed"] = False
            win.destroy()

        def _purge():
            result["confirmed"] = True
            win.destroy()

        btns = ttk.Frame(win)
        btns.grid(row=1, column=0, columnspan=2, pady=(0, 16))
        ttk.Button(btns, text="Annuler", command=_cancel).pack(side="left", padx=5)
        ttk.Button(btns, text="Purger", command=_purge).pack(side="left", padx=5)

        win.protocol("WM_DELETE_WINDOW", _cancel)
        win.wait_window(win)
        return result["confirmed"]

    def _on_log_purge(self):
        """Purger (revu le 2026-09-25, "Purgeons ce qui est dans les
        filtres en cours, comme ça on voit bien ce qui va être effacé",
        PUIS de nouveau le même jour pour Du/Au) : utilise désormais les
        6 MÊMES filtres que Rechercher, avec EXACTEMENT la même
        interprétation — voir _current_log_filter_values, source
        PARTAGÉE avec _refresh_log_tab pour ne jamais lire/convertir les
        combobox différemment selon l'appelant. Abandonne l'ancien
        comportement ("Purger = suppression par période uniquement",
        Tournoi/Utilisateur/Fonction/Joueur délibérément ignorés, ET Du/
        Au obligatoires) : jugé après coup surprenant, un filtre affiché
        à l'écran devant réellement délimiter ce qui est supprimé —
        "ce qui correspond aux filtres et est affiché = ce qui sera
        purgé", y compris quand Du et/ou Au sont vides.

        LES 6 FILTRES SONT DÉSORMAIS TOUS FACULTATIFS : "Du" vide =
        depuis le tout premier enregistrement du journal, "Au" vide =
        jusqu'au tout dernier, les deux vides = toute la période
        disponible — jamais de date artificielle fabriquée ici, une
        borne absente n'ajoute simplement aucune clause SQL (voir
        action_log._build_filters_clause). Une date RENSEIGNÉE reste
        soumise aux mêmes validations que Rechercher (format JJ/MM/AAAA,
        Du <= Au si les deux sont fournies) — voir _current_log_filter_
        values, qui affiche déjà l'erreur le cas échéant.

        ts_from/ts_to/tournament_path/category/user_name/player_name sont
        calculés UNE SEULE FOIS ici et réutilisés SANS RECALCUL pour
        action_log.count_actions PUIS action_log.purge_actions — garantit
        structurellement que le nombre annoncé dans la confirmation et ce
        qui est réellement supprimé portent sur EXACTEMENT les mêmes
        critères. Aucune opération correspondante -> message dédié, ni
        confirmation ni suppression. Sinon, confirmation OBLIGATOIRE (voir
        _confirm_log_purge, reçoit ce compte exact, les dates — même
        None — ET le résumé des filtres actifs) avant toute suppression
        réelle ; en cas de succès, self._refresh_log_tab() est rappelée
        pour tout rafraîchir (tableau + combobox) tout en CONSERVANT Du/
        Au et les autres filtres encore valides (voir _refresh_log_
        filter_choices)."""
        date_from, date_to, ok, tournament_path, category, user_name, player_name = (
            self._current_log_filter_values()
        )
        if not ok:
            return

        ts_from = date_from.strftime("%Y-%m-%d 00:00:00") if date_from is not None else None
        ts_to = date_to.strftime("%Y-%m-%d 23:59:59") if date_to is not None else None

        count = action_log.count_actions(
            ts_from, ts_to, tournament_path=tournament_path, user_name=user_name,
            category=category, player_name=player_name,
        )
        if count == 0:
            messagebox.showinfo("Purge", "Aucune opération à purger pour ces critères.", parent=self)
            return

        filter_summary = self._log_purge_filter_summary(tournament_path, category, user_name, player_name)
        if not self._confirm_log_purge(date_from, date_to, count, filter_summary):
            return

        try:
            deleted = action_log.purge_actions(
                ts_from, ts_to, tournament_path=tournament_path, user_name=user_name,
                category=category, player_name=player_name,
            )
        except Exception as e:
            messagebox.showerror("Erreur", f"La purge a échoué :\n{e}", parent=self)
            return

        if deleted > 0:
            messagebox.showinfo(
                "Purge effectuée", f"{deleted} opérations ont été supprimées du LOG.", parent=self,
            )
        else:
            # Rarissime course avec un autre processus (voir action_log.
            # purge_actions : cette même base est partagée entre
            # plusieurs fenêtres/tournois) : le compte affiché à la
            # confirmation était encore exact au moment du clic, mais
            # ces lignes ont disparu entre-temps (déjà purgées ou
            # rotées ailleurs) — jamais une erreur, juste un message
            # cohérent avec l'issue réelle.
            messagebox.showinfo(
                "Purge effectuée", "Aucune opération à purger pour ces critères.", parent=self,
            )
        self._refresh_log_tab()

    def _on_single_tournament_toggle(self):
        export_prefs.save_value(SINGLE_TOURNAMENT_PREF_KEY, self.single_tournament_var.get())

    def _test_mode_enabled(self):
        """Mode Test (demande du 2026-09-09) : jamais mémorisé, propre à
        CE process — voir self.test_mode_var (App.__init__) et la case
        "Mode Test" (_build_settings_tab). Assouplit les facilités
        d'élimination (_eliminate_selected/_remote_eliminate) :
        élimination groupée sans désigner d'éliminateur, et éliminateur
        redevenant facultatif (bouton "Ignorer") pour une élimination
        individuelle hors PKO+bounty. Active aussi, depuis le 2026-09-14,
        le tri par en-tête de la colonne des cases à cocher de l'onglet
        Joueurs (voir _on_players_checkbox_header_click), ET, depuis le
        2026-09-14 également, garde la section Primes des Paramètres
        modifiable même si la session est réellement verrouillée (voir
        _primes_section_effectively_locked — n'affecte que CET onglet,
        jamais primes_session_started.json ni les autres fenêtres de la
        session) — jamais d'autre effet sur le fonctionnement du
        logiciel."""
        return self.test_mode_var.get()

    def _on_test_mode_toggle(self):
        self._update_window_title()
        # Bascule immédiate du (dé)verrouillage de la section Primes
        # (demande du 2026-09-14, voir _primes_section_effectively_
        # locked) : sans cet appel, il faudrait attendre le prochain tick
        # (jusqu'à 1s, voir _sync_primes_enabled_checkbox) pour voir
        # l'effet — la demande est explicitement "immédiatement". Garde
        # défensive (comme la plupart des méthodes touchant l'onglet
        # Paramètres) : self.db/primes_enabled_check n'existent qu'une
        # fois un tournoi ouvert et cet onglet construit — toujours vrai
        # en pratique ici (la case "Mode Test" vit dans ce même onglet),
        # mais sans risque à vérifier.
        if getattr(self, "db", None) is not None and hasattr(self, "primes_enabled_check"):
            self._sync_primes_enabled_checkbox()

    def _sync_single_tournament_pref_checkbox(self):
        """Préférence GLOBALE (voir SINGLE_TOURNAMENT_PREF_KEY) : une
        SEULE valeur dans export_prefs.json, partagée par tous les
        process, mais chacun ne la lit qu'UNE fois pour initialiser
        self.single_tournament_var (voir _build_settings_tab) — sans ce
        rappel périodique, un tournoi A déjà ouvert continuait d'afficher
        l'ancien état de la case après qu'un tournoi B l'ait changée
        (chacun n'ayant sa propre BooleanVar qu'en mémoire locale,
        jamais relue depuis le disque après le démarrage). Appelé depuis
        _tick (1x/seconde, comme _check_phone_selected_pid juste
        au-dessus) plutôt qu'un nouveau mécanisme séparé : relit
        _single_tournament_pref_enabled() et ne touche la case que si sa
        valeur diffère réellement (évite tout scintillement/coup d'oeil
        inutile sur ce widget à chaque tick)."""
        current = _single_tournament_pref_enabled()
        if self.single_tournament_var.get() != current:
            self.single_tournament_var.set(current)

    def _primes_section_effectively_locked(self):
        """Verrouillage EFFECTIF de la section Primes pour CET onglet
        Paramètres (demande du 2026-09-14) — reflète _primes_session_
        locked() (verrouillage RÉEL de la session, dérivé de
        primes_session_started.json/open_windows.json, JAMAIS modifié
        ici), SAUF en Mode Test (self._test_mode_enabled()), où cette
        méthode renvoie toujours False : la section reste alors
        modifiable même si la session est réellement verrouillée
        (chronomètre déjà démarré sur ce tournoi ou un autre de la
        session).

        Mode Test (self.test_mode_var) est un réglage strictement LOCAL
        à CE PROCESS — jamais mémorisé, jamais partagé entre fenêtres
        (voir _test_mode_enabled) — donc cette exception ne touche
        jamais primes_session_started.json ni open_windows.json : les
        AUTRES fenêtres/tournois de la session (Mode Test coché ou non
        chez elles) continuent de voir et d'appliquer le verrouillage
        réel normalement, exactement comme avant cette demande. Décocher
        Mode Test fait immédiatement réapparaître le verrouillage réel
        (voir _on_test_mode_toggle, qui rappelle _sync_primes_enabled_
        checkbox sans attendre le prochain tick).

        Seuls appelants (tous les 3 anciens appels directs à
        _primes_session_locked() dans cette classe, remplacés le
        2026-09-14) : _on_primes_enabled_toggle, _update_primes_section_
        state (valeur par défaut), _sync_primes_enabled_checkbox — et le
        grisement initial de la case dans _build_settings_tab."""
        if self._test_mode_enabled():
            return False
        return _primes_session_locked()

    def _on_primes_enabled_toggle(self):
        """Case "Calculer les primes" (Paramètres, demande du
        2026-09-09) : modifiable uniquement tant qu'aucun tournoi de la
        session n'a démarré son chronomètre — la case est déjà grisée
        dans ce cas (voir _sync_primes_enabled_checkbox), ce garde n'est
        qu'un filet de sécurité (ex. clic "en vol" juste au moment où un
        autre tournoi démarre) — SAUF en Mode Test (demande du
        2026-09-14, voir _primes_section_effectively_locked), qui laisse
        alors passer le changement même session réellement verrouillée.
        Répercute le choix (a) dans la valeur globale "proposée"
        (export_prefs, voir _set_primes_enabled_proposed), pour que les
        autres tournois pas encore démarrés convergent au prochain tick
        (voir _sync_primes_enabled_pref) — SAUF précisément dans le cas
        Mode Test + session réellement verrouillée : cette exception
        doit rester strictement locale à CE tournoi/process, jamais se
        propager à une autre fenêtre de la session (qui n'est peut-être
        pas, elle, en Mode Test) — et (b) tout de suite dans la copie
        SQLite de CE tournoi précis, sans attendre ce prochain tick, pour
        que son propre onglet Primes et le grisement de la section
        réagissent sans délai perceptible."""
        if self._primes_section_effectively_locked():
            current = self.db.get_setting_int("primes_enabled", 1) == 1
            self.primes_enabled_var.set(current)
            self.primes_enabled_check.configure(state="disabled")
            self._update_primes_section_state(current, locked=True)
            return
        value = self.primes_enabled_var.get()
        if not _primes_session_locked():
            _set_primes_enabled_proposed(value)
        self.db.set_setting("primes_enabled", "1" if value else "0")
        self._update_primes_section_state(value, locked=False)
        self._refresh_bounty_tab()

    def _update_primes_section_state(self, enabled, locked=None):
        """Grise (valeurs conservées, jamais effacées) ou réactive tous
        les widgets de réglage des montants/PKO de la section Primes
        (voir self._primes_section_widgets, remplie dans _build_settings_
        tab), selon `enabled` ET `locked` combinés (demande du 2026-09-09,
        point 2 : "TOUS les paramètres de la section Primes sont
        verrouillés" dès que la session a démarré — pas seulement la case
        "Calculer les primes" elle-même) :
          - avant tout démarrage (locked=False) : modifiables normalement
            si `enabled`, grisés (valeurs conservées) sinon — comportement
            déjà en place (point 5 de la demande précédente).
          - dès que la session est verrouillée (locked=True) : TOUJOURS
            grisés, même si `enabled` est vrai — un tournoi de la session
            ne doit plus pouvoir changer un montant/le mode PKO en cours
            de route, qu'il ait démarré lui-même ou non.
        `locked=None` (valeur par défaut) : recalculé ici via
        _primes_section_effectively_locked() (verrouillage réel, sauf
        exception Mode Test — voir sa docstring, demande du 2026-09-14)
        — permet d'appeler cette méthode avec seulement `enabled` (ex.
        juste après un changement local qui ne change pas le
        verrouillage) sans le refaire à chaque appelant. N'agit jamais
        sur la case "Calculer les primes" elle-même (son propre état
        "disabled" est géré par ses appelants, voir _sync_primes_
        enabled_checkbox / _on_primes_enabled_toggle).

        Cas particulier du ttk.Combobox "Système de points distribués"
        (ranking_combo, voir _build_ranking_formula_widget) — demande du
        2026-09-11, diagnostic de la régression où ce widget restait
        visuellement figé sur "readonly" (confondu avec "disabled" sous
        le thème Aqua) : il ne doit JAMAIS recevoir state="normal" (ce
        qui autoriserait une saisie libre, valeur interne inconnue) —
        "readonly" quand la section est modifiable, "disabled" sinon,
        jamais le `state` générique calculé pour les autres widgets."""
        if locked is None:
            locked = self._primes_section_effectively_locked()
        editable = enabled and not locked
        state = "normal" if editable else "disabled"
        for widget in getattr(self, "_primes_section_widgets", []):
            try:
                if isinstance(widget, ttk.Combobox):
                    widget.configure(state="readonly" if editable else "disabled")
                else:
                    widget.configure(state=state)
            except tk.TclError:
                pass  # widget sans option "state" (ne devrait pas arriver ici)

    def _sync_primes_enabled_checkbox(self):
        """Appelée depuis _tick (1x/seconde), juste après _sync_primes_
        enabled_pref (qui vient de faire converger la copie SQLite locale
        de ce tournoi vers la valeur globale proposée, tant qu'il n'a pas
        démarré) : reflète cette copie locale dans la case et grise/
        réactive à la fois la case elle-même ET tout le reste de la
        section Primes (voir _update_primes_section_state) selon l'état
        de verrouillage EFFECTIF de la SESSION (_primes_section_
        effectively_locked — verrouillage réel, sauf exception Mode Test,
        demande du 2026-09-14 — pas seulement la case) — même principe
        que _sync_single_tournament_pref_checkbox, pour qu'un tournoi A
        reflète un changement fait depuis les Paramètres d'un tournoi B,
        ou le verrouillage causé par le démarrage d'un tournoi C de la
        même session, sans qu'aucune action ne soit nécessaire sur A.
        Appelée aussi directement par _on_test_mode_toggle (en plus du
        tick habituel) pour que cocher/décocher Mode Test rebascule le
        grisement de la section IMMÉDIATEMENT, sans attendre jusqu'à 1s."""
        if self.db is None:
            return
        current = self.db.get_setting_int("primes_enabled", 1) == 1
        if self.primes_enabled_var.get() != current:
            self.primes_enabled_var.set(current)
        locked = self._primes_section_effectively_locked()
        check_state = "disabled" if locked else "normal"
        if str(self.primes_enabled_check.cget("state")) != check_state:
            self.primes_enabled_check.configure(state=check_state)
        self._update_primes_section_state(current, locked=locked)

    def _on_bb_rebalance_prompt_toggle(self):
        """Case "Équilibrage guidé par UTG" (Paramètres) : mémorise le
        choix (réglage global, comme _on_remote_control_toggle) et prend
        effet immédiatement, sans redémarrer — database.py relit cette
        préférence à chaque appel de rebalance_tables() (voir _bb_
        rebalance_prompt_enabled), donc le PROCHAIN rééquilibrage en
        tient déjà compte.

        Cas particulier explicitement demandé : si la case est décochée
        alors qu'une question est actuellement en attente de réponse, on
        ne l'abandonne pas telle quelle (le mouvement resterait à
        décider indéfiniment) — on la résout tout de suite avec l'ancien
        mécanisme historique, exactement comme si "Continuer sans
        désigner le joueur" avait été cliqué. Passe par
        _resolve_pending_rebalance (pas un appel direct à la base) :
        cette demande est ainsi consommée de façon sûre, comme n'importe
        quelle autre réponse (voir sa docstring et celle de database.py:
        resolve_pending_rebalance) — aucun risque de double mouvement."""
        enabled = self.bb_rebalance_prompt_var.get()
        export_prefs.save_value(BB_REBALANCE_PROMPT_PREF_KEY, enabled)
        if not enabled and self.db is not None and self.db.pending_rebalance is not None:
            self._resolve_pending_rebalance(
                self.db.pending_rebalance["request_id"], None, from_remote=False
            )

    def _test_movement_signal(self):
        try:
            duration = int(self.settings_vars["movement_signal_duration_ms"].get())
        except (ValueError, KeyError):
            messagebox.showerror(
                "Erreur", "Veuillez saisir un nombre entier valide pour la durée max. du signal."
            )
            return
        wav_path = export_prefs.load_value("movement_signal_wav_path", "")
        if wav_path and sound_signal.play_file(wav_path, max_duration_ms=duration):
            return
        if not sound_signal.play_tone(880, duration):
            self.bell()

    def _parse_optional_positive_int(self, key):
        """Lit self.settings_vars[key] : "" (vide, espaces compris) ->
        (None, True) ; sinon tente un entier strictement positif -> (val,
        True), ou (None, False) si ce n'est pas un entier strictement
        positif. Utilisé par _generate_custom_blind_structure pour "Nb
        Rounds"/"Après Round" (chantier "Paramètres > Structure des
        blindes", 2026-09-24) — jamais pour un champ obligatoire comme la
        durée de la ligne 1, qui garde sa propre validation stricte
        héritée."""
        raw = self.settings_vars[key].get().strip()
        if not raw:
            return None, True
        try:
            value = int(raw)
        except ValueError:
            return None, False
        if value <= 0:
            return None, False
        return value, True

    def _generate_custom_blind_structure(self):
        try:
            sb = int(self.settings_vars["start_small_blind"].get())
            bb = int(self.settings_vars["start_big_blind"].get())
            ante_lvl = int(self.settings_vars["ante_start_level"].get())
            start_ante = int(self.settings_vars["start_ante"].get())
            round_duration_1 = int(self.settings_vars["round_duration_minutes"].get())
        except (ValueError, KeyError):
            messagebox.showerror(
                "Erreur",
                "Veuillez saisir des nombres entiers valides pour le small blind, "
                "le big blind, le niveau de début des antes, la valeur de l'ante "
                "et la durée de la première ligne de rounds.",
            )
            return
        if sb <= 0 or bb <= sb:
            messagebox.showerror(
                "Erreur",
                "Le big blind doit être strictement supérieur au small blind, tous deux positifs.",
            )
            return
        if ante_lvl <= 0:
            messagebox.showerror("Erreur", "Le niveau de début des antes doit être 1 ou plus.")
            return
        if start_ante < 0:
            messagebox.showerror("Erreur", "L'ante de départ ne peut pas être négative.")
            return
        if round_duration_1 <= 0:
            messagebox.showerror("Erreur", "La durée de la première ligne de rounds doit être supérieure à 0.")
            return

        # -- Durées de rounds : ligne 1 obligatoire, ligne 2 uniquement
        # pertinente si "Nb Rounds" de la ligne 1 est renseigné (sinon la
        # ligne 1 s'applique à tout le tournoi et la ligne 2 n'intervient
        # jamais, quoi qu'elle contienne — demande explicite).
        round_count_1, ok = self._parse_optional_positive_int("round_count_1")
        if not ok:
            messagebox.showerror("Erreur", "\"Nb Rounds\" (1re ligne) doit être un entier strictement positif, ou vide.")
            return

        duration_schedule = [(round_duration_1, round_count_1)]
        if round_count_1 is not None:
            # La ligne 2 devient pertinente : sa durée est alors
            # obligatoire (sans elle, aucune durée ne serait définie
            # pour les rounds suivants).
            raw_duration_2 = self.settings_vars["round_duration_minutes_2"].get().strip()
            try:
                round_duration_2 = int(raw_duration_2)
            except ValueError:
                messagebox.showerror(
                    "Erreur",
                    "La durée de la 2e ligne de rounds est obligatoire dès que "
                    "\"Nb Rounds\" de la 1re ligne est renseigné.",
                )
                return
            if round_duration_2 <= 0:
                messagebox.showerror("Erreur", "La durée de la 2e ligne de rounds doit être supérieure à 0.")
                return
            round_count_2, ok = self._parse_optional_positive_int("round_count_2")
            if not ok:
                messagebox.showerror("Erreur", "\"Nb Rounds\" (2e ligne) doit être un entier strictement positif, ou vide.")
                return
            duration_schedule.append((round_duration_2, round_count_2))

        # -- Pauses : chaque ligne doit être ENTIÈREMENT vide (pas de
        # pause) ou ENTIÈREMENT remplie (durée ET round) — jamais à
        # moitié, pour ne jamais deviner une intention incomplète.
        break_schedule = []
        break_rounds_seen = []
        for idx, (minutes_key, round_key) in enumerate(
            (("break_minutes_1", "break_after_round_1"), ("break_minutes_2", "break_after_round_2")), start=1
        ):
            raw_minutes = self.settings_vars[minutes_key].get().strip()
            raw_round = self.settings_vars[round_key].get().strip()
            if not raw_minutes and not raw_round:
                continue  # pause non définie, ignorée proprement
            if bool(raw_minutes) != bool(raw_round):
                messagebox.showerror(
                    "Erreur",
                    f"La pause {idx} nécessite une durée ET un numéro de round — "
                    "remplissez les deux champs, ou laissez-les tous les deux vides.",
                )
                return
            try:
                minutes = int(raw_minutes)
                after_round = int(raw_round)
            except ValueError:
                messagebox.showerror("Erreur", f"La pause {idx} : durée et round doivent être des entiers.")
                return
            if minutes <= 0:
                messagebox.showerror("Erreur", f"La durée de la pause {idx} doit être supérieure à 0.")
                return
            if after_round <= 0:
                messagebox.showerror("Erreur", f"\"Après Round\" de la pause {idx} doit être 1 ou plus.")
                return
            if after_round > GENERATED_ROUNDS_COUNT:
                messagebox.showerror(
                    "Erreur",
                    f"La pause {idx} est prévue après le round {after_round}, mais le "
                    f"tournoi ne comptera que {GENERATED_ROUNDS_COUNT} rounds de jeu au maximum — "
                    "elle ne serait jamais insérée.",
                )
                return
            break_rounds_seen.append(after_round)
            break_schedule.append((minutes, after_round))

        if len(break_rounds_seen) == 2:
            if break_rounds_seen[0] == break_rounds_seen[1]:
                messagebox.showerror("Erreur", "Les deux pauses ne peuvent pas être placées après le même round.")
                return
            if break_rounds_seen[1] < break_rounds_seen[0]:
                messagebox.showerror(
                    "Erreur",
                    "La 2e pause doit être après la 1re (round strictement supérieur).",
                )
                return

        if not messagebox.askyesno(
            "Confirmer",
            "Régénérer toute la structure de blindes avec ces valeurs ?\n"
            "(Cette action fonctionne aussi en plein milieu d'un tournoi.)",
        ):
            return

        new_structure = generate_blind_structure(
            start_small_blind=sb, start_big_blind=bb, ante_start_level=ante_lvl,
            start_ante=start_ante, duration_minutes=round_duration_1,
            duration_schedule=duration_schedule, break_schedule=break_schedule,
        )
        self.db.set_blind_structure(new_structure)
        # Enregistre TOUS les paramètres en même temps (pas seulement ceux
        # de la structure de blindes) : équivaut à cliquer aussi sur
        # "Enregistrer les paramètres", sans avoir à le faire séparément.
        self._collect_and_save_all_settings()
        # Tournoi en cours (demande explicite du 2026-09-24) : JAMAIS de
        # remise au niveau 1 — seule exception, reprise à l'identique de
        # la règle déjà en production ici même depuis l'origine de ce
        # bouton (voir aussi App._apply_blinds_from_tab, bouton
        # "Appliquer" de l'onglet Blindes, qui applique la MÊME règle) :
        # si la nouvelle structure est devenue plus courte que le niveau
        # courant, on le ramène au dernier niveau encore valide.
        current_order = self.db.get_setting_int("current_level_order", 1)
        if current_order > len(new_structure):
            self.db.set_settings({"current_level_order": len(new_structure)})
        self._refresh_all()
        # Rafraîchissement immédiat du Chronomètre ET du Chrono Projo
        # (décision explicite du 2026-09-24, même garantie que le bouton
        # "Appliquer") : _refresh_all() ci-dessus ne rafraîchit que
        # l'onglet actuellement affiché (ici Paramètres, jamais
        # Chronomètre) — sans cet appel explicite, l'affichage n'aurait
        # attendu le prochain tick que si l'onglet Chronomètre ou l'écran
        # projecteur étaient déjà visibles.
        if hasattr(self, "blinds_tree"):
            self._refresh_clock_tab()
        messagebox.showinfo(
            "Structure de blindes",
            "La structure de blindes a été régénérée et tous les paramètres "
            "ont été enregistrés.",
        )

    def _collect_and_save_all_settings(self):
        """Rassemble et enregistre tous les champs de l'onglet Paramètres
        (tous les settings_vars, quel que soit l'onglet/section où ils
        sont saisis). Utilisé à la fois par le bouton "Enregistrer
        Paramètres sous..." et par "Régénérer la structure de blindes",
        qui enregistre ainsi tout en même temps sans clic séparé. Renvoie
        le dict `values` rassemblé (ex : pour "Enregistrer Paramètres
        sous...", qui en a aussi besoin pour le modèle nommé).

        PHASE 2 de l'architecture de rééquilibrage (validée le
        2026-09-10) : un changement de "Nombre de sièges par table" qui
        laisserait, une fois le tournoi démarré (clock_started == 1), au
        moins une table avec plus de joueurs que ce nouveau nombre de
        sièges est REFUSÉ CIBLÉ — ni `max_seats_per_table` ni
        `tables_pk.max_seats` ne sont modifiés (voir database.py:
        tables_over_capacity), le champ à l'écran est ramené à l'ancienne
        valeur, un message d'erreur explique quelle(s) table(s) bloquent
        le changement, et TOUS LES AUTRES réglages modifiés dans le même
        geste sont malgré tout enregistrés normalement. Cette vérification
        est faite AVANT le moindre appel à set_settings/set_all_tables_
        max_seats : il ne doit jamais exister d'état intermédiaire où
        max_seats_per_table est enregistré sans que les tables réelles ne
        l'aient suivi. Avant le premier démarrage (clock_started == 0),
        comportement inchangé : la réorganisation automatique reste
        entièrement autorisée (PHASE 1)."""
        values = {}
        for k, v in self.settings_vars.items():
            raw = v.get()
            if isinstance(raw, bool):
                raw = "1" if raw else "0"
            values[k] = raw

        try:
            candidate_max_seats = int(values.get("max_seats_per_table", ""))
        except (ValueError, TypeError):
            candidate_max_seats = None
        new_max_seats = candidate_max_seats if candidate_max_seats and candidate_max_seats >= 2 else None

        if (
            new_max_seats is not None
            and self.db.get_setting_int("clock_started", 0) == 1
        ):
            over = self.db.tables_over_capacity(new_max_seats)
            if over:
                # Refus ciblé : on revient à l'ancienne valeur, à la fois
                # dans `values` (donc dans db_values ci-dessous et dans
                # tournament_prefs.save_last_settings) et dans le champ à
                # l'écran, pour qu'aucune trace du changement refusé ne
                # subsiste nulle part.
                old_value = self.db.get_setting("max_seats_per_table", "9")
                values["max_seats_per_table"] = old_value
                if "max_seats_per_table" in self.settings_vars:
                    self.settings_vars["max_seats_per_table"].set(old_value)
                new_max_seats = None
                if len(over) == 1:
                    detail = f"La {over[0]['name']} contient actuellement {over[0]['count']} joueurs."
                else:
                    noms = ", ".join(f"{t['name']} ({t['count']} joueurs)" for t in over)
                    detail = f"Ces tables contiennent plus de joueurs que {candidate_max_seats} : {noms}."
                messagebox.showerror(
                    "Nombre de sièges par table",
                    f"Impossible d'appliquer {candidate_max_seats} sièges par table.\n"
                    f"{detail}\n"
                    "Effectuez d'abord le rééquilibrage de cette table.",
                )

        # "club_name" est commun à tous les tournois/Sit & Go (préférences
        # partagées, voir le trace_add posé sur sa StringVar) : jamais
        # écrit dans ce fichier .tournoi, pour éviter une copie qui
        # divergerait silencieusement de la valeur réellement affichée.
        #
        # "ranking_formula" vide ("") : ce tournoi utilise encore la
        # valeur fixe historique (ranking_bonus_points, voir Database.
        # resolve_ranking_formula) et le responsable n'a fait AUCUN choix
        # explicite dans la nouvelle liste déroulante (voir
        # RANKING_FORMULA_LEGACY_PLACEHOLDER) — on n'écrit alors RIEN
        # dans ranking_formula, pour ne jamais remplacer silencieusement
        # cette valeur fixe par une formule à l'occasion d'un simple
        # "Appliquer"/"Enregistrer" portant sur d'AUTRES réglages.
        db_values = {
            k: v for k, v in values.items()
            if k != "club_name" and not (k == "ranking_formula" and v == "")
        }
        self.db.set_settings(db_values)
        # Même exclusion que db_values ci-dessus pour "ranking_formula"
        # == "" (placeholder légataire, aucun choix explicite fait) —
        # sans elle, tournament_prefs.PERSISTED_KEYS mémoriserait ce
        # non-choix comme préférence héritée, et un futur tournoi neuf
        # (voir _choose_tournament_file) tomberait silencieusement sur
        # le fallback légataire (Classique) au lieu du "Aucun" sûr
        # stampé par défaut (demande du 2026-09-18).
        tournament_prefs.save_last_settings(db_values)
        if new_max_seats is not None:
            self.db.set_all_tables_max_seats(new_max_seats)
        moves = self.db.rebalance_tables()
        self._update_window_title()
        if moves:
            self._trigger_movement_alert()
        self._check_pending_rebalance()
        return values

    def _choose_day_folder(self, day_folder_var):
        """Ouvre un sélecteur de dossier pour "Chemin du dossier du tournoi
        du jour" (voir _build_settings_tab). Le choix est enregistré par le
        trace_add posé sur day_folder_var (voir _save_day_folder), comme
        une saisie manuelle dans le champ."""
        path = filedialog.askdirectory(
            title="Choisir le dossier du tournoi du jour",
            initialdir=day_folder_var.get() or os.path.expanduser("~"),
        )
        if not path:
            return
        day_folder_var.set(path)

    def _save_day_folder(self):
        """Enregistre en continu "Dossier par défaut", au
        fur et à mesure de la saisie ou dès le choix via "Parcourir..."
        (voir _build_settings_tab) : à la fois pour CE tournoi (comme les
        autres réglages), et dans les préférences globales
        (tournament_prefs) pour que l'écran d'accueil puisse le proposer
        avant même l'ouverture d'un fichier .tournoi (voir
        tournament_day_folder_proposal)."""
        path = self.settings_vars["tournament_day_folder"].get()
        self.db.set_settings({"tournament_day_folder": path})
        tournament_prefs.save_last_settings({"tournament_day_folder": path})

    def _save_tournament_days(self):
        """Enregistre en continu les jours cochés dans "Jours de tournoi /
        Sit & Go" (voir _build_settings_tab), au fur et à mesure des
        cases cochées/décochées — même principe que _save_day_folder
        juste au-dessus (CE tournoi + préférences globales
        tournament_prefs, pour être proposé dès l'écran d'accueil)."""
        checked = sorted(idx for idx, var in self.tournament_day_vars.items() if var.get())
        value = ",".join(str(i) for i in checked)
        self.db.set_settings({"tournament_days_of_week": value})
        tournament_prefs.save_last_settings({"tournament_days_of_week": value})

    def _save_elimination_banner_seconds(self, var):
        """Enregistre la durée d'affichage du bandeau d'élimination (voir
        _build_settings_tab / _advance_elimination_banner), au clic sur
        les flèches du Spinbox comme à la frappe (Retour/perte de focus)
        — même principe que _save_tournament_days : CE tournoi +
        préférences globales (proposé par défaut au prochain tournoi).
        Une valeur invalide/vide (ex : champ momentanément vidé pendant
        la frappe) est ignorée sans planter — reste alors le dernier
        réglage valide déjà enregistré, jamais une valeur cassée. 0 est
        une valeur valide (désactive l'affichage du bandeau, voir
        _advance_elimination_banner) — jamais remonté à 1."""
        try:
            seconds = max(0, min(30, int(var.get())))
        except (tk.TclError, ValueError):
            return
        self.db.set_settings({"elimination_banner_seconds": seconds})
        tournament_prefs.save_last_settings({"elimination_banner_seconds": seconds})

    def _save_undo_elimination_timeout_minutes(self, var):
        """Enregistre le timeout de "Annule Eliminer" (demande du
        2026-09-17), même principe que _save_elimination_banner_seconds
        juste au-dessus : CE tournoi + préférences globales (repris par
        défaut pour le prochain). Une valeur invalide/vide est ignorée
        sans planter. 0 est une valeur valide (désactive complètement la
        fonction, voir Database.undo_last_elimination_available/undo_
        last_elimination — RÈGLE ABSOLUE : jamais interprété comme
        "illimité"), jamais remonté à 1. Rafraîchit aussitôt l'état du
        bouton "Annule Eliminer" (voir _update_undo_elimination_button_
        state) : un changement de ce réglage prend ainsi effet
        immédiatement, sans attendre le prochain rafraîchissement de
        l'onglet Joueurs."""
        try:
            minutes = max(0, min(180, int(var.get())))
        except (tk.TclError, ValueError):
            return
        self.db.set_settings({"undo_elimination_timeout_minutes": minutes})
        tournament_prefs.save_last_settings({"undo_elimination_timeout_minutes": minutes})
        if hasattr(self, "undo_elimination_btn"):
            self._update_undo_elimination_button_state()

    def _save_club_name(self):
        """Enregistre en continu le "Nom du Club" (préférence partagée,
        commune à tous les tournois/Sit & Go — voir _build_settings_tab)
        au fur et à mesure de la saisie, comme pour les jetons."""
        export_prefs.save_value("club_name", self.settings_vars["club_name"].get())

    def _save_tournament_name(self):
        """Enregistre en continu le "Nom du tournoi" pour CE tournoi, au fur
        et à mesure de la saisie (voir _build_settings_tab) — sans passer
        par _collect_and_save_all_settings, qui a d'autres effets de bord
        (rééquilibrage des tables...) non souhaités à chaque frappe."""
        self.db.set_settings({"tournament_name": self.settings_vars["tournament_name"].get()})
        self._update_window_title()

    def _apply_settings_button(self):
        """Bouton "Appliquer à ce tournoi" : applique tout de suite tous
        les réglages du formulaire (voir _collect_and_save_all_settings),
        sans rien demander d'autre — contrairement à "Enregistrer
        Paramètres sous...", qui applique aussi tout mais force en plus
        à nommer un modèle réutilisable. Ajouté après qu'un utilisateur
        ait changé "Nombre de sièges par table" sans jamais cliquer
        aucun des deux boutons de sauvegarde (juste tapé la nouvelle
        valeur dans le champ, en pensant que ça suffisait) : le champ
        seul ne fait rien tant qu'il n'est pas appliqué, ce bouton rend
        ça évident et immédiat, sans le détour par une fenêtre de nom de
        modèle."""
        self._collect_and_save_all_settings()
        self._refresh_all()
        messagebox.showinfo("Paramètres appliqués", "Les réglages ont été appliqués à ce tournoi.")

    def _save_settings_as_template(self):
        """Applique tous les réglages du formulaire à ce tournoi (comme
        l'ancien bouton "Enregistrer les paramètres"), PUIS propose EN
        PLUS de les enregistrer sous un nom choisi par l'utilisateur,
        pour les réappliquer plus tard à d'autres tournois via
        "Récupérer Paramètres..." (le nom du tournoi lui-même n'est
        jamais inclus dans le modèle).

        Ces deux actions sont volontairement indépendantes : appliquer
        les réglages à CE tournoi (ex : un nouveau "Nombre de sièges par
        table") ne doit JAMAIS dépendre de la nomination d'un modèle —
        avant ce correctif, annuler la fenêtre de nom (ce qu'on ferait
        naturellement en ne voulant pas créer de modèle réutilisable,
        juste appliquer le réglage à ce tournoi) annulait TOUT, y compris
        l'application des réglages eux-mêmes : un joueur avait ainsi pu
        changer "Nombre de sièges par table" sans que ça ne prenne jamais
        effet, simplement en annulant cette fenêtre."""
        values = self._collect_and_save_all_settings()
        self._refresh_all()

        dlg = SaveTemplateAsDialog(
            self,
            "Enregistrer Paramètres sous",
            "Nom de ce modèle de réglages (cliquez un modèle existant "
            "ci-dessous pour l'écraser, ou entrez un nouveau nom) :",
            settings_templates.list_templates(),
        )
        name = dlg.result
        if not name:
            # Réglages déjà appliqués ci-dessus, même sans nommer de
            # modèle réutilisable — voir le docstring.
            return
        if name in settings_templates.list_templates():
            if not messagebox.askyesno(
                "Confirmer",
                f"Un modèle nommé « {name} » existe déjà. Le remplacer ?",
            ):
                return

        settings_templates.save_template(name, values)
        messagebox.showinfo(
            "Paramètres enregistrés",
            f"Les réglages ont été appliqués à ce tournoi, et enregistrés "
            f"sous « {name} » pour une réutilisation future.",
        )

    def _open_settings_templates(self):
        SettingsTemplatesDialog(self)

    def _print_settings_pdf(self):
        """Enregistre tous les réglages du formulaire (comme "Enregistrer
        Paramètres sous...", mais sans leur donner de nom réutilisable —
        applique juste tout à ce tournoi) PUIS génère un PDF récapitulatif
        (voir Database.export_settings_pdf / SETTINGS_PRINT_FIELDS)."""
        self._collect_and_save_all_settings()
        name = self.db.get_setting("tournament_name", "parametres") or "parametres"
        safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip() or "parametres"
        path = filedialog.asksaveasfilename(
            title="Imprimer les paramètres",
            defaultextension=".pdf",
            filetypes=[("Fichier PDF", "*.pdf")],
            initialfile=f"parametres_{safe_name}.pdf",
        )
        if not path:
            return
        try:
            # club_name : lu directement depuis le champ du formulaire (pas
            # depuis self.db, où il n'est jamais écrit — voir le docstring
            # de Database.export_settings_pdf).
            self.db.export_settings_pdf(path, club_name=self.settings_vars["club_name"].get())
        except ImportError:
            show_missing_export_module("pdf")
            return
        open_file_with_default_app(path)

    def _print_movement_slips(self):
        """Bouton "Imprimer" de l'onglet Mouvements : génère une feuille de
        coupons vierges à découper (voir Database.export_movement_slips_pdf)
        — rien n'est préempli automatiquement à partir des mouvements
        affichés dans le tableau, le responsable écrit chaque coupon à la
        main au moment où il remet effectivement le joueur en mouvement."""
        path = filedialog.asksaveasfilename(
            title="Imprimer les coupons de mouvement de table",
            defaultextension=".pdf",
            filetypes=[("Fichier PDF", "*.pdf")],
            initialfile="coupons_mouvement_table.pdf",
        )
        if not path:
            return
        try:
            self.db.export_movement_slips_pdf(path)
        except ImportError:
            show_missing_export_module("pdf")
            return
        open_file_with_default_app(path)

    def _print_movement_slips_filled(self):
        """Bouton "Imprimer" de l'onglet Mouvements (à distinguer de
        "Imprimer Vierge") : imprime un coupon par mouvement réellement
        affiché dans le tableau, déjà rempli (voir
        Database.export_movement_slips_filled_pdf) — pratique quand
        beaucoup de joueurs sont concernés à la fois."""
        if self.db.count_seat_moves() == 0:
            messagebox.showinfo(
                "Aucun mouvement",
                "Aucun mouvement de table en attente à imprimer pour l'instant.",
            )
            return
        path = filedialog.asksaveasfilename(
            title="Imprimer les mouvements en cours",
            defaultextension=".pdf",
            filetypes=[("Fichier PDF", "*.pdf")],
            initialfile="coupons_mouvement_table_rempli.pdf",
        )
        if not path:
            return
        try:
            self.db.export_movement_slips_filled_pdf(path)
        except ImportError:
            show_missing_export_module("pdf")
            return
        except ValueError:
            messagebox.showinfo(
                "Aucun mouvement",
                "Aucun mouvement de table en attente à imprimer pour l'instant.",
            )
            return
        open_file_with_default_app(path)

    # ---------------------------------------------------------------
    # Boucle de rafraîchissement
    # ---------------------------------------------------------------
    def _refresh_all(self):
        current = self.notebook.tab(self.notebook.select(), "text")
        if current == "Joueurs":
            self._refresh_players_tab()
        elif current == "Tables":
            self._refresh_tables_tab()
        elif current == "Mouvements":
            self._refresh_moves_tab()
        elif current == "Primes":
            self._refresh_bounty_tab()
        elif current == "Chronomètre":
            self._refresh_clock_tab()
        elif current == "Blindes":
            self._refresh_blinds_tab()
        elif current == "Classement":
            self._refresh_payouts_tab()
        elif current == "Répertoire":
            # Le répertoire peut avoir changé depuis un autre onglet
            # (ex : nouveau joueur ajouté via Joueurs, qui l'enregistre
            # aussi au répertoire) — sans ce rafraîchissement, revenir
            # sur cet onglet pouvait afficher une liste périmée.
            self.roster_tab._refresh()
        elif current == "LOG":
            # Chantier "LOG", Phase 2 (2026-09-24) : relance la RECHERCHE
            # EN COURS (filtres actuellement affichés CONSERVÉS, jamais
            # réinitialisés ici — voir _refresh_log_tab) à chaque retour
            # sur cet onglet, pour refléter des actions journalisées
            # entre-temps — jamais de polling périodique (voir _tick,
            # volontairement non modifié pour LOG).
            self._refresh_log_tab()

    def _on_notebook_tab_changed(self, event):
        """<<NotebookTabChanged>> — événement virtuel émis par
        ttk.Notebook chaque fois que l'onglet sélectionné change (clic,
        clavier, ou self.notebook.select() programmatique), déjà utilisé
        ici pour rafraîchir le contenu de l'onglet nouvellement affiché
        (voir _refresh_all, appel PRÉEXISTANT, comportement inchangé).

        Complété le 2026-09-09 ("la fenêtre flottante de demande de
        téléphone ne doit être visible QUE sur l'onglet Paramètres") :
        appeler _check_remote_device_requests() ICI, en plus du sondage
        périodique (~2s, voir _tick), pour que le passage à/depuis
        Paramètres montre/masque la fenêtre IMMÉDIATEMENT au clic, sans
        attendre le prochain sondage — _refresh_remote_device_popup,
        appelée depuis _check_remote_device_requests, décide seule si la
        fenêtre doit apparaître ou se masquer, quel que soit ce qui a
        déclenché l'appel (ce changement d'onglet, ou le sondage
        périodique ci-dessous, qui continue de tourner sans jamais
        recréer/réafficher la fenêtre sur un autre onglet — voir sa
        docstring)."""
        self._refresh_all()
        self._check_remote_device_requests()

    def _tick(self):
        if not self.winfo_exists():
            return
        # Corps de _tick() entièrement protégé (demande du 2026-09-09,
        # diagnostic du bug de non-propagation de "Calculer les primes"
        # entre deux fenêtres) : une exception survenue N'IMPORTE OÙ
        # ci-dessous (bandeau d'élimination, contrôle à distance,
        # rééquilibrage en attente, sélection téléphone...) ne doit
        # JAMAIS empêcher la reprogrammation du tick suivant (`finally`
        # ci-dessous) — sans cette protection, un incident isolé sur
        # UNE seule fonctionnalité arrêtait silencieusement TOUTE la
        # boucle périodique de cette fenêtre pour de bon, y compris la
        # synchronisation des primes (placée plus loin dans cette même
        # fonction) alors que rien ne le signalait à l'utilisateur.
        # `except Exception` (jamais un `except:` nu, jamais un simple
        # `pass`) : laisse passer KeyboardInterrupt/SystemExit, et
        # consigne l'exception de façon EXPLICITE et exploitable dans
        # crash.log (voir _log_exception, déjà utilisé par ailleurs
        # dans ce fichier) — jamais avalée sans trace. Voir aussi
        # tests/test_tick_never_stops_scheduling.py, qui injecte une
        # exception avant la synchronisation des primes et vérifie les
        # trois garanties : trace journalisée, prochain tick programmé,
        # boucle non interrompue.
        try:
            current = self.notebook.tab(self.notebook.select(), "text")
            # Expiration du bandeau d'élimination : vérifiée ICI,
            # INCONDITIONNELLEMENT à chaque tick (1x/seconde), plutôt que
            # seulement dans _refresh_clock_tab() ci-dessous (qui, elle, ne
            # tourne QUE si l'onglet Chronomètre est affiché ou l'écran
            # projecteur est ouvert — voir la condition juste en dessous). Un
            # bandeau doit disparaître à l'heure même si aucun des deux n'est
            # vrai au moment précis de son échéance (ex : l'écran projecteur
            # fermé puis rouvert entre-temps) — voir aussi _refresh_clock_tab,
            # qui n'a plus besoin de repasser dessus (source unique, testé en
            # conditions réelles : onglet différent, fenêtre projecteur
            # ouverte plus de 15s, éliminations rapprochées, bouton
            # "Chronomètre" du téléphone).
            if (self._elimination_banner_current is not None
                    and time.time() >= self._elimination_banner_current["until"]):
                self._advance_elimination_banner()
            elif self._elimination_banner_current is None and self._elimination_banner_queue:
                self._advance_elimination_banner()
            if current == "Chronomètre" or (self.clock_window is not None and self.clock_window.winfo_exists()):
                self._refresh_clock_tab()
            elif current == "Mouvements":
                self._refresh_moves_tab()
            elif current == "Joueurs" and self.db is not None:
                # "Timeout pour Annuler Eliminer" (demande du 2026-09-17) :
                # revérifié ici, 1x/seconde, tant que l'onglet Joueurs est
                # affiché — pour que le bouton se grise de lui-même dans
                # la seconde qui suit l'expiration du délai, sans attendre
                # une action qui rafraîchirait cet onglet pour une autre
                # raison (voir _update_undo_elimination_button_state).
                self._update_undo_elimination_button_state()
            elif current.startswith("CA"):
                # Libellé "Code : XXXXXX" (demande du 2026-09-19, suite à
                # un incident où ce code avait été régénéré — pollution de
                # tests — pendant qu'un tournoi restait ouvert : le
                # libellé, calculé une seule fois à la construction de
                # l'onglet, restait alors périment affiché sans que le
                # responsable ne puisse le savoir) : revérifié ici,
                # UNIQUEMENT tant que l'onglet "CA" est affiché (déplacé
                # depuis Paramètres le 2026-09-22, séparé de "LOG" le
                # 2026-09-24 — jamais à chaque tick de CHAQUE fenêtre
                # ouverte, pour ne pas relire ce fichier partagé
                # inutilement ; "LOG" ne commence pas par "CA", il est
                # donc naturellement exclu) — voir _refresh_remote_
                # control_code_label.
                self._refresh_remote_control_code_label()
            if self._remote_photo_uploaded:
                # Une photo vient d'être envoyée depuis le téléphone (voir
                # _remote_upload_photo) : rafraîchit la colonne Photo de
                # l'onglet actuellement affiché, sans attendre que l'utilisateur
                # change d'onglet et y revienne.
                self._remote_photo_uploaded = False
                if current == "Répertoire":
                    self.roster_tab._refresh()
                elif current == "Joueurs":
                    self._refresh_players_tab()
            # Tenu à jour ici (thread principal) plutôt que lu directement
            # depuis le thread du serveur de contrôle à distance — voir
            # _start_remote_control_if_enabled.
            if self.remote_control_server is not None and self.db is not None:
                self._remote_control_tournament_name = self.db.get_setting("tournament_name", "Tournoi")
                self._refresh_remote_players_cache()
                self._refresh_remote_moves_cache()
                self._refresh_remote_dirto_permissions_cache()
                self._remote_clock_paused = self.db.get_setting_int("is_paused", 1) == 1
                self._remote_has_pending_moves = self.db.count_seat_moves() > 0
                self._maybe_reclaim_default_remote_port()
            # Approbation des téléphones (demande du 2026-09-09) : toutes
            # les ~2s seulement (pas à chaque tick, voir _check_remote_
            # device_requests) — un compteur simple plutôt qu'un after()
            # séparé, pour rester protégé par le même try/except que le
            # reste de _tick.
            self._remote_devices_tick_counter = getattr(self, "_remote_devices_tick_counter", 0) + 1
            if self._remote_devices_tick_counter % 2 == 0:
                self._check_remote_device_requests()
            # Filet de sécurité (voir docstring de _check_pending_rebalance) :
            # garantit qu'une question "grosse blinde" en attente est toujours
            # affichée/rafraîchie au moins une fois par seconde, même si
            # l'action qui l'a créée ne l'a pas déjà fait explicitement.
            self._check_pending_rebalance()
            self._check_phone_selected_pid()
            self._sync_single_tournament_pref_checkbox()
            # Interrupteur général "Calculer les primes" (voir le grand bloc
            # de commentaires au-dessus de _sync_primes_enabled_pref) :
            # appelé INCONDITIONNELLEMENT ici, à chaque tick de CHAQUE
            # fenêtre ouverte (pas seulement si l'onglet Paramètres est
            # affiché), pour garantir qu'aucun tournoi pas encore démarré ne
            # puisse rester bloqué sur une ancienne valeur plus d'environ une
            # seconde après un changement fait depuis une autre fenêtre.
            if self.db is not None:
                _sync_primes_enabled_pref(self.db)
            self._sync_primes_enabled_checkbox()
        except Exception:
            _log_exception(*sys.exc_info())
        finally:
            # Reprogrammé INCONDITIONNELLEMENT — y compris après une
            # exception ci-dessus — voir le commentaire au tout début de
            # cette méthode : c'est LA garantie que cette correction
            # apporte.
            self._tick_after_id = self.after(1000, self._tick)

    def _maybe_reclaim_default_remote_port(self):
        """Corrige la perte de connexion du téléphone après "Fin de la
        partie" (voir remote_control.py: /end_tournament et JS
        confirmEndTournament) quand le tournoi fermé tenait le port
        habituel (8765, remote_control.DEFAULT_PORT) : le téléphone ne
        parle jamais qu'à ce port précis (pare-feu club), qui disparaît
        avec le processus qui vient de se fermer — un simple lien "revenir
        au Lobby" ne servirait donc plus à rien puisque le serveur qui
        l'aurait servi n'existe plus.

        Appelé depuis _tick sur CHAQUE tournoi encore ouvert (donc sur
        chacun des éventuels survivants) : si CE tournoi-ci n'est pas déjà
        sur 8765 et qu'aucun tournoi encore inscrit au registre partagé
        (voir open_windows.list_remote_tournaments) ne l'occupe non plus,
        tente de le récupérer (RemoteControlServer.try_reclaim_default_
        port — sans risque de rester sans port du tout, voir sa
        docstring). Aucune négociation entre processus : chacun réessaie
        à son propre tick, le premier qui réussit son bind() l'emporte,
        les autres réessaieront simplement au tick suivant s'il s'avère
        que ce n'était pas encore le bon moment. Le téléphone (voir
        confirmEndTournament, qui retente /lobbylist un court instant
        après "Fin de la partie") retrouve ainsi ce tournoi tout seul,
        sans action manuelle."""
        server = self.remote_control_server
        if server is None or not server.is_running or server.port == remote_control.DEFAULT_PORT:
            return
        if any(t["port"] == remote_control.DEFAULT_PORT for t in open_windows.list_remote_tournaments()):
            return  # quelqu'un d'autre l'a déjà (ou toujours) — rien à faire ici
        if server.try_reclaim_default_port() and self.db:
            open_windows.update_remote_info(
                self.db.path, server.port, self._remote_control_tournament_name
            )
            self._refresh_remote_control_status()

    def _check_phone_selected_pid(self):
        """Bascule automatiquement CETTE fenêtre au premier plan sur le
        Mac si un téléphone vient de choisir SON tournoi dans la page
        "Lobby" du contrôle à distance (voir open_windows.
        set_phone_selected_pid, appelé par /select_tournament) — appelé
        depuis _tick (donc actif en continu tant que cette fenêtre de
        tournoi est ouverte, PAS seulement quand une fenêtre Lobby Mac
        est ouverte : avant ce correctif, seule LobbyDialog._refresh
        surveillait phone_selected_pid.json, donc rien ne réagissait au
        choix du téléphone si aucun Lobby n'était affiché sur le Mac à ce
        moment-là).

        Même principe et même garde-fou que LobbyDialog._refresh (voir
        son commentaire) : ne réagit qu'à un CHANGEMENT de pid par
        rapport au dernier déjà traité ici (_last_synced_phone_pid,
        jamais ré-appliqué en boucle), et seulement si ce pid est le
        SIEN — les autres fenêtres de tournoi ouvertes voient le même
        changement au même moment (chacune sa propre variable
        _last_synced_phone_pid) mais ne se ramènent pas elles-mêmes au
        premier plan puisque ce n'est pas leur pid."""
        phone_pid = open_windows.get_phone_selected_pid()
        if phone_pid is None or phone_pid == self._last_synced_phone_pid:
            return
        self._last_synced_phone_pid = phone_pid
        if phone_pid == os.getpid():
            open_windows.bring_pid_to_front(phone_pid)

    def _refresh_remote_control_code_label(self):
        """Tient à jour le libellé "Code : XXXXXX" de Paramètres (demande
        du 2026-09-19) : ce code est calculé une seule fois à la
        construction de l'onglet (voir _build_tabs, commentaire "pas
        besoin de rafraîchir ce libellé à chaque tick" — hypothèse
        invalidée par un incident réel où le fichier partagé avait été
        régénéré, avec un code différent, pendant qu'un tournoi restait
        ouvert : le libellé affichait alors un code périmé sans que le
        responsable ne puisse s'en rendre compte).

        Continue d'utiliser open_windows.remote_session_code() — la
        SEULE source de vérité déjà partagée par /authenticate (voir
        open_windows.verify_remote_code) — jamais un cache/état séparé
        recréé ici : simple lecture, jamais une régénération (remote_
        session_code() ne régénère que si le fichier a disparu, ce qui
        n'est jamais provoqué par une simple lecture).

        Appelé depuis _tick UNIQUEMENT tant que l'onglet Paramètres est
        affiché (jamais à chaque tick de CHAQUE fenêtre ouverte, pour
        rester un travail négligeable : une lecture d'un petit fichier
        JSON sous verrou, pas plus coûteuse que les autres lectures déjà
        faites à ce rythme ailleurs dans _tick), et ne touche le widget
        que si le texte a réellement changé."""
        code_lbl = getattr(self, "remote_control_code_lbl", None)
        if code_lbl is None:
            return
        try:
            if not code_lbl.winfo_exists():
                return
        except tk.TclError:
            return
        code = open_windows.remote_session_code()
        new_text = f"Code : {code}" if code else ""
        if code_lbl.cget("text") != new_text:
            code_lbl.config(text=new_text)

    def _refresh_remote_players_cache(self):
        """Reconstruit self._remote_players_cache (liste de joueurs actifs
        pour la page "Éliminations" du contrôle à distance) depuis le
        thread principal — jamais depuis le thread du serveur web, voir
        RemoteControlServer. Appelé chaque seconde tant que le contrôle à
        distance est actif (_tick), et une fois au démarrage du serveur."""
        if not self.db:
            self._remote_players_cache = []
            return
        tables = {t["id"]: t["name"] for t in self.db.list_tables(active_only=False)}
        self._remote_players_cache = [
            {
                "id": p["id"],
                "name": p["name"],
                "table": tables.get(p["table_id"]),
                "seat": p["seat"],
                # Pour la page "Photos" du contrôle à distance : indique
                # d'un coup d'œil qui a déjà une photo au répertoire,
                # sans endpoint séparé (voir player_photos.get_photo_path).
                "has_photo": player_photos.get_photo_path(p["name"]) is not None,
            }
            for p in self.db.list_players(status="active")
        ]

    def _refresh_remote_moves_cache(self):
        """Reconstruit self._remote_moves_cache (mouvements ACTUELLEMENT
        en attente pour la page "Mouvements" du contrôle à distance)
        depuis le thread principal — jamais depuis le thread du serveur
        web. Même source que l'onglet Mouvements (self.db.get_seat_moves,
        déjà "pending only" par construction : une ligne confirmée, via
        confirm_seat_move, ou tout le lot via clear_seat_moves, disparaît
        immédiatement de cette même table — aucune logique séparée)."""
        if not self.db:
            self._remote_moves_cache = []
            return
        self._remote_moves_cache = [
            {
                "id": m["id"],
                "player_name": m["player_name"],
                "old_table_name": m["old_table_name"],
                "old_seat": m["old_seat"],
                "new_table_name": m["new_table_name"],
                "new_seat": m["new_seat"],
            }
            for m in self.db.get_seat_moves()
        ]

    def _refresh_remote_dirto_permissions_cache(self):
        """Reconstruit self._remote_dirto_permissions_cache (Phase 4,
        "Sécurisation du Contrôle à distance", 2026-09-20) — {dirto_name
        (tel que stocké, voir Database.set_dirto_authorization) ->
        frozenset des clés REMOTE_PERMISSION_* accordées POUR CE
        TOURNOI} — depuis le thread principal, jamais depuis le thread
        du serveur de contrôle à distance (même remarque que _refresh_
        remote_players_cache/_refresh_remote_moves_cache : self.db ne
        doit JAMAIS être touché depuis ce dernier). Lu par remote_
        control.py via get_dirto_permissions, appelé UNIQUEMENT quand le
        propriétaire résolu de l'appareil est classé DIRTO au
        Répertoire. Appelée au démarrage du serveur ET à chaque tick
        (voir _tick) — une modification/un retrait fait depuis l'onglet
        Paramètres (voir _on_grant_remote_dirto_permissions/_on_revoke_
        remote_dirto_permissions, qui l'appellent aussi immédiatement)
        prend donc effet pour le téléphone sans nouvelle authentification,
        au plus tard au tick suivant (~1s)."""
        if not self.db:
            self._remote_dirto_permissions_cache = {}
            return
        self._remote_dirto_permissions_cache = {
            auth["dirto_name"]: frozenset(auth["permissions"])
            for auth in self.db.list_dirto_authorizations()
        }

    def _cancel_tick(self):
        after_id = getattr(self, "_tick_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
            self._tick_after_id = None


if __name__ == "__main__":
    # Argument optionnel : chemin d'un fichier .tournoi à ouvrir directement
    # (voir spawn_app_process/App.__init__) — utilisé par le Lobby SNG pour
    # ouvrir un tournoi précis dans une nouvelle fenêtre, sans passer par
    # l'écran d'accueil.
    _install_crash_logging()
    _open_path = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        app = App(open_path=_open_path)
        app.mainloop()
    except Exception:
        _log_exception(*sys.exc_info())
        raise
