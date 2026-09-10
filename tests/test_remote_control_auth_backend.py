# -*- coding: utf-8 -*-
"""Couverture automatisée de la partie open_windows.py du durcissement
du contrôle à distance (demande du 2026-09-09, "sécurisation du
contrôle à distance" PUIS "approbation des téléphones") : code de
session à 6 chiffres, code de test permanent 131261, anti-bruteforce
(seuils navigateur=5/IP=10, escalade 1/5/15 min), registre PERSISTANT
des appareils approuvés séparé du fichier ÉPHÉMÈRE de session, et le
verrou inter-processus (_remote_control_lock) qui protège les deux.

Ne teste PAS le serveur HTTP lui-même (routes /login, /authenticate,
/auth_status, la protection de toutes les routes sensibles) — voir
tests/test_remote_control_device_approval_http.py pour ça — ni le
scénario multi-tournois à VRAIS sous-processus séparés — voir tests/
test_remote_control_multi_process_real_subprocess.py.

Registre/fichiers redirigés vers un dossier temporaire dédié (jamais
~/.poker_tournament, le vrai dossier de l'utilisateur) — même précaution
que tests/test_primes_enabled_toggle.py: PrimesSessionStartedTest."""
import os
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


class _TmpRemoteControlTest(unittest.TestCase):
    """Mixin/TestCase de base : redirige open_windows vers un dossier
    temporaire (registre des fenêtres ET fichiers de contrôle à
    distance)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_control_auth_backend_test_")
        self.addCleanup(self._tmp.cleanup)
        self._registry_path = os.path.join(self._tmp.name, "open_windows.json")
        self._remote_dir = os.path.join(self._tmp.name, "remote")
        os.makedirs(self._remote_dir, exist_ok=True)
        for target in (
            patch.object(open_windows, "_registry_path", return_value=self._registry_path),
            patch.object(open_windows, "_remote_control_dir", return_value=self._remote_dir),
        ):
            self.addCleanup(target.stop)
            target.start()

    def _open_session(self, name="session.tournoi"):
        path = os.path.join(self._tmp.name, name)
        open_windows.register(path)
        self.addCleanup(lambda: open_windows.unregister(path) if path in open_windows.list_open_paths() else None)
        return path


# =======================================================================
# 1. Code de session à 6 chiffres + code de test permanent.
# =======================================================================
class SessionCodeTest(_TmpRemoteControlTest):
    def test_aucune_session_active_pas_de_code(self):
        self.assertIsNone(open_windows.remote_session_code())

    def test_code_genere_a_6_chiffres(self):
        self._open_session()
        code = open_windows.remote_session_code()
        self.assertRegex(code, r"^\d{6}$")

    def test_code_stable_tant_que_la_session_reste_active(self):
        self._open_session("A.tournoi")
        code1 = open_windows.remote_session_code()
        self._open_session("B.tournoi")  # 2e tournoi de la MÊME session
        code2 = open_windows.remote_session_code()
        self.assertEqual(code1, code2)

    def test_nouvelle_session_genere_un_nouveau_code(self):
        path = self._open_session("A.tournoi")
        code1 = open_windows.remote_session_code()
        open_windows.unregister(path)
        self.assertIsNone(open_windows.remote_session_code())
        self._open_session("C.tournoi")
        code2 = open_windows.remote_session_code()
        self.assertNotEqual(code1, code2)

    def test_verify_remote_code_accepte_le_code_de_session(self):
        self._open_session()
        code = open_windows.remote_session_code()
        self.assertTrue(open_windows.verify_remote_code(code))

    def test_verify_remote_code_accepte_toujours_131261_en_plus(self):
        self._open_session()
        self.assertTrue(open_windows.verify_remote_code("131261"))

    def test_verify_remote_code_refuse_un_mauvais_code(self):
        self._open_session()
        code = open_windows.remote_session_code()
        wrong = "000000" if code != "000000" else "111111"
        self.assertFalse(open_windows.verify_remote_code(wrong))

    def test_verify_remote_code_refuse_les_formats_invalides(self):
        self._open_session()
        for bad in ("12345", "1234567", "abcdef", "", None, 131261, "12 345"):
            self.assertFalse(open_windows.verify_remote_code(bad), repr(bad))

    def test_131261_jamais_expose_par_remote_session_code(self):
        self._open_session()
        self.assertNotEqual(open_windows.remote_session_code(), "131261")


# =======================================================================
# 2. Anti-bruteforce : seuils, escalade, remise à zéro sur succès.
# =======================================================================
class RateLimitTest(_TmpRemoteControlTest):
    def test_non_bloque_au_depart(self):
        blocked, remaining = open_windows.remote_auth_rate_limit_status("nav1", "1.2.3.4")
        self.assertFalse(blocked)
        self.assertEqual(remaining, 0.0)

    def test_4_echecs_navigateur_ne_bloquent_pas_encore(self):
        for _ in range(4):
            open_windows.record_remote_auth_failure("nav1", "1.2.3.4")
        blocked, _ = open_windows.remote_auth_rate_limit_status("nav1", "1.2.3.4")
        self.assertFalse(blocked)

    def test_5e_echec_declenche_le_blocage_immediatement(self):
        """Correction explicite du 2026-09-09 : le blocage doit se
        déclencher SUR le 5e échec lui-même, jamais toléré une fois de
        plus avant de bloquer au 6e."""
        for _ in range(5):
            open_windows.record_remote_auth_failure("nav1", "1.2.3.4")
        blocked, remaining = open_windows.remote_auth_rate_limit_status("nav1", "1.2.3.4")
        self.assertTrue(blocked)
        self.assertLessEqual(remaining, 60.0)
        self.assertGreater(remaining, 0.0)

    def test_seuil_ip_est_10_distinct_du_seuil_navigateur(self):
        """10 navigateurs DIFFÉRENTS depuis la MÊME IP, chacun sous son
        propre seuil (4 échecs), ne doivent PAS déclencher le seuil
        navigateur (propre à chacun) mais DOIVENT finir par déclencher
        le seuil IP (partagé, plus large : 10)."""
        ip = "9.9.9.9"
        for i in range(10):
            open_windows.record_remote_auth_failure(f"nav{i}", ip)
        # Chaque navigateur pris individuellement est sous son propre seuil.
        blocked, _ = open_windows.remote_auth_rate_limit_status("nav0", ip)
        # nav0 lui-même n'a échoué qu'une fois -> pas bloqué SUR CETTE CLÉ,
        # mais l'IP partagée l'est déjà (seuil IP=10 atteint) : le pire des
        # deux (voir remote_auth_rate_limit_status) doit remonter bloqué.
        self.assertTrue(blocked, "le seuil IP partagé doit bloquer même un navigateur individuellement peu fautif")

    def test_echecs_sous_le_seuil_ip_ne_bloquent_pas(self):
        ip = "9.9.9.8"
        for i in range(9):
            open_windows.record_remote_auth_failure(f"navB{i}", ip)
        blocked, _ = open_windows.remote_auth_rate_limit_status("navB0", ip)
        self.assertFalse(blocked)

    def test_succes_remet_a_zero_navigateur_et_ip(self):
        open_windows.record_remote_auth_failure("nav2", "5.5.5.5")
        open_windows.record_remote_auth_failure("nav2", "5.5.5.5")
        open_windows.record_remote_auth_success("nav2", "5.5.5.5")
        # 4 nouveaux échecs après le succès : ne doivent PAS déclencher le
        # blocage (le compteur est reparti de zéro, pas de 2).
        for _ in range(4):
            open_windows.record_remote_auth_failure("nav2", "5.5.5.5")
        blocked, _ = open_windows.remote_auth_rate_limit_status("nav2", "5.5.5.5")
        self.assertFalse(blocked)

    def test_escalade_1min_puis_5min_puis_15min_plafond(self):
        browser, ip = "nav3", "6.6.6.6"

        def _trigger_block_and_expire_it():
            for _ in range(5):
                open_windows.record_remote_auth_failure(browser, ip)
            blocked, remaining = open_windows.remote_auth_rate_limit_status(browser, ip)
            self.assertTrue(blocked)
            # Force l'expiration du blocage (sans attendre réellement) en
            # réécrivant directement block_until dans le passé — même
            # principe que les tests d'expiration ailleurs dans ce projet.
            data = open_windows._read_json_or_empty(open_windows._remote_ratelimit_path())
            data[f"browser:{browser}"]["block_until"] = time.time() - 1
            open_windows._atomic_write_json(open_windows._remote_ratelimit_path(), data)
            return remaining

        remaining_1 = _trigger_block_and_expire_it()
        self.assertLessEqual(remaining_1, 60.0)

        remaining_2 = _trigger_block_and_expire_it()
        self.assertGreater(remaining_2, 60.0)
        self.assertLessEqual(remaining_2, 300.0)

        remaining_3 = _trigger_block_and_expire_it()
        self.assertGreater(remaining_3, 300.0)
        self.assertLessEqual(remaining_3, 900.0)

        # Plafond : une 4e série ne doit JAMAIS dépasser 15 minutes.
        remaining_4 = _trigger_block_and_expire_it()
        self.assertLessEqual(remaining_4, 900.0)

    def test_expiration_du_blocage_redonne_acces(self):
        browser, ip = "nav4", "7.7.7.7"
        for _ in range(5):
            open_windows.record_remote_auth_failure(browser, ip)
        blocked, _ = open_windows.remote_auth_rate_limit_status(browser, ip)
        self.assertTrue(blocked)
        data = open_windows._read_json_or_empty(open_windows._remote_ratelimit_path())
        data[f"browser:{browser}"]["block_until"] = time.time() - 1
        open_windows._atomic_write_json(open_windows._remote_ratelimit_path(), data)
        blocked, remaining = open_windows.remote_auth_rate_limit_status(browser, ip)
        self.assertFalse(blocked)
        self.assertEqual(remaining, 0.0)

    def test_131261_soumis_au_meme_compteur_que_le_vrai_code(self):
        """open_windows ne fait aucune distinction de comptage entre
        131261 et le code réel — la distinction (aucune route séparée)
        est de toute façon garantie par remote_control.py, vérifié
        séparément (voir test_remote_control_device_approval_http.py) ;
        ici on vérifie juste que verify_remote_code seul ne contourne
        rien côté anti-bruteforce (les compteurs sont manipulés par
        l'APPELANT, jamais par verify_remote_code lui-même)."""
        self._open_session()
        for _ in range(5):
            open_windows.record_remote_auth_failure("nav5", "8.8.8.8")
        blocked, _ = open_windows.remote_auth_rate_limit_status("nav5", "8.8.8.8")
        self.assertTrue(blocked)
        # Même bloqué, verify_remote_code elle-même reste une pure
        # fonction de vérification (pas de logique de blocage ici) :
        # c'est à l'appelant de consulter le statut AVANT de l'appeler.
        self.assertTrue(open_windows.verify_remote_code("131261"))


# =======================================================================
# 3. Registre PERSISTANT des appareils : pending/approved/revoked.
# =======================================================================
class DeviceRegistryTest(_TmpRemoteControlTest):
    def test_nouvel_appareil_devient_pending(self):
        self._open_session()
        status = open_windows.register_device_attempt("bid1" * 8, "1.1.1.1")
        self.assertEqual(status, "pending")
        pending = open_windows.list_pending_remote_devices()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["browser_id"], "bid1" * 8)

    def test_rechargement_ne_multiplie_pas_les_demandes(self):
        self._open_session()
        bid = "bid2" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.register_device_attempt(bid, "1.1.1.2")  # IP différente, même appareil
        self.assertEqual(len(open_windows.list_pending_remote_devices()), 1)

    def test_short_id_6_caracteres_hex(self):
        self._open_session()
        open_windows.register_device_attempt("bid3" * 8, "1.1.1.1")
        short_id = open_windows.list_pending_remote_devices()[0]["short_id"]
        self.assertRegex(short_id, r"^[0-9A-F]{6}$")

    def test_short_id_unique_parmi_plusieurs_demandes(self):
        self._open_session()
        for i in range(15):
            open_windows.register_device_attempt(f"bidmulti{i}" + "0" * 20, f"1.1.1.{i}")
        short_ids = [d["short_id"] for d in open_windows.list_pending_remote_devices()]
        self.assertEqual(len(short_ids), len(set(short_ids)), "aucune collision de short_id attendue")

    def test_approbation_donne_le_statut_approved(self):
        self._open_session()
        bid = "bid4" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertTrue(open_windows.approve_remote_device(bid, label="Salle 1 - Jean"))
        self.assertEqual(open_windows.list_pending_remote_devices(), [])
        approved = open_windows.list_approved_remote_devices()
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["label"], "Salle 1 - Jean")

    def test_approbation_d_un_appareil_inconnu_echoue(self):
        self.assertFalse(open_windows.approve_remote_device("inconnu" * 5))

    def test_appareil_approuve_reste_approuve_a_une_nouvelle_tentative(self):
        self._open_session()
        bid = "bid5" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        status = open_windows.register_device_attempt(bid, "1.1.1.9")  # nouvelle IP, même appareil
        self.assertEqual(status, "approved")
        self.assertEqual(len(open_windows.list_pending_remote_devices()), 0)

    def test_refus_dune_demande_pending_la_fait_disparaitre(self):
        self._open_session()
        bid = "bid6" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertTrue(open_windows.revoke_remote_device(bid))
        self.assertEqual(open_windows.list_pending_remote_devices(), [])
        self.assertEqual(open_windows.get_device_auth_status(bid), "refused")

    def test_revocation_dun_appareil_approuve(self):
        self._open_session()
        bid = "bid7" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        self.assertTrue(open_windows.revoke_remote_device(bid))
        self.assertEqual(open_windows.list_approved_remote_devices(), [])
        self.assertEqual(open_windows.get_device_auth_status(bid), "refused")

    def test_appareil_revoque_qui_revient_redevient_pending_jamais_auto_approuve(self):
        self._open_session()
        bid = "bid8" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        open_windows.revoke_remote_device(bid)
        status = open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertEqual(status, "pending")
        self.assertEqual(open_windows.list_approved_remote_devices(), [])

    def test_revocation_dun_appareil_inconnu_ne_plante_pas(self):
        self.assertFalse(open_windows.revoke_remote_device("jamais_vu" * 3))

    def test_get_device_auth_status_appareil_jamais_vu_est_pending(self):
        self.assertEqual(open_windows.get_device_auth_status("jamais_vu_non_plus" * 2), "pending")

    def test_get_device_auth_status_sans_browser_id_est_pending(self):
        self.assertEqual(open_windows.get_device_auth_status(None), "pending")
        self.assertEqual(open_windows.get_device_auth_status(""), "pending")

    def test_demande_expiree_disparait_de_la_liste_pending(self):
        self._open_session()
        bid = "bidexp" * 6
        open_windows.register_device_attempt(bid, "1.1.1.1")
        devices = open_windows._read_json_or_empty(open_windows._remote_devices_path())
        devices[bid]["requested_at"] = time.time() - open_windows._REMOTE_DEVICE_PENDING_TTL_SECONDS - 1
        open_windows._atomic_write_json(open_windows._remote_devices_path(), devices)
        self.assertEqual(open_windows.list_pending_remote_devices(), [])

    def test_retentative_apres_expiration_redepose_une_demande_fraiche(self):
        self._open_session()
        bid = "bidexp2" * 5
        open_windows.register_device_attempt(bid, "1.1.1.1")
        devices = open_windows._read_json_or_empty(open_windows._remote_devices_path())
        devices[bid]["requested_at"] = time.time() - open_windows._REMOTE_DEVICE_PENDING_TTL_SECONDS - 1
        open_windows._atomic_write_json(open_windows._remote_devices_path(), devices)
        status = open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertEqual(status, "pending")
        pending = open_windows.list_pending_remote_devices()
        self.assertEqual(len(pending), 1)  # une seule entrée, jamais dupliquée


# =======================================================================
# 4. Jeton de session par appareil : minting idempotent, vérification à
#    deux conditions, invalidation immédiate à la révocation, frontière
#    de session (fichier ÉPHÉMÈRE distinct du registre PERSISTANT).
# =======================================================================
class DeviceSessionTokenTest(_TmpRemoteControlTest):
    def test_pas_de_jeton_pour_un_appareil_non_approuve(self):
        self._open_session()
        bid = "tok1" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertIsNone(open_windows.get_or_mint_device_session_token(bid))

    def test_jeton_delivre_pour_un_appareil_approuve(self):
        self._open_session()
        bid = "tok2" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        token = open_windows.get_or_mint_device_session_token(bid)
        self.assertIsNotNone(token)
        self.assertEqual(len(token), 64)  # secrets.token_hex(32)

    def test_minting_idempotent(self):
        self._open_session()
        bid = "tok3" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        token1 = open_windows.get_or_mint_device_session_token(bid)
        token2 = open_windows.get_or_mint_device_session_token(bid)
        self.assertEqual(token1, token2)

    def test_verify_device_session_accepte_le_bon_jeton(self):
        self._open_session()
        bid = "tok4" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        token = open_windows.get_or_mint_device_session_token(bid)
        self.assertTrue(open_windows.verify_device_session(bid, token))

    def test_verify_device_session_refuse_un_mauvais_jeton(self):
        self._open_session()
        bid = "tok5" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        open_windows.get_or_mint_device_session_token(bid)
        self.assertFalse(open_windows.verify_device_session(bid, "0" * 64))

    def test_verify_device_session_refuse_un_browser_id_invente(self):
        """Un attaquant qui invente un browser_id au hasard (jamais
        enregistré) ne peut jamais correspondre à une entrée "approved"
        — même en essayant de deviner/forger un jeton quelconque."""
        self._open_session()
        self.assertFalse(open_windows.verify_device_session("x" * 32, "y" * 64))

    def test_revocation_invalide_immediatement_le_jeton_en_cours(self):
        self._open_session()
        bid = "tok6" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        token = open_windows.get_or_mint_device_session_token(bid)
        self.assertTrue(open_windows.verify_device_session(bid, token))
        open_windows.revoke_remote_device(bid)
        self.assertFalse(open_windows.verify_device_session(bid, token))
        self.assertIsNone(open_windows.get_or_mint_device_session_token(bid))

    def test_jeton_ne_survit_pas_a_la_fin_de_session_mais_l_approbation_survit(self):
        path = self._open_session("S1.tournoi")
        bid = "tok7" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        token = open_windows.get_or_mint_device_session_token(bid)
        self.assertTrue(open_windows.verify_device_session(bid, token))

        open_windows.unregister(path)  # fin de session (dernier tournoi fermé)

        # Le jeton (fichier ÉPHÉMÈRE) est mort : plus aucune session active.
        self.assertFalse(open_windows.verify_device_session(bid, token))
        # L'approbation (registre PERSISTANT) a survécu.
        approved = open_windows.list_approved_remote_devices()
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0]["browser_id"], bid)

    def test_nouvelle_session_appareil_approuve_plus_ancien_code_recoit_un_nouveau_jeton_immediatement(self):
        path1 = self._open_session("S2.tournoi")
        bid = "tok8" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        old_token = open_windows.get_or_mint_device_session_token(bid)
        old_code = open_windows.remote_session_code()
        open_windows.unregister(path1)

        self._open_session("S3.tournoi")
        new_code = open_windows.remote_session_code()
        self.assertNotEqual(old_code, new_code)

        # L'ancien jeton ne fonctionne plus dans la nouvelle session.
        self.assertFalse(open_windows.verify_device_session(bid, old_token))

        # Mais l'appareil est TOUJOURS approuvé -> accès immédiat, un
        # NOUVEAU jeton est délivré sans repasser par une approbation.
        status = open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertEqual(status, "approved")
        new_token = open_windows.get_or_mint_device_session_token(bid)
        self.assertNotEqual(new_token, old_token)
        self.assertTrue(open_windows.verify_device_session(bid, new_token))

    def test_appareil_revoque_a_une_session_precedente_reste_revoque_a_la_suivante(self):
        path1 = self._open_session("S4.tournoi")
        bid = "tok9" * 8
        open_windows.register_device_attempt(bid, "1.1.1.1")
        open_windows.approve_remote_device(bid)
        open_windows.revoke_remote_device(bid)
        open_windows.unregister(path1)

        self._open_session("S5.tournoi")
        status = open_windows.register_device_attempt(bid, "1.1.1.1")
        self.assertEqual(status, "pending", "un appareil révoqué ne doit jamais retrouver l'accès automatiquement")
        self.assertIsNone(open_windows.get_or_mint_device_session_token(bid))

    def test_deux_appareils_differents_approbations_independantes(self):
        self._open_session()
        bid_x, bid_y = "devx" * 8, "devy" * 8
        open_windows.register_device_attempt(bid_x, "1.1.1.1")
        open_windows.register_device_attempt(bid_y, "1.1.1.2")
        open_windows.approve_remote_device(bid_x, label="X")
        # Y reste en attente.
        self.assertEqual(open_windows.get_device_auth_status(bid_x), "approved")
        self.assertEqual(open_windows.get_device_auth_status(bid_y), "pending")
        token_x = open_windows.get_or_mint_device_session_token(bid_x)
        self.assertTrue(open_windows.verify_device_session(bid_x, token_x))
        self.assertIsNone(open_windows.get_or_mint_device_session_token(bid_y))
        # Révoquer X ne doit rien changer pour Y.
        open_windows.revoke_remote_device(bid_x)
        self.assertEqual(open_windows.get_device_auth_status(bid_y), "pending")


if __name__ == "__main__":
    unittest.main()
