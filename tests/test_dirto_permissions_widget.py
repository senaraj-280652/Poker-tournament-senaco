# -*- coding: utf-8 -*-
"""Formulaire "Permissions DIRTO (pour ce tournoi)" de Paramètres —
Phase 3 du chantier "Sécurisation du Contrôle à distance" (demande du
2026-09-20) : App._build_remote_dirto_permissions_widgets/_refresh_
remote_dirto_permissions_panel/_on_remote_dirto_user_selected/_on_grant_
remote_dirto_permissions/_on_revoke_remote_dirto_permissions.

Même harnais que tests/test_remote_device_owner_widget.py (racine Tk
réelle greffée des vraies méthodes App) — ajoute une vraie Database
(fichier .tournoi temporaire, jamais ~/.poker_tournament) comme self.db,
et roster redirigé lui aussi vers un fichier temporaire."""
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


_INSTANCE_METHODS_TO_GRAFT = (
    "_build_remote_dirto_permissions_widgets",
    "_refresh_remote_dirto_permissions_panel",
    "_on_remote_dirto_user_selected",
    "_on_grant_remote_dirto_permissions",
    "_on_revoke_remote_dirto_permissions",
    # Phase 4, "Sécurisation du Contrôle à distance" (2026-09-20) :
    # _on_grant_remote_dirto_permissions/_on_revoke_remote_dirto_
    # permissions appellent désormais aussi ceci pour un effet immédiat
    # côté téléphone — nécessaire ici pour la même raison que les 5
    # méthodes ci-dessus (racine Tk réelle greffée, pas une vraie App).
    "_refresh_remote_dirto_permissions_cache",
)
_STATIC_METHODS_TO_GRAFT = (
    "_remote_dirto_signature",
)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class DirtoPermissionsWidgetTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

        self._tmp = tempfile.TemporaryDirectory(prefix="dirto_permissions_widget_test_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        patcher = patch.object(roster, "_roster_path", return_value=roster_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        self.win = self.root
        self.win.db = self.db
        for name in _INSTANCE_METHODS_TO_GRAFT:
            setattr(self.win, name, types.MethodType(getattr(main.App, name), self.win))
        for name in _STATIC_METHODS_TO_GRAFT:
            setattr(self.win, name, getattr(main.App, name))

        self.container = ttk.Frame(self.root)
        self.win._build_remote_dirto_permissions_widgets(self.container)

        self.addCleanup(lambda: cleanup_tk(
            self, "root", "win", "container",
            "remote_dirto_admin_combo", "remote_dirto_user_combo",
            "remote_dirto_list_container", "remote_dirto_permission_vars",
        ))

    def _set_group(self, name, group):
        roster.set_group(name, group)

    def _select_dirto(self, name):
        self.win.remote_dirto_user_var.set(name)
        self.win._on_remote_dirto_user_selected()

    def _check(self, *keys):
        for key, var in self.win.remote_dirto_permission_vars.items():
            var.set(key in keys)

    def _checked_keys(self):
        return {k for k, v in self.win.remote_dirto_permission_vars.items() if v.get()}

    def _row_texts(self):
        texts = []

        def _walk(widget):
            if isinstance(widget, (ttk.Label, tk.Label)):
                texts.append(widget.cget("text"))
            for child in widget.winfo_children():
                _walk(child)

        _walk(self.win.remote_dirto_list_container)
        return texts


class ListeDesPermissionsTest(DirtoPermissionsWidgetTest):
    def test_les_7_permissions_attendues_sont_presentes(self):
        self.assertEqual(
            set(self.win.remote_dirto_permission_vars.keys()),
            {"eliminations", "tables", "moves", "clock", "levels", "photos", "rebalance"},
        )

    def test_les_7_cases_sont_reparties_sur_deux_sous_frames_alignees(self):
        """Demande du 2026-09-24 ("réorganisation Permissions DIRTO"),
        mise à jour le 2026-09-25 (alignement précis avec les Combobox
        du dessus, voir _build_remote_dirto_permissions_widgets) : les 7
        cases passent de 1 à 2 colonnes, pour réduire la hauteur
        occupée — mais CHAQUE sous-colonne reste PARFAITEMENT alignée
        (héritage direct de la demande du 2026-09-22 : la longueur du
        texte ne doit jamais déplacer une case).

        Depuis le 2026-09-25, gauche et droite sont deux VRAIES
        sous-frames INDÉPENDANTES (plus une grille à 4 colonnes
        partagée — voir l'ajustement "décaler ~1cm à gauche" puis
        l'alignement précis du 2026-09-25) : chaque case y a donc
        toujours grid column=0 LOCALEMENT à sa propre sous-frame,
        colonne de grille qui ne distingue donc plus gauche/droite à
        elle seule. Vérifié ici via l'APPARTENANCE à la sous-frame
        (`.master`), jamais le numéro de colonne brut : 2 sous-frames
        distinctes, 4 cases dans l'une, 3 dans l'autre — jamais le
        texte dans le Checkbutton lui-même."""
        checkbuttons = self._all_checkbuttons()
        self.assertEqual(len(checkbuttons), 7)
        for cb in checkbuttons:
            self.assertEqual(cb.cget("text"), "", "le Checkbutton ne doit plus porter son propre texte")
            self.assertEqual(int(cb.grid_info()["column"]), 0, "colonne LOCALE à sa propre sous-frame")

        groups = {}
        for cb in checkbuttons:
            groups.setdefault(cb.master, []).append(cb)
        self.assertEqual(len(groups), 2, "les 7 cases doivent se répartir sur exactement 2 sous-frames")
        self.assertEqual(sorted(len(v) for v in groups.values()), [3, 4])

        # Les 7 libellés de permission précisément (pas les autres
        # Label du formulaire, ex. "Autorisé par (ADMIN) :") : identifiés
        # par leur texte, qui doit correspondre exactement aux 7 valeurs
        # de REMOTE_PERMISSION_LABELS — chacun colonne 1 LOCALE à SA
        # sous-frame, et son .master doit être exactement celui de la
        # case juste à sa gauche sur la même ligne (row identique).
        permission_texts = set(database.REMOTE_PERMISSION_LABELS.values())
        permission_labels = [w for w in self._all_labels() if str(w.cget("text")) in permission_texts]
        self.assertEqual(len(permission_labels), 7)
        for lbl in permission_labels:
            self.assertEqual(int(lbl.grid_info()["column"]), 1)
            self.assertIn(lbl.master, groups, "le libellé doit être dans l'une des 2 sous-frames des cases")

    def test_changer_de_niveau_ouvre_la_sous_frame_de_droite(self):
        """Demande explicite : "Changer de niveau (blindes)" doit être
        placé À DROITE de "Gérer les éliminations", en tête (row=0) de
        la sous-frame de DROITE — laquelle est la sous-frame la MOINS
        peuplée (3 cases, contre 4 pour celle de "Gérer les
        éliminations"/"Plan de tables"/"Afficher Mouvements"/
        "Chronomètre")."""
        checkbuttons = self._all_checkbuttons()
        groups = {}
        for cb in checkbuttons:
            groups.setdefault(cb.master, []).append(cb)
        right_frame = next(master for master, cbs in groups.items() if len(cbs) == 3)

        levels_label = next(
            w for w in self._all_labels()
            if str(w.cget("text")) == database.REMOTE_PERMISSION_LABELS["levels"]
        )
        eliminations_label = next(
            w for w in self._all_labels()
            if str(w.cget("text")) == database.REMOTE_PERMISSION_LABELS["eliminations"]
        )
        self.assertIs(levels_label.master, right_frame)
        self.assertEqual(int(levels_label.grid_info()["row"]), 0)
        self.assertIsNot(
            levels_label.master, eliminations_label.master,
            '"Changer de niveau (blindes)" doit être dans une sous-frame DIFFÉRENTE '
            'de "Gérer les éliminations" (à sa droite, pas en dessous)',
        )

    def test_terminer_le_tournoi_najamais_de_case(self):
        """Case et libellé sont deux widgets séparés depuis le
        2026-09-22 (alignement en colonne fixe, "réorganisation
        visuelle du contrôle à distance") — le Checkbutton lui-même n'a
        donc plus de texte : vérifié ici sur les VRAIS libellés
        affichés (ttk.Label), portée volontairement large (tout
        self.container, pas seulement la zone des cases) pour rester
        une garantie aussi large qu'avant ce changement de structure."""
        texts = [str(w.cget("text")) for w in self._all_checkbuttons()]
        texts += [str(w.cget("text")) for w in self._all_labels()]
        for text in texts:
            self.assertNotIn("Terminer", text)

    def _all_checkbuttons(self):
        result = []

        def _walk(widget):
            if isinstance(widget, ttk.Checkbutton):
                result.append(widget)
            for child in widget.winfo_children():
                _walk(child)

        _walk(self.container)
        return result

    def _all_labels(self):
        result = []

        def _walk(widget):
            if isinstance(widget, (ttk.Label, tk.Label)):
                result.append(widget)
            for child in widget.winfo_children():
                _walk(child)

        _walk(self.container)
        return result

    def test_7_cases_a_cocher_exactement(self):
        self.assertEqual(len(self._all_checkbuttons()), 7)


class ComboboxAdminDirtoTest(DirtoPermissionsWidgetTest):
    def test_combobox_admin_liste_uniquement_les_admin(self):
        self._set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self._set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self.win._refresh_remote_dirto_permissions_panel()
        self.assertEqual(list(self.win.remote_dirto_admin_combo["values"]), ["Raj"])

    def test_combobox_dirto_liste_uniquement_les_dirto(self):
        self._set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self._set_group("Marie", roster.ROSTER_GROUP_DIRTO)
        self._set_group("Bob", roster.ROSTER_GROUP_DIRTO)
        self.win._refresh_remote_dirto_permissions_panel()
        self.assertEqual(sorted(self.win.remote_dirto_user_combo["values"]), ["Bob", "Marie"])

    def test_personne_non_classee_najamais_proposee(self):
        roster.add_to_roster("Chris")  # jamais classé
        self.win._refresh_remote_dirto_permissions_panel()
        self.assertNotIn("Chris", self.win.remote_dirto_admin_combo["values"])
        self.assertNotIn("Chris", self.win.remote_dirto_user_combo["values"])

    def test_rafraichissement_ne_perd_jamais_la_selection_en_cours(self):
        """Le rafraîchissement périodique (values Combobox) ne doit
        JAMAIS effacer une sélection déjà faite par l'ADMIN — sinon une
        saisie en cours serait perturbée toutes les ~2s."""
        self._set_group("Raj", roster.ROSTER_GROUP_ADMIN)
        self.win.remote_dirto_admin_var.set("Raj")

        self._set_group("Julie", roster.ROSTER_GROUP_ADMIN)  # nouvel ADMIN ajouté entre-temps
        self.win._refresh_remote_dirto_permissions_panel()

        self.assertEqual(self.win.remote_dirto_admin_var.get(), "Raj")  # inchangé
        self.assertIn("Julie", self.win.remote_dirto_admin_combo["values"])  # mais bien visible


class PreselectionTest(DirtoPermissionsWidgetTest):
    def test_selectionner_un_dirto_sans_autorisation_ne_coche_rien(self):
        self._select_dirto("Marie")
        self.assertEqual(self._checked_keys(), set())

    def test_selectionner_un_dirto_deja_autorise_precharge_ses_permissions(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["eliminations", "tables"])
        self._select_dirto("Marie")
        self.assertEqual(self._checked_keys(), {"eliminations", "tables"})

    def test_changer_de_dirto_recharge_les_bonnes_permissions(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["eliminations"])
        self.db.set_dirto_authorization("Bob", "Raj", ["moves", "clock"])
        self._select_dirto("Marie")
        self.assertEqual(self._checked_keys(), {"eliminations"})
        self._select_dirto("Bob")
        self.assertEqual(self._checked_keys(), {"moves", "clock"})

    def test_admin_jamais_preselectionne(self):
        """"Autorisé par" reste vide même en sélectionnant un DIRTO déjà
        autorisé — identifie qui agit MAINTENANT, pas qui a accordé la
        dernière fois."""
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.win.remote_dirto_admin_var.set("")
        self._select_dirto("Marie")
        self.assertEqual(self.win.remote_dirto_admin_var.get(), "")


class OctroiEtModificationTest(DirtoPermissionsWidgetTest):
    def test_accorder_sans_admin_choisi_naffiche_pas_derreur_bloquante_mais_ne_sauve_pas(self):
        self.win.remote_dirto_user_var.set("Marie")
        self._check("tables")
        with patch.object(main.messagebox, "showinfo") as mock_info:
            self.win._on_grant_remote_dirto_permissions()
        mock_info.assert_called_once()
        self.assertIsNone(self.db.get_dirto_authorization("Marie"))

    def test_accorder_sans_dirto_choisi_ne_sauve_pas(self):
        self.win.remote_dirto_admin_var.set("Raj")
        self.win.remote_dirto_user_var.set("")
        with patch.object(main.messagebox, "showinfo") as mock_info:
            self.win._on_grant_remote_dirto_permissions()
        mock_info.assert_called_once()

    def test_accorder_persiste_correctement(self):
        self.win.remote_dirto_admin_var.set("Raj")
        self.win.remote_dirto_user_var.set("Marie")
        self._check("eliminations", "tables")

        self.win._on_grant_remote_dirto_permissions()

        auth = self.db.get_dirto_authorization("Marie")
        self.assertEqual(auth["admin_name"], "Raj")
        self.assertEqual(sorted(auth["permissions"]), ["eliminations", "tables"])

    def test_modifier_decocher_une_case_la_retire_reellement(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["eliminations", "tables"])
        self.win.remote_dirto_admin_var.set("Julie")
        self.win.remote_dirto_user_var.set("Marie")
        self._check("eliminations")  # "tables" décoché

        self.win._on_grant_remote_dirto_permissions()

        auth = self.db.get_dirto_authorization("Marie")
        self.assertEqual(auth["permissions"], ["eliminations"])
        self.assertEqual(auth["admin_name"], "Julie")

    def test_liste_affichee_apres_octroi(self):
        self.win.remote_dirto_admin_var.set("Raj")
        self.win.remote_dirto_user_var.set("Marie")
        self._check("eliminations")

        self.win._on_grant_remote_dirto_permissions()

        joined = " ".join(self._row_texts())
        self.assertIn("Marie", joined)
        self.assertIn("Raj", joined)
        self.assertIn("Gérer les éliminations", joined)


class RetraitTest(DirtoPermissionsWidgetTest):
    def test_retirer_sans_dirto_choisi_ne_leve_pas(self):
        self.win.remote_dirto_user_var.set("")
        with patch.object(main.messagebox, "showinfo") as mock_info:
            self.win._on_revoke_remote_dirto_permissions()
        mock_info.assert_called_once()

    def test_retirer_via_formulaire(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.win.remote_dirto_user_var.set("Marie")

        self.win._on_revoke_remote_dirto_permissions()

        self.assertIsNone(self.db.get_dirto_authorization("Marie"))

    def test_retirer_decoche_les_cases_si_cetait_le_dirto_courant(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self._select_dirto("Marie")
        self.assertEqual(self._checked_keys(), {"tables"})

        self.win._on_revoke_remote_dirto_permissions("Marie")

        self.assertEqual(self._checked_keys(), set())

    def test_retirer_via_bouton_dune_ligne_precise(self):
        """Le bouton "Retirer" d'une ligne de la liste passe explicitement
        le nom — ne doit jamais dépendre de la sélection courante du
        Combobox."""
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.db.set_dirto_authorization("Bob", "Raj", ["moves"])
        self.win.remote_dirto_user_var.set("")  # rien sélectionné dans le formulaire

        self.win._on_revoke_remote_dirto_permissions("Marie")

        self.assertIsNone(self.db.get_dirto_authorization("Marie"))
        self.assertIsNotNone(self.db.get_dirto_authorization("Bob"))  # intact


class AffichageListeTest(DirtoPermissionsWidgetTest):
    def test_aucune_autorisation_affiche_le_message_vide(self):
        self.win._refresh_remote_dirto_permissions_panel()
        self.assertIn("Aucune autorisation DIRTO pour ce tournoi.", self._row_texts())

    def test_plusieurs_dirto_tous_affiches(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.db.set_dirto_authorization("Bob", "Raj", ["moves"])
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()
        joined = " ".join(self._row_texts())
        self.assertIn("Marie", joined)
        self.assertIn("Bob", joined)

    def test_aucune_fonction_cochee_affiche_un_texte_explicite(self):
        """Demande du 2026-09-24 ("réorganisation Permissions DIRTO") :
        identité ("Marie — accordé par Raj") et "(aucune fonction
        cochée)" sont désormais deux libellés SÉPARÉS (ligne d'identité
        + ligne juste en dessous), plus une seule chaîne combinée à
        virgules."""
        self.db.set_dirto_authorization("Marie", "Raj", [])
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()
        texts = self._row_texts()
        self.assertIn("Marie — accordé par Raj", texts)
        self.assertIn("(aucune fonction cochée)", texts)

    def test_rafraichissement_sans_changement_ne_reconstruit_pas_la_liste(self):
        """Même principe que le panneau "Téléphones" (Phase 2) : éviter
        tout scintillement pour rien — vérifié en comparant l'identité
        Tcl d'un widget avant/après un rafraîchissement sans changement
        réel des autorisations."""
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()
        first_children = list(self.win.remote_dirto_list_container.winfo_children())

        self.win._refresh_remote_dirto_permissions_panel()  # rien n'a changé

        second_children = list(self.win.remote_dirto_list_container.winfo_children())
        self.assertEqual(first_children, second_children)  # mêmes objets widgets, pas recréés

    def test_proprietaire_absent_du_repertoire_naffiche_jamais_dexception(self):
        """DIRTO supprimé/reclassé du Répertoire APRÈS l'octroi : la
        ligne reste affichée normalement (simple texte), jamais une
        exception."""
        self.db.set_dirto_authorization("PersonneSupprimee", "Raj", ["tables"])
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()  # ne doit pas lever
        self.assertIn("PersonneSupprimee", " ".join(self._row_texts()))

    def test_permissions_accordees_affichees_deux_par_ligne_ordre_canonique(self):
        """Demande explicite du 2026-09-24 : les permissions accordées
        ne sont plus une chaîne à virgules, mais des libellés séparés
        groupés deux par ligne — dans l'ORDRE CANONIQUE de REMOTE_
        PERMISSION_LABELS (pas l'ordre de stockage), et UNIQUEMENT
        celles réellement accordées."""
        self.db.set_dirto_authorization(
            "Marie", "Raj",
            # Ordre de stockage délibérément différent de l'ordre
            # canonique, pour prouver que l'affichage se rebase bien
            # dessus plutôt que de suivre l'ordre donné ici.
            ["rebalance", "eliminations", "clock", "tables"],
        )
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()

        def _labels_with_grid():
            result = []

            def _walk(widget):
                if isinstance(widget, (ttk.Label, tk.Label)) and widget.cget("text") in database.REMOTE_PERMISSION_LABELS.values():
                    result.append((widget.cget("text"), widget.grid_info()))
                for child in widget.winfo_children():
                    _walk(child)

            _walk(self.win.remote_dirto_list_container)
            return result

        found = _labels_with_grid()
        texts_in_order = [t for t, _ in found]
        # Ordre canonique attendu (eliminations, tables, moves, clock,
        # levels, photos, rebalance), filtré aux 4 accordées :
        expected = [
            database.REMOTE_PERMISSION_LABELS["eliminations"],
            database.REMOTE_PERMISSION_LABELS["tables"],
            database.REMOTE_PERMISSION_LABELS["clock"],
            database.REMOTE_PERMISSION_LABELS["rebalance"],
        ]
        self.assertEqual(texts_in_order, expected)
        # Groupées deux par ligne : (row, col) = (0,0),(0,1),(1,0),(1,1).
        rows_cols = [(int(info["row"]), int(info["column"])) for _, info in found]
        self.assertEqual(rows_cols, [(0, 0), (0, 1), (1, 0), (1, 1)])
        # Jamais les 3 permissions NON accordées (moves, levels, photos).
        for not_granted in ("moves", "levels", "photos"):
            self.assertNotIn(database.REMOTE_PERMISSION_LABELS[not_granted], texts_in_order)

    def test_bouton_retirer_sur_la_meme_ligne_que_lidentite_pas_apres_les_permissions(self):
        """Demande explicite : le bouton "Retirer" doit être sur la
        MÊME ligne que "{dirto} — accordé par {admin}", jamais au bout
        d'une ligne contenant aussi les permissions — vérifié en
        confirmant que le libellé d'identité et le bouton "Retirer"
        partagent le même widget parent (donc la même ligne), et que ce
        parent ne contient AUCUN libellé de permission."""
        self.db.set_dirto_authorization("Marie", "Raj", ["eliminations", "tables", "moves"])
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()

        identity_label = None
        retirer_button = None

        def _walk(widget):
            nonlocal identity_label, retirer_button
            if isinstance(widget, (ttk.Label, tk.Label)) and widget.cget("text") == "Marie — accordé par Raj":
                identity_label = widget
            if isinstance(widget, ttk.Button) and widget.cget("text") == "Retirer":
                retirer_button = widget
            for child in widget.winfo_children():
                _walk(child)

        _walk(self.win.remote_dirto_list_container)
        self.assertIsNotNone(identity_label)
        self.assertIsNotNone(retirer_button)
        self.assertIs(
            identity_label.master, retirer_button.master,
            "le bouton Retirer doit être sur la MÊME ligne que l'identité du DIRTO",
        )
        # Cette ligne ne doit contenir AUCUN libellé de permission (elles
        # sont affichées EN DESSOUS, jamais sur cette même ligne).
        siblings_text = {
            str(w.cget("text")) for w in identity_label.master.winfo_children()
            if isinstance(w, (ttk.Label, tk.Label))
        }
        self.assertEqual(siblings_text, {"Marie — accordé par Raj"})


class EtancheiteMultiTournoisWidgetTest(DirtoPermissionsWidgetTest):
    """Vérifie qu'un changement de self.db (simule la fermeture d'un
    tournoi et l'ouverture d'un autre dans la même fenêtre logique)
    n'entraîne AUCUNE contamination croisée de la liste affichée."""

    def test_changement_de_tournoi_affiche_ses_propres_autorisations(self):
        self.db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()
        self.assertIn("Marie", " ".join(self._row_texts()))

        other_db = database.Database(os.path.join(self._tmp.name, "B.tournoi"))
        self.addCleanup(other_db.conn.close)
        other_db.set_dirto_authorization("Bob", "Raj", ["moves"])
        self.win.db = other_db
        self.win._last_remote_dirto_signature = None
        self.win._refresh_remote_dirto_permissions_panel()

        joined = " ".join(self._row_texts())
        self.assertIn("Bob", joined)
        self.assertNotIn("Marie", joined)


if __name__ == "__main__":
    unittest.main()
