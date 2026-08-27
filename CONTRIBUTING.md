# Contributing

Windows and macOS. Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
npm install
.\.venv\Scripts\snip2md.exe
```

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
npm install
.venv/bin/python snip2md.py
```

- Keep OCR local by default. Optional AI polish must keep using Cursor / Claude **subscription login**, never a pasted API key.
- Do not commit `%USERPROFILE%\.snip2md\` / `~/.snip2md`, `.venv`, or `node_modules`.
- Windows hotkeys go through `RegisterHotKey` on the dedicated listener thread in `snip2md.py`. Do not handle `WM_HOTKEY` in a Python `WndProc` (that crashes the interpreter).
- macOS hotkeys go through Carbon `RegisterEventHotKey` in `mac_host.py`. Do not add a CGEventTap / pynput keylogger.
