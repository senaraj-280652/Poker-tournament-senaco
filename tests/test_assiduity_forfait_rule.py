# -*- coding: utf-8 -*-
"""Tests ciblés de la règle métier validée le 2026-09-18 pour les Primes
de présence et d'assiduité :

"Un joueur FORFAIT (status='withdrawn') est un joueur inscrit qui n'est
jamais venu au tournoi. Il ne doit donc PAS recevoir les points de
présence, être considéré présent pour l'assiduité, ni permettre de
maintenir une série d'assiduité."

- joueur 'active' ou 'eliminated' = présent (a réellement joué) ;
- joueur 'withdrawn' (forfait) = absent, dans CE tournoi ET dans
  l'historique consulté par un tournoi ultérieur ;
- joueur supprimé (DELETE réel, Database.delete_player) = absent,
  puisqu'il n'existe plus du tout dans la table players.

Fonctions concernées : Database.get_presence_bonuses(), Database.
get_assiduity_bonuses(), et la nouvelle fonction dédiée _read_present_
player_names_from_file() (jamais read_player_names_from_file() elle-
même, réservée à un usage général sans rapport avec les primes — voir
ReadPlayerNamesFromFileNonRegressionTest ci-dessous).

Le fonctionnement "même dossier, non récursif" de find_previous_
tournament_files() est volontairement INCHANGÉ par cette demande : les
tests D vérifient que les séries Vendredi/Dimanche restent bien
indépendantes quand ces jours vivent dans des sous-dossiers séparés."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


def _new_db(path, **settings):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = database.Database(path)
    if settings:
        db.set_settings({k: str(v) for k, v in settings.items()})
    return db


class PresenceBonusForfaitTest(unittest.TestCase):
    """E) Prime de présence : actif/éliminé = OUI, forfait = NON."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="presence_forfait_")
        self.addCleanup(self._tmp.cleanup)

    def test_joueur_actif_recoit_la_prime(self):
        db = _new_db(
            os.path.join(self._tmp.name, "t.tournoi"),
            tournament_date="2026-01-01", attendance_bonus_points=5,
        )
        db.add_player("Alice")
        self.addCleanup(db.conn.close)
        self.assertEqual(db.get_presence_bonuses(), {"Alice": 5})

    def test_joueur_elimine_recoit_la_prime(self):
        db = _new_db(
            os.path.join(self._tmp.name, "t.tournoi"),
            tournament_date="2026-01-01", attendance_bonus_points=5,
        )
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.eliminate_player(b, eliminated_by_id=a)
        self.addCleanup(db.conn.close)
        bonuses = db.get_presence_bonuses()
        self.assertEqual(bonuses.get("Bob"), 5)
        self.assertEqual(bonuses.get("Alice"), 5)

    def test_joueur_forfait_ne_recoit_pas_la_prime(self):
        db = _new_db(
            os.path.join(self._tmp.name, "t.tournoi"),
            tournament_date="2026-01-01", attendance_bonus_points=5,
        )
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.withdraw_player(b)
        self.addCleanup(db.conn.close)
        bonuses = db.get_presence_bonuses()
        self.assertNotIn("Bob", bonuses)
        self.assertEqual(bonuses.get("Alice"), 5)


class ReadPlayerNamesFromFileNonRegressionTest(unittest.TestCase):
    """Vérifie explicitement que read_player_names_from_file() (usage
    général : reprendre la liste des joueurs d'un tournoi précédent dans
    un nouveau tournoi, voir main.py) N'A PAS été modifiée par cette
    demande — elle continue d'inclure les forfaits, contrairement à la
    nouvelle _read_present_player_names_from_file (réservée aux
    primes)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="read_names_noreg_")
        self.addCleanup(self._tmp.cleanup)
        path = os.path.join(self._tmp.name, "t.tournoi")
        db = _new_db(path, tournament_date="2026-01-01")
        a = db.add_player("Alice")
        db.add_player("Bob")
        db.withdraw_player(a)
        db.conn.close()
        self.path = path

    def test_read_player_names_from_file_inclut_toujours_les_forfaits(self):
        names = {n.strip().lower() for n in database.read_player_names_from_file(self.path)}
        self.assertEqual(names, {"alice", "bob"})

    def test_read_present_player_names_from_file_exclut_les_forfaits(self):
        names = database._read_present_player_names_from_file(self.path)
        self.assertEqual(names, {"bob"})


class AssiduityConsecutiveDaysScenariosTest(unittest.TestCase):
    """Scénarios A à D de la demande, assiduity_consecutive_days=3
    (needed_previous=2) — fichiers .tournoi réels sur disque."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="assiduity_scenarios_")
        self.addCleanup(self._tmp.cleanup)

    def _settings(self, **extra):
        base = {"assiduity_bonus_points": 10, "assiduity_consecutive_days": 3}
        base.update(extra)
        return base

    def _assiduity_points_for(self, db, name):
        row = next((r for r in db.get_assiduity_bonuses() if r["name"] == name), None)
        return row["points"] if row else None

    def test_A_trois_vendredis_presents_bonus_oui(self):
        folder = os.path.join(self._tmp.name, "Vendredi")
        db1 = _new_db(os.path.join(folder, "v1.tournoi"), tournament_date="2026-01-02", **self._settings())
        db1.add_player("Alice")
        db1.conn.close()
        db2 = _new_db(os.path.join(folder, "v2.tournoi"), tournament_date="2026-01-09", **self._settings())
        db2.add_player("Alice")
        db2.conn.close()
        db3 = _new_db(os.path.join(folder, "v3.tournoi"), tournament_date="2026-01-16", **self._settings())
        db3.add_player("Alice")
        self.addCleanup(db3.conn.close)

        self.assertEqual(self._assiduity_points_for(db3, "Alice"), 10)

    def test_B_forfait_au_vendredi_2_casse_la_serie(self):
        folder = os.path.join(self._tmp.name, "Vendredi")
        db1 = _new_db(os.path.join(folder, "v1.tournoi"), tournament_date="2026-01-02", **self._settings())
        db1.add_player("Alice")
        db1.conn.close()
        db2 = _new_db(os.path.join(folder, "v2.tournoi"), tournament_date="2026-01-09", **self._settings())
        a2 = db2.add_player("Alice")
        db2.withdraw_player(a2)  # forfait
        db2.conn.close()
        db3 = _new_db(os.path.join(folder, "v3.tournoi"), tournament_date="2026-01-16", **self._settings())
        db3.add_player("Alice")
        self.addCleanup(db3.conn.close)

        self.assertEqual(self._assiduity_points_for(db3, "Alice"), 0)

    def test_C_elimine_au_vendredi_2_maintient_la_serie(self):
        folder = os.path.join(self._tmp.name, "Vendredi")
        db1 = _new_db(os.path.join(folder, "v1.tournoi"), tournament_date="2026-01-02", **self._settings())
        db1.add_player("Alice")
        db1.conn.close()
        db2 = _new_db(os.path.join(folder, "v2.tournoi"), tournament_date="2026-01-09", **self._settings())
        a2 = db2.add_player("Alice")
        bob2 = db2.add_player("Bob")
        db2.eliminate_player(a2, eliminated_by_id=bob2)  # Alice a réellement joué puis éliminée
        db2.conn.close()
        db3 = _new_db(os.path.join(folder, "v3.tournoi"), tournament_date="2026-01-16", **self._settings())
        db3.add_player("Alice")
        self.addCleanup(db3.conn.close)

        self.assertEqual(self._assiduity_points_for(db3, "Alice"), 10)

    def test_D_dimanches_absents_nont_aucune_influence_sur_les_vendredis(self):
        """Vendredi/ et Dimanche/ comme sous-dossiers séparés (organisation
        réelle de l'utilisateur) : les Dimanches (forfait ou simplement
        absent) ne doivent ni casser ni compléter la série des Vendredis,
        find_previous_tournament_files étant non récursif et limité au
        MÊME dossier que le tournoi en cours — mécanisme INCHANGÉ par
        cette demande."""
        vendredi = os.path.join(self._tmp.name, "Vendredi")
        dimanche = os.path.join(self._tmp.name, "Dimanche")

        db_v1 = _new_db(os.path.join(vendredi, "v1.tournoi"), tournament_date="2026-01-02", **self._settings())
        db_v1.add_player("Alice")
        db_v1.conn.close()

        # Dimanche 1 : forfait (inscrite mais jamais venue).
        db_d1 = _new_db(os.path.join(dimanche, "d1.tournoi"), tournament_date="2026-01-04", **self._settings())
        a_d1 = db_d1.add_player("Alice")
        db_d1.withdraw_player(a_d1)
        db_d1.conn.close()

        db_v2 = _new_db(os.path.join(vendredi, "v2.tournoi"), tournament_date="2026-01-09", **self._settings())
        db_v2.add_player("Alice")
        db_v2.conn.close()

        # Dimanche 2 : totalement absente (jamais inscrite).
        db_d2 = _new_db(os.path.join(dimanche, "d2.tournoi"), tournament_date="2026-01-11", **self._settings())
        db_d2.add_player("Bob")  # un autre joueur, Alice n'y figure pas du tout
        db_d2.conn.close()

        db_v3 = _new_db(os.path.join(vendredi, "v3.tournoi"), tournament_date="2026-01-16", **self._settings())
        db_v3.add_player("Alice")
        self.addCleanup(db_v3.conn.close)

        self.assertEqual(self._assiduity_points_for(db_v3, "Alice"), 10)

    def test_forfait_au_tournoi_courant_ne_recoit_pas_lassiduite(self):
        """Extension symétrique de la règle (pas explicitement listée dans
        les 4 scénarios mais implicite : "il ne doit pas être considéré
        présent pour l'assiduité") : un joueur forfait AU TOURNOI EN
        COURS ne doit pas recevoir le bonus, même s'il remplissait la
        condition les deux fois précédentes."""
        folder = os.path.join(self._tmp.name, "Vendredi")
        db1 = _new_db(os.path.join(folder, "v1.tournoi"), tournament_date="2026-01-02", **self._settings())
        db1.add_player("Alice")
        db1.conn.close()
        db2 = _new_db(os.path.join(folder, "v2.tournoi"), tournament_date="2026-01-09", **self._settings())
        db2.add_player("Alice")
        db2.conn.close()
        db3 = _new_db(os.path.join(folder, "v3.tournoi"), tournament_date="2026-01-16", **self._settings())
        a3 = db3.add_player("Alice")
        db3.withdraw_player(a3)  # forfait CE SOIR-LÀ, malgré un historique parfait
        self.addCleanup(db3.conn.close)

        self.assertEqual(self._assiduity_points_for(db3, "Alice"), 0)


class PrimesSummaryAndStatsConsistencyTest(unittest.TestCase):
    """Vérifie que get_primes_summary() (source de vérité, jamais
    dupliquée) reflète bien la nouvelle règle, et que build_period_
    summary (total_presence_assiduity/TOTAL Pts en Statistiques) en
    hérite automatiquement sans formule séparée."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="primes_summary_forfait_")
        self.addCleanup(self._tmp.cleanup)

    def test_get_primes_summary_reflete_la_regle(self):
        db = _new_db(
            os.path.join(self._tmp.name, "t.tournoi"),
            tournament_date="2026-01-01", attendance_bonus_points=5,
        )
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.withdraw_player(b)
        self.addCleanup(db.conn.close)

        rows = {r["name"]: r for r in db.get_primes_summary()}
        self.assertEqual(rows["Alice"]["presence"], 5)
        self.assertEqual(rows["Bob"]["presence"], 0)
        self.assertEqual(rows["Bob"]["total"], 0)

    def test_build_period_summary_total_presence_assiduity_exclut_le_forfait(self):
        path = os.path.join(self._tmp.name, "t.tournoi")
        db = _new_db(path, tournament_date="2026-01-01", attendance_bonus_points=5)
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.withdraw_player(b)
        db.conn.close()

        summary = database.build_period_summary(self._tmp.name, recursive=False)
        by_name = {p["name"]: p for p in summary["players"]}
        self.assertEqual(by_name["Alice"]["total_presence_assiduity"], 5)
        self.assertEqual(by_name["Bob"]["total_presence_assiduity"], 0)
        self.assertEqual(by_name["Bob"]["total_points"], 0)

    def test_exports_csv_xlsx_pdf_ne_montrent_pas_le_forfait_credite(self):
        """Ne duplique aucune formule : les exports (CSV/XLSX/PDF) lisent
        tous PERIOD_PLAYER_COLUMNS -> total_presence_assiduity, déjà
        corrigé côté build_period_summary/get_primes_summary — ce test
        vérifie juste l'absence de régression bout en bout via l'export
        CSV (le plus simple à inspecter textuellement)."""
        path = os.path.join(self._tmp.name, "t.tournoi")
        # ranking_formula="none" : isole la prime de présence (sans ça,
        # Bob forfait laisse Alice seule active -> tournoi "Terminé" ->
        # Alice gagnante -> points de classement en plus, hors sujet ici).
        db = _new_db(
            path, tournament_date="2026-01-01", attendance_bonus_points=5,
            ranking_formula="none",
        )
        db.add_player("Alice")
        b = db.add_player("Bob")
        db.withdraw_player(b)
        db.conn.close()

        summary = database.build_period_summary(self._tmp.name, recursive=False)
        out_path = os.path.join(self._tmp.name, "export.csv")
        database.export_period_summary_csv(
            summary, out_path,
            player_keys=["name", "total_presence_assiduity", "total_points"],
        )
        with open(out_path, encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
        rows = {line.split(";")[0]: line.split(";")[1:] for line in lines if ";" in line and line.split(";")[0] in ("Alice", "Bob")}
        self.assertEqual(rows["Bob"], ["0", "0"])
        self.assertEqual(rows["Alice"], ["5", "5"])


if __name__ == "__main__":
    unittest.main()
