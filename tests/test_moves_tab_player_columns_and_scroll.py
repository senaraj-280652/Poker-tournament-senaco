# -*- coding: utf-8 -*-
"""Suite du "mode joueurs" de l'onglet Mouvements (demande du
2026-10-05, deuxième diagnostic) :

A. Largeurs des 5 colonnes du mode joueurs mémorisées de façon
   persistante (moves_tab_columns.py : ~/.poker_tournament/moves_tab_
   player_columns.json), INDÉPENDAMMENT des largeurs de l'affichage
   normal (jamais mélangées) — voir App._apply_moves_tab_display_mode,
   qui n'agit plus que sur un changement RÉEL de mode (self._moves_tab_
   display_mode_applied), jamais à chaque _refresh_moves_tab (appelée
   1x/seconde tant que l'onglet est affiché, voir _tick).

B. Défilement automatique du mode joueurs (App._moves_player_autoscroll_
   tick/_moves_player_reset_to_top/_moves_player_resume_autoscroll) —
   copie fidèle du principe de l'onglet Classement (_classement_
   autoscroll_tick, JAMAIS modifié par ce chantier), avec une garde
   supplémentaire sur self._moves_tab_player_mode. Démarré UNE SEULE
   FOIS dans _build_moves_tab, jamais recréé.

Complète tests/test_moves_tab_player_mode.py (chantier précédent,
toujours valide et non modifié ici)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402
import moves_tab_columns  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


# =========================================================================
# A.1 — Persistance de bas niveau (moves_tab_columns.py), sans toucher au
#       vrai ~/.poker_tournament
# =========================================================================
class MovesTabColumnsPersistenceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="moves_tab_columns_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "moves_tab_player_columns.json")
        patcher = patch.object(moves_tab_columns, "_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _widths(self, player=300, old_table=240, old_seat=200, new_table=240, new_seat=200):
        return {
            "player": player, "old_table": old_table, "old_seat": old_seat,
            "new_table": new_table, "new_seat": new_seat,
        }

    def test_load_renvoie_none_sans_fichier(self):
        self.assertIsNone(moves_tab_columns.load_player_column_widths())

    def test_round_trip_exact(self):
        widths = self._widths()
        moves_tab_columns.save_player_column_widths(widths)
        self.assertEqual(moves_tab_columns.load_player_column_widths(), widths)

    def test_remplace_plutot_que_daccumuler(self):
        moves_tab_columns.save_player_column_widths(self._widths(player=300))
        moves_tab_columns.save_player_column_widths(self._widths(player=400))
        self.assertEqual(
            moves_tab_columns.load_player_column_widths()["player"], 400,
        )

    def test_fichier_corrompu_ne_leve_jamais(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON valide")
        self.assertIsNone(moves_tab_columns.load_player_column_widths())

    def test_widths_incomplet_renvoie_none(self):
        import json
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"widths": {"player": 300}}, f)  # 4 colonnes manquantes
        self.assertIsNone(moves_tab_columns.load_player_column_widths())

    def test_persiste_apres_redemarrage_simule(self):
        """Rien ne persiste en mémoire entre deux "sessions" d'un module
        purement fichier — un nouvel appel à load_player_column_widths()
        après coup (sans aucun état intermédiaire) est l'équivalent exact
        d'un redémarrage de Senaco."""
        widths = self._widths(player=321)
        moves_tab_columns.save_player_column_widths(widths)
        # Rien d'autre entre les deux appels : simule la relecture au
        # prochain lancement de Senaco.
        reloaded = moves_tab_columns.load_player_column_widths()
        self.assertEqual(reloaded, widths)

    def test_jamais_mele_a_dautres_prefs(self):
        """Le fichier dédié ne doit jamais être confondu avec last_
        settings.json (tournament_prefs.py) ni un autre mécanisme."""
        moves_tab_columns.save_player_column_widths(self._widths())
        self.assertTrue(self.path.endswith("moves_tab_player_columns.json"))
        with open(self.path, encoding="utf-8") as f:
            content = f.read()
        self.assertNotIn("buyin_amount", content)


# =========================================================================
# A.2 — App._apply_moves_tab_display_mode : largeurs, gating sur
#        changement réel, séparation normal/joueurs
# =========================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovesTabPlayerColumnWidthsTest(unittest.TestCase):
    PLAYER_DEFAULTS = {
        "player": 260, "old_table": 230, "old_seat": 190, "new_table": 230, "new_seat": 190,
    }

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        main.App._apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="moves_tab_player_widths_test_")
        self.addCleanup(self._tmp.cleanup)
        self.json_path = os.path.join(self._tmp.name, "moves_tab_player_columns.json")
        patcher = patch.object(moves_tab_columns, "_path", return_value=self.json_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win._moves_tab_player_mode = False
        self.win._moves_tab_display_mode_applied = None
        self.win._moves_tab_normal_col_widths = None
        self.win._moves_tab_show_previous_moves = False

        self.win.moves_tab = ttk.Frame(self.root)
        self.addCleanup(self.win.moves_tab.destroy)
        self.win._finish_movement_alert = lambda: None
        self.win._print_movement_slips_filled = lambda: None
        self.win._print_movement_slips = lambda: None
        self.win.notebook = MagicMock()
        self.win.notebook.select.return_value = "tabid"
        self.win.notebook.tab.return_value = "Mouvements"

        # after()/after_cancel() doublés (voir tests/test_clock_projo_
        # moves_scroll.py, même précaution) : _build_moves_tab démarre la
        # boucle de défilement, qui ne doit jamais programmer de vrai
        # timer Tk dans ces tests.
        self.win.after = lambda ms, fn: "after-id"
        self.win.after_cancel = MagicMock()

        # Constantes de classe : self.win est un tk.Tk() brut (jamais une
        # vraie App), donc main.App.MOVES_TAB_*/MOVES_PLAYER_* ne sont pas
        # héritées automatiquement — copiées explicitement ici, à
        # l'identique de ce que ferait un vrai self (App).
        for const in (
            "MOVES_TAB_ALL_COLUMNS", "MOVES_TAB_PLAYER_COLUMNS",
            "MOVES_TAB_PLAYER_DEFAULT_WIDTHS", "MOVES_PLAYER_AUTOSCROLL_INTERVAL_MS",
            "MOVES_PLAYER_AUTOSCROLL_PAUSE_MS",
        ):
            setattr(self.win, const, getattr(main.App, const))

        for name in (
            "_build_moves_tab", "_apply_moves_tab_display_mode", "_refresh_moves_tab",
            "_on_moves_tree_button_release", "_save_moves_player_column_widths_if_changed",
            "_moves_player_autoscroll_tick", "_moves_player_reset_to_top",
            "_moves_player_resume_autoscroll", "_toggle_previous_moves_display",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))

        self.win._build_moves_tab()

    def test_premier_affichage_utilise_les_defauts_demandes(self):
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        widths = {c: self.win.moves_tree.column(c, "width") for c in self.PLAYER_DEFAULTS}
        self.assertEqual(widths, self.PLAYER_DEFAULTS)

    def test_redimensionnement_manuel_est_sauvegarde(self):
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        # Simule un glisser de souris sur la bordure d'en-tête : ttk
        # applique lui-même la nouvelle largeur, puis relâche le bouton.
        self.win.moves_tree.column("player", width=410)
        self.win.moves_tree.column("old_table", width=300)
        self.win._on_moves_tree_button_release(event=None)
        saved = moves_tab_columns.load_player_column_widths()
        self.assertEqual(saved["player"], 410)
        self.assertEqual(saved["old_table"], 300)

    def test_clic_simple_sans_redimensionner_necrit_rien_dinutile(self):
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.win._on_moves_tree_button_release(event=None)
        # Rien n'a été modifié depuis les défauts : aucun fichier ne doit
        # être créé pour autant (évite une écriture disque superflue).
        self.assertIsNone(moves_tab_columns.load_player_column_widths())

    def test_largeurs_retrouvees_au_prochain_affichage_mode_joueurs(self):
        """Scénario exact signalé : redimensionner, quitter, revenir."""
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.win.moves_tree.column("new_seat", width=222)
        self.win._on_moves_tree_button_release(event=None)

        self.win._moves_tab_player_mode = False
        self.win._apply_moves_tab_display_mode()

        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.assertEqual(self.win.moves_tree.column("new_seat", "width"), 222)

    def test_appels_repetes_sans_changement_de_mode_ne_reinitialisent_pas(self):
        """Coeur du correctif : tant que le mode ne change pas (cas du
        tick périodique de _refresh_moves_tab), un redimensionnement en
        cours ne doit jamais être écrasé par une réapplication des
        largeurs mémorisées/défauts."""
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.win.moves_tree.column("player", width=500)  # resize "en direct"
        # Plusieurs appels supplémentaires, mode TOUJOURS joueurs (comme
        # le ferait _tick une fois par seconde) :
        for _ in range(5):
            self.win._apply_moves_tab_display_mode()
        self.assertEqual(self.win.moves_tree.column("player", "width"), 500)

    def test_sortie_du_mode_joueurs_restaure_les_largeurs_normales(self):
        normal_widths_before = {
            c: self.win.moves_tree.column(c, "width") for c in main.App.MOVES_TAB_ALL_COLUMNS
        }
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.win.moves_tree.column("player", width=999)  # modifié EN mode joueurs

        self.win._moves_tab_player_mode = False
        self.win._apply_moves_tab_display_mode()

        normal_widths_after = {
            c: self.win.moves_tree.column(c, "width") for c in main.App.MOVES_TAB_ALL_COLUMNS
        }
        self.assertEqual(normal_widths_after, normal_widths_before)

    def test_largeurs_normales_personnalisees_par_lorganisateur_sont_protegees(self):
        """Si l'organisateur avait DÉJÀ ajusté ses propres largeurs en
        mode normal, un passage par le mode joueurs ne doit pas les
        remplacer par les défauts d'origine."""
        self.win.moves_tree.column("reason", width=250)  # organisateur ajuste "Raison"
        self.win._moves_tab_player_mode = True
        self.win._apply_moves_tab_display_mode()
        self.win._moves_tab_player_mode = False
        self.win._apply_moves_tab_display_mode()
        self.assertEqual(self.win.moves_tree.column("reason", "width"), 250)

    def test_premier_appel_en_mode_normal_necrit_jamais_le_fichier_joueurs(self):
        """Le tout premier appel (mode démarre à False) ne doit jamais
        confondre les largeurs normales d'origine avec des largeurs
        "mode joueurs" à sauvegarder."""
        self.assertIsNone(moves_tab_columns.load_player_column_widths())


# =========================================================================
# B — Défilement automatique du mode joueurs
# =========================================================================
class _FakeScrollApp:
    """Doublure minimale : seuls moves_tree (MagicMock, yview contrôlé
    explicitement par chaque test) et les attributs/méthodes réellement
    exercés par _moves_player_autoscroll_tick sont présents — même
    principe que tests/test_clock_projo_moves_scroll.py pour ClockWindow."""

    MOVES_PLAYER_AUTOSCROLL_INTERVAL_MS = main.App.MOVES_PLAYER_AUTOSCROLL_INTERVAL_MS
    MOVES_PLAYER_AUTOSCROLL_PAUSE_MS = main.App.MOVES_PLAYER_AUTOSCROLL_PAUSE_MS

    _moves_player_autoscroll_tick = main.App._moves_player_autoscroll_tick
    _moves_player_reset_to_top = main.App._moves_player_reset_to_top
    _moves_player_resume_autoscroll = main.App._moves_player_resume_autoscroll

    def __init__(self):
        self.moves_tree = MagicMock()
        self.moves_tree.winfo_exists.return_value = True
        self._moves_tab_player_mode = True
        self._moves_player_scroll_paused = False
        self.notebook = MagicMock()
        self.notebook.select.return_value = "tabid"
        self.notebook.tab.return_value = "Mouvements"
        self.after_calls = []
        self._next_after_id = 0

    def winfo_exists(self):
        return True

    def after(self, ms, fn):
        self._next_after_id += 1
        after_id = f"after-{self._next_after_id}"
        self.after_calls.append((after_id, ms, fn))
        return after_id


class MovesPlayerAutoscrollTest(unittest.TestCase):
    def setUp(self):
        self.app = _FakeScrollApp()

    def test_aucun_defilement_si_tout_tient_a_lecran(self):
        self.app.moves_tree.yview.return_value = (0.0, 1.0)
        self.app._moves_player_autoscroll_tick()
        self.app.moves_tree.yview_scroll.assert_not_called()
        self.assertFalse(self.app._moves_player_scroll_paused)

    def test_defile_dune_ligne_si_necessaire(self):
        self.app.moves_tree.yview.return_value = (0.0, 0.5)
        self.app._moves_player_autoscroll_tick()
        self.app.moves_tree.yview_scroll.assert_called_once_with(1, "units")

    def test_pause_en_bas_une_fois_tout_defile(self):
        self.app.moves_tree.yview.return_value = (0.6, 1.0)
        self.app._moves_player_autoscroll_tick()
        self.assertTrue(self.app._moves_player_scroll_paused)
        self.app.moves_tree.yview_scroll.assert_not_called()
        pause_calls = [c for c in self.app.after_calls if c[1] == main.App.MOVES_PLAYER_AUTOSCROLL_PAUSE_MS]
        self.assertEqual(len(pause_calls), 1)

    def test_retour_en_haut_puis_reprise_apres_la_pause(self):
        """Séquence réaliste complète : la dernière ligne devient visible
        (tick -> pause programmée) -> la pause s'écoule (_moves_player_
        reset_to_top, déjà programmé par le tick) -> retour en haut ->
        nouvelle pause programmée (_moves_player_resume_autoscroll) ->
        celle-ci s'écoule -> le défilement peut reprendre."""
        self.app.moves_tree.yview.return_value = (0.6, 1.0)
        self.app._moves_player_autoscroll_tick()
        self.assertTrue(self.app._moves_player_scroll_paused)
        reset_calls = [c for c in self.app.after_calls if c[2] == self.app._moves_player_reset_to_top]
        self.assertEqual(len(reset_calls), 1)

        self.app.after_calls.clear()
        self.app._moves_player_reset_to_top()
        self.app.moves_tree.yview_moveto.assert_called_once_with(0.0)
        resume_calls = [c for c in self.app.after_calls if c[2] == self.app._moves_player_resume_autoscroll]
        self.assertEqual(len(resume_calls), 1)
        self.assertTrue(self.app._moves_player_scroll_paused)  # toujours en pause tant que resume n'a pas tourné

        self.app._moves_player_resume_autoscroll()
        self.assertFalse(self.app._moves_player_scroll_paused)

    def test_aucun_defilement_hors_mode_joueurs(self):
        self.app._moves_tab_player_mode = False
        self.app.moves_tree.yview.return_value = (0.0, 0.5)  # dépasserait la hauteur
        self.app._moves_player_autoscroll_tick()
        self.app.moves_tree.yview_scroll.assert_not_called()
        self.app.moves_tree.yview.assert_not_called()

    def test_aucun_defilement_si_onglet_different(self):
        self.app.notebook.tab.return_value = "Chronomètre"
        self.app.moves_tree.yview.return_value = (0.0, 0.5)
        self.app._moves_player_autoscroll_tick()
        self.app.moves_tree.yview_scroll.assert_not_called()

    def test_boucle_se_reprogramme_tant_que_le_mode_est_actif(self):
        self.app.moves_tree.yview.return_value = (0.0, 0.5)
        self.app._moves_player_autoscroll_tick()
        normal_calls = [c for c in self.app.after_calls if c[1] == main.App.MOVES_PLAYER_AUTOSCROLL_INTERVAL_MS]
        self.assertEqual(len(normal_calls), 1)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class MovesTabRefreshNeverResetsScrollTest(unittest.TestCase):
    """IMPORTANT (demande explicite) : _refresh_moves_tab, appelée 1x/s
    par _tick tant que l'onglet est affiché, ne doit JAMAIS remettre le
    défilement à zéro pendant que le mode joueurs est affiché — seuls
    l'entrée en mode joueurs (_on_voice_word) et "Terminé" le font."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()
        main.App._apply_theme(cls.root)

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="moves_tab_refresh_scroll_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win._moves_tab_player_mode = True
        self.win._moves_tab_display_mode_applied = True  # déjà en mode joueurs
        self.win._moves_tab_normal_col_widths = {c: 130 for c in main.App.MOVES_TAB_ALL_COLUMNS}
        self.win._moves_tab_show_previous_moves = False

        # moves_tree entièrement doublé ici : seul le FAIT que _refresh_
        # moves_tab n'appelle jamais yview_moveto nous intéresse, pas la
        # géométrie réelle (indisponible en Tk non mappé, voir diagnostic).
        self.win.moves_tree = MagicMock()
        self.win.moves_tree.get_children.return_value = []

        self.win._apply_moves_tab_display_mode = types.MethodType(
            main.App._apply_moves_tab_display_mode, self.win
        )
        self.win._refresh_moves_tab = types.MethodType(main.App._refresh_moves_tab, self.win)

    def test_refresh_repete_ne_remet_jamais_yview_a_zero(self):
        for _ in range(5):
            self.win._refresh_moves_tab()
        self.win.moves_tree.yview_moveto.assert_not_called()


# =========================================================================
# Non-régression : l'onglet Classement est une référence, jamais modifié
# =========================================================================
class ClassementAutoscrollUntouchedTest(unittest.TestCase):
    def test_constantes_classement_inchangees(self):
        self.assertEqual(main.App.CLASSEMENT_AUTOSCROLL_INTERVAL_MS, 700)
        self.assertEqual(main.App.CLASSEMENT_AUTOSCROLL_PAUSE_MS, 2500)

    def test_methodes_classement_toujours_presentes_et_distinctes(self):
        # _moves_player_* doivent être des méthodes SÉPARÉES, jamais un
        # simple alias/renommage de celles de Classement.
        self.assertIsNot(
            main.App._classement_autoscroll_tick, main.App._moves_player_autoscroll_tick
        )
        self.assertIsNot(
            main.App._classement_reset_to_top, main.App._moves_player_reset_to_top
        )


if __name__ == "__main__":
    unittest.main()
