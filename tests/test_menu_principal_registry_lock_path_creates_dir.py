# -*- coding: utf-8 -*-
"""Non-régression du correctif du 2026-10-22 : premier lancement réel de
Senaco (macOS ou Windows) plantant avec FileNotFoundError dès l'ouverture
de menu_principal_<key>.lock, uniquement quand ~/.poker_tournament
n'existe ENCORE JAMAIS — reproduit à l'identique depuis les sources et
depuis un .app PyInstaller packagé (Senaco-1.2.45-macOS-x86_64.dmg).

Cause exacte : _menu_principal_registry_lock_path (open_windows.py)
construisait ce chemin sans jamais créer son dossier parent au
préalable — contrairement à ses deux fonctions sœurs utilisant le même
mécanisme (_registry_lock_path, _remote_control_dir), qui appellent
toutes deux os.makedirs(..., exist_ok=True) depuis longtemps sans
incident. La suite de tests existante (tests/test_menu_principal_
singleton.py et consorts) ne l'avait jamais détecté car elle remplace
systématiquement cette fonction par un faux chemin pointant dans un
tempfile.TemporaryDirectory() — qui CRÉE déjà son propre dossier —
masquant ainsi le vrai comportement de la fonction en situation réelle.
Ce fichier teste directement l'implémentation réelle, jamais une
doublure."""
import os
import stat
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


class _FakeHomeTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="menu_principal_lock_dir_test_")
        self.addCleanup(self._tmp.cleanup)
        # Le TemporaryDirectory lui-même existe déjà (comme le VRAI
        # dossier personnel existe toujours) — c'est son sous-dossier
        # .poker_tournament qui doit être absent au départ, exactement
        # comme sur un poste n'ayant jamais lancé Senaco.
        self.fake_home = os.path.join(self._tmp.name, "fake_home")
        os.makedirs(self.fake_home, exist_ok=True)
        self.poker_dir = os.path.join(self.fake_home, ".poker_tournament")
        patcher = patch.object(os.path, "expanduser", return_value=self.fake_home)
        self.addCleanup(patcher.stop)
        patcher.start()


class PremierLancementDossierAbsentTest(_FakeHomeTestCase):
    def test_le_dossier_poker_tournament_est_cree_automatiquement(self):
        """Cœur du correctif : ~/.poker_tournament n'existe PAS du
        tout — reproduit exactement le plantage réel du 2026-10-22."""
        self.assertFalse(os.path.isdir(self.poker_dir))

        path = open_windows._menu_principal_registry_lock_path("prod")

        self.assertTrue(os.path.isdir(self.poker_dir), "le dossier doit être créé par la fonction elle-même")
        self.assertEqual(path, os.path.join(self.poker_dir, "menu_principal_prod.lock"))

    def test_le_verrou_est_reellement_utilisable_juste_apres(self):
        """Pas seulement le chemin/dossier : le verrou lui-même doit
        pouvoir s'acquérir et se relâcher sans exception, exactement
        comme lors d'un lancement normal sur un poste déjà utilisé."""
        path = open_windows._menu_principal_registry_lock_path("prod")
        with open_windows._file_lock(path):
            pass  # ne doit lever aucune exception

    def test_fonctionne_identiquement_pour_la_ligne_test(self):
        """La clé "test" (Poker Senaco TEST, séparée de "prod") suit
        exactement le même chemin de code — jamais un cas particulier
        oublié."""
        path = open_windows._menu_principal_registry_lock_path("test")
        self.assertTrue(os.path.isdir(self.poker_dir))
        self.assertEqual(path, os.path.join(self.poker_dir, "menu_principal_test.lock"))


class DossierDejaExistantTest(_FakeHomeTestCase):
    def test_dossier_deja_present_ne_pose_aucun_probleme(self):
        """Non-régression explicite : le cas normal (poste déjà utilisé,
        ~/.poker_tournament déjà présent) continue de fonctionner à
        l'identique — exist_ok=True ne doit jamais lever sur un dossier
        déjà là."""
        os.makedirs(self.poker_dir, exist_ok=True)
        marker = os.path.join(self.poker_dir, "deja_present.marker")
        open(marker, "w", encoding="utf-8").close()

        path = open_windows._menu_principal_registry_lock_path("prod")

        self.assertEqual(path, os.path.join(self.poker_dir, "menu_principal_prod.lock"))
        self.assertTrue(os.path.exists(marker), "le contenu déjà présent ne doit jamais être touché")


class VerrouillageExclusifPreserveTest(_FakeHomeTestCase):
    def test_deux_acquisitions_sequentielles_fonctionnent_normalement(self):
        """Non-régression du fonctionnement normal du verrou (pas
        seulement sa création) : acquérir puis relâcher, deux fois de
        suite, sur le chemin désormais garanti existant."""
        path = open_windows._menu_principal_registry_lock_path("prod")
        with open_windows._file_lock(path):
            pass
        with open_windows._file_lock(path):
            pass  # le second passage ne doit pas être bloqué par le premier déjà relâché

    def test_exclusion_mutuelle_toujours_effective(self):
        """Cœur de la prévention des doubles instances : tant qu'un
        premier détenteur garde le verrou, un second thread qui tente de
        l'acquérir sur le MÊME chemin doit rester bloqué — exactement le
        comportement attendu avant ce correctif, qui ne change RIEN à
        _file_lock/_acquire_lock/_release_lock eux-mêmes."""
        path = open_windows._menu_principal_registry_lock_path("prod")
        first_holds = threading.Event()
        release_first = threading.Event()
        second_acquired = threading.Event()

        def hold_first():
            with open_windows._file_lock(path):
                first_holds.set()
                release_first.wait(timeout=5)

        def try_second():
            first_holds.wait(timeout=5)
            with open_windows._file_lock(path, timeout=5.0):
                second_acquired.set()

        t1 = threading.Thread(target=hold_first)
        t2 = threading.Thread(target=try_second)
        t1.start()
        self.assertTrue(first_holds.wait(timeout=5))

        t2.start()
        # Tant que le premier ne relâche pas, le second ne doit PAS avoir pu entrer.
        self.assertFalse(second_acquired.wait(timeout=0.5))

        release_first.set()
        t1.join(timeout=5)
        # Une fois relâché, le second doit alors réussir à son tour.
        self.assertTrue(second_acquired.wait(timeout=5))
        t2.join(timeout=5)


class DossierInaccessibleTest(_FakeHomeTestCase):
    def test_dossier_parent_inaccessible_leve_une_erreur_claire(self):
        """Dossier personnel existant mais dont les permissions
        empêchent toute création à l'intérieur (cas réel possible :
        permissions restreintes, volume partagé en lecture seule...) —
        doit lever une erreur claire et standard (PermissionError, une
        sous-classe d'OSError), jamais une exception masquée ou d'un
        autre type déroutant. Comportement IDENTIQUE à celui des deux
        fonctions sœurs déjà en place (_registry_lock_path,
        _remote_control_dir), qui n'interceptent pas davantage cette
        erreur — ce correctif ne change rien à cette politique
        existante, il l'applique simplement de façon cohérente."""
        os.chmod(self.fake_home, stat.S_IRUSR | stat.S_IXUSR)  # lecture + parcours seuls
        self.addCleanup(os.chmod, self.fake_home, stat.S_IRWXU)

        with self.assertRaises(PermissionError):
            open_windows._menu_principal_registry_lock_path("prod")


if __name__ == "__main__":
    unittest.main()
