# -*- coding: utf-8 -*-
"""Tests ciblés pour backup_history.py (garde-fou "Purger Données",
demande du 2026-10-07) — historique LOCAL des sauvegardes RÉUSSIES,
testé uniquement avec un ~/.poker_tournament SYNTHÉTIQUE (_history_dir
patché vers un dossier temporaire ci-dessous) — jamais le vrai dossier
de l'utilisateur.

Ne couvre PAS la revérification réelle de couverture (dossier encore
accessible, hash recalculé...) : voir tests/test_data_purge.py,
CheckPurgeCoverageTest — backup_history.py est PUREMENT déclaratif (voir
sa propre docstring de module)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import backup_history  # noqa: E402
import backup_restore  # noqa: E402


class RecordAndLoadHistoryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="backup_history_test_")
        self.addCleanup(self._tmp.cleanup)
        patcher = patch.object(backup_history, "_history_dir", return_value=self._tmp.name)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_load_history_vide_si_fichier_absent(self):
        self.assertEqual(backup_history.load_history(), [])

    def test_record_puis_load_round_trip(self):
        manifest = {
            "created_at": "2026-10-07T13:42:21",
            "tournament_files": ["Vendredi/To010126.tournoi"],
            "poker_tournament_data_included": True,
        }
        backup_history.record_backup("/fake/backup/dir", manifest)
        history = backup_history.load_history()
        self.assertEqual(len(history), 1)
        entry = history[0]
        self.assertEqual(entry["backup_dir"], "/fake/backup/dir")
        self.assertEqual(entry["tournament_files"], ["Vendredi/To010126.tournoi"])
        self.assertTrue(entry["poker_tournament_data_included"])
        self.assertEqual(entry["created_at"], "2026-10-07T13:42:21")
        self.assertIn(
            entry["destination_kind"],
            (backup_history.DESTINATION_REMOVABLE, backup_history.DESTINATION_FIXED, backup_history.DESTINATION_UNKNOWN),
        )

    def test_entree_reconstruite_depuis_le_manifeste_jamais_une_source_separee(self):
        """record_backup ne doit jamais inventer/compléter des champs
        absents du manifeste fourni (voir sa docstring) — un manifeste
        minimal produit une entrée tout aussi minimale, sans valeur par
        défaut surprenante pour tournament_files/poker_tournament_data_
        included."""
        backup_history.record_backup("/fake/dir", {"created_at": "t0"})
        entry = backup_history.load_history()[0]
        self.assertEqual(entry["tournament_files"], [])
        self.assertFalse(entry["poker_tournament_data_included"])

    def test_plusieurs_sauvegardes_saccumulent_dans_lordre_chronologique(self):
        for i in range(3):
            backup_history.record_backup(f"/fake/dir{i}", {"created_at": f"t{i}", "tournament_files": []})
        history = backup_history.load_history()
        self.assertEqual([h["backup_dir"] for h in history], ["/fake/dir0", "/fake/dir1", "/fake/dir2"])

    def test_ecriture_atomique_jamais_de_fichier_temporaire_residuel(self):
        backup_history.record_backup("/fake/dir", {"created_at": "t0", "tournament_files": []})
        backup_history.record_backup("/fake/dir2", {"created_at": "t1", "tournament_files": []})
        path = backup_history._history_path()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)  # lève si le JSON est tronqué/corrompu
        self.assertEqual(len(data), 2)
        leftover = [n for n in os.listdir(self._tmp.name) if n.endswith(".tmp")]
        self.assertEqual(leftover, [])

    def test_fichier_corrompu_traite_comme_historique_vide_jamais_une_exception(self):
        path = backup_history._history_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du JSON valide")
        self.assertEqual(backup_history.load_history(), [])


class DetectDestinationKindTest(unittest.TestCase):
    """Best-effort, PUREMENT informatif (voir la docstring du module) —
    ne doit jamais lever d'exception, quel que soit le chemin/l'OS."""

    def test_jamais_dexception_sur_un_chemin_inexistant(self):
        result = backup_history._detect_destination_kind("/chemin/qui/n/existe/pas")
        self.assertIn(
            result,
            (backup_history.DESTINATION_REMOVABLE, backup_history.DESTINATION_FIXED, backup_history.DESTINATION_UNKNOWN),
        )

    @patch("backup_history.platform.system", return_value="Darwin")
    def test_volumes_detecte_comme_amovible_sur_mac(self, _mock_system):
        self.assertEqual(
            backup_history._detect_destination_kind("/Volumes/MA_CLE/backup"), backup_history.DESTINATION_REMOVABLE,
        )

    @patch("backup_history.platform.system", return_value="Darwin")
    def test_hors_volumes_detecte_comme_fixe_sur_mac(self, _mock_system):
        self.assertEqual(
            backup_history._detect_destination_kind("/Users/raj/backup"), backup_history.DESTINATION_FIXED,
        )

    # os.path.splitdrive est lui-même spécifique à l'OS RÉEL (posixpath
    # ne reconnaît aucune lettre de lecteur, même en simulant Windows via
    # platform.system) — patché ici en plus de ctypes.windll pour
    # pouvoir exercer ce chemin Windows depuis une machine de
    # développement Mac.
    @patch("backup_history.platform.system", return_value="Windows")
    @patch("backup_history.os.path.splitdrive", return_value=("E:", "\\backup"))
    def test_windows_drive_type_removable(self, _mock_splitdrive, _mock_system):
        fake_kernel32 = Mock()
        fake_kernel32.GetDriveTypeW.return_value = 2  # DRIVE_REMOVABLE
        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.kernel32 = fake_kernel32
            self.assertEqual(
                backup_history._detect_destination_kind("E:\\backup"), backup_history.DESTINATION_REMOVABLE,
            )

    @patch("backup_history.platform.system", return_value="Windows")
    @patch("backup_history.os.path.splitdrive", return_value=("C:", "\\backup"))
    def test_windows_drive_type_fixed(self, _mock_splitdrive, _mock_system):
        fake_kernel32 = Mock()
        fake_kernel32.GetDriveTypeW.return_value = 3  # DRIVE_FIXED
        with patch("ctypes.windll", create=True) as mock_windll:
            mock_windll.kernel32 = fake_kernel32
            self.assertEqual(
                backup_history._detect_destination_kind("C:\\backup"), backup_history.DESTINATION_FIXED,
            )

    @patch("backup_history.platform.system", side_effect=Exception("boom"))
    def test_toute_erreur_renvoie_inconnu_jamais_une_exception(self, _mock_system):
        self.assertEqual(
            backup_history._detect_destination_kind("/peu/importe"), backup_history.DESTINATION_UNKNOWN,
        )


class RestoreExclusionInteractionTest(unittest.TestCase):
    """Lien croisé avec backup_restore.py (demande explicite : ce fichier
    doit survivre à une restauration) — voir aussi tests/
    test_backup_restore.py:CopyTreeExclusionUnitTest, qui teste le
    frozenset complet."""

    def test_history_filename_est_bien_exclu_de_la_restauration(self):
        self.assertIn(backup_history.HISTORY_FILENAME, backup_restore.RESTORE_EXCLUDED_DATA_FILENAMES)


if __name__ == "__main__":
    unittest.main()
