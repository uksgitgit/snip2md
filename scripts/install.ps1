# Install Snip2MD into a local venv and pin a Start Menu + Desktop shortcut.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python 3.11+ is not on PATH. Install it from https://www.python.org/downloads/ then re-run."
}

Write-Host "Creating .venv …"
python -m venv .venv
$pip = Join-Path $Root ".venv\Scripts\pip.exe"
$exe = Join-Path $Root ".venv\Scripts\snip2md.exe"
& $pip install -U pip
& $pip install -e .

$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($npm) {
    Write-Host "Installing Cursor login helper (npm) …"
    npm install
} else {
    Write-Host "npm not found — Cursor sign-in from the UI needs Node.js. Local OCR still works."
}

function New-SnipShortcut([string]$Path) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $exe
    $link.WorkingDirectory = $Root
    $link.WindowStyle = 7
    $link.Description = "Snip a screen region to Markdown"
    $link.Save()
}

$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $startDir | Out-Null
New-SnipShortcut (Join-Path $startDir "Snip2MD.lnk")
New-SnipShortcut (Join-Path $env:USERPROFILE "Desktop\Snip2MD.lnk")

Write-Host ""
Write-Host "Installed. Start Menu and Desktop shortcuts point at:"
Write-Host "  $exe"
Write-Host "Launch with:  $exe"
Write-Host "or double-click Start Snip2MD.bat"
