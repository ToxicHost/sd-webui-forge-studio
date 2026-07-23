#!/usr/bin/env bash
set -euo pipefail

# Review and customize UPSTREAM_BRANCH before first use.
UPSTREAM_BRANCH="${UPSTREAM_BRANCH:-neo}"
DATE_TAG="$(date +%Y%m%d)"
BRANCH="integration/upstream-${DATE_TAG}"

git status --short
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Working tree is not clean. Commit or stash changes first." >&2
  exit 1
fi

git fetch upstream
git switch -c "$BRANCH"
git merge --no-ff "upstream/${UPSTREAM_BRANCH}"

echo
echo "Merge created on $BRANCH."
echo "Now follow docs/05_UPSTREAM_SYNC_PLAYBOOK.md."
