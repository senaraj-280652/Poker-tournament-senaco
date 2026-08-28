# -*- coding: utf-8 -*-
"""Aide interactive intégrée à l'application : navigateur du manuel
utilisateur (sommaire cliquable + recherche), ouvert depuis le menu Aide
ou la touche F1 — sans avoir besoin d'ouvrir le PDF à côté.

Le contenu est extrait une bonne fois pour toutes du manuel
(MANUEL_UTILISATEUR_TOURNOI_CPC.docx) par extract_help_content.py, dans
help_content.json, embarqué avec l'application (voir *.spec, section
`datas`) — à régénérer manuellement après toute mise à jour du manuel :

    python3 extract_help_content.py
"""
import json
import os
import sys
import tkinter as tk
from tkinter import ttk

FELT_DARK = "#0b241a"
GOLD = "#e8c05c"


def _content_path():
    # Fonctionne à la fois lancé depuis les sources (help_content.json à
    # côté de ce fichier) et depuis un .exe/.app PyInstaller (fichiers
    # "datas" extraits dans un dossier temporaire, sys._MEIPASS).
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "help_content.json")


def _load_entries():
    try:
        with open(_content_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


HELP_ENTRIES = _load_entries()

# Onglet de la fenêtre principale -> titre du chapitre à afficher en
# premier quand on appuie sur F1 depuis cet onglet (aide contextuelle).
# Les titres doivent correspondre exactement à ceux du manuel.
TAB_TO_CHAPTER = {
    "Joueurs": "5. Onglet Joueurs",
    "Tables": "6. Onglet Tables",
    "Mouvements": "7. Onglet Mouvements",
    "Primes": "8. Onglet Primes",
    "Chronomètre": "10. Onglet Chronomètre",
    "Blindes": "9. Onglet Blindes",
    "Classement": "11. Onglet Classement",
    "Répertoire": "16. Menu Répertoire — joueurs habituels et photos",
    "Statistiques": "15. Statistiques — Synthèse par période",
    "Paramètres": "12. Onglet Paramètres",
}


class HelpBrowser(tk.Toplevel):
    """Fenêtre d'aide : un seul exemplaire à la fois (voir open_at) — un
    second F1 pendant que l'aide est déjà ouverte la ramène juste au
    premier plan sur la nouvelle section, plutôt que d'empiler les
    fenêtres."""

    _instance = None

    @classmethod
    def open_at(cls, master, chapter_title=None, section_title=None):
        if cls._instance is not None and cls._instance.winfo_exists():
            win = cls._instance
            win.deiconify()
            win.lift()
            win.focus_force()
        else:
            win = cls(master)
            cls._instance = win
        if chapter_title or section_title:
            win.show(chapter_title, section_title)
        return win

    def __init__(self, master):
        super().__init__(master)
        self.title("Aide — Gestionnaire de Tournoi de Poker")
        self.geometry("1000x640")
        self.minsize(700, 400)
        self.configure(bg=FELT_DARK)

        self._section_marks = {}  # index dans HELP_ENTRIES -> position ("l.c") dans le Text

        self._build_ui()
        self._populate_tree()
        if HELP_ENTRIES:
            self.show(HELP_ENTRIES[0]["title"])

        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_close(self):
        HelpBrowser._instance = None
        self.destroy()

    def _build_ui(self):
        top = ttk.Frame(self)
        top.pack(fill="x", padx=10, pady=8)
        ttk.Label(top, text="Rechercher :").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(top, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=6)
        search_entry.bind("<Return>", lambda e: self._do_search())
        ttk.Button(top, text="Chercher", command=self._do_search).pack(side="left")
        ttk.Button(top, text="Effacer", command=self._clear_search).pack(side="left", padx=(4, 0))

        if not HELP_ENTRIES:
            ttk.Label(
                self, foreground="#e08080",
                text="Contenu d'aide introuvable (help_content.json manquant ou illisible).",
            ).pack(padx=10, pady=10)

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        tree_frame = ttk.Frame(body, width=280)
        tree_frame.pack(side="left", fill="y")
        tree_frame.pack_propagate(False)
        self.tree = ttk.Treeview(tree_frame, show="tree")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        text_frame = ttk.Frame(body)
        text_frame.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.text = tk.Text(
            text_frame, wrap="word", relief="flat", padx=16, pady=14,
            font=("Helvetica", 12), state="disabled", cursor="arrow",
            bg="#f7f1e3",
        )
        text_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=text_scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        text_scroll.pack(side="right", fill="y")

        self.text.tag_configure("h1", font=("Helvetica", 20, "bold"), spacing3=10, foreground="#123a29")
        self.text.tag_configure("h2", font=("Helvetica", 15, "bold"), spacing1=16, spacing3=6, foreground="#1c5940")
        self.text.tag_configure("h3", font=("Helvetica", 13, "bold"), spacing1=12, spacing3=4, foreground="#1c5940")
        self.text.tag_configure("body", font=("Helvetica", 12), spacing3=10)
        self.text.tag_configure("highlight", background=GOLD)

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        last_id_by_level = {}
        for idx, e in enumerate(HELP_ENTRIES):
            level = e["level"]
            parent = ""
            if level == 2:
                parent = last_id_by_level.get(1, "")
            elif level == 3:
                parent = last_id_by_level.get(2, last_id_by_level.get(1, ""))
            node = self.tree.insert(parent, "end", iid=str(idx), text=e["title"])
            last_id_by_level[level] = node
            if level == 1:
                last_id_by_level.pop(2, None)
                last_id_by_level.pop(3, None)

    def _on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            idx = int(sel[0])
        except ValueError:
            return  # ligne "(aucun résultat)" de la recherche
        self._render_chapter_containing(idx, scroll_to=idx)

    def show(self, chapter_title=None, section_title=None):
        """Affiche le chapitre correspondant à chapter_title (ou celui
        contenant section_title), et fait défiler jusqu'à section_title
        si elle est précisée et plus spécifique que le chapitre entier."""
        target_idx = None
        if section_title:
            for i, e in enumerate(HELP_ENTRIES):
                if e["title"] == section_title:
                    target_idx = i
                    break
        if target_idx is None and chapter_title:
            for i, e in enumerate(HELP_ENTRIES):
                if e["title"] == chapter_title:
                    target_idx = i
                    break
        if target_idx is None:
            return
        if self.tree.exists(str(target_idx)):
            self.tree.selection_set(str(target_idx))
            self.tree.see(str(target_idx))
        self._render_chapter_containing(target_idx, scroll_to=target_idx)

    def _render_chapter_containing(self, idx, scroll_to=None):
        start = idx
        while start > 0 and HELP_ENTRIES[start]["level"] != 1:
            start -= 1
        end = start + 1
        while end < len(HELP_ENTRIES) and HELP_ENTRIES[end]["level"] != 1:
            end += 1

        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self._section_marks = {}
        for i in range(start, end):
            e = HELP_ENTRIES[i]
            tag = {1: "h1", 2: "h2", 3: "h3"}[e["level"]]
            self._section_marks[i] = self.text.index("end-1c")
            self.text.insert("end", e["title"] + "\n", tag)
            if e["body"]:
                self.text.insert("end", e["body"] + "\n", "body")
        self.text.configure(state="disabled")

        if scroll_to is not None and scroll_to in self._section_marks:
            self.text.see(self._section_marks[scroll_to])
        else:
            self.text.see("1.0")

    def _do_search(self):
        term = self.search_var.get().strip().lower()
        if not term:
            self._clear_search()
            return
        matches = [
            i for i, e in enumerate(HELP_ENTRIES)
            if term in e["title"].lower() or term in e["body"].lower()
        ]
        self.tree.delete(*self.tree.get_children())
        if not matches:
            self.tree.insert("", "end", iid="none", text="(aucun résultat)")
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.configure(state="disabled")
            return
        for i in matches:
            e = HELP_ENTRIES[i]
            label = e["title"] if e["level"] == 1 else f"— {e['title']}"
            self.tree.insert("", "end", iid=str(i), text=label)
        first = matches[0]
        self.tree.selection_set(str(first))
        self._render_chapter_containing(first, scroll_to=first)
        self._highlight_term(term)

    def _highlight_term(self, term):
        self.text.tag_remove("highlight", "1.0", "end")
        if not term:
            return
        start = "1.0"
        while True:
            pos = self.text.search(term, start, stopindex="end", nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(term)}c"
            self.text.tag_add("highlight", pos, end)
            start = end

    def _clear_search(self):
        self.search_var.set("")
        self.text.tag_remove("highlight", "1.0", "end")
        self._populate_tree()
