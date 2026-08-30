# KRX PAPER 5-1 — one-symbol PAPER_CANARY policy ratification proposal

Status: `UNRATIFIED_PROPOSAL_ONLY` / authoritative symbol `NONE` / state
`LOCKED`.

This packet is a CIO decision surface, not a ratification, Gate PASS, virtual-
ledger authorization, PAPER order authorization, or trading authority.  It
does not modify
[`config/krx_paper_policy_ratification_packet.json`](../config/krx_paper_policy_ratification_packet.json):
that existing packet remains `UNRATIFIED_EVIDENCE_INCOMPLETE`, has no effective
date, and keeps every authority field false.

## Audit cut and repository state

The audit cut is `2026-08-31 00:14:05 KST`, before the 2026-08-31 KRX regular
session.  The public repository readback was
`0421b473957e318a7642524b41820306455e5f41`.

| Lane | Exact merged input | What it proves | What it does not prove |
| --- | --- | --- | --- |
| Universe | PR #485 merge `acb33d8b1cd866d478ec6c1e49422571fe71a48d` | PIT identity and categorical screening interface | Investability or symbol-selection authority |
| Completed market data | PR #488 merge `37e07659aa934e9a6e09b27b786dc49203060af1` | `15m`/`1h`/`1d` completed-bar construction contract; `4h` remains unratified | A natural 2026-08-31 bar packet or ratified Korea freshness threshold |
| Execution measurements | PR #491 merge `7446cc2ba8261ab09cf40cd4daf2d3fe1a1bb17e` | Exact-session KRX turnover plus KIS ten-level depth/spread/slippage measurement contract | Any liquidity threshold, order notional, or eligibility promotion |
| Shadow | PR #484 merge `7353be0dc26af8d6cacf2115c07d68358b5d607f` | Deterministic non-authority `ENTER/HOLD/EXIT/NO_TRADE` diagnostic surface | Strategy, size, selection, order, or PAPER authority |
| P8-13 Bridge | PR #490 merge `f70249c306c3d069d7c3a549ac7c87f4f2bcf37f` | Exact merged Universe/Shadow pins and fail-closed proposal packaging | A non-`NONE` proposal or policy ratification |
| Public merge-main CI | run `33317349022`, SUCCESS at `2026-08-30T15:11:27Z` | Full repository merge regression at the #490 merge commit | Operational Gate PASS or authority |
| Private READ_ONLY reconciliation | PR #106 merge `196dfd3b1380f60673d84cd3df80a586f691ea85` | Private-only restart/reconciliation mechanism and fail-close semantics | Ledger consumption, order submission, or account equality |
| Private P8-13 lineage | PR #107 merge `b8f0538877ed149f2ad5e4ad59a3232722ce21ef` | Consumption of merged public P8-13 lineage | Permission to modify private ledger/reconciliation code or start a canary |

Private #106/#107 are references only.  This public proposal does not modify,
copy, or expose their account facts, positions, quantities, values, order IDs,
ledger, or reconciliation records.

## Authority-bearing one-symbol eligibility: exact required inputs

A future selector may return one authoritative symbol only after all of the
following inputs are independently validated and bound to one evaluation
instant:

1. Current KIS standard-code identity and KRX six-digit alias, with exact
   `security_id`, no identity collision, current KRX membership, prior
   append-only identity history, and official delisting/status evidence.
2. `CATEGORICAL_CANDIDATE` plus an upstream, authority-bearing
   `decision_eligibility=ELIGIBLE`.  Briefing rank, a six-digit code, or the
   committed 005930 fixture cannot create eligibility.
3. Natural, latest completed and PIT-visible `15m`, `1h`, and `1d` bars.  All
   source availability must precede evaluation, and each validity window must
   still be open.  A 4-hour bar is neither required nor accepted by v1.
4. Exact completed-session natural KRX `ACC_TRDVAL` turnover.
5. Fresh natural KIS `FHKST01010200`, venue `J`, ten-level order book bound to
   the same immutable identity: bid/ask depth, spread, and both-side capacity-
   VWAP slippage at the ratified order notional.
6. Fresh official tick/market-state evidence, including halt, VI, price limit,
   and circuit-breaker semantics where required.
7. Effective-dated, hash-bound thresholds for minimum turnover and depth,
   maximum spread, slippage notional and maximum impact.  Distributions are
   descriptive evidence, not thresholds.
8. `COMMON_SAFETY=PASS`, effective `KRX_SHADOW=PASS`, a non-stale position
   count below the one-position cap, and exact prior selection/proposal keys.
9. Effective-dated strategy, entry, hold/exit, position-size, virtual-NAV,
   planned-loss, exposure, cost/tax, expiry, daily-loss-stop, kill-switch, and
   restart/reconciliation policies.

The deterministic ranking after all eligibility checks is:

```text
turnover descending
→ minimum(bid depth, ask depth) descending
→ spread ascending
→ max(buy slippage, sell slippage) ascending
→ symbol ascending
→ immutable security_id ascending
```

The ranking is not an alpha claim.  It chooses at most one execution-eligible
symbol from an already eligible population.  A missing family, stale source,
unratified threshold, duplicate selection key, or open-position cap returns
`symbol=NONE`, `status=LOCKED`.

## Current measured gaps

The live P3-03 Tracker readback reports 4,390 KIS master records: 3,415
categorical candidates, 944 excluded, and 3,446 final `UNKNOWN`; final
`ELIGIBLE` is 0.  Exact-session KRX turnover coverage advanced to 2,271, but
natural depth, spread, and slippage coverage remain 0.  At this pre-open audit
cut, the 2026-08-31 natural `15m`/`1h`/`1d` packet is also absent.

The current Gate evidence remains `COMMON_SAFETY=UNKNOWN`, effective
`KRX_SHADOW=UNKNOWN`, and `KRX_PAPER_CANARY_START=FAIL`.  Therefore the current
authoritative eligibility result is necessarily `NONE/LOCKED`, regardless of
synthetic fixture results or green CI.

## Recommended values supported by merged contracts

Only three numeric/schedule recommendations are carried forward:

| Item | Recommended value | Exact source | Source effective date |
| --- | --- | --- | --- |
| Symbols | 1 | `krx_paper_proposal_bridge/1` canary scope | 2026-08-30 |
| Open positions | 1 | `krx_paper_proposal_bridge/1` canary scope | 2026-08-30 |
| Completed bars | `15m`, `1h`, `1d`; no `4h` | `krx_completed_market_data/1` | 2026-08-30 |

Stale/duplicate fail-close, an active kill switch until separate authority,
and append-only restart/reconciliation are recommended control semantics from
the merged Gate and private READ_ONLY contracts.  They do not supply a risk
number or order authority.

## CIO decision table: values deliberately not invented

All fields below remain `UNRATIFIED` and `null`.  The choices select a method;
the CIO must separately set any number, evidence window, and effective date.

| Policy axis | Choice A | Choice B | Choice C |
| --- | --- | --- | --- |
| Virtual NAV | Dedicated fixed virtual NAV in KRW | Percentage of a separately ratified total virtual NAV | No NAV; remain locked |
| Planned loss per trade | Fixed KRW cap | Virtual-NAV basis-point cap | Minimum of separately set fixed and NAV caps |
| Single-symbol / gross exposure | Fixed notional caps | Virtual-NAV fraction caps | Minimum of risk-sized notional, natural liquidity capacity, and NAV cap |
| Entry | Previous completed 15m-high breakout with 1h/1d confirmation | Completed-bar pullback/re-entry with 1h/1d confirmation | No entry policy; remain locked |
| Stop | Structural completed-bar invalidation | Fixed basis-point stop | Volatility-scaled stop |
| Profit taking | Two-stage fixed R multiples | First risk recovery then trailing exit | One final target |
| Time expiry | Same-session explicit timestamp | CIO-set completed-session count | Catalyst/thesis explicit timestamp |
| Fee/tax/slippage/tick | Effective KIS account fee schedule + official tax/tick sources | Worst case of effective schedule and natural observed cost envelope | No cost binding; remain locked |
| Daily loss stop | Realized plus open planned loss | Realized-only limit plus no new entries after hit | Minimum of fixed KRW and NAV-basis-point daily limits |

The exact machine-readable choices and null fields are in
[`krx_paper_canary_policy_ratification_proposal.json`](../config/krx_paper_canary_policy_ratification_proposal.json).

## Official source bindings

- KIS identity/status parsers and master endpoints are pinned by
  [`krx_investable_registry_contract.json`](../config/krx_investable_registry_contract.json)
  to the official Korea Investment & Securities repository commit
  `b4e6249714418aa57833d1cbbbced39cbcc5b125`.
- Turnover uses the official KRX daily-stock `ACC_TRDVAL` field for the exact
  completed session, as defined in
  [`krx_execution_measurement_contract.json`](../config/krx_execution_measurement_contract.json).
- Depth, spread, and slippage use official KIS GET
  `/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`,
  `FHKST01010200`, venue `J`, ten levels, from the same pinned official KIS
  repository.
- Fee and sell-tax numbers are not present in the audited canonical packet and
  can change over time.  No value is recommended until an effective-dated
  official/broker schedule is captured and bound.

## Synthetic-only verification

[`krx_paper_canary_eligibility.py`](../decision/krx_paper_canary_eligibility.py)
contains no network, broker, ledger, or order client.  The committed fixture is
explicitly `SYNTHETIC_TEST_FIXTURE`; it tests ordering, completed-bar coverage,
turnover/depth/spread/slippage/tick rejection, stale and duplicate rejection,
position cap, output tamper rederivation, and authority locks.

A successful synthetic calculation may populate only
`diagnostic_selected_symbol`.  The public output still has:

```json
{
  "status": "LOCKED",
  "symbol": "NONE",
  "authority": "ALL_FALSE",
  "order_draft": null,
  "broker_submission": null
}
```

Run the focused regression with:

```text
python3 test/test_krx_paper_canary_eligibility.py
python3 decision/krx_paper_canary_eligibility.py
```

## Ratification and activation boundary

This proposal cannot be made effective by changing `ratified`, an effective
date, or an authority flag in place.  A future decision must bind the complete
evidence packet, chosen policy methods and values, official source dates,
PIT/OOS/cost/regime/lifecycle results, and an explicit CIO decision in a new
reviewed version.  Internal virtual-ledger canary authority is a separate Gate;
KIS POST, PAPER order write, REAL/live-account, real-capital, Production, and
Trading authority all remain false.
