# KRX P8-13 PAPER proposal Bridge

Status: implemented interface and policy-decision packet; authoritative
proposal `NONE`; no ledger, KIS, order, REAL, Production, or trading authority.

## Purpose

This KRX-only Bridge packages one eligible symbol, completed `15m`/`1h`/`1d`
bars, an `ENTER`/`HOLD`/`EXIT` Shadow decision, entry/stop/take-profit/expiry,
planned loss, and the account risk budget into one content-addressed P8-13
proposal. The human briefing and machine proposal contain the same
`evidence_basis_sha256` and `proposal_key`.

Candidate selection, strategy policy, proposal construction, internal-ledger
consumption, KIS mock submission, and REAL authority are separate layers. A
briefing rank or diagnostic Shadow action never grants any downstream layer.

## Verified exact inputs and current result

- Public KRX Gate: PR #483 merge
  `016a2889c503066a3a07180e8d12b9da81869e7b`; assessment
  `780690500832dceffed4ede9059da4c9cb4e8d565042878ee1c503fc3e724e07`.
- Private KIS safety: PR #96 approved head
  `792aa93273e71813cab3ddebe529be69849cfbaa`, merged to private main as
  `273d07e73eb9577c4e5a4edcd241eab2037f3c8f`. It is a prerequisite only and
  grants no proposal or order authority.
- Wave1 Universe interface head (PR #485):
  `e7b7a209d785d63627dc596f4a58581b681b61ad`, merged to public main as
  `acb33d8b1cd866d478ec6c1e49422571fe71a48d`.
- Wave1 Shadow interface head (PR #484):
  `48782fe4892fc12e868bada05a0d82c3bddf6f7e`, merged to public main as
  `7353be0dc26af8d6cacf2115c07d68358b5d607f`.

Although both interfaces are merged, neither merge grants investable-universe,
strategy, or ledger authority and the fixture's decision eligibility is still
`UNKNOWN`. The Shadow action is `NO_TRADE`, the strategy policy is unratified,
`COMMON_SAFETY` is `UNKNOWN`, and effective `KRX_SHADOW` is `UNKNOWN`; the
tracked synthetic fixture therefore deterministically produces proposal `NONE`
with no ledger draft.

## Fail-close rules

- exactly one symbol and at most one open position;
- exact repository, interface version, and source head, with both Wave1 heads
  merged to public main before authoritative consumption;
- symbol identity must match across briefing, Universe, Shadow, position, and
  policy;
- all required bars must be completed, available no later than evaluation, and
  unexpired;
- proposal, policy, Universe, position, and bar windows must be unexpired;
- duplicate Shadow decision keys and proposal keys are blocked;
- `ENTER` requires a flat account slice with zero open positions;
- `HOLD` and `EXIT` require an open position; no-position `EXIT` becomes
  `NONE`;
- planned loss must fit inside the remaining account risk budget;
- a candidate policy is never a ratified policy, and merge/CI never ratifies
  either;
- `COMMON_SAFETY` and effective `KRX_SHADOW` must both pass. The non-NONE
  proposal is then evidence for the downstream `KRX_PAPER_CANARY_START` Gate,
  so the Bridge does not create a circular requirement for that Gate to pass
  first.

Malformed or look-ahead input is rejected. Missing, stale, unmerged,
unratified, duplicate, identity-mismatched, or lifecycle-inconsistent input is
retained as explicit blocker codes and returns proposal `NONE`.

## Policy decision packet

`config/krx_paper_policy_ratification_packet.json` distinguishes three
strategy candidates from a ratified strategy. It contains no invented sample,
expectancy, drawdown, false-probe, or opportunity-cost threshold. Every
required result is `NOT_AVAILABLE` until there is:

1. point-in-time chronological replay;
2. walk-forward out-of-sample evidence;
3. fees, costs, slippage, tick, and gap sensitivity;
4. separate rising, falling, and sideways regime results;
5. missed-upside and avoided-downside comparison; and
6. expiry and full lifecycle outcomes.

Only an explicit effective-dated CIO decision bound to those results may
replace the empty ratified-policy binding. A code merge or green CI cannot.

## Draft and broker boundary

A future valid non-NONE proposal may carry an
`INTERNAL_VIRTUAL_LEDGER_DRAFT_ONLY` object for review. Even that draft states
`consumption_authorized=false`, `submissionCompatible=false`, and false
exchange/order/KIS/PAPER-write/REAL/Production/trading authority. The Bridge
imports no network or broker client and permits no new broker POST. Separate
KRX Gate approval is required before any internal ledger consumes a draft.

## Offline reproduction

```text
python3 test/test_entry_proposal_boundary.py
python3 decision/krx_paper_proposal_bridge.py
```

The fixture contains synthetic, non-sensitive units only. It contains no
account identifier, credential, holding quantity, or real account value.
