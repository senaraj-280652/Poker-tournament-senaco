# -*- coding: utf-8 -*-
"""Tests ciblés de la PHASE 2 (changement de "Nombre de sièges par
table") de l'architecture de rééquilibrage validée le 2026-09-10, au
niveau du VRAI point d'entrée App._collect_and_save_all_settings
(main.py) — le gestionnaire commun à "✅ Appliquer à ce tournoi" et
"Régénérer la structure de blindes".

- AVANT le premier démarrage (clock_started == 0) : un changement 9 -> 8
  reste accepté, et rebalance_tables() réorganise automatiquement (test
  #2).
- APRÈS le premier démarrage (clock_started == 1) avec une table qui
  contient déjà plus de joueurs que la nouvelle capacité : REFUS CIBLÉ
  (option (b) choisie par l'utilisateur) — max_seats_per_table et
  tables_pk.max_seats restent inchangés, un message d'erreur est
  affiché, et les AUTRES réglages modifiés dans le même geste sont
  malgré tout enregistrés (test #3).
- Demande du 2026-09-14 (cas réel observé sur le HP, build TEST 3) :
  quand il ne reste qu'UNE seule table active et que son occupation
  tient dans la convention de "table finale" (<= FINAL_TABLE_MAX_SEATS,
  database.py — la même déjà appliquée par rebalance_tables), le refus
  ciblé ci-dessus ne doit JAMAIS s'appliquer, quelle que soit la
  nouvelle capacité demandée : voir TablesOverCapacityFinalTableException
  DirectTest (Database.tables_over_capacity isolée) et
  TableFinaleExceptionAucunPopupTest (au niveau App._collect_and_save_
  all_settings, popup compris) plus bas dans ce fichier.

N'instancie PAS App(tk.Tk) au complet : App._collect_and_save_all_
settings ne touche, pour ce qu'on teste ici, que self.db/self.
settings_vars et une poignée de méthodes de rafraîchissement qu'on peut
remplacer par des doublures — self.title() (appelée par _update_window_
title) a besoin d'un vrai tk.Tk() pour exister, d'où son utilisation
directe comme "self", exactement comme tests/test_tick_never_stops_
scheduling.py le fait déjà pour App._tick."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk

import database  # noqa: E402
import main  # noqa: E402
import tournament_prefs  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _seat_new_player(db, table_id, seat, name):
    cur = db.conn.execute(
        "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
        "chips, status, bounty, club) VALUES (?, 1, 0, 0, 10000, 'active', 0, '')",
        (name,),
    )
    player_id = cur.lastrowid
    db.conn.execute(
        "UPDATE players SET table_id=?, seat=? WHERE id=?",
        (table_id, seat, player_id),
    )
    db.conn.commit()
    return player_id


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class CollectAndSaveAllSettingsTestCase(unittest.TestCase):
    """Un seul tk.Tk() par CLASSE de test (setUpClass/tearDownClass),
    pas un par méthode de test : la suite complète compte déjà plusieurs
    fichiers créant de vraies racines Tk (voir tests/test_tick_never_
    stops_scheduling.py) — multiplier ces racines inutilement a
    provoqué, en pratique, une instabilité Tcl/Tk observée lors de
    l'exécution de la suite complète (RuntimeError "main thread is not
    in main loop" pendant la destruction différée d'un tk.Variable d'une
    AUTRE racine déjà détruite, propre à l'implémentation Tcl/Tk et au
    ramasse-miettes Python, sans lien avec la logique testée ici)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        # cleanup_tk (voir tests/_tk_cleanup.py, chantier "crash Tcl/Tk"
        # du 2026-09-19) : 2 méthodes de main.App greffées sur cls.root
        # (une racine DISTINCTE par sous-classe héritant de cette
        # classe) forment chacune un cycle, réclamé ici.
        cleanup_tk(cls, "root")

    def setUp(self):
        # Jamais le vrai ~/.poker_tournament/export_prefs.json : la valeur
        # réellement enregistrée sur cette machine (True/False) n'a aucun
        # rapport avec ce qui est testé ici (refus/acceptation du
        # changement de max_seats) et rendrait ces tests non
        # déterministes — True == guidage activé, comportement par
        # défaut le plus représentatif.
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="phase2_max_seats_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

        # Jamais le vrai ~/.poker_tournament/last_settings.json.
        prefs_path = os.path.join(self._tmp.name, "last_settings.json")
        patcher = patch.object(tournament_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

        self.win = self.root
        self.win.db = self.db
        self.win._collect_and_save_all_settings = types.MethodType(
            main.App._collect_and_save_all_settings, self.win
        )
        self.win._update_window_title = types.MethodType(
            main.App._update_window_title, self.win
        )
        self.win._trigger_movement_alert = MagicMock()
        self.win._check_pending_rebalance = MagicMock()

        # settings_vars : uniquement les champs pertinents pour ces tests
        # (max_seats_per_table + un réglage sans rapport, pour vérifier
        # qu'il est bien enregistré même quand max_seats_per_table est
        # refusé — voir test_refus_cible ci-dessous).
        self.win.settings_vars = {
            "max_seats_per_table": tk.StringVar(value="9"),
            "min_players_per_table": tk.StringVar(value="4"),
            "rake_percent": tk.StringVar(value="0"),
        }

    def _seat_two_tables(self, t1_count, t2_count):
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        for seat in range(1, t1_count + 1):
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")
        for seat in range(1, t2_count + 1):
            _seat_new_player(self.db, t2_id, seat, f"T2-{seat}")
        return t1_id, t2_id

    def _seat_one_table(self, count):
        t1_id = self.db.list_tables()[0]["id"]
        for seat in range(1, count + 1):
            _seat_new_player(self.db, t1_id, seat, f"T1-{seat}")
        return t1_id


class AvantDemarrageChangementAccepteTest(CollectAndSaveAllSettingsTestCase):
    """2. AVANT le premier démarrage (clock_started == 0, valeur par
    défaut), un changement 9 -> 8 est accepté même si une table contient
    déjà 9 joueurs : rebalance_tables() la réorganise automatiquement
    (PHASE 1/2 combinées — aucune restriction avant le premier
    démarrage)."""

    def test_9_vers_8_avant_demarrage_est_accepte_et_reorganise(self):
        self._seat_two_tables(9, 9)  # 18 joueurs > FINAL_TABLE_MAX_SEATS (10)
        self.win.settings_vars["max_seats_per_table"].set("8")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_not_called()

        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 8)
        for t in self.db.list_tables():
            self.assertEqual(t["max_seats"], 8)
            occ = self.db.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            self.assertLessEqual(occ, 8)


class ApresDemarrageChangementRefuseTest(CollectAndSaveAllSettingsTestCase):
    """3. APRÈS le premier démarrage (clock_started == 1), un changement
    9 -> 8 alors qu'une table contient 9 joueurs est REFUSÉ CIBLÉ : ni
    max_seats_per_table, ni tables_pk.max_seats ne changent, un message
    d'erreur est affiché, et les AUTRES réglages modifiés (ici
    rake_percent) sont malgré tout enregistrés normalement."""

    def setUp(self):
        super().setUp()
        self.db.set_settings({"clock_started": 1})

    def test_refus_cible_aucun_etat_9_joueurs_max_seats_8_enregistre(self):
        t1_id, t2_id = self._seat_two_tables(9, 3)
        self.win.settings_vars["max_seats_per_table"].set("8")
        self.win.settings_vars["rake_percent"].set("5")  # autre réglage modifié dans le même geste

        with patch.object(main, "messagebox") as mock_messagebox:
            values = self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_called_once()
            error_args = mock_messagebox.showerror.call_args[0]
            self.assertIn("Table 1", error_args[1])
            self.assertIn("9 joueurs", error_args[1])

        # max_seats_per_table reste à l'ancienne valeur, dans la base...
        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 9)
        # ... dans tables_pk (aucune table n'est jamais passée à 8)...
        for t in self.db.list_tables():
            self.assertEqual(t["max_seats"], 9)
        # ... et dans le champ de l'interface, ramené en arrière.
        self.assertEqual(self.win.settings_vars["max_seats_per_table"].get(), "9")
        # ... et dans la valeur renvoyée (utilisée par tournament_prefs).
        self.assertEqual(values["max_seats_per_table"], "9")

        # Aucune table n'est jamais passée par un état 9 joueurs / max_
        # seats 8 : la Table 1 a toujours 9 joueurs, toujours sous un
        # max_seats de 9 (jamais réduit puis "réparé").
        t1 = next(t for t in self.db.list_tables() if t["id"] == t1_id)
        occ_t1 = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (t1_id,)
        ).fetchone()["c"]
        self.assertEqual(occ_t1, 9)
        self.assertEqual(t1["max_seats"], 9)

        # L'AUTRE réglage modifié dans le même geste est malgré tout
        # enregistré normalement.
        self.assertEqual(self.db.get_setting("rake_percent"), "5")

    def test_changement_accepte_si_aucune_table_ne_deborde(self):
        """Non-régression : après démarrage, un changement qui ne laisse
        AUCUNE table en surcapacité reste accepté normalement (le refus
        ne doit pas devenir systématique après le premier démarrage).
        6 + 5 = 11 joueurs (> FINAL_TABLE_MAX_SEATS) pour éviter la
        convention "table finale", sans rapport avec ce test."""
        self._seat_two_tables(6, 5)  # aucune table à plus de 8 : rien à refuser
        self.win.settings_vars["max_seats_per_table"].set("8")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_not_called()

        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 8)
        for t in self.db.list_tables():
            self.assertEqual(t["max_seats"], 8)


class TablesOverCapacityFinalTableExceptionDirectTest(unittest.TestCase):
    """Couverture directe de Database.tables_over_capacity (sans App ni
    Tk) pour l'exception de "table finale" ajoutée le 2026-09-14 —
    isole précisément la règle réutilisée (même constante
    FINAL_TABLE_MAX_SEATS que rebalance_tables) de son câblage dans
    App._collect_and_save_all_settings, couvert séparément ci-dessous
    (TableFinaleExceptionAucunPopupTest)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="tables_over_capacity_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def _seat(self, table_id, count, prefix):
        for seat in range(1, count + 1):
            _seat_new_player(self.db, table_id, seat, f"{prefix}-{seat}")

    def test_une_table_8_joueurs_capacite_7_aucune_table_en_exces(self):
        t1_id = self.db.list_tables()[0]["id"]
        self._seat(t1_id, 8, "T1")
        self.assertEqual(self.db.tables_over_capacity(7), [])

    def test_une_table_10_joueurs_capacite_7_aucune_table_en_exces(self):
        t1_id = self.db.list_tables()[0]["id"]
        self._seat(t1_id, 10, "T1")
        self.assertEqual(self.db.tables_over_capacity(7), [])

    def test_une_table_11_joueurs_capacite_7_refuse(self):
        """Au-delà de FINAL_TABLE_MAX_SEATS (10), aucune exception :
        comportement de refus normal, même sur une seule table."""
        t1_id = self.db.list_tables()[0]["id"]
        self._seat(t1_id, 11, "T1")
        over = self.db.tables_over_capacity(7)
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["count"], 11)

    def test_deux_tables_8_et_3_joueurs_capacite_7_refuse(self):
        """Plusieurs tables actives : l'exception ne s'applique jamais,
        même si le total (11) est proche de FINAL_TABLE_MAX_SEATS."""
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        self._seat(t1_id, 8, "T1")
        self._seat(t2_id, 3, "T2")
        over = self.db.tables_over_capacity(7)
        self.assertEqual(len(over), 1)
        self.assertEqual(over[0]["name"], "Table 1")
        self.assertEqual(over[0]["count"], 8)


class TableFinaleExceptionAucunPopupTest(CollectAndSaveAllSettingsTestCase):
    """Demande du 2026-09-14, suite à un cas réel observé sur le HP (test
    TEST 3) : 1 seule Table 1 active, 8 joueurs, "Nombre de sièges par
    table" réglé à 7 -> le message "Impossible d'appliquer 7 sièges par
    table... Effectuez d'abord le rééquilibrage de cette table." était
    affiché à tort, alors que l'exception de "table finale" (voir
    FINAL_TABLE_MAX_SEATS, database.rebalance_tables) autorise déjà
    cette même table à dépasser "Nombre de sièges par table" tant que le
    total tient dans une table finale (<= 10 joueurs).

    Database.tables_over_capacity réutilise EXACTEMENT cette même
    convention (même constante FINAL_TABLE_MAX_SEATS) au lieu d'une
    deuxième règle indépendante — ces tests vérifient le comportement
    observable au niveau du VRAI point d'entrée, App._collect_and_save_
    all_settings, comme le reste de ce fichier."""

    def setUp(self):
        super().setUp()
        self.db.set_settings({"clock_started": 1})

    def test_une_table_8_joueurs_capacite_7_accepte_sans_popup(self):
        t1_id = self._seat_one_table(8)
        self.win.settings_vars["max_seats_per_table"].set("7")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_not_called()

        # La valeur configurée est bien enregistrée...
        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 7)
        # ... mais la table finale reste malgré tout ouverte à 8 (aucun
        # joueur déplacé, aucun rééquilibrage demandé) — exactement la
        # même mécanique que rebalance_tables applique déjà pour toute
        # table finale (voir n_tables_needed == 1 and n_active > max_seats).
        t1 = next(t for t in self.db.list_tables() if t["id"] == t1_id)
        self.assertEqual(t1["max_seats"], 8)
        occ = self.db.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'", (t1_id,)
        ).fetchone()["c"]
        self.assertEqual(occ, 8)

    def test_une_table_10_joueurs_capacite_7_accepte(self):
        """Borne haute exacte de la table finale (FINAL_TABLE_MAX_SEATS
        = 10, database.py) : toujours acceptée, jamais de popup."""
        t1_id = self._seat_one_table(10)
        self.win.settings_vars["max_seats_per_table"].set("7")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_not_called()

        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 7)
        t1 = next(t for t in self.db.list_tables() if t["id"] == t1_id)
        self.assertEqual(t1["max_seats"], 10)

    def test_plusieurs_tables_en_surcapacite_reste_refuse(self):
        """Non-régression explicite : l'exception ne s'applique QUE
        lorsqu'il ne reste qu'UNE seule table active — avec plusieurs
        tables, le contrôle de capacité existant (refus ciblé) reste
        strictement inchangé, popup compris."""
        self._seat_two_tables(8, 3)
        self.win.settings_vars["max_seats_per_table"].set("7")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_called_once()

        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 9)
        for t in self.db.list_tables():
            self.assertEqual(t["max_seats"], 9)

    def test_onze_joueurs_sur_une_table_aucune_exception(self):
        """11 joueurs dépasse strictement FINAL_TABLE_MAX_SEATS (10) :
        même regroupés sur une seule table active (état construit
        directement pour isoler précisément cette limite — en pratique
        rebalance_tables aurait déjà ouvert une deuxième table avant
        d'atteindre ce nombre), l'exception de table finale NE
        S'APPLIQUE JAMAIS au-delà de 10 joueurs, et le refus ciblé
        normal continue de s'appliquer."""
        t1_id = self._seat_one_table(11)
        self.win.settings_vars["max_seats_per_table"].set("7")

        with patch.object(main, "messagebox") as mock_messagebox:
            self.win._collect_and_save_all_settings()
            mock_messagebox.showerror.assert_called_once()
            error_args = mock_messagebox.showerror.call_args[0]
            self.assertIn("11 joueurs", error_args[1])

        self.assertEqual(self.db.get_setting_int("max_seats_per_table"), 9)
        t1 = next(t for t in self.db.list_tables() if t["id"] == t1_id)
        self.assertEqual(t1["max_seats"], 9)


if __name__ == "__main__":
    unittest.main()
