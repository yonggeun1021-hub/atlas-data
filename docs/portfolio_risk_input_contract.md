# Portfolio Risk Input Contract

Status: `DESIGN_DRAFT` implementation, PR not merged. WBS: P5-06 / P7-08 (`🟡 개발중`, no new row).

## Purpose

This is **not** "decide how much to buy." It supplies the real, PIT-safe
account facts (NAV, cash, positions, exposure) that a **future** sizing /
policy decision will need. Risk-budget percentages, stop-loss caps,
max-concurrent-Probe counts, and any other policy number are **not**
ratified or implemented by this module. See the CIO's own §8 recommendation
in the P5-06/P7-08 policy comparison packet (Notion, `3c59f2d7-3c84-81c9-a297-ff26fe229c29`,
v2.1) for the origin of this task.

## Physical separation of concerns

Every snapshot this package builds carries four top-level, physically
separate keys -- never merged:

| Key | Meaning | This PR's value |
|---|---|---|
| `portfolio_facts` | Real observed account facts (Alpaca paper account/positions, or explicitly-labeled manual snapshots) | Real data |
| `risk_capacity_inputs` | Inputs a future policy calculation will consume (NAV/cash/exposure breakdowns, completeness/staleness) | Real data |
| `risk_policy` | A ratified risk policy | Always `{"approval_status": "UNRATIFIED"}` |
| `position_size` | A computed position size | Always `{"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED"}` |

## Data sources

- **Alpaca Paper account** (`portfolio_risk/alpaca_client.py`): `GET /v2/account`,
  `GET /v2/positions` against `https://paper-api.alpaca.markets`
  (the trading/account host -- distinct from `https://data.alpaca.markets`,
  the market-data host already used by `collectors/free_market_data.py`).
  Credential pattern reused verbatim: `ALPACA_API_KEY`/`ALPACA_API_SECRET`
  via `os.getenv(...)`, same header pair. No new secrets mechanism.
- **Manual/fixture snapshots** (`portfolio_risk/portfolio_snapshot.build_manual_account_fact`):
  for accounts not connected this way today (Korea, Crypto). Always
  force-labeled `verification_status: PAPER_OR_MANUAL_UNVERIFIED` -- a
  caller cannot disguise a manual entry as `BROKER_VERIFIED` (that raises
  `MANUAL_INPUT_DISGUISED_AS_VERIFIED`).

## Structural (not conventional) order-API safety

`portfolio_risk/alpaca_client.py` can never issue an order-creation /
modification / cancellation call:

1. `PAPER_API_BASE` is a hard-coded module constant, never a parameter.
2. `_get()` is the only function in the module that opens a network
   connection, and it never passes `data=`/`method=` to
   `urllib.request.Request` -- a GET by construction.
3. Every fetch function hits a path from `ALLOWED_PATHS =
   frozenset({"/v2/account", "/v2/positions"})` only, checked **before**
   any network call. `/v2/orders` is not a member and no function in the
   module can reach it.

Proven in `test/test_portfolio_risk_input.py::CounterExample11NoOrderApiCallPossible`.

## FX / currency separation

Every cross-currency total (`connected_scope_nav`, `full_portfolio_nav`,
`total_cash_base_currency`, `gross_exposure_base_currency`,
`net_exposure_base_currency`) is computed only when either (a) every
account is in the same currency, or (b) every non-base-currency amount has
a **fresh** FX rate on file (via the single shared conversion path,
`_convert_amount_to_base`/`_aggregate_base_currency`). A missing or stale
rate never produces a silently-blended estimate -- the field's own
`*_status` becomes `NOT_COMPUTABLE_MISSING_FX_RATE` /
`NOT_COMPUTABLE_STALE_FX_RATE` and the value itself is `null`. Per-currency
raw amounts (`exposure_by_currency`, `cash_by_currency`) are **never**
summed across currency, regardless of whether the base-currency total
succeeded -- they are reported separately in every case. FX provenance
(`rate`/`as_of`/`source`) is always kept as separate fields in
`portfolio_facts.fx_rates`, never blended into a single number.

## Completeness / Account Scope Registry

Account scope is **not** a caller-suppliable parameter -- there is nothing
in `assemble_snapshot()`'s signature a caller could pass to shrink it.
`CANONICAL_ACCOUNT_SCOPE = frozenset({"ALPACA_PAPER_ACCOUNT", "KOREA", "CRYPTO"})`
is a fixed module-level registry. `risk_capacity_inputs.account_scope_label`
always says exactly what population is actually connected
(`US_PAPER_ACCOUNT_SCOPE_ONLY`, `FULL_CANONICAL_ACCOUNT_SCOPE`, or
`PARTIAL_ACCOUNT_SCOPE:<markets>`), and `full_portfolio_nav` /
`full_portfolio_nav_status` is non-null **only** when every canonical
market is present -- an Alpaca-only connection is `connected_scope_nav`
(labeled `US_PAPER_ACCOUNT_SCOPE_ONLY`), never presented as the full
portfolio total. `full_portfolio_nav_status` is
`NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE` whenever any canonical market is
absent.

## Stale / mismatched data forces the WHOLE risk block NOT_COMPUTABLE

If **any** connected account is stale (`staleness_status: STALE`, i.e. its
`captured_at` is more than 24h before `decision_at`) or has a NAV/positions
reconciliation mismatch (`nav_reconciliation_status: MISMATCH_FLAGGED`),
`risk_capacity_inputs.status` becomes
`NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT` and **every** numeric field in
the block (`connected_scope_nav`, `full_portfolio_nav`,
`total_cash_base_currency`, `gross_exposure_base_currency`,
`net_exposure_base_currency`, `existing_position_count`, and all the
breakdown lists) is `null`/empty -- not merely flagged while a number keeps
getting computed anyway.

## Security: no plaintext secrets or account numbers

- API keys/secrets are read from environment variables only, never
  hard-coded, and never appear as literal values anywhere in source.
- A real Alpaca account number is **never** written to any committed
  evidence file -- neither the normalized manifest nor the raw payload.
  `build_alpaca_paper_account_fact` replaces it with
  `account_id_hash = sha256(account_number)` for the normalized fact.
  Separately, `capture.py` runs `portfolio_snapshot.sanitize_for_raw_evidence()`
  on the raw Alpaca `/v2/account`/`/v2/positions` response bodies --
  recursively stripping `account_number`/`id` from any nesting depth --
  **before** they are ever gzip-compressed or written to disk. (An earlier
  version of this module stored the untouched raw response verbatim; gzip
  is not encryption. See "CIO review round 1" below.)

## Evidence layout

Content-addressed, genuinely append-only (mirrors the immutable-evidence
half of `collectors/free_market_data.py`, but with per-run collision
safety `free_market_data.py` doesn't need):

```
evidence/operational/portfolio_risk_input/raw/<day>/alpaca_account-<sha16>.json.gz     (immutable, sanitized)
evidence/operational/portfolio_risk_input/raw/<day>/alpaca_positions-<sha16>.json.gz   (immutable, sanitized)
evidence/operational/portfolio_risk_input/raw/<day>/manifest-<sha16>.json              (immutable, = packet_sha256[:16])
data/latest_portfolio_risk_input.json                                                  (mutable pointer)
```

Every filename embeds the sha256 of its own bytes. Re-running with an
identical snapshot reproduces the identical filename and is a
byte-identical no-op (`capture._write_append_only_or_noop`); a genuinely
different snapshot gets a genuinely different filename, so both survive; a
path colliding with *different* content (should be structurally
impossible given content-addressing) is a hard failure, never a silent
overwrite.

Captured by `.github/workflows/portfolio-risk-input.yml`
(`workflow_dispatch` + weekday cron), which runs the offline regression
(`test/test_portfolio_risk_input.py`) before the real capture step, exactly
like the free-market-data workflow.

## CIO review round 1 (2026-08-23) -- 6 defects fixed

An independent CIO code review of the first version of this PR found 4 P0
and 2 P1 defects, all fixed in the same PR (see
`test/test_portfolio_risk_input.py` for the regression proving each):

| # | Defect | Fix | Regression |
|---|---|---|---|
| P0-1/2 | Raw Alpaca response (incl. real `account_number`) committed verbatim, gzip is not encryption | `sanitize_for_raw_evidence()` strips forbidden keys recursively, applied before every compression | `CounterExampleAccountNumberNeverInRawEvidence` (decompresses the actual stored gzip and scans it) |
| P0-2 | Evidence not actually append-only -- same-day runs overwrote each other | Content-addressed filenames (`<name>-<sha16>.json[.gz]`); identical content = no-op, different content = new file, colliding-but-different content = hard failure | `EvidencePublishTests::test_rerun_with_identical_snapshot_is_a_byte_identical_noop`, `::test_two_genuinely_different_snapshots_same_day_both_preserved`, `::test_collision_with_genuinely_different_content_at_same_path_hard_fails` |
| P0-3 | `total_cash`/`gross_exposure`/`net_exposure` summed raw amounts across currency | Only explicit `*_base_currency` fields are cross-currency (FX-safe, same missing/stale-rate rule as NAV); raw per-currency breakdowns never blended | `CounterExample04MixedCurrencyNoFx::test_missing_fx_rate_blocks_base_currency_totals_but_never_blends_raw` |
| P0-4 | `total_nav_status=OK` reachable even when stale/mismatched (numbers kept computing) | ANY stale account or NAV mismatch forces the WHOLE `risk_capacity_inputs` block to `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT` -- every field `null`/empty | `CounterExample02StaleBalance`, `CounterExample08NavPositionMismatch` |
| P1-5 | Caller-supplied `expected_sources` could arbitrarily shrink account scope | `expected_sources` parameter removed entirely; scope is the fixed `CANONICAL_ACCOUNT_SCOPE` registry | `CounterExample09PartialMarketMissing::test_assemble_snapshot_has_no_expected_sources_parameter_to_shrink` |
| P1-6 | An Alpaca-only NAV could read as a complete total | `account_scope_label` (`US_PAPER_ACCOUNT_SCOPE_ONLY` etc.) + separate `full_portfolio_nav`/`full_portfolio_nav_status` (`NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE` unless full canonical scope connected) | `CounterExample09PartialMarketMissing::test_alpaca_only_never_presented_as_full_portfolio` |
| P0-7/8 | `validate_snapshot()` only re-hashed -- a re-signed tamper (value changed + hash regenerated) passed | Independently RE-DERIVES `risk_capacity_inputs` from `portfolio_facts` via the same `_compute_risk_capacity_inputs()` used to build it, and compares field-by-field | `CounterExampleReSignedSemanticTamperRejected` (3 tests) |

## Counter-example scenarios (all independently tested)

See `test/test_portfolio_risk_input.py` -- one dedicated `TestCase` class
per scenario:

1. Future-dated snapshot vs. a past decision -- rejected (`FUTURE_DATED_SNAPSHOT_REJECTED`).
2. Stale account balance used as current -- rejected (forces the whole `risk_capacity_inputs` block to `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT`, not just flagged).
3. Duplicate positions -- deduplicated (identical) / rejected (conflicting, `DUPLICATE_POSITION_CONFLICTING_DATA`).
4. Mixed-currency amounts summed without an FX rate -- rejected (`NOT_COMPUTABLE_MISSING_FX_RATE` / `NOT_COMPUTABLE_STALE_FX_RATE` on every `*_base_currency` field; raw per-currency breakdowns never blended).
5. Manual input disguised as broker-verified -- rejected (`MANUAL_INPUT_DISGUISED_AS_VERIFIED`).
6. Alpaca live vs. paper account confusion -- structurally impossible (hard-coded paper host, no parameter, no live-host string anywhere in the module).
7. Negative or NaN NAV -- rejected (`NEGATIVE_NAV_OR_CASH_REJECTED` / `NON_FINITE_VALUE`).
8. Account-level NAV disagreeing with the sum of positions -- rejected (forces the whole `risk_capacity_inputs` block to `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT`, not just flagged).
9. Total NAV confirmed while some market's data is missing -- rejected (`full_portfolio_nav_status: NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE`; a partial scope is only ever `connected_scope_nav`, explicitly labeled, never presented as the full portfolio).
10. Same-timestamp data tampering -- detected, including a **re-signed** tamper (value changed + hash regenerated to match) via independent semantic re-derivation, not just a hash check (`SEMANTIC_TAMPER_DETECTED`, falling back to `PACKET_HASH_MISMATCH` for anything the semantic check doesn't cover).
11. Any order-API call attempted from the read-only path -- structurally impossible, proven by a test.
12. Sizing/quantity/weight computed while policy is unratified -- rejected (`POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED`, and `position_size` is a fixed module-level constant, never a function).
13. Any existing authority field flipping to `true` -- rejected (`AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE`).

## What this PR unlocks (not part of this PR)

Connecting stop-distance (`UNRATIFIED_DIAGNOSTIC_NOT_AN_EXECUTABLE_STOP` in
the P5-06/P7-08 packet) to actual account-dollar loss, and then properly
comparing the two-axis P5-06 policy options (Entry Eligibility E1/E2 x
Post-Entry Management M1/M2/M3) against real risk capacity. None of that
sizing/policy math exists in this PR.
