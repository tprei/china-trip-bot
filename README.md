# China trip bot

Tracks flight prices to mainland China for our August–September 2027 trip and reports to a Telegram group.

Five people fly from three cities (Dublin, London, São Paulo) for one of three date windows. Every night the bot searches flights into any mainland airport, adds the cost of getting around China (trains, sleepers, domestic flights), and ranks the combinations by group total, under a fairness rule: no group pays more than 25% or travels more than 6 hours over its own best option.

## Configuration

- `config/trip.yaml` — groups, origins, windows, flight rules, fairness, the route and its alternatives, alert triggers, search budgets.

To add someone from an existing origin, add their name to that group's `people`. To add a new group, add a line under `groups`.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
uv run tripbot check-config
```

See `AGENTS.md` for the code rules and the pull-request workflow.
