param(
  [Parameter(Mandatory=$true)][string]$UpdateUrl,
  [Parameter(Mandatory=$true)][string]$PublicKeyFile,
  [Parameter(Mandatory=$true)][string]$InstallerUrl,
  [string]$ReleaseNotes = '',
  [switch]$DisableCli
)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (([uri]$UpdateUrl).Scheme -ne 'https' -or ([uri]$InstallerUrl).Scheme -ne 'https') { throw 'Update and installer URLs must use HTTPS.' }
if (-not $env:TAURI_SIGNING_PRIVATE_KEY) { throw 'Set TAURI_SIGNING_PRIVATE_KEY to your private signing key path. Do not commit the key.' }
$previousUpdateUrl=$env:PAPERDUET_UPDATE_URL
$previousUpdateKey=$env:PAPERDUET_UPDATE_PUBLIC_KEY
try {
$env:PAPERDUET_UPDATE_URL=$UpdateUrl
$env:PAPERDUET_UPDATE_PUBLIC_KEY=(Get-Content -LiteralPath $PublicKeyFile -Raw).Trim()
if (-not $env:PAPERDUET_UPDATE_PUBLIC_KEY) { throw 'Public key is empty.' }
New-Item -ItemType Directory -Force -Path build | Out-Null
# The updater plugin refuses to load (and the app to start) when plugins.updater
# lacks pubkey, so the key and endpoint go into the config as well as the binary.
$updateConfig=@{bundle=@{createUpdaterArtifacts=$true};plugins=@{updater=@{pubkey=$env:PAPERDUET_UPDATE_PUBLIC_KEY;endpoints=@($UpdateUrl);windows=@{installMode='passive'}}}}
[IO.File]::WriteAllText((Join-Path (Get-Location) 'build/update-tauri.json'),($updateConfig|ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
& "$PSScriptRoot/build-sidecar.ps1" -DisableCli:$DisableCli
npx.cmd tauri build --bundles nsis --config build/update-tauri.json
if ($LASTEXITCODE -ne 0) { throw 'Signed installer build failed.' }
$releaseVersion=(Get-Content package.json -Raw | ConvertFrom-Json).version
$releaseInstaller=Join-Path (Get-Location) "src-tauri/target/release/bundle/nsis/PaperDuet_${releaseVersion}_x64-setup.exe"
$releaseSignature=(Get-Content -LiteralPath "$releaseInstaller.sig" -Raw).Trim()
$manifest=@{version=$releaseVersion;pub_date=[DateTime]::UtcNow.ToString('o');notes=$ReleaseNotes;platforms=@{'windows-x86_64'=@{url=$InstallerUrl;signature=$releaseSignature}}}
[IO.File]::WriteAllText((Join-Path (Split-Path -Parent $releaseInstaller) 'latest.json'),($manifest|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
Write-Output 'Built signed installer, public signature, and latest.json. Nothing has been published.'
} finally {
  $env:PAPERDUET_UPDATE_URL=$previousUpdateUrl
  $env:PAPERDUET_UPDATE_PUBLIC_KEY=$previousUpdateKey
}
