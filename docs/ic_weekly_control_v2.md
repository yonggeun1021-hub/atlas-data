# Weekly IC Control & Learning Loop v2

Mandate: current user instruction and the 2026-09-05 IC6 CIO correction/Cockpit
control-loop mandate. This is a Codex-owned secondary control lane. It consumes
natural probes, preserves unknown KPI deltas, queues bounded system audits and
records operator delivery. It never evaluates entry, sets sizing or opens REAL.

## Ownership and integration

Public base at start: `eda86224f75f8a4801d1e0b4436960c078a6bae0`.
Private main: `201b451ec9f988110e5c6099a20a2aabd04c9609`.
Runtime Regime owners: Claude #584 technical wiring and Codex #585 integration
review. Open public #570/#576/#545/#475 and private #150 are unrelated. No changed
path overlaps these PRs, including their shared `run_all.py`. Dedicated CI runs
this contract without modifying their runner list. No WBS row/status is created
or promoted. The bootstrap Doctrine locator returns a deleted historical page;
current explicit user authorization and live Cockpit IC6 correction define this
bounded implementation. P9-03 remains an unrelated downstream eligibility gate.

Public files contain only redacted diagnostics and reported natural-probe facts.
No account holdings, quantities, cash or P&L values are published. IC6's deeper
RULE_UNVALIDATED/max_position=0% is a canonical claim, NOT a code audit conclusion.
Manual D3/C1/R1/B0 remains provisional. KPI deltas have no manufactured score or
baseline: missing comparison evidence is NOT_COMPUTABLE/null.

## Controller adapter

The live controller is outside Git. `services/ic_weekly_control/install.py`
installs a content-addressed release and a small bridge into that directory.
It backs up exact local integration files and refuses unknown anchors/concurrent
changes. Existing `dispatcher_tick.py` starts its money dispatcher before calling
the IC hook. `atlas-status` adds pending IC decisions to its existing User Action
field, with durable surface receipts. `runtime_ops.py`, `runtime_worker.py`,
`cio_dispatcher.py`, worker task definitions, worktrees and processes are untouched.
No restart, process signal or extra scheduler is needed. IC hooks never dispatch
an LLM worker; all existing worker capacity remains reserved for the money path.

Run the installer from a reviewed checkout:

```
python3 services/ic_weekly_control/install.py --base /path/to/atlas-pm-controller
```

The bridge uses the existing five-minute dispatcher. Immediate controlled ingestion:

```
python3 services/ic_weekly_control/controller_hook.py --base /path/to/atlas-pm-controller --at 2026-09-05T03:00:00Z
```

Private local state lives under `state/ic_weekly_control/`:

- `packets/`: immutable weekly inputs plus IC6 seed, never prose-only.
- `state.json`: controller-consumed safe action queue, routing and User Action outbox.
- `surface_receipts/`: actual atlas-status publication timestamps; not human ACK.
- `projection.json`: read-only summary eligibility hook, no Portal UI changes.
- `scheduler.json`: external weekly IC owner and observed ownership evidence.

Four IC6 actions queue under Codex/control: code-enforcement audit, private PAPER
state/P&L/thesis/exit evidence audit, lifecycle aggregation audit, IC5 routing audit.
QUEUED means accepted for bounded control-lane follow-up, not audited or completed.
The queue is intentionally not injected into money-path worker configuration.
An executor must consume these exact action IDs with explicit disjoint file scope;
this implementation does not pretend a queue receipt is enforcement/P&L evidence.

Only exact booleans `safe_auto_queue=true`, `authority_required=false`, a known
read-only audit kind and secondary nonpreemptive ownership qualify. Policy,
threshold, REAL/order/trading/entry/sizing/TTL/PIT kinds cannot queue. Unknown or
authority-bearing actions surface in User Action and never become worker prompts.
Action identity changes require a new explicit revision ID. Repeated packet/week
execution reuses existing action records, including their progress and receipts.

## Routing and time semantics

Routing fields are created → surfaced_to_cio → acknowledged → decided →
canonicalized. Unknown exact IC5 creation timestamps stay null; the reported
creation date is identified in the purpose field. No midnight time is invented.
A missing historical surface receipt means ROUTING_EVIDENCE_MISSING, never CIO
failure. Today's atlas-status publication is a NEW surface receipt and does not
repair the historical evidence gap or invent acknowledgement. Pending decisions
include historical disposition audits, not automatic demands to sign Entry rules.

All generated_at/observed_at inputs are explicit and timezone-aware. Future
observations cannot grade a prior IC. IC6 expectations are marked retrospective
canonical audit, not falsely described as a preregistered September 4 forecast.
The September 10 revenue assertion is prospective and references existing
canonical P5-07 rules; it provides no new threshold or Entry definition.

Saturday is evaluated in Asia/Seoul. The existing external ChatGPT weekly IC owns
the reporting schedule (IC6 notification and scheduled-prompt evidence). Its exact
scheduler ID cannot be read here and remains null. No duplicate scheduler is
created or enabled. Existing local controller ticks create one durable Saturday
packet carrying unresolved probes and explicitly unknown fresh weekly evidence;
The external owner can atomically deposit a validated public-safe packet in
`state/ic_weekly_control/inbox/`; the existing tick consumes it with identity and
time validation. The source transport from ChatGPT/Notion is external-owned and
is not invented here. Changed packets/actions need new revision IDs.

## Verification and limits

`python3 validation/tests/test_ic_weekly_control.py` covers No Action, queue authority gates,
missing/forged routing delivery, deterministic/future/retrospective time behavior,
unknown KPI values, weekly dedup, private projection exclusion and money-path
nonpreemption, plus an idempotent installation fixture.

DONE for this slice means seed + controller safe queue + actual surfaced receipt +
weekly persistence hook, with money workers undisturbed. It does not mean completed
IC6 audit findings, profitability evidence, canonical lifecycle aggregation, WBS
completion, new investment authority, or merged/deployed public Portal UI.
Auto-merge is not authorized. Human review follows exact-head CI.
