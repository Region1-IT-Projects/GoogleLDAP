#!/bin/zsh
# Launcher: runs install.py under the Python interpreter bundled in this payload.
# Usage: sudo ./install.sh [--no-reboot] [--dry-run]

set -u

readonly HERE="${0:A:h}"
readonly PYTHON="$HERE/python/Versions/Current/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
    print -u2 -- "ERROR: bundled interpreter missing at $PYTHON"
    print -u2 -- "This payload is incomplete. Rebuild it with deploy.sh."
    exit 78
fi

# A drive that has been through a download/unzip cycle can carry quarantine flags that stop
# the interpreter from launching. Clearing them is safe and idempotent.
xattr -dr com.apple.quarantine "$HERE/python" >/dev/null 2>&1 || true

exec "$PYTHON" "$HERE/install.py" "$@"
