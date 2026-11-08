# China trip bot

Tracks flight prices to mainland China for our August–September 2027 trip and reports to a Telegram group.

Five people fly from three cities (Dublin, London, São Paulo) for one of three date windows. Every night the bot searches flights into any mainland airport, adds the cost of getting around China (trains, sleepers, domestic flights), and ranks the combinations by group total, under a fairness rule: no group pays more than 25% or travels more than 6 hours over its own best option.

## Configuration

- `config/trip.yaml` — groups, origins, windows, flight rules, fairness, the route and its alternatives, alert triggers, search budgets.

To add someone from an existing origin, add their name to that group's `people`. To add a new group, add a line under `groups`.

## Commands

| Command | What it does |
| --- | --- |
| `tripbot check-config` | validate `config/trip.yaml` and `data/` |
| `tripbot nightly` | search, rank, snapshot, alert, write `site/index.html` |
| `tripbot nightly --no-search` | the same from stored offers only |
| `tripbot bot` | answer Telegram commands; send the Sunday summary |
| `tripbot build-dashboard` | write `site/index.html` from stored offers |
| `tripbot render-samples OUT` | every card and the dashboard from sample flights |

## Running it on the mini PC (WSL)

1. **Code and tools.** In WSL (Ubuntu):
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   git clone https://github.com/tprei/china-trip-bot.git ~/china-trip-bot
   cd ~/china-trip-bot
   uv sync --frozen
   uv run playwright install --with-deps chromium
   uv run tripbot check-config
   ```
2. **Telegram.** Message @BotFather, `/newbot`, keep the token. Then `/setprivacy` → Disable, so the bot sees commands in the group. Create the group, add the bot, send any message there and open `https://api.telegram.org/bot<TOKEN>/getUpdates`: the chat id is the negative number under `chat.id`. To have each weekly summary pinned, make the bot a group admin with the Pin messages right; it only unpins the summary it pinned before, so other pinned messages stay. Changing admin rights can upgrade a basic group to a supergroup with a new chat id, so check `chat.id` in `getUpdates` again afterwards.
3. **Secrets.** `cp .env.example .env` and fill in `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID` and `SERPAPI_KEY`. The bot only answers in that one chat.
4. **First run.** `uv run tripbot nightly`. It prints each source's searches and the best total per window; the first night only fills the store, alerts start from the second.
5. **Schedule it.** In an elevated PowerShell on Windows:
   ```powershell
   cd \\wsl$\Ubuntu\home\<you>\china-trip-bot\scripts\windows
   .\register-tasks.ps1 -RepoPath /home/<you>/china-trip-bot
   Start-ScheduledTask -TaskName tripbot-bot
   ```
   `tripbot-nightly` runs daily at 03:00 (wakes the PC, catches up if it was off). `tripbot-bot` starts at boot and `scripts/run-bot.sh` restarts it after a crash. If WSL will not start from a task while nobody is logged in, open each task, choose "Run only when user is logged on", and turn on automatic sign-in for that Windows user.
6. **Health check (optional).** Create a check on healthchecks.io with a 1-day period and a few hours of grace, and put its URL in `HEALTHCHECK_URL`. A missed or failed night then reaches you by email.
7. **Dashboard.** The nightly run writes `site/index.html`, the page behind the cards' Details button, and with `PUBLISH_PAGES=1` pushes it to the `gh-pages` branch, which GitHub Pages serves at https://tprei.github.io/china-trip-bot/. The page is public, like the repo. For the push, give the mini PC a deploy key:
   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/tripbot_deploy -N ""
   cat ~/.ssh/tripbot_deploy.pub   # repo Settings → Deploy keys → Add, allow write access
   printf 'Host github.com\n  IdentityFile ~/.ssh/tripbot_deploy\n' >> ~/.ssh/config
   git remote set-url origin git@github.com:tprei/china-trip-bot.git
   scripts/publish-site.sh
   ```
   Pages is set to deploy from the `gh-pages` branch (Settings → Pages). Leave `DASHBOARD_URL` empty to hide the Details button.

Logs are in `var/logs/`. The database is `var/tripbot.sqlite3`; copy it to back it up.

### Changing things later

- Dates, people, rules: edit `config/trip.yaml`, run `uv run tripbot check-config`. Both tasks pick it up on their next run; restart `tripbot-bot` for the listener.
- Train and fare facts: edit `data/*.yaml` and update its `as_of`.
- The poster `/banner` sends: replace `var/banner.png` (outside git). The bot reads it on each request, so no restart is needed.
- Updating the code: `git pull && uv sync --frozen`.

## Development

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest --cov
uv run tripbot check-config
```

See `AGENTS.md` for the code rules and the pull-request workflow.
