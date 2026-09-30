# 릴리스 한 번에 명령 하나: 자동 업데이트용으로 서명한 설치 파일과 latest.json을 만듭니다.
# 서명 키와 비밀번호는 이 PC 밖으로 나가지 않습니다. 비밀번호는 화면에 보이지 않게 입력받고
# 빌드가 끝나면 환경 변수에서 지웁니다. GitHub 업로드는 하지 않습니다.
param(
  [string]$KeyFile = (Join-Path $env:USERPROFILE '.paperduet-keys\paperduet.key'),
  [string]$Notes = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path -LiteralPath $KeyFile) -or -not (Test-Path -LiteralPath "$KeyFile.pub")) {
  throw "서명 키가 없습니다: $KeyFile`n먼저 만드세요: npx tauri signer generate -w `"$KeyFile`""
}
$version = (Get-Content package.json -Raw | ConvertFrom-Json).version
$repo = 'https://github.com/IM5K58/PaperDuet'
# latest/download는 항상 가장 최근 릴리스를 가리키므로 앱에 넣는 주소가 바뀌지 않습니다.
$updateUrl = "$repo/releases/latest/download/latest.json"
$installerUrl = "$repo/releases/download/$version/PaperDuet_${version}_x64-setup.exe"
if (-not $Notes) { $Notes = "변경 사항: $repo/releases/tag/$version" }

. "$PSScriptRoot/toolchain.ps1"
$secure = Read-Host '서명 키 비밀번호' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $env:TAURI_SIGNING_PRIVATE_KEY = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
  $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  & "$PSScriptRoot/build-release.ps1" -UpdateUrl $updateUrl -PublicKeyFile "$KeyFile.pub" -InstallerUrl $installerUrl -ReleaseNotes $Notes
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
  Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
}

$out = 'src-tauri/target/release/bundle/nsis'
$installer = Get-Item "$out/PaperDuet_${version}_x64-setup.exe"
Write-Host ''
Write-Host "GitHub 릴리스 태그 '$version'에 아래 두 파일을 함께 첨부하세요." -ForegroundColor Green
Write-Host "  $($installer.FullName)"
Write-Host "  $((Get-Item "$out/latest.json").FullName)"
Write-Host "설치 파일 SHA-256: $((Get-FileHash $installer -Algorithm SHA256).Hash)"
Write-Host ("크기: {0:N1}MB" -f ($installer.Length / 1MB))
