#!/usr/bin/env bash
# Upload site/ to Cloudflare Pages. Skipped unless CF_PAGES_PROJECT is set in .env.
set -euo pipefail
cd "$(dirname "$0")/.."
project="$(grep -E '^CF_PAGES_PROJECT=' .env 2>/dev/null | cut -d= -f2- || true)"
if [[ -z "$project" ]]; then
  echo "CF_PAGES_PROJECT not set; site stays local"
  exit 0
fi
set -a
# CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID for wrangler
source .env
set +a
npx --yes wrangler@3 pages deploy site --project-name "$project" --branch main --commit-dirty=true
