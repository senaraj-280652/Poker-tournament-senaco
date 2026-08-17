#!/bin/bash
# Construit PokerTournament.app (PyInstaller) puis un installateur .pkg
# (double-clic, assistant d'installation guidé vers /Applications — en
# plus du .dmg glisser-déposer produit par build_dmg.sh, voir ce
# script-là pour le détail des étapes communes) à partir des sources
# Python du dépôt.
#
# Prérequis : Python 3.9+, pkgbuild (fait partie des Command Line Tools,
# déjà présents sur macOS — rien à installer en plus).
#
# Utilisation (depuis la racine du dépôt) :
#   ./macos/build_pkg.sh
#
# Résultat : macos/dist/PokerTournament.pkg
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="PokerTournament"
PKG_ID="com.pokertournament.gestionnaire"
PKG_VERSION="$(python3 -c "import version; print(version.APP_VERSION)")"

echo "== 1/5 : environnement virtuel Python =="
rm -rf macos/venv
python3 -m venv macos/venv
VENV_PY="macos/venv/bin/python3"

echo "== 2/5 : installation des dépendances (PyInstaller, openpyxl, opencv, Pillow) =="
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r macos/requirements.txt --quiet

echo "== 3/5 : génération de PokerTournament.app (PyInstaller) =="
rm -rf macos/dist macos/build

if [ -f "_license_secret.py" ]; then
    echo "   (verrou de licence activé pour ce build)"
else
    echo "   ATTENTION : _license_secret.py absent -> ce build ne demandera jamais d'activation."
    echo "   Voir l'en-tête de license.py pour le générer (une seule fois)."
fi

"$VENV_PY" -m PyInstaller macos/poker_tournament.spec --distpath macos/dist --workpath macos/build --noconfirm
if [ ! -d "macos/dist/${APP_NAME}.app" ]; then
    echo "Erreur : PyInstaller n'a pas produit macos/dist/${APP_NAME}.app" >&2
    exit 1
fi

echo "== 4/5 : signature ad-hoc de l'app =="
# Même remarque que dans build_dmg.sh : signature "ad-hoc" (gratuite),
# évite "l'app est endommagée" au premier lancement. Le message normal de
# Gatekeeper ("développeur non identifié") reste inévitable sans compte
# développeur Apple payant, aussi bien pour l'app que pour ce .pkg
# (non signé lui non plus, faute d'un certificat d'installateur valide)
# — voir macos/README.md (clic droit > Ouvrir).
codesign --force --deep --sign - "macos/dist/${APP_NAME}.app"

echo "== 5/5 : génération du .pkg =="
STAGING="macos/dist/pkg_staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "macos/dist/${APP_NAME}.app" "$STAGING/"

rm -f "macos/dist/${APP_NAME}.pkg"
pkgbuild --root "$STAGING" \
    --identifier "$PKG_ID" \
    --version "$PKG_VERSION" \
    --install-location "/Applications" \
    "macos/dist/${APP_NAME}.pkg"

rm -rf "$STAGING"

echo ""
echo "Terminé : macos/dist/${APP_NAME}.pkg"
