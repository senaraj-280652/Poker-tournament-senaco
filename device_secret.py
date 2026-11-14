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
import contextlib
import os
import secrets
import sys
import threading

import keyring
import keyring.errors

# Détection de build TEST dupliquée ICI (même marqueur, même logique que
# main.py._is_test_build()) — jamais une dépendance vers main.py : ce
# module reste volontairement isolé (même principe déjà appliqué ailleurs
# dans ce dépôt, ex. les verrous de fichier dupliqués par module plutôt
# que partagés). Demande du 2026-11-13 (isolation des licences entre
# build TEST et production, préparation de la version TEST macOS) : un
# secret d'appareil de test ne doit jamais cohabiter avec un vrai secret
# de production dans le même emplacement sécurisé du système (Trousseau
# macOS / Coffre Windows) — des noms de service distincts garantissent
# deux espaces de noms totalement disjoints, jamais une lecture ni une
# écriture croisée, quelle que soit la plateforme.
_TEST_BUILD_MARKER_FILENAME = "TEST_BUILD_MARKER"


def _est_un_build_test():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.exists(os.path.join(base, _TEST_BUILD_MARKER_FILENAME))


NOM_SERVICE = "Senaco-TEST" if _est_un_build_test() else "Senaco"


# Protège la mutation temporaire de HOME (os.environ est un état global
# du process) contre un futur appel concurrent accidentel -- aujourd'hui
# tous les appels Trousseau se font uniquement sur le thread principal
# Tkinter (dialogues modaux), donc aucune concurrence réelle, mais ce
# verrou rend l'invariant imposé plutôt que simplement documenté.
_verrou_home = threading.Lock()


@contextlib.contextmanager
def _vrai_home_temporairement():
    """Diagnostic du 2026-11-13 : sur macOS, un build TEST redirige HOME
    vers ~/SenacoTEST (voir main.py._rediriger_home_test_macos_si_
    necessaire) pour isoler les données — mais Security.framework (via
    NSHomeDirectory(), utilisé par keyring.backends.macOS) s'appuie sur ce
    même HOME pour localiser le Trousseau de connexion réel, d'où
    « Trousseau introuvable » dès que HOME pointe vers un dossier sans
    Library/Keychains. Restaure le VRAI HOME (jamais en se fiant à la
    valeur courante de HOME, justement redirigée) uniquement le temps de
    l'appel au Trousseau, puis revient à la valeur précédente (même en
    cas d'exception, via finally) pour que les processus enfants lancés
    ensuite héritent toujours de l'isolation TEST normale."""
    if sys.platform != "darwin" or not _est_un_build_test():
        yield
        return
    import pwd
    with _verrou_home:
        ancien = os.environ.get("HOME")
        os.environ["HOME"] = pwd.getpwuid(os.getuid()).pw_dir
        try:
            yield
        finally:
            if ancien is not None:
                os.environ["HOME"] = ancien


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
        with _vrai_home_temporairement():
            return keyring.get_password(NOM_SERVICE, _compte(identifiant_machine))
    except keyring.errors.KeyringError:
        return None


def enregistrer_secret_local(identifiant_machine, secret):
    """Écrit le secret reçu du serveur. Lève ErreurStockageSecret en cas
    d'échec réel — ne JAMAIS continuer comme si l'activation était
    pleinement opérationnelle si cette fonction échoue (demande
    explicite : pas de succès partiel silencieux)."""
    try:
        with _vrai_home_temporairement():
            keyring.set_password(NOM_SERVICE, _compte(identifiant_machine), secret)
    except keyring.errors.KeyringError as e:
        raise ErreurStockageSecret(
            f"Impossible d'enregistrer le secret d'appareil dans le stockage sécurisé local : {e}"
        ) from e


def effacer_secret_local(identifiant_machine):
    """Utilisé par les tests et par une future réinitialisation locale —
    jamais appelée par le flux normal d'activation/vérification."""
    try:
        with _vrai_home_temporairement():
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
        with _vrai_home_temporairement():
            keyring.set_password(NOM_SERVICE, compte_test, valeur_test)
            lu = keyring.get_password(NOM_SERVICE, compte_test)
            keyring.delete_password(NOM_SERVICE, compte_test)
        return lu == valeur_test
    except keyring.errors.KeyringError:
        return False
