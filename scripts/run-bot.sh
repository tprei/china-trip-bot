#!/usr/bin/env bash
# The Telegram listener. Restarts after a crash; Task Scheduler starts it at boot.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
mkdir -p var/logs
export PATH="$HOME/.local/bin:$PATH"
while true; do
  {
    echo "== $(date -Is) bot start"
    uv run --frozen tripbot bot
    echo "== $(date -Is) bot exit $?"
  } >>var/logs/bot.log 2>&1
  sleep 30
done
