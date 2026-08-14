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

        self.blinds_lbl = tk.Label(
            self, text="", font=("Helvetica", 46, "bold"),
            bg="#0b1f14", fg="#f4c542"
        )
        self.blinds_lbl.pack()

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
                is_paused, next_break_text="", movement_alert=False):
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
