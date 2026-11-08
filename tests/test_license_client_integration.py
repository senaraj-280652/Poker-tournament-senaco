# -*- coding: utf-8 -*-
"""Lot 5 — test d'intégration bout-en-bout RÉEL : license_client.py
(client Python) contre un vrai serveur de développement PHP servant
public/activer.php/verifier.php (Lot 4), lui-même connecté à une vraie
base MariaDB locale jetable (jamais OVH). Exerce le VRAI chemin réseau
HTTP/JSON que les tests mockés de test_license_client.py ne couvrent
pas.

Ignoré proprement (jamais un échec) si php/mysql ou le module serveur
(~/Desktop/senaco_module_serveur/, hors dépôt) ne sont pas disponibles
sur cette machine — même principe que test_license_token_php_interop.py."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license_client as lc  # noqa: E402
import session_tolerance  # noqa: E402
import trusted_clock  # noqa: E402

_MODULE_SERVEUR = os.path.join(os.path.expanduser("~"), "Desktop", "senaco_module_serveur")
_PHP_BIN = shutil.which("php")
_MYSQL_BIN = shutil.which("mysql")
_PORT = 8931  # jamais 8930 : port couramment utilisé pour une démonstration manuelle
              # locale du panneau d'administration (voir le compte rendu) — une
              # collision ferait silencieusement parler ce test à un AUTRE serveur
              # déjà en écoute, contre la mauvaise base de données, sans jamais
              # lever d'erreur de connexion (le port répond, juste pas avec NOTRE
              # serveur de test) ; découvert et corrigé le 2026-11-05.

_DB_HOST = os.environ.get("SENACO_TEST_DB_HOST", "127.0.0.1")
_DB_PORT = os.environ.get("SENACO_TEST_DB_PORT", "3306")
_DB_USER = os.environ.get("SENACO_TEST_DB_USER", "senaco_test")
_DB_PASSWORD = os.environ.get("SENACO_TEST_DB_PASSWORD", "senaco_test_local_only")
# Incident du 2026-11-08 : ce fichier ciblait jusqu'ici directement le
# nom littéral "senaco_test" (base PERSISTANTE des essais réels V3 sur
# le HP) dans _executer_sql() et le calcul de date d'expiration
# ci-dessous — indépendamment du nom RÉELLEMENT utilisé par la config
# PHP générée (BaseDeTestLocale::creerBaseEtConfiguration(), voir
# setUpClass). Transmis explicitement ici pour ne plus jamais dépendre
# implicitement de la valeur par défaut PHP — voir aussi
# BaseDeTestLocale::NOM_BASE_EPHEMERE_AUTORISE, côté PHP, seule source
# de vérité sur le nom autorisé.
_NOM_BASE_EPHEMERE = os.environ.get("SENACO_TEST_DB_NAME", "senaco_test_auto")


def _mariadb_accessible():
    if not (_PHP_BIN and _MYSQL_BIN and os.path.isdir(_MODULE_SERVEUR)):
        return False
    try:
        resultat = subprocess.run(
            [_MYSQL_BIN, "-h", _DB_HOST, "-P", _DB_PORT, "-u", _DB_USER,
             f"-p{_DB_PASSWORD}", "-e", "SELECT 1"],
            capture_output=True, timeout=5,
        )
        return resultat.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@unittest.skipUnless(_mariadb_accessible(), "php/mysql/module serveur indisponibles sur ce poste")
class LicenceClientBoutEnBoutTest(unittest.TestCase):
    """Un seul scénario complet par test (démarrer un serveur PHP dédié
    par test est coûteux) : setUpClass crée UNE base/config/serveur
    partagés, chaque test utilise un identifiant_machine distinct pour
    rester indépendant sans tout recréer à chaque fois."""

    @classmethod
    def setUpClass(cls):
        env = {**os.environ, "SENACO_TEST_DB_HOST": _DB_HOST, "SENACO_TEST_DB_PORT": _DB_PORT,
               "SENACO_TEST_DB_USER": _DB_USER, "SENACO_TEST_DB_PASSWORD": _DB_PASSWORD,
               "SENACO_TEST_DB_NAME": _NOM_BASE_EPHEMERE}
        resultat = subprocess.run(
            [_PHP_BIN, "-r",
             'require "tests/BaseDeTestLocale.php"; '
             'echo json_encode(["config" => BaseDeTestLocale::creerBaseEtConfiguration()]);'],
            cwd=_MODULE_SERVEUR, capture_output=True, text=True, env=env, timeout=30,
        )
        if resultat.returncode != 0:
            raise RuntimeError(f"Échec de préparation de la base de test : {resultat.stderr}")
        cls.chemin_config = json.loads(resultat.stdout)["config"]

        config_php = subprocess.run(
            [_PHP_BIN, "-r", f'echo json_encode(require {json.dumps(cls.chemin_config)});'],
            capture_output=True, text=True, timeout=10,
        )
        config = json.loads(config_php.stdout)
        cle_privee_hex = config["cle_privee_signature_hex"]

        from nacl.signing import SigningKey
        cls.cle_publique_hex = SigningKey(bytes.fromhex(cle_privee_hex)).verify_key.encode().hex()

        # Garde-fou (ajouté le 2026-11-05, suite à une collision réelle
        # avec un serveur de démonstration manuel déjà lancé sur un
        # port couramment réutilisé) : si quelque chose écoute déjà sur
        # _PORT, la boucle d'attente ci-dessous "réussirait" aussitôt
        # SANS que ce soit notre propre serveur — tous les appels
        # suivants parleraient alors silencieusement à un AUTRE
        # serveur/une autre base, sans jamais lever d'erreur de
        # connexion. Un échec explicite ici vaut mieux qu'une
        # pollution silencieuse d'une base qui n'est pas la nôtre.
        import socket as _socket_precheck
        try:
            with _socket_precheck.create_connection(("127.0.0.1", _PORT), timeout=0.2):
                raise RuntimeError(
                    f"Le port {_PORT} est déjà occupé par un autre processus — ce test "
                    f"refuse de démarrer pour ne jamais parler accidentellement au mauvais "
                    f"serveur (ex. une démonstration manuelle en cours). Libérez ce port ou "
                    f"changez _PORT dans ce fichier."
                )
        except (ConnectionRefusedError, OSError):
            pass  # port libre, comme attendu

        env_serveur = {**os.environ, "SENACO_CONFIG_PATH": cls.chemin_config}
        cls.processus_serveur = subprocess.Popen(
            [_PHP_BIN, "-S", f"127.0.0.1:{_PORT}", "-t", "public/"],
            cwd=_MODULE_SERVEUR, env=env_serveur,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                import socket
                with socket.create_connection(("127.0.0.1", _PORT), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Le serveur PHP de test ne répond pas après 5 secondes.")

    @classmethod
    def tearDownClass(cls):
        cls.processus_serveur.terminate()
        cls.processus_serveur.wait(timeout=5)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="license_client_integration_")
        self.addCleanup(self._tmp.cleanup)

        # Stockage sécurisé local (device_secret.py) : un dictionnaire
        # en mémoire tient lieu de Trousseau/Coffre d'identifiants pour
        # ce test — même "bout en bout réel" qu'il est pour le serveur
        # PHP/MySQL, jamais le vrai Trousseau macOS pendant une suite de
        # tests automatisés (voir tests/test_device_secret.py pour la
        # vérification du VRAI stockage sécurisé, isolément).
        self._secrets_locaux = {}

        def _fausse_lire_secret(identifiant_machine):
            return self._secrets_locaux.get(identifiant_machine)

        def _fausse_enregistrer_secret(identifiant_machine, secret):
            self._secrets_locaux[identifiant_machine] = secret

        patchers = [
            patch.object(lc, "LICENSE_STATE_FILE", os.path.join(self._tmp.name, "licence_v3.json")),
            patch.object(lc, "ROTATION_EN_ATTENTE_FILE", os.path.join(self._tmp.name, "rotation_en_attente.json")),
            patch.object(lc, "LICENSE_DIAGNOSTIC_LOG_FILE", os.path.join(self._tmp.name, "licence_diagnostic.log")),
            patch.object(trusted_clock, "TRUSTED_CLOCK_FILE", os.path.join(self._tmp.name, "horloge.json")),
            patch.object(session_tolerance, "TOLERANCE_STATE_FILE", os.path.join(self._tmp.name, "tolerance.json")),
            patch.dict(os.environ, {
                lc.ENV_URL_SERVEUR: f"http://127.0.0.1:{_PORT}",
                lc.ENV_CLE_PUBLIQUE: self.cle_publique_hex,
            }),
            patch.object(lc.device_secret, "stockage_disponible", return_value=True),
            patch.object(lc.device_secret, "lire_secret_local", side_effect=_fausse_lire_secret),
            patch.object(lc.device_secret, "enregistrer_secret_local", side_effect=_fausse_enregistrer_secret),
        ]
        # Chaque test patche en plus license.machine_id_display() avec
        # un identifiant DISTINCT (voir chaque méthode ci-dessous) —
        # évite toute collision entre tests dans la base partagée,
        # sans avoir à la recréer à chaque fois.
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _executer_sql(self, sql):
        subprocess.run(
            [_MYSQL_BIN, "-h", _DB_HOST, "-P", _DB_PORT, "-u", _DB_USER,
             f"-p{_DB_PASSWORD}", _NOM_BASE_EPHEMERE, "-e", sql],
            capture_output=True, timeout=10, check=True,
        )

    def test_activation_reelle_puis_verification_reelle(self):
        with patch("license.machine_id_display", return_value="AAAA-TEST-0001"):
            decision = lc.activer_en_ligne("Club Intégration", "integration1@example.com")
            self.assertEqual(decision.etat, lc.ETAT_NORMAL)

            decision2 = lc.evaluer_licence()
            self.assertEqual(decision2.etat, lc.ETAT_NORMAL)
            self.assertIsNotNone(decision2.cycle_id)

    def test_verification_reelle_fait_tourner_le_secret_a_chaque_fois(self):
        """Protocole de rotation (validé le 2026-11-06) — bout en bout
        réel : le secret stocké localement change à chaque vérification
        réussie contre le vrai serveur, jamais le même deux fois."""
        with patch("license.machine_id_display", return_value="AAAA-TEST-0020"):
            lc.activer_en_ligne("Club Rotation", "rotation20@example.com")
            secret_apres_activation = self._secrets_locaux["AAAA-TEST-0020"]

            lc.evaluer_licence()
            secret_apres_verif1 = self._secrets_locaux["AAAA-TEST-0020"]
            self.assertNotEqual(secret_apres_activation, secret_apres_verif1)

            lc.evaluer_licence()
            secret_apres_verif2 = self._secrets_locaux["AAAA-TEST-0020"]
            self.assertNotEqual(secret_apres_verif1, secret_apres_verif2)

    def test_reprise_reelle_apres_coupure_reseau_simulee_avec_le_vrai_serveur(self):
        """Simule une vraie coupure réseau survenue APRÈS que le serveur
        a traité la rotation mais AVANT que le client ne reçoive/
        enregistre la réponse : un appel bas niveau identique à celui
        que ferait _post_json est effectué directement ici, sa réponse
        délibérément ignorée (jamais passée à enregistrer_secret_local),
        puis verifier_en_ligne() est appelé normalement avec ce même
        jeton de tentative pré-enregistré — doit récupérer EXACTEMENT
        le même secret auprès du vrai serveur, sans jamais contacter
        OVH ni modifier le secret courant une seconde fois."""
        with patch("license.machine_id_display", return_value="AAAA-TEST-0021"):
            lc.activer_en_ligne("Club Coupure", "coupure21@example.com")

            etat_local = lc._charger_etat_local()
            identifiant_machine = etat_local and lc.license_token.extraire_licence(
                etat_local["jeton"], self.cle_publique_hex
            )["identifiant_machine"]
            secret_local = self._secrets_locaux["AAAA-TEST-0021"]
            jeton_tentative = "ab" * 16

            # Appel réel direct (même requête que verifier_en_ligne
            # enverrait) — rotation réellement effectuée côté serveur,
            # réponse ensuite délibérément ignorée.
            code, reponse = lc._post_json(lc._url_serveur() + "/verifier.php", {
                "identifiant_machine": identifiant_machine,
                "identifiant_machine_frais": identifiant_machine,
                "secret_appareil": secret_local,
                "jeton_tentative": jeton_tentative,
                "dates_session_a_synchroniser": [],
            })
            self.assertEqual(code, 200)
            secret_jamais_enregistre = reponse["secret_appareil"]
            # Le secret local n'a PAS changé : exactement ce qui se
            # passerait après une vraie coupure réseau.
            self.assertEqual(self._secrets_locaux["AAAA-TEST-0021"], secret_local)

            # Reprise : la tentative en attente est pré-enregistrée avec
            # le MÊME jeton, comme si l'appel précédent avait été fait
            # par verifier_en_ligne() lui-même avant la coupure.
            lc._enregistrer_tentative_en_attente(jeton_tentative)
            resultat = lc.verifier_en_ligne()

            self.assertIsNotNone(resultat, "La reprise après coupure réseau ne doit jamais échouer dans la fenêtre de rattrapage.")
            self.assertEqual(
                self._secrets_locaux["AAAA-TEST-0021"], secret_jamais_enregistre,
                "Doit récupérer EXACTEMENT le même secret que celui émis lors de l'appel interrompu.",
            )
            self.assertFalse(
                os.path.exists(os.path.join(self._tmp.name, "rotation_en_attente.json")),
                "La tentative doit être effacée une fois le secret confirmé enregistré.",
            )

    def test_trois_mois_gratuits_date_expiration_exacte(self):
        """Point B (demande du 2026-11-05) : la date d'expiration d'un
        essai gratuit réel doit correspondre EXACTEMENT à la règle
        métier (date_debut + 3 mois calendaires via
        senaco_ajouter_mois_calendaires(), voir prive/lib/licences.php
        — déjà exercée pour les cas particuliers de calendrier, fins de
        mois et passages d'année, dans tests/CalculDatesTest.php côté
        PHP), jamais une approximation (ex. +90 jours). Le calcul
        attendu est obtenu en appelant la VRAIE fonction métier, pas
        une réimplémentation Python qui pourrait diverger silencieusement."""
        with patch("license.machine_id_display", return_value="AAAA-TEST-0008"):
            lc.activer_en_ligne("Club Essai", "integration8@example.com")

        resultat = subprocess.run(
            [_MYSQL_BIN, "-h", _DB_HOST, "-P", _DB_PORT, "-u", _DB_USER,
             f"-p{_DB_PASSWORD}", _NOM_BASE_EPHEMERE, "-N", "-e",
             "SELECT l.date_debut, l.date_expiration FROM licences l "
             "JOIN machines m ON m.id = l.machine_id "
             "WHERE m.identifiant_machine = 'AAAA-TEST-0008';"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        date_debut, date_expiration_reelle = resultat.stdout.strip().split("\t")

        calcul_attendu = subprocess.run(
            [_PHP_BIN, "-r",
             f'require "prive/lib/licences.php"; '
             f'echo senaco_ajouter_mois_calendaires("{date_debut}", 3);'],
            cwd=_MODULE_SERVEUR, capture_output=True, text=True, timeout=10, check=True,
        )
        date_expiration_attendue = calcul_attendu.stdout.strip()

        self.assertEqual(
            date_expiration_reelle, date_expiration_attendue,
            "La date d'expiration de l'essai gratuit doit correspondre exactement "
            "à date_debut + 3 mois calendaires (senaco_ajouter_mois_calendaires), "
            "jamais une approximation.",
        )

    def test_reinstallation_reelle_ne_redonne_pas_un_nouvel_essai(self):
        with patch("license.machine_id_display", return_value="AAAA-TEST-0002"):
            d1 = lc.activer_en_ligne("Club", "integration2@example.com")
            d2 = lc.activer_en_ligne("Club", "integration2@example.com")
            self.assertEqual(d1.cycle_id, d2.cycle_id)

    def test_expiration_reelle_detectee_en_ligne(self):
        with patch("license.machine_id_display", return_value="AAAA-TEST-0003"):
            lc.activer_en_ligne("Club", "integration3@example.com")
            self._executer_sql(
                "UPDATE licences l JOIN machines m ON m.id=l.machine_id "
                "SET l.date_expiration='2020-01-01' "
                "WHERE m.identifiant_machine='AAAA-TEST-0003';"
            )
            decision = lc.evaluer_licence()
            self.assertEqual(decision.etat, lc.ETAT_EXPIRE)
            # La tolérance (Lot 3) doit avoir été amorcée pour ce cycle.
            etat_tolerance = session_tolerance.charger_etat(
                chemin_etat=session_tolerance.TOLERANCE_STATE_FILE,
            )
            self.assertIsNotNone(etat_tolerance)

    def test_revocation_reelle_detectee_en_ligne(self):
        with patch("license.machine_id_display", return_value="AAAA-TEST-0004"):
            lc.activer_en_ligne("Club", "integration4@example.com")
            self._executer_sql(
                "UPDATE licences l JOIN machines m ON m.id=l.machine_id "
                "SET l.statut='revoquee' "
                "WHERE m.identifiant_machine='AAAA-TEST-0004';"
            )
            decision = lc.evaluer_licence()
            self.assertEqual(decision.etat, lc.ETAT_REVOQUE)

    def test_email_invalide_rejete_par_le_vrai_serveur(self):
        with patch("license.machine_id_display", return_value="AAAA-TEST-0005"):
            with self.assertRaises(lc.ErreurActivation):
                lc.activer_en_ligne("Club", "pas-un-email")

    def test_licence_offerte_a_vie_reelle_reste_normale_apres_verification_en_ligne(self):
        """Licences offertes (demande du 2026-11-05) : un vrai
        aller-retour contre le vrai serveur PHP, pour une licence
        offerte à vie (date_expiration = 9999-12-31 en base) — preuve
        que le VRAI verifier.php, pas seulement un mock, renvoie un
        jeton exploitable normalement pour cette date."""
        with patch("license.machine_id_display", return_value="AAAA-TEST-0007"):
            lc.activer_en_ligne("Club", "integration7@example.com")
            self._executer_sql(
                "UPDATE licences l JOIN machines m ON m.id=l.machine_id "
                "SET l.type='offerte', l.date_expiration='9999-12-31' "
                "WHERE m.identifiant_machine='AAAA-TEST-0007';"
            )
            decision = lc.evaluer_licence()
            self.assertEqual(decision.etat, lc.ETAT_NORMAL)

    def test_usurpation_reelle_est_refusee_par_le_vrai_serveur(self):
        """Preuve bout-en-bout du correctif central (demande du
        2026-11-04) : un identifiant de machine déjà activé, présenté
        sans son secret local (simule un tiers qui aurait reproduit cet
        identifiant sans jamais avoir possédé le secret), est refusé
        par le VRAI serveur — jamais un simple mock."""
        with patch("license.machine_id_display", return_value="AAAA-TEST-0006"):
            lc.activer_en_ligne("Club", "integration6@example.com")
            self.assertIn("AAAA-TEST-0006", self._secrets_locaux)

            # Simule la perte/l'absence du secret local (nouvelle
            # tentative sans lui, comme le ferait un tiers).
            del self._secrets_locaux["AAAA-TEST-0006"]
            with self.assertRaises(lc.ErreurIdentificationInsuffisante):
                lc.activer_en_ligne("Club", "integration6@example.com")


if __name__ == "__main__":
    unittest.main()
