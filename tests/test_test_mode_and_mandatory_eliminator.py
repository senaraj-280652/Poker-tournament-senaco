# -*- coding: utf-8 -*-
"""Couverture automatisée des points 3 et 4 de la demande du 2026-09-09
(complément à l'interrupteur général "Calculer les primes", voir
tests/test_primes_enabled_toggle.py pour les points 1, 2 et 5) :

Point 3 — "Mode Test" (Paramètres généraux, juste sous "Parcourir..." du
dossier par défaut) : case à cocher PROPRE À CE PROCESS, jamais
mémorisée (ni export_prefs, ni réglage de tournoi) — décochée à CHAQUE
lancement du logiciel, PAS à chaque session comme "Calculer les primes".
Affiche "MODE TEST" bien en évidence (titre de fenêtre + bandeau
interne) tant qu'actif, pour ne jamais l'oublier.

Point 4 — élimination et Mode Test (CORRIGÉ deux fois après relecture
utilisateur, voir les dates dans chaque test concerné) :
- éliminateur obligatoire pour une élimination INDIVIDUELLE ⟺ primes
  activées ET ce joueur précis porte une bounty > 0 (classique OU PKO) —
  totalement INDÉPENDANT du Mode Test, qui ne facilite jamais que
  l'élimination GROUPÉE. Le bouton "Ignorer (pas de prime)" de
  _ask_eliminator disparaît de lui-même dès que mandatory=True (devient
  "Annuler l'élimination", inchangé) ;
- élimination GROUPÉE : bloquée en fonctionnement normal dès que les
  primes sont activées (message invitant à éliminer un par un ou à
  activer le Mode Test) ; autorisée en Mode Test, y compris en PKO avec
  bounty en jeu, SANS créer de faux éliminateur, SANS attribuer la
  bounty à qui que ce soit — mais celle-ci est REMISE À 0 (abandonnée,
  jamais laissée non nulle sur un joueur désormais éliminé/inactif :
  état jugé incohérent même à des fins de test, voir Database.
  eliminate_player: orphan_bounty_ok) ; jamais possible hors Mode Test.

`_ask_eliminator`/`_eliminate_selected` ouvrent de vraies fenêtres Tk
modales (Toplevel + grab_set + wait_window) : comme le reste de la
suite (voir test_single_tournament_at_a_time.py:NoBypassPathExistsTest),
leur câblage est vérifié par inspection de l'AST plutôt que par un vrai
clic de souris. `_remote_eliminate` (pas de dialogue, logique pure) est
lui testé fonctionnellement, comme dans test_pko_mechanism.py."""
import ast
import os
import queue
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402


def _new_db(tmp_dir, name, **settings):
    path = os.path.join(tmp_dir, f"{name}.tournoi")
    db = database.Database(path)
    if settings:
        db.set_settings({k: str(v) for k, v in settings.items()})
    return db


class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


# =======================================================================
# Point 3 : Mode Test — jamais mémorisé, propre à ce process.
# =======================================================================
class _FakeAppForTestMode:
    def __init__(self, test_mode=False):
        self.test_mode_var = _FakeVar(test_mode)
        self.db = None
        self.title_calls = []

        class _FakeHeaderLabel:
            def __init__(self):
                self.configured = []

            def config(self, **kwargs):
                self.configured.append(kwargs)

        self.header_title_lbl = _FakeHeaderLabel()

    def title(self, text):
        self.title_calls.append(text)

    def get_tournament_date(self):
        return None


class TestModeFlagTest(unittest.TestCase):
    def test_test_mode_enabled_reflete_la_var(self):
        app = _FakeAppForTestMode(test_mode=False)
        self.assertFalse(main.App._test_mode_enabled(app))
        app.test_mode_var.set(True)
        self.assertTrue(main.App._test_mode_enabled(app))

    def test_toggle_rafraichit_le_titre(self):
        app = _FakeAppForTestMode(test_mode=True)
        called = []
        app._update_window_title = lambda: called.append(True)
        main.App._on_test_mode_toggle(app)
        self.assertEqual(called, [True])


class WindowTitleModeTestIndicatorTest(unittest.TestCase):
    """_update_window_title (main.py) : affiche "MODE TEST" en évidence
    tant que la case est cochée, jamais sinon — testé avec une doublure
    de fenêtre (pas de vrai Tk nécessaire, seule self.title()/
    header_title_lbl.config() sont utilisées ici)."""

    def test_titre_normal_sans_mode_test(self):
        app = _FakeAppForTestMode(test_mode=False)
        main.App._update_window_title(app)
        self.assertNotIn("MODE TEST", app.title_calls[-1])

    def test_titre_signale_le_mode_test(self):
        app = _FakeAppForTestMode(test_mode=True)
        main.App._update_window_title(app)
        self.assertIn("MODE TEST", app.title_calls[-1])

    def test_bandeau_interne_signale_aussi_le_mode_test(self):
        app = _FakeAppForTestMode(test_mode=True)
        main.App._update_window_title(app)
        last_config = app.header_title_lbl.configured[-1]
        self.assertIn("MODE TEST", last_config["text"])

    def test_bandeau_interne_ne_signale_rien_hors_mode_test(self):
        app = _FakeAppForTestMode(test_mode=False)
        main.App._update_window_title(app)
        last_config = app.header_title_lbl.configured[-1]
        self.assertNotIn("MODE TEST", last_config["text"])

    def test_fonctionne_meme_avant_que_test_mode_var_existe(self):
        """_update_window_title est déjà appelée dès l'écran d'accueil
        (avant tout tournoi choisi) : ne doit jamais planter si
        test_mode_var n'existe pas encore pour une raison quelconque."""
        app = _FakeAppForTestMode(test_mode=False)
        del app.test_mode_var
        main.App._update_window_title(app)  # ne doit lever aucune exception
        self.assertNotIn("MODE TEST", app.title_calls[-1])


class TestModeJamaisMemoriseStructurelTest(unittest.TestCase):
    """Vérifie par inspection de l'AST que self.test_mode_var n'est
    JAMAIS lu ni écrit via export_prefs, ni ajouté à self.settings_vars
    (ce qui le ferait persister comme un réglage de tournoi via
    "Enregistrer les paramètres") — seulement une BooleanVar en mémoire."""

    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=main_py)

    def test_jamais_ajoute_a_settings_vars(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
                if node.value.attr == "settings_vars":
                    key = node.slice
                    if isinstance(key, ast.Constant) and key.value == "test_mode":
                        self.fail("test_mode_var ne doit jamais être ajoutée à self.settings_vars")

    def test_jamais_lu_ou_ecrit_via_export_prefs(self):
        for node in ast.walk(self.tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("load_value", "save_value")
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "export_prefs"):
                args_src = ast.unparse(node)
                self.assertNotIn(
                    "test_mode", args_src,
                    "Mode Test ne doit jamais passer par export_prefs (pas mémorisé)",
                )

    def test_initialise_a_false_dans_init(self):
        app_class = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.ClassDef) and n.name == "App"
        )
        func = next(
            n for n in ast.walk(app_class)
            if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        found = False
        for node in ast.walk(func):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Attribute) and t.attr == "test_mode_var" for t in node.targets)):
                self.assertIn("False", ast.unparse(node.value))
                found = True
        self.assertTrue(found, "self.test_mode_var doit être initialisée dans App.__init__")


# =======================================================================
# Database.eliminate_player(orphan_bounty_ok=True) en isolation, SANS
# passer par main.App (comme tests/test_pko_mechanism.py) — demande du
# 2026-09-09 (3e relecture) : la bounty abandonnée doit être remise à 0,
# jamais laissée non nulle sur un joueur désormais éliminé/inactif.
# =======================================================================
class OrphanBountyOkResetsBountyToZeroTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="orphan_bounty_ok_test_")
        self.addCleanup(self._tmp.cleanup)

    def _build(self, **settings):
        db = _new_db(self._tmp.name, "orphan", **settings)
        self.addCleanup(db.conn.close)
        return db

    def test_orphan_bounty_ok_remet_la_bounty_a_zero_en_pko(self):
        db = self._build(pko_mode=1, bounty_amount=100)
        a = db.add_player("Alice")  # reste actif
        b = db.add_player("Bob")
        db.add_player("Chris")  # reste actif (évite la fin de tournoi)
        self.assertEqual(db.get_player(b)["bounty"], 100)
        db.eliminate_player(b, eliminated_by_id=None, orphan_bounty_ok=True)
        bob = db.get_player(b)
        self.assertEqual(bob["status"], "eliminated")
        self.assertEqual(bob["bounty"], 0, "bounty abandonnée : jamais laissée non nulle")
        self.assertEqual(bob["bounty_won"], 0, "abandonnée, pas attribuée à elle-même")
        self.assertEqual(db.get_player(a)["bounty_won"], 0, "abandonnée, pas attribuée à un autre joueur")
        self.assertEqual(len(db.get_bounty_events()), 0, "aucune ligne d'historique pour un abandon")

    def test_orphan_bounty_ok_sans_effet_si_bounty_deja_nulle(self):
        db = self._build(pko_mode=1, bounty_amount=0)
        db.add_player("Alice")
        b = db.add_player("Bob")
        db.add_player("Chris")
        db.eliminate_player(b, eliminated_by_id=None, orphan_bounty_ok=True)
        self.assertEqual(db.get_player(b)["bounty"], 0)

    def test_orphan_bounty_ok_sans_effet_si_un_eliminateur_est_fourni(self):
        """orphan_bounty_ok ne change rien au comportement normal (transfert
        complet à l'éliminateur) dès qu'un éliminateur est désigné — le
        chemin "abandon" ne concerne que l'absence d'éliminateur."""
        db = self._build(pko_mode=1, bounty_amount=100)
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.add_player("Chris")
        db.eliminate_player(b, eliminated_by_id=a, orphan_bounty_ok=True)
        alice = db.get_player(a)
        self.assertEqual(alice["bounty_won"], 50)  # transfert PKO normal, inchangé
        self.assertEqual(db.get_player(b)["bounty"], 0)  # déjà remis à 0 par le transfert normal

    def test_hors_orphan_bounty_ok_la_garde_pko_reste_intacte(self):
        """Non-régression absolue : SANS orphan_bounty_ok (fonctionnement
        normal), la ValueError PKO reste levée comme avant — ce
        contournement n'existe QUE si explicitement demandé par
        l'appelant (voir App._eliminate_selected, réservé au Mode Test)."""
        db = self._build(pko_mode=1, bounty_amount=100)
        db.add_player("Alice")
        b = db.add_player("Bob")
        db.add_player("Chris")
        with self.assertRaises(ValueError):
            db.eliminate_player(b, eliminated_by_id=None, orphan_bounty_ok=False)
        self.assertEqual(db.get_player(b)["status"], "active")
        self.assertEqual(db.get_player(b)["bounty"], 100)  # intacte, rien n'a été modifié

    def test_orphan_bounty_ok_classique_ne_plante_pas_et_ne_credite_personne(self):
        """Hors PKO, la garde n'existe déjà pas — orphan_bounty_ok=True
        est un no-op vis-à-vis de la garde elle-même, mais la remise à 0
        de la bounty abandonnée s'applique quand même (même principe
        d'incohérence évitée, qu'importe le mode)."""
        db = self._build(pko_mode=0, bounty_amount=100)
        db.add_player("Alice")
        b = db.add_player("Bob")
        db.add_player("Chris")
        db.eliminate_player(b, eliminated_by_id=None, orphan_bounty_ok=True)
        bob = db.get_player(b)
        self.assertEqual(bob["status"], "eliminated")
        self.assertEqual(bob["bounty"], 0)
        self.assertEqual(bob["bounty_won"], 0)


# =======================================================================
# Point 4 : câblage de l'élimination (dialogues Tk réels -> inspection
# de l'AST, comme NoBypassPathExistsTest).
# =======================================================================
class EliminateSelectedWiringTest(unittest.TestCase):
    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.source = f.read()
        self.tree = ast.parse(self.source, filename=main_py)

    def _find_function(self, name):
        matches = [n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == name]
        self.assertEqual(len(matches), 1, f"{name} introuvable ou nom ambigu dans main.py")
        return matches[0]

    def test_elimination_groupee_bloquee_hors_mode_test(self):
        """Le message d'erreur bloquant l'élimination groupée doit
        explicitement mentionner le Mode Test comme échappatoire."""
        func = self._find_function("_eliminate_selected")
        src = ast.unparse(func)
        self.assertIn("Mode Test", src)
        self.assertIn("Élimination groupée indisponible", src)

    def test_mandatory_calcule_a_partir_des_primes_et_de_la_bounty_pas_du_mode_test(self):
        """CORRECTION du 2026-09-09 (2e relecture utilisateur) : `mandatory`
        (élimination INDIVIDUELLE) ne doit dépendre que de "une prime
        est-elle réellement en jeu" — primes activées ET ce joueur précis
        porte une bounty > 0 — jamais du Mode Test (qui ne facilite QUE
        l'élimination GROUPÉE, voir test_elimination_groupee_bloquee_hors_
        mode_test) ni de pko_mode seul (le classique aussi doit désormais
        imposer un éliminateur dès qu'une bounty classique est en jeu)."""
        func = self._find_function("_eliminate_selected")
        assign = next(
            n for n in ast.walk(func)
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "mandatory" for t in n.targets)
        )
        expr_src = ast.unparse(assign.value)
        self.assertNotIn("test_mode", expr_src)
        self.assertIn("primes_matter", expr_src)
        self.assertIn("bounty", expr_src)

    def test_ask_eliminator_toujours_appelee_pour_une_elimination_individuelle(self):
        func = self._find_function("_eliminate_selected")
        calls = [
            n for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "_ask_eliminator"
        ]
        self.assertEqual(len(calls), 1)
        kw = {k.arg: k for k in calls[0].keywords}
        self.assertIn("mandatory", kw)
        self.assertEqual(ast.unparse(kw["mandatory"].value), "mandatory")

    def test_eliminate_player_recoit_orphan_bounty_ok_pour_le_mode_test(self):
        """Point 2 de la demande du 2026-09-09 (2e relecture) : le
        contournement PKO du Mode Test passe par le paramètre explicite
        `orphan_bounty_ok` de Database.eliminate_player — jamais par un
        faux éliminateur."""
        func = self._find_function("_eliminate_selected")
        calls = [
            n for n in ast.walk(func)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "eliminate_player"
        ]
        self.assertEqual(len(calls), 1)
        kw = {k.arg: k for k in calls[0].keywords}
        self.assertIn("orphan_bounty_ok", kw)


class _FakeRemoteApp:
    """Doublure de App pour _remote_eliminate : pas de notion de Mode
    Test ici (CORRECTION du 2026-09-09, 2e relecture — voir plus bas),
    puisque _remote_eliminate ne le consulte plus jamais."""

    def __init__(self, db):
        self.db = db
        self.voice_command_queue = queue.Queue()
        self._remote_elimination_results = {}
        self.clock_window = None

    def _queue_elimination_banner(self, *a, **k):
        pass

    def _trigger_movement_alert(self, *a, **k):
        pass

    def _finish_movement_alert(self, *a, **k):
        pass

    def _refresh_all(self):
        pass

    def _refresh_remote_players_cache(self):
        pass

    def _check_pending_rebalance(self):
        pass


class RemoteEliminateMandatoryWhenBountyAtStakeTest(unittest.TestCase):
    """Fonctionnel (pas de dialogue Tk, logique pure) : CORRECTION du
    2026-09-09 (2e relecture utilisateur) — un éliminateur n'est
    obligatoire, depuis le téléphone, QUE si une attribution de bounty en
    dépend réellement (primes activées ET ce joueur précis porte une
    bounty > 0), que ce soit en mode classique OU en PKO (nouveauté :
    avant cette correction, seul PKO+bounty>0 était protégé). Le Mode
    Test n'a PLUS aucun effet ici (pas de notion de "vrac" au téléphone,
    toujours une seule élimination à la fois) — voir aussi
    test_pko_mechanism.py:RemoteEliminatePkoOrphanTest pour la couverture
    PKO dédiée."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="mandatory_elim_test_")
        self.addCleanup(self._tmp.cleanup)

    def _build(self, name, **settings):
        db = _new_db(self._tmp.name, name, **settings)
        self.addCleanup(db.conn.close)
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.add_player("Chris")
        return db, a, b

    def test_refuse_sans_eliminateur_bounty_classique_en_jeu(self):
        """Nouveauté de la demande du 2026-09-09 : le bounty CLASSIQUE
        (hors PKO) impose désormais, lui aussi, un éliminateur dès qu'une
        attribution est en jeu — auparavant seul PKO l'imposait."""
        db, a, b = self._build("classique_bounty", pko_mode=0, bounty_amount=50)
        fake = _FakeRemoteApp(db)
        main.App._remote_eliminate(fake, b, None, request_id="req")
        result = fake._remote_elimination_results["req"]
        self.assertFalse(result["ok"])
        self.assertIn("prime", result["message"])
        self.assertEqual(db.get_player(b)["status"], "active")

    def test_autorise_sans_eliminateur_si_bounty_nulle_hors_pko(self):
        """Non-régression : sans bounty configurée, aucune contrainte —
        comportement historique inchangé."""
        db, a, b = self._build("classique_sans_bounty", pko_mode=0, bounty_amount=0)
        fake = _FakeRemoteApp(db)
        main.App._remote_eliminate(fake, b, None, request_id="req")
        self.assertTrue(fake._remote_elimination_results["req"]["ok"])
        self.assertEqual(db.get_player(b)["status"], "eliminated")

    def test_autorise_sans_eliminateur_quand_primes_desactivees_meme_bounty_configuree(self):
        """Point 3 de la demande du 2026-09-09 : "Calculer les primes" =
        OFF -> aucun garde PKO/bounty ne doit imposer un éliminateur,
        même si un montant de bounty est configuré dans les réglages (il
        n'est de toute façon jamais stampé à l'inscription, voir
        Database.add_player)."""
        db, a, b = self._build("primes_off", pko_mode=1, bounty_amount=50, primes_enabled=0)
        self.assertEqual(db.get_player(b)["bounty"], 0)  # confirmation : jamais stampé
        fake = _FakeRemoteApp(db)
        main.App._remote_eliminate(fake, b, None, request_id="req")
        result = fake._remote_elimination_results["req"]
        self.assertTrue(result["ok"])
        self.assertEqual(db.get_player(b)["status"], "eliminated")

    def test_avec_eliminateur_valide_fonctionne_classique_et_pko(self):
        for pko_mode in (0, 1):
            with self.subTest(pko_mode=pko_mode):
                db, a, b = self._build(f"ok_pko_{pko_mode}", pko_mode=pko_mode, bounty_amount=50)
                fake = _FakeRemoteApp(db)
                main.App._remote_eliminate(fake, b, a, request_id="req")
                self.assertTrue(fake._remote_elimination_results["req"]["ok"])
                self.assertEqual(db.get_player(b)["status"], "eliminated")


# =======================================================================
# Point 2 (2e relecture, 2026-09-09) : élimination GROUPÉE en Mode Test,
# y compris en PKO — sans faux éliminateur, sans attribution de bounty,
# et sans laisser de données incohérentes.
# =======================================================================
class _FakeAppForGroupElimination:
    """Doublure de App : ne reprend que ce que _eliminate_selected lit ou
    appelle — self.db est une VRAIE Database, le reste (sélection,
    dialogues, rafraîchissement) des doublures minimales/espionnes.
    `answers` : eliminator_id à renvoyer pour chaque appel successif à
    _ask_eliminator (non utilisé ici puisque le Mode Test élimine en
    groupe sans jamais l'appeler, mais présent pour rester réaliste)."""

    def __init__(self, db, ids, test_mode):
        self.db = db
        self._ids = ids
        self.test_mode_var = _FakeVar(test_mode)
        self.checked_calls = 0
        self.confirmed = True
        self.finish_movement_alert_calls = 0
        self.trigger_movement_alert_calls = 0

    def _test_mode_enabled(self):
        return main.App._test_mode_enabled(self)

    def _checked_or_selected_ids(self):
        return list(self._ids)

    def _clear_checked(self):
        pass

    def _refresh_all(self):
        pass

    def _check_pending_rebalance(self):
        pass

    def _trigger_movement_alert(self):
        self.trigger_movement_alert_calls += 1

    def _finish_movement_alert(self):
        self.finish_movement_alert_calls += 1

    def _queue_elimination_banner(self, *a, **k):
        pass


class GroupEliminationTestModeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="group_elim_test_mode_test_")
        self.addCleanup(self._tmp.cleanup)

    def _build(self, n_players=4, **settings):
        db = _new_db(self._tmp.name, "group", **settings)
        self.addCleanup(db.conn.close)
        ids = [db.add_player(f"J{i}") for i in range(n_players)]
        return db, ids

    def _run(self, db, ids, test_mode, n_to_eliminate, confirm=True):
        # Élimine seulement une PARTIE des joueurs (jamais tous sauf un) :
        # laisser au moins 2 actifs après coup évite de déclencher la
        # clôture de la bounty finale du vainqueur (_close_out_winner_
        # bounty, un mécanisme totalement séparé, qui alimenterait à tort
        # bounty_won/bounty_events du DERNIER joueur restant et fausserait
        # les assertions "aucune attribution" ci-dessous).
        group_ids = ids[:n_to_eliminate]
        app = _FakeAppForGroupElimination(db, group_ids, test_mode)
        with patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.messagebox, "askyesno", return_value=confirm) as mock_confirm:
            main.App._eliminate_selected(app)
        return app, mock_error, mock_confirm, group_ids

    def test_groupe_bloque_hors_mode_test_avec_primes_activees(self):
        db, ids = self._build(n_players=5, pko_mode=1, bounty_amount=100)
        app, mock_error, mock_confirm, group_ids = self._run(db, ids, test_mode=False, n_to_eliminate=3)
        mock_error.assert_called_once()
        mock_confirm.assert_not_called()
        for pid in group_ids:
            self.assertEqual(db.get_player(pid)["status"], "active")  # rien n'a été éliminé

    def test_groupe_autorise_en_mode_test_meme_en_pko_avec_bounty(self):
        """Coeur du point 2 (2e relecture) : Mode Test + PKO + bounty en
        jeu -> élimination groupée autorisée, SANS faux éliminateur, SANS
        attribution de bounty, sans lever la ValueError PKO. 5 joueurs,
        3 éliminés en groupe, 2 restent actifs (pas de fin de tournoi —
        voir _run) : le mécanisme de clôture du vainqueur n'entre pas en
        jeu, la vérification "aucune attribution nulle part" est donc
        propre pour tous les joueurs, y compris ceux restés actifs."""
        db, ids = self._build(n_players=5, pko_mode=1, bounty_amount=100)
        for pid in ids:
            self.assertGreater(db.get_player(pid)["bounty"], 0)
        app, mock_error, mock_confirm, group_ids = self._run(db, ids, test_mode=True, n_to_eliminate=3)
        mock_error.assert_not_called()
        for pid in group_ids:
            p = db.get_player(pid)
            self.assertEqual(p["status"], "eliminated")
            self.assertIsNone(p["eliminated_by_name"], "aucun faux éliminateur créé")
        self.assertEqual(len(db.list_players(status="active")), 2)  # pas de fin de tournoi
        # Aucune attribution nulle part : ni kills, ni bounty_won, ni
        # bounty_events (y compris pour les 2 joueurs restés actifs).
        for pid in ids:
            self.assertEqual(db.get_player(pid)["kills"], 0)
            self.assertEqual(db.get_player(pid)["bounty_won"], 0)
        self.assertEqual(len(db.get_bounty_events()), 0)
        # Demande du 2026-09-09 (3e relecture) : la bounty ABANDONNÉE des
        # joueurs éliminés en groupe est remise à 0 (jamais laissée non
        # nulle sur un joueur inactif, même à des fins de test) — celle
        # des 2 joueurs restés ACTIFS, elle, reste intacte (rien ne les
        # concerne ici).
        for pid in group_ids:
            self.assertEqual(db.get_player(pid)["bounty"], 0, "bounty abandonnée, jamais laissée non nulle")
        for pid in ids:
            if pid not in group_ids:
                self.assertGreater(db.get_player(pid)["bounty"], 0, "joueurs encore actifs : intacts")

    def test_groupe_hors_primes_toujours_autorise_meme_hors_mode_test(self):
        """Point 3 : primes désactivées -> aucune raison de bloquer le
        groupé, Mode Test ou pas."""
        db, ids = self._build(n_players=5, pko_mode=1, bounty_amount=100, primes_enabled=0)
        app, mock_error, mock_confirm, group_ids = self._run(db, ids, test_mode=False, n_to_eliminate=3)
        mock_error.assert_not_called()
        for pid in group_ids:
            self.assertEqual(db.get_player(pid)["status"], "eliminated")

    def test_groupe_classique_en_mode_test_ne_leve_rien(self):
        """Non-PKO : orphan_bounty_ok est un no-op (la garde qu'il lève
        n'existe qu'en PKO), mais ne doit jamais faire planter le
        classique non plus."""
        db, ids = self._build(n_players=5, pko_mode=0, bounty_amount=100)
        app, mock_error, mock_confirm, group_ids = self._run(db, ids, test_mode=True, n_to_eliminate=3)
        mock_error.assert_not_called()
        for pid in group_ids:
            self.assertEqual(db.get_player(pid)["status"], "eliminated")


if __name__ == "__main__":
    unittest.main()
