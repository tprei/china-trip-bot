#!/usr/bin/env bash
# Add new source strings from code and templates to every catalog and drop removed ones.
# Then fill in the empty `msgstr` entries; tests/test_i18n.py fails until every one is translated.
set -euo pipefail
cd "$(dirname "$0")/.."

pot="$(mktemp --suffix=.pot)"
trap 'rm -f "$pot"' EXIT

uv run pybabel --quiet extract --no-location --sort-output --no-wrap \
  --mapping-file babel.cfg --project tripbot --output-file "$pot" src
uv run pybabel --quiet update --no-fuzzy-matching --ignore-obsolete --no-wrap \
  --input-file "$pot" --output-dir src/tripbot/locales --domain tripbot
