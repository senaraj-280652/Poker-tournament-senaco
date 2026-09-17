# -*- coding: utf-8 -*-
"""Raccourci "clic droit -> Qui a éliminé ce joueur ? directement" sur
l'onglet Joueurs (demande du 2026-09-16, CORRIGÉE le même jour après une
première version avec menu contextuel — CETTE version n'a plus AUCUN
menu ni AUCUNE confirmation intermédiaire) — main.py:
App._on_players_tree_right_click (nouveau) et App._eliminate_selected
(nouveaux paramètres `skip_confirmation`/`ids`/`force_mandatory_
eliminator`, réutilisés SANS DUPLICATION par le clic droit : même
enregistrement, classement, Primes/PKO, rééquilibrage, mouvements,
bandeau d'élimination, contrôle à distance que le bouton rouge
"Éliminer" existant, dont le comportement par défaut — confirmation
comprise — reste strictement inchangé).

Le garde-fou du raccourci EST la fenêtre "Qui a éliminé ce joueur ?"
elle-même (_ask_eliminator, mandatory=True forcé) : fermer/annuler cette
fenêtre renvoie eliminator_id=None, et le garde-fou DÉJÀ EXISTANT
`if mandatory and eliminator_id is None: return` (main.py) abandonne
alors l'élimination — mécanisme réutilisé tel quel, jamais dupliqué.

Complété le 2026-09-17 (voir tests/test_undo_last_elimination.py pour la
couverture DATABASE de l'annulation elle-même) : un clic droit sur le
DERNIER joueur éliminé propose désormais son annulation
(_undo_last_elimination, même fonction métier centrale que le bouton
"Annule Eliminer") — un clic droit sur un joueur éliminé plus ANCIEN
reste sans aucun effet (RÈGLE ABSOLUE demandée : jamais d'annulation
d'une élimination antérieure à la dernière).

Deux volets, séparés pour éviter tout risque déjà documenté ailleurs
dans cette suite :

- EliminateSelectedShortcutTest : _eliminate_selected au niveau
  UNITAIRE, sur une doublure App minimale (jamais de vrai Tk) — même
  principe que tests/test_test_mode_and_mandatory_eliminator.py:
  GroupEliminationTestModeTest. _ask_eliminator est ESPIONNÉE, jamais
  réellement ouverte (elle crée un tk.Toplevel modal avec grab_set() +
  wait_window() — voir la mise en garde de tests/test_ask_eliminator_
  window_position.py sur le risque de segfault Tcl/Tk observé en
  pilotant réellement ce genre de fenêtre depuis un harnais de test).

- PlayersTreeRightClickTest : _on_players_tree_right_click, avec un VRAI
  ttk.Treeview (nécessaire pour identify_row/selection_set — un
  comportement Tk réel, pas la peine de le réimplémenter à la main) ;
  _eliminate_selected ET _undo_last_elimination sont ESPIONNÉES (jamais
  réellement exécutées dans cette classe — _eliminate_selected déjà
  couverte séparément ci-dessus, _undo_last_elimination dans tests/
  test_undo_last_elimination.py)."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, name):
    return database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))


def _set_elim_time_seconds_ago(db, player_id, seconds_ago):
    """Même helper que tests/test_undo_last_elimination.py (dupliqué à
    dessein plutôt qu'importé, pour garder ces deux fichiers de test
    indépendants) : fixe elim_time à `seconds_ago` secondes avant le
    vrai instant présent, pour contrôler précisément le délai "Timeout
    pour Annuler Eliminer" (demande du 2026-09-17) sans time.sleep."""
    target_epoch = time.time() - seconds_ago
    elim_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(target_epoch))
    db.conn.execute("UPDATE players SET elim_time=? WHERE id=?", (elim_time_str, player_id))
    db.conn.commit()
    return elim_time_str


class _FakeAppForEliminateSelected:
    """Doublure minimale de App pour _eliminate_selected : self.db est
    une VRAIE Database ; le reste (dialogues, rafraîchissement,
    éliminateur) sont des doublures/espions, jamais la vraie logique UI
    (voir la docstring du module pour pourquoi _ask_eliminator n'est
    jamais réellement ouverte ici)."""

    def __init__(self, db, ask_eliminator_return=None, raise_if_checked_or_selected=False):
        self.db = db
        self.ask_eliminator_calls = []
        self._ask_eliminator_return = ask_eliminator_return
        self.checked_or_selected_calls = 0
        self._raise_if_checked_or_selected = raise_if_checked_or_selected
        self.clear_checked_calls = 0
        self.refresh_all_calls = 0
        self.finish_movement_alert_calls = 0
        self.trigger_movement_alert_calls = 0
        self._test_mode = False

    def _checked_or_selected_ids(self):
        self.checked_or_selected_calls += 1
        if self._raise_if_checked_or_selected:
            raise AssertionError(
                "_checked_or_selected_ids ne doit pas être appelée quand "
                "`ids` est fourni explicitement (voir _on_players_tree_right_click)."
            )
        return []

    def _clear_checked(self):
        self.clear_checked_calls += 1

    def _ask_eliminator(self, exclude_id, mandatory=False):
        self.ask_eliminator_calls.append({"exclude_id": exclude_id, "mandatory": mandatory})
        return self._ask_eliminator_return

    def _queue_elimination_banner(self, *a, **k):
        pass

    def _trigger_movement_alert(self, from_remote=False):
        self.trigger_movement_alert_calls += 1

    def _finish_movement_alert(self):
        self.finish_movement_alert_calls += 1

    def _refresh_all(self):
        self.refresh_all_calls += 1

    def _check_pending_rebalance(self):
        pass

    def _test_mode_enabled(self):
        return self._test_mode


class EliminateSelectedShortcutTest(unittest.TestCase):
    """_eliminate_selected(skip_confirmation=, ids=,
    force_mandatory_eliminator=) — niveau unitaire, sans Tk réel (voir
    docstring du module)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="eliminate_shortcut_test_")
        self.addCleanup(self._tmp.cleanup)

    def _players(self, db, n=3):
        return [db.add_player(f"J{i}") for i in range(n)]

    # -- 3/4 : aucune confirmation, aucune case à cocher nécessaire ------

    def test_skip_confirmation_saute_askyesno(self):
        db = _new_db(self._tmp.name, "a")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 3)
        app = _FakeAppForEliminateSelected(db, ask_eliminator_return=ids[1])

        with patch.object(main.messagebox, "askyesno") as mock_confirm:
            main.App._eliminate_selected(
                app, skip_confirmation=True, ids=[ids[0]], force_mandatory_eliminator=True
            )

        mock_confirm.assert_not_called()

    def test_ids_explicite_jamais_checked_or_selected_ids(self):
        db = _new_db(self._tmp.name, "b")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 3)
        app = _FakeAppForEliminateSelected(db, raise_if_checked_or_selected=True)

        main.App._eliminate_selected(
            app, skip_confirmation=True, ids=[ids[1]], force_mandatory_eliminator=True
        )
        # (ask_eliminator_return=None par défaut -> élimination abandonnée,
        # mais _checked_or_selected_ids ne doit JAMAIS avoir été appelée)
        self.assertEqual(app.checked_or_selected_calls, 0)

    # -- 1/6 : ouverture immédiate + validation = élimination normale ----

    def test_ouvre_qui_a_elimine_immediatement_avec_mandatory_force(self):
        db = _new_db(self._tmp.name, "c")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 3)
        app = _FakeAppForEliminateSelected(db, ask_eliminator_return=ids[1])

        main.App._eliminate_selected(
            app, skip_confirmation=True, ids=[ids[0]], force_mandatory_eliminator=True
        )

        self.assertEqual(app.ask_eliminator_calls, [{"exclude_id": ids[0], "mandatory": True}])

    def test_validation_elimine_normalement_et_enregistre_l_eliminateur(self):
        db = _new_db(self._tmp.name, "d")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 3)
        app = _FakeAppForEliminateSelected(db, ask_eliminator_return=ids[1])

        main.App._eliminate_selected(
            app, skip_confirmation=True, ids=[ids[0]], force_mandatory_eliminator=True
        )

        eliminated = db.get_player(ids[0])
        self.assertEqual(eliminated["status"], "eliminated")
        self.assertEqual(eliminated["eliminated_by_name"], "J1")
        self.assertEqual(app.refresh_all_calls, 1)

    # -- 5 : fermeture/annulation = aucune élimination --------------------

    def test_fermeture_ou_annulation_n_elimine_personne(self):
        db = _new_db(self._tmp.name, "e")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 3)
        # _ask_eliminator_return=None : reproduit fidèlement à la fois
        # "Annuler l'élimination" ET la fermeture de la fenêtre par la
        # croix (les deux ne mettent jamais result["id"], voir
        # _ask_eliminator — indiscernables du point de vue de l'appelant).
        app = _FakeAppForEliminateSelected(db, ask_eliminator_return=None)

        main.App._eliminate_selected(
            app, skip_confirmation=True, ids=[ids[0]], force_mandatory_eliminator=True
        )

        self.assertEqual(db.get_player(ids[0])["status"], "active")  # toujours actif
        self.assertEqual(app.refresh_all_calls, 0)  # rien n'a été fait

    def test_force_mandatory_eliminator_ignore_si_pas_d_ask_eliminator_return(self):
        """Sans validation (Valider), même avec une bounty nulle et primes
        désactivées (cas qui rendrait mandatory=False normalement), le
        clic droit doit continuer à abandonner l'élimination — la force
        vient de force_mandatory_eliminator, pas du contexte de bounty."""
        db = _new_db(self._tmp.name, "f")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 2)
        app = _FakeAppForEliminateSelected(db, ask_eliminator_return=None)

        main.App._eliminate_selected(
            app, skip_confirmation=True, ids=[ids[0]], force_mandatory_eliminator=True
        )

        self.assertEqual(db.get_player(ids[0])["status"], "active")

    # -- 9 : bouton rouge existant, comportement strictement inchangé ----

    def test_bouton_rouge_sans_parametres_askyesno_toujours_appelee(self):
        db = _new_db(self._tmp.name, "g")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 2)
        app = _FakeAppForEliminateSelected(db, ask_eliminator_return=None)

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_confirm:
            main.App._eliminate_selected(app, ids=[ids[0]])  # aucun des 3 nouveaux paramètres

        mock_confirm.assert_called_once()
        # mandatory non forcé : bounty nulle par défaut -> mandatory=False
        # -> _ask_eliminator_return=None n'abandonne PAS l'élimination
        # (comportement "Ignorer (pas de prime)", inchangé).
        self.assertEqual(db.get_player(ids[0])["status"], "eliminated")

    def test_bouton_rouge_askyesno_refuse_annule_comme_avant(self):
        db = _new_db(self._tmp.name, "h")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 2)
        app = _FakeAppForEliminateSelected(db)

        with patch.object(main.messagebox, "askyesno", return_value=False):
            main.App._eliminate_selected(app, ids=[ids[0]])

        self.assertEqual(db.get_player(ids[0])["status"], "active")
        self.assertEqual(app.ask_eliminator_calls, [])

    def test_ids_none_utilise_checked_or_selected_ids_comme_avant(self):
        db = _new_db(self._tmp.name, "i")
        self.addCleanup(db.conn.close)
        self._players(db, 2)
        app = _FakeAppForEliminateSelected(db)

        with patch.object(main.messagebox, "askyesno"):
            main.App._eliminate_selected(app)

        self.assertEqual(app.checked_or_selected_calls, 1)

    def test_dernier_joueur_actif_toujours_protege(self):
        """Garde-fou existant (le vainqueur doit toujours rester) —
        jamais dupliqué ni contourné par le raccourci clic droit."""
        db = _new_db(self._tmp.name, "j")
        self.addCleanup(db.conn.close)
        ids = self._players(db, 2)
        db.eliminate_player(ids[0])  # ne reste qu'UN seul actif : ids[1]
        app = _FakeAppForEliminateSelected(db)

        with patch.object(main.messagebox, "askyesno") as mock_confirm, \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._eliminate_selected(
                app, skip_confirmation=True, ids=[ids[1]], force_mandatory_eliminator=True
            )

        mock_error.assert_called_once()
        mock_confirm.assert_not_called()
        self.assertEqual(db.get_player(ids[1])["status"], "active")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PlayersTreeRightClickTest(unittest.TestCase):
    """_on_players_tree_right_click sur un VRAI ttk.Treeview (identify_
    row/selection_set réels) — un seul tk.Tk() pour toute la classe,
    même précaution anti-flakiness Tcl/Tk qu'ailleurs dans cette
    suite. _eliminate_selected est ESPIONNÉE (jamais réellement
    exécutée ici, voir EliminateSelectedShortcutTest ci-dessus)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_right_click_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "A")
        self.addCleanup(self.db.conn.close)

        self.tree = ttk.Treeview(self.root, columns=("name",), show="headings")
        self.addCleanup(self.tree.destroy)
        self.tree.heading("name", text="Nom")
        self.tree.pack()

        self.eliminate_calls = []
        self.undo_calls = 0

        def fake_eliminate(skip_confirmation=False, ids=None, force_mandatory_eliminator=False):
            self.eliminate_calls.append(
                (skip_confirmation, tuple(ids) if ids is not None else None,
                 force_mandatory_eliminator)
            )

        def fake_undo():
            self.undo_calls += 1

        self.win = types.SimpleNamespace(
            players_tree=self.tree,
            db=self.db,
            _eliminate_selected=fake_eliminate,
            _undo_last_elimination=fake_undo,
        )
        self.win._on_players_tree_right_click = types.MethodType(
            main.App._on_players_tree_right_click, self.win
        )

    def _insert_row(self, pid, name):
        self.tree.insert("", "end", iid=str(pid), values=(name,))

    def _right_click_on_row(self, row_iid):
        """Simule un clic droit qui identifie CETTE ligne précise —
        SANS JAMAIS calculer de vraies coordonnées à l'écran.
        tree.update_idletasks()/tree.bbox() provoquent un crash NATIF
        Tcl/Tk (SIGSEGV) reproductible dans cet environnement précis
        (diagnostiqué le 2026-09-16 : rapport de plantage macOS pointant
        dans les bibliothèques Tcl/Tk elles-mêmes, pas du code Python) —
        même prudence déjà appliquée ailleurs dans cette suite pour
        tk.Canvas.update_idletasks() (voir tests/test_tables_tab_player_
        counts.py) et pour les fenêtres modales avec wait_window()/
        grab_set() (voir tests/test_ask_eliminator_window_position.py).

        _on_players_tree_right_click n'utilise l'event QUE pour
        `self.players_tree.identify_row(event.y)` (voir son code) : on
        patche donc directement identify_row pour renvoyer `row_iid`,
        sans jamais passer par le calcul de géométrie réel de Tk qui
        plante. selection_set/selection() restent de VRAIS appels Tk
        (jamais impliqués dans le crash observé), donc conservés tels
        quels pour tester fidèlement le comportement réel."""
        event = types.SimpleNamespace(x=0, y=0, x_root=100, y_root=100)
        with patch.object(self.tree, "identify_row", return_value=row_iid):
            self.win._on_players_tree_right_click(event)

    # -- 1/2 : ouverture immédiate, aucun menu ----------------------------

    def test_clic_droit_ouvre_directement_sans_menu(self):
        pid = self.db.add_player("Alice")
        self._insert_row(pid, "Alice")

        with patch.object(main.tk, "Menu") as mock_menu:
            self._right_click_on_row(str(pid))

        mock_menu.assert_not_called()  # AUCUN menu créé
        self.assertEqual(self.eliminate_calls, [(True, (pid,), True)])

    def test_clic_droit_selectionne_le_bon_joueur(self):
        pid_a = self.db.add_player("Alice")
        pid_b = self.db.add_player("Bob")
        self._insert_row(pid_a, "Alice")
        self._insert_row(pid_b, "Bob")

        self._right_click_on_row(str(pid_a))

        self.assertEqual(self.tree.selection(), (str(pid_a),))

    # -- 8 : zone vide -----------------------------------------------------

    def test_clic_droit_zone_vide_aucun_effet(self):
        pid = self.db.add_player("Alice")
        self._insert_row(pid, "Alice")

        self._right_click_on_row("")  # identify_row ne trouve aucune ligne

        self.assertEqual(self.eliminate_calls, [])

    def test_clic_droit_dernier_elimine_propose_annulation(self):
        """Demande du 2026-09-17 : clic droit sur le DERNIER joueur
        éliminé -> _undo_last_elimination (même fonction centrale que le
        bouton "Annule Eliminer"), jamais _eliminate_selected."""
        pid = self.db.add_player("Alice")
        self.db.add_player("Bob")  # garde un vainqueur possible
        self.db.eliminate_player(pid)
        self._insert_row(pid, "Alice")

        self._right_click_on_row(str(pid))

        self.assertEqual(self.undo_calls, 1)
        self.assertEqual(self.eliminate_calls, [])

    def test_clic_droit_elimine_plus_ancien_aucun_effet(self):
        """RÈGLE ABSOLUE demandée : un clic droit sur un joueur éliminé
        AVANT le dernier ne doit jamais rien proposer ni modifier."""
        pid_a = self.db.add_player("Alice")
        pid_b = self.db.add_player("Bob")
        self.db.add_player("Chris")  # garde un vainqueur possible
        self.db.eliminate_player(pid_a)  # plus ancien
        self.db.eliminate_player(pid_b)  # dernier éliminé
        self._insert_row(pid_a, "Alice")

        self._right_click_on_row(str(pid_a))

        self.assertEqual(self.undo_calls, 0)
        self.assertEqual(self.eliminate_calls, [])

    def test_clic_droit_forfait_aucun_effet(self):
        pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.withdraw_player(pid)
        self._insert_row(pid, "Alice")

        self._right_click_on_row(str(pid))

        self.assertEqual(self.undo_calls, 0)
        self.assertEqual(self.eliminate_calls, [])

    # -- 7 : clic droit sur B après sélection de A -------------------------

    def test_clic_droit_sur_b_agit_sur_b_meme_si_a_etait_selectionne_avant(self):
        pid_a = self.db.add_player("Alice")
        pid_b = self.db.add_player("Bob")
        self._insert_row(pid_a, "Alice")
        self._insert_row(pid_b, "Bob")
        self.tree.selection_set(str(pid_a))

        self._right_click_on_row(str(pid_b))

        self.assertEqual(self.tree.selection(), (str(pid_b),))
        self.assertEqual(self.eliminate_calls, [(True, (pid_b,), True)])

    # -- Timeout "Annule Eliminer" (demande du 2026-09-17) ----------------

    def test_clic_droit_dernier_elimine_dans_le_delai_propose_annulation(self):
        pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.eliminate_player(pid)
        _set_elim_time_seconds_ago(self.db, pid, 1)
        self._insert_row(pid, "Alice")

        self._right_click_on_row(str(pid))

        self.assertEqual(self.undo_calls, 1)

    def test_clic_droit_dernier_elimine_delai_depasse_aucun_effet(self):
        """RÈGLE demandée : passé le timeout, le clic droit sur le
        dernier éliminé ne propose plus rien — même comportement qu'un
        éliminé plus ancien."""
        pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.db.eliminate_player(pid)
        _set_elim_time_seconds_ago(self.db, pid, 5 * 60 + 1)  # timeout par défaut = 5 min
        self._insert_row(pid, "Alice")

        self._right_click_on_row(str(pid))

        self.assertEqual(self.undo_calls, 0)
        self.assertEqual(self.eliminate_calls, [])
        self.assertEqual(self.db.get_player(pid)["status"], "eliminated")  # inchangé


if __name__ == "__main__":
    unittest.main()
