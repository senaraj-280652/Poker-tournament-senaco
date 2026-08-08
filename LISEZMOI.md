# Gestionnaire de Tournoi de Poker

Application de bureau pour gérer un tournoi de poker multi-tables (testée
avec 100+ joueurs) : joueurs, tables, chips, chronomètre de blindes et
gains.

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
dossier. Une fenêtre de Terminal s'ouvre brièvement (normal) puis
l'application démarre — vous n'avez rien à taper.

*La toute première fois*, macOS peut refuser en disant que l'éditeur n'est
pas vérifié. Dans ce cas : clic droit (ou Ctrl+clic) sur
`Lancer_le_tournoi.command` → **Ouvrir** → confirmez **Ouvrir** dans la
boîte de dialogue. Cette étape n'est nécessaire qu'une seule fois ; les
lancements suivants se feront par simple double-clic.

### Alternative : en ligne de commande

Ouvrez un terminal dans le dossier `poker_tournament` puis :

```
python3 main.py
```

(sous Windows, `python main.py` — le fichier `.command` ne fonctionne que
sur Mac ; sous Windows, utilisez cette méthode.)

Au démarrage, choisissez **"Nouveau tournoi"** (vous créez un fichier
`.tournoi`, qui contient toutes les données) ou **"Ouvrir un tournoi
existant"** pour reprendre un tournoi déjà commencé.

## Fonctionnalités

- **Répertoire de joueurs** : vos joueurs habituels sont mémorisés (indépendamment
  des tournois) et proposés sous forme de liste à cocher/décocher à la
  création d'un nouveau tournoi. Gérable à tout moment via le menu
  "Répertoire > Gérer le répertoire de joueurs...". On peut aussi piocher
  dans le répertoire en cours de tournoi avec "Ajouter depuis le
  répertoire..." (utile pour les inscriptions tardives).
- **Joueurs** : inscription, rebuy, add-on, modification manuelle des chips,
  élimination (place calculée automatiquement), réinscription, suppression.
- **Tables** : placement automatique des joueurs, rééquilibrage automatique
  après chaque élimination/inscription (fermeture des tables devenues
  inutiles, déplacement des joueurs pour garder les tables équilibrées).
  Un bouton permet aussi de forcer un rééquilibrage. Si le nombre de
  tables dépasse la capacité d'affichage de l'écran, l'onglet défile
  automatiquement, lentement et en boucle (utile pour laisser cet onglet
  affiché en continu sur un écran dédié) ; sinon rien ne défile. La
  molette de la souris permet aussi de défiler manuellement à tout
  moment.
- **Chronomètre** : structure de blindes standard préchargée (modifiable),
  démarrage/pause/niveau suivant-précédent, passage automatique au niveau
  suivant à la fin du temps. Un écran séparé ("Affichage > Ouvrir l'écran
  chronomètre") peut être mis en plein écran (touche F11) sur un
  vidéoprojecteur ou un second écran.
- **Gains** : génération automatique d'une grille de gains standard basée
  sur le nombre d'entrées (buy-ins), modifiable place par place. Le prize
  pool est calculé à partir des buy-ins/rebuys/add-ons et d'un pourcentage
  de rake éventuel. Export du classement final en **CSV** ou **Excel
  (.xlsx)**, depuis le menu Fichier ou l'onglet Gains.
- **Joueurs** (compléments) : renommage d'un joueur, correction manuelle
  des compteurs buy-in/rebuy/add-on en cas d'erreur de saisie.
- **Paramètres** : montants de buy-in/rebuy/add-on, tapis de départ,
  nombre de sièges par table, rake.
- **Synthèse par période** (menu "Statistiques > Synthèse par
  période...") : balaye tous les fichiers `.tournoi` d'un dossier (et
  ses sous-dossiers si besoin) et affiche, pour une période donnée (dates
  de début/fin, bornes optionnelles) :
  - la liste des tournois de la période (date, statut, entrées, prize
    pool, vainqueur, primes distribuées) ;
  - le classement cumulé des joueurs sur la période, **primes (bounty)
    comprises** : nombre de tournois joués, victoires, meilleure place,
    total investi, gains de classement, primes empochées et solde net.

  La date d'un tournoi est celle fixée à sa création ; pour les fichiers
  créés avant l'existence de ce réglage, elle est déduite de la date de
  création du fichier lui-même. Exportable en CSV depuis la fenêtre de
  synthèse.

## Sauvegarde

Toutes les actions sont enregistrées immédiatement dans le fichier
`.tournoi` (base SQLite). Pour faire une sauvegarde, copiez simplement ce
fichier ailleurs.

## Structure du code

- `main.py` — interface graphique (Tkinter), tous les onglets, ainsi que
  la fenêtre "Synthèse par période" (`PeriodSummaryDialog`)
- `database.py` — accès aux données et logique métier (sièges,
  rééquilibrage, calculs de gains). Contient aussi, au niveau module
  (hors classe `Database`), les fonctions de synthèse multi-tournois :
  `find_tournament_files`, `build_period_summary` et
  `export_period_summary_csv`, qui parcourent plusieurs fichiers
  `.tournoi` à la fois
- `structures.py` — structure de blindes par défaut et grille de gains
  standard
- `clock_window.py` — fenêtre d'affichage du chronomètre (mode projecteur)
- `roster.py` — répertoire de joueurs habituels (indépendant des tournois)
- `tournament_prefs.py` — derniers paramètres utilisés, repris par défaut
  pour un nouveau tournoi (indépendant des tournois)
- `player_photos.py` — photos de joueurs, associées au répertoire
  (indépendant des tournois)
