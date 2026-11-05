# -*- coding: utf-8 -*-
"""Bouton « ? Aide » sur la barre d'en-tête (demande du 2026-11-05) —
même ligne que « Menu principal » et « Lobby », aligné complètement à
droite, réutilisant exclusivement App._show_context_help() (même
méthode que F1 et le menu Aide, jamais une nouvelle logique).

Tests structurels (source) + sur une VRAIE racine Tk (jamais une
doublure), sans jamais appeler update()/update_idletasks() sur un
Toplevel (risque de SIGSEGV Tcl/Tk déjà documenté dans ce harnais, voir
tests/test_bienvenue_window_height.py) : pack_info() suffit ici, aucun
besoin de mesure réelle à l'écran."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

from _tk_cleanup import cleanup_tk  # noqa: E402


def _lire_main():
    chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(chemin, "r", encoding="utf-8") as f:
        return f.read()


def _lire_build_header():
    contenu = _lire_main()
    debut = contenu.index("    def _build_header(self):")
    fin = contenu.index("    def _update_window_title(self):")
    return contenu[debut:fin]


class EmplacementStructurelTest(unittest.TestCase):
    """Emplacement et ordre validés : sur la barre de _build_header,
    après Menu principal et Lobby, jamais ailleurs dans le fichier."""

    def test_aide_apres_menu_principal_et_lobby_sur_la_meme_barre(self):
        bloc = _lire_build_header()
        pos_menu = bloc.index('"🏠  Menu principal"')
        pos_lobby = bloc.index('"📋  Lobby"')
        pos_aide = bloc.index('"? Aide"')
        self.assertLess(pos_menu, pos_lobby, "Ordre Menu principal/Lobby inchangé.")
        self.assertLess(pos_lobby, pos_aide, "Le bouton Aide doit être ajouté après Lobby.")

    def test_libelle_sans_emoji(self):
        bloc = _lire_build_header()
        self.assertIn('text="? Aide"', bloc)

    def test_aide_jamais_dupliquee_ailleurs_dans_main_py(self):
        """Ne doit apparaître qu'une seule fois dans tout le fichier —
        jamais recopiée dans une autre fenêtre/dialogue."""
        contenu = _lire_main()
        self.assertEqual(contenu.count('text="? Aide"'), 1)

    def test_reutilise_exclusivement_show_context_help(self):
        bloc = _lire_build_header()
        self.assertIn("command=self._show_context_help", bloc)

    def test_f1_toujours_lie_globalement_sans_changement(self):
        """F1 reste lié via bind_all dans les deux variantes du menu,
        strictement inchangé par cet ajout."""
        contenu = _lire_main()
        self.assertEqual(
            contenu.count('self.bind_all("<F1>", lambda e: self._show_context_help())'), 2,
        )


class _FakeApp(tk.Toplevel if _TK_AVAILABLE else object):
    """Greffe App._build_header sur un Toplevel ordinaire — mêmes
    attributs/méthodes que _build_header lit réellement sur `self`,
    sans jamais construire une vraie App() (DB, menus, etc.)."""

    def __init__(self, master, db=None):
        super().__init__(master)
        self.db = db
        self.appels = []

    def _open_new_window(self):
        self.appels.append("_open_new_window")

    def _new_tournament(self):
        self.appels.append("_new_tournament")

    def _open_lobby(self):
        self.appels.append("_open_lobby")

    def _show_context_help(self):
        self.appels.append("_show_context_help")

    def _update_window_title(self):
        pass


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class BarreReelleTest(unittest.TestCase):
    """Vraie fenêtre Tk (jamais une doublure) — confirme la présence,
    l'alignement et le comportement réels du bouton, sans jamais
    appeler update()/update_idletasks()."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _construire(self, db=None):
        fake = _FakeApp(self.root, db=db)
        self.addCleanup(fake.destroy)
        main.App._build_header(fake)
        return fake

    def _rangee_inner(self, fake):
        header = fake.winfo_children()[0]
        return header.winfo_children()[0]

    def test_bouton_aide_present_aligne_a_droite(self):
        fake = self._construire()
        inner = self._rangee_inner(fake)
        boutons_aide = [
            w for w in inner.winfo_children()
            if isinstance(w, ttk.Button) and str(w.cget("text")) == "? Aide"
        ]
        self.assertEqual(len(boutons_aide), 1, "Le bouton « ? Aide » doit être présent une seule fois.")
        self.assertEqual(
            boutons_aide[0].pack_info().get("side"), "right",
            "Le bouton « ? Aide » doit être aligné à droite (side=\"right\").",
        )

    def test_appel_du_bouton_declenche_show_context_help(self):
        fake = self._construire()
        inner = self._rangee_inner(fake)
        bouton_aide = next(
            w for w in inner.winfo_children()
            if isinstance(w, ttk.Button) and str(w.cget("text")) == "? Aide"
        )
        bouton_aide.invoke()
        self.assertEqual(fake.appels, ["_show_context_help"])

    def test_menu_principal_et_lobby_restent_a_gauche_sans_tournoi(self):
        fake = self._construire(db=None)
        inner = self._rangee_inner(fake)
        boutons_gauche = [
            w for w in inner.winfo_children()
            if isinstance(w, ttk.Button)
            and str(w.cget("text")) in ("🏠  Menu principal", "📋  Lobby")
        ]
        self.assertEqual(len(boutons_gauche), 2, "Menu principal et Lobby doivent toujours être présents.")
        for bouton in boutons_gauche:
            self.assertEqual(
                bouton.pack_info().get("side"), "left",
                "L'alignement existant de Menu principal/Lobby ne doit pas changer.",
            )

    def test_menu_principal_et_lobby_restent_a_gauche_avec_tournoi(self):
        """Même contrôle, variante self.db non None (fenêtre de
        tournoi) — la seule différence attendue est la commande du
        bouton Menu principal, jamais sa position."""
        fake = self._construire(db=object())
        inner = self._rangee_inner(fake)
        boutons_gauche = [
            w for w in inner.winfo_children()
            if isinstance(w, ttk.Button)
            and str(w.cget("text")) in ("🏠  Menu principal", "📋  Lobby")
        ]
        self.assertEqual(len(boutons_gauche), 2)
        for bouton in boutons_gauche:
            self.assertEqual(bouton.pack_info().get("side"), "left")

    def test_bouton_aide_present_que_tournoi_ouvert_ou_non(self):
        """Disponible partout où cette barre est présente — vérifié
        dans les deux états (self.db None ou non), puisque
        _build_header est la même méthode dans les deux cas."""
        for db in (None, object()):
            with self.subTest(db=db):
                fake = self._construire(db=db)
                inner = self._rangee_inner(fake)
                presents = [
                    w for w in inner.winfo_children()
                    if isinstance(w, ttk.Button) and str(w.cget("text")) == "? Aide"
                ]
                self.assertEqual(len(presents), 1)


if __name__ == "__main__":
    unittest.main()
