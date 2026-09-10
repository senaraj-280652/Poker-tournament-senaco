# Gestionnaire de Tournoi de Poker

Application de bureau pour gérer un ou plusieurs tournois de poker
multi-tables (Sit & Go compris), chacun dans sa propre fenêtre — testée
avec 100+ joueurs simultanés : joueurs, tables, chips, chronomètre de
blindes, primes en points (bounty classique et PKO), sauvegarde/
restauration et contrôle à distance depuis un téléphone.

Manuel utilisateur complet : [`MANUEL_UTILISATEUR_TOURNOI_CPC.docx`](MANUEL_UTILISATEUR_TOURNOI_CPC.docx).

## Installation

Il faut uniquement **Python 3.8 ou plus récent** — aucune autre dépendance.

- **Windows / macOS** : Python inclut déjà Tkinter, rien à installer en plus.
- **Linux** : si besoin, installez le paquet système :
  `sudo apt install python3-tk` (Debian/Ubuntu) ou équivalent.

### Pour l'export Excel (.xlsx) uniquement

L'export CSV fonctionne sans rien installer de plus. Pour l'export au
format Excel (.xlsx), il faut le paquet `openpyxl` :

```
pip3 install openpyxl
```

Si ce paquet n'est pas installé, l'application vous le rappellera
automatiquement au moment de l'export et vous pourrez toujours exporter en
CSV en attendant.

### Pour l'export PDF uniquement

Certains exports (résultats, primes, synthèse par période...) proposent
aussi le PDF, en plus du CSV et de l'Excel. Il faut le paquet `fpdf2` :

```
pip3 install fpdf2
```

### Pour les photos de joueurs uniquement

Importer une photo depuis un fichier existant fonctionne sans rien
installer de plus. Pour **prendre une photo avec la caméra** et pour
**afficher les aperçus/vignettes**, il faut les paquets `opencv-python`
et `Pillow` :

```
pip3 install opencv-python pillow
```

Si ces paquets ne sont pas installés, l'application vous le rappellera
au moment de prendre une photo (l'import de fichier reste disponible).

## Lancement

**Le plus simple : double-cliquez sur `Lancer_le_tournoi.command`** dans le
dossier (macOS). Une fenêtre de Terminal s'ouvre brièvement (normal) puis
l'application démarre — vous n'avez rien à taper.

*La toute première fois*, macOS peut refuser en disant que l'éditeur n'est
pas vérifié. Dans ce cas : clic droit (ou Ctrl+clic) sur
`Lancer_le_tournoi.command` → **Ouvrir** → confirmez **Ouvrir** dans la
boîte de dialogue. Cette étape n'est nécessaire qu'une seule fois ; les
lancements suivants se feront par simple double-clic.

Des installateurs prêts à l'emploi existent aussi pour Windows (`.msi`,
voir [`windows/README.md`](windows/README.md)) et macOS (`.dmg`/`.pkg`,
voir [`macos/README.md`](macos/README.md)) : rien à installer côté
utilisateur final dans ce cas non plus.

### Alternative : en ligne de commande

Ouvrez un terminal dans le dossier `poker_tournament` puis :

```
python3 main.py
```

(sous Windows, `python main.py`.)

Au démarrage (Menu principal), choisissez **"Nouveau tournoi"** /
**"Sit & Go rapide"** (vous créez un fichier `.tournoi`, qui contient
toutes les données), **"Ouvrir un tournoi existant"** pour reprendre un
tournoi déjà commencé, ou **"Lobby"** pour une vue d'ensemble de tous les
tournois d'un dossier.

## Fonctionnalités

- **Gestion de tournoi et Sit & Go** : inscription, rebuy, add-on,
  élimination (place calculée automatiquement), réinscription,
  suppression, placement et rééquilibrage automatique des tables
  (y compris table finale automatique à 10 joueurs ou moins, et
  rééquilibrage guidé par la grosse blinde), chronomètre de blindes
  avec écran projecteur dédié.
- **Répertoire de joueurs** : joueurs habituels mémorisés indépendamment
  des tournois (nom, club, photo), proposés à la création d'un nouveau
  tournoi ou piochables en cours de route.
- **Photos de joueurs** : import depuis un fichier, capture caméra
  (ordinateur ou téléphone, avec cadrage tactile côté téléphone),
  affichées dans le répertoire, l'onglet Joueurs et le bandeau
  d'élimination.
- **Chronomètre & bandeaux** : structure de blindes personnalisable
  (modèles réutilisables), sons configurables (fin de round/pause,
  prochain changement de blindes, sortie d'un joueur), écran projecteur
  plein écran. Après chaque élimination, un bandeau dédié (« XXX est
  sorti par YYY ») s'affiche sur le projecteur, avec photos si
  disponibles ; sa durée d'affichage est réglable (0 = désactivé, le son
  continue de jouer). Un bandeau séparé signale un mouvement de tables
  en cours.
- **Rééquilibrage des tables** : automatique après chaque
  inscription/élimination, avec redistribution aléatoire lors de la
  fermeture d'une table et confirmation guidée du siège "grosse blinde"
  quand c'est nécessaire (relayée sur le contrôle à distance).
- **Primes en points — bounty classique et PKO** : quatre primes
  cumulées (Présence, Assiduité, Classement, Bounty). En bounty
  classique, Nb Bounty × Val Bounty. En mode PKO (bounty progressive),
  une partie de la bounty d'un joueur éliminé est empochée immédiatement
  par l'éliminateur, le reste grossit sa propre bounty jusqu'à ce qu'il
  soit éliminé à son tour ; à la fin du tournoi, la bounty encore portée
  par le vainqueur lui est définitivement attribuée (Moy Bounty = Mon
  Bounty ÷ Nb Bounty, sans jamais augmenter Nb Bounty). Un éliminateur
  valide est obligatoire en PKO dès qu'une bounty est en jeu (PC,
  téléphone et élimination groupée). Tout est exprimé en points (pts),
  jamais en euros.
- **Sauvegarde/restauration sur clé USB** : depuis le Menu principal,
  deux boutons sauvegardent/restaurent en un clic l'ensemble des
  données (fichiers `.tournoi`, répertoire, photos, modèles, réglages,
  fichier de licence) — jamais de suppression des originaux,
  restauration protégée par une sauvegarde de sécurité automatique et
  refusée tant qu'un tournoi est ouvert.
- **Contrôle à distance (téléphone)** : page web (aucune app à
  installer) pour éliminer un joueur (glisser-déposer), piloter le
  chronomètre, consulter le plan des tables et les mouvements, gérer les
  photos, et un Lobby dédié quand plusieurs tournois sont joignables à
  la fois.
- **Lobby multi-tournois** : vue d'ensemble de tous les tournois/Sit & Go
  ouverts (état, joueurs actifs, niveau, temps restant), avec bascule
  directe vers l'un d'eux ; préférence "Un seul tournoi à la fois" pour
  limiter le club à un tournoi en cours.
- **Exports** : joueurs, classement, résultats nominatifs, primes
  (récapitulatif + historique PKO), synthèse par période (multi-
  tournois, avec filtre Club et lignes de total) — en CSV, Excel (.xlsx)
  ou PDF selon les paquets installés.
- **Licence** : un mécanisme d'activation par machine existe dans le
  code (`license.py`) mais n'est **pas actif** sur les exécutables
  actuellement distribués — aucune activation n'est demandée pour
  l'instant.

## Sauvegarde

Toutes les actions sont enregistrées immédiatement dans le fichier
`.tournoi` (base SQLite). Pour une sauvegarde ponctuelle, copiez
simplement ce fichier ailleurs ; pour une sauvegarde complète (données +
réglages), utilisez les boutons dédiés du Menu principal (voir
ci-dessus et le manuel utilisateur, chapitre 17).

## Structure du code

- `main.py` — interface graphique (Tkinter), tous les onglets et
  fenêtres, dont le Menu principal et l'écran de sauvegarde/restauration
- `database.py` — accès aux données et logique métier de chaque tournoi
  (sièges, rééquilibrage, primes/bounty classique et PKO, calculs de
  gains). Contient aussi, au niveau module, les fonctions de synthèse
  multi-tournois (`find_tournament_files`, `build_period_summary`,
  `export_period_summary_csv`)
- `backup_restore.py` — sauvegarde/restauration complète (`.tournoi` +
  dossier `~/.poker_tournament`) sur clé USB ou tout dossier de
  destination
- `remote_control.py` — serveur web du contrôle à distance depuis un
  téléphone (page HTML autonome, aucune dépendance externe)
- `open_windows.py` — registre des fenêtres/tournois actuellement
  ouverts (Lobby, unicité du Menu principal, sélection téléphone)
- `structures.py` — structure de blindes par défaut et grille de gains
  standard
- `clock_window.py` — fenêtre d'affichage du chronomètre (mode
  projecteur), y compris le bandeau d'élimination
- `roster.py` — répertoire de joueurs habituels (indépendant des
  tournois)
- `tournament_prefs.py` — derniers paramètres utilisés, repris par
  défaut pour un nouveau tournoi (indépendant des tournois)
- `player_photos.py` — photos de joueurs, associées au répertoire
- `chip_images.py` — images de jetons personnalisées (onglet Blindes)
- `chip_templates.py` / `blind_templates.py` / `settings_templates.py`
  — modèles réutilisables de jetons/blindes/réglages
- `sound_signal.py` — signaux sonores (mouvement de tables, etc.)
- `export_prefs.py` — préférences d'export (colonnes, format) et
  colonnes masquées
- `license.py` — verrou de licence par machine (voir
  [`LICENCE_ACTIVATION.md`](LICENCE_ACTIVATION.md) — mécanisme présent
  dans le code mais non activé sur les builds actuels)
- `version.py` — numéro de version affiché dans l'application

Chaque tournoi/Sit & Go correspond à un seul fichier `.tournoi` (base
SQLite autonome). Les données indépendantes des tournois (répertoire,
photos, modèles, réglages, licence, journal de plantage `crash.log`...)
sont stockées dans `~/.poker_tournament` (`%USERPROFILE%\.poker_tournament`
sous Windows).
