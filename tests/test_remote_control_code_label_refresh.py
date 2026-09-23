# -*- coding: utf-8 -*-
"""Correctif du 2026-09-19 (étape 2, après l'isolation des tests — voir
tests/test_remote_control_dir_isolation.py) : le libellé "Code : XXXXXX"
de l'onglet Paramètres (main.py, App._build_tabs) n'était calculé
QU'UNE SEULE FOIS à la construction de l'onglet — si open_windows.
remote_session_code() change légitimement pendant que le tournoi reste
ouvert (régénération du fichier partagé, quelle qu'en soit la cause),
le libellé continuait d'afficher l'ANCIEN code indéfiniment, sans que le
responsable ne puisse s'en rendre compte, alors que /authenticate,
lui, exige déjà le nouveau.

Solution minimale retenue (validée par l'utilisateur) : ne jamais créer
de nouvelle source de vérité, continuer à lire open_windows.remote_
session_code() (celle-là même que /authenticate utilise), simple
relecture qui ne régénère JAMAIS le code par elle-même, et rafraîchir le
libellé uniquement pendant que l'onglet Paramètres est affiché (voir
App._tick, jamais à chaque tick de chaque fenêtre ouverte)."""
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

import main  # noqa: E402
import open_windows  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


class _RecordingLabel:
    """Doublure minimale d'un ttk.Label — juste assez pour exercer
    _refresh_remote_control_code_label sans dépendre de Tkinter, et pour
    compter les appels à .config() (vérifier qu'aucune écriture inutile
    n'a lieu quand le texte n'a pas changé)."""

    def __init__(self, text=""):
        self._text = text
        self.config_calls = 0

    def winfo_exists(self):
        return True

    def cget(self, key):
        assert key == "text"
        return self._text

    def config(self, text):
        self.config_calls += 1
        self._text = text


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RefreshRemoteControlCodeLabelTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_code_label_refresh_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=remote_dir),
        ):
            self.addCleanup(target.stop)
            target.start()
        self._session_path = os.path.join(self._tmp.name, "A.tournoi")
        open_windows.register(self._session_path)
        self.addCleanup(open_windows.unregister, self._session_path)

        self.win = types.SimpleNamespace()
        self.win._refresh_remote_control_code_label = types.MethodType(
            main.App._refresh_remote_control_code_label, self.win
        )

    def test_reflete_le_code_reellement_accepte_apres_regeneration(self):
        """Coeur de la demande : si remote_session_code() change pendant
        que le tournoi reste ouvert, le libellé doit finir par refléter
        le NOUVEAU code — celui réellement accepté par /authenticate —
        jamais rester bloqué sur l'ancien."""
        old_code = open_windows.remote_session_code()
        self.win.remote_control_code_lbl = _RecordingLabel()

        self.win._refresh_remote_control_code_label()
        self.assertEqual(self.win.remote_control_code_lbl.cget("text"), f"Code : {old_code}")

        # Simule une régénération légitime du fichier partagé (celle-là
        # même qui, dans l'incident du 2026-09-19, avait été provoquée
        # par une pollution de tests) : le fichier disparaît, un nouveau
        # code est généré à la prochaine lecture — jamais ici, seulement
        # au moment où quelque chose (le téléphone, ou ce test) relit
        # remote_session_code().
        os.remove(open_windows._remote_auth_path())
        new_code = open_windows.remote_session_code()
        self.assertIsNotNone(new_code)

        self.win._refresh_remote_control_code_label()
        self.assertEqual(self.win.remote_control_code_lbl.cget("text"), f"Code : {new_code}")
        # Le nouveau code affiché doit être EXACTEMENT celui que
        # /authenticate accepterait désormais.
        self.assertTrue(open_windows.verify_remote_code(new_code))

    def test_ne_regenere_jamais_le_code_par_lui_meme(self):
        """« Ne pas régénérer inutilement le code » (demande explicite) :
        une simple relecture, même répétée, ne doit jamais faire changer
        la valeur en cours."""
        self.win.remote_control_code_lbl = _RecordingLabel()
        code_before = open_windows.remote_session_code()
        for _ in range(5):
            self.win._refresh_remote_control_code_label()
        self.assertEqual(open_windows.remote_session_code(), code_before)

    def test_ne_reecrit_pas_le_widget_si_le_texte_est_deja_a_jour(self):
        """Évite un travail (et un redessin Tk) inutile : deux appels
        consécutifs sans changement de code ne doivent produire qu'UNE
        seule écriture réelle sur le widget."""
        self.win.remote_control_code_lbl = _RecordingLabel()
        self.win._refresh_remote_control_code_label()
        self.assertEqual(self.win.remote_control_code_lbl.config_calls, 1)
        self.win._refresh_remote_control_code_label()
        self.win._refresh_remote_control_code_label()
        self.assertEqual(
            self.win.remote_control_code_lbl.config_calls, 1,
            "le libellé ne doit être réécrit que lorsque le code affiché change réellement",
        )

    def test_sans_widget_construit_ne_leve_jamais(self):
        """Robustesse (voir _tick, protégé par un try/except global, mais
        cette méthode doit rester sûre par elle-même) : appelée avant que
        l'onglet Paramètres n'ait été construit (self.remote_control_
        code_lbl n'existe pas encore), ne doit jamais lever."""
        win = types.SimpleNamespace()
        win._refresh_remote_control_code_label = types.MethodType(
            main.App._refresh_remote_control_code_label, win
        )
        win._refresh_remote_control_code_label()  # ne doit pas lever

    def test_widget_deja_detruit_ne_leve_jamais(self):
        self.root = tk.Tk()
        self.root.withdraw()
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : force gc.collect() sur le thread principal
        # après destroy(), avant qu'un test HTTP ultérieur ne puisse en
        # hériter par hasard.
        self.addCleanup(lambda: cleanup_tk(self, "root"))
        lbl = ttk.Label(self.root, text="Code : 000000")
        self.win.remote_control_code_lbl = lbl
        lbl.destroy()
        self.win._refresh_remote_control_code_label()  # ne doit pas lever


# =======================================================================
# Câblage structurel (même technique que tests/test_align_primes_
# enabled_on_open.py::WiringStructurelTest) : vérifie que _tick() appelle
# bien _refresh_remote_control_code_label(), et UNIQUEMENT pendant que
# l'onglet Paramètres est affiché — jamais à chaque tick de chaque
# fenêtre ouverte (coût négligeable exigé par la demande).
# =======================================================================
class TickWiringStructurelTest(unittest.TestCase):
    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.tree = ast.parse(f.read(), filename=main_py)

    def _find_method(self, class_name, method_name):
        cls = next(n for n in ast.walk(self.tree) if isinstance(n, ast.ClassDef) and n.name == class_name)
        return next(
            n for n in ast.walk(cls) if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def test_tick_appelle_le_refresh_du_code(self):
        tick = self._find_method("App", "_tick")
        calls_refresh = any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_refresh_remote_control_code_label"
            for n in ast.walk(tick)
        )
        self.assertTrue(calls_refresh, "_tick doit appeler _refresh_remote_control_code_label")

    def test_refresh_du_code_est_conditionne_a_laffichage_de_calog(self):
        """Le coeur de la contrainte "travail négligeable à chaque tick" :
        l'appel doit se trouver dans une branche testant `current`
        (l'onglet actuellement affiché), jamais inconditionnel. Onglet
        "CA/LOG" depuis le 2026-09-22 ("réorganisation visuelle du
        contrôle à distance") — auparavant "Paramètres"."""
        tick = self._find_method("App", "_tick")
        src = ast.unparse(tick)
        call_pos = src.index("_refresh_remote_control_code_label(")
        guard_pos = src.index("startswith")
        self.assertIn("CA/LOG", src[guard_pos:guard_pos + 40])
        self.assertLess(
            guard_pos, call_pos,
            "_refresh_remote_control_code_label doit être appelée après (donc à l'intérieur "
            "de) la condition sur l'onglet CA/LOG affiché",
        )


if __name__ == "__main__":
    unittest.main()
