# -*- mode: python ; coding: utf-8 -*-
# Spec PyInstaller DÉDIÉ à la version « TEST » (demande du 2026-09-12) :
# permet d'installer, sur un poste où la v1.2.38 tourne déjà, une seconde
# application totalement séparée et clairement identifiable — pour
# comparer le code de développement actuel à la version installée SANS
# jamais toucher à celle-ci. Voir windows/README.md, section
# « Version de TEST », et windows/app-test.wxs (installateur associé).
#
# Copie de windows/poker_tournament.spec avec exactement 2 différences :
#   1. name="PokerTournamentTest" (au lieu de "PokerTournament") — pour
#      que PyInstaller génère un dossier windows/dist/PokerTournamentTest/
#      totalement séparé de windows/dist/PokerTournament/ (la build de
#      production), sans jamais risquer d'écraser ou de mélanger les deux
#      si elles sont générées l'une après l'autre sans nettoyage entre les
#      deux (voir windows/build.ps1, qui supprime windows/dist AVANT
#      chaque build — mais un dist déjà présent d'un précédent build de
#      l'AUTRE ligne ne doit pas pour autant être menacé par un script
#      dédié à la ligne TEST qui ne le supprimerait pas).
#   2. datas ajoute windows/assets/TEST_BUILD_MARKER (voir main.py:
#      _is_test_build) — seule différence FONCTIONNELLE avec la
#      production : c'est ce fichier, et lui seul, qui fait afficher
#      "[TEST]" dans le titre de fenêtre et "À propos" une fois compilé.
#   3. datas ajoute AUSSI, s'il existe, windows/assets/TEST_BUILD_NUMBER
#      (demande du 2026-09-14 : identifier chaque build TEST par un
#      numéro, ex. "[TEST 3]", sans jamais modifier version.py ni coder
#      un numéro en dur ici). Ce fichier est généré par .github/
#      workflows/build-msi-test.yml juste avant l'appel à PyInstaller,
#      avec le contenu ${{ github.run_number }} (compteur qui
#      s'incrémente tout seul à chaque déclenchement de CE workflow).
#      Absent en local (build manuel sans passer par le workflow) : dans
#      ce cas on omet volontairement l'entrée `datas` correspondante —
#      PyInstaller échouerait sur un fichier source inexistant — et
#      main.py._test_build_number() retombe proprement sur None, donc
#      sur le libellé générique "[TEST]" (voir main.py:_test_build_label).
#
# Toute correction apportée ici (icône, dépendances cachées...) doit être
# reportée manuellement dans poker_tournament.spec si elle s'applique
# aussi à la production, et réciproquement — les deux fichiers ne sont
# PAS générés l'un à partir de l'autre.
#
# Utilisation (depuis la racine du dépôt) :
#   pyinstaller windows/poker_tournament-test.spec --distpath windows/dist --workpath windows/build
from pathlib import Path

block_cipher = None
ROOT = Path(SPECPATH).resolve().parent  # racine du dépôt (parent de windows/)
ICON = Path(SPECPATH) / "assets" / "app_icon.ico"
# Même icône que la production pour l'instant (aucune demande d'icône
# distincte) — la distinction se fait par le dossier d'installation, le
# nom des raccourcis (voir app-test.wxs) et le marqueur "[TEST]" affiché
# une fois l'application ouverte (voir main.py: _is_test_build).
TEST_MARKER = Path(SPECPATH) / "assets" / "TEST_BUILD_MARKER"
# Optionnel (voir point 3 de l'en-tête ci-dessus) : n'existe que lorsque
# ce spec est invoqué depuis le workflow GitHub Actions dédié, jamais en
# local sans action explicite — d'où la vérification .exists() avant de
# l'ajouter à `datas`.
TEST_BUILD_NUMBER = Path(SPECPATH) / "assets" / "TEST_BUILD_NUMBER"

_datas = [
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

splash = Splash(
    str(Path(SPECPATH) / "assets" / "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    exclude_binaries=True,
    name="PokerTournamentTest",
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
    icon=str(ICON),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    splash.binaries,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PokerTournamentTest",
)
