# Authoritative baseline — CIO 승인 2026-08-15

이 파일은 artifact 가 아니다. 해시 체인에 들어가지 않고 evaluator 도 읽지 않으며
`run_all.py` 의 byte 비교 대상도 아니다. **승인 사실의 기록**일 뿐이다.

## 승인 범위

- reproducibility 수정(실행 이력 필드 분리) **CLOSED**
- 아래 14개 해시를 authoritative baseline 으로 확정
- Rule 의미값 · 상태 · CIO 판정 36건은 **다시 열지 않는다**

⛔ 이 승인은 Production 승인도 evaluator 승인도 아니다.
   `Production HOLD` · `consumable_by_evaluator=false` · `Evaluator Consumable 0` 유지.

## baseline (sha256)

| 파일 | sha256 |
|---|---|
| `config/rules.candidates.json` | `09cb753e7b5b6f838c0b6177e2d68ef4d534625c6a962dd06949b22d3495af8d` |
| `rules/decompose_full.json` | `218749a2e29b7dc74602d1e5bf4240b6a35883dcd5d33c9a79671fcb387accd9` |
| `rules/canonical_rules.json` | `d067b5841bd59d4984f0240656e2e896a18b79655da77aafe88b84e78920da21` |
| `rules/equivalence_candidates.json` | `7364d6eeafa7402381ad544069c2b8cc7fd30dcf52683edc126c1828311b154e` |
| `rules/merge_decision.json` | `5f0fbdf822ca0ecc94f550a046666c0921764721b90f5fa2d9939cb15289fcb1` |
| `rules/definition_inventory.json` | `8b7a99b186038e727fc8c131d2b78f21d4b8623589c80b02bd5e08d64d6f08fa` |
| `rules/definition_decision.json` | `bf3abd1dff2c4bc739b66f25bc048e663820b0b4711baf691a0abf3c9791f3f9` |
| `rules/data_source_ambiguity.json` | `77264eb619186ad5d9560092c30036571f826c34003534d119dddd643d1ad9d2` |
| `rules/decision_normalization.json` | `dc840fa9ac874bdd67291c0fca59a245cd0bf8b39ed8ec30a92606765ab19fab` |
| `rules/decision_cards.json` | `d019a4f68eda8ee9bd22f56e9d8c1abbb561879968c4832ec86eb229a8a33585` |
| `rules/ssot_mapping.json` | `b70722a3086de7cb5355874fa89fd6bf4710d618e8848bd0fa19f5f8c7869c0e` |
| `config/rules.json` | `5afbc5e1bc918e6cc0b58bd0d2af21075d600e37ab7e0451a291ae9c612db354` |
| `rules/monitoring_identity.json` | `864c8d5755f29e741c8ac5d4d3cda3b4ecde087646957faf11e511c9610f99ae` |
| `rules/rule_inventory.json` | `f7e024c78abbed77c2178a1ec9882ff0804f8c5037837245bec3d0ab3ade0415` |

## 재현성 계약

같은 입력이면 **직전 출력 파일의 존재 여부와 무관하게** 위 14개 바이트가 같아야 한다.
세 경우 모두 회귀로 고정돼 있다 (`test/test_fault_injection.py` REPRO).

1. 출력 파일이 없는 상태에서 전체 DAG 빌드
2. 출력 파일이 있는 상태에서 재빌드
3. 제거된 실행 이력 필드가 되살아난 이전 형태 위에서 빌드

★ 실행 이력(`assignment` · `unchanged` · `newly_assigned`)은 authoritative payload 가
아니라 `build()` 의 진단 반환값이다. 다시 payload 로 옮기면 이 계약이 깨진다.

## Gate 상태

| Gate | 상태 |
|---|---|
| Rule SSOT (`config/rules.json`) | 승인 완료 |
| Machine Rule Inventory | PASS / CLOSED |
| reproducibility baseline | 승인 완료 |
| **Actions PASS** | **OPEN** — 실제 GitHub-hosted run 성공 전까지 CLOSED 아님 |

⛔ 로컬 시뮬레이션이나 disposable copy 의 PASS 를 `Actions PASS` 로 대체하지 않는다.

## KNOWN GAP (숨기지 않고 남긴다)

- **FI-3 frozen input tamper** — NOT GATED. `_watchlist_rows.json` ·
  `decompose_full.b1_frozen.json` 의 파일 단위 변조를 현재 계약이 차단하지 못한다.
  `b1_frozen.sha256` 은 기록만 되고 대조되지 않는다.
- D-4 ~ D-7 — `DEFERRED.md` 참조. 유예 판정 유지.
