# -*- coding: utf-8 -*-
"""Masquage de la section "Code personnel" de l'onglet CA (demande du
2026-10-03, "masquage de la section code personnel et code par
défaut") :

- Masquée par défaut à chaque construction de l'onglet CA (donc à
  chaque lancement de Senaco — _build_ca_tab s'exécute une fois par
  ouverture de fenêtre, cet état n'est jamais persisté sur disque).
- Ctrl+Maj+P bascule sa visibilité (titre + indicateur + champ +
  boutons Définir/Changer + Supprimer, d'un seul geste).
- Le raccourci n'a AUCUN effet depuis un autre onglet que "CA" — testé
  directement sur le handler (_on_toggle_personal_code_section), jamais
  via un event_generate clavier réel : ce fichier de test évite
  volontairement tout appel de géométrie réelle (update_idletasks/
  winfo_x/winfo_y/event_generate), reproduisant le même risque de
  segfault Tcl/Tk documenté dans tests/test_ca_log_tab_wiring.py — la
  vérification se fait via grid_info() seul (racine withdraw()ée dans ce
  harnais : winfo_ismapped() y resterait toujours faux, mappage réel
  exigeant toute la chaîne d'ancêtres visible — grid_info() renvoie {}
  dès que le widget n'est plus géré par grid, indépendamment de tout
  affichage réel, c'est l'état qui nous intéresse ici), jamais via un
  calcul de layout.
- Masquage réalisé par grid_remove() (jamais grid_forget()) sur les
  DEUX widgets (titre + ligne) : conserve les options de grille pour un
  grid() ultérieur et ne laisse aucun espace vide (une ligne de grille
  Tk sans widget visible ne réserve aucune hauteur)."""
import inspect
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


_GRAFTED_METHODS = (
    "_build_ca_tab",
    "_build_log_search_criteria_placeholder",
    "_refresh_remote_control_status",
    "_refresh_remote_devices_panel",
    "_build_remote_dirto_permissions_widgets",
    "_refresh_remote_dirto_permissions_panel",
    "_on_remote_dirto_user_selected",
    "_on_grant_remote_dirto_permissions",
    "_on_revoke_remote_dirto_permissions",
    "_on_remote_control_toggle",
    "_refresh_personal_remote_code_status",
    "_on_set_personal_remote_code",
    "_on_clear_personal_remote_code",
    "_is_ca_tab_active",
    "_on_toggle_personal_code_section",
    "_set_personal_code_section_visible",
    "_bind_personal_code_section_shortcut",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PersonalCodeSectionVisibilityTest(unittest.TestCase):
    """Même harnais que tests/test_ca_log_tab_wiring.py::CaTabRealBuildTest
    (racine Tk réelle greffée des vraies méthodes App) — étend juste la
    liste des méthodes greffées avec celles du masquage."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self._tmp = tempfile.TemporaryDirectory(prefix="personal_code_visibility_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        self.win.remote_control_server = None  # requis par _refresh_remote_control_status

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.ca_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.ca_tab, text="CA")
        self.other_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.other_tab, text="Autre")
        self.notebook.select(self.ca_tab)
        self.win.notebook = self.notebook
        self.win.ca_tab = self.ca_tab

        for name in _GRAFTED_METHODS:
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in ("_remote_dirto_signature", "_remote_devices_signature"):
            setattr(self.win, name, getattr(main.App, name))

        self.addCleanup(lambda: cleanup_tk(
            self, "root", "win", "notebook", "ca_tab", "other_tab",
            "remote_devices_container", "remote_dirto_container",
        ))

    @staticmethod
    def _grid_visible(widget):
        """True si `widget` est actuellement géré par grid (grid_info()
        renvoie un dict non vide), False juste après grid_remove() (dict
        vide) — PAS winfo_ismapped(), toujours faux ici puisque self.root
        est withdraw()ée (mappage réel exigeant toute la chaîne
        d'ancêtres visible, voir le commentaire d'en-tête du fichier)."""
        return bool(widget.grid_info())

    def test_section_masquee_par_defaut_a_la_construction(self):
        self.win._build_ca_tab()

        self.assertFalse(self.win._personal_code_section_visible)
        self.assertFalse(self._grid_visible(self.win.personal_code_title))
        self.assertFalse(self._grid_visible(self.win.personal_code_row))

    def test_raccourci_affiche_puis_masque(self):
        self.win._build_ca_tab()

        self.win._on_toggle_personal_code_section()
        self.assertTrue(self.win._personal_code_section_visible)
        self.assertTrue(self._grid_visible(self.win.personal_code_title))
        self.assertTrue(self._grid_visible(self.win.personal_code_row))

        self.win._on_toggle_personal_code_section()
        self.assertFalse(self.win._personal_code_section_visible)
        self.assertFalse(self._grid_visible(self.win.personal_code_title))
        self.assertFalse(self._grid_visible(self.win.personal_code_row))

    def test_affiche_les_deux_widgets_ensemble_jamais_un_seul(self):
        """"Le masquage concerne le titre, l'indicateur d'activation, le
        champ de saisie et les boutons" — un seul geste doit basculer
        les DEUX conteneurs (titre + ligne) ensemble, jamais l'un sans
        l'autre."""
        self.win._build_ca_tab()
        self.win._on_toggle_personal_code_section()
        self.assertEqual(
            self._grid_visible(self.win.personal_code_title),
            self._grid_visible(self.win.personal_code_row),
        )

    def test_raccourci_sans_effet_hors_onglet_ca(self):
        """"Le raccourci fonctionne uniquement lorsque l'onglet CA est
        ouvert" — sélectionner un autre onglet doit neutraliser le
        raccourci, sans qu'aucune exception ne soit levée."""
        self.win._build_ca_tab()
        self.notebook.select(self.other_tab)

        self.win._on_toggle_personal_code_section()

        self.assertFalse(self.win._personal_code_section_visible)
        self.assertFalse(self._grid_visible(self.win.personal_code_title))

    def test_redevient_actif_des_que_longlet_ca_est_reselectionne(self):
        self.win._build_ca_tab()
        self.notebook.select(self.other_tab)
        self.win._on_toggle_personal_code_section()  # sans effet
        self.notebook.select(self.ca_tab)

        self.win._on_toggle_personal_code_section()

        self.assertTrue(self.win._personal_code_section_visible)
        self.assertTrue(self._grid_visible(self.win.personal_code_title))

    def test_etat_toujours_masque_a_une_nouvelle_construction(self):
        """"Chaque nouveau lancement de Senaco rétablit automatiquement
        l'état masqué" — reconstruire l'onglet CA (comme à un nouveau
        lancement) doit repartir masqué, même si la section avait été
        affichée juste avant (sur une fenêtre précédente, dans ce test
        simulé par un second appel sur un ca_tab neuf)."""
        self.win._build_ca_tab()
        self.win._on_toggle_personal_code_section()
        self.assertTrue(self.win._personal_code_section_visible)

        for child in self.ca_tab.winfo_children():
            child.destroy()
        self.win._build_ca_tab()

        self.assertFalse(self.win._personal_code_section_visible)
        self.assertFalse(self._grid_visible(self.win.personal_code_title))

    def test_raccourci_lie_en_majuscule_et_minuscule(self):
        """Même précaution multiplateforme que _bind_voice_command_
        shortcuts (voir sa docstring, constatée sous Windows) : lié en
        MAJUSCULE et en minuscule — bind_all en mode "requête" (sans
        fonction) renvoie une chaîne non vide si un script est lié."""
        self.win._bind_personal_code_section_shortcut()
        self.assertTrue(self.win.bind_all("<Control-Shift-P>"))
        self.assertTrue(self.win.bind_all("<Control-Shift-p>"))

    def test_aucun_conflit_avec_les_raccourcis_existants(self):
        """Vérification explicitement demandée : les raccourcis Ctrl+Maj+
        lettre de ce fichier sont tous construits via le même idiome
        `for key in ("X", "x"): self.bind_all(f"<Control-Shift-{key}>", ...)`
        (voir _bind_voice_command_shortcuts, J/C/T) — "P" ne doit
        apparaître dans un tel tuple qu'une seule fois (notre nouveau
        raccourci), jamais déjà réclamé par une autre fonctionnalité."""
        with open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
            encoding="utf-8",
        ) as f:
            source = f.read()
        import re
        letters_used = re.findall(r'for key in \("([A-Z])", "[a-z]"\):', source)
        self.assertEqual(
            letters_used.count("P"), 1,
            "la lettre P devrait apparaître exactement une fois parmi les tuples de raccourcis Ctrl+Maj+lettre",
        )
        # Non-régression : les raccourcis déjà connus (J/C/T) doivent
        # toujours être présents, inchangés par cet ajout.
        for letter in ("J", "C", "T"):
            self.assertIn(letter, letters_used)


class NoBypassStructuralTest(unittest.TestCase):
    """Vérifications structurelles (source) pour des propriétés difficiles
    à observer de façon fiable via la seule API Tk dans ce harnais."""

    def test_masquage_utilise_grid_remove_jamais_grid_forget(self):
        """.grid_forget() recherché comme APPEL (parenthèse ouvrante) —
        jamais le simple mot, qui apparaît légitimement dans la
        docstring de la fonction pour expliquer ce choix."""
        source = inspect.getsource(main.App._set_personal_code_section_visible)
        self.assertIn(".grid_remove(", source)
        self.assertNotIn(".grid_forget(", source)

    def test_toggle_verifie_longlet_ca_avant_toute_action(self):
        source = inspect.getsource(main.App._on_toggle_personal_code_section)
        idx_check = source.index("_is_ca_tab_active")
        idx_toggle = source.index("_set_personal_code_section_visible")
        self.assertLess(idx_check, idx_toggle)


if __name__ == "__main__":
    unittest.main()
