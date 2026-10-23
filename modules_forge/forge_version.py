# The engine's name and release: what every picture's `Version:` says
# (`processing.program_version`) and what the console and system info report
# (`launch_utils.git_tag`).
#
# This engine is Studio's. Owner, 2026-10-22: "This isn't Studio on top of
# Neo. This is simply Studio." It began as Forge Neo 2.27 -- the pinned base
# is recorded in UPSTREAM_BASE, not here -- and its release is Studio's own,
# written in ONE place, `forge_studio.__version__`, so a version bump reaches
# the pictures without a second edit.
from forge_studio import __version__ as release

version = "Studio"
