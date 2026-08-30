# Upbit KRW Tradeable Universe / PAPER-Eligibility Contract (P3-12)

Status: **RATIFIED, PAPER-only, effective 2026-08-30**. The CIO-ratified
tradeable-universe policy, exclusion taxonomy, and exact evidence-bound
55-market identity registry are loaded fail-closed. Their document-level
effective dates prevent any 2026-08-29-or-earlier packet from being
retroactively reclassified. Classification grants no Exchange, REAL,
Production, Trading, order, or PAPER-exit authority.

The daily classification packet retains proposal counts/findings so its
decision surface stays compact. The full proposal bodies are independently
reconstructed from the same retained `market/all` bytes by
`.github/scripts/upbit_identity_review_bundle.py` and stored at
`data/observations/upbit_identity_review/<date>/packet.json`. That bundle is
append-only, source/hash-bound, and review-only; it cannot mutate the
taxonomy/identity/policy configs or promote a market.

## Kraken rank is not Upbit tradeability

`universe/upbit_tradeable_universe.py` never imports Kraken breadth or
leadership selection as a promotion input. A market's Kraken Top-N
membership can only ever set the purely observational
`kraken_cross_exchange_reference` display field on its output row; it can
never change `state`, `reason`, or any authority field. See the `# SAFETY
INVARIANT` comment on `build_classification()` and
`test_upbit_tradeable_universe.py::test_kraken_presence_never_promotes`,
which asserts this by both behavior (an otherwise-identical run with and
without a Kraken reference set produces the same `state`/`reason`) and by
inspecting the source between the point the parameter is read and the point
the display field is computed.

Upbit tradeability is derived only from Upbit's own public market data:
`GET /v1/market/all?is_details=true`, `GET /v1/ticker`, `GET /v1/orderbook`,
and `GET /v1/candles/days`. No API key/secret is used or required, and no
order/withdrawal/private endpoint is ever called --
`config/upbit_market_capture_contract.json` carries hard invariants
(`auth_required: false`, `order_or_withdrawal_endpoints_called: false`)
that `upbit_market_capture.py::load_contract()` enforces fail-closed, and
`test_upbit_market_capture.py` asserts no such endpoint path or credential
token appears anywhere in the capture module or its contract.

## State machine

Each captured KRW market classifies into exactly one state, always with an
explicit, never-blank `reason`:

```
OBSERVATION_POOL -> TRADEABLE_UNIVERSE -> PAPER_ELIGIBLE
                  \-> BLOCKED (identity collision / evidence tamper)
```

`state` is a classification, never an authority grant. Every output row's
`authority` block (`investable_eligible`, `paper_eligible`,
`stage_authorized`, `production_authorized`, `trading_authorized`,
`order_authorized`) is hardcoded `false` in code, unconditionally,
regardless of `state`. Turning a classification into real
investable/PAPER/order authority is a separate, later, explicitly-ratified
change this module cannot make -- exactly the same "propose, never ratify"
discipline as `identity/kis_provenance_proposal.py` and
`identity/candidate_identity_authority_proposal.py`.

### Gates, in order

1. **Identity collision** (`identity/upbit_market_identity_proposal.py`'s
   `identity_review_findings()` -- two markets proposing the same
   candidate canonical asset id) -- `BLOCKED`, reason
   `IDENTITY_COLLISION`. Never silently dropped or auto-resolved; every
   blocked market is listed by name in the identity-review findings.
2. **Missing/unavailable captured field** for this specific market
   (`market_all`, `market_event`, `orderbook`, or `candles`) --
   `OBSERVATION_POOL`, reason `MISSING_FIELD:<component>`. A gap in one
   market's data never affects any other market's classification.
3. **Investment warning active** (Upbit's own
   `market_event.warning == true`) -- `OBSERVATION_POOL`, reason
   `INVESTMENT_WARNING_ACTIVE`. This check is hardcoded, not a policy
   toggle a future config edit could weaken; it force-excludes a market
   from `TRADEABLE_UNIVERSE`/`PAPER_ELIGIBLE` regardless of every other
   metric. Upbit's public REST does not separately expose a
   "trading-suspended" or "delisting-notice" flag beyond `market_event`
   and removal from the market list itself; a market that disappears from
   one day's capture to the next simply is not classified that day, and
   its historical presence remains visible in the append-only raw
   evidence under `evidence/crypto/upbit/raw/`.
4. **Identity not ratified/effective** for this market (absent from the
   evidence-bound `config/upbit_asset_identity_registry.json` mapping, or
   evaluated before its `effective_from`) -- `OBSERVATION_POOL`, reason
   `IDENTITY_UNRATIFIED`. The loader revalidates the source packet's file
   hash, payload hash, exact 55 mappings, and held-market exclusion.
5. **Taxonomy not ratified/effective** -- `OBSERVATION_POOL`, reason
   `TAXONOMY_UNRATIFIED`. The document is RATIFIED effective 2026-08-30;
   its individual records retain their original evidence dates.
6. **Taxonomy category excluded or unknown** -- `OBSERVATION_POOL`, reason
   `TAXONOMY_EXCLUDED:<category>` or `TAXONOMY_UNKNOWN`
   (`unknown_asset_policy: fail_closed_unknown`, same discipline as
   `config/crypto_breadth_exclusion_taxonomy.json`).
7. **Policy not ratified/effective** -- `OBSERVATION_POOL`, reason
   `POLICY_UNRATIFIED`. The policy is RATIFIED effective 2026-08-30 and
   carries hard-false authority flags.
8. **Stale capture** (`available_at` older than the policy's
   `max_capture_age_hours` relative to `evaluation_as_of`) --
   `OBSERVATION_POOL`, reason `STALE_CAPTURE`.
9. **Metric thresholds** -- listing history, 30-finalized-day KRW
   turnover, then spread; the first failing check's reason is reported
   (`LISTING_HISTORY_BELOW_THRESHOLD`, `TURNOVER_HISTORY_INCOMPLETE`,
   `TURNOVER_BELOW_THRESHOLD`, `SPREAD_NOT_COMPUTABLE`,
   `SPREAD_ABOVE_THRESHOLD`). Passing all of them reaches
   `TRADEABLE_UNIVERSE`.
10. **Estimated PAPER slippage** -- a volume-weighted walk of the captured
    ask-side depth for the policy's `paper_slippage_estimate_notional_krw`
    versus best ask. `SLIPPAGE_NOT_COMPUTABLE` (captured depth cannot fill
    the notional) or `SLIPPAGE_ABOVE_THRESHOLD` keeps the market at
    `TRADEABLE_UNIVERSE` without `PAPER_ELIGIBLE`; passing reaches
    `PAPER_ELIGIBLE`, reason `PAPER_ELIGIBLE_ALL_GATES_PASSED`.

## PIT-honest metric semantics

- **Listing history**: `observed_daily_candle_count`, the number of daily
  candles Upbit's `candles/days` endpoint actually returned (a floor bound
  on true listing age, capped by the capture's lookback `count`).
- **30-day average turnover**: arithmetic mean of
  `candle_acc_trade_price` over exactly 30 finalized days immediately
  preceding the capture day. The packet retains the 30-day aggregate for
  schema compatibility; the gate divides it by the validated finalized-day
  count before comparing with KRW 5,000,000,000. Today's open candle is
  always excluded.
- **Spread**: a single best-bid/best-ask snapshot at capture time, not a
  true intraday median. Continuous intraday quote freshness/median-spread
  measurement is P4-07/P9-06's job (`execution/intraday_freshness.py`),
  not P3-12's; the field is honestly named to reflect what it actually
  proves -- one PIT sample, not a statistical median over the day.
- **Slippage**: a volume-weighted-average-price estimate from the same PIT
  orderbook snapshot, for a fixed policy notional -- an estimate, not an
  observed fill.

## Evidence and determinism

`evidence/crypto/upbit/raw/<date>/` holds gzip-compressed raw response
bytes (`market/all`, batched `ticker`, batched `orderbook`, and a
per-market `candles/days` bundle), a `_manifest.json` with a SHA-256 per
component and `downloaded_at_utc`, and is append-only --
`upbit_market_capture.py::capture_snapshot()` refuses to overwrite an
existing date. `universe/upbit_tradeable_universe.py::load_snapshot_core()`
reuses the same module's `validate_snapshot()` unchanged, so any tampered
or hash-mismatched raw file is rejected before a single market is
classified.

`build_classification()` is a pure function of its arguments: no
wall-clock or random value inside the classification math itself (only the
capture layer's `available_at` carries a timestamp). The same snapshot,
policy, taxonomy, and identity registry always produce byte-identical
output -- `test_upbit_tradeable_universe.py::test_determinism_same_input_twice_identical_output`.

## Versioning rule

The policy, taxonomy, and identity registry each carry a document-level
effective date. Taxonomy records additionally retain their own original
effective intervals. A later edit requires a new version/effective date;
the 2026-08-30 ratification never changes a classification for an earlier
`evaluation_as_of`.

## Offline commands

```bash
python3 .github/scripts/upbit_market_capture.py \
  --snapshot-root /tmp/upbit-universe/raw --snapshot-date 2026-08-28

python3 .github/scripts/upbit_universe_populate.py 2026-08-28 \
  --raw-root /tmp/upbit-universe/raw --data-root /tmp/upbit-universe/data
```

Neither command calls a network provider outside the four public GET
endpoints listed above, adds a portfolio policy, or opens a Production or
trading path.
