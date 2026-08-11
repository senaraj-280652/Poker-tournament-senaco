# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller : génère un unique PokerTournament.exe (Windows) à
# partir de main.py, autonome (Tkinter + toutes les dépendances
# optionnelles embarquées, rien à installer ensuite).
#
# Utilisation (depuis la racine du dépôt) :
#   pyinstaller windows/poker_tournament.spec --distpath windows/dist --workpath windows/build
#
# (windows/build.ps1 fait tout cela automatiquement.)
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de windows/)

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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PokerTournament",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # application graphique (Tkinter) : pas de fenêtre console
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
