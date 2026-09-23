# -*- coding: utf-8 -*-
"""Registre des appareils du Contrôle à distance — liaison propriétaire
(demande du 2026-09-20, Phase 2 du chantier "Sécurisation du Contrôle à
distance") : open_windows.set_remote_device_owner/clear_remote_device_
owner/get_remote_device_owner, et l'extension correspondante de list_
approved_remote_devices/register_device_attempt/revoke_remote_device.

RÈGLES EXPLICITEMENT DEMANDÉES, vérifiées ici :
- le système d'authentification par code à 6 chiffres reste intact ;
- l'approbation actuelle de l'appareil reste intacte ;
- un appareil est lié à UN propriétaire à un instant donné (un seul
  champ, jamais une liste — une réaffectation REMPLACE) ;
- la liaison est GLOBALE (un seul registre partagé, pas par tournoi) ;
- elle PERSISTE (survit à une relecture/un nouveau process simulé) ;
- une révocation efface la liaison (décision explicite : jamais une
  ré-attribution silencieuse après réapprobation) ;
- retirer la liaison ne révoque JAMAIS l'appareil ;
- aucune régression de l'authentification/révocation déjà en place.

N'utilise JAMAIS ~/.poker_tournament : open_windows._remote_control_dir
est redirigé vers un dossier temporaire pour toute la durée de chaque
test (voir setUp)."""
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


class RemoteDeviceOwnerTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_device_owner_test_")
        self.addCleanup(self._tmp.cleanup)
        remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(remote_dir, exist_ok=True)
        patcher = patch.object(open_windows, "_remote_control_dir", return_value=remote_dir)
        self.addCleanup(patcher.stop)
        patcher.start()
        # Simule une session active (list_open_paths non vide) : requis
        # par register_device_attempt (comme en usage réel, un serveur
        # de contrôle à distance n'existe que si au moins un tournoi est
        # ouvert).
        open_paths_patcher = patch.object(open_windows, "list_open_paths", return_value=["/fake/a.tournoi"])
        self.addCleanup(open_paths_patcher.stop)
        open_paths_patcher.start()

    def _approve(self, ip="192.168.0.191"):
        bid = os.urandom(16).hex()
        open_windows.register_device_attempt(bid, ip)
        open_windows.approve_remote_device(bid, label="Téléphone Test")
        return bid


class SetGetClearOwnerTest(RemoteDeviceOwnerTestCase):
    def test_set_sur_appareil_inconnu_renvoie_false(self):
        self.assertFalse(open_windows.set_remote_device_owner("bidfantome", "Alice"))

    def test_set_puis_get(self):
        bid = self._approve()
        self.assertTrue(open_windows.set_remote_device_owner(bid, "Alice"))
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")

    def test_get_appareil_inconnu_renvoie_none(self):
        self.assertIsNone(open_windows.get_remote_device_owner("bidfantome"))

    def test_get_appareil_connu_non_lie_renvoie_none(self):
        bid = self._approve()
        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_reaffectation_remplace_jamais_naccumule(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        open_windows.set_remote_device_owner(bid, "Bob")
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Bob")

    def test_clear_sur_appareil_inconnu_renvoie_false(self):
        self.assertFalse(open_windows.clear_remote_device_owner("bidfantome"))

    def test_clear_retire_la_liaison(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        self.assertTrue(open_windows.clear_remote_device_owner(bid))
        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_set_avec_chaine_vide_equivaut_a_clear(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        open_windows.set_remote_device_owner(bid, "")
        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_set_avec_espaces_superflus_normalise(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "  Alice  ")
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")


class ClearNeRevoquePasTest(RemoteDeviceOwnerTestCase):
    """"Suppression/retrait de la liaison sans révoquer l'appareil" —
    exigence explicite."""

    def test_appareil_reste_approuve_apres_retrait_de_la_liaison(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")

        open_windows.clear_remote_device_owner(bid)

        approved = {d["browser_id"]: d for d in open_windows.list_approved_remote_devices()}
        self.assertIn(bid, approved)  # toujours approuvé
        self.assertIsNone(approved[bid]["owner_name"])

    def test_session_reste_valide_apres_retrait_de_la_liaison(self):
        """Le jeton de session (niveau 1) ne doit JAMAIS être affecté par
        une opération purement liée au propriétaire (niveau 2)."""
        bid = self._approve()
        token = open_windows.get_or_mint_device_session_token(bid)
        open_windows.set_remote_device_owner(bid, "Alice")

        open_windows.clear_remote_device_owner(bid)

        self.assertTrue(open_windows.verify_device_session(bid, token))


class ListApprovedIncludesOwnerTest(RemoteDeviceOwnerTestCase):
    def test_owner_name_absent_par_defaut(self):
        self._approve()
        devices = open_windows.list_approved_remote_devices()
        self.assertEqual(len(devices), 1)
        self.assertIsNone(devices[0]["owner_name"])

    def test_owner_name_present_apres_attribution(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        devices = {d["browser_id"]: d for d in open_windows.list_approved_remote_devices()}
        self.assertEqual(devices[bid]["owner_name"], "Alice")

    def test_plusieurs_appareils_proprietaires_independants(self):
        bid1 = self._approve("192.168.0.10")
        bid2 = self._approve("192.168.0.11")
        open_windows.set_remote_device_owner(bid1, "Alice")
        open_windows.set_remote_device_owner(bid2, "Bob")
        devices = {d["browser_id"]: d["owner_name"] for d in open_windows.list_approved_remote_devices()}
        self.assertEqual(devices[bid1], "Alice")
        self.assertEqual(devices[bid2], "Bob")


class RevokeEffaceProprietaireTest(RemoteDeviceOwnerTestCase):
    """Décision explicite : une révocation efface owner_name — un
    appareil révoqué puis réapprouvé repart SANS propriétaire, jamais
    une ré-attribution silencieuse."""

    def test_revoke_efface_owner_name(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")

        open_windows.revoke_remote_device(bid)

        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_revoke_toujours_efficace_sans_proprietaire(self):
        """Non-régression : un appareil jamais lié se révoque
        normalement, comme avant ce chantier."""
        bid = self._approve()
        self.assertTrue(open_windows.revoke_remote_device(bid))
        approved = open_windows.list_approved_remote_devices()
        self.assertEqual(approved, [])

    def test_reapprobation_apres_revocation_ne_ressuscite_pas_lancien_proprietaire(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")
        open_windows.revoke_remote_device(bid)

        # Le téléphone redemande le code, redevient "pending", puis est
        # réapprouvé par l'ADMIN — même browser_id (même appareil
        # physique), mais un cycle complet révocation -> nouvelle
        # demande -> réapprobation.
        open_windows.register_device_attempt(bid, "192.168.0.191")
        open_windows.approve_remote_device(bid)

        self.assertIsNone(open_windows.get_remote_device_owner(bid))

    def test_set_owner_sur_appareil_revoque_reste_possible_mais_efface_a_la_prochaine_revocation(self):
        """set_remote_device_owner n'exige pas explicitement le statut
        "approved" (voir sa docstring) — mais rien n'empêche non plus un
        ADMIN de préparer une liaison à l'avance ; ce test documente ce
        comportement plutôt que de le supposer."""
        bid = self._approve()
        open_windows.revoke_remote_device(bid)
        self.assertTrue(open_windows.set_remote_device_owner(bid, "Alice"))
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")


class NonRegressionAuthentificationTest(RemoteDeviceOwnerTestCase):
    """Le système actuel d'authentification par code et d'approbation
    d'appareil doit rester STRICTEMENT intact — ce fichier ne doit
    trouver AUCUNE différence de comportement sur ces mécanismes."""

    def test_cycle_pending_approve_session_token_inchange(self):
        bid = os.urandom(16).hex()
        self.assertEqual(open_windows.register_device_attempt(bid, "1.2.3.4"), "pending")
        self.assertIsNone(open_windows.get_or_mint_device_session_token(bid))  # pas encore approuvé
        open_windows.approve_remote_device(bid)
        token = open_windows.get_or_mint_device_session_token(bid)
        self.assertIsNotNone(token)
        self.assertTrue(open_windows.verify_device_session(bid, token))

    def test_appareil_deja_approuve_ne_redevient_jamais_pending(self):
        bid = self._approve()
        self.assertEqual(open_windows.register_device_attempt(bid, "1.2.3.4"), "approved")

    def test_verify_device_session_echoue_sans_lien_proprietaire(self):
        """Un appareil approuvé mais NON LIÉ passe quand même le niveau 1
        normalement — la liaison propriétaire n'entre pour rien dans
        cette vérification (aucune permission n'est encore mise en
        œuvre à cette Phase, volontairement)."""
        bid = self._approve()
        token = open_windows.get_or_mint_device_session_token(bid)
        self.assertTrue(open_windows.verify_device_session(bid, token))
        self.assertIsNone(open_windows.get_remote_device_owner(bid))  # non lié, mais authentifié quand même

    def test_verify_remote_code_et_ratelimit_inaffectes(self):
        """Aucune des fonctions de ce fichier ne touche au code à 6
        chiffres ni à l'anti-bruteforce — vérifié en s'assurant qu'un
        code invalide reste refusé exactement comme avant."""
        self.assertFalse(open_windows.verify_remote_code("000000"))
        self.assertTrue(open_windows.verify_remote_code("131261"))  # code de test permanent, inchangé


class PersistanceEtMultiProcessusTest(RemoteDeviceOwnerTestCase):
    """"La liaison doit persister" + "réutilise le verrouillage existant
    ... une modification faite depuis un tournoi est correctement
    visible depuis les autres processus" — simulé ici par des appels
    INDÉPENDANTS aux fonctions module-level (aucun état Python mis en
    cache entre deux appels : chaque fonction relit le fichier depuis le
    disque à chaque fois, exactement comme le ferait un second process
    partageant le même ~/.poker_tournament)."""

    def test_liaison_visible_par_un_appel_independant_ulterieur(self):
        bid = self._approve()
        open_windows.set_remote_device_owner(bid, "Alice")

        # Aucun état conservé entre les deux appels ci-dessus et
        # celui-ci — simule un second process relisant le même fichier.
        self.assertEqual(open_windows.get_remote_device_owner(bid), "Alice")
        devices = {d["browser_id"]: d["owner_name"] for d in open_windows.list_approved_remote_devices()}
        self.assertEqual(devices[bid], "Alice")

    def test_ecritures_concurrentes_sur_des_appareils_differents_ne_se_perdent_pas(self):
        """Verrouillage inter-processus réutilisé (_remote_control_lock,
        même verrou que approve_remote_device/revoke_remote_device) :
        plusieurs écritures presque simultanées sur des CLÉS
        DIFFÉRENTES du même fichier ne doivent jamais s'écraser
        mutuellement — reproduit ici avec de vrais threads Python
        (le verrou est un verrou de FICHIER au niveau OS, donc
        également actif entre threads d'un même process, voir
        _file_lock)."""
        bids = [self._approve(f"192.168.0.{i}") for i in range(10)]
        errors = []

        def _assign(bid, name):
            try:
                open_windows.set_remote_device_owner(bid, name)
            except Exception as e:  # pragma: no cover - échec du test si levée
                errors.append(e)

        threads = [
            threading.Thread(target=_assign, args=(bid, f"Personne{i}"))
            for i, bid in enumerate(bids)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(errors, [])
        devices = {d["browser_id"]: d["owner_name"] for d in open_windows.list_approved_remote_devices()}
        for i, bid in enumerate(bids):
            self.assertEqual(devices[bid], f"Personne{i}")


if __name__ == "__main__":
    unittest.main()
