# Crypto PAPER Counterfactual Validation Contract

Status: **P10-12 scaffolding / diagnostic preparation only**. This contract does not start official D0, does not advance the canonical P10-12 WBS row, and does not authorize PAPER exit, exchange order, Production, Trading, or REAL capital.

## Purpose and boundary

`validation/crypto_paper_counterfactual.py` prepares the daily and 30-day review machinery required after P10-11 and P7-13 produce a natural virtual BUY-to-SELL cycle. It consumes already-produced evidence and never creates a strategy, candidate, entry, exit, size, fee, slippage, or risk threshold.

Value-bearing reports embed the exact source input so an independent validator can rederive every metric and reject a semantically changed, rehashed report. They must be persisted outside every Git worktree through `persist_daily_report()`. The CLI also rejects a tracked output path. Persistence keeps separate append-only namespaces for natural, manual, replay, and synthetic origins so a diagnostic dispatch cannot splice into the natural official chain.

## Reuse audit

| Need | Reused source | Reuse decision |
| --- | --- | --- |
| fee, slippage, partial fill, cash, position, realized/unrealized P&L | `shadow/crypto_paper_simulator.py` | Reuse the exact ledger and account-state validators; do not duplicate execution math. |
| append-only event identity, idempotency, restartable chain | P10-11 ledger plus private redacted receipt boundary | Treat a validated ledger as authoritative input. Accepted duplicate ledger events remain structurally impossible; attempted duplicates are assessed separately. |
| fixed entry plan and natural SELL lineage | `portfolio/crypto_paper_exit_manager.py` and P7-13 | Consume linked entry/exit order IDs and the entry-time plan hash. Do not select an exit after seeing MFE/MAE. |
| FALSE_POSITIVE, MISS, STALE, SILENT_ERROR semantics | `shadow/error_metrics.py` P10-03 vocabulary | Preserve evidence-bound `PRESENT` / `ABSENT` / `UNVERIFIED`; add `DUPLICATE` for the P10-12 execution review. |
| point-in-time / no-lookahead | `replay/lookahead_gate.py`, `replay/forward_metrics.py` | Enforce `observed_at <= available_at <= report.generated_at`; future source or mark data fails closed. |
| private values and restart proof | `atlas-private-evidence` P10-11 ledger receipt/runtime | Public code validates in memory. Cash, quantity, price, fee, and P&L reports remain outside Git; only a later redacted receipt may cross the public boundary. |

## Required daily artifacts

Every input contains exactly one content-hashed artifact for each role:

1. `D0_GATE` — explicit control artifact; `CLOSED` is the default.
2. `LEDGER` — exact P10-11 hash-chain ledger.
3. `ACCOUNT_STATE` — exact P10-11 account state embedding the same ledger hash.
4. `NAV_SERIES` — point-in-time NAV observations used for MDD.
5. `MARK_SERIES` — point-in-time marks used for MFE/MAE.
6. `PLANNED_LOSS` — entry-order plan lineage fixed no later than submission.
7. `ERROR_ASSESSMENT` — evidence-bound false-positive, miss, stale, duplicate, and silent-error assessments.

Each payload SHA is independently recomputed. The account state's embedded ledger must match the supplied ledger exactly. Planned loss must precede entry and match the entry intent's exact plan reference/hash. Re-signing a changed output does not work because `validate_daily_report()` rebuilds it from the embedded source input.

## Metrics and interpretation

- **No-trade benchmark:** always zero P&L. The reported delta is the PAPER ledger's final NAV minus initial virtual cash. It cannot promote a past candidate.
- **Net P&L:** already includes the simulator's actual fee, VWAP/slippage, and partial-fill path. Fee total, estimated VWAP-versus-best slippage cost, fill count, and partial-fill count remain separately visible.
- **MDD:** peak-to-trough amount and percentage from the supplied PIT NAV series.
- **MFE/MAE:** for each pre-linked entry order, marks available by the report cutoff are compared with the executed entry VWAP. A linked SELL determines the window end; MFE/MAE never selects the SELL rule.
- **Planned versus realized loss:** the entry-time plan is compared with realized loss from the explicitly linked SELL fills. Open or partially closed positions remain labeled, not fabricated as complete.
- **Errors:** `PRESENT / ABSENT / UNVERIFIED` counts and verified rate for false positive, miss, stale, duplicate, and silent error. A zero verified denominator produces `null`, never zero.

## Sample-origin contract

| Origin | Meaning | Official count eligibility |
| --- | --- | --- |
| `NATURAL_AUTOMATED` | Unforced scheduled 24/7 runtime evidence | Only after an explicit OPEN D0 gate. |
| `MANUAL_OBSERVATION` | Human-dispatched operational observation | Never countable. |
| `PIT_REPLAY` | Historical point-in-time replay | Never countable. |
| `SYNTHETIC_FIXTURE` | Deterministic lab/tamper fixture | Never countable. |

All four origins can exercise diagnostics. Manual, replay, and synthetic results cannot be relabeled or backfilled into the official chain, even if an OPEN gate exists.

## Official D0 checklist

D0 remains closed until one separately retained, content-hashed gate artifact records every prerequisite as true:

- exact approved public/private code pins and rolling-observation lineage verified;
- one natural eligible candidate followed by a later-book virtual BUY fill;
- one natural P7-13 virtual SELL and cash/position/P&L reconciliation;
- restart recovery verified on the operational host;
- a full 24-hour run with zero silent errors verified;
- explicit CIO D0 ratification recorded.

Opening the gate is an external governance action. This module has `official_d0_automatic_start_authorized=false`. The first countable report must be `NATURAL_AUTOMATED`, dated on or after the ratified D0 date, and is Day 1. No prior report can be backfilled.

## Thirty-day CIO review

`build_cio_review()` requires one exact append-only chain containing Days 1 through 30, all natural, consecutive calendar days, and bound to the same OPEN gate hash. Anything else is `PREVIEW_OR_INCOMPLETE_NOT_OFFICIAL`.

Even a valid 30-day chain yields only `READY_FOR_CIO_REVIEW_NOT_LIVE_AUTHORIZED`. It does not complete P10-12, open Live review automatically, change thresholds, or authorize an exchange call. Live remains a separate user-approved WBS Gate with least-privilege API, order limits, dual kill switches, runbook, and rollback proof; withdrawal authority remains permanently excluded.

## Commands

All paths containing value-bearing packets must be outside the repository:

```bash
python3 validation/crypto_paper_counterfactual.py daily --input /secure/input.json --output /secure/report.json
python3 validation/crypto_paper_counterfactual.py append-daily --input /secure/report.json --state-root /secure/p10-12
python3 validation/crypto_paper_counterfactual.py review --reports /secure/p10-12/daily/*/*.json --review-id P10.12.CIO.1 --generated-at 2026-10-02T00:00:00Z --output /secure/cio-review.json
```

The shipped regression is synthetic and offline:

```bash
python3 validation/tests/test_crypto_paper_counterfactual.py
```
