#!/usr/bin/env bash
# Trance native desktop application launcher.
set -e
cd "$(dirname "$(readlink -f "$0")")"

if [ "${1:-}" = "--install" ]; then
  mkdir -p "$HOME/.local/share/applications"
  cat > "$HOME/.local/share/applications/trance.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=Trance
Comment=Rhythm and media session app
Exec="$PWD/trance.sh"
Icon=$PWD/data/icon-512.png
Terminal=false
Categories=AudioVideo;Game;
DESK
  echo "Installed Trance in your application menu."
  exit 0
fi

command -v python3 >/dev/null 2>&1 || { echo "Python 3 is required." >&2; exit 1; }
python3 -c 'from PySide6.QtWebEngineWidgets import QWebEngineView' 2>/dev/null || {
  echo "PySide6 with Qt WebEngine is required. Install the PySide6 desktop package for your system." >&2
  exit 1
}
exec python3 app.py
