$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $projectRoot 'artifacts\m1'
New-Item -ItemType Directory -Force -Path $target | Out-Null
Invoke-WebRequest -Uri 'https://arxiv.org/pdf/2510.12798v1' -OutFile (Join-Path $target 'rex-omni.pdf')
Write-Output 'Golden PDF saved to artifacts/m1/rex-omni.pdf (test input only; not bundled).'
