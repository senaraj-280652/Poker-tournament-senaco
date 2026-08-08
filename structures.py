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


def _round_to_chip(value):
    """Arrondit à une dénomination de jeton plausible (5 en dessous de
    100, 25 au-delà), pour éviter des montants de blindes farfelus."""
    if value <= 0:
        return 0
    step = 5 if value < 100 else 25
    rounded = int(round(value / step)) * step
    return max(rounded, step)


def generate_blind_structure(start_small_blind=25, start_big_blind=50,
                             ante_start_level=4, start_ante=25,
                             duration_minutes=15, break_duration_minutes=None,
                             break_every=4):
    """Génère une structure de blindes complète à partir du small/big blind
    du niveau 1, du niveau à partir duquel les antes s'appliquent et de la
    valeur de l'ante à ce niveau-là.

    - Le niveau 1 utilise exactement les valeurs fournies.
    - Les niveaux suivants reprennent la même progression relative que la
      structure standard, mise à l'échelle du big blind de départ.
    - `ante_start_level` désigne le niveau tel qu'affiché dans le tableau
      (les pauses comptent comme un niveau, puisqu'elles y occupent une
      ligne) : avant ce niveau, l'ante est nulle ; à partir de ce niveau
      (inclus), elle démarre à `start_ante` puis grandit proportionnellement
      au big blind.
    - `break_duration_minutes` fixe la durée des pauses, indépendamment de
      `duration_minutes` (durée des niveaux de blindes). Par défaut, égale
      à `duration_minutes` si non précisée.
    """
    start_small_blind = max(1, int(start_small_blind))
    start_big_blind = max(start_small_blind + 1, int(start_big_blind))
    ante_start_level = max(1, int(ante_start_level))
    start_ante = max(0, int(start_ante))
    if break_duration_minutes is None:
        break_duration_minutes = duration_minutes
    scale = start_big_blind / _REFERENCE_BB_LEVEL1

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
        rows.append({
            "small_blind": sb, "big_blind": bb, "ante": 0,
            "duration_minutes": duration_minutes, "is_break": False,
        })
        if break_every and i % break_every == 0:
            rows.append({
                "small_blind": sb, "big_blind": bb, "ante": 0,
                "duration_minutes": break_duration_minutes, "is_break": True,
                "break_label": "Pause",
            })

    # 2) Ante : nulle avant ante_start_level ; à partir de ce niveau, elle
    #    démarre à start_ante puis suit la progression du big blind.
    anchor_bb = None
    for idx, row in enumerate(rows, start=1):
        if idx < ante_start_level:
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
