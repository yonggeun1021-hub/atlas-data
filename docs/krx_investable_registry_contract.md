# KRX point-in-time investable registry contract

## Purpose and authority boundary

This pipeline turns official KIS KOSPI/KOSDAQ master files plus an exact-date
official KRX stock source-coverage packet into a reproducible registry for
briefing and strategy consumers.  It produces evidence and screening states;
it does not approve an investable universe or authorize strategy entry, PAPER
orders, REAL orders, Production, or trading.

The output deliberately separates:

- `CATEGORICAL_CANDIDATE`: product and observed status flags passed the
  currently implemented categorical screen.  This is not investability.
- `EXCLUDED`: the official source explicitly identifies an out-of-scope product
  or a fail-closed status such as preferred stock, SPAC, ETN, halt, liquidation
  trading, managed issue, market-warning family, or KOSDAQ investment attention.
- `UNKNOWN`: a provider code is undocumented, identity/membership is ambiguous,
  a required source is missing, or another fail-closed check cannot be computed.
- `decision_eligibility`: remains `UNKNOWN` for every non-excluded record until
  freshness, prior identity history, delisting evidence, measurements, and
  ratified thresholds are all present.  It is never inferred from the screening
  state.

## Primary sources and lineage

KIS identity and status source:

- Official repository: <https://github.com/koreainvestment/open-trading-api>
- Pinned parser commit: `b4e6249714418aa57833d1cbbbced39cbcc5b125`
- Official parser paths:
  `stocks_info/kis_kospi_code_mst.py` and
  `stocks_info/kis_kosdaq_code_mst.py`
- Official master endpoints:
  `https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip` and
  `https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip`

KRX membership source:

- Existing `krx_global_universe_packet/1`, generated from the official KRX Open
  API KOSPI and KOSDAQ daily-stock endpoints with exact response hashes.
- The packet's `ISU_CD` is treated as a mutable KRX short-code alias.  It is not
  the immutable primary identity.  The KIS 12-character standard code is the
  immutable `security_id` anchor.
- A separately hashed official market-session packet supplies the latest
  completed-session date.  If it differs from the KRX membership packet date,
  `krx_snapshot_freshness=STALE` and decision eligibility fails closed.

Each full private record contains immutable standard code, mutable short code,
market, product type, eligibility reason codes, row-bound evidence hash, and
capture time.  Current duplicates fail the whole snapshot.  When a prior
append-only KIS registry is supplied, short-code reuse against a different
standard code becomes `UNKNOWN`.  Without prior KIS history, reuse detection is
`NOT_COMPUTABLE_NO_PRIOR_KIS_REGISTRY`; it is never reported as a zero-event
PASS.

## Product and status rules

Implemented mechanical classifications come only from documented KIS fields:

- Common stock: security group `ST`, preferred code `0`, SPAC flag `N`.
- Preferred stock: security group `ST`, preferred code `1` or `2`; excluded.
- SPAC: security group `ST`, SPAC flag `Y`; excluded.
- ETF: security group `EF`.  Only documented ETP codes `1` and `2` are
  categorical candidates; undocumented codes remain `UNKNOWN`.
- ETN: documented ETP codes `3` and `4`; excluded.
- Other documented groups are outside the common-stock/ETF scope and excluded.
- Undocumented group, preferred, SPAC, ETP, and status codes remain `UNKNOWN`.

For common-stock and ETF scope, the following official flags exclude the
record: trading halt, liquidation trading, managed issue, market-warning codes
`01`/`02`/`03`, warning advance notice, and KOSDAQ investment attention.
The KIS low-liquidity flag is retained as a measured reason code, but it is not
turned into an invented numeric eligibility threshold.

## Measurement versus policy

Turnover, order-book depth, spread, and slippage are four separate measurement
families.  Their current measurement coverage is reported independently.  The
contract fixes every proposed threshold to `null` and every policy status to
`UNRATIFIED`.  No source flag, current count, backtest result, or code merge may
ratify those thresholds.

## Distribution boundary

KRX Open API terms say API information may not be provided to a third party and
require attribution for screens based on KRX statistical information:
<https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp>.  KRX also states
that redistribution-program or commercial use of market data can require a
separate contract:
<https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp>.

The official KIS repository provides sample parsers and download endpoints, but
no redistribution license was found in the pinned repository snapshot.  This
is a distribution-risk observation, not a legal conclusion.

Therefore the public repository may retain only source URLs and commit, hashes,
byte lengths, capture/as-of times, aggregate counts, policy state, and authority
flags.  KIS archives and rows, KRX raw responses, symbol names and per-symbol
status fields, and the full registry are private-only evidence.  The public
summary is bound to the private registry by `private_registry_payload_sha256`.

## Reproduction

The CLI performs no network calls.  The caller supplies already captured KIS
ZIP files, an existing exact KRX packet, a hashed latest-session evidence row,
and optionally a prior private registry:

```text
python3 universe/krx_investable_registry.py INPUT.json \
  --private-out /private/evidence/registry.json \
  --public-summary-out /safe/public-summary.json
```

The offline pull-request gate uses generated synthetic master archives and KRX
fixtures.  Live provider material is never downloaded or uploaded by CI.

## Gate semantics

The registry consumes the merged `krx_paper_common_safety_gate/1` and
`krx_paper_market_gate/1` contracts and publishes their canonical hashes.  Its
output is only a `NON_AUTHORITY_EVIDENCE_CANDIDATE` for
`COMMON_PIT_AND_IMMUTABLE_LINEAGE` and
`KRX_FINAL_CANDIDATE_POLICY_RATIFIED`.  It cannot declare either check PASS,
claim a KRX Gate state, or authorize a state transition.

The current projection is `INSUFFICIENT` with
`COMMON_PIT_OR_LINEAGE_NOT_PROVEN` and
`KRX_FINAL_CANDIDATE_AUTHORITY_UNRATIFIED`.  COMMON SAFETY and the KRX PAPER
Gate remain independently evaluated by the merged Gate implementation.  The
latest completed KRX membership snapshot, append-only prior KIS identity
history, official scheduled-delisting evidence, observed
turnover/depth/spread/slippage coverage, and CIO-ratified policies are still
absent.  All virtual-ledger, KIS mock-account, REAL, live-account, Production,
and trading authority fields remain false.  Full CI is only a merge regression
and cannot replace either Gate.
