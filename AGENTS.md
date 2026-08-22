# Atlas Agent Start Contract

This file is the mandatory entry point for every coding or review session in this repository.

## Before acting

Before designing, coding, reviewing a PR, or changing WBS status:

1. Read `docs/ATLAS_SESSION_BOOTSTRAP.md` in full.
2. Inspect the current `main` HEAD, working-tree state, and all open PRs. Never assume a pasted handoff is current.
3. Read the live Notion canonical sources named in the bootstrap document: CIO Doctrine, Master WBS Tracker, Cockpit, and Master Map.
4. Find the existing canonical WBS row for the work. Do not create a duplicate row merely because a new chat or agent started.
5. State the applicable mandate, dependencies, Exit Gate, authority boundary, and conflicting active work before implementation.

If a required canonical source cannot be read, stop any irreversible or authority-expanding action. Report the missing source and continue only with safe, reversible investigation.

## Source precedence

Resolve conflicts in this order:

1. Current user instruction and ratified CIO Doctrine / policy
2. Canonical Master WBS Tracker row
3. Code on current `main` plus open, unmerged PR state
4. Cockpit and Master Map summaries
5. Repository/session handoff notes
6. Chat memory or pasted summaries

Lower sources may explain history but may not override higher sources.

## Permanent operating direction

Atlas is being built to turn high-quality briefing evidence into a human-approved, real trading service. It is not a candidate-filtering project. Preserve and advance the complete control loop:

`Opportunity Detection -> Entry/Probe -> Position Management -> Profit Harvesting -> Capital Reallocation -> Review/Learning`

The canonical delivery sequence is:

`P8-10/P8-12 -> P5-06/Position Sizing -> P8-13 Entry Proposal -> P7-11 Profit Harvesting -> P7-10 Capital Reallocation -> P10 Shadow -> P11 Human-approved Paper/Limited Real Capital`

Do not silently reorder this sequence. A justified change must first be recorded in the CIO Doctrine and canonical WBS dependencies.

## Non-negotiable controls

- Point-in-time integrity: never use future outcomes to create, promote, or grade an earlier operational signal.
- Evidence semantics: a field or status name must describe what the evidence actually proves.
- Authority separation: evidence, recommendation, shadow, paper, and real-order authority remain distinct.
- No implied completion: merged code is not an Exit Gate unless the canonical row's evidence requirement is met.
- No invented policy: risk budgets, sizing, stops, thresholds, and trading authority require explicit ratification.
- WBS synchronization order after an approved status change: Tracker -> Cockpit -> Master Map.

## End-of-session handoff

Record the verified main SHA, open PRs and owners, tests, changed canonical rows, unresolved blockers, next executable step, and all unchanged authority flags. Do not use chat memory as the sole handoff.
