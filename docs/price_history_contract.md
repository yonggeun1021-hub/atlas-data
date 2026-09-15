# Full-universe daily price history (`price_history_session/1`)

`config/price_history_contract.json` defines one market-agnostic session
contract, one compact projection, and one append-only index.  The KR
collector (`collectors/krx_price_history.py`) and the reader
(`universe/price_history_store.py`) implement it; the store itself lives in
the **private** evidence repository.

## Market-agnostic by construction

Every manifest field, compact field, index rule and reader entry point is
market-independent.  `market` distinguishes `KR` from `US`, and each market
contributes only a `sources.<MARKET>` block: its source contract, its parts,
its provider field mapping, its session timezone and close, and its calendar.

`sources.US` is deliberately `null`.  A later US window adds that block plus
its own `collectors/us_price_history.py`; it changes **no** field name, **no**
manifest rule, **no** index rule and **no** reader signature, and it reuses
`universe/price_history_store.py` unchanged.  `market_source()` fails closed
(`MARKET_SOURCE_NOT_DEFINED:US`) until that block exists, so nothing silently
falls back to the KR shape.

## Storage boundary

| Repository | Holds |
|---|---|
| `atlas-data` (public) | contract, collector, reader, tests, this document |
| `atlas-private-evidence` (private) | every raw and compact price byte, every manifest, the index |

The public repository tracks **zero** raw or compact price-history bytes
(F2 licence boundary).  The private workflow checks out an exact pinned public
commit and imports the collector from it; the pin lives in the private repo's
`config/approved_price_history_public_commit.json`.

```
price_history/{market}/{yyyy}/{bas_dd}/raw/{part_id}.json.gz
price_history/{market}/{yyyy}/{bas_dd}/compact.jsonl.gz
price_history/{market}/{yyyy}/{bas_dd}/manifest.json
price_history/{market}/index.jsonl
```

`compact.jsonl` rows are JSON arrays in exactly this field order, sorted by
code, values normalised strings or `null`:

```
[code, open, high, low, close, cmpprevdd, fluc_rt, vol, value, mktcap, list_shrs]
```

The compact file is a pure function of the raw bytes.
`rederive_compact()` re-derives it and compares digests, so a stored session
can always be shown to be what its raw response says it is.

## Fail-closed rules

* **No collection before publication.** `assert_collectable()` refuses any
  instant before the provider publication plus the settle offset (KR: next
  calendar day 08:00 + 70 minutes = 09:10 KST).  A same-day 18:13 KST request
  returned zero rows; the next morning (10:47 KST) the same session had rows.
  Forward capture therefore runs on weekday mornings (09:10, retry 11:10 KST)
  and targets `forward_target_session()`: the latest official open session
  strictly before the run's local date (Monday -> Friday).  There is no flag,
  argument or environment variable that permits an earlier collection.
* **A holiday request is refused.** Open/closed comes only from the official
  KRX calendar capture, through the existing
  `market_data/krx_official_holiday_calendar.py`.
* **A missing calendar year fails closed.** `resolve_calendar_capture()`
  raises `CALENDAR_CAPTURE_MISSING:<year>` rather than assuming a weekday is
  open.  Only **2026** has a committed capture today; see below.
* **Emptiness is never a holiday.** A zero-row response is recorded as
  `status: EMPTY` with `first_available_observed_at_utc: null` and is never
  written as session data.  It says the provider returned nothing for an
  officially open session, and nothing more.
* **A partial session fails closed.** One part returning rows while another
  returns none raises `PART_ROW_COUNT_ZERO`.
* **An OK session is never overwritten; EMPTY can be repaired.**
  `write_session()` refuses to replace an `OK` session.  A later attempt on an
  `EMPTY` session appends its attempt to the stored attempts; if it returns
  rows the session becomes `OK` and `first_available_observed_at_utc` is that
  attempt's retrieval time.  A differing re-receipt is appended to the index as
  `REVISION_OBSERVED` beside the original, and an identical re-receipt adds
  no row.
* **Rules over "the last n sessions" use the calendar.**
  `calendar_window(market, n, end_bas_dd, t)` returns the official calendar's
  last `n` open sessions ending at `end_bas_dd` and lists every one the store
  does not hold as OK at `t` (never stored, EMPTY, or observed later).
  `session_window()` is the last `n` *stored* sessions and can reach back past
  an EMPTY session; KR T2 C3 (`universe/kr_liquidity_c3.py`) uses the calendar
  window and returns UNKNOWN for any missing session.
* **Private per-symbol fields never reach the public population packet.**
  With `ATLAS_PRICE_HISTORY_ROOT` set, the KR population context is
  `PRIVATE_ONLY_KRX_OPENAPI_DERIVED`; `population_symbol_observation.build()`
  refuses to write that packet or its work chunks anywhere inside this
  repository, and a store located inside this repository is refused.
* **Gaps stay gaps.** `series()` returns exactly the rows on disk. A session
  with no row for a code contributes nothing; nothing is carried forward,
  interpolated or padded. `session_window()` exposes the denominator so the
  gap is measurable, and `sma_readiness()` reports a short window as
  `INSUFFICIENT_SESSIONS` rather than averaging fewer sessions.

## Prices and returns

`adjustment` is `NONE`: no split or dividend adjustment is ever applied to a
stored value.  A window return is the provider's own per-session change
compounded (`session_gross_factor` = `1 + FLUC_RT/100`, `compound_return`),
never `close_last / close_first`, which a split would silently corrupt.
`implied_previous_close` exposes `close - CMPPREVDD` for cross-checking.

A `LIST_SHRS` change between two consecutive stored sessions is reported as
`CORPORATE_ACTION_SUSPECT` for that code in the manifest.  It is a flag, not
a correction: the move is never quietly treated as an ordinary price move.

## Reuse, not reimplementation

| Need | Reused from |
|---|---|
| request construction, per-row provider validation | `.github/scripts/korea_breadth.py` (`build_request`, `validate_snapshot`) |
| open / closed for a `bas_dd` | `market_data/krx_official_holiday_calendar.py` |
| relative strength, volume baseline | `discovery/market_behavior.py` |
| population row-count reference | `universe/krx_global_universe.py` (`total_count`) |

`PriceHistoryStore.market_behavior_window()` *projects* stored sessions into
`market_behavior_radar_input/1`; `behavior_features()` then lets
`discovery/market_behavior.py` compute RS and the volume baseline.  Neither
formula is restated here, so there stays exactly one implementation of each
in the repository.

## Network boundary

`collectors/krx_price_history.py` contains no default transport.
`fetch_part()` has a **required** `opener` parameter and raises
`OPENER_REQUIRED` when it is absent, so no code path — and no test — can
reach the provider by accident.  `universe/price_history_store.py` imports no
networking module at all.

## Evaluator input

`decision/korea_population_symbol_observation.py` takes the store as an
optional input (`ATLAS_PRICE_HISTORY_ROOT`, or `inputs["price_history_root"]`).
With no store configured — the public default — the packet is byte-identical
to the one produced before this input existed.

With a store configured, a population symbol holding the session's stored bar
reaches the contract's **existing** `EVALUABLE_PRICE` level from
`config/population_symbol_observation_contract.json` — the same level the US
adapter already uses for daily bars.  No new state name is introduced.  SMA20
becomes computable from stored closes; investor flows still do not exist in
this source and stay explicitly missing rather than estimated.

## Known gaps

* **The 2025 calendar capture does not exist.** The 260-session backfill
  reaches into 2025, and only 2026 has a committed capture.  The collector
  already supports 2009-2100, so this is a missing *network run*, not missing
  code, and it must be performed for real by a human or CI exactly as the
  2026 one was:

  ```bash
  python3 collectors/krx_official_holiday_calendar.py 2025 \
    evidence/market_calendar/krx_global_holiday/<capture-date>/capture-2025.json
  ```

  No synthetic 2025 capture is committed: a fabricated official capture would
  be indistinguishable from a real one.  The offline derivation path is proved
  in `test/test_krx_price_history.py` against a capture synthesised in a
  temporary directory.
* **The provider's daily request limit is UNVERIFIED** (plan §10).  The
  backfill is therefore batched and records its exact request count and
  intervals in its receipt.

## Authority

Every `authority` field is false apart from
`price_history_observation_only`.  This contract grants no candidate,
ranking, stage-promotion, entry, order, strategy, production, trading, or
real-capital authority.
