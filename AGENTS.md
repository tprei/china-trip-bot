# Agent Instructions

This file is for AI coding agents working in this repository. Follow it unless a human gives a more specific instruction.

## Mission

A small, boring bot that watches flight prices to mainland China for one group trip (Aug–Sep 2027), ranks the options fairly across everyone, and reports to one Telegram group. It runs nightly on a home mini PC (WSL). Optimize for reviewability and for running unattended for months. Do not optimize for scale.

## Stack

- Python 3.12+, managed with `uv`. Lint and format with `ruff`, types with `mypy --strict`, tests with `pytest`.
- SQLite through the standard `sqlite3` module. No ORM.
- HTTP through `httpx`. Configuration and reference data in YAML.
- Card images: HTML templates (Jinja2) rendered to PNG by Playwright Chromium.
- Telegram: the plain Bot API over HTTPS long polling. No bot framework.

Do not add a web server, a queue, a scheduler library, an ORM or a bot framework unless a human asks.

## Layout

- `src/tripbot/domain/` — pure rules: money, trips and windows, filters, route planning, ranking, alerts. No I/O, no clock, no network. Everything here is unit tested.
- `src/tripbot/sources/` — one adapter per flight source. Adapters turn wire data into domain `Offer`s and nothing else.
- `src/tripbot/store/` — SQLite schema and queries.
- `src/tripbot/pipeline/` — the nightly run that wires sources, store and domain together.
- `src/tripbot/render/` — HTML card templates and the PNG renderer.
- `src/tripbot/telegram/` — Bot API client, command parsing and replies.
- `config/trip.yaml` — people, origins, windows and rules. `data/*.yaml` — reference facts (trains, domestic flights, weather, visa).

## Rules

- Prefer boring, explicit code. Keep files and functions small.
- Money is integer minor units (`Money`). Never floats for prices. Conversion rounds half up, once.
- Dates are `datetime.date`; local flight times are naive `datetime`s in the airport's local time.
- Domain functions take plain data and return plain data. Time ("today") is passed in, never read inside `domain/`.
- Use the product language: group, window, offer, segment, route, stop, leg, combo, fairness, alternative.
- Never use `# noqa`, `# type: ignore` or `cast` to silence a check. Fix the code, or change the config in `pyproject.toml` with a reason.
- User-facing text is English, short labels, no explanatory sentences.
- Reference data in `data/` carries its source and as-of date. Stable facts (timetables, face fares, climate normals) can drive ranking; volatile facts (fares on sale, visa status) are only shown with their date.
- Secrets come from the environment (`.env`), never from files in git.

## Tests

Write tests when they reduce real risk: the domain rules, adapter parsing against recorded fixtures, the store round trip, command parsing. Tests never touch the network.

CI runs two parallel jobs and both must pass: lint (ruff, `ruff format --check`, `mypy --strict`, shellcheck, `tripbot check-config`, the PR size limit) and tests (pytest with warnings as errors, Chromium card rendering, coverage of at least 85%). `tests/test_code_rules.py` enforces the no-silencing rule and keeps `domain/` free of I/O and the clock.

## Pull requests

- Never push directly to `main`. Every change goes through a PR. CI must pass.
- Keep PRs under 1,000 changed lines, excluding lockfiles and fixtures. Split larger work into stacked PRs.
- Stacked PRs: each slice is a branch on top of the previous one, titled `[n/N] ...`, with a `## Stack` section listing every PR in order, `## Scope`, `## Depends on` and `## Left out`.
- A PR that changes rendering carries screenshots of the cards, published as a secret gist and linked from the PR body. Screenshots are never committed.
- Merging is for humans. Never pass `--delete-branch` to `gh pr merge`; deleting a base branch closes the child PRs.
