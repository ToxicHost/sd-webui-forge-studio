#!/bin/sh
# Double-click in Finder to start Forge Studio on an Apple Silicon Mac.
#
# Finder runs this in Terminal from your home folder, so it first moves to the
# folder it lives in, then runs start-studio.sh. Closing the Terminal window
# stops Studio.
#
# First time only: macOS may say it "cannot be opened because it is from an
# unidentified developer". Control-click this file, choose Open, then Open
# again. See docs/studio/MAC_INSTALL.md.

cd "$(dirname "$0")" || exit 1
exec /bin/sh ./start-studio.sh "$@"
