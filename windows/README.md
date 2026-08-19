# Générer l'installateur Windows (.msi)

Ce dossier contient tout ce qu'il faut pour transformer l'application
Python (`main.py` et les autres fichiers à la racine du dépôt) en un
programme d'installation Windows classique : `PokerTournament-16.msi`.

Deux façons d'obtenir ce fichier :

## 1. Automatiquement, via GitHub Actions (recommandé)

Si ce dépôt est hébergé sur GitHub, l'onglet **Actions** propose un
workflow **"Build Windows .msi"** qui compile tout sur une machine
Windows fournie par GitHub — vous n'avez besoin de rien installer.

- Lancez-le manuellement : onglet *Actions* → *Build Windows .msi* →
  *Run workflow*.
- Ou poussez un tag `v*` (ex. `git tag v1.0.0 && git push --tags`) : le
  `.msi` est alors aussi joint automatiquement à une *Release* GitHub.
- Une fois terminé (quelques minutes), téléchargez `PokerTournament-16.msi`
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

Le fichier obtenu est `windows\dist\PokerTournament-16.msi`.

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
   d'installation Windows (`PokerTournament-16.msi`) : dossier dans
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
| `dist/` *(généré)* | Contient `PokerTournament.exe` puis `PokerTournament-16.msi` |

## Changer le numéro de version

Modifiez `Version="1.0.0"` dans [`app.wxs`](app.wxs) avant de
reconstruire. Conservez le même `UpgradeCode` d'une version à l'autre :
c'est lui qui permet à une mise à jour de remplacer proprement une
installation précédente.

## Comment Claude (l'assistant IA) génère et livre ce .msi

Cette section documente noir sur blanc la procédure suivie quand on
demande à Claude « génère/refais un .msi » depuis ce Mac — pour qu'elle
survive à un crash de session, un changement de machine ou d'assistant.
Rien ici ne dépend d'une mémoire propre à Claude : tout ce qui est
nécessaire est soit dans ce dépôt (donc sur GitHub), soit dans la
configuration de ce Mac (Trousseau macOS).

**Prérequis déjà en place** (rien à reconfigurer) :
- Ce dépôt local pointe déjà vers
  `https://github.com/senaraj-280652/Poker-tournament-senaco.git`
  (`git remote -v`).
- L'authentification pour `git push` est gérée par le Trousseau macOS
  (`git config credential.helper` → `osxkeychain`) : le jeton GitHub y
  est stocké une fois pour toutes, indépendamment de toute session
  Claude.
- Le workflow `.github/workflows/build-msi.yml` est déjà commité — il
  compile sur un runner Windows fourni par GitHub (PyInstaller + WiX
  Toolset 5.0.2), sans rien installer sur ce Mac.
- `gh` (GitHub CLI) **n'est pas installé** sur ce Mac : les appels à
  l'API GitHub se font en HTTPS anonyme via `curl` (le dépôt est
  public, la lecture ne demande pas d'authentification).

**Étapes pour livrer un nouveau `.msi` après une modification du code :**

1. Monter le numéro de version dans **les deux fichiers** qui doivent
   rester synchronisés (voir la section précédente) :
   [`version.py`](../version.py) (`APP_VERSION`) et
   [`windows/app.wxs`](app.wxs) (`Version=`).
2. Committer et pousser sur `main` :
   ```bash
   git add -A && git commit -m "..." && git push origin main
   ```
3. Poser un tag `vX.Y.Z` et le pousser — c'est ce qui déclenche le
   build ET la publication automatique dans une Release GitHub :
   ```bash
   git tag vX.Y.Z && git push origin vX.Y.Z
   ```
4. Suivre le build (± 5-8 min, pas de `gh` donc via l'API REST) :
   ```bash
   curl -s "https://api.github.com/repos/senaraj-280652/Poker-tournament-senaco/actions/runs?event=push&per_page=3"
   ```
   Repérer le run dont `head_branch` est le tag posé, attendre que
   `status` passe à `completed` (`conclusion: success`).
5. Récupérer l'URL de l'asset une fois la Release publiée :
   ```bash
   curl -s "https://api.github.com/repos/senaraj-280652/Poker-tournament-senaco/releases/tags/vX.Y.Z"
   ```
   (champ `assets[0].browser_download_url`, fichier
   `PokerTournament-16.msi`).
6. Télécharger ce `.msi` **dans `~/Downloads/`**, sous un nom incluant
   le numéro de version (ex. `PokerTournament-vX.Y.Z.msi`) plutôt que
   d'écraser un fichier existant du même nom : sur APFS, écraser un
   fichier en place conserve son ancienne date de création, ce qui a
   déjà causé une confusion (« pourquoi ce fichier date du 11/08 ? »)
   alors que le contenu était à jour. Un nom neuf = une date de
   création fiable.

**Repères utiles pour ce Mac spécifiquement** (évite de re-découvrir à
chaque fois) :
- Ni `pandoc`, ni LibreOffice (`soffice`), ni `poppler`
  (`pdftoppm`/`pdftotext`) ne sont installés. `python-docx` et
  `pymupdf` (`fitz`) le sont, et **Microsoft Word** est installé — pour
  produire un PDF à partir d'un `.docx` modifié (ex. le manuel
  utilisateur), passer par Word en AppleScript (`osascript`, commande
  `save as ... file format format PDF`) plutôt que par LibreOffice.
- `brew` n'est pas installé non plus.
