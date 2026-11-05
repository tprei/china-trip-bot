#!/usr/bin/env bash
# Publish site/ to the gh-pages branch, which GitHub Pages serves.
# The branch holds one commit, replaced on every run, so it never grows.
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
if [[ ! -f site/index.html ]]; then
  echo "no site/index.html yet"
  exit 0
fi
remote_url="$(git remote get-url "${PAGES_REMOTE:-origin}")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
cp -R site/. "$work/"
touch "$work/.nojekyll"
git -C "$work" init -q -b gh-pages
git -C "$work" add -A
git -C "$work" -c user.name="tripbot" -c user.email="tripbot@users.noreply.github.com" \
  commit -q -m "Dashboard $(date -u +%FT%RZ)"
git -C "$work" push -q --force "$remote_url" gh-pages
echo "published to gh-pages"
