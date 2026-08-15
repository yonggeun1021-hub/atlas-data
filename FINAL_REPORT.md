# B1 46칸 전체 분해 — CIO 검수 수정 후 최종 결과

```
46/46 cells covered      · invariant violations 0
unresolved objects 4     · READY 1
reference 21             · monitoring 16
non-rule evidence 42     (주석·부재표식 18 + 결합표기 24)
rule_candidate 39        · 총 조각 106
```

⛔ **`READY 1` 은 Rule Inventory 수가 아니다.** `DEFINED × AVAILABLE` 이라는 구조적 파생 결과 1건이며, Stage 변경·투자 판단으로 이어지지 않는다.
⛔ `rule_candidate 39` 도 Rule 수가 아니다 — canonical rule_id 미부여 · dedup 미착수 상태의 source occurrence 수다.

## 수정 전 / 후 회귀 결과

| 스위트 | 수정 전 | 수정 후 |
|---|---|---|
| extractor fail-closed | 37 PASS / 0 FAIL | **37 PASS / 0 FAIL** |
| decomposition pilot | 36 PASS / 0 FAIL (양성 1건이 오류였음) | **36 PASS / 0 FAIL** |
| 46칸 전체 검증 | *실행 경로 없음* | **24 PASS / 0 FAIL** |
| 46칸 불변식 | 위반 0 (단 ③ 정책 불일치로 놓치는 구간 존재) | **위반 0** |

합계 **97 PASS / 0 FAIL**

