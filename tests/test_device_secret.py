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


if __name__ == "__main__":
    unittest.main()
