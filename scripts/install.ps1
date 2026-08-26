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
    $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
    if (-not (Test-Path $pythonw)) {
        $pythonw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
    }
    if (-not $pythonw) {
        $pythonw = $exe
    }
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($Path)
    $link.TargetPath = $pythonw
    $link.Arguments = "`"$Root\snip2md.py`""
    $link.WorkingDirectory = $Root
    $link.WindowStyle = 1
    $link.Description = "Snip a screen region to Markdown"
    $ico = Join-Path $Root "share\snip2md.ico"
    if (Test-Path $ico) {
        $link.IconLocation = "$ico,0"
    }
    $link.Save()
}

$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
New-Item -ItemType Directory -Force -Path $startDir | Out-Null
New-SnipShortcut (Join-Path $startDir "Snip2MD.lnk")
New-SnipShortcut (Join-Path $env:USERPROFILE "Desktop\Snip2MD.lnk")

Write-Host ""
Write-Host "Installed. Start Menu and Desktop shortcuts launch pythonw (no console)."
Write-Host "Or double-click Start Snip2MD.bat / Start Snip2MD.vbs"
