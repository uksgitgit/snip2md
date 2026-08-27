#!/bin/bash
# Install Snip2MD into a local venv and add a Launchpad / Applications shortcut.
set -eu
ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3.11+ is not on PATH. Install it from https://www.python.org/downloads/ then re-run." >&2
  exit 1
fi

if ! python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"; then
  echo "Python 3.11+ is required." >&2
  exit 1
fi

echo "Creating .venv …"
python3 -m venv .venv
./.venv/bin/python -m pip install -U pip
./.venv/bin/python -m pip install -e .

if command -v npm >/dev/null 2>&1; then
  echo "Installing Cursor login helper (npm) …"
  npm install
else
  echo "npm not found — Cursor sign-in from the UI needs Node.js. Local OCR still works."
fi

chmod +x "$ROOT/Start Snip2MD.command" "$ROOT/scripts/install.sh"

APP_DIR="${HOME}/Applications/Snip2MD.app"
MACOS_DIR="$APP_DIR/Contents/MacOS"
mkdir -p "$MACOS_DIR"

cat > "$APP_DIR/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Snip2MD</string>
  <key>CFBundleDisplayName</key>
  <string>Snip2MD</string>
  <key>CFBundleIdentifier</key>
  <string>dk.kirlu.snip2md</string>
  <key>CFBundleVersion</key>
  <string>0.1.0</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleExecutable</key>
  <string>Snip2MD</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
EOF

ROOT_Q=$(printf '%q' "$ROOT")
cat > "$MACOS_DIR/Snip2MD" <<EOF
#!/bin/sh
cd $ROOT_Q || exit 1
exec $ROOT_Q/.venv/bin/python3 $ROOT_Q/snip2md.py
EOF
chmod +x "$MACOS_DIR/Snip2MD"

echo ""
echo "Installed. Open Snip2MD from ~/Applications, or double-click Start Snip2MD.command"
echo "First snip: allow Screen Recording for Snip2MD (or Python / Terminal) in"
echo "System Settings → Privacy & Security, then restart Snip2MD."
