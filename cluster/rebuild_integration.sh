#!/usr/bin/env bash
# rebuild_integration.sh [ref ...] — recreate the fork's `integration` branch from scratch: apache master + every
# listed ref merged in order, then force-pushed. Never commit on `integration` directly; fix conflicts in the PR
# branches and rebuild. Run from a clone with remotes `origin` (apache/hugegraph) and `fork` (your fork).
# Default refs: the open PRs and local branches we carry. A ref may be `pull/<n>/head` (fetched from apache) or a branch.
set -euo pipefail
REFS=("$@"); [ ${#REFS[@]} -gt 0 ] || REFS=(pull/3184/head pull/2994/head fix/store-client-retry-interrupt)
git fetch -q origin master
git checkout -q -B integration origin/master
echo "integration <- origin/master $(git rev-parse --short origin/master)"
for R in "${REFS[@]}"; do
  case $R in
    pull/*/head) N=${R#pull/}; N=${N%/head}; git fetch -q origin "$R:refs/remotes/origin/pr/$N"; SRC=origin/pr/$N ;;
    *) SRC=$R ;;
  esac
  git merge -q --no-edit "$SRC" || { echo "CONFLICT merging $SRC — resolve it in that branch, then rebuild"; exit 1; }
  echo "  + $R ($(git rev-parse --short "$SRC"))"
done
git diff --stat origin/master integration | tail -1
git push -q -f fork integration && echo "pushed fork/integration $(git rev-parse --short integration)"
