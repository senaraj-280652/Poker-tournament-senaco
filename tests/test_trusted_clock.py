# -*- coding: utf-8 -*-
"""Lot 2 du système de licences Senaco v3 (voir trusted_clock.py) :
détection de manipulation de l'horloge, sans aucune décision de
blocage (ce module ne fait que détecter/exposer un état). Isolé de
l'application — non branché, vérifié structurellement ci-dessous."""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trusted_clock as tc  # noqa: E402

_T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc)


class TrustedClockTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="trusted_clock_test_")
        self.addCleanup(self._tmp.cleanup)
        self.chemin = os.path.join(self._tmp.name, "horloge_fiable.json")

    def _initialiser(self, date_serveur=_T0):
        tc.initialiser_apres_verification_en_ligne(date_serveur, chemin=self.chemin)


class CasNormalTest(TrustedClockTestCase):
    def test_avance_normale_est_ok_et_met_a_jour_la_date_fiable(self):
        self._initialiser()
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=5))
        self.assertEqual(r.etat, tc.ETAT_OK)
        self.assertEqual(r.derniere_date_fiable, _T0 + timedelta(days=5))

    def test_appels_repetes_qui_avancent_font_toujours_progresser_la_reference(self):
        self._initialiser()
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=1))
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=2))
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=3))
        self.assertEqual(r.derniere_date_fiable, _T0 + timedelta(days=3))

    def test_petit_recul_sous_le_seuil_est_tolere(self):
        """Dérive mineure (ex. ressynchronisation NTP) : jamais signalée
        comme suspecte, et la date fiable n'est pas reculée pour autant."""
        self._initialiser()
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=1))
        petit_recul = _T0 + timedelta(days=1) - timedelta(minutes=30)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=petit_recul)
        self.assertEqual(r.etat, tc.ETAT_OK)
        self.assertEqual(r.derniere_date_fiable, _T0 + timedelta(days=1))

    def test_rester_a_la_meme_date_est_ok(self):
        self._initialiser()
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_OK)


class ReculSuspectTest(TrustedClockTestCase):
    def test_recul_important_est_detecte(self):
        self._initialiser()
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=10))
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=3))
        self.assertEqual(r.etat, tc.ETAT_RECUL_SUSPECT)
        self.assertEqual(r.ecart, timedelta(days=7))

    def test_recul_suspect_najamais_les_jours_de_la_date_fiable_stockee(self):
        """Point central de la protection anti-triche : un recul détecté
        ne doit JAMAIS faire reculer la référence elle-même, sinon
        l'attaquant pourrait "valider" son recul à l'appel suivant."""
        self._initialiser()
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=10))
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)  # recul suspect, ignoré

        stocke = tc.charger_etat(self.chemin)
        derniere_date_fiable, _ = stocke
        self.assertEqual(derniere_date_fiable, _T0 + timedelta(days=10))

    def test_recul_tente_puis_avance_normale_depuis_la_vraie_reference(self):
        """Après une tentative de recul (ignorée), une avance normale
        repartant de la VRAIE référence stockée doit redevenir OK."""
        self._initialiser()
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=10))
        tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)  # recul suspect
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0 + timedelta(days=11))
        self.assertEqual(r.etat, tc.ETAT_OK)


class DonneesAbsentesCorrompuesTest(TrustedClockTestCase):
    def test_fichier_absent_est_indetermine(self):
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_fichier_absent_najamais_cree_un_etat_ok_tout_seul(self):
        """Non-régression explicite de la protection anti-réinitialisation
        de l'essai : appeler evaluer_horloge() en boucle sur un fichier
        absent ne doit JAMAIS finir par renvoyer "ok" de lui-même."""
        for _ in range(5):
            r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
            self.assertEqual(r.etat, tc.ETAT_INDETERMINE)
        self.assertFalse(os.path.exists(self.chemin))

    def test_json_illisible_est_indetermine(self):
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            f.write("{ceci n'est pas du json valide")
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_json_qui_nest_pas_un_objet_est_indetermine(self):
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_champ_date_manquant_est_indetermine(self):
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump({"autre_champ": "valeur"}, f)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_champ_date_type_incorrect_est_indetermine(self):
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump({"derniere_date_fiable": 12345}, f)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_date_mal_formee_est_indeterminee(self):
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump({"derniere_date_fiable": "pas-une-date"}, f)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_date_sans_fuseau_horaire_est_indeterminee(self):
        """Une date naïve (sans fuseau) est une donnée incohérente pour
        ce module — jamais interprétée "par défaut" en UTC ou en heure
        locale, ce qui masquerait silencieusement une ambiguïté."""
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump({"derniere_date_fiable": "2026-11-01T10:00:00"}, f)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_date_serveur_confirmee_mal_formee_est_aussi_indeterminee(self):
        os.makedirs(os.path.dirname(self.chemin), exist_ok=True)
        with open(self.chemin, "w", encoding="utf-8") as f:
            json.dump({
                "derniere_date_fiable": tc._formater_iso_utc(_T0),
                "derniere_date_serveur_confirmee": "invalide",
            }, f)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=_T0)
        self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_initialiser_avec_une_date_naive_est_refuse(self):
        with self.assertRaises(ValueError):
            tc.initialiser_apres_verification_en_ligne(
                datetime(2026, 11, 1, 10, 0, 0), chemin=self.chemin,
            )


class FuseauHoraireEtHeureEteTest(TrustedClockTestCase):
    """Les comparaisons se font toujours en UTC : un changement de
    fuseau horaire ou un passage heure été/hiver ne doit jamais, en
    lui-même, ressembler à une avance ni à un recul."""

    def test_deux_fuseaux_representant_le_meme_instant_najamais_de_recul(self):
        self._initialiser(date_serveur=_T0)
        # Même instant UTC, représenté dans un fuseau +02:00 (ex. Paris
        # en été) : doit être traité IDENTIQUEMENT à _T0, pas comme un
        # recul lié à la différence d'affichage horaire.
        meme_instant_paris_ete = _T0.astimezone(timezone(timedelta(hours=2)))
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=meme_instant_paris_ete)
        self.assertEqual(r.etat, tc.ETAT_OK)
        self.assertEqual(r.ecart, timedelta(0))

    def test_passage_heure_hiver_doccident_naffecte_pas_linstant_utc(self):
        """Simule le passage heure d'été -> heure d'hiver (l'horloge
        LOCALE affichée recule d'une heure, mais l'instant UTC avance
        normalement) : ne doit jamais déclencher recul_suspect."""
        avant_horaire_hiver = datetime(2026, 10, 25, 1, 30, tzinfo=timezone(timedelta(hours=2)))
        apres_horaire_hiver = datetime(2026, 10, 25, 1, 30, tzinfo=timezone(timedelta(hours=1)))
        self.assertGreater(
            apres_horaire_hiver.astimezone(timezone.utc),
            avant_horaire_hiver.astimezone(timezone.utc),
        )
        tc.initialiser_apres_verification_en_ligne(avant_horaire_hiver, chemin=self.chemin)
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=apres_horaire_hiver)
        self.assertEqual(r.etat, tc.ETAT_OK)

    def test_changement_de_fuseau_horaire_du_poste_najamais_de_recul(self):
        """Un club qui change le fuseau horaire de son ordinateur (ex.
        poste reconfiguré) sans toucher à la date/heure réelle ne doit
        jamais être vu comme une manipulation."""
        self._initialiser(date_serveur=_T0)
        une_semaine_plus_tard_autre_fuseau = (
            (_T0 + timedelta(days=7)).astimezone(timezone(timedelta(hours=-5)))
        )
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=une_semaine_plus_tard_autre_fuseau)
        self.assertEqual(r.etat, tc.ETAT_OK)


class CorrectionLegitimeTest(TrustedClockTestCase):
    def test_petite_correction_arriere_est_toleree_sans_etre_signalee(self):
        """Une horloge légèrement en avance, corrigée de quelques
        minutes (ex. par NTP), ne doit pas être traitée comme une
        fraude — c'est précisément le rôle de SEUIL_RECUL_SUSPECT."""
        self._initialiser(date_serveur=_T0 + timedelta(minutes=10))
        correction = _T0  # 10 minutes "en arrière" par rapport à l'état stocké
        r = tc.evaluer_horloge(chemin=self.chemin, maintenant=correction)
        self.assertEqual(r.etat, tc.ETAT_OK)


class IntegrationTest(unittest.TestCase):
    """Vérifications structurelles demandées par le cahier des charges :
    module isolé, jamais interrompu, jamais branché pour l'instant."""

    def test_najamais_dexception_meme_sur_entrees_absurdes(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "horloge.json")
            with open(chemin, "w", encoding="utf-8") as f:
                f.write("\x00\x01binaire-invalide")
            try:
                r = tc.evaluer_horloge(chemin=chemin, maintenant=_T0)
            except Exception as e:  # pragma: no cover - ne doit jamais arriver
                self.fail(f"evaluer_horloge() a levé une exception : {e}")
            self.assertEqual(r.etat, tc.ETAT_INDETERMINE)

    def test_module_non_importe_par_main(self):
        chemin_main = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py",
        )
        with open(chemin_main, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.assertNotIn("trusted_clock", contenu)


if __name__ == "__main__":
    unittest.main()
