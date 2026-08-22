# Atlas Session Bootstrap — Canonical Direction and Continuity

This document prevents a new chat, agent, or worktree from restarting Atlas from an incomplete memory. `AGENTS.md` requires every session to read it before acting.

This is a routing document, not a frozen status report. Current status must always be re-read from Notion, `main`, and open PRs.

## Canonical sources

Read these live sources at the beginning of every implementation or CIO review session:

| Source | Canonical reference | Purpose |
| --- | --- | --- |
| CIO Investment Operating Doctrine | Notion page `3c49f2d7-3c84-8164-9f6d-e3662e222c3e` | Objective, portfolio doctrine, continuity contract |
| Master WBS Tracker | Live Notion WBS database; locate rows by WBS ID | Scope, dependency, Exit Gate, status, evidence |
| CIO Cockpit | Notion page `3ba9f2d7-3c84-81d6-953e-c414dafdbff4` | Current operational summary and handoff |
| Master Map | Notion page `3bf9f2d7-3c84-81b8-aa50-f9aa2384655d` | Program-level sequence and dependencies |
| Repository truth | Current `main` HEAD and all open PRs | What is actually merged, pending, or conflicting |

Canonical WBS rows central to the trading path include:

- P5-06 — Probe Entry Rule / Position Sizing: `3c49f2d7-3c84-81a8-8894-f691568187e2`
- P7-02 — portfolio allocation/control: `3bf9f2d7-3c84-819c-9565-f97cf0a2997b`
- P7-10 — Capital Reallocation: `3c49f2d7-3c84-81d9-ac68-fd0830b45356`
- P7-11 — Profit Harvesting / Rapid Gain Realization Engine: `3c49f2d7-3c84-8138-8644-eee246dd713f`
- P8-13 — Entry Proposal: `3c49f2d7-3c84-8106-83ee-d0f390af6860`
- P9-03 — post-trade learning: `3bf9f2d7-3c84-8104-9d23-c5deaa948e9e`

Search the live Tracker for all other rows. Do not infer their current state from this file.

## Stable CIO direction

The first goal is a real trading service that uses systematic briefings to move capital efficiently under human approval. The system must find emerging opportunities early without discarding evidence quality, and it must manage what happens after entry.

Three durable investment layers must remain visible in architecture and WBS:

1. Opportunity and entry: detect movement early, distinguish reflected price from missing data, permit governed probes, and produce an executable entry proposal.
2. Position and profit management: respond to fast gains, thesis acceleration or deterioration, volatility, stops, partial harvesting, and exits.
3. Capital recycling: compare held positions with newly emerging opportunities and deliberately reallocate scarce risk capital.

A briefing that only lists candidates or blocks weak evidence is incomplete. A trade service that enters but cannot harvest or reallocate is also incomplete.

## Mandatory session-start audit

1. Synchronize repository metadata without overwriting user work.
2. Record current main SHA and dirty-worktree state.
3. List open PRs, their exact HEADs, CI state, scope, and overlapping files.
4. Read the Doctrine, applicable WBS rows, Cockpit, and Master Map.
5. Compare live state with the previous handoff; explicitly discard stale claims.
6. Identify the next executable WBS dependency, not merely the most convenient code task.
7. Confirm whether the task changes evidence, recommendation, shadow, paper, or real-order authority.
8. Only then design, delegate, code, or review.

## Implementation and review rules

- Reuse existing contracts and evidence lineage before creating new vocabulary or databases.
- Keep historical audit and live operational decisions separate. Audit-confirmed future returns may assess a miss but may never promote the historical signal that preceded them.
- Preserve raw observations; suppress review overload at the triage layer with explainable, ratified logic.
- `UNKNOWN` must distinguish missing, stale, and genuinely uncertain evidence where the canonical contract permits it.
- Backtests and PIT replay must expose `NOT_COMPUTABLE` instead of manufacturing a population, entry time, or return.
- Realized or hypothetical gains do not themselves prove that an expectation was unreflected.
- A PR may implement a mechanism without completing the WBS Exit Gate; report both separately.

## Fixed delivery order

Unless the live Doctrine and WBS explicitly supersede it:

1. P8-10 Price Reflection and P8-12 Opportunity Trigger / Dynamic Clock
2. P5-06 Probe Entry Rule and portfolio position sizing
3. P8-13 executable Entry Proposal
4. P7-11 Profit Harvesting / Rapid Gain Realization
5. P7-10 Capital Reallocation
6. P10 Shadow validation
7. P11 human-approved paper and limited real-capital operation

Parallel work is allowed only when file ownership, contract dependencies, and merge order are explicit. Parallelism must not invert the dependency order.

## Required handoff record

Every meaningful session must leave a durable handoff in the canonical system containing:

- verified main SHA and open PR HEADs;
- merged, pending, and rejected changes;
- tests and authoritative-gate results;
- canonical WBS rows changed and why;
- observed data limitations and `NOT_COMPUTABLE` boundaries;
- unresolved policy decisions;
- the next executable task and its dependency;
- explicit confirmation of unchanged Stage, Buy, Action, Order, Production, and trading authority.

Update status only in the order `Tracker -> Cockpit -> Master Map`. Chat summaries and local memories are convenience copies, never the sole source of continuity.
