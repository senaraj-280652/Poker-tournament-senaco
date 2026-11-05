# -*- coding: utf-8 -*-
"""Lot 3 du système de licences Senaco v3 (voir session_tolerance.py) :
tolérance de 2 dates de session distinctes après expiration, avant
blocage. Isolé de l'application — non branché, vérifié
structurellement ci-dessous."""
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import session_tolerance as stol  # noqa: E402
from database import Database, _read_tournament_date_ro  # noqa: E402


def _date_fixe(date):
    return lambda chemin: date


class SessionToleranceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="session_tolerance_test_")
        self.addCleanup(self._tmp.cleanup)
        self.chemin_etat = os.path.join(self._tmp.name, "tolerance_sessions.json")

    def _amorcer(self, cycle_id="cycle-1"):
        stol.amorcer_ou_reinitialiser_tolerance(cycle_id, chemin_etat=self.chemin_etat)
        return cycle_id


class LicenceValideTest(SessionToleranceTestCase):
    def test_licence_valide_najamais_touche_au_fichier_detat(self):
        r = stol.evaluer_ouverture(
            "/fake.tournoi", licence_expiree=False, cycle_id="c1",
            lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat,
        )
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_NORMAL)
        self.assertFalse(os.path.exists(self.chemin_etat))


class UnTournoiUneDateTest(SessionToleranceTestCase):
    def test_premier_tournoi_dune_date_est_tolere_et_enregistre(self):
        cycle = self._amorcer()
        r = stol.evaluer_ouverture(
            "/tournoi_a.tournoi", True, cycle,
            lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat,
        )
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(r.dates_autorisees, ["2026-11-01"])


class PlusieursTournoisMemeDateTest(SessionToleranceTestCase):
    def test_deux_tournois_meme_date_ne_comptent_quune_fois(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        r = stol.evaluer_ouverture("/t2.tournoi", True, cycle,
                                    lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(r.dates_autorisees, ["2026-11-01"])


class PlusieursSitAndGoMemeDateTest(SessionToleranceTestCase):
    def test_deux_sng_meme_date_ne_comptent_quune_fois(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/sng1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        r = stol.evaluer_ouverture("/sng2.tournoi", True, cycle,
                                    lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(r.dates_autorisees, ["2026-11-01"])


class MelangeTournoiEtSngTest(SessionToleranceTestCase):
    def test_un_tournoi_et_un_sng_meme_date_ne_comptent_quune_fois(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/tournoi.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        r = stol.evaluer_ouverture("/sng.tournoi", True, cycle,
                                    lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(len(r.dates_autorisees), 1)


class DeuxDatesDistinctesTest(SessionToleranceTestCase):
    def test_deux_dates_distinctes_sont_toutes_deux_tolerees(self):
        cycle = self._amorcer()
        r1 = stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                     lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        r2 = stol.evaluer_ouverture("/t2.tournoi", True, cycle,
                                     lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)
        self.assertEqual(r1.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(r2.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(r2.dates_autorisees, ["2026-11-01", "2026-11-02"])


class TroisiemeDateBloqueeTest(SessionToleranceTestCase):
    def test_troisieme_date_distincte_est_bloquee(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        stol.evaluer_ouverture("/t2.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)
        r3 = stol.evaluer_ouverture("/t3.tournoi", True, cycle,
                                     lire_date_fn=_date_fixe("2026-11-03"), chemin_etat=self.chemin_etat)
        self.assertEqual(r3.etat, stol.ETAT_BLOQUE)
        # Un blocage ne consomme rien : la liste reste à 2 entrées.
        self.assertEqual(r3.dates_autorisees, ["2026-11-01", "2026-11-02"])

    def test_apres_blocage_une_date_deja_connue_reste_toleree(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        stol.evaluer_ouverture("/t2.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)
        stol.evaluer_ouverture("/t3.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-03"), chemin_etat=self.chemin_etat)
        r = stol.evaluer_ouverture("/t1-reouvert.tournoi", True, cycle,
                                    lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)


class ReouvertureSessionDejaCompteeTest(SessionToleranceTestCase):
    def test_reouverture_ne_consomme_rien_meme_apres_la_limite(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        for _ in range(5):
            r = stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                        lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
            self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
            self.assertEqual(r.dates_autorisees, ["2026-11-01"])


class RedemarrageSenacoTest(SessionToleranceTestCase):
    def test_etat_survit_a_un_redemarrage_simule(self):
        """"Redémarrer Senaco" = rien de plus, ici, qu'un nouvel appel à
        evaluer_ouverture() avec le MÊME chemin_etat sur disque — aucune
        donnée en mémoire n'est jamais nécessaire entre deux appels."""
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        # "Redémarrage" : relecture pure depuis le fichier, nouvel appel.
        etat = stol.charger_etat(self.chemin_etat)
        self.assertEqual(etat, (cycle, ["2026-11-01"]))
        r = stol.evaluer_ouverture("/t2.tournoi", True, cycle,
                                    lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.dates_autorisees, ["2026-11-01", "2026-11-02"])


class PassageMinuitTest(SessionToleranceTestCase):
    def test_meme_tournoi_ouvert_avant_et_apres_minuit_ne_compte_quune_fois(self):
        """La date de session vient du fichier (fixée à sa création),
        jamais de l'horloge au moment du contrôle — un même fichier
        ouvert "avant minuit" puis consulté "après minuit" renvoie
        TOUJOURS la même date de session, par construction."""
        cycle = self._amorcer()
        lire_date_du_fichier = _date_fixe("2026-11-01")  # fixée à la création, jamais recalculée
        r_avant_minuit = stol.evaluer_ouverture(
            "/soiree.tournoi", True, cycle, lire_date_fn=lire_date_du_fichier,
            chemin_etat=self.chemin_etat,
        )
        r_apres_minuit = stol.evaluer_ouverture(
            "/soiree.tournoi", True, cycle, lire_date_fn=lire_date_du_fichier,
            chemin_etat=self.chemin_etat,
        )
        self.assertEqual(r_avant_minuit.date_session, r_apres_minuit.date_session)
        self.assertEqual(r_apres_minuit.dates_autorisees, ["2026-11-01"])

    def test_verification_reelle_avec_une_vraie_base_tournoi_et_sng(self):
        """Vérifie, avec de VRAIS fichiers .tournoi (via Database, le
        même mécanisme qu'en production — main.py écrit tournament_date
        une seule fois à la création), que tournament_date fonctionne
        identiquement pour un tournoi normal et pour un Sit & Go, et
        qu'il reste fixe quelle que soit l'heure de consultation —
        demande explicite du point 6 du cahier des charges."""
        with tempfile.TemporaryDirectory() as d:
            chemin_tournoi = os.path.join(d, "soiree_normale.tournoi")
            chemin_sng = os.path.join(d, "soiree_sng.tournoi")

            db1 = Database(chemin_tournoi)
            db1.set_settings({"tournament_date": "2026-11-01"})
            db1.conn.close()

            db2 = Database(chemin_sng)
            db2.set_settings({"tournament_date": "2026-11-01", "is_sng": 1})
            db2.conn.close()

            self.assertEqual(_read_tournament_date_ro(chemin_tournoi), "2026-11-01")
            self.assertEqual(_read_tournament_date_ro(chemin_sng), "2026-11-01")

            cycle = self._amorcer()
            r1 = stol.evaluer_ouverture(chemin_tournoi, True, cycle, chemin_etat=self.chemin_etat)
            r2 = stol.evaluer_ouverture(chemin_sng, True, cycle, chemin_etat=self.chemin_etat)
            self.assertEqual(r1.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
            self.assertEqual(r2.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
            # Même date -> une seule entrée malgré les 2 fichiers distincts.
            self.assertEqual(r2.dates_autorisees, ["2026-11-01"])


class DonneesAbsentesCorrompuesManipuleesTest(SessionToleranceTestCase):
    def test_etat_absent_est_indetermine_jamais_un_depart_a_zero(self):
        r = stol.evaluer_ouverture("/t1.tournoi", True, "cycle-x",
                                    lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_INDETERMINE)
        self.assertFalse(os.path.exists(self.chemin_etat))

    def test_etat_absent_najamais_puis_devient_ok_tout_seul(self):
        for _ in range(5):
            r = stol.evaluer_ouverture("/t1.tournoi", True, "cycle-x",
                                        lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
            self.assertEqual(r.etat, stol.ETAT_INDETERMINE)

    def test_json_corrompu_est_indetermine(self):
        os.makedirs(os.path.dirname(self.chemin_etat), exist_ok=True)
        with open(self.chemin_etat, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json")
        r = stol.evaluer_ouverture("/t1.tournoi", True, "cycle-x",
                                    lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_INDETERMINE)

    def test_cycle_id_different_de_celui_stocke_est_indetermine(self):
        """Empêche un cycle_id local modifié/forgé de repartir à zéro
        silencieusement — seul amorcer_ou_reinitialiser_tolerance() peut
        légitimement changer de cycle."""
        self._amorcer(cycle_id="cycle-reel")
        stol.evaluer_ouverture("/t1.tournoi", True, "cycle-reel",
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        r = stol.evaluer_ouverture("/t2.tournoi", True, "cycle-invente-localement",
                                    lire_date_fn=_date_fixe("2026-11-05"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_INDETERMINE)
        # L'état réel n'a pas été modifié par cette tentative.
        self.assertEqual(stol.charger_etat(self.chemin_etat), ("cycle-reel", ["2026-11-01"]))

    def test_fichier_supprime_puis_recree_ne_redonne_pas_une_tolerance_fraiche(self):
        """Simule la "suppression du fichier local" évoquée par le
        cahier des charges : après suppression, l'état redevient
        indéterminé, jamais "0 date consommée" accepté tacitement."""
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        os.remove(self.chemin_etat)
        r = stol.evaluer_ouverture("/t2.tournoi", True, cycle,
                                    lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_INDETERMINE)

    def test_date_de_session_illisible_najamais_bloquant(self):
        cycle = self._amorcer()
        r = stol.evaluer_ouverture("/corrompu.tournoi", True, cycle,
                                    lire_date_fn=lambda p: "", chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_ANOMALIE_LECTURE)
        # Rien n'a été consommé pour une anomalie de lecture.
        self.assertEqual(stol.charger_etat(self.chemin_etat), (cycle, []))


class DeuxProcessusSimultanesTest(SessionToleranceTestCase):
    def test_deux_threads_ouvrant_deux_dates_distinctes_simultanement(self):
        """Simule 2 "process" Senaco (ici 2 threads, le verrou de
        fichier étant de toute façon un mécanisme OS valable aussi
        bien entre threads qu'entre process réels — même principe que
        open_windows._file_lock) ouvrant chacun une date DIFFÉRENTE au
        même instant : les deux doivent être acceptées (2 dates, pas
        plus), jamais une perte d'écriture ni un comptage en double."""
        cycle = self._amorcer()
        resultats = {}
        barriere = threading.Barrier(2)

        def ouvrir(nom, date):
            barriere.wait()
            resultats[nom] = stol.evaluer_ouverture(
                f"/{nom}.tournoi", True, cycle,
                lire_date_fn=_date_fixe(date), chemin_etat=self.chemin_etat,
            )

        t1 = threading.Thread(target=ouvrir, args=("a", "2026-11-01"))
        t2 = threading.Thread(target=ouvrir, args=("b", "2026-11-02"))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(resultats["a"].etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(resultats["b"].etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        etat_final = stol.charger_etat(self.chemin_etat)
        self.assertEqual(sorted(etat_final[1]), ["2026-11-01", "2026-11-02"])

    def test_trois_threads_une_seule_date_bloquee_en_trop(self):
        """3 dates distinctes ouvertes "simultanément" : exactement 2
        doivent être acceptées, la troisième bloquée — peu importe
        laquelle gagne la course, jamais les 3."""
        cycle = self._amorcer()
        resultats = {}
        barriere = threading.Barrier(3)

        def ouvrir(nom, date):
            barriere.wait()
            resultats[nom] = stol.evaluer_ouverture(
                f"/{nom}.tournoi", True, cycle,
                lire_date_fn=_date_fixe(date), chemin_etat=self.chemin_etat,
            )

        threads = [
            threading.Thread(target=ouvrir, args=(nom, date))
            for nom, date in (("a", "2026-11-01"), ("b", "2026-11-02"), ("c", "2026-11-03"))
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        etats = [r.etat for r in resultats.values()]
        self.assertEqual(etats.count(stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT), 2)
        self.assertEqual(etats.count(stol.ETAT_BLOQUE), 1)
        etat_final = stol.charger_etat(self.chemin_etat)
        self.assertEqual(len(etat_final[1]), 2)


class RenouvellementTest(SessionToleranceTestCase):
    def test_renouvellement_reinitialise_la_tolerance(self):
        cycle1 = self._amorcer(cycle_id="avant-renouvellement")
        stol.evaluer_ouverture("/t1.tournoi", True, cycle1,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        stol.evaluer_ouverture("/t2.tournoi", True, cycle1,
                                lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)

        cycle2 = self._amorcer(cycle_id="apres-renouvellement")
        r = stol.evaluer_ouverture("/t3.tournoi", True, cycle2,
                                    lire_date_fn=_date_fixe("2026-11-01"),  # même date qu'avant
                                    chemin_etat=self.chemin_etat)
        self.assertEqual(r.etat, stol.ETAT_AUTORISE_AVEC_AVERTISSEMENT)
        self.assertEqual(r.dates_autorisees, ["2026-11-01"])

    def test_dates_a_synchroniser_reflete_letat_courant(self):
        cycle = self._amorcer()
        stol.evaluer_ouverture("/t1.tournoi", True, cycle,
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        self.assertEqual(stol.dates_a_synchroniser(self.chemin_etat), ["2026-11-01"])

    def test_dates_a_synchroniser_vide_si_etat_absent(self):
        self.assertEqual(stol.dates_a_synchroniser(self.chemin_etat), [])


class ReconciliationDepuisServeurTest(SessionToleranceTestCase):
    """reconcilier_depuis_serveur() — sécurité du 2026-11-03 : protège
    contre la suppression du fichier local de tolérance en reconstruisant
    l'état exactement depuis ce que le serveur rapporte comme déjà
    connu, jamais une fusion ni un calcul local."""

    def test_reconstruit_letat_a_partir_de_rien(self):
        stol.reconcilier_depuis_serveur("cycle-x", ["2026-11-01", "2026-11-02"],
                                         chemin_etat=self.chemin_etat)
        self.assertEqual(
            stol.charger_etat(self.chemin_etat),
            ("cycle-x", ["2026-11-01", "2026-11-02"]),
        )

    def test_remplace_completement_un_etat_local_existant_different(self):
        """Simule exactement le scénario de contournement : un état
        local falsifié/incohérent est intégralement REMPLACÉ par la
        vérité serveur, jamais fusionné avec lui."""
        self._amorcer(cycle_id="cycle-x")
        stol.evaluer_ouverture("/t1.tournoi", True, "cycle-x",
                                lire_date_fn=_date_fixe("2026-11-09"), chemin_etat=self.chemin_etat)
        stol.reconcilier_depuis_serveur("cycle-x", ["2026-11-01", "2026-11-02"],
                                         chemin_etat=self.chemin_etat)
        etat = stol.charger_etat(self.chemin_etat)
        self.assertEqual(etat, ("cycle-x", ["2026-11-01", "2026-11-02"]))
        self.assertNotIn("2026-11-09", etat[1])

    def test_fichier_supprime_puis_reconcilie_retrouve_les_vraies_dates(self):
        """LE scénario demandé explicitement : suppression locale du
        fichier de tolérance, suivie d'une reconnexion — l'état doit
        redevenir celui du serveur, jamais 0 date consommée."""
        self._amorcer(cycle_id="cycle-x")
        stol.evaluer_ouverture("/t1.tournoi", True, "cycle-x",
                                lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=self.chemin_etat)
        stol.evaluer_ouverture("/t2.tournoi", True, "cycle-x",
                                lire_date_fn=_date_fixe("2026-11-02"), chemin_etat=self.chemin_etat)

        os.remove(self.chemin_etat)
        self.assertIsNone(stol.charger_etat(self.chemin_etat))  # bien supprimé

        # Le "serveur" (simulé ici) rapporte toujours ses 2 dates
        # réelles, inchangées par la suppression locale.
        stol.reconcilier_depuis_serveur("cycle-x", ["2026-11-01", "2026-11-02"],
                                         chemin_etat=self.chemin_etat)

        r = stol.evaluer_ouverture("/t3.tournoi", True, "cycle-x",
                                    lire_date_fn=_date_fixe("2026-11-03"), chemin_etat=self.chemin_etat)
        self.assertEqual(
            r.etat, stol.ETAT_BLOQUE,
            "La 3e date doit rester bloquée même après suppression+reconciliation "
            "— jamais un compteur remis à zéro par la suppression.",
        )

    def test_plus_de_deux_dates_serveur_est_tronque(self):
        """Défense en profondeur : même si la réponse réseau contenait
        par erreur plus de 2 dates, jamais plus de TOLERANCE_MAX_DATES
        retenues localement."""
        stol.reconcilier_depuis_serveur(
            "cycle-x", ["2026-11-01", "2026-11-02", "2026-11-03"], chemin_etat=self.chemin_etat,
        )
        self.assertEqual(len(stol.charger_etat(self.chemin_etat)[1]), 2)

    def test_cycle_id_vide_est_refuse(self):
        with self.assertRaises(ValueError):
            stol.reconcilier_depuis_serveur("", ["2026-11-01"], chemin_etat=self.chemin_etat)

    def test_valeurs_non_textuelles_dans_dates_serveur_sont_ignorees(self):
        stol.reconcilier_depuis_serveur(
            "cycle-x", ["2026-11-01", None, 42, "2026-11-02"], chemin_etat=self.chemin_etat,
        )
        self.assertEqual(
            stol.charger_etat(self.chemin_etat), ("cycle-x", ["2026-11-01", "2026-11-02"]),
        )


class IntegrationTest(unittest.TestCase):
    def test_module_importe_uniquement_localement_jamais_en_tete_de_fichier(self):
        """Mise à jour du 2026-11-03 (raccordement de la tolérance aux
        ouvertures de tournoi, voir tests/test_license_tolerance_
        tournament_opening.py pour le détail du câblage) : ce module
        EST désormais utilisé par main.py — mais JAMAIS importé en tête
        de fichier (import local uniquement, dans
        App._verifier_tolerance_licence_avant_ouverture), pour rester
        dormant par défaut comme license_client (voir ce même principe
        documenté dans main.py)."""
        chemin_main = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py",
        )
        with open(chemin_main, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.assertIn("import session_tolerance", contenu)

        lignes_import_entete = [
            l for l in contenu.splitlines()[:70] if l.strip().startswith(("import ", "from "))
        ]
        self.assertFalse(
            any("session_tolerance" in l for l in lignes_import_entete),
            "session_tolerance ne doit jamais être importé parmi les imports de tête de fichier.",
        )

    def test_najamais_dexception_meme_sur_entrees_absurdes(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "tolerance.json")
            with open(chemin, "w", encoding="utf-8") as f:
                f.write("\x00\x01binaire-invalide")
            try:
                r = stol.evaluer_ouverture("/t.tournoi", True, "c1",
                                            lire_date_fn=_date_fixe("2026-11-01"), chemin_etat=chemin)
            except Exception as e:  # pragma: no cover
                self.fail(f"evaluer_ouverture() a levé une exception : {e}")
            self.assertEqual(r.etat, stol.ETAT_INDETERMINE)


if __name__ == "__main__":
    unittest.main()
