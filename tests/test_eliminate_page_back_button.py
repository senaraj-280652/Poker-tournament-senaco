"""Vérifie que la page "Éliminations" du contrôle à distance (voir
remote_control.py: _ELIMINATE_PAGE) affiche un vrai bouton "← Retour"
au lieu de l'ancien lien texte discret, et que la page "Photos"
(_PHOTOS_PAGE), hors périmètre de cette demande, n'a pas été touchée."""
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

    def test_page_photos_non_affectee(self):
        html = _render(remote_control._PHOTOS_PAGE)
        self.assertIn('<a href="/">← Retour</a>', html)
        self.assertNotIn("btn-back", html)


if __name__ == "__main__":
    unittest.main()
