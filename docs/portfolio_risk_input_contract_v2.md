# Portfolio Risk Input Contract v2 (Account Fact) -- Provider/Scope Separation

Status: **MECHANISM_ONLY_PROPOSED_UNRATIFIED** (P0-2B, Atlas
execution-infra track). Independent of, and
does not modify, `docs/portfolio_risk_input_contract.md` (v1) -- v1 stays
exactly as merged and remains the only contract Alpaca capture uses.

## Why a v2, not a v1 extension

v1's account fact (`portfolio_risk/portfolio_snapshot.py`) identifies an
account with a single `source` string that conflates the actual
broker/data provider with the market it covers -- `"ALPACA_PAPER_ACCOUNT"`
(provider=Alpaca, scope=US, implicitly) or `"MANUAL_SNAPSHOT:KOREA"`
(provider=manual, scope=Korea, joined by a colon). `_validate_position_source_identity()`
hard-codes exactly those two shapes; anything else is rejected as
`POSITION_SOURCE_IDENTITY_UNSUPPORTED_ACCOUNT_SOURCE`.

Adding a real broker for Korea (KIS PAPER) the same way v1 already
distinguishes Alpaca from manual data would mean widening that hard-coded
check inside v1's own validator -- which changes what v1 accepts, not
merely what a *new* contract accepts. Per the reviewed direction for this
work: v1's semantics are not touched. Instead, `portfolio_snapshot_v2.py`
defines an independent `portfolio_account_fact/2` contract with `provider`
and `account_scope` as two explicit, separate fields from the start, plus
its own, independent build/validate pair -- not an extension of v1's.

## What v2 is, and is not

- **Is**: a build+validate pair for *one provider's account fact*, with
  the same PIT-timing, dedup, NAV-reconciliation, and position
  source-identity discipline v1 already applies -- reimplemented
  independently in this file rather than importing v1's private helpers
  (matching this codebase's existing convention of small, per-module,
  independently-auditable mechanics).
- **Is not**: a packet-level `assemble_snapshot`/cross-provider
  `risk_capacity_inputs` aggregator. Combining a v2 KIS fact with v1 Alpaca
  facts into one NAV/exposure view is a separate, later decision -- not
  defined here, and not assumed by any code in this file.
- **Is not**: an identity-resolution or canonical-instrument-mapping
  mechanism. A v2 position's `source_asset_id` is the provider's own raw
  identifier (e.g. KIS's `pdno`), transported verbatim. Binding that to an
  already-RATIFIED canonical instrument (`KRX:005930:COMMON`, etc.) is a
  separate, independently-reviewed identity-alias change, tracked apart
  from this contract.

## Implemented provider shapes are not authority

`PROVIDER_IMPLEMENTATIONS` (code) / `provider_implementations` (config)
only define the tuple that this diagnostic builder knows how to parse.
They are not an authority registry and cannot make caller-supplied values
broker-verified. The separately named `provider_authority_records` array
is empty in this PR. Therefore every emitted fact is fixed to
`verificationStatus=PROPOSED_UNRATIFIED`,
`providerAuthorityStatus=PROPOSED_UNRATIFIED`, and
`factUsabilityStatus=NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED`.

| Provider label | Mechanical account scope | Currency | Position source |
|---|---|---|---|
| `KIS_PAPER_ACCOUNT` | `KOREA` | `KRW` | `kis_paper_domestic_balance` |

The fixed tuple prevents a caller from relabelling the implemented parser
to another scope, currency, or source name. It does **not** prove that an
input came from KIS, that `KOREA` is a ratified account scope, or that the
provider/scope edge is operationally usable. Those claims require a later,
separately reviewed authority record with provenance and PIT gates; no such
record is created by this PR.

## Timing: `capturedAt` / `availableAt` / `decisionAt`

Every fact carries `capturedAt` (when the provider observed the account)
and `availableAt` (when that observation was actually usable as a
decision input), in addition to the `decisionAt` supplied at validation
time. The invariant `capturedAt <= availableAt <= decisionAt` is enforced
independently at both build time and validate time -- the validator
re-derives the chain from the fact's own fields and the caller-supplied
`decisionAt`; it never trusts a previously-computed timing verdict stored
on the fact itself. A future-dated `capturedAt`, an `availableAt` before
`capturedAt`, or an `availableAt` after `decisionAt` are all rejected.

## No fabricated valuation fields

This contract's `equity` / `buyingPower` / position `market_value` /
`unrealized_pl` fields exist because a general account-fact contract needs
them -- they are **not yet backed by any real bridge**. The private KIS
PAPER full-account snapshot (`atlas-private-evidence`'s
`kis_paper_full_account_snapshot.py`) deliberately records only
`holdingQuantity` / `orderableQuantity` / `confirmedOrderableCashKrw` per
the "never invent a fact the broker didn't return" discipline. A future
bridge from that snapshot into this contract must either extract a
genuinely KIS-response-confirmed valuation figure first, or leave the
corresponding capacity input `NOT_COMPUTABLE` -- it must never synthesize
`equity`, `buyingPower`, or a position's `market_value` / `unrealized_pl`
from cash plus quantity, a stale price, or a zero default.

## Real data

Same discipline as v1: this repo is PUBLIC. No real NAV/cash/position
value produced by this contract's code ever lands in this repo or GitHub
Actions. Real KIS capture and persistence happen entirely inside the
private `atlas-private-evidence` repo's own root-only Ubuntu runtime
state. The current private main contains a read-only KIS PAPER full-account
snapshot that records broker-returned quantities and orderable cash, but it
does not supply the valuation fields required by this v2 shape and it is not
an authority bridge into this public module.

## Authority

Every diagnostic account fact itself carries
`review_only: true` and every other authority flag hard-`false`; the
consumer validator rechecks the exact block. `orderEligibilityStatus` is
fixed to `NOT_APPLICABLE_READ_ONLY_FACT`, not supplied by a caller. No code
path in this file ever sets authority true. This contract supplies a
mechanically validated **unratified diagnostic** only -- it computes no
risk budget, no position size, grants no order/trading authority, and
cannot be consumed as broker-verified account capacity.
