# -*- coding: utf-8 -*-
"""Mémorisation de la position de la fenêtre "Qui a éliminé ce joueur ?"
(onglet Joueurs, demande du 2026-09-16, observée lors du test réel sur
le HP) — main.py: App._ask_eliminator_position/_save_ask_eliminator_
position/_on_ask_eliminator_window_configure, câblées dans App.
_ask_eliminator.

CORRECTIF du 2026-09-17 (régression constatée sur Mac : la position ne
se conservait plus correctement) : un <Configure> émis par n'importe
quel ENFANT de la fenêtre (Label, Combobox, Bouton) remonte jusqu'au
gestionnaire posé sur la fenêtre elle-même (bindtags) — `event.widget`
n'était alors PAS forcément la fenêtre, et winfo_x()/winfo_y() de cet
enfant renvoient une position relative à SON PARENT, pas la position
écran de la fenêtre, écrasant silencieusement la vraie position
mémorisée. _on_ask_eliminator_window_configure prend désormais `win` en
second paramètre (capturé par une lambda au bind(), jamais un nouvel
attribut persistant sur self) et ignore tout événement dont `event.
widget is not win` — voir AskEliminatorPositionUnitTest.test_configure_
dun_enfant_est_ignore ci-dessous, et le même correctif appliqué à
ask_club_dialog (tests/test_roster_add_club_prompt.py).

Réutilise EXACTEMENT le mécanisme déjà existant pour la fenêtre
flottante de demande de téléphone (export_prefs, préférence PERSISTANTE
— survit à un redémarrage complet de Senaco — et App._is_position_
onscreen, même garde-fou contre une position devenue hors écran après
un changement de résolution/moniteur) : voir tests/test_remote_device_
popup_window.py: PopupGeometryTest pour le même principe.

IMPORTANT — pourquoi pas de test pilotant la VRAIE fenêtre modale :
_ask_eliminator ouvre un tk.Toplevel avec grab_set()+wait_window(), sur
une racine Tk "greffée" (sans le reste d'une vraie App). Un premier
essai pilotant réellement cette boîte de dialogue (after() + clic sur
un vrai bouton) a fait planter l'INTERPRÈTE PYTHON LUI-MÊME (segfault
Tcl/Tk, code de sortie 139) dans cet environnement précis — cohérent
avec la même mise en garde déjà documentée dans tests/test_tables_tab_
player_counts.py pour un souci similaire (tk.Canvas.update_idletasks
sur une racine greffée), et avec le choix déjà fait par tests/test_
test_mode_and_mandatory_eliminator.py de vérifier l'appel à _ask_
eliminator par ANALYSE STATIQUE (AST) plutôt que de l'exécuter
réellement. Le câblage géométrie <-> _ask_eliminator est donc vérifié
ci-dessous de la même façon (AST/source), jamais en ouvrant la fenêtre
pour de vrai — un choix délibéré, pas un renoncement : ouvrir cette
boîte de dialogue reste possible et sûr dans l'application réelle
(fenêtre principale complète, jamais une racine greffée), seul CE
harnais de test précis y est fragile."""
import ast
import inspect
import os
import sys
import tempfile
import textwrap
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class AskEliminatorPositionUnitTest(unittest.TestCase):
    """Position mémorisée / hors écran / sauvegarde au déplacement — au
    niveau UNITAIRE (aucune fenêtre "Qui a éliminé...", donc aucun
    risque lié à wait_window/grab_set — voir la docstring du module).
    Un seul tk.Tk() pour toute la classe (même précaution anti-
    flakiness Tcl/Tk qu'ailleurs dans cette suite)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (demande du 2026-09-19, chantier "crash Tcl/Tk") :
        # destroy() seul ne détruit que le côté Tcl — les 3 méthodes de
        # main.App greffées sur cls.root dans setUp (_is_position_
        # onscreen, _ask_eliminator_position, _save_ask_eliminator_
        # position, _on_ask_eliminator_window_configure, chacune une
        # méthode liée dont __self__ est cls.root lui-même) forment
        # chacune un cycle de références que seul le ramasse-miettes
        # cyclique peut réclamer. cleanup_tk force ce ramassage ICI, sur
        # le thread principal, avant qu'un thread HTTP d'un test
        # ultérieur ne puisse s'en charger par hasard.
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ask_eliminator_position_test_")
        self.addCleanup(self._tmp.cleanup)
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        patcher = patch.object(export_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.win = self.root
        self.win._is_position_onscreen = main.App._is_position_onscreen
        self.win._ask_eliminator_position = types.MethodType(
            main.App._ask_eliminator_position, self.win
        )
        self.win._save_ask_eliminator_position = types.MethodType(
            main.App._save_ask_eliminator_position, self.win
        )
        self.win._on_ask_eliminator_window_configure = types.MethodType(
            main.App._on_ask_eliminator_window_configure, self.win
        )

    def test_aucune_position_memorisee_par_defaut(self):
        """Première ouverture : rien n'est encore mémorisé."""
        self.assertIsNone(self.win._ask_eliminator_position())

    def test_position_sauvegardee_et_relue(self):
        self.win._save_ask_eliminator_position(222, 111)
        self.assertEqual(export_prefs.load_value("ask_eliminator_window_x"), 222)
        self.assertEqual(export_prefs.load_value("ask_eliminator_window_y"), 111)
        self.assertEqual(self.win._ask_eliminator_position(), (222, 111))

    def test_position_hors_ecran_ignoree(self):
        """Ancienne position devenue hors écran (changement de
        résolution/moniteur) : ignorée — jamais de fenêtre rendue
        inaccessible."""
        export_prefs.save_value("ask_eliminator_window_x", 999999)
        export_prefs.save_value("ask_eliminator_window_y", 999999)
        self.assertIsNone(self.win._ask_eliminator_position())

    def test_position_non_numerique_ignoree(self):
        export_prefs.save_value("ask_eliminator_window_x", "pas un nombre")
        export_prefs.save_value("ask_eliminator_window_y", None)
        self.assertIsNone(self.win._ask_eliminator_position())

    def test_configure_memorise_la_position(self):
        """Même convention que tests/test_remote_device_popup_window.py:
        PopupGeometryTest.test_configure_de_la_fenetre_declenche_la_
        sauvegarde — un faux événement portant juste `.widget`, jamais
        un vrai <Configure> généré (event_generate a provoqué le
        segfault mentionné dans la docstring du module). `win` est
        désormais passé explicitement en second argument (CORRECTIF du
        2026-09-17, voir _on_ask_eliminator_window_configure) : l'appelé
        ne fait plus confiance à `event.widget` seul."""
        fake_win = tk.Toplevel(self.win)
        self.addCleanup(fake_win.destroy)
        fake_win.geometry("+333+444")
        # PAS de update_idletasks() ici (voir tests/test_remote_device_
        # popup_window.py: PopupGeometryTest.test_configure_de_la_
        # fenetre_declenche_la_sauvegarde, qui s'en passe aussi) : cet
        # appel précis, combiné à la sonde _TK_AVAILABLE au niveau module
        # (crée puis détruit un premier tk.Tk() avant ce test), a
        # provoqué un segfault Tcl/Tk reproductible dans cet
        # environnement — indépendant de main.py, isolé et confirmé par
        # un script minimal. winfo_x()/winfo_y() restent lus de façon
        # cohérente entre eux (dans _on_ask_eliminator_window_configure
        # ET dans l'assertion ci-dessous) sans avoir besoin de forcer un
        # passage d'idle-tasks.
        fake_event = type("FakeEvent", (), {"widget": fake_win})()

        self.win._on_ask_eliminator_window_configure(fake_event, fake_win)

        self.assertEqual(export_prefs.load_value("ask_eliminator_window_x"), fake_win.winfo_x())
        self.assertEqual(export_prefs.load_value("ask_eliminator_window_y"), fake_win.winfo_y())

    def test_configure_dun_enfant_est_ignore(self):
        """CORRECTIF du 2026-09-17 (diagnostic confirmé, régression
        constatée sur Mac) : un <Configure> dont `event.widget` est un
        ENFANT de la fenêtre (Label, Combobox, Bouton — voir _ask_
        eliminator, chacun empaqueté juste après le bind) remonte
        pourtant jusqu'à ce gestionnaire via les bindtags de `win` —
        c'était exactement la cause du bug : winfo_x()/winfo_y() de cet
        enfant renvoient sa position relative à SON PARENT (souvent une
        petite valeur), pas la position écran de la fenêtre, écrasant
        silencieusement la position réellement mémorisée. Doit
        désormais être totalement ignoré, quelles que soient ses
        coordonnées."""
        fake_win = tk.Toplevel(self.win)
        self.addCleanup(fake_win.destroy)
        fake_win.geometry("+333+444")
        child = tk.Label(fake_win)  # jamais empaqueté : pas nécessaire, seule l'identité compte
        self.addCleanup(child.destroy)
        export_prefs.save_value("ask_eliminator_window_x", 111)
        export_prefs.save_value("ask_eliminator_window_y", 222)
        fake_event = type("FakeEvent", (), {"widget": child})()

        self.win._on_ask_eliminator_window_configure(fake_event, fake_win)

        # Valeur précédente strictement inchangée : l'événement de
        # l'enfant a bien été ignoré, jamais confondu avec `win`.
        self.assertEqual(export_prefs.load_value("ask_eliminator_window_x"), 111)
        self.assertEqual(export_prefs.load_value("ask_eliminator_window_y"), 222)

    def test_configure_ne_leve_jamais_meme_fenetre_detruite(self):
        """Filet de sécurité (même principe que RemoteDeviceRequestWindow.
        _on_configure) : une fenêtre déjà détruite au moment de l'appel
        ne doit jamais lever — juste ne rien mémoriser."""
        fake_win = tk.Toplevel(self.win)
        fake_win.destroy()
        fake_event = type("FakeEvent", (), {"widget": fake_win})()
        self.win._on_ask_eliminator_window_configure(fake_event, fake_win)  # ne doit pas lever


class AskEliminatorSourceWiringTest(unittest.TestCase):
    """Câblage géométrie <-> _ask_eliminator, vérifié par ANALYSE
    STATIQUE (source/AST) plutôt qu'en ouvrant réellement la fenêtre —
    voir la docstring du module pour la raison (segfault Tcl/Tk observé
    dans ce harnais précis). Même technique déjà utilisée par tests/
    test_test_mode_and_mandatory_eliminator.py: test_ask_eliminator_
    toujours_appelee_pour_une_elimination_individuelle pour cette même
    fonction."""

    @classmethod
    def setUpClass(cls):
        cls.source = inspect.getsource(main.App._ask_eliminator)
        cls.tree = ast.parse(textwrap.dedent(cls.source))

    def _calls(self):
        calls = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
        return calls

    def test_interroge_la_position_memorisee_avant_ouverture(self):
        self.assertIn("_ask_eliminator_position", self._calls())

    def test_bind_configure_vers_la_methode_liee_dediee(self):
        """bind("<Configure>", lambda e: self._on_ask_eliminator_window_
        configure(e, win)) — `win` capturé explicitement par la lambda
        (CORRECTIF du 2026-09-17 : plus jamais de confiance aveugle en
        event.widget, voir _on_ask_eliminator_window_configure), jamais
        un nouvel attribut persistant sur self (demande explicite de
        l'utilisateur) ni une fonction imbriquée `def` locale (voir git
        history) : la méthode liée reste testable indépendamment (voir
        AskEliminatorPositionUnitTest.test_configure_memorise_la_
        position ci-dessus)."""
        normalized = self.source.replace("'<Configure>'", '"<Configure>"')
        self.assertIn(
            'bind("<Configure>", lambda e: self._on_ask_eliminator_window_configure(e, win))',
            normalized,
        )
        self.assertNotIn("def _on_configure", self.source)  # pas de fonction imbriquée réintroduite

    def test_geometry_appliquee_conditionnellement_jamais_inconditionnellement(self):
        """`win.geometry(...)` doit rester dans un bloc conditionnel (le
        comportement de première ouverture, sans position mémorisée,
        doit rester intact — jamais un appel systématique)."""
        found_conditional_geometry = False
        for node in ast.walk(self.tree):
            if isinstance(node, ast.If):
                if any(
                    isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "geometry"
                    for n in ast.walk(node)
                ):
                    found_conditional_geometry = True
        self.assertTrue(
            found_conditional_geometry,
            "win.geometry(...) devrait être appelé dans un bloc conditionnel "
            "(uniquement si une position mémorisée existe)",
        )

    def test_le_choix_de_leliminateur_nest_pas_touche(self):
        """Non-régression structurelle : la combobox de candidats et les
        boutons Valider/Ignorer (le cœur fonctionnel de cette boîte de
        dialogue) restent présents et inchangés dans le source — cette
        demande ne touche QUE la géométrie de la fenêtre."""
        for marker in ("Combobox", "Valider", "Ignorer (pas de prime)", "result[\"id\"]"):
            self.assertIn(marker, self.source)


if __name__ == "__main__":
    unittest.main()
