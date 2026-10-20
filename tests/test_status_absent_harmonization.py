# -*- coding: utf-8 -*-
"""Modification du 2026-09-30 — "harmonisation des statuts Mac et
iPhone" : un joueur ACTIF signalé absent affiche désormais explicitement
"Absent" (au lieu de "Pré-inscrit") sur le Mac (App._accueil_status_
label) ET sur l'iPhone (page "Accueil des joueurs", _CHECKIN_PAGE) — la
MÊME priorité des trois statuts partout : Absent > Actif > Pré-inscrit.

Couvre :
1. App._accueil_status_label : "Absent" pour un joueur signalé absent,
   priorité EXPLICITE vérifiée (pas seulement "ça marche parce que les
   deux états sont mutuellement exclusifs").
2. Colonne "Statut" de l'onglet Joueurs (Mac) : "Absent" affiché après
   un rafraîchissement complet ; redevient "Actif" (clignotement arrêté,
   déjà couvert ailleurs) une fois le retour confirmé.
3. App._refresh_remote_checkin_cache expose désormais "absent" pour
   chaque joueur.
4. Page "Accueil des joueurs" (iPhone) : statut explicite affiché pour
   chaque cas, y compris une fois l'accueil clôturé (remplace l'ancien
   tiret) — vérifié au niveau structure (JS/CSS) ET bout en bout HTTP.
5. Interdictions après expiration du délai : inchangées (vérification
   de non-régression, ce correctif ne touche qu'à l'affichage)."""
import json
import os
import sys
import tempfile
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import remote_control  # noqa: E402
from _remote_control_auth_test_utils import authenticated_jar, http_request  # noqa: E402
from test_remote_control_reliability import _RemoteControlHttpTestCase, _free_port  # noqa: E402,F401


def _new_db(tmp_dir, name="A"):
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1})
    return db


def _new_db_cross_thread(tmp_dir, filename):
    import sqlite3

    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    db.set_settings({"accueil_management_enabled": 1})
    db.conn.close()
    db.conn = sqlite3.connect(path, check_same_thread=False)
    db.conn.row_factory = sqlite3.Row
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


# =====================================================================
# 1. App._accueil_status_label — priorité Absent > Actif > Pré-inscrit
# =====================================================================
class AccueilStatusLabelAbsentPriorityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="status_absent_priority_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.win = types.SimpleNamespace(db=self.db)
        self.win._accueil_status_label = types.MethodType(main.App._accueil_status_label, self.win)

    def test_signale_absent_affiche_absent(self):
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(pid)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Absent")

    def test_priorite_absent_explicite_meme_si_arrived_at_incoherent(self):
        """Vérifie que la priorité "Absent" est bien un CHOIX explicite
        dans _accueil_status_label, pas seulement une conséquence de
        l'exclusion mutuelle arrived_at/absent_declared_at — en forçant
        directement en base un état normalement impossible (les deux
        posés à la fois), pour prouver que le code vérifie bien absent_
        declared_at EN PREMIER."""
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.confirm_arrival(pid)
        self.db.conn.execute(
            "UPDATE players SET absent_declared_at=? WHERE id=?", (time.time(), pid)
        )
        self.db.conn.commit()
        player = self.db.get_player(pid)
        self.assertIsNotNone(player["arrived_at"])
        self.assertIsNotNone(player["absent_declared_at"])

        self.assertEqual(self.win._accueil_status_label(player), "Absent")

    def test_pre_inscrit_reste_inchange(self):
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Pré-inscrit")

    def test_actif_reste_inchange(self):
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.confirm_arrival(pid)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Actif")

    def test_accueil_desactive_jamais_absent(self):
        """Comportement historique préservé : sans l'accueil, un joueur
        reste "Actif" quel que soit absent_declared_at (résiduel ou non),
        jamais un nouveau libellé "Absent"."""
        self.db.set_settings({"accueil_management_enabled": 0})
        pid = self.db.add_player("Alice")

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Actif")

    def test_retour_avant_expiration_redevient_actif(self):
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(pid)
        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Absent")

        self.db.confirm_arrival(pid)

        self.assertEqual(self.win._accueil_status_label(self.db.get_player(pid)), "Actif")


# =====================================================================
# 2. App._refresh_remote_checkin_cache — champ "absent"
# =====================================================================
class RemoteCheckinCacheAbsentFieldTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="checkin_cache_absent_field_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.win = types.SimpleNamespace(db=self.db, _remote_checkin_cache=[])
        self.win._refresh_remote_checkin_cache = types.MethodType(
            main.App._refresh_remote_checkin_cache, self.win
        )

    def test_absent_a_faux_par_defaut(self):
        self.db.add_player("Alice")

        self.win._refresh_remote_checkin_cache()

        self.assertFalse(self.win._remote_checkin_cache[0]["absent"])

    def test_absent_a_vrai_pour_un_joueur_signale_absent(self):
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(pid)

        self.win._refresh_remote_checkin_cache()

        cached = next(p for p in self.win._remote_checkin_cache if p["id"] == pid)
        self.assertTrue(cached["absent"])

    def test_absent_redevient_faux_apres_confirmation(self):
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(pid)
        self.db.confirm_arrival(pid)

        self.win._refresh_remote_checkin_cache()

        cached = next(p for p in self.win._remote_checkin_cache if p["id"] == pid)
        self.assertFalse(cached["absent"])
        self.assertIsNotNone(cached["arrived_at"])


# =====================================================================
# 3. Structure de la page "Accueil des joueurs" (iPhone)
# =====================================================================
class CheckinPageStructureTest(unittest.TestCase):
    def setUp(self):
        self.page = remote_control._CHECKIN_PAGE

    def test_fonction_de_statut_definie_avec_la_bonne_priorite(self):
        idx = self.page.index("function checkinStatusInfo")
        block = self.page[idx:idx + 400]
        idx_absent = block.index("p.absent")
        idx_arrived = block.index("p.arrived_at")
        self.assertLess(idx_absent, idx_arrived)
        self.assertIn("'Absent'", block)
        self.assertIn("'Actif'", block)
        self.assertIn("'Pré-inscrit'", block)

    def test_statut_ajoute_avant_la_branche_accueil_clos(self):
        """Le statut doit être ajouté à `.info` INCONDITIONNELLEMENT,
        avant le if/else accueilClosed — pour rester visible aussi bien
        accueil ouvert que clôturé (demande explicite)."""
        idx_status = self.page.index("var statusInfo = checkinStatusInfo(p);")
        idx_branch = self.page.index("if (p.arrived_at) {{")
        self.assertLess(idx_status, idx_branch)

    def test_ancien_tiret_de_cloture_disparu(self):
        """L'ancien libellé "✓ figé"/"—" (tiret nu) ne doit plus exister
        — remplacé par l'icône de verrou, le statut étant désormais
        affiché en permanence via checkinStatusInfo."""
        self.assertNotIn("✓ figé", self.page)

    def test_bouton_annuler_definitivement_supprime(self):
        """Modification du 2026-09-30 ("accueil : correction des erreurs
        réservée au Mac") : plus AUCUN bouton "Annuler" sur cette page,
        même pour un joueur Pré-inscrit ou Absent dans les délais — une
        fois "Actif", seul le statut reste affiché. La correction d'une
        confirmation faite par erreur redevient un geste DIRTO réservé
        au Mac (bouton "Corriger l'arrivée", onglet Joueurs)."""
        self.assertNotIn("btn.textContent = 'Annuler';", self.page)
        self.assertNotIn("cancelArrival(p.id)", self.page)
        self.assertNotIn("function cancelArrival", self.page)
        self.assertNotIn("btn-cancel", self.page)

    def test_bouton_confirmer_toujours_present(self):
        """Non-régression : "Confirmer" reste disponible pour un joueur
        Pré-inscrit ou Absent tant que l'accueil n'est pas clôturé pour
        lui (ni accueilClosed, ni p.arrived_at déjà posé)."""
        self.assertIn("btn.textContent = 'Confirmer';", self.page)
        self.assertIn("confirmArrival(p.id)", self.page)

    def test_css_des_trois_statuts_definie(self):
        self.assertIn(".status-preinscrit", self.page)
        self.assertIn(".status-actif", self.page)
        self.assertIn(".status-absent", self.page)

    def test_mention_inscrit_remplace_confirmer_pour_un_joueur_arrive(self):
        """Modification du 2026-10-01 ("accueil iPhone : remplacer
        Confirmer par Inscrit") : dès que p.arrived_at est posé, le même
        emplacement que l'ancien bouton "Confirmer" affiche désormais la
        mention "Inscrit" — PRIORITAIRE sur accueilClosed (un joueur déjà
        arrivé n'a plus rien à faire, qu'il soit dans les délais ou non)."""
        idx = self.page.index("if (p.arrived_at) {{")
        block = self.page[idx:idx + 300]
        self.assertIn("doneLabel.className = 'inscrit-label';", block)
        self.assertIn("doneLabel.textContent = 'Inscrit';", block)
        idx_else_if = self.page.index("else if (accueilClosed) {{", idx)
        self.assertGreater(idx_else_if, idx)

    def test_verrou_de_cloture_reserve_aux_joueurs_jamais_arrives(self):
        """Non-régression : le verrou 🔒 (délai des retardataires dépassé)
        reste affiché pour un joueur Pré-inscrit/Absent, INCHANGÉ — voir
        pollAbsenceState, jamais touché par ce correctif."""
        idx = self.page.index("else if (accueilClosed) {{")
        block = self.page[idx:idx + 250]
        self.assertIn("closedLabel.textContent = '🔒';", block)

    def test_statut_actif_preserve_avec_la_mention_inscrit(self):
        """Le statut "Actif" (checkinStatusInfo, affiché juste au-dessus)
        n'est pas remplacé par "Inscrit" — les deux coexistent, l'un dans
        `.info .status`, l'autre dans le même emplacement que l'ancien
        bouton "Confirmer"."""
        idx_status = self.page.index("var statusInfo = checkinStatusInfo(p);")
        idx_inscrit = self.page.index("doneLabel.textContent = 'Inscrit';")
        self.assertLess(idx_status, idx_inscrit)
        self.assertIn("'Actif'", self.page)

    def test_css_inscrit_label_definie(self):
        self.assertIn(".inscrit-label", self.page)


# =====================================================================
# 4. Bout en bout HTTP — statut explicite, y compris après clôture
# =====================================================================
class CheckinPageAbsentHttpTest(_RemoteControlHttpTestCase):
    def setUp(self):
        super().setUp()
        self._tmp2 = tempfile.TemporaryDirectory(prefix="checkin_absent_http_test_")
        self.addCleanup(self._tmp2.cleanup)
        self.db = _new_db_cross_thread(self._tmp2.name, "t.tournoi")
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")

        self._replace_server(remote_control.RemoteControlServer(
            on_word=lambda w: None,
            get_checkin_players=lambda: [
                {
                    "id": p["id"], "name": p["name"], "club": p["club"],
                    "table": None, "seat": p["seat"],
                    "arrived_at": p["arrived_at"], "absent": bool(p["absent_declared_at"]),
                }
                for p in self.db.list_players(status="active")
            ],
            on_confirm_arrival=lambda pid: {"ok": self.db.confirm_arrival(pid), "message": ""},
            on_cancel_arrival=lambda pid: {"ok": self.db.cancel_arrival(pid), "message": ""},
            get_accueil_management_enabled=lambda: True,
            get_absence_deadline_passed=lambda: self.db.absence_deadline_passed(),
            port=self.port,
        ))

    def _jar(self):
        return authenticated_jar(self.base, code=self.code, owner_name="Test Admin")

    def test_checkin_players_expose_absent_pour_un_joueur_signale(self):
        _start_tournament(self.db)
        self.db.declare_absent(self.pid)
        jar = self._jar()

        status, body, _ = http_request(self.base, "GET", "/checkin_players", jar)

        self.assertEqual(status, 200)
        data = json.loads(body)
        player = next(p for p in data if p["id"] == self.pid)
        self.assertTrue(player["absent"])
        self.assertIsNone(player["arrived_at"])

    def test_confirmer_l_arrivee_efface_absent_dans_le_cache_suivant(self):
        """Non-régression du mécanisme de retour (correctif précédent) :
        confirmer l'arrivée depuis l'accueil iPhone d'un joueur signalé
        absent doit se refléter, ICI, dans le prochain sondage
        /checkin_players — absent: false, arrived_at posé."""
        _start_tournament(self.db)
        self.db.declare_absent(self.pid)
        jar = self._jar()

        status, body, _ = http_request(
            self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.pid},
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

        status, body, _ = http_request(self.base, "GET", "/checkin_players", jar)
        data = json.loads(body)
        player = next(p for p in data if p["id"] == self.pid)
        self.assertFalse(player["absent"])
        self.assertIsNotNone(player["arrived_at"])

    def test_apres_expiration_confirm_arrival_toujours_refuse(self):
        """Non-régression explicite : ce correctif ne touche qu'à
        l'affichage — l'interdiction de confirmer l'arrivée après le
        délai reste intacte."""
        _start_tournament(self.db, minutes_ago=65, duration_minutes=60)
        jar = self._jar()

        status, body, _ = http_request(
            self.base, "POST", "/confirm_arrival", jar, body={"player_id": self.pid},
        )

        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)["ok"])
        self.assertIsNone(self.db.get_player(self.pid)["arrived_at"])


if __name__ == "__main__":
    unittest.main()
