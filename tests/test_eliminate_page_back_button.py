"""Vérifie que la page "Éliminations" du contrôle à distance (voir
remote_control.py: _ELIMINATE_PAGE) affiche un vrai bouton "← Retour"
au lieu de l'ancien lien texte discret, et que la page "Photos"
(_PHOTOS_PAGE) reprend maintenant exactement le même bouton (même id,
même présentation, même mécanisme de retour vers "/" résolu à chaque
clic par le routage normal — jamais un pid/URL figé en dur)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import remote_control  # noqa: E402


def _render(template):
    return template.format(
        tournament_name="Tournoi Test",
        reload_script="",
        rebalance_widget="",
        auth_redirect_script="",
    )


class EliminatePageBackButtonTest(unittest.TestCase):
    def test_ancien_lien_texte_retire(self):
        html = _render(remote_control._ELIMINATE_PAGE)
        self.assertNotIn('<a href="/">← Retour</a>', html)

    def test_nouveau_bouton_present_et_ramene_a_l_accueil(self):
        html = _render(remote_control._ELIMINATE_PAGE)
        self.assertIn('id="btn-back"', html)
        self.assertIn("← Retour", html)
        # Un vrai <button>, pas juste un <a> stylé différemment.
        self.assertIn("<button id=\"btn-back\"", html)
        self.assertIn("window.location.href='/'", html)

    def test_bouton_a_un_style_visible_pas_seulement_du_texte(self):
        html = _render(remote_control._ELIMINATE_PAGE)
        self.assertIn("#btn-back", html)
        self.assertIn("background: #2c4a6e", html)

    def test_reste_de_la_page_intact(self):
        html = _render(remote_control._ELIMINATE_PAGE)
        # Glisser-déposer et colonnes Éliminé/Éliminateur toujours là.
        self.assertIn('id="list-left"', html)
        self.assertIn('id="list-right"', html)
        self.assertIn("Éliminé — glisser vers", html)

class PhotosPageBackButtonTest(unittest.TestCase):
    """Même correctif que EliminatePageBackButtonTest ci-dessus, appliqué
    à _PHOTOS_PAGE (demande du 2026-09-07) : reprend le même bouton, à
    l'identique."""

    def test_ancien_lien_texte_retire(self):
        html = _render(remote_control._PHOTOS_PAGE)
        self.assertNotIn('<a href="/">← Retour</a>', html)

    def test_nouveau_bouton_present_et_ramene_a_l_accueil(self):
        html = _render(remote_control._PHOTOS_PAGE)
        self.assertIn('id="btn-back"', html)
        self.assertIn("← Retour", html)
        # Un vrai <button>, pas juste un <a> stylé différemment.
        self.assertIn("<button id=\"btn-back\"", html)
        # Jamais un pid ou un port figé en dur : toujours "/", résolu à
        # chaque clic par resolve_current_pid côté serveur (même
        # mécanisme que le bouton de la page Éliminations).
        self.assertIn("window.location.href='/'", html)

    def test_bouton_a_un_style_visible_pas_seulement_du_texte(self):
        html = _render(remote_control._PHOTOS_PAGE)
        self.assertIn("#btn-back", html)
        self.assertIn("background: #2c4a6e", html)

    def test_reste_de_la_page_intact(self):
        html = _render(remote_control._PHOTOS_PAGE)
        # Liste des joueurs, écran de cadrage : toujours là, seul le lien
        # de retour a changé.
        self.assertIn('id="list"', html)
        self.assertIn('id="crop-view"', html)
        self.assertIn('id="camera-input"', html)


if __name__ == "__main__":
    unittest.main()
