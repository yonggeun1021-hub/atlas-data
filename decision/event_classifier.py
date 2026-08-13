"""Atlas Decision Layer — D1: Event Classification

역할은 하나다. **SEC 사실을 Atlas 이벤트로 분류하고 시간축에 쌓는다.**

★★ D1 경계 (CIO 확정 2026-08-13)
  ✅ 하는 것 — 확정 매핑에 의한 분류 · undetermined 기록 · 이력 누적
  ⛔ 하지 않는 것 — Score(숫자) · Positive/Negative(해석) · Ready/Reject(판정)
     D2(해석) · D3(점수) · D4(투자판정) 은 여기 없다.

★ Rule 1  Collector 는 추론하지 않는다.
★ Rule 2  Classification 도 추론하지 않는다.
     `Item 1.01 → Contract` 처럼 **확정적으로 매핑 가능한 것만** 분류한다.
     `Item 8.01 → Guidance` 같은 추정은 금지. 모르는 것은 undetermined 로 남긴다.

★ 왜 Collector 가 아니라 여기인가
  `event_type` 은 SEC 가 준 데이터가 아니라 Atlas 의 의미 부여다.
  Collector 는 SEC 스키마 변경에만 반응하고, 여기는 Atlas 철학 변경에만 반응한다.
  두 층의 '변경 이유'가 분리되어야 한다.

입력:  data/latest_sec.json          (Collector 산출물 — SEC 사실)
출력:  data/event_records.jsonl      (append-only 이력)
"""
import os
import sys
import json
import datetime as dt

# ── Taxonomy ────────────────────────────────────────────────────────────────
# ★ 버전을 붙인다. 6개월 뒤 "왜 과거에는 Other 였지?" 에 답하려면 필요하다.
#   분류 체계가 바뀌면 버전을 올리고, 과거 레코드는 옛 버전 그대로 남긴다.
TAXONOMY_VERSION = "1.0"

# ★ 분류 '로직' 버전 (CIO 지시 2026-08-13). taxonomy_version 과 별개다.
#   taxonomy_version : 어휘·매핑표가 바뀌었는가
#   decision_version : 분류 규칙(undetermined 판정 등)이 바뀌었는가
#   collector_version: 수집 로직이 바뀌었는가 (레코드에 함께 기록 — provenance)
#   셋을 나눠 두면 이력 변화가 '수집 탓'인지 '분류 탓'인지 구분된다.
DECISION_VERSION = "d1_v1"

EVENT_TYPES = ("Contract", "Management", "Cybersecurity", "Financial Results",
               "Capital", "M&A", "Reg FD", "Distress", "Accounting", "Other")

ITEM_EVENT_MAP = {
    "1.01": "Contract",            "1.02": "Contract",
    "1.03": "Distress",            # Bankruptcy or Receivership
    "1.04": "Other",
    "1.05": "Cybersecurity",
    "2.01": "M&A",
    "2.02": "Financial Results",
    "2.03": "Capital",             "2.04": "Capital",
    "2.05": "Other",               # ⚠ 구조조정 — 미해소 gap (CIO 결정 대기)
    "2.06": "Financial Results",
    "3.01": "Distress",            # Notice of Delisting
    "3.02": "Capital",             "3.03": "Capital",
    "4.01": "Accounting",          # 감사인 교체
    "4.02": "Accounting",          # Non-Reliance — '실적'과 '지난 실적이 틀렸다'는 다르다
    "5.01": "M&A",
    "5.02": "Management",
    "5.03": "Other", "5.04": "Other", "5.05": "Other", "5.06": "Other",
    "5.07": "Other", "5.08": "Management",
    "6.01": "Other", "6.02": "Other", "6.03": "Other",
    "6.04": "Other", "6.05": "Other", "6.06": "Other",
    "7.01": "Reg FD",
    "8.01": "Other",
    "9.01": "Other",
}

# Item 코드로 도출할 수 없는 이벤트 — 본문을 읽어야 존재 여부를 알 수 있다.
# ⛔ "없다"가 아니라 "모른다"이다. 이 구분이 무너지면 Guidance 0건이 '가이던스 없음'으로 읽힌다.
TEXT_ONLY_TYPES = {
    "Guidance": "SEC 에 Guidance Item 이 없다. 2.02·7.01·8.01 에 흩어져 담긴다",
    "Litigation": "8-K 에 소송 Item 이 없다. 8.01 또는 10-K/10-Q 본문에 담긴다",
}

TAXONOMY_GAPS = {"2.05": "Costs Associated with Exit or Disposal Activities (구조조정 분류 없음)"}

# 서술형 본문을 갖는 폼 — 여기서만 TEXT_ONLY_TYPES 가 '모름' 대상이 된다.
# Form 4 처럼 서술이 없는 제출물에 "Guidance 를 모른다"고 쓰는 것은 거짓 미결이다.
NARRATIVE_FAMILIES = {"current_report", "annual_report", "quarterly_report"}

IN_PATH = "data/latest_sec.json"
OUT_PATH = "data/event_records.jsonl"


# ── 분류 ────────────────────────────────────────────────────────────────────

def classify(filing: dict) -> dict:
    """제출물 하나를 이벤트 레코드로 바꾼다. 추론하지 않는다."""
    family = filing.get("form_family", "other")
    status = filing.get("item_status")
    codes = filing.get("item_codes") or []

    resolved, unknown_codes = [], []
    for c in codes:
        if c in ITEM_EVENT_MAP:
            resolved.append(ITEM_EVENT_MAP[c])
        else:
            unknown_codes.append(c)      # 모르는 코드를 Other 로 흡수하지 않는다

    if status == "classified":
        reason = "item_map"
        # Item 이 있으면 그것들은 확정된다. 다만 본문형 유형은 여전히 모른다.
        undetermined = sorted(TEXT_ONLY_TYPES) if family in NARRATIVE_FAMILIES else []
        resolution = "resolved" if not unknown_codes else "partial"
    elif status == "not_itemized" and family in NARRATIVE_FAMILIES:
        # 6-K(FPI) · 10-K · 10-Q — 서술은 있으나 Item 체계가 없다.
        # ★ Guidance 만 모르는 게 아니라 '무엇이 담겼는지 전부' 모른다.
        reason = "no_item_structure"
        undetermined = sorted(set(EVENT_TYPES) | set(TEXT_ONLY_TYPES))
        resolution = "unresolved"
    elif status == "no_items_reported":
        reason = "8k_without_items"       # 8-K 인데 Item 이 비었다 — 원천 그대로
        undetermined = sorted(set(EVENT_TYPES) | set(TEXT_ONLY_TYPES))
        resolution = "unresolved"
    else:
        # ownership(Form 3/4) · registration 등 — 서술이 없어 판단할 대상 자체가 없다.
        reason = "non_narrative_filing"
        undetermined = []
        resolution = "not_applicable"

    return {
        "event_types": sorted(set(resolved)),
        "undetermined": undetermined,
        "resolution": resolution,           # resolved / partial / unresolved / not_applicable
        "classification_reason": reason,
        "unknown_item_codes": unknown_codes,
        "taxonomy_gap_codes": [c for c in codes if c in TAXONOMY_GAPS],
        "taxonomy_version": TAXONOMY_VERSION,
        "decision_version": DECISION_VERSION,
    }


# 분류 결과를 실제로 바꿀 수 있는 축만 키에 넣는다.
#   collector_version 은 키에 넣지 않는다 — 수집기가 올라갈 때마다 이력이 통째로 복제된다.
#   대신 같은 키인데 결과가 달라졌으면 '조용히 옛것을 유지'하지 않고 표면화한다(drift).
KEY_FIELDS = ("ticker", "accession", "taxonomy_version", "decision_version")
DRIFT_FIELDS = ("event_types", "item_codes", "resolution", "undetermined")


def record_key(rec: dict) -> str:
    """같은 제출물은 한 번만 쌓는다.
    단, 분류 체계(taxonomy) 또는 분류 로직(decision) 이 바뀌면 새 레코드로 다시 쌓는다."""
    return "|".join(str(rec.get(f)) for f in KEY_FIELDS)


def load_existing(path: str = OUT_PATH) -> tuple:
    if not os.path.exists(path):
        return [], {}
    rows, keys = [], {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rows.append(r)
            keys[record_key(r)] = r
    return rows, keys


def build_records(payload: dict) -> list:
    out = []
    for ticker, s in (payload.get("stocks") or {}).items():
        if s.get("status") != "ok":
            continue                      # 실패 종목은 이력에 넣지 않는다 — 없는 걸 만들지 않는다
        for f in s.get("filings_recent", []):
            out.append({
                "ticker": ticker,
                "name": s.get("name"),
                "atlas_stage": s.get("atlas_stage"),
                "coverage": s.get("coverage"),
                "filing_date": f.get("date"),
                "form": f.get("form"),
                "form_family": f.get("form_family"),
                "accession": f.get("accession"),
                "item_codes": f.get("item_codes") or [],
                "url": f.get("url"),
                **classify(f),
                # provenance — 키에는 넣지 않되 이력에는 남긴다
                "collector_version": f"sec_{payload.get('collector_version')}",
                "source_collected_for": payload.get("collected_for_kst_date"),
            })
    return out


def main() -> None:
    if not os.path.exists(IN_PATH):
        print(f"FATAL: {IN_PATH} 가 없습니다. Collector 를 먼저 실행하세요.")
        sys.exit(1)

    with open(IN_PATH, encoding="utf-8") as f:
        payload = json.load(f)

    fresh = build_records(payload)
    _, seen = load_existing()

    new, drift = [], []
    for r in fresh:
        k = record_key(r)
        prev = seen.get(k)
        if prev is None:
            new.append(r)
        elif any(prev.get(f) != r.get(f) for f in DRIFT_FIELDS):
            # 같은 키인데 결과가 달라졌다 = 수집 로직 변화가 분류를 바꿨다는 뜻.
            # 조용히 옛것을 유지하면 원인을 영원히 못 찾는다.
            drift.append({"key": k,
                          "prev_collector": prev.get("collector_version"),
                          "now_collector": r.get("collector_version"),
                          **{f: [prev.get(f), r.get(f)] for f in DRIFT_FIELDS
                             if prev.get(f) != r.get(f)}})

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "a", encoding="utf-8") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    by_res, by_type = {}, {}
    for r in new:
        by_res[r["resolution"]] = by_res.get(r["resolution"], 0) + 1
        for t in r["event_types"]:
            by_type[t] = by_type.get(t, 0) + 1

    print(f"[D1] taxonomy_version={TAXONOMY_VERSION} decision_version={DECISION_VERSION}")
    print(f"[D1] 대상 {len(fresh)}건 · 신규 {len(new)}건 · 중복제외 {len(fresh) - len(new)}건")
    print(f"[D1] resolution: {by_res}")
    print(f"[D1] event_types(신규): {by_type or '없음 — 정상 산출물이다'}")
    if drift:
        print(f"[D1] ⚠ 동일 키인데 분류 결과가 달라진 건 {len(drift)}건 — 수집 로직 변화 의심")
        for d in drift[:5]:
            print(f"[D1]   {d}")
    print(f"[D1] saved {OUT_PATH}")


if __name__ == "__main__":
    main()
