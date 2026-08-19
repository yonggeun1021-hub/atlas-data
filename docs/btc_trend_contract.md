# BTC Trend Source / Transform Contract (P1-CR-04)

## Source decision

Atlas uses Kraken Spot REST `BTC/USD` OHLC as a venue-specific primary market
data source:

- endpoint: `GET /0/public/OHLC?pair=XBTUSD&interval=1440&assetVersion=1`
- official documentation: <https://docs.kraken.com/api-reference/market-data/get-ohlc-data>
- quote: USD
- daily bucket boundary: UTC
- authentication: none

Kraken documents two properties that make the finality boundary explicit:
the final OHLC row is always the current, not-yet-committed timeframe, and the
endpoint returns only the recent bounded history.  Atlas therefore removes the
last row unconditionally.  It does not infer finality from the wall clock,
price stability, volume, or the number of trades.

The 2026-08-19 read-only source probe returned 721 rows: 720 historical rows
plus the current 2026-08-19 UTC row.  This observation validates the response
shape but is not a stored PIT capture and is not an investment input.

## Point-in-time capture

One scheduled request runs at 00:20 UTC after the prior UTC daily bucket has
closed.  Each successful response is stored once under:

```text
evidence/crypto/btc/raw/{UTC_DATE}/
  _downloaded_at.txt
  _sha256.txt
  _manifest.json
  kraken_ohlc_xbtusd.json.gz
```

The checksum is over the uncompressed response bytes.  The manifest repeats
the endpoint, pair, interval, UTC close semantics, last-row exclusion policy,
response hash, row counts, latest finalized day, and excluded current day.
Existing vintage directories and manifests are append-only.

## Daily close and 200DMA

`btc_trend/v1` uses the `close` (array index 4), defined by the source as the
last trade price in the finalized daily bucket.  The transform requires:

1. an empty Kraken `error` array;
2. the exact `BTC/USD` result key and cursor;
3. UTC-midnight timestamps in strictly increasing order;
4. a current last row whose date equals the capture vintage;
5. a latest finalized row exactly one UTC calendar day before the vintage;
6. exactly 200 contiguous finalized UTC daily closes for the indicator window.

The arithmetic mean is calculated with decimal values, never binary floats:

```text
DMA200(t, v) = sum(Close(d, v), d=t-199..t) / 200
```

Direction is descriptive factor output only:

- `ABOVE_200DMA`
- `BELOW_200DMA`
- `AT_200DMA`

Changing the current incomplete candle cannot change the factor because that
row is outside the window.

## Missing and invalid data

Atlas performs no interpolation, forward-fill, zero-fill, cross-exchange
substitution, or shorter-window fallback.  A missing day, duplicate timestamp,
out-of-order row, malformed OHLC, API error, checksum mismatch, insufficient
history, stale latest finalized day, or manifest mismatch fails closed as a
typed transform error.  A later common Regime layer may expose that failure as
`UNKNOWN`; it must not convert it to `NEUTRAL`.

## Authority boundary

The transform preserves snapshot vintage, availability, source response SHA,
capture version, close semantics, and current-candle exclusion.  It does not
authorize a Regime score, threshold, Production wiring, or trading action.

Code and fixture regressions do not close P1-CR-04.  Closure additionally
requires the first scheduled immutable capture to validate and reproduce the
same transform from committed bytes.
