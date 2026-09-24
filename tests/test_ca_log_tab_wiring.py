# -*- coding: utf-8 -*-
"""Réorganisation visuelle du contrôle à distance (demande du
2026-09-22) : le bloc "Contrôle à distance (téléphone) / Téléphones
autorisés / Permissions DIRTO" quitte l'onglet "Paramètres" pour son
propre onglet "CA/LOG" — déplacement/réorganisation UNIQUEMENT, aucune
logique de sécurité Phase 4 réécrite.

Le 2026-09-24, ce même onglet "CA/LOG" a été à son tour scindé en deux
onglets DISTINCTS et INDÉPENDANTS : "CA" (contrôle à distance/
téléphones/permissions DIRTO — présentation VALIDÉE visuellement,
inchangée) et "LOG" (nouvel espace propre, recevant les critères de
recherche du futur Journal des actions). Ce fichier couvre UNIQUEMENT
cette séparation structurelle — depuis le chantier "LOG", Phase 2, LE
MÊME JOUR, l'onglet LOG est devenu réellement fonctionnel (recherche,
filtres, export, détail) : cette couverture-là vit dans tests/test_log_
tab_functional.py, jamais ici. Ce fichier a été adapté en conséquence
(harnais redirigé vers un action_log temporaire, boutons désormais
attendus câblés), sans rien retirer de sa couverture structurelle
d'origine.

Trois volets complémentaires :

1. TabsWiringStructurelTest — analyse du CODE SOURCE (comme tests/
   test_remote_device_popup_window.py::SettingsGridUntouchedTest, même
   principe) : _build_settings_tab ne construit plus AUCUN widget de ce
   bloc (jamais de doublon, jamais un second état indépendant),
   _build_ca_tab construit tout le bloc contrôle à distance mais plus
   AUCUN critère de recherche LOG, _build_log_tab construit les
   critères LOG en réutilisant _build_log_search_criteria_placeholder,
   et _build_tabs ajoute bien les onglets "CA" et "LOG" (dans cet
   ordre, juste après "Statistiques" et avant "Paramètres") en
   appelant les deux méthodes de construction correspondantes.

2. CaTabRealBuildTest — construction RÉELLE de _build_ca_tab sur une
   racine Tk réelle (même principe que tests/test_dirto_permissions_
   widget.py) : vérifie que les widgets du bloc contrôle à distance
   existent, portent les MÊMES noms d'attributs qu'avant ce chantier
   (remote_control_enabled_var, remote_control_code_lbl,
   remote_devices_container, remote_dirto_container...), sont bien
   enfants de self.ca_tab (jamais de self.settings_tab), que les deux
   zones (gauche/droite) restent bien CÔTE À CÔTE au même niveau
   vertical, et qu'AUCUN widget du LOG (log_date_from_var, etc.) n'est
   plus construit par cette méthode.

3. LogTabRealBuildTest — construction RÉELLE de _build_log_tab (action_
   log redirigé vers un fichier temporaire, voir setUp) : vérifie que
   les 9 contrôles du Journal des actions existent bien, réutilisent
   EXACTEMENT les mêmes variables/widgets qu'à la séparation (jamais
   dupliqués), que les 3 boutons sont désormais câblés (Phase 2), et
   que Réinitialiser/Rechercher/Exporter restent sur la même ligne dans
   cet ordre."""
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

import action_log  # noqa: E402
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

# Attributs self.xxx propres aux critères de recherche du LOG (voir
# _build_log_search_criteria_placeholder) — doivent désormais être
# assignés UNIQUEMENT par _build_log_tab, plus jamais par _build_ca_tab.
_LOG_CRITERIA_ATTRS = frozenset({
    "log_date_from_var", "log_date_to_var", "log_tournament_var",
    "log_user_var", "log_function_var", "log_player_var",
    "log_reset_btn", "log_search_btn", "log_export_btn",
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


class TabsWiringStructurelTest(unittest.TestCase):
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
        """Coeur de la demande d'origine : "le bloc contrôle à distance
        ne doit plus apparaître dans Paramètres ; aucun doublon des
        widgets"."""
        func = self._find_method("App", "_build_settings_tab")
        leaked = _assigned_self_attrs(func) & _REMOTE_BLOCK_ATTRS
        self.assertFalse(leaked, f"_build_settings_tab assigne encore : {sorted(leaked)}")
        self.assertNotIn(
            "_build_remote_dirto_permissions_widgets", _called_method_names(func),
            "_build_settings_tab ne doit plus appeler _build_remote_dirto_permissions_widgets",
        )

    def test_build_ca_tab_existe_et_construit_bien_tout_le_bloc_remote(self):
        func = self._find_method("App", "_build_ca_tab")
        missing = _REMOTE_BLOCK_ATTRS - _assigned_self_attrs(func)
        self.assertFalse(missing, f"_build_ca_tab ne construit pas : {sorted(missing)}")
        self.assertIn(
            "_build_remote_dirto_permissions_widgets", _called_method_names(func),
            "_build_ca_tab doit construire les permissions DIRTO",
        )

    def test_build_ca_tab_ne_construit_plus_les_criteres_log(self):
        """Coeur du chantier de séparation : les critères de recherche
        LOG doivent avoir quitté l'onglet CA."""
        func = self._find_method("App", "_build_ca_tab")
        leaked = _assigned_self_attrs(func) & _LOG_CRITERIA_ATTRS
        self.assertFalse(leaked, f"_build_ca_tab assigne encore des critères LOG : {sorted(leaked)}")
        self.assertNotIn(
            "_build_log_search_criteria_placeholder", _called_method_names(func),
            "_build_ca_tab ne doit plus appeler _build_log_search_criteria_placeholder",
        )

    def test_build_log_tab_existe_et_construit_les_criteres_log(self):
        func = self._find_method("App", "_build_log_tab")
        self.assertIn(
            "_build_log_search_criteria_placeholder", _called_method_names(func),
            "_build_log_tab doit réutiliser _build_log_search_criteria_placeholder",
        )

    def test_build_tabs_ajoute_les_onglets_ca_et_log_et_les_appelle(self):
        func = self._find_method("App", "_build_tabs")
        func_source = ast.get_source_segment(self.source, func) or ""
        self.assertIn('text="CA"', func_source)
        self.assertIn('text="LOG"', func_source)
        self.assertIn("self._build_ca_tab()", func_source)
        self.assertIn("self._build_log_tab()", func_source)
        self.assertIn("self._build_settings_tab()", func_source)

    def test_ordre_des_onglets_statistiques_ca_log_parametres(self):
        """Ordre demandé : "...Répertoire / Statistiques / CA / LOG /
        Paramètres" — vérifié via l'ordre RÉEL des appels notebook.add
        dans le code source (jamais un ordre supposé)."""
        func = self._find_method("App", "_build_tabs")
        func_source = ast.get_source_segment(self.source, func) or ""
        pos_stats = func_source.index('text="Statistiques"')
        pos_ca = func_source.index('text="CA"')
        pos_log = func_source.index('text="LOG"')
        pos_params = func_source.index('text="Paramètres"')
        self.assertLess(pos_stats, pos_ca, "CA doit venir après Statistiques")
        self.assertLess(pos_ca, pos_log, "LOG doit venir après CA")
        self.assertLess(pos_log, pos_params, "Paramètres doit venir après LOG")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class CaTabRealBuildTest(unittest.TestCase):
    """Construction RÉELLE de _build_ca_tab (racine Tk réelle greffée
    des vraies méthodes App, même principe que tests/test_dirto_
    permissions_widget.py) — jamais de vraie App complète (inutilement
    lourd), seulement ce qui est nécessaire à CETTE méthode."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self._tmp = tempfile.TemporaryDirectory(prefix="ca_tab_test_")
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
        self.ca_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.ca_tab, text="CA")
        self.notebook.select(self.ca_tab)
        self.win.notebook = self.notebook
        self.win.ca_tab = self.ca_tab

        for name in (
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
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        # @staticmethod sur App : déjà des fonctions "nues" une fois
        # accédées via la classe, jamais de types.MethodType (même
        # principe que tests/test_dirto_permissions_widget.py).
        for name in ("_remote_dirto_signature", "_remote_devices_signature"):
            setattr(self.win, name, getattr(main.App, name))

        self.addCleanup(lambda: cleanup_tk(
            self, "root", "win", "notebook", "ca_tab",
            "remote_devices_container", "remote_dirto_container",
        ))

    def test_widgets_construits_avec_les_memes_noms_dattributs_quavant(self):
        self.win._build_ca_tab()
        for attr in (
            "remote_control_enabled_var", "remote_control_code_lbl",
            "remote_control_status_lbl", "remote_devices_container",
            "remote_dirto_container", "remote_dirto_admin_combo",
            "remote_dirto_user_combo", "remote_dirto_permission_vars",
        ):
            self.assertTrue(hasattr(self.win, attr), f"attribut manquant : {attr}")

    def test_conteneurs_enfants_de_ca_tab_jamais_de_settings_tab(self):
        """Non-régression directe de la demande d'origine : ces widgets
        doivent être ancrés dans CA, pas dans un onglet Paramètres qui
        n'existe même pas dans ce harnais réduit."""
        self.win._build_ca_tab()

        def _is_descendant_of(widget, ancestor):
            w = widget
            while w is not None:
                if w == ancestor:
                    return True
                w = w.master
            return False

        self.assertTrue(_is_descendant_of(self.win.remote_devices_container, self.ca_tab))
        self.assertTrue(_is_descendant_of(self.win.remote_dirto_container, self.ca_tab))

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
        sans jamais faire calculer de géométrie réelle par Tcl. Cette
        présentation a déjà été validée visuellement (voir la demande du
        2026-09-24, "ne redessine pas cette partie") — ce test garantit
        seulement qu'elle reste INTACTE après la séparation LOG."""
        self.win._build_ca_tab()
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

    def test_colonne_droite_permissions_dirto_strictement_intacte(self):
        """"Ne modifie pas sans nécessité le positionnement validé de la
        colonne droite des permissions" (demande explicite du
        2026-09-24) : la colonne droite ne doit contenir QUE son titre
        et son conteneur, rien du LOG n'y a jamais été ajouté."""
        self.win._build_ca_tab()
        right = self.win.remote_dirto_container.master
        right_slaves = right.grid_slaves()
        self.assertEqual(
            set(right_slaves), {self.win.remote_dirto_container, right.grid_slaves(row=0)[0]},
            "la colonne droite (Permissions DIRTO) ne doit contenir que son titre et son conteneur",
        )

    def test_ca_tab_ne_construit_plus_aucun_widget_du_log(self):
        """Coeur du chantier de séparation : plus aucun des 9 contrôles
        préparatoires du LOG ne doit être construit par _build_ca_tab —
        ils vivent désormais exclusivement dans _build_log_tab."""
        self.win._build_ca_tab()
        for attr in (
            "log_date_from_var", "log_date_to_var", "log_tournament_var",
            "log_user_var", "log_function_var", "log_player_var",
            "log_reset_btn", "log_search_btn", "log_export_btn",
        ):
            self.assertFalse(hasattr(self.win, attr), f"attribut LOG ne devrait plus exister sur CA : {attr}")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class LogTabRealBuildTest(unittest.TestCase):
    """Construction RÉELLE de _build_log_tab — devenue FONCTIONNELLE au
    chantier "LOG", Phase 2 (2026-09-24) : _build_log_tab charge
    désormais automatiquement les opérations récentes à la construction
    (voir _refresh_log_tab), d'où le besoin de rediriger action_log vers
    un fichier temporaire ici (même principe que tests/test_action_log.
    py) — jamais le vrai ~/.poker_tournament/actions_log.sqlite3. La
    couverture FONCTIONNELLE complète (filtres, tableau, export, retour
    sur l'onglet, détail au double-clic) vit dans tests/test_log_tab_
    functional.py ; ce fichier-ci ne vérifie que la STRUCTURE issue du
    chantier de séparation CA/LOG (widgets présents, bien ancrés dans
    log_tab, boutons câblés)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="log_tab_wiring_test_")
        self.addCleanup(self._tmp.cleanup)
        al_patcher = patch.object(
            action_log, "_log_path",
            return_value=os.path.join(self._tmp.name, "actions_log.sqlite3"),
        )
        self.addCleanup(al_patcher.stop)
        al_patcher.start()

        self.root = tk.Tk()
        self.root.withdraw()

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True)
        self.log_tab = ttk.Frame(self.notebook)
        self.notebook.add(self.log_tab, text="LOG")
        self.notebook.select(self.log_tab)

        self.win = self.root
        self.win.notebook = self.notebook
        self.win.log_tab = self.log_tab

        for name in (
            "_build_log_tab", "_build_log_search_criteria_placeholder",
            "_refresh_log_tab", "_refresh_log_filter_choices",
            "_parse_log_date_field", "_current_log_filter_values", "_populate_log_tree",
            "_on_log_search", "_on_log_reset", "_on_log_export",
            "_on_log_row_double_click", "_show_log_detail_window",
            "_show_log_date_picker", "_confirm_log_purge", "_log_purge_filter_summary",
            "_on_log_purge",
        ):
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in ("_build_log_tournament_label_maps", "_format_log_ts", "_log_count_label_text"):
            setattr(self.win, name, getattr(main.App, name))
        self.win._LOG_CALENDAR_MONTH_NAMES_FR = main.App._LOG_CALENDAR_MONTH_NAMES_FR

        self.addCleanup(lambda: cleanup_tk(self, "root", "win", "notebook", "log_tab"))

    def test_criteres_log_contiennent_les_9_controles_et_les_4_boutons_sont_cables(self):
        """Les 9 widgets d'origine existent, réutilisent les MÊMES
        variables qu'à la séparation CA/LOG (jamais dupliquées) — et,
        depuis la Phase 2 (complétée par la correction "Purger" du
        2026-09-24), les 4 boutons ont désormais une VRAIE `command`
        câblée (Réinitialiser/Rechercher/Exporter/Purger), plus aucun
        n'est inerte."""
        self.win._build_log_tab()
        for attr in (
            "log_date_from_var", "log_date_to_var", "log_tournament_var",
            "log_user_var", "log_function_var", "log_player_var",
            "log_reset_btn", "log_search_btn", "log_export_btn",
        ):
            self.assertTrue(hasattr(self.win, attr), f"attribut manquant : {attr}")
        # ttk.Button avec command= renvoie un nom de commande Tcl généré
        # (chaîne non vide) — jamais "" comme avant la Phase 2.
        self.assertNotEqual(self.win.log_reset_btn.cget("command"), "")
        self.assertNotEqual(self.win.log_search_btn.cget("command"), "")
        self.assertNotEqual(self.win.log_export_btn.cget("command"), "")
        self.assertTrue(hasattr(self.win, "log_purge_btn"), "attribut manquant : log_purge_btn")
        self.assertNotEqual(self.win.log_purge_btn.cget("command"), "")
        self.assertEqual(self.win.log_tournament_var.get(), "Tous")
        self.assertEqual(self.win.log_user_var.get(), "Tous")
        self.assertEqual(self.win.log_function_var.get(), "Toutes")
        self.assertEqual(self.win.log_player_var.get(), "Tous")

    def test_reinitialiser_rechercher_exporter_purger_sur_la_meme_ligne_dans_cet_ordre(self):
        """Précision du 2026-09-24 : les 4 boutons doivent être sur la
        MÊME ligne, dans l'ordre Réinitialiser -> Rechercher -> Exporter
        -> Purger (Purger ajouté par la correction du même jour, séparé
        visuellement des 3 premiers par un séparateur vertical)."""
        self.win._build_log_tab()
        self.assertIs(
            self.win.log_reset_btn.master, self.win.log_search_btn.master,
            "Réinitialiser et Rechercher doivent être sur la même ligne",
        )
        self.assertIs(
            self.win.log_search_btn.master, self.win.log_export_btn.master,
            "Exporter doit être sur la même ligne que Rechercher",
        )
        self.assertIs(
            self.win.log_export_btn.master, self.win.log_purge_btn.master,
            "Purger doit être sur la même ligne que les 3 autres boutons",
        )
        siblings = self.win.log_reset_btn.master.pack_slaves()
        positions = {
            "reset": siblings.index(self.win.log_reset_btn),
            "search": siblings.index(self.win.log_search_btn),
            "export": siblings.index(self.win.log_export_btn),
            "purge": siblings.index(self.win.log_purge_btn),
        }
        self.assertLess(positions["reset"], positions["search"])
        self.assertLess(positions["search"], positions["export"])
        self.assertLess(positions["export"], positions["purge"])

    def test_boutons_calendrier_du_et_au_existent(self):
        """Correction du 2026-09-24 : un bouton 📅 accompagne chacun des
        champs Du/Au (voir _show_log_date_picker)."""
        self.win._build_log_tab()

        def _count_calendar_buttons(widget):
            count = 0
            for c in widget.winfo_children():
                if isinstance(c, ttk.Button) and c.cget("text") == "📅":
                    count += 1
                count += _count_calendar_buttons(c)
            return count

        self.assertEqual(_count_calendar_buttons(self.log_tab), 2, "un bouton 📅 pour Du et un pour Au")

    def test_criteres_log_enfants_de_log_tab_jamais_de_ca_tab(self):
        self.win._build_log_tab()

        def _is_descendant_of(widget, ancestor):
            w = widget
            while w is not None:
                if w == ancestor:
                    return True
                w = w.master
            return False

        self.assertTrue(_is_descendant_of(self.win.log_reset_btn, self.log_tab))

    def test_construction_des_widgets_najoute_aucun_appel_direct_a_action_log(self):
        """SQL centralisé dans action_log.py (demande explicite du
        chantier "LOG", Phase 2) : les fonctions de CONSTRUCTION de
        widgets (_build_log_tab/_build_log_search_criteria_placeholder)
        ne doivent jamais appeler action_log.xxx(...) DIRECTEMENT —
        seules _refresh_log_tab/_refresh_log_filter_choices (methods
        séparées, couvertes par tests/test_log_tab_functional.py) le
        font. Vérifié via l'AST (appels RÉELS uniquement) plutôt qu'une
        recherche de texte brut, qui trouverait aussi de simples
        mentions du module dans les docstrings — jamais un remplacement
        aveugle."""
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            source = f.read()
        tree = ast.parse(source, filename=main_py)
        for method_name in ("_build_log_tab", "_build_log_search_criteria_placeholder"):
            func = next(
                n for n in ast.walk(tree)
                if isinstance(n, ast.FunctionDef) and n.name == method_name
            )
            calls_action_log = any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name) and n.func.value.id == "action_log"
                for n in ast.walk(func)
            )
            self.assertFalse(calls_action_log, f"{method_name} ne doit pas appeler action_log.* directement")


if __name__ == "__main__":
    unittest.main()
