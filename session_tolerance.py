# -*- coding: utf-8 -*-
"""Lot 3 du système de licences Senaco v3 — tolérance de 2 dates de
session distinctes après expiration d'une licence, avant blocage.

Totalement isolé pour l'instant : aucun autre fichier de Senaco
n'importe ce module (comme les Lots 1 et 2 — voir
tests/test_session_tolerance.py, qui le vérifie structurellement).

Principe
--------
L'unité comptée est la DATE DE SESSION (`tournament_date`, déjà fixée
une fois pour toutes à la création d'un tournoi OU d'un Sit & Go —
voir database.py:get_tournament_date/_read_tournament_date_ro,
identique pour les deux : `is_sng` est un réglage totalement
indépendant, qui n'influence jamais tournament_date). Plusieurs
tournois/Sit & Go partageant la même date ne consomment donc qu'UNE
tolérance ; une session qui se prolonge après minuit garde sa date
d'origine (fixée à la création, jamais recalculée depuis "aujourd'hui")
— ces deux propriétés sont héritées du mécanisme existant, pas
réimplémentées ici.

Ce module ne décide JAMAIS lui-même si une licence est expirée — ce
booléen est fourni par l'appelant (`licence_expiree`), exactement comme
`maintenant` est injectable dans trusted_clock.evaluer_horloge(). Ça
garde ce module isolé et testable sans aucune dépendance au reste du
futur système de licences.

Protection anti-réinitialisation (sections 9 et 10 du cahier des
charges) : même principe que trusted_clock.py — ce module ne CRÉE
jamais de lui-même un état "0 date consommée" à partir de rien.
`amorcer_ou_reinitialiser_tolerance(cycle_id)` est le SEUL point
d'entrée qui peut (re)créer un état vide pour un `cycle_id` donné —
réservé à un futur lot d'intégration, à appeler uniquement : (a) la
toute première fois qu'une licence donnée passe à l'état expiré, ou
(b) juste après un renouvellement confirmé authentiquement par OVH
(jamais sur une simple absence de fichier constatée localement — voir
evaluer_ouverture, qui renvoie ETAT_INDETERMINE dans ce cas, jamais un
état vierge).

Limite honnête (à ne jamais masquer) : tant qu'aucune synchronisation
avec le serveur OVH n'existe (lot ultérieur), rien n'empêche
localement un utilisateur déterminé d'éditer directement le fichier
JSON d'état pour retirer une date déjà comptée — ce module détecte la
CORRUPTION/l'absence de données, mais ne peut pas, à lui seul, détecter
une édition manuelle cohérente. La protection réelle contre ce cas
précis viendra de la synchronisation serveur (point 8 du cahier des
charges), pas d'un mécanisme local."""
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

from database import _read_tournament_date_ro

TOLERANCE_STATE_FILE = os.path.join(
    os.path.expanduser("~"), ".poker_tournament", "tolerance_sessions.json",
)

TOLERANCE_MAX_DATES = 2  # nombre de dates de session distinctes tolérées après expiration

ETAT_AUTORISE_NORMAL = "autorise_normal"                  # licence valide : rien à faire
ETAT_AUTORISE_AVEC_AVERTISSEMENT = "autorise_avec_avertissement"  # 1re ou 2e date, message rouge
ETAT_BLOQUE = "bloque"                                    # 3e date distincte
ETAT_INDETERMINE = "indetermine"                          # état local absent/corrompu/incohérent
ETAT_ANOMALIE_LECTURE = "anomalie_lecture"                # date de session illisible (jamais bloquant)


@dataclass
class ResultatTolerance:
    etat: str
    date_session: Optional[str]            # AAAA-MM-JJ, ou None si illisible
    dates_autorisees: List[str] = field(default_factory=list)  # état APRÈS décision
    message: str = ""


# ---------------------------------------------------------------------
# Verrou de fichier natif de l'OS — même mécanisme que open_windows.
# _file_lock (fcntl.flock / msvcrt.locking, bibliothèque standard
# uniquement), dupliqué ici pour garder ce module isolé de
# open_windows.py tant que l'intégration réelle n'est pas décidée (même
# choix déjà fait pour _ecrire_atomique dans trusted_clock.py).
# ---------------------------------------------------------------------
def _acquire_lock(fd, timeout):
    deadline = time.monotonic() + timeout
    if sys.platform == "win32":
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("verrou tolerance_sessions indisponible (timeout)")
                time.sleep(0.02)
    else:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("verrou tolerance_sessions indisponible (timeout)")
                time.sleep(0.02)


def _release_lock(fd):
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _lock_path(chemin_etat):
    return chemin_etat + ".lock"


@contextlib.contextmanager
def _verrou(chemin_etat, timeout=5.0):
    chemin_verrou = _lock_path(chemin_etat)
    os.makedirs(os.path.dirname(chemin_verrou), exist_ok=True)
    fd = os.open(chemin_verrou, os.O_CREAT | os.O_RDWR)
    try:
        _acquire_lock(fd, timeout)
        try:
            yield
        finally:
            _release_lock(fd)
    finally:
        os.close(fd)


def _ecrire_atomique(chemin, donnees):
    dossier = os.path.dirname(chemin)
    os.makedirs(dossier, exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)
    os.replace(tmp, chemin)


def charger_etat(chemin_etat=None):
    """Renvoie (cycle_id, dates_autorisees) si le fichier est présent et
    cohérent, sinon None. Ne lève jamais d'exception."""
    chemin_etat = chemin_etat or TOLERANCE_STATE_FILE
    try:
        with open(chemin_etat, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None
    cycle_id = data.get("cycle_id")
    dates_autorisees = data.get("dates_autorisees")
    if not isinstance(cycle_id, str) or not cycle_id:
        return None
    if not isinstance(dates_autorisees, list) or not all(
        isinstance(d, str) for d in dates_autorisees
    ):
        return None
    return cycle_id, dates_autorisees


def amorcer_ou_reinitialiser_tolerance(cycle_id, chemin_etat=None):
    """RÉSERVÉ à un futur lot d'intégration : (re)crée un état vide
    (aucune date encore consommée) pour `cycle_id`. À appeler UNIQUEMENT
    (a) à la toute première expiration réelle d'une licence, détectée
    via le vrai mécanisme de licence (Lots 1/5), ou (b) juste après un
    renouvellement confirmé authentiquement par OVH — jamais à partir
    d'une simple absence de fichier constatée localement (voir
    evaluer_ouverture, qui ne fait jamais ça elle-même)."""
    if not cycle_id:
        raise ValueError("cycle_id ne peut pas être vide.")
    with _verrou(chemin_etat or TOLERANCE_STATE_FILE):
        _ecrire_atomique(
            chemin_etat or TOLERANCE_STATE_FILE,
            {"cycle_id": cycle_id, "dates_autorisees": []},
        )


def reconcilier_depuis_serveur(cycle_id, dates_serveur, chemin_etat=None):
    """RÉSERVÉ à license_client.py (vérification en ligne réussie) :
    remplace l'état local par les dates que le SERVEUR rapporte comme
    déjà comptées pour ce cycle — une RECONSTRUCTION complète depuis la
    source d'autorité, jamais une fusion locale. Protection explicite
    contre la suppression du fichier local de tolérance (demande du
    cahier des charges, section sécurité) : le serveur conserve déjà
    l'historique réel des dates (sql/001_schema_senaco.sql,
    sessions_tolerance) — une suppression locale ne lui fait jamais
    rien "oublier", et une synchronisation réussie restaure donc
    toujours l'état réel, jamais un compteur remis à zéro.

    `dates_serveur` : liste de dates (AAAA-MM-JJ) telle que renvoyée
    par verifier.php (dates_tolerance.connues) — filtrée et tronquée
    ici à TOLERANCE_MAX_DATES par précaution (jamais une confiance
    aveugle dans la forme exacte de la réponse réseau), bien que le
    serveur applique déjà cette même limite de son côté (Lot 4,
    objectif 8)."""
    if not cycle_id:
        raise ValueError("cycle_id ne peut pas être vide.")
    dates_valides = sorted({d for d in dates_serveur if isinstance(d, str)})[:TOLERANCE_MAX_DATES]
    with _verrou(chemin_etat or TOLERANCE_STATE_FILE):
        _ecrire_atomique(
            chemin_etat or TOLERANCE_STATE_FILE,
            {"cycle_id": cycle_id, "dates_autorisees": dates_valides},
        )


def evaluer_ouverture(chemin_tournoi, licence_expiree, cycle_id,
                       lire_date_fn=None, chemin_etat=None):
    """Évalue l'ouverture du fichier `chemin_tournoi` (tournoi OU Sit &
    Go, indifféremment). `licence_expiree`/`cycle_id` proviennent de
    l'appelant (futur lot de licence) — jamais déterminés ici.

    Licence valide (`licence_expiree=False`) : ETAT_AUTORISE_NORMAL
    immédiat, sans toucher au fichier d'état ni lire la date de session
    (inutile : la tolérance ne s'applique qu'après expiration).

    Licence expirée : lit la date de session, puis sous verrou de
    fichier (atomique vis-à-vis d'autres process Senaco concurrents) :
    - date déjà connue pour ce cycle -> ETAT_AUTORISE_AVEC_AVERTISSEMENT,
      sans consommer de nouvelle tolérance (réouverture gratuite).
    - date nouvelle et moins de TOLERANCE_MAX_DATES déjà enregistrées ->
      ajoutée, ETAT_AUTORISE_AVEC_AVERTISSEMENT.
    - date nouvelle et tolérance déjà épuisée -> ETAT_BLOQUE, jamais
      ajoutée (un blocage ne consomme rien, pour ne pas pénaliser une
      nouvelle tentative après renouvellement).
    - état local absent/corrompu, ou cycle_id ne correspond pas à celui
      stocké -> ETAT_INDETERMINE, JAMAIS traité comme "0 date
      consommée" (voir amorcer_ou_reinitialiser_tolerance) ; permissif
      pour CET appel (jamais de blocage sur une simple incertitude
      locale — cohérent avec "ne jamais interrompre un tournoi" et "ne
      pas pénaliser un administrateur" déjà retenus pour les Lots
      précédents), mais rien n'est compté ni persisté tant que l'état
      n'est pas fiable.

    Ne lève jamais d'exception."""
    if not licence_expiree:
        return ResultatTolerance(
            etat=ETAT_AUTORISE_NORMAL, date_session=None,
            message="Licence valide, tolérance non applicable.",
        )

    date_session = (lire_date_fn or _read_tournament_date_ro)(chemin_tournoi)
    if not date_session:
        return ResultatTolerance(
            etat=ETAT_ANOMALIE_LECTURE, date_session=None,
            message=(
                "Date de session illisible pour ce fichier — ouverture "
                "autorisée par prudence, jamais bloquée sur une anomalie "
                "de lecture locale."
            ),
        )

    chemin_etat = chemin_etat or TOLERANCE_STATE_FILE
    with _verrou(chemin_etat):
        etat_charge = charger_etat(chemin_etat)
        if etat_charge is None or etat_charge[0] != cycle_id:
            return ResultatTolerance(
                etat=ETAT_INDETERMINE, date_session=date_session,
                message=(
                    "État local de tolérance absent, corrompu, ou associé "
                    "à un autre cycle de licence — une vérification en "
                    "ligne sera nécessaire pour le reconstituer ; ouverture "
                    "autorisée par prudence dans l'intervalle."
                ),
            )

        _, dates_autorisees = etat_charge
        if date_session in dates_autorisees:
            return ResultatTolerance(
                etat=ETAT_AUTORISE_AVEC_AVERTISSEMENT, date_session=date_session,
                dates_autorisees=list(dates_autorisees),
                message=(
                    "Licence expirée — date de session déjà tolérée, "
                    "aucune nouvelle autorisation consommée."
                ),
            )

        if len(dates_autorisees) >= TOLERANCE_MAX_DATES:
            return ResultatTolerance(
                etat=ETAT_BLOQUE, date_session=date_session,
                dates_autorisees=list(dates_autorisees),
                message=(
                    "Licence expirée — tolérance de "
                    f"{TOLERANCE_MAX_DATES} dates de session déjà "
                    "atteinte. Renouvellement requis pour continuer."
                ),
            )

        nouvelles_dates = dates_autorisees + [date_session]
        _ecrire_atomique(chemin_etat, {"cycle_id": cycle_id, "dates_autorisees": nouvelles_dates})
        return ResultatTolerance(
            etat=ETAT_AUTORISE_AVEC_AVERTISSEMENT, date_session=date_session,
            dates_autorisees=nouvelles_dates,
            message=(
                "Licence expirée — nouvelle date de session tolérée "
                f"({len(nouvelles_dates)}/{TOLERANCE_MAX_DATES})."
            ),
        )


def dates_a_synchroniser(chemin_etat=None):
    """Renvoie la liste des dates de session actuellement enregistrées
    pour le cycle en cours — ce qu'un futur lot devra transmettre au
    serveur OVH (point 8 du cahier des charges) lors de la prochaine
    connexion réussie. Renvoie une liste vide si l'état est absent ou
    incohérent (rien de fiable à synchroniser)."""
    etat = charger_etat(chemin_etat)
    if etat is None:
        return []
    return list(etat[1])
