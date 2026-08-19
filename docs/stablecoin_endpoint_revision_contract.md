# Stablecoin endpoint / revision contract

Status: P1-CR-02 implementation contract

Effective manifest date: 2026-08-20

PIT coverage start: 2026-08-17

## Purpose

DefiLlama Stablecoins 응답의 **현재 스냅샷 변화**와 **과거 시계열
재작성**을 분리한다. 이 계약은 원본 증거의 보존·검증·비교만 담당한다.
Stablecoin Net Issuance 계산, Regime score, 임계값, 가중치, 매매 판단에는
권한이 없다.

기계 판독 정본은 `config/stablecoin_endpoint_contract.json`, 검증 구현은
`.github/scripts/stablecoin_revision_contract.py`다.

## Endpoint semantics

| Name | Semantics | 비교 의미 |
| --- | --- | --- |
| `stablecoincharts_all` | `historical_series` | 같은 `date` 값 변경은 `historical_revision`; 기존 날짜 제거는 `historical_reindex`; 과거 날짜 추가는 `historical_backfill`; 기존 최댓값보다 뒤의 날짜 추가는 `forward_append` |
| `stablecoincharts_Terra` | `historical_series` | 위와 동일 |
| `stablecoinchains` | `live_snapshot` | 호출 시점 스냅샷 변화만 기록. 서로 다른 날짜의 차이를 historical revision으로 해석하지 않음 |
| `stablecoins_withprices` | `live_snapshot` | 호출 시점 스냅샷 변화만 기록. `circulatingPrev*` 변화도 이 endpoint만으로 historical revision이라 부르지 않음 |

응답 SHA 변경은 transport-level 관측값이다. `historical_series`는 날짜별
record 비교 후에만 revision/reindex/backfill/append를 판정하고,
`live_snapshot`은 언제나 `revision_inference = not_applicable`로 닫는다.

## PIT and storage policy

- 일별 디렉터리는 direct fetch 결과를 append-only로 보존한다.
- 이미 `_sha256.txt`가 존재하는 날짜는 덮어쓰지 않는다.
- 압축 전 응답 bytes의 SHA-256을 정본 해시로 사용한다.
- 2026-08-20부터 `_manifest.json`은 endpoint별로 다음 값을 반드시 가진다.
  `fetched_at_utc`, `endpoint`, `response_sha256`, `byte_length`,
  `collector_version`.
- 최초 두 capture(2026-08-17, 2026-08-18)는 raw bytes, 다운로드 시각,
  원문 해시가 이미 고정되어 있다. 존재하지 않았던 manifest를 사후 생성하지 않고
  `legacy_pre_manifest`로 보존한다.
- 일별 snapshot 또는 endpoint가 없으면 변화 0으로 간주하지 않는다.
  `NO_VINTAGE_MECHANISM`으로 fail-closed한다.
- 누락된 과거 응답을 현재 history endpoint 응답으로 재구성하지 않는다.
- source code 내부의 forward-fill, pricing, deletion, backfill 동작은 별도의
  source path와 commit SHA 증거 없이는 이 계약이 보증하지 않는다.

## Validation and comparison

로컬 저장 자료 전체의 무결성 검증:

```bash
python .github/scripts/stablecoin_revision_contract.py validate-all
```

두 PIT capture의 endpoint-aware 비교:

```bash
python .github/scripts/stablecoin_revision_contract.py compare \
  evidence/stablecoin/raw/2026-08-17 \
  evidence/stablecoin/raw/2026-08-18
```

현재 저장된 8/17 → 8/18 표본에서는 다음이 관측된다.

- `stablecoincharts_all`: `forward_append` 1건 +
  `historical_revision` 1건
- `stablecoincharts_Terra`: `forward_append` 1건 +
  `historical_reindex` 1건
- `stablecoinchains`, `stablecoins_withprices`: `snapshot_changed`.
  historical revision 판정은 하지 않음

이는 endpoint별 분류가 필요한 실제 회귀 표본이며, 변화의 경제적 원인을
증명하거나 Net Issuance를 계산하는 결과가 아니다.
