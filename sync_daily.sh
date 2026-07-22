#!/bin/zsh
set -eu

PROJECT_DIR="/Users/rudolph/Documents/Codex/2026-07-22/referenced-chatgpt-conversation-this-is-untrusted"
LOCK_DIR="/private/tmp/finhire-korea-sync.lock"

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  print "$(date '+%Y-%m-%d %H:%M:%S %Z') sync skipped: another run is active"
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT INT TERM

cd "$PROJECT_DIR"
export TZ="Asia/Seoul"
export PYTHONPYCACHEPREFIX="/private/tmp/finhire-pycache"
print "$(date '+%Y-%m-%d %H:%M:%S %Z') full sync started"
/usr/bin/python3 app.py sync
print "$(date '+%Y-%m-%d %H:%M:%S %Z') full sync completed"
