# Candidate evidence and lifecycle receipt

`candidate_evidence_lifecycle_receipt/2` is an evidence-bound sidecar for the
three-market candidate lookup. It makes retained evidence queryable and applies
the ratified, separate system-evaluated Candidate policy without mutating the
manual Watchlist Stage field.

## Connected evidence

- Inclusion rationale comes verbatim from `_watchlist_rows.json`, the retained
  Notion PM Watchlist snapshot. Every row keeps the Notion row URL, source
  collection, capture time, and exact file hash. The source is explicitly
  review-required and never grants Candidate or Stage authority.
- Candidate validity and expiry come from the existing ratified P8-12
  `candidate_validity_window_assessment/1`. The receipt independently verifies
  its hash, authority identity, 172800-second rule, temporal-only scope, and
  closed downstream locks before exposing a result.
- Lifecycle status is a mechanical comparison of the selected
  `data/stage_history.json` snapshot with its predecessor. It emits `NEW`,
  `MAINTAINED`, `PROMOTED`, `DEMOTED`, or `DROPPED`. These labels describe a
  recorded delta; they are not a rule authorizing that change.

Each lifecycle row exposes the observed date and precision, mechanical rule
version, transition-reason evidence state, exact inclusion source, and P8-12
validity result. A changed Stage whose decision reason is absent from
`stage_history.json` remains `NO_EVIDENCE`; the inclusion rationale is not
silently reused as the change reason.

## PIT boundary

The retained Watchlist snapshot was captured on 2026-08-15. Its text is not
shown in a receipt evaluated before that capture date. The P8-12 assessment is
subject to the same rule using its operational evaluation time. Receipt build
time is recorded separately and never replaces source time.

## Unresolved policy, kept unresolved

The repository currently contains a ratified Korea *sector-series identity* to
theme-id binding. It does not contain a ratified security-symbol to sector or
Theme membership. The cross-market Theme authority registry has zero records,
the US membership sources are empty and unratified, and the P2-05 rotation
ledger contract declares its repository state policy `ABSENT`.

Therefore the receipt reports the following fields as `POLICY_UNDEFINED` and
does not synthesize values:

- security symbol to sector/Theme binding;
- symbol-level rotation-ledger link;
- security-to-rotation promotion or exclusion rules.

The gap register distinguishes those policy gaps from two connection gaps now
closed by this sidecar: retained inclusion-reason consumption was
`IMPLEMENTATION_NOT_CONNECTED`, while P8-12 expiry was an existing ratified rule
whose consumer was missing.

## Usage

Build a deterministic receipt for a selected committed evaluation date:

```text
python3 discovery/candidate_evidence_lifecycle_receipt.py \
  --generated-at-utc 2026-09-13T05:30:00Z \
  --as-of-date 2026-09-11
```

Use `--output <path>` only when a caller needs a file. Without it, the receipt
is printed to standard output. The module's `lookup_symbol()` accepts either a
six-digit Korea symbol or its `.KS` form.

No collector, schedule, runner, pin, manual Stage mutation, Ready, Buy, Action,
Order, Production, Trading, or real-capital authority is added. The only opened
authority is system Candidate evaluation and its internal PAPER Stage4 handoff.

## Ratified decision: separate system-evaluated Stage

Status: **ratified and effective from 2026-09-13T05:27:28Z**.

The approved option is **A: Separate System-Evaluated Stage**. The retained
Notion Stage tag remains a source observation, while an independently versioned
system Stage is derived from market-native evaluator fields. The manual tag is
never an input requirement for system promotion.

| Policy element | Ratified rule |
| --- | --- |
| Required conditions | Population membership, resolved identity, market-native evaluation coverage, evidence quality, Translation, Expectations Gap, invalidation, freshness, and no active veto must all be `PASS` |
| Evidence source | Every PASS binds at least one hashed evidence reference and its point-in-time availability; each market keeps its native evaluator contract |
| Data period | No universal lookback is invented; each gate input records its own evaluation and review/expiry time |
| Missing data | `MISSING`, `UNKNOWN`, `STALE`, `FAIL`, or `ACTIVE_VETO` yields `HOLD` at the first required gate; none can become PASS |
| Promotion | All required gates PASS derives system `Candidate` and permits only the internal PAPER Stage4 handoff |
| Manual/system boundary | Manual Notion Stage is retained in the receipt for comparison but is not consumed by the decision expression |
| Demotion / drop | Only a separately ratified market-native invalidation or exit may derive either; missing evidence never does |
| Re-entry | A fresh full evaluation and new evidence references are required |
| Market differences | One envelope, separate market-native contracts, no cross-market score and no security-to-Theme inference |

The immutable bindings are:

- policy: `config/candidate_stage_evaluation_policy_v1.json`;
- current registry: `config/candidate_stage_evaluation_policy_registry.json`;
- explicit approval evidence:
  `evidence/authority/candidate_stage_evaluation_policy_approval_20260913.json`.

The policy adds no numeric investment threshold. Market-native PASS semantics
must arrive through `candidate_stage_gate_input/1`; this receipt does not invent
or duplicate the Korea, US, or Crypto evaluator logic.

### Current first blockers (evidence generation as of 2026-09-12)

This table describes the current reviewed generation only. It is not a claim
that every name was blocked by the same reason for the whole prior month.

| Item | First blocker | Current data position |
| --- | --- | --- |
| HD Hyundai Heavy Industries (`329180`) | Discovery admission and Stage transition rule absent; an obsolete event-date gate also needs factual correction | Official priority evidence was reported in IC #7, but evidence cannot authorize promotion without a rule |
| Hyosung Heavy Industries (`298040`) | Expectations Gap decision rule absent | Inclusion and invalidation rationale are retained; P8-12 temporal state is connectable |
| SanDisk (`SNDK`) | Discovery-to-Candidate and EXIT rules absent | Current state remains unknown; missing evidence cannot be promoted or dropped |
| Hanwha Aerospace (`012450`) | Discovery-to-Candidate rule and security-to-rotation binding absent | P8-12 temporal evidence exists; security membership cannot be inferred from sector-series taxonomy |
| Stage-null Coverage rows | Discovery admission rule and canonical Stage source mapping absent | Retained reasons are queryable where the 2026-08-15 Watchlist snapshot has a row; full-population evaluation remains a separate owner lane |
| TSM (`TSM`, control case) | Not an observation-to-Candidate case; entry language remains undefined | The already ratified P5-07 degradation condition produced an auditable natural hold, demonstrating the difference a ratified rule makes |

The current KR bounded evaluator selects three `supported_pipeline_subjects`.
Four additional Watchlist records have `stage=null` and are excluded even
though their source rows exist. The US bounded evaluator similarly uses a fixed
18-session price slice. These are evaluation-coverage limits, not evidence
that the excluded names failed. Expanding those evaluators belongs to the
separate population owner; this receipt does not duplicate it.

Raw KIS large/mid/small sector codes and US SEC SIC can be source facts, but
they are not a ratified Atlas investment Theme or rotation membership. Option
A must preserve that distinction and may not infer Theme from either code.

## Current re-evaluation

The current repository generation has no connected
`candidate_stage_gate_input/1` records. All 14 observed records therefore return
`HOLD` with
`canonical_population_membership:GATE_INPUT_NOT_CONNECTED`. This is a
connected policy with missing current evaluation input, not an
unratified-policy state and not evidence that the names failed.

Each row now includes `gate_connection_audit`, which identifies the exact
source position, owner, and automatic recovery requirement for all nine gates:

| Gate | Current 14-record position | Owner lane |
| --- | --- | --- |
| canonical population membership | Stage-history coverage observations exist for 14/14, but no ratified adapter emits the Stage gate result | market population evaluator |
| resolved security identity | two resolved source rows are available but not gate-admitted; two rows are explicitly not computable; ten rows are absent | canonical security identity authority |
| market-native evaluation coverage | five symbols are in the bounded KR/US evaluator contracts; nine are outside those bounded contracts | Korea/US market-native evaluator owners |
| evidence quality | no per-symbol ratified gate source for 14/14 | market-native evaluator owners |
| Translation | no current per-symbol gate source for 14/14 | Alpha Review / Translation owner |
| Expectations Gap | no current per-symbol gate source for 14/14 | Expectations Gap evaluator owner |
| invalidation | review prose exists for 12/14 but is not a machine gate; two have no retained source | market-native invalidation evaluator |
| freshness | P8-12 evidence exists for seven Korea rows but is trigger-temporal-only and cannot be reused as a Stage PASS; seven US rows have no Stage freshness source | market-native Stage freshness owner |
| active veto | no current per-symbol gate source for 14/14 | Stage veto policy owner |

No source fact in this audit is converted into PASS. The audit only closes the
diagnostic connection and prevents an existing-but-scope-incompatible source
from being mislabeled as wholly absent.

The Stage4 handoff reuses the existing
`common_paper_candidate_funnel_input/1` contract and binds its exact contract
and schema hashes. A system Candidate alone is not a valid Stage4 row: the
market adapter must still provide score breakdown, completed-bar evidence, all
Stage4 Hard Gates, risk fields, source timestamp, TTL, and source references.
Accordingly the current handoff reports
`RATIFIED_STAGE_POLICY_ACTIVE_NO_SYSTEM_CANDIDATE`, with both system Candidate
and Stage4 eligible counts at zero.

When a market-native evaluator supplies all required current PASS results, the
same receipt derives system `Candidate`, records `PROMOTE`, and lists that symbol
in `system_candidate_symbols`. It still reports
`SYSTEM_CANDIDATE_AVAILABLE_STAGE4_COMMON_FUNNEL_INPUT_REQUIRED` until the
existing Stage4 input contract is satisfied. Ready/Buy, orders, Production,
live trading, and real capital remain closed.
