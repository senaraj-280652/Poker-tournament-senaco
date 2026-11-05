# -*- coding: utf-8 -*-
"""Lot 2 du système de licences Senaco v3 — détection de manipulation
de l'horloge système (Windows/macOS), sans jamais interrompre quoi que
ce soit : ce module ne fait QUE détecter et exposer un état, aucune
décision de blocage n'est prise ici (ni dans ce lot, ni par ce
fichier — voir App._open_lobby_window et consorts pour le style déjà
établi : la détection et la décision métier restent toujours séparées).

Totalement isolé pour l'instant : aucun autre fichier de Senaco
n'importe ce module (Lot 2, comme le Lot 1, n'est pas encore branché à
l'application — voir tests/test_trusted_clock.py, qui le vérifie
structurellement).

Principe
--------
L'horloge système locale n'est JAMAIS la seule source de vérité (ni
pour la future vérification de licence, ni ici) : ce module se limite
à repérer un INDICE de manipulation en comparant l'horloge actuelle à
la dernière date que CE poste a lui-même déjà constatée comme
avancée — jamais à affirmer qu'une date est "la bonne". Un petit écart
négatif (SEUIL_RECUL_SUSPECT) est toléré en silence (dérive NTP
mineure, imprécision normale) ; un écart plus important déclenche
l'état "recul_suspect", qui devra se résoudre par une vérification
auprès du serveur OVH (lot ultérieur, non implémenté ici — champ
`derniere_date_serveur_confirmee` réservé à cet usage, jamais rempli
par ce module pour l'instant).

Toutes les comparaisons se font en UTC (datetime "aware", jamais
naïf) : un changement de fuseau horaire ou un passage heure été/hiver
ne modifie jamais l'instant UTC lui-même, seulement sa représentation
locale — ce choix élimine structurellement les faux positifs liés à
ces deux cas, sans logique spéciale à écrire pour eux (voir
tests/test_trusted_clock.py pour la preuve par le test).

Protection contre la réinitialisation de l'essai gratuit (section 4 du
cahier des charges) : ce module ne CRÉE jamais de lui-même un nouvel
état "propre" à partir de rien. Si le fichier d'état est absent,
corrompu ou incohérent, evaluer_horloge() renvoie systématiquement
"indetermine" — jamais "ok" — pour qu'un appelant futur sache qu'une
vérification en ligne est nécessaire avant de continuer (voir la
matrice Internet x validité du cahier des charges). Seule
initialiser_apres_verification_en_ligne() peut (re)créer un état
propre, et son nom documente qu'elle ne doit être appelée qu'après un
VRAI succès de vérification en ligne (lot ultérieur) — jamais à partir
d'une simple absence de fichier constatée localement."""
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

TRUSTED_CLOCK_FILE = os.path.join(
    os.path.expanduser("~"), ".poker_tournament", "horloge_fiable.json",
)

# Écart négatif (horloge actuelle avant la dernière date fiable connue)
# toléré SANS déclencher "recul_suspect" — couvre les petites dérives
# normales (ressynchronisation NTP, imprécision d'horloge matérielle),
# jamais un vrai retour en arrière délibéré. Valeur ajustable sans
# casser le format de fichier.
SEUIL_RECUL_SUSPECT = timedelta(hours=2)

ETAT_OK = "ok"
ETAT_RECUL_SUSPECT = "recul_suspect"
ETAT_INDETERMINE = "indetermine"


@dataclass
class ResultatHorloge:
    etat: str  # ETAT_OK | ETAT_RECUL_SUSPECT | ETAT_INDETERMINE
    derniere_date_fiable: Optional[datetime]  # toujours UTC aware, ou None si indéterminé
    ecart: Optional[timedelta]  # derniere_date_fiable - maintenant ; None si indéterminé
    message: str


def _maintenant_utc():
    return datetime.now(timezone.utc)


def _parser_iso_utc(texte):
    """Parse une date ISO 8601 en datetime UTC aware. Lève ValueError
    si `texte` n'est pas exploitable — jamais rattrapée ici, c'est à
    l'appelant (charger_etat) de traiter ça comme une incohérence."""
    dt = datetime.fromisoformat(texte)
    if dt.tzinfo is None:
        raise ValueError(f"date sans fuseau horaire explicite : {texte!r}")
    return dt.astimezone(timezone.utc)


def _formater_iso_utc(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _ecrire_atomique(chemin, donnees):
    """Même principe qu'open_windows._atomic_write_json (écriture dans
    un fichier temporaire puis os.replace) — dupliqué ici volontairement
    plutôt qu'importé : ce module reste isolé de open_windows.py tant
    que son intégration réelle n'a pas été décidée (lot ultérieur)."""
    dossier = os.path.dirname(chemin)
    os.makedirs(dossier, exist_ok=True)
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f, ensure_ascii=False, indent=2)
    os.replace(tmp, chemin)


def charger_etat(chemin=None):
    """Renvoie (derniere_date_fiable, derniere_date_serveur_confirmee)
    — deux datetime UTC aware, le second pouvant être None — ou None
    (tout court) si le fichier est absent, illisible, ou si son
    contenu est incohérent (champ manquant, type incorrect, date
    invalide). Ne lève JAMAIS d'exception : toute anomalie se traduit
    par un simple None, jamais par une erreur qui remonterait jusqu'à
    l'appelant (cohérent avec "ne jamais interrompre un tournoi")."""
    chemin = chemin or TRUSTED_CLOCK_FILE
    try:
        with open(chemin, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    brut_fiable = data.get("derniere_date_fiable")
    if not isinstance(brut_fiable, str):
        return None
    try:
        derniere_date_fiable = _parser_iso_utc(brut_fiable)
    except ValueError:
        return None

    derniere_date_serveur = None
    brut_serveur = data.get("derniere_date_serveur_confirmee")
    if brut_serveur is not None:
        if not isinstance(brut_serveur, str):
            return None
        try:
            derniere_date_serveur = _parser_iso_utc(brut_serveur)
        except ValueError:
            return None

    return derniere_date_fiable, derniere_date_serveur


def _enregistrer(derniere_date_fiable, derniere_date_serveur_confirmee, chemin=None):
    chemin = chemin or TRUSTED_CLOCK_FILE
    donnees = {"derniere_date_fiable": _formater_iso_utc(derniere_date_fiable)}
    if derniere_date_serveur_confirmee is not None:
        donnees["derniere_date_serveur_confirmee"] = _formater_iso_utc(
            derniere_date_serveur_confirmee
        )
    try:
        _ecrire_atomique(chemin, donnees)
    except OSError:
        pass  # jamais bloquant : une écriture ratée n'empêche pas de continuer


def initialiser_apres_verification_en_ligne(date_serveur_utc, chemin=None):
    """RÉSERVÉ à l'appel juste après un VRAI succès de vérification
    auprès du serveur OVH (lot ultérieur, non implémenté ici — cette
    fonction ne fait qu'exposer le point d'entrée, elle n'est appelée
    par aucun code pour l'instant). `date_serveur_utc` doit être la
    date officielle transmise par le serveur, jamais une date déduite
    localement — c'est ce qui distingue cette fonction d'une simple
    "remise à zéro" locale, qui ne doit jamais être possible (voir
    l'en-tête de ce fichier, section anti-réinitialisation)."""
    if date_serveur_utc.tzinfo is None:
        raise ValueError("date_serveur_utc doit être timezone-aware (UTC).")
    _enregistrer(date_serveur_utc, date_serveur_utc, chemin=chemin)


def evaluer_horloge(chemin=None, maintenant=None):
    """Compare l'horloge actuelle à la dernière date fiable connue et
    renvoie un ResultatHorloge. `maintenant` est injectable (tests
    uniquement) ; en usage réel, toujours l'horloge système actuelle en
    UTC. Ne lève jamais d'exception.

    - Fichier absent/corrompu/incohérent -> ETAT_INDETERMINE (jamais
      ETAT_OK : voir l'en-tête de ce fichier, aucune réinitialisation
      silencieuse possible par ce chemin).
    - Écart négatif au-delà de SEUIL_RECUL_SUSPECT -> ETAT_RECUL_SUSPECT,
      la date fiable stockée n'est JAMAIS avancée dans ce cas (sinon on
      accepterait tacitement le recul à la prochaine vérification).
    - Sinon -> ETAT_OK, et la date fiable stockée est avancée à
      max(ancienne valeur, maintenant) — persistée immédiatement."""
    maintenant = maintenant or _maintenant_utc()
    chemin = chemin or TRUSTED_CLOCK_FILE

    etat_charge = charger_etat(chemin)
    if etat_charge is None:
        return ResultatHorloge(
            etat=ETAT_INDETERMINE,
            derniere_date_fiable=None,
            ecart=None,
            message=(
                "Aucune donnée d'horloge fiable exploitable localement "
                "(absente, corrompue, ou jamais initialisée) — une "
                "vérification en ligne est nécessaire."
            ),
        )

    derniere_date_fiable, derniere_date_serveur = etat_charge
    ecart = derniere_date_fiable - maintenant  # positif si recul

    if ecart > SEUIL_RECUL_SUSPECT:
        return ResultatHorloge(
            etat=ETAT_RECUL_SUSPECT,
            derniere_date_fiable=derniere_date_fiable,
            ecart=ecart,
            message=(
                "L'horloge de cet ordinateur semble être revenue en "
                f"arrière de {ecart} par rapport à la dernière date "
                "connue comme fiable — une vérification en ligne est "
                "nécessaire pour confirmer la date réelle."
            ),
        )

    nouvelle_date_fiable = max(derniere_date_fiable, maintenant)
    _enregistrer(nouvelle_date_fiable, derniere_date_serveur, chemin=chemin)
    return ResultatHorloge(
        etat=ETAT_OK,
        derniere_date_fiable=nouvelle_date_fiable,
        ecart=ecart,
        message="Horloge cohérente avec l'historique local.",
    )
