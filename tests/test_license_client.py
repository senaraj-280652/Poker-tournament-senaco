# -*- coding: utf-8 -*-
"""Lot 5 du système de licences Senaco v3 (voir license_client.py) :
orchestration client réelle — appels réseau (mockés ici ; voir
tests/test_license_client_integration.py pour de vrais appels contre
le serveur de test local du Lot 4), matrice de décision Internet x
validité, et câblage avec les Lots 1-3.

DORMANT : ce module n'est appelé par main.py que si
POKER_TOURNAMENT_LICENSE_V3 est défini — ces tests ne vérifient que le
module lui-même, jamais l'intégration dans l'application (voir le test
structurel en fin de fichier)."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license_client as lc  # noqa: E402
import license_token  # noqa: E402
import session_tolerance  # noqa: E402
import trusted_clock  # noqa: E402

IDENTIFIANT_MACHINE_TEST = "A1B2-C3D4-E5F6-0789"  # identifiant_machine par défaut de _jeton_test()


def _jeton_test(cle_privee_hex, **overrides):
    champs = {
        "id_jeton": "cycle-test-1",
        "identifiant_machine": "A1B2-C3D4-E5F6-0789",
        "type": "essai",
        "date_debut": "2026-11-01",
        "date_expiration": "2027-02-01",
        "emis_le": "2026-11-01T10:00:00Z",
    }
    champs.update(overrides)
    return license_token.construire_jeton_licence(champs, cle_privee_hex)


class EnvironnementTestCase(unittest.TestCase):
    """Isole chaque test : fichiers d'état dans un dossier temporaire,
    clé de test dédiée, jamais les vrais fichiers de l'utilisateur."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="license_client_test_")
        self.addCleanup(self._tmp.cleanup)
        self.chemin_licence = os.path.join(self._tmp.name, "licence_v3.json")
        self.chemin_horloge = os.path.join(self._tmp.name, "horloge_fiable.json")
        self.chemin_tolerance = os.path.join(self._tmp.name, "tolerance_sessions.json")

        self.cle_privee, self.cle_publique = license_token.generer_cle_de_test()

        # Stockage sécurisé local (device_secret.py) : JAMAIS le vrai
        # Trousseau/Coffre d'identifiants pendant les tests — un
        # dictionnaire en mémoire fait exactement le même office pour
        # ces tests, qui ne vérifient pas device_secret.py lui-même
        # (voir tests/test_device_secret.py pour ça), seulement que
        # license_client.py l'utilise correctement. Pré-rempli avec un
        # secret pour l'identifiant de machine par défaut de
        # _jeton_test(), pour que les tests existants (écrits avant ce
        # correctif) continuent de représenter une machine déjà
        # correctement appariée, sans avoir chacun à le répéter.
        self._secrets_locaux = {IDENTIFIANT_MACHINE_TEST: "0" * 64}

        def _fausse_lire_secret(identifiant_machine):
            return self._secrets_locaux.get(identifiant_machine)

        def _fausse_enregistrer_secret(identifiant_machine, secret):
            self._secrets_locaux[identifiant_machine] = secret

        patchers = [
            patch.object(lc, "LICENSE_STATE_FILE", self.chemin_licence),
            patch.object(trusted_clock, "TRUSTED_CLOCK_FILE", self.chemin_horloge),
            patch.object(session_tolerance, "TOLERANCE_STATE_FILE", self.chemin_tolerance),
            patch.dict(os.environ, {
                lc.ENV_URL_SERVEUR: "https://test.invalide/",
                lc.ENV_CLE_PUBLIQUE: self.cle_publique,
            }),
            patch.object(lc.device_secret, "stockage_disponible", return_value=True),
            patch.object(lc.device_secret, "lire_secret_local", side_effect=_fausse_lire_secret),
            patch.object(lc.device_secret, "enregistrer_secret_local", side_effect=_fausse_enregistrer_secret),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    def _ecrire_etat_local(self, jeton):
        lc._enregistrer_etat_local(jeton, chemin=self.chemin_licence)


class PostJsonTest(unittest.TestCase):
    """_post_json() : mécanique réseau bas niveau, jamais d'exception."""

    @patch("urllib.request.urlopen")
    def test_succes_renvoie_code_et_corps(self, mock_urlopen):
        reponse_mock = MagicMock()
        reponse_mock.status = 200
        reponse_mock.read.return_value = json.dumps({"ok": True}).encode("utf-8")
        reponse_mock.__enter__.return_value = reponse_mock
        mock_urlopen.return_value = reponse_mock

        code, corps = lc._post_json("https://exemple.test/x", {"a": 1})
        self.assertEqual(code, 200)
        self.assertEqual(corps, {"ok": True})

    @patch("urllib.request.urlopen")
    def test_httperror_avec_corps_json_est_transmis(self, mock_urlopen):
        import urllib.error
        corps_erreur = json.dumps({"erreur": "invalide"}).encode("utf-8")
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://exemple.test/x", 400, "Bad Request", {}, io_fp(corps_erreur),
        )
        code, corps = lc._post_json("https://exemple.test/x", {})
        self.assertEqual(code, 400)
        self.assertEqual(corps, {"erreur": "invalide"})

    @patch("urllib.request.urlopen")
    def test_pas_de_reseau_renvoie_zero_none(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("pas de réseau")
        code, corps = lc._post_json("https://exemple.test/x", {})
        self.assertEqual((code, corps), (0, None))

    @patch("urllib.request.urlopen")
    def test_timeout_renvoie_zero_none(self, mock_urlopen):
        mock_urlopen.side_effect = TimeoutError("délai dépassé")
        code, corps = lc._post_json("https://exemple.test/x", {})
        self.assertEqual((code, corps), (0, None))


def io_fp(contenu_bytes):
    import io
    return io.BytesIO(contenu_bytes)


class ActiverEnLigneTest(EnvironnementTestCase):
    @patch.object(lc, "_post_json")
    def test_succes_enregistre_letat_local(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-01T10:00:00Z",
        })
        decision = lc.activer_en_ligne("Club Test", "club@example.com")
        self.assertEqual(decision.etat, lc.ETAT_NORMAL)
        etat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertEqual(etat["jeton"], jeton)

    @patch.object(lc, "_post_json")
    def test_refus_serveur_leve_erreur_activation(self, mock_post):
        mock_post.return_value = (400, {"erreur": "email invalide"})
        with self.assertRaises(lc.ErreurActivation):
            lc.activer_en_ligne("Club Test", "pas-un-email")
        self.assertIsNone(lc._charger_etat_local(chemin=self.chemin_licence))

    @patch.object(lc, "_post_json")
    def test_pas_de_reseau_leve_erreur_activation(self, mock_post):
        mock_post.return_value = (0, None)
        with self.assertRaises(lc.ErreurActivation):
            lc.activer_en_ligne("Club Test", "club@example.com")

    @patch.object(lc, "_post_json")
    def test_succes_enregistre_le_secret_recu_localement(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-01T10:00:00Z",
            "secret_appareil": "a" * 64,
        })
        lc.activer_en_ligne("Club Test", "club@example.com")
        self.assertEqual(self._secrets_locaux[lc.licensing.machine_id_display()], "a" * 64)

    @patch.object(lc, "_post_json")
    def test_secret_local_existant_est_transmis_au_serveur(self, mock_post):
        self._secrets_locaux[lc.licensing.machine_id_display()] = "b" * 64
        jeton = _jeton_test(self.cle_privee)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-01T10:00:00Z",
        })
        lc.activer_en_ligne("Club Test", "club@example.com")
        payload_envoye = mock_post.call_args[0][1]
        self.assertEqual(payload_envoye["secret_appareil"], "b" * 64)

    @patch.object(lc, "_post_json")
    def test_code_activation_est_transmis_au_serveur(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-01T10:00:00Z",
            "secret_appareil": "c" * 64,
        })
        lc.activer_en_ligne("Club Test", "club@example.com", code_activation="ABCD-1234")
        payload_envoye = mock_post.call_args[0][1]
        self.assertEqual(payload_envoye["code_activation"], "ABCD-1234")

    @patch.object(lc, "_post_json")
    def test_401_leve_erreur_identification_insuffisante(self, mock_post):
        mock_post.return_value = (401, {"erreur": "Identification insuffisante."})
        with self.assertRaises(lc.ErreurIdentificationInsuffisante):
            lc.activer_en_ligne("Club Test", "club@example.com")
        self.assertIsNone(lc._charger_etat_local(chemin=self.chemin_licence))

    def test_stockage_indisponible_refuse_sans_jamais_appeler_le_reseau(self):
        with patch.object(lc.device_secret, "stockage_disponible", return_value=False), \
                patch.object(lc, "_post_json") as mock_post:
            with self.assertRaises(lc.ErreurActivation):
                lc.activer_en_ligne("Club Test", "club@example.com")
            mock_post.assert_not_called()

    @patch.object(lc, "_post_json")
    def test_echec_de_stockage_du_secret_najamais_un_succes_partiel_silencieux(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-01T10:00:00Z",
            "secret_appareil": "d" * 64,
        })
        with patch.object(
            lc.device_secret, "enregistrer_secret_local",
            side_effect=lc.device_secret.ErreurStockageSecret("trousseau verrouillé"),
        ):
            with self.assertRaises(lc.ErreurActivation):
                lc.activer_en_ligne("Club Test", "club@example.com")
        # Rien de persisté localement — pas de jeton "orphelin" sans son secret.
        self.assertIsNone(lc._charger_etat_local(chemin=self.chemin_licence))


class VerifierEnLigneTest(EnvironnementTestCase):
    def test_sans_etat_local_najamais_dappel_reseau(self):
        with patch.object(lc, "_post_json") as mock_post:
            resultat = lc.verifier_en_ligne()
            self.assertIsNone(resultat)
            mock_post.assert_not_called()

    def test_sans_secret_local_najamais_dappel_reseau(self):
        # Sécurité (demande du 2026-11-04) : sans secret d'appareil
        # local, impossible de s'identifier — traité comme un simple
        # repli hors-ligne, jamais une exception ni un appel réseau
        # voué à échouer.
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        del self._secrets_locaux[IDENTIFIANT_MACHINE_TEST]
        with patch.object(lc, "_post_json") as mock_post:
            resultat = lc.verifier_en_ligne()
            self.assertIsNone(resultat)
            mock_post.assert_not_called()

    @patch.object(lc, "_post_json")
    def test_secret_local_est_transmis_au_serveur(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = (200, {
            "jeton": jeton, "revoque": False,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-02T10:00:00Z",
        })
        lc.verifier_en_ligne()
        payload_envoye = mock_post.call_args[0][1]
        self.assertEqual(payload_envoye["secret_appareil"], self._secrets_locaux[IDENTIFIANT_MACHINE_TEST])

    @patch.object(lc, "_post_json")
    def test_401_traite_comme_un_repli_hors_ligne_sans_exception(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = (401, {"erreur": "Identification insuffisante."})
        try:
            resultat = lc.verifier_en_ligne()
        except Exception as e:  # pragma: no cover
            self.fail(f"verifier_en_ligne() a levé une exception : {e}")
        self.assertIsNone(resultat)

    @patch.object(lc, "_post_json")
    def test_succes_met_a_jour_letat_local(self, mock_post):
        ancien_jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(ancien_jeton)
        nouveau_jeton = _jeton_test(self.cle_privee, emis_le="2026-11-02T10:00:00Z")
        mock_post.return_value = (200, {
            "jeton": nouveau_jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "revoque": False,
            "date_serveur": "2026-11-02T10:00:00Z",
        })
        resultat = lc.verifier_en_ligne()
        self.assertIsNotNone(resultat)
        etat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertEqual(etat["jeton"], nouveau_jeton)

    @patch.object(lc, "_post_json")
    def test_pas_de_reseau_renvoie_none_sans_toucher_a_letat(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = (0, None)
        resultat = lc.verifier_en_ligne()
        self.assertIsNone(resultat)
        self.assertEqual(lc._charger_etat_local(chemin=self.chemin_licence)["jeton"], jeton)

    @patch.object(lc, "_post_json")
    def test_licence_expiree_reconcilie_la_tolerance_depuis_le_serveur(self, mock_post):
        """Sécurité du 2026-11-03 : TOUTE vérification en ligne réussie
        d'une licence expirée reconstruit l'état local de tolérance
        depuis dates_tolerance.connues — pas seulement en cas d'état
        local déjà indéterminé."""
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        nouveau_jeton = _jeton_test(self.cle_privee, date_expiration="2020-01-01")
        mock_post.return_value = (200, {
            "jeton": nouveau_jeton,
            "licence": {"type": "essai", "statut": "expiree",
                        "date_debut": "2026-11-01", "date_expiration": "2020-01-01"},
            "revoque": False,
            "dates_tolerance": {"acceptees": [], "rejetees_limite": [],
                                 "connues": ["2026-11-01", "2026-11-02"]},
            "date_serveur": "2026-11-03T10:00:00Z",
        })
        lc.verifier_en_ligne()
        etat = session_tolerance.charger_etat(chemin_etat=self.chemin_tolerance)
        self.assertEqual(etat, ("cycle-test-1", ["2026-11-01", "2026-11-02"]))

    @patch.object(lc, "_post_json")
    def test_licence_valide_ne_touche_jamais_a_la_tolerance(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2099-01-01"},
            "revoque": False,
            "dates_tolerance": {"acceptees": [], "rejetees_limite": [], "connues": ["2026-11-01"]},
            "date_serveur": "2026-11-03T10:00:00Z",
        })
        lc.verifier_en_ligne()
        self.assertIsNone(session_tolerance.charger_etat(chemin_etat=self.chemin_tolerance))

    @patch.object(lc, "_post_json")
    def test_licence_revoquee_ne_touche_jamais_a_la_tolerance(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = (200, {
            "jeton": jeton,
            "licence": {"type": "essai", "statut": "revoquee",
                        "date_debut": "2026-11-01", "date_expiration": "2020-01-01"},
            "revoque": True,
            "dates_tolerance": {"acceptees": [], "rejetees_limite": [], "connues": ["2026-11-01"]},
            "date_serveur": "2026-11-03T10:00:00Z",
        })
        lc.verifier_en_ligne()
        self.assertIsNone(session_tolerance.charger_etat(chemin_etat=self.chemin_tolerance))


class MatriceDecisionTest(EnvironnementTestCase):
    """evaluer_licence() — les 5 cas de la matrice du cahier des charges."""

    def test_aucun_etat_local_demande_activation(self):
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_ACTIVATION_REQUISE)

    @patch.object(lc, "verifier_en_ligne")
    def test_en_ligne_et_valide_est_normal(self, mock_verifier):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_verifier.return_value = {
            "jeton": jeton, "revoque": False,
            "licence": {"statut": "active", "type": "essai",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
        }
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_NORMAL)
        self.assertEqual(decision.cycle_id, "cycle-test-1")

    @patch.object(lc, "verifier_en_ligne")
    def test_en_ligne_et_expiree_est_expire_et_amorce_la_tolerance(self, mock_verifier):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_verifier.return_value = {
            "jeton": jeton, "revoque": False,
            "licence": {"statut": "expiree", "type": "essai",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
        }
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_EXPIRE)
        etat_tolerance = session_tolerance.charger_etat(chemin_etat=self.chemin_tolerance)
        self.assertEqual(etat_tolerance[0], "cycle-test-1")

    @patch.object(lc, "verifier_en_ligne")
    def test_en_ligne_et_revoquee_est_revoque(self, mock_verifier):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_verifier.return_value = {
            "jeton": jeton, "revoque": True,
            "licence": {"statut": "revoquee", "type": "essai",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
        }
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_REVOQUE)
        # Décision du 2026-11-03 : aucune tolérance pour une révocation
        # — jamais d'amorçage de session_tolerance dans ce cas.
        self.assertIsNone(session_tolerance.charger_etat(chemin_etat=self.chemin_tolerance))

    @patch.object(lc, "verifier_en_ligne")
    def test_hors_ligne_licence_offerte_a_vie_est_normal_indefiniment(self, mock_verifier):
        """Licences offertes (demande du 2026-11-05) : une licence « à
        vie » (date_expiration = 9999-12-31, sentinelle jamais NULL)
        reste ETAT_NORMAL hors ligne, y compris très loin dans le
        futur — zéro changement nécessaire dans license_client.py pour
        ça, la comparaison existante (aujourd'hui > date_expiration)
        suffit par construction."""
        mock_verifier.return_value = None
        jeton = _jeton_test(self.cle_privee, date_expiration="9999-12-31")
        self._ecrire_etat_local(jeton)
        # Horloge de confiance initialisée à une date normale (pas dans
        # un futur lointain, qui déclencherait à tort la détection de
        # recul d'horloge du Lot 2 lors du prochain evaluer_horloge()) —
        # seule la date d'expiration de la licence doit être « à vie »
        # ici, pas la date de référence de l'horloge.
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 11, 2, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_NORMAL)

    @patch.object(lc, "verifier_en_ligne")
    def test_hors_ligne_valide_est_normal(self, mock_verifier):
        mock_verifier.return_value = None
        jeton = _jeton_test(self.cle_privee, date_expiration="2099-01-01")
        self._ecrire_etat_local(jeton)
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 11, 2, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_NORMAL)

    @patch.object(lc, "verifier_en_ligne")
    def test_hors_ligne_expiree_est_expire(self, mock_verifier):
        mock_verifier.return_value = None
        jeton = _jeton_test(self.cle_privee, date_expiration="2020-01-01")
        self._ecrire_etat_local(jeton)
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 11, 2, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_EXPIRE)

    @patch.object(lc, "verifier_en_ligne")
    def test_hors_ligne_horloge_indeterminee_exige_verification(self, mock_verifier):
        mock_verifier.return_value = None
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        # Pas d'initialisation de trusted_clock -> ETAT_INDETERMINE.
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_VERIFICATION_OBLIGATOIRE)

    @patch.object(lc, "verifier_en_ligne")
    def test_hors_ligne_recul_horloge_exige_verification(self, mock_verifier):
        mock_verifier.return_value = None
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 11, 10, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        trusted_clock.evaluer_horloge(
            chemin=self.chemin_horloge, maintenant=datetime(2026, 11, 1, tzinfo=timezone.utc),
        )
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_VERIFICATION_OBLIGATOIRE)

    @patch.object(lc, "verifier_en_ligne")
    def test_hors_ligne_jeton_local_corrompu_exige_verification(self, mock_verifier):
        mock_verifier.return_value = None
        self._ecrire_etat_local("ceci-nest-pas-un-jeton-valide")
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 11, 2, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_VERIFICATION_OBLIGATOIRE)

    @patch.object(lc, "verifier_en_ligne")
    def test_jamais_dexception_meme_sur_jeton_signe_par_une_autre_cle(self, mock_verifier):
        """Date système jamais seule source de vérité -- un jeton signé
        par une AUTRE clé (ex. serveur de test différent) doit être
        rejeté proprement, jamais planter l'appel."""
        mock_verifier.return_value = None
        autre_cle_privee, _ = license_token.generer_cle_de_test()
        jeton_etranger = _jeton_test(autre_cle_privee)
        self._ecrire_etat_local(jeton_etranger)
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 11, 2, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        try:
            decision = lc.evaluer_licence()
        except Exception as e:  # pragma: no cover
            self.fail(f"evaluer_licence() a levé une exception : {e}")
        self.assertEqual(decision.etat, lc.ETAT_VERIFICATION_OBLIGATOIRE)


class ObtenirInformationsAffichageTest(EnvironnementTestCase):
    """obtenir_informations_affichage() — fenêtre « Licence Senaco »
    (main.py). Ne doit JAMAIS déclencher d'appel réseau : chaque test
    patche _post_json avec un mock et vérifie qu'il n'est jamais
    appelé."""

    def test_sans_jeton_local_disponible_est_faux(self):
        with patch.object(lc, "_post_json") as mock_post:
            infos = lc.obtenir_informations_affichage(None)
            mock_post.assert_not_called()
        self.assertFalse(infos.disponible)
        self.assertIsNone(infos.type)
        self.assertIsNone(infos.etat)

    def test_jeton_local_illisible_disponible_est_faux(self):
        self._ecrire_etat_local("ceci-nest-pas-un-jeton-valide")
        with patch.object(lc, "_post_json") as mock_post:
            infos = lc.obtenir_informations_affichage(None)
            mock_post.assert_not_called()
        self.assertFalse(infos.disponible)

    def test_decision_none_najamais_dexception_etat_absent(self):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        infos = lc.obtenir_informations_affichage(None)
        self.assertTrue(infos.disponible)
        self.assertIsNone(infos.etat)
        self.assertEqual(infos.type, 'essai')
        self.assertEqual(infos.date_debut, '2026-11-01')

    def test_type_et_dates_refletent_le_jeton_signe(self):
        jeton = _jeton_test(self.cle_privee, type='payant', date_debut='2026-01-15', date_expiration='2027-01-15')
        self._ecrire_etat_local(jeton)
        decision = lc.DecisionLicence(lc.ETAT_NORMAL, 'cycle-test-1', '')
        infos = lc.obtenir_informations_affichage(decision)
        self.assertEqual(infos.type, 'payant')
        self.assertEqual(infos.date_debut, '2026-01-15')
        self.assertEqual(infos.date_expiration, '2027-01-15')
        self.assertEqual(infos.etat, lc.ETAT_NORMAL)
        self.assertFalse(infos.a_vie)

    def test_licence_a_vie_detectee_via_la_sentinelle(self):
        jeton = _jeton_test(self.cle_privee, type='offerte', date_expiration='9999-12-31')
        self._ecrire_etat_local(jeton)
        decision = lc.DecisionLicence(lc.ETAT_NORMAL, 'cycle-test-1', '')
        infos = lc.obtenir_informations_affichage(decision)
        self.assertTrue(infos.a_vie)
        self.assertEqual(infos.type, 'offerte')

    def test_tolerance_non_applicable_si_licence_non_expiree(self):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        session_tolerance._ecrire_atomique(
            self.chemin_tolerance, {"cycle_id": "cycle-test-1", "dates_autorisees": ["2026-11-01"]},
        )
        decision = lc.DecisionLicence(lc.ETAT_NORMAL, 'cycle-test-1', '')
        infos = lc.obtenir_informations_affichage(decision)
        self.assertFalse(infos.tolerance_applicable)
        self.assertEqual(infos.tolerance_utilisees, 0)

    def test_tolerance_comptee_quand_licence_expiree_et_cycle_correspondant(self):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        session_tolerance._ecrire_atomique(
            self.chemin_tolerance, {"cycle_id": "cycle-test-1", "dates_autorisees": ["2026-11-01", "2026-11-02"]},
        )
        decision = lc.DecisionLicence(lc.ETAT_EXPIRE, 'cycle-test-1', '')
        infos = lc.obtenir_informations_affichage(decision)
        self.assertTrue(infos.tolerance_applicable)
        self.assertEqual(infos.tolerance_utilisees, 2)
        self.assertEqual(infos.tolerance_max, session_tolerance.TOLERANCE_MAX_DATES)

    def test_tolerance_dun_cycle_perime_najamais_affichee_comme_actuelle(self):
        """Sécurité : un fichier de tolérance laissé par un cycle PRÉCÉDENT
        (cycle_id différent du jeton actuel) ne doit jamais être compté — la
        même garantie que _amorcer_tolerance_si_nouveau_cycle, ici pour
        l'affichage plutôt que pour la décision."""
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        session_tolerance._ecrire_atomique(
            self.chemin_tolerance, {"cycle_id": "ancien-cycle-perime", "dates_autorisees": ["2026-01-01", "2026-01-02"]},
        )
        decision = lc.DecisionLicence(lc.ETAT_EXPIRE, 'cycle-test-1', '')
        infos = lc.obtenir_informations_affichage(decision)
        self.assertTrue(infos.tolerance_applicable)
        self.assertEqual(infos.tolerance_utilisees, 0)

    def test_derniere_confirmation_en_ligne_absente_par_defaut(self):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        infos = lc.obtenir_informations_affichage(None)
        self.assertIsNone(infos.derniere_confirmation_en_ligne)

    def test_derniere_confirmation_en_ligne_refletee_quand_presente(self):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        date_serveur = datetime(2026, 11, 4, 14, 2, tzinfo=timezone.utc)
        trusted_clock.initialiser_apres_verification_en_ligne(date_serveur, chemin=self.chemin_horloge)
        infos = lc.obtenir_informations_affichage(None)
        self.assertEqual(infos.derniere_confirmation_en_ligne, date_serveur)


class IntegrationStructurelleTest(unittest.TestCase):
    def test_module_non_importe_par_main_sauf_dans_une_branche_conditionnelle(self):
        """Lot 5, contrairement aux Lots 1-3, EST câblé dans main.py —
        mais uniquement sous condition POKER_TOURNAMENT_LICENSE_V3
        (voir le compte rendu). Ce test vérifie juste que l'import
        existe quelque part, pas qu'il est inconditionnel — une
        vérification plus précise (gating réel) vit dans
        tests/test_license_v3_integration_main.py."""
        chemin_main = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py",
        )
        with open(chemin_main, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.assertIn("license_client", contenu)
        self.assertIn("POKER_TOURNAMENT_LICENSE_V3", contenu)


if __name__ == "__main__":
    unittest.main()
