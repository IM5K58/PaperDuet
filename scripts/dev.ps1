$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
. "$PSScriptRoot\toolchain.ps1"
npx.cmd tauri dev @args
exit $LASTEXITCODE
