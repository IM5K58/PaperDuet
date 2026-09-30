param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
  & $Python -m venv .venv
  if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required to build the sidecar.' }
}
& '.venv\Scripts\python.exe' -m pip install -r backend/requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
npm ci
if ($LASTEXITCODE -ne 0) { throw 'Node dependency installation failed.' }
& "$PSScriptRoot\build-sidecar.ps1"
