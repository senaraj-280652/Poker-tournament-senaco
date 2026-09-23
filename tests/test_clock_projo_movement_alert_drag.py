# -*- coding: utf-8 -*-
"""Glisser-déposer vertical du bandeau "Changement de tables en cours"
sur le Chrono Projo (demande du 2026-09-19, ergonomie) : un test réel
avec 7 mouvements a montré que la position calculée par défaut laissait
le tableau déborder trop bas de l'écran projecteur (~4 lignes visibles
seulement). ClockWindow.movement_alert_frame reste positionné par
place() comme avant, mais peut désormais être déplacé verticalement à
la souris depuis son titre (movement_alert_lbl) — confirmé fonctionnel
en usage réel le 2026-09-20 : la prise en main doit se faire DANS la
zone rouge du titre, pas sur son bord (comportement volontaire, jamais
sur movement_alert_frame lui-même).

IMPORTANT — pourquoi ce fichier n'appelle JAMAIS ClockWindow.refresh()
ni self.win.update_idletasks() (contournement explicitement demandé le
2026-09-19, AUCUNE correction du problème lui-même) :

Un crash Tcl/Tk PRÉ-EXISTANT et totalement indépendant de ce chantier a
été isolé par diagnostic (reproduit de façon déterministe, y compris sur
clock_window.py d'AVANT toute modification de ce chantier, extrait via
`git show HEAD:clock_window.py`) : dès qu'un `tk.Tk()` a été créé PUIS
détruit plus tôt dans le même processus — exactement le schéma de la
sonde `_TK_AVAILABLE` utilisée par TOUS les fichiers Tk de cette suite,
y compris celui-ci —, le premier `update_idletasks()` appelé sur N'IMPORTE
QUEL widget réel d'une ClockWindow construite ensuite (la fenêtre
elle-même OU un de ses widgets, ex. _moves_canvas) segfault
systématiquement. Aucun test existant de cette suite n'avait jamais
détecté ce problème car aucun n'appelait ClockWindow.refresh() ni
update_idletasks() directement avant ce chantier (test_clock_projo_
moves_scroll.py, par exemple, teste _update_movement_moves_table/
_moves_autoscroll_tick en isolation avec un canvas factice, jamais via
refresh()).

Pour couvrir malgré tout précisément la nouvelle logique de glisser sans
jamais déclencher ce crash pré-existant :

- les gestionnaires de glisser eux-mêmes (_movement_alert_drag_start/
  _motion/_end, _clamp_movement_alert_y) sont appelés pour de VRAI —
  aucun d'eux n'appelle update_idletasks() (vérifié par diagnostic) ;
- la géométrie du bandeau est lue via place_info() (métadonnées de la
  requête place(), disponibles immédiatement) plutôt que winfo_y(), qui
  nécessiterait un update_idletasks() pour refléter un place() récent
  dans ce harnais sans vraie boucle d'évènements ;
- winfo_height() (fenêtre et bandeau) est doublé par un simple lambda —
  comme déjà pratiqué ailleurs dans cette suite (ex. movement_alert_
  frame.winfo_ismapped dans test_clock_projo_moves_scroll.py) — pour
  donner des bornes de clamp déterministes sans dépendre d'une vraie
  résolution de géométrie ;
- le bloc de décision de placement qui vit à l'intérieur de refresh()
  ("if movement_alert: ... if not self._movement_alert_dragging: ...")
  est reproduit fidèlement par _mirror_movement_alert_placement
  ci-dessous, qui appelle les VRAIES méthodes/attributs de production
  (_clamp_movement_alert_y, _movement_alert_manual_y,
  _movement_alert_dragging, movement_alert_frame.place/place_forget/
  tkraise) sans jamais passer par refresh() lui-même — voir sa docstring ;
- _update_movement_moves_table est appelée pour de VRAI (elle ne
  segfault pas en elle-même), mais avec _moves_canvas remplacé par un
  MagicMock (même technique que test_clock_projo_moves_scroll.py) : sans
  ce remplacement, son propre appel interne à
  self._moves_canvas.update_idletasks() déclencherait le même crash
  pré-existant.

Le défilement automatique existant (autoscroll) reste testé en détail
par tests/test_clock_projo_moves_scroll.py, non modifié par ce
chantier — ce fichier-ci se contente de vérifier que les nouveaux
bindings de glisser ne sont posés que sur movement_alert_lbl, jamais sur
le canvas/tableau."""
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import clock_window  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


class _FakeEvent:
    """Doublure minimale d'un évènement souris Tk : seul y_root est lu
    par les gestionnaires de glisser (voir ClockWindow._movement_alert_
    drag_start/_movement_alert_drag_motion)."""
    def __init__(self, y_root):
        self.y_root = y_root


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovementAlertDragTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.win = clock_window.ClockWindow(self.root, app=None)
        self.addCleanup(lambda: cleanup_tk(self, "win", "root"))
        self.win.withdraw()

        # Bornes de clamp déterministes et généreuses par défaut (voir
        # _clamp_movement_alert_y) : une fenêtre "haute" de 800px pour un
        # bandeau de 200px laisse largement de la place pour les tests de
        # glisser normal ci-dessous, sans déclencher le clamp par
        # accident — les tests dédiés au clamp resserrent ces valeurs
        # localement.
        self.win.winfo_height = lambda: 800
        self.win.movement_alert_frame.winfo_height = lambda: 200

    def _moves(self, n):
        return [
            {
                "player_name": f"Joueur{i}", "old_table_name": "Table 1",
                "old_seat": i, "new_table_name": "Table 2", "new_seat": i,
            }
            for i in range(1, n + 1)
        ]

    def _place_y(self):
        """Position actuellement REQUISE par place() (métadonnées,
        disponibles sans update_idletasks — voir la docstring du module)."""
        return int(self.win.movement_alert_frame.place_info()["y"])

    def _mirror_movement_alert_placement(self, movement_alert, movement_y):
        """Reproduit fidèlement le bloc de placement de movement_alert_
        frame tel qu'il existe dans ClockWindow.refresh() (section
        "Glisser-déposer... refresh() ne doit JAMAIS replacer le bandeau
        pendant un glisser"), sans jamais appeler refresh() lui-même —
        voir la docstring du module pour la raison. Utilise les VRAIS
        attributs/méthodes de production ; si ce bloc de refresh() est un
        jour modifié, ce miroir devra être mis à jour en conséquence."""
        win = self.win
        if movement_alert:
            if not win._movement_alert_dragging:
                if win._movement_alert_manual_y is not None:
                    y_to_place = win._clamp_movement_alert_y(win._movement_alert_manual_y)
                else:
                    y_to_place = movement_y
                win.movement_alert_frame.place(relx=0.5, y=y_to_place, anchor="n")
            win.movement_alert_frame.tkraise()
        else:
            win.movement_alert_frame.place_forget()
            win._movement_alert_manual_y = None

    # -- Glisser lui-même : vertical uniquement -----------------------------

    def test_glisser_deplace_verticalement_sans_toucher_relx(self):
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        start_frame_y = self.win._movement_alert_drag_start_frame_y

        self.win._movement_alert_drag_motion(_FakeEvent(y_root=540))  # +40

        info = self.win.movement_alert_frame.place_info()
        self.assertEqual(int(info["y"]), start_frame_y + 40)
        self.assertEqual(self.win._movement_alert_manual_y, start_frame_y + 40)
        # Horizontal jamais touché par le glisser : x reste à sa valeur
        # neutre ('0', jamais utilisée puisque le placement horizontal
        # est piloté par relx) et relx reste fixé à 0.5.
        self.assertEqual(info["x"], "0")
        self.assertEqual(float(info["relx"]), 0.5)
        self.assertEqual(info["anchor"], "n")

    def test_glisser_vers_le_haut_fonctionne_aussi(self):
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        start_frame_y = self.win._movement_alert_drag_start_frame_y

        self.win._movement_alert_drag_motion(_FakeEvent(y_root=470))  # -30

        self.assertEqual(self._place_y(), max(0, start_frame_y - 30))

    def test_glisser_repete_naccumule_jamais_le_x_ou_le_relx(self):
        """anchor="n" avec seul y qui varie : jamais de x/relx modifié par
        le glisser, à aucun moment — vérifié après plusieurs mouvements
        successifs, pas seulement le premier."""
        self.win._movement_alert_drag_start(_FakeEvent(y_root=100))
        for y_root in (110, 90, 200, 50):
            self.win._movement_alert_drag_motion(_FakeEvent(y_root=y_root))
            info = self.win.movement_alert_frame.place_info()
            self.assertEqual(info["x"], "0")
            self.assertEqual(float(info["relx"]), 0.5)

    def test_motion_sans_press_prealable_ne_fait_rien(self):
        """_movement_alert_dragging est False par défaut : un B1-Motion
        égaré (sans ButtonPress-1 préalable) ne doit rien déplacer."""
        self.win.movement_alert_frame.place(relx=0.5, y=77, anchor="n")

        self.win._movement_alert_drag_motion(_FakeEvent(y_root=999))

        self.assertEqual(self._place_y(), 77)
        self.assertIsNone(self.win._movement_alert_manual_y)

    # -- Limites verticales (_clamp_movement_alert_y) ------------------------

    def test_clamp_borne_haute_et_basse(self):
        self.win.winfo_height = lambda: 500
        self.win.movement_alert_frame.winfo_height = lambda: 150

        self.assertEqual(self.win._clamp_movement_alert_y(-999), 0)
        self.assertEqual(self.win._clamp_movement_alert_y(10_000), 350)  # 500 - 150
        self.assertEqual(self.win._clamp_movement_alert_y(200), 200)  # dans les bornes

    def test_glisser_respecte_le_clamp_en_temps_reel(self):
        """Un glisser qui viserait une position hors fenêtre est ramené
        dans les bornes DÈS le mouvement de souris, pas seulement à un
        tick refresh() ultérieur."""
        self.win.winfo_height = lambda: 500
        self.win.movement_alert_frame.winfo_height = lambda: 150

        self.win._movement_alert_drag_start(_FakeEvent(y_root=0))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=100_000))  # vise très bas
        self.assertEqual(self._place_y(), 350)
        self.assertEqual(self.win._movement_alert_manual_y, 350)

        self.win._movement_alert_drag_motion(_FakeEvent(y_root=-100_000))  # vise très haut
        self.assertEqual(self._place_y(), 0)

    def test_reclamp_apres_redimensionnement_fenetre(self):
        """Une position glissée valide au moment du glisser doit être
        reclampée si la fenêtre/le bandeau changent de taille ensuite
        (redimensionnement, bascule plein écran) — vérifié via un tick
        refresh() simulé après coup, comme le ferait le prochain tick
        réel (~1×/s)."""
        self.win._movement_alert_drag_start(_FakeEvent(y_root=0))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=700))  # proche du bas (borne large 800-200=600)
        self.win._movement_alert_drag_end(_FakeEvent(y_root=700))
        self.assertEqual(self.win._movement_alert_manual_y, 600)  # déjà clampé à la borne large

        # La fenêtre "rétrécit" (ex. sortie du plein écran) : la même
        # position manuelle mémorisée doit être reclampée à la NOUVELLE
        # borne, jamais laissée à l'ancienne valeur devenue hors fenêtre.
        self.win.winfo_height = lambda: 300
        self.win.movement_alert_frame.winfo_height = lambda: 150

        self._mirror_movement_alert_placement(movement_alert=True, movement_y=42)

        self.assertEqual(self._place_y(), 150)  # 300 - 150, nouvelle borne
        # La position manuelle mémorisée elle-même reste inchangée (600) :
        # seul le PLACEMENT affiché est reclampé à chaque tick — un
        # agrandissement ultérieur de la fenêtre peut donc redonner accès
        # à la pleine position d'origine, sans qu'un redimensionnement
        # passager ne l'efface définitivement.
        self.assertEqual(self.win._movement_alert_manual_y, 600)

    # -- Pas de rebond pendant/après le glisser ------------------------------

    def test_pas_de_rebond_pendant_un_glisser_en_cours(self):
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=560))
        dragged_y = self._place_y()
        self.assertTrue(self.win._movement_alert_dragging)

        # Un tick refresh() simulé survient PENDANT le glisser (bouton
        # toujours maintenu, drag_end jamais appelé) : ne doit jamais
        # replacer le bandeau ni perdre l'état de glisser.
        self._mirror_movement_alert_placement(movement_alert=True, movement_y=42)

        self.assertTrue(self.win._movement_alert_dragging)
        self.assertEqual(self._place_y(), dragged_y)

    def test_position_memorisee_apres_relachement_sur_plusieurs_ticks(self):
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=560))
        self.win._movement_alert_drag_end(_FakeEvent(y_root=560))
        self.assertFalse(self.win._movement_alert_dragging)
        dragged_y = self._place_y()
        self.assertEqual(self.win._movement_alert_manual_y, dragged_y)

        # Plusieurs ticks refresh() simulés (movement_y volontairement
        # DIFFÉRENT à chaque fois, comme le ferait un vrai recalcul de
        # position par défaut) : la position glissée doit tenir tant que
        # cette alerte continue.
        for movement_y in (10, 900, 42):
            self._mirror_movement_alert_placement(movement_alert=True, movement_y=movement_y)
            self.assertEqual(self._place_y(), dragged_y)

    # -- Confirmations individuelles ------------------------------------------

    def test_confirmation_individuelle_ne_change_pas_la_position_glissee(self):
        # _moves_canvas remplacé par un factice (voir docstring du module) :
        # seul _update_movement_moves_table (reconstruction du tableau
        # suite à une confirmation, voir le chantier Mouvements) doit
        # s'exécuter réellement ici, jamais le vrai canvas Tk.
        self.win._moves_canvas = MagicMock()
        self.win._update_movement_moves_table(self._moves(3))

        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=560))
        self.win._movement_alert_drag_end(_FakeEvent(y_root=560))
        dragged_y = self._place_y()

        # Une confirmation individuelle reçue depuis l'iPhone reconstruit
        # le tableau avec un mouvement en moins (voir _remote_confirm_move),
        # puis le tick refresh() suivant redessine le bandeau — la
        # position glissée ne doit jamais en être affectée.
        self.win._update_movement_moves_table(self._moves(2))
        self._mirror_movement_alert_placement(movement_alert=True, movement_y=42)

        self.assertEqual(self._place_y(), dragged_y)

    # -- Fin d'alerte puis nouvelle alerte ------------------------------------

    def test_dernier_mouvement_confirme_masque_le_bandeau_puis_nouvelle_alerte_repart_par_defaut(self):
        self.win.movement_alert_frame.place(relx=0.5, y=10, anchor="n")
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=560))
        self.win._movement_alert_drag_end(_FakeEvent(y_root=560))
        self.assertIsNotNone(self.win._movement_alert_manual_y)

        # Dernier mouvement confirmé : movement_alert redevient faux,
        # exactement comme aujourd'hui (place_forget()).
        self._mirror_movement_alert_placement(movement_alert=False, movement_y=0)
        self.assertEqual(self.win.movement_alert_frame.place_info(), {})
        self.assertFalse(self.win.movement_alert_frame.winfo_ismapped())
        self.assertIsNone(self.win._movement_alert_manual_y)

        # Nouvelle alerte plus tard : repart de la position par défaut
        # (ici 123, valeur arbitraire représentant ce que refresh()
        # calculerait normalement), jamais de celle laissée par le
        # glisser précédent.
        self._mirror_movement_alert_placement(movement_alert=True, movement_y=123)
        self.assertEqual(self._place_y(), 123)

    # -- Le tableau/l'autoscroll ne doivent jamais être concernés ------------

    def test_seul_le_titre_recoit_les_bindings_de_glisser(self):
        self.assertNotEqual(self.win.movement_alert_lbl.bind("<ButtonPress-1>"), "")
        self.assertNotEqual(self.win.movement_alert_lbl.bind("<B1-Motion>"), "")
        self.assertNotEqual(self.win.movement_alert_lbl.bind("<ButtonRelease-1>"), "")

        # Le cadre lui-même (marge rouge) et a fortiori le tableau/canvas
        # blanc (autoscroll) ne reçoivent aucun de ces bindings.
        self.assertEqual(self.win.movement_alert_frame.bind("<ButtonPress-1>"), "")
        self.assertEqual(self.win._moves_canvas.bind("<ButtonPress-1>"), "")
        self.assertEqual(self.win._moves_table_frame.bind("<ButtonPress-1>"), "")

    def test_autoscroll_toujours_actif_apres_lajout_du_glisser(self):
        """Non-régression légère (couverture détaillée dans tests/
        test_clock_projo_moves_scroll.py, non modifié par ce chantier) :
        _moves_needs_scroll/_moves_autoscroll_tick restent fonctionnels et
        ne sont jamais désactivés par l'ajout du glisser."""
        self.win._moves_canvas = MagicMock()
        self.win._moves_canvas.yview.return_value = (0.2, 0.5)
        self.win._moves_needs_scroll = True
        self.win.movement_alert_frame.winfo_ismapped = lambda: True
        self.win.after = MagicMock(return_value="fake-after-id")

        self.win._moves_autoscroll_tick()

        self.win._moves_canvas.yview_scroll.assert_called_once_with(
            self.win.MOVES_AUTOSCROLL_STEP_PX, "units"
        )

    # -- nudge_movement_alert_position (flèches ↓/↑ du téléphone, demande
    # du 2026-09-20) : RÉUTILISE _movement_alert_manual_y/_clamp_movement_
    # alert_y, aucun second système de positionnement — voir la méthode
    # dans clock_window.py. Appelée directement ici (sans event souris),
    # exactement comme le fera App._on_voice_word depuis le thread Tk.

    def test_nudge_bas_deplace_vers_le_bas_du_pas_configure(self):
        self.win.movement_alert_frame.place(relx=0.5, y=100, anchor="n")
        self.win.movement_alert_frame.winfo_ismapped = lambda: True
        # winfo_y() doublé (voir la docstring du module : sans vraie
        # boucle d'évènements dans ce harnais, il reste "figé" à sa
        # valeur par défaut malgré le place() ci-dessus — c'est le
        # SEUL repère lu par nudge_movement_alert_position quand aucun
        # glisser souris n'a encore eu lieu, _movement_alert_manual_y
        # valant alors None).
        self.win.movement_alert_frame.winfo_y = lambda: 100

        self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        # "bas" = y AUGMENTE (Tk : y croît vers le bas de l'écran).
        self.assertEqual(self._place_y(), 100 + self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)
        self.assertEqual(self.win._movement_alert_manual_y, 100 + self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

    def test_nudge_haut_deplace_vers_le_haut_du_pas_configure(self):
        self.win.movement_alert_frame.place(relx=0.5, y=100, anchor="n")
        self.win.movement_alert_frame.winfo_ismapped = lambda: True
        self.win.movement_alert_frame.winfo_y = lambda: 100  # voir commentaire ci-dessus

        self.win.nudge_movement_alert_position(-self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        # "haut" = y DIMINUE.
        self.assertEqual(self._place_y(), 100 - self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

    def test_nudge_part_de_la_position_deja_glissee_a_la_souris(self):
        """Un nudge après un glisser souris part de la DERNIÈRE position
        connue (_movement_alert_manual_y) — même variable que le glisser,
        jamais un second système de positionnement."""
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))
        self.win._movement_alert_drag_motion(_FakeEvent(y_root=560))
        self.win._movement_alert_drag_end(_FakeEvent(y_root=560))
        dragged_y = self._place_y()
        self.win.movement_alert_frame.winfo_ismapped = lambda: True

        self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        self.assertEqual(self._place_y(), dragged_y + self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

    def test_nudge_respecte_le_clamp(self):
        self.win.winfo_height = lambda: 500
        self.win.movement_alert_frame.winfo_height = lambda: 150
        self.win.movement_alert_frame.place(relx=0.5, y=340, anchor="n")
        self.win.movement_alert_frame.winfo_ismapped = lambda: True
        self.win.movement_alert_frame.winfo_y = lambda: 340  # voir commentaire plus haut

        # 340 + 40 = 380, au-delà de la borne basse (500 - 150 = 350).
        self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        self.assertEqual(self._place_y(), 350)

    def test_nudge_repetes_saccumulent_puis_se_clampent(self):
        self.win.winfo_height = lambda: 200
        self.win.movement_alert_frame.winfo_height = lambda: 150
        self.win.movement_alert_frame.place(relx=0.5, y=0, anchor="n")
        self.win.movement_alert_frame.winfo_ismapped = lambda: True
        self.win.movement_alert_frame.winfo_y = lambda: 0  # voir commentaire plus haut ; seul le 1er nudge le lit (_movement_alert_manual_y prend ensuite le relais)

        for _ in range(3):  # 0 -> 40 -> 80 -> clampé à 50 (200-150)
            self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        self.assertEqual(self._place_y(), 50)

    def test_nudge_ignore_pendant_un_glisser_souris_en_cours(self):
        """Garde-fou 1 (validé explicitement) : un nudge tombant pendant
        un glisser souris en cours ne doit JAMAIS le perturber."""
        self.win.movement_alert_frame.place(relx=0.5, y=100, anchor="n")
        self.win.movement_alert_frame.winfo_ismapped = lambda: True
        self.win._movement_alert_drag_start(_FakeEvent(y_root=500))  # dragging=True, jamais drag_end ici

        self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        self.assertEqual(self._place_y(), 100)  # inchangé
        self.assertIsNone(self.win._movement_alert_manual_y)  # jamais écrit
        self.assertTrue(self.win._movement_alert_dragging)  # état de glisser jamais touché

    def test_nudge_ignore_si_bandeau_non_affiche(self):
        """Garde-fou 2a (validé explicitement) : aucune alerte de
        mouvements en cours -> le nudge ne doit rien faire, et surtout
        pas faire réapparaître un bandeau masqué hors contexte."""
        self.win.movement_alert_frame.place(relx=0.5, y=100, anchor="n")
        self.win.movement_alert_frame.place_forget()
        # winfo_ismapped() volontairement NON doublé ici : False par
        # défaut dans ce harnais (voir la docstring du module), exactement
        # comme un vrai bandeau masqué.

        self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        self.assertEqual(self.win.movement_alert_frame.place_info(), {})
        self.assertIsNone(self.win._movement_alert_manual_y)

    def test_nudge_ignore_en_mode_partie_terminee(self):
        """Garde-fou 2b (validé explicitement) : movement_alert_frame est
        réutilisé pour "Partie terminée" (place(rely=0.42,
        anchor="center"), jamais y=...,anchor="n") — un nudge ne doit
        JAMAIS déplacer CE bandeau-là, distingué ici par l'anchor courant."""
        self.win.movement_alert_frame.place(relx=0.5, rely=0.42, anchor="center")
        self.win.movement_alert_frame.winfo_ismapped = lambda: True

        self.win.nudge_movement_alert_position(self.win.MOVEMENT_ALERT_NUDGE_STEP_PX)

        info = self.win.movement_alert_frame.place_info()
        self.assertEqual(info["rely"], "0.42")
        self.assertEqual(info["anchor"], "center")
        self.assertIsNone(self.win._movement_alert_manual_y)


if __name__ == "__main__":
    unittest.main()
