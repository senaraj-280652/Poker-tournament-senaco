# -*- coding: utf-8 -*-
"""Code personnel permanent (demande du 2026-10-01, "onglet CA : code
personnel permanent") — troisième code accepté par open_windows.
verify_remote_code, EN PLUS du code de session et du code de test
permanent (131261), jamais à la place. Choisi par l'utilisateur,
contrairement au code de test : doit donc rester CONFIDENTIEL — jamais
écrit en clair (voir set_personal_remote_code, qui ne persiste qu'un
sel + un hachage salé PBKDF2-HMAC-SHA256), jamais journalisé, jamais
réaffiché une fois enregistré.

Couvre :
1. set_personal_remote_code/has_personal_remote_code/clear_personal_
   remote_code — stockage, jamais le code en clair sur disque.
2. verify_remote_code — accepte le code personnel EN PLUS des deux
   autres, fonctionne indépendamment de toute session active (à la
   différence du code de session), aucune incidence sur le code de
   session ni sur le code de test.
3. Non-dépendance au multi-tournois : accepté quel que soit l'état de
   la session (aucune session, une session, plusieurs tournois
   ouverts).
4. App._on_set_personal_remote_code/_on_clear_personal_remote_code
   (onglet CA, Mac) : validation, confirmation, champ vidé
   immédiatement, jamais de message contenant le code.

SÉCURISATION (demande du 2026-10-02, "sécurisation du code personnel
permanent") — couvre en plus :
5. open_windows.verify_personal_code_change_authorization : exige le
   code personnel ACTUEL une fois un code configuré (jamais le code
   initial, qui échoue systématiquement dans ce cas — POINT DE SÉCURITÉ
   central : ne doit JAMAIS devenir une porte dérobée permanente), ou le
   code initial tant qu'aucun code n'existe encore.
6. Anti-bruteforce LOCAL (personal_code_rate_limit_status/_record_
   personal_code_attempt) : blocage progressif après échecs répétés,
   partagé par le code personnel courant ET le code initial.
7. App._prompt_personal_code_authorization : boîte de dialogue masquée,
   jamais de code affiché/journalisé, blocage respecté avant toute
   saisie.
8. Non-contournement : _on_set_personal_remote_code/_on_clear_personal_
   remote_code passent TOUJOURS par cette autorisation (vérification
   structurelle), jamais une voie directe vers set_personal_remote_code/
   clear_personal_remote_code."""
import os
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402
import open_windows  # noqa: E402


class _TmpRemoteControlTest(unittest.TestCase):
    """Même principe que tests/test_remote_control_auth_backend.py :
    redirige open_windows vers un dossier temporaire, jamais le vrai
    ~/.poker_tournament."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="personal_remote_code_test_")
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


# =====================================================================
# 1. Stockage — jamais le code en clair.
# =====================================================================
class PersonalCodeStorageTest(_TmpRemoteControlTest):
    def test_aucun_code_personnel_par_defaut(self):
        self.assertFalse(open_windows.has_personal_remote_code())

    def test_set_puis_has_devient_vrai(self):
        open_windows.set_personal_remote_code("482913")
        self.assertTrue(open_windows.has_personal_remote_code())

    def test_le_code_en_clair_n_est_jamais_ecrit_sur_disque(self):
        """Point central de la demande : le fichier créé ne doit
        contenir ni le code tapé, ni aucune de ses sous-chaînes
        évidentes — seulement un sel et un hachage, en hexadécimal."""
        code = "482913"
        open_windows.set_personal_remote_code(code)
        path = open_windows._personal_remote_code_path()
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn(code, raw)
        import json
        data = json.loads(raw)
        self.assertEqual(set(data.keys()), {"salt", "hash", "iterations", "ratelimit"})
        # Hex uniquement : jamais le code lui-même ou une transformation
        # triviale (base64 du code, etc.) ne passerait ce test par
        # coïncidence vu la longueur (32+ caractères hex attendus).
        bytes.fromhex(data["salt"])
        bytes.fromhex(data["hash"])

    def test_deux_codes_differents_produisent_des_sels_differents(self):
        """Un sel FIXE réutilisé affaiblirait la protection (tables
        arc-en-ciel précalculées) — chaque définition doit tirer un sel
        neuf, même pour le même code redéfini à l'identique."""
        open_windows.set_personal_remote_code("111111")
        path = open_windows._personal_remote_code_path()
        with open(path, "r", encoding="utf-8") as f:
            import json
            salt1 = json.load(f)["salt"]
        open_windows.set_personal_remote_code("111111")
        with open(path, "r", encoding="utf-8") as f:
            import json
            salt2 = json.load(f)["salt"]
        self.assertNotEqual(salt1, salt2)

    def test_redefinir_remplace_entierement_l_ancien(self):
        open_windows.set_personal_remote_code("111111")
        self.assertTrue(open_windows.verify_remote_code("111111"))
        open_windows.set_personal_remote_code("222222")
        self.assertFalse(open_windows.verify_remote_code("111111"))
        self.assertTrue(open_windows.verify_remote_code("222222"))

    def test_clear_supprime_le_code(self):
        open_windows.set_personal_remote_code("482913")
        open_windows.clear_personal_remote_code()
        self.assertFalse(open_windows.has_personal_remote_code())
        self.assertFalse(open_windows.verify_remote_code("482913"))

    def test_clear_sans_code_defini_ne_leve_rien(self):
        open_windows.clear_personal_remote_code()  # ne doit pas lever

    def test_refuse_un_format_invalide(self):
        for bad in ("12345", "1234567", "abcdef", "", None, 482913, "12 345"):
            with self.assertRaises(ValueError):
                open_windows.set_personal_remote_code(bad)
        self.assertFalse(open_windows.has_personal_remote_code())

    def test_message_derreur_ne_contient_jamais_le_code_refuse(self):
        try:
            open_windows.set_personal_remote_code("abcdef")
        except ValueError as e:
            self.assertNotIn("abcdef", str(e))
        else:
            self.fail("ValueError attendue")


# =====================================================================
# 2-3. verify_remote_code — en plus, jamais à la place, indépendant de
#      toute session/multi-tournois.
# =====================================================================
class VerifyRemoteCodeWithPersonalCodeTest(_TmpRemoteControlTest):
    def test_code_personnel_accepte_sans_aucune_session_active(self):
        """Différence structurelle avec le code de session : doit
        fonctionner MÊME quand aucun tournoi n'est ouvert — c'est tout
        l'intérêt d'un code "permanent" (voir open_windows.remote_
        session_code, qui renvoie None dans ce cas)."""
        self.assertIsNone(open_windows.remote_session_code())
        open_windows.set_personal_remote_code("482913")
        self.assertTrue(open_windows.verify_remote_code("482913"))

    def test_code_personnel_accepte_avec_plusieurs_tournois_ouverts(self):
        """Non-régression multi-tournois : identique quel que soit le
        nombre de tournois ouverts simultanément dans la session."""
        open_windows.set_personal_remote_code("482913")
        self._open_session("A.tournoi")
        self._open_session("B.tournoi")
        self.assertTrue(open_windows.verify_remote_code("482913"))

    def test_code_de_session_toujours_accepte_en_plus(self):
        """Non-régression : le code personnel s'AJOUTE, ne remplace
        jamais le code de session."""
        self._open_session()
        session_code = open_windows.remote_session_code()
        open_windows.set_personal_remote_code("482913")
        self.assertTrue(open_windows.verify_remote_code(session_code))
        self.assertTrue(open_windows.verify_remote_code("482913"))

    def test_code_de_test_toujours_accepte_en_plus(self):
        open_windows.set_personal_remote_code("482913")
        self.assertTrue(open_windows.verify_remote_code("131261"))

    def test_mauvais_code_toujours_refuse(self):
        open_windows.set_personal_remote_code("482913")
        self.assertFalse(open_windows.verify_remote_code("000000"))

    def test_sans_code_personnel_defini_code_de_session_et_de_test_fonctionnent_toujours(self):
        """Non-régression : code de session et code de test continuent de
        fonctionner normalement quand aucun code personnalisé n'est
        défini (depuis le 2026-10-03, "code personnel par défaut", ce cas
        accepte AUSSI 280652 — voir DefaultPersonalCodeTest ci-dessous —
        mais n'a rien changé au reste)."""
        self._open_session()
        session_code = open_windows.remote_session_code()
        self.assertTrue(open_windows.verify_remote_code(session_code))
        self.assertTrue(open_windows.verify_remote_code("131261"))
        self.assertFalse(open_windows.verify_remote_code("000000"))

    def test_fichier_personnel_absent_ne_leve_jamais_dexception(self):
        """has_personal_remote_code à False -> _verify_personal_remote_
        code doit renvoyer False proprement, jamais une exception qui
        ferait échouer toute authentification."""
        self.assertFalse(open_windows.has_personal_remote_code())
        self.assertFalse(open_windows.verify_remote_code("482913"))

    def test_fichier_personnel_corrompu_est_traite_comme_absent(self):
        open_windows.set_personal_remote_code("482913")
        path = open_windows._personal_remote_code_path()
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        self.assertFalse(open_windows.verify_remote_code("482913"))

    def test_ratelimit_et_approbation_inaffectes_par_le_code_personnel(self):
        """Non-régression du reste de la chaîne d'authentification : un
        succès via le code personnel enclenche exactement le même
        parcours (register_device_attempt) qu'un succès via le code de
        session ou le code de test — rien de spécifique au code
        personnel n'est bypassé."""
        open_windows.set_personal_remote_code("482913")
        self._open_session()
        status = open_windows.register_device_attempt("browser-1", "127.0.0.1")
        self.assertEqual(status, "pending")


# =====================================================================
# 3bis. Code personnel PAR DÉFAUT (demande du 2026-10-03, "masquage de
#       la section code personnel et code par défaut") — 280652 permet
#       de connecter un téléphone TANT qu'aucun code personnalisé
#       n'existe, en plus d'autoriser sa première création (déjà couvert
#       par VerifyPersonalCodeChangeAuthorizationTest).
# =====================================================================
class DefaultPersonalCodeTest(_TmpRemoteControlTest):
    def test_code_par_defaut_connecte_un_telephone_sans_code_personnalise(self):
        self.assertFalse(open_windows.has_personal_remote_code())
        self.assertTrue(open_windows.verify_remote_code("280652"))

    def test_code_par_defaut_fonctionne_sans_aucune_session_active(self):
        """Même propriété que le vrai code personnel (voir Verify
        RemoteCodeWithPersonalCodeTest) : indépendant de toute session,
        puisqu'il en tient lieu tant qu'aucun code n'est personnalisé."""
        self.assertIsNone(open_windows.remote_session_code())
        self.assertTrue(open_windows.verify_remote_code("280652"))

    def test_code_par_defaut_refuse_une_fois_un_code_personnalise_defini(self):
        """POINT DE SÉCURITÉ central (déjà posé le 2026-10-02, étendu ici
        à la connexion téléphone) : 280652 cesse ENTIÈREMENT de
        fonctionner dès qu'un code personnalisé existe — jamais un accès
        parallèle permanent."""
        open_windows.set_personal_remote_code("111111")
        self.assertFalse(open_windows.verify_remote_code("280652"))
        self.assertTrue(open_windows.verify_remote_code("111111"))

    def test_code_personnalise_remplace_entierement_apres_redefinition(self):
        open_windows.set_personal_remote_code("111111")
        open_windows.set_personal_remote_code("222222")
        self.assertFalse(open_windows.verify_remote_code("280652"))
        self.assertFalse(open_windows.verify_remote_code("111111"))
        self.assertTrue(open_windows.verify_remote_code("222222"))

    def test_code_par_defaut_redevient_actif_apres_suppression_du_code_personnalise(self):
        """Non-régression cohérente avec has_personal_remote_code() :
        supprimer le code personnalisé fait retomber sur le code par
        défaut, exactement comme avant toute personnalisation."""
        open_windows.set_personal_remote_code("111111")
        open_windows.clear_personal_remote_code()
        self.assertTrue(open_windows.verify_remote_code("280652"))

    def test_code_de_session_et_de_test_restent_acceptes_en_plus_du_defaut(self):
        self._open_session()
        session_code = open_windows.remote_session_code()
        self.assertTrue(open_windows.verify_remote_code("280652"))
        self.assertTrue(open_windows.verify_remote_code(session_code))
        self.assertTrue(open_windows.verify_remote_code("131261"))

    def test_code_par_defaut_accepte_avec_plusieurs_tournois_ouverts(self):
        self._open_session("A.tournoi")
        self._open_session("B.tournoi")
        self.assertTrue(open_windows.verify_remote_code("280652"))


# =====================================================================
# 4. App._on_set_personal_remote_code / _on_clear_personal_remote_code
#    (onglet CA, Mac) — validation, confirmation, jamais le code dans
#    un message affiché.
# =====================================================================
class _FakeAppForPersonalCode:
    def __init__(self, code_value=""):
        self.personal_code_var = types.SimpleNamespace(
            _value=code_value,
            get=lambda: self.personal_code_var._value,
            set=lambda v: setattr(self.personal_code_var, "_value", v),
        )
        self.status_refreshed = 0
        # Greffé comme les autres méthodes App greffées ailleurs dans
        # cette suite : _on_set_personal_remote_code/_on_clear_personal_
        # remote_code appellent self._prompt_personal_code_authorization
        # (demande du 2026-10-02, "sécurisation du code personnel
        # permanent"), qui doit donc exister sur cette doublure légère.
        self._prompt_personal_code_authorization = types.MethodType(
            main.App._prompt_personal_code_authorization, self
        )

    def _refresh_personal_remote_code_status(self):
        self.status_refreshed += 1


class SetPersonalRemoteCodeHandlerTest(_TmpRemoteControlTest):
    def test_premiere_creation_exige_le_code_initial(self):
        """Aucun code personnel encore configuré : le code INITIAL doit
        être demandé et accepté avant toute création."""
        app = _FakeAppForPersonalCode("482913")
        with patch.object(main.simpledialog, "askstring", return_value="280652") as mock_ask, \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._on_set_personal_remote_code(app)

        mock_ask.assert_called_once()
        self.assertIn("initial", mock_ask.call_args[0][1].lower())
        self.assertTrue(open_windows.has_personal_remote_code())
        self.assertTrue(open_windows.verify_remote_code("482913"))
        self.assertEqual(app.personal_code_var.get(), "")
        self.assertEqual(app.status_refreshed, 1)
        mock_info.assert_called_once()

    def test_premiere_creation_refusee_si_code_initial_incorrect(self):
        app = _FakeAppForPersonalCode("482913")
        with patch.object(main.simpledialog, "askstring", return_value="000000"), \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._on_set_personal_remote_code(app)

        mock_error.assert_called_once()
        self.assertFalse(open_windows.has_personal_remote_code())

    def test_annuler_la_boite_de_dialogue_naccorde_rien(self):
        """simpledialog.askstring renvoie None quand l'utilisateur annule
        (Échap/Annuler) — doit être traité comme un refus, jamais comme
        une chaîne vide acceptée par erreur."""
        app = _FakeAppForPersonalCode("482913")
        with patch.object(main.simpledialog, "askstring", return_value=None):
            main.App._on_set_personal_remote_code(app)

        self.assertFalse(open_windows.has_personal_remote_code())

    def test_changement_exige_le_code_personnel_actuel(self):
        """Un code personnel est DÉJÀ configuré : le changer exige CE
        code actuel — jamais le code initial, qui doit échouer ici (point
        de sécurité central)."""
        open_windows.set_personal_remote_code("111111")
        app = _FakeAppForPersonalCode("222222")
        with patch.object(main.simpledialog, "askstring", return_value="111111") as mock_ask, \
             patch.object(main.messagebox, "showinfo"):
            main.App._on_set_personal_remote_code(app)

        self.assertIn("actuel", mock_ask.call_args[0][1].lower())
        self.assertTrue(open_windows.verify_remote_code("222222"))
        self.assertFalse(open_windows.verify_remote_code("111111"))

    def test_changement_refuse_si_mauvais_code_actuel(self):
        open_windows.set_personal_remote_code("111111")
        app = _FakeAppForPersonalCode("222222")
        with patch.object(main.simpledialog, "askstring", return_value="999999"), \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._on_set_personal_remote_code(app)

        mock_error.assert_called_once()
        # Le code reste INCHANGÉ — ni remplacé, ni effacé.
        self.assertTrue(open_windows.verify_remote_code("111111"))
        self.assertFalse(open_windows.verify_remote_code("222222"))

    def test_code_initial_ne_fonctionne_jamais_une_fois_un_code_configure(self):
        """POINT DE SÉCURITÉ explicitement demandé : le code initial ne
        doit JAMAIS devenir une porte dérobée permanente capable de
        remplacer un code déjà configuré."""
        open_windows.set_personal_remote_code("111111")
        app = _FakeAppForPersonalCode("222222")
        with patch.object(main.simpledialog, "askstring", return_value="280652"), \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._on_set_personal_remote_code(app)

        mock_error.assert_called_once()
        self.assertTrue(open_windows.verify_remote_code("111111"))
        self.assertFalse(open_windows.verify_remote_code("222222"))

    def test_format_invalide_refuse_avant_meme_de_demander_lautorisation(self):
        app = _FakeAppForPersonalCode("12ab")
        with patch.object(main.simpledialog, "askstring") as mock_ask, \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._on_set_personal_remote_code(app)

        mock_ask.assert_not_called()
        self.assertFalse(open_windows.has_personal_remote_code())
        self.assertEqual(app.personal_code_var.get(), "")
        mock_error.assert_called_once()

    def test_champ_vide_est_vide_meme_en_cas_de_refus(self):
        """Le champ ne doit JAMAIS garder la saisie, succès ou échec —
        la valeur tapée ne doit jamais rester affichée plus longtemps
        que nécessaire."""
        app = _FakeAppForPersonalCode("000")
        with patch.object(main.messagebox, "showerror"):
            main.App._on_set_personal_remote_code(app)
        self.assertEqual(app.personal_code_var.get(), "")

    def test_aucun_message_ne_contient_jamais_le_code_saisi_ou_lautorisation(self):
        app = _FakeAppForPersonalCode("482913")
        with patch.object(main.simpledialog, "askstring", return_value="280652"), \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._on_set_personal_remote_code(app)
        for call in mock_info.call_args_list:
            for arg in list(call.args) + list(call.kwargs.values()):
                self.assertNotIn("482913", str(arg))
                self.assertNotIn("280652", str(arg))

    def test_bloque_apres_trop_dechecs_meme_avec_le_bon_code_initial(self):
        """Anti-bruteforce local : 5 échecs consécutifs déclenchent un
        blocage — le code correct n'est alors même plus évalué. Les 5
        échecs sont provoqués directement via open_windows (plus simple
        et plus sûr qu'un même faux self.personal_code_var réutilisé 5
        fois, qui se viderait après la 1re tentative et biaiserait le
        compte) ; seul le comportement DU HANDLER face à un blocage déjà
        actif est ensuite vérifié, ce qui est le point de ce test."""
        for _ in range(5):
            open_windows.verify_personal_code_change_authorization("000000")
        blocked, _ = open_windows.personal_code_rate_limit_status()
        self.assertTrue(blocked, "précondition : le blocage doit déjà être actif")

        app2 = _FakeAppForPersonalCode("482913")
        with patch.object(main.simpledialog, "askstring", return_value="280652") as mock_ask, \
             patch.object(main.messagebox, "showerror") as mock_error:
            main.App._on_set_personal_remote_code(app2)

        mock_ask.assert_not_called()  # bloqué avant même d'afficher la boîte de dialogue
        mock_error.assert_called_once()
        self.assertIn("tentatives", mock_error.call_args[0][1].lower())
        self.assertFalse(open_windows.has_personal_remote_code())


class ClearPersonalRemoteCodeHandlerTest(_TmpRemoteControlTest):
    def test_exige_le_code_personnel_actuel_avant_confirmation(self):
        open_windows.set_personal_remote_code("482913")
        app = _FakeAppForPersonalCode()

        with patch.object(main.simpledialog, "askstring", return_value="482913") as mock_ask, \
             patch.object(main.messagebox, "askyesno", return_value=False) as mock_yesno:
            main.App._on_clear_personal_remote_code(app)

        mock_ask.assert_called_once()
        mock_yesno.assert_called_once()
        self.assertTrue(open_windows.has_personal_remote_code())
        self.assertEqual(app.status_refreshed, 0)

    def test_refuse_si_mauvais_code_actuel_jamais_de_confirmation_demandee(self):
        open_windows.set_personal_remote_code("482913")
        app = _FakeAppForPersonalCode()

        with patch.object(main.simpledialog, "askstring", return_value="000000"), \
             patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.messagebox, "askyesno") as mock_yesno:
            main.App._on_clear_personal_remote_code(app)

        mock_error.assert_called_once()
        mock_yesno.assert_not_called()
        self.assertTrue(open_windows.has_personal_remote_code())

    def test_confirmation_acceptee_supprime(self):
        open_windows.set_personal_remote_code("482913")
        app = _FakeAppForPersonalCode()

        with patch.object(main.simpledialog, "askstring", return_value="482913"), \
             patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._on_clear_personal_remote_code(app)

        self.assertFalse(open_windows.has_personal_remote_code())
        self.assertEqual(app.status_refreshed, 1)

    def test_sans_code_defini_aucune_question_posee(self):
        app = _FakeAppForPersonalCode()

        with patch.object(main.simpledialog, "askstring") as mock_ask, \
             patch.object(main.messagebox, "askyesno") as mock_yesno:
            main.App._on_clear_personal_remote_code(app)

        mock_ask.assert_not_called()
        mock_yesno.assert_not_called()
        self.assertEqual(app.status_refreshed, 0)


# =====================================================================
# 5-6. open_windows.verify_personal_code_change_authorization +
#      anti-bruteforce local.
# =====================================================================
class VerifyPersonalCodeChangeAuthorizationTest(_TmpRemoteControlTest):
    def test_aucun_code_configure_exige_le_code_initial(self):
        self.assertTrue(open_windows.verify_personal_code_change_authorization("280652"))

    def test_aucun_code_configure_refuse_un_mauvais_code_initial(self):
        self.assertFalse(open_windows.verify_personal_code_change_authorization("000000"))

    def test_code_deja_configure_exige_ce_code_actuel(self):
        open_windows.set_personal_remote_code("111111")
        self.assertTrue(open_windows.verify_personal_code_change_authorization("111111"))

    def test_code_initial_echoue_une_fois_un_code_configure(self):
        """POINT DE SÉCURITÉ central, testé directement au niveau de la
        fonction qui prend la décision (pas seulement via l'UI) : 280652
        ne doit JAMAIS réussir une fois un code personnel configuré."""
        open_windows.set_personal_remote_code("111111")
        self.assertFalse(open_windows.verify_personal_code_change_authorization("280652"))

    def test_refuse_les_formats_invalides(self):
        for bad in ("12345", "1234567", "abcdef", "", None, 280652):
            self.assertFalse(open_windows.verify_personal_code_change_authorization(bad))

    def test_echec_incremente_le_compteur_anti_bruteforce(self):
        blocked_before, _ = open_windows.personal_code_rate_limit_status()
        self.assertFalse(blocked_before)
        for _ in range(4):
            open_windows.verify_personal_code_change_authorization("000000")
        blocked_still, _ = open_windows.personal_code_rate_limit_status()
        self.assertFalse(blocked_still)  # seuil (5) pas encore atteint
        open_windows.verify_personal_code_change_authorization("000000")
        blocked_now, remaining = open_windows.personal_code_rate_limit_status()
        self.assertTrue(blocked_now)
        self.assertGreater(remaining, 0)

    def test_bloque_meme_le_bon_code(self):
        """Une fois bloqué, même le bon code initial échoue — le blocage
        doit être vérifié par l'APPELANT avant d'évaluer quoi que ce
        soit (voir App._prompt_personal_code_authorization)."""
        for _ in range(5):
            open_windows.verify_personal_code_change_authorization("000000")
        blocked, _ = open_windows.personal_code_rate_limit_status()
        self.assertTrue(blocked)
        # La fonction elle-même continuerait d'évaluer si on l'appelait
        # directement (c'est à l'appelant de respecter le blocage) :
        # documenté ici pour clarifier la responsabilité, pas un bug.
        self.assertTrue(open_windows.verify_personal_code_change_authorization("280652"))

    def test_succes_reinitialise_le_compteur(self):
        for _ in range(4):
            open_windows.verify_personal_code_change_authorization("000000")
        self.assertTrue(open_windows.verify_personal_code_change_authorization("280652"))
        blocked, _ = open_windows.personal_code_rate_limit_status()
        self.assertFalse(blocked)

    def test_blocage_expire_avec_le_temps(self):
        for _ in range(5):
            open_windows.verify_personal_code_change_authorization("000000")
        blocked, _ = open_windows.personal_code_rate_limit_status()
        self.assertTrue(blocked)
        # Fait "avancer le temps" en reculant directement block_until,
        # plutôt qu'un vrai sleep(60) — même principe que les tests
        # d'anti-bruteforce réseau existants.
        path = open_windows._personal_remote_code_path()
        data = open_windows._read_json_or_empty(path)
        data["ratelimit"]["block_until"] = time.time() - 1
        open_windows._atomic_write_json(path, data)
        blocked_after, _ = open_windows.personal_code_rate_limit_status()
        self.assertFalse(blocked_after)


# =====================================================================
# 7. App._prompt_personal_code_authorization — orchestration Mac.
# =====================================================================
class PromptPersonalCodeAuthorizationTest(_TmpRemoteControlTest):
    def test_respecte_le_blocage_sans_meme_afficher_la_boite_de_dialogue(self):
        for _ in range(5):
            open_windows.verify_personal_code_change_authorization("000000")
        app = _FakeAppForPersonalCode()
        with patch.object(main.simpledialog, "askstring") as mock_ask, \
             patch.object(main.messagebox, "showerror") as mock_error:
            result = main.App._prompt_personal_code_authorization(app)

        self.assertFalse(result)
        mock_ask.assert_not_called()
        mock_error.assert_called_once()

    def test_libelle_le_bon_prompt_selon_letat(self):
        app = _FakeAppForPersonalCode()
        with patch.object(main.simpledialog, "askstring", return_value="280652") as mock_ask:
            main.App._prompt_personal_code_authorization(app)
        self.assertIn("initial", mock_ask.call_args[0][1].lower())

        open_windows.set_personal_remote_code("111111")
        with patch.object(main.simpledialog, "askstring", return_value="111111") as mock_ask2:
            main.App._prompt_personal_code_authorization(app)
        self.assertIn("actuel", mock_ask2.call_args[0][1].lower())

    def test_boite_de_dialogue_masquee(self):
        """`show="*"` : jamais le code affiché en clair pendant la
        saisie."""
        app = _FakeAppForPersonalCode()
        with patch.object(main.simpledialog, "askstring", return_value="280652") as mock_ask:
            main.App._prompt_personal_code_authorization(app)
        self.assertEqual(mock_ask.call_args.kwargs.get("show"), "*")


# =====================================================================
# 8. Non-contournement — vérification structurelle.
# =====================================================================
class NoBypassStructuralTest(unittest.TestCase):
    """ADMIN ne doit JAMAIS pouvoir contourner cette vérification — ce
    module n'a même pas la notion de rôle local, donc la seule garantie
    vérifiable est que les deux handlers passent TOUJOURS par
    _prompt_personal_code_authorization avant d'appeler les fonctions
    d'écriture (set_personal_remote_code/clear_personal_remote_code)."""

    def setUp(self):
        import inspect
        self.set_source = inspect.getsource(main.App._on_set_personal_remote_code)
        self.clear_source = inspect.getsource(main.App._on_clear_personal_remote_code)

    def test_on_set_appelle_lautorisation_avant_decrire(self):
        idx_auth = self.set_source.index("_prompt_personal_code_authorization")
        idx_write = self.set_source.index("open_windows.set_personal_remote_code(")
        self.assertLess(idx_auth, idx_write)

    def test_on_clear_appelle_lautorisation_avant_de_supprimer(self):
        idx_auth = self.clear_source.index("_prompt_personal_code_authorization")
        idx_write = self.clear_source.index("open_windows.clear_personal_remote_code(")
        self.assertLess(idx_auth, idx_write)


if __name__ == "__main__":
    unittest.main()
