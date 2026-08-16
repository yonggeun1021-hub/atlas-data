#!/usr/bin/env python3
"""RULE-0021 — Azure CC 표의 **원문 HTML 블록**을 bounded fixture 로 보존한다.
(CIO 판정 2026-08-16 · 항목 5)

★ 왜 이 스크립트가 존재하는가
   회귀 fixture 가 **합성 마크업**이었던 것이 이번 Gate 실패의 배경이다.
   실제 SEC 마크업을 확보하지 않으면 같은 실패를 반복한다.
   ⛔ 그러나 「전체 1MB 문서」를 fixture 로 넣지 않는다 — 대상 `<table>` 블록과
      그 **식별에 필요한 최소 주변 markup** 만 보존한다.

★ 절대 규칙 — 재구성하지 않는다 (CIO 판정 그대로)
   이 스크립트는 원문 문자열을 **잘라내기만** 한다.
   · 파싱 후 재직렬화하지 않는다
   · 값을 다시 쓰지 않는다 · 공백/엔티티를 정규화하지 않는다
   · 태그를 보정하거나 닫지 않는다
   보존물이 원문의 **부분 문자열**임을 매 실행마다 검증하고 manifest 에 기록한다.

★ 경계
   ⛔ 이 스크립트는 `msft_azure_cc.py` 를 수정하지 않는다. 승인된 취득 함수를
      **재사용**만 한다 — collector 의 「저장소를 쓰지 않는다」 불변식은 그대로다.
   ⛔ 저장소 안에 쓰지 않는다. 출력은 `FIXTURE_OUT`(기본: 임시 디렉터리)이며
      Actions 가 artifact 로 업로드한다. 작업 트리는 깨끗한 상태로 남는다.
   ⛔ Rule 상태를 바꾸지 않는다. 값을 판정하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "collectors"))
# ★ 승인된 취득 경로를 그대로 재사용한다. 여기서 다시 구현하지 않는다.
import msft_azure_cc as M                                          # noqa: E402

# ── capture 전용 조회 상한 override (CIO 판정 2026-08-16) ──────────────
#   ⛔ production 의 `M.MAX_FILINGS` 를 **바꾸지 않는다.** capture 도구에서만 덮는다.
#      RULE-0022 의 전년동기 이력 확보라는 일회성 목적으로 RULE-0021 production
#      collector 의 탐색 범위까지 넓히지 않기 위한 것이다.
#   ⛔ 환경변수가 없으면 반드시 `M.MAX_FILINGS` 다 — 기존 동작과 완전히 같다.
#   ⛔ 이 숫자는 규칙이 아니다. 판정 기준은 「필요한 공식 보고기간이 확보됐는가」이며
#      상한 값 자체에 의미를 부여하지 않는다.
#   ⛔ 잘못된 값이면 기본값으로 조용히 되돌아가지 않는다 — 중단한다.
CAPTURE_LIMIT_ENV = "ATLAS_CAPTURE_MAX_FILINGS"
RE_POSITIVE_INT = re.compile(r"^[1-9][0-9]*$")


def capture_limit(env=None):
    """(상한, 문제) — 문제가 있으면 상한을 쓰지 않고 중단한다."""
    env = os.environ if env is None else env
    raw = env.get(CAPTURE_LIMIT_ENV)
    if raw is None or not raw.strip():
        return M.MAX_FILINGS, None
    raw = raw.strip()
    if not RE_POSITIVE_INT.match(raw):
        return None, (f"{CAPTURE_LIMIT_ENV}={raw!r} 이 양의 정수가 아니다 — "
                      f"기본값으로 되돌리지 않고 중단한다")
    return int(raw), None
from c4_sec_edgar_check import get                                 # noqa: E402

# ── 무엇을 잘라낼지 — 문면은 **관측된 것만** 열거한다 ──────────────────
#   ⛔ `.*` 같은 포괄 완화를 쓰지 않는다 (CIO 판정 2026-08-16 · 항목 2).
#   ⛔ 이것은 collector 의 추출 계약이 아니다. **fixture 를 잘라낼 위치를 찾는**
#      앵커일 뿐이다. 계약 갱신은 회귀가 확보된 뒤 별도 커밋에서 한다.
TITLE_WORDS = ["Selected", "Product", "and", "Service",
               "(?:Revenue|Information)",           # 구형 · 신형
               "Constant", "Currency", "Reconciliation"]
AZURE_WORDS = ["Azure", "and", "other", "cloud", "services"]

# 워드 사이에 올 수 있는 것 — 공백 · 엔티티 · 줄바꿈
GAP = r"(?:\s|&nbsp;|&#160;|&#xa0;)+"

MAX_SLICE_BYTES = 300_000       # 상한 초과 시 조용히 자르지 않고 fail-closed
MAX_TITLE_DISTANCE = 40_000     # 제목이 표에서 이 이상 떨어지면 같은 절로 보지 않는다
POLITE_DELAY_SEC = 0.5


def build_pattern(words) -> re.Pattern:
    return re.compile(GAP.join(words), re.I)


RE_TITLE = build_pattern(TITLE_WORDS)
RE_AZURE = build_pattern(AZURE_WORDS)


def text_with_offsets(raw: str):
    """태그 밖 문자만 모으고, 각 문자의 **원문 인덱스**를 함께 돌려준다.

    ★ 원문 오프셋을 잃지 않는 것이 이 함수의 존재 이유다. 텍스트만 뽑으면
      어디를 잘라야 할지 알 수 없고, 그러면 재구성으로 갈 수밖에 없다.
    ⛔ 엔티티를 풀지 않는다 — 원문 그대로 둔다.
    """
    chars, idx, in_tag = [], [], False
    for i, ch in enumerate(raw):
        if ch == "<":
            in_tag = True
            continue
        if ch == ">":
            in_tag = False
            continue
        if in_tag:
            continue
        chars.append(ch)
        idx.append(i)
    return "".join(chars), idx


def table_spans(raw: str):
    """`<table>` 여닫이를 짝지어 (시작, 끝) 목록을 만든다. 중첩을 센다."""
    events = []
    for m in re.finditer(r"<\s*table\b", raw, re.I):
        events.append((m.start(), "open"))
    for m in re.finditer(r"<\s*/\s*table\s*>", raw, re.I):
        events.append((m.start(), "close", m.end()))
    events.sort(key=lambda e: e[0])
    stack, spans = [], []
    for ev in events:
        if ev[1] == "open":
            stack.append(ev[0])
        elif stack:
            spans.append((stack.pop(), ev[2]))
    return spans


def innermost_span(spans, pos):
    """`pos` 를 포함하는 가장 안쪽 `<table>` 구간. 없으면 None."""
    best = None
    for s, e in spans:
        if s <= pos < e and (best is None or (e - s) < (best[1] - best[0])):
            best = (s, e)
    return best


def outermost_span_after(spans, pos, after):
    """`pos` 를 포함하되 `after` **뒤에서 시작하는** 가장 바깥 `<table>` 구간.

    ★ SEC 는 데이터 표를 레이아웃 표 안에 넣는다. 안쪽 표에서 끊으면 바깥 표가
      닫히지 않은 **깨진 markup** 이 된다 — fixture 로 못 쓴다.
    """
    best = None
    for s, e in spans:
        if s <= pos < e and s > after and (best is None or (e - s) > (best[1] - best[0])):
            best = (s, e)
    return best


def balanced(frag: str) -> bool:
    """조각 안에서 `<table>` 여닫이가 맞는지. 도중에 음수로 내려가도 안 된다."""
    depth = 0
    for m in re.finditer(r"<\s*(/?)\s*table\b", frag, re.I):
        depth += -1 if m.group(1) else 1
        if depth < 0:
            return False
    return depth == 0


def locate_block(raw: str):
    """대상 `<table>` 블록 + 최소 주변 markup 의 (시작, 끝, 진단) 을 찾는다.

    ⛔ 후보가 정확히 1건이 아니면 fail-closed 하고, **무엇을 걸렀는지** 남긴다
       (collector 공통 규칙).
    """
    text, off = text_with_offsets(raw)
    spans = table_spans(raw)

    # ① Azure 행이 들어 있는 표를 후보로 삼는다
    cands, diag = [], []
    for m in RE_AZURE.finditer(text):
        raw_pos = off[m.start()]
        sp = innermost_span(spans, raw_pos)
        if sp is None:
            diag.append(f"Azure 문구가 표 밖에 있다 (raw {raw_pos})")
            continue
        # ② 그 표 앞쪽에 제목이 있어야 한다 — 없으면 다른 표다
        title = None
        for t in RE_TITLE.finditer(text):
            t_raw = off[t.start()]
            if t_raw < sp[0] and sp[0] - t_raw <= MAX_TITLE_DISTANCE:
                if title is None or t_raw > title:
                    title = t_raw
        if title is None:
            diag.append(f"표[{sp[0]}:{sp[1]}] 앞 {MAX_TITLE_DISTANCE}자에 제목이 없다")
            continue
        if (sp, title) not in cands:
            cands.append((sp, title))

    uniq = {sp: ti for sp, ti in cands}
    if len(uniq) != 1:
        print(f"  ✗ 대상 표 후보가 정확히 1건이 아니다 ({len(uniq)}건)")
        print(f"    Azure 문구 출현 {len(list(RE_AZURE.finditer(text)))}회 · "
              f"제목 출현 {len(list(RE_TITLE.finditer(text)))}회 · 표 {len(spans)}개")
        for d in diag[:M.CAND_LOG_LIMIT]:
            print(f"    후보탈락 {d}")
        for sp in list(uniq)[:M.CAND_LOG_LIMIT]:
            print(f"    후보표 {sp}")
        return None

    (t_start, t_end), title_raw = next(iter(uniq.items()))

    # ③ 레이아웃 표까지 포함해 **닫힌** 구간을 고른다
    outer = outermost_span_after(spans, t_start, title_raw)
    if outer is not None:
        t_start, t_end = outer

    # ④ 제목을 감싼 태그의 시작까지 확장한다 — 최소 주변 markup
    lt = raw.rfind("<", max(0, title_raw - 300), title_raw)
    start = lt if lt != -1 else title_raw

    # ⑤ ★ 깨진 markup 은 만들지 않는다. 제목을 포함해서 균형이 깨지면
    #    표 시작부터 자르고(제목 제외), 그래도 안 맞으면 fail-closed.
    title_included = True
    if not balanced(raw[start:t_end]):
        start, title_included = t_start, False
        print("    ⚠️ 제목을 포함하면 표 여닫이가 맞지 않는다 — 표부터 자른다")
    if not balanced(raw[start:t_end]):
        print(f"    ✗ 잘라낸 구간의 `<table>` 여닫이가 맞지 않는다 [{start}:{t_end}] "
              f"— 보정하지 않고 중단한다")
        return None

    return start, t_end, {"table_start": t_start, "table_end": t_end,
                          "title_raw": title_raw, "slice_start": start,
                          "title_included": title_included}


def slice_and_verify(doc_text: str, start: int, end: int):
    """원문에서 잘라내고 **재구성하지 않았음을 검증**한다. 실패하면 None.

    ★ 이 함수가 「자르기만 한다」는 계약의 집행 지점이다. 여기를 통과하지 못한
      내용은 fixture 로 저장되지 않는다.
    ⛔ 보정·정규화·자르기(truncate) 를 하지 않는다 — 어긋나면 중단한다.
    """
    block = doc_text[start:end]
    if len(block.encode("utf-8")) > MAX_SLICE_BYTES:
        print(f"    ✗ 슬라이스가 상한을 넘는다 "
              f"({len(block.encode('utf-8'))}B > {MAX_SLICE_BYTES}B) — 자르지 않고 중단")
        return None
    if block not in doc_text:
        print("    ✗ 슬라이스가 원문의 부분 문자열이 아니다 — 재구성 의심 · 중단")
        return None
    if not balanced(block):
        print("    ✗ 슬라이스의 `<table>` 여닫이가 맞지 않는다 — 보정하지 않고 중단")
        return None
    return block


def sha(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def discovery_record(limit, default_limit, considered, selected, dropped):
    """discovery 증거를 그대로 남긴다. ⛔ dropped 를 자르지 않는다."""
    return {"limit": limit,
            "limit_source": "default" if limit == default_limit else "override",
            "limit_env": CAPTURE_LIMIT_ENV,
            "considered": len(considered),
            "selected": [{"filing_date": c["filing_date"],
                          "accession": c["accession"]} for c in selected],
            "dropped": [{"filing_date": c["filing_date"],
                         "accession": c["accession"]} for c in dropped]}


def capture_one(c, outdir, failures=None):
    """실패하면 None 을 돌려준다(계약 불변). `failures` 를 주면 **사유를 함께 남긴다** —
    ⛔ 실패가 「보존 0건」으로만 보이고 이유가 사라지는 것을 막기 위한 것이다."""
    acc = c["accession"].replace("-", "")
    print(f"\n  ── {c['filing_date']} · {c['accession']}")

    def _fail(reason):
        if failures is not None:
            failures.append({"filing_date": c["filing_date"],
                             "accession": c["accession"], "reason": reason})
        return None

    # ★ 취득은 승인된 경로 그대로 — full .txt → <DOCUMENT> → EX-99.1 → secondary
    time.sleep(POLITE_DELAY_SEC)
    _, raw_txt = get(f"{M.ARCHIVE_BASE}/{acc}/{c['accession']}.txt")
    docs = M.parse_document_blocks(raw_txt.decode("utf-8", errors="replace"))
    target, probs, chosen = M.select_exhibit(docs, sec_types=None)
    if target is None:
        M.log_candidates(docs, "; ".join(probs))
        return _fail("primary <TYPE> 식별 실패: " + "; ".join(probs))
    time.sleep(POLITE_DELAY_SEC)
    _, ihtml = get(f"{M.ARCHIVE_BASE}/{acc}/{c['accession']}-index.html")
    sec_types = M.index_html_types(ihtml.decode("utf-8", errors="replace"))
    target2, probs2, _ = M.select_exhibit(docs, sec_types=sec_types)
    if target2 is None:
        print(f"    ✗ {'; '.join(probs2)}")
        M.log_candidates(docs, "primary/secondary 교차확인 실패")
        return _fail("primary/secondary 교차확인 실패: " + "; ".join(probs2))
    print(f"    exhibit {target2}")

    time.sleep(POLITE_DELAY_SEC)
    _, body = get(f"{M.ARCHIVE_BASE}/{acc}/{target2}")
    doc_text = body.decode("utf-8", errors="replace")

    loc = locate_block(doc_text)
    if loc is None:
        print("    → fixture 를 만들지 않는다 (fail-closed)")
        return _fail("슬라이스 생성 실패 — 위 진단 참조")
    start, end, info = loc
    block = slice_and_verify(doc_text, start, end)
    if block is None:
        print("    → fixture 를 만들지 않는다 (fail-closed)")
        return _fail("슬라이스 생성 실패 — 위 진단 참조")

    name = f"{c['filing_date']}_{c['accession']}_azure_cc_table.html"
    path = os.path.join(outdir, name)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(block)

    rec = {"filing_date": c["filing_date"], "accession": c["accession"],
           "items": c["items"], "exhibit": target2,
           "exhibit_url": f"{M.ARCHIVE_BASE}/{acc}/{target2}",
           "exhibit_sha256": sha(doc_text), "exhibit_chars": len(doc_text),
           "slice_start": start, "slice_end": end, "slice_chars": len(block),
           "slice_sha256": sha(block), "fixture_file": name,
           "table_span": [info["table_start"], info["table_end"]],
           "verbatim_substring_of_exhibit": True}
    print(f"    ✓ {name}  {len(block)}자  [{start}:{end}]  sha {rec['slice_sha256'][:12]}")
    return rec


def main() -> int:
    print("=" * 74)
    print("RULE-0021 — Azure CC 표 원문 HTML fixture 보존")
    print("  ⛔ 원문을 잘라내기만 한다 — 재구성·정규화·값 재기록 없음")
    print("  ⛔ 저장소에 쓰지 않는다 (출력은 FIXTURE_OUT)")
    print("  ⛔ Rule 상태를 바꾸지 않는다 · 값을 판정하지 않는다")
    print("=" * 74)

    outdir = os.environ.get("FIXTURE_OUT") or tempfile.mkdtemp(prefix="atlas_fx_")
    # ★ 만들기 **전에** 막는다 — 거부하면서 흔적을 남기지 않는다.
    if os.path.abspath(outdir).startswith(os.path.abspath(ROOT) + os.sep):
        print(f"\n⛔ FIXTURE_OUT 이 저장소 안이다 ({outdir}) — 중단")
        return 1
    os.makedirs(outdir, exist_ok=True)
    print(f"\n출력 {outdir}")

    print("\n① Discovery — collector 와 동일 경로 (form=8-K AND items 2.02)")
    _, raw = get(M.SUBMISSIONS_URL)
    rec = json.loads(raw.decode("utf-8"))["filings"]["recent"]
    n = len(rec["form"])
    cands = []
    for i in range(n):
        if rec["form"][i] != "8-K":
            continue
        items = rec.get("items", [""] * n)[i] or ""
        if M.EARNINGS_ITEM not in items:
            continue
        cands.append({"accession": rec["accessionNumber"][i],
                      "filing_date": rec["filingDate"][i], "items": items})
    cands.sort(key=lambda c: c["filing_date"], reverse=True)
    limit, problem = capture_limit()
    if problem:
        print(f"\n⛔ {problem}")
        return 1
    dropped = cands[limit:]
    cands = cands[:limit]
    print(f"  후보 {len(cands)}건 (조회 상한 {limit}"
          + (f" · 기본 {M.MAX_FILINGS} 을 {CAPTURE_LIMIT_ENV} 로 덮었다"
             if limit != M.MAX_FILINGS else " · 기본값")
          + ")")
    if dropped:
        # ⛔ 조용히 자르지 않는다 — 무엇이 빠졌는지가 이번 run 의 판정 근거다
        print(f"  ⚠️ 상한으로 조회하지 않은 것 {len(dropped)}건: "
              + ", ".join(c["filing_date"] for c in dropped))

    print("\n② 보존")
    fails: list = []
    out = [r for r in (capture_one(c, outdir, fails) for c in cands) if r]

    man = os.path.join(outdir, "MANIFEST.json")
    with open(man, "w", encoding="utf-8") as f:
        json.dump({"captured": out, "attempted": len(cands),
                   "discovery": discovery_record(limit, M.MAX_FILINGS,
                                                 cands + dropped, cands, dropped),
                   "failures": fails,
                   "note": "각 fixture 는 exhibit 원문의 부분 문자열이다. "
                           "재구성·정규화하지 않았다. "
                           "⛔ discovery 의 dropped 와 failures 는 자르지 않는다."}, f,
                  ensure_ascii=False, indent=2)

    print("\n" + "=" * 74)
    print(f"③ 결과 — 시도 {len(cands)}건 · 보존 {len(out)}건")
    for r in out:
        print(f"  {r['filing_date']}  {r['slice_chars']:>7}자  {r['fixture_file']}")
    if len(out) < 2:
        print("\n⛔ fixture 2건 미만 — 구형·신형 대조가 불가능하다")
        return 1
    print("\n★ 이 fixture 는 원문 슬라이스다. 값 판정·계약 갱신은 여기서 하지 않는다.")
    print("⛔ RULE-0021 의 blocker 는 이 실행으로 해제되지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
