# Snip2MD

Windows app: snip a screen region (like Snipping Tool) and get GitHub-flavored Markdown on the clipboard.

Local OCR by default (RapidOCR, typically well under a second after warmup). Optional AI polish uses your existing **Cursor** or **Claude** subscription — no pasted API keys.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Requirements

- Windows 10/11
- [Python 3.11+](https://www.python.org/downloads/) (tick **Add python.exe to PATH**)
- Node.js only if you want **Sign in with Cursor** from the app

## Install (makes it a normal Windows program)

In PowerShell, from this folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
```

That creates a local `.venv`, installs the package, and adds **Snip2MD** to the Start Menu and the Desktop. After that, launch from the shortcut or double-click `Start Snip2MD.bat`.

Manual equivalent:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
npm install
.\.venv\Scripts\snip2md.exe
```

## Use it

1. **Change shortcut** if you do not want the default. Combos are saved in `%USERPROFILE%\.snip2md\settings.json`.
2. Press the shortcut (or click **+**).
3. Drag a rectangle. Release. Markdown is copied from local OCR.
4. Optional: tick **AI polish** and pick Auto / Cursor / Claude.

**Esc** cancels a snip.

OCR models download once into `%USERPROFILE%\.snip2md\models`.

## Privacy

RapidOCR and Windows OCR never leave the PC. AI polish sends the snip to the model under **your** Cursor or Claude login. Do not snip password managers, bank screens, or anything you would not paste into those products.

## CLI

```text
python snip2md.py          # same as the window
python snip2md.py status
python snip2md.py login
python snip2md.py cli      # hotkey only, no window
```

## License

[MIT](LICENSE)
