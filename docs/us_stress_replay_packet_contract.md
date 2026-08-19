# US Stress Replay Packet Contract (P1-US-07)

Status: offline research-packet capability implemented; replay case dates and
source policy unratified; authoritative 2008 PIT source unavailable; no
behavior assessment, threshold, weight, Regime, Production, or trading
authority.

## Purpose

This contract binds already validated `regime_output/v1` US envelopes into a
deterministic replay evidence packet. It answers which evidence was replayed,
for which explicitly approved market dates, and where each point came from.
It does not decide whether the market behavior was sensible.

The packet requires four distinct research contexts from the WBS:

1. `STRESS_2008`
2. `RECENT_BULL`
3. `RECENT_BEAR`
4. `RECENT_SIDEWAYS`

The names are evidence contexts, not Atlas Regime classifications. The helper
does not select dates for them. A separately ratified case policy must provide
the exact expected US market dates and a source-policy version.

## Absence and integrity boundaries

The repository default case policy is `UNRATIFIED` and empty. Running the
builder with defaults fails with `CASE_POLICY_UNRATIFIED`; an empty catalog is
not a successful replay.

For a ratified policy, every context must occur exactly once and every explicit
date must have exactly one source envelope. Missing cases, missing points,
duplicates, reordered cases, wrong US market dates, wrong markets, invalid
source outputs, floats, and derivation tampering fail closed.

Every source envelope is revalidated against `regime_output/v1`. The packet
records its canonical JSON SHA-256, coverage, evidence date bounds,
availability, warnings, and still-unclassified `UNKNOWN` state. Vendor price
rows and reconstructive price series are never emitted.

## Temporal evidence classes

Version 1 permits only:

- `CAUSAL_RESEARCH_ONLY`
- `REVISED_SENSITIVITY_ONLY`

`AUTHORITATIVE_HISTORICAL_PIT` is intentionally unavailable. The known 2008
official historical source gap cannot be converted into authority by labeling
a research packet. Adding authoritative historical PIT requires separate
source evidence, policy approval, and a new contract version.

## Authority boundary

The packet status is `RESEARCH_PACKET_AVAILABLE_UNCALIBRATED` only when all
explicit research inputs validate. It keeps these false:

- authoritative historical PIT;
- behavior assessment;
- thresholds and weights;
- Regime classification;
- strategy eligibility;
- Production wiring;
- trading action.

The contained source envelopes remain `UNKNOWN` under the current common
pre-score contract. Replay evidence must exist before any later threshold or
weight research, but evidence alone does not authorize either.

## Offline commands

Build from a transient JSON payload on stdin and an explicitly ratified policy:

```bash
python3 .github/scripts/us_stress_replay_packet.py build \
  --policy /tmp/ratified-us-replay-cases.json \
  --out /tmp/us-stress-replay-packet.json \
  < /tmp/us-replay-source.json
```

Revalidate an existing packet against its source:

```bash
python3 .github/scripts/us_stress_replay_packet.py validate \
  /tmp/us-stress-replay-packet.json \
  --source /tmp/us-replay-source.json \
  --policy /tmp/ratified-us-replay-cases.json
```

No command makes a network request or writes a tracked factor by default.
