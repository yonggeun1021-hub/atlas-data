# B1 migration tooling / inactive preparation

**정확한 상태명은 이것뿐이다.** 아래 표현은 쓰지 않는다.

> ⛔ B1 완료 · Rule SSOT 완료 · `rules.json` 생성 완료 · Rule Inventory 확정 · Actions PASS · production evidence

착수 승인 근거: CIO 판정 2026-08-15 — (가) migration/authority 분리 승인 · (나) v1 셀 단위 승인.

---

## 경계

```
Notion PM Watchlist (B0 source)
        ↓
  candidate extraction              rules/extract.py
        ↓
config/rules.candidates.json        ← migration evidence · NOT authority
        ↓
human-reviewed decomposition & mapping     ← 아직 하지 않았다
        ↓
config/rules.json                   ← Rule SSOT / authority
        ↓
machine Rule Inventory
```

`rules.candidates.json` 은 **authority 가 아니다.** Rule Engine 이 직접 소비하지 않는다.
정본 §21-9① 의 SSOT 판정은 `config/rules.json` 승격 시점에 발생한다.

---

## 파일

| 파일 | 역할 |
|---|---|
| `rules/extract.py` | 순수 변환기 + 파일 어댑터. 네트워크를 타지 않는다 |
| `test/test_rules_extract.py` | fail-closed 회귀 테스트 **37건** |
| `_watchlist_rows.json` | 원천 덤프 — Notion API 토큰 미배선 상태의 임시 입력 |
| `config/rules.candidates.json` | 산출물 (row 12 · cell 46 · population incomplete) |

```bash
python3 test/test_rules_extract.py     # 37 PASS / 0 FAIL
python3 rules/extract.py               # → config/rules.candidates.json
```

---

## v1 이 하지 않는 것 (전부 테스트로 고정)

| 금지 | 근거 | 강제 방식 |
|---|---|---|
| `split_index` 자동 생성 | §21-13 — 문장 분해는 **이관 시점** 작업 | 항상 `null`, 생성 경로 없음 |
| Rule 종류(kind) 부여 | §21-13 — 한 칸이 서로 다른 효과의 두 Rule 을 담는다 | 항상 `null` |
| `rule_id` 부여 | §21-12 — 고유 `rule_id` 는 집계 대상 객체의 것 | 항상 `null`. 대신 `candidate_id`(종목×칸) |
| Rule 개수 집계 | §21-11 · §21-15 — 분해 전에는 셀 수 없다 | `rule_count` 는 항상 `None`. `cell_count` 만 값을 가짐 |
| provisional → official 승격 | §21-15 | `save()` 가 `population_status != "incomplete"` 를 거부 |
| evaluator 입력화 | CIO 확정 | `authority: false` · `consumable_by_evaluator: false`, 변경 시 저장 거부 |
| 없는 객체 복원·추정 | B0 rev.4 본문 미확보 | `population_status: "incomplete"` + 사유 명시 |

## 숨기지 않는 것

- **제외한 칸 7개** — 각각 사유와 함께 `excluded_cells` 에 남는다 (`편입 사유` 포함)
- **빈 칸 26개** — `empty_cells` 에 관측으로 기록
- **모집단 불완전** — `population_note` 에 B0 rev.4 미확보 사실 명시

## fail-closed

원천 0행 · `None` · 타입 불일치 · 후보 칸 소실 · 식별 키 소실 → **`SourceUnavailable` 로 차단하고 산출물을 만들지 않는다.**
A-1(실패했는데 정상처럼 보이는 파일이 남아 다음 슬롯을 초록불로 통과시킨 사고)의 재발 방지다.
"칸이 사라진 것"과 "값이 빈 것"을 구분한다 — 후자만 `empty_cells` 로 기록된다.

---

## 산출물 요약 (2026-08-15 실행 1회)

```
row_count   = 12
cell_count  = 46      ← 칸이다. Rule 수가 아니다
rule_count  = None    ← 분해 전에는 셀 수 없다
population  = incomplete
authority   = False
무손실 검증  = 46/46 원문·sha256 일치 (독립 대조)
```

칸별: `탈락 조건` 12 · `다음 이벤트` 12 · `핵심 지지` 9 · `핵심 저항` 9 · `기술적 무효화` 2 · `진입 패턴` 2

⚠ **46 은 Rule 수가 아니다.** 한 칸이 여러 Rule 을 담을 수 있고(§21-13), 부재 표식
(`미정 — …`, `해당 없음 — …`, `❓미확인`)도 원문 그대로 1건으로 보존돼 있다.
분해·판정은 reviewed migration 단계의 일이다.

---

## Production 경계

- Production HOLD 유지 · 저장소 커밋 **0건** · `4ae592a`/`28bcf86` 무변경
- 이 패키지는 저장소 밖 산출물이다. 세션 컨테이너 소실 방지를 위해 파일로 전달한다
- Actions 미편입 · Fault Injection 스위트 미편입
