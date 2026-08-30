# KRX PAPER market Gate contract

Status: implemented Gate-separation mechanism; no strategy, PAPER-order, REAL,
Production, or trading authority is granted by this document.

As-of: 2026-08-30 08:00 UTC

Audited revisions:

- public `atlas-data`: `3cf338e3572d456340328d1f8dda8daaaaa3ad33`
- private `atlas-private-evidence`: `c1473a27ce7b721ebdd0f1d7f7c703eddfafc25b`

## Outcome

The evidence-derived current KRX state is **`LOCKED`**. `COMMON_SAFETY` is
`UNKNOWN` because the exact KIS PAPER protocol/account cancel, query, and
request-field compatibility has no accepted natural verdict. `KRX_SHADOW`
passes its own checks but is effectively `UNKNOWN` behind that common
prerequisite. `KRX_PAPER_CANARY_START` independently fails because the KRX
final candidate, entry, hold/exit, position-size, and P8-13 executable PAPER
proposal authorities are not ratified or present. The current
machine-readable result is
[`assessment.json`](../evidence/krx_paper_gate/2026-08-30/assessment.json).

Crypto and US readiness are diagnostic context only and do not enter any KRX
market Gate calculation. Any `COMMON_SAFETY` result other than `PASS` keeps KRX
at `LOCKED`.

## Canonical-source audit and reclassification

The audit used the live CIO Doctrine, Master WBS Tracker rows, Cockpit, Master
Map, both repository `main` revisions, open PRs, and the scheduled KIS PAPER
canary obligation. Repository code and Notion evidence agree on the following
scope classification:

| Slice | Reclassification | What the evidence proves | What it does not prove |
| --- | --- | --- | --- |
| P10-08 Human-approved KIS Paper Order Pilot | **Complete for its bounded scope** | Human-approved KIS PAPER order/fill/reconciliation mechanics, private credential boundary, idempotency, and restored locks | Strategy selection, unattended automation, or REAL authority |
| P10-09 PAPER Shadow Strategy Runner | **Complete for its bounded scope** | Signed realtime, fail-closed `WAIT`, read-only history, null quantity/order draft | Ratified entry/hold/exit/size rules or a broker order |
| P10-10 PAPER Counterfactual Policy Lab | **Complete diagnostic mechanism** | Versioned counterfactual `WOULD_*` diagnostics and natural minute evidence | Ratified policy; its authoritative action remains `WAIT` |
| KIS provider/valuation evidence | **Partial downstream prerequisite** | Exact PAPER provider tuple and valuation/freshness semantics are available on current public `main` | Candidate, risk, size, action, or order authority |
| KIS protocol/account safety compatibility | **Partial; open safety finding** | Private PR #96 identifies the current PAPER transaction profile and forbids legacy fallback | Accepted natural cancel/query/request-field compatibility verdict; PR #96 is not merged into audited private `main` |
| P5-06/P7-08/P7-02/P7-11/P8-13 KRX decision chain | **Incomplete for PAPER entry** | Fail-closed review/readiness mechanisms exist | Candidate validity, entry, hold/exit, size, non-NONE proposal, or order authority |
| KRX strategy-driven internal virtual ledger | **Incomplete / evidence unknown** | No accepted isolated-main terminal KRX strategy receipt was found | PAPER_CANARY start |
| KIS mock-account automated order | **Incomplete / evidence unknown** | Human-approved P10-08 lifecycles are not reclassified as automation evidence | PAPER_ACTIVE authority |
| 30-natural-calendar-day PAPER validation | **Incomplete** | D1/minute evidence exists | `PAPER_VALIDATED` or `LIVE_REVIEW` |

The three P10 WBS rows should remain complete for the exact mechanisms they
delivered. The KRX market state is separately `LOCKED`; completion of those
mechanisms must not be read as completion of the market Gate.

## State machine

```text
LOCKED
  -> SHADOW
  -> PAPER_CANARY
  -> PAPER_ACTIVE
  -> PAPER_VALIDATED
  -> LIVE_REVIEW
```

State advancement is monotonic within one assessment and requires every prior
Gate to pass. Missing evidence is `UNKNOWN`; a known unmet condition is
`FAIL`. A self-rehashed result is not sufficient—the validator rederives the
entire assessment from the exact evidence input and both contracts.

`LIVE_REVIEW` is deliberately not `LIVE`. Every state, including
`LIVE_REVIEW`, pins REAL capital, live-account order, Production, and trading
authority to `false`.

## COMMON SAFETY and KRX market Gate separation

[`krx_paper_common_safety_gate_contract.json`](../config/krx_paper_common_safety_gate_contract.json)
defines market-independent safety checks as applied to this KRX lane:

- REAL/live route hard-false
- PAPER endpoint allowlist
- exact KIS PAPER protocol/account compatibility
- private credential and redaction boundary
- static write lock and runtime kill switch
- idempotency and broker reconciliation
- PIT and immutable semantic lineage
- evidence/Shadow/PAPER/REAL authority separation

[`krx_paper_market_gate_contract.json`](../config/krx_paper_market_gate_contract.json)
defines only Korea-specific strategy, ledger, KIS PAPER, natural-sample, and
review inputs. Each check declares its input, required evidence, failure
reason, and approval authority.

No Crypto or US Gate ID appears in the required KRX check set. Other-market
status can be retained in `other_market_context`, but changing it cannot change
the KRX state. The focused regression proves this isolation.

## Current Gate results

| Gate | Result | Basis |
| --- | --- | --- |
| `COMMON_SAFETY` | **UNKNOWN** | Private PR #96 leaves exact KIS PAPER cancel/query/request-field compatibility without an accepted natural verdict |
| `KRX_SHADOW` | **UNKNOWN** (`own_status=PASS`) | P10-09/P10-10 satisfy this Gate's own checks, but the common prerequisite is not PASS |
| `KRX_PAPER_CANARY_START` | **FAIL** | Candidate validity, entry, hold/exit, size, P8-13 proposal, and internal-ledger canary evidence are absent/unratified |
| `KRX_PAPER_ACTIVE` | **FAIL** | Upstream canary Gate fails; KIS mock automation approval and terminal bounded canary remain unknown |
| `KRX_PAPER_VALIDATED_30D` | **FAIL** | Upstream active Gate fails and 30 natural calendar days are not complete |
| `KRX_LIVE_REVIEW` | **FAIL** | Upstream 30-day validation fails; review packet and user acceptance are unknown |

The effective downstream result is `FAIL` when an upstream Gate is known to
fail. Each row also exposes `own_status`, so an unobserved downstream slice is
not falsely described as independently tested.

## PAPER_CANARY start versus PAPER_VALIDATED

`PAPER_CANARY` does **not** require 30 days. It requires the fully ratified KRX
decision loop, a non-NONE P8-13 PAPER proposal, explicit internal-ledger canary
authority, and restart-safe reconciliation.

`PAPER_VALIDATED` is later and requires all of the following:

- 30 forward-only natural calendar days; replay, manual, synthetic, and
  backfilled days do not count;
- complete lineage for PASS, FAIL, WAIT, no-candidate, and outage outcomes;
- an outcome-independent KRX validation policy approved before grading; and
- independent rederivation and PASS.

This contract creates no return, drawdown, win-rate, order-count, or error-rate
threshold. Those criteria require a separate explicit policy decision.

## Two PAPER substages and authorities

The state and authority contract intentionally distinguishes:

1. `INTERNAL_VIRTUAL_LEDGER_PAPER` — available only after
   `KRX_PAPER_CANARY_START`. It may write the Atlas internal PAPER ledger but
   cannot submit to KIS.
2. `KIS_MOCK_ACCOUNT_AUTO_ORDER` — available only after the separate
   `KRX_PAPER_ACTIVE` Gate. It requires a separately approved mock-account
   automation policy, terminal bounded KIS canary, and automation kill-path
   evidence.

Passing the first authority never implies the second. Neither implies REAL.

## Minimum work sequence to open the next Gate

1. Review and merge the private KIS safety correction, then collect a redacted
   natural read-only compatibility verdict for the exact approved PAPER
   profile/account: exchange code, cancel, query, and request-field semantics.
   Keep KRX `LOCKED` until the independent validator accepts it.
2. Ratify the existing KRX-only final-candidate, Candidate Validity, entry,
   hold/exit, and size policy inputs. Reuse existing P5/P7/P8 vocabulary; do not
   invent thresholds in this Gate layer.
3. Produce and independently rederive one non-NONE P8-13 PAPER proposal with
   exact candidate/rule/account-fact lineage and all REAL authority false.
4. Approve one exact `INTERNAL_VIRTUAL_LEDGER_PAPER` canary and prove intent,
   simulated fill, position management, exit, duplicate handling, and exact
   restart reconciliation. This opens `PAPER_CANARY` only.
5. Separately review and approve the KIS mock-account automation scope. Run a
   bounded broker canary from a clean accepted revision, reconcile it to a
   terminal state, restore locks, and prove kill/stale/ambiguous-submit paths.
   This opens `PAPER_ACTIVE` only.
6. Start the 30-natural-calendar-day forward window. Inventory every scheduled
   outcome without backfill and grade it only against separately ratified,
   outcome-independent criteria.
7. After independent PASS, enter `PAPER_VALIDATED`; prepare `LIVE_REVIEW` as a
   review-only packet. REAL/live-order authority remains false under this
   contract.

## Active-work and scheduling boundary

The local KIS PAPER canary heartbeat is **PAUSED**, with no replacement
schedule. It targets a separate, very dirty reference checkout. This change
did not edit, execute, or consume that checkout. A future receipt is diagnostic
until it is tied to a clean accepted revision and passes this evidence
contract. A heartbeat cannot update the WBS or Gate to PASS.

## WBS synchronization rule

No duplicate WBS row is required. Keep P10-08, P10-09, and P10-10 scoped to
`Market=Korea` and append this independent KRX market-state evidence to those
rows. Status changes, when supported by future evidence, must be synchronized
in the order Tracker → Cockpit → Master Map. Crypto rows and summaries are out
of scope.
