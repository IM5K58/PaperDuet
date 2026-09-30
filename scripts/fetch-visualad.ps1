$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$target = Join-Path $projectRoot 'artifacts\m3'
New-Item -ItemType Directory -Force -Path $target | Out-Null
$pdfPath = Join-Path $target 'visualad.pdf'
Invoke-WebRequest -Uri 'https://arxiv.org/pdf/2603.07952v1' -OutFile $pdfPath
$expectedHash = '15D9F9ADEB136F44BA0218BE87DACDC6D06643A8BB78F07449AE8E21BBFBD0FE'
if ((Get-FileHash -LiteralPath $pdfPath -Algorithm SHA256).Hash -ne $expectedHash) { throw 'VisualAD PDF differs from the reviewed v1 regression input.' }
Write-Output 'VisualAD v1 saved to artifacts/m3/visualad.pdf (test input only; not bundled).'
