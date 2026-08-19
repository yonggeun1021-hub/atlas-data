# KOFIA Investor Deposits / Credit Financing Contract (P1-KR-03)

Status: source qualification implementation; WBS Exit Gate remains open.

## Confirmed primary-source surface

The Financial Services Commission data.go.kr catalog `15094809` exposes KOFIA
statistics through
`GetKofiaStatisticsInfoService`.  This contract selects only two documented
operations:

| Atlas observation | Official operation | Primary raw field |
| --- | --- | --- |
| Investor deposits | `getSecuritiesMarketTotalCapitalInfo` | `invrDpsgAmt` |
| Credit financing | `getGrantingOfCreditBalanceInfo` | `crdTrFingWhl` |

The API model documents `basDt` as a `YYYYMMDD` observation date and documents
the exact numeric fields fixed in `config/kofia_liquidity_contract.json`.
FreeSIS separately displays investor deposits and credit financing as KOFIA
market statistics.

The official Swagger declares those value fields as JSON `number`. A live
Atlas probe on 2026-08-19 observed `crdTrFingWhl` as a canonical unsigned
decimal string and later observed `ucolMnyVsOppsTrdRlImpt` with a leading zero
omitted, such as `.x`. Source contract v3 therefore accepts either the
documented JSON number or an exact string matching
`^(?:(?:0|[1-9][0-9]*)(?:\.[0-9]+)?|\.[0-9]+)$` and normalizes both through
`Decimal`; a leading fraction becomes `0.x` in the normalized observation.
Blank values, whitespace, signs, exponents, grouping separators, placeholders,
nulls, and container values remain contract failures; none are converted to
zero.

Primary evidence:

- https://www.data.go.kr/data/15094809/openapi.do
- https://www.data.go.kr/catalog/15094809/openapi.json
- https://freesis.kofia.or.kr/stat/main.do

## Deliberately unresolved fields

The official catalog leaves `temporalCoverage` blank.  Its generic update-cycle
label does not state when a particular `basDt` row first becomes available.
The KOFIA inquiry asking for FreeSIS/API update time contains the question but
no published official answer:

- https://www.kofia.or.kr/voc/m_113/view.do?answer_seq=0&page=1&srchTp=&srchWord=&voc_id=3009

Therefore the contract keeps all of the following `unverified`:

- historical range as a durable source guarantee;
- source release time and decision `available_at`;
- API numeric-field unit (the FreeSIS display unit is not silently promoted to
  an API field contract).

`basDt`, API fetch time, the portal's “real-time” metadata, and a FreeSIS page
view must never be substituted for `available_at`.

## Offline qualification behavior

`.github/scripts/kofia_liquidity.py` accepts already downloaded JSON responses.
It makes no live request and reads no API key.  A response qualifies as a
coverage observation only when page 1 contains the complete `totalCount`, all
dates are unique and valid, all documented fields are present exactly, and all
numeric values are finite and non-negative after the explicit transport
normalization above.

Example after an approved API capture, with all files outside the repository:

```bash
python .github/scripts/kofia_liquidity.py \
  --investor-deposits /tmp/kofia/investor-deposits.json \
  --credit-financing /tmp/kofia/credit-financing.json \
  --captured-at 2026-08-20T01:00:00Z \
  --out /tmp/kofia/qualification.json
```

The report records response SHA-256, captured time, observed min/max dates,
row count, and raw latest primary values.  It still emits `available_at: null`,
`decision_eligible: false`, and denies Regime score, Production wiring, and
trading authority.

## Exit Gate

This implementation does not close P1-KR-03.  Closure requires primary
evidence for both:

1. the historical range required by the approved replay window; and
2. first-availability/source release timing sufficient to define
   point-in-time `available_at`.

Only a later approved collector may create append-only first-seen evidence.
No collector, workflow, score, threshold, or Production wiring is added here.
