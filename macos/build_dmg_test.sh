#!/bin/bash
# Construit PokerTournamentTest.app (arm64, Apple Silicon M1/M2/M3/M4)
# puis un installateur .dmg — chantier "Senaco autonome sur Mac Apple
# Silicon" (2026-09-26). Copie volontairement indépendante de
# build_dmg.sh (production, x86_64/target_arch natif de la machine) :
# PAS générée à partir de lui, PAS maintenue en le modifiant — toute
# correction commune doit être reportée à la main dans les deux.
#
# Différences avec build_dmg.sh, TOUTES nécessaires à la sécurité de
# cette ligne TEST (même principe que windows/build-msi-test.yml) :
#   - construit macos/poker_tournament-test.spec (PAS poker_tournament.
#     spec) -> macos/dist/PokerTournamentTest.app (jamais le nom de la
#     production, jamais le même dossier) ;
#   - AUCUNE vérification de _license_secret.py qui l'ACTIVERAIT : ce
#     script REFUSE de construire si _license_secret.py est présent
#     (contrairement à build_dmg.sh, qui se contente d'avertir) — cette
#     ligne TEST ne doit JAMAIS pouvoir accidentellement embarquer un
#     verrou de licence actif ;
#   - MACOSX_DEPLOYMENT_TARGET=11.0 fixé explicitement (Big Sor, la toute
#     première version macOS ayant existé sur Apple Silicon) plutôt que
#     laissé implicite (dépendant de la machine/Python de build) ;
#   - exige un Python fournissant réellement la tranche arm64 (universal2
#     ou nativement arm64) — target_arch="arm64" dans le spec échouerait
#     sinon avec une erreur PyInstaller peu claire ; ce script vérifie
#     et explique AVANT d'y arriver.
#
# Prérequis : Python 3.9+ fournissant la tranche arm64 (le Python.org
# officiel est "universal2" depuis plusieurs versions : `file $(which
# python3)` doit lister "arm64" parmi les architectures). Rien d'autre à
# installer au préalable : hdiutil et codesign font partie de macOS.
#
# Utilisation (depuis la racine du dépôt) :
#   ./macos/build_dmg_test.sh
#
# Résultat : macos/dist/PokerTournamentTest.dmg
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

APP_NAME="PokerTournamentTest"
VOL_NAME="Gestionnaire de Tournoi de Poker [TEST]"

echo "== 0/6 : vérifications préalables =="
if [ -f "_license_secret.py" ]; then
    echo "ERREUR : _license_secret.py est présent — ce script REFUSE de construire" >&2
    echo "         une ligne TEST avec la licence active. Retirez-le (ou renommez-le" >&2
    echo "         temporairement) avant de relancer ce script." >&2
    exit 1
fi
echo "   (licence : _license_secret.py absent -> désactivée, comme voulu pour ce build)"

if ! file "$(command -v python3)" | grep -q "arm64"; then
    echo "ERREUR : le Python3 utilisé (\"$(command -v python3)\") ne fournit pas la" >&2
    echo "         tranche arm64 (universal2 ou natif arm64 attendu) — target_arch=" >&2
    echo "         \"arm64\" du spec échouerait. Utilisez un Python.org officiel récent," >&2
    echo "         ou construisez directement sur un Mac Apple Silicon, ou via le" >&2
    echo "         workflow GitHub Actions dédié (runner macOS natif arm64)." >&2
    exit 1
fi
echo "   (Python arm64 : présent, confirmé via 'file')"

echo "== 1/6 : environnement virtuel Python =="
rm -rf macos/venv_test
python3 -m venv macos/venv_test
VENV_PY="macos/venv_test/bin/python3"

echo "== 2/6 : installation des dépendances (PyInstaller, openpyxl, opencv, Pillow) =="
"$VENV_PY" -m pip install --upgrade pip --quiet
"$VENV_PY" -m pip install -r macos/requirements.txt --quiet

echo "== 3/6 : génération de ${APP_NAME}.app (PyInstaller, arm64) =="
rm -rf macos/dist macos/build

# Plancher macOS explicite (voir l'en-tête de ce script et
# LSMinimumSystemVersion dans le spec, qui déclare la même valeur à
# Launch Services/Finder) — jamais laissé implicite.
export MACOSX_DEPLOYMENT_TARGET=11.0

"$VENV_PY" -m PyInstaller macos/poker_tournament-test.spec --distpath macos/dist --workpath macos/build --noconfirm
if [ ! -d "macos/dist/${APP_NAME}.app" ]; then
    echo "Erreur : PyInstaller n'a pas produit macos/dist/${APP_NAME}.app" >&2
    exit 1
fi

echo "== 4/6 : vérification de l'architecture produite =="
ACTUAL_ARCH="$(lipo -archs "macos/dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}" 2>&1 || true)"
if [ "$ACTUAL_ARCH" != "arm64" ]; then
    echo "ERREUR : l'exécutable produit n'est pas purement arm64 (obtenu : '${ACTUAL_ARCH}')." >&2
    echo "         Le .dmg ne sera PAS généré — ne pas distribuer ce build." >&2
    exit 1
fi
echo "   (confirmé : arm64 pur, compatible M1/M2/M3/M4)"

echo "== 5/6 : signature ad-hoc de l'app =="
# Signature "ad-hoc" (gratuite, sans compte développeur Apple) : évite le
# message "l'app est endommagée" au premier lancement. Le message normal
# de Gatekeeper ("développeur non identifié"), lui, reste inévitable sans
# un vrai compte développeur Apple payant — voir macos/README.md.
codesign --force --deep --sign - "macos/dist/${APP_NAME}.app"

echo "== 6/6 : génération du .dmg =="
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
echo "Terminé : macos/dist/${APP_NAME}.dmg (arm64, licence désactivée, marqueur [TEST])"
