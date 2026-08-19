# Crypto Leadership Contract (P1-CR-07)

Status: dual-window observation policy ratified; sector/chain taxonomy and
group coverage policy unratified; no classification, ranking, threshold,
Regime, Production, or trading authority.

## Purpose

This offline contract reproduces raw Crypto relative-strength observations
from the independent, append-only daily Kraken snapshots qualified by
P1-CR-06. It does not call Kraken, capture data, select an investable
universe, declare a leader, rank assets, create a score, or publish a tracked
factor.

For every UTC day in a window, daily gross return is:

```text
latest finalized close / previous finalized close
```

The current Kraken OHLC row remains excluded. Cumulative gross return is the
product of exact daily gross returns, and raw relative strength is:

```text
cumulative gross return / BTC cumulative gross return - 1
```

Equal-weight daily rebalancing is the approved raw group arithmetic. It does
not authorize a leadership conclusion.

## Ratified independent windows

The ratified policy has two independently evaluated exact calendar windows:

| Window | Role | Requirement |
|---|---|---|
| `pilot_7d` | PILOT | 7 contiguous qualified daily snapshots |
| `primary_30d` | PRIMARY | 30 contiguous qualified daily snapshots |

A complete 7-day window is observable while the 30-day window is still
`UNKNOWN`; missing 30-day history cannot erase the 7-day evidence. If some
windows are observed and some are unknown, the top-level status is `PARTIAL`.
`PARTIAL` is an audit summary, not a market classification.

Each unknown window records all missing dates and policy-effective-date
blockers. There is no pre-policy or pre-first-snapshot backfill.

## Layered bucket and taxonomy policy

BTC/ETH/Alt buckets are deterministic:

```text
BTC -> BTC
ETH -> ETH
every other eligible CR-06 asset -> ALT
```

This bucket mapping does not depend on the sector/chain taxonomy. Asset and
bucket observations may therefore remain `OBSERVED_UNCLASSIFIED` when the
sector/chain layer is `UNKNOWN`.

The sector/chain group layer is `UNKNOWN` when any of these is true:

- the effective-dated taxonomy is unratified;
- an asset/date taxonomy record is missing;
- the taxonomy is not effective for the full window;
- the minimum group coverage policy is unratified.

The repository currently has an empty, unratified sector/chain taxonomy and an
unratified group coverage policy. Those values will be based on actual CR-06
Top-100 live evidence, not invented before the first snapshots exist.
Structural taxonomy defects such as invalid records or overlapping effective
ranges remain hard failures.

## Point-in-time and failure boundaries

Each point is rebuilt from that day's captured `Assets`, `AssetPairs`, and
OHLC bytes. A newer catalog is never carried backward. Assets that enter or
leave the as-captured universe are reported as `partial_window_assets` and are
not presented as full-window asset observations.

Expected absence is represented, not converted to success:

- missing dates -> affected window `UNKNOWN`;
- CR-06 source point `UNKNOWN` -> affected window `UNKNOWN`, with source date
  and reason;
- empty BTC/ETH/Alt bucket -> that bucket `UNKNOWN`;
- missing sector/chain classification -> sector/chain layer `UNKNOWN`.

Integrity violations still stop the run: checksum or manifest drift, malformed
source documents, invalid policy schemas, taxonomy overlaps, and adjacent
snapshot close mismatches are hard failures. For an asset present in adjacent
snapshots, the earlier latest finalized close must exactly equal the later
previous finalized close.

## Authority boundary

Every output keeps all of these false:

- leader classification;
- ranking;
- threshold;
- Regime score;
- Production wiring;
- trading action.

Arrays are ordered by identifiers or ratified policy order only. Their order
is not a market ranking.

## Offline command

```bash
python3 .github/scripts/crypto_leadership.py transform \
  /tmp/crypto-breadth/raw \
  --end-date 2026-08-25 \
  --out /tmp/crypto-leadership.json
```

The command makes no network request and writes no tracked factor by default.
