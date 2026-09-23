# -*- coding: utf-8 -*-
"""Style des libellés "Éliminé"/"Éliminateur" de la page Éliminations du
contrôle à distance (demande du 2026-09-20) : "ÉLIMINÉ" en majuscules et
en rouge en haut à gauche, "ÉLIMINATEUR" en majuscules et en bleu en
haut à droite — voir remote_control.py, règles CSS `.col-left h2`/
`.col-right h2` ajoutées après `.col h2`.

Implémenté via `text-transform: uppercase` (jamais le texte HTML retapé
en majuscules en dur) : évite tout risque de faute d'accent sur la
version majuscule, et laisse le texte source ("Éliminé"/"Éliminateur",
déjà couvert par tests/test_eliminate_page_back_button.py) totalement
inchangé — seule la présentation visuelle change."""
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


class EliminatePageLabelsStyleTest(unittest.TestCase):
    def setUp(self):
        self.html = _render(remote_control._ELIMINATE_PAGE)

    def test_regle_css_col_left_h2_majuscules_et_rouge(self):
        self.assertIn(".col-left h2 {", self.html)
        # Isole précisément le bloc de règle .col-left h2 (jusqu'à la
        # première accolade fermante), pour ne jamais confondre avec
        # une autre règle qui contiendrait par coïncidence les mêmes
        # propriétés ailleurs dans la feuille de style.
        start = self.html.index(".col-left h2 {")
        end = self.html.index("}", start)
        rule = self.html[start:end]
        self.assertIn("text-transform: uppercase", rule)
        self.assertIn("color: #d9614a", rule)

    def test_regle_css_col_right_h2_majuscules_et_bleu(self):
        self.assertIn(".col-right h2 {", self.html)
        start = self.html.index(".col-right h2 {")
        end = self.html.index("}", start)
        rule = self.html[start:end]
        self.assertIn("text-transform: uppercase", rule)
        self.assertIn("color: #5fa8cc", rule)

    def test_couleurs_gauche_et_droite_distinctes(self):
        start_left = self.html.index(".col-left h2 {")
        end_left = self.html.index("}", start_left)
        color_left = self.html[start_left:end_left]

        start_right = self.html.index(".col-right h2 {")
        end_right = self.html.index("}", start_right)
        color_right = self.html[start_right:end_right]

        self.assertNotEqual(color_left, color_right)

    def test_texte_source_eliminee_eliminateur_toujours_present(self):
        """Non-régression : le texte HTML lui-même (déjà couvert par
        test_eliminate_page_back_button.py) reste intact — seule la
        présentation CSS change, jamais le contenu textuel source."""
        self.assertIn("Éliminé — glisser vers", self.html)
        self.assertIn("← déposer ici — Éliminateur", self.html)

    def test_contraste_suffisant_sur_fond_sombre_du_bandeau(self):
        """Les couleurs choisies doivent rester lisibles sur le fond
        très sombre du bandeau (#0b1c15, voir la règle .col h2 commune)
        — contraste WCAG AA (>= 4.5:1) plutôt que reprendre telles
        quelles les couleurs de bouton existantes (#b5442e/#2c6e8a,
        pensées pour du texte blanc PAR-DESSUS elles, pas comme couleur
        de texte sur ce fond)."""
        def _luminance(hex_color):
            hex_color = hex_color.lstrip("#")
            r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

            def lin(c):
                return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

            r, g, b = lin(r), lin(g), lin(b)
            return 0.2126 * r + 0.7152 * g + 0.0722 * b

        def _contrast(c1, c2):
            l1, l2 = _luminance(c1), _luminance(c2)
            l1, l2 = max(l1, l2), min(l1, l2)
            return (l1 + 0.05) / (l2 + 0.05)

        background = "#0b1c15"
        self.assertGreaterEqual(_contrast("#d9614a", background), 4.5)
        self.assertGreaterEqual(_contrast("#5fa8cc", background), 4.5)


if __name__ == "__main__":
    unittest.main()
