# Korea Investor Flow Coverage Contract (P1-KR-04)

Status: implemented coverage boundary; no score or Production authority.

## What the current collector actually contains

`collectors/krx.py` requests per-security investor trading value and volume from
the KRX Information Data System through `pykrx`.  Every newly collected payload
now embeds `investor_flow_coverage` with these fixed statements:

- `market_venue_scope = KRX_ONLY`;
- `nxt_included = false`;
- `whole_korea_market_claim_authorized = false`;
- the security's KOSPI/KOSDAQ/KONEX segment is not recorded in the payload;
- source release time is unverified and `available_at` is `null`.

NXT operates a separate trading venue for KRX-listed securities.  Therefore a
KRX-sourced per-security flow cannot be relabeled as NXT-inclusive or as total
Korea-market flow.  The public KRX Open API service catalog exposes KOSPI,
KOSDAQ, and KONEX daily trading-information services, but does not list an
investor-flow operation.  This change does not substitute those services or add
an API call.

Primary references:

- https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd
- https://www.nextrade.co.kr/menu/transactionSys.do
- https://www.nextrade.co.kr/marketOverview/content.do
- https://portal.nextrade.co.kr/mdclient/home.do

## Finality boundary

The existing `next_day` policy remains the sole production path.  A row from
the collection date remains observed but unconfirmed.  Only a prior-session row
with `confirmed = true` and `confirm_reason = prior_session` is selected by the
offline qualification report.

`observed_at_kst` and `collected_at_*` are observation times.  They are not the
source's release time and must not be promoted to decision `available_at`.

## Missing policy

The contract keeps three independent states:

| Condition | Machine state | Meaning |
| --- | --- | --- |
| KRX response has no `net_value` or `net_volume` row | `SOURCE_ROW_MISSING` | source row was absent |
| A row lacks a basic investor category | `INVESTOR_CATEGORY_MISSING` | column/category was absent |
| NXT is outside the source scope | `VENUE_NOT_INCLUDED` | a market venue was never covered |

A numeric zero is `OBSERVED_ZERO`, never a missing value.  NXT exclusion must
never be encoded as a zero or as a missing KRX row.

## Offline qualification

The helper reads an already saved KRX snapshot and writes only outside the
repository:

```bash
python collectors/krx_investor_flow.py \
  --snapshot data/latest_krx.json \
  --out /tmp/korea-investor-flow.json
```

It records the exact source SHA-256, exposes the latest confirmed raw KRX flow,
and preserves missing states.  It always returns
`KRX_ONLY_PARTIAL_MARKET_COVERAGE`, `available_at: null`, and false authority
for decision eligibility, Regime score, Production wiring, and trading.

Snapshots created before this contract do not contain the embedded coverage
metadata and intentionally fail qualification.  The first scheduled collection
after deployment is the first operating proof of the new schema.

## Explicit non-goals

- no new KRX, NXT, or broker API call;
- no workflow or cron change;
- no foreign-flow threshold, weight, score, or Regime classification;
- no inferred KOSPI/KOSDAQ segment;
- no Production or trading connection.
