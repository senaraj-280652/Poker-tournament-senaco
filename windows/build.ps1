# Construit PokerTournament.exe (PyInstaller) puis l'installateur .msi
# (WiX Toolset v5) à partir des sources Python du dépôt.
#
# Prérequis sur la machine Windows qui exécute ce script :
#   - Python 3.9 ou plus récent, dans le PATH (commande "py" ou "python")
#   - .NET SDK 6 ou plus récent (pour installer l'outil "wix" via
#     `dotnet tool install` — télécharger sur https://dotnet.microsoft.com
#     si besoin ; gratuit, quelques minutes)
#
# Utilisation : ouvrez PowerShell à la racine du dépôt, puis :
#   .\windows\build.ps1
#
# Résultat : windows\dist\PokerTournament.msi

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # racine du dépôt (parent de windows/)
Set-Location $root

function Get-PythonCommand {
    if (Get-Command py -ErrorAction SilentlyContinue) { return "py -3" }
    if (Get-Command python -ErrorAction SilentlyContinue) { return "python" }
    throw "Python introuvable. Installez-le depuis https://www.python.org/downloads/ (cochez 'Add to PATH') puis relancez ce script."
}

Write-Host "== 1/5 : environnement virtuel Python ==" -ForegroundColor Cyan
$pythonCmd = Get-PythonCommand
if (Test-Path "windows\venv") { Remove-Item -Recurse -Force "windows\venv" }
Invoke-Expression "$pythonCmd -m venv windows\venv"
$venvPython = "windows\venv\Scripts\python.exe"

Write-Host "== 2/5 : installation des dépendances (PyInstaller, openpyxl, opencv, Pillow) ==" -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -r windows\requirements.txt

Write-Host "== 3/5 : génération de PokerTournament.exe (PyInstaller) ==" -ForegroundColor Cyan
if (Test-Path "windows\dist") { Remove-Item -Recurse -Force "windows\dist" }
if (Test-Path "windows\build") { Remove-Item -Recurse -Force "windows\build" }

# Verrou anti-copie (voir license.py) : si un secret de licence est
# disponible dans la variable d'environnement LICENSE_SECRET (ou déjà
# présent localement dans _license_secret.py), on l'embarque dans
# l'exécutable pour que le poste installé exige une activation. Sans lui,
# l'exécutable démarre sans jamais demander de licence (comme en
# développement) — pratique pour des builds de test, à éviter pour une
# vraie distribution.
$injectedSecret = $false
if (-not (Test-Path "_license_secret.py") -and $env:LICENSE_SECRET) {
    "SECRET = '$($env:LICENSE_SECRET)'" | Out-File -Encoding utf8 "_license_secret.py"
    $injectedSecret = $true
}
if (Test-Path "_license_secret.py") {
    Write-Host "   (verrou de licence activé pour ce build)" -ForegroundColor DarkGray
} else {
    Write-Host "   ATTENTION : _license_secret.py absent -> ce build ne demandera jamais d'activation." -ForegroundColor Yellow
}

& $venvPython -m PyInstaller windows\poker_tournament.spec --distpath windows\dist --workpath windows\build --noconfirm
if ($injectedSecret) { Remove-Item -Force "_license_secret.py" }
if (-not (Test-Path "windows\dist\PokerTournament.exe")) {
    throw "PyInstaller n'a pas produit windows\dist\PokerTournament.exe — voir le journal ci-dessus."
}

Write-Host "== 4/5 : installation de l'outil WiX (si nécessaire) ==" -ForegroundColor Cyan
if (-not (Get-Command dotnet -ErrorAction SilentlyContinue)) {
    throw ".NET SDK introuvable. Installez-le depuis https://dotnet.microsoft.com/download puis relancez ce script."
}
if (-not (Get-Command wix -ErrorAction SilentlyContinue)) {
    dotnet tool install --global wix --version 5.0.2
    # Recharge le PATH de la session courante pour voir l'outil "wix"
    # tout juste installé par dotnet (dans %USERPROFILE%\.dotnet\tools).
    $dotnetTools = Join-Path $env:USERPROFILE ".dotnet\tools"
    if ($env:PATH -notlike "*$dotnetTools*") { $env:PATH = "$env:PATH;$dotnetTools" }
}
wix extension add WixToolset.UI.wixext/5.0.2 --global 2>$null

Write-Host "== 5/5 : génération de PokerTournament.msi (WiX) ==" -ForegroundColor Cyan
wix build windows\app.wxs `
    -arch x64 `
    -ext WixToolset.UI.wixext `
    -d Dist=windows\dist `
    -out windows\dist\PokerTournament.msi

Write-Host ""
if (Test-Path "windows\dist\PokerTournament.msi") {
    Write-Host "Terminé : windows\dist\PokerTournament.msi" -ForegroundColor Green
} else {
    throw "La génération du .msi a échoué — voir le journal ci-dessus."
}
