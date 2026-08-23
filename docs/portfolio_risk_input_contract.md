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

`risk_capacity_inputs.total_nav` is computed only when either (a) every
account is in the same currency, or (b) every non-base-currency account has
a **fresh** FX rate on file. A missing or stale rate never produces a
silently-blended estimate -- `total_nav_status` becomes
`NOT_COMPUTABLE_MISSING_FX_RATE` / `NOT_COMPUTABLE_STALE_FX_RATE` and
`total_nav` is `null`. Per-currency amounts and FX provenance
(`rate`/`as_of`/`source`) are always kept as separate fields in
`portfolio_facts.fx_rates`, never blended into a single number.

## Completeness

If the caller declares an `expected_sources` set (e.g. `{"ALPACA_PAPER_ACCOUNT",
"KOREA", "CRYPTO"}`) and any of them is absent from the supplied
`account_facts`, `total_nav_status` becomes `NOT_COMPUTABLE_MISSING_MARKET_DATA`
-- never a total computed from just the available subset.

## Security: no plaintext secrets or account numbers

- API keys/secrets are read from environment variables only, never
  hard-coded, and never appear as literal values anywhere in source.
- A real Alpaca account number is **never** written to any committed
  evidence file. `build_alpaca_paper_account_fact` replaces it with
  `account_id_hash = sha256(account_number)` before the raw number is ever
  seen outside that one function call.

## Evidence layout

Mirrors `collectors/free_market_data.py`:

```
evidence/operational/portfolio_risk_input/raw/<day>/alpaca_account.json.gz     (immutable)
evidence/operational/portfolio_risk_input/raw/<day>/alpaca_positions.json.gz   (immutable)
evidence/operational/portfolio_risk_input/raw/<day>/manifest.json             (immutable)
data/latest_portfolio_risk_input.json                                          (mutable pointer)
```

Captured by `.github/workflows/portfolio-risk-input.yml`
(`workflow_dispatch` + weekday cron), which runs the offline regression
(`test/test_portfolio_risk_input.py`) before the real capture step, exactly
like the free-market-data workflow.

## Counter-example scenarios (all independently tested)

See `test/test_portfolio_risk_input.py` -- one dedicated `TestCase` class
per scenario:

1. Future-dated snapshot vs. a past decision -- rejected (`FUTURE_DATED_SNAPSHOT_REJECTED`).
2. Stale account balance used as current -- flagged (`staleness_status: STALE`, `data_completeness.any_stale`).
3. Duplicate positions -- deduplicated (identical) / rejected (conflicting, `DUPLICATE_POSITION_CONFLICTING_DATA`).
4. Mixed-currency amounts summed without an FX rate -- rejected (`NOT_COMPUTABLE_MISSING_FX_RATE` / `NOT_COMPUTABLE_STALE_FX_RATE`).
5. Manual input disguised as broker-verified -- rejected (`MANUAL_INPUT_DISGUISED_AS_VERIFIED`).
6. Alpaca live vs. paper account confusion -- structurally impossible (hard-coded paper host, no parameter, no live-host string anywhere in the module).
7. Negative or NaN NAV -- rejected (`NEGATIVE_NAV_OR_CASH_REJECTED` / `NON_FINITE_VALUE`).
8. Account-level NAV disagreeing with the sum of positions -- flagged (`nav_reconciliation_status: MISMATCH_FLAGGED`).
9. Total NAV confirmed while some market's data is missing -- rejected (`NOT_COMPUTABLE_MISSING_MARKET_DATA`).
10. Same-timestamp data tampering -- detected (`validate_snapshot` re-hashes and raises `PACKET_HASH_MISMATCH`).
11. Any order-API call attempted from the read-only path -- structurally impossible, proven by a test.
12. Sizing/quantity/weight computed while policy is unratified -- rejected (`POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED`, and `position_size` is a fixed module-level constant, never a function).
13. Any existing authority field flipping to `true` -- rejected (`AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE`).

## What this PR unlocks (not part of this PR)

Connecting stop-distance (`UNRATIFIED_DIAGNOSTIC_NOT_AN_EXECUTABLE_STOP` in
the P5-06/P7-08 packet) to actual account-dollar loss, and then properly
comparing the two-axis P5-06 policy options (Entry Eligibility E1/E2 x
Post-Entry Management M1/M2/M3) against real risk capacity. None of that
sizing/policy math exists in this PR.
