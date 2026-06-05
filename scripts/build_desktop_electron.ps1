param(
    [switch]$InstallNodeDeps
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -e ".[build]"

$desktopRoot = Join-Path $root "desktop-electron"
Set-Location $desktopRoot

$env:npm_config_cache = Join-Path $desktopRoot ".npm-cache"
if ($InstallNodeDeps -or -not (Test-Path (Join-Path $desktopRoot "node_modules"))) {
    npm install
}

npm run package:win
