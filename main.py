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
import time
import json
import shutil
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog

from database import (
    Database, build_period_summary, export_period_summary_csv,
    export_period_summary_xlsx, export_period_summary_pdf,
    read_player_names_from_file, bounty_unit_value, find_players_active_elsewhere,
    find_stale_active_players, withdraw_stale_active_players, find_tournament_files,
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
import player_photos
import sound_signal

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
    """Tente (macOS uniquement, via AppleScript/System Events) de faire
    passer au premier plan le processus `pid` tout juste lancé par
    spawn_app_process. `widget` : n'importe quel widget Tk vivant, utilisé
    seulement pour planifier les tentatives (.after) — pas besoin que ce
    soit la fenêtre App elle-même. Plusieurs essais espacés de 700 ms : le
    temps que Tk démarre et affiche sa fenêtre dans le nouveau processus
    varie. Échoue silencieusement si l'accès Accessibilité n'est pas
    accordé à l'application qui lance ceci (Terminal, IDE...) — la
    fenêtre reste alors ouverte, juste pas mise en avant automatiquement."""
    if sys.platform != "darwin":
        return
    script = (
        f'tell application "System Events" to set frontmost of '
        f'(first process whose unix id is {pid}) to true'
    )
    try:
        subprocess.Popen(
            ["osascript", "-e", script],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
    if attempt < 3:
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
        self.grab_set()
        self.selected_names = []
        self.check_vars = {}
        self.exclude_names = exclude_names or set()
        self.roster_names = [n for n in roster.load_roster() if n not in self.exclude_names]
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
            if conflicted:
                var.set(False)
            check = ttk.Checkbutton(
                self.list_frame,
                text=f"{name}  (déjà actif ailleurs)" if conflicted else name,
                variable=var, state="disabled" if conflicted else "normal",
            )
            check.grid(row=idx, column=0, sticky="w", pady=1, padx=(0, 20))
            if conflicted:
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
        self._build_list(filtered)

    def _check_all(self):
        for name in self.roster_names:
            if name in self.active_elsewhere:
                continue  # déjà actif ailleurs : jamais coché, même par "Tout cocher"
            self.check_vars.setdefault(name, tk.BooleanVar()).set(True)
        self._filter()

    def _uncheck_all(self):
        for name in self.roster_names:
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
            self._filter()

    def _skip(self):
        self.selected_names = []
        self.destroy()

    def _confirm(self):
        self.selected_names = [
            n for n, v in self.check_vars.items()
            if v.get() and n not in self.active_elsewhere
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


class RosterManagerDialog(tk.Toplevel):
    """Fenêtre de gestion du répertoire de joueurs habituels, indépendante
    de tout tournoi en cours."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Répertoire de joueurs")
        self.geometry("640x540")
        self.grab_set()
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
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right", padx=3)

        btns2 = ttk.Frame(self)
        btns2.pack(fill="x", padx=12, pady=(0, 8))
        ttk.Button(
            btns2, text="Importer les joueurs d'un tournoi existant...",
            command=self._import_from_tournament,
        ).pack(fill="x")

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
        # Dossier choisi explicitement pour le Lobby en priorité ; sinon,
        # se rabat automatiquement sur le dernier dossier utilisé pour
        # créer un tournoi (voir default_tournament_dir) — pour que les
        # SNG tout juste créés apparaissent sans avoir à re-choisir un
        # dossier à la main.
        self.folder = (
            export_prefs.load_value("lobby_folder")
            or export_prefs.load_value("last_tournament_dir")
            or ""
        )
        self._after_id = None
        self._paths_by_iid = {}

        top = ttk.Frame(self)
        top.pack(fill="x", padx=12, pady=10)
        ttk.Button(top, text="📂  Choisir un dossier...", command=self._choose_folder).pack(side="left")
        self.folder_lbl = ttk.Label(
            top, text=self.folder or "(aucun dossier choisi)", foreground=MUTED,
        )
        self.folder_lbl.pack(side="left", padx=10)
        ttk.Button(top, text="🔄  Rafraîchir", command=self._refresh).pack(side="right")

        cols = ("name", "date", "players", "level", "remaining", "status")
        headers = ["Tournoi", "Date", "Joueurs actifs", "Niveau", "Temps restant", "État"]
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=14)
        for c, h in zip(cols, headers):
            self.tree.heading(c, text=h)
            self.tree.column(c, width=130, anchor="center")
        self.tree.column("name", width=220, anchor="w")
        self.tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        self.tree.bind("<Double-Button-1>", lambda e: self._open_selected())

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(
            bottom, text="Ouvrir dans une nouvelle fenêtre", command=self._open_selected,
        ).pack(side="left")
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
        export_prefs.save_value("lobby_folder", folder)
        self.folder_lbl.config(text=folder)
        self._refresh()

    def _refresh(self):
        selected_path = self._selected_path()
        for row in self.tree.get_children():
            self.tree.delete(row)
        self._paths_by_iid = {}
        if not self.folder or not os.path.isdir(self.folder):
            return
        for idx, path in enumerate(find_tournament_files(self.folder, recursive=False)):
            try:
                db = Database(path)
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
                    status["name"], status["date"],
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

    def _open_selected(self):
        path = self._selected_path()
        if not path:
            messagebox.showinfo(
                "Lobby", "Sélectionnez d'abord un tournoi dans la liste.", parent=self,
            )
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


class PeriodSummaryDialog(tk.Toplevel):
    """Synthèse des résultats de tous les tournois (.tournoi) trouvés dans
    un dossier, pour une période donnée en paramètre (dates de début/fin),
    en mentionnant les primes (bounty) empochées par chaque joueur."""

    def __init__(self, master):
        super().__init__(master)
        self.title("Synthèse des résultats par période")
        self.configure(bg=FELT_DARK)
        self.geometry("920x620")
        self.transient(master)
        self.summary = None

        default_folder = ""
        if getattr(master, "db", None) is not None:
            default_folder = os.path.dirname(os.path.abspath(master.db.path))
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
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right")

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
        cols_t = ("date", "name", "status", "entries", "pool", "winner", "bounty")
        headers_t = ["Date", "Tournoi", "Statut", "Entrées", "Prize pool (€)", "Vainqueur", "Primes distribuées (€)"]
        self.tournaments_tree = ttk.Treeview(top_pane, columns=cols_t, show="headings", height=8)
        for c, h in zip(cols_t, headers_t):
            self.tournaments_tree.heading(c, text=h)
            self.tournaments_tree.column(c, width=120, anchor="center")
        self.tournaments_tree.column("name", width=180, anchor="w")
        self.tournaments_tree.pack(fill="both", expand=True, padx=6, pady=6)

        bottom_pane = ttk.LabelFrame(panes, text="Classement des joueurs sur la période (primes incluses)")
        bottom_pane.pack(fill="both", expand=True)
        cols_p = ("name", "played", "wins", "best", "cost", "gain", "bounty", "net")
        headers_p = [
            "Joueur", "Tournois joués", "Victoires", "Meilleur Rang",
            "Total investi (€)", "Gains classement (€)", "Primes gagnées (€)", "Net (€)",
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
                    t["date"], t["name"], t["status"], t["entries"],
                    f"{t['prize_pool']:.2f}", t["winner"],
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
                    f"{a['total_cost']:.2f}", f"{a['total_gain']:.2f}",
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


class PayoutExportDialog(tk.Toplevel):
    """Choix du format (CSV / Excel) et des colonnes à exporter pour la
    grille de gains telle qu'affichée dans l'onglet Gains (place,
    pourcentage, montant — sans nom de joueur). Distinct de "Exporter les
    résultats..." (menu Fichier), qui exporte le classement nominatif."""

    def __init__(self, master, db):
        super().__init__(master)
        self.db = db
        self.title("Exporter la grille de gains")
        self.configure(bg=FELT_DARK)
        self.geometry("380x360")
        self.transient(master)
        self.grab_set()

        self.format_var = tk.StringVar(value=export_prefs.load_format("payouts"))
        saved_cols = export_prefs.load_columns("payouts", [k for k, _, _ in PAYOUT_COLUMNS])
        self.col_vars = {
            key: tk.BooleanVar(value=key in saved_cols) for key, _, _ in PAYOUT_COLUMNS
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
        for idx, (key, header, _fn) in enumerate(PAYOUT_COLUMNS):
            ttk.Checkbutton(grid, text=header, variable=self.col_vars[key]).grid(
                row=idx, column=0, sticky="w", padx=6, pady=2
            )

    def _do_export(self):
        keys = [k for k, v in self.col_vars.items() if v.get()]
        if not keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        export_prefs.save_columns("payouts", keys)
        export_prefs.save_format("payouts", self.format_var.get())

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
            title="Exporter la grille de gains",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=f"grille_gains_{safe_name}{ext}",
        )
        if not path:
            return

        try:
            if fmt == "xlsx":
                self.db.export_payouts_xlsx(path, columns=keys)
            elif fmt == "pdf":
                self.db.export_payouts_pdf(path, columns=keys)
            else:
                self.db.export_payouts_csv(path, columns=keys)
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
        try:
            if fmt == "xlsx":
                self.db.export_players_xlsx(
                    path, columns=keys, sort_column=sort_column, ascending=ascending
                )
            elif fmt == "pdf":
                self.db.export_players_pdf(
                    path, columns=keys, sort_column=sort_column, ascending=ascending
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

        try:
            if kind == "summary":
                sort_column = self.sort_state.get("column")
                ascending = self.sort_state.get("ascending", True)
                if fmt == "xlsx":
                    self.db.export_primes_xlsx(
                        path, columns=keys, sort_column=sort_column, ascending=ascending
                    )
                elif fmt == "pdf":
                    self.db.export_primes_pdf(
                        path, columns=keys, sort_column=sort_column, ascending=ascending
                    )
                else:
                    self.db.export_primes_csv(
                        path, columns=keys, sort_column=sort_column, ascending=ascending
                    )
            else:
                if fmt == "xlsx":
                    self.db.export_bounty_history_xlsx(path, columns=keys)
                elif fmt == "pdf":
                    self.db.export_bounty_history_pdf(path, columns=keys)
                else:
                    self.db.export_bounty_history_csv(path, columns=keys)
        except ImportError:
            show_missing_export_module(fmt)
            return
        self.destroy()
        open_file_with_default_app(path)


class App(tk.Tk):
    def __init__(self, open_path=None):
        super().__init__()
        self.withdraw()
        self.title("Gestionnaire de Poker Senaco")
        self.geometry("1200x750")

        self.db = None
        self.clock_window = None
        self._apply_theme()

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
        ttk.Button(
            inner, text="🏠  Menu principal", command=self._back_to_main_menu,
        ).pack(side="left", pady=14)
        new_window_btn = ttk.Button(
            inner, text="🚀  Nouveau SitnGO", command=self._open_new_window,
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
            inner, text="♠ ♥  Gestionnaire de Poker Senaco  ♦ ♣",
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
        title = f"Gestionnaire de Poker Senaco — {name}" if name else "Gestionnaire de Poker Senaco"
        self.title(title)
        if hasattr(self, "header_title_lbl"):
            self.header_title_lbl.config(
                text=f"♠ ♥  Gestionnaire de Poker Senaco — {name}  ♦ ♣" if name
                else "♠ ♥  Gestionnaire de Poker Senaco  ♦ ♣"
            )

    def _back_to_main_menu(self):
        if messagebox.askyesno(
            "Menu principal",
            "Fermer ce tournoi et revenir au menu principal ?\n\n"
            "(Rien n'est perdu : toutes les données sont déjà enregistrées "
            "dans le fichier .tournoi.)",
        ):
            self._new_tournament()

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
        win.geometry("480x400")
        win.resizable(False, False)
        win.grab_set()
        result = {"path": None}

        tk.Label(
            win, text="♠ ♥ ♦ ♣",
            bg=FELT_DARK, fg=GOLD, font=("Helvetica", 22, "bold"),
        ).pack(pady=(28, 4))
        tk.Label(
            win, text="Gestionnaire de Tournoi de Poker",
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
                # Le sélecteur de fichier a déjà demandé confirmation pour
                # remplacer ce fichier : on repart alors d'une base
                # complètement vierge (joueurs, tables, mouvements...),
                # plutôt que de rouvrir silencieusement l'ancien tournoi.
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
            "Vue d'ensemble de tous les tournois/Sit & Go d'un dossier :\n"
            "joueurs actifs, niveau, temps restant, en un coup d'œil —\n"
            "double-cliquez un tournoi pour l'ouvrir dans une nouvelle\n"
            "fenêtre. N'ouvre ni ne ferme celle-ci.",
        )

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
        })
        if n_players > 0:
            self.db.set_payout_structure(standard_payout_structure(n_players))

    def _on_close(self):
        if self.db:
            self.db.close()
        self.destroy()

    # ---------------------------------------------------------------
    # Menu
    # ---------------------------------------------------------------
    def _build_menu(self):
        menubar = tk.Menu(self)
        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="🏠 Menu principal", command=self._back_to_main_menu)
        filemenu.add_command(label="🚀 Nouveau SitnGO (nouvelle fenêtre)...", command=self._open_new_window)
        filemenu.add_command(label="📋 Lobby (plusieurs tournois)...", command=self._open_lobby)
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

        self.config(menu=menubar)

    def _open_period_summary(self):
        PeriodSummaryDialog(self)

    def _manage_roster(self):
        RosterManagerDialog(self)

    def _new_tournament(self):
        self.destroy()
        app = App()
        app.mainloop()

    def _open_tournament(self):
        self._new_tournament()

    def _export_results(self):
        if not self.db:
            return
        ResultsExportDialog(self, self.db)

    def _export_payouts(self):
        if not self.db:
            return
        PayoutExportDialog(self, self.db)

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
        self.settings_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.players_tab, text="Joueurs")
        self.notebook.add(self.tables_tab, text="Tables")
        self.notebook.add(self.moves_tab, text="Mouvements")
        self.notebook.add(self.bounty_tab, text="Primes")
        self.notebook.add(self.clock_tab, text="Chronomètre")
        self.notebook.add(self.blinds_tab, text="Blindes")
        self.notebook.add(self.payouts_tab, text="Gains")
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

        self.stats_lbl = ttk.Label(top, text="", font=("Helvetica", 10, "bold"))
        self.stats_lbl.pack(side="right")

        check_bar = ttk.Frame(self.players_tab)
        check_bar.pack(fill="x", padx=10)
        ttk.Button(check_bar, text="Tout cocher", command=self._check_all_players).pack(side="left", padx=3)
        ttk.Button(check_bar, text="Tout décocher", command=self._uncheck_all_players).pack(side="left", padx=3)
        eliminate_btn = ttk.Button(
            check_bar, text="Éliminer", command=self._eliminate_selected, style="Danger.TButton",
        )
        eliminate_btn.pack(side="left", padx=3)
        Tooltip(
            eliminate_btn,
            "Élimine tous les joueurs cochés ci-dessus. Pour un seul\n"
            "joueur, demande qui l'a éliminé (calcule bounty et prime\n"
            "de classement) ; pour plusieurs à la fois, aucun éliminateur\n"
            "n'est demandé.",
        )
        ttk.Button(
            check_bar, text="Exporter les joueurs (Excel/CSV)...", command=self._export_players,
        ).pack(side="left", padx=3)
        columns_btn = ttk.Button(check_bar, text="Colonnes...", command=self._manage_player_columns)
        columns_btn.pack(side="left", padx=3)
        Tooltip(columns_btn, "Choisir quelles colonnes du tableau ci-dessous afficher.")
        self.checked_count_lbl = ttk.Label(check_bar, text="", foreground=GOLD)
        self.checked_count_lbl.pack(side="left", padx=10)

        actions = ttk.Frame(self.players_tab)
        actions.pack(fill="x", padx=10, pady=(6, 10))
        ttk.Button(actions, text="Renommer...", command=self._rename_selected).pack(side="left", padx=3)
        rebuy_btn = ttk.Button(actions, text="Rebuy (+)", command=self._rebuy_selected)
        rebuy_btn.pack(side="left", padx=3)
        Tooltip(rebuy_btn, "Recave : remet le joueur en jeu avec le nombre de\njetons réglé dans Paramètres, incrémente son compteur de rebuys.")
        addon_btn = ttk.Button(actions, text="Add-on (+)", command=self._addon_selected)
        addon_btn.pack(side="left", padx=3)
        Tooltip(addon_btn, "Recharge (add-on) : ajoute des jetons au joueur (réglage\nParamètres), incrémente son compteur d'add-ons.")
        ttk.Button(actions, text="Modifier chips...", command=self._edit_chips_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Modifier achats...", command=self._edit_purchases_selected).pack(side="left", padx=3)
        withdraw_btn = ttk.Button(actions, text="Désactiver (forfait)", command=self._withdraw_selected)
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
        ttk.Button(actions, text="Supprimer", command=self._delete_selected).pack(side="left", padx=3)

        columns = ("sel", "id", "name", "table", "seat", "chips", "buyin", "rebuy", "addon", "bounty", "status", "rang")
        headers = ["", "ID", "Nom", "Table", "Siège", "Chips", "Buy-in", "Rebuys", "Add-ons", "Prime", "Statut", "Rang"]
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
        self.players_tree.column("sel", width=56, anchor="center", stretch=False)
        self.players_tree.column("name", width=180, anchor="w")
        TreeHeadingTooltip(self.players_tree, {
            "sel": "Cocher pour inclure ce joueur dans les actions groupées\n(Éliminer, etc.).",
            "rang": "Place finale du joueur : 1 = vainqueur, un chiffre plus élevé\n= éliminé plus tôt. Vide tant que le joueur est encore en jeu.",
            "bounty": "Prime (bounty) actuellement portée par ce joueur, en points\n(mécanisme interne PKO — voir Paramètres > Primes) —\nà ne pas confondre avec le tableau de l'onglet Primes.",
            "buyin": "Nombre de buy-ins (entrées) de ce joueur dans ce tournoi.",
            "rebuy": "Nombre de recaves (rebuys).",
            "addon": "Nombre de recharges (add-ons).",
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
        base_headers = {"name": "Nom", "status": "Statut", "table": "Table", "rang": "Rang"}
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

    def _manage_player_columns(self):
        """Fenêtre pour réafficher (ou masquer manuellement) les colonnes
        du tableau Joueurs — seul moyen de récupérer une colonne réduite
        à rien, puisqu'elle n'a alors plus de bordure à ressaisir."""
        win = tk.Toplevel(self)
        win.title("Colonnes affichées")
        win.configure(bg=FELT_DARK)
        win.transient(self)
        win.grab_set()

        tk.Label(
            win, text="Colonnes affichées dans l'onglet Joueurs :",
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
            # Redonne explicitement la main (au niveau fenêtre) à la
            # fenêtre principale : sous macOS, une fenêtre overrideredirect
            # qui a reçu un clic peut sinon laisser l'appli entière
            # insensible aux clics une fois masquée (voir commentaire dans
            # _show_autocomplete). On ne force pas le focus clavier sur un
            # champ précis ici, pour ne pas perturber une saisie déjà
            # entamée ailleurs (ex : champ Club) au moment où ce masquage
            # différé se déclenche.
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
        candidates = [
            p for p in self.db.list_players(status="active") if p["id"] != exclude_id
        ]
        if not candidates:
            return None

        win = tk.Toplevel(self)
        win.title("Qui a éliminé ce joueur ?")
        win.configure(bg=FELT_DARK)
        win.resizable(False, False)
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
        """Émet le signal de mouvements (tonalité/durée réglables dans
        Paramètres)."""
        freq = self.db.get_setting_int("movement_signal_frequency_hz", 880)
        duration = self.db.get_setting_int("movement_signal_duration_ms", 300)
        if not sound_signal.play_tone(freq, duration):
            self.bell()  # repli si la lecture audio n'a pas pu être lancée

    def _trigger_movement_alert(self):
        """Appelé dès qu'un rééquilibrage a réellement déplacé des joueurs
        (élimination ou bouton "Rééquilibrer les tables") : joue le signal
        sonore, met le chronomètre en pause (comme _clock_pause) s'il
        tournait, et active le bandeau clignotant "Changement de tables en
        cours" (onglets Chronomètre + écran projecteur). Le bouton "Terminé"
        de l'onglet Mouvements (_finish_movement_alert) referme le bandeau
        et relance le chronomètre."""
        self._play_movement_signal()
        if (self.db.get_setting_int("clock_started", 0) == 1
                and self.db.get_setting_int("is_paused", 1) == 0):
            start = self.db.get_setting_int("level_start_epoch", int(time.time()))
            elapsed = int(time.time()) - start
            self.db.set_settings({"is_paused": 1, "paused_accum_seconds": elapsed})
        self.db.set_settings({"movement_alert_active": 1})
        self._refresh_clock_tab()

    def _finish_movement_alert(self):
        """Bouton "Terminé" de l'onglet Mouvements : referme le bandeau
        d'alerte, relance le chronomètre (comme _clock_resume) et vide la
        liste des mouvements affichée (le prochain rééquilibrage la
        repeuplera avec son propre lot)."""
        self.db.set_settings({"movement_alert_active": 0})
        self._clock_resume()
        self.db.clear_seat_moves()
        self._refresh_moves_tab()

    def _refresh_players_tab(self):
        # Garde la liste déroulante des clubs à jour (un club a pu être
        # ajouté/modifié entre-temps depuis le répertoire de joueurs).
        self.new_player_club_combo.configure(values=roster.list_clubs())
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
        self.stats_lbl.config(
            text=(f"Actifs : {stats['active_count']}  |  Entrées : {stats['entries']}  |  "
                  f"Rebuys : {stats['rebuys']}  |  Add-ons : {stats['addons']}  |  "
                  f"Prize pool : {stats['prize_pool']:,.0f} €").replace(",", " ")
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
        ttk.Button(top, text="Ajouter une table", command=self._add_table).pack(side="left", padx=3)

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

    def _add_table(self):
        self.db.add_table()
        self._refresh_all()

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
                    text=f"Siège {p['seat']} — {p['name']}  ({p['chips']:,}".replace(",", " ") + ")",
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
                    m["moved_at"],
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
                    "Bounty — Nb", "Bounty — Val", "Bounty — Mt", "TOTAL"]
        self.primes_tree = ttk.Treeview(summary, columns=cols1, show="headings", height=14)
        for c, h in zip(cols1, headers1):
            self.primes_tree.heading(c, text=h)
            width = 150 if c == "name" else 95
            self.primes_tree.column(c, width=width, anchor="w" if c == "name" else "center")
        # Tri par clic sur en-tête : Rang, Bounty — Nb, TOTAL (les autres
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
        """Tri par clic sur un en-tête (Rang / Bounty — Nb / TOTAL) : ré-
        appuyer sur le même en-tête inverse l'ordre."""
        if self.primes_sort["column"] == column:
            self.primes_sort["ascending"] = not self.primes_sort["ascending"]
        else:
            self.primes_sort["column"] = column
            self.primes_sort["ascending"] = True
        self._refresh_bounty_tab()

    def _update_primes_sort_headings(self):
        base_headers = {"rang": "Rang", "bo_nombre": "Bounty — Nb", "total": "TOTAL"}
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
                    e["event_time"], e["eliminated_name"], e["eliminator_name"] or "—",
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
        # overlay (place(), pas pack()) par-dessus le reste de l'onglet,
        # affiché/masqué en clignotant depuis _refresh_clock_tab tant que
        # movement_alert_active est actif (voir _trigger_movement_alert /
        # _finish_movement_alert, onglet Mouvements).
        self.movement_alert_lbl = tk.Label(
            frame, text="⚠  Changement de tables en cours  ⚠",
            font=("Helvetica", 20, "bold"), bg=DANGER_RED, fg="white",
            relief="solid", borderwidth=3, padx=24, pady=16,
        )
        self.level_display = ttk.Label(frame, text="", font=("Helvetica", 20, "bold"))
        self.level_display.pack(pady=(20, 5))

        self.timer_display = ttk.Label(frame, text="00:00", font=("Helvetica", 60, "bold"))
        self.timer_display.pack(pady=10)

        self.blinds_display = ttk.Label(frame, text="", font=("Helvetica", 28))
        self.blinds_display.pack()

        self.next_display = ttk.Label(frame, text="", font=("Helvetica", 12))
        self.next_display.pack(pady=(5, 20))

        controls = ttk.Frame(frame)
        controls.pack(pady=10)
        ttk.Button(controls, text="Démarrer / Reprendre", command=self._clock_resume).pack(side="left", padx=5)
        ttk.Button(controls, text="Pause", command=self._clock_pause).pack(side="left", padx=5)
        ttk.Button(controls, text="Niveau précédent", command=self._clock_prev_level).pack(side="left", padx=5)
        ttk.Button(controls, text="Niveau suivant", command=self._clock_next_level).pack(side="left", padx=5)
        ttk.Button(controls, text="Ouvrir l'écran projecteur", command=self._open_clock_window).pack(side="left", padx=15)

        struct_frame = ttk.LabelFrame(frame, text="Structure de blindes")
        struct_frame.pack(fill="both", expand=True, padx=15, pady=10)

        ttk.Label(
            struct_frame, foreground=MUTED,
            text="Astuce : double-cliquez sur un niveau ci-dessous pour y aller directement.",
        ).pack(anchor="w", padx=5, pady=(5, 0))

        tree_frame = ttk.Frame(struct_frame)
        tree_frame.pack(fill="both", expand=True, side="left", padx=5, pady=5)

        cols = ("order", "sb", "bb", "ante", "duration", "break")
        headers = ["Niveau", "SB", "BB", "Ante", "Durée (min)", "Pause ?"]
        self.blinds_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8)
        for c, h in zip(cols, headers):
            self.blinds_tree.heading(c, text=h)
            self.blinds_tree.column(c, width=100, anchor="center")
        TreeHeadingTooltip(self.blinds_tree, {
            "order": "Numéro de niveau, dans l'ordre de jeu (les pauses comptent\naussi comme une ligne).",
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

    def _clock_resume(self):
        if self.db.get_setting_int("clock_started", 0) == 0:
            self.db.set_settings({
                "clock_started": 1,
                "level_start_epoch": int(time.time()),
                "is_paused": 0,
                "paused_accum_seconds": 0,
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
                values=(lvl["level_order"], lvl["small_blind"], lvl["big_blind"],
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
            self.clock_window.refresh(
                remaining, level, next_level, stats, name, paused,
                self._next_break_eta_text(), movement_alert,
            )

    def _open_clock_window(self):
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.lift()
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

        # Conteneur défilable : le nombre de rounds peut largement dépasser
        # la hauteur de l'écran.
        canvas = tk.Canvas(self.blinds_tab, bg=FELT, highlightthickness=0)
        vscroll = ttk.Scrollbar(self.blinds_tab, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vscroll.set)
        canvas.pack(side="left", fill="both", expand=True, padx=(15, 0), pady=10)
        vscroll.pack(side="right", fill="y", pady=10)

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
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["duration"], width=10).grid(row=i, column=2, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["sb"], width=10).grid(row=i, column=3, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["bb"], width=10).grid(row=i, column=4, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["ante"], width=10).grid(row=i, column=5, padx=6, pady=2)
            ttk.Entry(self.blinds_rows_frame, textvariable=row_vars["pause"], width=10).grid(row=i, column=6, padx=6, pady=2)
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

        name = simpledialog.askstring(
            "Enregistrer Blindes sous",
            "Nom de ce modèle de structure de blindes :",
            parent=self,
        )
        if not name or not name.strip():
            return
        name = name.strip()
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
    def _build_payouts_tab(self):
        top = ttk.Frame(self.payouts_tab)
        top.pack(fill="x", padx=10, pady=10)
        standard_payouts_btn = ttk.Button(
            top, text="Générer grille standard (selon le nombre d'entrées)",
            command=self._generate_standard_payouts,
        )
        standard_payouts_btn.pack(side="left", padx=3)
        Tooltip(
            standard_payouts_btn,
            "Remplace la grille actuelle par une répartition standard\n"
            "(nombre de places payées et pourcentages) adaptée au\n"
            "nombre d'entrées de ce tournoi.",
        )
        edit_pct_btn = ttk.Button(top, text="Modifier % d'une place", command=self._edit_payout_pct)
        edit_pct_btn.pack(side="left", padx=3)
        Tooltip(edit_pct_btn, "Sélectionnez d'abord une ligne dans le tableau ci-dessous,\npuis cliquez ici pour changer son pourcentage.")
        ttk.Button(
            top, text="Exporter la grille de gains (Excel/CSV)...", command=self._export_payouts,
        ).pack(side="left", padx=3)

        self.payout_summary_lbl = ttk.Label(self.payouts_tab, text="", font=("Helvetica", 11, "bold"))
        self.payout_summary_lbl.pack(padx=10, anchor="w")

        cols = ("place", "pct", "amount")
        headers = ["Place", "Pourcentage", "Montant"]
        self.payouts_tree = ttk.Treeview(self.payouts_tab, columns=cols, show="headings", height=15)
        for c, h in zip(cols, headers):
            self.payouts_tree.heading(c, text=h)
            self.payouts_tree.column(c, width=150, anchor="center")
        self.payouts_tree.pack(fill="both", expand=True, padx=10, pady=10)

    def _generate_standard_payouts(self):
        stats = self.db.get_stats()
        structure = standard_payout_structure(stats["entries"])
        self.db.set_payout_structure(structure)
        self._refresh_payouts_tab()

    def _edit_payout_pct(self):
        sel = self.payouts_tree.selection()
        if not sel:
            return
        place = int(sel[0])
        current = self.db.conn.execute(
            "SELECT percentage FROM payout_structure WHERE place=?", (place,)
        ).fetchone()
        val = simpledialog.askfloat(
            "Pourcentage", f"Nouveau pourcentage pour la place {place} :",
            initialvalue=current["percentage"] if current else 0, minvalue=0, maxvalue=100,
        )
        if val is not None:
            self.db.conn.execute(
                "INSERT INTO payout_structure(place, percentage) VALUES (?, ?) "
                "ON CONFLICT(place) DO UPDATE SET percentage=excluded.percentage",
                (place, val),
            )
            self.db.conn.commit()
            self._refresh_payouts_tab()

    def _refresh_payouts_tab(self):
        stats = self.db.get_stats()
        self.payout_summary_lbl.config(
            text=(f"Entrées : {stats['entries']}  |  Prize pool : "
                  f"{stats['prize_pool']:,.0f} €").replace(",", " ")
        )
        for row in self.payouts_tree.get_children():
            self.payouts_tree.delete(row)
        for p in self.db.get_payouts_amounts():
            self.payouts_tree.insert(
                "", "end", iid=str(p["place"]),
                values=(p["place"], f"{p['percentage']:.1f} %", f"{p['amount']:,.0f} €".replace(",", " ")),
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
        edit_duration_btn = ttk.Button(
            left, text="⏱  Modifier la durée des niveaux (tous)...",
            command=self._edit_level_duration,
        )
        edit_duration_btn.grid(row=len(fields) + 3, column=0, columnspan=2, pady=(0, 15))
        Tooltip(
            edit_duration_btn,
            "Change la durée (en minutes) de tous les niveaux de blindes\n"
            "existants d'un coup, sans toucher aux montants ni aux pauses.",
        )

        ttk.Label(
            left,
            text=("Astuce : le fichier de tournoi (.tournoi) contient toutes les données\n"
                  "et se sauvegarde automatiquement à chaque action. Vous pouvez le copier\n"
                  "pour en garder une sauvegarde."),
            foreground=MUTED,
        ).grid(row=len(fields) + 4, column=0, columnspan=2, sticky="w", pady=10)

        # -- Colonne droite : structure de blindes + signal de mouvements --
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

        ttk.Separator(right, orient="horizontal").grid(
            row=blind_next_row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 15)
        )
        signal_title = ttk.Label(
            right, text="Signal de mouvements",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        )
        signal_title.grid(row=blind_next_row + 2, column=0, columnspan=2, sticky="w", pady=(0, 8))
        Tooltip(
            signal_title,
            "Bip sonore joué sur l'écran projecteur quand un joueur vient\n"
            "d'être déplacé de table (voir Mouvements) — permet de le\n"
            "repérer sans regarder l'écran en continu.",
        )

        signal_fields = [
            ("movement_signal_frequency_hz", "Tonalité du signal (Hz, ex : 880)"),
            ("movement_signal_duration_ms", "Durée du signal (millisecondes, ex : 300)"),
        ]
        signal_row = blind_next_row + 3
        for k, (key, label) in enumerate(signal_fields):
            default = {"movement_signal_frequency_hz": 880, "movement_signal_duration_ms": 300}[key]
            ttk.Label(right, text=label + " :").grid(row=signal_row + k, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=self.db.get_setting(key, str(default)))
            ttk.Entry(right, textvariable=var, width=25).grid(row=signal_row + k, column=1, pady=4, padx=10)
            self.settings_vars[key] = var

        ttk.Button(
            right, text="🔊  Tester le signal",
            command=self._test_movement_signal,
        ).grid(row=signal_row + len(signal_fields), column=0, columnspan=2, pady=(4, 15))

        bounty_start_row = signal_row + len(signal_fields) + 1
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

    def _test_movement_signal(self):
        try:
            freq = int(self.settings_vars["movement_signal_frequency_hz"].get())
            duration = int(self.settings_vars["movement_signal_duration_ms"].get())
        except (ValueError, KeyError):
            messagebox.showerror(
                "Erreur", "Veuillez saisir des nombres entiers valides pour la tonalité et la durée."
            )
            return
        if not sound_signal.play_tone(freq, duration):
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
        self.db.set_settings(values)
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

    def _save_settings_as_template(self):
        """Applique tous les réglages du formulaire à ce tournoi (comme
        l'ancien bouton "Enregistrer les paramètres"), ET les enregistre
        sous un nom choisi par l'utilisateur, pour les réappliquer plus
        tard à d'autres tournois via "Récupérer Paramètres..." (le nom du
        tournoi lui-même n'est jamais inclus dans le modèle)."""
        name = simpledialog.askstring(
            "Enregistrer Paramètres sous",
            "Nom de ce modèle de réglages :",
            parent=self,
        )
        if not name or not name.strip():
            return
        name = name.strip()
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
        elif current == "Gains":
            self._refresh_payouts_tab()

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
