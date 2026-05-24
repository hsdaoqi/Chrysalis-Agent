param(
    [switch]$OneDir,
    [switch]$Shortcut
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

python -m pip install -e ".[desktop,build]"

$mode = if ($OneDir) { "--onedir" } else { "--onefile" }
$sep = [IO.Path]::PathSeparator
$qmlSource = Join-Path $root "chrysalis\desktop\qml"
$addData = "$qmlSource${sep}qml"
$iconPath = Join-Path $root "assets\images\chrysalis-icon.ico"
$addIcon = "$iconPath${sep}."

python -m PyInstaller `
    $mode `
    --noconfirm `
    --clean `
    --windowed `
    --name Chrysalis `
    --add-data $addData `
    --add-data $addIcon `
    --collect-all PySide6 `
    --hidden-import chrysalis.tools.file_tools `
    --hidden-import chrysalis.tools.web_tools `
    --hidden-import chrysalis.tools.code_tools `
    --hidden-import chrysalis.tools.agent_tools `
    --hidden-import chrysalis.tools.vision_tools `
    --hidden-import configs.config `
    chrysalis\desktop\main.py

Write-Host ""
$exePath = if ($OneDir) {
    Join-Path $root "dist\Chrysalis\Chrysalis.exe"
} else {
    Join-Path $root "dist\Chrysalis.exe"
}

if ($Shortcut) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "Chrysalis.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $exePath
    $shortcut.WorkingDirectory = Split-Path -Parent $exePath
    $shortcut.Description = "Chrysalis Desktop"
    $shortcut.Save()
    Write-Host "Shortcut: $shortcutPath"
}

Write-Host "Built: $exePath"
