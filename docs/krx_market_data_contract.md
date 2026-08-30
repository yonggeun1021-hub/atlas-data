# KRX completed market-data, session, and freshness contract

Status: deterministic observation mechanism implemented. This contract grants
no Universe, candidate, strategy, virtual-fill, KIS mock-order, REAL,
Production, or trading authority.

As-of: 2026-08-30 KST. Venue scope is KRX only (`J` in KIS requests); NXT
(`NX`) and combined-market (`UN`) data are outside this contract.

## Official basis and immutable provider references

KRX's current equity-market pages establish the regular trading session and
holiday rules used here:

- [KRX trading days, holidays, and regular/off-hours sessions](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T1.jsp)
- [KRX current equity trading-hour detail](https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T1.jsp)
- [KRX quotation/tick-size rules](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T3.jsp)
- [KRX base price, ±30% general limit, and corporate-action adjustment](https://global.krx.co.kr/contents/GLB/06/0602/0602010201/GLB0602010201T6.jsp)
- [KRX VI behavior](https://global.krx.co.kr/contents/GLB/06/0602/0602020204/GLB0602020204T7.jsp)
- [KRX individual-issue suspension/resumption](https://global.krx.co.kr/contents/GLB/06/0602/0602010204/GLB0602010204T3.jsp)

KIS endpoint semantics were read from Korea Investment & Securities' official
`open-trading-api` repository at immutable revision
`b4e6249714418aa57833d1cbbbced39cbcc5b125`:

- [dated one-minute history, `FHKST03010230`](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_time_dailychartprice/inquire_time_dailychartprice.py)
- [current-day minute history, `FHKST03010200`](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_time_itemchartprice/inquire_time_itemchartprice.py)
- [daily history and endpoint-specific raw/adjusted selector, `FHKST03010100`](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_daily_itemchartprice/inquire_daily_itemchartprice.py)
- [date-specific domestic holiday/open-day query, `CTCA0903R`](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/chk_holiday/chk_holiday.py)
- [current price/limit/tick facts, `FHKST01010100`](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_price/inquire_price.py)
- [VI status, `FHPST01390000`](https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_vi_status/inquire_vi_status.py)

Every provider operation in the allowlist is GET/read-only market data. This
change adds no broker POST.

## Session contract

The regular KRX session is `09:00:00 <= t < 15:30:00` in `Asia/Seoul`.
The contract records UTC+09:00 and no active DST as of 2026-08-30; it does not
claim that an IANA timezone rule can never change in the future. KRX pre-open,
opening-auction receipt, after-hours close, and after-hours single-price
sessions never contribute to strategy bars.

A date is not open merely because it is Monday-Friday. The consumer requires a
hash-retained `CTCA0903R` snapshot (`opnd_yn`) plus the KRX market-rule source.
Saturday, public holiday, May 1, year-end closure, KRX-designated closures, or
missing date-specific evidence are `CLOSED`/`UNKNOWN`. A delayed or otherwise
special session needs a separate KRX notice and ratified override; without it,
the only permitted result is `UNKNOWN`.

Supported completed bars:

| Bar | Complete KRX intervals | Consumer status |
| --- | --- | --- |
| 15m | `[09:00,09:15)` through `[15:15,15:30)`; 26 on a normal day | required |
| 1h | `[09:00,10:00)` through `[14:00,15:00)`; 6 on a normal day | required |
| 1d | the exact `09:00–15:30` regular session, only after 15:30 | required |
| 4h | no ratified KRX session boundary | not required / rejected |

The 30-minute `15:00–15:30` tail is not called a one-hour bar. The current
bucket is never emitted. Before 09:15 there is no completed 15-minute bar;
before 10:00 there is no completed one-hour bar; before 15:30 there is no
completed daily bar.

KIS provides one-minute and daily sources, not native 15-minute or one-hour
bars. `aggregate_normalized_minutes()` therefore aggregates only an exact set
of normalized one-minute interval starts, with OHLC and summed volume. A
missing minute omits the whole parent bucket and the downstream gap gate
blocks it; prices are never carried forward to manufacture a no-trade minute.

The official KIS sample does not state conclusively whether
`stck_cntg_hour` labels the minute start or end, nor the full fake/no-trade
tick semantics. Raw KIS minute rows consequently remain `UNKNOWN`; the
aggregator requires a separately evidenced `INTERVAL_START_RATIFIED`
normalization. This prevents a boundary-row guess from becoming a completed
bar.

## Source time and point-in-time lineage

Every accepted bar retains:

- `observed_at`: Atlas observed a complete provider response after the bucket
  close. The provider's constituent business date/time remains inside the
  referenced snapshot; it is not silently relabeled.
- `available_at`: first time the complete, validated response was available to
  Atlas.
- `generated_at`: time the normalized/aggregated bar was finished.
- provider ID, endpoint/TR ID, request/snapshot reference, and SHA-256.

The required order is
`bar.close <= observed_at <= available_at <= generated_at <= decision_at`.
Naive timestamps, non-`+09:00` session rows, future rows, and reversed
timestamps fail closed. The same raw snapshot and decision time produce the
same canonical result hash.

`replay_visible_bars()` admits a row only when both its source and any
adjustment snapshot were available at the replay cut-off. A backfill keeps its
real first-seen `available_at`; it cannot repair an earlier decision. Exact
duplicate intervals are idempotently counted. Conflicting duplicates, gaps,
and attempts to rewrite an already observed interval block consumption.

Freshness has two independent parts:

1. the last bar must equal the exact latest completed session interval, with
   no preceding expected gap;
2. the latest bar or market-state source must pass the existing P9-01
   `intraday_freshness_guard/1` evaluator with an external, effective-dated,
   hash-valid `RATIFIED` policy for `KOREA` provider age and transport delay.

P9-01 is the canonical WBS row and deliberately ships no repository default
threshold. This contract reuses its evaluator and preserves
`repository_default_policy=ABSENT`; it does not invent a KIS/KRX SLA or a
second freshness vocabulary. The regression policy is explicitly a test
fixture, not operational ratification. Missing, draft, ineffective, stale,
delayed, or reversed freshness evidence fails closed.

## Corporate actions and price basis

`RAW` and `ADJUSTED` are distinct series identities and cannot be mixed.
For `FHKST03010100`, the official sample defines
`FID_ORG_ADJ_PRC=0` as adjusted and `1` as original/raw. That enum is bound to
this endpoint only; a different KIS endpoint with opposite wording must not be
reused.

Intraday 15m/1h bars are raw only. An adjusted daily row needs an explicit
factor, corporate-action references, an action snapshot hash, and an action
`available_at` no later than replay. A raw row may disclose a split/dividend
without rewriting prices. KRX adjusts base prices for rights issues, bonus
issues, stock dividends, splits, and reverse splits, but the public KIS sample
does not define dividend-adjustment mathematics or historical revision
policy; those details remain `UNKNOWN` rather than inferred.

## Market operability is not Universe eligibility

The output keeps current trading mechanics in a separate `market_state`:

- KIS-observed base/upper/lower prices and tick size;
- VI (`INACTIVE`, `ACTIVE`, `UNKNOWN`);
- individual trading halt;
- market circuit breaker.

An active VI means a two-minute call auction, not necessarily that order
receipt is closed, so it is reported as
`VI_CALL_AUCTION_OBSERVATION_ONLY`. A halt or circuit breaker is
`NOT_ORDERABLE_MARKET_STATE`. Any stale or unknown required fact is
`UNKNOWN`. `universe_eligibility` remains `null` in every market-data result.
Even `ORDERABLE_OBSERVATION_ONLY` is market mechanics, not candidate or order
authority.

## Exact-hash consumer boundary and current Gate

[`krx_market_data_consumer_contract.json`](../config/krx_market_data_consumer_contract.json)
pins the already merged public KRX Gate main revision
`016a2889c503066a3a07180e8d12b9da81869e7b`, contract versions, and canonical
contract hashes. Universe and Shadow remain external exact-hash consumers and
cannot change market-data semantics. Their files are not modified here.

At that pin, KRX remains `LOCKED`; `COMMON_SAFETY` is `UNKNOWN`, KRX Shadow is
own `PASS` but effective `UNKNOWN`, and PAPER canary start is `FAIL`. The
private safety dependency was later merged at
`273d07e73eb9577c4e5a4edcd241eab2037f3c8f`, but it is diagnostic-only and
does not convert merge/CI into operational approval. All internal-ledger,
KIS mock-order, REAL, Production, and trading authority fields remain false.

## Explicit UNKNOWN inventory

- KIS `stck_cntg_hour` start/end labeling and no-trade/fake-tick rules;
- an authoritative machine-readable feed for every KRX special session;
- official KIS/KRX transport/freshness SLA and an operationally ratified P9-01
  Korea policy;
- full public enum for every KIS market-operation status code;
- one direct KIS field for market-wide circuit-breaker state;
- dividend adjustment mathematics and KIS historical response revision
  policy;
- exact future Universe and Shadow consumer commit hashes until those lanes
  merge independently.

Every item above fails closed and grants no authority.

## Offline verification

```bash
python3 validation/tests/test_krx_market_data.py
python3 validation/tests/test_krx_paper_gate.py
python3 shadow/krx_paper_gate.py validate \
  evidence/krx_paper_gate/2026-08-30/assessment.json \
  --evidence-input evidence/krx_paper_gate/2026-08-30/evidence_input.json
```

PR `actions-pass` is merge regression only. A green workflow is not a KRX
Gate PASS and never grants operation.
