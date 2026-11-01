# -*- coding: utf-8 -*-
"""Correctif du 2026-10-31 — "une seule instance du Lobby pour toute la
session" : constat réel sur le Sony Vaio (capture à l'appui) que
chaque fenêtre de tournoi étant un PROCESSUS séparé (voir main.py:
spawn_app_process), l'ancien correctif "singleton" du 2026-10-28
(LobbyDialog._instance, un simple attribut de CLASSE Python) ne pouvait
structurellement pas empêcher plusieurs fenêtres de tournoi d'ouvrir
chacune leur propre Lobby. Nouvelle architecture (option B validée
explicitement) : LobbyDialog n'est plus jamais construit QUE dans un
processus Lobby autonome dédié (voir App.__init__, POKER_TOURNAMENT_
LOBBY_STANDALONE) ; l'unicité réelle vit désormais dans un registre
inter-processus (voir open_windows.py, section "Instance unique du
Lobby", et tests/test_lobby_cross_process_registry.py pour ce registre
lui-même et le vrai scénario multiprocessus).

Ce fichier-ci couvre ce que LobbyDialog fait réellement, LUI, dans son
propre processus :
- non modal (point 3 de la demande du 2026-10-31, remplace le
  comportement modal du 2026-10-28) ;
- fermeture propre : désenregistrement inter-processus ET fin du
  "processus" (ici, destruction de self.master — voir destroy()) ;
- auto-fermeture dès que plus aucun tournoi n'est ouvert (point 5,
  "sans processus orphelin"), mais JAMAIS si des tournois ouverts sont
  seulement masqués par "Masquer les tournois terminés" (ça resterait
  un vrai Lobby utile, juste filtré à l'affichage).

IMPORTANT — destroy() détruit désormais AUSSI self.master (fin du
"processus" Lobby) : chaque test utilise donc sa PROPRE racine Tk
jetable (setUp, jamais une racine partagée entre tests comme avant ce
correctif — elle ne survivrait pas à la fermeture du premier Lobby
testé). Même convention établie par ailleurs dans cette suite (racine
réelle jamais une doublure, jamais update_idletasks()/update() — voir
tests/test_bienvenue_window_height.py pour le diagnostic du SIGSEGV
que ça évite)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402

import main  # noqa: E402
import open_windows  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _root_fully_destroyed(root):
    """True si `root` (une vraie racine tk.Tk(), jamais un simple
    widget) a été détruite — contrairement à winfo_exists() sur un
    widget/Toplevel ordinaire, l'appeler sur une racine tk.Tk() déjà
    détruite ne renvoie PAS False : il lève TclError ("application has
    been destroyed"), l'interpréteur Tcl entier ayant disparu avec
    elle. "a levé" est ici la SEULE façon de constater la destruction,
    jamais un signe d'échec à masquer (même principe que _still_exists
    dans tests/_tk_cleanup.py, appliqué ici à la racine elle-même)."""
    try:
        return not root.winfo_exists()
    except tk.TclError:
        return True


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LobbySingletonModalTest(unittest.TestCase):
    """Une racine Tk FRAÎCHE par test (voir l'avertissement en tête de
    fichier) — jamais partagée, puisque destroy() du Lobby la détruit
    désormais elle aussi."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        # Non détruite explicitement dans tearDown : la plupart des
        # tests ferment eux-mêmes le Lobby, ce qui détruit déjà `root`
        # (voir destroy()) — un destroy() redondant sur une racine déjà
        # partie est sans risque (winfo_exists() le détecte), mais
        # autant l'éviter : addCleanup avec garde explicite.
        self.addCleanup(self._cleanup_root_if_alive)

        main.LobbyDialog._instance = None
        self.addCleanup(setattr, main.LobbyDialog, "_instance", None)

        # Par défaut : au moins un "tournoi ouvert" fictif, pour que
        # _refresh() (appelée à la construction) ne déclenche jamais
        # l'auto-fermeture (point 5) dans les tests qui ne testent pas
        # spécifiquement CE comportement — voir _rendre_non_vide ci-
        # dessous. os.path.exists patché aussi : sans ça, ce chemin
        # fictif serait filtré AVANT même d'atteindre la vérification
        # d'auto-fermeture (voir _refresh : "if path not in seen and
        # os.path.exists(path)").
        self._rendre_non_vide()

        # open_windows.unregister_lobby réellement appelée (jamais
        # mockée par défaut) : un test dédié vérifie son appel précis ;
        # les autres n'en dépendent pas mais ne doivent jamais écrire
        # pour de vrai dans ~/.poker_tournament — redirigée vers un
        # fichier jetable.
        import tempfile
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="lobby_singleton_test_")
        self.addCleanup(self._tmp_dir.cleanup)
        home_patcher = patch.object(os.path, "expanduser", return_value=self._tmp_dir.name)
        self.addCleanup(home_patcher.stop)
        home_patcher.start()

    def _cleanup_root_if_alive(self):
        try:
            if self.root is not None and self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass

    def _rendre_non_vide(self, chemin="/fake/not_really_opened.tournoi"):
        patcher_paths = patch.object(open_windows, "list_open_paths", return_value=[chemin])
        patcher_exists = patch.object(main.os.path, "exists", return_value=True)
        self.addCleanup(patcher_paths.stop)
        self.addCleanup(patcher_exists.stop)
        patcher_paths.start()
        patcher_exists.start()
        # La vraie Database() échouerait sur ce chemin fictif — déjà
        # intercepté silencieusement par le try/except de _refresh()
        # (voir son commentaire "fichier illisible/corrompu : ignoré
        # plutôt que planter le Lobby") : aucun mock supplémentaire
        # nécessaire, ces tests ne portent jamais sur le CONTENU du
        # tableau, seulement sur l'existence/fermeture de la fenêtre.

    def _ouvrir(self):
        lobby = main.LobbyDialog.ouvrir(self.root)
        return lobby

    def test_ouvrir_cree_une_instance_et_l_enregistre(self):
        lobby = self._ouvrir()
        self.assertIs(main.LobbyDialog._instance, lobby)
        self.assertTrue(lobby.winfo_exists())

    def test_deuxieme_appel_a_ouvrir_dans_le_meme_processus_reutilise_la_meme_instance(self):
        """Garde locale, défense en profondeur seulement (voir le
        commentaire de _instance dans main.py) — l'unicité RÉELLE, elle,
        est désormais testée dans test_lobby_cross_process_registry.py."""
        lobby1 = self._ouvrir()
        lobby2 = main.LobbyDialog.ouvrir(self.root)
        self.assertIs(lobby1, lobby2)

    def test_lobby_est_non_modal(self):
        """Point 3 de la demande du 2026-10-31 — inverse explicitement
        le comportement modal posé le 2026-10-28 : le Lobby ne doit
        JAMAIS détenir le grab Tk, pour ne jamais bloquer l'usage des
        fenêtres de tournoi pendant qu'il reste ouvert."""
        self._ouvrir()
        self.assertIsNone(self.root.grab_current())

    def test_aucun_attribut_topmost_global(self):
        """Jamais de -topmost, qui maintiendrait Senaco devant toutes
        les autres applications."""
        lobby = self._ouvrir()
        self.assertFalse(bool(lobby.attributes("-topmost")))

    def test_fermeture_detruit_aussi_le_master(self):
        """Fin du "processus" Lobby (demande du 2026-10-31, "sans
        processus orphelin") : ce processus n'ayant plus rien d'autre à
        faire une fois le Lobby fermé, sa racine doit elle aussi être
        détruite — c'est ce qui met fin à mainloop() en production."""
        lobby = self._ouvrir()
        self.assertTrue(self.root.winfo_exists())

        with patch.object(open_windows, "unregister_lobby"):
            lobby._on_close()

        self.assertTrue(_root_fully_destroyed(self.root))
        self.assertIsNone(main.LobbyDialog._instance)

    def test_fermeture_desenregistre_du_registre_inter_processus(self):
        """Vérifie l'appel précis à open_windows.unregister_lobby, avec
        le PID de CE processus — voir open_windows.py pour le registre
        lui-même (testé en détail séparément)."""
        lobby = self._ouvrir()
        with patch.object(open_windows, "unregister_lobby") as mock_unregister:
            lobby._on_close()
        mock_unregister.assert_called_once_with(os.getpid())

    def test_auto_fermeture_si_plus_aucun_tournoi_nest_ouvert(self):
        """Point 5 ("sans processus orphelin") : dès que open_windows.
        list_open_paths() ne renvoie plus rien, un rafraîchissement
        (manuel ou automatique) doit fermer le Lobby lui-même — jamais
        le laisser tourner sans rien à afficher."""
        lobby = self._ouvrir()
        self.assertTrue(self.root.winfo_exists())

        with patch.object(open_windows, "list_open_paths", return_value=[]), \
             patch.object(open_windows, "unregister_lobby"):
            lobby._refresh()

        self.assertTrue(_root_fully_destroyed(self.root))

    def test_pas_dauto_fermeture_si_des_tournois_restent_ouverts(self):
        """Non-régression explicite : tant qu'au moins un tournoi est
        ouvert, un rafraîchissement normal ne doit jamais fermer le
        Lobby."""
        lobby = self._ouvrir()
        lobby._refresh()  # toujours le chemin fictif non-vide de setUp
        self.assertTrue(self.root.winfo_exists())

    def test_tournois_tous_masques_nest_pas_vu_comme_aucun_tournoi(self):
        """Distinction explicite documentée dans _refresh() : des
        tournois réellement ouverts mais tous "Terminé" et masqués par
        "Masquer les tournois terminés" restent un Lobby légitimement
        ouvert — ce n'est PAS "aucun tournoi", seulement un affichage
        filtré. Le test porte sur `paths` (calculée AVANT tout filtrage
        d'affichage), jamais sur le nombre de lignes réellement
        insérées dans le tableau."""
        lobby = self._ouvrir()

        fake_status = {
            "level": None, "remaining_seconds": None, "finished": True,
            "clock_started": True, "is_paused": False,
            "name": "Tournoi fictif", "date": "2026-10-31",
            "active_count": 0, "total_players_ever": 8,
        }
        fake_db = type("FakeDB", (), {
            "get_live_status": lambda self: fake_status,
            "close": lambda self: None,
        })()
        lobby.hide_finished_var.set(True)

        with patch.object(main, "Database", return_value=fake_db):
            lobby._refresh()

        self.assertTrue(self.root.winfo_exists())


if __name__ == "__main__":
    unittest.main()
