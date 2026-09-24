# -*- coding: utf-8 -*-
"""Générateurs de structures par défaut : blindes et grille de gains."""

# Progression de référence (paliers relatifs), utilisée comme base pour
# générer une structure à l'échelle du small blind / big blind de départ
# choisis par l'utilisateur. Le big blind du niveau 1 de cette référence
# est 50 ; on calcule un facteur d'échelle par rapport à ce repère.
_REFERENCE_STEPS = [
    (25, 50), (50, 100), (75, 150), (100, 200), (150, 300),
    (200, 400), (300, 600), (400, 800), (500, 1000), (600, 1200),
    (800, 1600), (1000, 2000), (1500, 3000), (2000, 4000), (3000, 6000),
    (4000, 8000), (5000, 10000), (6000, 12000), (8000, 16000), (10000, 20000),
]
_REFERENCE_BB_LEVEL1 = _REFERENCE_STEPS[0][1]  # 50
_DEFAULT_ANTE_RATIO = 0.125  # ante ≈ 12,5 % du big blind (repère par défaut)

# Nombre de rounds DE JEU réellement produits par generate_blind_structure
# (jamais plus, jamais moins — une ligne de _REFERENCE_STEPS = un round de
# jeu, les pauses s'ajoutent en plus sans en faire partie). Exposé
# publiquement (chantier "Paramètres > Structure des blindes", 2026-09-24)
# pour que main.py puisse valider qu'une pause "Après Round" désigne bien
# un round qui existera réellement, sans dépendre de la variable privée
# _REFERENCE_STEPS.
GENERATED_ROUNDS_COUNT = len(_REFERENCE_STEPS)


def _round_to_chip(value):
    """Arrondit à une dénomination de jeton plausible (5 en dessous de
    100, 25 au-delà), pour éviter des montants de blindes farfelus."""
    if value <= 0:
        return 0
    step = 5 if value < 100 else 25
    rounded = int(round(value / step)) * step
    return max(rounded, step)


def _duration_for_round(i, schedule, fallback_duration):
    """Durée (minutes) du round de jeu `i` (1-indexé, jamais les pauses —
    voir la boucle principale) selon `schedule` — liste de (durée,
    nb_rounds_ou_None), chantier "Paramètres > Structure des blindes"
    (2026-09-24). `count=None` s'applique IMMÉDIATEMENT et pour TOUJOURS
    (les paliers suivants du barème, s'il y en a, ne sont alors jamais
    atteints) — c'est exactement la règle demandée : une ligne 1 sans
    "Nb Rounds" s'applique à tout le tournoi, une ligne 2 sans "Nb
    Rounds" s'applique à tout le reste. Si `i` dépasse la somme des
    compteurs chiffrés SANS qu'aucune ligne ouverte (count=None) n'ait
    été rencontrée (décision explicite du 2026-09-24) : prolonge la
    DERNIÈRE durée du barème pour tous les rounds restants, plutôt que
    de lever une erreur ou de retomber sur `fallback_duration`."""
    if not schedule:
        return fallback_duration
    cumulative = 0
    for duration, count in schedule:
        if count is None:
            return duration
        cumulative += count
        if i <= cumulative:
            return duration
    return schedule[-1][0]


def generate_blind_structure(start_small_blind=25, start_big_blind=50,
                             ante_start_level=4, start_ante=25,
                             duration_minutes=15, break_duration_minutes=None,
                             break_every=4,
                             duration_schedule=None, break_schedule=None):
    """Génère une structure de blindes complète à partir du small/big blind
    du niveau 1, du niveau à partir duquel les antes s'appliquent et de la
    valeur de l'ante à ce niveau-là.

    - Le niveau 1 utilise exactement les valeurs fournies.
    - Les niveaux suivants reprennent la même progression relative que la
      structure standard, mise à l'échelle du big blind de départ.
    - `ante_start_level` désigne le niveau de blindes (les pauses ne
      comptent pas, seuls les niveaux avec petite/grosse blinde sont
      numérotés) : avant ce niveau, l'ante est nulle ; à partir de ce
      niveau (inclus), elle démarre à `start_ante` puis grandit
      proportionnellement au big blind. Les pauses gardent toujours une
      ante à 0.
    - `break_duration_minutes` fixe la durée des pauses, indépendamment de
      `duration_minutes` (durée des niveaux de blindes). Par défaut, égale
      à `duration_minutes` si non précisée.

    `duration_schedule`/`break_schedule` (chantier "Paramètres > Structure
    des blindes — durées variables + 2 pauses programmables", 2026-09-24)
    — OPTIONNELS, `None` par défaut : dans ce cas, comportement
    STRICTEMENT INCHANGÉ (`duration_minutes` flat + pause toutes les
    `break_every` niveaux, exactement comme avant ce chantier — voir
    default_blind_structure/blind_templates.py, jamais impactés).

    - `duration_schedule` : liste de `(durée_minutes, nb_rounds_ou_None)`
      — voir _duration_for_round ci-dessus pour la règle exacte
      (count=None = "jusqu'à la fin", barème épuisé = prolonge la
      dernière durée).
    - `break_schedule` : liste de `(durée_pause_minutes, après_round)` —
      `après_round` désigne le numéro du ROUND DE JEU (jamais une pause)
      après lequel insérer cette pause ; remplace entièrement `break_every`
      quand fourni (une pause précise par ligne, plus une pause
      automatique toutes les N rounds)."""
    start_small_blind = max(1, int(start_small_blind))
    start_big_blind = max(start_small_blind + 1, int(start_big_blind))
    ante_start_level = max(1, int(ante_start_level))
    start_ante = max(0, int(start_ante))
    if break_duration_minutes is None:
        break_duration_minutes = duration_minutes
    scale = start_big_blind / _REFERENCE_BB_LEVEL1

    # break_map : {après_round: durée_pause} — construit une seule fois,
    # jamais recalculé dans la boucle. Une entrée invalide/incomplète ne
    # doit jamais arriver ici (validée en amont par l'appelant, voir
    # App._generate_custom_blind_structure) mais reste ignorée sans
    # lever si elle survenait malgré tout (défensif, jamais bloquant).
    break_map = {}
    if break_schedule:
        for minutes, after_round in break_schedule:
            if minutes and after_round:
                break_map[int(after_round)] = int(minutes)

    # 1) Squelette des niveaux (blindes + pauses), sans ante pour l'instant.
    #    La position dans cette liste (1-indexée) correspond exactement au
    #    numéro de niveau affiché dans le tableau de l'application.
    rows = []
    for i, (ref_sb, ref_bb) in enumerate(_REFERENCE_STEPS, start=1):
        if i == 1:
            sb, bb = start_small_blind, start_big_blind
        else:
            bb = _round_to_chip(ref_bb * scale)
            sb = _round_to_chip(ref_sb * scale)
            if sb >= bb:
                sb = max(_round_to_chip(bb / 2), 1)
        this_duration = (
            _duration_for_round(i, duration_schedule, duration_minutes)
            if duration_schedule is not None else duration_minutes
        )
        rows.append({
            "small_blind": sb, "big_blind": bb, "ante": 0,
            "duration_minutes": this_duration, "is_break": False,
        })
        # "is not None" (jamais juste `if break_schedule:`) : une liste
        # VIDE ([], "aucune pause configurée") doit se comporter
        # différemment de `None` ("aucun échéancier fourni du tout,
        # appelant historique") — sinon les deux se confondraient et une
        # régénération avec les 2 lignes de pause délibérément vidées
        # retomberait à tort sur l'ancien break_every=4.
        if break_schedule is not None:
            if i in break_map:
                rows.append({
                    "small_blind": sb, "big_blind": bb, "ante": 0,
                    "duration_minutes": break_map[i], "is_break": True,
                    "break_label": "Pause",
                })
        elif break_every and i % break_every == 0:
            rows.append({
                "small_blind": sb, "big_blind": bb, "ante": 0,
                "duration_minutes": break_duration_minutes, "is_break": True,
                "break_label": "Pause",
            })

    # 2) Ante : nulle avant ante_start_level ; à partir de ce niveau, elle
    #    démarre à start_ante puis suit la progression du big blind.
    #    `ante_start_level` compte uniquement les niveaux de blindes (les
    #    pauses n'en sont pas un et ne reçoivent donc jamais d'ante), pour
    #    correspondre au numéro de "Round" affiché dans l'onglet Blindes.
    anchor_bb = None
    blind_idx = 0
    for row in rows:
        if row["is_break"]:
            continue
        blind_idx += 1
        if blind_idx < ante_start_level:
            continue
        if anchor_bb is None:
            anchor_bb = row["big_blind"] or 1
            row["ante"] = start_ante
        else:
            ratio = start_ante / anchor_bb if anchor_bb else _DEFAULT_ANTE_RATIO
            row["ante"] = _round_to_chip(row["big_blind"] * ratio)

    return rows


def default_blind_structure():
    """Structure de blindes standard (25/50, antes de 25 à partir du
    niveau 4, paliers de 15 minutes avec pauses régulières). Convient à un
    tournoi multi-tables classique."""
    return generate_blind_structure(
        start_small_blind=25, start_big_blind=50, ante_start_level=4,
        start_ante=25, duration_minutes=15, break_duration_minutes=15,
        break_every=4,
    )


def standard_payout_structure(num_entries):
    """Renvoie {place: pourcentage} pour un champ donné, selon une grille
    standard courante en tournoi multi-tables (proportion de payés et
    répartition dégressive)."""
    if num_entries <= 0:
        return {1: 100.0}

    if num_entries <= 9:
        paid = 1
    elif num_entries <= 18:
        paid = 2
    elif num_entries <= 27:
        paid = 3
    elif num_entries <= 45:
        paid = 4
    elif num_entries <= 67:
        paid = 6
    elif num_entries <= 90:
        paid = 8
    elif num_entries <= 130:
        paid = 10
    elif num_entries <= 200:
        paid = 12
    elif num_entries <= 300:
        paid = 15
    else:
        paid = max(15, round(num_entries * 0.10))

    if paid == 1:
        return {1: 100.0}

    # Poids décroissants (géométrique) puis normalisation à 100%
    ratio = 0.62
    weights = [ratio ** i for i in range(paid)]
    total = sum(weights)
    pcts = [w / total * 100.0 for w in weights]

    # Arrondi propre à 1 décimale, en corrigeant l'arrondi sur la 1ère place
    rounded = [round(p, 1) for p in pcts]
    diff = round(100.0 - sum(rounded), 1)
    rounded[0] = round(rounded[0] + diff, 1)

    return {i + 1: rounded[i] for i in range(paid)}
