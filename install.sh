#!/usr/bin/env bash
# Family Locator installer. Safe to run again: it only adds what's missing.
#
#   ./install.sh               install: venv + packages, data/ folder, systemd user service (started)
#   ./install.sh --no-service  install without the service (run it yourself: .venv/bin/python -m famloc.main)
#   ./install.sh --update      git pull, update packages, restart the service
#   ./install.sh --uninstall   stop and remove the service; keeps the code and data/
#
# Environment: PYTHON=python3.12 picks the interpreter; FAMLOC_UNIT_DIR / FAMLOC_UNIT_NAME override the unit path/name.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="${FAMLOC_UNIT_NAME:-family-locator.service}"
UNIT_DIR="${FAMLOC_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
PY="${PYTHON:-python3}"
MODE="install"
SERVICE=1

for a in "$@"; do
  case "$a" in
    --no-service) SERVICE=0 ;;
    --update) MODE="update" ;;
    --uninstall) MODE="uninstall" ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
have_systemd() { command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; }

port() {  # the port the app will listen on (data/config.toml, else the default)
  local p
  p=$(sed -n 's/^[[:space:]]*port[[:space:]]*=[[:space:]]*\([0-9]\{2,5\}\).*/\1/p' "$APP_DIR/data/config.toml" 2>/dev/null | head -1)
  echo "${p:-8790}"
}

if [ "$MODE" = "uninstall" ]; then
  if have_systemd; then
    systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT_NAME"
    systemctl --user daemon-reload
    say "Service removed. Code and data/ are untouched; delete $APP_DIR to remove everything."
  else
    rm -f "$UNIT_DIR/$UNIT_NAME"; say "Unit file removed."
  fi
  exit 0
fi

if [ "$MODE" = "update" ]; then
  [ -d "$APP_DIR/.git" ] || die "not a git checkout; download the new release over this folder instead"
  say "Pulling the latest code"
  git -C "$APP_DIR" pull --ff-only
fi

# 1. Python 3.11+
command -v "$PY" >/dev/null || die "$PY not found. Install Python 3.11 or newer (e.g. 'sudo apt install python3 python3-venv' or 'sudo pacman -S python')."
"$PY" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || die "Python 3.11 or newer is needed; $PY is $("$PY" -V 2>&1)."
"$PY" -c 'import venv, ensurepip' 2>/dev/null || die "Python's venv module is missing (Debian/Ubuntu: sudo apt install python3-venv)."

# 2. virtualenv + packages
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  say "Creating the virtual environment (.venv)"
  "$PY" -m venv "$APP_DIR/.venv"
fi
say "Installing packages"
"$APP_DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$APP_DIR/.venv/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
"$APP_DIR/.venv/bin/python" -c 'import famloc, pyicloud, fastapi, uvicorn' || die "packages installed but the app won't import"

# 3. data/ (owner-only) + starter config
mkdir -p "$APP_DIR/data"; chmod 700 "$APP_DIR/data"
if [ ! -f "$APP_DIR/data/config.toml" ]; then
  cp "$APP_DIR/config.example.toml" "$APP_DIR/data/config.toml"; chmod 600 "$APP_DIR/data/config.toml"
  say "Created data/config.toml from the example (fill in [email] for alerts)"
fi

# 4. systemd user service
if [ "$SERVICE" = 1 ]; then
  if ! have_systemd; then
    warn "no systemd user session here; skipping the service. Start the app with: $APP_DIR/.venv/bin/python -m famloc.main"
    SERVICE=0
  else
    mkdir -p "$UNIT_DIR"
    tmp=$(mktemp)
    sed -e "s#%h/Downloads/family-locator#$APP_DIR#g" "$APP_DIR/deploy/family-locator.service" > "$tmp"
    if [ -L "$UNIT_DIR/$UNIT_NAME" ] || ! cmp -s "$tmp" "$UNIT_DIR/$UNIT_NAME" 2>/dev/null; then
      rm -f "$UNIT_DIR/$UNIT_NAME"; mv "$tmp" "$UNIT_DIR/$UNIT_NAME"; chmod 644 "$UNIT_DIR/$UNIT_NAME"
      say "Installed $UNIT_DIR/$UNIT_NAME"
    else
      rm -f "$tmp"
    fi
    systemctl --user daemon-reload
    systemctl --user enable "$UNIT_NAME" >/dev/null 2>&1
    if [ "$MODE" = "update" ] || systemctl --user is-active --quiet "$UNIT_NAME"; then
      systemctl --user restart "$UNIT_NAME"
    else
      systemctl --user start "$UNIT_NAME"
    fi
    sleep 2
    systemctl --user is-active --quiet "$UNIT_NAME" || die "the service didn't start; see: journalctl --user-unit family-locator -n 50"
    say "Service running"
    if [ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != "yes" ]; then
      warn "lingering is off, so the app stops when you log out and won't start at boot. To fix: sudo loginctl enable-linger $USER"
    fi
  fi
fi

# 5. next steps
ip=$( (hostname -I 2>/dev/null || ip -4 route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p') | awk '{print $1; exit}' || true)
echo
say "Done. Open http://${ip:-localhost}:$(port) in a browser."
if [ "$MODE" = "install" ]; then
  echo "    1. Choose a page password on the first visit (do it straight away)."
  echo "    2. Setup tab: sign in to iCloud and approve the code on your Apple device."
  echo "    3. Places tab: add Home, then your other places."
  if [ "$SERVICE" = 0 ]; then echo "    Start the app with: $APP_DIR/.venv/bin/python -m famloc.main"; fi
  echo "    Guide: $APP_DIR/docs/getting-started.md"
fi
