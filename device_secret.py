# -*- coding: utf-8 -*-
"""Secret d'appariement par appareil (sécurité, demande du 2026-11-04) —
stockage local sécurisé via le Trousseau macOS / le coffre
d'identifiants Windows (paquet `keyring`, API unique pour les deux
systèmes), JAMAIS un fichier en clair.

DORMANT par construction : importé uniquement depuis license_client.py
(lui-même importé uniquement en local, à l'intérieur du système de
licences v3 gated par POKER_TOURNAMENT_LICENSE_V3 — voir main.py).

Principe (voir prive/lib/securite_appareil.php côté serveur) : un
identifiant_machine seul n'est jamais une preuve d'identité suffisante.
Le secret reçu une seule fois à l'activation est la preuve réelle,
conservée ici hors d'atteinte d'une simple lecture de fichier.

Échec de stockage (Trousseau/coffre indisponible, verrouillé, refusé) :
JAMAIS traité comme un repli silencieux vers un fichier en clair — voir
stockage_disponible() (auto-test à vide, à appeler AVANT toute requête
consommant un code à usage unique) et ErreurStockageSecret (levée par
enregistrer_secret_local() en cas d'échec réel d'écriture, que
license_client.py doit alors traiter comme une activation NON
pleinement opérationnelle, jamais un succès partiel silencieux)."""
import secrets

import keyring
import keyring.errors

NOM_SERVICE = "Senaco"


class ErreurStockageSecret(Exception):
    """Levée par enregistrer_secret_local() quand l'écriture dans le
    stockage sécurisé échoue réellement — ne doit jamais être avalée
    silencieusement par l'appelant (voir license_client.activer_en_ligne)."""


def _compte(identifiant_machine):
    return f"secret-appareil:{identifiant_machine}"


def lire_secret_local(identifiant_machine):
    """Renvoie le secret stocké pour cette machine, ou None (absent,
    jamais émis, ou stockage sécurisé indisponible — les trois cas sont
    indiscernables ici par construction, jamais une distinction fiable
    à faire côté client : voir le serveur, seule source d'autorité)."""
    try:
        return keyring.get_password(NOM_SERVICE, _compte(identifiant_machine))
    except keyring.errors.KeyringError:
        return None


def enregistrer_secret_local(identifiant_machine, secret):
    """Écrit le secret reçu du serveur. Lève ErreurStockageSecret en cas
    d'échec réel — ne JAMAIS continuer comme si l'activation était
    pleinement opérationnelle si cette fonction échoue (demande
    explicite : pas de succès partiel silencieux)."""
    try:
        keyring.set_password(NOM_SERVICE, _compte(identifiant_machine), secret)
    except keyring.errors.KeyringError as e:
        raise ErreurStockageSecret(
            f"Impossible d'enregistrer le secret d'appareil dans le stockage sécurisé local : {e}"
        ) from e


def effacer_secret_local(identifiant_machine):
    """Utilisé par les tests et par une future réinitialisation locale —
    jamais appelée par le flux normal d'activation/vérification."""
    try:
        keyring.delete_password(NOM_SERVICE, _compte(identifiant_machine))
    except keyring.errors.PasswordDeleteError:
        pass  # déjà absent : pas une erreur


def stockage_disponible():
    """Auto-test d'écriture/lecture/suppression d'une valeur jetable —
    à appeler AVANT toute requête serveur qui consommerait un code à
    usage unique ou émettrait un nouveau secret, pour ne jamais gâcher
    une autorisation à cause d'un stockage local défaillant (demande
    explicite, point 6). Ne lève jamais — renvoie simplement un
    booléen."""
    compte_test = "_test_disponibilite_"
    valeur_test = secrets.token_hex(16)
    try:
        keyring.set_password(NOM_SERVICE, compte_test, valeur_test)
        lu = keyring.get_password(NOM_SERVICE, compte_test)
        keyring.delete_password(NOM_SERVICE, compte_test)
        return lu == valeur_test
    except keyring.errors.KeyringError:
        return False
