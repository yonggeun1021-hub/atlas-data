# US-U1 Investable Universe Display Generator (T1 only, unratified)

CIO US-DATA-1 dispatch, item 1. Companion to `universe/us_global_universe.py`
(P3-02) — reads that adapter's already-published `us_global_universe_packet/1`
(`source_attribute_rows`, all facts, no filtering, `investable_eligible=false`
on every row) and produces a **separate, explicitly unratified T1 display
estimate** of which rows look like a plain common stock or ADS with a
resolvable SEC CIK.

This module grants **no authority**. It does not resolve identity (that is
W3, `identity/bulk_identity_authority.py`, separately scoped and
RATIFIED-gated), does not set `investable_eligible`, and is not a T2/T3
input. Every output row carries `t1_display_only = true`, `ratified = false`.

## Filter pipeline

Applied in order; every step's exclusion count is recorded (`exclusion_counts`)
so nothing is silently dropped, and `kept_count + sum(exclusion_counts)` is
enforced to equal `total_source_rows` (fail-closed reconciliation check):

1. `ETF == 'Y'` excluded (official flag).
2. `Test Issue == 'Y'` excluded (official flag).
3. `Financial Status != 'N'` excluded **only where that field is present**.
   `otherlisted.txt` rows carry no such field in this source at all — its
   absence is recorded (`financial_status_available: false`) and is never
   treated as an assumed "normal".
4. Security Name pattern says non-common (`classify_security_name`) —
   excluded. This is a heuristic over free text, not a ratified
   security-type determination; see caveats below.
5. The row's primary symbol does not resolve to a CIK in the supplied SEC
   `company_tickers_exchange.json` snapshot (exact match, then a
   dot-to-dash and a dollar-strip normalization, both recorded per kept
   row as `cik_match_method`) — excluded.

## Security-type heuristic buckets (kept)

- `ADS` — "ADS(s)", "ADR", "American Depositary/Depository Shares".
- `ORDINARY` — "Ordinary Share(s)".
- `COMMON` — "Common Stock/Share(s)", "Subordinate Voting Share",
  "Capital Stock".
- `COMMON_BARE` — **no** recognized common-stock suffix and **no**
  recognized non-common keyword (fund/trust/preferred/pfd/warrant/right/
  unit/note/bond/ETN/…). Kept, but tagged
  `security_type_certainty = inferred_from_absence_of_non_common_suffix`
  since it is absence of a red flag, not presence of a positive match —
  the weakest evidence bucket. Sampling this bucket against real 2026-09-11
  data showed it is mostly plain-name common stocks (e.g. "AMETEK, Inc.")
  with a residual tail of abbreviated preferred/fund names the pattern set
  does not yet catch.

## Real run (2026-09-11 packet, 13,214 source rows)

```
kept:      5,529   (ADS 384 / ORDINARY 804 / COMMON 4,259 / COMMON_BARE 82)
excluded:  7,685
  ETF                              5,676
  TEST_ISSUE                          33
  FINANCIAL_STATUS_NOT_NORMAL        363
  SECURITY_TYPE_NOT_COMMON_OR_ADS  1,342
  SEC_CIK_NOT_FOUND                  271
```

Reconciles exactly against `total_source_rows = 13214`. The CIO plan's own
estimate ("약 4,887종목", a rougher name-pattern count) is in the same order
of magnitude; the gap is expected heuristic-to-heuristic variance, not a
contradiction — neither number is ratified.

## SEC CIK snapshot lineage

`evidence/us_investable_universe/raw/sec_company_tickers_exchange/<sha256>.json`
is the unmodified response body of
`https://www.sec.gov/files/company_tickers_exchange.json` (public, no key;
SEC requires a contactable `User-Agent`, matching the convention already
used by `collectors/sec.py`). Its sibling `<sha256>.manifest.json` records
the endpoint, retrieval time, SHA-256, and row count. This is a **display
cross-check only** — it is not a W3 bulk identity ratification batch.

## Offline command

```bash
python3 universe/us_investable_universe_v1.py \
  --cik-snapshot evidence/us_investable_universe/raw/sec_company_tickers_exchange/<sha256>.json \
  --out /tmp/us-investable-universe-v1.json
```

Reads the most recent `data/observations/us_global_universe/<date>/packet.json`.
No network client; the CIK snapshot is supplied, not fetched, by this module.

## What this is not

- Not an investable-universe ratification (`investable_eligible` stays
  `false` upstream and is not set here).
- Not W2 sector/theme membership, not W3 bulk identity, not T2/T3 gate
  input.
- Not a liquidity, tradability, or listing-history judgment.
- Grants no trading, order, or capital authority.
