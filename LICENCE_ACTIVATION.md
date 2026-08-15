# Verrou anti-copie et numéro de version — guide de l'éditeur

Ce document s'adresse à vous (l'éditeur du logiciel), pas aux clubs
utilisateurs. Il explique comment fonctionne le verrou de licence mis en
place et comment délivrer une clé à un nouveau club.

## Principe en une phrase

Un exécutable compilé (`.msi` Windows ou `.dmg` macOS) construit avec un
« secret de licence » exige une activation au premier lancement : le
club vous envoie un identifiant machine, vous lui répondez avec une clé,
il la saisit une fois, et le logiciel ne redemande plus rien ensuite —
tout se passe hors-ligne.

Tant qu'un build n'a **pas** de secret de licence embarqué (cas normal
d'un lancement depuis les sources avec `python3 main.py`, ou d'un build
de test sans injection du secret), le logiciel démarre normalement sans
jamais rien demander.

## Étape 0 — créer le secret (une seule fois, à vie)

```bash
python3 -c "import secrets; print('SECRET = ' + repr(secrets.token_hex(32)))" > _license_secret.py
```

- Ce fichier **ne doit jamais être commité** (il est dans `.gitignore`) —
  le dépôt GitHub est public, un secret qui s'y trouverait permettrait à
  n'importe qui de fabriquer de fausses licences valides.
- **Gardez-en une copie de sûreté privée**, hors du dépôt Git (ex. :
  gestionnaire de mots de passe, clé USB). Le perdre = ne plus jamais
  pouvoir délivrer de nouvelle licence ni valider les anciennes. En
  changer = invalider toutes les licences déjà délivrées.
- Pour les builds Windows automatiques (GitHub Actions), ajoutez aussi
  son contenu comme secret du dépôt : sur GitHub → **Settings** →
  **Secrets and variables** → **Actions** → **New repository secret** →
  nom `LICENSE_SECRET`, valeur = ce qui suit `SECRET = '...'` dans le
  fichier (juste la chaîne hexadécimale, sans les guillemets). Le
  workflow `build-msi.yml` l'injecte alors automatiquement.

## Étape 1 — construire un exécutable avec le verrou actif

- **Windows, en local** : placez `_license_secret.py` à la racine du
  dépôt avant de lancer `.\windows\build.ps1` (le script le détecte tout
  seul et le retire après le build).
- **Windows, via GitHub Actions** : rien à faire une fois le secret
  `LICENSE_SECRET` ajouté (étape 0) — chaque build en tient
  automatiquement compte.
- **macOS** : placez `_license_secret.py` à la racine du dépôt avant de
  lancer `./macos/build_dmg.sh`.

Si le fichier est absent au moment du build, le script vous prévient
(« ATTENTION : ce build ne demandera jamais d'activation ») et continue
quand même — pratique pour des builds de test.

## Étape 2 — délivrer une licence à un club

1. Le club installe le logiciel et le lance : une fenêtre « Activation
   requise » affiche un **identifiant de machine** (ex.
   `A1B2-C3D4-E5F6-0789`).
2. Il vous communique cet identifiant, avec le **nom du club**.
3. Sur votre machine (là où se trouve `_license_secret.py`) :

   ```bash
   python3 generate_license.py "A1B2-C3D4-E5F6-0789" "Nom exact du club"
   ```

4. Le script affiche une **clé de licence**. Envoyez-la au club.
5. Le club saisit le nom du club (exactement comme vous l'avez tapé,
   la casse n'a pas d'importance mais l'orthographe si) et la clé dans
   la fenêtre d'activation, clique « Activer ». C'est fait, définitivement,
   sur cette machine — aucune reconnexion à faire ensuite.

Un club qui change d'ordinateur devra refaire une demande (nouvel
identifiant machine → nouvelle clé).

## Numéro de version

Le numéro affiché dans l'application (titre de fenêtre, menu **Aide** →
**À propos...**) vient de [`version.py`](version.py). Pensez à
l'incrémenter à chaque nouvelle version distribuée, et à reporter la
même valeur dans `windows/app.wxs` (attribut `Version` du `<Package>`,
voir la section « Changer le numéro de version » de
[`windows/README.md`](windows/README.md)) — ce fichier ne peut pas lire
`version.py` automatiquement.

## Limites (honnêteté technique)

Ce verrou dissuade la copie ordinaire (donner le `.msi`/`.dmg` à un
autre club sans demander de clé) mais n'est pas incassable : le
logiciel est du Python compilé avec PyInstaller, techniquement
décompilable par quelqu'un de suffisamment déterminé, qui pourrait en
théorie en extraire le secret ou contourner la vérification. Pour une
protection plus forte, il faudrait de la signature de code (empêche la
falsification silencieuse d'un exécutable) et/ou une vérification en
ligne — deux chantiers distincts, non couverts ici.
