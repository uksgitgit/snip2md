@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\snip2md.exe" (
  start "" ".venv\Scripts\snip2md.exe"
  exit /b 0
)
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" snip2md.py
  exit /b 0
)
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "" pythonw snip2md.py
) else (
  start "" python snip2md.py
)
