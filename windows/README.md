# Générer l'installateur Windows (.msi)

Ce dossier contient tout ce qu'il faut pour transformer l'application
Python (`main.py` et les autres fichiers à la racine du dépôt) en un
programme d'installation Windows classique : `PokerTournament.msi`.

Deux façons d'obtenir ce fichier :

## 1. Automatiquement, via GitHub Actions (recommandé)

Si ce dépôt est hébergé sur GitHub, l'onglet **Actions** propose un
workflow **"Build Windows .msi"** qui compile tout sur une machine
Windows fournie par GitHub — vous n'avez besoin de rien installer.

- Lancez-le manuellement : onglet *Actions* → *Build Windows .msi* →
  *Run workflow*.
- Ou poussez un tag `v*` (ex. `git tag v1.0.0 && git push --tags`) : le
  `.msi` est alors aussi joint automatiquement à une *Release* GitHub.
- Une fois terminé (quelques minutes), téléchargez `PokerTournament.msi`
  dans les *Artifacts* de l'exécution (ou dans la Release).

## 2. Localement, sur un PC Windows

Prérequis (une seule fois) :
- **Python 3.9+** — https://www.python.org/downloads/ (cochez "Add
  python.exe to PATH" pendant l'installation)
- **.NET SDK 6+** — https://dotnet.microsoft.com/download (nécessaire
  pour l'outil `wix`, installé automatiquement par le script)

Puis, dans PowerShell, à la racine du dépôt :

```powershell
.\windows\build.ps1
```

Le fichier obtenu est `windows\dist\PokerTournament.msi`.

## Ce que fait le build

1. **PyInstaller** empaquette `main.py` (+ Tkinter, openpyxl, OpenCV,
   Pillow) en un exécutable autonome `PokerTournament.exe` — rien à
   installer sur le poste de l'utilisateur final, tout est inclus. Un
   écran de démarrage (« Chargement en cours... », `assets/splash.png`)
   s'affiche dès le lancement de l'`.exe`, le temps que celui-ci
   s'extraie et que la fenêtre principale soit prête (voir la section
   `Splash` de `poker_tournament.spec` et l'appel `pyi_splash.close()`
   dans `main.py`).
2. **WiX Toolset** enveloppe cet `.exe` dans un vrai programme
   d'installation Windows (`PokerTournament.msi`) : dossier dans
   *Program Files*, raccourcis dans le menu Démarrer et sur le Bureau,
   entrée dans "Applications et fonctionnalités" pour la désinstallation.

Les données de l'utilisateur (répertoire de joueurs, photos, fichiers
`.tournoi`...) ne sont jamais embarquées dans l'installateur : elles
restent dans son dossier personnel (`%USERPROFILE%\.poker_tournament`
et les fichiers `.tournoi` qu'il choisit d'ouvrir/créer), exactement
comme sur macOS.

## Fichiers de ce dossier

| Fichier | Rôle |
|---|---|
| `poker_tournament.spec` | Configuration PyInstaller (génère l'`.exe`) |
| `assets/splash.png` | Image de l'écran de démarrage (« Chargement en cours... ») |
| `requirements.txt` | Dépendances Python nécessaires au build |
| `app.wxs` | Description de l'installateur (WiX Toolset) |
| `License.rtf` | Texte affiché sur l'écran de licence de l'installateur |
| `build.ps1` | Script tout-en-un (exe → msi) |
| `dist/` *(généré)* | Contient `PokerTournament.exe` puis `PokerTournament.msi` |

## Changer le numéro de version

Modifiez `Version="1.0.0"` dans [`app.wxs`](app.wxs) avant de
reconstruire. Conservez le même `UpgradeCode` d'une version à l'autre :
c'est lui qui permet à une mise à jour de remplacer proprement une
installation précédente.
