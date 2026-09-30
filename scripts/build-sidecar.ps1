param([switch]$DisableCli)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Run scripts/setup.ps1 first.' }
$policyPath = Join-Path $projectRoot 'build\build_policy.json'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $policyPath) | Out-Null
[IO.File]::WriteAllText($policyPath, (@{ cli_enabled = -not $DisableCli.IsPresent } | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
node scripts/build-export.mjs
if ($LASTEXITCODE -ne 0) { throw 'Export reader build failed.' }
# The sample paper is kept out of the public repository; bundle it only when present locally.
$sampleFixture = Join-Path $projectRoot 'fixtures\rex-omni.blocks.json'
$sampleData = if (Test-Path -LiteralPath $sampleFixture) { @('--add-data', "$sampleFixture;fixtures") } else { @() }
& $pythonExe -m PyInstaller --noconfirm --clean --onedir --console --name paperduet-backend --paths (Join-Path $projectRoot 'backend') --distpath src-tauri/resources/backend-build --workpath build/pyinstaller --specpath build @sampleData --add-data "$(Join-Path $projectRoot 'backend\paperduet\schema.sql');paperduet" --add-data "$(Join-Path $projectRoot 'backend\paperduet\migration_2.sql');paperduet" --add-data "$(Join-Path $projectRoot 'backend\paperduet\migration_3.sql');paperduet" --add-data "$(Join-Path $projectRoot 'backend\paperduet\prompts');paperduet/prompts" --add-data "$(Join-Path $projectRoot 'backend\paperduet\migration_4.sql');paperduet" --add-data "$policyPath;paperduet" --add-data "$(Join-Path $projectRoot 'backend\paperduet\migration_5.sql');paperduet" --add-data "$(Join-Path $projectRoot 'backend\paperduet\migration_6.sql');paperduet" --add-data "$(Join-Path $projectRoot 'backend\resources');resources" --hidden-import keyring.backends.Windows --hidden-import uvicorn.logging --hidden-import uvicorn.loops.asyncio --hidden-import uvicorn.protocols.http.h11_impl --hidden-import uvicorn.lifespan.on backend/entrypoint.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
$sourceDir = [IO.Path]::GetFullPath((Join-Path $projectRoot 'src-tauri\resources\backend-build\paperduet-backend'))
$targetDir = [IO.Path]::GetFullPath((Join-Path $projectRoot 'src-tauri\resources\backend'))
$allowedRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'src-tauri\resources')) + [IO.Path]::DirectorySeparatorChar
if (-not $targetDir.StartsWith($allowedRoot) -or -not $sourceDir.StartsWith($allowedRoot)) { throw 'Unsafe build output path.' }
if (Test-Path -LiteralPath $targetDir) { Remove-Item -LiteralPath $targetDir -Recurse -Force }
Move-Item -LiteralPath $sourceDir -Destination $targetDir
