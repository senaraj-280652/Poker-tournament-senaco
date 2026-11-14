#!/bin/bash
# Construit PokerTournament-TEST.app (PyInstaller, spec TEST dédié) puis
# un installateur .dmg clairement identifié TEST (demande du 2026-11-13,
# préparation d'une version TEST macOS Intel distincte de Senaco 1.2.45).
#
# Script DÉLIBÉRÉMENT SÉPARÉ de build_dmg.sh (jamais un paramètre/
# drapeau sur ce dernier) : aucun risque qu'une erreur de frappe ou
# d'automatisation future ne produise accidentellement un build TEST en
# croyant lancer la production, ou l'inverse.
#
# Prérequis : identiques à build_dmg.sh (Python 3.9+, déjà présent).
#
# Utilisation (depuis la racine du dépôt) :
#   ./macos/build_dmg_test.sh
#
# Résultat : macos/dist/Senaco-<version>-TEST-macOS-<arch>.dmg
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="PokerTournament-TEST"
VOL_NAME="Gestionnaire de Tournoi de Poker (TEST)"

APP_VERSION="$(python3 "$ROOT/macos/read_version.py")"
ARCH="$(uname -m)"
DMG_NAME="Senaco-${APP_VERSION}-TEST-macOS-${ARCH}.dmg"

echo "== 1/5 : environnement virtuel Python =="
rm -rf macos/venv
python3 -m venv macos/venv
VENV_PY="macos/venv/bin/python3"

echo "== 2/5 : installation des dépendances (PyInstaller, openpyxl, opencv, Pillow) =="
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r macos/requirements.txt --quiet

echo "== 3/5 : génération de ${APP_NAME}.app (PyInstaller, spec TEST) =="
rm -rf macos/dist macos/build

# Verrou anti-copie V2 (voir license.py) : comme pour build_dmg.sh, un
# build TEST local sans _license_secret.py ne demande jamais
# d'activation V2/HMAC — sans incidence sur les tests V3 (gérés par
# variable d'environnement POKER_TOURNAMENT_LICENSE_V3, prioritaire).
if [ -f "_license_secret.py" ]; then
    echo "   (verrou de licence V2 activé pour ce build — probablement non voulu pour un build TEST)"
else
    echo "   (pas de _license_secret.py : ce build TEST ne demandera jamais d'activation V2 — normal)"
fi

"$VENV_PY" -m PyInstaller macos/poker_tournament-test.spec --distpath macos/dist --workpath macos/build --noconfirm
if [ ! -d "macos/dist/${APP_NAME}.app" ]; then
    echo "Erreur : PyInstaller n'a pas produit macos/dist/${APP_NAME}.app" >&2
    exit 1
fi

echo "== 4/5 : signature ad-hoc de l'app =="
codesign --force --deep --sign - "macos/dist/${APP_NAME}.app"

echo "== 5/5 : génération du .dmg =="
STAGING="macos/dist/dmg_staging"
rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "macos/dist/${APP_NAME}.app" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

rm -f "macos/dist/${DMG_NAME}"
hdiutil create -volname "$VOL_NAME" \
    -srcfolder "$STAGING" \
    -ov -format UDZO \
    "macos/dist/${DMG_NAME}"

rm -rf "$STAGING"

echo ""
echo "Terminé : macos/dist/${DMG_NAME}"
echo "Rappel : installer en glissant dans /Applications ne remplace jamais"
echo "PokerTournament.app (nom et identifiant de bundle distincts)."
echo "Isolation des données : automatique sous macOS (~/SenacoTEST),"
echo "voir le bloc dédié en tête de main.py."
