# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller DÉDIÉ à la version « TEST » macOS (chantier
# "Senaco autonome sur Mac Apple Silicon", 2026-09-26) : permet de
# construire une application totalement séparée de la ligne de
# production macOS (poker_tournament.spec), destinée à des tests à
# distance (licence désactivée, marqueur "[TEST]" visible) — jamais
# confondue avec une vraie installation, et sans jamais y toucher.
# Copie de poker_tournament.spec avec exactement 3 différences,
# symétriques de windows/poker_tournament-test.spec (même principe,
# jamais généré à partir de lui) :
#   1. name="PokerTournamentTest" (BUNDLE/EXE/COLLECT) — pour que
#      PyInstaller génère macos/dist/PokerTournamentTest.app, totalement
#      séparé de macos/dist/PokerTournament.app (production).
#   2. bundle_identifier distinct ("com.pokertournament.gestionnaire.
#      test") — un identifiant de bundle macOS est significatif au-delà
#      du simple nom de dossier (Launch Services, préférences...),
#      contrairement à un simple chemin d'installation Windows.
#   3. datas ajoute macos/assets/TEST_BUILD_MARKER (voir main.py:
#      _is_test_build, mécanisme déjà multiplateforme via sys._MEIPASS,
#      AUCUNE modification de main.py nécessaire) — seule différence
#      FONCTIONNELLE avec la production : c'est ce fichier, et lui seul,
#      qui fait afficher "[TEST]" dans le titre de fenêtre et "À propos"
#      une fois compilé. Ajoute aussi, s'il existe, macos/assets/
#      TEST_BUILD_NUMBER (même mécanisme que windows/poker_tournament-
#      test.spec : absent en local, généré par le workflow GitHub
#      Actions dédié avec ${{ github.run_number }}).
#
# Numéro de version LU DYNAMIQUEMENT depuis version.py (jamais codé en
# dur ici) : poker_tournament.spec (production) portait encore "1.2.16"
# alors que version.py affichait déjà 1.2.44 — cet écart ne peut plus se
# reproduire avec cette lecture dynamique, déjà le principe utilisé par
# macos/build_pkg.sh pour le numéro de version du .pkg.
#
# Architecture : target_arch="arm64" fixé EXPLICITEMENT (jamais None) —
# ce chantier vise spécifiquement M1/M2/M3/M4 (même jeu d'instructions
# arm64 sur toute la gamme, un seul binaire suffit) ; PyInstaller ne
# permet cette valeur que si le Python utilisé pour construire fournit
# réellement la tranche arm64 (universal2, ou un Python natif arm64 —
# voir macos/build_dmg_test.sh et son message d'erreur explicite sinon).
#
# LSMinimumSystemVersion="11.0" (Big Sur, la toute première version
# macOS ayant existé sur Apple Silicon) : déclare explicitement le
# plancher macOS supporté à Launch Services/Finder, plutôt que de
# dépendre implicitement de ce que le Python de build a lui-même comme
# plancher.
#
# Utilisation (depuis la racine du dépôt, avec un Python fournissant la
# tranche arm64 — voir macos/build_dmg_test.sh, qui fait tout cela
# automatiquement) :
#   pyinstaller macos/poker_tournament-test.spec --distpath macos/dist --workpath macos/build
#
# Toute correction apportée ici (icône, dépendances cachées...) doit être
# reportée manuellement dans poker_tournament.spec si elle s'applique
# aussi à la production, et réciproquement — les deux fichiers ne sont
# PAS générés l'un à partir de l'autre.
import sys
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de macos/)

sys.path.insert(0, str(ROOT))
import version as _version_module  # noqa: E402

APP_VERSION = _version_module.APP_VERSION

TEST_MARKER = Path(SPECPATH) / "assets" / "TEST_BUILD_MARKER"
# Optionnel : n'existe que lorsque ce spec est invoqué depuis le workflow
# GitHub Actions dédié (.github/workflows/build-dmg-test.yml), jamais en
# local sans action explicite — d'où la vérification .exists() avant de
# l'ajouter à `datas`. Voir main.py: _test_build_number, qui retombe
# proprement sur None (libellé générique "[TEST]", sans numéro) en son
# absence.
TEST_BUILD_NUMBER = Path(SPECPATH) / "assets" / "TEST_BUILD_NUMBER"

_datas = [
    # help_content.json : contenu de l'aide intégrée (menu Aide / touche
    # F1, voir help_browser.py), lu au runtime via sys._MEIPASS — sans
    # cette entrée, l'aide s'ouvrirait vide dans l'app packagée.
    (str(ROOT / "help_content.json"), "."),
    # Seule différence fonctionnelle avec poker_tournament.spec — voir
    # l'en-tête de ce fichier et main.py: _is_test_build.
    (str(TEST_MARKER), "."),
]
if TEST_BUILD_NUMBER.exists():
    _datas.append((str(TEST_BUILD_NUMBER), "."))

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=_datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PokerTournamentTest",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # application graphique (Tkinter) : pas de terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="arm64",
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PokerTournamentTest",
)

app = BUNDLE(
    coll,
    name="PokerTournamentTest.app",
    icon=None,
    bundle_identifier="com.pokertournament.gestionnaire.test",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "Gestionnaire de Tournoi de Poker [TEST]",
        "CFBundleDisplayName": "Gestionnaire de Tournoi de Poker [TEST]",
        "CFBundleShortVersionString": APP_VERSION,
        "NSHumanReadableCopyright": "Raj Juganaikloo",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "Utilisée pour prendre une photo de joueur depuis l'application.",
        "LSMinimumSystemVersion": "11.0",
    },
)
