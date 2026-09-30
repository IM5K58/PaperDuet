$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $projectRoot 'artifacts\m4'
New-Item -ItemType Directory -Force -Path $target | Out-Null
Invoke-WebRequest -Uri 'https://arxiv.org/html/2603.07952v1' -OutFile (Join-Path $target 'visualad-source.html')
Write-Output 'VisualAD v1 HTML saved to artifacts/m4/visualad-source.html (test input only; not bundled).'
