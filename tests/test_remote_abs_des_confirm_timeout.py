# -*- coding: utf-8 -*-
"""Confirmation Abs/Dés sur téléphone, avec timeout, et texte adapté à
l'état du joueur (demandes du 2026-10-03 puis 2026-10-04).

Quatre branches dans `handleAbsDesDrop` (_ELIMINATE_PAGE,
remote_control.py), chacune avec son propre comportement :

- **A** (`drag.arrived`, déjà accueilli) : `confirmWithdrawAbsentPlayer`
  — `window.confirm()`, SANS timeout, message « — déjà accueilli(e),
  désactiver définitivement ? ». INCHANGÉE par les deux demandes.
- **B** (`!drag.absent`, pas encore signalé absent) : `showAbsDesConfirm`
  — confirmation personnalisée AVEC timeout (10s), message « Déclarer
  NOM absent ? ». OK -> `declareAbsence` (horodatage `absent_declared_at`
  uniquement, STRICTEMENT inchangée) ; Annuler ou expiration -> aucun
  appel réseau.
- **C** (`drag.absent` ET délai pas dépassé) : aucun effet, aucun
  message — `return;` silencieux, INCHANGÉE.
- **D** (`drag.absent` ET délai dépassé) : migrée le 2026-10-04 de
  `confirmWithdrawAbsentPlayer` (window.confirm, sans timeout) vers
  `showAbsDesConfirm` — MÊME mécanisme que B, message « Déclarer NOM
  désinscrit ? ». OK -> `withdrawAbsentPlayer` (POST
  /withdraw_absent_player, STRICTEMENT inchangée — extraite de
  confirmWithdrawAbsentPlayer sans dupliquer l'appel réseau, voir plus
  bas) ; Annuler ou expiration -> aucun appel réseau.

`showAbsDesConfirm(message, onConfirm)` est désormais PARAMÉTRÉE par le
message complet (plus de texte générique codé en dur) — réutilisée à
l'identique par B et D, jamais dupliquée. `withdrawAbsentPlayer(playerId)`
est le SEUL point qui appelle /withdraw_absent_player, partagé par la
branche A (via confirmWithdrawAbsentPlayer, window.confirm) et la
branche D (via showAbsDesConfirm) — extraction pure, aucun changement
de comportement réseau.

Pas de moteur JavaScript disponible dans cet environnement (ni Node, ni
js2py/PyMiniRacer) — ce fichier suit donc la convention déjà établie
dans cette suite pour le JS embarqué côté téléphone (inspection
structurelle du source) : prouve le câblage exact (quelle branche
appelle quoi, avec quel message, dans quel ordre), jamais une
réexécution du JS lui-même. Le comportement réel dans un navigateur est
confirmé par le test manuel sur téléphone demandé en complément."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import remote_control  # noqa: E402


class _JsSourceTestCase(unittest.TestCase):
    """Doublure de la convention "inspection du source" déjà utilisée
    ailleurs dans cette suite pour du JS/HTML généré côté serveur."""

    @classmethod
    def setUpClass(cls):
        cls.page = remote_control._ELIMINATE_PAGE

    def _function_body(self, name, page=None):
        """Corps de `function NAME(...) {{ ... }}` dans `page` (par
        défaut _ELIMINATE_PAGE — confirmEndTournament vit, lui, dans
        _PAGE_TEMPLATE, voir son appelant ci-dessous) — recherche la
        première accolade ouvrante après la signature, puis compte les
        accolades pour trouver la fermante correspondante (le corps
        contient lui-même des accolades imbriquées)."""
        page = self.page if page is None else page
        m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{\{", page)
        self.assertIsNotNone(m, f"fonction {name} introuvable dans la page fournie")
        start = m.end()
        depth = 1
        i = start
        while depth > 0:
            if page[i:i + 2] == "{{":
                depth += 1
                i += 2
            elif page[i:i + 2] == "}}":
                depth -= 1
                i += 2
            else:
                i += 1
        return page[start:i - 2]


# =========================================================================
# 1. handleAbsDesDrop — les 4 branches, messages et mécanismes corrects
# =========================================================================
class HandleAbsDesDropBranchesTest(_JsSourceTestCase):
    def test_branche_b_pas_encore_absent_appelle_showabsdesconfirm_avec_message_absent(self):
        body = self._function_body("handleAbsDesDrop")
        self.assertIn(
            "showAbsDesConfirm('Déclarer ' + drag.label + ' absent ?', "
            "function() {{ declareAbsence(drag.id, drag.label); }});",
            body,
        )

    def test_declareabsence_nest_jamais_appelee_en_instruction_autonome(self):
        """Avant le premier correctif : `declareAbsence(drag.id,
        drag.label);` apparaissait seule, en appel direct, sans
        confirmation. Elle ne doit apparaître QUE comme callback de
        showAbsDesConfirm — jamais ailleurs dans cette fonction."""
        body = self._function_body("handleAbsDesDrop")
        self.assertNotIn("  declareAbsence(drag.id, drag.label);\n", body)
        self.assertEqual(body.count("declareAbsence(drag.id, drag.label)"), 1)

    def test_branche_a_deja_accueilli_inchangee(self):
        """Hors de portée des deux demandes : toujours
        confirmWithdrawAbsentPlayer, toujours le même message,
        JAMAIS showAbsDesConfirm."""
        body = self._function_body("handleAbsDesDrop")
        self.assertIn(
            "confirmWithdrawAbsentPlayer(drag.id, drag.label, "
            "' — déjà accueilli(e), désactiver définitivement ?');",
            body,
        )

    def test_branche_c_delai_pas_depasse_aucun_effet_aucun_message(self):
        """Inchangée : un simple `return;`, sans condition sur showAbsDesConfirm
        ni confirmWithdrawAbsentPlayer juste après le test du délai."""
        body = self._function_body("handleAbsDesDrop")
        idx = body.index("if (!absenceDeadlinePassed)")
        block = body[idx:idx + 400]
        self.assertIn("return;", block)
        before_return = block[:block.index("return;")]
        self.assertNotIn("showAbsDesConfirm(", before_return)
        self.assertNotIn("confirmWithdrawAbsentPlayer(", before_return)

    def test_branche_d_delai_depasse_appelle_showabsdesconfirm_avec_message_desinscrit(self):
        """Migration du 2026-10-04 : la branche D n'appelle plus
        confirmWithdrawAbsentPlayer directement — elle utilise désormais
        showAbsDesConfirm, avec le callback withdrawAbsentPlayer."""
        body = self._function_body("handleAbsDesDrop")
        self.assertIn(
            "showAbsDesConfirm('Déclarer ' + drag.label + ' désinscrit ?', "
            "function() {{ withdrawAbsentPlayer(drag.id); }});",
            body,
        )
        # Plus aucun appel à confirmWithdrawAbsentPlayer pour le cas
        # "désactiver définitivement (absence confirmée)" : seul l'appel
        # de la branche A (déjà accueilli, vérifié ci-dessus) subsiste.
        self.assertEqual(body.count("confirmWithdrawAbsentPlayer("), 1)


# =========================================================================
# 2. showAbsDesConfirm — paramétrée par message, câblage OK/Annuler/timeout
# =========================================================================
class ShowAbsDesConfirmWiringTest(_JsSourceTestCase):
    def test_signature_parametree_par_message_complet(self):
        """Plus de texte générique codé en dur : showAbsDesConfirm
        affiche tel quel le message que l'appelant lui donne (B et D
        passent chacun leur propre texte, voir la classe précédente)."""
        body = self._function_body("showAbsDesConfirm")
        self.assertIn("function showAbsDesConfirm(message, onConfirm)", self.page)
        self.assertIn("textContent = message;", body)
        self.assertNotIn("absent/désinscrit", body)

    def test_ok_appelle_hideabsconfirm_puis_onconfirm(self):
        body = self._function_body("showAbsDesConfirm")
        ok_match = re.search(
            r"abs-confirm-ok'\)\.onclick = function\(\) \{\{(.*?)\}\};",
            body, re.DOTALL,
        )
        self.assertIsNotNone(ok_match, "gestionnaire onclick du bouton OK introuvable")
        ok_body = ok_match.group(1)
        self.assertIn("hideAbsConfirm();", ok_body)
        self.assertIn("onConfirm();", ok_body)

    def test_annuler_ferme_seulement_sans_appeler_onconfirm(self):
        body = self._function_body("showAbsDesConfirm")
        cancel_match = re.search(
            r"abs-confirm-cancel'\)\.onclick = function\(\) \{\{(.*?)\}\};",
            body, re.DOTALL,
        )
        self.assertIsNotNone(cancel_match, "gestionnaire onclick du bouton Annuler introuvable")
        cancel_body = cancel_match.group(1)
        self.assertIn("hideAbsConfirm();", cancel_body)
        self.assertNotIn("onConfirm", cancel_body)

    def test_timeout_10_secondes(self):
        self.assertIn("var ABS_CONFIRM_TIMEOUT_MS = 10000;", self.page)
        body = self._function_body("showAbsDesConfirm")
        self.assertIn("ABS_CONFIRM_TIMEOUT_MS);", body)

    def test_expiration_du_timeout_nappelle_jamais_onconfirm(self):
        """L'expiration doit être strictement équivalente à Annuler :
        seul hideAbsConfirm() dans le corps du setTimeout, jamais
        onConfirm — valable identiquement pour B et D, puisqu'elles
        partagent cette MÊME fonction."""
        body = self._function_body("showAbsDesConfirm")
        timeout_match = re.search(
            r"absConfirmTimer = setTimeout\(function\(\) \{\{(.*?)\}\}, ABS_CONFIRM_TIMEOUT_MS\);",
            body, re.DOTALL,
        )
        self.assertIsNotNone(timeout_match, "corps du setTimeout introuvable")
        timeout_body = timeout_match.group(1)
        self.assertIn("hideAbsConfirm();", timeout_body)
        self.assertNotIn("onConfirm", timeout_body)

    def test_hideabsconfirm_annule_le_minuteur_et_masque_loverlay(self):
        body = self._function_body("hideAbsConfirm")
        self.assertIn("style.display = 'none';", body)
        self.assertIn("clearTimeout(absConfirmTimer);", body)


# =========================================================================
# 3. withdrawAbsentPlayer — extraite, partagée par A (window.confirm)
#    et D (showAbsDesConfirm), jamais dupliquée
# =========================================================================
class WithdrawAbsentPlayerSharedCallTest(_JsSourceTestCase):
    def test_withdrawabsentplayer_contient_lappel_reseau(self):
        body = self._function_body("withdrawAbsentPlayer")
        self.assertIn("fetchWithTimeout('/withdraw_absent_player',", body)
        self.assertIn("body: JSON.stringify({{player_id: playerId}}),", body)
        self.assertIn("Désactivation refusée.", body)

    def test_un_seul_appel_a_lendpoint_dans_toute_la_page(self):
        """Garantit qu'aucune duplication n'a eu lieu : un seul endroit
        construit cette requête, quelle que soit la branche (A ou D)."""
        self.assertEqual(self.page.count("fetchWithTimeout('/withdraw_absent_player',"), 1)

    def test_confirmwithdrawabsentplayer_delegue_a_withdrawabsentplayer(self):
        """Branche A : window.confirm() inchangé, puis délègue à
        withdrawAbsentPlayer (extraction du 2026-10-04) au lieu de
        construire sa propre requête."""
        body = self._function_body("confirmWithdrawAbsentPlayer")
        self.assertIn("window.confirm(label + question)", body)
        self.assertIn("withdrawAbsentPlayer(playerId);", body)
        self.assertNotIn("fetchWithTimeout", body)
        self.assertNotIn("showAbsDesConfirm(", body)  # pas d'APPEL — un commentaire peut mentionner le nom


# =========================================================================
# 4. Non-régression — élimination, Fin de la partie
# =========================================================================
class UnrelatedConfirmationsUntouchedTest(_JsSourceTestCase):
    def test_elimination_reste_sur_window_confirm_sans_timeout(self):
        body = self._function_body("confirmElimination")
        self.assertIn("window.confirm(msg)", body)
        self.assertNotIn("showAbsDesConfirm", body)
        self.assertNotIn("setTimeout", body)

    def test_fin_de_partie_reste_sur_window_confirm_sans_timeout(self):
        # confirmEndTournament vit dans _PAGE_TEMPLATE (bouton "Fin de la
        # partie" de l'écran principal), pas dans _ELIMINATE_PAGE.
        body = self._function_body("confirmEndTournament", page=remote_control._PAGE_TEMPLATE)
        self.assertIn("window.confirm(", body)
        self.assertNotIn("showAbsDesConfirm", body)
        self.assertNotIn("setTimeout", body)


# =========================================================================
# 5. HTML/CSS de l'overlay — présent une fois, isolé à cette page
# =========================================================================
class AbsConfirmOverlayMarkupTest(unittest.TestCase):
    def test_overlay_present_une_seule_fois_dans_eliminate_page(self):
        page = remote_control._ELIMINATE_PAGE
        self.assertEqual(page.count('id="abs-confirm-overlay"'), 1)
        self.assertEqual(page.count('id="abs-confirm-ok"'), 1)
        self.assertEqual(page.count('id="abs-confirm-cancel"'), 1)

    def test_overlay_absent_des_8_autres_pages(self):
        """Isolé à la page Éliminations — jamais injecté ailleurs par
        erreur (ex. via un fragment partagé mal ciblé)."""
        autres = {
            "_NO_ACCESS_PAGE": remote_control._NO_ACCESS_PAGE,
            "_FORBIDDEN_PAGE": remote_control._FORBIDDEN_PAGE,
            "_LOGIN_PAGE": remote_control._LOGIN_PAGE,
            "_PAGE_TEMPLATE": remote_control._PAGE_TEMPLATE,
            "_LOBBY_PAGE": remote_control._LOBBY_PAGE,
            "_PHOTOS_PAGE": remote_control._PHOTOS_PAGE,
            "_MOVES_PAGE": remote_control._MOVES_PAGE,
            "_CHECKIN_PAGE": remote_control._CHECKIN_PAGE,
        }
        for name, page in autres.items():
            with self.subTest(page=name):
                self.assertNotIn("abs-confirm-overlay", page)

    def test_boutons_annuler_et_ok_presents_dans_le_html(self):
        page = remote_control._ELIMINATE_PAGE
        idx = page.index('id="abs-confirm-overlay"')
        block = page[idx:idx + 400]
        self.assertIn(">Annuler<", block)
        self.assertIn(">OK<", block)


if __name__ == "__main__":
    unittest.main()
