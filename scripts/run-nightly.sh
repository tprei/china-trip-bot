#!/usr/bin/env bash
# Nightly search, rank, alert and dashboard build. Run by Task Scheduler through wsl.exe.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p var/logs
log="var/logs/nightly-$(date +%F).log"
export PATH="$HOME/.local/bin:$PATH"
{
  echo "== $(date -Is) nightly"
  status=0
  uv run --frozen tripbot nightly || status=$?
  if [[ -x scripts/deploy-site.sh ]]; then
    scripts/deploy-site.sh || echo "site deploy failed"
  fi
  echo "== $(date -Is) exit $status"
} >>"$log" 2>&1
find var/logs -name 'nightly-*.log' -mtime +60 -delete
exit "$status"
