#!/bin/sh
# Start Forge Studio from a Git checkout on macOS (Apple Silicon) or Linux.
#
# The POSIX twin of Start-Studio.bat (MAC1): prepare app/venv once, install
# the seven approved auxiliary models, then hand over to start_studio.py,
# which opens the browser. Start-Studio.command runs this when you
# double-click it in Finder.
#
# Unlike Start-Studio.bat this passes no --fast-fp16: that option is CUDA-only,
# and on a Mac it would only be refused with a warning.

set -u
cd "$(dirname "$0")" || exit 1

VENV_PYTHON="venv/bin/python"
STUDIO_ARGS=""

fail() {
    echo
    echo "  $1"
    echo
    printf "Press Return to close this window. "
    read -r _ || true
    exit 1
}

if [ -x "$VENV_PYTHON" ]; then
    # Also repairs an interrupted setup or newly required dependencies.
    "$VENV_PYTHON" scripts/bootstrap_environment.py --quiet \
        || fail "Studio could not prepare its Python environment (see above)."
else
    BOOT_PYTHON="${PYTHON:-}"
    if [ -z "$BOOT_PYTHON" ]; then
        # Never a bare `python3`: on macOS that is 3.9, and on a Mac without
        # Apple's developer tools it opens an installer window instead.
        for candidate in python3.13 python3.12 python3.11; do
            if command -v "$candidate" >/dev/null 2>&1; then
                BOOT_PYTHON="$candidate"
                break
            fi
        done
    fi
    if [ -z "$BOOT_PYTHON" ]; then
        fail "Studio needs Python 3.11 to 3.13. Install Python 3.13 from python.org (macOS 64-bit universal2 installer), then start Studio again."
    fi
    "$BOOT_PYTHON" scripts/bootstrap_environment.py \
        || fail "Studio could not prepare its Python environment (see above)."
fi

if [ -n "${STUDIO_ASSET_DIR:-}" ]; then
    "$VENV_PYTHON" scripts/setup_assets.py --from-directory "$STUDIO_ASSET_DIR" \
        || fail "Studio could not install its auxiliary models (see above)."
else
    "$VENV_PYTHON" scripts/setup_assets.py \
        || fail "Studio could not install its auxiliary models (see above)."
fi

# shellcheck disable=SC2086  # STUDIO_ARGS is intentionally word-split
"$VENV_PYTHON" start_studio.py $STUDIO_ARGS "$@" \
    || fail "Studio stopped with an error (see above)."
exit 0
