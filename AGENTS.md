# Atlas Agent Start Contract

This file is the mandatory entry point for every coding or review session in this repository.

## Before acting

Before designing, coding, reviewing a PR, or changing WBS status:

1. Read `docs/ATLAS_SESSION_BOOTSTRAP.md` in full.
2. Inspect the current `main` HEAD, every relevant working tree, and all open PRs in both `atlas-data` and `atlas-private-evidence`. Never assume a pasted handoff is current.
3. Read the live Notion canonical sources named in the bootstrap document: CIO Doctrine, Master WBS Tracker, Cockpit, and Master Map.
4. Find the existing canonical WBS row for the work. Do not create a duplicate row merely because a new chat or agent started.
5. Inspect scheduled workflows and Codex automations that own a future Exit Gate. Record who must consume a success or failure into the WBS.
6. State the applicable mandate, dependencies, Exit Gate, authority boundary, conflicting active work, and the current money-path bottleneck before implementation.

If a required canonical source cannot be read, stop any irreversible or authority-expanding action. Report the missing source and continue only with safe, reversible investigation.

## Source precedence

Resolve conflicts in this order:

1. Current user instruction and ratified CIO Doctrine / policy, subject to the CIO anti-drift rule below
2. Canonical Master WBS Tracker row
3. Code on current `main` plus open, unmerged PR state
4. Cockpit and Master Map summaries
5. Repository/session handoff notes
6. Chat memory or pasted summaries

Lower sources may explain history but may not override higher sources. A new user request may propose a priority change, but it is not automatically a CIO priority override: first compare it with the permanent North Star, current evidence, and money-path bottleneck. If it would materially divert the project, surface the conflict and recommend the better path before implementation.

## CIO North Star / anti-drift constitution

Atlas exists to produce **risk-adjusted excess return that can be honestly validated in natural PAPER and, only after sufficient evidence and explicit user approval, progressed to limited real capital**. More code, more WBS rows, more PRs, more dashboards, and more automation are not outcomes by themselves.

The fixed vertical money path is:

`Regime -> Cross-Market Flow -> Sector/Theme Rotation -> Cash/Reduce/Hedge/Inverse -> Capital Posture -> Candidate -> Entry/Exit/Size -> Natural PAPER -> Profitability Evidence -> Human-approved Limited Real`

Three markets remain intelligence inputs, but first real-capital execution does not wait for every market to reach equal maturity. Start with the execution market whose evidence, PAPER lifecycle, risk controls, and operational path are sufficiently mature; expand later.

The CIO/PM role is independent judgment, not agreement-seeking. User requests are important inputs, but the user is not assumed to be technically or strategically correct in every moment. When a request conflicts with the North Star, current evidence, or the shortest safe path to validated profitability, the CIO must push back, explain the trade-off, and recommend the better direction. The user remains the final authority for actual capital and REAL/live activation.

Before starting any new feature, automation, hardening, refactor, Portal expansion, or governance work, require at least one concrete advance in one of these four CIO KPIs:

- Decision Readiness
- Natural PAPER Readiness
- Profitability Evidence
- Limited REAL Readiness

Exception: a real production incident, material security issue, legal/compliance requirement, or integrity defect that can invalidate current evidence may take precedence.

### Rabbit-hole stop rule

Automation controllers, validation frameworks, hash/type/timestamp hardening, Portal plumbing, and governance tooling must never become independent projects. If one supporting topic expands beyond one bounded implementation slice, stop and re-evaluate whether the next slice advances the vertical money path more than the current top investment-system bottleneck. If not, defer it.

Merged code and green CI prove mechanism quality, not investment completion. Conversely, do not wait for all WBS rows to finish before starting natural PAPER and profitability measurement. As soon as an investment slice can produce honest natural Shadow/PAPER evidence, begin measurement in parallel.

For every material implementation or review, state briefly:

- current investment objective
- why this is the highest-value work now
- which of the four CIO KPIs advances
- what is explicitly not being built now
- Exit Gate
- next money-related milestone
- whether the work is drifting from the North Star

## Current critical-path order

Until the canonical CIO Doctrine and WBS explicitly ratify another order, the active top-level sequence is:

1. P1-COM-05 Regime Decision Authority: market-specific signed normalization, common aggregation, PIT replay, runtime consumer wiring
2. P2 Cross-Market Flow and transition semantics
3. US/Korea/Crypto Sector/Theme Rotation using market-native evidence plus approved cross-market value-chain links
4. P6 Defensive Action and P7 Strategic Capital Posture
5. P8 Flow-First integrated decision/briefing and natural-chain acceptance
6. Candidate/Entry/Exit/Size wiring into Natural PAPER
7. Profitability / opportunity-cost evaluation
8. Human-approved Limited Real Capital, beginning with the most mature execution market

Do not silently reorder this sequence because a supporting defect or interesting subproblem appears. Operational collection, natural observation, and bounded incident fixes may continue in parallel.

The older Entry-first sequence remains useful as a downstream subsystem but does not outrank the Capital Rotation critical path. Portal remains a read-only consumer/product lane and must not displace the investment-system bottleneck unless a Portal defect prevents evidence consumption or operational verification.

## Non-negotiable controls

- Point-in-time integrity: never use future outcomes to create, promote, or grade an earlier operational signal.
- Evidence semantics: a field or status name must describe what the evidence actually proves.
- Authority separation: evidence, recommendation, shadow, paper, and real-order authority remain distinct.
- No implied completion: merged code is not an Exit Gate unless the canonical row's evidence requirement is met.
- No invented policy: risk budgets, sizing, stops, thresholds, and trading authority require explicit ratification.
- Public/private boundary: public `atlas-data` may contain code and redacted diagnostics, never private account facts, credentials, holdings, quantities, or money values. Private evidence must pull an immutable approved public commit.
- One accountable owner: every active implementation has one PM owner, explicit file scope, and merge order. A new chat or agent must not create a parallel duplicate implementation.
- WBS synchronization order after an approved status change: Tracker -> Cockpit -> Master Map.
- Profitability is a parallel validation stream, not a final afterthought.
- REAL/live capital remains closed until explicit user approval and the applicable evidence/risk gates are satisfied.

## End-of-session handoff

Record the verified public and private main SHAs, open PRs and owners, tests, changed canonical rows, unresolved blockers, next executable step, scheduled/automation follow-ups, the four CIO KPI states, next money-related milestone, and all unchanged authority flags. Do not use chat memory as the sole handoff.
