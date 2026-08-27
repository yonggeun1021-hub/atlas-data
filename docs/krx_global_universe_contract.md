# KOSPI / KOSDAQ Global Universe Adapter (P3-03)

Status: exact-date KRX source-coverage membership capability and append-only
manual population wiring implemented. An approved investable Korea universe,
liquidity/tradability rules, and scheduled population remain unratified or
unimplemented.

## Purpose

`universe/krx_global_universe.py` takes the exact response bytes from both
official KRX daily-stock endpoints for one completed trading date and maps
every returned `ISU_CD` into the P3-01 Global Asset Master shape.

- KOSPI: `sto/stk_bydd_trd`
- KOSDAQ: `sto/ksq_bydd_trd`

Both markets are mandatory. The adapter verifies the supplied response-byte
SHA-256, source ID/URL/timestamps, response schema, `BAS_DD`, unique identity,
and row market. A missing market, partial/zero response, duplicate identifier,
malformed body, wrong date, or lineage mismatch fails the whole packet.

## Point-in-time membership

Membership means only that the official endpoint returned the instrument for
that exact completed trading date. It is recorded as
`[trading_date, next_calendar_date)`. The adapter never carries the current
catalog backward or forward and never guesses an exchange calendar session.

Each source row becomes:

- stable Atlas ID `KR:XKRX:{ISU_CD}`;
- market `KOREA`, asset class `EQUITY`, exchange `XKRX`, quote currency `KRW`;
- exact `KRX_ISU_CD` identity and source-provided display name;
- active `MARKET=KOREA` membership; and
- active `UNIVERSE=KOSPI|KOSDAQ` membership.

`ISU_CD` itself is kept as the primary symbol because this source contract
does not establish a separate display ticker. The adapter does not infer the
six-digit code from an ISIN-like identity or from a company name.

## Authority boundary

The result is explicitly
`exact_trading_date_source_coverage_not_investable`. It does not filter or
approve assets using listing age, delisting handling, security type,
liquidity, capacity, suspension, tradability, theme, price, or fundamentals.
It does not rank candidates or promote a Discovery stage.

The nested Global Asset Master records retain:

- `universe_approved = false`;
- `investable_eligible = false`; and
- `stage_transition = null`.

The outer adapter keeps investability, liquidity, tradability, theme,
Production, and trading authorities false. Those policies require separate
ratification before P3-03 can satisfy its approved-Universe Exit Gate.

## Offline command

The input packet embeds each exact JSON response body as base64 and supplies
its immutable source lineage. The adapter has no network client and writes
only the requested atomic output path.

```bash
python3 universe/krx_global_universe.py /tmp/krx-universe-input.json \
  --out /tmp/krx-universe.json
```

No tracked Master, workflow, provider request, evaluator input, or trading
artifact is created by the adapter command itself.  The existing P1-KR-05
workflow reuses its already-built recent packet without another provider call,
independently validates it with
`.github/scripts/korea_global_universe_populate.py`, and commits the exact
packet to `data/observations/krx_global_universe/{date}/packet.json`.

That tracked packet is still source coverage only. It contains KRX identity,
name, membership and source lineage, but no response body or price/volume/
market-cap fields. Re-running the same bytes is a no-op; a different packet for
an existing date fails closed. Investability, liquidity, tradability, listing/
delisting, Theme, Stage, Production, and trading authorities remain false.
