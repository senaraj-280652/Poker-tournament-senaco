# -*- coding: utf-8 -*-
"""Tests de device_secret.py (sécurité, demande du 2026-11-04) —
stockage local sécurisé du secret d'appariement par appareil.

La suite principale (DeviceSecretTest) utilise un FAUX trousseau en
mémoire (jamais le vrai Trousseau macOS/Coffre Windows pendant une
suite automatisée — rapide, déterministe, isolé). Un test complémentaire
(TrousseauReelTest) vérifie le VRAI backend du système, mais seulement
s'il est réellement utilisable sur cette machine (ignoré proprement
sinon, jamais un échec) — même principe que test_license_token_php_
interop.py pour PHP."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import keyring  # noqa: E402
import keyring.backend  # noqa: E402
import keyring.errors  # noqa: E402

import device_secret  # noqa: E402


class _FauxTrousseauEnMemoire(keyring.backend.KeyringBackend):
    """Backend keyring jetable, purement en mémoire — jamais écrit sur
    le disque ni dans un vrai trousseau système."""

    priority = 1

    def __init__(self):
        super().__init__()
        self._stockage = {}

    def set_password(self, servicename, username, password):
        self._stockage[(servicename, username)] = password

    def get_password(self, servicename, username):
        return self._stockage.get((servicename, username))

    def delete_password(self, servicename, username):
        cle = (servicename, username)
        if cle not in self._stockage:
            raise keyring.errors.PasswordDeleteError("Mot de passe absent.")
        del self._stockage[cle]


class DeviceSecretTest(unittest.TestCase):
    def setUp(self):
        self._ancien_trousseau = keyring.get_keyring()
        self._faux_trousseau = _FauxTrousseauEnMemoire()
        keyring.set_keyring(self._faux_trousseau)
        self.addCleanup(keyring.set_keyring, self._ancien_trousseau)

    def test_lire_secret_absent_renvoie_none(self):
        self.assertIsNone(device_secret.lire_secret_local("MACHINE-INCONNUE"))

    def test_enregistrer_puis_lire_fait_un_aller_retour_fidele(self):
        device_secret.enregistrer_secret_local("MACHINE-0001", "a" * 64)
        self.assertEqual(device_secret.lire_secret_local("MACHINE-0001"), "a" * 64)

    def test_deux_machines_distinctes_ont_des_secrets_independants(self):
        device_secret.enregistrer_secret_local("MACHINE-0001", "a" * 64)
        device_secret.enregistrer_secret_local("MACHINE-0002", "b" * 64)
        self.assertEqual(device_secret.lire_secret_local("MACHINE-0001"), "a" * 64)
        self.assertEqual(device_secret.lire_secret_local("MACHINE-0002"), "b" * 64)

    def test_reenregistrer_remplace_lancien_secret(self):
        device_secret.enregistrer_secret_local("MACHINE-0001", "a" * 64)
        device_secret.enregistrer_secret_local("MACHINE-0001", "c" * 64)
        self.assertEqual(device_secret.lire_secret_local("MACHINE-0001"), "c" * 64)

    def test_effacer_secret_absent_ne_leve_pas(self):
        try:
            device_secret.effacer_secret_local("MACHINE-JAMAIS-ENREGISTREE")
        except Exception as e:  # pragma: no cover
            self.fail(f"effacer_secret_local() a levé une exception sur une machine inconnue : {e}")

    def test_effacer_secret_existant_le_supprime(self):
        device_secret.enregistrer_secret_local("MACHINE-0001", "a" * 64)
        device_secret.effacer_secret_local("MACHINE-0001")
        self.assertIsNone(device_secret.lire_secret_local("MACHINE-0001"))

    def test_stockage_disponible_est_vrai_avec_un_trousseau_fonctionnel(self):
        self.assertTrue(device_secret.stockage_disponible())

    def test_stockage_disponible_ne_laisse_aucune_trace(self):
        device_secret.stockage_disponible()
        # Le compte de test interne ne doit jamais apparaître comme un
        # "vrai" secret d'appareil résiduel.
        self.assertIsNone(device_secret.lire_secret_local("_test_disponibilite_"))

    def test_enregistrer_leve_erreurstockagesecret_si_le_trousseau_echoue(self):
        class _TrousseauCasse(keyring.backend.KeyringBackend):
            priority = 1

            def set_password(self, servicename, username, password):
                raise keyring.errors.PasswordSetError("verrouillé")

            def get_password(self, servicename, username):
                return None

            def delete_password(self, servicename, username):
                raise keyring.errors.PasswordDeleteError("verrouillé")

        keyring.set_keyring(_TrousseauCasse())
        with self.assertRaises(device_secret.ErreurStockageSecret):
            device_secret.enregistrer_secret_local("MACHINE-0001", "a" * 64)

    def test_stockage_disponible_est_faux_si_le_trousseau_echoue(self):
        class _TrousseauCasse(keyring.backend.KeyringBackend):
            priority = 1

            def set_password(self, servicename, username, password):
                raise keyring.errors.PasswordSetError("verrouillé")

            def get_password(self, servicename, username):
                return None

            def delete_password(self, servicename, username):
                raise keyring.errors.PasswordDeleteError("verrouillé")

        keyring.set_keyring(_TrousseauCasse())
        self.assertFalse(device_secret.stockage_disponible())

    def test_lire_secret_renvoie_none_si_le_trousseau_echoue(self):
        class _TrousseauCasse(keyring.backend.KeyringBackend):
            priority = 1

            def set_password(self, servicename, username, password):
                raise keyring.errors.PasswordSetError("verrouillé")

            def get_password(self, servicename, username):
                raise keyring.errors.KeyringError("verrouillé")

            def delete_password(self, servicename, username):
                raise keyring.errors.PasswordDeleteError("verrouillé")

        keyring.set_keyring(_TrousseauCasse())
        self.assertIsNone(device_secret.lire_secret_local("MACHINE-0001"))


def _vrai_trousseau_utilisable():
    """Sonde légère : le vrai backend système peut-il réellement faire
    un aller-retour écriture/lecture/suppression sur CETTE machine ?
    Jamais appelée hors de ce fichier de test."""
    ancien = keyring.get_keyring()
    try:
        compte = "_sonde_disponibilite_test_"
        valeur = "sonde-123"
        keyring.set_password(device_secret.NOM_SERVICE, compte, valeur)
        ok = keyring.get_password(device_secret.NOM_SERVICE, compte) == valeur
        keyring.delete_password(device_secret.NOM_SERVICE, compte)
        return ok
    except Exception:
        return False
    finally:
        keyring.set_keyring(ancien)


@unittest.skipUnless(
    _vrai_trousseau_utilisable(),
    "Trousseau/coffre d'identifiants système réellement indisponible sur ce poste",
)
class TrousseauReelTest(unittest.TestCase):
    """Vérifie le VRAI backend du système d'exploitation (Trousseau
    macOS confirmé ici ; Coffre d'identifiants Windows NON vérifiable
    depuis cette machine — voir le compte rendu). Utilise un identifiant
    de machine clairement marqué comme test et nettoie systématiquement
    après chaque test, pour ne jamais laisser de trace dans le vrai
    trousseau de l'utilisateur."""

    IDENTIFIANT_TEST = "_test_device_secret_py_ne_jamais_laisser_"

    def tearDown(self):
        device_secret.effacer_secret_local(self.IDENTIFIANT_TEST)

    def test_aller_retour_reel_fonctionne(self):
        device_secret.enregistrer_secret_local(self.IDENTIFIANT_TEST, "d" * 64)
        self.assertEqual(device_secret.lire_secret_local(self.IDENTIFIANT_TEST), "d" * 64)

    def test_stockage_disponible_reel_est_vrai(self):
        self.assertTrue(device_secret.stockage_disponible())


class NomServiceTestVsProductionTest(unittest.TestCase):
    """Correctif du 2026-11-13 (préparation de la version TEST macOS,
    isolation complète des licences) : un secret d'appareil de build
    TEST ne doit jamais cohabiter avec un vrai, dans le même
    emplacement sécurisé du système — voir device_secret._est_un_build_
    test() (détection dupliquée, jamais une dépendance vers main.py,
    même principe que les verrous de fichier ailleurs dans ce dépôt)."""

    def test_absent_par_defaut(self):
        """Dépôt de développement (aucun TEST_BUILD_MARKER à la racine) :
        comportement de production inchangé."""
        self.assertFalse(device_secret._est_un_build_test())

    def test_vrai_si_marqueur_present_dans_meipass(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_test_") as d:
            open(os.path.join(d, "TEST_BUILD_MARKER"), "w").close()
            with patch.object(device_secret.sys, "_MEIPASS", d, create=True):
                self.assertTrue(device_secret._est_un_build_test())

    def test_faux_si_meipass_sans_marqueur(self):
        import tempfile
        with tempfile.TemporaryDirectory(prefix="fake_meipass_prod_") as d:
            with patch.object(device_secret.sys, "_MEIPASS", d, create=True):
                self.assertFalse(device_secret._est_un_build_test())

    def test_nom_service_coherent_avec_la_detection_actuelle(self):
        """NOM_SERVICE est une constante de MODULE (calculée une seule
        fois à l'import, comme tous les chemins ~/.poker_tournament
        ailleurs dans ce dépôt) — ce test confirme que le câblage
        "TEST -> 'Senaco-TEST', sinon -> 'Senaco'" correspond bien à
        l'état RÉEL de ce process, pas une valeur figée en dur."""
        attendu = "Senaco-TEST" if device_secret._est_un_build_test() else "Senaco"
        self.assertEqual(device_secret.NOM_SERVICE, attendu)

    def test_jamais_le_meme_nom_que_la_production(self):
        """Garde-fou explicite : quel que soit l'état de détection,
        NOM_SERVICE ne vaut 'Senaco' QUE si ce n'est PAS un build TEST —
        jamais une cohabitation involontaire dans le même espace de noms
        du Trousseau/Coffre."""
        if device_secret._est_un_build_test():
            self.assertEqual(device_secret.NOM_SERVICE, "Senaco-TEST")
            self.assertNotEqual(device_secret.NOM_SERVICE, "Senaco")


class VraiHomeTemporairementTest(unittest.TestCase):
    """Correctif du 2026-11-13 (diagnostic « Trousseau introuvable » sur
    macOS TEST, reproduit et confirmé empiriquement avant correction) —
    device_secret._vrai_home_temporairement() : Security.framework (via
    NSHomeDirectory(), utilisé par keyring.backends.macOS) s'appuie sur
    HOME pour localiser le Trousseau de connexion réel ; sans restaurer
    temporairement le VRAI HOME, la redirection vers ~/SenacoTEST (voir
    main._rediriger_home_test_macos_si_necessaire) empêche de le
    retrouver."""

    def setUp(self):
        self._home_original = os.environ.get("HOME")
        self.addCleanup(self._restaurer_home)

    def _restaurer_home(self):
        if self._home_original is None:
            os.environ.pop("HOME", None)
        else:
            os.environ["HOME"] = self._home_original

    @unittest.skipIf(sys.platform == "win32", "pwd indisponible sur Windows — mécanisme volontairement macOS uniquement")
    def test_restaure_le_vrai_home_pendant_l_appel_puis_revient_a_la_valeur_precedente(self):
        os.environ["HOME"] = "/Users/quelquun/SenacoTEST"
        with patch.object(device_secret.sys, "platform", "darwin"), \
                patch.object(device_secret, "_est_un_build_test", return_value=True), \
                patch("pwd.getpwuid") as mock_getpwuid:
            mock_getpwuid.return_value.pw_dir = "/Users/quelquun"
            with device_secret._vrai_home_temporairement():
                self.assertEqual(os.environ["HOME"], "/Users/quelquun")
            self.assertEqual(os.environ["HOME"], "/Users/quelquun/SenacoTEST")

    @unittest.skipIf(sys.platform == "win32", "pwd indisponible sur Windows — mécanisme volontairement macOS uniquement")
    def test_ne_laisse_pas_home_defini_si_absent_avant_l_appel(self):
        """Asymétrie relevée le 2026-11-14 (vérification post-push) :
        si HOME n'était pas défini du tout avant l'appel, le laisser au
        vrai dossier personnel après coup serait une fuite d'état —
        HOME doit redevenir ABSENT, pas seulement « une autre valeur »."""
        os.environ.pop("HOME", None)
        with patch.object(device_secret.sys, "platform", "darwin"), \
                patch.object(device_secret, "_est_un_build_test", return_value=True), \
                patch("pwd.getpwuid") as mock_getpwuid:
            mock_getpwuid.return_value.pw_dir = "/Users/quelquun"
            with device_secret._vrai_home_temporairement():
                self.assertEqual(os.environ["HOME"], "/Users/quelquun")
            self.assertNotIn("HOME", os.environ)

    @unittest.skipIf(sys.platform == "win32", "pwd indisponible sur Windows — mécanisme volontairement macOS uniquement")
    def test_restaure_meme_en_cas_d_exception(self):
        """Précaution explicite demandée le 2026-11-13 : un échec pendant
        l'appel Trousseau ne doit jamais laisser HOME bloqué sur le vrai
        dossier (ce qui casserait l'isolation des processus enfants
        lancés ensuite)."""
        os.environ["HOME"] = "/Users/quelquun/SenacoTEST"
        with patch.object(device_secret.sys, "platform", "darwin"), \
                patch.object(device_secret, "_est_un_build_test", return_value=True), \
                patch("pwd.getpwuid") as mock_getpwuid:
            mock_getpwuid.return_value.pw_dir = "/Users/quelquun"
            with self.assertRaises(ValueError):
                with device_secret._vrai_home_temporairement():
                    self.assertEqual(os.environ["HOME"], "/Users/quelquun")
                    raise ValueError("echec simule pendant l'appel Trousseau")
        self.assertEqual(os.environ["HOME"], "/Users/quelquun/SenacoTEST")

    def test_jamais_hors_build_test_meme_sur_macos(self):
        os.environ["HOME"] = "/Users/quelquun/SenacoTEST"
        with patch.object(device_secret.sys, "platform", "darwin"), \
                patch.object(device_secret, "_est_un_build_test", return_value=False):
            with device_secret._vrai_home_temporairement():
                self.assertEqual(os.environ["HOME"], "/Users/quelquun/SenacoTEST")

    def test_jamais_sur_windows_meme_en_build_test(self):
        """Précaution explicite demandée le 2026-11-13 : Windows et macOS
        stable ne doivent voir aucun changement de comportement."""
        os.environ["HOME"] = "/Users/quelquun/SenacoTEST"
        with patch.object(device_secret.sys, "platform", "win32"), \
                patch.object(device_secret, "_est_un_build_test", return_value=True):
            with device_secret._vrai_home_temporairement():
                self.assertEqual(os.environ["HOME"], "/Users/quelquun/SenacoTEST")

    @unittest.skipIf(sys.platform == "win32", "pwd indisponible sur Windows — mécanisme volontairement macOS uniquement")
    def test_appels_concurrents_jamais_entrelaces(self):
        """Vérifie le verrou _verrou_home demandé le 2026-11-13 (examen du
        risque de concurrence) : deux entrées concurrentes sont
        sérialisées — sans lui, un thread pourrait restaurer HOME à la
        mauvaise valeur pendant que l'autre croit toujours être dans sa
        propre fenêtre de VRAI HOME."""
        import threading
        import time

        os.environ["HOME"] = "/Users/quelquun/SenacoTEST"
        releves = []

        def pwd_fake(_uid):
            class _P:
                pw_dir = "/Users/quelquun"
            return _P()

        with patch.object(device_secret.sys, "platform", "darwin"), \
                patch.object(device_secret, "_est_un_build_test", return_value=True), \
                patch("pwd.getpwuid", side_effect=pwd_fake):

            def worker():
                with device_secret._vrai_home_temporairement():
                    releves.append(os.environ["HOME"])
                    time.sleep(0.05)
                    releves.append(os.environ["HOME"])

            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            time.sleep(0.01)
            t2.start()
            t1.join()
            t2.join()

        # Jamais d'entrelacement : chaque thread doit avoir vu le VRAI
        # home pendant toute la durée de son propre passage.
        self.assertTrue(releves)
        for valeur in releves:
            self.assertEqual(valeur, "/Users/quelquun")
        self.assertEqual(os.environ["HOME"], "/Users/quelquun/SenacoTEST")


if __name__ == "__main__":
    unittest.main()
