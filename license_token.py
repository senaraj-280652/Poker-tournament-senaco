# -*- coding: utf-8 -*-
"""Lot 1 du système de licences Senaco v3 — format de jeton de licence
signé par cryptographie asymétrique (Ed25519, via PyNaCl).

Principe
--------
Remplace à terme le HMAC symétrique de license.py (où extraire le
secret d'un exécutable compilé permettrait de FORGER de nouvelles
licences) par une signature asymétrique : la clé PRIVÉE ne sert qu'à
SIGNER un jeton, et ne vit QUE côté serveur OVH — jamais dans ce dépôt,
jamais dans un exécutable Senaco. La clé PUBLIQUE, elle, ne sert qu'à
VÉRIFIER une signature : l'extraire d'un exécutable compilé ne permet
jamais de forger un nouveau jeton valide, seulement de confirmer qu'un
jeton donné a bien été signé par le détenteur de la clé privée.

Ce module ne fait QUE le format de jeton et sa vérification (ce que
demande le Lot 1) — ni la logique d'expiration/tolérance (Lot 3), ni
l'intégration dans l'application (lot ultérieur, non commencé : ce
module n'est appelé par aucun autre fichier de Senaco pour l'instant).

Portée volontairement limitée du Lot 1 : AUCUNE clé privée réelle n'est
codée en dur ici — `generer_cle_de_test` et `construire_jeton`
n'existent que pour les tests et les futurs outils réservés à
l'éditeur (jamais Senaco lui-même, qui n'importe que `verifier_jeton`/
`extraire_licence`). Voir tests/test_license_token.py, qui vérifie
structurellement l'absence de toute clé privée codée en dur dans ce
fichier.

Format du jeton (texte, transportable par simple copier-coller) :
    base64url(payload JSON canonique) + "." + base64url(signature)

Le payload JSON est signé tel quel (octets UTF-8, clés triées, sans
espace) — la moindre modification d'un seul caractère invalide la
signature. `version_format` est inclus dans chaque payload pour
permettre une évolution future du format sans ambiguïté sur les
jetons déjà émis.
"""
import base64
import json

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

TOKEN_FORMAT_VERSION = 1

# Champs attendus dans le payload d'un jeton de LICENCE (par opposition
# au jeton générique de construire_jeton/verifier_jeton, réutilisable
# pour d'autres usages futurs). Liste fermée : extraire_licence()
# vérifie leur présence, pour détecter tout de suite un jeton d'un
# autre type que l'on tenterait de faire passer pour une licence.
CHAMPS_LICENCE = (
    "id_jeton",         # UUID, identifie CE jeton précis (base possible
                         # d'une future liste de révocation — mécanisme
                         # exact non tranché, voir cahier des charges)
    "identifiant_machine",  # format license.raw_machine_id()
    "type",             # "essai" | "payant" (voir sql/001_schema_senaco.sql)
    "date_debut",        # AAAA-MM-JJ
    "date_expiration",   # AAAA-MM-JJ
    "emis_le",           # horodatage ISO 8601 UTC, émission par le serveur
)


class JetonInvalide(Exception):
    """Levée par verifier_jeton()/extraire_licence() : signature
    invalide, jeton malformé, clé publique absente/invalide, ou format
    non supporté. JAMAIS levée pour une licence dont la DATE est
    dépassée — un jeton authentique mais expiré reste structurellement
    valide (c'est une donnée métier pour le Lot 3, pas une erreur de
    format/signature) : cette distinction permet à l'appelant de
    traiter "jeton forgé/corrompu" (rejet immédiat) séparément de
    "licence expirée" (logique de tolérance)."""


def _encoder_b64(donnees: bytes) -> str:
    return base64.urlsafe_b64encode(donnees).decode("ascii").rstrip("=")


def _decoder_b64(texte: str) -> bytes:
    completement = texte + "=" * (-len(texte) % 4)
    return base64.urlsafe_b64decode(completement)


def generer_cle_de_test():
    """Outil de TEST/OUTILLAGE UNIQUEMENT : génère une paire de clés
    Ed25519 fraîche. Ne représente JAMAIS la vraie clé de production
    (celle-ci n'existera que côté serveur OVH, générée et conservée là-
    bas — voir le cahier des charges, section sécurité). Renvoie
    (cle_privee_hex, cle_publique_hex)."""
    cle_privee = SigningKey.generate()
    return cle_privee.encode().hex(), cle_privee.verify_key.encode().hex()


def construire_jeton(payload, cle_privee_hex):
    """Construit un jeton signé à partir de `payload` (dict JSON-
    sérialisable) et d'une clé privée Ed25519 (hex, 64 caractères).

    RÉSERVÉ au serveur et aux tests : Senaco lui-même n'appelle jamais
    cette fonction avec une vraie clé privée (voir l'en-tête de ce
    fichier) — seulement verifier_jeton()/extraire_licence()."""
    cle_privee = SigningKey(bytes.fromhex(cle_privee_hex))
    corps = dict(payload)
    corps["version_format"] = TOKEN_FORMAT_VERSION
    corps_json = json.dumps(
        corps, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8")
    signature = cle_privee.sign(corps_json).signature
    return f"{_encoder_b64(corps_json)}.{_encoder_b64(signature)}"


def verifier_jeton(jeton, cle_publique_hex):
    """Vérifie la signature de `jeton` avec la clé PUBLIQUE fournie
    (hex) et renvoie son payload (dict) si authentique.

    Lève JetonInvalide — jamais une autre exception — dans tous les
    cas de rejet : clé publique absente/invalide, jeton malformé
    (séparateur manquant, base64 invalide, JSON illisible), signature
    ne correspondant pas, ou version de format non supportée. Ne lève
    JAMAIS JetonInvalide pour un payload dont les DATES sont dépassées
    (voir JetonInvalide)."""
    if not cle_publique_hex:
        raise JetonInvalide("Aucune clé publique de vérification disponible.")

    try:
        corps_b64, signature_b64 = jeton.split(".")
        corps_json = _decoder_b64(corps_b64)
        signature = _decoder_b64(signature_b64)
    except (ValueError, TypeError) as e:
        raise JetonInvalide(f"Jeton malformé : {e}") from e

    try:
        cle_publique = VerifyKey(bytes.fromhex(cle_publique_hex))
        cle_publique.verify(corps_json, signature)
    except (BadSignatureError, ValueError, TypeError) as e:
        raise JetonInvalide(f"Signature invalide : {e}") from e

    try:
        payload = json.loads(corps_json.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise JetonInvalide(f"Contenu du jeton illisible : {e}") from e

    if payload.get("version_format") != TOKEN_FORMAT_VERSION:
        raise JetonInvalide(
            f"Version de format de jeton non supportée : {payload.get('version_format')!r}"
        )

    return payload


def construire_jeton_licence(champs, cle_privee_hex):
    """Construit un jeton de LICENCE (voir CHAMPS_LICENCE) — vérifie
    que `champs` fournit exactement les clés attendues avant de
    signer, pour ne jamais émettre silencieusement un jeton incomplet.
    RÉSERVÉ au serveur et aux tests, comme construire_jeton()."""
    manquants = [c for c in CHAMPS_LICENCE if c not in champs]
    if manquants:
        raise ValueError(f"Champs de licence manquants : {', '.join(manquants)}")
    payload = {c: champs[c] for c in CHAMPS_LICENCE}
    return construire_jeton(payload, cle_privee_hex)


def extraire_licence(jeton, cle_publique_hex):
    """Vérifie `jeton` (voir verifier_jeton) puis contrôle qu'il
    contient bien tous les champs d'une licence (CHAMPS_LICENCE) —
    lève JetonInvalide si le jeton est authentique mais n'est
    structurellement pas un jeton de licence (ex. un futur jeton d'un
    autre usage présenté par erreur à cette fonction)."""
    payload = verifier_jeton(jeton, cle_publique_hex)
    manquants = [c for c in CHAMPS_LICENCE if c not in payload]
    if manquants:
        raise JetonInvalide(
            f"Jeton authentique mais incomplet pour une licence : {', '.join(manquants)}"
        )
    return payload
