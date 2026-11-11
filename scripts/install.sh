#!/usr/bin/env bash
# Install CO2-Response on macOS or Linux, for this user, in its own Python environment.
#
#   curl -fsSL https://raw.githubusercontent.com/shrlak/CO2-Response/main/scripts/install.sh | bash
#   ./scripts/install.sh                 # from a downloaded copy of the repository
#   ./scripts/install.sh --terminal-only # without the Qt desktop window (smaller)
#
# Afterwards:  co2response-tui --sim    (the monitor in this terminal)
#              co2response --sim        (the desktop window)
set -euo pipefail

SOURCE="git+https://github.com/shrlak/CO2-Response"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "$HERE" ] && [ -f "$HERE/../pyproject.toml" ]; then SOURCE="$(cd "$HERE/.." && pwd)"; fi
EXTRA="[desktop]"
[ "${1:-}" = "--terminal-only" ] && EXTRA=""

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "Python 3.10 or newer is needed: https://www.python.org/downloads/ (macOS), or your package manager (Linux)." >&2
  exit 1
fi

VENV="${CO2R_VENV:-$HOME/.co2response/venv}"
echo "Installing CO2-Response$EXTRA with $($PY --version) into $VENV"
"$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
if [ -d "$SOURCE" ]; then
  "$VENV/bin/python" -m pip install --quiet "$SOURCE$EXTRA"   # a local copy
else
  "$VENV/bin/python" -m pip install --quiet "co2response$EXTRA @ $SOURCE"
fi

BIN="$HOME/.local/bin"
mkdir -p "$BIN"
for cmd in co2response co2response-tui; do ln -sf "$VENV/bin/$cmd" "$BIN/$cmd"; done
echo
echo "Installed. Try it (no hardware needed):"
echo "  co2response-tui --sim             the monitor full-screen in this terminal"
echo "  co2response --fast-test normal    a whole simulated rebreathing test in a second, with its report"
[ -n "$EXTRA" ] && echo "  co2response --sim                the desktop window"
case ":$PATH:" in *":$BIN:"*) ;; *) echo; echo "Add $BIN to your PATH (e.g. in ~/.zshrc or ~/.bashrc):  export PATH=\"$BIN:\$PATH\"" ;; esac
if [ "$(uname)" = "Linux" ] && ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
  echo "For alarm sounds on Linux:  sudo apt install libportaudio2   (without it the terminal bell is used)"
fi
