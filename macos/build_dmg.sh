#!/bin/bash
# Construit PokerTournament.app (PyInstaller) puis un installateur .dmg
# (glisser-déposer vers /Applications, le format standard sur macOS) à
# partir des sources Python du dépôt.
#
# Prérequis : Python 3.9+ (déjà présent sur macOS). Rien d'autre à
# installer au préalable : hdiutil et codesign font partie de macOS.
#
# Utilisation (depuis la racine du dépôt) :
#   ./macos/build_dmg.sh
#
# Résultat : macos/dist/PokerTournament.dmg
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="PokerTournament"
VOL_NAME="Gestionnaire de Tournoi de Poker"

echo "== 1/5 : environnement virtuel Python =="
rm -rf macos/venv
python3 -m venv macos/venv
VENV_PY="macos/venv/bin/python3"

echo "== 2/5 : installation des dépendances (PyInstaller, openpyxl, opencv, Pillow) =="
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r macos/requirements.txt --quiet

echo "== 3/5 : génération de PokerTournament.app (PyInstaller) =="
rm -rf macos/dist macos/build

# Verrou anti-copie (voir license.py) : nécessite un fichier local
# _license_secret.py (jamais versionné — voir .gitignore et l'en-tête de
# license.py). Absent de ce dossier, l'app produite démarre sans jamais
# demander d'activation (utile pour des builds de test, à éviter pour une
# vraie distribution).
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
# Signature "ad-hoc" (gratuite, sans compte développeur Apple) : évite le
# message "l'app est endommagée" au premier lancement. Le message normal
# de Gatekeeper ("développeur non identifié"), lui, reste inévitable sans
# un vrai compte développeur Apple payant — voir macos/README.md pour la
# marche à suivre (clic droit > Ouvrir).
codesign --force --deep --sign - "macos/dist/${APP_NAME}.app"

echo "== 5/5 : génération du .dmg =="
STAGING="macos/dist/dmg_staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "macos/dist/${APP_NAME}.app" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

rm -f "macos/dist/${APP_NAME}.dmg"
hdiutil create -volname "$VOL_NAME" \
    -srcfolder "$STAGING" \
    -ov -format UDZO \
    "macos/dist/${APP_NAME}.dmg"

rm -rf "$STAGING"

echo ""
echo "Terminé : macos/dist/${APP_NAME}.dmg"
