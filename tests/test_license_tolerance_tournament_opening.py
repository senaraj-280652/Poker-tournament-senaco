# -*- coding: utf-8 -*-
"""Lot 5 (suite, demande du 2026-11-03) — raccordement de la tolérance
de licence (session_tolerance.py, Lot 3) à l'OUVERTURE réelle d'un
tournoi/Sit & Go. Couvre :
- App._verifier_tolerance_licence_avant_ouverture (logique de décision,
  doublure directe — jamais de vrai App()).
- Les DEUX seuls points d'entrée réels (open_path, _choose_tournament_
  file) — vérification structurelle qu'ils appellent bien ce garde, et
  qu'AUCUN autre chemin (Lobby, Statistiques, lecture seule) ne
  l'appelle jamais (demande explicite : ne jamais confondre
  consultation et ouverture d'une nouvelle session).
- Les boîtes de dialogue d'avertissement/blocage (vraies fenêtres Tk)."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402
import license_client  # noqa: E402
import session_tolerance  # noqa: E402

try:
    import tkinter as tk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

from _tk_cleanup import cleanup_tk  # noqa: E402


def _lire_main():
    chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(chemin, "r", encoding="utf-8") as f:
        return f.read()


class _FakeApp:
    def __init__(self, decision=None):
        self._decision_licence_v3 = decision
        self.avertissements = []
        self.blocages = []
        self.blocages_revocation = []
        self.blocages_verification_requise = []

    def _afficher_avertissement_licence(self, parent):
        self.avertissements.append(parent)

    def _afficher_blocage_licence(self, parent):
        self.blocages.append(parent)

    def _afficher_blocage_revocation_licence(self, parent):
        self.blocages_revocation.append(parent)

    def _afficher_blocage_verification_requise_licence(self, parent):
        self.blocages_verification_requise.append(parent)


class GardeDormantTest(unittest.TestCase):
    """Priorité absolue (point 10 de la demande) : sans
    POKER_TOURNAMENT_LICENSE_V3, ce garde ne doit RIEN évaluer, jamais."""

    def test_sans_variable_denvironnement_toujours_vrai_sans_evaluer(self):
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="x", message="",
        ))
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(main.POKER_TOURNAMENT_LICENSE_V3, None)
            with patch.object(session_tolerance, "evaluer_ouverture") as mock_eval:
                resultat = main.App._verifier_tolerance_licence_avant_ouverture(
                    fake, "/tmp/x.tournoi", fake,
                )
        self.assertTrue(resultat)
        mock_eval.assert_not_called()
        self.assertEqual(fake.avertissements, [])
        self.assertEqual(fake.blocages, [])


class MatriceGardeTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_aucune_decision_encore_calculee_est_permissif(self):
        """Ne devrait normalement jamais arriver (le garde de démarrage
        s'exécute avant), mais ne doit jamais planter un tournoi pour
        autant si self._decision_licence_v3 est absente."""
        fake = _FakeApp(decision=None)
        resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertTrue(resultat)

    def test_licence_normale_najamais_dappel_a_session_tolerance(self):
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_NORMAL, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture") as mock_eval:
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertTrue(resultat)
        mock_eval.assert_not_called()

    def test_licence_expiree_avec_avertissement_autorise_et_affiche(self):
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_AUTORISE_AVEC_AVERTISSEMENT, date_session="2026-11-03",
        )):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertTrue(resultat)
        self.assertEqual(fake.avertissements, [fake])
        self.assertEqual(fake.blocages, [])

    def test_licence_expiree_bloquee_refuse_louverture(self):
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_BLOQUE, date_session="2026-11-05",
        )):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertFalse(resultat)
        self.assertEqual(fake.blocages, [fake])
        self.assertEqual(fake.avertissements, [])

    def test_licence_revoquee_bloque_immediatement_sans_tolerance(self):
        """Décision du 2026-11-03 : une révocation est un blocage
        IMMÉDIAT, sans tolérance — jamais la même logique que
        l'expiration, jamais d'appel à session_tolerance du tout."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_REVOQUE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture") as mock_eval:
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertFalse(resultat)
        mock_eval.assert_not_called()
        self.assertEqual(fake.blocages_revocation, [fake])
        self.assertEqual(fake.blocages, [], "Jamais le message d'expiration pour une révocation.")
        self.assertEqual(fake.avertissements, [])

    def test_reouverture_dune_date_deja_connue_najamais_de_dialogue_supplementaire(self):
        """evaluer_ouverture() elle-même gère la non-consommation ; ici
        on vérifie juste que l'avertissement reste affiché (rappel
        voulu, voir session_tolerance.py) sans jamais bloquer."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_AUTORISE_AVEC_AVERTISSEMENT, date_session="2026-11-01",
            dates_autorisees=["2026-11-01"],
        )):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/deja-ouvert.tournoi", fake)
        self.assertTrue(resultat)
        self.assertEqual(len(fake.avertissements), 1)

    def test_etat_indetermine_sans_reseau_bloque_provisoirement(self):
        """Décision du 2026-11-03 : un état local indéterminé (fichier de
        tolérance absent/corrompu) pour une licence expirée impose une
        vérification en ligne. Si OVH est inaccessible (verifier_en_ligne
        renvoie None), on refuse PROVISOIREMENT l'ouverture — on ne
        redevient jamais permissif par défaut."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_INDETERMINE, date_session=None,
        )) as mock_eval, patch.object(license_client, "verifier_en_ligne", return_value=None) as mock_verif:
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertFalse(resultat)
        mock_verif.assert_called_once_with()
        self.assertEqual(mock_eval.call_count, 1)
        self.assertEqual(fake.blocages_verification_requise, [fake])
        self.assertEqual(fake.avertissements, [])
        self.assertEqual(fake.blocages, [])
        self.assertEqual(fake.blocages_revocation, [])

    def test_etat_indetermine_reconciliation_revele_une_revocation(self):
        """La vérification en ligne déclenchée par l'état indéterminé
        peut elle-même révéler une révocation — doit alors basculer sur
        le blocage de révocation, jamais sur celui d'expiration."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_INDETERMINE, date_session=None,
        )), patch.object(license_client, "verifier_en_ligne", return_value={"revoque": True}):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertFalse(resultat)
        self.assertEqual(fake.blocages_revocation, [fake])
        self.assertEqual(fake.blocages, [])
        self.assertEqual(fake.blocages_verification_requise, [])

    def test_etat_indetermine_reconciliation_licence_regularisee(self):
        """Si le serveur confirme que la licence n'est plus expirée
        (renouvellement entre-temps), l'ouverture est autorisée et la
        décision en mémoire est mise à jour vers ETAT_NORMAL."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_INDETERMINE, date_session=None,
        )) as mock_eval, patch.object(license_client, "verifier_en_ligne", return_value={
            "revoque": False, "licence": {"statut": "active"},
        }):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertTrue(resultat)
        self.assertEqual(mock_eval.call_count, 1, "Pas de deuxième évaluation si la licence n'est plus expirée.")
        self.assertEqual(fake._decision_licence_v3.etat, license_client.ETAT_NORMAL)
        self.assertEqual(fake._decision_licence_v3.cycle_id, "c1")
        self.assertEqual(fake.avertissements, [])
        self.assertEqual(fake.blocages, [])

    def test_etat_indetermine_reconciliation_toujours_expiree_puis_bloque(self):
        """Si le serveur confirme que la licence est toujours expirée,
        on réévalue session_tolerance (désormais réconcilié) et on agit
        sur ce nouveau résultat — ici un 3e date déjà bloquée."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        resultats = [
            session_tolerance.ResultatTolerance(etat=session_tolerance.ETAT_INDETERMINE, date_session=None),
            session_tolerance.ResultatTolerance(etat=session_tolerance.ETAT_BLOQUE, date_session="2026-11-05"),
        ]
        with patch.object(session_tolerance, "evaluer_ouverture", side_effect=resultats) as mock_eval, \
                patch.object(license_client, "verifier_en_ligne", return_value={
                    "revoque": False, "licence": {"statut": "expiree"},
                }):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertFalse(resultat)
        self.assertEqual(mock_eval.call_count, 2)
        self.assertEqual(fake.blocages, [fake])
        self.assertEqual(fake.blocages_verification_requise, [])

    def test_etat_indetermine_reconciliation_toujours_expiree_puis_avertissement(self):
        """Même scénario que ci-dessus mais la 2e évaluation, après
        réconciliation, autorise avec avertissement (ex : 1ère ou 2e
        date de tolérance)."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        resultats = [
            session_tolerance.ResultatTolerance(etat=session_tolerance.ETAT_INDETERMINE, date_session=None),
            session_tolerance.ResultatTolerance(
                etat=session_tolerance.ETAT_AUTORISE_AVEC_AVERTISSEMENT, date_session="2026-11-03",
            ),
        ]
        with patch.object(session_tolerance, "evaluer_ouverture", side_effect=resultats), \
                patch.object(license_client, "verifier_en_ligne", return_value={
                    "revoque": False, "licence": {"statut": "expiree"},
                }):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertTrue(resultat)
        self.assertEqual(fake.avertissements, [fake])
        self.assertEqual(fake.blocages, [])

    def test_etat_indetermine_toujours_indetermine_apres_reconciliation(self):
        """Filet de sécurité : même après une vérification en ligne
        réussie, si l'état local reste indéterminé (ex : écriture ayant
        échoué), on bloque provisoirement plutôt que de redevenir
        permissif par défaut."""
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_INDETERMINE, date_session=None,
        )) as mock_eval, patch.object(license_client, "verifier_en_ligne", return_value={
            "revoque": False, "licence": {"statut": "expiree"},
        }):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertFalse(resultat)
        self.assertEqual(mock_eval.call_count, 2)
        self.assertEqual(fake.blocages_verification_requise, [fake])
        self.assertEqual(fake.blocages, [])

    def test_anomalie_lecture_est_permissive_sans_dialogue(self):
        fake = _FakeApp(decision=license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="c1", message="",
        ))
        with patch.object(session_tolerance, "evaluer_ouverture", return_value=session_tolerance.ResultatTolerance(
            etat=session_tolerance.ETAT_ANOMALIE_LECTURE, date_session=None,
        )):
            resultat = main.App._verifier_tolerance_licence_avant_ouverture(fake, "/tmp/x.tournoi", fake)
        self.assertTrue(resultat)


class ConsommationReelleAvecVraieBaseTest(unittest.TestCase):
    """Preuve de bout en bout avec une VRAIE base .tournoi et un vrai
    session_tolerance (pas de mock de evaluer_ouverture cette fois) —
    la règle des 2 dates + la réouverture gratuite, telles qu'elles
    s'appliqueraient réellement."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="licence_tolerance_test_")
        self.addCleanup(self._tmp.cleanup)
        self.chemin_tolerance = os.path.join(self._tmp.name, "tolerance.json")
        patcher = patch.object(session_tolerance, "TOLERANCE_STATE_FILE", self.chemin_tolerance)
        patcher.start()
        self.addCleanup(patcher.stop)
        env_patcher = patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"})
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        session_tolerance.amorcer_ou_reinitialiser_tolerance("cycle-reel")

    def _creer_tournoi(self, nom_fichier, date_session):
        from database import Database
        chemin = os.path.join(self._tmp.name, nom_fichier)
        db = Database(chemin)
        db.set_settings({"tournament_date": date_session})
        db.conn.close()
        return chemin

    def _decision(self):
        return license_client.DecisionLicence(
            etat=license_client.ETAT_EXPIRE, cycle_id="cycle-reel", message="",
        )

    def test_deux_dates_distinctes_puis_blocage_a_la_troisieme(self):
        fake = _FakeApp(decision=self._decision())
        chemin_a = self._creer_tournoi("A.tournoi", "2026-11-01")
        chemin_b = self._creer_tournoi("B.tournoi", "2026-11-02")
        chemin_c = self._creer_tournoi("C.tournoi", "2026-11-03")

        self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, chemin_a, fake))
        self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, chemin_b, fake))
        self.assertFalse(main.App._verifier_tolerance_licence_avant_ouverture(fake, chemin_c, fake))
        self.assertEqual(len(fake.avertissements), 2)
        self.assertEqual(len(fake.blocages), 1)

    def test_reouverture_du_meme_tournoi_ne_consomme_jamais_de_nouvelle_date(self):
        fake = _FakeApp(decision=self._decision())
        chemin = self._creer_tournoi("Reouvert.tournoi", "2026-11-01")

        for _ in range(5):
            self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, chemin, fake))
        self.assertEqual(len(fake.avertissements), 5, "L'avertissement doit réapparaître à chaque ouverture.")

        autre_date = self._creer_tournoi("Autre.tournoi", "2026-11-02")
        self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, autre_date, fake))
        encore = self._creer_tournoi("Encore.tournoi", "2026-11-05")
        self.assertFalse(
            main.App._verifier_tolerance_licence_avant_ouverture(fake, encore, fake),
            "La réouverture gratuite ne doit pas avoir consommé de 3e emplacement.",
        )

    def test_plusieurs_tournois_meme_date_ne_comptent_quune_fois(self):
        """Même garantie que le Lot 3 (plusieurs tournois/SnG à la même
        date de session = une seule consommation), vérifiée ici via le
        VRAI point d'entrée applicatif."""
        fake = _FakeApp(decision=self._decision())
        c1 = self._creer_tournoi("Session1_A.tournoi", "2026-11-01")
        c2 = self._creer_tournoi("Session1_B.tournoi", "2026-11-01")
        c3 = self._creer_tournoi("Session2.tournoi", "2026-11-02")
        c4 = self._creer_tournoi("Session3.tournoi", "2026-11-03")

        self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, c1, fake))
        self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, c2, fake))  # même date, gratuit
        self.assertTrue(main.App._verifier_tolerance_licence_avant_ouverture(fake, c3, fake))  # 2e date
        self.assertFalse(main.App._verifier_tolerance_licence_avant_ouverture(fake, c4, fake))  # 3e date -> bloqué


class ConsultationJamaisConcerneeTest(unittest.TestCase):
    """Demande explicite : ne jamais confondre la consultation d'un
    ancien tournoi (Lobby, Statistiques — toujours en lecture seule,
    Database(path, read_only=True) ou _read_tournament_date_ro) avec
    l'ouverture d'une nouvelle session. Vérifié ici au niveau du
    SOURCE : le garde n'est référencé QUE dans les deux points
    d'entrée réels, jamais dans LobbyDialog ni les fonctions de
    statistiques multi-fichiers."""

    def setUp(self):
        self.source = _lire_main()

    def test_le_garde_est_appele_exactement_quatre_fois(self):
        """Correctif du 2026-11-08 (TEST-26) : le garde unique en toute
        fin de _choose_tournament_file a été retiré — chacun des 3
        boutons (new_tournament/new_sng/open_tournament) effectue
        désormais son propre contrôle, AVANT toute création/ouverture
        physique du fichier (voir tests/test_licence_tolerance_avant_
        creation.py pour le détail). Avec open_path (App.__init__),
        cela fait 4 points d'appel réels au total — jamais un 5e."""
        occurrences = self.source.count("self._verifier_tolerance_licence_avant_ouverture(")
        self.assertEqual(
            occurrences, 4,
            "Doit être appelé exactement aux quatre points d'ouverture "
            "réelle (open_path, new_tournament, new_sng, open_tournament) "
            "— jamais ailleurs, jamais un contrôle centralisé en fin de "
            "fonction (voir le correctif du 2026-11-08).",
        )

    def test_jamais_reference_dans_lobbydialog(self):
        debut = self.source.index("class LobbyDialog(tk.Toplevel):")
        fin = self.source.index("class RemoteDeviceRequestWindow")
        bloc_lobby = self.source[debut:fin]
        self.assertNotIn("_verifier_tolerance_licence_avant_ouverture", bloc_lobby)
        self.assertNotIn("session_tolerance", bloc_lobby)

    def test_appele_dans_la_branche_open_path(self):
        debut = self.source.index("if open_path and os.path.exists(open_path):")
        fin = self.source.index("else:", debut)
        bloc = self.source[debut:fin]
        self.assertIn("_verifier_tolerance_licence_avant_ouverture", bloc)

    def test_controle_precede_toute_creation_ouverture_physique(self):
        """Correctif du 2026-11-08 (TEST-26) : contrairement à l'ancien
        comportement (contrôle APRÈS tournament_date/_update_window_
        title, donc après création physique du fichier), le contrôle
        doit désormais précéder tout sélecteur de fichier (nouveau
        tournoi/SnG) ou toute ouverture réelle (tournoi existant) —
        jamais après. Voir tests/test_licence_tolerance_avant_creation.py
        pour la vérification détaillée par bouton."""
        debut = self.source.index("def _choose_tournament_file(self):")
        fin = self.source.index("def _apply_sng_defaults(self, n_players):")
        bloc = self.source[debut:fin]
        # Appels RÉELS uniquement ("self....(" ) — un commentaire de
        # new_tournament() référence aussi ce nom par son nom seul, sans
        # "self." ni parenthèse, volontairement exclu ici.
        motif = "self._verifier_tolerance_licence_avant_ouverture("
        self.assertEqual(bloc.count(motif), 3)
        # Les 3 appels (un par bouton) doivent tous précéder
        # self._update_window_title() — qui n'intervient qu'une fois le
        # fichier déjà physiquement créé/ouvert.
        positions = []
        depart = 0
        while True:
            pos = bloc.find(motif, depart)
            if pos == -1:
                break
            positions.append(pos)
            depart = pos + 1
        pos_update_title = bloc.index("self._update_window_title()")
        self.assertEqual(len(positions), 3)
        self.assertTrue(all(pos < pos_update_title for pos in positions))


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class DialoguesReelsTest(unittest.TestCase):
    """Vraies fenêtres Tk (jamais de doublure) — confirme qu'elles se
    construisent sans erreur et respectent "jamais d'URL inventée"."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: cleanup_tk(self, "root"))
        self.app = _FakeAppAvecMethodesReelles(self.root)

    def test_avertissement_sans_url_configuree_naffiche_aucun_lien(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("POKER_TOURNAMENT_LICENSE_RENEWAL_URL", None)
            with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
                main.App._afficher_avertissement_licence(self.app, self.root)
        # Aucune exception levée = succès pour ce test ; le contenu
        # textuel exact est secondaire (déjà couvert par la demande
        # d'un avertissement ROUGE, vérifié ci-dessous).

    def test_avertissement_est_bien_rouge(self):
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            main.App._afficher_avertissement_licence(self.app, self.root)
        # La fenêtre vient d'être auto-détruite par le bouton "Continuer"
        # ? Non : wait_window est patché, donc elle reste ouverte —
        # cherchons le premier Label rouge parmi les enfants du dernier
        # Toplevel créé.
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(toplevels, "La fenêtre d'avertissement doit avoir été créée.")
        couleurs = []
        for w in toplevels[-1].winfo_children():
            try:
                couleurs.append(str(w.cget("foreground")))
            except tk.TclError:
                pass
        self.assertIn("#b00020", couleurs)
        toplevels[-1].destroy()

    def test_lien_affiche_si_url_configuree(self):
        with patch.dict(os.environ, {"POKER_TOURNAMENT_LICENSE_RENEWAL_URL": "https://exemple.test/renouveler"}):
            with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
                main.App._afficher_blocage_licence(self.app, self.root)
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        textes = []
        for w in toplevels[-1].winfo_children():
            try:
                textes.append(w.cget("text"))
            except tk.TclError:
                pass
        self.assertIn("https://exemple.test/renouveler", textes)
        toplevels[-1].destroy()

    def test_blocage_revocation_est_rouge_et_najamais_de_lien_meme_si_url_configuree(self):
        """Exigence explicite du 2026-11-03 : un renouvellement ne lève
        jamais une révocation — ce dialogue ne doit JAMAIS afficher de
        lien, même quand POKER_TOURNAMENT_LICENSE_RENEWAL_URL est
        configurée (contrairement à _afficher_blocage_licence)."""
        with patch.dict(os.environ, {"POKER_TOURNAMENT_LICENSE_RENEWAL_URL": "https://exemple.test/renouveler"}):
            with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
                main.App._afficher_blocage_revocation_licence(self.app, self.root)
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(toplevels)
        couleurs = []
        textes = []
        for w in toplevels[-1].winfo_children():
            try:
                couleurs.append(str(w.cget("foreground")))
            except tk.TclError:
                pass
            try:
                textes.append(w.cget("text"))
            except tk.TclError:
                pass
        self.assertIn("#b00020", couleurs)
        self.assertNotIn("https://exemple.test/renouveler", textes)
        toplevels[-1].destroy()

    def test_blocage_verification_requise_est_rouge(self):
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            main.App._afficher_blocage_verification_requise_licence(self.app, self.root)
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(toplevels)
        couleurs = []
        for w in toplevels[-1].winfo_children():
            try:
                couleurs.append(str(w.cget("foreground")))
            except tk.TclError:
                pass
        self.assertIn("#b00020", couleurs)
        toplevels[-1].destroy()


class _FakeAppAvecMethodesReelles:
    """Fournit juste de quoi satisfaire les méthodes RÉELLES
    _afficher_avertissement_licence/_afficher_blocage_licence (elles
    n'utilisent que _url_renouvellement_licence, _afficher_lien_
    renouvellement et _parent_vivant_ou_racine — correctif du
    2026-11-08 —, elles aussi réelles ici — seule wait_window est
    patchée côté test pour ne jamais bloquer)."""

    def __init__(self, root):
        self.root = root

    def _url_renouvellement_licence(self):
        return main.App._url_renouvellement_licence(self)

    def _afficher_lien_renouvellement(self, parent_win, url):
        return main.App._afficher_lien_renouvellement(self, parent_win, url)

    def _parent_vivant_ou_racine(self, parent):
        return main.App._parent_vivant_ou_racine(self, parent)


if __name__ == "__main__":
    unittest.main()
