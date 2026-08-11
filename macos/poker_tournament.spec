# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller : génère PokerTournament.app (macOS) à partir de
# main.py, autonome (Tkinter + toutes les dépendances optionnelles
# embarquées, rien à installer ensuite).
#
# Utilisation (depuis la racine du dépôt) :
#   pyinstaller macos/poker_tournament.spec --distpath macos/dist --workpath macos/build
#
# (macos/build_dmg.sh fait tout cela automatiquement, puis génère le .dmg.)
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de macos/)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
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
    version="1.0.0",
    info_plist={
        "CFBundleName": "Gestionnaire de Tournoi de Poker",
        "CFBundleDisplayName": "Gestionnaire de Tournoi de Poker",
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "Raj Juganaikloo",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "Utilisée pour prendre une photo de joueur depuis l'application.",
    },
)
