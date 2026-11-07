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


class GardeDestructionRacineTest(unittest.TestCase):
    """Correctif du 2026-11-06 (crash Windows "TclError: application has
    been destroyed" sur TEST-25, protocole V3) — vérification structurelle
    du SOURCE, même principe que GatingLicenceV3Test ci-dessus. Les tests
    comportementaux (vraie racine Tk, vrais dialogues) sont dans
    tests/test_activation_root_destroy_guard.py."""

    def setUp(self):
        self.source = _lire_main()

    def _bloc_init(self):
        debut = self.source.index("if os.environ.get(POKER_TOURNAMENT_LICENSE_V3):")
        fin = self.source.index("# Lobby autonome (demande du 2026-10-31")
        return self.source[debut:fin]

    def test_aucun_destroy_brut_dans_les_chemins_v2_et_v3(self):
        """Les deux chemins d'activation (abandon V3, ETAT_VERIFICATION_
        OBLIGATOIRE, abandon V2) doivent tous passer par
        _detruire_racine_en_securite — plus aucun self.destroy() brut non
        protégé dans ce bloc précis."""
        bloc = self._bloc_init()
        self.assertNotIn("self.destroy()", bloc)
        self.assertEqual(bloc.count("self._detruire_racine_en_securite()"), 3)

    def test_methode_detruire_racine_en_securite_definie_et_protegee(self):
        self.assertIn("def _detruire_racine_en_securite(self):", self.source)
        debut = self.source.index("def _detruire_racine_en_securite(self):")
        fin = self.source.index("def __init__(self, open_path=None,")
        corps = self.source[debut:fin]
        self.assertIn("self._fermee_pendant_init = True", corps)
        self.assertIn("self.destroy()", corps)
        self.assertIn("except tk.TclError:", corps)

    def test_drapeau_initialise_a_false_au_debut_de_init(self):
        debut = self.source.index("def __init__(self, open_path=None,")
        fin = self.source.index("if os.environ.get(POKER_TOURNAMENT_LICENSE_V3):")
        corps = self.source[debut:fin]
        self.assertIn("self._fermee_pendant_init = False", corps)

    def test_les_trois_mainloop_reels_sont_tous_proteges_par_le_drapeau(self):
        """Les 3 appels réels à .mainloop() de ce fichier (bloc __main__ et
        les deux relances internes après fermeture du dernier tournoi/
        Menu principal) doivent tous être précédés d'une vérification de
        _fermee_pendant_init — jamais un appel nu."""
        positions = []
        debut = 0
        while True:
            position = self.source.find(".mainloop()", debut)
            if position == -1:
                break
            ligne_debut = self.source.rfind("\n", 0, position) + 1
            ligne = self.source[ligne_debut:self.source.find("\n", position)]
            # Un VRAI appel, une fois la ligne dépouillée, se termine
            # EXACTEMENT par ".mainloop()" (rien après) — exclut aussi bien
            # les commentaires ("# ... mainloop() ...") que les mentions en
            # prose dans une docstring ("App().mainloop() dans ce même
            # processus)"), qui ont toujours du texte à la suite.
            if ligne.strip().endswith(".mainloop()"):
                positions.append(position)
            debut = position + 1
        self.assertEqual(len(positions), 3, "Nombre inattendu d'appels .mainloop() réels dans main.py.")
        for position in positions:
            with self.subTest(position=position):
                contexte_avant = self.source[max(0, position - 400):position]
                self.assertIn("_fermee_pendant_init", contexte_avant)


if __name__ == "__main__":
    unittest.main()
