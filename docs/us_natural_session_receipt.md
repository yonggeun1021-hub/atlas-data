# US natural finished-session receipt

`market_data/us_natural_session_receipt.py` is the minimal offline producer for
the missing US Gate 1 evidence. It does not collect data. A caller must inject:

1. a hash-bound, date-specific consensus snapshot from the official NYSE
   trading-hours calendar and the official Nasdaq holiday schedule; and
2. an immutable `NATURAL_ORIGINAL` one-minute capture for one identified US
   reference asset.

The producer validates source identity, source agreement, regular/early-close/
holiday status, IANA `America/New_York` DST, minute continuity, session finish,
and the existing session-open anchored 15m/1h interval rules from
`market_data/us_session_bars.py`. It persists only counts and SHA-256 identities,
never raw or aggregate prices.

Missing official calendar evidence, a closed or unknown session, an unfinished
session, partial minute bars, a source disagreement, invalid PIT ordering, or
normalization failure produces `Gate1 UNKNOWN / Gate2 HOLD / WAIT`. The checked-in
2026-09-01 receipt is exactly that honest absence result; it is not a finished
session and cannot be promoted later by relabeling.

Gate 2 remains independent and closed: numeric TTL is `null`, repository default
is `ABSENT`, and provider SLA is `UNRATIFIED`. The producer creates no numeric
freshness value, policy ratification, scoring, Regime, candidate, entry, exit,
PAPER mutation, broker/network/OAuth/order/cancel, REAL/live, Production, or
Trading authority.

Focused validation:

```bash
python3 validation/tests/test_us_natural_session_receipt.py
python3 market_data/us_natural_session_receipt.py \
  --verify data/observations/us_natural_finished_session/2026-09-01/receipt.json
```

The next natural evidence opportunity recorded by the initial receipt is
2026-09-01 16:05 ET (`2026-09-01T20:05:00Z`), after the candidate regular-session
close. It remains only an observation opportunity until both official calendar
records and the complete natural minute capture are supplied.
