"""OpenDART 공시 수집

인증: 환경변수 DART_API_KEY
주의: DART는 종목코드(6자리)가 아니라 고유번호 corp_code(8자리)를 사용한다.
      config/corp_map.json 이 없으면 자동으로 내려받아 생성한다.

v2.2 (2026-09-18) — 악재성 공시를 수집 대상에 추가한다 (CIO 확정,
  CLAUDE_CIO_ADVERSE_DISCLOSURE_CARD_20260916.md · 사용자 확정
  USER_RATIFICATION_DECISION_BUNDLE_20260918.json 항목 2_adverse_disclosure)

  문제: 지금까지 KEYWORDS 7종은 전부 호재·자금조달·실적(Group C)이었다.
        악재성 공시(상장폐지·감사의견·회생파산·횡령배임·불성실공시법인·
        최대주주변경·경영권분쟁)는 한 건도 수집하지 않았다 — 조용한 누락.
  수정: 확정 카드의 A·B 항목을 KEYWORDS 에 추가하고, 매칭된 제목이 어느
        그룹(A/B/C)에 속하는지 `group` + `matched_keyword` 필드로 함께
        기록한다. 이 필드는 **제목 문자열 매칭에서 나온 사실 분류**이며
        가격·중요도 판단이 아니다.
  ⛔ 이 커밋은 수집·분류만 한다. 매도/매수 차단 같은 조치는 구현하지
     않는다 — runtime·decision·portfolio 모듈은 건드리지 않는다.
  ⛔ 관리종목·투자경고·단기과열·불성실공시법인·거래정지 등 KRX 시장조치
     통보는 DART가 아니라 KIS 종목 마스터가 매일 확인한다(2026-09-16 확정).
     그중 DART 공시로도 나오는 것(불성실공시법인 지정)만 여기 추가했고,
     나머지(관리종목·투자경고·단기과열·거래정지)는 KIS 마스터 전용으로
     남겨 중복 수집하지 않는다.

v2.1 (2026-08-13) — Stage 와 Coverage 를 분리한다 (CIO 확정, krx.py v3.1과 동일)
  `atlas_stage: "Coverage"` 는 쓰지 않는다 → `{"atlas_stage": null, "coverage": true}`

v2 (2026-08-13) — 종목 레벨에 Atlas 단계를 실어 보낸다 (krx.py v3과 동일 패턴)
  문제: 공시 payload만 읽으면 그 종목이 Candidate인지 Coverage인지 알 수 없었다.
        브리핑이 "어느 단계 종목의 공시인가"를 판단할 근거가 빠져 있었다 — 조용한 누락이다.
  수정: Notion `편입 사유`의 `Atlas Stage:` 태그를 종목마다 실어 보낸다.
        DB select 원본은 db_state 로 참고 보존만 하고 판정에 쓰지 않는다.
"""
import io
import os
import sys
import json
import zipfile
import datetime as dt
import xml.etree.ElementTree as ET

from common import save, save_incident, load_universe, today_kst, now_utc_iso

# `requests` is imported lazily inside build_corp_map()/fetch() (the only two
# call sites), the same convention used for `websockets` in the Upbit
# realtime capture script — so the offline classify()/is_relevant()/KEYWORDS
# regression (test/test_dart_adverse_filing_classification.py) has zero
# dependency on it. The approved-regression environment
# (requirements-ci.txt) deliberately excludes network-capable packages.

KEY = os.getenv("DART_API_KEY")
if not KEY:
    print("FATAL: DART_API_KEY 환경변수가 없습니다.")
    sys.exit(1)

BASE = "https://opendart.fss.or.kr/api"
CORP_MAP_PATH = "config/corp_map.json"

# Atlas 증거 우선순위에 해당하는 공시만 남긴다 (전체는 노이즈)
#
# 그룹은 확정 카드(CLAUDE_CIO_ADVERSE_DISCLOSURE_CARD_20260916.md)의 조치
# 강도를 그대로 옮긴 것이다 — 이 파일은 조치를 구현하지 않고 사실만 기록한다.
#   A: 보유 시 즉시 매도 + 신규 매수 차단 (회수 가능성·재무제표 신뢰 훼손 등)
#   B: 신규 매수만 차단, 보유는 유지 (신뢰·연속성 훼손이지만 즉시 매도 사유는 아님)
#   C: 기록만 (판단에 쓰지 않음, 기존 7종 — 호재·자금조달·실적)
#
# ★ 각 키워드는 실제 DART/KIND 공시 제목으로 확인한 것만 남긴다(2026-09-18
#   교차검증 — Codex 리뷰 반영). 확인하지 못한 추정 표기(예: 띄어쓰기 변형,
#   "파산선고")는 넣지 않는다 — 못 찾은 것을 "아마 있을 것"으로 만들어내지
#   않는다.
#     · 상장폐지         — 실제 제목 "주권상장폐지사유발생"(포함 매칭)
#     · 정리매매         — 실제 제목에 그대로 등장(정리매매개시 등)
#     · 회생절차         — 실제 제목에 그대로 등장(회생절차개시신청 등)
#     · 파산신청         — 실제 제목 "파산신청"(예: 프로브잇, rcpNo 20250102900590)
#       ⛔ "파산선고"는 실제 공시 제목에서 확인하지 못해 제외했다.
#     · 횡령 / 배임      — 실제 제목 "횡령ㆍ배임혐의발생"(가운데점 ㆍ 포함, 각각
#       독립 부분 문자열로 매칭)
#     · 불성실공시법인   — 실제 제목 "불성실공시법인지정"
GROUP_A_KEYWORDS = [
    "상장폐지",                       # 상장폐지 사유 발생 / 상장폐지 결정
    "정리매매",                       # 정리매매 개시
    "회생절차",                       # 회생절차 개시/신청
    "파산신청",                       # 파산 신청 (파산선고는 미확인 — 제외)
    "횡령",                           # 임원·주요주주 횡령 혐의
    "배임",                           # 임원·주요주주 배임 혐의
    # 감사의견/검토의견 — 실제 제목은 "감사의견거절" 처럼 붙여 쓰지 않는다.
    # 실측 사례(쌍용자동차·코오롱머티리얼, 2021 반기검토):
    #   "반기검토 의견 부적정 또는 의견거절"
    # → "의견 부적정"·"의견거절"이 그 안에 그대로 들어 있다. 감사·반기검토·
    #   분기검토 어느 접두어가 와도 "의견"+거절/부적정/한정 형태는 공통이므로
    #   접두어는 키워드에 넣지 않고 이 부분만 잡는다(거절/부적정 각각 붙여
    #   쓴 형태·띄어 쓴 형태, 그리고 카드 문구의 결합형 "부적정 또는 의견거절").
    "의견거절", "의견 거절",
    "의견부적정", "의견 부적정",
    "의견한정", "의견 한정",
    "부적정 또는 의견거절",
]
GROUP_B_KEYWORDS = [
    "불성실공시법인",                 # 불성실공시법인 지정
    # 최대주주변경 — 실제 KIND 공시 제목은 붙여 쓴 "최대주주변경"뿐이었다
    # (예: "[3S] 최대주주변경", "[AP위성] 최대주주변경",
    #  "[드림어스컴퍼니] 최대주주변경"). 띄어 쓴 "최대주주 변경"은 실제 제목에서
    # 확인하지 못해 제외했다. "최대주주변경을 수반하는 주식양수도 계약 체결"
    # 등 더 긴 실제 제목도 "최대주주변경"을 그대로 포함하므로 이 키워드
    # 하나로 잡힌다.
    "최대주주변경",
    # 경영권분쟁 — 실제 공시 항목명은 "소송등의제기·신청(경영권분쟁소송)"
    # 형태로, 붙여 쓴 "경영권분쟁"이 그 안에 그대로 들어 있다. 띄어 쓴
    # "경영권 분쟁"은 실제 제목에서 확인하지 못해 제외했다.
    "경영권분쟁",
]
GROUP_C_KEYWORDS = [
    "단일판매", "공급계약",        # 1순위 — 확약 물량
    "신규시설투자",                # 1순위 — CAPEX
    "유상증자", "전환사채", "신주인수권부사채",   # 자금조달·희석
    "영업(잠정)실적", "매출액또는손익구조",       # 3순위 — 실적
]

# 매칭·저장 대상 전체 (기존 filter_keywords 계약과 하위호환 — guard.py,
# discovery/dart_event_observation.py 는 이 평평한 리스트만 본다).
KEYWORDS = GROUP_A_KEYWORDS + GROUP_B_KEYWORDS + GROUP_C_KEYWORDS

# 그룹 우선순위 — 제목이 두 그룹 키워드에 동시에 걸리면 조치 강도가 더 센
# 쪽으로 분류를 확정한다 (A: 즉시매도+매수차단 > B: 신규매수차단 > C: 기록만).
# 결정론적 규칙이다 — 리스트 순서나 매칭 우연에 좌우되지 않는다.
_GROUPS_IN_PRIORITY = (
    ("A", GROUP_A_KEYWORDS),
    ("B", GROUP_B_KEYWORDS),
    ("C", GROUP_C_KEYWORDS),
)

LOOKBACK_DAYS = 7


def build_corp_map() -> dict:
    """전체 기업 고유번호 ZIP을 내려받아 {종목코드: corp_code} 생성."""
    import requests  # 지연 import — 위 상단 주석 참조

    print("[dart] corp_map 생성 중...")
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": KEY}, timeout=60)
    r.raise_for_status()
    if r.headers.get("Content-Type", "").startswith("application/json"):
        raise RuntimeError(f"corpCode 응답 오류: {r.text[:200]}")

    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        xml = z.read(z.namelist()[0])

    mapping = {}
    for item in ET.fromstring(xml).iter("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            mapping[stock_code] = corp_code

    os.makedirs("config", exist_ok=True)
    with open(CORP_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False)
    print(f"[dart] corp_map {len(mapping)}건 생성")
    return mapping


def get_corp_map() -> dict:
    if os.path.exists(CORP_MAP_PATH):
        with open(CORP_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return build_corp_map()


def fetch(corp_code: str, days: int = LOOKBACK_DAYS) -> list:
    import requests  # 지연 import — 파일 상단 주석 참조

    end = today_kst()
    start = end - dt.timedelta(days=days)
    r = requests.get(f"{BASE}/list.json", params={
        "crtfc_key": KEY,
        "corp_code": corp_code,
        "bgn_de": start.strftime("%Y%m%d"),
        "end_de": end.strftime("%Y%m%d"),
        "page_count": 100,
    }, timeout=30)
    r.raise_for_status()
    body = r.json()

    status = body.get("status")
    if status == "013":          # 조회 결과 없음 — 정상
        return []
    if status != "000":
        raise RuntimeError(f"DART status={status}: {body.get('message')}")
    return body.get("list", [])


def classify(report_nm: str) -> tuple:
    """제목이 속한 그룹을 사실 기반으로 분류한다 — 가격·중요도 판단이 아니다.

    반환: (group, matched_keyword)
      group 은 "A" / "B" / "C" 중 하나, 또는 어느 키워드에도 걸리지 않으면
      명시적으로 (None, None) — **조용히 "C"로 떨어지지 않는다.**
      matched_keyword 는 실제로 걸린 키워드 원문(디버그·검증용).

    두 그룹에 동시에 걸리는 제목은 조치 강도가 더 센 그룹으로 결정한다
    (A > B > C, `_GROUPS_IN_PRIORITY` 순서 고정) — 그래서 동일 입력은
    항상 동일 결과를 낸다.
    """
    if not report_nm:
        return None, None
    for group, keywords in _GROUPS_IN_PRIORITY:
        for kw in keywords:
            if kw in report_nm:
                return group, kw
    return None, None


def is_relevant(report_nm: str) -> bool:
    return classify(report_nm)[0] is not None


def meta(s: dict) -> dict:
    """★ Atlas 단계는 Notion `편입 사유`의 Atlas Stage 태그에서 온다 (CIO 확정 2026-08-13).
    Stage 와 Coverage 는 서로 다른 축이다 — Coverage 는 Stage 값이 아니다.
      atlas_stage : Discovery / Candidate / Ready / Buy / Holding / Closed / None
      coverage    : true / false / None(Unknown)
    DB select 원본(db_state)은 참고 보존만 하고 판정에 쓰지 않는다."""
    return {
        "atlas_stage": s.get("atlas_stage"),
        "coverage": s.get("coverage"),
        "db_state": s.get("db_state"),
        "in_notion": s.get("in_notion"),
    }


def main() -> None:
    corp_map = get_corp_map()
    payload = {
        "collected_at_utc": now_utc_iso(),
        "collected_for_kst_date": today_kst().isoformat(),
        "source": "OpenDART (금융감독원)",
        "source_tier": "Official",
        "collector_version": "v2.2",
        "lookback_days": LOOKBACK_DAYS,
        "filter_keywords": KEYWORDS,
        # 그룹별 키워드 — group/matched_keyword 필드가 어디서 왔는지 그대로
        # 남긴다. 판단(조치)에는 쓰지 않는다 — 사실 기록·분류 전용.
        "filter_keyword_groups": {
            "A": GROUP_A_KEYWORDS,
            "B": GROUP_B_KEYWORDS,
            "C": GROUP_C_KEYWORDS,
        },
        "stocks": {},
    }

    ok = failed = 0
    for s in load_universe():
        code, name = s["code"], s["name"]
        corp_code = corp_map.get(code)
        if not corp_code:
            payload["stocks"][code] = {
                "name": name, **meta(s),
                "status": "FAILED",
                "error": "corp_code 매핑 없음",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — corp_code 없음")
            continue
        try:
            items = fetch(corp_code)
            relevant = []
            for i in items:
                title = i.get("report_nm", "")
                group, matched_keyword = classify(title)
                if group is None:
                    continue
                relevant.append({
                    "date": i.get("rcept_dt"),
                    "title": title,
                    "rcept_no": i.get("rcept_no"),
                    "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={i.get('rcept_no')}",
                    # 제목 문자열 매칭에서 나온 사실 분류다 (가격·중요도 판단
                    # 아님). A/B/C 의미는 파일 상단 확정 카드 참조를 그대로 따른다.
                    "group": group,
                    "matched_keyword": matched_keyword,
                })
            payload["stocks"][code] = {
                "name": name,
                **meta(s),
                "corp_code": corp_code,
                "status": "ok",
                "total_count": len(items),
                "relevant_count": len(relevant),
                "relevant": relevant,
            }
            ok += 1
            print(f"[ok]     {code} {name} "
                  f"[stage={s.get('atlas_stage')} coverage={s.get('coverage')}] "
                  f"— 전체 {len(items)} / 관련 {len(relevant)}")
        except Exception as e:                      # noqa: BLE001
            payload["stocks"][code] = {
                "name": name, **meta(s),
                "corp_code": corp_code,
                "status": "FAILED",
                "error": f"{type(e).__name__}: {e}",
            }
            failed += 1
            print(f"[FAILED] {code} {name} — {type(e).__name__}: {e}")

    payload["summary"] = {"ok": ok, "failed": failed}
    if ok == 0:
        save_incident(payload, "dart.json")
        print("FATAL: 모든 종목 수집 실패 — 정본 미갱신")
        sys.exit(1)

    save(payload, "dart.json")


if __name__ == "__main__":
    main()
