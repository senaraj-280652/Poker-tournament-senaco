# -*- coding: utf-8 -*-
"""API de LECTURE du journal (chantier "LOG", Phase 2, 2026-09-24) —
action_log.search_actions/list_tournaments/list_users/list_players/
list_categories, ainsi que category_label/action_label/role_label/
result_label. Le moteur d'ÉCRITURE (log_action, Phase 1) n'est PAS
retesté ici — voir tests/test_action_log.py, volontairement inchangé et
relancé tel quel en non-régression.

Deux niveaux, même principe que test_action_log.py :
1. Tests directs sur action_log.py (pas de serveur HTTP, pas de Tk).
2. ConcurrenceLectureEcritureTest : lecture pendant qu'un thread séparé
   écrit en continu — le mode WAL doit absorber ça sans erreur ni
   blocage perceptible."""
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import action_log  # noqa: E402


class ActionLogSearchTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="action_log_search_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = os.path.join(self._tmp.name, "actions_log.sqlite3")
        patcher = patch.object(action_log, "_log_path", return_value=self.db_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _log(self, **kwargs):
        base = dict(
            tournament_name="Tournoi du vendredi",
            tournament_path="/tmp/vendredi.tournoi",
            role="ADMIN",
            category="eliminations",
            action="eliminate",
            result=action_log.RESULT_SUCCESS,
            user_name="Raj",
        )
        base.update(kwargs)
        action_log.log_action(**base)


# =====================================================================
# 1. Base absente / vide : jamais de création accidentelle par la
#    LECTURE seule (voir _read_query).
# =====================================================================
class FichierAbsentTest(ActionLogSearchTestCase):
    def test_search_actions_sans_fichier_renvoie_vide_sans_creer_la_base(self):
        self.assertFalse(os.path.exists(self.db_path))
        rows, truncated = action_log.search_actions()
        self.assertEqual(rows, [])
        self.assertFalse(truncated)
        self.assertFalse(os.path.exists(self.db_path), "une simple lecture ne doit jamais créer le fichier")

    def test_list_functions_sans_fichier_renvoient_des_listes_vides(self):
        self.assertEqual(action_log.list_tournaments(), [])
        self.assertEqual(action_log.list_users(), [])
        self.assertEqual(action_log.list_players(), [])
        self.assertEqual(action_log.list_categories(), [])
        self.assertFalse(os.path.exists(self.db_path))


# =====================================================================
# 2. search_actions : filtres, tri, limite
# =====================================================================
class SearchSansFiltreTest(ActionLogSearchTestCase):
    def test_recherche_sans_filtre_renvoie_toutes_les_lignes(self):
        self._log(player_name="Alice")
        self._log(player_name="Bob", category="clock", action="toggle_pause")
        rows, truncated = action_log.search_actions()
        self.assertEqual(len(rows), 2)
        self.assertFalse(truncated)

    def test_tri_plus_recent_en_premier(self):
        self._log(player_name="Alice")
        time.sleep(1.01)  # ts a une résolution de la seconde
        self._log(player_name="Bob")
        rows, _ = action_log.search_actions()
        self.assertEqual([r["player_name"] for r in rows], ["Bob", "Alice"])

    def test_depart_par_id_a_ts_egal(self):
        # Deux insertions très rapprochées peuvent partager le même
        # "ts" (résolution seconde) : id DESC doit alors les départager
        # de façon déterministe (la plus RÉCEMMENT insérée en premier).
        self._log(player_name="Alice")
        self._log(player_name="Bob")
        rows, _ = action_log.search_actions()
        self.assertEqual([r["player_name"] for r in rows], ["Bob", "Alice"])


class SearchFiltreDatesTest(ActionLogSearchTestCase):
    def _log_with_ts(self, ts, **kwargs):
        self._log(**kwargs)
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("UPDATE actions_log SET ts = ? WHERE id = (SELECT MAX(id) FROM actions_log)", (ts,))
            conn.commit()
        finally:
            conn.close()

    def setUp(self):
        super().setUp()
        self._log_with_ts("2026-09-20 10:00:00", player_name="Le20")
        self._log_with_ts("2026-09-22 23:59:59", player_name="Le22")
        self._log_with_ts("2026-09-24 00:00:00", player_name="Le24")

    def test_filtre_du_seul(self):
        rows, _ = action_log.search_actions(ts_from="2026-09-22 00:00:00")
        self.assertEqual({r["player_name"] for r in rows}, {"Le22", "Le24"})

    def test_filtre_au_seul(self):
        rows, _ = action_log.search_actions(ts_to="2026-09-22 23:59:59")
        self.assertEqual({r["player_name"] for r in rows}, {"Le20", "Le22"})

    def test_intervalle_du_au_inclusif(self):
        rows, _ = action_log.search_actions(
            ts_from="2026-09-20 00:00:00", ts_to="2026-09-22 23:59:59",
        )
        self.assertEqual({r["player_name"] for r in rows}, {"Le20", "Le22"})

    def test_aucune_borne_renvoie_tout(self):
        rows, _ = action_log.search_actions(ts_from=None, ts_to=None)
        self.assertEqual(len(rows), 3)


class SearchAutresFiltresTest(ActionLogSearchTestCase):
    def setUp(self):
        super().setUp()
        self._log(
            tournament_name="Tournoi A", tournament_path="/tmp/a.tournoi",
            user_name="Raj", category="eliminations", player_name="Alice",
        )
        self._log(
            tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi",
            user_name="Marie", role="DIRTO", category="clock", action="toggle_pause",
            player_name="Bob",
        )

    def test_filtre_tournoi_par_path(self):
        rows, _ = action_log.search_actions(tournament_path="/tmp/a.tournoi")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tournament_name"], "Tournoi A")

    def test_filtre_utilisateur(self):
        rows, _ = action_log.search_actions(user_name="Marie")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["player_name"], "Bob")

    def test_filtre_categorie(self):
        rows, _ = action_log.search_actions(category="clock")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "clock")

    def test_filtre_joueur(self):
        rows, _ = action_log.search_actions(player_name="Alice")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_name"], "Raj")

    def test_combinaison_de_plusieurs_filtres(self):
        rows, _ = action_log.search_actions(
            tournament_path="/tmp/b.tournoi", user_name="Marie", category="clock",
        )
        self.assertEqual(len(rows), 1)
        rows, _ = action_log.search_actions(
            tournament_path="/tmp/b.tournoi", user_name="Raj",
        )
        self.assertEqual(rows, [])

    def test_user_name_null_absent_si_filtre_actif_mais_present_sans_filtre(self):
        self._log(user_name=None, role="NONE", player_name=None)
        rows_all, _ = action_log.search_actions()
        self.assertEqual(len(rows_all), 3)
        rows_marie, _ = action_log.search_actions(user_name="Marie")
        self.assertEqual(len(rows_marie), 1)

    def test_player_name_null_present_sans_filtre_joueur(self):
        self._log(player_name=None, category="clock", action="chronometre")
        rows, _ = action_log.search_actions()
        self.assertIn(None, [r["player_name"] for r in rows])


class SearchLimiteTest(ActionLogSearchTestCase):
    def test_pas_de_troncature_sous_la_limite(self):
        for i in range(5):
            self._log(player_name=f"joueur-{i}")
        rows, truncated = action_log.search_actions(limit=10)
        self.assertEqual(len(rows), 5)
        self.assertFalse(truncated)

    def test_troncature_exacte_a_la_limite(self):
        for i in range(5):
            self._log(player_name=f"joueur-{i}")
        rows, truncated = action_log.search_actions(limit=5)
        self.assertEqual(len(rows), 5)
        self.assertFalse(truncated, "exactement `limit` lignes ne doit pas déclencher la troncature")

    def test_troncature_signalee_au_dela_de_la_limite(self):
        for i in range(7):
            self._log(player_name=f"joueur-{i}")
        rows, truncated = action_log.search_actions(limit=5)
        self.assertEqual(len(rows), 5)
        self.assertTrue(truncated)

    def test_troncature_garde_les_plus_recentes(self):
        for i in range(7):
            self._log(player_name=f"joueur-{i}")
            time.sleep(1.01)
        rows, truncated = action_log.search_actions(limit=5)
        self.assertTrue(truncated)
        self.assertEqual(
            [r["player_name"] for r in rows],
            [f"joueur-{i}" for i in (6, 5, 4, 3, 2)],
        )

    def test_pas_de_count_supplementaire_un_seul_select(self):
        """"Sans COUNT(*) supplémentaire" (demande explicite) — vérifié
        en comptant les appels réels d'exécution SQL. sqlite3.Connection
        est un type C immuable (on ne peut pas monkey-patcher sa méthode
        execute directement) : on intercepte plutôt _open_read_connection
        elle-même, avec un petit wrapper qui délègue tout le reste."""
        for i in range(3):
            self._log(player_name=f"joueur-{i}")

        calls = []
        real_open = action_log._open_read_connection

        class _CountingConn:
            def __init__(self, real_conn):
                self._real_conn = real_conn

            def execute(self, sql, params=()):
                calls.append(sql)
                return self._real_conn.execute(sql, params)

            def close(self):
                self._real_conn.close()

        def counting_open(path):
            return _CountingConn(real_open(path))

        with patch.object(action_log, "_open_read_connection", counting_open):
            action_log.search_actions(limit=10)
        select_calls = [c for c in calls if c.strip().upper().startswith("SELECT")]
        self.assertEqual(len(select_calls), 1, f"un seul SELECT attendu, obtenu : {select_calls}")


# =====================================================================
# 3. list_tournaments / list_users / list_players / list_categories
# =====================================================================
class ListTournamentsTest(ActionLogSearchTestCase):
    def test_tournois_distincts_par_path(self):
        self._log(tournament_name="Tournoi A", tournament_path="/tmp/a1.tournoi")
        self._log(tournament_name="Tournoi A", tournament_path="/tmp/a1.tournoi")
        self._log(tournament_name="Tournoi B", tournament_path="/tmp/b.tournoi")
        result = action_log.list_tournaments()
        self.assertEqual(
            {(e["tournament_name"], e["tournament_path"]) for e in result},
            {("Tournoi A", "/tmp/a1.tournoi"), ("Tournoi B", "/tmp/b.tournoi")},
        )

    def test_meme_nom_deux_paths_differents_conserve_les_deux_entrees(self):
        # Cas explicitement signalé dans l'analyse validée : deux
        # tournois différents peuvent légitimement partager le même nom
        # — la désambiguïsation d'affichage est laissée à main.py,
        # action_log.py doit ici renvoyer les DEUX entrées distinctes.
        self._log(tournament_name="Tournoi du vendredi", tournament_path="/tmp/v1.tournoi")
        self._log(tournament_name="Tournoi du vendredi", tournament_path="/tmp/v2.tournoi")
        result = action_log.list_tournaments()
        self.assertEqual(len(result), 2)
        self.assertEqual({e["tournament_path"] for e in result}, {"/tmp/v1.tournoi", "/tmp/v2.tournoi"})

    def test_triee_par_nom_puis_chemin(self):
        self._log(tournament_name="Zorro", tournament_path="/tmp/z.tournoi")
        self._log(tournament_name="Alpha", tournament_path="/tmp/a.tournoi")
        result = action_log.list_tournaments()
        self.assertEqual([e["tournament_name"] for e in result], ["Alpha", "Zorro"])


class ListUsersTest(ActionLogSearchTestCase):
    def test_utilisateurs_distincts_non_null(self):
        self._log(user_name="Raj")
        self._log(user_name="Marie", role="DIRTO")
        self._log(user_name="Raj")
        self._log(user_name=None, role="NONE")
        self.assertEqual(action_log.list_users(), ["Marie", "Raj"])

    def test_aucun_utilisateur_liste_vide(self):
        self._log(user_name=None, role="NONE")
        self.assertEqual(action_log.list_users(), [])


class ListPlayersTest(ActionLogSearchTestCase):
    def test_joueurs_distincts_non_null(self):
        self._log(player_name="Bob")
        self._log(player_name="Alice")
        self._log(player_name="Bob")
        self._log(player_name=None, category="clock", action="toggle_pause")
        self.assertEqual(action_log.list_players(), ["Alice", "Bob"])

    def test_tri_naturel_francais_insensible_casse_et_accents(self):
        for name in ("zoé", "Émile", "alice", "Émilie", "Bernard"):
            self._log(player_name=name)
        result = action_log.list_players()
        # Attendu (casefold + accents neutralisés pour la clé de tri) :
        # alice, Bernard, Émile, Émilie, zoé — mais l'orthographe et les
        # accents d'ORIGINE doivent rester intacts dans le résultat.
        self.assertEqual(result, ["alice", "Bernard", "Émile", "Émilie", "zoé"])

    def test_orthographe_et_accents_dorigine_preserves(self):
        self._log(player_name="Éric")
        result = action_log.list_players()
        self.assertEqual(result, ["Éric"])
        self.assertNotEqual(result, ["ERIC"], "le tri ne doit jamais altérer la valeur elle-même")


class ListCategoriesTest(ActionLogSearchTestCase):
    def test_seules_les_categories_reellement_presentes_sont_renvoyees(self):
        self._log(category="eliminations", action="eliminate")
        self._log(category="clock", action="toggle_pause")
        result = action_log.list_categories()
        self.assertEqual(result, ["eliminations", "clock"])  # ordre _CATEGORY_ORDER

    def test_ordre_fixe_pas_alphabetique(self):
        self._log(category="rebalance", action="rebalance_answer")
        self._log(category="eliminations", action="eliminate")
        self._log(category="admin_only", action="end_tournament")
        result = action_log.list_categories()
        self.assertEqual(result, ["eliminations", "rebalance", "admin_only"])

    def test_base_vide_liste_vide(self):
        self.assertEqual(action_log.list_categories(), [])


# =====================================================================
# 4. Libellés français (category_label/action_label/role_label/
#    result_label) — utilisés par main.py pour le tableau/export/détail.
# =====================================================================
class LabelsTest(unittest.TestCase):
    def test_category_label_connue(self):
        self.assertEqual(action_log.category_label("eliminations"), "Éliminations")
        self.assertEqual(action_log.category_label("admin_only"), "Administration")

    def test_category_label_inconnue_repli_sur_la_valeur_brute(self):
        self.assertEqual(action_log.category_label("futur_inconnu"), "futur_inconnu")

    def test_action_label_connue(self):
        self.assertEqual(action_log.action_label("eliminate"), "Élimination")
        self.assertEqual(action_log.action_label("rebalance_answer"), "Réponse au rééquilibrage (UTG)")

    def test_action_label_inconnue_repli_sur_la_valeur_brute(self):
        self.assertEqual(action_log.action_label("futur_inconnu"), "futur_inconnu")

    def test_role_label_none_devient_non_lie(self):
        self.assertEqual(action_log.role_label("NONE"), "Non lié")

    def test_role_label_admin_dirto_inchanges(self):
        self.assertEqual(action_log.role_label("ADMIN"), "ADMIN")
        self.assertEqual(action_log.role_label("DIRTO"), "DIRTO")

    def test_result_label_les_4_valeurs(self):
        self.assertEqual(action_log.result_label("SUCCESS"), "Réussi")
        self.assertEqual(action_log.result_label("ACCEPTED"), "Accepté")
        self.assertEqual(action_log.result_label("DENIED"), "Refusé")
        self.assertEqual(action_log.result_label("ERROR"), "Erreur")


# =====================================================================
# 5. Lecture EN LECTURE SEULE (query_only) + jamais bloquante pour une
#    écriture concurrente (mode WAL).
# =====================================================================
class LectureSeuleTest(ActionLogSearchTestCase):
    def test_une_connexion_de_lecture_ne_peut_pas_ecrire(self):
        self._log(player_name="Alice")
        conn = action_log._open_read_connection(self.db_path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO actions_log (ts, tournament_name, tournament_path, role, category, action, result) VALUES ('x','x','x','ADMIN','clock','toggle_pause','SUCCESS')")
        finally:
            conn.close()


class ConcurrenceLectureEcritureTest(ActionLogSearchTestCase):
    def test_lecture_pendant_ecriture_continue_naleve_jamais(self):
        stop = threading.Event()
        errors = []

        def writer():
            i = 0
            while not stop.is_set():
                try:
                    self._log(player_name=f"joueur-{i}")
                except Exception as e:  # pragma: no cover
                    errors.append(e)
                i += 1

        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            for _ in range(20):
                try:
                    action_log.search_actions(limit=50)
                    action_log.list_tournaments()
                    action_log.list_users()
                    action_log.list_players()
                    action_log.list_categories()
                except Exception as e:  # pragma: no cover
                    errors.append(e)
        finally:
            stop.set()
            t.join(timeout=5)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
