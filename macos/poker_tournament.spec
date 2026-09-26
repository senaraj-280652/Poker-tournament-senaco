# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller : génère PokerTournament.app (macOS) à partir de
# main.py, autonome (Tkinter + toutes les dépendances optionnelles
# embarquées, rien à installer ensuite).
#
# Utilisation (depuis la racine du dépôt) :
#   pyinstaller macos/poker_tournament.spec --distpath macos/dist --workpath macos/build
#
# (macos/build_dmg.sh fait tout cela automatiquement, puis génère le .dmg.)
#
# Numéro de version LU DYNAMIQUEMENT depuis version.py (corrigé le
# 2026-09-26 : ce fichier affichait encore "1.2.16" en dur alors que
# version.py était déjà à 1.2.44 — resynchronisé une fois ici, et cette
# lecture dynamique évite qu'un tel écart puisse se reproduire à
# l'avenir, comme le fait déjà macos/build_pkg.sh pour le numéro de
# version du .pkg).
from pathlib import Path
import sys

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de macos/)

sys.path.insert(0, str(ROOT))
import version as _version_module  # noqa: E402

APP_VERSION = _version_module.APP_VERSION

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # help_content.json : contenu de l'aide intégrée (menu Aide / touche
    # F1, voir help_browser.py), lu au runtime via sys._MEIPASS — sans
    # cette entrée, l'aide s'ouvrirait vide dans l'app packagée.
    datas=[(str(ROOT / "help_content.json"), ".")],
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
    name="PokerTournament",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # application graphique (Tkinter) : pas de terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
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
    name="PokerTournament",
)

app = BUNDLE(
    coll,
    name="PokerTournament.app",
    icon=None,
    bundle_identifier="com.pokertournament.gestionnaire",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "Gestionnaire de Tournoi de Poker",
        "CFBundleDisplayName": "Gestionnaire de Tournoi de Poker",
        "CFBundleShortVersionString": APP_VERSION,
        "NSHumanReadableCopyright": "Raj Juganaikloo",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "Utilisée pour prendre une photo de joueur depuis l'application.",
    },
)
