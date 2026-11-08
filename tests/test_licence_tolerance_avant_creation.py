# -*- coding: utf-8 -*-
"""Correctif du 2026-11-08 (TEST-26, HP Windows 10) — deux problèmes liés,
diagnostiqués ensemble :

A. Crash Windows "TclError: bad window path name" : `_choose_tournament_
   file` détruisait `win` (l'écran Bienvenue) AVANT que le contrôle de
   licence/tolérance ne s'exécute, qui tentait alors tk.Toplevel(win) sur
   une fenêtre déjà détruite dans les 4 fonctions `_afficher_*_licence`.
B. Contrôle de licence trop tardif : le fichier .tournoi physique était
   déjà créé (Database(path) + réglages écrits) AVANT que le contrôle ne
   puisse éventuellement le refuser.

Correctif : le contrôle (`_verifier_tolerance_licence_avant_ouverture`,
avec son nouveau paramètre `date_session_connue`) s'exécute désormais
DANS new_tournament()/new_sng()/open_tournament(), AVANT tout sélecteur
de fichier (nouveaux) ou toute ouverture réelle (existant), et AVANT que
`win` ne soit détruite — éliminant la cause du crash ET le problème de
timing en un seul mouvement. Une garde défensive supplémentaire
(`_parent_vivant_ou_racine`) protège aussi tout appelant futur qui
referait la même erreur.

Deux volets de tests, jamais via un vrai App() (trop lourd — voir
tests/test_license_v3_integration_main.py) :
- Structurels (AST/source) : l'ordre des appels dans new_tournament/
  new_sng/open_tournament, et l'absence de transient()/présence du repli
  dans les 4 messages — même principe que tests/test_fermeture_tournoi_
  retour_standalone.py.
- Comportementaux : `_verifier_tolerance_licence_avant_ouverture` sur une
  App réelle au sens Python mais dont __init__ n'a JAMAIS tourné
  (App.__new__ + tk.Tk.__init__ directement — contourne __init__, trop
  coûteux/fragile à instancier pour un test, sans recourir à une
  doublure manuelle de 7 méthodes) ; et les 4 messages eux-mêmes, sur une
  VRAIE racine Tk, avec un parent DÉJÀ DÉTRUIT — reproduit exactement le
  scénario du crash observé sur TEST-26."""
import ast
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

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


class _SourceHelper:
    @classmethod
    def setUpClass(cls):
        cls.source = _lire_main()
        cls.tree = ast.parse(cls.source, filename="main.py")

    def _corps_fonction_imbriquee(self, nom_fonction_parente, nom_fonction_imbriquee):
        """Isole le corps d'une fonction DÉFINIE À L'INTÉRIEUR de
        _choose_tournament_file (new_tournament/new_sng/open_tournament —
        des closures, jamais des méthodes : impossible à appeler
        isolément dans un test, d'où cette vérification structurelle,
        même principe que test_fermeture_tournoi_retour_standalone.py)."""
        meth = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef) and n.name == nom_fonction_parente
        )
        func = next(
            n for n in ast.walk(meth)
            if isinstance(n, ast.FunctionDef) and n.name == nom_fonction_imbriquee
        )
        return ast.get_source_segment(self.source, func) or ""

    def _corps_methode(self, nom_methode):
        func = next(
            n for n in ast.walk(self.tree)
            if isinstance(n, ast.FunctionDef) and n.name == nom_methode
        )
        return ast.get_source_segment(self.source, func) or ""


class OrdreDesAppelsStructurelTest(_SourceHelper, unittest.TestCase):
    """Partie B — le contrôle de licence doit précéder toute création/
    ouverture physique du fichier, dans les 3 chemins possibles."""

    def test_new_tournament_controle_licence_avant_le_selecteur_de_fichier(self):
        corps = self._corps_fonction_imbriquee("_choose_tournament_file", "new_tournament")
        self.assertIn("_verifier_tolerance_licence_avant_ouverture", corps)
        self.assertIn("filedialog.asksaveasfilename", corps)
        self.assertLess(
            corps.index("_verifier_tolerance_licence_avant_ouverture"),
            corps.index("filedialog.asksaveasfilename"),
            "Le contrôle de licence doit precéder le sélecteur de fichier.",
        )

    def test_new_sng_controle_licence_avant_le_selecteur_de_fichier(self):
        corps = self._corps_fonction_imbriquee("_choose_tournament_file", "new_sng")
        self.assertIn("_verifier_tolerance_licence_avant_ouverture", corps)
        self.assertIn("filedialog.asksaveasfilename", corps)
        self.assertLess(
            corps.index("_verifier_tolerance_licence_avant_ouverture"),
            corps.index("filedialog.asksaveasfilename"),
        )

    def test_open_tournament_controle_licence_avant_win_destroy(self):
        corps = self._corps_fonction_imbriquee("_choose_tournament_file", "open_tournament")
        self.assertIn("_verifier_tolerance_licence_avant_ouverture", corps)
        self.assertIn("_read_tournament_date_ro", corps)
        self.assertIn("win.destroy()", corps)
        self.assertLess(
            corps.index("_verifier_tolerance_licence_avant_ouverture"),
            corps.index("win.destroy()"),
            "Le contrôle de licence doit precéder la destruction de win.",
        )

    def test_ancien_controle_en_fin_de_fonction_retire(self):
        """L'ancien appel unique en toute fin de _choose_tournament_file
        (après Database(result["path"])) a été retiré — chacun des 3
        chemins effectue désormais son propre contrôle, plus tôt."""
        corps = self._corps_methode("_choose_tournament_file")
        # Ne doit plus apparaître APRÈS Database( — seuls les appels DANS
        # new_tournament/new_sng/open_tournament (avant tout `self.db =`)
        # doivent subsister.
        idx_database = corps.index("self.db = Database(")
        apres_database = corps[idx_database:]
        self.assertNotIn("_verifier_tolerance_licence_avant_ouverture", apres_database)


class MessagesLicenceSansTransientTest(_SourceHelper, unittest.TestCase):
    """Partie A — les 4 messages ne doivent plus jamais appeler
    transient(), et doivent tous passer par le repli défensif."""

    def test_parent_vivant_ou_racine_definie(self):
        self.assertIn("def _parent_vivant_ou_racine(self, parent):", self.source)

    def test_aucun_transient_dans_les_quatre_messages(self):
        for nom in (
            "_afficher_avertissement_licence", "_afficher_blocage_licence",
            "_afficher_blocage_revocation_licence",
            "_afficher_blocage_verification_requise_licence",
        ):
            with self.subTest(fonction=nom):
                corps = self._corps_methode(nom)
                # Lignes de CODE réel uniquement — les commentaires
                # documentent volontairement "PAS de win.transient(...)",
                # qui contient la même sous-chaîne.
                lignes_code = [
                    ligne for ligne in corps.splitlines()
                    if not ligne.strip().startswith("#")
                ]
                self.assertFalse(
                    any(".transient(" in ligne for ligne in lignes_code),
                    f"{nom} contient encore un appel réel à transient().",
                )
                self.assertIn("_parent_vivant_ou_racine(parent)", corps)
                self.assertIn("grab_set()", corps)


def _app_vide_pour_licence(decision=None):
    """App réelle au sens Python (toutes les méthodes réellement
    appelées en interne — self._afficher_blocage_licence(...), etc. —
    sont donc résolues normalement), mais dont __init__ n'exécute
    JAMAIS (trop lourd/fragile à instancier pour un test — DB, threads,
    voir tests/test_licence_window.py) : App.__new__ + tk.Tk.__init__
    directement, contournement volontaire et explicite."""
    app = main.App.__new__(main.App)
    tk.Tk.__init__(app)
    app.withdraw()
    app._decision_licence_v3 = decision
    return app


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class VerifierToleranceLogiqueTest(unittest.TestCase):
    """_verifier_tolerance_licence_avant_ouverture, décision par décision
    — les 4 messages sont ici des Mock (jamais rendus pour de vrai) :
    seule la LOGIQUE de décision est en jeu, pas le rendu Tk (couvert
    séparément par MessagesLicenceParentDetruitTest ci-dessous)."""

    def setUp(self):
        self.app = _app_vide_pour_licence()
        self.addCleanup(lambda: cleanup_tk(self, "app"))
        for nom in (
            "_afficher_blocage_revocation_licence",
            "_afficher_blocage_verification_requise_licence",
            "_afficher_blocage_licence", "_afficher_avertissement_licence",
        ):
            setattr(self.app, nom, Mock())
        self._tmp_dir = tempfile.TemporaryDirectory(prefix="senaco_tolerance_test_")
        self.addCleanup(self._tmp_dir.cleanup)
        self._tmp_tolerance = os.path.join(self._tmp_dir.name, "tolerance_sessions.json")
        patcher = patch.object(session_tolerance, "TOLERANCE_STATE_FILE", self._tmp_tolerance)
        self.addCleanup(patcher.stop)
        patcher.start()
        env_patcher = patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"})
        self.addCleanup(env_patcher.stop)
        env_patcher.start()

    def test_v3_desactivee_retourne_true_sans_rien_evaluer(self):
        with patch.dict(os.environ, {}, clear=True):
            resultat = self.app._verifier_tolerance_licence_avant_ouverture(
                None, self.app, date_session_connue="2026-11-08",
            )
        self.assertTrue(resultat)
        self.app._afficher_avertissement_licence.assert_not_called()

    def test_decision_none_retourne_true(self):
        self.app._decision_licence_v3 = None
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            None, self.app, date_session_connue="2026-11-08",
        )
        self.assertTrue(resultat)

    def test_licence_normale_retourne_true_sans_dialogue(self):
        self.app._decision_licence_v3 = license_client.DecisionLicence(
            license_client.ETAT_NORMAL, "cycle-1", "",
        )
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            None, self.app, date_session_connue="2026-11-08",
        )
        self.assertTrue(resultat)
        self.app._afficher_avertissement_licence.assert_not_called()
        self.app._afficher_blocage_licence.assert_not_called()

    def test_revocation_bloque_immediatement_sans_tolerance(self):
        self.app._decision_licence_v3 = license_client.DecisionLicence(
            license_client.ETAT_REVOQUE, "cycle-1", "",
        )
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            None, self.app, date_session_connue="2026-11-08",
        )
        self.assertFalse(resultat)
        self.app._afficher_blocage_revocation_licence.assert_called_once()
        self.app._afficher_blocage_licence.assert_not_called()

    def test_nouveau_tournoi_premiere_journee_toleree_avertissement_une_seule_fois(self):
        """Correctif du 2026-11-08 : avant, ce contrôle pouvait s'exécuter
        deux fois pour le MÊME tournoi (une fois via l'ancien appel en
        fin de fonction) — l'avertissement ne doit apparaître qu'UNE
        fois pour un seul appel de cette méthode."""
        session_tolerance.amorcer_ou_reinitialiser_tolerance("cycle-1", chemin_etat=self._tmp_tolerance)
        self.app._decision_licence_v3 = license_client.DecisionLicence(
            license_client.ETAT_EXPIRE, "cycle-1", "",
        )
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            None, self.app, date_session_connue="2026-11-08",
        )
        self.assertTrue(resultat, "1ère journée tolérée : l'ouverture doit continuer.")
        self.app._afficher_avertissement_licence.assert_called_once()
        self.app._afficher_blocage_licence.assert_not_called()

    def test_troisieme_journee_distincte_est_bloquee(self):
        """Simule exactement le scénario du HP : 2 journées déjà
        tolérées (06/11, 07/11), une 3ème distincte (08/11, "aujourd'hui"
        pour un nouveau tournoi) doit être refusée."""
        session_tolerance.reconcilier_depuis_serveur(
            "cycle-1", ["2026-11-06", "2026-11-07"], chemin_etat=self._tmp_tolerance,
        )
        self.app._decision_licence_v3 = license_client.DecisionLicence(
            license_client.ETAT_EXPIRE, "cycle-1", "",
        )
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            None, self.app, date_session_connue="2026-11-08",
        )
        self.assertFalse(resultat, "3ème date de session distincte : doit être refusée.")
        self.app._afficher_blocage_licence.assert_called_once()
        self.app._afficher_avertissement_licence.assert_not_called()

    def test_meme_journee_deja_toleree_najamais_bloquee_reste_gratuite(self):
        """Un nouveau tournoi créé LE MÊME JOUR qu'une journée déjà
        tolérée ne doit jamais être bloqué — l'avertissement continue de
        s'afficher (rappel de l'expiration, comportement établi et
        inchangé), mais AUCUNE nouvelle date n'est consommée (réouverture
        gratuite — voir session_tolerance.py, docstring de
        evaluer_ouverture)."""
        session_tolerance.reconcilier_depuis_serveur(
            "cycle-1", ["2026-11-08"], chemin_etat=self._tmp_tolerance,
        )
        self.app._decision_licence_v3 = license_client.DecisionLicence(
            license_client.ETAT_EXPIRE, "cycle-1", "",
        )
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            None, self.app, date_session_connue="2026-11-08",
        )
        self.assertTrue(resultat)
        self.app._afficher_avertissement_licence.assert_called_once()
        self.app._afficher_blocage_licence.assert_not_called()
        etat = session_tolerance.charger_etat(self._tmp_tolerance)
        self.assertEqual(etat[1], ["2026-11-08"], "Aucune date supplémentaire ne doit avoir été consommée.")

    def test_ouverture_existant_utilise_la_date_du_fichier_jamais_aujourdhui(self):
        """Un tournoi EXISTANT, déjà daté d'il y a longtemps, ne doit
        jamais être traité comme une nouvelle journée "aujourd'hui" —
        confirme que date_session_connue transmis explicitement (ex.
        depuis _read_tournament_date_ro) est bien celui utilisé, jamais
        recalculé. Reste gratuit (date déjà connue), avertissement
        affiché mais aucune date supplémentaire consommée."""
        session_tolerance.reconcilier_depuis_serveur(
            "cycle-1", ["2026-10-01", "2026-10-02"], chemin_etat=self._tmp_tolerance,
        )
        self.app._decision_licence_v3 = license_client.DecisionLicence(
            license_client.ETAT_EXPIRE, "cycle-1", "",
        )
        resultat = self.app._verifier_tolerance_licence_avant_ouverture(
            "/chemin/fictif/ancien.tournoi", self.app, date_session_connue="2026-10-01",
        )
        self.assertTrue(resultat)
        self.app._afficher_avertissement_licence.assert_called_once()
        self.app._afficher_blocage_licence.assert_not_called()
        etat = session_tolerance.charger_etat(self._tmp_tolerance)
        self.assertEqual(sorted(etat[1]), ["2026-10-01", "2026-10-02"])


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class MessagesLicenceParentDetruitTest(unittest.TestCase):
    """Partie A — reproduit exactement le scénario du crash TEST-26 :
    appelle les 4 messages avec un `parent` DÉJÀ DÉTRUIT. Avant le
    correctif, ceci levait TclError "bad window path name" ; après, le
    repli sur self (une vraie racine Tk vivante) doit fonctionner sans
    erreur."""

    def setUp(self):
        self.app = _app_vide_pour_licence()
        self.addCleanup(lambda: cleanup_tk(self, "app"))

    def _parent_deja_detruit(self):
        parent = tk.Toplevel(self.app)
        parent.destroy()
        return parent

    def _appeler_sans_bloquer(self, nom_methode):
        parent = self._parent_deja_detruit()
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            try:
                getattr(self.app, nom_methode)(parent)
            except tk.TclError as e:
                self.fail(f"{nom_methode} a levé TclError avec un parent détruit : {e}")

    def test_avertissement_avec_parent_detruit_ne_leve_pas(self):
        self._appeler_sans_bloquer("_afficher_avertissement_licence")

    def test_blocage_avec_parent_detruit_ne_leve_pas(self):
        """Le scénario EXACT du crash observé sur TEST-26."""
        self._appeler_sans_bloquer("_afficher_blocage_licence")

    def test_revocation_avec_parent_detruit_ne_leve_pas(self):
        self._appeler_sans_bloquer("_afficher_blocage_revocation_licence")

    def test_verification_requise_avec_parent_detruit_ne_leve_pas(self):
        self._appeler_sans_bloquer("_afficher_blocage_verification_requise_licence")

    def test_parent_vivant_ou_racine_renvoie_le_parent_si_vivant(self):
        parent = tk.Toplevel(self.app)
        self.addCleanup(lambda: parent.winfo_exists() and parent.destroy())
        self.assertIs(self.app._parent_vivant_ou_racine(parent), parent)

    def test_parent_vivant_ou_racine_replie_sur_self_si_detruit(self):
        parent = self._parent_deja_detruit()
        self.assertIs(self.app._parent_vivant_ou_racine(parent), self.app)


if __name__ == "__main__":
    unittest.main()
