# US Forward Source-Coverage Universe Contract (P3-02)

Status: exact-byte Nasdaq Trader directory to P3-01 Global Asset Master adapter
and scheduled source-coverage population implemented. Listing, delisting,
security-type, liquidity, tradability, investability, source hierarchy, and
historical reconstruction remain unratified or unavailable.

## Purpose and approved input

`universe/us_global_universe.py` consumes the two official Nasdaq Trader Symbol
Directory response bodies already covered by the ratified forward-only P1-US-04
source contract:

- `nasdaqlisted.txt`; and
- `otherlisted.txt`.

The caller supplies each exact response as base64 plus the official endpoint,
SHA-256, source-date `available_at`, and UTC retrieval time. The adapter verifies
the exact endpoint, bytes, hash, header, footer creation date, minimum record
count, unique source-row identity, and temporal ordering. Both files must carry
the requested source date and must be present exactly once.

The adapter has no network client. It does not refetch a newer directory or use
current rows to backfill an earlier date.

## Identity and membership boundary

Every source row becomes a distinct P3-01 record. Its internal asset ID is a
stable hash of the source file name plus the exact primary source symbol. Exact
symbols, including Nasdaq Trader preferred-share `$` notation, remain in the
primary symbol, alias, and source identifiers.

The two source files are never merged automatically. A primary symbol appearing
in both files is a collision and fails closed. The adapter also does not infer
NYSE/Nasdaq/Arca/IEX MIC codes. It records an exchange ID in the form
`NASDAQ_TRADER:{raw-code}`; `NASDAQ` is used only where the official Nasdaq-
listed source has no exchange field. This is a source-directory exchange label,
not a canonical trading-venue decision.

Each record receives only:

- exact-date `MARKET = US` membership; and
- exact-date source-coverage `UNIVERSE` membership identifying which directory
  contained the row.

The effective interval is `[source_date, next_calendar_date)`. Presence means
only that the official current-day directory contained the row. It does not
prove listing inception, delisting time, liquidity, tradability, or
investability.

## Uninterpreted source attributes

All official directory fields are preserved in `source_attribute_rows`,
including Security Name, Test Issue, ETF, Financial Status, Market Category,
Round Lot Size, NextShares, Exchange, CQS Symbol, and NASDAQ Symbol where the
source provides them.

No row is filtered based on those values. In particular, a test issue, ETF, or
non-normal financial-status code remains observed source data with:

- `eligibility_interpretation = null`;
- `liquidity_observation = null`;
- `tradability_decision = null`; and
- `investable_eligible = false`.

This preserves the facts required by a future ratified policy without inventing
the policy now.

## Paid-data and authority boundary

The adapter inherits P1-US-04's `USER_RECONFIRMATION_REQUIRED` checkpoint.
Historical universe reconstruction, delisted-security OHLCV, paid APIs,
vendors, licences, subscriptions, and converting trials are not authorized.

Cross-source identity merge, MIC inference, security-type filtering, liquidity
filtering, tradability filtering, investable-universe approval, historical
reconstruction, Stage promotion, Production, trading, and paid-data acquisition
authorities are all false. Every P3-01 output record remains
`universe_approved = false` and `investable_eligible = false`.

## Scheduled population state

`.github/scripts/us_forward_universe_populate.py` revalidates an already
committed P1-US-04 archive and publishes a content-addressed, append-only
population record. Population record v2 separates two scopes that the original
v1 record conflated:

- the nested `us_global_universe_packet/1` keeps the adapter's static capability
  boundary, including `SCHEDULED_MASTER_POPULATION_NOT_IMPLEMENTED`; and
- the outer `population_execution` is authoritative for the actual population
  run, marks that one static boundary resolved, and exposes the remaining
  effective unresolved boundaries.

Existing v1 population artifacts are immutable. A same-date retry rebuilds and
verifies them under v1 without rewriting or silently migrating them. Only new
dates use v2. Neither version authorizes investability, Stage, Production, or
trading.

## Offline command

```bash
python3 universe/us_global_universe.py /tmp/us-global-universe-input.json \
  --out /tmp/us-global-universe.json
```

The adapter output is written atomically only to the requested path. The
population workflow adds no provider request, default eligibility policy, or
trading path; it only reuses the existing P1-US-04 schedule and committed raw
archive.
