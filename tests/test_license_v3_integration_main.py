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
        """`if os.environ.get(POKER_TOURNAMENT_LICENSE_V3) [...]:` doit
        être immédiatement suivi, dans le MÊME bloc conditionnel, d'un
        `elif not licensing.is_licensed():` — jamais deux `if` séparés
        qui pourraient, par erreur future, s'exécuter tous les deux.
        Le `[...]` tolère l'ajout du 2026-11-15 (` or _is_test_build()`)
        sans affaiblir la vérification : la condition complète doit
        toujours commencer EXACTEMENT par `os.environ.get(POKER_
        TOURNAMENT_LICENSE_V3)`, jamais autre chose en premier."""
        motif = re.search(
            r"if os\.environ\.get\(POKER_TOURNAMENT_LICENSE_V3\)[^:\n]*:.*?"
            r"elif not licensing\.is_licensed\(\):",
            self.source, re.DOTALL,
        )
        self.assertIsNotNone(
            motif,
            "Le nouveau et l'ancien mécanisme de licence doivent être "
            "les branches d'un seul if/elif, pas deux `if` indépendants.",
        )

    def test_condition_v3_inclut_desormais_is_test_build(self):
        """Correctif du 2026-11-15 (lancement direct depuis le raccourci,
        sans PowerShell) : un build TEST active V3 même sans la
        variable d'environnement — vérifié ici au niveau du source,
        complément du test ci-dessus (qui tolère mais n'exige pas cet
        ajout)."""
        self.assertIn(
            "if os.environ.get(POKER_TOURNAMENT_LICENSE_V3) or _is_test_build():",
            self.source,
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
        debut = self.source.index("if os.environ.get(POKER_TOURNAMENT_LICENSE_V3) or _is_test_build():")
        fin = self.source.index("elif not licensing.is_licensed():")
        bloc = self.source[debut:fin]
        self.assertNotIn("ETAT_NORMAL", bloc)
        self.assertNotIn("ETAT_EXPIRE", bloc)
        self.assertNotIn("ETAT_REVOQUE", bloc)
        self.assertIn("ETAT_ACTIVATION_REQUISE", bloc)
        self.assertIn("ETAT_VERIFICATION_OBLIGATOIRE", bloc)
        self.assertIn(
            "ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE", bloc,
            "Correctif du 2026-11-11 : cet état doit aussi pouvoir bloquer "
            "le démarrage (licence présente mais illisible), sans jamais "
            "être confondu avec ETAT_ACTIVATION_REQUISE.",
        )

    def test_lecture_indisponible_najamais_la_fenetre_dactivation(self):
        """Correctif du 2026-11-11 (cœur du risque identifié le
        2026-11-10) : la branche ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE
        ne doit JAMAIS instancier ActivationEnLigneDialog — ce serait
        créer un second client/machine pour un poste déjà licencié."""
        debut = self.source.index("elif decision.etat == license_client.ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE:")
        fin = self.source.index("self._decision_licence_v3 = decision")
        bloc = self.source[debut:fin]
        self.assertNotIn("ActivationEnLigneDialog", bloc)
        self.assertIn("self._detruire_racine_en_securite()", bloc)

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
        debut = self.source.index("if os.environ.get(POKER_TOURNAMENT_LICENSE_V3) or _is_test_build():")
        fin = self.source.index("# Lobby autonome (demande du 2026-10-31")
        return self.source[debut:fin]

    def test_aucun_destroy_brut_dans_les_chemins_v2_et_v3(self):
        """Les deux chemins d'activation (abandon V3, ETAT_VERIFICATION_
        OBLIGATOIRE, abandon V2) doivent tous passer par
        _detruire_racine_en_securite — plus aucun self.destroy() brut non
        protégé dans ce bloc précis."""
        bloc = self._bloc_init()
        self.assertNotIn("self.destroy()", bloc)
        self.assertEqual(
            bloc.count("self._detruire_racine_en_securite()"), 4,
            "4 depuis le correctif du 2026-11-11 (nouvelle branche "
            "ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE) — 3 auparavant.",
        )

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
        fin = self.source.index("if os.environ.get(POKER_TOURNAMENT_LICENSE_V3) or _is_test_build():")
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


class VerificationAsynchroneEtPeriodiqueTest(unittest.TestCase):
    """Correctif du 2026-11-17 (règles de vérification de licence V3,
    issue GitHub n°9) : vérification structurelle du SOURCE, même
    principe que GatingLicenceV3Test ci-dessus — instancier un vrai
    App() reste hors de portée pour un test (voir l'en-tête de ce
    fichier), donc ce lot se vérifie à la lecture du code, pas à
    l'exécution. Le comportement réel (thread, file d'attente, matrice
    de décision) est, lui, testé pour de vrai dans tests/test_license_
    client.py (EvaluerLicenceSansReseauTest, VerificationAsynchroneTest,
    VerificationPeriodiqueNecessaireTest)."""

    def setUp(self):
        self.source = _lire_main()

    def test_demarrage_utilise_la_matrice_sans_reseau(self):
        """Règle 1/5 : le démarrage doit utiliser evaluer_licence_sans_
        reseau() — jamais evaluer_licence() en premier (qui bloquerait
        sur un réseau lent avant l'affichage du menu principal)."""
        self.assertIn("license_client.evaluer_licence_sans_reseau()", self.source)

    def test_retente_la_matrice_complete_seulement_pour_les_deux_etats_indetermines(self):
        """Les deux seuls états où le repli local est inexploitable
        doivent encore retenter evaluer_licence() (comportement
        rigoureusement inchangé pour eux, voir license_client.py) —
        jamais pour ETAT_NORMAL/ETAT_EXPIRE, désormais rafraîchis en
        tâche de fond."""
        debut = self.source.index("decision = license_client.evaluer_licence_sans_reseau()")
        fin = self.source.index("if decision.etat == license_client.ETAT_ACTIVATION_REQUISE:")
        bloc = self.source[debut:fin]
        self.assertIn("ETAT_VERIFICATION_OBLIGATOIRE", bloc)
        self.assertIn("ETAT_LECTURE_TEMPORAIREMENT_INDISPONIBLE", bloc)
        self.assertIn("license_client.evaluer_licence()", bloc)
        self.assertNotIn("ETAT_NORMAL", bloc)
        self.assertNotIn("ETAT_EXPIRE", bloc)

    def test_verification_en_arriere_plan_lancee_au_demarrage(self):
        self.assertIn("self._lancer_verification_v3_en_arriere_plan()", self.source)

    def test_sondage_periodique_planifie_au_demarrage(self):
        self.assertIn(
            "self.after(_SONDAGE_VERIFICATION_V3_INTERVALLE_MS, self._planifier_sondage_verification_v3)",
            self.source,
        )

    def test_methode_arriere_plan_utilise_un_thread_demon_jamais_le_thread_principal(self):
        self.assertIn("def _lancer_verification_v3_en_arriere_plan(self):", self.source)
        debut = self.source.index("def _lancer_verification_v3_en_arriere_plan(self):")
        fin = self.source.index("def _appliquer_resultats_verification_v3_en_attente(self):")
        bloc = self.source[debut:fin]
        self.assertIn("license_client.demarrer_verification_en_arriere_plan(", bloc)

    def test_sondage_periodique_sarrete_de_lui_meme_a_la_fermeture(self):
        """Règle "fermeture propre des tâches de vérification" : jamais
        une boucle self.after() qui continue après la destruction de la
        fenêtre — même garde winfo_exists() déjà établie ailleurs dans
        ce fichier pour ce type de boucle récurrente."""
        self.assertIn("def _planifier_sondage_verification_v3(self):", self.source)
        debut = self.source.index("def _planifier_sondage_verification_v3(self):")
        fin = self.source.index(
            "def _verifier_tolerance_licence_avant_ouverture(", debut,
        )
        bloc = self.source[debut:fin]
        self.assertIn("if not self.winfo_exists():", bloc)
        self.assertIn("return", bloc)
        self.assertIn("license_client.verification_periodique_necessaire()", bloc)
        self.assertIn(
            "self.after(_SONDAGE_VERIFICATION_V3_INTERVALLE_MS, self._planifier_sondage_verification_v3)",
            bloc,
        )

    def test_ouverture_tournoi_applique_les_resultats_en_attente_avant_de_lire_la_decision(self):
        """Cœur de la demande du 2026-11-17 : "aucune décision de
        blocage reçue ne doit pouvoir être ignorée" — la purge de la
        file d'attente doit avoir lieu AVANT la lecture de self.
        _decision_licence_v3, jamais après."""
        debut = self.source.index("def _verifier_tolerance_licence_avant_ouverture(")
        fin = self.source.index(
            'decision = getattr(self, "_decision_licence_v3", None)', debut,
        )
        bloc = self.source[debut:fin]
        self.assertIn("self._appliquer_resultats_verification_v3_en_attente()", bloc)


if __name__ == "__main__":
    unittest.main()
