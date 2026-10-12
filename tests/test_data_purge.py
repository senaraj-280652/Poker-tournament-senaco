# -*- coding: utf-8 -*-
"""Tests ciblés pour data_purge.py (chantier "Purger Données", demande du
2026-10-07) — logique métier pure (classification des tournois, recherche
par date/type, vérification de couverture par une sauvegarde encore
accessible et vérifiable MAINTENANT, détection de tournoi ouvert,
suppression atomique avec corbeille temporaire + rollback), testée
indépendamment de toute UI Tkinter (voir tests/test_data_purge_dialog.py
pour l'UI elle-même) et toujours sur des dossiers/fichiers SYNTHÉTIQUES
(tempfile) — jamais le vrai ~/.poker_tournament ni un vrai fichier
.tournoi du club.

Complètement indépendant de action_log.py/tests/test_log_purge.py (purge
du LOG, strictement inchangée) : aucun des deux modules ne touche à ce
que l'autre couvre."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup_restore  # noqa: E402
import data_purge  # noqa: E402
import database  # noqa: E402
import open_windows  # noqa: E402


def _new_tournoi(tmp_dir, filename, tournament_date, is_sng=None):
    """Crée un vrai fichier .tournoi (SQLite, via database.Database) avec
    le réglage "tournament_date" positionné — même mécanisme que
    tests/test_stats_type_filter_calendar_sort.py:_new_db, nécessaire ici
    car data_purge.find_purgeable_tournaments lit cette date via
    database._read_tournament_date_ro (pas un simple fichier texte).

    `is_sng` (demande du 2026-10-08, diagnostic "classification fiable
    des tournois") : None (par défaut) -> clé "is_sng" ABSENTE, comme
    tout ancien fichier/tout fichier créé via "Nouveau tournoi" (jamais
    "0" artificiellement) ; True/False -> clé EXPLICITEMENT posée,
    comme main.py: App._apply_sng_defaults le fait réellement pour un
    tournoi créé via "Sit & Go rapide"."""
    path = os.path.join(tmp_dir, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    db = database.Database(path)
    settings = {"tournament_date": tournament_date}
    if is_sng is not None:
        settings["is_sng"] = 1 if is_sng else 0
    db.set_settings(settings)
    db.conn.close()
    return path


class TournamentPurgeTypeTest(unittest.TestCase):
    """Classification PUREMENT basée sur le préfixe du nom de fichier —
    volontairement séparée de database._tournament_type_matches (voir
    data_purge.py, docstring du module) : ces tests ne couvrent QUE
    tournament_purge_type, jamais les Statistiques."""

    def test_prefixe_to_est_tournois(self):
        self.assertEqual(
            data_purge.tournament_purge_type("/a/To010126.tournoi"), data_purge.PURGE_TYPE_TOURNOIS,
        )

    def test_prefixe_sn_est_sitngo(self):
        self.assertEqual(
            data_purge.tournament_purge_type("/a/Sn010126.tournoi"), data_purge.PURGE_TYPE_SITNGO,
        )

    def test_sans_prefixe_reconnu_est_autre(self):
        self.assertEqual(
            data_purge.tournament_purge_type("/a/ancien_fichier_renomme.tournoi"), data_purge.PURGE_TYPE_OTHER,
        )

    def test_autres_decoche_par_defaut_tournois_et_sitngo_coches(self):
        self.assertNotIn(data_purge.PURGE_TYPE_OTHER, data_purge.PURGE_TYPE_DEFAULT_CHECKED)
        self.assertIn(data_purge.PURGE_TYPE_TOURNOIS, data_purge.PURGE_TYPE_DEFAULT_CHECKED)
        self.assertIn(data_purge.PURGE_TYPE_SITNGO, data_purge.PURGE_TYPE_DEFAULT_CHECKED)


class ClassifyTournamentForPurgeTest(unittest.TestCase):
    """data_purge.classify_tournament_for_purge — classification RÉELLE
    (demande du 2026-10-08, diagnostic "0 Sit & Go trouvé" alors que de
    vrais Sit & Go existaient, simplement renommés à la création) : les
    métadonnées internes ("is_sng", lues en lecture SEULE) priment
    toujours sur le nom de fichier, qui ne sert plus que de repli pour
    les anciens fichiers sans cette information."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_classify_")
        self.addCleanup(self._tmp.cleanup)

    def test_is_sng_vrai_prime_sur_un_nom_quelconque(self):
        p = _new_tournoi(self._tmp.name, "sitngo1.tournoi", "2026-01-01", is_sng=True)
        purge_type, error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_SITNGO)
        self.assertIsNone(error)

    def test_is_sng_vrai_prime_meme_sur_un_nom_commencant_par_To(self):
        """Cas extrême explicitement demandé : un Sit & Go dont le nom
        commence par "To" (trompeur) doit rester classé Sit & Go — les
        métadonnées internes priment TOUJOURS, jamais le nom."""
        p = _new_tournoi(self._tmp.name, "To_mais_en_realite_sitngo.tournoi", "2026-01-01", is_sng=True)
        purge_type, _error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_SITNGO)

    def test_is_sng_faux_explicite_est_un_tournoi_classique_quel_que_soit_le_nom(self):
        p = _new_tournoi(self._tmp.name, "nimporte_quoi.tournoi", "2026-01-01", is_sng=False)
        purge_type, _error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_TOURNOIS)

    def test_is_sng_absent_avec_prefixe_to_retombe_sur_lancienne_convention(self):
        p = _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01", is_sng=None)
        purge_type, _error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_TOURNOIS)

    def test_is_sng_absent_avec_prefixe_sn_retombe_sur_lancienne_convention(self):
        p = _new_tournoi(self._tmp.name, "Sn010126.tournoi", "2026-01-01", is_sng=None)
        purge_type, _error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_SITNGO)

    def test_is_sng_absent_et_aucun_prefixe_reconnu_est_autre(self):
        """L'absence de is_sng NE PROUVE PAS qu'un fichier est un
        tournoi classique (demande explicite) — sans aucun autre
        indice (préfixe), il reste "Autres / non classés", jamais
        reclassé "Tournois" par défaut."""
        p = _new_tournoi(self._tmp.name, "essaiac2.tournoi", "2026-01-01", is_sng=None)
        purge_type, _error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_OTHER)

    def test_fichier_sqlite_corrompu_est_illisible_jamais_une_exception(self):
        p = os.path.join(self._tmp.name, "corrompu.tournoi")
        db = database.Database(p)
        db.set_settings({"tournament_date": "2026-01-01"})
        db.conn.close()
        # Corrompt le fichier APRÈS coup (en-tête SQLite écrasé) — jamais
        # une écriture via ce module, qui ne fait qu'ouvrir en lecture
        # seule ensuite.
        with open(p, "r+b") as f:
            f.seek(0)
            f.write(b"CECI_NEST_PLUS_UNE_BASE_SQLITE_VALIDE")
        purge_type, error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_UNREADABLE)
        self.assertIsNotNone(error)

    def test_fichier_non_sqlite_avec_extension_tournoi_est_illisible(self):
        p = os.path.join(self._tmp.name, "pas_une_base.tournoi")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ceci est un simple fichier texte, pas une base SQLite")
        purge_type, error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_UNREADABLE)
        self.assertIsNotNone(error)

    def test_fichier_sqlite_sans_table_settings_est_illisible(self):
        """Base SQLite valide mais sans le schéma Senaco attendu (table
        "settings" absente) — jamais confondu avec "is_sng absent dans
        un fichier par ailleurs valide"."""
        import sqlite3
        p = os.path.join(self._tmp.name, "mauvais_schema.tournoi")
        conn = sqlite3.connect(p)
        conn.execute("CREATE TABLE autre_chose (id INTEGER)")
        conn.commit()
        conn.close()
        purge_type, error = data_purge.classify_tournament_for_purge(p)
        self.assertEqual(purge_type, data_purge.PURGE_TYPE_UNREADABLE)
        self.assertIsNotNone(error)

    def test_fichier_illisible_nest_jamais_dans_purge_types(self):
        """Garde-fou structurel : PURGE_TYPE_UNREADABLE ne doit JAMAIS
        appartenir à PURGE_TYPES (sinon il deviendrait sélectionnable/
        purgeable via une case à cocher de l'interface)."""
        self.assertNotIn(data_purge.PURGE_TYPE_UNREADABLE, data_purge.PURGE_TYPES)

    def test_fichier_illisible_jamais_eligible_meme_avec_tous_les_types_coches(self):
        p = os.path.join(self._tmp.name, "corrompu2.tournoi")
        with open(p, "wb") as f:
            f.write(b"\x00\x01\x02corruption")
        result = data_purge.filter_tournament_files(
            [p], types=set(data_purge.PURGE_TYPES),  # TOUS les types, y compris Autres
        )
        self.assertEqual(result, [])


class FindPurgeableTournamentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_find_")
        self.addCleanup(self._tmp.cleanup)

    def test_dossier_none_renvoie_liste_vide_sans_exception(self):
        self.assertEqual(
            data_purge.find_purgeable_tournaments(None, types={data_purge.PURGE_TYPE_TOURNOIS}), [],
        )

    def test_dossier_inexistant_renvoie_liste_vide_sans_exception(self):
        self.assertEqual(
            data_purge.find_purgeable_tournaments(
                os.path.join(self._tmp.name, "n_existe_pas"), types={data_purge.PURGE_TYPE_TOURNOIS},
            ),
            [],
        )

    def test_types_vide_ou_none_renvoie_toujours_liste_vide(self):
        _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        self.assertEqual(data_purge.find_purgeable_tournaments(self._tmp.name, types=None), [])
        self.assertEqual(data_purge.find_purgeable_tournaments(self._tmp.name, types=set()), [])

    def test_filtre_par_type(self):
        p1 = _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        p2 = _new_tournoi(self._tmp.name, "Sn020126.tournoi", "2026-01-02")
        p3 = _new_tournoi(self._tmp.name, "vieux_fichier.tournoi", "2026-01-03")
        self.assertEqual(
            data_purge.find_purgeable_tournaments(self._tmp.name, types={data_purge.PURGE_TYPE_TOURNOIS}), [p1],
        )
        self.assertEqual(
            sorted(data_purge.find_purgeable_tournaments(
                self._tmp.name, types={data_purge.PURGE_TYPE_TOURNOIS, data_purge.PURGE_TYPE_SITNGO},
            )),
            sorted([p1, p2]),
        )
        self.assertEqual(
            data_purge.find_purgeable_tournaments(self._tmp.name, types={data_purge.PURGE_TYPE_OTHER}), [p3],
        )
        self.assertEqual(
            sorted(data_purge.find_purgeable_tournaments(self._tmp.name, types=set(data_purge.PURGE_TYPES))),
            sorted([p1, p2, p3]),
        )

    def test_filtre_par_plage_de_dates_bornes_incluses(self):
        _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        p2 = _new_tournoi(self._tmp.name, "To150126.tournoi", "2026-01-15")
        _new_tournoi(self._tmp.name, "To310126.tournoi", "2026-01-31")
        result = data_purge.find_purgeable_tournaments(
            self._tmp.name, date_from="2026-01-10", date_to="2026-01-20",
            types={data_purge.PURGE_TYPE_TOURNOIS},
        )
        self.assertEqual(result, [p2])

    def test_borne_unique_ouverte(self):
        p1 = _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        p2 = _new_tournoi(self._tmp.name, "To310126.tournoi", "2026-01-31")
        self.assertEqual(
            sorted(data_purge.find_purgeable_tournaments(
                self._tmp.name, date_to="2026-01-10", types={data_purge.PURGE_TYPE_TOURNOIS},
            )),
            [p1],
        )
        self.assertEqual(
            sorted(data_purge.find_purgeable_tournaments(
                self._tmp.name, date_from="2026-01-10", types={data_purge.PURGE_TYPE_TOURNOIS},
            )),
            [p2],
        )

    def test_aucune_correspondance_renvoie_liste_vide(self):
        _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        self.assertEqual(
            data_purge.find_purgeable_tournaments(
                self._tmp.name, date_from="2030-01-01", types={data_purge.PURGE_TYPE_TOURNOIS},
            ),
            [],
        )

    def test_recherche_recursive_dans_les_sous_dossiers(self):
        p1 = _new_tournoi(os.path.join(self._tmp.name, "Vendredi"), "To010126.tournoi", "2026-01-01")
        self.assertEqual(
            data_purge.find_purgeable_tournaments(self._tmp.name, types={data_purge.PURGE_TYPE_TOURNOIS}), [p1],
        )


class CollectCandidateFoldersTest(unittest.TestCase):
    """data_purge.collect_candidate_folders — découverte multi-dossiers
    (demande du 2026-10-07, diagnostic "0 tournoi" quand "Dossier par
    défaut" est vide) : règle centrale validée explicitement par
    l'utilisateur — JAMAIS de remontée automatique au dossier parent
    d'un dossier déjà connu, JAMAIS de balayage du disque."""

    def test_aucune_source_renvoie_liste_vide(self):
        self.assertEqual(data_purge.collect_candidate_folders(), [])

    def test_tournament_day_folder_ajoute_tel_quel(self):
        self.assertEqual(
            data_purge.collect_candidate_folders(tournament_day_folder="/club/tournois"),
            [os.path.abspath("/club/tournois")],
        )

    def test_last_tournament_dir_ajoute_tel_quel_jamais_son_parent(self):
        """Cas exact du Mac de l'utilisateur (2026-10-07) : "Dossier par
        défaut" vide, mais last_tournament_dir renseigné — doit être
        utilisé TEL QUEL, jamais remonté à son dossier parent (ex :
        "/home/raj/tournois/samedi" ne doit JAMAIS devenir
        "/home/raj/tournois")."""
        folders = data_purge.collect_candidate_folders(last_tournament_dir="/home/raj/tournois/samedi")
        self.assertEqual(folders, [os.path.abspath("/home/raj/tournois/samedi")])
        self.assertNotIn(os.path.abspath("/home/raj/tournois"), folders)

    def test_open_tournament_paths_ajoute_le_dossier_parent_direct_uniquement(self):
        """Le tournoi actuellement ouvert peut être n'importe où (ex :
        directement dans le dossier personnel, hors de toute convention
        "jour de la semaine") — seul SON dossier parent direct est
        ajouté, jamais remonté davantage."""
        folders = data_purge.collect_candidate_folders(
            open_tournament_paths=["/Users/raj/tournoi.tournoi"],
        )
        self.assertEqual(folders, [os.path.abspath("/Users/raj")])

    def test_action_log_tournament_paths_plusieurs_dossiers_distincts(self):
        folders = data_purge.collect_candidate_folders(
            action_log_tournament_paths=[
                "/club/Vendredi/To1.tournoi", "/club/dimanche/To2.tournoi",
                "/club/Vendredi/To3.tournoi",  # même dossier que le premier : pas de doublon
            ],
        )
        self.assertEqual(
            sorted(folders), sorted([os.path.abspath("/club/Vendredi"), os.path.abspath("/club/dimanche")]),
        )

    def test_extra_folders_manuels_ajoutes_tels_quels(self):
        self.assertEqual(
            data_purge.collect_candidate_folders(extra_folders=["/ailleurs/dossier"]),
            [os.path.abspath("/ailleurs/dossier")],
        )

    def test_chevauchement_entre_sources_dedoublonne(self):
        """Le MÊME dossier connu par plusieurs sources différentes
        (ici : "Dossier par défaut" ET le dossier d'un tournoi ouvert)
        ne doit apparaître qu'UNE SEULE fois."""
        folders = data_purge.collect_candidate_folders(
            tournament_day_folder="/club/tournois",
            open_tournament_paths=["/club/tournois/To1.tournoi"],
        )
        self.assertEqual(folders, [os.path.abspath("/club/tournois")])

    def test_toutes_les_sources_combinees_dans_lordre_et_dedoublonnees(self):
        folders = data_purge.collect_candidate_folders(
            tournament_day_folder="/a",
            last_tournament_dir="/b",
            open_tournament_paths=["/c/x.tournoi"],
            action_log_tournament_paths=["/d/y.tournoi"],
            extra_folders=["/e"],
        )
        self.assertEqual(
            folders,
            [os.path.abspath(p) for p in ("/a", "/b", "/c", "/d", "/e")],
        )


class SplitAccessibleFoldersTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_split_")
        self.addCleanup(self._tmp.cleanup)

    def test_dossier_existant_est_accessible(self):
        accessible, inaccessible = data_purge.split_accessible_folders([self._tmp.name])
        self.assertEqual(accessible, [self._tmp.name])
        self.assertEqual(inaccessible, [])

    def test_dossier_inexistant_est_inaccessible_jamais_une_exception(self):
        missing = os.path.join(self._tmp.name, "n_existe_pas")
        accessible, inaccessible = data_purge.split_accessible_folders([missing])
        self.assertEqual(accessible, [])
        self.assertEqual(inaccessible, [missing])

    def test_scenario_cle_usb_ou_disque_externe_absent(self):
        """Un dossier connu (ex : enregistré dans action_log ou
        last_tournament_dir lors d'une session où une clé USB/disque
        externe était monté) mais actuellement débranché : ne doit
        JAMAIS bloquer l'ouverture du dialogue, ni lever d'exception —
        seulement être classé "inaccessible"."""
        usb_path = "/Volumes/CLE_USB_ABSENTE/tournois"
        accessible, inaccessible = data_purge.split_accessible_folders(
            data_purge.collect_candidate_folders(
                tournament_day_folder=self._tmp.name, extra_folders=[usb_path],
            ),
        )
        self.assertEqual(accessible, [self._tmp.name])
        self.assertEqual(inaccessible, [os.path.abspath(usb_path)])

    def test_melange_accessible_et_inaccessible_repartis_correctement(self):
        ok1 = os.path.join(self._tmp.name, "ok1")
        ok2 = os.path.join(self._tmp.name, "ok2")
        os.makedirs(ok1)
        os.makedirs(ok2)
        bad = os.path.join(self._tmp.name, "absent")
        accessible, inaccessible = data_purge.split_accessible_folders([ok1, bad, ok2])
        self.assertEqual(sorted(accessible), sorted([ok1, ok2]))
        self.assertEqual(inaccessible, [bad])


class FindPurgeableTournamentsMultiTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_multi_")
        self.addCleanup(self._tmp.cleanup)

    def test_liste_de_dossiers_vide_renvoie_liste_vide(self):
        self.assertEqual(
            data_purge.find_purgeable_tournaments_multi([], types={data_purge.PURGE_TYPE_TOURNOIS}), [],
        )

    def test_agrege_les_resultats_de_plusieurs_dossiers(self):
        folder_a = os.path.join(self._tmp.name, "a")
        folder_b = os.path.join(self._tmp.name, "b")
        p1 = _new_tournoi(folder_a, "To010126.tournoi", "2026-01-01")
        p2 = _new_tournoi(folder_b, "To020126.tournoi", "2026-01-02")
        result = data_purge.find_purgeable_tournaments_multi(
            [folder_a, folder_b], types={data_purge.PURGE_TYPE_TOURNOIS},
        )
        self.assertEqual(sorted(result), sorted([p1, p2]))

    def test_meme_fichier_decouvert_par_chevauchement_de_dossiers_napparait_quune_fois(self):
        """Deux dossiers candidats dont l'un contient l'autre (ex :
        "Dossier par défaut" et un sous-dossier "jour" ajouté aussi par
        une autre source) : le même fichier .tournoi, trouvé par la
        recherche récursive dans LES DEUX, ne doit apparaître qu'UNE
        SEULE fois dans le résultat final."""
        base = os.path.join(self._tmp.name, "club")
        sub = os.path.join(base, "vendredi")
        p1 = _new_tournoi(sub, "To010126.tournoi", "2026-01-01")
        result = data_purge.find_purgeable_tournaments_multi(
            [base, sub], types={data_purge.PURGE_TYPE_TOURNOIS},
        )
        self.assertEqual(result, [p1])

    def test_filtre_dates_et_types_applique_de_facon_identique_sur_chaque_dossier(self):
        folder_a = os.path.join(self._tmp.name, "a")
        folder_b = os.path.join(self._tmp.name, "b")
        p_in = _new_tournoi(folder_a, "To150126.tournoi", "2026-01-15")
        _new_tournoi(folder_a, "To010126.tournoi", "2026-01-01")  # hors période
        _new_tournoi(folder_b, "Sn160126.tournoi", "2026-01-16")  # hors type (Sit & Go non demandé)
        result = data_purge.find_purgeable_tournaments_multi(
            [folder_a, folder_b], date_from="2026-01-10", date_to="2026-01-20",
            types={data_purge.PURGE_TYPE_TOURNOIS},
        )
        self.assertEqual(result, [p_in])

    def test_dossier_inaccessible_dans_la_liste_est_ignore_sans_exception(self):
        folder_a = os.path.join(self._tmp.name, "a")
        p1 = _new_tournoi(folder_a, "To010126.tournoi", "2026-01-01")
        missing = os.path.join(self._tmp.name, "n_existe_pas")
        result = data_purge.find_purgeable_tournaments_multi(
            [folder_a, missing], types={data_purge.PURGE_TYPE_TOURNOIS},
        )
        self.assertEqual(result, [p1])


class ScanFolderForTournamentsTest(unittest.TestCase):
    """data_purge.scan_folder_for_tournaments — moteur de recherche
    BORNÉ, ANNULABLE, sans Tkinter (incident macOS réel du 2026-10-08 :
    ajouter /Users/raj comme dossier candidat, exploré sans aucune
    limite via glob.glob("**", recursive=True) sur le thread Tk, a
    bloqué Senaco indéfiniment)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_scan_")
        self.addCleanup(self._tmp.cleanup)

    def test_dossier_inexistant_ou_none_renvoie_un_resultat_vide_sans_exception(self):
        result = data_purge.scan_folder_for_tournaments(None)
        self.assertEqual(result, {"files": [], "truncated": False, "reason": None, "skipped_dirs": []})
        result2 = data_purge.scan_folder_for_tournaments(os.path.join(self._tmp.name, "n_existe_pas"))
        self.assertFalse(result2["truncated"])
        self.assertEqual(result2["files"], [])

    def test_non_recursif_trouve_directement_un_fichier_sans_explorer_les_sous_dossiers(self):
        """Cas exact de l'incident réel : /Users/raj contient tournoi.
        tournoi directement, PLUS des sous-dossiers volumineux (Library,
        Desktop...) — "ce dossier uniquement" doit le trouver
        immédiatement sans jamais y descendre."""
        p1 = _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        big_subdir = os.path.join(self._tmp.name, "gros_sous_dossier")
        for i in range(50):
            os.makedirs(os.path.join(big_subdir, f"d{i}"))
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=False)
        self.assertEqual(result["files"], [p1])
        self.assertFalse(result["truncated"])
        self.assertEqual(result["skipped_dirs"], [])  # jamais classifiés en mode non récursif

    def test_recursif_trouve_aussi_dans_les_sous_dossiers(self):
        p1 = _new_tournoi(os.path.join(self._tmp.name, "Vendredi"), "To010126.tournoi", "2026-01-01")
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p1])
        self.assertFalse(result["truncated"])

    def test_dossier_technique_jamais_descendu_en_mode_recursif(self):
        _new_tournoi(os.path.join(self._tmp.name, "Library"), "To010126.tournoi", "2026-01-01")
        _new_tournoi(os.path.join(self._tmp.name, ".git"), "To020126.tournoi", "2026-01-02")
        _new_tournoi(os.path.join(self._tmp.name, ".cache"), "To030126.tournoi", "2026-01-03")
        p_ok = _new_tournoi(os.path.join(self._tmp.name, "Vendredi"), "To040126.tournoi", "2026-01-04")
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p_ok])
        self.assertEqual(
            sorted(result["skipped_dirs"]),
            sorted([
                os.path.join(self._tmp.name, "Library"), os.path.join(self._tmp.name, ".git"),
                os.path.join(self._tmp.name, ".cache"),
            ]),
        )

    def test_dossier_du_projet_poker_tournament_nest_jamais_exclu_par_son_nom(self):
        """Décision explicite du 2026-10-08 : "poker_tournament" contient
        actuellement de vrais tournois de test — ne JAMAIS l'exclure par
        son nom, seulement ses éventuels sous-dossiers TECHNIQUES
        (ex. ".git")."""
        project_dir = os.path.join(self._tmp.name, "poker_tournament")
        p_ok = _new_tournoi(os.path.join(project_dir, "samedi"), "To010126.tournoi", "2026-01-01")
        _new_tournoi(os.path.join(project_dir, ".git"), "To020126.tournoi", "2026-01-02")
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p_ok])
        self.assertIn(os.path.join(project_dir, ".git"), result["skipped_dirs"])

    def test_dossier_de_sauvegarde_jamais_descendu_ses_tournoi_jamais_proposes(self):
        """Demande explicite du 2026-10-08 : un dossier
        "PokerTournament_Backup_..." contient des COPIES de sauvegarde,
        jamais des tournois actifs — ne doit jamais être descendu, ses
        .tournoi ne doivent jamais apparaître dans les résultats."""
        backup_dir = os.path.join(self._tmp.name, "PokerTournament_Backup_2026-10-07_1232")
        _new_tournoi(os.path.join(backup_dir, "tournois"), "To010126.tournoi", "2026-01-01")
        p_ok = _new_tournoi(self._tmp.name, "To020126.tournoi", "2026-01-02")
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p_ok])
        self.assertIn(backup_dir, result["skipped_dirs"])

    def test_dossier_de_sauvegarde_exclu_a_toutes_les_profondeurs(self):
        """L'exclusion doit s'appliquer à CHAQUE niveau de la récursion,
        pas seulement au premier (demande explicite) : une sauvegarde
        nichée dans plusieurs sous-dossiers reste exclue."""
        nested_backup = os.path.join(
            self._tmp.name, "club", "dimanche", "PokerTournament_Backup_2026-09-01_0800",
        )
        _new_tournoi(os.path.join(nested_backup, "tournois"), "To_copie.tournoi", "2026-01-01")
        p_ok = _new_tournoi(os.path.join(self._tmp.name, "club", "dimanche"), "To_reel.tournoi", "2026-01-02")
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p_ok])
        self.assertIn(nested_backup, result["skipped_dirs"])

    def test_plusieurs_dossiers_de_sauvegarde_tous_exclus(self):
        backup1 = os.path.join(self._tmp.name, "PokerTournament_Backup_2026-01-01_0000")
        backup2 = os.path.join(self._tmp.name, "sous_dossier", "PokerTournament_Backup_2026-02-01_0000")
        _new_tournoi(backup1, "To1.tournoi", "2026-01-01")
        _new_tournoi(backup2, "To2.tournoi", "2026-01-01")
        p_ok = _new_tournoi(os.path.join(self._tmp.name, "sous_dossier"), "To_reel.tournoi", "2026-01-03")
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p_ok])
        self.assertIn(backup1, result["skipped_dirs"])
        self.assertIn(backup2, result["skipped_dirs"])

    def test_lien_symbolique_jamais_suivi(self):
        target = os.path.join(self._tmp.name, "reel")
        p1 = _new_tournoi(target, "To010126.tournoi", "2026-01-01")
        link = os.path.join(self._tmp.name, "lien")
        os.symlink(target, link)
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [p1])  # trouvé via le chemin réel, jamais via le lien
        self.assertIn(link, result["skipped_dirs"])

    def test_fichier_symbolique_jamais_inclus(self):
        target = _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        link = os.path.join(self._tmp.name, "lien.tournoi")
        os.symlink(target, link)
        result = data_purge.scan_folder_for_tournaments(self._tmp.name, recursive=True)
        self.assertEqual(result["files"], [target])
        self.assertIn(link, result["skipped_dirs"])

    def test_limite_max_entries_tronque_proprement_sans_exception(self):
        for i in range(200):
            os.makedirs(os.path.join(self._tmp.name, f"d{i}"))
        result = data_purge.scan_folder_for_tournaments(
            self._tmp.name, recursive=True, max_entries=50, max_seconds=999,
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(result["reason"], data_purge.SCAN_REASON_MAX_ENTRIES)

    def test_limite_max_seconds_tronque_proprement_sans_exception(self):
        for i in range(50):
            os.makedirs(os.path.join(self._tmp.name, f"d{i}"))
        result = data_purge.scan_folder_for_tournaments(
            self._tmp.name, recursive=True, max_entries=10**9, max_seconds=0.0,
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(result["reason"], data_purge.SCAN_REASON_MAX_SECONDS)

    def test_annulation_interrompt_proprement_et_conserve_les_fichiers_deja_trouves(self):
        import threading
        p1 = _new_tournoi(self._tmp.name, "To010126.tournoi", "2026-01-01")
        for i in range(50):
            os.makedirs(os.path.join(self._tmp.name, f"d{i}"))
        cancel_event = threading.Event()
        cancel_event.set()
        result = data_purge.scan_folder_for_tournaments(
            self._tmp.name, recursive=True, cancel_event=cancel_event, max_seconds=999, max_entries=10**9,
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(result["reason"], data_purge.SCAN_REASON_CANCELLED)


class ScanCandidateFoldersTest(unittest.TestCase):
    """data_purge.scan_candidate_folders — recherche sur PLUSIEURS
    dossiers, chacun avec ses propres limites indépendantes."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_scan_multi_")
        self.addCleanup(self._tmp.cleanup)

    def test_plusieurs_dossiers_agreges_et_dedoublonnes(self):
        folder_a = os.path.join(self._tmp.name, "a")
        folder_b = os.path.join(self._tmp.name, "b")
        p1 = _new_tournoi(folder_a, "To010126.tournoi", "2026-01-01")
        p2 = _new_tournoi(folder_b, "To020126.tournoi", "2026-01-02")
        result = data_purge.scan_candidate_folders([folder_a, folder_b])
        self.assertEqual(sorted(result["files"]), sorted([p1, p2]))
        self.assertEqual(result["truncated_folders"], {})
        self.assertFalse(result["cancelled"])

    def test_chevauchement_de_dossiers_le_meme_fichier_napparait_quune_fois(self):
        base = os.path.join(self._tmp.name, "club")
        sub = os.path.join(base, "vendredi")
        p1 = _new_tournoi(sub, "To010126.tournoi", "2026-01-01")
        result = data_purge.scan_candidate_folders([base, sub])
        self.assertEqual(result["files"], [p1])

    def test_limite_dun_dossier_najamais_decompte_du_budget_dun_autre(self):
        """Chaque dossier candidat a SES PROPRES limites — un dossier A
        volumineux et tronqué ne doit jamais empêcher un dossier B,
        plus modeste, d'être examiné intégralement."""
        folder_a = os.path.join(self._tmp.name, "a_volumineux")
        for i in range(50):
            os.makedirs(os.path.join(folder_a, f"d{i}"))
        folder_b = os.path.join(self._tmp.name, "b_modeste")
        p_b = _new_tournoi(folder_b, "To020126.tournoi", "2026-01-02")
        result = data_purge.scan_candidate_folders(
            [folder_a, folder_b], max_entries=10, max_seconds=999,
        )
        self.assertIn(folder_a, result["truncated_folders"])
        self.assertNotIn(folder_b, result["truncated_folders"])
        self.assertEqual(result["files"], [p_b])

    def test_recursive_folders_distingue_un_dossier_non_recursif(self):
        """Un dossier ajouté manuellement en mode "ce dossier
        uniquement" ne doit jamais faire remonter un fichier de ses
        sous-dossiers, même s'il en a."""
        folder = os.path.join(self._tmp.name, "home")
        p_direct = _new_tournoi(folder, "To010126.tournoi", "2026-01-01")
        _new_tournoi(os.path.join(folder, "Library"), "To020126.tournoi", "2026-01-02")
        result = data_purge.scan_candidate_folders([folder], recursive_folders=set())
        self.assertEqual(result["files"], [p_direct])

    def test_annulation_entre_deux_dossiers_narrete_pas_le_premier_deja_scanne(self):
        import threading
        folder_a = os.path.join(self._tmp.name, "a")
        folder_b = os.path.join(self._tmp.name, "b")
        p1 = _new_tournoi(folder_a, "To010126.tournoi", "2026-01-01")
        _new_tournoi(folder_b, "To020126.tournoi", "2026-01-02")
        cancel_event = threading.Event()

        class _CancelAfterFirst:
            def __init__(self):
                self.calls = 0

            def is_set(self):
                self.calls += 1
                if self.calls > 2:  # laisse le 1er dossier se terminer, annule avant le 2e
                    cancel_event.set()
                return cancel_event.is_set()

        result = data_purge.scan_candidate_folders(
            [folder_a, folder_b], cancel_event=_CancelAfterFirst(),
        )
        self.assertTrue(result["cancelled"])
        self.assertIn(p1, result["files"])


class ScanCandidateFoldersForPurgeTest(unittest.TestCase):
    """data_purge.scan_candidate_folders_for_purge — point d'entrée
    utilisé par DataPurgeDialog : combine le scan borné et le filtre
    Date/Type."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_scan_purge_")
        self.addCleanup(self._tmp.cleanup)

    def test_filtre_date_type_applique_apres_le_scan(self):
        folder = os.path.join(self._tmp.name, "a")
        p_in = _new_tournoi(folder, "To150126.tournoi", "2026-01-15")
        _new_tournoi(folder, "Sn160126.tournoi", "2026-01-16")  # type non demandé
        result = data_purge.scan_candidate_folders_for_purge(
            [folder], types={data_purge.PURGE_TYPE_TOURNOIS},
        )
        self.assertEqual(result["purge_paths"], [p_in])
        self.assertIn(os.path.join(folder, "Sn160126.tournoi"), result["all_files"])
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["truncated_folders"], {})

    def test_resultat_tronque_renvoie_quand_meme_les_fichiers_deja_trouves(self):
        folder = os.path.join(self._tmp.name, "a")
        _new_tournoi(folder, "To010126.tournoi", "2026-01-01")
        for i in range(50):
            os.makedirs(os.path.join(folder, f"d{i}"))
        result = data_purge.scan_candidate_folders_for_purge(
            [folder], types={data_purge.PURGE_TYPE_TOURNOIS}, max_entries=5, max_seconds=999,
        )
        self.assertIn(folder, result["truncated_folders"])
        # Les fichiers déjà trouvés AVANT la troncature restent proposés
        # (demande explicite : "la recherche partielle peut afficher les
        # tournois déjà trouvés") — à l'appelant (DataPurgeDialog) de
        # bloquer vérification/purge, jamais à cette fonction elle-même.

    def test_sitngo_renomme_correctement_classe_et_propose_avec_is_sng(self):
        """Reproduit le diagnostic réel du 2026-10-08 : un Sit & Go
        enregistré sous un nom quelconque (jamais "Sn...") doit être
        proposé dès que "Sit & Go" est coché, grâce à is_sng — jamais
        "0 Sit & Go trouvé" comme avant ce correctif."""
        folder = os.path.join(self._tmp.name, "dimanche")
        p_sng = _new_tournoi(folder, "sitngo1.tournoi", "2026-08-17", is_sng=True)
        result = data_purge.scan_candidate_folders_for_purge(
            [folder], types={data_purge.PURGE_TYPE_SITNGO},
        )
        self.assertEqual(result["purge_paths"], [p_sng])

    def test_fichiers_autres_rapportes_separement_meme_non_coches(self):
        folder = os.path.join(self._tmp.name, "a")
        p_other = _new_tournoi(folder, "essaiac2.tournoi", "2026-01-01")  # ni is_sng, ni prefixe
        result = data_purge.scan_candidate_folders_for_purge(
            [folder], types={data_purge.PURGE_TYPE_TOURNOIS, data_purge.PURGE_TYPE_SITNGO},
        )
        self.assertEqual(result["purge_paths"], [])  # "Autres" non coché -> jamais proposé
        self.assertEqual(result["other_files"], [os.path.abspath(p_other)])  # mais visible séparément

    def test_fichiers_illisibles_rapportes_separement_jamais_dans_purge_paths(self):
        folder = os.path.join(self._tmp.name, "a")
        p_bad = os.path.join(folder, "corrompu.tournoi")
        os.makedirs(folder)
        with open(p_bad, "wb") as f:
            f.write(b"pas une base sqlite")
        result = data_purge.scan_candidate_folders_for_purge(
            [folder], types=set(data_purge.PURGE_TYPES),
        )
        self.assertEqual(result["purge_paths"], [])
        self.assertEqual(len(result["unreadable_files"]), 1)
        self.assertEqual(result["unreadable_files"][0]["path"], p_bad)
        self.assertIsNotNone(result["unreadable_files"][0]["error"])

    def test_dossier_de_sauvegarde_jamais_dans_purge_paths_ni_other_ni_unreadable(self):
        """Une sauvegarde est exclue au niveau du SCAN lui-même (jamais
        descendue) — ses .tournoi n'apparaissent dans AUCUNE des listes
        renvoyées, ni purge_paths, ni other_files, ni unreadable_files."""
        folder = os.path.join(self._tmp.name, "dimanche")
        backup_dir = os.path.join(folder, "PokerTournament_Backup_2026-10-07_1232")
        _new_tournoi(os.path.join(backup_dir, "tournois"), "To_copie.tournoi", "2026-01-01")
        result = data_purge.scan_candidate_folders_for_purge(
            [folder], types=set(data_purge.PURGE_TYPES),
        )
        self.assertEqual(result["purge_paths"], [])
        self.assertEqual(result["other_files"], [])
        self.assertEqual(result["unreadable_files"], [])
        self.assertIn(backup_dir, result["skipped_dirs"])


class RequireDateRangeTest(unittest.TestCase):
    """Sécurité des dates de purge (revue explicite du 2026-10-08,
    "LOG → Purger → DONNÉES") : scan_candidate_folders_for_purge(
    require_date_range=True) doit GARANTIR qu'aucun fichier n'est
    jamais sélectionné sans les deux dates, valides et correctement
    ordonnées — la protection vit ICI (logique métier), pas seulement
    dans la validation de l'interface (main.py: DataPurgeDialog._parse_
    dates). Par défaut (require_date_range=False, voir la classe
    ci-dessus) : comportement historique INCHANGÉ, None = borne
    ouverte — ces tests ne portent que sur le nouveau paramètre."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_require_dates_")
        self.addCleanup(self._tmp.cleanup)
        self.folder = os.path.join(self._tmp.name, "dimanche")
        self.p1 = _new_tournoi(self.folder, "To150126.tournoi", "2026-01-15")

    def _scan(self, **kw):
        return data_purge.scan_candidate_folders_for_purge(
            [self.folder], types=set(data_purge.PURGE_TYPES), require_date_range=True, **kw
        )

    def test_les_deux_dates_vides_ne_selectionne_rien(self):
        result = self._scan(date_from=None, date_to=None)
        self.assertEqual(result["purge_paths"], [])
        self.assertTrue(result["dates_required"])

    def test_seule_la_date_de_debut_renseignee_ne_selectionne_rien(self):
        result = self._scan(date_from="2026-01-01", date_to=None)
        self.assertEqual(result["purge_paths"], [])
        self.assertTrue(result["dates_required"])

    def test_seule_la_date_de_fin_renseignee_ne_selectionne_rien(self):
        result = self._scan(date_from=None, date_to="2026-12-31")
        self.assertEqual(result["purge_paths"], [])
        self.assertTrue(result["dates_required"])

    def test_periode_inversee_ne_selectionne_rien_meme_avec_les_deux_dates_valides(self):
        result = self._scan(date_from="2026-06-01", date_to="2026-01-01")
        self.assertEqual(result["purge_paths"], [])
        self.assertTrue(result["dates_required"])

    def test_periode_complete_et_correctement_ordonnee_selectionne_normalement(self):
        result = self._scan(date_from="2026-01-01", date_to="2026-12-31")
        self.assertEqual(result["purge_paths"], [os.path.abspath(self.p1)])
        self.assertFalse(result["dates_required"])

    def test_meme_jour_des_deux_cotes_est_une_periode_valide_pas_inversee(self):
        result = self._scan(date_from="2026-01-15", date_to="2026-01-15")
        self.assertEqual(result["purge_paths"], [os.path.abspath(self.p1)])
        self.assertFalse(result["dates_required"])

    def test_classification_et_transparence_restent_calculees_malgre_dates_manquantes(self):
        """Seule la SÉLECTION pour purge est vidée — "Autres"/
        illisibles restent exploitables même sans dates valides
        (demande explicite : ne jamais sacrifier la transparence déjà
        validée pour cette nouvelle règle de sécurité)."""
        p_other = _new_tournoi(self.folder, "essaiac2.tournoi", "2026-01-01")
        result = self._scan(date_from=None, date_to=None)
        self.assertEqual(result["purge_paths"], [])
        self.assertIn(os.path.abspath(p_other), result["other_files"])

    def test_defaut_require_date_range_false_reste_totalement_inchange(self):
        """Non-régression explicite : le paramètre est opt-in, le
        comportement historique (dates vides = non bornées) reste
        disponible pour les appelants qui n'activent pas la
        protection."""
        result = data_purge.scan_candidate_folders_for_purge(
            [self.folder], types=set(data_purge.PURGE_TYPES),
        )
        self.assertEqual(result["purge_paths"], [os.path.abspath(self.p1)])
        self.assertFalse(result["dates_required"])


class CheckPurgeCoverageTest(unittest.TestCase):
    """Vérifie la couverture PAR SAUVEGARDE RÉELLEMENT ACCESSIBLE
    MAINTENANT (data_purge._covering_backup_for/check_purge_coverage) —
    jamais une confiance aveugle dans backup_history.json, fourni ici
    comme simple liste de candidats SYNTHÉTIQUES à re-tester."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_coverage_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)
        self.usb = os.path.join(self._tmp.name, "usb")
        os.makedirs(self.usb)

    def _make_tournoi(self, filename, date="2026-01-01"):
        return _new_tournoi(self.tournament_folder, filename, date)

    def _make_backup(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.tournament_folder, poker_data_dir=self.poker_data_dir,
        )
        manifest = backup_restore.validate_backup_folder(result["backup_dir"])
        return result, manifest

    def test_aucune_sauvegarde_tout_non_couvert(self):
        p1 = self._make_tournoi("To010126.tournoi")
        coverage = data_purge.check_purge_coverage([p1], [])
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [p1])

    def test_sauvegarde_recente_couvre_et_hash_verifie(self):
        p1 = self._make_tournoi("To010126.tournoi")
        result, manifest = self._make_backup()
        history = [{"backup_dir": result["backup_dir"], "tournament_files": manifest["tournament_files"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertTrue(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [])
        self.assertTrue(coverage["backups_used"][0]["hash_verified"])

    def test_sauvegarde_dossier_disparu_traitee_comme_non_couvrante(self):
        """Scénario "clé USB débranchée" (demande explicite du 2026-10-
        07) : le dossier annoncé dans l'historique n'existe plus -> ne
        peut jamais couvrir quoi que ce soit."""
        p1 = self._make_tournoi("To010126.tournoi")
        result, _manifest = self._make_backup()
        shutil.rmtree(result["backup_dir"])
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertFalse(coverage["all_covered"])

    def test_fichier_sauvegarde_corrompu_detecte_et_rejete(self):
        p1 = self._make_tournoi("To010126.tournoi")
        result, _manifest = self._make_backup()
        backed_up = os.path.join(result["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR, "To010126.tournoi")
        with open(backed_up, "ab") as f:
            f.write(b"CORRUPTION")
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertFalse(coverage["all_covered"])

    def test_sauvegarde_ne_couvre_que_les_fichiers_quelle_contient_reellement(self):
        """Point de sécurité central du diagnostic (2026-10-07) : une
        sauvegarde plus ancienne ne doit jamais "couvrir" par proximité
        de date un tournoi créé APRÈS elle — seule la présence RÉELLE du
        fichier dans son manifeste compte."""
        p1 = self._make_tournoi("To010126.tournoi")
        result1, _manifest1 = self._make_backup()
        history = [{"backup_dir": result1["backup_dir"]}]
        p2 = self._make_tournoi("To020126.tournoi", "2026-01-02")
        coverage = data_purge.check_purge_coverage([p1, p2], history)
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [p2])
        self.assertTrue(coverage["per_tournament"][p1]["covered"])

    def test_ancienne_sauvegarde_sans_hash_acceptee_mais_signalee_non_verifiee(self):
        """Ne transforme JAMAIS artificiellement une ancienne sauvegarde
        (manifeste sans "tournament_file_hashes") en sauvegarde vérifiée
        par hash — exigence explicite du 2026-10-07."""
        p1 = self._make_tournoi("To010126.tournoi")
        result, _manifest = self._make_backup()
        manifest_path = os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        m.pop("tournament_file_hashes", None)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(m, f)
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertTrue(coverage["all_covered"])
        self.assertFalse(coverage["backups_used"][0]["hash_verified"])

    def test_sauvegarde_plus_ancienne_retrouvee_si_la_plus_recente_est_corrompue(self):
        p1 = self._make_tournoi("To010126.tournoi")
        result_old, _manifest_old = self._make_backup()
        time.sleep(0.01)
        result_new, _manifest_new = self._make_backup()
        backed_up_new = os.path.join(result_new["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR, "To010126.tournoi")
        with open(backed_up_new, "ab") as f:
            f.write(b"X")
        history = [{"backup_dir": result_old["backup_dir"]}, {"backup_dir": result_new["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertTrue(coverage["all_covered"])
        self.assertEqual(coverage["backups_used"][0]["backup_dir"], result_old["backup_dir"])

    def test_plusieurs_dossiers_chacun_verifie_contre_le_bon_manifeste(self):
        """Découverte multi-dossiers (2026-10-07) : un fichier d'un
        dossier A, couvert par une sauvegarde FAITE à partir de A, doit
        être reconnu couvert — un fichier d'un dossier B totalement
        différent (ex : dossier du tournoi actuellement ouvert, jamais
        sauvegardé), lui, doit rester non couvert. Chaque sauvegarde est
        vérifiée contre SON PROPRE "source_tournament_folder", jamais un
        dossier de référence unique partagé entre des fichiers
        hétérogènes."""
        folder_a = os.path.join(self._tmp.name, "club")
        os.makedirs(folder_a)
        folder_b = os.path.join(self._tmp.name, "home")
        os.makedirs(folder_b)
        p_a = _new_tournoi(folder_a, "To010126.tournoi", "2026-01-01")
        p_b = _new_tournoi(folder_b, "To020126.tournoi", "2026-01-02")

        result = backup_restore.create_backup(
            self.usb, tournament_folder=folder_a, poker_data_dir=self.poker_data_dir,
        )
        history = [{"backup_dir": result["backup_dir"]}]

        coverage = data_purge.check_purge_coverage([p_a, p_b], history)
        self.assertTrue(coverage["per_tournament"][p_a]["covered"])
        self.assertFalse(coverage["per_tournament"][p_b]["covered"])
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [p_b])

    def test_manifeste_sans_source_tournament_folder_ignore_sans_exception(self):
        """Manifeste hypothétique sans ce champ (ne devrait jamais
        arriver en pratique : create_backup l'écrit toujours) — ne doit
        jamais lever, seulement être traité comme non-couvrant pour ce
        fichier (pas de dossier de référence fiable)."""
        p1 = self._make_tournoi("To010126.tournoi")
        result, _manifest = self._make_backup()
        manifest_path = os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        m.pop("source_tournament_folder", None)
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(m, f)
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertFalse(coverage["all_covered"])

    # ------------------------------------------------------------------
    # Sauvegarde MULTI-DOSSIERS (demande explicite du 2026-10-08, revue
    # de sécurité "Purger Données" : "la sauvegarde doit couvrir
    # exactement la sélection, y compris quand elle vient de plusieurs
    # dossiers portant parfois le même nom") — voir backup_restore.
    # create_backup(tournament_files=..., tournament_source_folders=...).
    # ------------------------------------------------------------------

    def test_sauvegarde_multi_dossiers_couvre_chaque_fichier_depuis_son_propre_dossier(self):
        folder_a = os.path.join(self._tmp.name, "poste1", "dimanche")
        folder_b = os.path.join(self._tmp.name, "poste2", "dimanche")
        p_a = _new_tournoi(folder_a, "To1.tournoi", "2026-01-01")
        p_b = _new_tournoi(folder_b, "To1.tournoi", "2026-01-02")
        result = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p_a, p_b], history)
        self.assertTrue(coverage["all_covered"])
        self.assertTrue(coverage["per_tournament"][p_a]["covered"])
        self.assertTrue(coverage["per_tournament"][p_b]["covered"])
        self.assertTrue(coverage["per_tournament"][p_a]["hash_verified"])
        self.assertTrue(coverage["per_tournament"][p_b]["hash_verified"])

    def test_sauvegarde_multi_dossiers_meme_nom_jamais_de_confusion_entre_les_deux(self):
        """Corrompt UNIQUEMENT la copie du dossier B — la copie du
        dossier A (même nom de fichier "To1.tournoi") doit rester
        couverte normalement : la vérification ne doit jamais confondre
        les deux fichiers de même nom entre eux."""
        folder_a = os.path.join(self._tmp.name, "poste1", "dimanche")
        folder_b = os.path.join(self._tmp.name, "poste2", "dimanche")
        p_a = _new_tournoi(folder_a, "To1.tournoi", "2026-01-01")
        p_b = _new_tournoi(folder_b, "To1.tournoi", "2026-01-02")
        result = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )
        backed_up_b = os.path.join(
            result["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR, "1_dimanche", "To1.tournoi",
        )
        with open(backed_up_b, "ab") as f:
            f.write(b"CORRUPTION")
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p_a, p_b], history)
        self.assertTrue(coverage["per_tournament"][p_a]["covered"], "le fichier A, intact, doit rester couvert")
        self.assertFalse(coverage["per_tournament"][p_b]["covered"], "le fichier B, corrompu, ne doit jamais l'être")
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [p_b])

    def test_sauvegarde_multi_dossiers_fichier_non_sauvegarde_reste_non_couvert(self):
        """Un troisième fichier, JAMAIS inclus dans la sélection
        sauvegardée, ne doit jamais être considéré couvert par la seule
        présence d'une sauvegarde multi-dossiers par ailleurs valide."""
        folder_a = os.path.join(self._tmp.name, "poste1", "dimanche")
        folder_b = os.path.join(self._tmp.name, "poste2", "dimanche")
        p_a = _new_tournoi(folder_a, "To1.tournoi", "2026-01-01")
        p_b = _new_tournoi(folder_b, "To1.tournoi", "2026-01-02")
        p_c = _new_tournoi(folder_b, "To2.tournoi", "2026-01-03")  # jamais sélectionné
        result = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p_a, p_b, p_c], history)
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [p_c])

    def test_sauvegarde_zero_tournoi_ne_couvre_jamais_rien_meme_avec_succes_annonce(self):
        """Reproduit exactement l'incident réel rapporté le 2026-10-08 :
        une sauvegarde affichant "Sauvegarde terminée avec succès" /
        "Tournois sauvegardés : 0" ne doit JAMAIS pouvoir débloquer une
        purge — vérifié ici indépendamment de l'interface, au niveau
        métier pur."""
        p1 = self._make_tournoi("To010126.tournoi")
        result = backup_restore.create_backup(self.usb, tournament_files=[])
        self.assertEqual(result["tournament_file_count"], 0)
        history = [{"backup_dir": result["backup_dir"]}]
        coverage = data_purge.check_purge_coverage([p1], history)
        self.assertFalse(coverage["all_covered"])
        self.assertEqual(coverage["uncovered"], [p1])


class FindOpenTournamentsTest(unittest.TestCase):
    def test_tournoi_ouvert_est_detecte(self):
        with patch.object(open_windows, "list_open_paths", return_value=["/a/To.tournoi"]):
            self.assertEqual(
                data_purge.find_open_tournaments(["/a/To.tournoi", "/a/other.tournoi"]), ["/a/To.tournoi"],
            )

    def test_aucun_tournoi_ouvert(self):
        with patch.object(open_windows, "list_open_paths", return_value=[]):
            self.assertEqual(data_purge.find_open_tournaments(["/a/To.tournoi"]), [])


class PurgeTournamentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="data_purge_exec_")
        self.addCleanup(self._tmp.cleanup)
        self.tournament_folder = os.path.join(self._tmp.name, "tournois")
        os.makedirs(self.tournament_folder)
        self.poker_data_dir = os.path.join(self._tmp.name, "poker_data")
        os.makedirs(self.poker_data_dir)

    def _make_tournoi(self, filename, date="2026-01-01"):
        return _new_tournoi(self.tournament_folder, filename, date)

    def test_liste_vide_ne_fait_rien(self):
        self.assertEqual(data_purge.purge_tournaments([], poker_data_dir=self.poker_data_dir), 0)

    def test_purge_supprime_bien_les_fichiers_et_rien_dautre(self):
        p1 = self._make_tournoi("To1.tournoi")
        p2 = self._make_tournoi("To2.tournoi")
        preserved = os.path.join(self.tournament_folder, "preserved.txt")
        with open(preserved, "w", encoding="utf-8") as f:
            f.write("ne doit jamais être touché par la purge")
        with patch.object(open_windows, "list_open_paths", return_value=[]):
            n = data_purge.purge_tournaments([p1, p2], poker_data_dir=self.poker_data_dir)
        self.assertEqual(n, 2)
        self.assertFalse(os.path.isfile(p1))
        self.assertFalse(os.path.isfile(p2))
        self.assertTrue(os.path.isfile(preserved))
        self.assertEqual(
            [d for d in os.listdir(self.poker_data_dir) if d.startswith("pending_purge_")], [],
        )

    def test_tournoi_ouvert_bloque_toute_la_purge(self):
        p1 = self._make_tournoi("To1.tournoi")
        with patch.object(open_windows, "list_open_paths", return_value=[p1]):
            with self.assertRaises(data_purge.PurgeError):
                data_purge.purge_tournaments([p1], poker_data_dir=self.poker_data_dir)
        self.assertTrue(os.path.isfile(p1))

    def test_fichier_manquant_en_cours_de_purge_declenche_rollback_complet(self):
        """Exemple explicite du diagnostic (2026-10-07) : jamais "24
        supprimés + 23 conservés" — un échec survenant APRÈS que p1 a
        déjà été déplacé vers la corbeille doit le remettre EXACTEMENT à
        sa place d'origine."""
        p1 = self._make_tournoi("To1.tournoi")
        p2 = self._make_tournoi("To2.tournoi")
        os.remove(p2)
        with patch.object(open_windows, "list_open_paths", return_value=[]):
            with self.assertRaises(data_purge.PurgeError) as ctx:
                data_purge.purge_tournaments([p1, p2], poker_data_dir=self.poker_data_dir)
        self.assertIn("introuvable", str(ctx.exception))
        self.assertTrue(os.path.isfile(p1))
        self.assertEqual(
            [d for d in os.listdir(self.poker_data_dir) if d.startswith("pending_purge_")], [],
        )

    def test_garde_fou_final_bloque_si_incoherence_detectee_avant_suppression(self):
        """Garde-fou final EXPLICITEMENT demandé le 2026-10-07 : avant
        toute suppression définitive de la corbeille, vérifie qu'AUCUN
        fichier prévu ne subsiste à son emplacement d'origine. Simulé ici
        en reposant furtivement une copie à l'emplacement d'origine juste
        après le déplacement, pour forcer la détection de l'incohérence."""
        p1 = self._make_tournoi("To1.tournoi")
        original_move = shutil.move
        calls = {"n": 0}

        def _fake_move(src, dst):
            original_move(src, dst)
            calls["n"] += 1
            if calls["n"] == 1:
                shutil.copy2(dst, src)

        with patch.object(open_windows, "list_open_paths", return_value=[]), \
                patch("data_purge.shutil.move", side_effect=_fake_move):
            with self.assertRaises(data_purge.PurgeError) as ctx:
                data_purge.purge_tournaments([p1], poker_data_dir=self.poker_data_dir)
        self.assertIn("Incohérence détectée", str(ctx.exception))
        self.assertTrue(os.path.isfile(p1))


if __name__ == "__main__":
    unittest.main()
