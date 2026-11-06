# -*- coding: utf-8 -*-
"""Lot 5 du système de licences Senaco v3 — orchestration client réelle.

DORMANT PAR DÉFAUT (demande explicite du 2026-11-03) : ce module n'est
appelé par main.py QUE si la variable d'environnement
POKER_TOURNAMENT_LICENSE_V3 est définie — voir main.py, App.__init__.
Tant qu'elle ne l'est pas, le verrou HMAC actuel (license.py,
ActivationDialog) reste l'UNIQUE mécanisme actif, strictement
inchangé. Aucune machine réelle n'est affectée par ce lot tant que
cette variable n'est pas explicitement positionnée.

Combine les trois modules isolés des lots précédents avec de vrais
appels réseau (Lot 4, activer.php/verifier.php) :
- license_token.py (Lot 1) : vérification du jeton signé Ed25519
- trusted_clock.py (Lot 2) : détection de manipulation de l'horloge
- session_tolerance.py (Lot 3) : amorcé ici dès qu'un nouveau cycle de
  licence EXPIRE (jamais pour une révocation, voir ci-dessous), et
  réconcilié avec le serveur à chaque vérification en ligne réussie
  (voir verifier_en_ligne — protection explicite du 2026-11-03 contre
  une suppression du fichier local de tolérance). Son câblage dans le
  flux d'OUVERTURE DE TOURNOI vit dans main.py (App._verifier_
  tolerance_licence_avant_ouverture), pas ici.

RÉVOCATION vs EXPIRATION (décision du 2026-11-03, section sécurité) :
traitées différemment, jamais interchangeables. Une expiration est un
délai naturel, non punitif — la tolérance de 2 dates de session
s'applique (session_tolerance.py). Une révocation est une décision
ADMINISTRATIVE délibérée de l'éditeur (fraude, remplacement
d'ordinateur, impayé contesté...) : AUCUNE tolérance, blocage immédiat
de toute nouvelle ouverture — jamais d'amorçage de session_tolerance
pour ce cas (voir evaluer_licence, branche "revoque"). Un renouvellement
ne lève jamais, à lui seul, une révocation administrative.

Matrice de décision (cahier des charges, "Contrôle de licence et
connexion Internet") :
    Internet dispo + licence valide        -> ETAT_NORMAL
    Internet dispo + licence expirée       -> ETAT_EXPIRE
    Internet indispo + dernière info valide -> ETAT_NORMAL (hors-ligne)
    Internet indispo + dernière info expirée -> ETAT_EXPIRE
    Internet indispo + validité inconnue   -> ETAT_VERIFICATION_OBLIGATOIRE
Révocation détectée en ligne -> ETAT_REVOQUE (jamais détectable hors
ligne, par construction — voir le cahier des charges, "vérification de
la révocation à chaque connexion réussie").

La date système locale n'est JAMAIS utilisée pour décider de
l'expiration : seule trusted_clock.evaluer_horloge() (dernière date
fiable connue, mise à jour par le SERVEUR lors d'un succès en ligne)
sert de référence hors-ligne."""
import json
import os
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import device_secret
import license as licensing
import license_token
import session_tolerance
import trusted_clock

LICENSE_STATE_FILE = os.path.join(os.path.expanduser("~"), ".poker_tournament", "licence_v3.json")

# Protocole de rotation des secrets (validé le 2026-11-06) : jeton de
# tentative conservé DURABLEMENT sur disque, écrit AVANT tout appel
# réseau — survit à une coupure électrique/un redémarrage, pas
# seulement à une simple coupure réseau (voir verifier_en_ligne). Se
# supprime UNIQUEMENT une fois le nouveau secret confirmé enregistré
# dans le stockage sécurisé local (device_secret.py) — jamais avant.
ROTATION_EN_ATTENTE_FILE = os.path.join(os.path.expanduser("~"), ".poker_tournament", "rotation_en_attente.json")
# Purge défensive d'une tentative devenue trop ancienne pour être
# honorée côté serveur (fenêtre de 24h, voir securite_appareil.php:
# SENACO_ROTATION_FENETRE_HEURES) — marge large, jamais une source de
# blocage, juste un nettoyage pour ne pas conserver indéfiniment un
# fichier devenu inutile.
ROTATION_EN_ATTENTE_AGE_MAX_HEURES = 48

ENV_URL_SERVEUR = "POKER_TOURNAMENT_LICENSE_SERVER_URL"
ENV_CLE_PUBLIQUE = "POKER_TOURNAMENT_LICENSE_PUBLIC_KEY_HEX"

TIMEOUT_RESEAU_SECONDES = 5.0

ETAT_NORMAL = "normal"
ETAT_EXPIRE = "expire"
ETAT_REVOQUE = "revoque"
ETAT_VERIFICATION_OBLIGATOIRE = "verification_obligatoire"
ETAT_ACTIVATION_REQUISE = "activation_requise"  # jamais activé, aucun état local


@dataclass
class DecisionLicence:
    etat: str
    cycle_id: Optional[str]
    message: str


class ErreurActivation(Exception):
    """Levée par activer_en_ligne() en cas d'échec — jamais avalée
    silencieusement, contrairement à verifier_en_ligne() : la première
    activation DOIT être en ligne (cahier des charges), un échec doit
    donc être visible de l'utilisateur, pas traité comme un simple
    "pas de réseau" transitoire."""


class ErreurIdentificationInsuffisante(ErreurActivation):
    """Levée par activer_en_ligne() quand le serveur refuse explicitement
    (401) une machine déjà enregistrée sans secret d'appareil valide ni
    code d'autorisation — jamais confondue avec une simple panne réseau
    ou un refus générique : l'appelant (main.py) doit orienter
    l'utilisateur vers une procédure de migration/récupération, jamais
    vers un nouvel essai gratuit (sécurité, demande du 2026-11-04)."""


def _url_serveur():
    url = os.environ.get(ENV_URL_SERVEUR)
    if not url:
        raise RuntimeError(
            f"{ENV_URL_SERVEUR} non défini — aucun serveur de licences configuré."
        )
    return url.rstrip("/")


def _cle_publique_hex():
    cle = os.environ.get(ENV_CLE_PUBLIQUE)
    if not cle:
        raise RuntimeError(
            f"{ENV_CLE_PUBLIQUE} non défini — impossible de vérifier un jeton de licence."
        )
    return cle


def _charger_etat_local(chemin=None):
    chemin = chemin or LICENSE_STATE_FILE
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("jeton"), str):
        return None
    return data


def _enregistrer_etat_local(jeton, chemin=None):
    chemin = chemin or LICENSE_STATE_FILE
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"jeton": jeton}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, chemin)


def _jeton_tentative_en_attente(chemin=None):
    """Relit un jeton de tentative déjà écrit AVANT une précédente
    coupure (réseau, électrique, redémarrage) — pour le réutiliser tel
    quel, jamais un nouveau, afin que le serveur reconnaisse qu'il
    s'agit du rejeu de la MÊME tentative (voir securite_appareil.php:
    senaco_verifier_et_roter_secret). Renvoie None si absent, invalide,
    ou trop ancien pour être encore honoré côté serveur (purge au
    passage dans ce dernier cas — jamais une erreur, juste un nettoyage)."""
    chemin = chemin or ROTATION_EN_ATTENTE_FILE
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("jeton_tentative"), str) \
            or not isinstance(data.get("cree_le"), str):
        return None
    try:
        cree_le = datetime.fromisoformat(data["cree_le"])
    except ValueError:
        return None
    age_heures = (datetime.now(timezone.utc) - cree_le).total_seconds() / 3600.0
    if age_heures > ROTATION_EN_ATTENTE_AGE_MAX_HEURES:
        _effacer_tentative_en_attente(chemin)
        return None
    return data["jeton_tentative"]


def _enregistrer_tentative_en_attente(jeton_tentative, chemin=None):
    """Écrit le jeton de tentative AVANT l'appel réseau — voir le
    commentaire de ROTATION_EN_ATTENTE_FILE. Écriture atomique, même
    principe que _enregistrer_etat_local."""
    chemin = chemin or ROTATION_EN_ATTENTE_FILE
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            {"jeton_tentative": jeton_tentative, "cree_le": datetime.now(timezone.utc).isoformat()},
            f, ensure_ascii=False, indent=2,
        )
    os.replace(tmp, chemin)


def _effacer_tentative_en_attente(chemin=None):
    """À appeler UNIQUEMENT après confirmation que le nouveau secret a
    bien été enregistré dans le stockage sécurisé local — jamais avant
    (voir verifier_en_ligne)."""
    chemin = chemin or ROTATION_EN_ATTENTE_FILE
    try:
        os.remove(chemin)
    except FileNotFoundError:
        pass


def _post_json(url, payload):
    """Renvoie (code_http, dict_reponse) — (0, None) pour toute
    anomalie réseau (hors-ligne, timeout, DNS, TLS...), jamais
    d'exception. Un code HTTP d'erreur (4xx/5xx) renvoie son propre
    corps JSON si disponible, pour permettre à l'appelant de
    distinguer une vraie erreur serveur d'une simple absence de
    réseau."""
    donnees = json.dumps(payload).encode("utf-8")
    requete = urllib.request.Request(
        url, data=donnees, method="POST",
        headers={"Content-Type": "application/json", "X-Forwarded-Proto": "https"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=TIMEOUT_RESEAU_SECONDES) as reponse:
            return reponse.status, json.loads(reponse.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return e.code, None
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError, ValueError):
        return 0, None


def activer_en_ligne(nom_club, email, plateforme=None, code_activation=None):
    """Première activation, réinstallation, migration/récupération ou
    transfert — selon les paramètres et l'état local/serveur (voir
    public/activer.php). Ne tolère AUCUN mode hors-ligne, lève une
    exception sur tout échec, jamais un repli silencieux.

    `code_activation` : code à usage unique fourni par l'administrateur
    (migration/récupération/transfert) — optionnel, jamais requis pour
    une toute première activation.

    Sécurité (demande du 2026-11-04, point 6) : teste la disponibilité
    du stockage sécurisé local AVANT tout appel serveur — n'importe
    quelle réponse réussie émettrait/renverrait un secret qu'il faut
    ensuite pouvoir enregistrer ; un stockage indisponible ne doit
    jamais gâcher un code à usage unique ni laisser croire à une
    activation pleinement opérationnelle."""
    if not device_secret.stockage_disponible():
        raise ErreurActivation(
            "Le stockage sécurisé local (Trousseau/Coffre d'identifiants) est indisponible sur cet "
            "ordinateur — activation impossible pour l'instant. Réessayez après avoir vérifié que "
            "cette fonctionnalité du système d'exploitation est accessible."
        )

    plateforme = plateforme or ("windows" if os.name == "nt" else "macos")
    identifiant_machine = licensing.machine_id_display()
    secret_local = device_secret.lire_secret_local(identifiant_machine)

    payload = {
        "identifiant_machine": identifiant_machine,
        "plateforme": plateforme,
        "nom_club": nom_club,
        "email": email,
    }
    if secret_local:
        payload["secret_appareil"] = secret_local
    if code_activation:
        payload["code_activation"] = code_activation

    code, reponse = _post_json(_url_serveur() + "/activer.php", payload)
    if code == 401:
        raise ErreurIdentificationInsuffisante(
            "Cet ordinateur est déjà enregistré mais ne peut pas être identifié automatiquement — "
            "contactez l'éditeur pour obtenir un code de migration ou de récupération."
        )
    if code != 200 or reponse is None:
        message = (reponse or {}).get("erreur", "connexion impossible") if reponse else "connexion impossible"
        raise ErreurActivation(f"Activation refusée par le serveur : {message}")

    jeton = reponse.get("jeton")
    if not isinstance(jeton, str):
        raise ErreurActivation("Réponse du serveur invalide (jeton absent).")

    secret_recu = reponse.get("secret_appareil")
    if isinstance(secret_recu, str) and secret_recu:
        try:
            device_secret.enregistrer_secret_local(identifiant_machine, secret_recu)
        except device_secret.ErreurStockageSecret as e:
            # Demande explicite (point 6) : jamais un succès partiel
            # silencieux — le serveur a bien émis/consommé une
            # autorisation, mais sans le secret enregistré localement,
            # la prochaine vérification échouera. L'utilisateur doit le
            # savoir immédiatement, pas découvrir un blocage plus tard.
            raise ErreurActivation(
                f"Le serveur a répondu, mais l'enregistrement sécurisé local du secret d'appareil a "
                f"échoué ({e}) — l'activation n'est PAS pleinement opérationnelle. Contactez l'éditeur "
                f"si le problème persiste."
            ) from e

    _enregistrer_etat_local(jeton)

    date_serveur_brute = reponse.get("date_serveur")
    if isinstance(date_serveur_brute, str):
        try:
            trusted_clock.initialiser_apres_verification_en_ligne(
                datetime.fromisoformat(date_serveur_brute.replace("Z", "+00:00"))
            )
        except ValueError:
            pass  # date serveur mal formée : n'empêche pas l'activation elle-même

    return DecisionLicence(ETAT_NORMAL, reponse.get("licence", {}).get("id_jeton"), "Activation réussie.")


def verifier_en_ligne():
    """Appelle /verifier.php. Renvoie le dict de réponse si succès,
    None pour toute anomalie — réseau, jeton local illisible, secret
    d'appareil local absent, OU refus explicite du serveur (401,
    identification insuffisante) — ces trois derniers cas sont traités
    IDENTIQUEMENT à une panne réseau : jamais d'exception, la matrice de
    décision (evaluer_licence) se replie alors sur l'état local fiable,
    exactement comme pour une vraie coupure réseau. Une distinction plus
    fine entre "hors ligne" et "identification refusée" n'apporterait
    rien ici : dans les deux cas, impossible de confirmer l'état réel en
    ligne, donc même repli hors-ligne prudent.

    Protocole de rotation des secrets (validé le 2026-11-06) : chaque
    vérification réussie fait émettre un nouveau secret par le serveur
    (voir securite_appareil.php). Le jeton de tentative correspondant
    est écrit DURABLEMENT sur disque AVANT l'appel réseau (survit à une
    coupure électrique, pas seulement réseau) et n'est effacé qu'une
    fois le nouveau secret confirmé enregistré dans le stockage
    sécurisé local — jamais avant, pour ne jamais perdre la possibilité
    de rejouer la même tentative après un redémarrage."""
    etat_local = _charger_etat_local()
    if etat_local is None:
        return None
    try:
        cle_publique = _cle_publique_hex()
        payload_courant = license_token.extraire_licence(etat_local["jeton"], cle_publique)
    except (RuntimeError, license_token.JetonInvalide):
        return None

    identifiant_machine = payload_courant["identifiant_machine"]
    secret_local = device_secret.lire_secret_local(identifiant_machine)
    if not secret_local:
        return None

    jeton_tentative = _jeton_tentative_en_attente()
    if jeton_tentative is None:
        jeton_tentative = secrets.token_hex(16)
        _enregistrer_tentative_en_attente(jeton_tentative)

    dates = session_tolerance.dates_a_synchroniser()
    code, reponse = _post_json(_url_serveur() + "/verifier.php", {
        "identifiant_machine": identifiant_machine,
        "identifiant_machine_frais": licensing.machine_id_display(),
        "secret_appareil": secret_local,
        "jeton_tentative": jeton_tentative,
        "dates_session_a_synchroniser": dates,
    })
    if code != 200 or reponse is None:
        return None  # Tentative en attente CONSERVÉE : la fenêtre de rattrapage serveur (24h) couvre le prochain essai.

    secret_recu = reponse.get("secret_appareil")
    if isinstance(secret_recu, str) and secret_recu:
        try:
            device_secret.enregistrer_secret_local(identifiant_machine, secret_recu)
        except device_secret.ErreurStockageSecret:
            # Jamais un succès partiel silencieux (même principe
            # qu'activer_en_ligne) : la tentative reste en attente,
            # rejouable au prochain lancement avec ce même jeton — rien
            # d'autre n'est modifié localement tant que ce n'est pas
            # confirmé.
            return None
    _effacer_tentative_en_attente()

    jeton = reponse.get("jeton")
    if isinstance(jeton, str):
        _enregistrer_etat_local(jeton)

    date_serveur_brute = reponse.get("date_serveur")
    if isinstance(date_serveur_brute, str):
        try:
            trusted_clock.initialiser_apres_verification_en_ligne(
                datetime.fromisoformat(date_serveur_brute.replace("Z", "+00:00"))
            )
        except ValueError:
            pass

    # Sécurité (demande du 2026-11-03) : reconstitue SYSTÉMATIQUEMENT
    # l'état local de tolérance depuis le serveur — source d'autorité,
    # jamais une fusion locale — à chaque vérification en ligne réussie
    # d'une licence expirée, jamais seulement en réaction à un état
    # local déjà indéterminé. Garantit qu'une suppression du fichier
    # local de tolérance ne peut jamais, après une reconnexion, repartir
    # sur un compteur remis à zéro : le serveur connaît déjà
    # l'historique réel (voir session_tolerance.reconcilier_depuis_
    # serveur et public/verifier.php, dates_tolerance.connues).
    if not reponse.get("revoque") and reponse.get("licence", {}).get("statut") == "expiree":
        dates_connues = reponse.get("dates_tolerance", {}).get("connues")
        if isinstance(dates_connues, list):
            try:
                session_tolerance.reconcilier_depuis_serveur(payload_courant["id_jeton"], dates_connues)
            except ValueError:
                pass  # id_jeton absent/vide : rien de fiable à réconcilier

    return reponse


def _amorcer_tolerance_si_nouveau_cycle(cycle_id):
    """Appelée uniquement en détectant une licence EXPIRÉE : amorce
    session_tolerance.py (Lot 3) pour CE cycle si ce n'est pas déjà
    fait — jamais si un cycle (même expiré) est déjà en cours, pour ne
    jamais réinitialiser une tolérance déjà partiellement consommée."""
    etat_tolerance = session_tolerance.charger_etat()
    if etat_tolerance is None or etat_tolerance[0] != cycle_id:
        session_tolerance.amorcer_ou_reinitialiser_tolerance(cycle_id)


def evaluer_licence():
    """Point d'entrée principal : implémente la matrice de décision
    complète. Ne lève jamais d'exception — toute anomalie se traduit
    par un DecisionLicence explicite, jamais une interruption."""
    etat_local = _charger_etat_local()
    if etat_local is None:
        return DecisionLicence(
            ETAT_ACTIVATION_REQUISE, None,
            "Aucune licence locale — activation en ligne requise (voir activer_en_ligne).",
        )

    reponse_serveur = verifier_en_ligne()

    if reponse_serveur is not None:
        # id_jeton n'apparaît pas tel quel dans reponse_serveur["licence"]
        # (voir public/verifier.php) — seul le jeton signé lui-même le
        # porte ; on le relit donc depuis là, seule source fiable ici.
        try:
            payload = license_token.extraire_licence(reponse_serveur["jeton"], _cle_publique_hex())
            cycle_id = payload["id_jeton"]
        except (KeyError, RuntimeError, license_token.JetonInvalide):
            cycle_id = None

        if reponse_serveur.get("revoque"):
            # Décision du 2026-11-03 : une révocation entraîne un
            # blocage IMMÉDIAT, SANS tolérance (contrairement à une
            # expiration naturelle) — jamais d'amorçage de
            # session_tolerance ici, il n'y a rien à tolérer.
            return DecisionLicence(ETAT_REVOQUE, cycle_id, "Licence révoquée.")

        if reponse_serveur.get("licence", {}).get("statut") == "expiree":
            if cycle_id:
                _amorcer_tolerance_si_nouveau_cycle(cycle_id)
            return DecisionLicence(ETAT_EXPIRE, cycle_id, "Licence expirée (confirmé en ligne).")

        return DecisionLicence(ETAT_NORMAL, cycle_id, "Licence valide (confirmé en ligne).")

    # --- Hors ligne : se replier sur le dernier jeton connu localement.
    try:
        cle_publique = _cle_publique_hex()
        payload = license_token.extraire_licence(etat_local["jeton"], cle_publique)
    except (RuntimeError, license_token.JetonInvalide):
        return DecisionLicence(
            ETAT_VERIFICATION_OBLIGATOIRE, None,
            "Jeton local illisible/invalide — vérification en ligne nécessaire.",
        )

    resultat_horloge = trusted_clock.evaluer_horloge()
    if resultat_horloge.etat != trusted_clock.ETAT_OK:
        return DecisionLicence(
            ETAT_VERIFICATION_OBLIGATOIRE, payload["id_jeton"],
            f"Horloge non fiable ({resultat_horloge.etat}) — vérification en ligne nécessaire.",
        )

    date_expiration = date.fromisoformat(payload["date_expiration"])
    aujourdhui_fiable = resultat_horloge.derniere_date_fiable.astimezone(timezone.utc).date()
    if aujourdhui_fiable > date_expiration:
        _amorcer_tolerance_si_nouveau_cycle(payload["id_jeton"])
        return DecisionLicence(ETAT_EXPIRE, payload["id_jeton"], "Licence expirée (dernière info locale fiable).")

    return DecisionLicence(ETAT_NORMAL, payload["id_jeton"], "Licence valide (hors ligne, dernière info fiable).")


@dataclass
class InformationsLicenceAffichage:
    """Instantané PUREMENT LOCAL des informations de licence v3, à
    l'usage exclusif de la fenêtre « Licence Senaco » (main.py) —
    construit par obtenir_informations_affichage() ci-dessous, qui ne
    déclenche JAMAIS de vérification réseau. `disponible=False` quand
    aucun jeton local lisible n'existe (jamais de valeur inventée dans
    ce cas, voir obtenir_informations_affichage)."""
    disponible: bool
    type: Optional[str] = None
    etat: Optional[str] = None
    date_debut: Optional[str] = None
    date_expiration: Optional[str] = None
    a_vie: bool = False
    tolerance_applicable: bool = False
    tolerance_utilisees: int = 0
    tolerance_max: int = session_tolerance.TOLERANCE_MAX_DATES
    derniere_confirmation_en_ligne: Optional[datetime] = None
    derniere_info_locale: Optional[datetime] = None


DATE_EXPIRATION_A_VIE = "9999-12-31"  # même sentinelle que côté serveur (sql/005_licences_offertes.sql)


def obtenir_informations_affichage(decision):
    """Rassemble, SANS AUCUN appel réseau, tout ce qui peut être
    affiché dans la fenêtre « Licence Senaco » : lit le jeton local
    déjà signé, l'état de tolérance local, et l'horloge de confiance
    locale — rien d'autre. `decision` doit être la DecisionLicence
    DÉJÀ calculée par un appel antérieur à evaluer_licence() (en
    pratique, App._decision_licence_v3 dans main.py) : cette fonction
    ne la recalcule JAMAIS elle-même, pour ne jamais déclencher de
    nouvelle vérification en ligne au seul motif d'ouvrir cette
    fenêtre. `decision` peut être None (ex. licence jamais activée) —
    traité sans erreur.

    N'invente jamais une information absente : si le jeton local est
    illisible ou absent, `disponible=False` est renvoyé et AUCUN autre
    champ n'est à considérer comme fiable."""
    etat_local = _charger_etat_local()
    if etat_local is None:
        return InformationsLicenceAffichage(disponible=False)
    try:
        cle_publique = _cle_publique_hex()
        payload = license_token.extraire_licence(etat_local["jeton"], cle_publique)
    except (RuntimeError, license_token.JetonInvalide):
        return InformationsLicenceAffichage(disponible=False)

    id_jeton = payload.get("id_jeton")
    date_expiration = payload.get("date_expiration")
    etat = decision.etat if decision is not None else None

    tolerance_applicable = etat == ETAT_EXPIRE
    tolerance_utilisees = 0
    if tolerance_applicable:
        etat_tolerance = session_tolerance.charger_etat()
        # Ne compte que si l'état de tolérance local correspond bien au
        # CYCLE de licence actuel — jamais un reliquat d'un cycle
        # antérieur (même principe que _amorcer_tolerance_si_nouveau_
        # cycle : un cycle différent compte pour zéro, jamais une
        # valeur périmée affichée comme actuelle).
        if etat_tolerance is not None and etat_tolerance[0] == id_jeton:
            tolerance_utilisees = len(etat_tolerance[1])

    derniere_confirmation_en_ligne = None
    derniere_info_locale = None
    etat_horloge = trusted_clock.charger_etat()
    if etat_horloge is not None:
        derniere_info_locale, derniere_confirmation_en_ligne = etat_horloge

    return InformationsLicenceAffichage(
        disponible=True,
        type=payload.get("type"),
        etat=etat,
        date_debut=payload.get("date_debut"),
        date_expiration=date_expiration,
        a_vie=(date_expiration == DATE_EXPIRATION_A_VIE),
        tolerance_applicable=tolerance_applicable,
        tolerance_utilisees=tolerance_utilisees,
        tolerance_max=session_tolerance.TOLERANCE_MAX_DATES,
        derniere_confirmation_en_ligne=derniere_confirmation_en_ligne,
        derniere_info_locale=derniere_info_locale,
    )
