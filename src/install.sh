#!/bin/zsh
# Launcher: runs install.py under the Python interpreter bundled in this payload.
# Usage: sudo ./install.sh [--no-reboot] [--dry-run]

set -u

readonly HERE="${0:A:h}"
readonly PYTHON_SYMLINK="$HERE/python/Versions/Current/bin/python3"

if [[ ! -x "$PYTHON_SYMLINK" ]]; then
    print -u2 -- "ERROR: bundled interpreter missing at $PYTHON_SYMLINK"
    print -u2 -- "This payload is incomplete. Rebuild it with deploy.sh."
    exit 78
fi

# Resolve Versions/Current and bin/python3 (both symlinks) down to the real binary path.
# CPython's own exec_prefix/library-path resolution has had regressions specifically when
# invoked through a symlink chain (e.g. relocatable-python issue #31, Python 3.11.2), where
# it silently falls back to the hardcoded default framework location instead of this
# relocated one, breaking platform-dependent imports. Invoking the real path sidesteps that
# class of bug entirely rather than betting the exact version bundled here is unaffected.
readonly PYTHON="${PYTHON_SYMLINK:A}"

# A drive that has been through a download/unzip cycle can carry quarantine flags that stop
# the interpreter from launching. Clearing them is safe and idempotent.
xattr -dr com.apple.quarantine "$HERE/python" >/dev/null 2>&1 || true

exec "$PYTHON" "$HERE/install.py" "$@"
