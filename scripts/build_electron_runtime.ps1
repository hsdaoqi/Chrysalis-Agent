param(
    [string]$OutDir
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -e ".[build]"

if (-not $OutDir) {
    $OutDir = Join-Path $root "desktop-electron\dist\runtime"
}

$workDir = Join-Path $root "desktop-electron\.pyinstaller-runtime"
$buildDir = Join-Path $workDir "build"
$specDir = Join-Path $workDir "spec"

foreach ($dir in @($OutDir, $buildDir, $specDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

python -m PyInstaller `
    --onefile `
    --noconfirm `
    --clean `
    --windowed `
    --name chrysalis-runtime `
    --distpath $OutDir `
    --workpath $buildDir `
    --specpath $specDir `
    --hidden-import configs.config `
    --hidden-import chrysalis.tools.file_tools `
    --hidden-import chrysalis.tools.web_tools `
    --hidden-import chrysalis.tools.code_tools `
    --hidden-import chrysalis.tools.agent_tools `
    --hidden-import chrysalis.tools.vision_tools `
    --hidden-import chrysalis.electron_runtime `
    --hidden-import chrysalis.electron_runtime.tasks `
    --hidden-import chrysalis.electron_runtime.settings `
    --hidden-import chrysalis.electron_runtime.sessions `
    --hidden-import chrysalis.electron_runtime.workspace `
    --hidden-import chrysalis.electron_runtime.gateway `
    --hidden-import chrysalis.electron_runtime.cron `
    --hidden-import chrysalis.electron_runtime.review `
    chrysalis\electron_runtime_main.py

Write-Host ""
Write-Host "Built runtime: $(Join-Path $OutDir 'chrysalis-runtime.exe')"
