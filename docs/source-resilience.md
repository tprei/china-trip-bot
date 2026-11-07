# Flight-source resilience

Google Flights and Trip.com remain independent sources with separate budgets.
An adapter exception is contained so remaining searches continue.

## Google parsing

The compatibility layer reuses faster-flights 3.8.0 transport and model parser.
It decodes the ds:1 callback and separate sideChannel JSON, validates the whole
envelope, and includes top results. Unknown/truncated/duplicate envelopes fail
closed. No dependency patch, extra request or automatic retry is introduced.

Full integral dates and clock components are required: explicit midnight is
valid, missing components are not defaulted. Provider elapsed durations are
preserved; local clocks are subtracted only at the same connecting airport.
DST ambiguity cannot be resolved without upstream offsets. Invalid itineraries
are isolated; complete offers survive with bounded index-only diagnostics.
All invalid means error, not empty inventory.

## Fixtures and verification

The wire-shaped regression fixture retains public flight fields and malformed
clock shapes, with dates and prices changed to fictional values. It contains
no passenger identity, tokens, cookies, raw HTML or current fare claims.
Envelopes and edge cases are synthetic. It is not a replay of the original
missing callback failure body. Tests cover top-result retention, partial/all
invalid itineraries, midnight/year boundaries and independent source failures.

Live endpoint availability is not established by these offline tests. Trip.com
HTTP 430/432 may be gateway/WAF denials, not malformed request diagnostics.
No scheduler, Telegram notification, purchase or deployment is activated.
