"""Test ciblé de la sauvegarde/restauration sur clé USB (demande du
2026-09-07) : backup_restore.py, testé uniquement avec des dossiers et
fichiers SYNTHÉTIQUES (tempfile.TemporaryDirectory) — jamais le vrai
~/.poker_tournament ni un vrai fichier .tournoi. Tous les appels à
create_backup/restore_backup ci-dessous passent explicitement
`tournament_folder`/`poker_data_dir` (des dossiers temporaires) :
default_tournament_folder()/default_poker_data_dir() — qui liraient
respectivement le vrai ~/.poker_tournament/last_settings.json et le
vrai ~/.poker_tournament — ne sont donc jamais sollicitées ici."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup_restore  # noqa: E402
import main  # noqa: E402


def _write(path, content="contenu"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class _SyntheticEnv:
    """Reconstruit, dans un dossier temporaire, une arborescence synthétique
    imitant celle d'un vrai poste club : un dossier de tournois avec deux
    sous-dossiers "jour de semaine" contenant chacun un faux .tournoi, et
    un dossier ~/.poker_tournament synthétique avec quelques fichiers/
    sous-dossiers représentatifs (réglages, roster, photos)."""

    def __init__(self, tmp_root):
        self.tournament_folder = os.path.join(tmp_root, "tournois_club")
        self.poker_data_dir = os.path.join(tmp_root, "poker_tournament_data_fake")
        _write(os.path.join(self.tournament_folder, "dimanche", "tournoi14.tournoi"), "DB tournoi 14")
        _write(os.path.join(self.tournament_folder, "Vendredi", "tournoi9.tournoi"), "DB tournoi 9")
        _write(os.path.join(self.poker_data_dir, "export_prefs.json"), '{"club_name": "CPC"}')
        _write(os.path.join(self.poker_data_dir, "roster.json"), '{"players": []}')
        _write(os.path.join(self.poker_data_dir, "photos", "jean.jpg"), "FAKEJPEGBYTES")
        # Fichiers d'état de session temporaires (demande du 2026-09-07) :
        # doivent être sauvegardés comme tout le reste, mais jamais
        # réinjectés lors d'une restauration — voir RestoreBackupTest.
        _write(os.path.join(self.poker_data_dir, "open_windows.json"), '{"/tmp/x.tournoi": {"pid": 111}}')
        _write(os.path.join(self.poker_data_dir, "phone_selected_pid.json"), '{"pid": 111}')
        # license.json, lui, n'est PAS exclu : doit être sauvegardé ET
        # restauré normalement.
        _write(os.path.join(self.poker_data_dir, "license.json"), '{"machine_id": "ABC", "club_name": "CPC", "key": "XYZ"}')

    def snapshot(self):
        """{chemin relatif à tmp_root: contenu} de TOUT ce qui existe
        actuellement sous tournament_folder et poker_data_dir — sert à
        vérifier qu'une opération n'a rien modifié par ailleurs."""
        snap = {}
        for base in (self.tournament_folder, self.poker_data_dir):
            for root, _dirs, files in os.walk(base):
                for name in files:
                    p = os.path.join(root, name)
                    with open(p, "r", encoding="utf-8") as f:
                        snap[p] = f.read()
        return snap


class CreateBackupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)

    def test_1_sauvegarde_contient_bien_les_donnees_attendues(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="POSTE-TEST",
        )
        self.assertTrue(os.path.basename(result["backup_dir"]).startswith(
            backup_restore.BACKUP_FOLDER_PREFIX
        ))
        self.assertEqual(result["tournament_file_count"], 2)
        self.assertTrue(result["poker_tournament_data_included"])

        backup_dir = result["backup_dir"]
        self.assertTrue(os.path.isfile(
            os.path.join(backup_dir, "tournois", "dimanche", "tournoi14.tournoi")
        ))
        self.assertTrue(os.path.isfile(
            os.path.join(backup_dir, "tournois", "Vendredi", "tournoi9.tournoi")
        ))
        self.assertTrue(os.path.isfile(
            os.path.join(backup_dir, "poker_tournament_data", "export_prefs.json")
        ))
        self.assertTrue(os.path.isfile(
            os.path.join(backup_dir, "poker_tournament_data", "photos", "jean.jpg")
        ))
        with open(os.path.join(backup_dir, "tournois", "dimanche", "tournoi14.tournoi")) as f:
            self.assertEqual(f.read(), "DB tournoi 14")

    def test_manifeste_contient_les_champs_demandes(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="POSTE-TEST",
        )
        with open(os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)) as f:
            manifest = json.load(f)
        self.assertIn("created_at", manifest)
        self.assertEqual(manifest["app_version"], main.APP_VERSION)
        self.assertEqual(manifest["computer_name"], "POSTE-TEST")
        self.assertEqual(manifest["source_tournament_folder"], self.env.tournament_folder)
        self.assertEqual(manifest["source_poker_tournament_data"], self.env.poker_data_dir)
        self.assertEqual(
            sorted(manifest["tournament_files"]),
            ["Vendredi/tournoi9.tournoi", "dimanche/tournoi14.tournoi"],
        )
        self.assertTrue(manifest["poker_tournament_data_included"])

    def test_ne_modifie_jamais_les_fichiers_d_origine(self):
        before = self.env.snapshot()
        backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        after = self.env.snapshot()
        self.assertEqual(before, after)

    def test_destination_absente_leve_une_erreur_claire(self):
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.create_backup(
                os.path.join(self._tmp.name, "cle_usb_debranchee_inexistante"),
                tournament_folder=self.env.tournament_folder,
                poker_data_dir=self.env.poker_data_dir,
            )

    def test_aucun_tournoi_configure_sauvegarde_quand_meme_les_donnees_app(self):
        """tournament_folder=None ET rien de configuré (default_tournament_folder
        mocké à None, pour ne pas dépendre du vrai réglage "Dossier par
        défaut" de CETTE machine — voir default_tournament_folder) : pas
        d'erreur, juste 0 tournoi sauvegardé, les données
        ~/.poker_tournament restent sauvegardées."""
        with patch.object(backup_restore, "default_tournament_folder", return_value=None):
            result = backup_restore.create_backup(
                self.usb, tournament_folder=None, poker_data_dir=self.env.poker_data_dir,
            )
        self.assertEqual(result["tournament_file_count"], 0)
        self.assertTrue(result["poker_tournament_data_included"])

    def test_echec_pendant_la_copie_ne_laisse_pas_de_sauvegarde_partielle(self):
        """Simule une clé qui se débranche pendant la copie des données
        de l'application (2e étape) : le dossier de sauvegarde déjà
        créé (avec les .tournoi dedans) doit être nettoyé, pas laissé
        en état à moitié écrit."""
        with patch.object(
            backup_restore, "_copy_tree_file_by_file", side_effect=OSError("clé débranchée simulée"),
        ):
            with self.assertRaises(backup_restore.BackupError):
                backup_restore.create_backup(
                    self.usb, tournament_folder=self.env.tournament_folder,
                    poker_data_dir=self.env.poker_data_dir,
                )
        leftovers = [
            n for n in os.listdir(self.usb) if n.startswith(backup_restore.BACKUP_FOLDER_PREFIX)
        ]
        self.assertEqual(leftovers, [])

    def test_sauvegarde_inclut_toujours_open_windows_et_phone_pid_et_license(self):
        """La sauvegarde continue de tout copier sans exception (demande
        du 2026-09-07) : open_windows.json et phone_selected_pid.json
        sont bien présents dans la sauvegarde, au même titre que
        license.json — seule la RESTAURATION exclut les deux premiers
        (voir RestoreBackupTest)."""
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        data_dir = os.path.join(result["backup_dir"], "poker_tournament_data")
        with open(os.path.join(data_dir, "open_windows.json")) as f:
            self.assertEqual(f.read(), '{"/tmp/x.tournoi": {"pid": 111}}')
        with open(os.path.join(data_dir, "phone_selected_pid.json")) as f:
            self.assertEqual(f.read(), '{"pid": 111}')
        with open(os.path.join(data_dir, "license.json")) as f:
            self.assertEqual(f.read(), '{"machine_id": "ABC", "club_name": "CPC", "key": "XYZ"}')

    def test_deux_sauvegardes_la_meme_minute_ne_s_ecrasent_pas(self):
        r1 = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        r2 = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        self.assertNotEqual(r1["backup_dir"], r2["backup_dir"])
        self.assertTrue(os.path.isdir(r1["backup_dir"]))
        self.assertTrue(os.path.isdir(r2["backup_dir"]))


class TournamentFolderExtraneousFilesTest(unittest.TestCase):
    """Incident RÉEL du 2026-10-09 (HP, PokerTournament-TEST-12) :
    "Fichier manquant après la copie : primes_To170926.pdf" — le dossier
    de tournois contenait un PDF d'export (prix), jamais copié (seuls
    les .tournoi le sont, voir find_tournament_files), mais que l'ancien
    _verify_copied_tree(tournament_folder, ...) cherchait quand même
    dans la destination, en parcourant TOUT le dossier source sans
    filtre — asymétrie copie/vérification, corrigée en vérifiant
    désormais UNIQUEMENT les paires réellement copiées
    (tournament_copy_pairs). Généralisé ici à plusieurs types de
    fichiers annexes (PDF, CSV, fichiers techniques OS) — jamais un
    traitement particulier pour un seul nom de fichier."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_extraneous_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        # Fichiers annexes réels (PDF d'export, CSV répertoire, fichiers
        # techniques OS) — exactement le type de mélange déjà observé
        # dans l'usage réel du club, jamais copiés ni attendus dans la
        # sauvegarde.
        _write(os.path.join(self.env.tournament_folder, "dimanche", "primes_To170926.pdf"), "PDF PRIMES")
        _write(os.path.join(self.env.tournament_folder, "repertoire_joueurs.csv"), "nom;club\nJean;CPC")
        _write(os.path.join(self.env.tournament_folder, "Vendredi", "placement_sitngo.pdf"), "PDF PLACEMENT")
        _write(os.path.join(self.env.tournament_folder, ".DS_Store"), "FAUX FICHIER TECHNIQUE MAC")

    def test_fichiers_annexes_ne_bloquent_plus_la_sauvegarde(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="POSTE-TEST",
        )
        self.assertEqual(result["tournament_file_count"], 2)  # toujours les 2 .tournoi de _SyntheticEnv

    def test_les_tournois_restent_bien_copies_et_corrects(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        backed_up = os.path.join(result["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR, "dimanche", "tournoi14.tournoi")
        with open(backed_up) as f:
            self.assertEqual(f.read(), "DB tournoi 14")

    def test_fichiers_annexes_ne_sont_jamais_copies_dans_la_sauvegarde(self):
        """Comportement de COPIE inchangé (demande explicite : ne pas
        corriger en commençant à copier ce qui n'a rien à faire dans
        tournois/) — seule la VÉRIFICATION change."""
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        tournois_dst = os.path.join(result["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR)
        self.assertFalse(os.path.exists(os.path.join(tournois_dst, "dimanche", "primes_To170926.pdf")))
        self.assertFalse(os.path.exists(os.path.join(tournois_dst, "repertoire_joueurs.csv")))
        self.assertFalse(os.path.exists(os.path.join(tournois_dst, ".DS_Store")))

    def test_fichiers_annexes_restent_intacts_sur_le_disque_dorigine(self):
        """Point explicite : les données et fichiers annexes existants
        doivent rester intacts — jamais modifiés ni supprimés par la
        sauvegarde."""
        pdf_path = os.path.join(self.env.tournament_folder, "dimanche", "primes_To170926.pdf")
        csv_path = os.path.join(self.env.tournament_folder, "repertoire_joueurs.csv")
        backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        with open(pdf_path) as f:
            self.assertEqual(f.read(), "PDF PRIMES")
        with open(csv_path) as f:
            self.assertEqual(f.read(), "nom;club\nJean;CPC")

    def test_un_vrai_tournoi_manquant_reste_bien_detecte(self):
        """Non-régression centrale : la correction ne doit jamais
        masquer un VRAI fichier .tournoi manquant après la copie —
        simulé en supprimant la copie juste après coup."""
        orig_copy2 = backup_restore.shutil.copy2

        def _copy2_puis_supprime_un_tournoi(src, dst):
            orig_copy2(src, dst)
            if os.path.basename(dst) == "tournoi14.tournoi":
                os.remove(dst)

        with patch.object(backup_restore.shutil, "copy2", side_effect=_copy2_puis_supprime_un_tournoi):
            with self.assertRaises(backup_restore.BackupError) as ctx:
                backup_restore.create_backup(
                    self.usb, tournament_folder=self.env.tournament_folder,
                    poker_data_dir=self.env.poker_data_dir,
                )
        self.assertIn("tournoi14.tournoi", str(ctx.exception))

    def test_plusieurs_sauvegardes_et_restaurations_successives_avec_fichiers_annexes(self):
        """Point explicite : plusieurs sauvegardes/restaurations
        successives avec les fichiers annexes toujours présents."""
        restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        os.makedirs(restore_tournament_folder)
        for i in range(3):
            result = backup_restore.create_backup(
                self.usb, tournament_folder=self.env.tournament_folder,
                poker_data_dir=self.env.poker_data_dir,
            )
            self.assertEqual(result["tournament_file_count"], 2)
            restore_result = backup_restore.restore_backup(
                result["backup_dir"],
                tournament_folder=restore_tournament_folder,
                poker_data_dir=restore_poker_data_dir,
                pre_restore_root=os.path.join(self._tmp.name, f"pre_restore_{i}"),
                open_tournament_paths=[],
            )
            self.assertEqual(restore_result["restored_tournament_files"], 2)
        # Les fichiers annexes d'origine n'ont jamais bougé après 3 cycles.
        with open(os.path.join(self.env.tournament_folder, "repertoire_joueurs.csv")) as f:
            self.assertEqual(f.read(), "nom;club\nJean;CPC")


class MultiSourceBackupTest(unittest.TestCase):
    """Sélection EXPLICITE de fichiers (demande explicite du 2026-10-08,
    revue de sécurité "Purger Données" : "la sauvegarde lancée depuis
    Purger Données doit couvrir EXACTEMENT les fichiers sélectionnés,
    sans redécouverte indépendante dans le seul Dossier par défaut")
    — paramètre `tournament_files` de create_backup, avec regroupement
    par dossier d'origine réel (`tournament_source_folders`) pour
    éviter toute collision entre fichiers de même nom provenant de
    dossiers différents (ex. plusieurs "dimanche", un par source)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_multi_test_")
        self.addCleanup(self._tmp.cleanup)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)

    def test_un_seul_dossier_source_produit_un_manifeste_identique_a_lancien_schema(self):
        """Non-régression explicite : quand TOUS les fichiers de la
        sélection appartiennent au même dossier (cas le plus courant),
        le manifeste reste STRICTEMENT identique à celui du schéma
        historique — aucun champ "tournament_source_folders", chemins
        relatifs SANS préfixe."""
        folder = os.path.join(self._tmp.name, "club", "dimanche")
        p1 = os.path.join(folder, "To1.tournoi")
        p2 = os.path.join(folder, "To2.tournoi")
        _write(p1, "contenu 1")
        _write(p2, "contenu 2")
        result = backup_restore.create_backup(
            self.usb, tournament_files=[p1, p2],
            tournament_source_folders=[folder],
        )
        with open(os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)) as f:
            manifest = json.load(f)
        self.assertNotIn("tournament_source_folders", manifest)
        self.assertEqual(manifest["source_tournament_folder"], folder)
        self.assertEqual(sorted(manifest["tournament_files"]), ["To1.tournoi", "To2.tournoi"])
        self.assertEqual(result["tournament_file_count"], 2)

    def test_deux_fichiers_de_meme_nom_dans_des_dossiers_distincts_jamais_de_collision(self):
        """Le scénario exact signalé par l'utilisateur : dimanche_A et
        dimanche_B contiennent CHACUN un "To1.tournoi" — les deux
        doivent être sauvegardés séparément, avec leur contenu propre
        intact, jamais l'un écrasant l'autre."""
        folder_a = os.path.join(self._tmp.name, "poste1", "dimanche")
        folder_b = os.path.join(self._tmp.name, "poste2", "dimanche")
        p_a = os.path.join(folder_a, "To1.tournoi")
        p_b = os.path.join(folder_b, "To1.tournoi")
        _write(p_a, "CONTENU DU POSTE 1")
        _write(p_b, "CONTENU DU POSTE 2 (different)")
        result = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b],
            tournament_source_folders=[folder_a, folder_b],
        )
        self.assertEqual(result["tournament_file_count"], 2)
        with open(os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)) as f:
            manifest = json.load(f)
        self.assertIsNone(manifest["source_tournament_folder"])
        self.assertEqual(manifest["tournament_source_folders"], [folder_a, folder_b])
        self.assertEqual(
            sorted(manifest["tournament_files"]),
            sorted(["0_dimanche/To1.tournoi", "1_dimanche/To1.tournoi"]),
        )
        backed_a = os.path.join(result["backup_dir"], "tournois", "0_dimanche", "To1.tournoi")
        backed_b = os.path.join(result["backup_dir"], "tournois", "1_dimanche", "To1.tournoi")
        with open(backed_a) as f:
            self.assertEqual(f.read(), "CONTENU DU POSTE 1")
        with open(backed_b) as f:
            self.assertEqual(f.read(), "CONTENU DU POSTE 2 (different)")
        # Les empreintes SHA-256 doivent elles aussi être distinctes et
        # correctes pour chaque copie (jamais mélangées entre les deux).
        self.assertNotEqual(
            manifest["tournament_file_hashes"]["0_dimanche/To1.tournoi"],
            manifest["tournament_file_hashes"]["1_dimanche/To1.tournoi"],
        )
        self.assertEqual(
            manifest["tournament_file_hashes"]["0_dimanche/To1.tournoi"],
            backup_restore.sha256_of_file(backed_a),
        )

    def test_dossier_ajoute_manuellement_imbrique_dans_le_dossier_par_defaut(self):
        """Un dossier candidat SOUS un autre (ex. ajout manuel d'un
        sous-dossier du dossier par défaut) doit être reconnu comme le
        propriétaire le plus SPÉCIFIQUE — jamais le plus large."""
        base = os.path.join(self._tmp.name, "club")
        nested = os.path.join(base, "dimanche")
        p = os.path.join(nested, "sous_dossier", "To1.tournoi")
        _write(p, "contenu")
        result = backup_restore.create_backup(
            self.usb, tournament_files=[p], tournament_source_folders=[base, nested],
        )
        with open(os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)) as f:
            manifest = json.load(f)
        # Un seul dossier RÉELLEMENT utilisé (nested, le plus spécifique
        # qui contient p) -> schéma à un seul dossier, chemin relatif à
        # NESTED, pas à base.
        self.assertEqual(manifest["source_tournament_folder"], nested)
        self.assertEqual(manifest["tournament_files"], ["sous_dossier/To1.tournoi"])

    def test_sans_dossiers_candidats_fournis_repli_sur_un_seul_ancetre_commun(self):
        """Sans tournament_source_folders (appelant qui ne les connaît
        pas) : repli sur UN SEUL groupe (l'ancêtre commun) — jamais une
        exception, jamais un groupe par sous-dossier."""
        folder_a = os.path.join(self._tmp.name, "club", "dimanche")
        folder_b = os.path.join(self._tmp.name, "club", "lundi")
        p_a = os.path.join(folder_a, "To1.tournoi")
        p_b = os.path.join(folder_b, "To2.tournoi")
        _write(p_a, "a")
        _write(p_b, "b")
        result = backup_restore.create_backup(self.usb, tournament_files=[p_a, p_b])
        with open(os.path.join(result["backup_dir"], backup_restore.MANIFEST_FILENAME)) as f:
            manifest = json.load(f)
        self.assertNotIn("tournament_source_folders", manifest)
        self.assertEqual(manifest["source_tournament_folder"], os.path.join(self._tmp.name, "club"))
        self.assertEqual(
            sorted(manifest["tournament_files"]),
            sorted(["dimanche/To1.tournoi", "lundi/To2.tournoi"]),
        )

    def test_selection_explicite_vide_sauvegarde_zero_tournoi_sans_erreur(self):
        result = backup_restore.create_backup(self.usb, tournament_files=[])
        self.assertEqual(result["tournament_file_count"], 0)

    def test_fichier_disparu_pendant_la_copie_annule_toute_la_sauvegarde(self):
        """Une sélection explicite qui référence un fichier disparu
        (supprimé entre le scan et la sauvegarde) doit échouer
        intégralement — jamais une sauvegarde partielle silencieuse."""
        folder = os.path.join(self._tmp.name, "club", "dimanche")
        p1 = os.path.join(folder, "To1.tournoi")
        _write(p1, "contenu")
        p_missing = os.path.join(folder, "introuvable.tournoi")
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.create_backup(
                self.usb, tournament_files=[p1, p_missing], tournament_source_folders=[folder],
            )
        leftovers = [n for n in os.listdir(self.usb) if n.startswith(backup_restore.BACKUP_FOLDER_PREFIX)]
        self.assertEqual(leftovers, [])

    def test_restauration_dune_sauvegarde_multi_dossiers_preserve_les_deux_fichiers_de_meme_nom(self):
        """Demande explicite du 2026-10-08, point 7 : test de
        restauration avec deux fichiers de même nom provenant de
        dossiers distincts — UNIQUEMENT des dossiers temporaires, jamais
        les vraies données.

        Mis à jour le 2026-10-13 (correctif "dossiers techniques
        4_mardi après restauration") : restore_backup() ne recrée plus
        le préfixe technique "<index>_<nom>/" à la destination — voir
        backup_restore.resolve_multi_folder_destination_labels. Les deux
        dossiers sources partagent ICI le même nom de base ("dimanche") :
        c'est exactement le cas de collision que cette fonction doit
        distinguer — le premier (index 0) reçoit le libellé simple
        "dimanche", le second (index 1) le libellé numéroté
        "dimanche (2)", jamais fusionnés sous un même nom."""
        folder_a = os.path.join(self._tmp.name, "poste1", "dimanche")
        folder_b = os.path.join(self._tmp.name, "poste2", "dimanche")
        p_a = os.path.join(folder_a, "To1.tournoi")
        p_b = os.path.join(folder_b, "To1.tournoi")
        _write(p_a, "CONTENU POSTE 1")
        _write(p_b, "CONTENU POSTE 2")
        backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )["backup_dir"]

        restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")
        os.makedirs(restore_tournament_folder)

        result = backup_restore.restore_backup(
            backup_dir,
            tournament_folder=restore_tournament_folder,
            poker_data_dir=restore_poker_data_dir,
            pre_restore_root=pre_restore_root,
            open_tournament_paths=[],
        )
        self.assertEqual(result["restored_tournament_files"], 2)

        restored_a = os.path.join(restore_tournament_folder, "dimanche", "To1.tournoi")
        restored_b = os.path.join(restore_tournament_folder, "dimanche (2)", "To1.tournoi")
        self.assertTrue(os.path.isfile(restored_a))
        self.assertTrue(os.path.isfile(restored_b))
        with open(restored_a) as f:
            self.assertEqual(f.read(), "CONTENU POSTE 1")
        with open(restored_b) as f:
            self.assertEqual(f.read(), "CONTENU POSTE 2")
        # Aucune collision : les deux fichiers restaurés restent DEUX
        # fichiers distincts sur le disque, jamais l'un écrasant l'autre.
        self.assertNotEqual(os.path.abspath(restored_a), os.path.abspath(restored_b))
        # Aucun dossier technique "<index>_<nom>" ne doit exister.
        self.assertFalse(os.path.isdir(os.path.join(restore_tournament_folder, "0_dimanche")))
        self.assertFalse(os.path.isdir(os.path.join(restore_tournament_folder, "1_dimanche")))

    def test_ne_modifie_jamais_les_fichiers_source_en_selection_explicite(self):
        folder_a = os.path.join(self._tmp.name, "poste1", "dimanche")
        folder_b = os.path.join(self._tmp.name, "poste2", "dimanche")
        p_a = os.path.join(folder_a, "To1.tournoi")
        p_b = os.path.join(folder_b, "To1.tournoi")
        _write(p_a, "A")
        _write(p_b, "B")
        backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )
        with open(p_a) as f:
            self.assertEqual(f.read(), "A")
        with open(p_b) as f:
            self.assertEqual(f.read(), "B")


class ResolveMultiFolderDestinationLabelsUnitTest(unittest.TestCase):
    """backup_restore.resolve_multi_folder_destination_labels — fonction
    pure, testée isolément (demande du 2026-10-13, correctif "dossiers
    techniques 4_mardi après restauration")."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="resolve_labels_test_")
        self.addCleanup(self._tmp.cleanup)
        self.destination = os.path.join(self._tmp.name, "destination")
        os.makedirs(self.destination)

    def test_libelles_tous_distincts_restent_simples(self):
        folders = [
            os.path.join(self._tmp.name, "a", "lundi"),
            os.path.join(self._tmp.name, "b", "mardi"),
            os.path.join(self._tmp.name, "c", "vendredi"),
        ]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, self.destination)
        self.assertEqual(resolved, {0: "lundi", 1: "mardi", 2: "vendredi"})

    def test_deux_dossiers_meme_nom_second_numerote(self):
        folders = [
            os.path.join(self._tmp.name, "poste1", "mardi"),
            os.path.join(self._tmp.name, "poste2", "mardi"),
        ]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, self.destination)
        self.assertEqual(resolved, {0: "mardi", 1: "mardi (2)"})

    def test_trois_dossiers_meme_nom_numerotation_sequentielle(self):
        folders = [
            os.path.join(self._tmp.name, "a", "mardi"),
            os.path.join(self._tmp.name, "b", "mardi"),
            os.path.join(self._tmp.name, "c", "mardi"),
        ]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, self.destination)
        self.assertEqual(resolved, {0: "mardi", 1: "mardi (2)", 2: "mardi (3)"})

    def test_melange_collisions_et_noms_uniques(self):
        folders = [
            os.path.join(self._tmp.name, "a", "mardi"),
            os.path.join(self._tmp.name, "b", "lundi"),
            os.path.join(self._tmp.name, "c", "mardi"),
        ]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, self.destination)
        self.assertEqual(resolved, {0: "mardi", 1: "lundi", 2: "mardi (2)"})

    def test_evite_un_dossier_deja_present_sans_rapport_avec_la_restauration(self):
        """Demande explicite du 2026-10-13, point 1 : vérifier aussi les
        collisions avec les dossiers DÉJÀ PRÉSENTS à la destination, pas
        seulement entre dossiers sources de la sauvegarde. "mardi (2)"
        existe déjà (sans rapport avec cette restauration) : le second
        groupe "mardi" ne doit JAMAIS s'y fondre, il doit sauter à
        "mardi (3)"."""
        os.makedirs(os.path.join(self.destination, "mardi (2)"))
        folders = [
            os.path.join(self._tmp.name, "poste1", "mardi"),
            os.path.join(self._tmp.name, "poste2", "mardi"),
        ]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, self.destination)
        self.assertEqual(resolved, {0: "mardi", 1: "mardi (3)"})

    def test_libelle_simple_peut_correspondre_a_un_dossier_deja_present(self):
        """Le cas NORMAL et VOULU : le dossier "mardi" existe déjà (usage
        hebdomadaire habituel) — la fusion est intentionnelle, jamais
        évitée pour le libellé simple (non numéroté)."""
        os.makedirs(os.path.join(self.destination, "mardi"))
        folders = [os.path.join(self._tmp.name, "poste1", "mardi")]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, self.destination)
        self.assertEqual(resolved, {0: "mardi"})

    def test_dossier_destination_inexistant_ne_leve_jamais(self):
        """`tournament_folder` pas encore créé (cas normal avant la
        validation de restore_backup) : jamais d'exception, simplement
        aucune collision connue à éviter."""
        missing = os.path.join(self._tmp.name, "n_existe_pas_encore")
        folders = [
            os.path.join(self._tmp.name, "poste1", "mardi"),
            os.path.join(self._tmp.name, "poste2", "mardi"),
        ]
        resolved = backup_restore.resolve_multi_folder_destination_labels(folders, missing)
        self.assertEqual(resolved, {0: "mardi", 1: "mardi (2)"})


class RestoreMultiFolderReadableLabelsTest(unittest.TestCase):
    """restore_backup() — destination sous des noms de dossiers LISIBLES
    pour une sauvegarde multi-dossiers (correctif du 2026-10-13, suite à
    l'apparition de dossiers techniques comme "4_mardi" après une
    restauration réelle sur un poste Windows). Couvre la compatibilité
    ascendante, le respect du Dossier par défaut, la non-perte de
    tournois homonymes, et les collisions avec le contenu DÉJÀ PRÉSENT à
    la destination (pas seulement entre dossiers sources de la
    sauvegarde)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="restore_readable_labels_test_")
        self.addCleanup(self._tmp.cleanup)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        os.makedirs(self.restore_tournament_folder)
        self.restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        self.pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")

    def _restore(self):
        return backup_restore.restore_backup(
            self._backup_dir,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )

    # -- 1. Compatibilité ascendante ---------------------------------------

    def test_ancien_manifeste_sans_tournament_source_folders_comportement_inchange(self):
        """Simule une ANCIENNE sauvegarde (créée avant ce correctif, et
        même avant la fonctionnalité multi-dossiers elle-même) : aucun
        champ "tournament_source_folders" dans le manifeste, chemins
        relatifs SANS préfixe — restore_backup doit se comporter EXACTE-
        MENT comme avant, sans jamais consulter resolve_multi_folder_
        destination_labels."""
        folder = os.path.join(self._tmp.name, "club", "mardi")
        p1 = os.path.join(folder, "To1.tournoi")
        _write(p1, "contenu historique")
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_folder=folder,
        )["backup_dir"]
        # Confirme l'hypothèse : pas de champ multi-dossiers pour une
        # sauvegarde à un seul dossier (schéma historique).
        manifest = backup_restore.validate_backup_folder(self._backup_dir)
        self.assertIsNone(manifest.get("tournament_source_folders"))

        result = self._restore()

        self.assertEqual(result["restored_tournament_files"], 1)
        restored = os.path.join(self.restore_tournament_folder, "To1.tournoi")
        self.assertTrue(os.path.isfile(restored))
        with open(restored) as f:
            self.assertEqual(f.read(), "contenu historique")

    # -- 2. Cas courant : noms tous distincts -------------------------------

    def test_sauvegarde_multi_dossiers_noms_distincts_aucun_prefixe_technique(self):
        folder_lundi = os.path.join(self._tmp.name, "club", "lundi")
        folder_mardi = os.path.join(self._tmp.name, "club", "mardi")
        folder_vendredi = os.path.join(self._tmp.name, "club", "vendredi")
        p1 = os.path.join(folder_lundi, "To1.tournoi")
        p2 = os.path.join(folder_mardi, "To2.tournoi")
        p3 = os.path.join(folder_vendredi, "To3.tournoi")
        _write(p1, "L")
        _write(p2, "M")
        _write(p3, "V")
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=[p1, p2, p3],
            tournament_source_folders=[folder_lundi, folder_mardi, folder_vendredi],
        )["backup_dir"]

        result = self._restore()
        self.assertEqual(result["restored_tournament_files"], 3)

        self.assertTrue(os.path.isfile(os.path.join(self.restore_tournament_folder, "lundi", "To1.tournoi")))
        self.assertTrue(os.path.isfile(os.path.join(self.restore_tournament_folder, "mardi", "To2.tournoi")))
        self.assertTrue(os.path.isfile(os.path.join(self.restore_tournament_folder, "vendredi", "To3.tournoi")))
        # Aucun dossier technique "<chiffre>_<nom>" — LE bug signalé.
        for name in os.listdir(self.restore_tournament_folder):
            self.assertNotRegex(name, r"^\d+_")

    # -- 3. Intégrité en aller-retour ---------------------------------------

    def test_aller_retour_integrite_complete_aucun_fichier_perdu(self):
        folder_a = os.path.join(self._tmp.name, "posteA", "mardi")
        folder_b = os.path.join(self._tmp.name, "posteB", "mardi")  # même nom, dossier distinct
        folder_c = os.path.join(self._tmp.name, "posteC", "jeudi")
        sources = {
            os.path.join(folder_a, "To1.tournoi"): "contenu A1",
            os.path.join(folder_a, "To2.tournoi"): "contenu A2",
            os.path.join(folder_b, "To1.tournoi"): "contenu B1",  # même nom que A1 !
            os.path.join(folder_c, "To9.tournoi"): "contenu C9",
        }
        for path, content in sources.items():
            _write(path, content)
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=list(sources.keys()),
            tournament_source_folders=[folder_a, folder_b, folder_c],
        )["backup_dir"]

        result = self._restore()
        self.assertEqual(result["restored_tournament_files"], 4)

        # Chaque contenu d'origine doit se retrouver QUELQUE PART sous la
        # destination — aucun perdu, aucun écrasé par un AUTRE tournoi.
        found_contents = set()
        for root, _dirs, files in os.walk(self.restore_tournament_folder):
            for name in files:
                with open(os.path.join(root, name)) as f:
                    found_contents.add(f.read())
        self.assertEqual(found_contents, set(sources.values()))

    # -- 4. Dossier par défaut respecté --------------------------------------

    def test_racine_de_destination_est_toujours_le_dossier_par_defaut_configure(self):
        folder_a = os.path.join(self._tmp.name, "machine_source_1", "chemin", "tres", "different", "mardi")
        folder_b = os.path.join(self._tmp.name, "autre_machine", "ailleurs", "mardi")
        p_a = os.path.join(folder_a, "To1.tournoi")
        p_b = os.path.join(folder_b, "To2.tournoi")
        _write(p_a, "A")
        _write(p_b, "B")
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )["backup_dir"]

        self._restore()

        # Tout doit être sous self.restore_tournament_folder — jamais sous
        # un chemin dérivé des dossiers sources d'origine.
        for root, _dirs, files in os.walk(self.restore_tournament_folder):
            for name in files:
                full = os.path.join(root, name)
                self.assertTrue(
                    os.path.commonpath([full, self.restore_tournament_folder])
                    == os.path.normpath(self.restore_tournament_folder)
                )

    # -- 5. Collision avec un dossier EXISTANT sans rapport -----------------

    def test_ne_fusionne_pas_avec_un_dossier_existant_sans_rapport(self):
        """Demande explicite du 2026-10-13, point 1. Avant la
        restauration, "mardi (2)" existe déjà à la destination, avec un
        fichier SANS rapport avec cette sauvegarde. Les deux groupes
        sources "mardi" de la sauvegarde ne doivent jamais s'y fondre —
        le second doit sauter à "mardi (3)", et le contenu déjà présent
        dans "mardi (2)" doit rester intact."""
        unrelated = os.path.join(self.restore_tournament_folder, "mardi (2)", "ToInconnu.tournoi")
        _write(unrelated, "contenu sans rapport, ne doit jamais bouger")

        folder_a = os.path.join(self._tmp.name, "posteA", "mardi")
        folder_b = os.path.join(self._tmp.name, "posteB", "mardi")
        p_a = os.path.join(folder_a, "To1.tournoi")
        p_b = os.path.join(folder_b, "To1.tournoi")
        _write(p_a, "contenu A")
        _write(p_b, "contenu B")
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=[p_a, p_b], tournament_source_folders=[folder_a, folder_b],
        )["backup_dir"]

        self._restore()

        # Le fichier sans rapport est TOUJOURS là, inchangé.
        with open(unrelated) as f:
            self.assertEqual(f.read(), "contenu sans rapport, ne doit jamais bouger")
        # Les deux groupes de la sauvegarde sont allés ailleurs.
        self.assertTrue(os.path.isfile(os.path.join(self.restore_tournament_folder, "mardi", "To1.tournoi")))
        self.assertTrue(os.path.isfile(os.path.join(self.restore_tournament_folder, "mardi (3)", "To1.tournoi")))

    # -- 6. Fusion intentionnelle avec un dossier existant du même usage ----

    def test_fusionne_avec_le_dossier_existant_du_meme_nom_simple(self):
        """Cas normal et voulu : "mardi" existe déjà (usage hebdomadaire
        habituel du club) — la restauration y AJOUTE les fichiers de la
        sauvegarde, sans toucher à ce qui y était déjà (noms différents).
        Deux dossiers sources distincts ("mardi" et "jeudi") pour que
        cette sauvegarde soit bien une sauvegarde MULTI-dossiers (sinon
        un seul groupe retombe sur le schéma historique à un seul
        dossier, sans le moindre sous-dossier — voir create_backup,
        len(groups) <= 1) et exerce réellement resolve_multi_folder_
        destination_labels, pas seulement le chemin de compatibilité."""
        existing = os.path.join(self.restore_tournament_folder, "mardi", "ToDejaLa.tournoi")
        _write(existing, "déjà présent avant la restauration")

        folder_mardi = os.path.join(self._tmp.name, "club", "mardi")
        folder_jeudi = os.path.join(self._tmp.name, "club", "jeudi")
        p1 = os.path.join(folder_mardi, "ToNouveau.tournoi")
        p2 = os.path.join(folder_jeudi, "ToAutre.tournoi")
        _write(p1, "nouveau, depuis la sauvegarde")
        _write(p2, "autre dossier, sans rapport avec mardi")
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=[p1, p2], tournament_source_folders=[folder_mardi, folder_jeudi],
        )["backup_dir"]

        self._restore()

        with open(existing) as f:
            self.assertEqual(f.read(), "déjà présent avant la restauration")
        restored = os.path.join(self.restore_tournament_folder, "mardi", "ToNouveau.tournoi")
        with open(restored) as f:
            self.assertEqual(f.read(), "nouveau, depuis la sauvegarde")

    # -- 7. Écrasement protégé par la sauvegarde de sécurité -----------------

    def test_ecrasement_dun_tournoi_existant_protege_par_sauvegarde_de_securite(self):
        """"Aucun tournoi existant ne doit être écrasé SILENCIEUSEMENT" :
        un .tournoi de même nom dans le dossier final EST remplacé (
        comportement de restauration intentionnel, inchangé), mais son
        contenu AVANT remplacement doit être retrouvable dans la
        sauvegarde de sécurité automatique. Deux dossiers sources
        distincts, même raison que le test précédent (exercer réellement
        la résolution multi-dossiers, pas le repli historique)."""
        existing = os.path.join(self.restore_tournament_folder, "mardi", "To1.tournoi")
        _write(existing, "ANCIEN CONTENU AVANT RESTAURATION")

        folder_mardi = os.path.join(self._tmp.name, "club", "mardi")
        folder_jeudi = os.path.join(self._tmp.name, "club", "jeudi")
        p1 = os.path.join(folder_mardi, "To1.tournoi")
        p2 = os.path.join(folder_jeudi, "ToAutre.tournoi")
        _write(p1, "NOUVEAU CONTENU DE LA SAUVEGARDE")
        _write(p2, "autre dossier, sans rapport")
        self._backup_dir = backup_restore.create_backup(
            self.usb, tournament_files=[p1, p2], tournament_source_folders=[folder_mardi, folder_jeudi],
        )["backup_dir"]

        result = self._restore()

        # Remplacement bien effectué (comportement voulu, inchangé).
        with open(existing) as f:
            self.assertEqual(f.read(), "NOUVEAU CONTENU DE LA SAUVEGARDE")
        # L'ancien contenu reste récupérable dans la sauvegarde de sécurité.
        safety_dir = result["safety_backup_dir"]
        found_old_content = False
        for root, _dirs, files in os.walk(safety_dir):
            for name in files:
                if name == "To1.tournoi":
                    with open(os.path.join(root, name)) as f:
                        if f.read() == "ANCIEN CONTENU AVANT RESTAURATION":
                            found_old_content = True
        self.assertTrue(
            found_old_content,
            "L'ancien contenu écrasé doit être retrouvable dans la sauvegarde de sécurité.",
        )


class ValidateBackupFolderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)

    def test_dossier_valide_renvoie_le_manifeste(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        manifest = backup_restore.validate_backup_folder(result["backup_dir"])
        self.assertEqual(len(manifest["tournament_files"]), 2)

    def test_dossier_sans_manifeste_refuse(self):
        empty_dir = os.path.join(self._tmp.name, "pas_une_sauvegarde")
        os.makedirs(empty_dir)
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.validate_backup_folder(empty_dir)

    def test_manifeste_corrompu_refuse(self):
        bad_dir = os.path.join(self._tmp.name, "sauvegarde_corrompue")
        os.makedirs(bad_dir)
        _write(os.path.join(bad_dir, backup_restore.MANIFEST_FILENAME), "{ceci n'est pas du JSON")
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.validate_backup_folder(bad_dir)

    def test_manifeste_incomplet_refuse(self):
        bad_dir = os.path.join(self._tmp.name, "sauvegarde_incomplete")
        os.makedirs(bad_dir)
        _write(os.path.join(bad_dir, backup_restore.MANIFEST_FILENAME), json.dumps({"created_at": "x"}))
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.validate_backup_folder(bad_dir)


class RestoreBackupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.backup = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="POSTE-TEST",
        )["backup_dir"]

        # "Nouvelle machine" cible de la restauration : dossiers séparés
        # de ceux utilisés pour créer la sauvegarde ci-dessus, pour bien
        # distinguer source de sauvegarde et cible de restauration.
        self.restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        self.restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        self.pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")

    def test_2_restauration_reproduit_correctement_les_donnees(self):
        # Depuis le 2026-10-10 : le dossier de destination doit déjà
        # exister (refusé sinon, jamais créé silencieusement) — voir
        # TournamentFolderValidationTest pour ce comportement lui-même.
        os.makedirs(self.restore_tournament_folder)
        result = backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        self.assertEqual(result["restored_tournament_files"], 2)
        self.assertTrue(result["restored_data"])
        with open(os.path.join(self.restore_tournament_folder, "dimanche", "tournoi14.tournoi")) as f:
            self.assertEqual(f.read(), "DB tournoi 14")
        with open(os.path.join(self.restore_poker_data_dir, "export_prefs.json")) as f:
            self.assertEqual(f.read(), '{"club_name": "CPC"}')
        with open(os.path.join(self.restore_poker_data_dir, "photos", "jean.jpg")) as f:
            self.assertEqual(f.read(), "FAKEJPEGBYTES")

    def test_3_sauvegarde_pre_restauration_est_bien_creee(self):
        # Données déjà présentes sur la "machine cible" avant restauration.
        _write(os.path.join(self.restore_tournament_folder, "dimanche", "ancien.tournoi"), "ANCIEN")
        _write(os.path.join(self.restore_poker_data_dir, "roster.json"), '{"players": ["Ancien"]}')

        result = backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        safety_dir = result["safety_backup_dir"]
        self.assertTrue(os.path.isdir(safety_dir))
        # La sauvegarde de sécurité contient bien l'ANCIEN état (celui
        # qui va être remplacé), pas le nouveau.
        with open(os.path.join(safety_dir, "tournois", "dimanche", "ancien.tournoi")) as f:
            self.assertEqual(f.read(), "ANCIEN")
        with open(os.path.join(safety_dir, "poker_tournament_data", "roster.json")) as f:
            self.assertEqual(f.read(), '{"players": ["Ancien"]}')

    def test_4_refuse_si_un_tournoi_est_ouvert(self):
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.restore_backup(
                self.backup,
                tournament_folder=self.restore_tournament_folder,
                poker_data_dir=self.restore_poker_data_dir,
                pre_restore_root=self.pre_restore_root,
                open_tournament_paths=["/tmp/un_tournoi_ouvert.tournoi"],
            )
        # Rien ne doit avoir été créé/modifié : ni sauvegarde de
        # sécurité, ni dossier cible.
        self.assertFalse(os.path.exists(self.pre_restore_root))
        self.assertFalse(os.path.exists(self.restore_tournament_folder))
        self.assertFalse(os.path.exists(self.restore_poker_data_dir))

    def test_refuse_avec_open_windows_reel_mocke(self):
        """Sans open_tournament_paths explicite : utilise bien
        open_windows.list_open_paths() par défaut."""
        with patch.object(backup_restore.open_windows, "list_open_paths", return_value=["/tmp/x.tournoi"]):
            with self.assertRaises(backup_restore.BackupError):
                backup_restore.restore_backup(
                    self.backup,
                    tournament_folder=self.restore_tournament_folder,
                    poker_data_dir=self.restore_poker_data_dir,
                    pre_restore_root=self.pre_restore_root,
                )

    def test_dossier_de_sauvegarde_invalide_refuse_sans_effet(self):
        empty_dir = os.path.join(self._tmp.name, "pas_une_sauvegarde")
        os.makedirs(empty_dir)
        with self.assertRaises(backup_restore.BackupError):
            backup_restore.restore_backup(
                empty_dir,
                tournament_folder=self.restore_tournament_folder,
                poker_data_dir=self.restore_poker_data_dir,
                pre_restore_root=self.pre_restore_root,
                open_tournament_paths=[],
            )
        self.assertFalse(os.path.exists(self.pre_restore_root))

    def test_restauration_ne_supprime_jamais_un_fichier_deja_present_et_absent_de_la_sauvegarde(self):
        """Copie fichier par fichier, jamais un rmtree préalable de la
        cible : un fichier présent seulement dans la cible (pas dans la
        sauvegarde) doit survivre à la restauration."""
        _write(os.path.join(self.restore_poker_data_dir, "un_fichier_local_non_sauvegarde.txt"), "reste")
        os.makedirs(self.restore_tournament_folder)
        backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        self.assertTrue(os.path.isfile(
            os.path.join(self.restore_poker_data_dir, "un_fichier_local_non_sauvegarde.txt")
        ))


class TournamentFolderValidationTest(unittest.TestCase):
    """Demande explicite du 2026-10-10 : la restauration doit utiliser
    EXCLUSIVEMENT le « Dossier par défaut » réellement configuré sur LA
    MACHINE DE DESTINATION (tournament_prefs.load_last_settings, jamais
    default_tournament_folder() ni le chemin de la machine source), le
    refuser explicitement s'il est vide/inexistant/non accessible en
    écriture, et ne jamais rien créer/modifier avant que ces validations
    aient toutes réussi. Sauvegarde créée avec un dossier de tournoi
    "source" distinct (imite une autre machine), tournament_folder=None
    passé à restore_backup à chaque fois pour bien exercer le chemin de
    résolution via tournament_prefs (jamais explicitement injecté)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.backup = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="MAC-SOURCE",
        )["backup_dir"]
        self.restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        self.pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")

    def _restore_with_configured_folder(self, configured_folder):
        with patch.object(
            backup_restore.tournament_prefs, "load_last_settings",
            return_value={"tournament_day_folder": configured_folder},
        ):
            return backup_restore.restore_backup(
                self.backup,
                tournament_folder=None,
                poker_data_dir=self.restore_poker_data_dir,
                pre_restore_root=self.pre_restore_root,
                open_tournament_paths=[],
            )

    def test_refuse_si_le_dossier_par_defaut_est_vide(self):
        with self.assertRaises(backup_restore.BackupError) as ctx:
            self._restore_with_configured_folder("")
        self.assertIn("Dossier par défaut", str(ctx.exception))
        self.assertIn("Paramètres", str(ctx.exception))
        # Validation AVANT toute écriture : aucune sauvegarde de
        # sécurité ni dossier de destination ne doit avoir été créé.
        self.assertFalse(os.path.exists(self.pre_restore_root))
        self.assertFalse(os.path.exists(self.restore_poker_data_dir))

    def test_refuse_si_la_cle_tournament_day_folder_est_absente(self):
        """Aucune valeur n'a jamais été enregistrée du tout (poste
        tout neuf) : même refus explicite que pour une chaîne vide."""
        with patch.object(
            backup_restore.tournament_prefs, "load_last_settings", return_value={},
        ):
            with self.assertRaises(backup_restore.BackupError) as ctx:
                backup_restore.restore_backup(
                    self.backup,
                    tournament_folder=None,
                    poker_data_dir=self.restore_poker_data_dir,
                    pre_restore_root=self.pre_restore_root,
                    open_tournament_paths=[],
                )
        self.assertIn("Dossier par défaut", str(ctx.exception))
        self.assertFalse(os.path.exists(self.pre_restore_root))

    def test_refuse_si_le_dossier_configure_nexiste_pas(self):
        chemin_inexistant = os.path.join(self._tmp.name, "nexiste_pas_du_tout")
        with self.assertRaises(backup_restore.BackupError) as ctx:
            self._restore_with_configured_folder(chemin_inexistant)
        self.assertIn(chemin_inexistant, str(ctx.exception))
        self.assertFalse(os.path.exists(self.pre_restore_root))
        # Le dossier configuré lui-même ne doit jamais être créé
        # silencieusement par la restauration.
        self.assertFalse(os.path.exists(chemin_inexistant))

    def test_refuse_si_le_dossier_configure_nest_pas_accessible_en_ecriture(self):
        non_writable = os.path.join(self._tmp.name, "dossier_lecture_seule")
        os.makedirs(non_writable)
        os.chmod(non_writable, 0o555)
        self.addCleanup(os.chmod, non_writable, 0o755)
        if os.access(non_writable, os.W_OK):
            self.skipTest("os.access ne reflète pas chmod sur ce système (ex. exécuté en root)")
        with self.assertRaises(backup_restore.BackupError) as ctx:
            self._restore_with_configured_folder(non_writable)
        self.assertIn("écriture", str(ctx.exception))
        self.assertFalse(os.path.exists(self.pre_restore_root))

    def test_la_validation_du_dossier_decriture_ne_cree_ni_ne_modifie_aucun_fichier(self):
        """Précaution explicite du 2026-10-10 : la vérification
        d'accessibilité en écriture elle-même (os.path.isdir/os.access,
        AVANT la moindre copie réelle) ne doit créer ni modifier aucun
        fichier dans le dossier configuré. Isolé de la copie réelle qui
        suit en faisant échouer l'étape SUIVANTE (sauvegarde de
        sécurité) : si la validation elle-même avait écrit quoi que ce
        soit (ex. un fichier-sonde), ce serait visible ici, avant même
        que l'exception ne se propage."""
        configured = os.path.join(self._tmp.name, "dossier_configure")
        os.makedirs(configured)
        avant = sorted(os.listdir(configured))
        with patch.object(
            backup_restore, "create_backup", side_effect=RuntimeError("échec simulé de la sauvegarde de sécurité"),
        ):
            with self.assertRaises(RuntimeError):
                self._restore_with_configured_folder(configured)
        apres = sorted(os.listdir(configured))
        self.assertEqual(avant, apres)

    def test_dossier_valide_la_restauration_reussit(self):
        """Confirmation positive : un « Dossier par défaut » valide
        (existant, accessible en écriture) ne bloque jamais une
        restauration légitime."""
        configured = os.path.join(self._tmp.name, "dossier_configure_valide")
        os.makedirs(configured)
        result = self._restore_with_configured_folder(configured)
        self.assertEqual(result["restored_tournament_files"], 2)
        self.assertTrue(os.path.isfile(os.path.join(configured, "dimanche", "tournoi14.tournoi")))

    def test_ne_reutilise_jamais_le_chemin_de_la_machine_source(self):
        """La sauvegarde a été créée depuis self.env.tournament_folder
        (simule la machine Mac source) : la restauration doit utiliser
        UNIQUEMENT le dossier configuré localement, jamais ce chemin
        source, même si ce dernier existe toujours sur cette machine."""
        manifest = backup_restore.validate_backup_folder(self.backup)
        self.assertEqual(manifest["source_tournament_folder"], self.env.tournament_folder)
        configured = os.path.join(self._tmp.name, "dossier_configure_different_du_source")
        os.makedirs(configured)
        self._restore_with_configured_folder(configured)
        # Les fichiers sont bien arrivés dans le dossier CONFIGURÉ...
        self.assertTrue(os.path.isfile(os.path.join(configured, "dimanche", "tournoi14.tournoi")))
        # ...et le dossier source (toujours présent) n'a reçu aucune
        # écriture de la restauration (il contient encore EXACTEMENT
        # ce qu'il avait avant, rien de plus).
        with open(os.path.join(self.env.tournament_folder, "dimanche", "tournoi14.tournoi")) as f:
            self.assertEqual(f.read(), "DB tournoi 14")


class TournamentFolderOverwriteBehaviorTest(unittest.TestCase):
    """Précaution explicite du 2026-10-10 : un fichier .tournoi de même
    nom relatif déjà présent dans le dossier de destination doit être
    ÉCRASÉ par la restauration (comportement de restauration voulu,
    jamais un écrasement "non prévu") — mais récupérable via la
    sauvegarde de sécurité pré-restauration existante, jamais perdu."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.backup = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )["backup_dir"]
        self.restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        self.restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        self.pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")
        os.makedirs(self.restore_tournament_folder)
        # Fichier de même nom relatif que celui de la sauvegarde
        # ("dimanche/tournoi14.tournoi"), contenu DIFFÉRENT.
        _write(
            os.path.join(self.restore_tournament_folder, "dimanche", "tournoi14.tournoi"),
            "ANCIEN CONTENU AVANT RESTAURATION",
        )

    def test_le_fichier_de_meme_nom_est_ecrase_par_celui_de_la_sauvegarde(self):
        backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        with open(os.path.join(self.restore_tournament_folder, "dimanche", "tournoi14.tournoi")) as f:
            self.assertEqual(f.read(), "DB tournoi 14")

    def test_lancien_contenu_reste_recuperable_via_la_sauvegarde_de_securite(self):
        result = backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        safety_dir = result["safety_backup_dir"]
        with open(os.path.join(safety_dir, "tournois", "dimanche", "tournoi14.tournoi")) as f:
            self.assertEqual(f.read(), "ANCIEN CONTENU AVANT RESTAURATION")


class TournamentDayFolderPreservationTest(unittest.TestCase):
    """Demande explicite du 2026-10-10 (points 5 et 6) : le « Dossier
    par défaut » (tournament_day_folder, dans last_settings.json) de la
    machine de destination ne doit JAMAIS être remplacé par celui de la
    machine source lors de la restauration des autres préférences — et
    réciproquement, jamais importer un chemin absent localement. Les
    AUTRES préférences de last_settings.json (buyin_amount, etc.)
    doivent, elles, continuer à être mises à jour normalement depuis la
    sauvegarde."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        self.restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        self.pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")
        os.makedirs(self.restore_tournament_folder)

    def _create_backup_with_source_last_settings(self, source_day_folder):
        _write(
            os.path.join(self.env.poker_data_dir, "last_settings.json"),
            json.dumps({"tournament_day_folder": source_day_folder, "buyin_amount": 20}),
        )
        return backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="MAC-SOURCE",
        )["backup_dir"]

    def test_preserve_le_dossier_local_meme_si_la_sauvegarde_contient_une_autre_valeur(self):
        backup = self._create_backup_with_source_last_settings("/Users/raj/tournois_mac")
        local_settings_path = os.path.join(self.restore_poker_data_dir, "last_settings.json")
        _write(local_settings_path, json.dumps({"tournament_day_folder": "C:\\poker\\tournois_hp"}))
        backup_restore.restore_backup(
            backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        with open(local_settings_path) as f:
            data = json.load(f)
        # Valeur LOCALE préservée, jamais remplacée par celle du Mac.
        self.assertEqual(data["tournament_day_folder"], "C:\\poker\\tournois_hp")
        # Les AUTRES préférences, elles, sont bien mises à jour.
        self.assertEqual(data["buyin_amount"], 20)

    def test_dossier_local_absent_reste_absent_apres_restauration(self):
        """Aucune valeur locale avant restauration : la sauvegarde en
        contient pourtant une (machine source) — elle ne doit jamais
        être importée."""
        backup = self._create_backup_with_source_last_settings("/Users/raj/tournois_mac")
        backup_restore.restore_backup(
            backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        local_settings_path = os.path.join(self.restore_poker_data_dir, "last_settings.json")
        with open(local_settings_path) as f:
            data = json.load(f)
        self.assertNotIn("tournament_day_folder", data)
        # L'autre préférence, elle, est bien arrivée.
        self.assertEqual(data["buyin_amount"], 20)

    def test_aucun_last_settings_json_cree_si_ni_source_ni_destination_nen_ont(self):
        """Non-régression (bug réel rencontré et corrigé pendant ce
        chantier) : si NI la sauvegarde NI la destination n'ont jamais
        eu de last_settings.json, la restauration ne doit en créer
        aucun pour rien — un fichier vide créé "pour rien" avait fait
        grossir artificiellement le compte de fichiers d'une
        sauvegarde de sécurité à l'autre (voir PreRestoreBackupsRecursionTest)."""
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )["backup_dir"]
        backup_restore.restore_backup(
            backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        self.assertFalse(
            os.path.exists(os.path.join(self.restore_poker_data_dir, "last_settings.json"))
        )

    def test_plusieurs_restaurations_successives_preservent_la_valeur_locale_a_chaque_fois(self):
        backup = self._create_backup_with_source_last_settings("/Users/raj/tournois_mac")
        local_settings_path = os.path.join(self.restore_poker_data_dir, "last_settings.json")
        _write(local_settings_path, json.dumps({"tournament_day_folder": "C:\\poker\\tournois_hp"}))
        for _ in range(3):
            backup_restore.restore_backup(
                backup,
                tournament_folder=self.restore_tournament_folder,
                poker_data_dir=self.restore_poker_data_dir,
                pre_restore_root=self.pre_restore_root,
                open_tournament_paths=[],
            )
            with open(local_settings_path) as f:
                data = json.load(f)
            self.assertEqual(data["tournament_day_folder"], "C:\\poker\\tournois_hp")


class TournamentDayFolderHelperUnitTest(unittest.TestCase):
    """Tests unitaires directs des deux fonctions internes qui opèrent
    sur last_settings.json pendant une restauration — _read_
    tournament_day_folder/_restore_tournament_day_folder — notamment la
    garde "n'écrit rien si rien ne change" (correction du bug [6, 7, 7]
    rencontré pendant ce chantier)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "last_settings.json")

    def test_lecture_renvoie_none_si_fichier_absent(self):
        self.assertIsNone(backup_restore._read_tournament_day_folder(self.path))

    def test_lecture_renvoie_la_valeur_presente(self):
        _write(self.path, json.dumps({"tournament_day_folder": "/x/y"}))
        self.assertEqual(backup_restore._read_tournament_day_folder(self.path), "/x/y")

    def test_restore_ne_cree_aucun_fichier_si_value_none_et_fichier_absent(self):
        backup_restore._restore_tournament_day_folder(self.path, None)
        self.assertFalse(os.path.exists(self.path))

    def test_restore_ecrit_la_valeur_si_fichier_absent_et_value_fournie(self):
        backup_restore._restore_tournament_day_folder(self.path, "/local/jour")
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["tournament_day_folder"], "/local/jour")

    def test_restore_retire_la_cle_si_value_none_et_cle_presente(self):
        _write(self.path, json.dumps({"tournament_day_folder": "/source/jour", "buyin_amount": 20}))
        backup_restore._restore_tournament_day_folder(self.path, None)
        with open(self.path) as f:
            data = json.load(f)
        self.assertNotIn("tournament_day_folder", data)
        self.assertEqual(data["buyin_amount"], 20)

    def test_restore_ne_reecrit_pas_le_fichier_si_la_valeur_est_deja_correcte(self):
        _write(self.path, json.dumps({"tournament_day_folder": "/local/jour", "buyin_amount": 20}))
        mtime_avant = os.path.getmtime(self.path)
        import time
        time.sleep(0.01)
        backup_restore._restore_tournament_day_folder(self.path, "/local/jour")
        self.assertEqual(os.path.getmtime(self.path), mtime_avant)


class RestoreExcludesSessionStateFilesTest(unittest.TestCase):
    """Demande du 2026-09-07 : open_windows.json et phone_selected_pid.json
    ne doivent JAMAIS être réinjectés par une restauration (états de
    session temporaires liés aux processus d'une autre machine/d'un
    autre moment) — license.json, lui, continue d'être restauré
    normalement, sans que le système de licence lui-même (license.py)
    ne soit modifié ou activé par ce test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.backup = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )["backup_dir"]
        self.restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        self.restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        self.pre_restore_root = os.path.join(self._tmp.name, "cible_poker_data_pre_restore")
        # Depuis le 2026-10-10 : le dossier de destination doit déjà
        # exister (refusé sinon, jamais créé silencieusement).
        os.makedirs(self.restore_tournament_folder)

    def test_ne_cree_ni_open_windows_ni_phone_pid_si_absents_de_la_cible(self):
        """La cible n'a ni l'un ni l'autre avant restauration : la
        sauvegarde en contient pourtant (voir _SyntheticEnv) — ils ne
        doivent PAS apparaître après restauration."""
        backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        self.assertFalse(os.path.exists(os.path.join(self.restore_poker_data_dir, "open_windows.json")))
        self.assertFalse(os.path.exists(os.path.join(self.restore_poker_data_dir, "phone_selected_pid.json")))

    def test_n_ecrase_jamais_un_open_windows_json_deja_present_sur_la_cible(self):
        """La machine cible a SA PROPRE session en cours (son propre
        open_windows.json, différent de celui de la sauvegarde) : la
        restauration ne doit pas l'écraser avec l'ancien contenu."""
        _write(
            os.path.join(self.restore_poker_data_dir, "open_windows.json"),
            '{"/tmp/tournoi_reellement_ouvert_ici.tournoi": {"pid": 999}}',
        )
        backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        with open(os.path.join(self.restore_poker_data_dir, "open_windows.json")) as f:
            self.assertEqual(f.read(), '{"/tmp/tournoi_reellement_ouvert_ici.tournoi": {"pid": 999}}')

    def test_n_ecrase_jamais_un_phone_selected_pid_json_deja_present_sur_la_cible(self):
        _write(
            os.path.join(self.restore_poker_data_dir, "phone_selected_pid.json"),
            '{"pid": 999}',
        )
        backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        with open(os.path.join(self.restore_poker_data_dir, "phone_selected_pid.json")) as f:
            self.assertEqual(f.read(), '{"pid": 999}')

    def test_license_json_reste_restaure_normalement(self):
        """Non-régression explicite : license.json n'est PAS dans la
        liste d'exclusion, il doit être restauré comme n'importe quel
        autre fichier de ~/.poker_tournament."""
        result = backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        self.assertTrue(result["restored_data"])
        with open(os.path.join(self.restore_poker_data_dir, "license.json")) as f:
            self.assertEqual(f.read(), '{"machine_id": "ABC", "club_name": "CPC", "key": "XYZ"}')

    def test_la_sauvegarde_de_securite_pre_restauration_conserve_bien_ces_fichiers(self):
        """L'exclusion ne s'applique qu'à la RESTAURATION elle-même : la
        sauvegarde de sécurité pré-restauration (qui utilise
        create_backup, jamais concerné par l'exclusion) doit, elle,
        conserver l'éventuel open_windows.json/phone_selected_pid.json
        déjà présent sur la cible avant l'opération."""
        _write(
            os.path.join(self.restore_poker_data_dir, "open_windows.json"),
            '{"/tmp/etat_avant_restauration.tournoi": {"pid": 42}}',
        )
        result = backup_restore.restore_backup(
            self.backup,
            tournament_folder=self.restore_tournament_folder,
            poker_data_dir=self.restore_poker_data_dir,
            pre_restore_root=self.pre_restore_root,
            open_tournament_paths=[],
        )
        safety_dir = result["safety_backup_dir"]
        with open(os.path.join(safety_dir, "poker_tournament_data", "open_windows.json")) as f:
            self.assertEqual(f.read(), '{"/tmp/etat_avant_restauration.tournoi": {"pid": 42}}')


class PreRestoreBackupsRecursionTest(unittest.TestCase):
    """Incident RÉEL du 2026-10-09 (HP, Senaco TEST v1.2.44) :
    restore_backup() avec pre_restore_root=None — le vrai défaut,
    jamais utilisé ailleurs dans ce fichier (tous les autres tests
    injectent délibérément un pre_restore_root séparé, ce qui masquait
    ce bug) — calcule pre_restore_root À L'INTÉRIEUR MÊME de
    poker_data_dir. La sauvegarde de sécurité se recopiait alors
    indéfiniment dans elle-même ("WinError 206 : nom de fichier...
    trop long" sur Windows). Ces tests patchent default_poker_data_dir
    pour reproduire EXACTEMENT ce calcul par défaut, sur un dossier
    synthétique — jamais le vrai ~/.poker_tournament."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_pre_restore_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)

        self._patchers = [
            patch.object(backup_restore, "default_poker_data_dir", return_value=self.env.poker_data_dir),
            patch.object(backup_restore, "default_tournament_folder", return_value=self.env.tournament_folder),
        ]
        for p in self._patchers:
            self.addCleanup(p.stop)
            p.start()

        self.backup_dir = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="POSTE-TEST",
        )["backup_dir"]

    def _restore(self, **overrides):
        """`poker_data_dir`/`pre_restore_root` volontairement LAISSÉS À
        None (sauf override explicite) : c'est exactement ce qui
        déclenchait l'incident réel — reproduire autre chose ici
        reviendrait à ne jamais tester le vrai défaut."""
        kwargs = dict(
            tournament_folder=self.env.tournament_folder,
            poker_data_dir=None,
            pre_restore_root=None,
            open_tournament_paths=[],
        )
        kwargs.update(overrides)
        return backup_restore.restore_backup(self.backup_dir, **kwargs)

    def test_restauration_avec_le_vrai_defaut_pre_restore_root_ne_plante_pas(self):
        result = self._restore()
        self.assertTrue(result["restored_data"])
        pre_restore_root = os.path.join(self.env.poker_data_dir, backup_restore.PRE_RESTORE_BACKUPS_DIRNAME)
        self.assertEqual(len(os.listdir(pre_restore_root)), 1, "une seule sauvegarde de sécurité créée")

    def test_la_sauvegarde_de_securite_nest_jamais_imbriquee_dans_elle_meme(self):
        result = self._restore()
        safety_data_dir = os.path.join(result["safety_backup_dir"], backup_restore.POKER_DATA_SUBDIR)
        self.assertFalse(
            os.path.exists(os.path.join(safety_data_dir, backup_restore.PRE_RESTORE_BACKUPS_DIRNAME)),
            "la sauvegarde de sécurité ne doit jamais contenir pre_restore_backups (ni elle-même, ni une ancienne)",
        )

    def test_fichiers_reels_restent_inclus_dans_la_sauvegarde_de_securite(self):
        """Point 4 explicite : l'exclusion ne doit jamais faire perdre
        les fichiers réellement nécessaires à une restauration."""
        result = self._restore()
        data_dir = os.path.join(result["safety_backup_dir"], backup_restore.POKER_DATA_SUBDIR)
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "export_prefs.json")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "roster.json")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "photos", "jean.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "license.json")))

    def test_trois_restaurations_successives_ne_font_pas_grossir_chaque_sauvegarde(self):
        """Point 6 explicite : plusieurs sauvegardes/restaurations
        successives — chaque nouvelle sauvegarde de sécurité doit
        contenir le MÊME nombre de fichiers (celui de poker_data_dir à
        cet instant), jamais une progression 1x/2x/3x qui inclurait les
        sauvegardes de sécurité précédentes."""
        file_counts = []
        safety_dirs = []
        for _ in range(3):
            result = self._restore()
            safety_dirs.append(result["safety_backup_dir"])
            data_dir = os.path.join(result["safety_backup_dir"], backup_restore.POKER_DATA_SUBDIR)
            file_counts.append(sum(len(files) for _, _, files in os.walk(data_dir)))
        self.assertEqual(len(set(safety_dirs)), 3, "3 sauvegardes de sécurité bien distinctes")
        self.assertEqual(len(set(file_counts)), 1, f"décomptes attendus identiques, obtenus {file_counts}")

    def test_sauvegarde_de_securite_precedente_reste_intacte_sur_le_disque(self):
        """Point 3 explicite : une sauvegarde de sécurité déjà présente
        ne doit jamais être supprimée ni modifiée par une restauration
        suivante."""
        result1 = self._restore()
        first_safety_dir = result1["safety_backup_dir"]
        before_path = os.path.join(first_safety_dir, backup_restore.POKER_DATA_SUBDIR, "export_prefs.json")
        with open(before_path) as f:
            before = f.read()

        result2 = self._restore()
        self.assertNotEqual(first_safety_dir, result2["safety_backup_dir"])
        self.assertTrue(os.path.isdir(first_safety_dir), "la première sauvegarde de sécurité doit toujours exister")
        with open(before_path) as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_sauvegarde_manuelle_classique_exclut_aussi_pre_restore_backups(self):
        """Point 2 explicite : l'exclusion s'applique aussi à une
        sauvegarde manuelle classique (Menu principal/Purger Données),
        pas seulement à la sauvegarde de sécurité interne de
        restore_backup."""
        _write(
            os.path.join(
                self.env.poker_data_dir, backup_restore.PRE_RESTORE_BACKUPS_DIRNAME, "ancien", "x.json",
            ),
            "ancien contenu",
        )
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder, poker_data_dir=self.env.poker_data_dir,
        )
        data_dir = os.path.join(result["backup_dir"], backup_restore.POKER_DATA_SUBDIR)
        self.assertFalse(os.path.exists(os.path.join(data_dir, backup_restore.PRE_RESTORE_BACKUPS_DIRNAME)))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "export_prefs.json")))

    def test_restauration_depuis_une_ancienne_sauvegarde_contenant_deja_pre_restore_backups_ne_le_reinjecte_pas(self):
        """Défense en profondeur : si une sauvegarde créée AVANT ce
        correctif contient déjà un pre_restore_backups gonflé dans son
        poker_tournament_data/, la restauration ne doit jamais le
        réinjecter dans le poker_data_dir réel."""
        _write(
            os.path.join(
                self.backup_dir, backup_restore.POKER_DATA_SUBDIR,
                backup_restore.PRE_RESTORE_BACKUPS_DIRNAME, "vieux", "y.json",
            ),
            "vieux contenu",
        )
        self._restore()
        self.assertFalse(
            os.path.exists(
                os.path.join(self.env.poker_data_dir, backup_restore.PRE_RESTORE_BACKUPS_DIRNAME, "vieux"),
            ),
        )


class CopyTreeExclusionUnitTest(unittest.TestCase):
    """Test unitaire ciblé de _copy_tree_file_by_file(..., exclude_relpaths=...),
    indépendamment de tout le mécanisme de sauvegarde/restauration."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_test_")
        self.addCleanup(self._tmp.cleanup)
        self.src = os.path.join(self._tmp.name, "src")
        self.dst = os.path.join(self._tmp.name, "dst")
        _write(os.path.join(self.src, "a.json"), "A")
        _write(os.path.join(self.src, "b.json"), "B")
        _write(os.path.join(self.src, "sous_dossier", "c.json"), "C")

    def test_exclut_bien_les_fichiers_demandes_copie_le_reste(self):
        count = backup_restore._copy_tree_file_by_file(
            self.src, self.dst, exclude_relpaths={"a.json"},
        )
        self.assertEqual(count, 2)
        self.assertFalse(os.path.exists(os.path.join(self.dst, "a.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "b.json")))
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "sous_dossier", "c.json")))

    def test_sans_exclusion_copie_tout_comme_avant(self):
        count = backup_restore._copy_tree_file_by_file(self.src, self.dst)
        self.assertEqual(count, 3)
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "a.json")))

    def test_constante_exclusion_restauration_contient_bien_les_fichiers_attendus(self):
        # backup_history.json ajouté le 2026-10-07 (garde-fou "Purger
        # Données") : l'historique RÉEL des sauvegardes de CETTE machine
        # ne doit jamais être écrasé par une restauration — voir
        # tests/test_backup_history.py pour la couverture dédiée.
        self.assertEqual(
            backup_restore.RESTORE_EXCLUDED_DATA_FILENAMES,
            frozenset({"open_windows.json", "phone_selected_pid.json", "backup_history.json"}),
        )
        self.assertNotIn("license.json", backup_restore.RESTORE_EXCLUDED_DATA_FILENAMES)

    # ------------------------------------------------------------------
    # exclude_dir_names (incident réel du 2026-10-09, "WinError 206" —
    # pre_restore_backups recopié dans lui-même)
    # ------------------------------------------------------------------

    def test_exclude_dir_names_elague_bien_le_sous_dossier_a_toute_profondeur(self):
        _write(os.path.join(self.src, "pre_restore_backups", "ancien", "x.json"), "X")
        _write(os.path.join(self.src, "sous_dossier", "pre_restore_backups", "y.json"), "Y")
        count = backup_restore._copy_tree_file_by_file(
            self.src, self.dst, exclude_dir_names={"pre_restore_backups"},
        )
        # Seuls a.json, b.json, sous_dossier/c.json (3 fichiers déjà
        # présents dans setUp) doivent être copiés — rien de ce qui est
        # sous un "pre_restore_backups", à aucune profondeur.
        self.assertEqual(count, 3)
        self.assertFalse(os.path.exists(os.path.join(self.dst, "pre_restore_backups")))
        self.assertFalse(os.path.exists(os.path.join(self.dst, "sous_dossier", "pre_restore_backups")))
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "sous_dossier", "c.json")))

    def test_verify_copied_tree_avec_exclude_dir_names_ne_signale_jamais_lexclu_comme_manquant(self):
        _write(os.path.join(self.src, "pre_restore_backups", "ancien", "x.json"), "X")
        backup_restore._copy_tree_file_by_file(
            self.src, self.dst, exclude_dir_names={"pre_restore_backups"},
        )
        # Ne doit JAMAIS lever BackupError — le dossier exclu n'a jamais
        # été copié, donc jamais "manquant" au sens de cette vérification.
        backup_restore._verify_copied_tree(
            self.src, self.dst, exclude_dir_names={"pre_restore_backups"},
        )

    def test_verify_copied_tree_sans_exclude_dir_names_detecte_bien_un_vrai_manquant(self):
        """Non-régression : la vérification doit continuer à détecter un
        VRAI fichier manquant quand on ne lui demande d'exclure rien."""
        backup_restore._copy_tree_file_by_file(self.src, self.dst)
        os.remove(os.path.join(self.dst, "b.json"))
        with self.assertRaises(backup_restore.BackupError):
            backup_restore._verify_copied_tree(self.src, self.dst)


class NoOpOnCancellationTest(unittest.TestCase):
    """"Vérifie que l'annulation par l'utilisateur ne modifie rien" —
    structurel : backup_now()/restore_now() (voir _choose_tournament_file)
    doivent retourner immédiatement si l'utilisateur annule le sélecteur
    de dossier (askdirectory renvoie ""), avant tout appel à
    backup_restore.create_backup/restore_backup."""

    def test_backup_now_et_restore_now_retournent_tot_si_askdirectory_annule(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        # backup_now() délègue désormais entièrement à App._run_backup_flow
        # (demande du 2026-10-07 : même mécanisme réutilisé par le bouton
        # "Faire une sauvegarde maintenant..." de la purge Données, voir
        # tests/test_data_purge.py) — la logique "annulation = retour
        # immédiat" vit donc maintenant dans _run_backup_flow, jamais plus
        # dans backup_now() elle-même.
        backup_now_start = source.index("def backup_now():")
        backup_now_end = source.index("def restore_now():")
        backup_now_body = source[backup_now_start:backup_now_end]
        self.assertIn("self._run_backup_flow(win)", backup_now_body)

        flow_start = source.index(
            "def _run_backup_flow(self, parent, tournament_files=None, tournament_source_folders=None):"
        )
        flow_end = source.index("def _run_restore_flow(self, parent):")
        flow_body = source[flow_start:flow_end]
        self.assertIn("if not dest:\n            return None", flow_body)
        # La sauvegarde elle-même passe désormais par _run_blocking_with_
        # progress (demande du 2026-10-12, barres de progression RÉELLES
        # pour Menu principal ET Purge Données, qui partagent toutes deux
        # _run_backup_flow) — backup_restore.create_backup reste bien
        # l'opération réellement effectuée, simplement appelée depuis un
        # thread d'arrière-plan via un lambda transmis à ce helper.
        self.assertIn("result_info = _run_blocking_with_progress(", flow_body)
        self.assertIn("backup_restore.create_backup(", flow_body)
        self.assertLess(
            flow_body.index("if not dest:"),
            flow_body.index("result_info = _run_blocking_with_progress("),
        )
        self.assertLess(
            flow_body.index("result_info = _run_blocking_with_progress("),
            flow_body.index("backup_restore.create_backup("),
        )

        # restore_now() délègue désormais entièrement à App._run_restore_
        # flow (extraction du 2026-10-13, même raison que backup_now/
        # _run_backup_flow ci-dessus, réutilisable DEPUIS Senaco sans
        # tournoi) — la logique "annulation = retour immédiat" vit donc
        # maintenant dans _run_restore_flow, jamais plus dans restore_now()
        # elle-même.
        restore_now_start = source.index("def restore_now():")
        restore_now_end = source.index("def purge_now():")
        restore_now_body = source[restore_now_start:restore_now_end]
        self.assertIn("self._run_restore_flow(win)", restore_now_body)

        restore_start = source.index("def _run_restore_flow(self, parent):")
        restore_end = source.index("def _open_data_purge_dialog(self, parent):", restore_start)
        restore_body = source[restore_start:restore_end]
        self.assertIn("if not src:\n            return", restore_body)
        self.assertIn("backup_restore.restore_backup", restore_body)
        self.assertLess(
            restore_body.index("if not src:"),
            restore_body.index("backup_restore.restore_backup"),
        )


class RestoreMultiFolderTransparencyTest(unittest.TestCase):
    """Transparence EXPLICITE sur l'arborescence d'une sauvegarde multi-
    dossiers AVANT confirmation de restauration (demande explicite du
    2026-10-08, point 3 : "la restauration ne doit pas créer
    silencieusement une organisation différente de celle attendue") —
    restore_now() est une closure imbriquée dans _choose_tournament_file,
    difficile à invoquer isolément (construit une vraie fenêtre
    Toplevel) : vérification STRUCTURELLE du code source, même
    convention déjà établie par NoOpOnCancellationTest ci-dessus pour
    cette même fonction.

    Mis à jour le 2026-10-13 (correctif "dossiers techniques 4_mardi
    après restauration") : le mapping annoncé utilise désormais
    resolve_multi_folder_destination_labels (noms lisibles), plus
    sanitize_folder_label seule (qui ne gérait pas les collisions avec
    le dossier de destination réel)."""

    def test_le_mapping_libelle_vers_dossier_dorigine_est_annonce_avant_confirmation(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        restore_start = source.index("def _run_restore_flow(self, parent):")
        restore_end = source.index("def _open_data_purge_dialog(self, parent):", restore_start)
        restore_body = source[restore_start:restore_end]

        self.assertIn('manifest.get("tournament_source_folders")', restore_body)
        self.assertIn("backup_restore.resolve_multi_folder_destination_labels(", restore_body)
        # Le mapping (multi_note) doit être calculé et inclus dans le
        # message AVANT l'appel à askyesno — jamais une restauration
        # déjà lancée puis expliquée après coup.
        self.assertLess(
            restore_body.index("multi_note = "),
            restore_body.index("messagebox.askyesno("),
        )
        self.assertIn("multi_note", restore_body[restore_body.index("messagebox.askyesno("):])

    def test_explication_mentionne_des_noms_lisibles(self):
        """Demande explicite du 2026-10-13, point 2 : courte explication
        indiquant que les dossiers seront restaurés sous des noms
        lisibles."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        restore_start = source.index("def _run_restore_flow(self, parent):")
        restore_end = source.index("def _open_data_purge_dialog(self, parent):", restore_start)
        restore_body = source[restore_start:restore_end]
        self.assertIn("noms de dossiers lisibles", restore_body)

    def test_aucun_prefixe_technique_indexe_dans_le_message(self):
        """Non-régression explicite : le message ne doit plus jamais
        afficher le préfixe technique "<index>_<nom>" de l'ARCHIVE — la
        confirmation doit annoncer le résultat final sur le disque,
        jamais un détail d'implémentation interne."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        restore_start = source.index("def _run_restore_flow(self, parent):")
        restore_end = source.index("def _open_data_purge_dialog(self, parent):", restore_start)
        restore_body = source[restore_start:restore_end]
        self.assertNotIn('f"  {index}_{backup_restore', restore_body)
        self.assertNotIn("sous-dossiers techniques", restore_body)


class RestoreOverwriteWarningTest(unittest.TestCase):
    """Demande explicite du 2026-10-10 : la confirmation de
    restauration doit avertir explicitement que les fichiers .tournoi
    de même nom déjà présents seront REMPLACÉS — jusqu'ici seule la
    création automatique d'une sauvegarde de sécurité était mentionnée,
    rien sur le remplacement lui-même. Vérification STRUCTURELLE du
    code source (restore_now() construit une vraie fenêtre Toplevel,
    difficile à invoquer isolément) — même convention déjà établie par
    NoOpOnCancellationTest/RestoreMultiFolderTransparencyTest pour
    cette même fonction."""

    def _restore_now_body(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        restore_start = source.index("def _run_restore_flow(self, parent):")
        restore_end = source.index("def _open_data_purge_dialog(self, parent):", restore_start)
        return source[restore_start:restore_end]

    def test_avertissement_de_remplacement_present_et_avant_confirmation(self):
        restore_body = self._restore_now_body()
        self.assertIn(".tournoi", restore_body)
        self.assertIn("REMPLACÉS", restore_body)
        # Calculé AVANT askyesno, jamais une restauration déjà lancée
        # puis expliquée après coup (même exigence que pour multi_note).
        self.assertLess(
            restore_body.index("overwrite_note = "),
            restore_body.index("messagebox.askyesno("),
        )
        self.assertIn("overwrite_note", restore_body[restore_body.index("messagebox.askyesno("):])

    def test_avertissement_conditionne_a_la_presence_reelle_de_tournois(self):
        """Pas d'avertissement trompeur si la sauvegarde ne contient
        aucun fichier .tournoi (count == 0) : rien à remplacer dans ce
        cas."""
        restore_body = self._restore_now_body()
        note_def = restore_body[
            restore_body.index("overwrite_note = "):restore_body.index("messagebox.askyesno(")
        ]
        self.assertIn("if count else", note_def)


class MenuPrincipalPlacementTest(unittest.TestCase):
    """Vérifie l'ORDRE exact demandé : Lobby, puis les 2 boutons de la
    zone "Sauvegarde des données" (sans titre, sans séparateur, sans
    texte explicatif depuis le 2026-09-08 — voir
    SauvegardeZonePresentationTest), puis "À propos" — jamais
    l'inverse."""

    def test_ordre_lobby_puis_sauvegarde_puis_a_propos(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        idx_lobby = source.index('text="📋  Lobby (plusieurs tournois)"')
        idx_backup_btn = source.index('text="💾  Sauvegarder sur clé USB"')
        idx_restore_btn = source.index('text="♻️  Restaurer depuis une clé USB"')
        idx_about = source.index('text="ℹ️  À propos"')
        self.assertLess(idx_lobby, idx_backup_btn)
        self.assertLess(idx_backup_btn, idx_restore_btn)
        self.assertLess(idx_restore_btn, idx_about)

    def test_boutons_sauvegarde_ne_sont_jamais_grises(self):
        """La zone "Sauvegarde des données" ne doit jamais être passée à
        _refresh_launch_buttons_state (sans rapport avec "Un seul
        tournoi à la fois")."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        call_start = source.index("_refresh_launch_buttons_state(win, [")
        call_end = source.index("\n", call_start)
        call_line = source[call_start:call_end]
        self.assertNotIn("backup", call_line.lower())
        self.assertNotIn("restore", call_line.lower())


class SauvegardeZonePresentationTest(unittest.TestCase):
    """Demande du 2026-09-08 : plus de séparateur, plus de texte
    explicatif, et — demande complémentaire du même jour — plus de
    titre "💾 Sauvegarde des données" du tout dans cette zone : ne
    restent que les 2 boutons, qui remontent naturellement juste après
    Lobby. Les explications vivent uniquement dans les tooltips des
    deux boutons (voir tests dédiés ci-dessous), jamais touchés par ces
    changements de présentation."""

    def _zone_source(self):
        """Code source entre le bouton Lobby et "À propos" — englobe
        toute la zone "Sauvegarde des données", quel que soit ce
        qu'elle contient (titre, séparateur, texte...)."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        start = source.index('text="📋  Lobby (plusieurs tournois)"')
        end = source.index('text="ℹ️  À propos"', start)
        return source[start:end]

    def test_aucun_separateur_dans_la_zone(self):
        self.assertNotIn("Separator", self._zone_source())

    def test_aucun_titre_ni_texte_explicatif_dans_la_zone(self):
        """Ni l'ancien widget-titre "💾 Sauvegarde des données", ni
        l'ancien texte explicatif ne doivent plus apparaître dans cette
        zone (demande du 2026-09-08 : suppression complète du titre,
        après celle du séparateur/texte explicatif le même jour) — la
        simple mention "Sauvegarde des données" dans un COMMENTAIRE de
        code reste, elle, normale et n'est pas ce qui est testé ici."""
        zone = self._zone_source()
        self.assertNotIn('text="💾 Sauvegarde des données"', zone)
        self.assertNotIn("Permet de sauvegarder ou restaurer", zone)

    def test_aucun_tk_label_entre_lobby_et_le_premier_bouton(self):
        """Plus aucun widget (titre ou autre) entre le Tooltip de Lobby
        et le premier bouton de sauvegarde : celui-ci doit suivre
        directement, sans espace réservé à un ancien titre."""
        zone = self._zone_source()
        idx_first_btn = zone.index('text="💾  Sauvegarder sur clé USB"')
        between = zone[:idx_first_btn]
        self.assertEqual(between.count("tk.Label("), 0)

    def test_tooltip_bouton_sauvegarder_a_le_texte_demande(self):
        zone = self._zone_source()
        idx_btn = zone.index('text="💾  Sauvegarder sur clé USB"')
        idx_tooltip = zone.index("Tooltip(", idx_btn)
        idx_next_btn = zone.index('text="♻️  Restaurer depuis une clé USB"')
        self.assertLess(idx_tooltip, idx_next_btn)
        tooltip_block = zone[idx_tooltip:idx_next_btn]
        self.assertIn("Permet de sauvegarder les tournois, joueurs, photos et", tooltip_block)
        self.assertIn("réglages sur une clé USB.", tooltip_block)

    def test_tooltip_bouton_restaurer_a_le_texte_demande(self):
        zone = self._zone_source()
        idx_btn = zone.index('text="♻️  Restaurer depuis une clé USB"')
        idx_tooltip = zone.index("Tooltip(", idx_btn)
        tooltip_block = zone[idx_tooltip:]
        self.assertIn("Permet de restaurer les tournois, joueurs, photos et", tooltip_block)
        self.assertIn("réglages depuis une clé USB.", tooltip_block)

    def test_titre_completement_retire_du_fichier(self):
        """Non-régression explicite (demande du 2026-09-08) : le titre
        "💾 Sauvegarde des données" ne doit plus exister NULLE PART dans
        main.py, pas seulement dans cette zone."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        self.assertNotIn('text="💾 Sauvegarde des données"', source)

    def test_les_deux_boutons_restent_bien_presents(self):
        """Non-régression : seul le titre a été retiré, jamais les
        boutons eux-mêmes ni leurs tooltips (voir tests dédiés)."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        self.assertIn('text="💾  Sauvegarder sur clé USB"', source)
        self.assertIn('text="♻️  Restaurer depuis une clé USB"', source)


# =======================================================================
# Correctif du 2026-10-14 (quater) : "C:\poker\senaco\senaco" après
# restauration d'une sauvegarde faite sur Mac, sur un poste Windows (HP)
# — voir backup_restore.detect_redundant_source_folder_prefix pour le
# diagnostic complet et les conditions STRICTES (toutes nécessaires,
# aucune suffisante seule) sous lesquelles un premier segment de chemin
# est jugé redondant et retiré.
# =======================================================================
class DetectRedundantSourceFolderPrefixUnitTest(unittest.TestCase):
    """Tests unitaires directs de la fonction de détection — manifestes
    synthétiques minimaux (seuls "tournament_files" est lu), jamais de
    vraie sauvegarde sur disque ici (voir RestoreRedundantFolderPrefixTest
    plus bas pour l'intégration complète via restore_backup)."""

    def _manifest(self, tournament_files):
        return {"tournament_files": tournament_files}

    def test_detecte_le_segment_redondant_quand_tout_concorde(self):
        manifest = self._manifest([
            "senaco/samedi/To1.tournoi", "senaco/mardi/To2.tournoi",
        ])
        self.assertEqual(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco"),
            "senaco",
        )

    def test_aucune_detection_si_premier_segment_est_deja_un_jour(self):
        """Structure déjà normale (le cas très majoritaire) : rien à
        faire, comportement actuel strictement préservé."""
        manifest = self._manifest(["samedi/To1.tournoi"])
        self.assertIsNone(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco")
        )

    def test_aucune_detection_si_le_nom_ne_correspond_pas_a_la_destination(self):
        """Signal structurel présent (jour au second niveau) mais AUCUNE
        coïncidence de nom avec la destination — jamais assez pour
        conclure à une redondance (demande explicite : privilégier les
        métadonnées/la structure, jamais une simple supposition)."""
        manifest = self._manifest(["dossier_sans_rapport/samedi/To1.tournoi"])
        self.assertIsNone(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco")
        )

    def test_aucune_detection_si_tous_les_fichiers_ne_partagent_pas_le_meme_segment(self):
        """Incohérence partielle : jamais une supposition sur certains
        fichiers seulement — voir test_chemin_ambigu_un_seul_fichier_non_
        concluant_reste_inchange pour la version intégrée via restore_
        backup."""
        manifest = self._manifest([
            "senaco/samedi/To1.tournoi", "autre_dossier/mardi/To2.tournoi",
        ])
        self.assertIsNone(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco")
        )

    def test_aucune_detection_si_un_fichier_est_directement_sous_le_segment_sans_jour(self):
        manifest = self._manifest(["senaco/To1.tournoi"])
        self.assertIsNone(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco")
        )

    def test_aucune_detection_si_le_segment_suivant_nest_pas_un_jour(self):
        manifest = self._manifest(["senaco/archives/To1.tournoi"])
        self.assertIsNone(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco")
        )

    def test_sauvegarde_sans_tournois_renvoie_none(self):
        manifest = self._manifest([])
        self.assertIsNone(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco")
        )

    def test_insensible_a_la_casse(self):
        """Nom de dossier destination et/ou jour avec une casse
        différente (ex. Windows, ou "Vendredi" comme dans _SyntheticEnv
        ci-dessus) : toujours reconnu. Chemin de destination au format
        POSIX (séparateur "/") : un séparateur Windows ("\\") n'est de
        toute façon reconnu comme tel que SOUS Windows — os.path.
        basename/normpath traiteraient sinon tout le chemin "C:\\poker\\
        SENACO" comme un seul composant sur ce poste de développement
        Mac, hors du périmètre réellement testé ici (la casse, pas le
        séparateur)."""
        manifest = self._manifest(["Senaco/Samedi/To1.tournoi"])
        self.assertEqual(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/SENACO"),
            "Senaco",
        )

    def test_sous_dossiers_profonds_toujours_detectes(self):
        """Seuls les DEUX premiers segments comptent pour la détection —
        peu importe la profondeur au-delà (ex. "samedi/soir/...")."""
        manifest = self._manifest(["senaco/samedi/soir/To1.tournoi"])
        self.assertEqual(
            backup_restore.detect_redundant_source_folder_prefix(manifest, "/C/poker/senaco"),
            "senaco",
        )


class RestoreRedundantFolderPrefixTest(unittest.TestCase):
    """Intégration complète via restore_backup — reproduction du scénario
    exact signalé sur le HP (Mac -> Windows), sous-dossiers multiples,
    restaurations répétées, sauvegardes anciennes/sans redondance,
    chemins ambigus, et non-interférence avec les sauvegardes multi-
    dossiers et la sauvegarde de sécurité."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_redundant_test_")
        self.addCleanup(self._tmp.cleanup)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        self.pre_restore_root = os.path.join(self._tmp.name, "pre_restore")

    def test_scenario_exact_reproduit_mac_vers_windows(self):
        """Mac (source) : "Dossier par défaut" configuré UN NIVEAU AU-
        DESSUS d'un sous-dossier nommé "senaco" contenant les jours de
        tournoi. Windows (destination) : "Dossier par défaut" = .../
        senaco, DÉJÀ PEUPLÉ (mardi, mercredi) — reproduit exactement
        l'arborescence de la photographie fournie par l'utilisateur."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "senaco", "samedi", "To131026-2.tournoi"), "DB samedi")
        _write(os.path.join(mac_root, "senaco", "mardi", "ToMac.tournoi"), "DB mardi mac")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None, computer_name="MAC-RAJ",
        )["backup_dir"]
        manifest = backup_restore.validate_backup_folder(backup)
        self.assertEqual(
            sorted(manifest["tournament_files"]),
            ["senaco/mardi/ToMac.tournoi", "senaco/samedi/To131026-2.tournoi"],
        )

        windows_senaco = os.path.join(self._tmp.name, "poker", "senaco")
        _write(os.path.join(windows_senaco, "mardi", "existant.tournoi"), "deja la avant")
        _write(os.path.join(windows_senaco, "mercredi", "autre.tournoi"), "deja la aussi")

        result = backup_restore.restore_backup(
            backup, tournament_folder=windows_senaco, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        self.assertEqual(result["restored_tournament_files"], 2)
        # Jamais de dossier "senaco" imbriqué une seconde fois.
        self.assertFalse(os.path.isdir(os.path.join(windows_senaco, "senaco")))
        self.assertTrue(os.path.isfile(
            os.path.join(windows_senaco, "samedi", "To131026-2.tournoi")
        ))
        self.assertTrue(os.path.isfile(os.path.join(windows_senaco, "mardi", "ToMac.tournoi")))
        # Les fichiers déjà présents (mardi, mercredi) restent intacts.
        with open(os.path.join(windows_senaco, "mardi", "existant.tournoi")) as f:
            self.assertEqual(f.read(), "deja la avant")
        with open(os.path.join(windows_senaco, "mercredi", "autre.tournoi")) as f:
            self.assertEqual(f.read(), "deja la aussi")

    def test_sous_dossiers_multiples_au_dela_du_jour(self):
        """Un niveau supplémentaire APRÈS le jour (ex. "samedi/soir/...")
        doit être préservé à l'identique — seul le premier segment
        redondant est retiré, jamais plus."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "club", "samedi", "soir", "To1.tournoi"), "DB")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        destination = os.path.join(self._tmp.name, "poker", "club")
        os.makedirs(destination)

        backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        self.assertTrue(os.path.isfile(os.path.join(destination, "samedi", "soir", "To1.tournoi")))
        self.assertFalse(os.path.isdir(os.path.join(destination, "club")))

    def test_restaurations_repetees_restent_idempotentes(self):
        """Restaurer deux fois la MÊME sauvegarde sur la MÊME destination
        ne doit jamais aggraver ni modifier l'emplacement final."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "club", "samedi", "To1.tournoi"), "v1")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        destination = os.path.join(self._tmp.name, "poker", "club")
        os.makedirs(destination)

        for _ in range(2):
            result = backup_restore.restore_backup(
                backup, tournament_folder=destination, poker_data_dir=None,
                pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
            )
            self.assertEqual(result["restored_tournament_files"], 1)
            self.assertTrue(os.path.isfile(os.path.join(destination, "samedi", "To1.tournoi")))
            self.assertFalse(os.path.isdir(os.path.join(destination, "club")))
        # Toujours UN SEUL fichier à cet emplacement (écrasé, pas dupliqué).
        self.assertEqual(
            sorted(os.listdir(os.path.join(destination, "samedi"))), ["To1.tournoi"],
        )

    def test_sauvegarde_sans_redondance_comportement_strictement_inchange(self):
        """Le cas NORMAL/majoritaire (aucun niveau redondant, structure
        déjà conforme) — y compris quand le dossier source ne porte pas
        du tout le même nom que la destination : comportement identique
        à avant ce correctif, aucune détection ne doit se déclencher."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau_normal")
        _write(os.path.join(mac_root, "samedi", "To1.tournoi"), "v1")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        destination = os.path.join(self._tmp.name, "poker", "senaco")
        os.makedirs(destination)

        result = backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        self.assertEqual(result["restored_tournament_files"], 1)
        self.assertTrue(os.path.isfile(os.path.join(destination, "samedi", "To1.tournoi")))

    def test_sauvegarde_ancienne_sans_le_nouveau_champ_reste_compatible(self):
        """Simule une sauvegarde créée AVANT ce correctif : son manifeste
        ne contient jamais que les champs déjà existants — la détection
        ne repose sur AUCUN champ nouveau, donc fonctionne (ou s'abstient
        correctement) de façon identique sur une telle sauvegarde."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "senaco", "samedi", "To1.tournoi"), "v1")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        manifest_path = os.path.join(backup, backup_restore.MANIFEST_FILENAME)
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        self.assertNotIn("tournament_source_folders", manifest)  # sauvegarde à un seul dossier
        self.assertIn("tournament_files", manifest)  # seul champ dont dépend la détection

        destination = os.path.join(self._tmp.name, "poker", "senaco")
        os.makedirs(destination)
        result = backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )
        self.assertTrue(os.path.isfile(os.path.join(destination, "samedi", "To1.tournoi")))

    def test_chemin_ambigu_un_seul_fichier_non_concluant_reste_inchange(self):
        """Un seul fichier NE partage PAS le segment "senaco" des autres
        (incohérence) : la détection doit s'abstenir ENTIÈREMENT, pour
        TOUS les fichiers de cette sauvegarde — jamais une correction
        partielle devinée au cas par cas."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "senaco", "samedi", "To1.tournoi"), "v1")
        _write(os.path.join(mac_root, "autre_ligne", "mardi", "To2.tournoi"), "v2")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        destination = os.path.join(self._tmp.name, "poker", "senaco")
        os.makedirs(destination)

        result = backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        self.assertEqual(result["restored_tournament_files"], 2)
        # Chemins restaurés TELS QUELS (jamais de retrait partiel/deviné).
        self.assertTrue(os.path.isfile(
            os.path.join(destination, "senaco", "samedi", "To1.tournoi")
        ))
        self.assertTrue(os.path.isfile(
            os.path.join(destination, "autre_ligne", "mardi", "To2.tournoi")
        ))

    def test_sauvegarde_multi_dossiers_jamais_affectee_par_ce_mecanisme(self):
        """Une sauvegarde multi-dossiers (tournament_source_folders)
        dont un des dossiers porte, par coïncidence, le même nom que la
        destination : son propre mécanisme (resolve_multi_folder_
        destination_labels) reste seul responsable, jamais interféré par
        detect_redundant_source_folder_prefix (jamais appelée dans ce
        cas, voir restore_backup)."""
        folder_a = os.path.join(self._tmp.name, "mac_un", "senaco")
        folder_b = os.path.join(self._tmp.name, "mac_deux", "autre_club")
        _write(os.path.join(folder_a, "samedi", "To1.tournoi"), "A")
        _write(os.path.join(folder_b, "mardi", "To2.tournoi"), "B")
        backup = backup_restore.create_backup(
            self.usb,
            tournament_files=[
                os.path.join(folder_a, "samedi", "To1.tournoi"),
                os.path.join(folder_b, "mardi", "To2.tournoi"),
            ],
            tournament_source_folders=[folder_a, folder_b],
            poker_data_dir=None,
        )["backup_dir"]
        manifest = backup_restore.validate_backup_folder(backup)
        self.assertIn("tournament_source_folders", manifest)

        destination = os.path.join(self._tmp.name, "poker", "senaco")
        os.makedirs(destination)
        result = backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        self.assertEqual(result["restored_tournament_files"], 2)
        # Comportement EXISTANT du mécanisme multi-dossiers, entièrement
        # inchangé ici : chaque dossier source garde son propre sous-
        # dossier à la destination (resolve_multi_folder_destination_
        # labels) — y compris "senaco", qui coïncide avec le nom de la
        # destination elle-même, sans qu'aucune fusion ne soit tentée
        # (ce serait précisément le comportement risqué que detect_
        # redundant_source_folder_prefix évite, mais qui ne s'applique
        # jamais ici puisque ce n'est pas elle qui est à l'œuvre pour
        # une sauvegarde multi-dossiers).
        self.assertTrue(os.path.isfile(os.path.join(destination, "senaco", "samedi", "To1.tournoi")))
        self.assertTrue(os.path.isfile(os.path.join(destination, "autre_club", "mardi", "To2.tournoi")))

    def test_securite_pre_restauration_toujours_creee(self):
        """Le mécanisme de sauvegarde de sécurité pré-restauration
        (étape 1, AVANT tout écrasement) reste entièrement inchangé et
        actif dans ce scénario."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "senaco", "samedi", "To1.tournoi"), "v1")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        destination = os.path.join(self._tmp.name, "poker", "senaco")
        _write(os.path.join(destination, "samedi", "To1.tournoi"), "ancien contenu local")

        result = backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        self.assertTrue(os.path.isdir(result["safety_backup_dir"]))
        safety_manifest = backup_restore.validate_backup_folder(result["safety_backup_dir"])
        self.assertEqual(safety_manifest["tournament_files"], ["samedi/To1.tournoi"])
        # Le contenu LOCAL d'avant restauration (jamais celui de la
        # sauvegarde Mac) a bien été sauvegardé avant écrasement.
        with open(os.path.join(result["safety_backup_dir"], "tournois", "samedi", "To1.tournoi")) as f:
            self.assertEqual(f.read(), "ancien contenu local")

    def test_ne_deplace_ni_ne_supprime_un_fichier_deja_imbrique_dune_restauration_passee(self):
        """Demande explicite : la correction ne concerne QUE les
        restaurations FUTURES — un dossier déjà imbriqué par une
        restauration PASSÉE (avant ce correctif) ne doit jamais être
        déplacé ni supprimé automatiquement."""
        mac_root = os.path.join(self._tmp.name, "mac_bureau")
        _write(os.path.join(mac_root, "senaco", "samedi", "To1.tournoi"), "v1")
        backup = backup_restore.create_backup(
            self.usb, tournament_folder=mac_root, poker_data_dir=None,
        )["backup_dir"]
        destination = os.path.join(self._tmp.name, "poker", "senaco")
        # Simule le résultat d'une restauration PASSÉE, déjà imbriquée
        # (bug non corrigé à l'époque) — ne doit jamais être touché.
        _write(os.path.join(destination, "senaco", "samedi", "ancien_imbrique.tournoi"), "ne pas toucher")

        backup_restore.restore_backup(
            backup, tournament_folder=destination, poker_data_dir=None,
            pre_restore_root=self.pre_restore_root, open_tournament_paths=[],
        )

        # L'ancien fichier imbriqué est resté EXACTEMENT où il était.
        with open(os.path.join(destination, "senaco", "samedi", "ancien_imbrique.tournoi")) as f:
            self.assertEqual(f.read(), "ne pas toucher")
        # La NOUVELLE restauration, elle, atterrit bien au bon endroit.
        self.assertTrue(os.path.isfile(os.path.join(destination, "samedi", "To1.tournoi")))


class RestoreRedundantFolderPrefixTransparencyTest(unittest.TestCase):
    """Demande explicite : jamais une correction silencieuse — toute
    détection doit être annoncée avant confirmation. Vérification
    STRUCTURELLE (même convention que RestoreMultiFolderTransparencyTest
    ci-dessus) : _run_restore_flow construit une vraie fenêtre Toplevel,
    difficile à invoquer isolément."""

    def _restore_flow_body(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        restore_start = source.index("def _run_restore_flow(self, parent):")
        restore_end = source.index("def _open_data_purge_dialog(self, parent):", restore_start)
        return source[restore_start:restore_end]

    def test_detection_calculee_et_incluse_avant_confirmation(self):
        restore_body = self._restore_flow_body()
        self.assertIn(
            "backup_restore.detect_redundant_source_folder_prefix(", restore_body,
        )
        self.assertLess(
            restore_body.index("redundant_note = "),
            restore_body.index("messagebox.askyesno("),
        )
        self.assertIn("redundant_note", restore_body[restore_body.index("messagebox.askyesno("):])

    def test_jamais_declenchee_pour_une_sauvegarde_multi_dossiers(self):
        """Les deux mécanismes (multi_note / redundant_note) restent
        mutuellement exclusifs — jamais les deux calculés pour la même
        sauvegarde."""
        restore_body = self._restore_flow_body()
        idx_redundant_block = restore_body.index("redundant_prefix = None")
        idx_multi_folders_check = restore_body.index(
            "if count and not multi_folders:", idx_redundant_block,
        )
        self.assertGreater(idx_multi_folders_check, idx_redundant_block)


if __name__ == "__main__":
    unittest.main()
