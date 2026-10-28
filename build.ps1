# Build a portable Windows folder.
# Run from this directory:
#   powershell -ExecutionPolicy Bypass -File .\build.ps1

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path ".\app.py")) { throw "app.py not found" }
if (-not (Test-Path ".\assets\icon.ico")) { throw "assets\icon.ico not found" }

pyinstaller `
  --uac-admin `
  --noconfirm `
  --clean `
  --onedir `
  --windowed `
  --name "WuwaAutoChest" `
  --icon "assets\icon.ico" `
  --version-file "version_info.txt" `
  --add-data "assets;assets" `
  --collect-submodules comtypes.gen `
  --exclude-module numpy `
  --exclude-module psutil `
  "app.py"

$old = Join-Path $PSScriptRoot "dist\WuwaAutoChest\WuwaAutoChest.exe"
$new = Join-Path $PSScriptRoot "dist\WuwaAutoChest\WuwaTappo 小助手.exe"
if (Test-Path -LiteralPath $old) {
  if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Force }
  Rename-Item -LiteralPath $old -NewName "WuwaTappo 小助手.exe"
}

Write-Host ""
Write-Host "Build finished: $PSScriptRoot\dist\WuwaAutoChest\WuwaTappo 小助手.exe" -ForegroundColor Green
Write-Host "Zip the whole 'dist\WuwaAutoChest' folder to distribute." -ForegroundColor Yellow
