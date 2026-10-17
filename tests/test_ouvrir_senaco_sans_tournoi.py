# -*- coding: utf-8 -*-
"""Bouton "🖥️ Ouvrir Senaco" (fenêtre "Bienvenue") — demande explicite du
2026-10-13 : ouvre l'interface habituelle de Senaco SANS créer ni charger
de tournoi (self.db reste None), pour préparer des structures de blindes,
régler les paramètres/sons globaux, consulter Statistiques/Répertoire, et
gérer les sauvegardes, sans y être obligé.

Même principe de découpage que tests/test_apply_blinds_button.py :
1. Tests STRUCTURELS (AST sur le source de main.py, aucun Tk nécessaire)
   pour tout ce qui concerne le câblage (boutons, menus, branches
   `if self.db is None`/`is not None`) — robustes aux détails Tk.
2. Tests COMPORTEMENTAUX (self.db RÉEL ou None, méthodes réelles
   attachées à une doublure légère de App, jamais une vraie fenêtre
   construite) pour la logique de l'onglet Blindes sans tournoi.

ATTENTION (voir tests/test_bienvenue_window_height.py et tests/
test_primes_settings_inheritance.py) : ne JAMAIS piloter réellement
App._choose_tournament_file() via son wait_window() — SIGSEGV Tcl/Tk
reproductible déjà diagnostiqué dans ce harnais. Le chemin "standalone"
de cette méthode est donc vérifié par AST (ordre des instructions),
jamais en l'exécutant pour de vrai."""
import ast
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402
import structures  # noqa: E402
import tournament_prefs  # noqa: E402


def _main_source():
    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(main_py, encoding="utf-8") as f:
        return f.read()


class _SourceHelper:
    """Même principe que tests/test_apply_blinds_button.py::
    ButtonWiringStructuralTest — analyse AST du source, jamais une
    recopie manuelle fragile."""

    @classmethod
    def setUpClass(cls):
        cls.source = _main_source()
        cls.tree = ast.parse(cls.source, filename="main.py")

    def _find_method(self, class_name, method_name):
        cls_node = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.ClassDef) and n.name == class_name
        )
        return next(
            n for n in ast.walk(cls_node)
            if isinstance(n, ast.FunctionDef) and n.name == method_name
        )

    def _find_function(self, func_name):
        """Comme _find_method, mais pour une fonction MODULE-LEVEL (pas
        une méthode de classe) — ex. offer_whatsapp_share."""
        return next(
            n for n in self.tree.body
            if isinstance(n, ast.FunctionDef) and n.name == func_name
        )

    def _source_of(self, node):
        return ast.get_source_segment(self.source, node) or ""


# =====================================================================
# 1. Bienvenue : bouton "🖥️ Ouvrir Senaco"
# =====================================================================
class BienvenueButtonStructuralTest(_SourceHelper, unittest.TestCase):
    def test_bouton_ouvrir_senaco_immediatement_avant_nouveau_tournoi(self):
        """"Ouvrir Senaco" doit être le tout premier bouton de btn_frame
        (avant lui, aucun autre ttk.Button(btn_frame, ...)) — ses propres
        .pack()/Tooltip entre les deux statements ne comptent pas comme
        un "autre bouton"."""
        func = self._find_method("App", "_choose_tournament_file")
        idx_senaco = idx_nouveau = None
        for i, stmt in enumerate(func.body):
            seg = self._source_of(stmt)
            if 'text="🖥️  Ouvrir Senaco"' in seg:
                idx_senaco = i
            if "new_tournament_btn = ttk.Button(" in seg:
                idx_nouveau = i
        self.assertIsNotNone(idx_senaco, "bouton 'Ouvrir Senaco' introuvable")
        self.assertIsNotNone(idx_nouveau, "bouton 'Nouveau tournoi' introuvable")
        self.assertLess(idx_senaco, idx_nouveau)
        between = func.body[idx_senaco + 1:idx_nouveau]
        for stmt in between:
            self.assertNotIn("ttk.Button(btn_frame", self._source_of(stmt))

    def test_bouton_ouvrir_senaco_meme_largeur_que_les_autres(self):
        func = self._find_method("App", "_choose_tournament_file")
        seg = self._source_of(func)
        self.assertIn('text="🖥️  Ouvrir Senaco", command=open_senaco, width=28', seg)

    def test_ouvrir_senaco_ne_s_enregistre_jamais_au_lobby(self):
        """open_senaco() ne doit JAMAIS appeler open_windows.try_register
        ni Database(...) — seulement result["standalone"]=True puis
        win.destroy()."""
        func = self._find_method("App", "_choose_tournament_file")
        open_senaco_node = next(
            n for n in ast.walk(func)
            if isinstance(n, ast.FunctionDef) and n.name == "open_senaco"
        )
        # Code réel UNIQUEMENT (docstring exclue : elle mentionne elle-
        # même "try_register"/"Database" en explication, ce qui ferait
        # échouer une simple recherche de sous-chaîne sur tout le texte).
        body_stmts = open_senaco_node.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        code_only = "\n".join(self._source_of(s) for s in body_stmts)
        self.assertIn('result["standalone"] = True', code_only)
        self.assertIn("win.destroy()", code_only)
        self.assertNotIn("try_register", code_only)
        self.assertNotIn("Database(", code_only)

    def test_standalone_retourne_avant_try_register_et_database(self):
        """Après wait_window(win), le cas standalone doit renvoyer True
        AVANT toute ligne touchant open_windows.try_register ou
        Database(result["path"]) — jamais un enregistrement/une ouverture
        de base accidentelle pour ce mode."""
        func = self._find_method("App", "_choose_tournament_file")
        seg = self._source_of(func)
        idx_wait = seg.index("self.wait_window(win)")
        idx_standalone = seg.index('result.get("standalone")')
        idx_try_register = seg.index("open_windows.try_register(result")
        idx_database_call = seg.index('self.db = Database(result["path"])')
        self.assertLess(idx_wait, idx_standalone)
        self.assertLess(idx_standalone, idx_try_register)
        self.assertLess(idx_standalone, idx_database_call)

    def test_geometrie_agrandie(self):
        func = self._find_method("App", "_choose_tournament_file")
        seg = self._source_of(func)
        self.assertIn('win.geometry("480x607")', seg)


# =====================================================================
# 2. Stabilité : gardes d'App.__init__, _build_header, _build_menu
# =====================================================================
class StabilityGuardsStructuralTest(_SourceHelper, unittest.TestCase):
    def test_align_primes_enabled_garde_par_self_db(self):
        """Correctif du 2026-10-14 (bis, registre multi-entrées des
        écrans "sans tournoi") : une nouvelle ligne (désinscription de ce
        registre) s'est intercalée dans ce même bloc `if self.db is not
        None:`, AVANT l'appel historique — toujours sous la même garde,
        voir tests/test_menu_principal_singleton.py pour la couverture
        du nouveau mécanisme lui-même."""
        func = self._find_method("App", "__init__")
        seg = self._source_of(func)
        self.assertIn(
            "if self.db is not None:\n"
            "            # Ce lancement a finalement une vraie base de données",
            seg,
        )
        idx_guard = seg.index(
            "if self.db is not None:\n"
            "            # Ce lancement a finalement une vraie base de données"
        )
        idx_unregister = seg.index("open_windows.unregister_standalone_screen(os.getpid(), _menu_principal_key())")
        idx_align = seg.index("_align_primes_enabled_on_open(self.db)")
        self.assertLess(idx_guard, idx_unregister)
        self.assertLess(idx_unregister, idx_align)

    def test_remote_control_jamais_demarre_sans_tournoi(self):
        func = self._find_method("App", "__init__")
        seg = self._source_of(func)
        self.assertIn(
            "if self.db is not None:\n            self._start_remote_control_if_enabled(silent=True)",
            seg,
        )

    def test_header_menu_principal_bascule_vers_new_tournament_sans_db(self):
        func = self._find_method("App", "_build_header")
        seg = self._source_of(func)
        self.assertIn("command=self._open_new_window if self.db else self._new_tournament", seg)

    def test_menu_reduit_sans_tournoi(self):
        func = self._find_method("App", "_build_menu")
        if_node = next(
            n for n in ast.walk(func)
            if isinstance(n, ast.If) and self._source_of(n.test) == "self.db is None"
        )
        reduced_branch = "\n".join(self._source_of(s) for s in if_node.body)
        for forbidden in ("📋 Lobby", "Nouveau tournoi...", "Ouvrir...", "Exporter les résultats"):
            self.assertNotIn(forbidden, reduced_branch)
        for expected in ("Paramètres de base", "Sons de fin de Round", "Répertoire", "Statistiques"):
            self.assertIn(expected, reduced_branch)
        self.assertTrue(any(isinstance(s, ast.Return) for s in if_node.body))

    def test_check_remote_device_requests_retour_immediat_sans_db(self):
        func = self._find_method("App", "_check_remote_device_requests")
        body_first_stmts = func.body[: 3]
        found = any("self.db is None" in self._source_of(s) for s in body_first_stmts)
        self.assertTrue(found, "garde 'if self.db is None: return' absente en tête de _check_remote_device_requests")


# =====================================================================
# 3. Réutilisation stricte (option B) : backup/restore/purge/paramètres
# =====================================================================
class ReuseNoDuplicationStructuralTest(_SourceHelper, unittest.TestCase):
    """Vérifie que les fermetures de _choose_tournament_file ne sont plus
    que de minces délégations vers des méthodes App partagées — jamais
    une seconde copie de la logique métier (demande explicite : "Réutilise
    impérativement les mêmes fonctions métier... sans dupliquer leur
    logique interne")."""

    def test_restore_now_delegue_a_run_restore_flow(self):
        func = self._find_method("App", "_choose_tournament_file")
        node = next(n for n in ast.walk(func) if isinstance(n, ast.FunctionDef) and n.name == "restore_now")
        seg = self._source_of(node)
        self.assertIn("self._run_restore_flow(win)", seg)
        self.assertNotIn("backup_restore.restore_backup", seg)
        self.assertNotIn("validate_backup_folder", seg)

    def test_purge_now_delegue_a_open_data_purge_dialog(self):
        func = self._find_method("App", "_choose_tournament_file")
        node = next(n for n in ast.walk(func) if isinstance(n, ast.FunctionDef) and n.name == "purge_now")
        seg = self._source_of(node)
        self.assertIn("self._open_data_purge_dialog(win)", seg)
        self.assertNotIn("DataPurgeDialog(", seg)

    def test_open_basic_settings_delegue_a_open_basic_settings_dialog(self):
        func = self._find_method("App", "_choose_tournament_file")
        node = next(n for n in ast.walk(func) if isinstance(n, ast.FunctionDef) and n.name == "open_basic_settings")
        seg = self._source_of(node)
        self.assertIn("self._open_basic_settings_dialog(win)", seg)
        self.assertNotIn("club_name", seg)

    def test_backup_now_reste_sur_run_backup_flow_inchange(self):
        func = self._find_method("App", "_choose_tournament_file")
        node = next(n for n in ast.walk(func) if isinstance(n, ast.FunctionDef) and n.name == "backup_now")
        seg = self._source_of(node)
        self.assertIn("self._run_backup_flow(win)", seg)

    def test_methodes_partagees_existent_sur_app(self):
        for name in ("_run_restore_flow", "_open_data_purge_dialog", "_open_basic_settings_dialog"):
            self.assertTrue(hasattr(main.App, name), f"App.{name} manquante")

    def test_menu_reduit_reutilise_les_memes_methodes_partagees(self):
        func = self._find_method("App", "_build_menu")
        seg = self._source_of(func)
        self.assertIn("self._run_backup_flow(self)", seg)
        self.assertIn("self._run_restore_flow(self)", seg)
        self.assertIn("self._open_data_purge_dialog(self)", seg)
        self.assertIn("self._open_basic_settings_dialog(self)", seg)


# =====================================================================
# 4. WhatsApp "📱 Mon téléphone" désactivé sans tournoi (Statistiques)
# =====================================================================
class WhatsAppPhoneGuardStructuralTest(_SourceHelper, unittest.TestCase):
    def test_offer_whatsapp_share_calcule_phone_available(self):
        func = self._find_function("offer_whatsapp_share")
        seg = self._source_of(func)
        self.assertIn("phone_available=app.db is not None", seg)

    def test_choice_dialog_desactive_le_bouton_telephone(self):
        cls_node = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.ClassDef) and n.name == "WhatsAppDestinationChoiceDialog"
        )
        init_node = next(
            n for n in ast.walk(cls_node) if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        seg = self._source_of(init_node)
        self.assertIn("phone_available=True", seg)  # défaut rétro-compatible
        self.assertIn('state="normal" if phone_available else "disabled"', seg)


# =====================================================================
# 5. _build_tabs : branche standalone
# =====================================================================
class BuildTabsStandaloneStructuralTest(_SourceHelper, unittest.TestCase):
    def test_branche_standalone_seulement_5_onglets(self):
        """Blindes/Répertoire/Statistiques (depuis le 2026-10-13) +
        Paramètres (ajouté le 2026-10-14) + LOG (ajouté le 2026-10-16,
        "le LOG fait partie des outils de suivi et de statistiques") —
        tous les autres onglets tournoi-spécifiques restent absents."""
        func = self._find_method("App", "_build_tabs")
        seg = self._source_of(func)
        start = seg.index("if self.db is None:")
        end = seg.index("self.players_tab = ttk.Frame(self.notebook)")
        standalone_branch = seg[start:end]
        self.assertIn('self.notebook.add(self.blinds_tab, text="Blindes")', standalone_branch)
        self.assertIn('self.notebook.add(self.roster_tab, text="Répertoire")', standalone_branch)
        self.assertIn('self.notebook.add(self.stats_tab, text="Statistiques")', standalone_branch)
        self.assertIn('self.notebook.add(self.log_tab, text="LOG")', standalone_branch)
        self.assertIn('self.notebook.add(self.settings_tab, text="Paramètres")', standalone_branch)
        for forbidden in ("Joueurs", "Tables", "Mouvements", "Primes", "Chronomètre", "Classement", "CA"):
            self.assertNotIn(f'text="{forbidden}"', standalone_branch)
        self.assertIn("return", standalone_branch)


# =====================================================================
# 6. Onglet Blindes sans tournoi : comportement réel
# =====================================================================
class _FakeStandaloneApp:
    """Même principe que tests/test_apply_blinds_button.py::_FakeApp :
    self.db RÉEL (None ici), les méthodes métier réelles de App attachées
    telles quelles, les rafraîchissements Tk réduits à de simples
    compteurs."""

    _collect_blinds_from_widgets = main.App._collect_blinds_from_widgets
    _rounds_to_flat_structure = main.App._rounds_to_flat_structure
    _flat_structure_to_rounds = main.App._flat_structure_to_rounds
    _commit_blind_rounds = main.App._commit_blind_rounds
    _add_blind_round = main.App._add_blind_round
    _delete_blind_round = main.App._delete_blind_round
    _reset_blind_structure_from_tab = main.App._reset_blind_structure_from_tab
    _save_current_as_standard_structure = main.App._save_current_as_standard_structure
    _go_to_level = main.App._go_to_level

    def __init__(self, db, standalone_rounds=None):
        self.db = db
        self._blind_row_vars = []
        self._standalone_blind_rounds = standalone_rounds if standalone_rounds is not None else []
        self.refresh_blinds_calls = 0
        self.refresh_clock_calls = 0

    def _refresh_blinds_tab(self):
        self.refresh_blinds_calls += 1

    def _refresh_clock_tab(self):
        self.refresh_clock_calls += 1


class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


def _row_vars(duration, sb, bb, ante, pause=0):
    return {
        "duration": _FakeVar(duration), "sb": _FakeVar(sb), "bb": _FakeVar(bb),
        "ante": _FakeVar(ante), "pause": _FakeVar(pause),
    }


class FlatStructureToRoundsTest(unittest.TestCase):
    def test_sans_pause(self):
        flat = [
            {"small_blind": 25, "big_blind": 50, "ante": 0, "duration_minutes": 15, "is_break": False},
            {"small_blind": 50, "big_blind": 100, "ante": 0, "duration_minutes": 15, "is_break": False},
        ]
        rounds = main.App._flat_structure_to_rounds(main.App, flat)
        self.assertEqual(rounds, [
            {"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0},
            {"duration": 15, "sb": 50, "bb": 100, "ante": 0, "pause": 0},
        ])

    def test_avec_pause_regroupee_dans_le_round_precedent(self):
        flat = [
            {"small_blind": 25, "big_blind": 50, "ante": 0, "duration_minutes": 15, "is_break": False},
            {"small_blind": 0, "big_blind": 0, "ante": 0, "duration_minutes": 5, "is_break": True, "break_label": "Pause"},
            {"small_blind": 50, "big_blind": 100, "ante": 0, "duration_minutes": 15, "is_break": False},
        ]
        rounds = main.App._flat_structure_to_rounds(main.App, flat)
        self.assertEqual(rounds, [
            {"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 5},
            {"duration": 15, "sb": 50, "bb": 100, "ante": 0, "pause": 0},
        ])

    def test_round_trip_avec_default_blind_structure(self):
        """Le point de départ utilisé par _build_blinds_tab sans tournoi
        (default_blind_structure() converti en rounds) doit redonner
        EXACTEMENT la même structure plate une fois reconvertie — aucune
        perte d'information (break_label notamment, voir _rounds_to_flat_
        structure)."""
        flat = structures.default_blind_structure()
        rounds = main.App._flat_structure_to_rounds(main.App, flat)
        roundtrip_flat = main.App._rounds_to_flat_structure(main.App, rounds)
        self.assertEqual(flat, roundtrip_flat)


class CommitBlindRoundsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ouvrir_senaco_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_sans_tournoi_ecrit_uniquement_en_memoire(self):
        app = _FakeStandaloneApp(db=None)
        rounds = [{"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0}]
        app._commit_blind_rounds(rounds)
        self.assertEqual(app._standalone_blind_rounds, rounds)

    def test_avec_tournoi_ecrit_dans_self_db_comme_avant(self):
        """Non-régression : le chemin AVEC tournoi doit continuer
        d'écrire dans self.db, strictement comme avant ce chantier."""
        db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(db.conn.close)
        app = _FakeStandaloneApp(db=db)
        rounds = [{"duration": 20, "sb": 100, "bb": 200, "ante": 25, "pause": 0}]
        app._commit_blind_rounds(rounds)
        levels = db.get_blind_structure()
        self.assertEqual(
            (levels[0]["small_blind"], levels[0]["big_blind"], levels[0]["ante"]),
            (100, 200, 25),
        )


class AddDeleteBlindRoundWithoutTournamentTest(unittest.TestCase):
    def test_ajouter_un_round_sans_tournoi(self):
        app = _FakeStandaloneApp(
            db=None,
            standalone_rounds=[{"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0}],
        )
        app._blind_row_vars = [_row_vars(15, 25, 50, 0)]
        app._add_blind_round()
        self.assertEqual(len(app._standalone_blind_rounds), 2)
        self.assertEqual(app.refresh_blinds_calls, 1)

    def test_supprimer_un_round_sans_tournoi_avec_confirmation(self):
        app = _FakeStandaloneApp(
            db=None,
            standalone_rounds=[
                {"duration": 15, "sb": 25, "bb": 50, "ante": 0, "pause": 0},
                {"duration": 15, "sb": 50, "bb": 100, "ante": 0, "pause": 0},
            ],
        )
        app._blind_row_vars = [_row_vars(15, 25, 50, 0), _row_vars(15, 50, 100, 0)]
        with patch.object(main, "messagebox") as mock_mb:
            mock_mb.askyesno.return_value = True
            app._delete_blind_round(2)
        self.assertEqual(len(app._standalone_blind_rounds), 1)


class ResetStandardStructureWithoutTournamentTest(unittest.TestCase):
    def test_structure_standard_sans_tournoi_remplace_en_memoire(self):
        app = _FakeStandaloneApp(
            db=None,
            standalone_rounds=[{"duration": 99, "sb": 1, "bb": 2, "ante": 0, "pause": 0}],
        )
        with patch.object(main, "messagebox") as mock_mb:
            mock_mb.askyesno.return_value = True
            app._reset_blind_structure_from_tab()
        expected = main.App._flat_structure_to_rounds(main.App, structures.default_blind_structure())
        self.assertEqual(app._standalone_blind_rounds, expected)
        self.assertEqual(app.refresh_blinds_calls, 1)

    def test_structure_standard_sans_tournoi_sans_go_to_level(self):
        """hasattr(self, 'blinds_tree') est False ici (jamais défini sur
        _FakeStandaloneApp) : _go_to_level ne doit donc jamais être
        appelée, exactement comme pour un vrai tournoi sans Chronomètre
        construit."""
        app = _FakeStandaloneApp(db=None, standalone_rounds=[])
        with patch.object(main, "messagebox") as mock_mb, \
             patch.object(main.App, "_go_to_level") as mock_go_to_level:
            mock_mb.askyesno.return_value = True
            app._reset_blind_structure_from_tab()
            mock_go_to_level.assert_not_called()


class SaveCurrentAsStandardStructureWithoutTournamentTest(unittest.TestCase):
    """"Enr. Struc. Std" (precaution utilisateur explicite : "assure-toi
    qu'aucune modification involontaire ne soit enregistrée") — déjà
    self.db-indépendante avant ce chantier (voir _collect_blinds_from_
    widgets), vérifiée ici fonctionner SANS AUCUN changement, y compris
    sans tournoi, et n'écrire QUE dans le magasin global structures.py,
    jamais ailleurs."""

    def test_fonctionne_sans_tournoi_et_sans_confirmation_refuse_aucune_ecriture(self):
        app = _FakeStandaloneApp(db=None)
        app._blind_row_vars = [_row_vars(15, 25, 50, 0)]
        with patch.object(main, "messagebox") as mock_mb, \
             patch.object(main, "save_custom_standard_structure") as mock_save:
            mock_mb.askyesno.return_value = False
            app._save_current_as_standard_structure()
            mock_save.assert_not_called()

    def test_fonctionne_sans_tournoi_et_confirme_ecrit_uniquement_le_magasin_global(self):
        app = _FakeStandaloneApp(db=None)
        app._blind_row_vars = [_row_vars(15, 25, 50, 0)]
        with patch.object(main, "messagebox") as mock_mb, \
             patch.object(main, "save_custom_standard_structure") as mock_save:
            mock_mb.askyesno.return_value = True
            app._save_current_as_standard_structure()
            mock_save.assert_called_once()
        # Rien d'autre ne doit avoir été touché : self._standalone_blind_
        # rounds reste tel quel (cette fonction ne l'altère jamais, avec
        # ou sans tournoi — voir sa docstring, "n'applique PAS cette
        # structure").
        self.assertEqual(app._standalone_blind_rounds, [])


class CheckRemoteDeviceRequestsWithoutTournamentTest(unittest.TestCase):
    def test_retour_immediat_sans_effet_de_bord(self):
        class _FakeApp:
            _check_remote_device_requests = main.App._check_remote_device_requests

            def __init__(self):
                self.db = None

            def _is_ca_tab_active(self):
                raise AssertionError("ne doit jamais être appelée sans tournoi")

        app = _FakeApp()
        app._check_remote_device_requests()  # ne doit lever aucune exception


class OpenWindowsNeverRegisteredTest(unittest.TestCase):
    """Garantie indirecte (precaution utilisateur "ne pas enregistrer le
    mode sans tournoi dans le Lobby") : _build_tabs standalone ne fait
    jamais référence à open_windows, et open_senaco() non plus (déjà
    vérifié par AST ci-dessus) — ici, on vérifie que le registre reste
    bien vide tant que rien ne l'enregistre explicitement.

    Registre redirigé vers un dossier temporaire dédié (jamais
    ~/.poker_tournament, le VRAI registre de l'utilisateur — qui peut
    légitimement contenir un vrai tournoi encore ouvert par ailleurs sur
    cette même machine, auquel cas lire le vrai registre ferait échouer
    ce test à tort, sans rapport avec "Ouvrir Senaco" lui-même) — même
    précaution que tests/test_fermeture_un_tournoi_parmi_plusieurs.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="open_windows_never_registered_test_")
        self.addCleanup(self._tmp.cleanup)
        registry_path = os.path.join(self._tmp.name, "open_windows.json")
        patcher = patch.object(open_windows, "_registry_path", return_value=registry_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_registre_vide_sans_enregistrement_explicite(self):
        self.assertEqual(open_windows.list_open_paths(), [])


# =====================================================================
# 7. Onglet Paramètres sans tournoi (demande explicite du 2026-10-14)
# =====================================================================
class BuildTabsIncludesSettingsStructuralTest(_SourceHelper, unittest.TestCase):
    def test_branche_standalone_ajoute_l_onglet_parametres(self):
        func = self._find_method("App", "_build_tabs")
        seg = self._source_of(func)
        start = seg.index("if self.db is None:")
        end = seg.index("self.players_tab = ttk.Frame(self.notebook)")
        standalone_branch = seg[start:end]
        self.assertIn('self.notebook.add(self.settings_tab, text="Paramètres")', standalone_branch)
        self.assertIn("self._build_settings_tab_standalone()", standalone_branch)


class SettingsTabStandaloneStructuralTest(_SourceHelper, unittest.TestCase):
    """_build_settings_tab_standalone ne doit JAMAIS accéder à self.db
    (aucun tournoi n'existe dans ce mode) — seules les docstrings
    peuvent le mentionner en explication. Tous les tests ci-dessous
    travaillent donc sur le CODE SEUL (docstring exclue), jamais sur le
    texte brut complet — sans quoi la docstring de cette méthode, qui
    énumère elle-même les champs exclus en explication, ferait échouer
    à tort toute recherche de sous-chaîne naïve."""

    def _code_only(self):
        func = self._find_method("App", "_build_settings_tab_standalone")
        body_stmts = func.body
        if (
            body_stmts
            and isinstance(body_stmts[0], ast.Expr)
            and isinstance(body_stmts[0].value, ast.Constant)
            and isinstance(body_stmts[0].value.value, str)
        ):
            body_stmts = body_stmts[1:]
        return "\n".join(self._source_of(s) for s in body_stmts)

    def test_aucun_acces_reel_a_self_db(self):
        self.assertNotIn("self.db", self._code_only())

    def test_champs_tournoi_specifiques_absents(self):
        code_only = self._code_only()
        for forbidden in (
            "tournament_name", "Mode Test", "accueil_management",
            "primes_enabled", "Calculer les primes", "bounty_amount",
            "pko_mode", "undo_elimination_timeout_minutes",
            "start_small_blind", "_generate_custom_blind_structure",
        ):
            self.assertNotIn(forbidden, code_only, f"{forbidden!r} ne devrait pas apparaître sans tournoi")

    def test_champs_generaux_et_defauts_presents(self):
        code_only = self._code_only()
        for expected in (
            "club_name", "whatsapp_group_name", "buyin_amount", "max_seats_per_table",
            "rake_percent", "movement_signal_wav_path", "movement_signal_duration_ms",
            "tournament_day_folder", "tournament_days_of_week",
            "check_multi_table_conflict", "BB_REBALANCE_PROMPT_PREF_KEY",
            "elimination_banner_seconds",
        ):
            self.assertIn(expected, code_only, f"{expected!r} devrait apparaître sans tournoi")


class _FakeStandaloneSettingsApp:
    """Doublure légère SANS self.db du tout (ni même None) : toute
    tentative d'accès à self.db dans les méthodes testées lèverait
    immédiatement une AttributeError, preuve qu'aucune écriture base
    n'est tentée."""

    _save_standalone_tournament_pref = main.App._save_standalone_tournament_pref
    _save_standalone_tournament_days = main.App._save_standalone_tournament_days
    _save_standalone_elimination_banner_seconds = main.App._save_standalone_elimination_banner_seconds

    def __init__(self):
        self.settings_vars = {}
        self.tournament_day_vars = {}


class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class SaveStandaloneTournamentPrefTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="standalone_settings_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "last_settings.json")
        patcher = patch.object(tournament_prefs, "_prefs_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_ecrit_uniquement_dans_tournament_prefs(self):
        app = _FakeStandaloneSettingsApp()
        app.settings_vars["buyin_amount"] = _FakeVar("25")
        app._save_standalone_tournament_pref("buyin_amount")
        self.assertEqual(tournament_prefs.load_last_settings()["buyin_amount"], "25")

    def test_fusionne_sans_effacer_les_autres_cles(self):
        app = _FakeStandaloneSettingsApp()
        app.settings_vars["buyin_amount"] = _FakeVar("25")
        app._save_standalone_tournament_pref("buyin_amount")
        app.settings_vars["rake_percent"] = _FakeVar("5")
        app._save_standalone_tournament_pref("rake_percent")
        saved = tournament_prefs.load_last_settings()
        self.assertEqual(saved["buyin_amount"], "25")
        self.assertEqual(saved["rake_percent"], "5")

    def test_jours_de_tournoi_ecrit_uniquement_dans_tournament_prefs(self):
        app = _FakeStandaloneSettingsApp()
        app.tournament_day_vars = {0: _FakeVar(True), 1: _FakeVar(False), 4: _FakeVar(True)}
        app._save_standalone_tournament_days()
        self.assertEqual(tournament_prefs.load_last_settings()["tournament_days_of_week"], "0,4")

    def test_duree_bandeau_elimination_validee_et_ecrite(self):
        app = _FakeStandaloneSettingsApp()
        app._save_standalone_elimination_banner_seconds(_FakeVar("45"))  # > 30, plafonné
        self.assertEqual(tournament_prefs.load_last_settings()["elimination_banner_seconds"], 30)

    def test_duree_bandeau_elimination_valeur_invalide_ignoree_sans_lever(self):
        app = _FakeStandaloneSettingsApp()
        app._save_standalone_elimination_banner_seconds(_FakeVar("abc"))  # ne doit pas lever
        self.assertEqual(tournament_prefs.load_last_settings(), {})


class RecupererBlindesTooltipTest(_SourceHelper, unittest.TestCase):
    """Correction explicite du 2026-10-14 : l'infobulle "Récupérer
    Blindes" mentionnait encore "ce tournoi" inconditionnellement."""

    def test_tooltip_conditionnee_par_self_db(self):
        func = self._find_method("App", "_build_blinds_tab")
        seg = self._source_of(func)
        idx = seg.index("load_btn = ttk.Button(")
        tooltip_seg = seg[idx:idx + 600]
        self.assertIn('if self.db is not None else "."', tooltip_seg)


if __name__ == "__main__":
    unittest.main()
