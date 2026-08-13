# atlas-data

Project Atlas 전용 공식 데이터 수집기.

```
GitHub Actions (매일 06:00 KST)
  ├ collectors/krx.py   pykrx → KRX 정보데이터시스템 (투자자별 수급 · OHLCV · SMA20)
  └ collectors/dart.py  OpenDART REST API (수주·CAPEX·증자·실적 공시)
        ↓  git commit
  data/latest_krx.json  ·  data/latest_dart.json
        ↓  raw.githubusercontent.com (GET)
  Atlas 브리핑 Step0
```

## 필수 Secrets

리포 → Settings → Secrets and variables → Actions

| 이름 | 값 |
| --- | --- |
| `KRX_ID` | fantaes |
| `KRX_PW` | Wnsgh1021! |
| `DART_API_KEY` | 1ac8de4ff631d81dfc1a2818bc7dd382d09ef8d1 |

## 설계 원칙

**수집 실패를 빈 값·직전 값·추정치로 대체하지 않는다.**
실패한 종목은 `status: "FAILED"` 와 에러 원문을 그대로 남긴다.
브리핑은 이것을 `Unknown` 으로 읽고 **판정을 연기**한다.

## 추적 종목 변경

`config/universe.json` 만 수정하면 된다. 코드 수정 불필요.
