# P1 — 미국 가격 Source Contract (CIO 확정 2026-08-15)

## 대상 Rule

| Cluster | Rule | 필요한 capability |
|---|---|---|
| **P1-a** | `RULE-0005` | TSM 확정 미조정 정규장 종가 (단일 값) |
| **P1-b** | `RULE-0006` | TSM 종가 시계열 + 최근 20 거래일 SMA20 |
| **P1-b** | `RULE-0010` | CRDO·ANET 종가 + 공통 거래일 정렬 + 직전 비교가능 관측 |
| P2 이관 | `RULE-0012` | 실적 3축 + 발표시각 + P1 가격 capability |
| 범위 밖 | `RULE-0009` | `B 박스권` 정의 자체가 미해결 |

⛔ P1-a 와 P1-b 는 **Source Contract 를 공유하되 evaluation contract 는 공유하지 않는다.**
collector 는 종가 관측을 사실대로 공급하고, SMA20 · 공통 거래일 상대강도 · 2회 연속 지속 같은
의미 계산을 collector 안에 넣지 않는다.

## Decision SSOT — 확정

**US Price Decision Observation = primary listing exchange 의 정규장 Closing Auction / Cross Price**

| 종목 | 상장 시장 | Decision 관측값 |
|---|---|---|
| `TSM` | NYSE | NYSE closing-auction observation |
| `ANET` | NYSE | NYSE closing-auction observation |
| `CRDO` | Nasdaq | Nasdaq Closing Cross observation |

### 세 줄 규칙 — 여기서 물러서지 않는다

```
closing auction 존재            → AVAILABLE
closing auction 없음/식별불가    → UNAVAILABLE (fail-closed)
last sale 로 보충              → 금지
```

⛔ **fallback 금지 대상을 명시한다** — consolidated last sale · NLS+ EOD close ·
vendor 의 `close` 필드 · 그 밖의 last-sale 계열 값으로 대체하지 않는다.
경매 미성립일에 last sale 로 채우면, 아래에서 폐기한 문제를 그대로 다시 들여오는 것이 된다.

### 조정 정책

- Decision 입력은 **미조정(unadjusted) 정규장 확정 종가**다. 그 거래일 당시의 실제 값이다.
- ⛔ collector 가 과거 가격을 조용히 adjusted 값으로 치환하지 않는다.
- corporate action 은 **가격과 별도의 observation stream** 으로 유지한다.
- **rebase gate** — 주당 가격 단위를 기계적으로 바꾸는 사건(split · reverse split ·
  stock dividend 등)이 탐지되면 절대가격 Rule 을 `rebase_required` 로 올리고
  **CIO 판정 전 자동 평가를 금지**한다.
  ⛔ 일반 현금배당만으로는 절대가격 Rule 을 자동 HOLD 하지 않는다 — 미조정 종가를 쓰는 이상
  배당락 하락은 실제 시장가격 변화다.

## 조사로 폐기된 후보 — 재조사하지 않는다

| 후보 | 판정 | 근거 (문서) |
|---|---|---|
| Nasdaq `EOD Trade Summary` 의 `Consolidated Closing Price` | **FAIL** | SEC Rule Filing 34-91458 이 이 필드를 *"The final last sale eligible transaction on Tapes A, B or C received on the trading day"* 로 정의 — last-sale 값이며 primary listing official close 가 아니다 |
| Databento `EQUS.SUMMARY` | **FAIL** | 원천이 Nasdaq NLS+ 이며, 그 종가 필드가 위 `Consolidated Closing Price` 다. 문서가 *"마지막 summary(20:15 ET)만 제공"* 하며 *"다른 소스와 비교 시 불일치를 낳을 수 있다"* 고 자체 경고 |
| Nasdaq `historical-nocp` 웹페이지 | **취득 불가** | 페이지는 존재하나 `Data is currently not available` — 여러 티커에서 동일 |
| NYSE `nyse.com/quote/...` | **취득 불가** | 가격 데이터 미렌더 |

⛔ **무료 Gold 경로 탐색은 반복하지 않는다.** 위 두 경로에서 이미 실패했고,
공식 종가의 기계적 정의는 그 대신 명확해졌다.

### 왜 NOCP 를 TSM·ANET 에 쓰면 안 되는가 (문서 근거)

Nasdaq 공식 문서상 NOCP 는 조건부 정의다.

- Nasdaq 상장: Nasdaq Cross 체결가(경매 발생 시) · 경매 없으면 "Nasdaq 에서 실행된 마지막 적격 거래의 reprint"
- **비 Nasdaq 상장: "Nasdaq 에서 실행된 마지막 적격 거래의 reprint"**

따라서 NYSE 상장 종목에 Nasdaq 계열 NOCP 를 쓰면 *Nasdaq 거래소에서의 마지막 체결* 을
공식 종가로 오인하게 된다.

## acquisition candidate — 아직 채택하지 않았다

| 후보 | 상태 | 비고 |
|---|---|---|
| Databento `XNYS.PILLAR` statistics | **UNVERIFIED** | NYSE *Cross Trade* 메시지를 statistics 로 정규화하며 auction type 에 `Closing price: Closing auction` 존재. 다만 문서가 "Official Closing Price" 라고 말하지 않으며, **경매 미성립일 동작이 미확인** |
| Databento `XNAS.BASIC` statistics | **UNVERIFIED** | NLS+ *Trade Report* 의 sale condition 기반 opening/closing price statistics. NOCP 라고 명시하지 않음 |
| Databento Corporate Actions | **PASS** | 61 이벤트 타입(정·역분할, bonus issue, 배당 각 일자), 조정계수, 6년 point-in-time, 일 4회 갱신 |
| Massive (구 Polygon) | 보류 | `close` 정의 미기재 · official-close 전용 필드 문서에 없음 · `adjusted` 기본값이 조정가 |

⛔ 유료 Gold 구매 · Databento 계정/API key 발급 · collector 구현 · Rule 상태 변경은 하지 않았다.

## 경계 (유지)

Production HOLD · `consumable_by_evaluator=false` · evaluator 미연결 ·
`RULE-0005 · 0006 · 0010` 은 `DATA_MISSING` · `SOURCE_UNRESOLVED` 그대로.

⛔ 이 문서는 **관측값의 의미**를 확정한 것이다. 취득 경로 확정도, source 채택도 아니다.
