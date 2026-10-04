# -*- coding: utf-8 -*-
"""Correctif du 2026-09-29 — glisser-déposer imprécis vers "Abs/Dés" sur
téléphone (page "Gestion des éliminations", contrôle à distance) :

CAUSE : le fantôme qui suit le doigt pendant un glissement (voir
remote_control._ELIMINATE_PAGE: moveGhost) est dessiné GHOST_Y_OFFSET
(60px) AU-DESSUS du doigt réel — pour que la main ne cache pas ce que
l'utilisateur tient. La détection de cible de dépôt (onTouchMove), elle,
teste le point du doigt RÉEL (document.elementFromPoint(t.clientX,
t.clientY)), jamais celui du fantôme. Pour la colonne "Éliminateur"
(grande, au milieu de l'écran), cet écart de 60px passe inaperçu ; pour
"Abs/Dés", petit bouton tout en haut du bandeau avec très peu de marge
au-dessus, il obligeait l'utilisateur à faire dépasser son doigt bien
au-delà du bord visible du bouton pour compenser — exactement le
symptôme rapporté ("je dois dépasser son bord supérieur", "le centre du
bouton ne fonctionne pas").

CORRECTIF (minimal, ciblé UNIQUEMENT sur "Abs/Dés") : si le doigt réel
ne touche aucune cible, on teste EN PLUS le point du fantôme (celui que
l'utilisateur voit réellement) contre le rectangle de "Abs/Dés"
spécifiquement — jamais contre la colonne "Éliminateur", dont le
glissement reste entièrement inchangé. Aucune règle métier touchée
(déclaration d'absence, désactivation, rééquilibrage) : uniquement la
détection géométrique de la cible de dépôt.

Suite string-based (pas de moteur JS dans cette suite, voir tests/
test_lot2_manual_fixes.py: EliminatePageTemplateStructureTest, dont ce
fichier reprend directement le patron)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_control  # noqa: E402


class TouchDragGhostOffsetConstantTest(unittest.TestCase):
    def test_ghost_y_offset_est_une_constante_partagee(self):
        page = remote_control._ELIMINATE_PAGE
        self.assertIn("var GHOST_Y_OFFSET = 60;", page)

    def test_moveghost_utilise_la_constante_partagee_pas_un_60_en_dur(self):
        """moveGhost ne doit plus contenir "60" en dur : la même
        constante que la détection de cible, pour qu'un futur ajustement
        de l'un ajuste automatiquement l'autre (jamais deux valeurs qui
        peuvent diverger silencieusement)."""
        page = remote_control._ELIMINATE_PAGE
        move_ghost = page[page.index("function moveGhost"):page.index("function pointInRect")]
        self.assertIn("GHOST_Y_OFFSET", move_ghost)
        self.assertNotIn("- 60", move_ghost)

    def test_pointinrect_est_defini(self):
        page = remote_control._ELIMINATE_PAGE
        self.assertIn("function pointInRect(x, y, rect)", page)


class TouchDragAbsDesFallbackDetectionTest(unittest.TestCase):
    """Vérifie la structure exacte du correctif dans onTouchMove : le
    repli sur le fantôme est bien ciblé UNIQUEMENT sur "btn-abs-des",
    n'intervient QUE si le doigt réel n'a rien trouvé, et est posé APRÈS
    la détection primaire mais AVANT la mise à jour de hoverTarget/
    drop-hover (pour que le retour visuel en profite lui aussi)."""

    def setUp(self):
        page = remote_control._ELIMINATE_PAGE
        start = page.index("function onTouchMove")
        end = page.index("function autoScrollNearEdge")
        self.on_touch_move = page[start:end]

    def test_detection_primaire_du_doigt_reel_inchangee(self):
        """Le mécanisme historique (doigt réel, elementFromPoint) reste
        la détection PRINCIPALE, jamais remplacé — c'est lui qui gouverne
        toujours le glissement Éliminé/Éliminateur."""
        self.assertIn(
            "var el = document.elementFromPoint(t.clientX, t.clientY);", self.on_touch_move
        )
        self.assertIn(
            'var target = el ? el.closest(\'[data-target="true"]\') : null;', self.on_touch_move
        )

    def test_repli_ne_s_applique_que_si_le_doigt_reel_n_a_rien_trouve(self):
        idx_primary = self.on_touch_move.index("var target = el ? el.closest")
        idx_guard = self.on_touch_move.index("if (!target) {{", idx_primary)
        # Rien d'autre ne doit réaffecter `target` entre la détection
        # primaire et ce garde-fou — sinon le repli pourrait s'appliquer
        # à tort après un résultat déjà déterminé autrement.
        between = self.on_touch_move[idx_primary:idx_guard]
        self.assertNotIn("target =", between.split("var target =", 1)[1])

    def test_repli_cible_specifiquement_btn_abs_des(self):
        idx_guard = self.on_touch_move.index("if (!target) {{")
        fallback_block = self.on_touch_move[idx_guard:idx_guard + 400]
        self.assertIn("document.getElementById('btn-abs-des')", fallback_block)

    def test_repli_utilise_le_point_du_fantome_pas_le_doigt_reel(self):
        """Le point testé pour "Abs/Dés" doit être celui du FANTÔME
        (t.clientY - GHOST_Y_OFFSET) — c'est précisément ce qui corrige
        le bogue (voir docstring du module)."""
        idx_guard = self.on_touch_move.index("if (!target) {{")
        fallback_block = self.on_touch_move[idx_guard:idx_guard + 400]
        self.assertIn(
            "pointInRect(t.clientX, t.clientY - GHOST_Y_OFFSET, absDesBtn.getBoundingClientRect())",
            fallback_block,
        )

    def test_repli_gele_absdesbtn_null_sans_planter(self):
        """« Gestion de l'accueil » désactivée (modification du
        2026-09-29 précédente) : #btn-abs-des n'existe alors pas dans le
        DOM — getElementById renvoie null, jamais une exception. Le
        garde `absDesBtn &&` doit être présent."""
        idx_guard = self.on_touch_move.index("if (!target) {{")
        fallback_block = self.on_touch_move[idx_guard:idx_guard + 400]
        self.assertIn("if (absDesBtn && pointInRect(", fallback_block)

    def test_repli_pose_avant_la_mise_a_jour_du_survol(self):
        idx_guard = self.on_touch_move.index("if (!target) {{")
        idx_hover_update = self.on_touch_move.index("if (hoverTarget && hoverTarget !== target)")
        self.assertLess(idx_guard, idx_hover_update)

    def test_retour_visuel_drop_hover_toujours_pilote_par_target(self):
        """Cahier des charges : "le bouton doit fournir un retour visuel
        lorsqu'il constitue une cible de dépôt valide" — déjà assuré par
        ce mécanisme existant (classe CSS drop-hover), qui bénéficie
        automatiquement du correctif puisqu'il lit la MÊME variable
        `target`, jamais dupliqué."""
        self.assertIn("target.classList.add('drop-hover');", self.on_touch_move)


class ExistingDragMechanismsUnaffectedTest(unittest.TestCase):
    """Non-régression explicite demandée : le bouton "Retour" et le
    glissement Éliminé/Éliminateur ne doivent RIEN perdre de leur
    fonctionnement."""

    def test_bouton_retour_toujours_present_et_inchange(self):
        page = remote_control._ELIMINATE_PAGE
        self.assertIn(
            '<button id="btn-back" onclick="window.location.href=\'/\'">← Retour</button>', page
        )

    def test_confirmelimination_toujours_declenche_par_le_glissement_liste(self):
        page = remote_control._ELIMINATE_PAGE
        self.assertIn("confirmElimination(", page)
        self.assertIn(
            "if (wasEngaged && target && target.dataset.id !== drag.id) {{", page
        )

    def test_data_target_toujours_pose_sur_la_colonne_eliminateur(self):
        page = remote_control._ELIMINATE_PAGE
        self.assertIn("div.dataset.target = 'true';", page)

    def test_heuristique_defilement_vs_glissement_horizontal_inchangee(self):
        """L'heuristique dx/dy distinguant un défilement d'un glissement
        horizontal (Éliminé -> Éliminateur) doit rester intacte — ce
        correctif ne touche qu'à la détection de cible APRÈS engagement,
        jamais à la décision initiale d'engager ou non le glissement."""
        page = remote_control._ELIMINATE_PAGE
        self.assertIn("Math.abs(dy) >= Math.abs(dx)", page)

    def test_handle_abs_des_drop_regles_metier_inchangees(self):
        """Aucune règle métier touchée : handleAbsDesDrop (déclarer/
        désactiver) garde exactement sa logique — vérifié au niveau
        chaîne, non-régression de tests/test_lot2_manual_fixes.py."""
        page = remote_control._ELIMINATE_PAGE
        idx_handle = page.index("function handleAbsDesDrop")
        idx_arrived = page.index("drag.arrived", idx_handle)
        idx_absent_check = page.index("if (!drag.absent)", idx_handle)
        self.assertLess(idx_arrived, idx_absent_check)


if __name__ == "__main__":
    unittest.main()
