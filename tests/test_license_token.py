# -*- coding: utf-8 -*-
"""Lot 1 du système de licences Senaco v3 (voir license_token.py) :
format de jeton signé (Ed25519/PyNaCl) et sa vérification. Ne teste
QUE le format/la signature — ni l'expiration ni la tolérance (Lot 3),
ni l'intégration dans l'application (lot ultérieur, non commencé)."""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license_token as lt  # noqa: E402


def _champs_licence_valides(**overrides):
    base = {
        "id_jeton": "11111111-1111-1111-1111-111111111111",
        "identifiant_machine": "A1B2-C3D4-E5F6-0789",
        "type": "essai",
        "date_debut": "2026-11-02",
        "date_expiration": "2027-02-02",
        "emis_le": "2026-11-02T10:00:00Z",
    }
    base.update(overrides)
    return base


class JetonGeneriqueTest(unittest.TestCase):
    """construire_jeton()/verifier_jeton() — le mécanisme de signature
    générique, indépendant du contenu métier "licence"."""

    def setUp(self):
        self.cle_privee, self.cle_publique = lt.generer_cle_de_test()

    def test_aller_retour_preserve_le_payload(self):
        payload = {"a": 1, "b": "deux"}
        jeton = lt.construire_jeton(payload, self.cle_privee)
        relu = lt.verifier_jeton(jeton, self.cle_publique)
        self.assertEqual(relu, {**payload, "version_format": lt.TOKEN_FORMAT_VERSION})

    def test_jeton_est_une_chaine_texte_avec_un_seul_point(self):
        jeton = lt.construire_jeton({"x": 1}, self.cle_privee)
        self.assertIsInstance(jeton, str)
        self.assertEqual(jeton.count("."), 1)

    def test_deux_cles_generees_sont_differentes(self):
        priv2, pub2 = lt.generer_cle_de_test()
        self.assertNotEqual(priv2, self.cle_privee)
        self.assertNotEqual(pub2, self.cle_publique)

    def test_payload_modifie_invalide_la_signature(self):
        jeton = lt.construire_jeton({"montant": 100}, self.cle_privee)
        corps_b64, signature_b64 = jeton.split(".")
        # Remplace le corps par celui d'un AUTRE payload, en gardant la
        # signature d'origine : la moindre modification doit invalider.
        autre_jeton = lt.construire_jeton({"montant": 999999}, self.cle_privee)
        autre_corps_b64, _ = autre_jeton.split(".")
        falsifie = f"{autre_corps_b64}.{signature_b64}"
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(falsifie, self.cle_publique)

    def test_signature_tronquee_est_rejetee(self):
        jeton = lt.construire_jeton({"x": 1}, self.cle_privee)
        falsifie = jeton[:-4]
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(falsifie, self.cle_publique)

    def test_jeton_signe_par_une_autre_cle_est_rejete(self):
        """Le cœur de la propriété recherchée : seul le détenteur de
        LA clé privée attendue peut produire un jeton accepté — une
        autre paire de clés, même valide en elle-même, ne suffit pas."""
        autre_cle_privee, _ = lt.generer_cle_de_test()
        jeton = lt.construire_jeton({"x": 1}, autre_cle_privee)
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(jeton, self.cle_publique)

    def test_jeton_sans_point_separateur_est_rejete(self):
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton("ceci-nest-pas-un-jeton", self.cle_publique)

    def test_jeton_vide_est_rejete(self):
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton("", self.cle_publique)

    def test_base64_invalide_est_rejete(self):
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton("!!!invalide!!!.!!!invalide!!!", self.cle_publique)

    def test_cle_publique_absente_est_rejetee(self):
        jeton = lt.construire_jeton({"x": 1}, self.cle_privee)
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(jeton, "")
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(jeton, None)

    def test_cle_publique_mal_formee_est_rejetee(self):
        jeton = lt.construire_jeton({"x": 1}, self.cle_privee)
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(jeton, "pas-de-lhexadecimal-valide")

    def test_version_de_format_non_supportee_est_rejetee(self):
        """Falsifie uniquement version_format (en resignant avec la
        bonne clé, pour isoler CETTE vérification de celle de la
        signature) : un jeton par ailleurs authentique mais d'une
        version de format future/inconnue doit être rejeté, jamais
        interprété à l'aveugle."""
        import json
        payload = {"x": 1, "version_format": 999}
        corps_json = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        from nacl.signing import SigningKey
        cle = SigningKey(bytes.fromhex(self.cle_privee))
        signature = cle.sign(corps_json).signature
        jeton = f"{lt._encoder_b64(corps_json)}.{lt._encoder_b64(signature)}"
        with self.assertRaises(lt.JetonInvalide):
            lt.verifier_jeton(jeton, self.cle_publique)


class JetonLicenceTest(unittest.TestCase):
    """construire_jeton_licence()/extraire_licence() — la spécialisation
    "licence" du jeton générique."""

    def setUp(self):
        self.cle_privee, self.cle_publique = lt.generer_cle_de_test()

    def test_aller_retour_preserve_tous_les_champs(self):
        champs = _champs_licence_valides()
        jeton = lt.construire_jeton_licence(champs, self.cle_privee)
        relu = lt.extraire_licence(jeton, self.cle_publique)
        for cle, valeur in champs.items():
            self.assertEqual(relu[cle], valeur)

    def test_licence_offerte_a_vie_9999_12_31_fait_un_aller_retour_normal(self):
        """Licences offertes (demande du 2026-11-05) : une licence « à
        vie » est représentée par la sentinelle date_expiration =
        9999-12-31 — jamais NULL ni un format spécial. Ce module ne doit
        avoir besoin d'AUCUN cas particulier pour ça : un simple champ
        texte comme un autre, signé et vérifié normalement."""
        champs = _champs_licence_valides()
        champs["type"] = "offerte"
        champs["date_expiration"] = "9999-12-31"
        jeton = lt.construire_jeton_licence(champs, self.cle_privee)
        relu = lt.extraire_licence(jeton, self.cle_publique)
        self.assertEqual(relu["date_expiration"], "9999-12-31")
        self.assertEqual(relu["type"], "offerte")

    def test_champ_manquant_refuse_la_construction(self):
        champs = _champs_licence_valides()
        del champs["date_expiration"]
        with self.assertRaises(ValueError):
            lt.construire_jeton_licence(champs, self.cle_privee)

    def test_jeton_generique_sans_les_champs_licence_est_refuse_par_extraire_licence(self):
        """Un jeton authentique (signature valide) mais qui n'est
        structurellement pas un jeton de licence doit être rejeté par
        extraire_licence(), pas accepté avec des champs manquants."""
        jeton = lt.construire_jeton({"autre_chose": 1}, self.cle_privee)
        with self.assertRaises(lt.JetonInvalide):
            lt.extraire_licence(jeton, self.cle_publique)

    def test_falsification_dune_date_est_detectee(self):
        """Non-régression explicite de la propriété centrale du Lot 1 :
        on ne peut pas prolonger une licence en modifiant juste la date
        d'expiration du jeton sans en invalider la signature."""
        champs = _champs_licence_valides(date_expiration="2027-02-02")
        jeton = lt.construire_jeton_licence(champs, self.cle_privee)

        champs_prolonges = dict(champs)
        champs_prolonges["date_expiration"] = "2099-01-01"
        jeton_falsifie_complet = lt.construire_jeton_licence(champs_prolonges, self.cle_privee)
        # Prend le corps du jeton falsifié (non signé par la vraie clé
        # dans ce test) mais la signature de l'original : simule une
        # tentative de "copier-coller" de date sans détenir la clé.
        _, signature_originale_b64 = jeton.split(".")
        corps_falsifie_b64, _ = jeton_falsifie_complet.split(".")
        jeton_bricole = f"{corps_falsifie_b64}.{signature_originale_b64}"

        with self.assertRaises(lt.JetonInvalide):
            lt.extraire_licence(jeton_bricole, self.cle_publique)


class AucuneCleProductionCodeEnDurTest(unittest.TestCase):
    """Garde structurelle (demande explicite du cahier des charges :
    "ne jamais intégrer de clé privée dans Senaco") — relit le code
    source de license_token.py et vérifie qu'aucune constante de clé
    privée n'y est codée en dur au niveau module. N'empêche pas
    generer_cle_de_test()/construire_jeton() d'exister (outils réservés
    au serveur/aux tests, qui ne contiennent eux-mêmes aucune clé) : ne
    vérifie que l'ABSENCE d'une valeur de clé figée dans le fichier."""

    def test_aucune_constante_hexadecimale_de_64_caracteres_au_niveau_module(self):
        source = inspect.getsource(lt)
        import re
        # Une clé Ed25519 (privée ou publique) fait 64 caractères hex.
        # Cherche toute chaîne littérale de ce format dans le fichier —
        # il ne doit en exister AUCUNE (les clés ne sont que générées
        # dynamiquement ou reçues en paramètre, jamais écrites ici).
        motifs = re.findall(r'"[0-9a-fA-F]{64}"|\'[0-9a-fA-F]{64}\'', source)
        self.assertEqual(motifs, [], "Une clé (privée ou publique) semble codée en dur.")

    def test_module_najoute_aucun_point_dentree_appele_par_main(self):
        """Lot 1 n'est pas censé être branché à l'application : vérifie
        que main.py n'importe pas encore license_token (confirmerait
        une intégration prématurée, hors périmètre de ce lot)."""
        chemin_main = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py",
        )
        with open(chemin_main, "r", encoding="utf-8") as f:
            contenu = f.read()
        self.assertNotIn("license_token", contenu)


if __name__ == "__main__":
    unittest.main()
