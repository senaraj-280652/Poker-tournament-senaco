# -*- coding: utf-8 -*-
"""Lot 5 du système de licences Senaco v3 — vérifie que le branchement
dans App.__init__ est réellement DORMANT par défaut (demande explicite
du 2026-11-03) : construire un vrai `App()` est trop lourd/intrusif
pour un test (effets de bord nombreux, aucun test existant ne le fait
— voir l'absence totale de tests sur l'ancien ActivationDialog/
is_licensed() avant ce lot). Vérification structurelle du SOURCE à la
place : les deux mécanismes doivent être deux branches MUTUELLEMENT
EXCLUSIVES d'un seul if/elif, jamais deux `if` indépendants qui
pourraient tous les deux s'exécuter."""
import os
import re
import unittest


def _lire_main():
    chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(chemin, "r", encoding="utf-8") as f:
        return f.read()


class GatingLicenceV3Test(unittest.TestCase):
    def setUp(self):
        self.source = _lire_main()

    def test_les_deux_mecanismes_sont_un_seul_ifelif(self):
        """`if os.environ.get(POKER_TOURNAMENT_LICENSE_V3):` doit être
        immédiatement suivi, dans le MÊME bloc conditionnel, d'un
        `elif not licensing.is_licensed():` — jamais deux `if` séparés
        qui pourraient, par erreur future, s'exécuter tous les deux."""
        motif = re.search(
            r"if os\.environ\.get\(POKER_TOURNAMENT_LICENSE_V3\):.*?"
            r"elif not licensing\.is_licensed\(\):",
            self.source, re.DOTALL,
        )
        self.assertIsNotNone(
            motif,
            "Le nouveau et l'ancien mécanisme de licence doivent être "
            "les branches d'un seul if/elif, pas deux `if` indépendants.",
        )

    def test_ancien_mecanisme_toujours_present_et_inchange(self):
        """ActivationDialog (ancien) reste présent et utilisé — jamais
        supprimé par ce lot."""
        self.assertIn("class ActivationDialog(tk.Toplevel):", self.source)
        self.assertIn("ActivationDialog(self).activated", self.source)

    def test_nouveau_mecanisme_present(self):
        self.assertIn("class ActivationEnLigneDialog(tk.Toplevel):", self.source)
        self.assertIn("license_client.evaluer_licence()", self.source)

    def test_etat_normal_et_expire_ne_bloquent_jamais_le_demarrage(self):
        """Vérifie l'absence, dans le bloc du Lot 5, d'un quelconque
        appel à self.destroy() en réaction à ETAT_NORMAL ou ETAT_EXPIRE
        — seul ETAT_ACTIVATION_REQUISE (annulé) et ETAT_VERIFICATION_
        OBLIGATOIRE doivent pouvoir empêcher le démarrage dans ce lot
        (la tolérance de session reste hors périmètre, voir le compte
        rendu)."""
        debut = self.source.index("if os.environ.get(POKER_TOURNAMENT_LICENSE_V3):")
        fin = self.source.index("elif not licensing.is_licensed():")
        bloc = self.source[debut:fin]
        self.assertNotIn("ETAT_NORMAL", bloc)
        self.assertNotIn("ETAT_EXPIRE", bloc)
        self.assertNotIn("ETAT_REVOQUE", bloc)
        self.assertIn("ETAT_ACTIVATION_REQUISE", bloc)
        self.assertIn("ETAT_VERIFICATION_OBLIGATOIRE", bloc)

    def test_import_license_client_present(self):
        self.assertIn("import license_client", self.source)


if __name__ == "__main__":
    unittest.main()
