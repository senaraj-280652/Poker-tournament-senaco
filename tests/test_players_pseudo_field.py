# -*- coding: utf-8 -*-
"""Colonne "pseudo" de la table `players` (demande du 2026-10-03,
"ajouter la colonne Pseudo au Répertoire") — copiée du Répertoire à
l'inscription (Database.add_player), exactement comme "club" l'est déjà
(voir tests/test_... pour club, non dupliqué ici) : un instantané PAR
TOURNOI, jamais relu en direct depuis roster.py une fois le joueur
ajouté.

Couvre :
1. Migration : un ancien fichier .tournoi sans la colonne "pseudo" est
   mis à jour automatiquement à l'ouverture, sans perte.
2. Database.add_player(name, club, pseudo) — stockage, valeur par
   défaut "", espaces superflus retirés.
3. Non-régression : add_player(name) et add_player(name, club) (appels
   positionnels existants, SANS pseudo) continuent de fonctionner
   normalement.
4. App._add_player / _add_from_roster / le chemin "joueurs sélectionnés
   à la création" (main.py) transmettent bien roster.get_pseudo(name) à
   add_player — vérifié par inspection structurelle du source (jamais
   une resimulation lourde de l'UI Tkinter pour une simple plomberie)."""
import inspect
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402


class AddPlayerPseudoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="players_pseudo_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "A.tournoi"))
        self.addCleanup(self.db.conn.close)

    def test_pseudo_stocke(self):
        pid = self.db.add_player("Alice", pseudo="Toto")
        self.assertEqual(self.db.get_player(pid)["pseudo"], "Toto")

    def test_pseudo_par_defaut_chaine_vide(self):
        pid = self.db.add_player("Alice")
        self.assertEqual(self.db.get_player(pid)["pseudo"], "")

    def test_espaces_superflus_retires(self):
        pid = self.db.add_player("Alice", pseudo="  Toto  ")
        self.assertEqual(self.db.get_player(pid)["pseudo"], "Toto")

    def test_appel_positionnel_name_club_sans_pseudo_fonctionne_toujours(self):
        """Non-régression explicite : add_player(name, club) — signature
        historique, utilisée par de nombreux appels existants — continue
        de fonctionner sans erreur, pseudo retombant sur ""."""
        pid = self.db.add_player("Alice", "CPC")
        player = self.db.get_player(pid)
        self.assertEqual(player["club"], "CPC")
        self.assertEqual(player["pseudo"], "")

    def test_appel_name_seul_fonctionne_toujours(self):
        pid = self.db.add_player("Alice")
        self.assertEqual(self.db.get_player(pid)["pseudo"], "")

    def test_pseudo_nest_jamais_resynchronise_apres_coup(self):
        """Instantané PAR TOURNOI, même principe que "club" : si le
        Répertoire change après l'inscription, ce tournoi garde le
        pseudo tel qu'il était à l'ajout."""
        import roster
        import tempfile as _tempfile
        roster_tmp = _tempfile.TemporaryDirectory()
        self.addCleanup(roster_tmp.cleanup)
        from unittest.mock import patch
        with patch.object(roster, "_roster_path", return_value=os.path.join(roster_tmp.name, "roster.json")):
            roster.set_pseudo("Alice", "Toto")
            pid = self.db.add_player("Alice", pseudo=roster.get_pseudo("Alice"))
            roster.set_pseudo("Alice", "Tata")  # change après coup
            self.assertEqual(self.db.get_player(pid)["pseudo"], "Toto")  # inchangé


class PseudoColumnMigrationTest(unittest.TestCase):
    """Un ancien fichier .tournoi (créé avant ce chantier) n'a pas la
    colonne "pseudo" — Database._migrate doit l'ajouter automatiquement
    à l'ouverture, sans perte des données déjà présentes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="pseudo_migration_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "old.tournoi")

    def _create_legacy_file_without_pseudo(self):
        """Construit un fichier .tournoi minimal mais RÉALISTE (même
        schéma que Database.SCHEMA, sans la colonne pseudo) — pas un
        vrai fichier d'avant 2026-10-03 conservé sur disque (aucun
        disponible dans ce dépôt), mais structurellement équivalent :
        c'est précisément ce que _migrate doit savoir réparer."""
        db = database.Database(self.path)
        db.conn.execute("ALTER TABLE players RENAME TO players_new")
        db.conn.execute("""
            CREATE TABLE players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                buyin_count INTEGER NOT NULL DEFAULT 1,
                rebuy_count INTEGER NOT NULL DEFAULT 0,
                addon_count INTEGER NOT NULL DEFAULT 0,
                chips INTEGER NOT NULL DEFAULT 0,
                table_id INTEGER,
                seat INTEGER,
                status TEXT NOT NULL DEFAULT 'active',
                place INTEGER,
                elim_time TEXT,
                bounty INTEGER NOT NULL DEFAULT 0,
                bounty_won INTEGER NOT NULL DEFAULT 0,
                kills INTEGER NOT NULL DEFAULT 0,
                club TEXT NOT NULL DEFAULT '',
                arrived_at REAL,
                absent_declared_at REAL
            )
        """)
        db.conn.execute(
            "INSERT INTO players(id, name, chips, status, club) "
            "SELECT id, name, chips, status, club FROM players_new"
        )
        db.conn.execute("DROP TABLE players_new")
        db.conn.commit()
        db.conn.close()

    def test_colonne_pseudo_ajoutee_a_louverture(self):
        self._create_legacy_file_without_pseudo()
        db = database.Database(self.path)
        self.addCleanup(db.conn.close)
        cols = {row["name"] for row in db.conn.execute("PRAGMA table_info(players)")}
        self.assertIn("pseudo", cols)

    def test_joueurs_existants_recoivent_pseudo_vide_sans_perte(self):
        db = database.Database(self.path)
        pid = db.add_player("Alice", "CPC")
        db.conn.close()
        # Simule un fichier antérieur : retire la colonne en reconstruisant
        # la table sans elle (comme dans _create_legacy_file_without_pseudo),
        # mais à partir de données réelles déjà insérées.
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("ALTER TABLE players RENAME TO players_old")
        conn.execute("""
            CREATE TABLE players (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                buyin_count INTEGER NOT NULL DEFAULT 1, rebuy_count INTEGER NOT NULL DEFAULT 0,
                addon_count INTEGER NOT NULL DEFAULT 0, chips INTEGER NOT NULL DEFAULT 0,
                table_id INTEGER, seat INTEGER, status TEXT NOT NULL DEFAULT 'active',
                place INTEGER, elim_time TEXT, bounty INTEGER NOT NULL DEFAULT 0,
                bounty_won INTEGER NOT NULL DEFAULT 0, kills INTEGER NOT NULL DEFAULT 0,
                club TEXT NOT NULL DEFAULT '', arrived_at REAL, absent_declared_at REAL
            )
        """)
        conn.execute(
            "INSERT INTO players(id, name, buyin_count, rebuy_count, addon_count, chips, "
            "table_id, seat, status, place, elim_time, bounty, bounty_won, kills, club) "
            "SELECT id, name, buyin_count, rebuy_count, addon_count, chips, table_id, seat, "
            "status, place, elim_time, bounty, bounty_won, kills, club FROM players_old"
        )
        conn.execute("DROP TABLE players_old")
        conn.commit()
        conn.close()

        db2 = database.Database(self.path)
        self.addCleanup(db2.conn.close)
        player = db2.get_player(pid)
        self.assertEqual(player["name"], "Alice")
        self.assertEqual(player["club"], "CPC")
        self.assertEqual(player["pseudo"], "")  # jamais une KeyError/exception


class AddPlayerCallSitesStructuralTest(unittest.TestCase):
    """Vérifie que les 3 points d'appel de Database.add_player dans
    main.py transmettent bien roster.get_pseudo(name) — inspection du
    source plutôt qu'une resimulation lourde de l'UI Tkinter pour une
    simple plomberie (même convention que d'autres vérifications
    structurelles de cette suite, ex. tests/test_phone_identifier_
    permanent_display.py)."""

    def setUp(self):
        with open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
            encoding="utf-8",
        ) as f:
            self.source = f.read()

    def test_add_player_appele_avec_roster_get_pseudo_aux_3_points_dappel(self):
        """Les 3 appels à Database.add_player (initial_players_dialog via
        _choose_tournament_file, _add_player, _add_from_roster) passent
        tous roster.get_pseudo(name) en 3e argument — motif précis
        ", roster.get_pseudo(name))" pour ne compter que les VRAIS appels
        à add_player, pas l'usage distinct dans _edit_pseudo
        (initialvalue=...)."""
        occurrences = self.source.count(", roster.get_pseudo(name))")
        self.assertEqual(occurrences, 3)

    def test_add_player_dans_add_from_roster(self):
        source = inspect.getsource(main.App._add_from_roster)
        self.assertIn("roster.get_pseudo(name)", source)

    def test_add_player_dans_add_player_handler(self):
        source = inspect.getsource(main.App._add_player)
        self.assertIn("roster.get_pseudo(name)", source)

    def test_add_player_dans_choose_tournament_file(self):
        source = inspect.getsource(main.App._choose_tournament_file)
        self.assertIn("roster.get_pseudo(name)", source)


if __name__ == "__main__":
    unittest.main()
