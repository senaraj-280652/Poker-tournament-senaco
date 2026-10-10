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
    "Fichier manquant après la copie : primes_To170926.pdf" — asymétrie
    copie/vérification des .tournoi, corrigée en vérifiant uniquement
    les paires réellement copiées (tournament_copy_pairs)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="poker_backup_extraneous_")
        self.addCleanup(self._tmp.cleanup)
        self.env = _SyntheticEnv(self._tmp.name)
        self.usb = os.path.join(self._tmp.name, "usb_stick")
        os.makedirs(self.usb)
        _write(os.path.join(self.env.tournament_folder, "dimanche", "primes_To170926.pdf"), "PDF PRIMES")
        _write(os.path.join(self.env.tournament_folder, "repertoire_joueurs.csv"), "nom;club\nJean;CPC")
        _write(os.path.join(self.env.tournament_folder, "Vendredi", "placement_sitngo.pdf"), "PDF PLACEMENT")
        _write(os.path.join(self.env.tournament_folder, ".DS_Store"), "FAUX FICHIER TECHNIQUE MAC")

    def test_fichiers_annexes_ne_bloquent_plus_la_sauvegarde(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir, computer_name="POSTE-TEST",
        )
        self.assertEqual(result["tournament_file_count"], 2)

    def test_les_tournois_restent_bien_copies_et_corrects(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        backed_up = os.path.join(result["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR, "dimanche", "tournoi14.tournoi")
        with open(backed_up) as f:
            self.assertEqual(f.read(), "DB tournoi 14")

    def test_fichiers_annexes_ne_sont_jamais_copies_dans_la_sauvegarde(self):
        result = backup_restore.create_backup(
            self.usb, tournament_folder=self.env.tournament_folder,
            poker_data_dir=self.env.poker_data_dir,
        )
        tournois_dst = os.path.join(result["backup_dir"], backup_restore.TOURNAMENTS_SUBDIR)
        self.assertFalse(os.path.exists(os.path.join(tournois_dst, "dimanche", "primes_To170926.pdf")))
        self.assertFalse(os.path.exists(os.path.join(tournois_dst, "repertoire_joueurs.csv")))
        self.assertFalse(os.path.exists(os.path.join(tournois_dst, ".DS_Store")))

    def test_fichiers_annexes_restent_intacts_sur_le_disque_dorigine(self):
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
        restore_tournament_folder = os.path.join(self._tmp.name, "cible_tournois")
        restore_poker_data_dir = os.path.join(self._tmp.name, "cible_poker_data")
        # Depuis le 2026-10-10 : le dossier de destination doit déjà
        # exister (refusé sinon, jamais créé silencieusement) — voir
        # TournamentFolderValidationTest pour ce comportement lui-même.
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
        with open(os.path.join(self.env.tournament_folder, "repertoire_joueurs.csv")) as f:
            self.assertEqual(f.read(), "nom;club\nJean;CPC")


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
        self.assertTrue(os.path.isfile(os.path.join(configured, "dimanche", "tournoi14.tournoi")))
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
        self.assertEqual(data["tournament_day_folder"], "C:\\poker\\tournois_hp")
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
        self.assertEqual(data["buyin_amount"], 20)

    def test_aucun_last_settings_json_cree_si_ni_source_ni_destination_nen_ont(self):
        """Non-régression (bug réel rencontré et corrigé pendant ce
        chantier) : si NI la sauvegarde NI la destination n'ont jamais
        eu de last_settings.json, la restauration ne doit en créer
        aucun pour rien."""
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
    garde "n'écrit rien si rien ne change" (correction du bug [N, N+1,
    N+1] rencontré pendant ce chantier sur la branche principale)."""

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

    def test_constante_exclusion_restauration_contient_bien_les_deux_fichiers_attendus(self):
        self.assertEqual(
            backup_restore.RESTORE_EXCLUDED_DATA_FILENAMES,
            frozenset({"open_windows.json", "phone_selected_pid.json"}),
        )
        self.assertNotIn("license.json", backup_restore.RESTORE_EXCLUDED_DATA_FILENAMES)

    # ------------------------------------------------------------------
    # exclude_dir_names (correctif du 2026-10-09 — pre_restore_backups
    # recopié dans lui-même)
    # ------------------------------------------------------------------

    def test_exclude_dir_names_elague_bien_le_sous_dossier_a_toute_profondeur(self):
        _write(os.path.join(self.src, "pre_restore_backups", "ancien", "x.json"), "X")
        _write(os.path.join(self.src, "sous_dossier", "pre_restore_backups", "y.json"), "Y")
        count = backup_restore._copy_tree_file_by_file(
            self.src, self.dst, exclude_dir_names={"pre_restore_backups"},
        )
        self.assertEqual(count, 3)
        self.assertFalse(os.path.exists(os.path.join(self.dst, "pre_restore_backups")))
        self.assertFalse(os.path.exists(os.path.join(self.dst, "sous_dossier", "pre_restore_backups")))
        self.assertTrue(os.path.isfile(os.path.join(self.dst, "sous_dossier", "c.json")))

    def test_verify_copied_tree_avec_exclude_dir_names_ne_signale_jamais_lexclu_comme_manquant(self):
        _write(os.path.join(self.src, "pre_restore_backups", "ancien", "x.json"), "X")
        backup_restore._copy_tree_file_by_file(
            self.src, self.dst, exclude_dir_names={"pre_restore_backups"},
        )
        backup_restore._verify_copied_tree(
            self.src, self.dst, exclude_dir_names={"pre_restore_backups"},
        )

    def test_verify_copied_tree_sans_exclude_dir_names_detecte_bien_un_vrai_manquant(self):
        backup_restore._copy_tree_file_by_file(self.src, self.dst)
        os.remove(os.path.join(self.dst, "b.json"))
        with self.assertRaises(backup_restore.BackupError):
            backup_restore._verify_copied_tree(self.src, self.dst)


class PreRestoreBackupsRecursionTest(unittest.TestCase):
    """Correctif du 2026-10-09 : restore_backup() avec pre_restore_root=
    None (le vrai défaut, jamais utilisé ailleurs dans ce fichier) calcule
    pre_restore_root À L'INTÉRIEUR de poker_data_dir — la sauvegarde de
    sécurité se recopiait alors indéfiniment dans elle-même ("WinError
    206 : nom de fichier... trop long" sur un poste réel). Ces tests
    patchent default_poker_data_dir pour reproduire EXACTEMENT ce calcul
    par défaut, sur un dossier synthétique — jamais le vrai
    ~/.poker_tournament."""

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
        self.assertEqual(len(os.listdir(pre_restore_root)), 1)

    def test_la_sauvegarde_de_securite_nest_jamais_imbriquee_dans_elle_meme(self):
        result = self._restore()
        safety_data_dir = os.path.join(result["safety_backup_dir"], backup_restore.POKER_DATA_SUBDIR)
        self.assertFalse(
            os.path.exists(os.path.join(safety_data_dir, backup_restore.PRE_RESTORE_BACKUPS_DIRNAME)),
        )

    def test_fichiers_reels_restent_inclus_dans_la_sauvegarde_de_securite(self):
        result = self._restore()
        data_dir = os.path.join(result["safety_backup_dir"], backup_restore.POKER_DATA_SUBDIR)
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "export_prefs.json")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "roster.json")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "photos", "jean.jpg")))
        self.assertTrue(os.path.isfile(os.path.join(data_dir, "license.json")))

    def test_trois_restaurations_successives_ne_font_pas_grossir_chaque_sauvegarde(self):
        file_counts = []
        safety_dirs = []
        for _ in range(3):
            result = self._restore()
            safety_dirs.append(result["safety_backup_dir"])
            data_dir = os.path.join(result["safety_backup_dir"], backup_restore.POKER_DATA_SUBDIR)
            file_counts.append(sum(len(files) for _, _, files in os.walk(data_dir)))
        self.assertEqual(len(set(safety_dirs)), 3)
        self.assertEqual(len(set(file_counts)), 1, f"décomptes attendus identiques, obtenus {file_counts}")

    def test_sauvegarde_de_securite_precedente_reste_intacte_sur_le_disque(self):
        result1 = self._restore()
        first_safety_dir = result1["safety_backup_dir"]
        before_path = os.path.join(first_safety_dir, backup_restore.POKER_DATA_SUBDIR, "export_prefs.json")
        with open(before_path) as f:
            before = f.read()

        result2 = self._restore()
        self.assertNotEqual(first_safety_dir, result2["safety_backup_dir"])
        self.assertTrue(os.path.isdir(first_safety_dir))
        with open(before_path) as f:
            after = f.read()
        self.assertEqual(before, after)

    def test_sauvegarde_manuelle_classique_exclut_aussi_pre_restore_backups(self):
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
        backup_start = source.index("def backup_now():")
        backup_end = source.index("def restore_now():")
        backup_body = source[backup_start:backup_end]
        self.assertIn("if not dest:\n                return", backup_body)
        self.assertIn("result_info = backup_restore.create_backup(dest)", backup_body)
        # L'appel réel (pas sa mention dans la docstring) doit venir
        # APRÈS le retour anticipé.
        self.assertLess(
            backup_body.index("if not dest:"),
            backup_body.index("result_info = backup_restore.create_backup(dest)"),
        )

        restore_start = backup_end
        restore_end = source.index("btn_frame = tk.Frame(win", restore_start)
        restore_body = source[restore_start:restore_end]
        self.assertIn("if not src:\n                return", restore_body)
        self.assertIn("backup_restore.restore_backup", restore_body)
        self.assertLess(
            restore_body.index("if not src:"),
            restore_body.index("backup_restore.restore_backup"),
        )


class RestoreOverwriteWarningTest(unittest.TestCase):
    """Demande explicite du 2026-10-10 : la confirmation de
    restauration doit avertir explicitement que les fichiers .tournoi
    de même nom déjà présents seront REMPLACÉS — jusqu'ici seule la
    création automatique d'une sauvegarde de sécurité était mentionnée,
    rien sur le remplacement lui-même. Vérification STRUCTURELLE du
    code source (restore_now() construit une vraie fenêtre Toplevel,
    difficile à invoquer isolément) — même convention déjà établie par
    NoOpOnCancellationTest pour cette même fonction."""

    def _restore_now_body(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        restore_start = source.index("def restore_now():")
        restore_end = source.index("btn_frame = tk.Frame(win", restore_start)
        return source[restore_start:restore_end]

    def test_avertissement_de_remplacement_present_et_avant_confirmation(self):
        restore_body = self._restore_now_body()
        self.assertIn(".tournoi", restore_body)
        self.assertIn("REMPLACÉS", restore_body)
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


if __name__ == "__main__":
    unittest.main()
