# KRX read-only execution measurement evidence

## Scope and non-authority boundary

`krx_execution_measurement/1` is an offline transformer for four measurement
families that block the Korea investable-universe decision: completed-session
turnover, displayed order-book depth, spread, and source-derived slippage
curves. It accepts only previously captured official-source files. It contains
no network client, credential storage, broker POST, order endpoint, order
quantity, threshold approval, or eligibility-promotion path.

Every policy candidate is `UNRATIFIED`; turnover lookback, minimum turnover,
minimum depth, maximum spread, slippage order notional, and maximum impact are
all `null`. Measurement coverage and descriptive distributions are facts about
the supplied snapshots, not investability rules. All mock-order, live-account,
REAL-capital, Production, strategy-entry, and trading authority remains false.

## Official sources and point-in-time semantics

Turnover uses the KRX Open API daily-stock response field `ACC_TRDVAL`, with
`BAS_DD` required to equal the separately evidenced latest completed session.
The contract accepts only the official KOSPI and KOSDAQ stock daily endpoints,
GET, exact market identity, unique short codes, non-negative numeric values,
and exact raw-response SHA-256 and byte length. This endpoint does not establish
ETF turnover coverage; missing ETF turnover remains missing.

Order-book evidence uses the official Korea Investment & Securities
open-trading-api sample pinned at commit
`b4e6249714418aa57833d1cbbbced39cbcc5b125`:

- Sample field check:
  <https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_asking_price_exp_ccn/chk_inquire_asking_price_exp_ccn.py>
- GET implementation:
  <https://github.com/koreainvestment/open-trading-api/blob/b4e6249714418aa57833d1cbbbced39cbcc5b125/examples_llm/domestic_stock/inquire_asking_price_exp_ccn/inquire_asking_price_exp_ccn.py>

The accepted source identity is domestic-stock order-book TR
`FHKST01010200`, endpoint
`/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn`, venue `J`
(KRX), and ten ask/bid price and residual-quantity levels. Each capture must be
dated in Korea local time to the completed session and bind to the same hashed
completed-session evidence as the registry. NX and combined-venue observations
are rejected rather than silently mixed with KRX.

The transformer checks non-crossed books, positive paired price/quantity
levels, monotonic asks and bids, unique immutable security identities, exact
registry identity-snapshot hash, source success code, and payload hashes.

## Measurements, not thresholds

- `turnover_krw`: exact-session KRX `ACC_TRDVAL`.
- `displayed_depth_krw`: sum of price times displayed residual quantity over
  the official ten levels on both sides; ask and bid depth are also retained.
- `spread_bps`: `(ask1 - bid1) / midpoint * 10,000`.
- `slippage_curve`: cumulative quantity, cumulative notional, VWAP, and impact
  basis points at every available book level. Buy impact is measured from best
  ask and sell impact from best bid.

No arbitrary order notional is selected. The full capacity–impact curve is
private evidence; a later ratified policy may select a notional and impact
limit. A backtest, distribution, code merge, or CI result cannot perform that
ratification.

Public distributions use deterministic nearest-rank min/p25/p50/p75/max.
Values are suppressed when fewer than five observations are present so a
small aggregate cannot simply disclose a per-symbol measurement. This count
is a publication-control rule, not a trading threshold.

## Distribution and usage-rights boundary

KRX Open API terms restrict third-party provision of API information and
describe attribution requirements for screens based on KRX statistical
information:
<https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp>. KRX separately
states that redistribution-program or commercial data use can require a
contract:
<https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA003.jsp>.

Accordingly, raw KRX responses, raw KIS books, per-symbol measurements,
identifiers, and slippage curves are private-only evidence. The public output
contains source URLs/commit, hashes, byte lengths, capture/as-of times,
aggregate coverage, sufficiently large aggregate distributions, policy state,
and false authority flags. The public projection is cryptographically bound to
the full private measurement payload. This implementation boundary is not a
legal opinion and does not grant redistribution rights.

## Reproduction and registry integration

The caller supplies a current private registry, optional exact-session KRX raw
snapshots, and an optional private KIS capture envelope:

```text
python3 universe/krx_execution_measurements.py INPUT.json \
  --private-out /private/evidence/execution-measurements.json \
  --public-summary-out /safe/public-execution-summary.json
```

With no provider files, the output honestly reports zero coverage. With valid
files, it emits a private row for every categorical candidate and a redacted
public summary. Supplying the private output as the registry input's optional
`execution_evidence_path` removes only the corresponding
`*_MEASUREMENT_MISSING` blocker. Missing source coverage remains missing, and
`LIQUIDITY_AND_EXECUTION_THRESHOLDS_UNRATIFIED` remains. Consequently this
integration cannot itself produce `ELIGIBLE` or approve any order.

CI uses synthetic KRX and KIS-shaped fixtures only. It verifies calculations,
hash/date/identity binding, small-sample suppression, POST/NX/crossed-book
rejection, authority locks, public redaction, and the non-promotion registry
effect. Provider secrets and raw live data never enter pull-request CI.
