param([string]$Installer = '', [string]$Pdf = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (-not $Installer) {
  $Installer = (Get-ChildItem -LiteralPath 'src-tauri\target\release\bundle\nsis' -Filter '*-setup.exe' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $Installer) { throw 'NSIS installer not found.' }
$installDir = Join-Path $projectRoot 'artifacts\설치 스모크\PaperDuet'
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
# NSIS /D must be the last argument and must not be quoted, even with spaces.
$installProcess = Start-Process -FilePath $Installer -ArgumentList @('/S', "/D=$installDir") -WindowStyle Hidden -PassThru -Wait
if ($installProcess.ExitCode -ne 0) { throw "Installer failed: $($installProcess.ExitCode)" }
$appExe = Join-Path $installDir 'paperduet.exe'
if (-not (Test-Path -LiteralPath $appExe)) { throw 'Installed application missing.' }
if ($Pdf) { node scripts/native-smoke.mjs $appExe $Pdf }
else { node scripts/native-smoke.mjs $appExe }
if ($LASTEXITCODE -ne 0) { throw 'Installed application smoke test failed.' }
Write-Output "Smoke installation retained for inspection: $installDir"
Write-Output 'The smoke test closes the app automatically. Launch paperduet.exe directly to keep reading.'
