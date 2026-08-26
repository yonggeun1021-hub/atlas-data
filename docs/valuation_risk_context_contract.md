# P3-10 Valuation / Risk deterioration context contract

Status: Crypto BTC PIT risk-source population is scheduled; candidate binding,
valuation population, and interpretation policy remain gated.

## Candidate boundary

`discovery/valuation_risk_context.py` consumes immutable references to existing
Discovery Cases. A reference includes the case schema, case ID, asset/market,
observation date, and exact case payload SHA-256. The output never rewrites the
candidate, changes its rank or Stage, evaluates a Rule, or creates an action.

For each candidate the packet keeps separate `VALUATION` and `RISK` sections.
If a dimension is absent it is `EVIDENCE_ABSENT`; incomplete observations are
`UNKNOWN_EVIDENCE`. Neither state is converted to zero, neutral, or safe.

## Raw context

Every context metric uses exactly two caller-declared dates. The helper emits
the prior value, latest value, and `latest - prior` change together with all
source identities. Valuation metrics for US/Korea require both a fundamental
source and a market-price source at each point. Risk metrics require the
market-specific price source. Crypto valuation is intentionally unsupported
and remains `UNDEFINED_NO_RATIFIED_METRIC`.

This capability reuses source fragments; it does not claim that their upstream
P1 contracts are operationally ratified. In particular, US/Korea risk input
policies and Korea publication timing remain open.

## Deterioration policy gate

Raw context never implies that a candidate deteriorated. An external policy
must be effective and explicitly `RATIFIED`, and must bind the exact market,
context ID, dimension, measurement, metric type, unit, and comparison basis.
Only that policy may specify `HIGHER_IS_DETERIORATION` or
`LOWER_IS_DETERIORATION` and a non-negative minimum change.

The policy hash and ratification proof are attached to an interpreted metric.
There is no repository default policy, metric selection, or source hierarchy.
Even a matched deterioration label grants no candidate rank, Stage promotion,
Rule evaluation, portfolio action, Production, or trading authority.

## Persisted packet validation

`valuation_risk_context_packet/3`는 정규화한 `source_policy` 전체를 보존한다.
`validate_packet()`은 ingestion과 동일한 정책 validator를 소비 시점에 다시
실행하고, 정책 SHA·유효기간·exact context rule·방향·minimum으로 interpretation
label과 proof를 재계산한다.

또한 저장된 candidate reference 형식, dimension grouping,
2-point status/source lineage, canonical decimal change, missing state,
interpretation label·proof, candidate/dimension/packet summary와 모든 권한 봉쇄를
다시 검증한다. source requirement group과 deterioration 방향·minimum 비교도
보존 값에서 재수행하므로 self-rehashed change, lineage, label, action drift는
실패한다.

원 Discovery Case 본문과 full interpretation-policy rule은 packet에 포함되지 않고
각각 SHA-256만 남는다. standalone validator는 그 두 원문의 진위를 증명하지
않으며, 원 입력을 직접 검증하는 `build_packet()`이 해당 경계를 담당한다.

## Operation

The generic helper makes no network request and writes only to an explicit path
outside the repository. A failed run preserves an existing output.

`.github/scripts/p3_10_crypto_risk_population.py` reuses the scheduled immutable
Kraken snapshot, the existing `btc_risk/v1` transform, and the ratified BTC
canonical identity. It publishes the latest two finalized observations for
current drawdown magnitude, maximum drawdown magnitude, and annualized realized
volatility under
`evidence/valuation_risk_sources/crypto/YYYY-MM-DD/rev-001.json`. Existing
revisions must be byte-identical and are never overwritten.

The tracked packet is deliberately detached from candidates. If no existing BTC
case uses an allowed Discovery Case schema, the packet remains
`BLOCKED_NO_ALLOWED_CASE`. The source adapter exposes no candidate-binding
function: a later consumer must authenticate an actual committed Discovery Case
before binding these observations through the generic P3-10 contract. The
adapter does not create a case or interpretation policy. Crypto valuation,
US/Korea live population, deterioration policy, and briefing integration remain
separate WBS gates.
