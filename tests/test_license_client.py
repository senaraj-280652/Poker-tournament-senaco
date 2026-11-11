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
import threading
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
        self.chemin_rotation_en_attente = os.path.join(self._tmp.name, "rotation_en_attente.json")
        self.chemin_diagnostic = os.path.join(self._tmp.name, "licence_diagnostic.log")
        self.chemin_diagnostic_secours = os.path.join(self._tmp.name, "licence_diagnostic_secours.log")
        self.chemin_verrou_verification = os.path.join(self._tmp.name, "verification_en_ligne.lock")

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
            patch.object(lc, "ROTATION_EN_ATTENTE_FILE", self.chemin_rotation_en_attente),
            patch.object(lc, "LICENSE_DIAGNOSTIC_LOG_FILE", self.chemin_diagnostic),
            patch.object(lc, "LICENSE_DIAGNOSTIC_LOG_FALLBACK_FILE", self.chemin_diagnostic_secours),
            patch.object(lc, "VERIFICATION_LOCK_FILE", self.chemin_verrou_verification),
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

    def _lire_journal(self):
        if not os.path.exists(self.chemin_diagnostic):
            return ""
        with open(self.chemin_diagnostic, "r", encoding="utf-8") as f:
            return f.read()


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


class ChargerEtatLocalDiagnosticTest(EnvironnementTestCase):
    """Correctif du 2026-11-08 (TEST-27, HP Windows 10) : _charger_etat_
    local() journalise désormais pourquoi il rejette un fichier (chemin
    recherché + nature du rejet), jamais une valeur lue dedans. Couvre
    les 4 causes de rejet possibles, confirme l'absence totale
    d'écriture en cas de succès, et l'absence de toute fuite de valeur
    sensible dans le journal."""

    def test_succes_necrit_rien_dans_le_journal(self):
        self._ecrire_etat_local(_jeton_test(self.cle_privee))
        resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIsNotNone(resultat)
        self.assertFalse(os.path.exists(self.chemin_diagnostic), "Aucune écriture attendue en cas de succès.")

    def test_fichier_absent_journalise_le_chemin_et_le_type_dexception(self):
        chemin_absent = os.path.join(self._tmp.name, "nexiste_pas.json")
        resultat = lc._charger_etat_local(chemin=chemin_absent)
        self.assertIsNone(resultat)
        journal = self._lire_journal()
        self.assertIn(repr(chemin_absent), journal)
        self.assertIn("echec_lecture", journal)
        self.assertIn("FileNotFoundError", journal)

    def test_json_invalide_journalise_json_invalide(self):
        with open(self.chemin_licence, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json valide")
        resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIsNone(resultat)
        journal = self._lire_journal()
        self.assertIn("json_invalide", journal)
        self.assertIn("JSONDecodeError", journal)

    def test_racine_json_pas_un_dict_journalise_le_type_recu(self):
        with open(self.chemin_licence, "w", encoding="utf-8") as f:
            json.dump(["pas", "un", "dict"], f)
        resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIsNone(resultat)
        journal = self._lire_journal()
        self.assertIn("contenu_rejete", journal)
        self.assertIn("list", journal)

    def test_jeton_absent_ou_mauvais_type_journalise_sans_jamais_exposer_sa_valeur(self):
        secret_qui_ne_doit_jamais_fuiter = "SECRET-NE-DOIT-JAMAIS-APPARAITRE-DANS-LE-JOURNAL"
        with open(self.chemin_licence, "w", encoding="utf-8") as f:
            json.dump({"jeton": 12345, "autre_champ": secret_qui_ne_doit_jamais_fuiter}, f)
        resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIsNone(resultat)
        journal = self._lire_journal()
        self.assertIn("contenu_rejete", journal)
        self.assertIn("int", journal, "Le type reçu (int) doit être journalisé.")
        self.assertNotIn("12345", journal, "La valeur elle-même ne doit jamais apparaître.")
        self.assertNotIn(secret_qui_ne_doit_jamais_fuiter, journal)

    def test_jeton_valide_present_najamais_sa_valeur_dans_un_journal_dechec_dun_autre_champ(self):
        """Un jeton par ailleurs valide ne doit jamais apparaître dans le
        journal, même lorsque l'échec vient d'un AUTRE motif (ici une
        racine JSON qui n'est pas un dict, contenant malgré tout un
        jeton valide ailleurs dans le fichier)."""
        jeton_reel = _jeton_test(self.cle_privee)
        with open(self.chemin_licence, "w", encoding="utf-8") as f:
            json.dump([{"jeton": jeton_reel}], f)
        lc._charger_etat_local(chemin=self.chemin_licence)
        journal = self._lire_journal()
        self.assertNotIn(jeton_reel, journal)

    def test_chemin_par_defaut_journalise_correctement(self):
        """Sans `chemin` explicite, c'est bien LICENSE_STATE_FILE (ici
        redirigé par l'isolation du test) qui doit apparaître dans le
        journal — jamais un autre chemin recalculé différemment."""
        resultat = lc._charger_etat_local()
        self.assertIsNone(resultat)
        journal = self._lire_journal()
        self.assertIn(repr(self.chemin_licence), journal)


class LectureAvecTentativesTest(EnvironnementTestCase):
    """Correctif du 2026-11-11 : PermissionError (WinError 32 "violation
    de partage" ET WinError 33 "violation de verrouillage" remontent
    toutes les deux ainsi en Python — vérifié empiriquement le
    2026-11-11 sur le HP Windows 10 réel, voir le compte-rendu) déclenche
    jusqu'à 3 tentatives supplémentaires, espacées de ~150ms. AUCUNE
    autre exception n'est retentée — un fichier réellement absent ne
    doit jamais être ralenti par une attente inutile."""

    def setUp(self):
        super().setUp()
        self._ecrire_etat_local(_jeton_test(self.cle_privee))
        self._vrai_open = open

    def _open_qui_echoue_n_fois(self, nb_echecs, exception=PermissionError):
        compteur = {"appels": 0}

        def _faux_open(chemin, *args, **kwargs):
            if chemin == self.chemin_licence:
                compteur["appels"] += 1
                if compteur["appels"] <= nb_echecs:
                    raise exception(13, "Simulation de verrou transitoire")
            return self._vrai_open(chemin, *args, **kwargs)

        return _faux_open, compteur

    def test_permission_error_puis_succes_renvoie_les_vraies_donnees(self):
        """2 échecs puis succès (dans la limite des 3 tentatives
        supplémentaires) : le résultat final doit être EXACTEMENT celui
        d'une lecture réussie, jamais dégradé."""
        faux_open, compteur = self._open_qui_echoue_n_fois(2)
        with patch("builtins.open", side_effect=faux_open), \
                patch.object(lc.time, "sleep") as faux_sleep:
            resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIsNotNone(resultat)
        self.assertIsInstance(resultat, dict)
        self.assertEqual(compteur["appels"], 3)  # 2 échecs + 1 succès
        self.assertEqual(faux_sleep.call_count, 2)
        for appel in faux_sleep.call_args_list:
            self.assertEqual(appel.args[0], lc.LECTURE_LICENCE_DELAI_SECONDES)

    def test_permission_error_sur_toutes_les_tentatives_renvoie_la_sentinelle(self):
        """4 échecs (1 + 3 tentatives supplémentaires), tous par
        PermissionError : la sentinelle _ACCES_TEMPORAIREMENT_INDISPONIBLE
        doit être renvoyée — JAMAIS None (qui signifierait "jamais
        activé" à evaluer_licence, voir EtatLectureIndisponibleTest)."""
        faux_open, compteur = self._open_qui_echoue_n_fois(99)
        with patch("builtins.open", side_effect=faux_open), \
                patch.object(lc.time, "sleep"):
            resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIs(resultat, lc._ACCES_TEMPORAIREMENT_INDISPONIBLE)
        self.assertIsNot(resultat, None)
        self.assertEqual(compteur["appels"], 4)
        journal = self._lire_journal()
        self.assertIn("acces_temporairement_indisponible", journal)
        self.assertIn("apres 4 tentatives", journal)

    def test_filenotfounderror_nest_jamais_retentee(self):
        """Une licence réellement absente ne doit jamais subir le délai
        d'attente — comportement identique à avant ce correctif."""
        chemin_absent = os.path.join(self._tmp.name, "nexiste_vraiment_pas.json")
        with patch.object(lc.time, "sleep") as faux_sleep:
            resultat = lc._charger_etat_local(chemin=chemin_absent)
        self.assertIsNone(resultat)
        faux_sleep.assert_not_called()

    def test_jsondecodeerror_nest_jamais_retentee(self):
        """Un contenu JSON corrompu ne devient pas valide en relisant le
        même fichier (écriture atomique déjà en place) — aucune
        tentative supplémentaire, comportement identique à avant."""
        with open(self.chemin_licence, "w", encoding="utf-8") as f:
            f.write("{pas du json valide")
        with patch.object(lc.time, "sleep") as faux_sleep:
            resultat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertIsNone(resultat)
        faux_sleep.assert_not_called()


class EtatLectureIndisponibleTest(EnvironnementTestCase):
    """Correctif du 2026-11-11 : ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE
    ne doit JAMAIS être confondu avec ETAT_ACTIVATION_REQUISE — c'est le
    cœur du correctif (voir l'audit du 2026-11-10 : un client déjà
    licencié ne doit jamais être invité à réactiver à cause d'un verrou
    transitoire)."""

    def test_evaluer_licence_renvoie_le_nouvel_etat_sur_sentinelle(self):
        with patch.object(lc, "_charger_etat_local", return_value=lc._ACCES_TEMPORAIREMENT_INDISPONIBLE):
            decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE)
        self.assertNotEqual(decision.etat, lc.ETAT_ACTIVATION_REQUISE)

    def test_evaluer_licence_renvoie_toujours_activation_requise_sur_none_reel(self):
        """Non-régression : une absence RÉELLE (None, pas la sentinelle)
        continue de produire ETAT_ACTIVATION_REQUISE, inchangé."""
        with patch.object(lc, "_charger_etat_local", return_value=None):
            decision = lc.evaluer_licence()
        self.assertEqual(decision.etat, lc.ETAT_ACTIVATION_REQUISE)

    def test_verifier_en_ligne_se_replie_silencieusement_sur_sentinelle(self):
        """verifier_en_ligne() ne doit jamais planter ni tenter un appel
        réseau quand l'état local est temporairement inaccessible."""
        with patch.object(lc, "_charger_etat_local", return_value=lc._ACCES_TEMPORAIREMENT_INDISPONIBLE), \
                patch.object(lc, "_post_json") as faux_post:
            resultat = lc.verifier_en_ligne()
        self.assertIsNone(resultat)
        faux_post.assert_not_called()

    def test_obtenir_informations_affichage_ne_plante_pas_sur_sentinelle(self):
        """Régression : avant correction, indexer la sentinelle
        (object() non souscriptible) levait TypeError au lieu de
        renvoyer disponible=False — ce test aurait échoué sans le
        correctif correspondant dans obtenir_informations_affichage()."""
        with patch.object(lc, "_charger_etat_local", return_value=lc._ACCES_TEMPORAIREMENT_INDISPONIBLE):
            infos = lc.obtenir_informations_affichage(None)
        self.assertFalse(infos.disponible)


class JournalSecoursEtRotationTest(EnvironnementTestCase):
    """Correctif du 2026-11-11 : journal de secours (dossier distinct de
    .poker_tournament, voir l'audit du 2026-11-10) et plafonnement à
    200 Ko des deux journaux, une seule génération précédente
    conservée."""

    def test_bascule_vers_le_journal_de_secours_si_le_principal_echoue(self):
        _vrai_open = open

        def _faux_open(chemin, *args, **kwargs):
            if chemin == self.chemin_diagnostic:
                raise PermissionError(13, "Dossier .poker_tournament inaccessible")
            return _vrai_open(chemin, *args, **kwargs)

        with patch("builtins.open", side_effect=_faux_open):
            lc._journaliser_diagnostic_chargement(self.chemin_licence, "echec_lecture", "detail de test")

        self.assertFalse(os.path.exists(self.chemin_diagnostic))
        self.assertTrue(os.path.exists(self.chemin_diagnostic_secours))
        with open(self.chemin_diagnostic_secours, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.assertIn("echec_lecture", contenu)
        self.assertIn(repr(self.chemin_licence), contenu)

    def test_echec_des_deux_journaux_ne_leve_jamais(self):
        """Même si le journal principal ET le journal de secours
        échouent tous les deux, _journaliser_diagnostic_chargement() ne
        doit jamais lever — appelé depuis _charger_etat_local(), qui ne
        doit jamais planter l'évaluation de la licence pour cette raison."""
        with patch("builtins.open", side_effect=PermissionError(13, "aucun dossier accessible")):
            try:
                lc._journaliser_diagnostic_chargement(self.chemin_licence, "echec_lecture", "detail")
            except Exception as e:  # pragma: no cover - le test échoue si ceci est atteint
                self.fail(f"_journaliser_diagnostic_chargement a levé {e!r} au lieu de rester silencieuse.")

    def test_rotation_quand_le_journal_depasse_la_taille_max(self):
        with patch.object(lc, "DIAGNOSTIC_LOG_TAILLE_MAX_OCTETS", 50):
            lc._journaliser_diagnostic_chargement(self.chemin_licence, "echec_lecture", "a" * 60)
            taille_apres_premier = os.path.getsize(self.chemin_diagnostic)
            self.assertGreater(taille_apres_premier, 50)
            lc._journaliser_diagnostic_chargement(self.chemin_licence, "echec_lecture", "b" * 10)

        self.assertTrue(os.path.exists(self.chemin_diagnostic + ".1"))
        with open(self.chemin_diagnostic + ".1", "r", encoding="utf-8") as f:
            self.assertIn("a" * 60, f.read())
        with open(self.chemin_diagnostic, "r", encoding="utf-8") as f:
            contenu_courant = f.read()
        self.assertIn("b" * 10, contenu_courant)
        self.assertNotIn("a" * 60, contenu_courant)

    def test_aucun_jeton_ni_secret_dans_le_journal_de_secours(self):
        jeton_reel = _jeton_test(self.cle_privee)
        secret_qui_ne_doit_jamais_fuiter = "SECRET-NE-DOIT-JAMAIS-FUITER-DANS-LE-SECOURS"
        with open(self.chemin_licence, "w", encoding="utf-8") as f:
            json.dump([{"jeton": jeton_reel, "secret": secret_qui_ne_doit_jamais_fuiter}], f)

        _vrai_open = open

        def _faux_open(chemin, *args, **kwargs):
            if chemin == self.chemin_diagnostic:
                raise PermissionError(13, "simulation")
            return _vrai_open(chemin, *args, **kwargs)

        with patch("builtins.open", side_effect=_faux_open):
            lc._charger_etat_local(chemin=self.chemin_licence)

        with open(self.chemin_diagnostic_secours, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.assertNotIn(jeton_reel, contenu)
        self.assertNotIn(secret_qui_ne_doit_jamais_fuiter, contenu)


class VerrouVerificationTest(EnvironnementTestCase):
    """Correctif du 2026-11-11 : verrou interprocessus natif de l'OS
    (fcntl.flock/msvcrt.locking — même mécanisme déjà utilisé par
    session_tolerance.py/open_windows.py, voir tests/
    test_open_windows_try_register.py pour le même principe de test
    avec un VRAI verrou tenu via threading.Barrier, jamais une
    simulation icacls)."""

    def test_verrou_obtenu_normalement_sans_contention(self):
        with lc._verrou_verification(chemin=self.chemin_verrou_verification, timeout=1.0):
            pass  # ne doit jamais lever en l'absence de tout concurrent

    def test_dossier_du_verrou_impossible_a_creer_ne_plante_pas(self):
        """Trouvé le 2026-11-11 en vérification (précaution impérative
        du 2026-11-11) : avant correction, une PermissionError sur
        os.makedirs() (dossier inaccessible) se propageait hors de
        verifier_en_ligne(), violant son contrat "ne lève jamais" et
        plantant App.__init__ au lieu de se replier. TimeoutError étant
        une sous-classe d'OSError, la même clause `except OSError`
        couvre ce cas sans traitement séparé."""
        dossier_verrou_impossible = os.path.join(self._tmp.name, "dossier_verrou_impossible")
        chemin_verrou = os.path.join(dossier_verrou_impossible, "verification_en_ligne.lock")
        _vrai_makedirs = os.makedirs

        def _makedirs_qui_refuse_le_verrou(chemin, *args, **kwargs):
            if chemin == dossier_verrou_impossible:
                raise PermissionError(13, "Simulation : dossier du verrou inaccessible")
            return _vrai_makedirs(chemin, *args, **kwargs)

        with patch.object(lc, "VERIFICATION_LOCK_FILE", chemin_verrou), \
                patch.object(lc.os, "makedirs", side_effect=_makedirs_qui_refuse_le_verrou):
            try:
                resultat = lc.verifier_en_ligne()
            except Exception as e:  # pragma: no cover - le test échoue si ceci est atteint
                self.fail(f"verifier_en_ligne() a levé {e!r} au lieu de se replier silencieusement.")
        self.assertIsNone(resultat)

    def test_fichier_du_verrou_impossible_a_ouvrir_ne_plante_pas(self):
        """Même principe que ci-dessus, mais l'échec porte sur
        os.open() du fichier de verrou lui-même (dossier accessible,
        fichier refusé — ex. verrou système/antivirus)."""
        with patch.object(lc.os, "open", side_effect=PermissionError(13, "Simulation : fichier de verrou refusé")):
            try:
                resultat = lc.verifier_en_ligne()
            except Exception as e:  # pragma: no cover - le test échoue si ceci est atteint
                self.fail(f"verifier_en_ligne() a levé {e!r} au lieu de se replier silencieusement.")
        self.assertIsNone(resultat)

    def test_licence_locale_expiree_najamais_contournee_quand_le_verrou_est_inutilisable(self):
        """Précaution impérative du 2026-11-11, point 2 : un verrou
        totalement inutilisable (dossier inaccessible) doit produire
        EXACTEMENT le même repli qu'une vraie coupure réseau — jamais
        un raccourci qui traiterait une licence localement expirée
        comme valide. Vérifié ici avec un VRAI jeton signé expiré,
        jamais un mock de la décision elle-même."""
        jeton_expire = _jeton_test(
            self.cle_privee, date_debut="2026-01-01", date_expiration="2026-01-15",
        )
        self._ecrire_etat_local(jeton_expire)
        trusted_clock.initialiser_apres_verification_en_ligne(
            datetime(2026, 2, 1, tzinfo=timezone.utc), chemin=self.chemin_horloge,
        )
        dossier_verrou_impossible = os.path.join(self._tmp.name, "dossier_verrou_impossible_2")
        chemin_verrou = os.path.join(dossier_verrou_impossible, "verification_en_ligne.lock")
        _vrai_makedirs = os.makedirs

        def _makedirs_qui_refuse_le_verrou(chemin, *args, **kwargs):
            if chemin == dossier_verrou_impossible:
                raise PermissionError(13, "Simulation : dossier du verrou inaccessible")
            return _vrai_makedirs(chemin, *args, **kwargs)

        with patch.object(lc, "VERIFICATION_LOCK_FILE", chemin_verrou), \
                patch.object(lc.os, "makedirs", side_effect=_makedirs_qui_refuse_le_verrou):
            decision = lc.evaluer_licence()
        self.assertEqual(
            decision.etat, lc.ETAT_EXPIRE,
            "Un verrou inutilisable ne doit jamais, même indirectement, faire passer une licence expirée pour valide.",
        )

    def test_timeout_leve_quand_le_verrou_est_deja_tenu(self):
        """Tient le VRAI verrou OS via un descripteur distinct (comme le
        ferait un second processus), puis vérifie que la tentative
        concurrente échoue par TimeoutError — jamais un blocage
        indéfini."""
        fd_tenu = os.open(self.chemin_verrou_verification, os.O_CREAT | os.O_RDWR)
        lc._acquire_lock(fd_tenu, timeout=1.0)
        try:
            with self.assertRaises(TimeoutError):
                with lc._verrou_verification(chemin=self.chemin_verrou_verification, timeout=0.3):
                    pass  # jamais atteint
        finally:
            lc._release_lock(fd_tenu)
            os.close(fd_tenu)

    def test_verifier_en_ligne_se_replie_sur_none_si_verrou_occupe_sans_lever(self):
        """Précaution impérative : jamais un blocage durable ni une
        exception visible si le verrou est occupé — repli identique à
        une panne réseau."""
        with patch.object(lc, "VERIFICATION_LOCK_TIMEOUT_SECONDES", 0.3):
            fd_tenu = os.open(self.chemin_verrou_verification, os.O_CREAT | os.O_RDWR)
            lc._acquire_lock(fd_tenu, timeout=1.0)
            try:
                resultat = lc.verifier_en_ligne()
            finally:
                lc._release_lock(fd_tenu)
                os.close(fd_tenu)
        self.assertIsNone(resultat)

    def test_mesure_le_delai_de_demarrage_quand_le_verrou_est_occupe(self):
        """Précaution impérative : mesurer le délai réel — doit être
        borné par le timeout configuré, jamais instantané (le verrou a
        réellement été attendu) ni significativement plus long (pas de
        blocage au-delà du délai prévu)."""
        delai_configure = 0.4
        with patch.object(lc, "VERIFICATION_LOCK_TIMEOUT_SECONDES", delai_configure):
            fd_tenu = os.open(self.chemin_verrou_verification, os.O_CREAT | os.O_RDWR)
            lc._acquire_lock(fd_tenu, timeout=1.0)
            try:
                debut = lc.time.monotonic()
                lc.verifier_en_ligne()
                duree = lc.time.monotonic() - debut
            finally:
                lc._release_lock(fd_tenu)
                os.close(fd_tenu)
        self.assertGreaterEqual(duree, delai_configure * 0.8)
        self.assertLess(duree, delai_configure + 1.0, "Le délai d'attente du verrou ne doit jamais dériver largement au-delà du timeout configuré.")

    def test_second_processus_relit_letat_actualise_apres_le_verrou(self):
        """Cœur du point 2 du correctif : deux appels quasi simultanés à
        verifier_en_ligne() (deux threads, deux descripteurs distincts
        sur le MÊME verrou réel — même principe que test_open_windows_
        try_register.py) — le second ne doit jamais utiliser ni écraser
        le secret que le premier vient d'écrire ; il doit relire l'état
        réellement à jour une fois son tour venu."""
        self._ecrire_etat_local(_jeton_test(self.cle_privee))
        barrier = threading.Barrier(2)
        secrets_vus_a_la_lecture = []
        verrou_appels = threading.Lock()

        appels_restants = {"n": 2}

        def fausse_post_json(url, payload):
            # Chaque appel "réussit" et fait tourner un secret différent,
            # pour détecter si un processus utilise/écrase une valeur
            # périmée par rapport à celle que l'autre vient d'écrire.
            with verrou_appels:
                appels_restants["n"] -= 1
                nouveau_secret = f"secret-rotation-{appels_restants['n']}"
            secrets_vus_a_la_lecture.append(payload["secret_appareil"])
            return 200, {
                "secret_appareil": nouveau_secret,
                "jeton": _jeton_test(self.cle_privee),
                "date_serveur": "2026-11-11T12:00:00Z",
            }

        resultats = []

        def worker():
            barrier.wait(timeout=2)
            resultats.append(lc.verifier_en_ligne())

        # Le correctif patché UNE SEULE FOIS avant de démarrer les deux
        # threads — unittest.mock.patch n'est pas conçu pour être
        # entré/sorti depuis plusieurs threads simultanément sur LA
        # MÊME cible (ce serait une fragilité du test, pas du code
        # réel) ; les deux threads partagent donc la même fonction
        # patchée, ce qui est exactement ce qu'on veut tester.
        with patch.object(lc, "_post_json", side_effect=fausse_post_json):
            t1 = threading.Thread(target=worker)
            t2 = threading.Thread(target=worker)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

        self.assertEqual(len(resultats), 2)
        self.assertTrue(all(r is not None for r in resultats), "Les deux vérifications doivent réussir, jamais une course silencieuse.")
        # Le second appel (dans l'ordre réel d'exécution sous le verrou)
        # doit avoir vu le secret ISSU du premier, jamais le secret
        # d'origine pré-rotation pour les deux à la fois.
        self.assertEqual(len(set(secrets_vus_a_la_lecture)), 2, "Les deux tentatives ne doivent jamais lire le même secret pré-rotation — la seconde doit voir celui écrit par la première.")
        self.assertIn("0" * 64, secrets_vus_a_la_lecture, "Le tout premier appel doit bien voir le secret d'origine.")


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


class RotationSecretClientTest(EnvironnementTestCase):
    """Protocole de rotation des secrets (validé le 2026-11-06), côté
    client : jeton de tentative persistant, reprise après coupure/
    redémarrage, et séquencement strict (jamais effacé avant
    confirmation de l'enregistrement local)."""

    def _reponse_avec_secret(self, jeton, nouveau_secret="1" * 64):
        return (200, {
            "jeton": jeton, "revoque": False, "secret_appareil": nouveau_secret,
            "licence": {"type": "essai", "statut": "active",
                        "date_debut": "2026-11-01", "date_expiration": "2027-02-01"},
            "date_serveur": "2026-11-02T10:00:00Z",
        })

    @patch.object(lc, "_post_json")
    def test_jeton_tentative_et_identifiant_frais_sont_transmis(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = self._reponse_avec_secret(jeton)
        with patch.object(lc.licensing, "machine_id_display", return_value="ID-FRAIS-RECALCULE"):
            lc.verifier_en_ligne()
        payload_envoye = mock_post.call_args[0][1]
        self.assertRegex(payload_envoye["jeton_tentative"], r"^[0-9a-f]{32,}$")
        self.assertEqual(payload_envoye["identifiant_machine_frais"], "ID-FRAIS-RECALCULE")
        # La clé de recherche, elle, reste celle du jeton en cache — jamais la fraîche.
        self.assertEqual(payload_envoye["identifiant_machine"], IDENTIFIANT_MACHINE_TEST)

    @patch.object(lc, "_post_json")
    def test_succes_efface_la_tentative_en_attente_apres_stockage_reussi(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = self._reponse_avec_secret(jeton)
        lc.verifier_en_ligne()
        self.assertFalse(os.path.exists(self.chemin_rotation_en_attente))
        self.assertEqual(self._secrets_locaux[IDENTIFIANT_MACHINE_TEST], "1" * 64)

    @patch.object(lc, "_post_json")
    def test_coupure_reseau_simulee_conserve_la_tentative_pour_un_rejeu_identique(self, mock_post):
        """La réponse réussie du serveur n'arrive jamais (coupure) —
        simulé ici en renvoyant (0, None), comme le ferait _post_json
        sur toute anomalie réseau."""
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = (0, None)
        resultat = lc.verifier_en_ligne()
        self.assertIsNone(resultat)

        self.assertTrue(os.path.exists(self.chemin_rotation_en_attente))
        jeton_ecrit = lc._jeton_tentative_en_attente(chemin=self.chemin_rotation_en_attente)

        # Nouvel essai (redémarrage de l'application) : le MÊME jeton
        # doit être réutilisé, jamais un nouveau.
        mock_post.return_value = self._reponse_avec_secret(jeton)
        lc.verifier_en_ligne()
        payload_envoye = mock_post.call_args[0][1]
        self.assertEqual(payload_envoye["jeton_tentative"], jeton_ecrit)

    @patch.object(lc, "_post_json")
    def test_echec_de_stockage_local_conserve_la_tentative_et_najamais_de_succes_partiel(self, mock_post):
        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = self._reponse_avec_secret(jeton, nouveau_secret="2" * 64)
        with patch.object(
            lc.device_secret, "enregistrer_secret_local",
            side_effect=lc.device_secret.ErreurStockageSecret("échec simulé"),
        ):
            resultat = lc.verifier_en_ligne()

        self.assertIsNone(resultat, "Un échec de stockage local ne doit jamais être un succès partiel.")
        self.assertTrue(os.path.exists(self.chemin_rotation_en_attente), "La tentative doit rester disponible pour un rejeu.")
        # Ni le jeton signé ni rien d'autre ne doit avoir été mis à jour localement.
        etat = lc._charger_etat_local(chemin=self.chemin_licence)
        self.assertEqual(etat["jeton"], jeton)

    @patch.object(lc, "_post_json")
    def test_tentative_trop_ancienne_est_purgee_et_jamais_reutilisee(self, mock_post):
        ancien = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
        with open(self.chemin_rotation_en_attente, "w", encoding="utf-8") as f:
            json.dump({"jeton_tentative": "a" * 32, "cree_le": ancien}, f)

        jeton = _jeton_test(self.cle_privee)
        self._ecrire_etat_local(jeton)
        mock_post.return_value = self._reponse_avec_secret(jeton)
        lc.verifier_en_ligne()

        payload_envoye = mock_post.call_args[0][1]
        self.assertNotEqual(payload_envoye["jeton_tentative"], "a" * 32, "Une tentative trop ancienne ne doit jamais être réutilisée.")


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
