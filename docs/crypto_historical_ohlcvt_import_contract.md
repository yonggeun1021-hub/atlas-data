# Kraken Historical OHLCVT Replay Import

Status: replay-only import capability. It creates no live evidence, historical
catalog, investable universe, Leadership conclusion, Regime, action, order, or
trading authority.

## Purpose

Kraken publishes a bulk OHLCVT ZIP containing multiple candle intervals and
quote currencies. This importer reads only the daily (`1440`) USD CSV entries
directly from the ZIP and writes a small deterministic local replay archive.
The multi-gigabyte source ZIP and generated replay files stay outside Git.

The importer is intentionally separate from P1-CR-06 live snapshots:

- bulk CSV proves historical candles, not the exact asset/pair catalog visible
  to Atlas on each historical day;
- a missing CSV day remains absent because Kraken's bulk file omits intervals
  with no trades;
- the CSV contains base volume but no daily VWAP, so the importer does not
  derive the live contract's `VWAP × base volume` turnover ranking;
- source ticker aliases are preserved and are not backfilled using a newer
  Kraken catalog.

These boundaries prevent a research dataset from masquerading as natural,
point-in-time live evidence.

## Output

The output directory is append-only and contains:

- `daily_usd_1440.ndjson.gz` — canonical rows ordered by source pair then date;
- `pair_inventory.json` — source entry and selected coverage by pair;
- `manifest.json` — source ZIP hash, selected range, output hashes, and false
  authority boundary;
- `SHA256SUMS` — deterministic output checksums.

All decimals remain exact strings. No float conversion, forward fill, quote
turnover proxy, source alias conversion, or missing-day synthesis occurs. An
exact duplicate timestamp is deduplicated and disclosed. If duplicate rows for
one pair disagree after exact decimal normalization, the entire pair is
excluded and recorded in `pair_inventory.json`; the importer never chooses one
conflicting source row silently. Empty source entries are excluded and recorded
with `SOURCE_EMPTY_ENTRY`; they are never converted into zero candles.

## Local command

```bash
python3 .github/scripts/crypto_historical_ohlcvt_import.py \
  /path/to/Kraken_OHLCVT.zip \
  --start-date 2024-01-01 \
  --end-date 2025-12-31 \
  --out /path/outside/git/kraken-usd-daily-2024-2025
```

The source archive is hashed in streaming chunks. ZIP members are read in
place; the importer never extracts the full archive. A failure leaves no final
output directory.

## Downstream boundary

This replay archive may support policy calibration and comparison. Before a
P1-CR-07 Leadership policy can consume it, a separate approved replay adapter
must define historical identity, asset exclusions, universe selection, and the
difference between close-volume research turnover and the live VWAP-volume
contract. Live 7/30-day observations continue to come only from append-only
P1-CR-06 daily snapshots.
