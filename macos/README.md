# Générer l'installateur macOS (.dmg)

Ce dossier contient tout ce qu'il faut pour transformer l'application
Python (`main.py` et les autres fichiers à la racine du dépôt) en une
vraie application macOS (`PokerTournament.app`) livrée dans un
disque-image `.dmg` classique (glisser-déposer vers *Applications*).

## Construire

Depuis la racine du dépôt, dans Terminal :

```bash
./macos/build_dmg.sh
```

Aucun outil à installer au préalable : seul Python 3 (déjà présent sur
macOS) est nécessaire — le script crée son propre environnement virtuel
et installe PyInstaller + les dépendances optionnelles (openpyxl, OpenCV,
Pillow) dedans, sans rien toucher au reste du système.

Le fichier obtenu est `macos/dist/PokerTournament.dmg`.

## Ce que fait le build

1. **PyInstaller** empaquette `main.py` (+ Tkinter, openpyxl, OpenCV,
   Pillow) en une application autonome `PokerTournament.app` — rien à
   installer sur le Mac de l'utilisateur final, tout est inclus.
2. **codesign** (outil intégré à macOS) signe l'app en mode *ad-hoc*
   (gratuit, sans compte développeur Apple) pour éviter le message
   « l'app est endommagée » au premier lancement.
3. **hdiutil** (outil intégré à macOS) assemble l'app et un raccourci
   vers */Applications* dans un `.dmg` compressé.

Les données de l'utilisateur (répertoire de joueurs, photos, fichiers
`.tournoi`...) ne sont jamais embarquées dans l'installateur : elles
restent dans son dossier personnel (`~/.poker_tournament` et les
fichiers `.tournoi` qu'il choisit d'ouvrir/créer), exactement comme
avant.

## Installer (côté utilisateur final)

1. Double-cliquer sur `PokerTournament.dmg`
2. Glisser **PokerTournament.app** dans le raccourci **Applications**
   affiché à côté
3. Éjecter le disque-image, lancer l'app depuis le Launchpad ou le
   dossier Applications

**Premier lancement uniquement** : comme l'app n'est pas distribuée via
le compte développeur Apple payant, macOS Gatekeeper affichera
« développeur non identifié ». Il faut alors : **clic droit (ou
Ctrl+clic) sur l'app → Ouvrir → confirmer Ouvrir**. Les lancements
suivants se font normalement, en double-cliquant.

## Limitation connue

Le `.app` généré ici est compilé pour l'architecture **Intel
(x86_64)**, celle du Mac qui a servi à la construction. Sur un Mac Apple
Silicon (M1/M2/M3...), il fonctionnera automatiquement via Rosetta 2
(macOS propose de l'installer au premier lancement si besoin), mais un
peu moins vite qu'une version native. Construire une version native
Apple Silicon nécessite de lancer `./macos/build_dmg.sh` directement sur
un Mac Apple Silicon.

## Fichiers de ce dossier

| Fichier | Rôle |
|---|---|
| `poker_tournament.spec` | Configuration PyInstaller (génère l'app `.app`) |
| `requirements.txt` | Dépendances Python nécessaires au build |
| `build_dmg.sh` | Script tout-en-un (app → dmg) |
| `dist/` *(généré)* | Contient `PokerTournament.app` puis `PokerTournament.dmg` |

## Changer le numéro de version

Modifiez `version="1.0.0"` (et `CFBundleShortVersionString`) dans
[`poker_tournament.spec`](poker_tournament.spec) avant de reconstruire.
