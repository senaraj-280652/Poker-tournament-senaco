# -*- coding: utf-8 -*-
"""Fenêtre d'affichage du chronomètre, pensée pour être projetée sur un
second écran pendant le tournoi."""
import tkinter as tk
import time

import chip_images
import player_photos

# Pillow est optionnel (voir main.py) : sans lui, une dénomination avec
# une image de jeton retombe simplement sur sa pastille de couleur ici
# aussi, plutôt que de planter.
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


def _load_square_thumbnail(path, size):
    """Vignette carrée (recadrée) de `size` pixels pour une image de
    jeton ou de joueur (voir _update_chips_display / photos du bandeau
    d'élimination), ou None si le fichier est absent, corrompu ou si
    Pillow est indisponible — équivalent local de main.load_thumbnail
    (clock_window.py ne dépend pas de main.py, pour éviter un import
    circulaire : main.py importe déjà clock_window.py)."""
    if not path or not PIL_AVAILABLE:
        return None
    try:
        img = Image.open(path).convert("RGB")
        w, h = img.size
        side = min(w, h)
        left, top = (w - side) // 2, (h - side) // 2
        img = img.crop((left, top, left + side, top + side)).resize(
            (size, size), Image.LANCZOS
        )
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


class ClockWindow(tk.Toplevel):
    # Au-delà de cette hauteur (px), le tableau des joueurs concernés par
    # un mouvement défile au lieu de pousser le reste de l'écran vers le
    # bas ou de déborder de la fenêtre.
    MOVES_TABLE_MAX_HEIGHT = 260
    MOVES_AUTOSCROLL_STEP_PX = 2
    MOVES_AUTOSCROLL_INTERVAL_MS = 45
    MOVES_AUTOSCROLL_PAUSE_MS = 2000  # pause en haut et en bas avant de reboucler

    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.title("Chronomètre du tournoi")
        self.configure(bg="#0b1f14")
        self.geometry("1000x650")

        # Rappel des raccourcis clavier (Ctrl+Maj+J/C/T), en haut à gauche
        # — contenu fixe (ne dépend pas du tournoi), affiché en permanence,
        # pas seulement pendant une alerte de mouvement (contrairement au
        # bandeau movement_alert_frame ci-dessous), pour que le responsable
        # les ait toujours sous les yeux sur cet écran.
        shortcuts_frame = tk.Frame(self, bg="#0b1f14")
        shortcuts_frame.place(x=20, y=16, anchor="nw")
        tk.Label(
            shortcuts_frame, text="Raccourcis clavier", font=("Helvetica", 14, "bold"),
            bg="#0b1f14", fg="#f4c542", anchor="w", justify="left",
        ).pack(anchor="w")
        for line in (
            "Ctrl+Maj+J  Joueurs",
            "Ctrl+Maj+C  Chronomètre",
            "Ctrl+Maj+T  Terminé",
        ):
            tk.Label(
                shortcuts_frame, text=line, font=("Helvetica", 12),
                bg="#0b1f14", fg="#cccccc", anchor="w", justify="left",
            ).pack(anchor="w")

        self.name_lbl = tk.Label(
            self, text="", font=("Helvetica", 22, "bold"),
            bg="#0b1f14", fg="#8fd694"
        )
        self.name_lbl.pack(pady=(8, 0))

        self.level_lbl = tk.Label(
            self, text="", font=("Helvetica", 28),
            bg="#0b1f14", fg="#ffffff"
        )
        self.level_lbl.pack(pady=(2, 0))

        self.timer_lbl = tk.Label(
            self, text="00:00", font=("Helvetica", 130, "bold"),
            bg="#0b1f14", fg="#ffffff"
        )
        self.timer_lbl.pack(pady=(0, 4))

        self.blinds_lbl = tk.Label(
            self, text="", font=("Helvetica", 46, "bold"),
            # Blanc plutôt que doré : peu visible de loin sur un
            # vidéoprojecteur (contrairement au chrono et au bandeau
            # "Round", déjà blancs/dorés à des tailles plus grandes).
            bg="#0b1f14", fg="#ffffff"
        )
        self.blinds_lbl.pack()

        # Tableau des jetons (couleur/valeur/nombre par joueur — voir
        # l'onglet Blindes) : en haut à droite, en overlay (comme le
        # rappel des raccourcis clavier en haut à gauche), plutôt qu'à
        # côté des blindes sur la même ligne — les blindes s'écrivent de
        # plus en plus large en montant de niveau (ex : "5000 / 10000
        # Ante 1000") et finissaient par s'approcher du tableau des
        # jetons. Reconstruit à chaque refresh() (voir
        # _update_chips_display) uniquement s'il a changé, car les
        # couleurs peuvent être modifiées en cours de tournoi.
        self.chips_table_frame = tk.Frame(self, bg="#0b1f14")
        self.chips_table_frame.place(relx=1.0, x=-20, y=16, anchor="ne")

        self.next_lbl = tk.Label(
            self, text="", font=("Helvetica", 18),
            bg="#0b1f14", fg="#aaaaaa"
        )
        self.next_lbl.pack(pady=(4, 0))

        self.next_break_lbl = tk.Label(
            self, text="", font=("Helvetica", 16),
            bg="#0b1f14", fg="#aaaaaa"
        )
        self.next_break_lbl.pack(pady=(0, 0))

        bottom = tk.Frame(self, bg="#0b1f14")
        bottom.pack(side="bottom", fill="x", pady=8)

        # Sous-frame non étirée, centrée dans "bottom" (qui occupe toute
        # la largeur) : les deux labels à l'intérieur restent groupés côte
        # à côte (side="left") mais le groupe lui-même est centré, au lieu
        # d'être collé au bord gauche de l'écran.
        bottom_center = tk.Frame(bottom, bg="#0b1f14")
        bottom_center.pack()

        self.players_lbl = tk.Label(
            bottom_center, text="", font=("Helvetica", 20),
            bg="#0b1f14", fg="#ffffff"
        )
        self.players_lbl.pack(side="left", padx=40)

        self.avg_lbl = tk.Label(
            bottom_center, text="", font=("Helvetica", 20),
            bg="#0b1f14", fg="#ffffff"
        )
        self.avg_lbl.pack(side="left", padx=40)

        self.duration_lbl = tk.Label(
            bottom_center, text="", font=("Helvetica", 20),
            bg="#0b1f14", fg="#ffffff"
        )
        self.duration_lbl.pack(side="left", padx=40)

        # Bandeau "Changement de tables en cours" : overlay affiché fixe
        # par-dessus le reste de l'écran, affiché/masqué depuis refresh()
        # selon movement_alert (voir App._trigger_movement_alert). Contient
        # le titre d'alerte + un tableau (Joueur/Ancienne table/Ancien
        # siège/Nouvelle table/Nouveau siège) listant les joueurs concernés
        # — reconstruit uniquement quand la liste change (voir
        # _update_movement_moves_table), pas à chaque tick de clignotement.
        self.movement_alert_frame = tk.Frame(
            self, bg="#8a1f1f", relief="solid", borderwidth=5,
        )
        self.movement_alert_lbl = tk.Label(
            self.movement_alert_frame, text="⚠  Changement de tables en cours  ⚠",
            font=("Helvetica", 40, "bold"), bg="#8a1f1f", fg="white",
        )
        self.movement_alert_lbl.pack(padx=40, pady=(22, 0))

        # Le tableau lui-même vit dans un Canvas plutôt qu'un simple Frame :
        # ça permet de le défiler par programme quand un gros
        # rééquilibrage déplace beaucoup de joueurs à la fois (au-delà de
        # MOVES_TABLE_MAX_HEIGHT), au lieu de le laisser déborder de
        # l'écran. Hauteur ajustée à chaque reconstruction (voir
        # _update_movement_moves_table) : pas de scroll tant que ça tient.
        self._moves_canvas = tk.Canvas(
            self.movement_alert_frame, bg="white", highlightthickness=0,
        )
        self._moves_canvas.configure(yscrollincrement=1)
        self._moves_canvas.pack(padx=40, pady=(16, 24))
        self._moves_table_frame = tk.Frame(self._moves_canvas, bg="white")
        self._moves_table_window = self._moves_canvas.create_window(
            (0, 0), window=self._moves_table_frame, anchor="nw"
        )
        self._moves_signature = None
        self._moves_needs_scroll = False
        self._moves_scroll_after_id = None
        self._moves_scroll_paused = False
        self._moves_autoscroll_tick()

        # Bandeau d'élimination "XXX est sorti par YYY" : overlay affiché
        # fixe par-dessus le reste de l'écran, comme movement_alert_frame
        # ci-dessus, mais géré depuis App._advance_elimination_banner
        # (état en mémoire avec échéance, pas un réglage persistant en
        # base comme movement_alert_active — ce bandeau n'a pas besoin de
        # survivre à un redémarrage). Photo éliminé à gauche, texte au
        # centre, photo éliminateur à droite — chaque photo masquée
        # (plutôt que vide) si le joueur correspondant n'en a pas.
        self.elimination_banner_frame = tk.Frame(
            self, bg="#1f4e8a", relief="solid", borderwidth=5,
        )
        elim_inner = tk.Frame(self.elimination_banner_frame, bg="#1f4e8a")
        elim_inner.pack(padx=50, pady=26)
        self._elimination_photo_left_lbl = tk.Label(elim_inner, bg="#1f4e8a", borderwidth=0)
        self._elimination_photo_left_lbl.pack(side="left", padx=(0, 30))
        self._elimination_text_lbl = tk.Label(
            elim_inner, text="", font=("Helvetica", 32, "bold"),
            bg="#1f4e8a", fg="white", justify="center",
        )
        self._elimination_text_lbl.pack(side="left")
        self._elimination_photo_right_lbl = tk.Label(elim_inner, bg="#1f4e8a", borderwidth=0)
        self._elimination_photo_right_lbl.pack(side="left", padx=(30, 0))
        # Références gardées explicitement (voir _update_elimination_banner) :
        # un PhotoImage/ImageTk sans référence Python est jeté à la
        # prochaine collecte, ce qui viderait silencieusement le label.
        self._elimination_photo_images = {"left": None, "right": None}
        self._elimination_signature = None

        self.bind("<F11>", self._toggle_fullscreen)
        self.bind("<Escape>", self._exit_fullscreen)
        self._fullscreen = False

    def _toggle_fullscreen(self, event=None):
        self._fullscreen = not self._fullscreen
        self.attributes("-fullscreen", self._fullscreen)

    def _exit_fullscreen(self, event=None):
        # Distincte de _toggle_fullscreen (Echap ne bascule pas, ne quitte
        # que le plein écran) mais doit quand même mettre _fullscreen à
        # jour — sinon bring_to_front() ci-dessous le repasserait à tort
        # en plein écran en croyant que c'est toujours son état voulu.
        self._fullscreen = False
        self.attributes("-fullscreen", False)

    def enter_fullscreen(self):
        """Force le plein écran (contrairement à _toggle_fullscreen, ne
        bascule pas : toujours vers l'état plein écran). Utilisé par le
        bouton "Chronomètre" du contrôle à distance (voir
        App._voice_show_clock / _voice_resume_clock) : quand ce bouton crée
        la fenêtre projecteur à distance, personne n'est physiquement là
        pour appuyer sur F11 — elle doit donc s'afficher directement "en
        mode écran projecteur" (plein écran), pas en petite fenêtre."""
        self._fullscreen = True
        self.attributes("-fullscreen", True)

    def bring_to_front(self):
        """Ramène cette fenêtre au premier plan, quitte son état réduit/
        rapetissé le cas échéant (voir Ctrl+Maj+C, App._voice_show_clock) :
        restaure le plein écran si c'était son état avant d'être réduite
        pour faire autre chose dans le logiciel, sinon la ramène juste au
        premier plan sans changer sa taille actuelle.

        self.lift()/focus_force() SEULS ne suffisent pas de façon fiable
        sous Windows : ce sont in fine des appels à SetForegroundWindow,
        que Windows refuse silencieusement (aucune erreur, la fenêtre ne
        vient juste pas devant) si ce processus n'a pas déjà la main —
        typiquement quand le responsable fait autre chose sur le PC au
        moment où le bouton "Chronomètre" est pressé à distance. D'où le
        comportement "ça marche parfois" observé : ça dépendait de ce que
        Windows autorisait sur le moment, pas d'un vrai aléa du logiciel.
        Bascule brève de -topmost (Z-order, jamais soumis à cette
        protection, contrairement au focus clavier) avant lift/
        focus_force : place la fenêtre au premier plan à coup sûr — ce
        qui suffit pour un écran projecteur, qu'on veut juste voir, pas
        y taper au clavier."""
        self.deiconify()
        self.attributes("-topmost", True)
        self.attributes("-topmost", False)
        self.lift()
        self.focus_force()
        if self._fullscreen:
            # Réappliqué EN DERNIER (pas avant le lift/focus_force
            # ci-dessus, comme précédemment) : sur macOS, réaffirmer le
            # plein écran natif après avoir repris la main semble plus
            # fiable pour vraiment repasser devant que de le faire avant.
            self.attributes("-fullscreen", True)

    def refresh(self, remaining_seconds, level_row, next_row, stats, tournament_name,
                is_paused, next_break_text="", movement_alert=False, chip_denominations=None,
                moves=None, round_number=None, elimination_banner=None):
        self.name_lbl.config(text=tournament_name)

        mins, secs = divmod(max(0, int(remaining_seconds)), 60)
        pause_suffix = "  ⏸" if is_paused else ""
        self.timer_lbl.config(text=f"{mins:02d}:{secs:02d}{pause_suffix}")

        is_break = level_row is not None and level_row["is_break"]
        # Pendant toute la durée d'une pause (niveau "Pause" de la
        # structure de blindes — pas une simple mise en pause manuelle du
        # chrono), le chrono clignote pour attirer l'œil du responsable
        # comme des joueurs sur l'écran projecteur.
        if is_break:
            blink_on = int(time.time()) % 2 == 0
            self.timer_lbl.config(fg="#f4c542" if blink_on else "#ffffff")
        else:
            self.timer_lbl.config(fg="#ffffff")

        if is_break:
            self.level_lbl.config(text=level_row["break_label"] or "Pause")
            self.blinds_lbl.config(text="")
        elif level_row is not None:
            # round_number (voir Database.get_round_number) plutôt que
            # level_row['level_order'] brut : doit afficher le même
            # numéro que la colonne "Round" de l'onglet Blindes, qui ne
            # compte pas les pauses comme une ligne à part entière —
            # d'où "Round" (et non "Niveau") ici aussi, même terminologie.
            self.level_lbl.config(text=f"Round {round_number}")
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
                    text=f"Prochain round : {next_row['small_blind']} / {next_row['big_blind']}"
                )
        else:
            self.next_lbl.config(text="Dernier round de la structure")

        self.players_lbl.config(
            text=f"Joueurs restants : {stats['active_count']} / {stats['total_players_ever']}"
        )
        self.avg_lbl.config(text=f"Tapis moyen : {int(stats['avg_stack']):,}".replace(",", " "))
        duration_h, rem = divmod(max(0, int(stats.get("duration_seconds", 0))), 3600)
        duration_m, duration_s = divmod(rem, 60)
        self.duration_lbl.config(text=f"Durée : {duration_h:02d}:{duration_m:02d}:{duration_s:02d}")
        self.next_break_lbl.config(text=next_break_text)

        if stats.get("tournament_finished"):
            # La partie est terminée (1 seul joueur actif restant) : le
            # bandeau "Partie terminée" prend toute la place, plus de
            # tableau de mouvements à montrer (voir App.eliminate_player /
            # Database.eliminate_player, qui fige aussi la "Durée"
            # ci-dessus) — et plus rien à annoncer non plus côté
            # élimination : ce dernier joueur restant EST déjà l'annonce.
            self.movement_alert_lbl.config(text="Partie terminée")
            self._moves_canvas.pack_forget()
            self._moves_needs_scroll = False
            self._moves_signature = None
            self.movement_alert_frame.place(relx=0.5, rely=0.42, anchor="center")
            self.elimination_banner_frame.place_forget()
        else:
            # Bandeau d'élimination : placé EN PREMIER, juste sous les
            # blindes (comme le bandeau de mouvement auparavant seul à
            # cet endroit) — voir App._advance_elimination_banner pour la
            # file/l'échéance, ce module ne fait que l'afficher. Sa
            # hauteur réelle (une fois placé) sert ensuite de référence
            # au bandeau de mouvement ci-dessous, pour que les deux ne se
            # superposent jamais s'ils sont actifs en même temps.
            self.update_idletasks()
            base_y = self.blinds_lbl.winfo_y() + self.blinds_lbl.winfo_height() + 10
            if elimination_banner is not None:
                self._update_elimination_banner(elimination_banner)
                self.elimination_banner_frame.place(relx=0.5, y=base_y, anchor="n")
                self.update_idletasks()
                movement_y = (
                    self.elimination_banner_frame.winfo_y()
                    + self.elimination_banner_frame.winfo_height() + 10
                )
            else:
                self.elimination_banner_frame.place_forget()
                movement_y = base_y

            if movement_alert:
                # Affiché fixe (ne clignote plus — ça perturbait la lecture
                # du tableau des joueurs concernés), tant que le mouvement
                # est en cours. Sous le bandeau d'élimination s'il est
                # affiché, sinon directement sous les blindes (pas au
                # milieu de l'écran, contrairement à "Partie terminée"
                # ci-dessus, et pas tout en bas de la fenêtre non plus —
                # ça poussait le tableau des joueurs concernés hors de la
                # zone visible sur une fenêtre pas assez haute, le rendant
                # tout simplement invisible) : le chronomètre continue de
                # tourner pendant un mouvement de tables (ne se met plus
                # en pause, voir App._trigger_movement_alert), il doit
                # donc rester visible au-dessus de ce bandeau, qui peut en
                # repousser/recouvrir le contenu en dessous (Prochain
                # niveau/pause).
                self.movement_alert_lbl.config(text="⚠  Changement de tables en cours  ⚠")
                self._update_movement_moves_table(moves or [])
                self.movement_alert_frame.place(relx=0.5, y=movement_y, anchor="n")
            else:
                self.movement_alert_frame.place_forget()

        self._update_chips_display(chip_denominations or [])
        self._ensure_fits_content()

    def _ensure_fits_content(self):
        """Agrandit la fenêtre en hauteur si le contenu (par exemple un
        tableau de jetons avec beaucoup de valeurs différentes, ou le
        tableau des joueurs concernés par un mouvement de tables) dépasse
        la taille actuelle, pour ne jamais couper de texte en bas de
        l'écran — sans quoi le responsable doit agrandir la fenêtre à la
        main pour tout voir. Ne rétrécit jamais tout seul (pas de
        scintillement si le contenu redevient plus court), et ne dépasse
        jamais la hauteur de l'écran. Pas d'effet en plein écran (F11)."""
        if self._fullscreen:
            return
        self.update_idletasks()
        needed_h = self.winfo_reqheight()
        # movement_alert_frame est positionné par place() (voir refresh),
        # pas pack()/grid() : sa taille ne compte donc PAS dans
        # winfo_reqheight() ci-dessus (place() ne participe pas à la
        # négociation de taille du conteneur) — sans ceci, la fenêtre ne
        # grandissait jamais pour montrer en entier un tableau de
        # mouvements un peu long, même une fois repositionné juste sous
        # le chrono (voir refresh()), le bas du tableau restant coupé.
        if self.movement_alert_frame.winfo_ismapped():
            needed_h = max(
                needed_h,
                self.movement_alert_frame.winfo_y() + self.movement_alert_frame.winfo_height() + 10,
            )
        # Même remarque pour elimination_banner_frame (voir refresh()) :
        # lui aussi positionné par place(), pas pris en compte par
        # winfo_reqheight() tout seul.
        if self.elimination_banner_frame.winfo_ismapped():
            needed_h = max(
                needed_h,
                self.elimination_banner_frame.winfo_y()
                + self.elimination_banner_frame.winfo_height() + 10,
            )
        current_h = self.winfo_height()
        if needed_h <= current_h:
            return
        # Pas de plafond calculé à la main ("hauteur d'écran - marge") :
        # winfo_screenheight() renvoie la résolution PHYSIQUE de l'écran,
        # pas la hauteur réellement disponible pour une fenêtre (menu,
        # dock...), donc un plafond basé dessus pouvait refuser une
        # croissance en fait possible, ou au contraire s'avérer trop
        # généreux et se faire quand même rogner en silence par le
        # système sans qu'on le sache. On demande donc directement la
        # hauteur voulue (remonte aussi la fenêtre si besoin pour lui
        # laisser un maximum de place vers le bas) et on laisse le
        # système d'exploitation, qui connaît la vraie zone visible,
        # accorder ou plafonner lui-même tout seul — jamais d'erreur ni
        # de débordement possible de son côté.
        new_h = needed_h + 10
        screen_h = self.winfo_screenheight()
        new_y = max(0, min(self.winfo_y(), max(0, screen_h - new_h)))
        self.geometry(f"{self.winfo_width()}x{new_h}+{self.winfo_x()}+{new_y}")

    def _update_movement_moves_table(self, moves):
        """Reconstruit le tableau des joueurs concernés par le
        rééquilibrage en cours (Joueur/Ancienne table/Ancien siège/
        Nouvelle table/Nouveau siège), centré et en gras, sous le titre
        d'alerte — seulement si la liste a changé depuis le dernier appel
        (comparaison d'une signature), pas à chaque tick de clignotement
        (refresh() est appelé une fois par seconde)."""
        signature = tuple(
            (m["player_name"], m["old_table_name"], m["old_seat"],
             m["new_table_name"], m["new_seat"])
            for m in moves
        )
        if signature == self._moves_signature:
            return
        self._moves_signature = signature

        for w in self._moves_table_frame.winfo_children():
            w.destroy()

        if not moves:
            self._moves_canvas.pack_forget()
            self._moves_needs_scroll = False
            return

        headers = ["Joueur", "Ancienne table", "Ancien siège", "Nouvelle table", "Nouveau siège"]
        cell_font = ("Helvetica", 20, "bold")
        for col, text in enumerate(headers):
            tk.Label(
                self._moves_table_frame, text=text, font=cell_font,
                bg="#f4c542", fg="#0b1f14", anchor="center", justify="center",
                padx=14, pady=8, borderwidth=1, relief="solid",
            ).grid(row=0, column=col, sticky="nsew")

        for row, m in enumerate(moves, start=1):
            values = [
                m["player_name"],
                m["old_table_name"] or "—", m["old_seat"] or "—",
                m["new_table_name"] or "—", m["new_seat"] or "—",
            ]
            for col, text in enumerate(values):
                tk.Label(
                    self._moves_table_frame, text=str(text), font=cell_font,
                    bg="white", fg="#0b1f14", anchor="center", justify="center",
                    padx=14, pady=6, borderwidth=1, relief="solid",
                ).grid(row=row, column=col, sticky="nsew")

        # Redimensionne le Canvas exactement au contenu tant que ça tient
        # dans MOVES_TABLE_MAX_HEIGHT ; au-delà, le Canvas se fige à cette
        # hauteur max et _moves_autoscroll_tick prend le relais pour faire
        # défiler lentement jusqu'à la dernière ligne.
        self._moves_canvas.update_idletasks()
        content_w = self._moves_table_frame.winfo_reqwidth()
        content_h = self._moves_table_frame.winfo_reqheight()
        visible_h = min(content_h, self.MOVES_TABLE_MAX_HEIGHT)
        self._moves_canvas.configure(width=content_w, height=visible_h)
        self._moves_canvas.configure(scrollregion=(0, 0, content_w, content_h))
        self._moves_canvas.yview_moveto(0.0)
        self._moves_needs_scroll = content_h > self.MOVES_TABLE_MAX_HEIGHT
        self._moves_scroll_paused = False
        self._moves_canvas.pack(padx=40, pady=(16, 24))

    def _moves_autoscroll_tick(self):
        """Boucle de défilement automatique et lent du tableau des
        mouvements sur le bandeau d'alerte, active seulement quand ce
        tableau est affiché ET que son contenu dépasse
        MOVES_TABLE_MAX_HEIGHT (sinon rien ne défile)."""
        if not self.winfo_exists():
            return
        if not self._moves_needs_scroll or not self.movement_alert_frame.winfo_ismapped():
            self._moves_scroll_after_id = self.after(500, self._moves_autoscroll_tick)
            return

        if not self._moves_scroll_paused:
            top_frac, bottom_frac = self._moves_canvas.yview()
            if bottom_frac >= 1.0:
                self._moves_scroll_paused = True
                self._moves_canvas.yview_moveto(0.0)
                self.after(self.MOVES_AUTOSCROLL_PAUSE_MS, self._moves_resume_autoscroll)
            else:
                self._moves_canvas.yview_scroll(self.MOVES_AUTOSCROLL_STEP_PX, "units")

        self._moves_scroll_after_id = self.after(
            self.MOVES_AUTOSCROLL_INTERVAL_MS, self._moves_autoscroll_tick
        )

    def _moves_resume_autoscroll(self):
        self._moves_scroll_paused = False

    ELIMINATION_PHOTO_SIZE = 150

    def _update_elimination_banner(self, banner):
        """Met à jour le texte et les deux photos du bandeau d'élimination
        (voir refresh()) — reconstruit seulement si le contenu a changé
        depuis le dernier appel (comparaison d'une signature), pas à
        chaque tick pendant tout l'affichage du même bandeau (refresh()
        est appelé une fois par seconde). `banner` est le dict construit
        par App._queue_elimination_banner (eliminated_name,
        eliminator_name) — jamais None ici (voir refresh)."""
        eliminated_name = banner.get("eliminated_name") or "?"
        eliminator_name = banner.get("eliminator_name")
        signature = (eliminated_name, eliminator_name)
        if signature == self._elimination_signature:
            return
        self._elimination_signature = signature

        if eliminator_name:
            text = f"{eliminated_name} est sorti par {eliminator_name}\nMerci d'avoir participé"
        else:
            text = f"{eliminated_name} est éliminé\nMerci d'avoir participé"
        self._elimination_text_lbl.config(text=text)

        # Chaque photo est chargée indépendamment : l'absence ou la
        # corruption de l'une ne doit jamais empêcher l'affichage de
        # l'autre ni du texte (voir _load_square_thumbnail, qui renvoie
        # toujours None plutôt qu'une exception). Référence explicitement
        # gardée dans self._elimination_photo_images : un PhotoImage sans
        # référence Python est jeté à la prochaine collecte, ce qui
        # viderait silencieusement le Label.
        for side, name, lbl in (
            ("left", eliminated_name, self._elimination_photo_left_lbl),
            ("right", eliminator_name, self._elimination_photo_right_lbl),
        ):
            path = player_photos.get_photo_path(name) if name else None
            photo = _load_square_thumbnail(path, self.ELIMINATION_PHOTO_SIZE) if path else None
            self._elimination_photo_images[side] = photo
            if photo is not None:
                lbl.config(image=photo, width=0, height=0)
            else:
                # Pas de photo (ou introuvable/corrompue) : label vide de
                # taille nulle, plutôt qu'un espace vide de la taille
                # d'une photo — le texte central reste bien centré entre
                # les deux (voir _update_chips_display pour le même
                # principe de repli propre).
                lbl.config(image="", width=0, height=0)

    def _update_chips_display(self, denominations):
        """Reconstruit le tableau des jetons (à hauteur des blindes)
        seulement s'il a changé depuis le dernier refresh() — appelé une
        fois par seconde, inutile de tout redétruire/reconstruire à
        chaque tick."""
        signature = tuple(
            (d.get("name", ""), d.get("color", ""), d.get("image", ""),
             d.get("value", 0), d.get("count", 0))
            for d in denominations
        )
        if signature == getattr(self, "_chips_signature", None):
            return
        self._chips_signature = signature

        for w in self.chips_table_frame.winfo_children():
            w.destroy()
        # Garde une référence à chaque PhotoImage affichée : sans ça, Tk
        # les récupère en mémoire (garbage collect) dès la fin de cette
        # méthode et les vignettes disparaissent du Canvas.
        self._chip_photo_images = []
        if not denominations:
            return

        tk.Label(
            self.chips_table_frame, text="Jetons", font=("Helvetica", 15, "bold"),
            bg="#0b1f14", fg="#f4c542",
        ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 2))

        # pady=1 (au lieu de 2) sur chaque ligne : avec beaucoup de valeurs
        # de jetons, ce tableau peut devenir plus haut que la ligne des
        # blindes à côté de lui et pousser tout le reste de l'écran vers le
        # bas — resserré au maximum pour que tout reste visible sans avoir
        # à agrandir la fenêtre.
        for i, d in enumerate(denominations, start=1):
            swatch = tk.Canvas(
                self.chips_table_frame, width=18, height=18, highlightthickness=0, bg="#0b1f14",
            )
            swatch.grid(row=i, column=0, padx=(0, 8), pady=1, sticky="w")
            image_path = chip_images.get_chip_image_path(d.get("image")) if d.get("image") else None
            photo = _load_square_thumbnail(image_path, 18) if image_path else None
            if photo is not None:
                swatch.create_image(9, 9, image=photo)
                self._chip_photo_images.append(photo)
            else:
                swatch.create_oval(1, 1, 17, 17, fill=d.get("color") or "#000000", outline="#f4c542")

            tk.Label(
                self.chips_table_frame, text=d.get("name") or "?", font=("Helvetica", 16),
                bg="#0b1f14", fg="#ffffff", anchor="w",
            ).grid(row=i, column=1, padx=(0, 16), pady=1, sticky="w")

            value_text = f"{d.get('value', 0):,}".replace(",", " ")
            tk.Label(
                self.chips_table_frame, text=value_text, font=("Helvetica", 16),
                bg="#0b1f14", fg="#ffffff", anchor="e",
            ).grid(row=i, column=2, padx=(0, 8), pady=1, sticky="e")

            tk.Label(
                self.chips_table_frame, text=f"× {d.get('count', 0)}", font=("Helvetica", 16),
                bg="#0b1f14", fg="#aaaaaa", anchor="w",
            ).grid(row=i, column=3, pady=1, sticky="w")
