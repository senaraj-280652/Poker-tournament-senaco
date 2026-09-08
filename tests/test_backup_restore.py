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


class MenuPrincipalPlacementTest(unittest.TestCase):
    """Vérifie l'ORDRE exact demandé : Lobby, puis la zone "Sauvegarde
    des données" (titre + 2 boutons, sans séparateur ni texte explicatif
    depuis le 2026-09-08 — voir SauvegardeZonePresentationTest), puis
    "À propos" — jamais l'inverse."""

    def test_ordre_lobby_puis_sauvegarde_puis_a_propos(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        idx_lobby = source.index('text="📋  Lobby (plusieurs tournois)"')
        idx_section_title = source.index('text="💾 Sauvegarde des données"')
        idx_backup_btn = source.index('text="💾  Sauvegarder sur clé USB"')
        idx_restore_btn = source.index('text="♻️  Restaurer depuis une clé USB"')
        idx_about = source.index('text="ℹ️  À propos"')
        self.assertLess(idx_lobby, idx_section_title)
        self.assertLess(idx_section_title, idx_backup_btn)
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
    """Demande du 2026-09-08 : plus de séparateur ni de texte explicatif
    sous le titre "💾 Sauvegarde des données" dans cette zone — les
    explications vivent désormais uniquement dans les tooltips des deux
    boutons. Le titre lui-même et l'ordre des boutons restent
    inchangés (voir MenuPrincipalPlacementTest)."""

    def _zone_source(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        start = source.index('text="💾 Sauvegarde des données"')
        end = source.index('text="ℹ️  À propos"', start)
        return source[start:end]

    def test_aucun_separateur_dans_la_zone(self):
        self.assertNotIn("Separator", self._zone_source())

    def test_aucun_texte_explicatif_sous_le_titre(self):
        zone = self._zone_source()
        self.assertNotIn("Permet de sauvegarder ou restaurer", zone)
        # Le morceau de code ENTRE le titre et le premier bouton (donc
        # avant toute chance de matcher le texte d'un tooltip) ne doit
        # contenir aucun bloc tk.Label supplémentaire.
        idx_title = zone.index('text="💾 Sauvegarde des données"')
        idx_first_btn = zone.index('text="💾  Sauvegarder sur clé USB"')
        between = zone[idx_title:idx_first_btn]
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

    def test_titre_de_la_zone_inchange(self):
        # Non-régression explicite : seuls le séparateur et le texte
        # explicatif ont été retirés, jamais le titre lui-même.
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        self.assertIn('text="💾 Sauvegarde des données"', source)


if __name__ == "__main__":
    unittest.main()
