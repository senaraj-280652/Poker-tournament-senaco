# -*- coding: utf-8 -*-
"""Tests ciblés de l'amélioration d'affichage de l'onglet Tables (demande
du 2026-09-10) : le titre de chaque table affiche désormais son nombre
de joueurs actuellement assis ("Table 1 — 8 joueurs"), et un total
("22 joueurs", texte du seul _tables_total_label) résume l'ensemble —
voir main.py: _format_players_count et App._refresh_tables_tab.

Demande du 2026-09-16 (visibilité, observée lors du test réel sur le
HP) : ce total est désormais précédé d'un libellé SÉPARÉ, blanc, "Nombre
de joueurs :" (voir TablesTotalLabelStyleTest plus bas) — _tables_total_
label lui-même ne porte donc plus que le nombre nu ("22 joueurs"), sans
le préfixe "Total : " d'avant cette demande (uniquement un changement de
texte/style : AUCUN changement du calcul, toujours total_players/
_format_players_count).

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
        self.assertEqual(self._total_text(), "1 joueur")

    def test_apres_inscriptions_titre_et_total_corrects(self):
        for i in range(1, 28):
            self.db.add_player(f"Joueur {i}")
        self.win._refresh_tables_tab()

        titles = self._table_titles()
        self.assertEqual(len(titles), len(self.db.list_tables()))
        total_from_titles = sum(int(t.split("—")[1].strip().split()[0]) for t in titles)
        self.assertEqual(total_from_titles, 27)
        self.assertEqual(self._total_text(), "27 joueurs")

    def test_apres_elimination_le_compte_diminue(self):
        for i in range(1, 10):
            self.db.add_player(f"Joueur {i}")
        self.win._refresh_tables_tab()
        self.assertEqual(self._total_text(), "9 joueurs")

        pid = self.db.list_players(status="active")[0]["id"]
        self.db.eliminate_player(pid)
        self.win._refresh_tables_tab()
        self.assertEqual(self._total_text(), "8 joueurs")

    def test_apres_deplacement_manuel_le_total_ne_change_pas_mais_la_repartition_oui(self):
        t1_id = self.db.list_tables()[0]["id"]
        t2_id = self.db.add_table("Table 2")
        for seat in range(1, 4):
            self._seat(t1_id, seat, f"T1-{seat}")
        for seat in range(1, 3):
            self._seat(t2_id, seat, f"T2-{seat}")
        self.win._refresh_tables_tab()
        self.assertEqual(self._total_text(), "5 joueurs")
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

        self.assertEqual(self._total_text(), "5 joueurs")  # total inchangé
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
        self.assertEqual(self._total_text(), "12 joueurs")
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
        self.assertEqual(self._total_text(), "1 joueur")

    def test_apres_ouverture_de_table_le_nouveau_titre_apparait_a_0_joueur(self):
        self.win._refresh_tables_tab()
        self.assertEqual(self._table_titles(), ["Table 1 — 0 joueurs"])

        self.db.add_table("Table 2")
        self.win._refresh_tables_tab()

        self.assertEqual(
            self._table_titles(), ["Table 1 — 0 joueurs", "Table 2 — 0 joueurs"],
        )
        self.assertEqual(self._total_text(), "0 joueurs")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class TablesTotalLabelStyleTest(unittest.TestCase):
    """Demande du 2026-09-16 (visibilité du nombre de joueurs, observée
    lors du test réel sur le HP) : vérifie la configuration VISUELLE
    réelle construite par App._build_tables_tab (jamais seulement le
    texte, déjà couvert par RefreshTablesTabPlayerCountsTest ci-dessus)
    — libellé "Nombre de joueurs :" en blanc, police sensiblement plus
    grande que la police par défaut du thème ttk (~9-10 pt), et le
    nombre lui-même également agrandi. N'appelle PAS _refresh_tables_tab
    ici : seule la construction initiale (_build_tables_tab) importe."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        prefs_patcher = patch.object(main.export_prefs, "load_value", return_value=1.0)
        self.addCleanup(prefs_patcher.stop)
        prefs_patcher.start()

        self.win = self.root
        self.win.tables_tab = ttk.Frame(self.root)
        self.addCleanup(self.win.tables_tab.destroy)
        # Jamais réellement invoquée dans ce test (aucun rafraîchissement
        # périodique voulu ici) : seule la construction des widgets du
        # bandeau importe — voir la docstring de la classe.
        self.win._tables_autoscroll_tick = lambda: None
        self.win._rebalance = lambda: None
        self.win._tables_zoom_by = lambda delta: None
        self.win._continue_pending_rebalance_without_bb = lambda: None
        self.win._on_tables_mousewheel = lambda event: None

        self.win._build_tables_tab = types.MethodType(main.App._build_tables_tab, self.win)
        self.win._build_tables_tab()

    def _toolbar_labels(self):
        """Tous les tk.Label directement enfants du bandeau du haut (pas
        les libellés à l'intérieur des cadres de table, construits plus
        tard par _refresh_tables_tab, jamais appelée ici)."""
        top = self.win._tables_total_label.master
        return [w for w in top.winfo_children() if isinstance(w, tk.Label)]

    def test_libelle_nombre_de_joueurs_present_en_blanc(self):
        labels = self._toolbar_labels()
        caption = next(w for w in labels if w.cget("text") == "Nombre de joueurs :")
        self.assertEqual(caption.cget("fg"), "white")
        self.assertEqual(caption.cget("background"), main.FELT)

    def test_nombre_lui_meme_bien_visible(self):
        total_label = self.win._tables_total_label
        self.assertEqual(total_label.cget("background"), main.FELT)
        self.assertEqual(total_label.cget("foreground"), main.GOLD)

    def test_polices_sensiblement_plus_grandes_que_le_defaut_du_theme(self):
        """"sensiblement plus grande que maintenant" : comparée à la
        police par défaut d'un ttk.Label de ce même thème (celle
        utilisée par CE label avant cette demande, voir git history) —
        jamais une valeur absolue arbitraire, pour rester robuste à un
        futur changement de thème global."""
        default_ttk_size = ttk.Label(self.win.tables_tab).tk.call(
            "font", "actual", ttk.Style().lookup("TLabel", "font") or "TkDefaultFont", "-size"
        )
        default_ttk_size = abs(int(default_ttk_size))

        caption = next(
            w for w in self._toolbar_labels() if w.cget("text") == "Nombre de joueurs :"
        )
        caption_size = abs(int(caption.tk.call("font", "actual", caption.cget("font"), "-size")))
        total_size = abs(int(
            self.win._tables_total_label.tk.call(
                "font", "actual", self.win._tables_total_label.cget("font"), "-size"
            )
        ))
        self.assertGreater(caption_size, default_ttk_size)
        self.assertGreater(total_size, default_ttk_size)

    def test_position_du_libelle_toujours_a_droite_du_bandeau(self):
        """Disposition NON bouleversée (demande explicite) : les deux
        nouveaux libellés restent dans le MÊME bandeau du haut, du même
        côté (droite) qu'avant cette demande — jamais une nouvelle ligne
        ni un nouveau conteneur."""
        top = self.win._tables_total_label.master
        self.assertIs(top.master, self.win.tables_tab)
        info = self.win._tables_total_label.pack_info()
        self.assertEqual(info["side"], "right")


if __name__ == "__main__":
    unittest.main()
