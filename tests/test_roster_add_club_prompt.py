# -*- coding: utf-8 -*-
"""Répertoire — proposition immédiate du club à la création d'un membre
(demande du 2026-09-16) : main.py: RosterManagerDialog._add() crée
désormais le membre SANS club, puis ouvre immédiatement "Club de {name}"
(ask_club_dialog, déjà existante — réutilisée telle quelle, jamais
dupliquée) pré-remplie du club par défaut réglé dans Paramètres ("Nom du
Club"). Règle confirmée avec l'utilisateur :
- valider (même avec le club par défaut inchangé, ou un club modifié) ->
  enregistre le club choisi ;
- fermer/annuler -> le membre reste dans le répertoire, SANS AUCUN club
  attribué (jamais le défaut appliqué en douce).

Ajout : ask_club_dialog mémorise désormais sa position flottante (mêmes
principes que App._ask_eliminator_position — voir tests/test_ask_
eliminator_window_position.py, dont ce fichier reprend la même
structure : jamais de fenêtre modale réellement ouverte (elle a
grab_set()+wait_window(), risque de segfault Tcl/Tk déjà documenté dans
ce harnais précis — voir aussi le diagnostic du 2026-09-16 sur
update_idletasks()/bbox()), seuls les mécanismes UNITAIRES sont testés
avec de vrais petits widgets Tk, jamais event_generate().

Ajout (même demande, complément) : le nouveau membre est désormais
sélectionné/rendu visible (roster_tree.selection_set()/see(), le VRAI
Treeview de RosterManagerDialog — voir _select_and_reveal) après le
flux "Club de XXX" complet, que la fenêtre ait été validée ou annulée.
Voir RosterSelectsNewMemberTest, qui exécute la VRAIE _add()/_refresh()
de RosterManagerDialog (jamais réimplémentées) sur un harnais minimal
mais réel (roster_tree/club_summary_tree/summary_box/preview_lbl de
vrais widgets Tk — aucun de ces widgets ni selection_set()/see() n'est
impliqué dans le risque de segfault documenté ci-dessus, propre à
update_idletasks()/bbox() sur un Treeview non mappé)."""
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

import export_prefs  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


class _FakeVar:
    def __init__(self, value=""):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeRosterDialog:
    """Doublure minimale de RosterManagerDialog pour _add() : new_name_
    var, _refresh() et _select_and_reveal() sont lus/appelés par cette
    méthode (ce dernier ajouté le 2026-09-16 — sélection/visibilité du
    nouveau membre dans roster_tree une fois le flux "Club de XXX"
    terminé). Ici, _select_and_reveal() n'a volontairement PAS de vrai
    roster_tree derrière (RosterSelectsNewMemberTest, plus bas, couvre
    ce comportement réel sur un harnais Tk) : cette classe reste centrée
    sur la création/le club, se contente d'enregistrer les noms reçus.
    ask_club_dialog est appelée avec `self` comme `master`, mais jamais
    utilisée comme un vrai widget puisqu'elle est mockée dans les tests
    ci-dessous (jamais de fenêtre réellement ouverte)."""

    def __init__(self, initial_name=""):
        self.new_name_var = _FakeVar(initial_name)
        self.refresh_calls = 0
        self.selected_names = []

    def _refresh(self):
        self.refresh_calls += 1

    def _select_and_reveal(self, name):
        self.selected_names.append(name)


class RosterAddOpensClubPromptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_add_club_prompt_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        roster_patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(roster_patcher.stop)
        roster_patcher.start()

        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        prefs_patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

    def test_membre_cree_sans_club_avant_toute_reponse_a_la_fenetre(self):
        """La création (roster.add_to_roster) doit avoir eu lieu AVANT
        même que ask_club_dialog ne soit invoquée — vérifié en faisant
        planter ask_club_dialog volontairement : le membre doit malgré
        tout déjà exister, sans club."""
        dialog = _FakeRosterDialog("Alice")
        with patch.object(main, "ask_club_dialog", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                main.RosterManagerDialog._add(dialog)
        self.assertIn("Alice", roster.load_roster())
        self.assertEqual(roster.get_club("Alice"), "")

    def test_champ_nom_vide_apres_creation_et_refresh_appele(self):
        dialog = _FakeRosterDialog("Alice")
        with patch.object(main, "ask_club_dialog", return_value=None):
            main.RosterManagerDialog._add(dialog)
        self.assertEqual(dialog.new_name_var.get(), "")
        self.assertGreaterEqual(dialog.refresh_calls, 1)

    def test_ouvre_club_de_xxx_pre_rempli_du_club_par_defaut(self):
        export_prefs.save_value("club_name", "CPC")
        dialog = _FakeRosterDialog("Alice")
        with patch.object(main, "ask_club_dialog", return_value=None) as mock_dialog:
            main.RosterManagerDialog._add(dialog)
        mock_dialog.assert_called_once_with(dialog, title="Club de Alice", current_club="CPC")

    def test_annulation_laisse_le_membre_sans_club(self):
        export_prefs.save_value("club_name", "CPC")
        dialog = _FakeRosterDialog("Alice")
        with patch.object(main, "ask_club_dialog", return_value=None):
            main.RosterManagerDialog._add(dialog)
        self.assertIn("Alice", roster.load_roster())
        self.assertEqual(roster.get_club("Alice"), "")  # jamais "CPC" imposé en douce

    def test_validation_avec_le_defaut_enregistre_ce_club(self):
        export_prefs.save_value("club_name", "CPC")
        dialog = _FakeRosterDialog("Alice")
        with patch.object(main, "ask_club_dialog", return_value="CPC"):
            main.RosterManagerDialog._add(dialog)
        self.assertEqual(roster.get_club("Alice"), "CPC")

    def test_validation_avec_un_club_modifie_enregistre_le_nouveau_choix(self):
        export_prefs.save_value("club_name", "CPC")
        dialog = _FakeRosterDialog("Bob")
        with patch.object(main, "ask_club_dialog", return_value="Autre Club"):
            main.RosterManagerDialog._add(dialog)
        self.assertEqual(roster.get_club("Bob"), "Autre Club")

    def test_validation_avec_club_vide_explicitement_reste_vide(self):
        """Choisir explicitement un champ vide puis Valider (chaîne vide,
        pas None) doit être appliqué comme un choix délibéré — jamais
        distingué d'une case restée vide par défaut."""
        export_prefs.save_value("club_name", "CPC")
        dialog = _FakeRosterDialog("Chloé")
        with patch.object(main, "ask_club_dialog", return_value=""):
            main.RosterManagerDialog._add(dialog)
        self.assertEqual(roster.get_club("Chloé"), "")

    def test_nom_vide_ne_fait_rien(self):
        dialog = _FakeRosterDialog("   ")
        with patch.object(main, "ask_club_dialog") as mock_dialog:
            main.RosterManagerDialog._add(dialog)
        mock_dialog.assert_not_called()
        self.assertEqual(dialog.refresh_calls, 0)

    def test_membre_deja_existant_ne_duplique_pas_mais_propose_quand_meme_le_club(self):
        roster.add_to_roster("Alice", club="Ancien Club")
        dialog = _FakeRosterDialog("Alice")
        with patch.object(main, "ask_club_dialog", return_value="Nouveau Club") as mock_dialog:
            main.RosterManagerDialog._add(dialog)
        mock_dialog.assert_called_once()
        self.assertEqual(roster.load_roster().count("Alice"), 1)
        self.assertEqual(roster.get_club("Alice"), "Nouveau Club")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class AskClubDialogPositionUnitTest(unittest.TestCase):
    """Position mémorisée / hors écran / sauvegarde au déplacement — au
    niveau UNITAIRE (aucune fenêtre "Club de..." réellement ouverte,
    donc aucun risque lié à wait_window/grab_set — voir la docstring du
    module). Un seul tk.Tk() pour toute la classe (même précaution
    anti-flakiness Tcl/Tk qu'ailleurs dans cette suite). Fonctions
    MODULE-LEVEL (pas des méthodes d'App) : appelées directement,
    `master` passé explicitement."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : force gc.collect() sur le thread principal
        # après destroy(), avant qu'un test HTTP ultérieur ne puisse en
        # hériter par hasard.
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ask_club_position_test_")
        self.addCleanup(self._tmp.cleanup)
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_aucune_position_memorisee_par_defaut(self):
        self.assertIsNone(main._ask_club_dialog_position(self.root))

    def test_position_sauvegardee_et_relue(self):
        main._save_ask_club_dialog_position(222, 111)
        self.assertEqual(export_prefs.load_value("ask_club_window_x"), 222)
        self.assertEqual(export_prefs.load_value("ask_club_window_y"), 111)
        self.assertEqual(main._ask_club_dialog_position(self.root), (222, 111))

    def test_position_hors_ecran_ignoree(self):
        export_prefs.save_value("ask_club_window_x", 999999)
        export_prefs.save_value("ask_club_window_y", 999999)
        self.assertIsNone(main._ask_club_dialog_position(self.root))

    def test_position_non_numerique_ignoree(self):
        export_prefs.save_value("ask_club_window_x", "pas un nombre")
        export_prefs.save_value("ask_club_window_y", None)
        self.assertIsNone(main._ask_club_dialog_position(self.root))

    def test_configure_memorise_la_position(self):
        """Même convention que tests/test_ask_eliminator_window_
        position.py : un faux événement portant juste `.widget`, jamais
        un vrai <Configure> généré (event_generate a provoqué un
        segfault ailleurs dans cette suite) — PAS de update_idletasks()
        non plus (même mise en garde). `win` passé explicitement en
        second argument (CORRECTIF du 2026-09-17, même diagnostic que
        App._on_ask_eliminator_window_configure) : l'appelé ne fait plus
        confiance à `event.widget` seul."""
        fake_win = tk.Toplevel(self.root)
        self.addCleanup(fake_win.destroy)
        fake_win.geometry("+333+444")
        fake_event = type("FakeEvent", (), {"widget": fake_win})()

        main._on_ask_club_dialog_configure(fake_event, fake_win)

        self.assertEqual(export_prefs.load_value("ask_club_window_x"), fake_win.winfo_x())
        self.assertEqual(export_prefs.load_value("ask_club_window_y"), fake_win.winfo_y())

    def test_configure_dun_enfant_est_ignore(self):
        """CORRECTIF du 2026-09-17 (régression constatée sur Mac, même
        cause que pour "Qui a éliminé ce joueur ?" — voir tests/test_ask_
        eliminator_window_position.py) : un <Configure> dont `event.
        widget` est un ENFANT de la fenêtre (Label, Combobox — voir ask_
        club_dialog, empaquetés juste après le bind) remonte pourtant
        jusqu'à ce gestionnaire via les bindtags de `win` — winfo_x()/
        winfo_y() de cet enfant renvoient sa position relative à SON
        PARENT, pas la position écran de la fenêtre. Doit désormais être
        totalement ignoré."""
        fake_win = tk.Toplevel(self.root)
        self.addCleanup(fake_win.destroy)
        fake_win.geometry("+333+444")
        child = tk.Label(fake_win)  # jamais empaqueté : seule l'identité compte ici
        self.addCleanup(child.destroy)
        export_prefs.save_value("ask_club_window_x", 111)
        export_prefs.save_value("ask_club_window_y", 222)
        fake_event = type("FakeEvent", (), {"widget": child})()

        main._on_ask_club_dialog_configure(fake_event, fake_win)

        self.assertEqual(export_prefs.load_value("ask_club_window_x"), 111)
        self.assertEqual(export_prefs.load_value("ask_club_window_y"), 222)

    def test_configure_ne_leve_jamais_meme_fenetre_detruite(self):
        fake_win = tk.Toplevel(self.root)
        fake_win.destroy()
        fake_event = type("FakeEvent", (), {"widget": fake_win})()
        main._on_ask_club_dialog_configure(fake_event, fake_win)  # ne doit pas lever

    def test_position_partagee_entre_tous_les_appelants(self):
        """ask_club_dialog est une fonction unique, appelée aussi bien
        depuis RosterManagerDialog._add()/._edit_club() que depuis
        PlayerSelectionDialog : une seule paire de clés export_prefs,
        donc une position déplacée une fois vaut pour TOUS les
        appelants suivants, quel que soit lequel a fait le déplacement."""
        main._save_ask_club_dialog_position(50, 60)
        # Peu importe quel "appelant" relit la position : même fonction,
        # mêmes clés, résultat identique.
        self.assertEqual(main._ask_club_dialog_position(self.root), (50, 60))
        self.assertEqual(main._ask_club_dialog_position(self.root), (50, 60))


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RosterSelectsNewMemberTest(unittest.TestCase):
    """Le nouveau membre doit apparaître sélectionné + visible dans
    roster_tree après le flux "Club de XXX" complet (demande du
    2026-09-16, complément) — que la fenêtre ait été validée ou
    annulée/fermée. Exécute les VRAIES RosterManagerDialog._add()/
    _refresh()/_select_and_reveal() (jamais réimplémentées) sur un
    harnais minimal mais réel : roster_tree, club_summary_tree,
    summary_box, preview_lbl sont de VRAIS widgets Tk (aucun d'eux
    n'appelle update_idletasks()/bbox(), donc aucun rapport avec le
    risque de segfault déjà documenté dans cette suite). Un seul
    tk.Tk() pour toute la classe (même précaution anti-flakiness
    Tcl/Tk qu'ailleurs)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : force gc.collect() sur le thread principal
        # après destroy(), avant qu'un test HTTP ultérieur ne puisse en
        # hériter par hasard.
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_select_new_member_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        roster_patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(roster_patcher.stop)
        roster_patcher.start()
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        prefs_patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        # 5 colonnes (pas seulement name/club) : reflète le VRAI Treeview
        # de RosterManagerDialog.__init__ depuis le 2026-09-20 (ajout
        # group/phone/mail) — _update_roster_sort_headings() itère sur
        # les 5, une racine à seulement 2 colonnes déclarées lèverait
        # TclError: Invalid column index "group".
        self.roster_tree = ttk.Treeview(
            self.root, columns=("name", "club", "group", "phone", "mail"), show="tree headings",
        )
        self.roster_tree.heading("name", text="Nom")
        self.roster_tree.heading("club", text="Club")
        self.roster_tree.heading("group", text="Groupe")
        self.roster_tree.heading("phone", text="Téléphone")
        self.roster_tree.heading("mail", text="Mail")

        self.club_summary_tree = ttk.Treeview(self.root, columns=("club", "count"), show="headings")
        self.club_summary_tree.heading("club", text="Club")
        self.club_summary_tree.heading("count", text="Nombre")

        self.summary_box = ttk.LabelFrame(self.root, text="")
        self.preview_lbl = tk.Label(self.root)

        self.dlg = types.SimpleNamespace(
            new_name_var=_FakeVar(),
            roster_tree=self.roster_tree,
            club_summary_tree=self.club_summary_tree,
            summary_box=self.summary_box,
            preview_lbl=self.preview_lbl,
            roster_sort={"column": "name", "ascending": True},
            roster_row_photo_images={},
            _preview_photo=None,
        )
        # Méthodes RÉELLES de RosterManagerDialog, jamais réimplémentées
        # (voir la docstring de la classe) — greffées directement (une
        # fonction assignée en attribut se comporte comme une méthode
        # liée normale via le protocole des descripteurs).
        for meth in (
            "_refresh", "_refresh_club_summary", "_refresh_preview",
            "_selected_name", "_update_roster_sort_headings",
            "_select_and_reveal", "_add",
        ):
            setattr(self.dlg, meth, types.MethodType(getattr(main.RosterManagerDialog, meth), self.dlg))
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : self.dlg (SimpleNamespace) porte 7 méthodes
        # liées à lui-même (cycle) — remplace les 4 addCleanup(...destroy)
        # séparés d'origine, PER TEST (cls.root, lui, n'est nettoyé
        # qu'une fois en tearDownClass).
        self.addCleanup(lambda: cleanup_tk(
            self, "dlg", "roster_tree", "club_summary_tree", "summary_box", "preview_lbl",
        ))

    def _add_member(self, name, ask_club_return):
        self.dlg.new_name_var.set(name)
        with patch.object(main, "ask_club_dialog", return_value=ask_club_return):
            self.dlg._add()

    def test_nouveau_membre_cree_et_selectionne_apres_validation(self):
        self._add_member("Alice", ask_club_return="CPC")

        self.assertIn("Alice", roster.load_roster())
        self.assertEqual(self.roster_tree.selection(), ("Alice",))

    def test_nouveau_membre_cree_et_selectionne_apres_annulation(self):
        """Fonctionne AUSSI si "Club de XXX" est annulée/fermée — le
        membre est créé dans les deux cas (voir RosterAddOpensClub
        PromptTest), donc toujours sélectionné ensuite."""
        self._add_member("Bob", ask_club_return=None)

        self.assertIn("Bob", roster.load_roster())
        self.assertEqual(roster.get_club("Bob"), "")  # bien sans club
        self.assertEqual(self.roster_tree.selection(), ("Bob",))

    def test_nouveau_membre_rendu_visible_hors_de_la_zone_visible(self):
        """Ajoute d'abord assez de monde pour que la liste dépasse une
        zone visible réduite, puis vérifie que le nouveau membre (tout
        en bas alphabétiquement) est bien amené dans cette zone par
        see() — pas seulement sélectionné sans être vraiment visible."""
        self.roster_tree.pack()
        self.roster_tree.configure(height=3)  # ne montre que 3 lignes à la fois
        for i in range(10):
            roster.add_to_roster(f"J{i:02d}")
        self.dlg._refresh()

        self._add_member("Zoe", ask_club_return=None)  # dernier alphabétiquement

        self.assertEqual(self.roster_tree.selection(), ("Zoe",))
        # bbox() non vide = la ligne est dans la zone actuellement
        # affichée (voir la mise en garde du module sur bbox() : ici
        # SANS update_idletasks() préalable, juste une lecture après
        # coup — see() a déjà fait le travail de défilement réel).
        self.assertTrue(self.roster_tree.bbox("Zoe"), "Zoe devrait être défilée dans la zone visible")

    def test_ne_modifie_pas_le_tri_actuel(self):
        roster.add_to_roster("Michel")
        self.dlg.roster_sort = {"column": "name", "ascending": False}  # tri décroissant
        self.dlg._refresh()

        self._add_member("Aline", ask_club_return=None)

        self.assertEqual(self.dlg.roster_sort, {"column": "name", "ascending": False})
        children = self.roster_tree.get_children()
        # Toujours décroissant : "Michel" avant "Aline".
        self.assertEqual(list(children), ["Michel", "Aline"])
        self.assertEqual(self.roster_tree.selection(), ("Aline",))

    def test_ne_modifie_pas_les_autres_comportements_de_selection(self):
        """_refresh() continue de préserver une sélection PRÉEXISTANTE
        entre deux rafraîchissements normaux (comportement déjà en place,
        voir _refresh) — non affecté par _select_and_reveal, qui n'agit
        que sur LE nouveau membre, à la fin de _add()."""
        roster.add_to_roster("Marc")
        self.dlg._refresh()
        self.roster_tree.selection_set("Marc")

        self.dlg._refresh()  # un rafraîchissement "normal", sans rapport avec _add()

        self.assertEqual(self.roster_tree.selection(), ("Marc",))


if __name__ == "__main__":
    unittest.main()
