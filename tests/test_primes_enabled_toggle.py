# -*- coding: utf-8 -*-
"""Couverture automatisée de l'interrupteur général "Calculer les primes"
(demande du 2026-09-09) : une case à cocher (Paramètres, à côté du titre
"Primes"), cochée par défaut, qui désactive COMPLÈTEMENT le calcul des
primes (présence/assiduité/classement/bounty classique/PKO) — jamais un
simple masquage à l'affichage — pour TOUS les tournois de la session en
cours (déjà ouverts, pas encore démarrés, ou créés plus tard), tout en
préservant les montants déjà configurés pour une réactivation future.

Règle de verrouillage (2e correction du 2026-09-09, après relecture
utilisateur) : modifiable depuis N'IMPORTE LEQUEL des tournois de la
session tant qu'AUCUN d'entre eux n'a démarré son chronomètre (même si
plusieurs sont déjà créés/ouverts) ; DÉFINITIVEMENT verrouillée pour
TOUTE LA SESSION dès que le PREMIER tournoi démarre (`clock_started`
0->1, voir App._clock_resume) — y compris les tournois déjà commencés,
terminés, pas encore commencés ou créés plus tard — jusqu'à ce que TOUS
les tournois de cette session soient FERMÉS (pas seulement "terminés" :
voir open_windows.list_open_paths(), vide uniquement quand tous ont
refermé leur fenêtre). Point 2 (même demande) : ce n'est pas seulement
la case "Calculer les primes" qui se verrouille alors, mais TOUTE la
section Primes (montants, PKO...), même si elle reste cochée.

Architecture testée ici :
- `Database._primes_enabled`/`Database.primes_enabled` (database.py) :
  source de vérité PAR TOURNOI, consultée par tous les calculs.
- `Database.DEFAULT_SETTINGS["primes_enabled"] = "1"` + INSERT OR IGNORE
  (_init_defaults) : compatibilité automatique des anciens fichiers.
- `main._primes_enabled_proposed`/`main._set_primes_enabled_proposed` :
  valeur "proposée" globale à la session (export_prefs.json, même
  mécanisme que SINGLE_TOURNAMENT_PREF_KEY).
- `open_windows.mark_primes_session_started`/`primes_session_started` :
  le VRAI verrou de session (fichier séparé, ~/.poker_tournament/
  primes_session_started.json) — posé une fois pour toutes au moment où
  un tournoi démarre (voir App._clock_resume), et qui ne se lève QUE
  lorsque list_open_paths() devient complètement vide (tous fermés) —
  jamais simplement "plus aucun DÉMARRÉ actuellement ouvert" (c'était le
  bug de la 1ère version, voir PrimesSessionStartedTest.
  test_scenario_exact_de_la_demande_open_puis_sng_open_ferme_reste_verrouille).
  `main._primes_session_locked` n'est plus qu'une délégation directe.
- `main._sync_primes_enabled_pref` : fait converger la copie SQLite d'UN
  tournoi vers la valeur globale proposée, tant qu'il n'a pas démarré —
  appelée à chaque tick (voir App._tick) et défensivement dans
  App._clock_resume juste avant de démarrer.
- `main.App._on_primes_enabled_toggle` / `_sync_primes_enabled_checkbox` /
  `_update_primes_section_state` : la case ET (point 2) tout le reste de
  la section Primes, grisés ensemble dès que la session est verrouillée.

Le Mode Test et la règle "éliminateur obligatoire en fonctionnement
normal" (points 3 et 4 de la même demande) sont couverts séparément
dans tests/test_test_mode_and_mandatory_eliminator.py.

Comme tests/test_pko_mechanism.py : les tests "Database" utilisent de
vrais fichiers .tournoi SYNTHÉTIQUES en dossier temporaire, jamais un
vrai fichier ni ~/.poker_tournament. Comme tests/test_single_tournament_
at_a_time.py : les tests "main.py" utilisent des doublures légères
(_FakeApp/_FakeVar/_FakeCheckbutton), export_prefs/open_windows mockés,
jamais le vrai fichier de préférences."""
import ast
import os
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


# ---------------------------------------------------------------------
# 1. Compatibilité des anciens tournois : clé absente -> primes activées.
# ---------------------------------------------------------------------
class CompatibiliteAncienTournoiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_toggle_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_valeur_par_defaut_activee_sur_un_fichier_neuf(self):
        db = _new_db(self._tmp.name, "neuf")
        self.addCleanup(db.conn.close)
        self.assertTrue(db.primes_enabled())
        self.assertEqual(db.get_setting("primes_enabled"), "1")

    def test_cle_absente_en_base_simule_un_ancien_fichier_reste_activee(self):
        """Simule un VRAI ancien fichier .tournoi, créé avant l'existence
        de ce réglage : retire directement la ligne "primes_enabled" de
        la table settings (INSERT OR IGNORE ne s'exécute qu'à l'ouverture
        du fichier, pas utile de rouvrir ici — le point testé est bien le
        comportement de primes_enabled() face à une clé manquante)."""
        db = _new_db(self._tmp.name, "ancien")
        self.addCleanup(db.conn.close)
        db.conn.execute("DELETE FROM settings WHERE key='primes_enabled'")
        db.conn.commit()
        self.assertIsNone(db.get_setting("primes_enabled"))
        self.assertTrue(db.primes_enabled(), "absente -> doit être traitée comme activée")

    def test_reouverture_du_meme_fichier_backfill_bien_la_cle_manquante(self):
        """_init_defaults (INSERT OR IGNORE) tourne à CHAQUE ouverture,
        pas seulement à la création : un ancien fichier rouvert doit se
        voir attribuer la clé par défaut sans jamais rien écraser si elle
        existe déjà."""
        path = os.path.join(self._tmp.name, "ancien2.tournoi")
        db1 = database.Database(path)
        db1.conn.execute("DELETE FROM settings WHERE key='primes_enabled'")
        db1.conn.commit()
        db1.conn.close()

        db2 = database.Database(path)
        self.addCleanup(db2.conn.close)
        self.assertEqual(db2.get_setting("primes_enabled"), "1")
        self.assertTrue(db2.primes_enabled())


# ---------------------------------------------------------------------
# 2. primes_enabled = 1 (par défaut) : comportement STRICTEMENT inchangé.
# ---------------------------------------------------------------------
class PrimesActiveesComportementInchangeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_toggle_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(
            self._tmp.name, "actif", bounty_amount=30, pko_mode=0,
            attendance_bonus_points=10, ranking_bonus_points=0,
        )
        self.addCleanup(self.db.conn.close)

    def test_add_player_stampe_bien_le_bounty_configure(self):
        pid = self.db.add_player("Alice")
        self.assertEqual(self.db.get_player(pid)["bounty"], 30)

    def test_rebuy_ajoute_bien_le_bounty_configure(self):
        pid = self.db.add_player("Alice")
        self.db.rebuy_player(pid)
        self.assertEqual(self.db.get_player(pid)["bounty"], 60)

    def test_elimination_incremente_kills_et_transfere_le_bounty(self):
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.eliminate_player(b, eliminated_by_id=a)
        alice = self.db.get_player(a)
        self.assertEqual(alice["kills"], 1)
        self.assertEqual(alice["bounty_won"], 30)
        self.assertEqual(self.db.get_bounty_events()[-1]["eliminated_name"], "Bob")

    def test_get_bonuses_non_vides(self):
        a = self.db.add_player("Alice")
        self.db.add_player("Bob")
        self.assertEqual(self.db.get_presence_bonuses()["Alice"], 10)
        rows = self.db.get_primes_summary()
        self.assertEqual(len(rows), 2)

    def test_garde_pko_eliminateur_obligatoire_reste_actif(self):
        """Non-régression du mécanisme du 2026-09-08 : primes activées +
        PKO -> toujours impossible d'éliminer sans éliminateur un joueur
        qui porte une bounty."""
        self.db.set_settings({"pko_mode": "1"})
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.assertGreater(self.db.get_player(b)["bounty"], 0)
        with self.assertRaises(ValueError):
            self.db.eliminate_player(b, eliminated_by_id=None)
        # Rien n'a été modifié par la tentative refusée.
        self.assertEqual(self.db.get_player(b)["status"], "active")


# ---------------------------------------------------------------------
# 3. primes_enabled = 0 : AUCUN calcul, nulle part.
# ---------------------------------------------------------------------
class PrimesDesactiveesAucunCalculTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_toggle_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(
            self._tmp.name, "off", bounty_amount=30, pko_mode=1, pko_cash_percent=50,
            attendance_bonus_points=10, assiduity_bonus_points=5,
            assiduity_consecutive_days=2, ranking_bonus_points=20,
            primes_enabled=0,
        )
        self.addCleanup(self.db.conn.close)

    def test_add_player_ne_stampe_aucun_bounty(self):
        pid = self.db.add_player("Alice")
        self.assertEqual(self.db.get_player(pid)["bounty"], 0)

    def test_rebuy_n_ajoute_aucun_bounty(self):
        pid = self.db.add_player("Alice")
        self.db.rebuy_player(pid)
        self.assertEqual(self.db.get_player(pid)["bounty"], 0)

    def test_elimination_ne_touche_ni_kills_ni_bounty_won_ni_bounty_events(self):
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        # Simule un joueur qui porterait quand même une bounty (ex :
        # valeur restée d'avant la désactivation) : même dans ce cas,
        # l'élimination ne doit RIEN en faire.
        self.db.conn.execute("UPDATE players SET bounty=999 WHERE id=?", (b,))
        self.db.conn.commit()
        n_events_before = len(self.db.get_bounty_events())
        self.db.eliminate_player(b, eliminated_by_id=a)
        alice = self.db.get_player(a)
        self.assertEqual(alice["kills"], 0, "aucun kill ne doit être compté")
        self.assertEqual(alice["bounty_won"], 0, "aucun transfert de bounty")
        self.assertEqual(alice["bounty"], 0, "aucune croissance de bounty PKO")
        self.assertEqual(
            len(self.db.get_bounty_events()), n_events_before,
            "aucune ligne d'historique bounty ajoutée",
        )

    def test_garde_pko_eliminateur_obligatoire_est_court_circuite(self):
        """Coeur de la demande : même bounty > 0 et pko_mode=1, une
        élimination SANS éliminateur ne doit plus jamais être bloquée
        quand les primes sont désactivées — l'iPhone doit pouvoir
        éliminer normalement."""
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.conn.execute("UPDATE players SET bounty=500 WHERE id=?", (b,))
        self.db.conn.commit()
        try:
            self.db.eliminate_player(b, eliminated_by_id=None)
        except ValueError:
            self.fail("la contrainte d'éliminateur obligatoire ne doit plus s'appliquer")
        self.assertEqual(self.db.get_player(b)["status"], "eliminated")

    def test_eliminated_by_name_et_elim_round_restent_enregistres(self):
        """Protection explicitement demandée : les informations
        indépendantes des primes (bandeau d'élimination, historique,
        statistiques) ne doivent JAMAIS disparaître."""
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.eliminate_player(b, eliminated_by_id=a)
        bob = self.db.get_player(b)
        self.assertEqual(bob["eliminated_by_name"], "Alice")
        self.assertIsNotNone(bob["elim_round"])
        self.assertIsNotNone(bob["elim_time"])

    def test_aucune_cloture_de_bounty_du_vainqueur(self):
        """_close_out_winner_bounty ne doit jamais être appelée quand les
        primes sont désactivées, même en mode PKO à la toute dernière
        élimination."""
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.conn.execute("UPDATE players SET bounty=500 WHERE id=?", (a,))
        self.db.conn.commit()
        self.db.eliminate_player(b, eliminated_by_id=a)  # Bob part, Alice seule active -> fin
        alice = self.db.get_player(a)
        self.assertEqual(alice["bounty_won"], 0)
        self.assertFalse(
            any(e["event_type"] == "victory_collect" for e in self.db.get_bounty_events())
        )

    def test_get_presence_bonuses_vide(self):
        self.db.add_player("Alice")
        self.assertEqual(self.db.get_presence_bonuses(), {})

    def test_get_assiduity_bonuses_vide(self):
        self.db.add_player("Alice")
        self.assertEqual(self.db.get_assiduity_bonuses(), [])

    def test_get_ranking_bonuses_vide(self):
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.eliminate_player(b, eliminated_by_id=a)
        self.assertEqual(self.db.get_ranking_bonuses(), [])

    def test_get_bounty_bonuses_vide(self):
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.eliminate_player(b, eliminated_by_id=a)
        self.assertEqual(self.db.get_bounty_bonuses(), [])

    def test_get_primes_summary_vide_le_tableau_primes_doit_rester_vide(self):
        a = self.db.add_player("Alice")
        b = self.db.add_player("Bob")
        self.db.eliminate_player(b, eliminated_by_id=a)
        self.assertEqual(self.db.get_primes_summary(), [])

    def test_les_valeurs_configurees_restent_intactes_pour_une_reactivation(self):
        """Les MONTANTS/pourcentages saisis dans les réglages Primes ne
        doivent jamais être effacés/remis à zéro par la désactivation —
        seul le CALCUL est court-circuité."""
        self.assertEqual(self.db.get_setting_int("bounty_amount"), 30)
        self.assertEqual(self.db.get_setting_int("attendance_bonus_points"), 10)
        self.assertEqual(self.db.get_setting_int("assiduity_bonus_points"), 5)
        self.assertEqual(self.db.get_setting_int("assiduity_consecutive_days"), 2)
        self.assertEqual(self.db.get_setting_int("ranking_bonus_points"), 20)
        self.assertEqual(self.db.get_setting_int("pko_mode"), 1)
        self.assertEqual(self.db.get_setting_int("pko_cash_percent"), 50)


# ---------------------------------------------------------------------
# 4. Décocher puis recocher (avant tout démarrage) : le calcul reprend
#    normalement, sans contamination résiduelle.
# ---------------------------------------------------------------------
class DecocherPuisRecocherTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_toggle_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name, "toggle", bounty_amount=30)
        self.addCleanup(self.db.conn.close)

    def test_reactivation_avant_inscription_restaure_le_calcul(self):
        self.db.set_settings({"primes_enabled": "0"})
        pid_off = self.db.add_player("Alice")
        self.assertEqual(self.db.get_player(pid_off)["bounty"], 0)

        self.db.set_settings({"primes_enabled": "1"})
        pid_on = self.db.add_player("Bob")
        self.assertEqual(self.db.get_player(pid_on)["bounty"], 30)
        # Alice, inscrite pendant que c'était désactivé, ne devient PAS
        # rétroactivement porteuse d'un bounty (comportement attendu :
        # seules les inscriptions/rebuys FUTURES sont concernées, comme
        # tout changement du montant du bounty lui-même).
        self.assertEqual(self.db.get_player(pid_off)["bounty"], 0)


# ---------------------------------------------------------------------
# Onglet Primes RÉELLEMENT vide (correction du 2026-09-09, 2e relecture) :
# App._refresh_bounty_tab n'insérait jusqu'ici une ligne "TOTAL" (à 0 pts)
# QUE si primes_rows était vide, indépendamment de l'état de la case —
# corrigé pour conditionner cette ligne sur Database.primes_enabled()
# elle-même, jamais sur une conséquence indirecte (un tournoi sans aucun
# joueur, primes ACTIVÉES, doit continuer à afficher "TOTAL : 0 pts").
# Fausses doublures de ttk.Treeview (pas de vrai Tk nécessaire) : seules
# insert/get_children/delete/heading/tag_configure sont utilisées par
# _refresh_bounty_tab.
# ---------------------------------------------------------------------
class _FakeTree:
    def __init__(self):
        self.rows = []  # dans l'ordre d'affichage ACTUEL (vidé par delete, comme un vrai Treeview)
        self.headings = {}

    def insert(self, parent, index, values=(), tags=()):
        row = {"values": values, "tags": tags}
        self.rows.append(row)
        return row  # sert aussi d'"iid" pour delete() ci-dessous

    def get_children(self):
        return list(self.rows)

    def delete(self, iid):
        try:
            self.rows.remove(iid)
        except ValueError:
            pass

    def heading(self, col, text=None, **kwargs):
        if text is not None:
            self.headings[col] = text

    def tag_configure(self, *a, **k):
        pass


class _FakeLabel:
    def config(self, **kwargs):
        pass


class _FakeTooltip:
    def __init__(self):
        self.column_texts = {}


class _FakeAppForBountyTab:
    """Doublure de App : ne reprend que ce que _refresh_bounty_tab lit ou
    appelle — self.db est une VRAIE Database sur un fichier .tournoi
    synthétique (comme tests/test_pko_mechanism.py), le reste (widgets
    Tk) des doublures minimales."""

    def __init__(self, db):
        self.db = db
        self.bounty_info_lbl = _FakeLabel()
        self.primes_tree = _FakeTree()
        self.primes_heading_tooltip = _FakeTooltip()
        self.primes_sort = {"column": None, "ascending": True}
        self.bounty_history_tree = _FakeTree()

    def _update_primes_sort_headings(self):
        main.App._update_primes_sort_headings(self)


class PrimesTabTrulyEmptyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_tab_empty_test_")
        self.addCleanup(self._tmp.cleanup)

    def _build(self, **settings):
        db = _new_db(self._tmp.name, "tab", **settings)
        self.addCleanup(db.conn.close)
        return db, _FakeAppForBountyTab(db)

    def test_total_absente_quand_primes_desactivees(self):
        db, app = self._build(primes_enabled=0, attendance_bonus_points=10)
        db.add_player("Alice")
        db.add_player("Bob")
        main.App._refresh_bounty_tab(app)
        self.assertEqual(app.primes_tree.rows, [], "aucune ligne, pas même TOTAL, quand les primes sont désactivées")

    def test_total_presente_quand_primes_activees_et_joueurs_inscrits(self):
        db, app = self._build(primes_enabled=1, attendance_bonus_points=10)
        db.add_player("Alice")
        db.add_player("Bob")
        main.App._refresh_bounty_tab(app)
        self.assertGreaterEqual(len(app.primes_tree.rows), 1)
        self.assertEqual(app.primes_tree.rows[0]["values"][0], "TOTAL")

    def test_total_presente_a_0_pts_meme_sans_aucun_joueur_primes_activees(self):
        """Non-régression explicite : ce n'est PAS "aucune ligne dans
        primes_rows" qui doit faire disparaître la ligne TOTAL, seulement
        l'état de la case elle-même — un tournoi flambant neuf, primes
        activées mais sans encore aucun joueur inscrit, doit continuer à
        afficher "TOTAL : 0 pts" comme avant cette correction."""
        db, app = self._build(primes_enabled=1)
        main.App._refresh_bounty_tab(app)
        self.assertEqual(len(app.primes_tree.rows), 1)
        self.assertEqual(app.primes_tree.rows[0]["values"][0], "TOTAL")

    def test_reactivation_restaure_l_affichage_normal(self):
        """"Quand on recoche les primes lors d'une future session,
        l'affichage normal revient exactement comme avant" (demande
        explicite du 2026-09-09)."""
        db, app = self._build(primes_enabled=0, attendance_bonus_points=10)
        db.add_player("Alice")
        main.App._refresh_bounty_tab(app)
        self.assertEqual(app.primes_tree.rows, [])

        db.set_settings({"primes_enabled": "1"})
        main.App._refresh_bounty_tab(app)
        self.assertGreaterEqual(len(app.primes_tree.rows), 2)  # TOTAL + Alice
        self.assertEqual(app.primes_tree.rows[0]["values"][0], "TOTAL")
        names = [r["values"][0] for r in app.primes_tree.rows[1:]]
        self.assertIn("Alice", names)


# =======================================================================
# main.py : valeur globale "proposée", verrouillage dérivé, synchronisation
# =======================================================================
class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value

    def set(self, value):
        self._value = value


class _FakeCheckbutton:
    def __init__(self, state="normal"):
        self._state = state
        self.configure_calls = []

    def configure(self, **kwargs):
        self.configure_calls.append(kwargs)
        if "state" in kwargs:
            self._state = kwargs["state"]

    def cget(self, key):
        assert key == "state"
        return self._state


class _FakeDb:
    """Doublure minimale pour les tests de logique pure de main.py (pas
    de vraie base SQLite) : seuls get_setting_int/set_setting sont
    utilisés par le code testé ici."""

    def __init__(self, settings=None):
        self._settings = dict(settings or {})
        self.set_setting_calls = []

    def get_setting_int(self, key, default=0):
        try:
            return int(self._settings.get(key, default))
        except (TypeError, ValueError):
            return default

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self.set_setting_calls.append((key, value))
        self._settings[key] = value


class _FakeApp:
    def __init__(self, db=None, primes_enabled=True):
        self.db = db
        self.primes_enabled_var = _FakeVar(primes_enabled)
        self.primes_enabled_check = _FakeCheckbutton()
        self._primes_section_widgets = []
        self.refresh_bounty_tab_calls = 0

    def _refresh_bounty_tab(self):
        self.refresh_bounty_tab_calls += 1

    def _update_primes_section_state(self, enabled, locked=None):
        main.App._update_primes_section_state(self, enabled, locked=locked)


class PrimesEnabledProposedTest(unittest.TestCase):
    def test_valeur_par_defaut_activee(self):
        with patch.object(main.export_prefs, "load_value", side_effect=lambda k, default=None: default):
            self.assertTrue(main._primes_enabled_proposed())

    def test_relit_bien_la_valeur_enregistree(self):
        with patch.object(main.export_prefs, "load_value", return_value=False):
            self.assertFalse(main._primes_enabled_proposed())
        with patch.object(main.export_prefs, "load_value", return_value=True):
            self.assertTrue(main._primes_enabled_proposed())

    def test_set_enregistre_sous_la_bonne_cle(self):
        with patch.object(main.export_prefs, "save_value") as mock_save:
            main._set_primes_enabled_proposed(False)
        mock_save.assert_called_once_with(main.PRIMES_ENABLED_PROPOSED_KEY, False)


class PrimesSessionLockedDelegatesTest(unittest.TestCase):
    """`main._primes_session_locked` : simple délégation à
    open_windows.primes_session_started() (voir PrimesSessionStartedTest
    ci-dessous pour la vraie logique, corrigée le 2026-09-09)."""

    def test_delegue_a_open_windows_primes_session_started(self):
        with patch.object(main.open_windows, "primes_session_started", return_value=True) as mock_started:
            self.assertTrue(main._primes_session_locked())
        mock_started.assert_called_once_with()
        with patch.object(main.open_windows, "primes_session_started", return_value=False):
            self.assertFalse(main._primes_session_locked())


class PrimesSessionStartedTest(unittest.TestCase):
    """open_windows.mark_primes_session_started/primes_session_started :
    CORRECTION du 2026-09-09 (relecture utilisateur) — le verrouillage de
    session ne doit PAS se lever simplement parce qu'aucun tournoi
    ACTUELLEMENT ouvert n'a clock_started=1 ; il doit rester verrouillé
    tant qu'il reste ne serait-ce qu'UN tournoi de la session ouvert,
    même si celui qui a effectivement démarré a depuis été refermé.
    Registre/drapeau redirigés vers un dossier temporaire dédié (jamais
    ~/.poker_tournament, le vrai registre de l'utilisateur) — même
    précaution que tests/test_open_windows_atomic_write.py."""

    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_primes_lock_test_")
        self.addCleanup(self._tmpdir_ctx.cleanup)
        registry_path = os.path.join(self._tmpdir_ctx.name, "open_windows.json")
        lock_path = os.path.join(self._tmpdir_ctx.name, "primes_session_started.json")
        patcher1 = patch.object(main.open_windows, "_registry_path", return_value=registry_path)
        patcher2 = patch.object(main.open_windows, "_primes_session_lock_path", return_value=lock_path)
        self.addCleanup(patcher1.stop)
        self.addCleanup(patcher2.stop)
        patcher1.start()
        patcher2.start()

    def test_rien_d_ouvert_jamais_verrouille(self):
        self.assertFalse(main.open_windows.primes_session_started())

    def test_ouverts_mais_jamais_demarres_pas_verrouille(self):
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.register("/tmp/B.tournoi")
        self.assertFalse(main.open_windows.primes_session_started())

    def test_demarrage_verrouille_toute_la_session(self):
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.mark_primes_session_started()
        self.assertTrue(main.open_windows.primes_session_started())

    def test_scenario_exact_de_la_demande_open_puis_sng_open_ferme_reste_verrouille(self):
        """LE scénario du 2026-09-09 : OPEN créé, Sit&Go créé (jamais
        démarré), l'OPEN démarre (verrouille la session), l'OPEN se
        ferme, le Sit&Go — TOUJOURS pas démarré — reste seul ouvert : la
        session doit RESTER verrouillée (et pas seulement parce qu'un
        tournoi non démarré est encore ouvert : même un Sit&Go qui ne
        démarrera jamais doit garder la session verrouillée)."""
        main.open_windows.register("/tmp/OPEN.tournoi")
        main.open_windows.register("/tmp/SNG.tournoi")
        self.assertFalse(main.open_windows.primes_session_started())

        main.open_windows.mark_primes_session_started()  # l'OPEN démarre
        self.assertTrue(main.open_windows.primes_session_started())

        main.open_windows.unregister("/tmp/OPEN.tournoi")  # l'OPEN se ferme
        self.assertTrue(
            main.open_windows.primes_session_started(),
            "le Sit&Go (jamais démarré) est encore ouvert -> session TOUJOURS verrouillée",
        )

        main.open_windows.unregister("/tmp/SNG.tournoi")  # dernier tournoi fermé
        self.assertFalse(
            main.open_windows.primes_session_started(),
            "plus aucun tournoi de la session ouvert -> déverrouillage automatique",
        )

    def test_reouverture_seule_d_un_tournoi_deja_demarre_d_une_session_anterieure(self):
        """Si un ancien tournoi déjà démarré (clock_started=1 en base,
        d'une session précédente entièrement close) est rouvert SEUL,
        alors qu'aucun drapeau de session n'est encore posé (le fichier a
        été nettoyé à la fermeture de la session précédente) : ce n'est
        PAS ce module qui doit détecter cela — voir main._clock_resume,
        qui repose le drapeau dès qu'il consulte un tournoi déjà démarré
        au moment opportun (resynchronisation défensive). Ici on vérifie
        seulement qu'ouvrir un tournoi tout seul, sans jamais appeler
        mark_primes_session_started, ne verrouille pas la session à tort."""
        main.open_windows.register("/tmp/ancien_deja_demarre.tournoi")
        self.assertFalse(main.open_windows.primes_session_started())

    def test_mark_est_idempotent(self):
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.mark_primes_session_started()
        main.open_windows.mark_primes_session_started()
        self.assertTrue(main.open_windows.primes_session_started())

    def test_fichier_de_verrouillage_supprime_a_la_fermeture_complete(self):
        """Nettoyage explicite (pas seulement "ignoré en lecture") :
        vérifie que le fichier lui-même disparaît du disque une fois la
        session entièrement close, pour ne jamais laisser une trace
        périmée."""
        lock_path = main.open_windows._primes_session_lock_path()
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.mark_primes_session_started()
        self.assertTrue(os.path.exists(lock_path))
        main.open_windows.unregister("/tmp/A.tournoi")
        main.open_windows.primes_session_started()  # déclenche le nettoyage
        self.assertFalse(os.path.exists(lock_path))

    # -------------------------------------------------------------
    # Point 4 de la demande du 2026-09-09 (2e relecture utilisateur) :
    # nettoyage ROBUSTE — pas seulement paresseux (à la prochaine lecture
    # de primes_session_started) — directement dans unregister (fermeture
    # du DERNIER tournoi) et register (résidu d'une session antérieure
    # jamais nettoyée proprement, ex. après un plantage).
    # -------------------------------------------------------------
    def test_unregister_du_dernier_tournoi_nettoie_immediatement_le_fichier(self):
        """Le fichier doit disparaître DÈS l'appel à unregister() lui-même
        — sans dépendre d'un futur appel à primes_session_started()."""
        lock_path = main.open_windows._primes_session_lock_path()
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.mark_primes_session_started()
        self.assertTrue(os.path.exists(lock_path))
        main.open_windows.unregister("/tmp/A.tournoi")
        self.assertFalse(
            os.path.exists(lock_path),
            "unregister() du dernier tournoi doit nettoyer IMMÉDIATEMENT, "
            "pas seulement au prochain appel de primes_session_started()",
        )

    def test_unregister_qui_n_est_pas_le_dernier_ne_nettoie_pas(self):
        """Si un autre tournoi de la session reste ouvert, unregister() de
        celui-ci ne doit surtout pas effacer le drapeau à tort."""
        lock_path = main.open_windows._primes_session_lock_path()
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.register("/tmp/B.tournoi")
        main.open_windows.mark_primes_session_started()
        main.open_windows.unregister("/tmp/A.tournoi")
        self.assertTrue(os.path.exists(lock_path))
        self.assertTrue(main.open_windows.primes_session_started())

    def test_register_nettoie_un_residu_de_session_crashee_avant_d_ajouter_la_fenetre(self):
        """Reproduit un plantage : le dernier processus d'une session a
        disparu SANS jamais appeler unregister (donc sans le nettoyage
        immédiat ci-dessus) — le registre PID redevient vide dès que
        _prune l'a constaté, mais le fichier `primes_session_started.json`
        traîne encore. Le tout PROCHAIN register() (nouvelle session) doit
        le nettoyer lui-même, AVANT même d'ajouter sa propre fenêtre,
        pour qu'un "started=true" périmé ne puisse jamais s'appliquer à
        cette nouvelle session."""
        lock_path = main.open_windows._primes_session_lock_path()
        main.open_windows.mark_primes_session_started()  # résidu, sans aucune fenêtre enregistrée
        self.assertTrue(os.path.exists(lock_path))
        # (registre PID déjà vide ici : aucun register() n'a eu lieu avant
        # ce mark_primes_session_started, exactement comme un fichier
        # resté seul après un plantage suivi d'un nettoyage du registre.)
        main.open_windows.register("/tmp/nouvelle_session.tournoi")
        self.assertFalse(
            os.path.exists(lock_path),
            "register() doit nettoyer un drapeau résiduel AVANT d'ajouter sa fenêtre",
        )
        self.assertFalse(main.open_windows.primes_session_started())

    def test_register_ne_nettoie_pas_si_une_session_est_deja_en_cours(self):
        """Si le registre n'était PAS vide avant cet ajout (une autre
        fenêtre de la session en cours est déjà là), register() ne doit
        surtout pas effacer le drapeau à tort."""
        lock_path = main.open_windows._primes_session_lock_path()
        main.open_windows.register("/tmp/A.tournoi")
        main.open_windows.mark_primes_session_started()
        main.open_windows.register("/tmp/B.tournoi")  # 2e fenêtre de la MÊME session
        self.assertTrue(os.path.exists(lock_path))
        self.assertTrue(main.open_windows.primes_session_started())


class SyncPrimesEnabledPrefTest(unittest.TestCase):
    def test_tournoi_pas_encore_demarre_converge_vers_la_valeur_globale(self):
        db = _FakeDb({"clock_started": 0, "primes_enabled": "1"})
        with patch.object(main, "_primes_enabled_proposed", return_value=False):
            main._sync_primes_enabled_pref(db)
        self.assertEqual(db.get_setting("primes_enabled"), "0")

    def test_tournoi_pas_encore_demarre_dans_l_autre_sens(self):
        db = _FakeDb({"clock_started": 0, "primes_enabled": "0"})
        with patch.object(main, "_primes_enabled_proposed", return_value=True):
            main._sync_primes_enabled_pref(db)
        self.assertEqual(db.get_setting("primes_enabled"), "1")

    def test_tournoi_deja_demarre_n_est_jamais_touche(self):
        """Coeur de la correction demandée par l'utilisateur : une fois
        démarré, la copie locale d'UN tournoi précis reste figée, même
        si la session globale n'est pas (encore, du point de vue d'un
        autre tournoi) considérée comme verrouillée."""
        db = _FakeDb({"clock_started": 1, "primes_enabled": "1"})
        with patch.object(main, "_primes_enabled_proposed", return_value=False):
            main._sync_primes_enabled_pref(db)
        self.assertEqual(db.get_setting("primes_enabled"), "1")
        self.assertEqual(db.set_setting_calls, [])

    def test_deja_a_jour_n_ecrit_pas_inutilement(self):
        db = _FakeDb({"clock_started": 0, "primes_enabled": "1"})
        with patch.object(main, "_primes_enabled_proposed", return_value=True):
            main._sync_primes_enabled_pref(db)
        self.assertEqual(db.set_setting_calls, [])

    def test_deux_tournois_crees_a_des_moments_differents_convergent_ensemble(self):
        """Reproduit l'exemple exact de l'utilisateur : un OPEN et un
        Sit & Go créés à des instants différents, aucun démarré, la case
        est décochée depuis les Paramètres de L'UN DES DEUX -> les DEUX
        doivent finir désactivés au tick suivant, jamais l'un des deux
        figé sur une ancienne valeur."""
        open_db = _FakeDb({"clock_started": 0, "primes_enabled": "1"})
        sng_db = _FakeDb({"clock_started": 0, "primes_enabled": "1"})
        with patch.object(main, "_primes_enabled_proposed", return_value=True):
            main._sync_primes_enabled_pref(open_db)
            main._sync_primes_enabled_pref(sng_db)
        self.assertEqual(open_db.get_setting("primes_enabled"), "1")
        self.assertEqual(sng_db.get_setting("primes_enabled"), "1")

        # Décoché depuis le Sit & Go (peu importe lequel) : global -> False.
        with patch.object(main, "_primes_enabled_proposed", return_value=False):
            main._sync_primes_enabled_pref(open_db)
            main._sync_primes_enabled_pref(sng_db)
        self.assertEqual(open_db.get_setting("primes_enabled"), "0")
        self.assertEqual(sng_db.get_setting("primes_enabled"), "0")


class OnPrimesEnabledToggleTest(unittest.TestCase):
    def test_session_verrouillee_annule_le_changement(self):
        db = _FakeDb({"primes_enabled": "1"})
        app = _FakeApp(db=db, primes_enabled=False)  # l'utilisateur vient de décocher...
        with patch.object(main, "_primes_session_locked", return_value=True), \
             patch.object(main, "_set_primes_enabled_proposed") as mock_set_proposed:
            main.App._on_primes_enabled_toggle(app)
        self.assertTrue(app.primes_enabled_var.get(), "doit revenir à la valeur réelle du tournoi")
        self.assertEqual(app.primes_enabled_check._state, "disabled")
        mock_set_proposed.assert_not_called()
        self.assertEqual(db.set_setting_calls, [])

    def test_session_libre_propage_au_global_et_au_local_immediatement(self):
        db = _FakeDb({"primes_enabled": "1"})
        app = _FakeApp(db=db, primes_enabled=False)
        with patch.object(main, "_primes_session_locked", return_value=False), \
             patch.object(main, "_set_primes_enabled_proposed") as mock_set_proposed:
            main.App._on_primes_enabled_toggle(app)
        mock_set_proposed.assert_called_once_with(False)
        self.assertEqual(db.set_setting_calls, [("primes_enabled", "0")])
        self.assertEqual(app.refresh_bounty_tab_calls, 1)


class SyncPrimesEnabledCheckboxTest(unittest.TestCase):
    def test_reflete_la_copie_locale_dans_la_case(self):
        db = _FakeDb({"primes_enabled": "0"})
        app = _FakeApp(db=db, primes_enabled=True)
        with patch.object(main, "_primes_session_locked", return_value=False):
            main.App._sync_primes_enabled_checkbox(app)
        self.assertFalse(app.primes_enabled_var.get())

    def test_grise_la_case_si_la_session_est_verrouillee(self):
        db = _FakeDb({"primes_enabled": "1"})
        app = _FakeApp(db=db, primes_enabled=True)
        with patch.object(main, "_primes_session_locked", return_value=True):
            main.App._sync_primes_enabled_checkbox(app)
        self.assertEqual(app.primes_enabled_check._state, "disabled")

    def test_reactive_la_case_si_la_session_se_deverrouille(self):
        db = _FakeDb({"primes_enabled": "1"})
        app = _FakeApp(db=db, primes_enabled=True)
        app.primes_enabled_check._state = "disabled"
        with patch.object(main, "_primes_session_locked", return_value=False):
            main.App._sync_primes_enabled_checkbox(app)
        self.assertEqual(app.primes_enabled_check._state, "normal")

    def test_aucun_tournoi_ouvert_ne_plante_pas(self):
        app = _FakeApp(db=None)
        main.App._sync_primes_enabled_checkbox(app)  # ne doit lever aucune exception


class UpdatePrimesSectionStateTest(unittest.TestCase):
    class _FakeWidget:
        def __init__(self, raise_error=False):
            self._raise = raise_error
            self.state = None

        def configure(self, state):
            if self._raise:
                raise __import__("tkinter").TclError("pas d'option state")
            self.state = state

    def test_grise_tous_les_widgets_quand_desactive_et_non_verrouille(self):
        app = _FakeApp()
        widgets = [self._FakeWidget() for _ in range(4)]
        app._primes_section_widgets = widgets
        main.App._update_primes_section_state(app, False, locked=False)
        self.assertTrue(all(w.state == "disabled" for w in widgets))

    def test_reactive_tous_les_widgets_quand_active_et_non_verrouille(self):
        app = _FakeApp()
        widgets = [self._FakeWidget() for _ in range(4)]
        app._primes_section_widgets = widgets
        main.App._update_primes_section_state(app, True, locked=False)
        self.assertTrue(all(w.state == "normal" for w in widgets))

    def test_widget_sans_option_state_ne_plante_pas(self):
        app = _FakeApp()
        app._primes_section_widgets = [self._FakeWidget(raise_error=True)]
        main.App._update_primes_section_state(app, False, locked=False)  # ne doit rien lever

    def test_verrouille_grise_meme_si_actives_point_2_de_la_demande(self):
        """Coeur du point 2 (demande du 2026-09-09) : dès que la session
        est verrouillée, TOUS les réglages de la section Primes restent
        grisés même si "Calculer les primes" est toujours cochée — pas
        seulement la case elle-même."""
        app = _FakeApp()
        widgets = [self._FakeWidget() for _ in range(4)]
        app._primes_section_widgets = widgets
        main.App._update_primes_section_state(app, True, locked=True)
        self.assertTrue(all(w.state == "disabled" for w in widgets))

    def test_verrouille_et_desactive_reste_grise(self):
        app = _FakeApp()
        widgets = [self._FakeWidget() for _ in range(4)]
        app._primes_section_widgets = widgets
        main.App._update_primes_section_state(app, False, locked=True)
        self.assertTrue(all(w.state == "disabled" for w in widgets))

    def test_locked_none_recalcule_via_primes_session_locked(self):
        """`locked=None` (valeur par défaut) doit retomber sur
        _primes_session_locked() plutôt que de planter ou d'ignorer le
        verrouillage — vérifié ici en le mockant explicitement pour ne
        JAMAIS toucher le vrai registre partagé de l'utilisateur."""
        app = _FakeApp()
        widgets = [self._FakeWidget() for _ in range(2)]
        app._primes_section_widgets = widgets
        with patch.object(main, "_primes_session_locked", return_value=True):
            main.App._update_primes_section_state(app, True)
        self.assertTrue(all(w.state == "disabled" for w in widgets))


# =======================================================================
# Vérifications structurelles (comme NoBypassPathExistsTest dans
# test_single_tournament_at_a_time.py) : les points de câblage qu'il est
# trop coûteux de tester par de vrais widgets Tk (fenêtres modales,
# sélecteurs de fichiers) sont vérifiés par inspection de l'AST.
# =======================================================================
class WiringStructurelTest(unittest.TestCase):
    def setUp(self):
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(main_py, encoding="utf-8") as f:
            self.tree = ast.parse(f.read(), filename=main_py)

    def _find_function(self, name):
        matches = [n for n in ast.walk(self.tree) if isinstance(n, ast.FunctionDef) and n.name == name]
        self.assertEqual(len(matches), 1, f"{name} introuvable ou nom ambigu dans main.py")
        return matches[0]

    def _calls_name(self, node, name):
        return any(
            isinstance(n, ast.Call) and (
                (isinstance(n.func, ast.Name) and n.func.id == name)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
            )
            for n in ast.walk(node)
        )

    def test_clock_resume_resynchronise_avant_de_demarrer(self):
        func = self._find_function("_clock_resume")
        self.assertTrue(self._calls_name(func, "_sync_primes_enabled_pref"))

    def test_clock_resume_pose_le_verrou_de_session(self):
        """Point 1 de la demande du 2026-09-09 : le drapeau de session
        doit être posé au moment même où CE tournoi démarre (voir
        open_windows.mark_primes_session_started), pas seulement
        recalculé à la volée à partir de l'état courant."""
        func = self._find_function("_clock_resume")
        self.assertTrue(self._calls_name(func, "mark_primes_session_started"))

    def test_tick_appelle_la_synchronisation_et_le_rafraichissement_de_la_case(self):
        func = self._find_function("_tick")
        self.assertTrue(self._calls_name(func, "_sync_primes_enabled_pref"))
        self.assertTrue(self._calls_name(func, "_sync_primes_enabled_checkbox"))

    def test_choose_tournament_file_stampe_les_nouveaux_tournois(self):
        func = self._find_function("_choose_tournament_file")
        self.assertTrue(self._calls_name(func, "_primes_enabled_proposed"))
        self.assertTrue(self._calls_name(func, "set_setting"))

    def test_eliminate_selected_court_circuite_pko_avec_primes_enabled(self):
        func = self._find_function("_eliminate_selected")
        self.assertTrue(self._calls_name(func, "primes_enabled"))

    def test_remote_eliminate_court_circuite_pko_avec_primes_enabled(self):
        func = self._find_function("_remote_eliminate")
        self.assertTrue(self._calls_name(func, "primes_enabled"))

    def test_eliminate_selected_consulte_le_mode_test(self):
        """Point 4 de la demande du 2026-09-09 : l'élimination groupée
        sans éliminateur ne doit plus être possible qu'en Mode Test."""
        func = self._find_function("_eliminate_selected")
        self.assertTrue(self._calls_name(func, "_test_mode_enabled"))

    def test_remote_eliminate_ne_depend_plus_du_mode_test(self):
        """Correction du 2026-09-09 (2e relecture) : le Mode Test ne
        facilite QUE l'élimination groupée (_eliminate_selected, onglet
        Joueurs) — un concept qui n'existe pas côté téléphone (toujours
        une seule élimination à la fois, par glisser-déposer). La règle
        d'éliminateur obligatoire de _remote_eliminate ne doit donc plus
        du tout consulter _test_mode_enabled (voir tests/test_test_mode_
        and_mandatory_eliminator.py pour la couverture fonctionnelle de
        la règle corrigée : mandatory ⟺ primes activées ET bounty > 0,
        indépendant du Mode Test)."""
        func = self._find_function("_remote_eliminate")
        self.assertFalse(self._calls_name(func, "_test_mode_enabled"))


if __name__ == "__main__":
    unittest.main()
