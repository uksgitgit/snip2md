# Contributing

Windows only. Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -e .
npm install
.\.venv\Scripts\snip2md.exe
```

- Keep OCR local by default. Optional AI polish must keep using Cursor / Claude **subscription login**, never a pasted API key.
- Do not commit `%USERPROFILE%\.snip2md\`, `.venv`, or `node_modules`.
- Hotkeys go through `RegisterHotKey` on the dedicated listener thread in `snip2md.py`. Do not handle `WM_HOTKEY` in a Python `WndProc` (that crashes the interpreter).
