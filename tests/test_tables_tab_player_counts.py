# -*- coding: utf-8 -*-
"""Tests ciblés de l'amélioration d'affichage de l'onglet Tables (demande
du 2026-09-10) : le titre de chaque table affiche désormais son nombre
de joueurs actuellement assis ("Table 1 — 8 joueurs"), et un total
discret ("Total : 22 joueurs") résume l'ensemble — voir main.py:
_format_players_count et App._refresh_tables_tab.

PUREMENT UN AFFICHAGE : aucun changement de la logique des tables ni du
rééquilibrage (voir database.py, non touché par cette demande) — ces
tests vérifient donc le texte affiché, jamais l'état de la base.

Le comptage doit se mettre à jour après inscription, élimination,
déplacement, rééquilibrage, et fermeture/ouverture de table — couvert
ci-dessous en rappelant _refresh_tables_tab après chacune de ces
actions réelles sur une vraie Database (pas de doublure) et en vérifiant
le nouveau texte à chaque fois.

N'instancie PAS App(tk.Tk) au complet : seuls les widgets/attributs lus
par App._refresh_tables_tab sont greffés sur une racine Tk réelle
partagée pour toute la classe (voir tests/test_pending_rebalance_mac_
badge.py pour la même précaution anti-flakiness Tcl/Tk)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk
from tkinter import ttk

import database  # noqa: E402
import main  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


class FormatPlayersCountTest(unittest.TestCase):
    """Singulier/pluriel de main._format_players_count, indépendant de
    Tkinter (fonction module-level pure)."""

    def test_singulier_pour_1(self):
        self.assertEqual(main._format_players_count(1), "1 joueur")

    def test_pluriel_pour_0_et_plusieurs(self):
        self.assertEqual(main._format_players_count(0), "0 joueurs")
        self.assertEqual(main._format_players_count(2), "2 joueurs")
        self.assertEqual(main._format_players_count(22), "22 joueurs")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RefreshTablesTabPlayerCountsTest(unittest.TestCase):
    """Un seul tk.Tk() pour toute la classe (voir la même précaution dans
    tests/test_pending_rebalance_mac_badge.py et tests/test_rebalance_
    phase2_max_seats_change.py : multiplier les racines Tk réelles dans
    la suite complète a provoqué une instabilité Tcl/Tk sans rapport
    avec la logique testée)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        prefs_patcher = patch.object(database.export_prefs, "load_value", return_value=True)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self._tmp = tempfile.TemporaryDirectory(prefix="tables_tab_counts_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)
        self.db.set_settings({"max_seats_per_table": 9, "min_players_per_table": 1})

        # Greffe, sur la racine réelle partagée, exactement les widgets
        # construits par App._build_tables_tab pour cette zone (bandeau
        # + zone défilante), et les méthodes réelles nécessaires.
        self.win = self.root
        self.win.db = self.db
        self.win._tables_zoom = 1.0
        self.win._tables_scroll_paused = False

        top = ttk.Frame(self.root)
        self.addCleanup(top.destroy)
        self.win._tables_total_label = ttk.Label(top, text="")

        # tables_canvas EN DOUBLURE (MagicMock) : _refresh_tables_tab
        # n'appelle sur lui que update_idletasks()/configure(scrollregion=
        # ...)/bbox("all")/yview_moveto(0.0), purement des détails de
        # défilement SANS AUCUN rapport avec le texte des titres/le total
        # testés ici. Un vrai tk.Canvas.update_idletasks() s'est avéré
        # provoquer, dans ce harnais de test précis (racine Tk greffée à
        # la main, sans le reste de la fenêtre réelle), un crash bas
        # niveau de Tcl/Tk (segfault, hors de portée d'un try/except
        # Python) — un risque inutile à courir puisque seul
        # `self.tables_inner` (un vrai widget, pour pouvoir inspecter les
        # tk.LabelFrame réellement créés ci-dessous) importe ici.
        self.win.tables_canvas = MagicMock()
        self.win.tables_canvas.bbox.return_value = None
        self.win.tables_inner = ttk.Frame(self.root)
        self.addCleanup(self.win.tables_inner.destroy)

        self.win._pending_old_seat_by_name = types.MethodType(
            main.App._pending_old_seat_by_name, self.win
        )
        self.win._refresh_tables_tab = types.MethodType(
            main.App._refresh_tables_tab, self.win
        )

    def _table_titles(self):
        return sorted(
            w.cget("text") for w in self.win.tables_inner.winfo_children()
            if isinstance(w, tk.LabelFrame)
        )

    def _total_text(self):
        return self.win._tables_total_label.cget("text")

    def _seat(self, table_id, seat, name):
        cur = self.db.conn.execute(
            "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
            "chips, status, bounty, club) VALUES (?, 1, 0, 0, 10000, 'active', 0, '')",
            (name,),
        )
        player_id = cur.lastrowid
        self.db.conn.execute(
            "UPDATE players SET table_id=?, seat=? WHERE id=?", (table_id, seat, player_id)
        )
        self.db.conn.commit()
        return player_id

    def test_titre_singulier_pluriel_et_table_vide(self):
        t1_id = self.db.list_tables()[0]["id"]
        self._seat(t1_id, 1, "Solo")
        self.win._refresh_tables_tab()
        self.assertEqual(self._table_titles(), ["Table 1 — 1 joueur"])
        self.assertEqual(self._total_text(), "Total : 1 joueur")

    def test_apres_inscriptions_titre_et_total_corrects(self):
        for i in range(1, 28):
            self.db.add_player(f"Joueur {i}")
        self.win._refresh_tables_tab()

        titles = self._table_titles()
        self.assertEqual(len(titles), len(self.db.list_tables()))
        total_from_titles = sum(int(t.split("—")[1].strip().split()[0]) for t in titles)
        self.assertEqual(total_from_titles, 27)
        self.assertEqual(self._total_text(), "Total : 27 joueurs")

    def test_apres_elimination_le_compte_diminue(self):
        for i in range(1, 10):
            self.db.add_player(f"Joueur {i}")
        self.win._refresh_tables_tab()
        self.assertEqual(self._total_text(), "Total : 9 joueurs")

        pid = self.db.list_players(status="active")[0]["id"]
        self.db.eliminate_player(pid)
        self.win._refresh_tables_tab()
        self.assertEqual(self._total_text(), "Total : 8 joueurs")

    def test_apres_deplacement_manuel_le_total_ne_change_pas_mais_la_repartition_oui(self):
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        for seat in range(1, 4):
            self._seat(t1_id, seat, f"T1-{seat}")
        for seat in range(1, 3):
            self._seat(t2_id, seat, f"T2-{seat}")
        self.win._refresh_tables_tab()
        self.assertEqual(self._total_text(), "Total : 5 joueurs")
        self.assertEqual(
            self._table_titles(), ["Table 1 — 3 joueurs", "Table 2 — 2 joueurs"],
        )

        # Déplacement manuel d'un joueur de Table 1 vers Table 2 (comme le
        # ferait le double-clic "Déplacer" de l'onglet Joueurs) : PAS via
        # rebalance_tables, juste une mise à jour directe table_id/seat,
        # pour vérifier que l'affichage suit n'importe quel changement de
        # répartition, pas seulement ceux causés par rebalance_tables.
        moved = self.db.conn.execute(
            "SELECT id FROM players WHERE table_id=? LIMIT 1", (t1_id,)
        ).fetchone()["id"]
        self.db.conn.execute(
            "UPDATE players SET table_id=?, seat=3 WHERE id=?", (t2_id, moved)
        )
        self.db.conn.commit()
        self.win._refresh_tables_tab()

        self.assertEqual(self._total_text(), "Total : 5 joueurs")  # total inchangé
        self.assertEqual(
            self._table_titles(), ["Table 1 — 2 joueurs", "Table 2 — 3 joueurs"],
        )

    def test_apres_rebalance_tables_le_titre_reflete_la_nouvelle_repartition(self):
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        for seat in range(1, 10):
            self._seat(t1_id, seat, f"T1-{seat}")
        for seat in range(1, 4):
            self._seat(t2_id, seat, f"T2-{seat}")
        self.win._refresh_tables_tab()
        self.assertEqual(
            self._table_titles(), ["Table 1 — 9 joueurs", "Table 2 — 3 joueurs"],
        )

        self.db.rebalance_tables()  # pas de guidage BB actif ici : résolution immédiate
        self.win._refresh_tables_tab()

        titles = self._table_titles()
        total_from_titles = sum(int(t.split("—")[1].strip().split()[0]) for t in titles)
        self.assertEqual(total_from_titles, 12)
        self.assertEqual(self._total_text(), "Total : 12 joueurs")
        # L'écart doit avoir diminué (moins de 6, l'écart initial 9 vs 3).
        counts = [int(t.split("—")[1].strip().split()[0]) for t in titles]
        self.assertLess(max(counts) - min(counts), 6)

    def test_apres_fermeture_de_table_plus_aucun_titre_pour_elle(self):
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        self._seat(t1_id, 1, "Reste")
        self.win._refresh_tables_tab()
        self.assertEqual(len(self._table_titles()), 2)  # Table 1 (1 joueur) + Table 2 (vide)

        self.db.close_table(t2_id)
        self.win._refresh_tables_tab()

        titles = self._table_titles()
        self.assertEqual(titles, ["Table 1 — 1 joueur"])
        self.assertEqual(self._total_text(), "Total : 1 joueur")

    def test_apres_ouverture_de_table_le_nouveau_titre_apparait_a_0_joueur(self):
        self.win._refresh_tables_tab()
        self.assertEqual(self._table_titles(), ["Table 1 — 0 joueurs"])

        self.db.add_table("Table 2")
        self.win._refresh_tables_tab()

        self.assertEqual(
            self._table_titles(), ["Table 1 — 0 joueurs", "Table 2 — 0 joueurs"],
        )
        self.assertEqual(self._total_text(), "Total : 0 joueurs")


if __name__ == "__main__":
    unittest.main()
