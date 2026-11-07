# Joint-fare redundancy

`tripbot search-groups PLAN.json OUTPUT --window B --browser-fallback --budget 3`
runs an explicit bounded comparison, not the publishing nightly command. On Linux
use `xvfb-run -a` with the already installed Playwright Chromium. The browser is
headed, with a fresh context; there are no anti-detection modifications, credentials,
proxy services, CAPTCHA solvers or purchases. Browser fallback is opt-in.

Plan is a JSON array of objects containing `group_id`, `origin`, `destination`,
`outbound_date`, `return_date` (ISO dates). Passenger counts come from trip config.
All groups, airports, dates and flexibility are validated before network access.
The plan is an explicit sample; it does not change the configuration or silently
scan every possible date. A new output directory is required for every run.

The existing Trip.com API is tried first. An error opens its circuit for the
remaining execution, with the initial reason retained. A valid API response
(including a valid empty list) does not invoke Chromium. The endpoint currently
has only a validated one-way parser: a successful API response is NOT a joint
round-trip quote. Joint results are therefore empty in that case, with an explicit
diagnostic, rather than relabeling independent fares. Invalid API schemas fail
closed. Browser starts are spaced at least four seconds apart; group requests
consume the explicit budget, capped by configured Trip.com budget.

Google remains available alongside Trip.com, with two one-way searches per group
within its configured budget. Those prices remain per-person one-way evidence;
they do not prove multi-passenger inventory. No paid source is called.

The separate round-trip model keeps the selected outbound and return bound to
one observed group price and separately observed per-person display. It does
not divide prices between legs or mix halves from different tickets. The ranking
uses the existing route score and price/time fairness rules, applying flight
restrictions and arrival-window constraints to whole quotes. Internal route fares
are dated reference estimates, not live bookings or a purchasable package.

Outputs: isolated SQLite results, JSON report, and private browser evidence.
URLs have query strings removed, but text/screenshots and request metadata can
still contain private trip data. Keep outputs outside Git and review before
sharing; this is not an automatic anonymizer.
No Telegram, Pages, healthcheck, scheduler or persistent service is called. The
existing nightly one-way renderer/store is deliberately not fed fabricated leg
prices. Joint fares are consumed by the new explicit group command and its
separate ranking report. Quote validity ends at the observed UI state; final
checkout repricing and availability remain unverified.

The adapter currently samples **one** selected pair per request (the option count
is an upper bound, not an exhaustive enumeration). Its card prefilter is at most
one change / eight hours of connection; the configured hard rules are still
applied to expanded journeys before ranking. Public query-stripped page URLs are
search-page references, not durable selected-offer links. Fare-panel warnings
such as multiple bookings/tickets are retained; a selected round-trip display
must not be described as a single protected ticket. Short-connection warnings
and baggage recheck/protection should be reviewed in the itinerary evidence.

Verification includes local-DOM parsing of both journeys and fare totals, API
valid/empty/error and circuit behavior, browser runtime/verification failure,
request/passenger identity, budgets, isolated persistence and CLI side-effect
boundaries. Unit tests never contact travel sites. Live captures are deliberately
untracked; sample fares are not bundled as current prices.

## Offline rendering demo

`python scripts/render-joint-sample.py /tmp/joint-demo` writes a fictional
HTML/PNG card without querying a flight provider. Playwright Chromium is
required; headless suffices for rendering. Joint templates are separate from
legacy nightly rendering; search-groups outputs JSON/SQLite, not a published
dashboard. Never commit plans, outputs or browser evidence.
