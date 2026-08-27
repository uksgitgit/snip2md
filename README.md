# Snip2MD

Windows and macOS app: snip a screen region (like Snipping Tool) and get GitHub-flavored Markdown on the clipboard.

Local OCR by default (RapidOCR, typically well under a second after warmup). Optional AI polish uses your existing **Cursor** or **Claude** subscription — no pasted API keys.

**Repo:** [github.com/uksgitgit/snip2md](https://github.com/uksgitgit/snip2md)

<video src="./share/snip2md-how-it-works.mp4" controls width="720"></video>

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

## Requirements

- Windows 10/11 or macOS 12+
- [Python 3.11+](https://www.python.org/downloads/) (on Windows, tick **Add python.exe to PATH**; on Mac, the python.org installer includes Tk — Homebrew needs `brew install python-tk`)
- Node.js only if you want **Sign in with Cursor** from the app

## Install

### Windows

In PowerShell, from this folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
```

That creates a local `.venv`, installs the package, and adds **Snip2MD** to the Start Menu and the Desktop. After that, launch from the shortcut or double-click `Start Snip2MD.bat`.

### macOS

From this folder:

```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

That creates a local `.venv` and a **Snip2MD** app in `~/Applications`. You can also double-click `Start Snip2MD.command`.

The first snip asks macOS for **Screen Recording**. Allow Snip2MD (or Python / Terminal, depending on how you launched it) under System Settings → Privacy & Security, then restart Snip2MD.

Manual equivalent (both platforms):

```text
python3 -m venv .venv
.venv/bin/pip install -e .          # Windows: .\.venv\Scripts\pip install -e .
npm install                         # optional, Cursor sign-in
.venv/bin/python snip2md.py         # Windows: .\.venv\Scripts\snip2md.exe
```

Optional on Mac: `pip install pyobjc-framework-Vision` so Apple Vision can refine Danish letters, the same way Windows OCR does.

## Use it

1. **Change shortcut** if you do not want the default (`Ctrl+Alt+M` on Windows, Control+Option+M on Mac). Combos are saved in `~/.snip2md/settings.json` (`%USERPROFILE%\.snip2md\settings.json` on Windows).
2. Press the shortcut (or click **+**).
3. Drag a rectangle. Release. Markdown is copied as soon as RapidOCR finishes (usually well under a second). System OCR may refine Danish letters a moment later if it reads them better.
4. Optional: tick **AI polish** and pick Auto / Cursor / Claude.

**Esc** cancels a snip.

OCR models download once into `~/.snip2md/models`.

## Privacy

RapidOCR and system OCR never leave the machine. AI polish sends the snip to the model under **your** Cursor or Claude login. Do not snip password managers, bank screens, or anything you would not paste into those products.

## CLI

```text
python snip2md.py          # same as the window
python snip2md.py status
python snip2md.py login
python snip2md.py cli      # hotkey only, no window
```

## License

[MIT](LICENSE)
