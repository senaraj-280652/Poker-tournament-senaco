# -*- coding: utf-8 -*-
"""Réorganisation visuelle du contrôle à distance (demande du
2026-09-22) : le bloc "Contrôle à distance (téléphone) / Téléphones
autorisés / Permissions DIRTO" quitte l'onglet "Paramètres" pour son
propre onglet "CA/LOG" — déplacement/réorganisation UNIQUEMENT, aucune
logique de sécurité Phase 4 réécrite.

Deux volets complémentaires :

1. SettingsCaLogWiringStructurelTest — analyse du CODE SOURCE (comme
   tests/test_remote_device_popup_window.py::SettingsGridUntouchedTest,
   même principe) : _build_settings_tab ne construit plus AUCUN widget
   de ce bloc (jamais de doublon, jamais un second état indépendant),
   _build_ca_log_tab les construit tous, _build_tabs ajoute bien
   l'onglet "CA/LOG" et appelle _build_ca_log_tab().

2. CaLogTabRealBuildTest — construction RÉELLE de _build_ca_log_tab sur
   une racine Tk réelle (même principe que tests/test_dirto_permissions_
   widget.py) : vérifie que les widgets existent, portent les MÊMES noms
   d'attributs qu'avant ce chantier (remote_control_enabled_var,
   remote_control_code_lbl, remote_devices_container, remote_dirto_
   container...), sont bien enfants de self.ca_log_tab (jamais de
   self.settings_tab), et que les deux zones (gauche/droite) sont bien
   CÔTE À CÔTE au même niveau vertical."""
import ast
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk
from tkinter import ttk

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


# Attributs self.xxx propres au bloc contrôle à distance/téléphones/
# permissions DIRTO — vérifiés via une VRAIE assignation AST (jamais une
# recherche de texte brut, qui trouverait aussi de simples commentaires
# mentionnant légitimement l'autre onglet en référence historique).
_REMOTE_BLOCK_ATTRS = frozenset({
    "remote_control_enabled_var",
    "remote_control_code_lbl",
    "remote_control_status_lbl",
    "remote_devices_container",
    "remote_dirto_container",
})


def _assigned_self_attrs(func):
    """Noms d'attributs self.xxx = ... assignés dans `func` (AST)."""
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self":
                    names.add(target.attr)
    return names


def _called_method_names(func):
    return {
        n.func.attr for n in ast.walk(func)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }


class SettingsCaLogWiringStructurelTest(unittest.TestCase):
    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=main_py)

    def _find_method(self, class_name, method_name):
        cls = next(n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef) and n.name == class_name)
        return next(
            n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def test_build_settings_tab_ne_construit_plus_aucun_widget_du_bloc(self):
        """Coeur de la demande : "le bloc contrôle à distance ne doit
        plus apparaître dans Paramètres ; aucun doublon des widgets"."""
        func = self._find_method("App", "_build_settings_tab")
        leaked = _assigned_self_attrs(func) & _REMOTE_BLOCK_ATTRS
        self.assertFalse(leaked, f"_build_settings_tab assigne encore : {sorted(leaked)}")
        self.assertNotIn(
            "_build_remote_dirto_permissions_widgets", _called_method_names(func),
            "_build_settings_tab ne doit plus appeler _build_remote_dirto_permissions_widgets",
        )

    def test_build_ca_log_tab_existe_et_construit_bien_tout_le_bloc(self):
        func = self._find_method("App", "_build_ca_log_tab")
        missing = _REMOTE_BLOCK_ATTRS - _assigned_self_attrs(func)
        self.assertFalse(missing, f"_build_ca_log_tab ne construit pas : {sorted(missing)}")
        self.assertIn(
            "_build_remote_dirto_permissions_widgets", _called_method_names(func),
            "_build_ca_log_tab doit construire les permissions DIRTO",
        )

    def test_build_tabs_ajoute_longlet_ca_log_et_l_appelle(self):
        func = self._find_method("App", "_build_tabs")
        func_source = ast.get_source_segment(self.source, func) or ""
        self.assertIn('text="CA/LOG"', func_source)
        self.assertIn("self._build_ca_log_tab()", func_source)
        # L'ordre réel importe peu ici (Paramètres pourrait rester avant
        # ou après CA/LOG sans rien casser) — seule l'EXISTENCE des deux
        # appels compte.
        self.assertIn("self._build_settings_tab()", func_source)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class CaLogTabRealBuildTest(unittest.TestCase):
    """Construction RÉELLE de _build_ca_log_tab (racine Tk réelle
    greffée des vraies méthodes App, même principe que tests/test_dirto_
    permissions_widget.py) — jamais de vraie App complète (inutilement
    lourd), seulement ce qui est nécessaire à CETTE méthode."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self._tmp = tempfile.TemporaryDirectory(prefix="ca_log_tab_test_")
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
        # PACKÉ et sélectionné (jamais laissé "orphelin") : un update_
        # idletasks()/winfo_x()/winfo_y() sur un widget d'un onglet
        # jamais réellement intégré à la hiérarchie de fenêtres (racine
        # withdraw() n'empêche PAS le calcul de géométrie interne, mais
        # un Notebook jamais packé, si) s'est avéré provoquer un crash
        # natif Tcl/Tk direct (Segmentation fault) lors du premier test
        # de ce fichier — corrigé ici, jamais un problème du code de
        # production lui-même.
        self.notebook.pack(fill="both", expand=True)
        self.ca_log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.ca_log_tab, text="CA/LOG")
        self.notebook.select(self.ca_log_tab)
        self.win.notebook = self.notebook
        self.win.ca_log_tab = self.ca_log_tab

        for name in (
            "_build_ca_log_tab",
            "_build_log_search_criteria_placeholder",
            "_refresh_remote_control_status",
            "_refresh_remote_devices_panel",
            "_build_remote_dirto_permissions_widgets",
            "_refresh_remote_dirto_permissions_panel",
            "_on_remote_dirto_user_selected",
            "_on_grant_remote_dirto_permissions",
            "_on_revoke_remote_dirto_permissions",
            "_on_remote_control_toggle",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        # @staticmethod sur App : déjà des fonctions "nues" une fois
        # accédées via la classe, jamais de types.MethodType (même
        # principe que tests/test_dirto_permissions_widget.py).
        for name in ("_remote_dirto_signature", "_remote_devices_signature"):
            setattr(self.win, name, getattr(main.App, name))

        self.addCleanup(lambda: cleanup_tk(
            self, "root", "win", "notebook", "ca_log_tab",
            "remote_devices_container", "remote_dirto_container",
        ))

    def test_widgets_construits_avec_les_memes_noms_dattributs_quavant(self):
        self.win._build_ca_log_tab()
        for attr in (
            "remote_control_enabled_var", "remote_control_code_lbl",
            "remote_control_status_lbl", "remote_devices_container",
            "remote_dirto_container", "remote_dirto_admin_combo",
            "remote_dirto_user_combo", "remote_dirto_permission_vars",
        ):
            self.assertTrue(hasattr(self.win, attr), f"attribut manquant : {attr}")

    def test_conteneurs_enfants_de_ca_log_jamais_de_settings_tab(self):
        """Non-régression directe de la demande : ces widgets doivent
        être ancrés dans CA/LOG, pas dans un onglet Paramètres qui
        n'existe même pas dans ce harnais réduit."""
        self.win._build_ca_log_tab()

        def _is_descendant_of(widget, ancestor):
            w = widget
            while w is not None:
                if w == ancestor:
                    return True
                w = w.master
            return False

        self.assertTrue(_is_descendant_of(self.win.remote_devices_container, self.ca_log_tab))
        self.assertTrue(_is_descendant_of(self.win.remote_dirto_container, self.ca_log_tab))

    def test_deux_zones_cote_a_cote_au_meme_niveau(self):
        """"Utiliser toute la largeur disponible avec deux zones côte à
        côte [...] au même niveau vertical" : vérifié SANS update_
        idletasks()/winfo_x()/winfo_y() — voir tests/test_clock_projo_
        movement_alert_drag.py, qui documente en détail un crash natif
        Tcl/Tk PRÉ-EXISTANT et systématique (reproduit indépendamment de
        tout chantier) dès qu'un update_idletasks() est appelé sur un
        VRAI widget dans un process où un tk.Tk() a déjà été créé PUIS
        détruit plus tôt — exactement la sonde _TK_AVAILABLE de CE
        fichier, comme dans toute la suite. La métadonnée pack() elle-
        même (side="left", parent commun, ordre d'empilement) suffit à
        garantir structurellement "côte à côte, même niveau vertical",
        sans jamais faire calculer de géométrie réelle par Tcl."""
        self.win._build_ca_log_tab()
        # Les conteneurs eux-mêmes ont des hauteurs différentes au-dessus
        # (titre + tooltip) : leur MASTER commun ("left"/"right", tous
        # deux enfants directs du même "top") est ce qui doit être
        # comparé pour "même niveau, gauche puis droite".
        left = self.win.remote_devices_container.master
        right = self.win.remote_dirto_container.master
        self.assertIs(left.master, right.master, "les deux zones doivent partager le même parent (\"top\")")
        self.assertEqual(left.pack_info()["side"], "left")
        self.assertEqual(right.pack_info()["side"], "left")
        # pack() empile ses enfants dans l'ordre d'appel : "left" doit
        # avoir été empaqueté AVANT "right" pour apparaître à sa gauche.
        siblings = left.master.pack_slaves()
        self.assertLess(
            siblings.index(left), siblings.index(right),
            "la zone Permissions DIRTO doit être empaquetée APRÈS (donc à droite de) "
            "la zone Contrôle à distance",
        )

    def test_criteres_log_places_sous_telephones_a_gauche_jamais_a_droite(self):
        """Demande explicite du 2026-09-24 ("préparation visuelle de la
        partie LOG") : le trait de séparation et les critères de
        recherche doivent apparaître SOUS "Téléphones autorisés",
        colonne GAUCHE ("left") — jamais sous "Permissions DIRTO",
        colonne DROITE ("right"), qui doit rester par ailleurs
        totalement intacte (mêmes attributs qu'avant ce chantier).
        Vérifié via grid_slaves(row=...) (métadonnée pure, jamais de
        géométrie réellement calculée — voir la remarque sur le crash
        Tcl/Tk pré-existant dans test_deux_zones_cote_a_cote_au_meme_
        niveau ci-dessus)."""
        self.win._build_ca_log_tab()
        left = self.win.remote_devices_container.master
        right = self.win.remote_dirto_container.master

        devices_row = int(self.win.remote_devices_container.grid_info()["row"])
        # Le trait ET les critères doivent tomber sur des lignes de
        # grille STRICTEMENT après "Téléphones autorisés", toutes deux
        # dans "left".
        rows_after_devices_left = set()
        for row in range(devices_row + 1, devices_row + 5):
            if left.grid_slaves(row=row):
                rows_after_devices_left.add(row)
        self.assertTrue(
            rows_after_devices_left,
            "aucun widget trouvé sous Téléphones autorisés, colonne gauche",
        )

        # Colonne DROITE ("Permissions DIRTO") : rigoureusement intacte —
        # seuls les deux widgets déjà connus (titre + conteneur), jamais
        # rien du LOG ajouté à côté ou en dessous.
        right_slaves = right.grid_slaves()
        self.assertEqual(
            set(right_slaves), {self.win.remote_dirto_container, right.grid_slaves(row=0)[0]},
            "la colonne droite (Permissions DIRTO) ne doit contenir que son titre et son conteneur",
        )

    def test_criteres_log_contiennent_les_9_controles_demandes_sans_action_cablee(self):
        """Contrôles PRÉPARATOIRES uniquement (demande explicite,
        complétée le 2026-09-24 avec le bouton "Exporter") : les 9
        widgets attendus existent, mais aucun bouton n'a de `command`
        câblée — aucune recherche réelle, aucune écriture de LOG, aucun
        export de fichier."""
        self.win._build_ca_log_tab()
        for attr in (
            "log_date_from_var", "log_date_to_var", "log_tournament_var",
            "log_user_var", "log_function_var", "log_player_var",
            "log_reset_btn", "log_search_btn", "log_export_btn",
        ):
            self.assertTrue(hasattr(self.win, attr), f"attribut manquant : {attr}")
        # ttk.Button sans command= renvoie une chaîne vide pour cette
        # option — jamais un nom de commande Tcl généré pour une
        # fonction Python réelle.
        self.assertEqual(self.win.log_reset_btn.cget("command"), "")
        self.assertEqual(self.win.log_search_btn.cget("command"), "")
        self.assertEqual(self.win.log_export_btn.cget("command"), "")
        self.assertEqual(self.win.log_tournament_var.get(), "Tous")
        self.assertEqual(self.win.log_user_var.get(), "Tous")
        self.assertEqual(self.win.log_function_var.get(), "Toutes")
        self.assertEqual(self.win.log_player_var.get(), "Tous")

    def test_reinitialiser_rechercher_exporter_sur_la_meme_ligne_dans_cet_ordre(self):
        """Précision du 2026-09-24 : les 3 boutons doivent être sur la
        MÊME ligne, dans l'ordre Réinitialiser -> Rechercher -> Exporter
        (donc Rechercher à droite de Réinitialiser, Exporter à droite de
        Rechercher)."""
        self.win._build_ca_log_tab()
        self.assertIs(
            self.win.log_reset_btn.master, self.win.log_search_btn.master,
            "Réinitialiser et Rechercher doivent être sur la même ligne",
        )
        self.assertIs(
            self.win.log_search_btn.master, self.win.log_export_btn.master,
            "Exporter doit être sur la même ligne que Rechercher",
        )
        siblings = self.win.log_reset_btn.master.pack_slaves()
        self.assertEqual(
            siblings, [self.win.log_reset_btn, self.win.log_search_btn, self.win.log_export_btn],
            "ordre attendu : Réinitialiser, Rechercher, Exporter (de gauche à droite)",
        )


if __name__ == "__main__":
    unittest.main()
