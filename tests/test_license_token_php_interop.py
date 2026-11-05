# -*- coding: utf-8 -*-
"""Lot 4 du système de licences Senaco v3 — test d'intégration entre
license_token.py (client Python, Lot 1) et licences.php (serveur,
~/Desktop/senaco_module_serveur/, HORS du dépôt Git — module préparé
séparément, voir le cahier des charges). Vérifie que le serveur PHP
peut signer un jeton qu'un VRAI client Python accepte, avec le MÊME
format de jeton et la même signature Ed25519 des deux côtés — c'est le
point le plus sensible de toute l'architecture (toute divergence de
canonicalisation JSON entre les deux langages romprait silencieusement
la vérification des licences en production).

Ignoré proprement (skip, jamais un échec) si :
- aucun interpréteur `php` n'est disponible sur cette machine, ou
- le module serveur n'est pas présent à l'emplacement attendu (il vit
  hors du dépôt, sur le poste de développement de l'éditeur
  uniquement) — jamais une dépendance du dépôt Git lui-même."""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import license_token as lt  # noqa: E402

_MODULE_SERVEUR = os.path.join(os.path.expanduser("~"), "Desktop", "senaco_module_serveur")
_LICENCES_PHP = os.path.join(_MODULE_SERVEUR, "prive", "lib", "licences.php")
_PHP_BIN = shutil.which("php")


def _php_disponible():
    return _PHP_BIN is not None and os.path.isfile(_LICENCES_PHP)


_PAYLOAD_REFERENCE = {
    "id_jeton": "22222222-2222-2222-2222-222222222222",
    "identifiant_machine": "B2C3-D4E5-F607-89A1",
    "type": "payant",
    "date_debut": "2026-11-02",
    "date_expiration": "2027-11-02",
    "emis_le": "2026-11-02T12:34:56Z",
}


def _executer_php_signature(payload, cle_privee_hex):
    """Lance un script PHP minimal qui charge licences.php, signe
    `payload` avec `cle_privee_hex`, et imprime
    {"jeton": "...", "json_canonique": "..."} sur stdout en JSON.

    `payload` est transmis comme une CHAÎNE JSON (double-encodée : une
    fois pour son propre contenu, une fois pour en faire un littéral de
    chaîne PHP valide), puis décodé côté PHP via json_decode() —
    jamais injecté tel quel comme syntaxe PHP : {"a": "b"} n'est pas un
    littéral de tableau PHP valide (PHP interprète {...} comme un bloc),
    contrairement à un objet JSON ou dict Python."""
    payload_json = json.dumps(payload)
    script_php = f"""<?php
declare(strict_types=1);
require {json.dumps(_LICENCES_PHP)};

$payload = json_decode({json.dumps(payload_json)}, true);
$cle_privee_hex = {json.dumps(cle_privee_hex)};

$payload_avec_version = $payload;
$payload_avec_version['version_format'] = SENACO_TOKEN_FORMAT_VERSION;
$json_canonique = senaco_canonicaliser_json($payload_avec_version);

$jeton = senaco_construire_jeton_licence($payload, $cle_privee_hex);

echo json_encode(['jeton' => $jeton, 'json_canonique' => $json_canonique]);
"""
    resultat = subprocess.run(
        [_PHP_BIN, "-r", script_php.replace("<?php\n", "")],
        capture_output=True, text=True, timeout=15,
    )
    if resultat.returncode != 0:
        raise RuntimeError(f"Échec du script PHP : {resultat.stderr}")
    return json.loads(resultat.stdout)


@unittest.skipUnless(_php_disponible(), "php introuvable ou module serveur absent de ce poste")
class PhpSigneEtPythonVerifieTest(unittest.TestCase):
    """Le scénario réel de production : le serveur (PHP) signe, le
    client (Python, license_token.py) vérifie — jamais l'inverse en
    usage normal (voir license_token.py : construire_jeton_licence
    n'est réservée qu'aux tests côté Python)."""

    def test_jeton_signe_par_php_est_accepte_par_python(self):
        priv, pub = lt.generer_cle_de_test()
        resultat = _executer_php_signature(_PAYLOAD_REFERENCE, priv)

        payload_verifie = lt.extraire_licence(resultat["jeton"], pub)
        for cle, valeur in _PAYLOAD_REFERENCE.items():
            self.assertEqual(payload_verifie[cle], valeur)
        self.assertEqual(payload_verifie["version_format"], lt.TOKEN_FORMAT_VERSION)

    def test_canonicalisation_json_identique_cote_php_et_python(self):
        """Le cœur de la compatibilité : les OCTETS JSON signés doivent
        être rigoureusement identiques des deux côtés, sinon une
        signature produite par l'un ne sera jamais vérifiable par
        l'autre malgré un format de jeton par ailleurs correct."""
        priv, _ = lt.generer_cle_de_test()
        resultat = _executer_php_signature(_PAYLOAD_REFERENCE, priv)

        payload_python = dict(_PAYLOAD_REFERENCE)
        payload_python["version_format"] = lt.TOKEN_FORMAT_VERSION
        json_canonique_python = json.dumps(
            payload_python, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        )
        self.assertEqual(resultat["json_canonique"], json_canonique_python)

    def test_jeton_signe_par_php_avec_une_mauvaise_cle_publique_est_rejete(self):
        priv, _ = lt.generer_cle_de_test()
        _, autre_pub = lt.generer_cle_de_test()
        resultat = _executer_php_signature(_PAYLOAD_REFERENCE, priv)

        with self.assertRaises(lt.JetonInvalide):
            lt.extraire_licence(resultat["jeton"], autre_pub)

    def test_jeton_php_falsifie_est_rejete_par_python(self):
        priv, pub = lt.generer_cle_de_test()
        resultat = _executer_php_signature(_PAYLOAD_REFERENCE, priv)
        jeton_falsifie = resultat["jeton"][:-4] + "xxxx"
        with self.assertRaises(lt.JetonInvalide):
            lt.extraire_licence(jeton_falsifie, pub)

    def test_licence_offerte_a_vie_9999_12_31_est_signee_et_verifiee_sans_aucun_cas_particulier(self):
        """Licences offertes (demande du 2026-11-05) : une licence « à
        vie » est représentée par la sentinelle date_expiration =
        9999-12-31 — jamais une colonne/un champ nullable. Ce test
        prouve, avec un VRAI aller-retour PHP -> Python, qu'aucun des
        deux côtés n'a besoin d'un cas particulier pour cette date :
        licences.php la signe comme n'importe quelle autre chaîne, et
        license_token.py la vérifie identiquement."""
        priv, pub = lt.generer_cle_de_test()
        payload_a_vie = dict(_PAYLOAD_REFERENCE)
        payload_a_vie["type"] = "offerte"
        payload_a_vie["date_expiration"] = "9999-12-31"

        resultat = _executer_php_signature(payload_a_vie, priv)
        payload_verifie = lt.extraire_licence(resultat["jeton"], pub)
        self.assertEqual(payload_verifie["date_expiration"], "9999-12-31")
        self.assertEqual(payload_verifie["type"], "offerte")


class SansPhpTest(unittest.TestCase):
    """Garde-fou : si php/le module serveur ne sont pas disponibles sur
    CETTE machine, le reste de la suite ne doit jamais en dépendre
    (déjà garanti par le skip ci-dessus) — ce test documente juste
    explicitement l'état constaté, visible dans le rapport de tests."""

    def test_etat_de_disponibilite_constate(self):
        if not _php_disponible():
            self.skipTest(
                f"php={_PHP_BIN!r}, module serveur présent={os.path.isfile(_LICENCES_PHP)!r} "
                "— tests d'intégration PHP non exécutés sur cette machine."
            )


if __name__ == "__main__":
    unittest.main()
