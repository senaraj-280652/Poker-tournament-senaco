# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller : génère PokerTournament.app (macOS) à partir de
# main.py, autonome (Tkinter + toutes les dépendances optionnelles
# embarquées, rien à installer ensuite).
#
# Utilisation (depuis la racine du dépôt) :
#   pyinstaller macos/poker_tournament.spec --distpath macos/dist --workpath macos/build
#
# (macos/build_dmg.sh fait tout cela automatiquement, puis génère le .dmg.)
import re
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de macos/)

# Numéro de version lu depuis version.py (source unique de vérité, voir
# sa docstring) — jamais recopié en dur ici (demande du 2026-10-22, suite
# au même oubli déjà corrigé côté Windows via windows/read_version.py :
# ce fichier était resté bloqué à "1.2.16" malgré de nombreuses versions
# distribuées depuis). Un spec PyInstaller est lui-même du code Python
# exécuté par `pyinstaller`, donc cette lecture se fait directement ici,
# sans script séparé ni risque d'échappement de shell.
_version_text = (ROOT / "version.py").read_text(encoding="utf-8")
_version_match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _version_text)
if not _version_match:
    raise SystemExit("APP_VERSION introuvable dans version.py")
APP_VERSION = _version_match.group(1)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    # help_content.json : contenu de l'aide intégrée (menu Aide / touche
    # F1, voir help_browser.py), lu au runtime via sys._MEIPASS — sans
    # cette entrée, l'aide s'ouvrirait vide dans l'app packagée.
    datas=[(str(ROOT / "help_content.json"), ".")],
    # AppKit (PyObjC) : import conditionnel dans main.py (voir
    # NSPASTEBOARD_AVAILABLE, copy_file_to_clipboard — bouton "Envoi
    # WhatsApp") que PyInstaller peut ne pas détecter automatiquement
    # par analyse statique seule, listé explicitement par précaution.
    # keyring.backends.macOS : chargement dynamique du backend Trousseau
    # (device_secret.py, sécurité du 2026-11-04) — détecté correctement
    # par PyInstaller même sans cette entrée (vérifié par un vrai build
    # onefile de test), mais listé explicitement par précaution, même
    # principe qu'AppKit ci-dessous.
    hiddenimports=["AppKit", "keyring.backends.macOS"],
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
    # Icône Senaco dédiée (demande du 2026-10-22) — générée depuis
    # windows/assets/logo_sena_source.png via sips+iconutil (outils macOS
    # gratuits) ; remplace l'icône générique PyInstaller utilisée jusque-
    # là (icon-windowed.icns). Voir macos/assets/app_icon.icns.
    icon=str(ROOT / "macos" / "assets" / "app_icon.icns"),
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
