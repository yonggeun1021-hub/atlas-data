# US Breadth Forward Source Contract (P1-US-04)

Status: free forward-only membership capture approved on 2026-08-19; historical
reconstruction, price breadth, Regime, Production, and trading authority remain
closed.

## Approved free scope

Atlas captures the two official Nasdaq Trader Symbol Directory files once per
regular US session after the close:

- `nasdaqlisted.txt`: Nasdaq-listed issues;
- `otherlisted.txt`: securities listed on other US exchanges.

Nasdaq defines these files as the current trading day's directory and says they
are updated throughout the day.  Therefore a capture proves only what the
source showed on its own `File Creation Time` date.  Atlas stores the exact raw
bytes, hashes, source creation strings, fetch time, and manifest under:

```text
evidence/us_breadth/raw/{SOURCE_DATE}/
  _downloaded_at.txt
  _sha256.txt
  _manifest.json
  _membership_diff.json
  nasdaqlisted.txt.gz
  otherlisted.txt.gz
```

The directory date comes from both source footers, not from the runner clock.
The source does not specify a timezone for that footer, so Atlas preserves the
raw value and does not invent one.  A second capture for the same source date is
skipped and incomplete existing directories fail instead of being overwritten.

Primary source definitions:

- https://nasdaqtrader.com/Trader.aspx?id=SymbolDirDefs

## What the daily diff means

`_membership_diff.json` compares only independently captured date directories.
It reports exact source-directory identities that entered, exited, or changed
attributes.  The baseline capture leaves change counts null; it does not claim
that every current row entered that day.

This is `source_directory_membership_not_investable_universe`.  Test issues,
ETFs, and other security types remain in the raw directory.  No liquidity,
eligibility, ranking, or portfolio-universe rule is inferred from presence.
Most importantly, the newest directory is never applied to an older date.

## Price and breadth boundary

The free Symbol Directory has no daily close.  Until a separate price source is
qualified, every membership diff must contain:

```json
{
  "price_breadth": {
    "status": "UNKNOWN_PRICE_SOURCE_UNAVAILABLE",
    "advancing_count": null,
    "declining_count": null,
    "unchanged_count": null,
    "advance_fraction": null
  }
}
```

Membership coverage is not advance/decline breadth.  Missing prices are not
zero, unchanged, neutral, or a successful breadth observation.

## Paid-data confirmation checkpoint

The machine-readable contract contains
`paid_data_checkpoint.status = USER_RECONFIRMATION_REQUIRED` and
`approved = false`.  Automation must stop and ask the user again before any of
the following:

- historical breadth reconstruction before Atlas' first capture;
- delisted-security OHLCV acquisition;
- a paid API, dataset, vendor, licence, or subscription;
- a free trial that converts to a paid plan.

This approval cannot be inferred from the 2026-08-19 forward-only decision.
The original WBS Exit Gate—rebuilding historical date-specific universe and
breadth—therefore remains open even after this capture workflow goes live.

## Offline verification

```bash
python3 test/test_us_breadth_forward.py
python3 .github/scripts/us_breadth_forward.py validate \
  evidence/us_breadth/raw/YYYY-MM-DD
```

No helper command fetches data.  Network access exists only in the dedicated
scheduled workflow, and every derived authority remains false.
