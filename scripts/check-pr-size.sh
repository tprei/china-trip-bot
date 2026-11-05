#!/usr/bin/env bash
# Fails when a branch changes more than 1,000 lines against its base, excluding
# lockfiles, recorded fixtures and vendored fonts (see AGENTS.md).
set -euo pipefail
base="${1:?usage: check-pr-size.sh <base ref>}"
limit=1000
changed="$(git diff --numstat "$base"...HEAD -- . \
  ':(exclude)uv.lock' ':(exclude)tests/fixtures/**' ':(exclude)src/tripbot/render/fonts/**' \
  | awk '$1 != "-" { total += $1 + $2 } END { print total + 0 }')"
echo "changed lines: $changed (limit $limit)"
if (( changed > limit )); then
  echo "Split this into stacked PRs under $limit lines each." >&2
  exit 1
fi
