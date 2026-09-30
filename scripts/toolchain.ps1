# Optional workspace-local Rust installed during development. Does not modify
# user PATH or require a particular developer machine layout.
$projectRoot = Split-Path -Parent $PSScriptRoot
$localCargo = Join-Path $projectRoot '.tools\cargo\bin'
if (Test-Path -LiteralPath (Join-Path $localCargo 'cargo.exe')) {
  $env:CARGO_HOME = Join-Path $projectRoot '.tools\cargo'
  $env:RUSTUP_HOME = Join-Path $projectRoot '.tools\rustup'
  $env:PATH = $localCargo + [IO.Path]::PathSeparator + $env:PATH
}
