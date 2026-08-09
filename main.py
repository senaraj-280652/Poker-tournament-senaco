# -*- coding: utf-8 -*-
"""
Gestionnaire de tournoi de poker — application de bureau.
Lancement : python main.py
Nécessite uniquement Python 3.8+ (Tkinter est inclus dans la distribution
standard de Python sous Windows/macOS ; sous Linux, installez le paquet
python3-tk si besoin).
"""
import os
import time
from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog

from database import (
    Database, build_period_summary, export_period_summary_csv,
    export_period_summary_xlsx, PERIOD_TOURNAMENT_COLUMNS, PERIOD_PLAYER_COLUMNS,
)
from structures import default_blind_structure, standard_payout_structure, generate_blind_structure
from clock_window import ClockWindow
import roster
import tournament_prefs
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


class PlayerSelectionDialog(tk.Toplevel):
    """Fenêtre permettant de cocher/décocher, parmi le répertoire de joueurs
    habituels, ceux qui participent au nouveau tournoi. Permet aussi
    d'ajouter un nouveau nom au répertoire à la volée."""

    def __init__(self, master, title="Joueurs participants",
                 confirm_text="Créer le tournoi", cancel_text="Annuler",
                 exclude_names=None):
        super().__init__(master)
        self.title(title)
        self.geometry("500x560")
        self.grab_set()
        self.selected_names = []
        self.check_vars = {}
        exclude_names = exclude_names or set()
        self.roster_names = [n for n in roster.load_roster() if n not in exclude_names]
        self.sort_state = {"column": "name", "ascending": True}
        self.header_labels = {}

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
            ttk.Checkbutton(self.list_frame, text=name, variable=var).grid(
                row=idx, column=0, sticky="w", pady=1, padx=(0, 20)
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
        self.selected_names = [n for n, v in self.check_vars.items() if v.get()]
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
        tmp_dir = os.path.join(os.path.expanduser("~"), ".poker_tournament")
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, "_capture_tmp.jpg")
        try:
            self._frozen_frame.save(tmp_path, "JPEG", quality=90)
            player_photos.save_photo_from_file(self.player_name, tmp_path)
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'enregistrer la photo :\n{e}")
            return
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        if self.on_saved:
            self.on_saved()
        self._on_close()

    def _on_close(self):
        self._live = False
        if self._cap is not None:
            self._cap.release()
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

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=12)

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

        add_frame = ttk.Frame(self)
        add_frame.pack(fill="x", padx=12, pady=8)
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
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right", padx=3)

        btns2 = ttk.Frame(self)
        btns2.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(
            btns2, text="Importer les joueurs d'un tournoi existant...",
            command=self._import_from_tournament,
        ).pack(fill="x")

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
            "Joueur", "Tournois joués", "Victoires", "Meilleure place",
            "Total investi (€)", "Gains classement (€)", "Primes gagnées (€)", "Net (€)",
        ]
        self.players_tree = ttk.Treeview(bottom_pane, columns=cols_p, show="headings", height=10)
        for c, h in zip(cols_p, headers_p):
            self.players_tree.heading(c, text=h)
            self.players_tree.column(c, width=115, anchor="center")
        self.players_tree.column("name", width=170, anchor="w")
        self.players_tree.pack(fill="both", expand=True, padx=6, pady=6)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=14, pady=(0, 14))
        ttk.Button(btns, text="Exporter...", command=self._open_export_dialog).pack(side="left")
        ttk.Button(btns, text="Fermer", command=self.destroy).pack(side="right")

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

        self.format_var = tk.StringVar(value="csv")
        self.tournament_vars = {key: tk.BooleanVar(value=True) for key, _, _ in PERIOD_TOURNAMENT_COLUMNS}
        self.player_vars = {key: tk.BooleanVar(value=True) for key, _, _ in PERIOD_PLAYER_COLUMNS}

        fmt_frame = ttk.LabelFrame(self, text="Format")
        fmt_frame.pack(fill="x", padx=14, pady=(14, 8))
        ttk.Radiobutton(fmt_frame, text="CSV", variable=self.format_var, value="csv").pack(
            side="left", padx=10, pady=6
        )
        ttk.Radiobutton(fmt_frame, text="Excel (.xlsx)", variable=self.format_var, value="xlsx").pack(
            side="left", padx=10, pady=6
        )

        t_frame = ttk.LabelFrame(self, text="Colonnes — Tournois de la période")
        t_frame.pack(fill="x", padx=14, pady=8)
        self._build_column_checks(t_frame, PERIOD_TOURNAMENT_COLUMNS, self.tournament_vars)

        p_frame = ttk.LabelFrame(self, text="Colonnes — Classement des joueurs")
        p_frame.pack(fill="both", expand=True, padx=14, pady=8)
        self._build_column_checks(p_frame, PERIOD_PLAYER_COLUMNS, self.player_vars)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=14, pady=(4, 14))
        ttk.Button(btns, text="Exporter...", command=self._do_export).pack(side="left")
        ttk.Button(btns, text="Annuler", command=self.destroy).pack(side="right")

    def _build_column_checks(self, parent, columns, var_map):
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
        grid = ttk.Frame(parent)
        grid.pack(fill="both", expand=True, padx=8, pady=(2, 8))
        for idx, (key, header, _fn) in enumerate(columns):
            ttk.Checkbutton(grid, text=header, variable=var_map[key]).grid(
                row=idx // 2, column=idx % 2, sticky="w", padx=6, pady=2
            )

    def _do_export(self):
        t_keys = [k for k, v in self.tournament_vars.items() if v.get()]
        p_keys = [k for k, v in self.player_vars.items() if v.get()]
        if not t_keys and not p_keys:
            messagebox.showerror("Erreur", "Sélectionnez au moins une colonne à exporter.")
            return

        is_xlsx = self.format_var.get() == "xlsx"
        ext = ".xlsx" if is_xlsx else ".csv"
        path = filedialog.asksaveasfilename(
            title="Exporter la synthèse",
            defaultextension=ext,
            filetypes=[("Fichier Excel", "*.xlsx")] if is_xlsx else [("Fichier CSV", "*.csv")],
            initialfile=f"synthese_periode{ext}",
        )
        if not path:
            return

        try:
            if is_xlsx:
                export_period_summary_xlsx(self.summary, path, tournament_keys=t_keys, player_keys=p_keys)
            else:
                export_period_summary_csv(self.summary, path, tournament_keys=t_keys, player_keys=p_keys)
        except ImportError:
            messagebox.showerror(
                "Module manquant",
                "L'export Excel (.xlsx) nécessite le paquet 'openpyxl', qui n'est "
                "pas installé.\n\nOuvrez un terminal et tapez :\n\n"
                "    pip3 install openpyxl\n\n"
                "puis relancez l'export. (Vous pouvez aussi choisir le format CSV, "
                "qui ne nécessite rien de plus.)",
            )
            return
        messagebox.showinfo("Export", f"Synthèse exportée vers :\n{path}")
        self.destroy()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.withdraw()
        self.title("Gestionnaire de Poker Senaco")
        self.geometry("1200x750")

        self.db = None
        self.clock_window = None
        self._apply_theme()

        if not self._choose_tournament_file():
            self.destroy()
            return

        self.deiconify()
        self._build_header()
        self._build_menu()
        self._build_tabs()
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

    # ---------------------------------------------------------------
    # Ouverture / création du fichier de tournoi
    # ---------------------------------------------------------------
    def _choose_tournament_file(self):
        win = tk.Toplevel(self)
        win.title("Bienvenue")
        win.configure(bg=FELT_DARK)
        win.geometry("480x340")
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
            )
            if not path:
                return
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
            )
            win.wait_window(selector)
            result["path"] = path
            result["is_new"] = True
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
        ttk.Button(
            btn_frame, text="📂  Ouvrir un tournoi existant", command=open_tournament, width=28,
        ).pack(pady=6)

        self.wait_window(win)
        if not result["path"]:
            return False
        self.db = Database(result["path"])
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
            for name in result["selected_players"]:
                self.db.add_player(name)
        return True

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
        name = self.db.get_setting("tournament_name", "tournoi")
        safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "tournoi"
        path = filedialog.asksaveasfilename(
            title="Exporter les résultats",
            defaultextension=".xlsx",
            filetypes=[("Fichier Excel", "*.xlsx"), ("Fichier CSV", "*.csv")],
            initialfile=f"resultats_{safe_name}.xlsx",
        )
        if not path:
            return
        try:
            if path.lower().endswith(".csv"):
                self.db.export_results_csv(path)
            else:
                if not path.lower().endswith(".xlsx"):
                    path += ".xlsx"
                self.db.export_results_xlsx(path)
        except ImportError:
            messagebox.showerror(
                "Module manquant",
                "L'export Excel (.xlsx) nécessite le paquet 'openpyxl', qui n'est "
                "pas installé.\n\nOuvrez un terminal et tapez :\n\n"
                "    pip3 install openpyxl\n\n"
                "puis relancez l'export. (Vous pouvez aussi choisir le format CSV, "
                "qui ne nécessite rien de plus.)",
            )
            return
        messagebox.showinfo("Export", f"Résultats exportés vers :\n{path}")

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
        self.payouts_tab = ttk.Frame(self.notebook)
        self.settings_tab = ttk.Frame(self.notebook)

        self.notebook.add(self.players_tab, text="Joueurs")
        self.notebook.add(self.tables_tab, text="Tables")
        self.notebook.add(self.moves_tab, text="Mouvements")
        self.notebook.add(self.bounty_tab, text="Primes")
        self.notebook.add(self.clock_tab, text="Chronomètre")
        self.notebook.add(self.payouts_tab, text="Gains")
        self.notebook.add(self.settings_tab, text="Paramètres")

        self._build_players_tab()
        self._build_tables_tab()
        self._build_moves_tab()
        self._build_bounty_tab()
        self._build_clock_tab()
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
        ttk.Checkbutton(
            top, text="Temp (ne pas ajouter au répertoire)", variable=self.temp_player_var,
        ).pack(side="left", padx=(10, 5))

        self.stats_lbl = ttk.Label(top, text="", font=("Helvetica", 10, "bold"))
        self.stats_lbl.pack(side="right")

        check_bar = ttk.Frame(self.players_tab)
        check_bar.pack(fill="x", padx=10)
        ttk.Button(check_bar, text="Tout cocher", command=self._check_all_players).pack(side="left", padx=3)
        ttk.Button(check_bar, text="Tout décocher", command=self._uncheck_all_players).pack(side="left", padx=3)
        self.checked_count_lbl = ttk.Label(check_bar, text="", foreground=GOLD)
        self.checked_count_lbl.pack(side="left", padx=10)

        actions = ttk.Frame(self.players_tab)
        actions.pack(fill="x", padx=10, pady=(6, 10))
        ttk.Button(actions, text="Renommer...", command=self._rename_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Rebuy (+)", command=self._rebuy_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Add-on (+)", command=self._addon_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Modifier chips...", command=self._edit_chips_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Modifier achats...", command=self._edit_purchases_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Éliminer", command=self._eliminate_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Désactiver (forfait)", command=self._withdraw_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Réinscrire", command=self._reinstate_selected).pack(side="left", padx=3)
        ttk.Button(actions, text="Supprimer", command=self._delete_selected).pack(side="left", padx=3)

        columns = ("sel", "id", "name", "table", "seat", "chips", "buyin", "rebuy", "addon", "bounty", "status", "place")
        headers = ["", "ID", "Nom", "Table", "Siège", "Chips", "Buy-in", "Rebuys", "Add-ons", "Prime", "Statut", "Place"]
        self.players_tree = ttk.Treeview(
            self.players_tab, columns=columns, show="tree headings", height=20,
            style="Players.Treeview",
        )
        self.players_tree.heading("#0", text="Photo")
        self.players_tree.column("#0", width=PLAYER_THUMB_SIZE + 16, stretch=False, anchor="center")
        for c, h in zip(columns, headers):
            self.players_tree.heading(c, text=h)
            self.players_tree.column(c, width=90, anchor="center")
        self.players_tree.heading("name", command=lambda: self._sort_players_by("name"))
        self.players_tree.heading("status", command=lambda: self._sort_players_by("status"))
        self.players_tree.heading("table", command=lambda: self._sort_players_by("table"))
        self.players_tree.column("sel", width=56, anchor="center", stretch=False)
        self.players_tree.column("name", width=180, anchor="w")
        self.players_tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.players_tree.bind("<Button-1>", self._on_players_tree_click)
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
        base_headers = {"name": "Nom", "status": "Statut", "table": "Table"}
        for col, label in base_headers.items():
            if self.players_sort["column"] == col:
                arrow = " ▲" if self.players_sort["ascending"] else " ▼"
                self.players_tree.heading(col, text=label + arrow)
            else:
                self.players_tree.heading(col, text=label)

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
            try:
                popup.attributes("-topmost", True)
            except tk.TclError:
                pass
            listbox = tk.Listbox(
                popup, bg=CREAM, fg=TEXT_DARK, selectbackground=GOLD,
                selectforeground=TEXT_DARK, font=("Helvetica", 11),
                exportselection=False, activestyle="none",
                highlightthickness=1, highlightbackground=GOLD_DARK, borderwidth=0,
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

    def _add_player(self):
        name = self.new_player_var.get().strip()
        if not name:
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

    def _add_from_roster(self):
        existing_names = {p["name"] for p in self.db.list_players()}
        dialog = PlayerSelectionDialog(
            self, title="Ajouter des joueurs depuis le répertoire",
            confirm_text="Ajouter les joueurs sélectionnés", cancel_text="Annuler",
            exclude_names=existing_names,
        )
        self.wait_window(dialog)
        for name in dialog.selected_names:
            self.db.add_player(name)
        if dialog.selected_names:
            self._refresh_all()

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
        bounty_active = self.db.get_setting_int("bounty_amount", 0) > 0
        if len(ids) == 1:
            p = self.db.get_player(ids[0])
            question = f"Éliminer {p['name']} du tournoi ?"
        else:
            question = f"Éliminer ces {len(ids)} joueurs du tournoi ?"
            if bounty_active:
                question += (
                    "\n\n(Élimination groupée : les primes ne seront pas "
                    "attribuées ici — éliminez ces joueurs un par un si "
                    "vous voulez enregistrer qui empoche chaque prime.)"
                )
        if not messagebox.askyesno("Confirmer", question):
            return

        eliminator_id = None
        if bounty_active and len(ids) == 1 and self.db.get_player(ids[0])["bounty"] > 0:
            eliminator_id = self._ask_eliminator(exclude_id=ids[0])

        moved_count = 0
        for pid in ids:
            moved_count += len(self.db.eliminate_player(pid, eliminated_by_id=eliminator_id))
        self._clear_checked()
        self._refresh_all()
        if moved_count:
            self._play_movement_signal()

    def _ask_eliminator(self, exclude_id):
        """Petite fenêtre pour choisir qui a éliminé le joueur (attribution
        de la prime bounty). Renvoie l'id du joueur choisi, ou None si
        ignoré/annulé."""
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
        tk.Label(
            win, bg=FELT_DARK, fg=GOLD, font=("Helvetica", 12, "bold"),
            text=f"💰  {eliminated['name']} portait une prime de "
                 f"{eliminated['bounty']:,} €".replace(",", " "),
        ).pack(padx=16, pady=(16, 4))
        tk.Label(
            win, bg=FELT_DARK, fg=CREAM,
            text="Qui l'a éliminé(e) ?",
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
        for p in players:
            p["status_label"] = status_labels.get(p["status"], p["status"])
            p["table_name"] = tables.get(p["table_id"], "-") if p["table_id"] else "-"

        sort_col = self.players_sort["column"]
        if sort_col == "name":
            players.sort(key=lambda p: p["name"].lower())
        elif sort_col == "status":
            players.sort(key=lambda p: p["status_label"].lower())
        elif sort_col == "table":
            players.sort(key=lambda p: (p["table_name"].lower(), p["seat"] or 0))
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
            bounty_txt = f"{p['bounty']:,} €".replace(",", " ") if p["bounty"] else "-"
            self.players_tree.insert(
                "", "end", iid=str(p["id"]),
                image=photo if photo is not None else "",
                values=(
                    mark, p["id"], p["name"], table_name, p["seat"] or "-",
                    f"{p['chips']:,}".replace(",", " "),
                    p["buyin_count"], p["rebuy_count"], p["addon_count"],
                    bounty_txt, status, p["place"] or "-",
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
        ttk.Button(top, text="Rééquilibrer les tables", command=self._rebalance).pack(side="left", padx=3)
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
        self.db.rebalance_tables()
        self._refresh_all()

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
        top = ttk.Frame(self.bounty_tab)
        top.pack(fill="x", padx=10, pady=10)
        self.bounty_info_lbl = ttk.Label(top, text="", font=("Helvetica", 10, "bold"))
        self.bounty_info_lbl.pack(side="left")

        panes = ttk.Frame(self.bounty_tab)
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        left = ttk.LabelFrame(panes, text="Primes en jeu (joueurs actifs)")
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        cols1 = ("name", "bounty")
        self.bounty_active_tree = ttk.Treeview(left, columns=cols1, show="headings", height=14)
        self.bounty_active_tree.heading("name", text="Joueur")
        self.bounty_active_tree.heading("bounty", text="Prime actuelle")
        self.bounty_active_tree.column("name", width=160, anchor="w")
        self.bounty_active_tree.column("bounty", width=120, anchor="center")
        self.bounty_active_tree.pack(fill="both", expand=True, padx=6, pady=6)

        right = ttk.LabelFrame(panes, text="Primes empochées (cumul par joueur)")
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        cols2 = ("name", "won")
        self.bounty_totals_tree = ttk.Treeview(right, columns=cols2, show="headings", height=14)
        self.bounty_totals_tree.heading("name", text="Joueur")
        self.bounty_totals_tree.heading("won", text="Total empoché")
        self.bounty_totals_tree.column("name", width=160, anchor="w")
        self.bounty_totals_tree.column("won", width=120, anchor="center")
        self.bounty_totals_tree.pack(fill="both", expand=True, padx=6, pady=6)

        history = ttk.LabelFrame(self.bounty_tab, text="Historique des primes gagnées")
        history.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        cols3 = ("time", "eliminated", "eliminator", "amount", "grow")
        headers3 = ["Heure", "Joueur éliminé", "Éliminé par", "Cash gagné", "Ajouté à sa prime"]
        self.bounty_history_tree = ttk.Treeview(history, columns=cols3, show="headings", height=8)
        for c, h in zip(cols3, headers3):
            self.bounty_history_tree.heading(c, text=h)
            self.bounty_history_tree.column(c, width=130, anchor="center")
        self.bounty_history_tree.pack(fill="both", expand=True, padx=6, pady=6)

    def _refresh_bounty_tab(self):
        bounty_amount = self.db.get_setting_int("bounty_amount", 0)
        pko_mode = self.db.get_setting_int("pko_mode", 0) == 1
        if bounty_amount <= 0:
            self.bounty_info_lbl.config(
                text="Bounty désactivé (montant à 0 dans Paramètres)."
            )
        else:
            mode_txt = "PKO (prime progressive)" if pko_mode else "Bounty classique"
            self.bounty_info_lbl.config(
                text=(f"{mode_txt}  |  Prime de départ : "
                      f"{bounty_amount:,} €".replace(",", " "))
            )

        for row in self.bounty_active_tree.get_children():
            self.bounty_active_tree.delete(row)
        for idx, p in enumerate(self.db.get_active_bounties()):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.bounty_active_tree.insert(
                "", "end",
                values=(p["name"], f"{p['bounty']:,} €".replace(",", " ")),
                tags=(tag,),
            )
        self.bounty_active_tree.tag_configure("evenrow", background=CREAM)
        self.bounty_active_tree.tag_configure("oddrow", background=CREAM_ALT)

        for row in self.bounty_totals_tree.get_children():
            self.bounty_totals_tree.delete(row)
        for idx, p in enumerate(self.db.get_bounty_totals()):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.bounty_totals_tree.insert(
                "", "end",
                values=(p["name"], f"{p['bounty_won']:,} €".replace(",", " ")),
                tags=(tag,),
            )
        self.bounty_totals_tree.tag_configure("evenrow", background=CREAM)
        self.bounty_totals_tree.tag_configure("oddrow", background=CREAM_ALT)

        for row in self.bounty_history_tree.get_children():
            self.bounty_history_tree.delete(row)
        for idx, e in enumerate(self.db.get_bounty_events()):
            tag = "evenrow" if idx % 2 == 0 else "oddrow"
            self.bounty_history_tree.insert(
                "", "end",
                values=(
                    e["event_time"], e["eliminated_name"], e["eliminator_name"] or "—",
                    f"{e['amount_won']:,} €".replace(",", " "),
                    f"{e['added_to_eliminator_bounty']:,} €".replace(",", " ")
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
        self.blinds_tree.pack(fill="both", expand=True)
        self.blinds_tree.bind("<Double-Button-1>", self._on_blinds_tree_double_click)

        struct_btns = ttk.Frame(struct_frame)
        struct_btns.pack(side="left", fill="y", padx=5)
        ttk.Button(struct_btns, text="Aller à ce niveau", command=self._go_to_selected_level).pack(pady=3, fill="x")
        ttk.Button(struct_btns, text="Structure standard", command=self._reset_blind_structure).pack(pady=3, fill="x")
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

        if self.clock_window is not None and self.clock_window.winfo_exists():
            stats = self.db.get_stats()
            name = self.db.get_setting("tournament_name", "Tournoi")
            self.clock_window.refresh(remaining, level, next_level, stats, name, paused)

    def _open_clock_window(self):
        if self.clock_window is not None and self.clock_window.winfo_exists():
            self.clock_window.lift()
            return
        self.clock_window = ClockWindow(self, self)

    # ---------------------------------------------------------------
    # Onglet Gains
    # ---------------------------------------------------------------
    def _build_payouts_tab(self):
        top = ttk.Frame(self.payouts_tab)
        top.pack(fill="x", padx=10, pady=10)
        ttk.Button(top, text="Générer grille standard (selon le nombre d'entrées)",
                   command=self._generate_standard_payouts).pack(side="left", padx=3)
        ttk.Button(top, text="Modifier % d'une place", command=self._edit_payout_pct).pack(side="left", padx=3)
        ttk.Button(top, text="Exporter les résultats (Excel/CSV)...", command=self._export_results).pack(side="left", padx=3)

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
        for i, (key, label) in enumerate(fields):
            ttk.Label(left, text=label + " :").grid(row=i, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=self.db.get_setting(key, ""))
            ttk.Entry(left, textvariable=var, width=25).grid(row=i, column=1, pady=4, padx=10)
            self.settings_vars[key] = var

        ttk.Button(left, text="Enregistrer les paramètres", command=self._save_settings).grid(
            row=len(fields), column=0, columnspan=2, pady=15
        )

        ttk.Separator(left, orient="horizontal").grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky="ew", pady=(5, 15)
        )
        ttk.Button(
            left, text="⏱  Modifier la durée des niveaux (tous)...",
            command=self._edit_level_duration,
        ).grid(row=len(fields) + 2, column=0, columnspan=2, pady=(0, 15))

        ttk.Label(
            left,
            text=("Astuce : le fichier de tournoi (.tournoi) contient toutes les données\n"
                  "et se sauvegarde automatiquement à chaque action. Vous pouvez le copier\n"
                  "pour en garder une sauvegarde."),
            foreground=MUTED,
        ).grid(row=len(fields) + 3, column=0, columnspan=2, sticky="w", pady=10)

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
            ("break_duration_minutes", "Durée de la Pause (minutes)"),
        ]
        for j, (key, label) in enumerate(blind_fields, start=1):
            default = {
                "start_small_blind": 25, "start_big_blind": 50,
                "ante_start_level": 4, "start_ante": 25,
                "break_duration_minutes": 15,
            }[key]
            ttk.Label(right, text=label + " :").grid(row=j, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=self.db.get_setting(key, str(default)))
            ttk.Entry(right, textvariable=var, width=25).grid(row=j, column=1, pady=4, padx=10)
            self.settings_vars[key] = var

        blind_next_row = len(blind_fields) + 1
        ttk.Button(
            right, text="🎲  Régénérer la structure de blindes avec ces valeurs",
            command=self._generate_custom_blind_structure,
        ).grid(row=blind_next_row, column=0, columnspan=2, pady=(8, 15))

        ttk.Separator(right, orient="horizontal").grid(
            row=blind_next_row + 1, column=0, columnspan=2, sticky="ew", pady=(0, 15)
        )
        ttk.Label(
            right, text="Signal de mouvements",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        ).grid(row=blind_next_row + 2, column=0, columnspan=2, sticky="w", pady=(0, 8))

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
        ttk.Label(
            right, text="Primes (bounty)",
            font=("Helvetica", 11, "bold"), foreground=GOLD,
        ).grid(row=bounty_start_row + 1, column=0, columnspan=2, sticky="w", pady=(0, 8))

        ttk.Label(right, text="Montant de la prime par joueur (€) :").grid(
            row=bounty_start_row + 2, column=0, sticky="w", pady=4
        )
        bounty_var = tk.StringVar(value=self.db.get_setting("bounty_amount", "0"))
        ttk.Entry(right, textvariable=bounty_var, width=25).grid(
            row=bounty_start_row + 2, column=1, pady=4, padx=10
        )
        self.settings_vars["bounty_amount"] = bounty_var

        pko_var = tk.BooleanVar(value=self.db.get_setting_int("pko_mode", 0) == 1)
        ttk.Checkbutton(
            right, text="Mode PKO (prime progressive)", variable=pko_var,
        ).grid(row=bounty_start_row + 3, column=0, columnspan=2, sticky="w", pady=4)
        self.settings_vars["pko_mode"] = pko_var

        ttk.Label(right, text="Part en cash immédiat en PKO (%) :").grid(
            row=bounty_start_row + 4, column=0, sticky="w", pady=4
        )
        pko_pct_var = tk.StringVar(value=self.db.get_setting("pko_cash_percent", "50"))
        ttk.Entry(right, textvariable=pko_pct_var, width=25).grid(
            row=bounty_start_row + 4, column=1, pady=4, padx=10
        )
        self.settings_vars["pko_cash_percent"] = pko_pct_var

        ttk.Label(
            right,
            text=("Le bounty s'applique aux nouvelles inscriptions/rebuys après\n"
                  "avoir enregistré. En mode classique, l'éliminateur empoche toute\n"
                  "la prime en cash ; en PKO, une partie s'ajoute à sa propre prime."),
            foreground=MUTED,
        ).grid(row=bounty_start_row + 5, column=0, columnspan=2, sticky="w", pady=(4, 10))

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
            break_duration = int(self.settings_vars["break_duration_minutes"].get())
        except (ValueError, KeyError):
            messagebox.showerror(
                "Erreur",
                "Veuillez saisir des nombres entiers valides pour le small blind, "
                "le big blind, le niveau de début des antes et la valeur de l'ante.",
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
        if break_duration <= 0:
            messagebox.showerror("Erreur", "La durée de la pause doit être supérieure à 0.")
            return
        if not messagebox.askyesno(
            "Confirmer",
            "Régénérer toute la structure de blindes avec ces valeurs ?\n"
            "(Les durées de niveau actuelles sont conservées ; cette action "
            "fonctionne aussi en plein milieu d'un tournoi.)",
        ):
            return

        existing = self.db.get_blind_structure()
        duration = existing[0]["duration_minutes"] if existing else 15
        new_structure = generate_blind_structure(
            start_small_blind=sb, start_big_blind=bb, ante_start_level=ante_lvl,
            start_ante=start_ante, duration_minutes=duration,
            break_duration_minutes=break_duration, break_every=4,
        )
        self.db.set_blind_structure(new_structure)
        blind_settings = {
            "start_small_blind": sb, "start_big_blind": bb,
            "ante_start_level": ante_lvl, "start_ante": start_ante,
            "break_duration_minutes": break_duration,
        }
        self.db.set_settings(blind_settings)
        tournament_prefs.save_last_settings(blind_settings)
        current_order = self.db.get_setting_int("current_level_order", 1)
        if current_order > len(new_structure):
            self.db.set_settings({"current_level_order": len(new_structure)})
        self._refresh_all()
        messagebox.showinfo("Structure de blindes", "La structure de blindes a été régénérée.")

    def _save_settings(self):
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
        self.db.rebalance_tables()
        messagebox.showinfo("Paramètres", "Paramètres enregistrés.")
        self._update_window_title()
        self._refresh_all()

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
    app = App()
    app.mainloop()
