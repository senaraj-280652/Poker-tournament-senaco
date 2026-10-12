# -*- coding: utf-8 -*-
"""
Répertoire de joueurs habituels, indépendant des tournois eux-mêmes.
Stocké dans le dossier personnel de l'utilisateur (~/.poker_tournament/roster.json)
afin d'être disponible quel que soit l'endroit où l'application est lancée
et quel que soit le fichier .tournoi ouvert.

Chaque entrée est un dict {"name", "club", "group", "phone", "mail",
"pseudo"} (tous les champs sauf "name" peuvent être des chaînes vides si
inconnus/non renseignés). Les anciens fichiers roster.json (simple liste
de chaînes, ou dicts {"name", "club"} d'avant le 2026-09-20) sont migrés
automatiquement à la lecture, sans club/groupe/téléphone/mail/pseudo.

"pseudo" (demande du 2026-10-03, "ajouter la colonne Pseudo au
Répertoire") : surnom éventuel d'une personne du Répertoire, purement
informatif — n'affecte ni les permissions ni aucune autre logique.
Propagé une seule fois vers la table `players` d'un tournoi à l'ajout du
joueur (voir Database.add_player), exactement comme "club" l'est déjà —
jamais une seconde source de vérité, jamais relu en direct depuis le
Répertoire une fois le joueur ajouté à un tournoi.

"group" (demande du 2026-09-20, chantier "Sécurisation du Contrôle à
distance") : identifie qui, parmi les personnes du Répertoire, peut être
ADMIN (accès total au Contrôle à distance, peut accorder/retirer des
droits) ou DIRTO (accès limité aux fonctions explicitement accordées par
un ADMIN, pour un tournoi donné) — voir ROSTER_GROUP_ADMIN/
ROSTER_GROUP_DIRTO ci-dessous. Une personne appartient à UN SEUL groupe
à la fois, JAMAIS les deux simultanément (décision explicite de
l'utilisateur) — la chaîne vide "" signifie "non classé" (comportement
de TOUTE fiche existante avant ce chantier, jamais une erreur). Ce champ
ne donne AUCUN droit par lui-même : il identifie seulement qui PEUT être
choisi comme ADMIN/DIRTO ailleurs dans l'application (chantiers
suivants) — la logique d'autorisation elle-même vit ailleurs (base du
tournoi, registre des appareils), jamais ici."""
import json
import os

# Valeurs valides de "group" (voir la docstring du module). Toute autre
# valeur lue depuis le disque (fichier corrompu, future valeur non
# encore supportée, etc.) est silencieusement ramenée à "" par
# _normalize_group ci-dessous — jamais une exception, même principe
# défensif que le reste de ce module vis-à-vis d'un fichier existant.
ROSTER_GROUP_ADMIN = "ADMIN"
ROSTER_GROUP_DIRTO = "DIRTO"
ROSTER_GROUPS = (ROSTER_GROUP_ADMIN, ROSTER_GROUP_DIRTO)

# Impression PDF du Répertoire (demande du 2026-10-07) : liste ordonnée des
# champs proposés à l'impression, (clé technique, libellé) — reprend
# EXACTEMENT les 6 champs réels de load_roster_entries()/save_roster_
# entries ci-dessous, plus "photo" (stockage séparé, voir player_photos.py,
# jamais une clé de roster.json) ; aucun champ inventé. Ordre = celui déjà
# affiché dans le Treeview du Répertoire (main.py: RosterManagerDialog).
ROSTER_PDF_COLUMNS = [
    ("photo", "Photo"),
    ("name", "Nom"),
    ("club", "Club"),
    ("group", "Groupe"),
    ("phone", "Téléphone"),
    ("mail", "Mail"),
    ("pseudo", "Pseudo"),
]
# Choix par défaut validés explicitement par l'utilisateur (liste de
# contact classique) — Photo/Groupe/Mail/Pseudo restent décochés par
# défaut (plus sensibles ou, pour Photo, plus lourds à produire).
ROSTER_PDF_DEFAULT_COLUMNS = ["name", "club", "phone"]

def _roster_dir():
    home = os.path.expanduser("~")
    d = os.path.join(home, ".poker_tournament")
    os.makedirs(d, exist_ok=True)
    return d

def _roster_path():
    return os.path.join(_roster_dir(), "roster.json")

def _normalize_group(value):
    """"" (non classé) si `value` n'est pas exactement l'une des deux
    valeurs valides — jamais une exception, y compris pour une chaîne
    vide, None, un type inattendu, ou une future valeur non encore
    supportée par cette version de l'application."""
    value = str(value or "").strip().upper()
    return value if value in ROSTER_GROUPS else ""

def load_roster_entries():
    """Liste de dicts {"name", "club", "group", "phone", "mail", "pseudo"},
    triée par nom (insensible à la casse). Migre automatiquement l'ancien
    format (liste de chaînes, ou dicts sans group/phone/mail/pseudo)."""
    path = _roster_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []

    by_name = {}
    for item in data:
        if isinstance(item, str):
            name, club, group, phone, mail, pseudo = item.strip(), "", "", "", "", ""
        elif isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            club = str(item.get("club", "") or "").strip()
            group = _normalize_group(item.get("group", ""))
            phone = str(item.get("phone", "") or "").strip()
            mail = str(item.get("mail", "") or "").strip()
            pseudo = str(item.get("pseudo", "") or "").strip()
        else:
            continue
        if not name:
            continue
        # En cas de doublon dans le fichier, garde la première valeur
        # non vide rencontrée pour chaque champ (même principe déjà en
        # place pour "club" avant ce chantier).
        if name in by_name:
            existing = by_name[name]
            if not existing["club"] and club:
                existing["club"] = club
            if not existing["group"] and group:
                existing["group"] = group
            if not existing["phone"] and phone:
                existing["phone"] = phone
            if not existing["mail"] and mail:
                existing["mail"] = mail
            if not existing["pseudo"] and pseudo:
                existing["pseudo"] = pseudo
        else:
            by_name[name] = {
                "name": name, "club": club, "group": group,
                "phone": phone, "mail": mail, "pseudo": pseudo,
            }
    return sorted(by_name.values(), key=lambda e: e["name"].lower())

def save_roster_entries(entries):
    path = _roster_path()
    by_name = {}
    for e in entries:
        name = str(e.get("name", "")).strip()
        if not name:
            continue
        by_name[name] = {
            "name": name,
            "club": str(e.get("club", "") or "").strip(),
            "group": _normalize_group(e.get("group", "")),
            "phone": str(e.get("phone", "") or "").strip(),
            "mail": str(e.get("mail", "") or "").strip(),
            "pseudo": str(e.get("pseudo", "") or "").strip(),
        }
    cleaned = sorted(by_name.values(), key=lambda e: e["name"].lower())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def load_roster():
    """Liste des noms seuls (triée), pour les usages qui n'ont besoin que
    du nom (sélection de joueurs, etc.)."""
    return [e["name"] for e in load_roster_entries()]

def save_roster(names):
    """Compatibilité : remplace la liste des noms en conservant le club/
    groupe/téléphone/mail déjà connus de chacun (ceux qui disparaissent
    de `names` sont retirés, avec toutes leurs autres informations)."""
    existing = {e["name"]: e for e in load_roster_entries()}
    cleaned = sorted({str(n).strip() for n in names if str(n).strip()}, key=str.lower)
    save_roster_entries([
        existing.get(n, {"name": n, "club": "", "group": "", "phone": "", "mail": "", "pseudo": ""})
        for n in cleaned
    ])

def list_clubs():
    """Clubs connus (non vides), triés, pour proposer un menu déroulant."""
    clubs = {e["club"] for e in load_roster_entries() if e["club"]}
    return sorted(clubs, key=str.lower)

def get_club(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["club"]
    return ""

def set_club(name, club):
    """Définit (ou efface, si club vide) le club d'un joueur du répertoire,
    en le créant si besoin."""
    name = name.strip()
    if not name:
        return
    club = (club or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["club"] = club
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": club, "group": "", "phone": "", "mail": "", "pseudo": ""})
    save_roster_entries(entries)

def get_group(name):
    """"ADMIN", "DIRTO" ou "" (non classé, y compris pour un nom absent
    du Répertoire)."""
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["group"]
    return ""

def set_group(name, group):
    """Définit (ou efface, si `group` invalide/vide) le groupe ADMIN/
    DIRTO d'une personne du répertoire, en la créant si besoin. Une
    valeur invalide (ni "ADMIN" ni "DIRTO") est silencieusement ramenée
    à "" plutôt que de lever une exception — voir _normalize_group."""
    name = name.strip()
    if not name:
        return
    group = _normalize_group(group)
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["group"] = group
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": group, "phone": "", "mail": "", "pseudo": ""})
    save_roster_entries(entries)

def get_phone(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["phone"]
    return ""

def set_phone(name, phone):
    """Définit (ou efface, si phone vide) le téléphone d'une personne du
    répertoire, en la créant si besoin."""
    name = name.strip()
    if not name:
        return
    phone = (phone or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["phone"] = phone
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": "", "phone": phone, "mail": "", "pseudo": ""})
    save_roster_entries(entries)

def get_mail(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["mail"]
    return ""

def set_mail(name, mail):
    """Définit (ou efface, si mail vide) le mail d'une personne du
    répertoire, en la créant si besoin."""
    name = name.strip()
    if not name:
        return
    mail = (mail or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["mail"] = mail
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": "", "phone": "", "mail": mail, "pseudo": ""})
    save_roster_entries(entries)

def get_pseudo(name):
    name = name.strip()
    for e in load_roster_entries():
        if e["name"] == name:
            return e["pseudo"]
    return ""

def set_pseudo(name, pseudo):
    """Définit (ou efface, si pseudo vide) le pseudo d'une personne du
    répertoire, en la créant si besoin."""
    name = name.strip()
    if not name:
        return
    pseudo = (pseudo or "").strip()
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            e["pseudo"] = pseudo
            save_roster_entries(entries)
            return
    entries.append({"name": name, "club": "", "group": "", "phone": "", "mail": "", "pseudo": pseudo})
    save_roster_entries(entries)

def list_by_group(group):
    """Entrées complètes ({"name", "club", "group", "phone", "mail", "pseudo"})
    dont le groupe est exactement `group` ("ADMIN" ou "DIRTO"), triées
    par nom — pour peupler les futurs sélecteurs "AUTORISÉ PAR"/
    "UTILISATEUR AUTORISÉ" du Contrôle à distance (chantiers suivants).
    Liste vide si `group` n'est pas une valeur valide (jamais une
    exception)."""
    group = _normalize_group(group)
    if not group:
        return []
    return [e for e in load_roster_entries() if e["group"] == group]

def add_to_roster(name, club=None):
    """Ajoute `name` au répertoire s'il n'y est pas déjà. Si `club` est
    fourni (non None), le club est enregistré/mis à jour même si le
    joueur existait déjà ; sinon le club existant est conservé tel quel.
    Ne touche jamais group/phone/mail/pseudo (voir set_group/set_phone/
    set_mail/set_pseudo dédiés) — une personne nouvellement créée ici
    démarre donc toujours "non classée", exactement comme une ancienne
    fiche."""
    name = name.strip()
    if not name:
        return
    entries = load_roster_entries()
    for e in entries:
        if e["name"] == name:
            if club is not None and club.strip() and e["club"] != club.strip():
                e["club"] = club.strip()
                save_roster_entries(entries)
            return
    entries.append({"name": name, "club": (club or "").strip(), "group": "", "phone": "", "mail": "", "pseudo": ""})
    save_roster_entries(entries)

def remove_from_roster(name):
    entries = load_roster_entries()
    filtered = [e for e in entries if e["name"] != name]
    if len(filtered) != len(entries):
        save_roster_entries(filtered)

def rename_in_roster(old_name, new_name):
    """Renomme une entrée en conservant son club/groupe/téléphone/mail/
    pseudo. Si `new_name` existe déjà, ses deux entrées fusionnent (pour
    chaque champ, la valeur non vide de l'ancien nom l'emporte, sinon
    celle du nom cible est conservée)."""
    old_name = old_name.strip()
    new_name = new_name.strip()
    if not new_name:
        return
    entries = load_roster_entries()
    old_entry = next((e for e in entries if e["name"] == old_name), None)
    old_entry = old_entry or {"club": "", "group": "", "phone": "", "mail": "", "pseudo": ""}
    entries = [e for e in entries if e["name"] not in (old_name, new_name)]
    existing_new = next((e for e in load_roster_entries() if e["name"] == new_name), None)
    existing_new = existing_new or {"club": "", "group": "", "phone": "", "mail": "", "pseudo": ""}
    merged = {
        "name": new_name,
        "club": old_entry["club"] or existing_new["club"],
        "group": old_entry["group"] or existing_new["group"],
        "phone": old_entry["phone"] or existing_new["phone"],
        "mail": old_entry["mail"] or existing_new["mail"],
        "pseudo": old_entry["pseudo"] or existing_new["pseudo"],
    }
    entries.append(merged)
    save_roster_entries(entries)


# =========================================================================
# Impression PDF du Répertoire (demande du 2026-10-07) — voir ROSTER_PDF_
# COLUMNS/ROSTER_PDF_DEFAULT_COLUMNS plus haut. Purement un export/
# affichage à partir des données déjà en mémoire (`entries`, fournies par
# l'appelant dans l'ordre voulu) : ne modifie JAMAIS roster.json, ni le
# stockage des photos (player_photos.py), ni aucune base de tournoi —
# lecture seule de bout en bout.
# =========================================================================

def export_roster_pdf(path, entries, columns):
    """Génère le PDF du Répertoire à `path`, pour les `entries` fournies
    (déjà dans l'ordre voulu — voir main.py: RosterManagerDialog, qui
    reprend l'ordre actuellement affiché à l'écran) et UNIQUEMENT les
    champs listés dans `columns` (sous-ensemble de clés de ROSTER_PDF_
    COLUMNS). Un champ absent de `columns` n'est jamais lu pour
    construire une ligne/un en-tête — confidentialité par construction,
    jamais par masquage après coup.

    Lève ValueError si `columns` ne contient aucun champ reconnu (garde-
    fou supplémentaire : le bouton "Créer le PDF" refuse déjà ce cas
    avant même d'appeler cette fonction, voir RosterPdfDialog._do_export
    dans main.py). Lève ImportError si fpdf2 n'est pas installé — propagée
    telle quelle, voir App.show_missing_export_module, même convention
    que les autres exports PDF de Senaco."""
    selected = [(key, header) for key, header in ROSTER_PDF_COLUMNS if key in columns]
    if not selected:
        raise ValueError("Aucun champ sélectionné pour l'impression du Répertoire.")
    if any(key == "photo" for key, _ in selected):
        return _export_roster_pdf_with_photos(path, entries, selected)
    return _export_roster_pdf_text_only(path, entries, selected)


def _export_roster_pdf_text_only(path, entries, selected):
    """Aucune colonne Photo cochée : réutilise _write_pdf_table
    (database.py) à l'identique du reste de Senaco pour une présentation
    homogène (repeat_header/page_numbers activés, nouveaux paramètres
    optionnels — voir leur docstring, comportement des autres exports
    strictement inchangé). Pré-assainit les textes avec action_log.
    _pdf_text (gère accents/apostrophes/tirets/points de suspension de
    façon robuste, contrairement à la version plus simple de
    database._pdf_text — qui s'applique ensuite une seconde fois, sans
    effet puisque le texte est déjà sûr à ce stade)."""
    from database import _write_pdf_table
    from action_log import _pdf_text as _safe_pdf_text

    headers = [_safe_pdf_text(header) for _, header in selected]
    rows = [
        [_safe_pdf_text(entry.get(key, "")) for key, _ in selected]
        for entry in entries
    ]
    return _write_pdf_table(
        path, _safe_pdf_text("Répertoire"), [], headers, rows,
        repeat_header=True, page_numbers=True,
    )


# Dimensions fixes (mm) de la colonne/ligne Photo — suffisamment grandes
# pour rester lisibles (demande explicite : "privilégier un résultat
# lisible... plutôt que de chercher à mettre trop de joueurs sur chaque
# page"), sans chercher à les maximiser — environ 9-10 lignes par page en
# A4, marges incluses.
_ROSTER_PDF_PHOTO_COL_WIDTH_MM = 22
_ROSTER_PDF_PHOTO_ROW_HEIGHT_MM = 24
_ROSTER_PDF_HEADER_ROW_HEIGHT_MM = 8
# Hauteur de ligne COMPACTE (demande du 2026-10-07, correction) : utilisée
# UNIQUEMENT pour un joueur sans photo alors que la colonne Photo est
# cochée — même hauteur que le tableau texte seul (_write_pdf_table,
# row_height=7) pour rester cohérente avec "le rendu actuel est déjà
# validé" ailleurs. Un joueur AVEC photo garde toujours
# _ROSTER_PDF_PHOTO_ROW_HEIGHT_MM, intégralement inchangée.
_ROSTER_PDF_COMPACT_ROW_HEIGHT_MM = 7


def _fit_image_box(image_path, max_w, max_h):
    """(largeur, hauteur) en mm de l'image mise à l'échelle pour tenir
    dans un rectangle max_w x max_h SANS jamais dépasser ni déformer —
    proportions conservées (jamais un simple max_w x max_h, qui
    étirerait une photo non carrée). Nécessite Pillow (déjà une
    dépendance du projet — voir player_photos.save_photo_from_image,
    requirements.txt Mac/Windows) ; repli sur le plein cadre si la
    taille réelle ne peut pas être lue (fichier illisible/corrompu)
    plutôt que de faire échouer tout le PDF pour une seule photo."""
    try:
        from PIL import Image
        with Image.open(image_path) as img:
            iw, ih = img.size
    except Exception:
        return max_w, max_h
    if iw <= 0 or ih <= 0:
        return max_w, max_h
    scale = min(max_w / iw, max_h / ih)
    return iw * scale, ih * scale


def _export_roster_pdf_with_photos(path, entries, selected):
    """Variante dédiée (demande explicite du 2026-10-07, confirmée après
    le diagnostic) : _write_pdf_table (database.py) n'est pas conçue pour
    des images — dessin manuel ligne par ligne avec fpdf2 directement,
    mais en reprenant la MÊME présentation (police/couleurs d'en-tête,
    lignes alternées... en l'occurrence header seul ici par simplicité,
    voir plus bas) et le même mécanisme repeat_header/page_numbers que
    _write_pdf_table, via les callbacks header()/footer() de fpdf2, pour
    une présentation homogène.

    Une photo manquante laisse la cellule vide (jamais une erreur) ; une
    photo illisible est ignorée (try/except autour de pdf.image()) plutôt
    que de faire échouer tout le document pour une seule ligne. Les
    proportions de chaque photo sont conservées (voir _fit_image_box),
    jamais étirées — centrée dans sa cellule. Toutes les cellules d'une
    même ligne (photo + texte) partagent la même hauteur fixe : le nom et
    les autres champs restent alignés avec la photo."""
    import player_photos
    from fpdf import FPDF
    from action_log import _pdf_text as _safe_pdf_text
    from database import _pdf_fit_font_size

    text_cols = [(key, header) for key, header in selected if key != "photo"]
    photo_col_width = _ROSTER_PDF_PHOTO_COL_WIDTH_MM
    row_height = _ROSTER_PDF_PHOTO_ROW_HEIGHT_MM
    header_height = _ROSTER_PDF_HEADER_ROW_HEIGHT_MM

    # Même principe que _write_pdf_table (paysage au-delà de 6 colonnes) :
    # la colonne Photo occupe à elle seule une largeur comparable à ~2
    # colonnes de texte, donc comptée comme telle ici plutôt que comme
    # une simple colonne de plus.
    orientation = "L" if len(text_cols) > 4 else "P"

    headers = [_safe_pdf_text(h) for _, h in text_cols]
    photo_header = _safe_pdf_text("Photo")

    class _RosterPhotoPDF(FPDF):
        _text_col_width = 0.0
        _header_font_size = 9

        def header(self):
            # Jamais sur la 1ère page (titre dessiné "à la main" juste
            # après add_page(), plus bas) — seulement les pages suivantes.
            if self.page_no() == 1:
                return
            self._draw_header_row()

        def footer(self):
            self.set_y(-12)
            self.set_font("Helvetica", "", 8)
            self.set_text_color(120, 120, 120)
            self.cell(0, 8, f"Page {self.page_no()}/{{nb}}", align="C")
            self.set_text_color(0, 0, 0)

        def _draw_header_row(self):
            self.set_font("Helvetica", "B", self._header_font_size)
            self.set_fill_color(31, 78, 36)
            self.set_text_color(255, 255, 255)
            self.cell(photo_col_width, header_height, photo_header, border=1, align="C", fill=True)
            for h in headers:
                self.cell(self._text_col_width, header_height, h, border=1, align="C", fill=True)
            self.ln(header_height)
            self.set_text_color(0, 0, 0)

    pdf = _RosterPhotoPDF(orientation=orientation, unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _safe_pdf_text("Répertoire"), border=0)
    pdf.ln(12)

    avail_width = pdf.w - pdf.l_margin - pdf.r_margin
    text_col_width = (avail_width - photo_col_width) / max(1, len(text_cols))
    pdf._text_col_width = text_col_width

    header_font_size = _pdf_fit_font_size(pdf, [photo_header] + headers, text_col_width, bold=True)
    pdf._header_font_size = header_font_size
    pdf._draw_header_row()

    body_texts = [
        _safe_pdf_text(entry.get(key, "")) for entry in entries for key, _ in text_cols
    ] or [""]
    body_font_size = _pdf_fit_font_size(pdf, body_texts, text_col_width, bold=False)

    for entry in entries:
        # Hauteur de ligne VARIABLE (correction du 2026-10-07) : haute
        # (row_height, intégralement inchangée) UNIQUEMENT si ce joueur a
        # réellement une photo ; compacte (adaptée au texte) sinon — la
        # cellule Photo reste vide, Nom/Club/etc. restent centrés dans une
        # ligne normale, exactement comme le rendu texte seul déjà validé.
        # Déterminé AVANT tout dessin pour que le calcul de saut de page
        # ci-dessous utilise la hauteur RÉELLE de CETTE ligne précise.
        photo_path = player_photos.get_photo_path(entry.get("name", ""))
        row_h = row_height if photo_path else _ROSTER_PDF_COMPACT_ROW_HEIGHT_MM

        # Saut de page déclenché NOUS-MÊMES, avant de capturer x0/y0
        # (correctif trouvé par un test direct avant livraison) : laisser
        # le pdf.cell() ci-dessous déclencher SON PROPRE saut de page
        # automatique (set_auto_page_break) capturait x0/y0 sur l'ANCIENNE
        # page juste avant, puis plaçait l'image et les cellules de texte
        # à ces coordonnées désormais invalides sur la page suivante
        # (page 2 quasi vide, ligne scindée sur la page 3) — reproduit et
        # corrigé par tests/test_roster_pdf_export.py. En vérifiant nous-
        # mêmes AVANT de dessiner quoi que ce soit, avec la hauteur RÉELLE
        # de cette ligne (row_h, jamais la constante haute fixe — sinon
        # une ligne compacte sans photo, suivie d'une ligne AVEC photo,
        # pourrait voir cette dernière coupée entre deux pages), pdf.cell()
        # ci-dessous ne peut plus jamais déclencher de saut de page en
        # cours de ligne : x0/y0 restent valides pour toute la ligne, et
        # une photo n'est jamais scindée entre deux pages.
        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()
        # Police explicitement réaffirmée à chaque ligne (après un saut de
        # page éventuel, header() — voir au-dessus — laisse la police en
        # gras/taille d'en-tête ; sans cette ligne, le corps du tableau
        # continuerait à tort dans cette police, même précaution que
        # _write_pdf_table).
        pdf.set_font("Helvetica", "", body_font_size)
        pdf.set_text_color(0, 0, 0)
        x0 = pdf.get_x()
        y0 = pdf.get_y()
        pdf.cell(photo_col_width, row_h, "", border=1)

        if photo_path:
            # row_h vaut ici forcément row_height (la hauteur haute,
            # inchangée) : mêmes marges/mise à l'échelle qu'avant cette
            # correction, proportions et taille de la photo identiques.
            max_w = photo_col_width - 4
            max_h = row_h - 4
            try:
                w, h = _fit_image_box(photo_path, max_w, max_h)
                img_x = x0 + (photo_col_width - w) / 2
                img_y = y0 + (row_h - h) / 2
                pdf.image(photo_path, x=img_x, y=img_y, w=w, h=h)
            except Exception:
                pass  # photo illisible : cellule laissée vide, jamais une erreur bloquante

        pdf.set_xy(x0 + photo_col_width, y0)
        for key, _ in text_cols:
            pdf.cell(
                text_col_width, row_h, _safe_pdf_text(entry.get(key, "")),
                border=1, align="C",
            )
        pdf.ln(row_h)

    pdf.output(path)
    return path
