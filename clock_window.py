# -*- coding: utf-8 -*-
"""Fenêtre d'affichage du chronomètre, pensée pour être projetée sur un
second écran pendant le tournoi."""
import tkinter as tk
import time


class ClockWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("Chronomètre du tournoi")
        self.configure(bg="#0b1f14")
        self.geometry("1000x600")

        self.name_lbl = tk.Label(
            self, text="", font=("Helvetica", 22, "bold"),
            bg="#0b1f14", fg="#8fd694"
        )
        self.name_lbl.pack(pady=(20, 0))

        self.level_lbl = tk.Label(
            self, text="", font=("Helvetica", 28),
            bg="#0b1f14", fg="#ffffff"
        )
        self.level_lbl.pack(pady=(10, 0))

        self.timer_lbl = tk.Label(
            self, text="00:00", font=("Helvetica", 130, "bold"),
            bg="#0b1f14", fg="#ffffff"
        )
        self.timer_lbl.pack(pady=10)

        # Ligne des blindes + tableau des jetons, à la même hauteur : trois
        # colonnes de poids égal aux deux extrémités (0 et 2) pour que
        # blinds_lbl reste centré sur toute la largeur de l'écran, la
        # colonne 2 se contentant d'accueillir le tableau des jetons sur
        # sa droite (sticky="e") sans déséquilibrer le centrage.
        blinds_row = tk.Frame(self, bg="#0b1f14")
        blinds_row.pack(fill="x")
        blinds_row.grid_columnconfigure(0, weight=1)
        blinds_row.grid_columnconfigure(1, weight=0)
        blinds_row.grid_columnconfigure(2, weight=1)

        self.blinds_lbl = tk.Label(
            blinds_row, text="", font=("Helvetica", 46, "bold"),
            bg="#0b1f14", fg="#f4c542"
        )
        self.blinds_lbl.grid(row=0, column=1)

        # Tableau des jetons (couleur/valeur/nombre par joueur — voir
        # l'onglet Blindes), à la même hauteur que les blindes. Reconstruit
        # à chaque refresh() (voir _update_chips_display) uniquement s'il a
        # changé, car les couleurs peuvent être modifiées en cours de
        # tournoi.
        self.chips_table_frame = tk.Frame(blinds_row, bg="#0b1f14")
        self.chips_table_frame.grid(row=0, column=2, sticky="e", padx=40)

        self.next_lbl = tk.Label(
            self, text="", font=("Helvetica", 18),
            bg="#0b1f14", fg="#aaaaaa"
        )
        self.next_lbl.pack(pady=(10, 0))

        self.next_break_lbl = tk.Label(
            self, text="", font=("Helvetica", 16),
            bg="#0b1f14", fg="#aaaaaa"
        )
        self.next_break_lbl.pack(pady=(2, 0))

        bottom = tk.Frame(self, bg="#0b1f14")
        bottom.pack(side="bottom", fill="x", pady=20)

        self.players_lbl = tk.Label(
            bottom, text="", font=("Helvetica", 20),
            bg="#0b1f14", fg="#ffffff"
        )
        self.players_lbl.pack(side="left", padx=40)

        self.avg_lbl = tk.Label(
            bottom, text="", font=("Helvetica", 20),
            bg="#0b1f14", fg="#ffffff"
        )
        self.avg_lbl.pack(side="left", padx=40)

        # Bandeau "Changement de tables en cours" : overlay clignotant
        # par-dessus le reste de l'écran, affiché/masqué depuis refresh()
        # selon movement_alert (voir App._trigger_movement_alert).
        self.movement_alert_lbl = tk.Label(
            self, text="⚠  Changement de tables en cours  ⚠",
            font=("Helvetica", 40, "bold"), bg="#8a1f1f", fg="white",
            relief="solid", borderwidth=5, padx=40, pady=26,
        )

        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self._fullscreen = False

    def _toggle_fullscreen(self, event=None):
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)

    def refresh(self, remaining_seconds, level_row, next_row, stats, tournament_name,
                is_paused, next_break_text="", movement_alert=False, chip_denominations=None):
        self.name_lbl.config(text=tournament_name)

        mins, secs = divmod(max(0, int(remaining_seconds)), 60)
        pause_suffix = "  ⏸" if is_paused else ""
        self.timer_lbl.config(text=f"{mins:02d}:{secs:02d}{pause_suffix}")

        if level_row is not None and level_row["is_break"]:
            self.level_lbl.config(text=level_row["break_label"] or "Pause")
            self.blinds_lbl.config(text="")
        elif level_row is not None:
            self.level_lbl.config(text=f"Niveau {level_row['level_order']}")
            ante_txt = f"   Ante {level_row['ante']}" if level_row["ante"] else ""
            self.blinds_lbl.config(
                text=f"{level_row['small_blind']} / {level_row['big_blind']}{ante_txt}"
            )
        else:
            self.level_lbl.config(text="")
            self.blinds_lbl.config(text="")

        if next_row is not None:
            if next_row["is_break"]:
                self.next_lbl.config(text=f"Prochain : {next_row['break_label'] or 'Pause'}")
            else:
                self.next_lbl.config(
                    text=f"Prochain niveau : {next_row['small_blind']} / {next_row['big_blind']}"
                )
        else:
            self.next_lbl.config(text="Dernier niveau de la structure")

        self.players_lbl.config(
            text=f"Joueurs restants : {stats['active_count']} / {stats['total_players_ever']}"
        )
        self.avg_lbl.config(text=f"Tapis moyen : {int(stats['avg_stack']):,}".replace(",", " "))
        self.next_break_lbl.config(text=next_break_text)

        if movement_alert and int(time.time()) % 2 == 0:
            self.movement_alert_lbl.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.movement_alert_lbl.place_forget()

        self._update_chips_display(chip_denominations or [])

    def _update_chips_display(self, denominations):
        """Reconstruit le tableau des jetons (à hauteur des blindes)
        seulement s'il a changé depuis le dernier refresh() — appelé une
        fois par seconde, inutile de tout redétruire/reconstruire à
        chaque tick."""
        signature = tuple(
            (d.get("name", ""), d.get("color", ""), d.get("value", 0), d.get("count", 0))
            for d in denominations
        )
        if signature == getattr(self, "_chips_signature", None):
            return
        self._chips_signature = signature

        for w in self.chips_table_frame.winfo_children():
            w.destroy()
        if not denominations:
            return

        tk.Label(
            self.chips_table_frame, text="Jetons", font=("Helvetica", 15, "bold"),
            bg="#0b1f14", fg="#f4c542",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))

        for i, d in enumerate(denominations, start=1):
            swatch = tk.Canvas(
                self.chips_table_frame, width=18, height=18, highlightthickness=0, bg="#0b1f14",
            )
            swatch.create_oval(1, 1, 17, 17, fill=d.get("color") or "#000000", outline="#f4c542")
            swatch.grid(row=i, column=0, padx=(0, 8), pady=2, sticky="w")

            tk.Label(
                self.chips_table_frame, text=d.get("name") or "?", font=("Helvetica", 16),
                bg="#0b1f14", fg="#ffffff", anchor="w",
            ).grid(row=i, column=1, padx=(0, 16), pady=2, sticky="w")

            value_text = f"{d.get('value', 0):,}".replace(",", " ")
            tk.Label(
                self.chips_table_frame, text=value_text, font=("Helvetica", 16),
                bg="#0b1f14", fg="#ffffff", anchor="e",
            ).grid(row=i, column=2, padx=(0, 8), pady=2, sticky="e")

            tk.Label(
                self.chips_table_frame, text=f"× {d.get('count', 0)}", font=("Helvetica", 16),
                bg="#0b1f14", fg="#aaaaaa", anchor="w",
            ).grid(row=i, column=3, pady=2, sticky="w")
