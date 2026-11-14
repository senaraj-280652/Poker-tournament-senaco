# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller DÉDIÉ à la version « TEST » macOS (demande du
# 2026-11-13, préparation d'une version TEST macOS Intel distincte de
# Senaco 1.2.45) : permet d'installer, sur un Mac où la version stable
# tourne déjà, une seconde application totalement séparée et clairement
# identifiable — pour comparer le code de développement actuel à la
# version installée SANS jamais toucher à celle-ci. Même principe déjà
# en place côté Windows (windows/poker_tournament-test.spec).
#
# Copie de macos/poker_tournament.spec avec exactement ces différences :
#   1. name="PokerTournament-TEST" (au lieu de "PokerTournament") pour
#      l'exécutable/COLLECT, et app bundle "PokerTournament-TEST.app" —
#      pour que PyInstaller/Finder ne puissent jamais confondre ce
#      résultat avec celui d'un build de production généré avant ou
#      après, dans le même dossier macos/dist/.
#   2. bundle_identifier="com.pokertournament.gestionnaire.test" (au
#      lieu de "com.pokertournament.gestionnaire") — un identifiant
#      différent garantit, du point de vue de macOS/Launch Services,
#      qu'il s'agit de DEUX applications totalement distinctes : glisser
#      "PokerTournament-TEST.app" dans /Applications ne peut jamais
#      remplacer ni perturber /Applications/PokerTournament.app (nom de
#      fichier ET identifiant de bundle différents tous les deux).
#   3. CFBundleName/CFBundleDisplayName affichent explicitement « (TEST) »
#      — visible dans le Dock/Finder, jamais confondu visuellement avec
#      la version stable.
#   4. datas ajoute macos/assets/TEST_BUILD_MARKER (voir main.py:
#      _is_test_build) — seule différence FONCTIONNELLE avec la
#      production : c'est ce fichier, et lui seul, qui fait afficher
#      "[TEST]" dans le titre de fenêtre et "À propos" une fois compilé,
#      ET qui active l'isolation automatique de ~/.poker_tournament vers
#      ~/SenacoTEST (voir le bloc dédié tout en haut de main.py) ainsi
#      que le nom de service distinct "Senaco-TEST" du Trousseau macOS
#      (voir device_secret.py).
#
# Toute correction apportée ici (dépendances cachées, icône...) doit être
# reportée manuellement dans poker_tournament.spec si elle s'applique
# aussi à la production, et réciproquement — les deux fichiers ne sont
# PAS générés l'un à partir de l'autre.
#
# Utilisation (depuis la racine du dépôt) :
#   pyinstaller macos/poker_tournament-test.spec --distpath macos/dist --workpath macos/build
# (macos/build_dmg_test.sh fait tout cela automatiquement, puis génère le .dmg TEST.)
import re
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de macos/)
TEST_MARKER = Path(SPECPATH) / "assets" / "TEST_BUILD_MARKER"

_version_text = (ROOT / "version.py").read_text(encoding="utf-8")
_version_match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', _version_text)
if not _version_match:
    raise SystemExit("APP_VERSION introuvable dans version.py")
APP_VERSION = _version_match.group(1)

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "help_content.json"), "."),
        # Seule différence fonctionnelle avec poker_tournament.spec —
        # voir l'en-tête de ce fichier et main.py: _is_test_build.
        (str(TEST_MARKER), "."),
    ],
    # "_cffi_backend" : dependance cachee de nacl._sodium.abi3.so (import
    # Python fait depuis le code C du module compile, invisible a la fois
    # a l'analyse de bytecode de PyInstaller et a son analyse des
    # dependances Mach-O via otool -- confirme par diagnostic du
    # 2026-11-13 ; voir le compte rendu correspondant. hook-nacl.py de
    # _pyinstaller_hooks_contrib ne la declare pas (ecrit pour une
    # disposition de PyNaCl anterieure a nacl/_sodium.abi3.so).
    hiddenimports=["AppKit", "keyring.backends.macOS", "_cffi_backend"],
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
    name="PokerTournament-TEST",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    name="PokerTournament-TEST",
)

app = BUNDLE(
    coll,
    name="PokerTournament-TEST.app",
    icon=str(ROOT / "macos" / "assets" / "app_icon.icns"),
    bundle_identifier="com.pokertournament.gestionnaire.test",
    version=APP_VERSION,
    info_plist={
        "CFBundleName": "Gestionnaire de Tournoi de Poker (TEST)",
        "CFBundleDisplayName": "Gestionnaire de Tournoi de Poker (TEST)",
        "CFBundleShortVersionString": APP_VERSION,
        "NSHumanReadableCopyright": "Raj Juganaikloo",
        "NSHighResolutionCapable": True,
        "NSCameraUsageDescription": "Utilisée pour prendre une photo de joueur depuis l'application.",
    },
)
