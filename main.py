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
import time
import json
import csv
import shutil
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog, colorchooser

from database import (
    Database, build_period_summary, export_period_summary_csv,
    export_period_summary_xlsx, export_period_summary_pdf,
    read_player_names_from_file, bounty_unit_value, find_players_active_elsewhere,
    find_stale_active_players, withdraw_stale_active_players,
    find_finished_tournament_files, archive_tournament_files,
    format_date_fr, format_datetime_fr,
    PERIOD_TOURNAMENT_COLUMNS, PERIOD_PLAYER_COLUMNS,
    RESULT_COLUMNS, PAYOUT_COLUMNS, PLAYERS_TAB_COLUMNS, PRIMES_COLUMNS,
    BOUNTY_HISTORY_COLUMNS,
)
from structures import default_blind_structure, standard_payout_structure, generate_blind_structure
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
from help_browser import HelpBrowser, TAB_TO_CHAPTER
import license as licensing
from version import APP_NAME, APP_VERSION

# Écran de démarrage ("Chargement en cours...", voir
# windows/poker_tournament.spec) : le module pyi_splash n'existe que
# dans l'exécutable Windows compilé avec un écran de démarrage — absent
# en lancement depuis les sources ou sur macOS, d'où ce try/except.
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

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


def spawn_app_process(extra_args=None):
    """Lance une nouvelle instance indépendante de l'application (autre
    processus). `extra_args` : arguments supplémentaires passés au
    programme — notamment le chemin d'un fichier .tournoi à ouvrir
    directement, sans passer par l'écran d'accueil (voir
    App.__init__/open_path, et LobbyDialog qui l'utilise pour "Ouvrir"
    un tournoi de la liste dans sa propre fenêtre). Renvoie l'objet
    Popen. Lève OSError si le lancement échoue (à l'appelant de gérer)."""
    extra_args = list(extra_args or [])
    if getattr(sys, "frozen", False):
        # Application empaquetée (PyInstaller) : sys.executable est déjà
        # le programme lui-même, pas besoin de lui repasser main.py.
        return subprocess.Popen([sys.executable, *extra_args])
    return subprocess.Popen([sys.executable, os.path.abspath(__file__), *extra_args])


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
    fenêtre reste alors ouverte, juste pas mise en avant automatiquement."""
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
        # contexte de dossier connu (aucun grisage dans ce cas).
        self.active_elsewhere = find_players_active_elsewhere(
            conflict_folder, self.roster_names,
            exclude_path=conflict_exclude_path, date=conflict_date,
        )

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
        self.new_name_club_var = tk.StringVar()
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
        self.new_name_club_var.set("")
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


def ask_club_dialog(master, title="Club", current_club=""):
    """Petite fenêtre pour choisir un club dans une liste déroulante des
    clubs déjà connus du répertoire, ou en saisir un nouveau. Renvoie le
    club choisi/saisi (chaîne, éventuellement vide), ou None si annulé."""
    win = tk.Toplevel(master)
    win.title(title)
    win.configure(bg=FELT_DARK)
    win.resizable(False, False)
    win.transient(master)
    win.grab_set()
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

        ttk.Label(
            self, text="Joueurs habituels (proposés à la création d'un tournoi)",
            font=("Helvetica", 10, "bold"),
        ).pack(anchor="w", padx=12, pady=(12, 6))

        # Tous les boutons d'action tout en haut de la fenêtre (avant la
        # zone de liste, extensible) : ils restent ainsi toujours visibles
        # en premier, quelle que soit la hauteur prise par la liste sur un
        # écran donné.
        add_frame = ttk.Frame(self)
        add_frame.pack(fill="x", padx=12, pady=(0, 8))
        self.new_name_var = tk.StringVar()
        entry = ttk.Entry(add_frame, textvariable=self.new_name_var)
        entry.pack(side="left", fill="x", expand=True)
        entry.bind("<Return>", lambda e: self._add())
        ttk.Button(add_frame, text="Ajouter", command=self._add).pack(side="left", padx=5)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=12, pady=(0, 4))
        ttk.Button(btns, text="Renommer...", command=self._rename).pack(side="left", padx=3)
        ttk.Button(btns, text="Modifier le club...", command=self._edit_club).pack(side="left", padx=3)
        ttk.Button(btns, text="Supprimer", command=self._delete).pack(side="left", padx=3)
        ttk.Button(
            btns, text="Tout supprimer", command=self._delete_all, style="Danger.TButton",
        ).pack(side="left", padx=3)

        btns2 = ttk.Frame(self)
        btns2.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Button(
            btns2, text="Importer les joueurs d'un tournoi existant...",
            command=self._import_from_tournament,
        ).pack(fill="x")

        btns_csv = ttk.Frame(self)
        btns_csv.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Button(
            btns_csv, text="Importer (CSV)...", command=self._import_csv,
        ).pack(side="left", fill="x", expand=True)
        ttk.Button(
            btns_csv, text="Exporter (CSV)...", command=self._export_csv,
        ).pack(side="left", fill="x", expand=True, padx=(6, 0))

        btns3 = ttk.Frame(self)
        btns3.pack(fill="x", padx=12, pady=(0, 8))
        reactivate_btn = ttk.Button(
            btns3, text="Tout réactiver...", command=self._reactivate_all,
        )
        reactivate_btn.pack(fill="x")
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
        self.roster_tree = ttk.Treeview(
            list_frame, columns=("name", "club"), show="headings", selectmode="browse",
        )
        self.roster_tree.heading("name", text="Nom", command=lambda: self._sort_roster_by("name"))
        self.roster_tree.heading("club", text="Club", command=lambda: self._sort_roster_by("club"))
        self.roster_tree.column("name", width=180, anchor="w")
        self.roster_tree.column("club", width=140, anchor="w")
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
        # pady(top) : laisse un peu d'air au-dessus de la vignette, qui
        # touchait presque le haut de la fenêtre auparavant.
        preview_container.pack(pady=(24, 0))
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
        labels = {"name": "Nom", "club": "Club"}
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
        for e in entries:
            self.roster_tree.insert("", "end", iid=e["name"], values=(e["name"], e["club"]))
        if selected and self.roster_tree.exists(selected):
            self.roster_tree.selection_set(selected)
        self._refresh_preview()

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
        if name:
            roster.add_to_roster(name)
            self.new_name_var.set("")
            self._refresh()

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
        for name in names:
            roster.add_to_roster(name)
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
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, delimiters=";,")
                except csv.Error:
                    dialect = csv.excel
                    dialect.delimiter = ";"
                rows = list(csv.reader(f, dialect))
        except OSError as e:
            messagebox.showerror("Erreur", f"Impossible de lire ce fichier :\n{e}", parent=self)
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
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f, delimiter=";")
                writer.writerow(["NOM", "CLUB"])
                for e in entries:
                    writer.writerow([e["name"], e["club"]])
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
        # Dossier choisi explicitement pour le Lobby (via "Choisir un
        # dossier...") en priorité ; sinon, se rabat sur le dernier
        # dossier utilisé pour créer un tournoi (voir default_tournament_dir)
        # — et LE SUIT À CHAQUE RAFRAÎCHISSEMENT tant qu'aucun dossier
        # explicite n'a été choisi (voir _refresh), pour qu'un tournoi/SNG
        # créé dans un nouveau dossier apparaisse sans avoir à fermer et
        # rouvrir le Lobby, ni à re-choisir un dossier à la main.
        self._explicit_folder = bool(export_prefs.load_value("lobby_folder"))
        self.folder = (
            export_prefs.load_value("lobby_folder")
            or export_prefs.load_value("last_tournament_dir")
            or ""
        )
        self._after_id = None
        self._paths_by_iid = {}

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=10)
        choose_folder_btn = ttk.Button(top, text="📂  Choisir un dossier...", command=self._choose_folder)
        choose_folder_btn.pack(side="left")
        self.folder_lbl = ttk.Label(
            top, text=self.folder or "(aucun dossier choisi)", foreground=MUTED,
        )
        self.folder_lbl.pack(side="left", padx=10)
        Tooltip(
            choose_folder_btn,
            "Ne sert qu'à \"Archiver les terminés...\" ci-contre : la\n"
            "liste ci-dessous montre toujours tous les tournois\n"
            "actuellement ouverts (peu importe leur dossier), jamais\n"
            "les autres fichiers .tournoi d'un dossier.",
        )
        ttk.Button(top, text="🔄  Rafraîchir", command=self._refresh).pack(side="right")
        archive_btn = ttk.Button(
            top, text="🗄  Archiver les terminés...", command=self._archive_finished,
        )
        archive_btn.pack(side="right", padx=(0, 8))
        Tooltip(
            archive_btn,
            "Déplace chaque tournoi terminé de ce dossier dans un\n"
            "sous-dossier \"archive\" créé (si besoin) dans son propre\n"
            "dossier — celui où il a été créé, pas ailleurs.",
        )

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
        if self.folder:
            self._refresh()
        self._schedule_refresh()

    def _choose_folder(self):
        folder = filedialog.askdirectory(
            title="Choisir le dossier des tournois", parent=self,
        )
        if not folder:
            return
        self.folder = folder
        self._explicit_folder = True
        export_prefs.save_value("lobby_folder", folder)
        self.folder_lbl.config(text=folder)
        self._refresh()

    def _refresh(self):
        if not self._explicit_folder:
            # Aucun dossier choisi à la main : reprend le dernier dossier
            # utilisé pour créer un tournoi à CHAQUE rafraîchissement (pas
            # seulement à l'ouverture du Lobby), pour qu'un tournoi/SNG
            # tout juste créé dans un nouveau dossier apparaisse sans
            # avoir à fermer/rouvrir cette fenêtre.
            latest = export_prefs.load_value("last_tournament_dir") or ""
            if latest and latest != self.folder:
                self.folder = latest
                self.folder_lbl.config(text=latest)
        selected_path = self._selected_path()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._paths_by_iid = {}

        # UNIQUEMENT les tournois actuellement ouverts (voir
        # open_windows.py), peu importe leur dossier — plus le contenu
        # d'un dossier scanné : un tournoi juste présent sur disque (pas
        # ouvert dans une fenêtre en ce moment) n'a pas sa place ici,
        # seul "Archiver les terminés..." ci-dessus continue de
        # s'appuyer sur le dossier choisi. "Masquer les tournois
        # terminés" (coché par défaut) filtre encore ensuite : un
        # tournoi ouvert mais déjà terminé (fenêtre pas encore refermée)
        # ne doit pas non plus y figurer.
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

        if selected_path:
            for iid, p in self._paths_by_iid.items():
                if p == selected_path:
                    self.tree.selection_set(iid)
                    break

    def _selected_path(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self._paths_by_iid.get(sel[0])

    def _archive_finished(self):
        if not self.folder or not os.path.isdir(self.folder):
            messagebox.showinfo("Archiver", "Choisissez d'abord un dossier.", parent=self)
            return
        entries = find_finished_tournament_files(self.folder)
        if not entries:
            messagebox.showinfo(
                "Archiver", "Aucun tournoi terminé à archiver dans ce dossier.", parent=self,
            )
            return
        names = "\n".join(
            f"- {e['name']} ({os.path.basename(e['path'])})" for e in entries
        )
        if not messagebox.askyesno(
            "Archiver les tournois terminés",
            f"{len(entries)} tournoi(s) terminé(s) va(ont) être déplacé(s) chacun dans un "
            f"sous-dossier « archive » (créé si besoin, dans son propre dossier) :\n\n"
            f"{names}\n\nContinuer ?",
            parent=self,
        ):
            return
        moved = archive_tournament_files([e["path"] for e in entries])
        self._refresh()
        messagebox.showinfo("Archiver", f"{moved} tournoi(s) archivé(s).", parent=self)

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
        try:
            proc = spawn_app_process([path])
        except OSError as e:
            messagebox.showerror(
                "Erreur", f"Impossible d'ouvrir ce tournoi :\n{e}", parent=self,
            )
            return
        raise_process_when_ready(self, proc.pid)

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
            default_folder = os.path.dirname(os.path.abspath(app.db.path))
        self.folder_var = tk.StringVar(value=default_folder)
        self.recursive_var = tk.BooleanVar(value=True)
        today = datetime.now()
        self.date_from_var = tk.StringVar(value=f"{today.year}-01-01")
        self.date_to_var = tk.StringVar(value=today.strftime("%Y-%m-%d"))

        # Barre de boutons tout en haut de la fenêtre (et non en bas) :
        # ainsi elle reste toujours visible en premier, quelle que soit la
        # hauteur prise par le reste du contenu sur un écran donné.
        btns = ttk.Frame(self)
        btns.pack(side="top", fill="x", padx=14, pady=(14, 6))
        ttk.Button(btns, text="Exporter...", command=self._open_export_dialog).pack(side="left")

        params = ttk.Frame(self)
        params.pack(fill="x", padx=14, pady=(14, 6))

        row1 = ttk.Frame(params)
        row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="Dossier des tournois :").pack(side="left")
        ttk.Entry(row1, textvariable=self.folder_var, width=55).pack(
            side="left", padx=6, fill="x", expand=True
        )
        ttk.Button(row1, text="Parcourir...", command=self._browse_folder).pack(side="left")

        row2 = ttk.Frame(params)
        row2.pack(fill="x", pady=3)
        ttk.Checkbutton(
            row2, text="Inclure les sous-dossiers", variable=self.recursive_var,
        ).pack(side="left")

        row3 = ttk.Frame(params)
        row3.pack(fill="x", pady=3)
        ttk.Label(row3, text="Période — du (AAAA-MM-JJ) :").pack(side="left")
        ttk.Entry(row3, textvariable=self.date_from_var, width=12).pack(side="left", padx=(4, 16))
        ttk.Label(row3, text="au (AAAA-MM-JJ) :").pack(side="left")
        ttk.Entry(row3, textvariable=self.date_to_var, width=12).pack(side="left", padx=4)
        ttk.Label(
            row3, text="(laisser vide = pas de borne)", foreground=MUTED,
        ).pack(side="left", padx=10)
        ttk.Button(row3, text="Générer la synthèse", command=self._generate).pack(side="right")

        self.info_lbl = ttk.Label(self, text="", font=("Helvetica", 10, "bold"))
        self.info_lbl.pack(fill="x", padx=14, pady=(0, 6))

        panes = ttk.Frame(self)
        panes.pack(fill="both", expand=True, padx=14, pady=(0, 6))

        top_pane = ttk.LabelFrame(panes, text="Tournois de la période")
        top_pane.pack(fill="both", expand=True, pady=(0, 6))
        # Pas de colonne "Prize pool (€)" ici : ce club ne distribue pas de
        # gains en argent réel (voir Classement, colonnes Total investi/
        # Gains classement retirées pour la même raison) — la donnée reste
        # calculée normalement (build_period_summary), juste pas affichée.
        cols_t = ("date", "name", "status", "entries", "winner", "bounty")
        headers_t = ["Date", "Tournoi", "Statut", "Entrées", "Vainqueur", "Primes distribuées (€)"]
        self.tournaments_tree = ttk.Treeview(top_pane, columns=cols_t, show="headings", height=8)
        for c, h in zip(cols_t, headers_t):
            self.tournaments_tree.heading(c, text=h)
            self.tournaments_tree.column(c, width=120, anchor="center")
        self.tournaments_tree.column("name", width=180, anchor="w")
        self.tournaments_tree.pack(fill="both", expand=True, padx=6, pady=6)

        bottom_pane = ttk.LabelFrame(panes, text="Classement des joueurs sur la période (primes incluses)")
        bottom_pane.pack(fill="both", expand=True)
        # Pas de "Total investi (€)" ni "Gains classement (€)" : voir la
        # remarque équivalente ci-dessus pour "Tournois de la période".
        cols_p = ("name", "played", "wins", "best", "bounty", "net")
        headers_p = [
            "Joueur", "Tournois joués", "Victoires", "Meilleur Rang",
            "Primes gagnées (€)", "Net (€)",
        ]
        self.players_tree = ttk.Treeview(bottom_pane, columns=cols_p, show="headings", height=10)
        for c, h in zip(cols_p, headers_p):
            self.players_tree.heading(c, text=h)
            self.players_tree.column(c, width=115, anchor="center")
        self.players_tree.column("name", width=170, anchor="w")
        self.players_tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _browse_folder(self):
        path = filedialog.askdirectory(
            title="Choisir le dossier contenant les fichiers .tournoi",
            initialdir=self.folder_var.get() or os.path.expanduser("~"),
        )
        if path:
            self.folder_var.set(path)

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

        self.summary = build_period_summary(
            folder, date_from=date_from, date_to=date_to,
            recursive=self.recursive_var.get(),
        )
        self._refresh_display()

    def _refresh_display(self):
        for row in self.tournaments_tree.get_children():
            self.tournaments_tree.delete(row)
        for row in self.players_tree.get_children():
            self.players_tree.delete(row)

        if self.summary is None:
            return

        tournaments = self.summary["tournaments"]
        players = self.summary["players"]

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

        for idx, a in enumerate(players):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.players_tree.insert(
                "", "end",
                values=(
                    a["name"], a["tournaments_played"], a["wins"], a["best_place"] or "-",
                    f"{a['total_bounty_won']:,}".replace(",", " ") if a["total_bounty_won"] else "-",
                    f"{a['net']:.2f}",
                ),
                tags=(tag,),
            )
        self.players_tree.tag_configure("evenrow", background=CREAM)
        self.players_tree.tag_configure("oddrow", background=CREAM_ALT)

        self.info_lbl.config(
            text=f"{len(tournaments)} tournoi(s) trouvé(s) sur la période — {len(players)} joueur(s) distinct(s)."
        )

    def _open_export_dialog(self):
        if not self.summary or not (self.summary["tournaments"] or self.summary["players"]):
            messagebox.showinfo("Info", "Générez d'abord une synthèse non vide.")
            return
        PeriodExportDialog(self, self.summary)


class PeriodExportDialog(tk.Toplevel):
    """Choix du format (CSV / Excel) et des colonnes à exporter pour une
    synthèse par période déjà générée (voir PeriodSummaryDialog)."""

    def __init__(self, master, summary):
        super().__init__(master)
        self.summary = summary
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
                export_period_summary_xlsx(self.summary, path, tournament_keys=t_keys, player_keys=p_keys)
            elif fmt == "pdf":
                export_period_summary_pdf(self.summary, path, tournament_keys=t_keys, player_keys=p_keys)
            else:
                export_period_summary_csv(self.summary, path, tournament_keys=t_keys, player_keys=p_keys)
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
            columns, var_map = PRIMES_COLUMNS, self.col_vars_summary
            sort_col = self.sort_state.get("column")
            if sort_col:
                headers_by_key = {k: h for k, h, _ in PRIMES_COLUMNS}
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


class App(tk.Tk):
    def __init__(self, open_path=None):
        super().__init__()
        self.withdraw()
        self.title(f"{APP_NAME}  —  v{APP_VERSION}")
        self.geometry("1200x750")

        self.db = None
        self.clock_window = None
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
        # Contrôle à distance depuis un téléphone (voir remote_control.py).
        self.remote_control_server = None
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
            try:
                self.db = Database(open_path)
            except Exception as e:
                messagebox.showerror(
                    "Erreur", f"Impossible d'ouvrir ce fichier :\n{e}"
                )
                self.destroy()
                return
        elif not self._choose_tournament_file():
            self.destroy()
            return

        # Enregistre ce processus comme affichant ce tournoi (voir
        # open_windows.py) : permet au Lobby SNG de détecter qu'il est
        # déjà ouvert ici et de ramener CETTE fenêtre au premier plan
        # plutôt que d'en ouvrir une deuxième sur le même fichier.
        open_windows.register(self.db.path)

        self.deiconify()
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
        # remplaçant celle-ci.
        lobby_header_btn = ttk.Button(
            inner, text="📋  Lobby", command=self._open_lobby,
        )
        lobby_header_btn.pack(side="left", pady=14)
        Tooltip(
            lobby_header_btn,
            "Vue d'ensemble de tous les tournois/Sit & Go actuellement\n"
            "ouverts (dans n'importe quelle fenêtre) et pas encore\n"
            "terminés — double-cliquez pour basculer vers l'un d'eux.",
        )
        new_window_btn = ttk.Button(
            inner, text="🏠  Menu principal", command=self._open_new_window,
        )
        new_window_btn.pack(side="left", padx=(8, 0), pady=14)
        Tooltip(
            new_window_btn,
            "Ouvre une nouvelle fenêtre indépendante de l'application, sans\n"
            "fermer celle-ci — pratique pour gérer plusieurs Sit & Go (ou\n"
            "tournois) en même temps, chacun dans sa propre fenêtre. Vous\n"
            "y retrouverez l'écran d'accueil habituel (Nouveau tournoi /\n"
            "Sit & Go rapide / Ouvrir).",
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
        de paramètres)."""
        name = self.db.get_setting("tournament_name", "Tournoi") if self.db else ""
        if self.db and name in ("", "Nouveau tournoi", "Tournoi"):
            fallback = os.path.splitext(os.path.basename(self.db.path))[0]
            if fallback:
                name = fallback
        title = f"{APP_NAME} — {name}" if name else APP_NAME
        self.title(title)
        if hasattr(self, "header_title_lbl"):
            self.header_title_lbl.config(
                text=f"♠ ♥  {APP_NAME} — {name}  ♦ ♣" if name
                else f"♠ ♥  {APP_NAME}  ♦ ♣"
            )

    def _open_new_window(self):
        """Lance une nouvelle instance indépendante de l'application (autre
        processus, avec son propre écran d'accueil), sans toucher à celle
        déjà ouverte — pour gérer plusieurs tournois/Sit & Go à la fois,
        chacun dans sa propre fenêtre."""
        try:
            proc = spawn_app_process()
        except OSError as e:
            messagebox.showerror(
                "Erreur", f"Impossible d'ouvrir une nouvelle fenêtre :\n{e}"
            )
            return
        raise_process_when_ready(self, proc.pid)

    def _open_lobby(self):
        LobbyDialog(self)

    # ---------------------------------------------------------------
    # Ouverture / création du fichier de tournoi
    # ---------------------------------------------------------------
    def _choose_tournament_file(self):
        win = tk.Toplevel(self)
        win.title("Bienvenue")
        win.configure(bg=FELT_DARK)
        win.geometry("480x460")
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
            path = filedialog.asksaveasfilename(
                title="Créer un nouveau tournoi",
                defaultextension=".tournoi",
                filetypes=[("Fichier de tournoi", "*.tournoi")],
                initialfile="tournoi.tournoi",
                initialdir=default_tournament_dir(),
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
            path = filedialog.asksaveasfilename(
                title="Créer un nouveau Sit & Go",
                defaultextension=".tournoi",
                filetypes=[("Fichier de tournoi", "*.tournoi")],
                initialfile="sitngo.tournoi",
                initialdir=default_tournament_dir(),
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
            path = filedialog.askopenfilename(
                title="Ouvrir un tournoi existant",
                filetypes=[("Fichier de tournoi", "*.tournoi"), ("Tous les fichiers", "*.*")],
            )
            if path:
                result["path"] = path
                result["is_new"] = False
                win.destroy()

        btn_frame = tk.Frame(win, bg=FELT_DARK)
        btn_frame.pack(pady=4)
        ttk.Button(
            btn_frame, text="🆕  Nouveau tournoi", command=new_tournament, width=28,
        ).pack(pady=6)
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
        ttk.Button(
            btn_frame, text="📂  Ouvrir un tournoi existant", command=open_tournament, width=28,
        ).pack(pady=6)
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
        ttk.Button(
            btn_frame, text="ℹ️  À propos", command=lambda: self._show_about(win), width=28,
        ).pack(pady=6)

        self.wait_window(win)
        if not result["path"]:
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
            messagebox.showerror(
                "Erreur",
                f"Impossible de créer/ouvrir le fichier de tournoi :\n"
                f"{result['path']}\n\n{e}",
            )
            return False
        if result.get("is_new"):
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
        self._update_window_title()
        if result.get("is_new") and result.get("selected_players"):
            for name in self._filter_active_conflicts(result["selected_players"]):
                self.db.add_player(name)
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
        que ce soit pour de bon (_on_close) ou pour revenir au menu
        principal et en ouvrir un autre dans la même fenêtre
        (_new_tournament) : arrête le contrôle à distance, désinscrit ce
        tournoi du registre des fenêtres ouvertes (voir open_windows.py
        — sinon il resterait signalé "ouvert ici" alors que ce n'est
        plus vrai) et ferme la base."""
        self._stop_remote_control()
        if self.db:
            open_windows.unregister(self.db.path)
            self.db.close()

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
        filemenu.add_command(label="📋 Lobby...", command=self._open_lobby)
        filemenu.add_command(label="🏠 Menu principal (nouvelle fenêtre)...", command=self._open_new_window)
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
        lines = [
            APP_NAME,
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

        self.notebook.add(self.players_tab, text="Joueurs")
        self.notebook.add(self.tables_tab, text="Tables")
        self.notebook.add(self.moves_tab, text="Mouvements")
        self.notebook.add(self.bounty_tab, text="Primes")
        self.notebook.add(self.blinds_tab, text="Blindes")
        self.notebook.add(self.clock_tab, text="Chronomètre")
        self.notebook.add(self.payouts_tab, text="Classement")
        self.notebook.add(self.roster_tab, text="Répertoire")
        self.notebook.add(self.stats_tab, text="Statistiques")
        self.notebook.add(self.settings_tab, text="Paramètres")

        self._build_players_tab()
        self._build_tables_tab()
        self._build_moves_tab()
        self._build_bounty_tab()
        self._build_clock_tab()
        self._build_blinds_tab()
        self._build_payouts_tab()
        self._build_settings_tab()

        self.notebook.bind("<<NotebookTabChanged>>", lambda e: self._refresh_all())

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

        columns = ("sel", "id", "name", "table", "seat", "chips", "buyin", "rebuy", "addon", "bounty", "status", "rang",
                   "elim_time", "elim_round", "eliminated_by")
        headers = ["", "ID", "Nom", "Table", "Siège", "Chips", "Buy-in", "Rebuys", "Add-ons", "Prime", "Statut", "Rang",
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
        self.players_tree = ttk.Treeview(
            self.players_tab, columns=columns, show="tree headings", height=20,
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
        self.players_tree.heading("name", command=lambda: self._sort_players_by("name"))
        self.players_tree.heading("status", command=lambda: self._sort_players_by("status"))
        self.players_tree.heading("table", command=lambda: self._sort_players_by("table"))
        # "Rang" : classement final du joueur (100 pour le 1er éliminé d'un
        # champ de 100, 99 pour le 2e, etc. — voir _sort_players_by), triable.
        self.players_tree.heading("rang", command=lambda: self._sort_players_by("rang"))
        self.players_tree.heading("elim_time", command=lambda: self._sort_players_by("elim_time"))
        self.players_tree.heading("eliminated_by", command=lambda: self._sort_players_by("eliminated_by"))
        self.players_tree.column("sel", width=56, anchor="center", stretch=False)
        self.players_tree.column("name", width=180, anchor="w")
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
        self.players_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.players_tree.bind("<Button-1>", self._on_players_tree_click)
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

    def _apply_checkbox_display(self, row_iid):
        pid = int(row_iid)
        mark = self.CHECKBOX_CHECKED if pid in self.checked_player_ids else self.CHECKBOX_UNCHECKED
        self.players_tree.set(row_iid, "sel", mark)

    def _update_checked_count_label(self):
        n = len(self.checked_player_ids)
        self.checked_count_lbl.config(text=f"{n} joueur(s) coché(s)" if n else "")

    def _sort_players_by(self, column):
        """Tri par clic sur un en-tête (Nom / Statut) : ré-appuyer sur le
        même en-tête inverse l'ordre (croissant <-> décroissant)."""
        if self.players_sort["column"] == column:
            self.players_sort["ascending"] = not self.players_sort["ascending"]
        else:
            self.players_sort["column"] = column
            self.players_sort["ascending"] = True
        self._refresh_players_tab()

    def _update_sort_headings(self):
        base_headers = {
            "name": "Nom", "status": "Statut", "table": "Table", "rang": "Rang",
            "elim_time": "Éliminé le", "eliminated_by": "Éliminé par",
        }
        for col, label in base_headers.items():
            if self.players_sort["column"] == col:
                arrow = " ▲" if self.players_sort["ascending"] else " ▼"
                self.players_tree.heading(col, text=label + arrow)
            else:
                self.players_tree.heading(col, text=label)

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
        continuer l'ajout (pas de conflit, ou confirmé malgré tout)."""
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
        self.db.add_player(name)
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
            self.db.add_player(name)
        if to_add:
            self._refresh_all()

    def _filter_active_conflicts(self, names):
        """Pour une liste de noms à ajouter en une fois : sépare ceux déjà
        actifs dans un autre tournoi du même dossier, prévient en un seul
        message groupé et demande confirmation pour eux uniquement. Renvoie
        la liste finale des noms à ajouter (sans conflit + confirmés)."""
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

    def _eliminate_selected(self):
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
        if len(ids) == 1:
            p = self.db.get_player(ids[0])
            question = f"Éliminer {p['name']} du tournoi ?"
        else:
            question = (
                f"Éliminer ces {len(ids)} joueurs du tournoi ?"
                "\n\n(Élimination groupée : personne ne sera désigné comme "
                "éliminateur, donc aucun bounty (points) ne sera attribué "
                "ici. Éliminez ces joueurs un par un si vous voulez "
                "enregistrer qui élimine qui.)"
            )
        if not messagebox.askyesno("Confirmer", question):
            return

        eliminator_id = None
        if len(ids) == 1:
            eliminator_id = self._ask_eliminator(exclude_id=ids[0])

        moved_count = 0
        for pid in ids:
            moved_count += len(self.db.eliminate_player(pid, eliminated_by_id=eliminator_id))
        self._clear_checked()
        self._refresh_all()
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

    def _ask_eliminator(self, exclude_id):
        """Petite fenêtre pour choisir qui a éliminé le joueur — sert à
        compter ses bounties (kills, onglet Primes) et, si un bounty fixe
        en € est configuré (mécanisme PKO), à le lui attribuer. Renvoie
        l'id du joueur choisi, ou None si ignoré/annulé."""
        # list_players() trie par table/siège (pratique pour l'affichage du
        # tableau Joueurs, pas pour retrouver un nom ici) — trié par nom
        # pour ce menu, plus facile à parcourir.
        candidates = sorted(
            (p for p in self.db.list_players(status="active") if p["id"] != exclude_id),
            key=lambda p: p["name"].lower(),
        )
        if not candidates:
            return None

        win = tk.Toplevel(self)
        win.title("Qui a éliminé ce joueur ?")
        win.configure(bg=FELT_DARK)
        win.resizable(False, False)
        win.transient(self)
        win.grab_set()
        result = {"id": None}

        eliminated = self.db.get_player(exclude_id)
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
        tk.Label(
            win, bg=FELT_DARK, fg=CREAM,
            text="Qui l'a éliminé(e) ?" if eliminated["bounty"] > 0 else
                 "(Compte pour son bounty en points, onglet Primes.)",
        ).pack(padx=16, pady=(0, 10))

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
        ttk.Button(btns, text="Ignorer (pas de prime)", command=skip).pack(side="left", padx=5)
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

    def _trigger_movement_alert(self):
        """Appelé dès qu'un rééquilibrage a réellement déplacé des joueurs
        (élimination ou bouton "Rééquilibrer les tables") : joue le signal
        sonore, met le chronomètre en pause (comme _clock_pause) s'il
        tournait, et active le bandeau clignotant "Changement de tables en
        cours" (onglets Chronomètre + écran projecteur). Bascule aussi
        automatiquement l'onglet Mouvements au premier plan (au-dessus du
        Chronomètre ou de tout autre onglet affiché) pour que le
        responsable voie tout de suite qui doit changer de table, avant de
        dire "Terminé". Le bouton "Terminé" de l'onglet Mouvements
        (_finish_movement_alert) referme le bandeau et relance le
        chronomètre — comme le raccourci clavier Ctrl+Maj+T ou le bouton
        "Terminé" du contrôle à distance (voir _on_voice_word). Annule
        aussi une éventuelle "élimination en attente" (voir
        _voice_start_elimination) : si l'élimination qui vient de se
        produire a justement causé ce rééquilibrage, c'est "Terminé" qui
        clôt le tout maintenant, plus "Chronomètre"."""
        self._play_movement_signal()
        if (self.db.get_setting_int("clock_started", 0) == 1
                and self.db.get_setting_int("is_paused", 1) == 0):
            start = self.db.get_setting_int("level_start_epoch", int(time.time()))
            elapsed = int(time.time()) - start
            self.db.set_settings({"is_paused": 1, "paused_accum_seconds": elapsed})
        self.db.set_settings({"movement_alert_active": 1})
        self.voice_awaiting_resume = False
        self.notebook.select(self.moves_tab)
        self._refresh_moves_tab()
        self._refresh_clock_tab()
        # Ramène la fenêtre principale au premier plan (devant l'écran
        # projecteur, potentiellement plein écran) : changer d'onglet ne
        # suffit pas à rendre le bandeau visible si une autre fenêtre le
        # recouvre encore (voir aussi _voice_start_elimination).
        self.lift()
        self.focus_force()

    def _finish_movement_alert(self):
        """Bouton "Terminé" de l'onglet Mouvements (ou raccourci clavier/
        contrôle à distance, voir _on_voice_word) : referme le bandeau d'alerte,
        relance le chronomètre (comme _clock_resume), vide la liste des
        mouvements affichée (le prochain rééquilibrage la repeuplera avec
        son propre lot), rebascule l'onglet Chronomètre au premier plan
        dans la fenêtre principale (symétrique du passage automatique sur
        Mouvements fait par _trigger_movement_alert) et ramène aussi la
        fenêtre séparée "écran projecteur" au premier plan si elle est
        ouverte — sans ça elle peut rester cachée derrière la fenêtre
        principale une fois l'alerte terminée."""
        self.db.set_settings({"movement_alert_active": 0})
        self.voice_awaiting_resume = False
        self._clock_resume()
        self.db.clear_seat_moves()
        self._refresh_moves_tab()
        self.notebook.select(self.clock_tab)
        self._refresh_clock_tab()
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()

    # ---------------------------------------------------------------
    # Raccourcis clavier "Élimination" / "Terminé" / "Chronomètre" — même
    # dispatch que le contrôle à distance (_on_voice_word) : chaque mot
    # n'est traité que s'il a un sens dans l'état courant du tournoi.
    # ---------------------------------------------------------------
    def _bind_voice_command_shortcuts(self):
        """Raccourcis clavier Ctrl+Maj+E / Ctrl+Maj+C / Ctrl+Maj+T pour les
        3 actions "Élimination"/"Chronomètre"/"Terminé", TOUJOURS actifs.
        Ctrl+Alt a été écarté (Ctrl+Maj utilisé à la place) : sur Mac,
        Option("Alt")+E/C/T compose des caractères spéciaux (é/è, ç, †) au
        niveau du système avant même que l'application ne voie la touche,
        rendant Ctrl+Alt peu fiable là-bas — Maj ne compose jamais de
        caractère spécial, donc fiable sur Windows et Mac. Passent par
        _on_voice_word, exactement comme le contrôle à distance : mêmes
        conditions (ex : "Ctrl+Maj+C" ne fait rien tant qu'aucune
        élimination n'est en attente)."""
        # Chaque raccourci est lié en MAJUSCULE et en minuscule : avec
        # Control enfoncé, Tk ne met pas toujours le keysym en majuscule
        # comme il le ferait pour Maj+lettre seule (constaté sous Windows,
        # où <Control-Shift-E> ne se déclenchait jamais alors que
        # <Control-Shift-e> fonctionne) — lier les deux couvre tous les
        # cas sans dépendre de ce détail d'implémentation par plateforme.
        for key in ("E", "e"):
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
        démarré. Le port ne peut être occupé que par une seule fenêtre à
        la fois — normal et attendu si plusieurs tournois/Sit & Go tournent
        en parallèle (voir "Menu principal", chapitre 14) : la première
        fenêtre ouverte garde le contrôle à distance, les suivantes ne
        l'activent pas mais restent sinon parfaitement utilisables.
        `silent=True` (utilisé au lancement automatique d'une fenêtre,
        justement pour ce cas courant) n'affiche alors aucune fenêtre
        d'erreur — seulement un message dans la console ; `silent=False`
        (case à cocher cliquée explicitement dans Paramètres) affiche
        l'erreur normalement, l'utilisateur a alors besoin du retour."""
        if export_prefs.load_value("remote_control_enabled", False) is not True:
            return
        if self.remote_control_server is not None and self.remote_control_server.is_running:
            return
        server = remote_control.RemoteControlServer(
            on_word=lambda word: self.voice_command_queue.put(word),
            get_tournament_name=lambda: self.db.get_setting("tournament_name", "Tournoi") if self.db else "Tournoi",
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
        téléphone (ou son absence si le serveur n'est pas démarré)."""
        if not hasattr(self, "remote_control_status_lbl"):
            return
        if self.remote_control_server is not None and self.remote_control_server.is_running:
            url = self.remote_control_server.url
            self.remote_control_status_lbl.config(
                text=f"📱 Sur votre téléphone (même wifi que cet ordinateur), ouvrez :\n{url}"
            )
        else:
            self.remote_control_status_lbl.config(text="")

    def _poll_voice_queue(self):
        """Relève régulièrement les mots-clés déposés par le contrôle à
        distance (voir remote_control.py) et les traite ici, sur le thread
        Tkinter — même principe périodique que _tick. S'arrête
        silencieusement de se reprogrammer si la fenêtre a été détruite
        entre-temps (fenêtre secondaire fermée, appli quittée)."""
        if not self.winfo_exists():
            return
        try:
            while True:
                item = self.voice_command_queue.get_nowait()
                self._on_voice_word(item)
        except queue.Empty:
            pass
        self.after(150, self._poll_voice_queue)

    def _on_voice_word(self, word):
        """Dispatché pour chaque mot-clé reçu ("elimination"/"terminer"/
        "chronometre", depuis un raccourci clavier ou le contrôle à
        distance) : n'agit que si ce mot a un sens dans l'état courant du
        tournoi (ex : "chronomètre" ne fait rien tant qu'aucune
        élimination n'est en attente)."""
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
            if alert_active:
                pass
            elif self.voice_awaiting_resume:
                self._voice_resume_clock()
            else:
                # Hors du contexte "reprendre après une élimination" :
                # sert simplement à ramener l'écran projecteur au premier
                # plan (utile s'il a été rapetissé/réduit pour faire autre
                # chose dans le logiciel entre-temps) — sans quoi Ctrl+Maj+C
                # ne faisait rien du tout en dehors de ce contexte précis.
                self._voice_show_clock()

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
        ramène l'onglet Chronomètre + l'écran projecteur au premier plan."""
        self.voice_awaiting_resume = False
        self._clock_resume()
        self.notebook.select(self.clock_tab)
        self._refresh_clock_tab()
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()

    def _voice_show_clock(self):
        """Commande "Chronomètre" (raccourci clavier Ctrl+Maj+C) hors du
        contexte "reprendre après une élimination" (voir _on_voice_word) :
        ramène simplement l'onglet Chronomètre et l'écran projecteur au
        premier plan, en restaurant son plein écran s'il l'était avant
        d'être rapetissé pour faire autre chose dans le logiciel — sans
        toucher à l'état pause/lecture du chrono ni à quoi que ce soit
        d'autre."""
        self.notebook.select(self.clock_tab)
        self._refresh_clock_tab()
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()
        self.lift()

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

    def _refresh_players_tab(self):
        # Garde la liste déroulante des clubs à jour (un club a pu être
        # ajouté/modifié entre-temps depuis le répertoire de joueurs).
        self.new_player_club_combo.configure(values=roster.list_clubs())
        self._update_tournament_started_buttons()
        for row in self.players_tree.get_children():
            self.players_tree.delete(row)
        tables = {t["id"]: t["name"] for t in self.db.list_tables(active_only=False)}
        present_ids = set()

        status_labels = {"active": "Actif", "withdrawn": "Forfait", "eliminated": "Éliminé"}
        players = [dict(p) for p in self.db.list_players()]
        n_active = sum(1 for p in players if p["status"] == "active")
        for p in players:
            p["status_label"] = status_labels.get(p["status"], p["status"])
            p["table_name"] = tables.get(p["table_id"], "-") if p["table_id"] else "-"
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
        if sort_col and not self.players_sort["ascending"]:
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
                    mark, p["id"], p["name"], table_name, p["seat"] or "-",
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

    def _build_tables_tab(self):
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

        scroll_container = ttk.Frame(self.tables_tab)
        scroll_container.pack(fill="both", expand=True, padx=10, pady=(0, 10))

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

    def _on_tables_mousewheel(self, event):
        # event.delta : multiples de 120 sous Windows, valeur brute (~1-3)
        # sous macOS — dans les deux cas, on veut quelques dizaines de
        # pixels par cran de molette.
        self.tables_canvas.yview_scroll(int(-event.delta / 120 * 40) or (-40 if event.delta > 0 else 40), "units")

    def _rebalance(self):
        moves = self.db.rebalance_tables()
        self._refresh_all()
        if moves:
            self._trigger_movement_alert()

    def _refresh_tables_tab(self):
        for w in self.tables_inner.winfo_children():
            w.destroy()
        tables = self.db.list_tables()
        players_by_table = {}
        for p in self.db.list_players(status="active"):
            players_by_table.setdefault(p["table_id"], []).append(p)

        cols = 3
        for idx, t in enumerate(tables):
            frame = ttk.LabelFrame(self.tables_inner, text=t["name"])
            frame.grid(row=idx // cols, column=idx % cols, padx=8, pady=8, sticky="n")
            plist = sorted(players_by_table.get(t["id"], []), key=lambda p: p["seat"] or 0)
            if not plist:
                ttk.Label(frame, text="(vide)").pack(padx=10, pady=6)
            for p in plist:
                ttk.Label(
                    frame,
                    text=f"Siège {p['seat']} — {p['name']}",
                ).pack(anchor="w", padx=10, pady=2)

        # Repart du haut à chaque rafraîchissement (rééquilibrage,
        # élimination...) plutôt que de rester sur une position de
        # défilement qui ne correspond plus forcément au même contenu.
        self.tables_canvas.update_idletasks()
        self.tables_canvas.configure(scrollregion=self.tables_canvas.bbox("all"))
        self.tables_canvas.yview_moveto(0.0)
        self._tables_scroll_paused = False

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

        cols = ("time", "player", "old_table", "old_seat", "new_table", "new_seat")
        headers = ["Heure", "Joueur", "Ancienne table", "Ancien siège", "Nouvelle table", "Nouveau siège"]
        self.moves_tree = ttk.Treeview(self.moves_tab, columns=cols, show="headings", height=20)
        for c, h in zip(cols, headers):
            self.moves_tree.heading(c, text=h)
            self.moves_tree.column(c, width=130, anchor="center")
        self.moves_tree.column("player", width=180, anchor="w")
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
            self.moves_tree.insert(
                "", "end",
                values=(
                    format_datetime_fr(m["moved_at"]),
                    m["player_name"],
                    m["old_table_name"] or "—",
                    m["old_seat"] or "—",
                    m["new_table_name"] or "—",
                    m["new_seat"] or "—",
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
        self.primes_tree.tag_configure("totalcol", font=("Helvetica", 9, "bold"))
        TreeHeadingTooltip(self.primes_tree, {
            "name": "Nom du joueur.",
            "presence": "Prime de présence : points pour avoir participé à ce\ntournoi (réglage Paramètres, 0 = désactivée).",
            "assiduite": "Prime d'assiduité : points si le joueur était déjà présent\naux N derniers tournois consécutifs (réglages Paramètres).",
            "rang": "Place finale du joueur (1 = vainqueur, un chiffre plus élevé\n= éliminé plus tôt). Vide tant que le joueur est encore en jeu.",
            "cl_montant": "Prime de classement : réglage manuel (Paramètres) s'il est\nnon nul, sinon 100×√N / P (N = nb de joueurs, P = Rang).",
            "bo_nombre": "Nombre : nombre de joueurs qu'il a éliminés (kills).",
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

        self._update_primes_sort_headings()
        for row in self.primes_tree.get_children():
            self.primes_tree.delete(row)
        primes_rows = self.db.get_primes_summary(
            sort_column=self.primes_sort["column"], ascending=self.primes_sort["ascending"]
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
            self.bounty_history_tree.insert(
                "", "end",
                values=(
                    format_datetime_fr(e["event_time"]), e["eliminated_name"], e["eliminator_name"] or "—",
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
            ("sound_break_end_path", "Son Fin Pause"),
            ("sound_round_end_path", "Son fin Round"),
        ):
            row = ttk.Frame(win)
            row.pack(fill="x", padx=12, pady=4)
            btn = ttk.Button(row, text=self._clock_sound_button_text(key, label), width=26)
            btn.pack(side="left")
            btn.config(command=lambda k=key, l=label, b=btn: self._choose_clock_sound(k, l, b))
            btn.bind("<Button-2>", lambda e, k=key, l=label, b=btn: self._clear_clock_sound(k, l, b))
            btn.bind("<Button-3>", lambda e, k=key, l=label, b=btn: self._clear_clock_sound(k, l, b))
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
            ttk.Button(
                row, text="Test", width=5, command=lambda k=key: self._test_clock_sound(k),
            ).pack(side="left")

        ttk.Button(win, text="Fermer", command=win.destroy).pack(pady=(6, 12))

    def _clock_resume(self):
        if self.db.get_setting_int("clock_started", 0) == 0:
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
        elif self.db.get_setting_int("is_paused", 1) == 1:
            # reprise : on décale level_start_epoch du temps passé en pause
            self.db.set_settings({"is_paused": 0, "level_start_epoch": int(time.time()) - self._elapsed_before_pause()})
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
        path = export_prefs.load_value(setting_key, "")
        if not path:
            messagebox.showinfo("Test", "Aucun fichier son choisi pour l'instant.")
            return
        if not sound_signal.play_file(path, max_duration_ms=self._clock_sound_duration_ms(setting_key)):
            self.bell()

    def _clock_sound_button_text(self, setting_key, label):
        path = export_prefs.load_value(setting_key, "")
        return f"{label} : {os.path.basename(path)}" if path else f"{label} : (aucun)"

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

    def _next_break_eta_text(self):
        """Texte 'Prochaine pause à HH:MM', calculé à partir du temps
        restant du niveau en cours puis des durées des niveaux suivants
        jusqu'à la prochaine pause de la structure. Si le niveau en cours
        est lui-même une pause, cherche la suivante (pas celle-ci)."""
        remaining, level, _ = self._remaining_seconds()
        if level is None:
            return "Aucune structure définie"
        total_seconds = max(0, remaining)
        for lvl in self.db.get_blind_structure():
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
        round_number = self.db.get_round_number(level["level_order"]) if level is not None else None
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

        selected = self.blinds_tree.selection()
        for row in self.blinds_tree.get_children():
            self.blinds_tree.delete(row)
        current_order = level["level_order"] if level is not None else -1
        for lvl in self.db.get_blind_structure():
            label = "Oui" if lvl["is_break"] else "Non"
            tag = "current" if lvl["level_order"] == current_order else ""
            self.blinds_tree.insert(
                "", "end", iid=str(lvl["level_order"]),
                values=(lvl["level_order"], self.db.get_round_number(lvl["level_order"]),
                        lvl["small_blind"], lvl["big_blind"],
                        lvl["ante"], lvl["duration_minutes"], label),
                tags=(tag,),
            )
        self.blinds_tree.tag_configure("current", background=GOLD, foreground=TEXT_DARK)
        if selected and self.blinds_tree.exists(selected[0]):
            self.blinds_tree.selection_set(selected)

        movement_alert = self.db.get_setting_int("movement_alert_active", 0) == 1
        blink_on = movement_alert and int(time.time()) % 2 == 0
        if blink_on:
            self.movement_alert_lbl.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.movement_alert_lbl.place_forget()

        if self.clock_window is not None and self.clock_window.winfo_exists():
            stats = self.db.get_stats()
            name = self.db.get_setting("tournament_name", "Tournoi")
            # La liste des joueurs concernés n'est utile à l'écran
            # projecteur que pendant l'alerte elle-même (voir
            # ClockWindow._update_movement_moves_table) : inutile
            # d'interroger la base à chaque tick le reste du temps.
            moves = self.db.get_seat_moves() if movement_alert else []
            self.clock_window.refresh(
                remaining, level, next_level, stats, name, paused,
                self._next_break_eta_text(), movement_alert,
                self._load_chip_denominations(), moves, round_number,
            )

    def _open_clock_window(self):
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.bring_to_front()
            return
        self.clock_window = ClockWindow(self, self)

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

    def _refresh_blinds_tab(self):
        for w in self.blinds_rows_frame.winfo_children():
            w.destroy()
        self._blind_row_vars = []

        headers = ["Round", "Hr de Début", "Durée (min)", "Petite Blind", "Grosse Blind",
                   "Ante", "Durée Pause (min)", ""]
        for col, h in enumerate(headers):
            ttk.Label(self.blinds_rows_frame, text=h, font=("Helvetica", 9, "bold"),
                      foreground=GOLD_DARK).grid(row=0, column=col, padx=6, pady=(0, 6), sticky="w")

        rounds = self._blind_rounds_from_db()
        if not rounds:
            rounds = [{"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0}]

        # Heure de début (temps écoulé depuis le début du tournoi) de chaque
        # round : 0:00 pour le premier, puis chaque round suivant démarre
        # à la fin du round précédent + sa pause éventuelle (Durée Pause),
        # pour refléter le temps réellement écoulé à la table.
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

            start_h, start_m = divmod(elapsed_minutes, 60)
            ttk.Label(self.blinds_rows_frame, text=str(i)).grid(row=i, column=0, padx=6, pady=2)
            ttk.Label(self.blinds_rows_frame, text=f"{start_h}:{start_m:02d}").grid(
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
        au plus ancien ; cliquer Nom/Rang trie autrement."""
        rows = [r for r in self.db.players_rows() if r["rang"] is not None]
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
        d'affichage à l'écran (sinon rien ne défile)."""
        if not self.winfo_exists() or not self.payouts_tree.winfo_exists():
            return

        # N'anime que si l'onglet Classement est actuellement affiché, pour
        # ne pas défiler inutilement en arrière-plan.
        if self.notebook.tab(self.notebook.select(), "text") != "Classement":
            self._classement_scroll_after_id = self.after(500, self._classement_autoscroll_tick)
            return

        children = self.payouts_tree.get_children()
        # bbox() d'une ligne renvoie '' tant qu'elle n'est pas réellement
        # visible à l'écran : la dernière ligne a un bbox vide si, et
        # seulement si, le tableau déborde de la zone visible — un
        # indicateur direct, sans les arrondis de fraction de yview().
        overflow = bool(children) and not self.payouts_tree.bbox(children[-1])

        if overflow and not self._classement_scroll_paused:
            top_frac, bottom_frac = self.payouts_tree.yview()
            if bottom_frac < 1.0:
                self.payouts_tree.yview_scroll(1, "units")
                if not self.payouts_tree.bbox(children[-1]):
                    # Toujours pas visible : encore à faire défiler.
                    pass
                else:
                    self._classement_scroll_paused = True
                    self.after(self.CLASSEMENT_AUTOSCROLL_PAUSE_MS, self._classement_resume_autoscroll)
            elif top_frac > 0.0:
                # Arrivé en bas : pause puis retour en haut.
                self._classement_scroll_paused = True
                self.payouts_tree.yview_moveto(0.0)
                self.after(self.CLASSEMENT_AUTOSCROLL_PAUSE_MS, self._classement_resume_autoscroll)

        self._classement_scroll_after_id = self.after(
            self.CLASSEMENT_AUTOSCROLL_INTERVAL_MS, self._classement_autoscroll_tick
        )

    def _classement_resume_autoscroll(self):
        self._classement_scroll_paused = False

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

        save_as_settings_btn = ttk.Button(
            left, text="💾 Enregistrer Paramètres sous...", command=self._save_settings_as_template,
        )
        save_as_settings_btn.grid(row=len(fields), column=0, columnspan=2, pady=(15, 3))
        Tooltip(
            save_as_settings_btn,
            "Applique tous les réglages ci-dessus à ce tournoi ET les\n"
            "enregistre sous un nom au choix, pour les réutiliser plus\n"
            "tard sur d'autres tournois/Sit & Go (le nom du tournoi et la\n"
            "structure de blindes elle-même ne sont pas inclus — voir\n"
            "\"Récupérer Blindes\" pour ça séparément).",
        )
        load_settings_btn = ttk.Button(
            left, text="📂 Récupérer Paramètres...", command=self._open_settings_templates,
        )
        load_settings_btn.grid(row=len(fields) + 1, column=0, columnspan=2, pady=(0, 15))
        Tooltip(
            load_settings_btn,
            "Ouvre la liste des réglages déjà enregistrés (via\n"
            "\"Enregistrer Paramètres sous...\") pour en appliquer un à\n"
            "ce tournoi.",
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
            ("round_duration_minutes", "Durée d'un Round en min"),
            ("break_duration_minutes", "Durée de la Pause (minutes)"),
        ]
        blind_field_tips = {
            "ante_start_level": "Compte uniquement les niveaux de blindes\n(les pauses ne sont pas comptées comme un niveau).",
            "start_ante": "Ante au niveau de départ choisi ci-dessus ; elle grandit\nensuite proportionnellement au big blind sur les niveaux suivants.",
            "round_duration_minutes": "Durée (en minutes) de chaque niveau de blindes lors\nde la régénération ci-dessous — pas les pauses (réglage séparé).",
        }
        for j, (key, label) in enumerate(blind_fields, start=1):
            default = {
                "start_small_blind": 25, "start_big_blind": 50,
                "ante_start_level": 4, "start_ante": 25,
                "round_duration_minutes": 15,
                "break_duration_minutes": 15,
            }[key]
            lbl = ttk.Label(right, text=label + " :")
            lbl.grid(row=j, column=0, sticky="w", pady=4)
            if key in blind_field_tips:
                Tooltip(lbl, blind_field_tips[key])
            var = tk.StringVar(value=self.db.get_setting(key, str(default)))
            ttk.Entry(right, textvariable=var, width=25).grid(row=j, column=1, pady=4, padx=10)
            self.settings_vars[key] = var

        blind_next_row = len(blind_fields) + 1
        regen_btn = ttk.Button(
            right, text="🎲  Régénérer la structure de blindes avec ces valeurs",
            command=self._generate_custom_blind_structure,
        )
        regen_btn.grid(row=blind_next_row, column=0, columnspan=2, pady=(8, 15))
        Tooltip(
            regen_btn,
            "Remplace toute la structure de blindes par une nouvelle,\n"
            "calculée à partir des 6 valeurs ci-dessus (fonctionne aussi\n"
            "en plein milieu d'un tournoi). Enregistre aussi TOUS les\n"
            "paramètres en même temps, comme le bouton \"Enregistrer les\n"
            "paramètres\" — pas besoin de cliquer les deux.",
        )

        bounty_start_row = blind_next_row + 1
        ttk.Separator(right, orient="horizontal").grid(
            row=bounty_start_row, column=0, columnspan=2, sticky="ew", pady=(0, 15)
        )
        primes_title = ttk.Label(
            right, text="Primes",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        primes_title.grid(row=bounty_start_row + 1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        Tooltip(
            primes_title,
            "4 primes en points, cumulées par joueur dans l'onglet Primes :\n"
            "Présence, Assiduité, Classement et Bounty. Leur somme donne\n"
            "le TOTAL de chaque joueur pour ce tournoi.",
        )

        presence_lbl = ttk.Label(right, text="Montant de la prime de présence (en points) :")
        presence_lbl.grid(row=bounty_start_row + 2, column=0, sticky="w", pady=4)
        Tooltip(
            presence_lbl,
            "Points attribués à tout joueur inscrit à ce tournoi, quel que\n"
            "soit son résultat. 0 = prime désactivée.",
        )
        attendance_var = tk.StringVar(value=self.db.get_setting("attendance_bonus_points", "0"))
        ttk.Entry(right, textvariable=attendance_var, width=25).grid(
            row=bounty_start_row + 2, column=1, pady=4, padx=10
        )
        self.settings_vars["attendance_bonus_points"] = attendance_var

        assiduity_lbl = ttk.Label(right, text="Montant de la prime d'assiduité en points :")
        assiduity_lbl.grid(row=bounty_start_row + 3, column=0, sticky="w", pady=4)
        Tooltip(
            assiduity_lbl,
            "Points attribués si le joueur a été présent lors des\n"
            "N derniers tournois consécutifs (voir réglage juste en dessous),\n"
            "ce tournoi-ci inclus. 0 = prime désactivée.",
        )
        assiduity_var = tk.StringVar(value=self.db.get_setting("assiduity_bonus_points", "0"))
        ttk.Entry(right, textvariable=assiduity_var, width=25).grid(
            row=bounty_start_row + 3, column=1, pady=4, padx=10
        )
        self.settings_vars["assiduity_bonus_points"] = assiduity_var

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
        ttk.Entry(right, textvariable=consecutive_var, width=25).grid(
            row=bounty_start_row + 4, column=1, pady=4, padx=10
        )
        self.settings_vars["assiduity_consecutive_days"] = consecutive_var
        ttk.Label(
            right,
            text=("0 = pas de prime d'assiduité ; 2 = ce tournoi + le précédent ;\n"
                  "3 = ce tournoi + les 2 précédents ; etc."),
            foreground=MUTED,
        ).grid(row=bounty_start_row + 5, column=0, columnspan=2, sticky="w", pady=(0, 4))

        ranking_lbl = ttk.Label(right, text="Montant de la prime de classement en points :")
        ranking_lbl.grid(row=bounty_start_row + 6, column=0, sticky="w", pady=4)
        Tooltip(
            ranking_lbl,
            "Valeur fixe (en points) attribuée au rang final d'un joueur.\n"
            "Si ce champ est à 0, la valeur est calculée automatiquement\n"
            "avec la formule 100×√N / P (N = nombre de joueurs du tournoi,\n"
            "P = rang du joueur), pour ne pas sur-récompenser les petits\n"
            "champs. Connue seulement une fois le joueur éliminé (ou\n"
            "vainqueur, tournoi terminé).",
        )
        ranking_var = tk.StringVar(value=self.db.get_setting("ranking_bonus_points", "0"))
        ttk.Entry(right, textvariable=ranking_var, width=25).grid(
            row=bounty_start_row + 6, column=1, pady=4, padx=10
        )
        self.settings_vars["ranking_bonus_points"] = ranking_var

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
        ttk.Entry(right, textvariable=bounty_var, width=25).grid(
            row=bounty_start_row + 7, column=1, pady=4, padx=10
        )
        self.settings_vars["bounty_amount"] = bounty_var

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

        pko_pct_lbl = ttk.Label(right, text="Part en Perso immédiat en PKO (%) :")
        pko_pct_lbl.grid(row=bounty_start_row + 9, column=0, sticky="w", pady=4)
        Tooltip(
            pko_pct_lbl,
            "% du bounty que l'éliminateur empoche immédiatement en\n"
            "Perso ; le reste (100% - ce pourcentage) s'ajoute à son\n"
            "propre bounty, à remporter par qui l'éliminera à son tour.",
        )
        pko_pct_var = tk.StringVar(value=self.db.get_setting("pko_cash_percent", "50"))
        ttk.Entry(right, textvariable=pko_pct_var, width=25).grid(
            row=bounty_start_row + 9, column=1, pady=4, padx=10
        )
        self.settings_vars["pko_cash_percent"] = pko_pct_var

        ttk.Label(
            right,
            text=("Le bounty s'applique aux nouvelles inscriptions/rebuys après\n"
                  "avoir enregistré. En mode classique, l'éliminateur empoche toute\n"
                  "la prime en Perso ; en PKO, une partie s'ajoute à sa propre prime."),
            foreground=MUTED,
        ).grid(row=bounty_start_row + 10, column=0, columnspan=2, sticky="w", pady=(4, 10))

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
            "Ctrl+Maj+E met le chrono en pause et bascule sur l'onglet\n"
            "Joueurs ; Ctrl+Maj+C le relance (si aucun mouvement n'a eu\n"
            "lieu) ; Ctrl+Maj+T referme l'alerte de mouvement. Toujours\n"
            "actifs, rien à activer dans les réglages.",
        )
        ttk.Label(
            right,
            text=("Ctrl+Maj+E Élimination   |   Ctrl+Maj+C Chronomètre   |   Ctrl+Maj+T Terminé"),
            foreground=MUTED, wraplength=340, justify="left",
        ).grid(row=voice_start_row + 2, column=0, columnspan=2, sticky="w", pady=(0, 10))

        # -- Contrôle à distance depuis un téléphone (voir
        # remote_control.py) : 3 mêmes actions (Élimination/Chronomètre/
        # Terminé) accessibles depuis une page web ouverte sur un
        # téléphone connecté au même wifi que cet ordinateur — rien à
        # installer sur le téléphone.
        remote_start_row = voice_start_row + 3
        ttk.Separator(right, orient="horizontal").grid(
            row=remote_start_row, column=0, columnspan=2, sticky="ew", pady=(0, 15)
        )
        remote_title = ttk.Label(
            right, text="Contrôle à distance (téléphone)",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        remote_title.grid(row=remote_start_row + 1, column=0, columnspan=2, sticky="w", pady=(0, 8))
        Tooltip(
            remote_title,
            "Sert une petite page web (3 boutons : Élimination/\n"
            "Chronomètre/Terminé, identiques aux raccourcis clavier)\n"
            "consultable depuis n'importe quel téléphone connecté au\n"
            "même réseau Wifi que cet ordinateur — rien à installer,\n"
            "juste ouvrir l'adresse affichée dans un navigateur.",
        )

        self.remote_control_enabled_var = tk.BooleanVar(
            value=export_prefs.load_value("remote_control_enabled", False) is True
        )
        remote_check = ttk.Checkbutton(
            right, text="Activer le contrôle à distance",
            variable=self.remote_control_enabled_var, command=self._on_remote_control_toggle,
        )
        remote_check.grid(row=remote_start_row + 2, column=0, columnspan=2, sticky="w", pady=(0, 6))

        self.remote_control_status_lbl = ttk.Label(right, foreground=MUTED, justify="left")
        self.remote_control_status_lbl.grid(
            row=remote_start_row + 3, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )
        self._refresh_remote_control_status()

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

    def _generate_custom_blind_structure(self):
        try:
            sb = int(self.settings_vars["start_small_blind"].get())
            bb = int(self.settings_vars["start_big_blind"].get())
            ante_lvl = int(self.settings_vars["ante_start_level"].get())
            start_ante = int(self.settings_vars["start_ante"].get())
            round_duration = int(self.settings_vars["round_duration_minutes"].get())
            break_duration = int(self.settings_vars["break_duration_minutes"].get())
        except (ValueError, KeyError):
            messagebox.showerror(
                "Erreur",
                "Veuillez saisir des nombres entiers valides pour le small blind, "
                "le big blind, le niveau de début des antes, la valeur de l'ante "
                "et les durées.",
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
        if round_duration <= 0:
            messagebox.showerror("Erreur", "La durée d'un round doit être supérieure à 0.")
            return
        if break_duration <= 0:
            messagebox.showerror("Erreur", "La durée de la pause doit être supérieure à 0.")
            return
        if not messagebox.askyesno(
            "Confirmer",
            "Régénérer toute la structure de blindes avec ces valeurs ?\n"
            "(Cette action fonctionne aussi en plein milieu d'un tournoi.)",
        ):
            return

        new_structure = generate_blind_structure(
            start_small_blind=sb, start_big_blind=bb, ante_start_level=ante_lvl,
            start_ante=start_ante, duration_minutes=round_duration,
            break_duration_minutes=break_duration, break_every=4,
        )
        self.db.set_blind_structure(new_structure)
        # Enregistre TOUS les paramètres en même temps (pas seulement ceux
        # de la structure de blindes) : équivaut à cliquer aussi sur
        # "Enregistrer les paramètres", sans avoir à le faire séparément.
        self._collect_and_save_all_settings()
        current_order = self.db.get_setting_int("current_level_order", 1)
        if current_order > len(new_structure):
            self.db.set_settings({"current_level_order": len(new_structure)})
        self._refresh_all()
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
        sous...", qui en a aussi besoin pour le modèle nommé)."""
        values = {}
        for k, v in self.settings_vars.items():
            raw = v.get()
            if isinstance(raw, bool):
                raw = "1" if raw else "0"
            values[k] = raw
        # "club_name" est commun à tous les tournois/Sit & Go (préférences
        # partagées, voir le trace_add posé sur son StringVar) : jamais
        # écrit dans ce fichier .tournoi, pour éviter une copie qui
        # divergerait silencieusement de la valeur réellement affichée.
        db_values = {k: v for k, v in values.items() if k != "club_name"}
        self.db.set_settings(db_values)
        tournament_prefs.save_last_settings(values)
        try:
            new_max_seats = int(values.get("max_seats_per_table", ""))
        except ValueError:
            new_max_seats = None
        if new_max_seats and new_max_seats >= 2:
            self.db.set_all_tables_max_seats(new_max_seats)
        moves = self.db.rebalance_tables()
        self._update_window_title()
        if moves:
            self._trigger_movement_alert()
        return values

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

    def _save_settings_as_template(self):
        """Applique tous les réglages du formulaire à ce tournoi (comme
        l'ancien bouton "Enregistrer les paramètres"), ET les enregistre
        sous un nom choisi par l'utilisateur, pour les réappliquer plus
        tard à d'autres tournois via "Récupérer Paramètres..." (le nom du
        tournoi lui-même n'est jamais inclus dans le modèle)."""
        dlg = SaveTemplateAsDialog(
            self,
            "Enregistrer Paramètres sous",
            "Nom de ce modèle de réglages (cliquez un modèle existant "
            "ci-dessous pour l'écraser, ou entrez un nouveau nom) :",
            settings_templates.list_templates(),
        )
        name = dlg.result
        if not name:
            return
        if name in settings_templates.list_templates():
            if not messagebox.askyesno(
                "Confirmer",
                f"Un modèle nommé « {name} » existe déjà. Le remplacer ?",
            ):
                return

        values = self._collect_and_save_all_settings()
        settings_templates.save_template(name, values)
        self._refresh_all()
        messagebox.showinfo(
            "Paramètres enregistrés",
            f"Les réglages ont été appliqués à ce tournoi, et enregistrés "
            f"sous « {name} » pour une réutilisation future.",
        )

    def _open_settings_templates(self):
        SettingsTemplatesDialog(self)

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

    def _tick(self):
        if not self.winfo_exists():
            return
        current = self.notebook.tab(self.notebook.select(), "text")
        if current == "Chronomètre" or (self.clock_window is not None and self.clock_window.winfo_exists()):
            self._refresh_clock_tab()
        elif current == "Mouvements":
            self._refresh_moves_tab()
        self._tick_after_id = self.after(1000, self._tick)

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
    _open_path = sys.argv[1] if len(sys.argv) > 1 else None
    app = App(open_path=_open_path)
    app.mainloop()
