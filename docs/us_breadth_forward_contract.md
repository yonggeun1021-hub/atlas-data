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
skipped only after provider-free bundle validation. That validation reparses
both raw source files, exact-matches the manifest, requires the closed six-file
inventory, and rebuilds `_membership_diff.json` from the current and preceding
snapshot. Missing, extra, non-canonical, or semantically changed files fail
instead of being silently reused or overwritten.

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

## Archive-wide replay and as-of reconstruction

`validate-bundle`/`validate_snapshot_bundle` only ever revalidated one
snapshot against whichever `--previous-dir` its caller supplied. The
workflow derives that predecessor with a shell `find | sort | awk`, and
nothing re-checked that the supplied predecessor was actually the archive's
true immediately-preceding snapshot -- so a deleted, reordered, or
mis-linked predecessor could pass an isolated pairwise check.

`replay-archive`/`replay_archive()` closes that gap: it walks
`evidence/us_breadth/raw/` in captured order and threads each snapshot into
the *next* call's `previous_dir` itself, so every bundle is revalidated
against its one true predecessor. Because `validate_snapshot_bundle`
rebuilds `_membership_diff.json` from that predecessor and requires a
byte-identical match, a snapshot whose committed diff was built against any
other baseline -- including one that has since been deleted from the
archive -- fails `MEMBERSHIP_DIFF_MISMATCH` before any of it is trusted. An
unexpected file, a malformed directory name, or a symlinked snapshot inside
`evidence/us_breadth/raw/` fails `ARCHIVE_INVENTORY_INVALID`. The scheduled
workflow now runs `replay-archive` immediately after a new snapshot is
placed in its final path and before the commit step, so a bad predecessor
linkage fails the run instead of being committed.

`universe-as-of`/`universe_as_of(date)` fully replays the archive first,
then returns the latest snapshot's membership with `snapshot_date <= date`
-- strict forward-fill across gaps such as weekends, never backward. A date
earlier than the archive's first snapshot fails
`AS_OF_BEFORE_ARCHIVE_BASELINE` rather than being silently backfilled from
current membership. This closes the *universe* half of the original Exit
Gate for every date the archive already covers going forward; it does not
and cannot answer for a date before Atlas' first capture, and it still says
nothing about price advance/decline breadth -- that half remains gated by
the paid-data checkpoint below.

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
`universe_as_of()` reconstructs date-specific *universe* membership, but only
from the archive's own first capture forward -- it refuses any date before
that baseline rather than backfilling it. Date-specific advance/decline
*breadth* still requires delisted-security OHLCV, which sits on the
paid-data stop list above. The original WBS Exit Gate therefore remains open
on the breadth half, and on any universe date before Atlas began capturing,
even after this capture workflow goes live.

## Offline verification

```bash
python3 test/test_us_breadth_forward.py
python3 .github/scripts/us_breadth_forward.py validate \
  evidence/us_breadth/raw/YYYY-MM-DD
python3 .github/scripts/us_breadth_forward.py validate-bundle \
  evidence/us_breadth/raw/YYYY-MM-DD \
  --previous-dir evidence/us_breadth/raw/PREVIOUS-YYYY-MM-DD
python3 .github/scripts/us_breadth_forward.py replay-archive
python3 .github/scripts/us_breadth_forward.py universe-as-of --date YYYY-MM-DD
```

No helper command fetches data.  Network access exists only in the dedicated
scheduled workflow, and every derived authority remains false.
