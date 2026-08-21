# Business Acceleration Radar Contract (P3-05)

Status: comparable published-growth pattern and radar-case capability
implemented. Cross-company coverage, source hierarchy, importance thresholds,
candidate ranking, and live population remain unratified or unimplemented.

## Purpose

`discovery/business_acceleration.py` consumes three consecutive
`evidence_envelope/1` observations for one company and one unchanged published
growth-rate measurement. Supported metric classes are revenue growth, order
growth, and guidance growth; supported periods are monthly and quarterly.

The helper uses the source-published percentage values. It does not parse
management text, infer a metric, convert currency, annualize, fill a missing
period, or compare unlike measurement bases. A caller must supply the exact
measurement identity, comparison basis, frequency, Atlas asset ID, and three
evidence envelopes.

## Pattern contract

The periods must be consecutive calendar month-ends or quarter-ends. For
published growth rates `v1`, `v2`, and `v3`, the helper records:

- prior change: `v2 - v1` percentage points;
- latest change: `v3 - v2` percentage points; and
- change in acceleration: `(v3 - v2) - (v2 - v1)` percentage points.

A `TWO_STEP_ACCELERATION_OBSERVED` radar case is created only when both the
prior and latest changes are strictly positive. `LATEST_STEP_UP_ONLY` records
one latest increase without creating a case. A zero or negative latest change
is `LATEST_STEP_NOT_UP`. These names describe published numeric motion only;
they do not claim business quality, valuation, persistence, materiality, or an
investment opportunity.

All arithmetic uses decimal strings, 50-digit intermediate precision, and
12-decimal HALF_EVEN output. Floats, NaN/infinity, mixed units, missing periods,
non-consecutive dates, subject/measurement drift, and duplicate series fail
closed.

## Evidence and missing data

An available point must be a consistent consumable evidence envelope with
acquisition provenance, a registered partial-coverage source ID, HTTPS URL,
source SHA-256, observed `available_at`, retrieval timestamp, `pct` unit, and
retrieval no later than the packet's `as_of_utc`.

`EVIDENCE_BLOCKED` and `EVIDENCE_UNRESOLVED` remain
`UNKNOWN_EVIDENCE`. They are never replaced with zero, neutral, a previous
period, or another source. The source registry does not establish a hierarchy
and automatic fallback is not performed.

## Case and authority boundary

A created case preserves the three source identities and exact values, plus
the transparent arithmetic that caused it to be recorded. Every case retains:

- `importance = UNRATIFIED`;
- `candidate_rank = null`;
- `candidate_eligible = false`;
- `stage_transition = null`; and
- `action = null`.

Source ranking, importance ranking, cross-company comparison, candidate
ranking, Rule evaluation, Stage promotion, Production, and trading authorities
are false. This capability cannot convert a radar record into Ready, Buy, or an
order.

## Persisted packet validation

`validate_packet()`은 저장된 모든 series result의 3기간 연속성, 12자리 decimal
정규화, prior/latest/acceleration 변화, pattern, case 생성 여부, 요약 카운트를
다시 계산한다. 생성된 case는 보존된 3개 numeric value와 source lineage까지
해당 series result에 역대조하고 모든 권한 봉쇄값을 확인한다. 값을 바꾼 뒤
`payload_sha256`을 다시 계산해도 semantic drift는 통과하지 않는다.

case가 생성되지 않은 series result는 원 evidence envelope를 packet에 보존하지
않으므로 standalone validator가 원 입력이나 source completeness까지 증명하지는
않는다. 그 범위는 원 evidence를 직접 검증하는 `build_packet()` 경로에 남는다.

## Offline command

```bash
python3 discovery/business_acceleration.py /tmp/business-acceleration-input.json \
  --out /tmp/business-acceleration.json
```

The module has no network client and writes only the requested atomic output.
It does not publish a tracked radar, modify existing Discovery cases, or wire a
workflow.
