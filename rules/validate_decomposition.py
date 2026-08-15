"""B1 분해 불변식 검증기 — 분해 결과가 지켜야 하는 구조를 기계가 검사한다.

분해 자체는 사람이 한다(CIO 지시). 이 파일은 사람이 만든 분해가
금지된 모양을 갖지 않았는지만 본다.

검사하는 불변식 5종
  I-1 무손실     모든 조각은 원문의 부분문자열이고, 덮지 못한 구간은 명시된다
  I-2 층 분리    execution_reference 와 daily_eligibility 가 한 조각에 섞이지 않는다
  I-3 fail-closed  UNDEFINED 는 executable 로 승격되지 않는다
  I-4 어휘 폐쇄  정본/draft 밖의 토큰이 들어오지 않는다
  I-5 파생 강제  evaluator_status 는 입력이 아니라 파생이다
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vocabulary import (VOCAB, UNRESOLVED, derive_evaluator_status,   # noqa: E402
                        derive_blocked_by, CONNECTIVES, PUNCTUATION,
                        IGNORABLE_CHARS)


class DecompositionViolation(RuntimeError):
    """분해가 불변식을 깼다. 이 경우 다음 단계 입력으로 쓰지 않는다."""


# ★ 조각이 가질 수 있는 필드 — 폐쇄 집합.
#   여기 없는 이름으로 값을 넣는 것은 스키마를 현장에서 늘리는 것이다.
ALLOWED_FIELDS = {
    "split_index", "raw_fragment",
    "object_role", "rule_kind", "downstream_effect",
    "definition_status", "data_status", "data_capability", "source_qualification",
    "annotates_split_index",     # non_rule_evidence 가 무엇을 주석하는가
    "effect_evidence",           # 참조 칸을 Rule 로 올릴 때 원문의 어느 말이 근거인가
    "notes",
}

# CIO 판정 2026-08-15 — 이 칸들의 기본 분류는 execution_reference 다
REFERENCE_CELLS = {"핵심 지지", "핵심 저항"}

# CIO 판정 2026-08-15 #2 — Rule Evaluator 의 최종 downstream effect 로 승인하지 않는다.
# migration 중 legacy/provisional 표현으로만 보존하며 executable semantics 로 쓰지 않는다.
# Rule 은 조건 충족 사실까지만 산출하고, 실제 Stage 변경은 Decision Layer 소관이다.
LEGACY_PROVISIONAL_EFFECTS = {"강등 검토"}

# ★★ semantic unresolved gate (CIO 지시 B2-1)
#    "UNRESOLVED object = 0" 만으로는 부족하다 — object_role 은 정해졌는데
#    rule_kind 가 미정인 조각이 조용히 남을 수 있었다(TSM::기술적 무효화#1 사례).
#    canonical ID·dedup 으로 넘어가기 전에 **종류가 정해지지 않은 occurrence 가
#    정규화되는 것**을 막는 게이트다.
#    ⛔ definition_status / data_status / data_capability 의 UNRESOLVED 와 섞지 않는다.
#       그것들은 semantic classification 과 **별도 dimension** 이다.
SEMANTIC_KINDS = {"FAL", "ENT", "MON"}


def _raw_texts(candidates_path: str, extra: dict | None = None) -> dict:
    with open(candidates_path, encoding="utf-8") as f:
        p = json.load(f)
    out = {c["candidate_id"]: c["raw_text"] for c in p["candidates"]}
    out.update(extra or {})
    return out


def coverage(raw: str, fragments: list) -> dict:
    """조각들이 원문의 어디를 덮었는가. 겹침·누락·순서를 전부 낸다."""
    spans, problems, repeated = [], [], []
    cursor = 0
    for fr in fragments:
        frag = fr["raw_fragment"]
        n = raw.count(frag)
        if n == 0:
            problems.append(f"split {fr['split_index']}: 원문에 없는 조각")
            continue
        # 반복 등장 자체는 위반이 아니다 — 배치는 커서로 결정되고,
        # 겹침·순서·전구간 피복 검사가 배치의 정확성을 이미 보증한다.
        # (결합 표기 '/'·'→' 는 한 칸에 여러 번 나오는 것이 정상이다)
        if n > 1:
            repeated.append(fr["split_index"])
        i = raw.find(frag, cursor)
        if i < 0:                       # 순서가 원문과 다르다
            i = raw.find(frag)
            problems.append(f"split {fr['split_index']}: 원문 등장 순서와 어긋난다")
        spans.append((i, i + len(frag)))
        cursor = i + len(frag)

    covered = [False] * len(raw)
    for a, b in spans:
        for k in range(a, b):
            if covered[k]:
                problems.append("조각끼리 겹친다")
            covered[k] = True

    gaps = []
    for m in re.finditer(r"(?s).", raw):
        pass
    i = 0
    while i < len(raw):
        if not covered[i]:
            j = i
            while j < len(raw) and not covered[j]:
                j += 1
            gaps.append(raw[i:j])
            i = j
        else:
            i += 1

    # ★ CIO 검수 2026-08-15 ③ — 무시할 수 있는 것은 공백과 문장 부호뿐이다.
    #   결합 표기(또는·/·→·+··)는 builder 가 보존 대상으로 선언했으므로
    #   여기서 무의미 구분자로 지워버리면 두 모듈의 철학이 어긋난다.
    #   결합 표기가 객체로 보존되지 않으면 이 검사에서 I-1 위반이 나야 한다.
    meaningful = [g for g in gaps if g.strip(IGNORABLE_CHARS)]
    return {
        "covered_chars": sum(covered),
        "total_chars": len(raw),
        "ratio": round(sum(covered) / len(raw), 4) if raw else 0.0,
        "uncovered_spans": gaps,
        "uncovered_meaningful": meaningful,
        "problems": problems,
        "repeated_fragments": repeated,
    }


def validate(pilot: dict, raws: dict) -> dict:
    v: list = []
    report = {"cells": [], "derived": []}

    for cell in pilot["cells"]:
        cid = cell["candidate_id"]
        raw = raws.get(cid)
        if raw is None:
            v.append(f"{cid}: 원문을 찾을 수 없다 — 분해 대상이 실재하는지 확인 불가")
            continue

        frs = cell["fragments"]

        # ── I-1 무손실 ──────────────────────────────────────────────
        cov = coverage(raw, frs)
        for p in cov["problems"]:
            v.append(f"{cid}: {p}")
        scope = cell.get("decomposition_scope")
        if scope not in ("full", "partial"):
            v.append(f"{cid}: decomposition_scope 는 full|partial 이어야 한다")
        if cov["uncovered_meaningful"] and scope != "partial":
            v.append(f"{cid}: 의미 있는 미분해 구간이 있는데 scope=full 이다 "
                     f"→ {cov['uncovered_meaningful'][:2]}")
        if scope == "partial" and not cell.get("partial_reason"):
            v.append(f"{cid}: partial 인데 사유가 없다 — 조용한 누락 금지")

        # split_index 연속성
        idx = [f["split_index"] for f in frs]
        if idx != list(range(1, len(frs) + 1)):
            v.append(f"{cid}: split_index 가 1..n 연속이 아니다 → {idx}")

        by_idx = {f["split_index"]: f for f in frs}

        for fr in frs:
            tag = f"{cid}#{fr['split_index']}"

            # ── I-4b 필드 폐쇄 ──────────────────────────────────────
            # ★ 승인된 필드 밖의 키는 전부 위반이다. threshold·definition·enum 같은
            #   이름으로 정의를 현장에서 만들어 넣는 경로를 구조로 막는다.
            extra_fields = set(fr) - ALLOWED_FIELDS
            if extra_fields:
                v.append(f"{tag}: ★★ 승인 밖 필드 {sorted(extra_fields)} — "
                         f"현장에서 스키마/정의를 만들었다")

            # ── I-4 어휘 폐쇄 ────────────────────────────────────────
            for field, allowed in VOCAB.items():
                if field == "evaluator_status":
                    continue                     # 파생값 — 입력에 있으면 안 된다
                if field in fr and fr[field] not in allowed:
                    v.append(f"{tag}: {field}={fr[field]!r} 는 허용 어휘 밖이다")

            # ── I-5 파생 강제 ────────────────────────────────────────
            if "evaluator_status" in fr:
                v.append(f"{tag}: evaluator_status 를 직접 적었다 — 파생값이다")
            if "blocked_by" in fr:
                v.append(f"{tag}: blocked_by 를 직접 적었다 — 파생값이다")

            role = fr.get("object_role")
            eff = fr.get("downstream_effect")
            kind = fr.get("rule_kind")
            dfn = fr.get("definition_status")
            dat = fr.get("data_status")

            # ── I-2 층 분리 ──────────────────────────────────────────
            if role == "execution_reference" and eff != "execution_reference":
                v.append(f"{tag}: execution_reference 인데 effect 가 {eff!r} 다")
            if role == "execution_reference" and kind in ("FAL", "ENT", "MON"):
                v.append(f"{tag}: ★ execution_reference 에 rule_kind={kind} 를 붙였다 "
                         f"— 참조값을 Rule 로 승격했다")
            if eff == "execution_reference" and role == "rule_candidate":
                v.append(f"{tag}: ★ execution_reference 효과를 rule_candidate 로 분류했다 "
                         f"— §21-12(4) 층 배분 위반")
            if role == "non_rule_evidence" and (kind is not None or eff is not None):
                v.append(f"{tag}: non_rule_evidence 인데 rule_kind/effect 가 채워져 있다")
            if role == "non_rule_evidence" and "annotates_split_index" not in fr:
                v.append(f"{tag}: non_rule_evidence 는 무엇을 주석하는지 밝혀야 한다 "
                         f"(대상 없으면 빈 배열)")

            # ── I-2b 참조 칸의 기본값 ────────────────────────────────
            # CIO 판정 2026-08-15 — 핵심 지지/저항의 기본은 execution_reference 다.
            # "예외적으로 원문 자체가 판정 효과를 명시할 때만" 밖으로 나갈 수 있으므로,
            # 나가려면 원문의 어느 말이 그 효과인지 지목해야 한다.
            if (cell["source_cell"] in REFERENCE_CELLS
                    and role != "non_rule_evidence"
                    and eff != "execution_reference"):
                # ★★ CIO 검수 2026-08-15 ② — effect_evidence 의 substring 검사는
                #   구조적으로 너무 약하다. "SMA20" 같은 지표명만 넣어도 통과했다.
                #   지표명은 참조값이지 허가/차단 효과의 근거가 아니다.
                #   그렇다고 검증기가 '효과란 무엇인가'를 판별하는 규칙을 만들면
                #   그건 검증기가 의미론적 권위가 되는 것이라 금지된다.
                #   → 이번 B1 에서는 reference cell 의 Rule 승격을 **0건으로 유지**한다.
                #     실제 사례가 나오면 그때 CIO 판정으로 일반화한다.
                v.append(f"{tag}: ★★ {cell['source_cell']} 조각을 {eff!r} 로 올렸다 "
                         f"— 이번 B1 에서 reference cell 의 Rule 승격은 허용하지 않는다. "
                         f"실제 사례라면 CIO 판정을 요청하라 "
                         f"(effect_evidence 만으로는 효과를 증명하지 못한다)")

            # ── I-2c legacy 토큰 ────────────────────────────────────
            if eff in LEGACY_PROVISIONAL_EFFECTS and role == "execution_reference":
                v.append(f"{tag}: legacy 토큰 {eff!r} 를 execution_reference 에 붙였다")

            # ── I-3 fail-closed ─────────────────────────────────────
            if dfn == "UNDEFINED" and role == "execution_reference":
                v.append(f"{tag}: ★ UNDEFINED 를 execution_reference 로 우회시켰다")

            # ★ 원문이 스스로 Undefined 를 선언한 조각은 DEFINED 로 못 올린다
            for other in frs:
                if other.get("object_role") != "non_rule_evidence":
                    continue
                if fr["split_index"] not in (other.get("annotates_split_index") or []):
                    continue
                txt = (other.get("raw_fragment", "") + " " + other.get("notes", ""))
                if ("Undefined" in txt or "미정의" in txt) and dfn != "UNDEFINED":
                    v.append(f"{tag}: ★★ 원문 주석이 Undefined 를 명시하는데 "
                             f"definition_status={dfn} 다 — 현장에서 정의를 만들었다 "
                             f"(근거: #{other['split_index']})")

            # 파생 계산 (입력이 아니라 결과다)
            ev = derive_evaluator_status(dfn, dat) if dfn and dat else UNRESOLVED
            bb = derive_blocked_by(dfn, dat, fr.get("source_qualification"))
            if dfn == "UNDEFINED" and ev == "READY":
                v.append(f"{tag}: ★★ UNDEFINED 인데 READY 로 파생됐다 — 파생식이 깨졌다")
            if dfn == "UNDEFINED" and "DEFINITION_UNDEFINED" not in bb:
                v.append(f"{tag}: UNDEFINED 인데 차단 원인에 남지 않았다")
            if ev == "READY" and bb:
                v.append(f"{tag}: ★★ READY 인데 blocked_by 가 비어 있지 않다 → {bb}")

            report["derived"].append({
                "id": tag, "object_role": role, "rule_kind": kind,
                "downstream_effect": eff,
                "definition_status": dfn, "data_status": dat,
                "evaluator_status": ev, "blocked_by": bb,
            })

        report["cells"].append({
            "candidate_id": cid, "scope": scope,
            "fragments": len(frs),
            "coverage": cov["ratio"],
            "uncovered_meaningful": len(cov["uncovered_meaningful"]),
        })

    # ── semantic unresolved gate — 구조 invariant 와 채널을 분리해 보고한다 ──
    semantic = []
    for cell in pilot["cells"]:
        for fr in cell["fragments"]:
            if fr.get("object_role") != "rule_candidate":
                continue
            if fr.get("rule_kind") not in SEMANTIC_KINDS:
                semantic.append(
                    f"{cell['candidate_id']}#{fr['split_index']}: rule_candidate 인데 "
                    f"rule_kind={fr.get('rule_kind')!r} 가 {sorted(SEMANTIC_KINDS)} 밖이다 "
                    f"— 종류가 정해지지 않은 occurrence 는 canonicalization 대상이 될 수 없다")
    report["semantic_unresolved"] = semantic
    report["semantic_gate_pass"] = not semantic

    report["violations"] = v
    report["valid"] = not v
    return report


def main() -> None:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "rules", "decompose_pilot.json"), encoding="utf-8") as f:
        pilot = json.load(f)
    with open(os.path.join(root, "_watchlist_rows.json"), encoding="utf-8") as f:
        rows = json.load(f)["results"]
    extra = {f"{r['티커']}::편입 사유": r["편입 사유"] for r in rows if r.get("편입 사유")}
    raws = _raw_texts(os.path.join(root, "config", "rules.candidates.json"), extra)

    rep = validate(pilot, raws)

    print("B1 reviewed decomposition pilot — 불변식 검증\n")
    print(f"{'셀':28s} {'scope':8s} {'조각':>4s} {'커버리지':>9s} {'미분해(유의미)':>14s}")
    for c in rep["cells"]:
        print(f"{c['candidate_id']:28s} {c['scope']:8s} {c['fragments']:>4d} "
              f"{c['coverage']*100:>8.1f}% {c['uncovered_meaningful']:>14d}")

    print("\n파생 결과 (evaluator_status 는 계산된 값이다)")
    for d in rep["derived"]:
        if d["object_role"] == "non_rule_evidence":
            continue
        print(f"  {d['id']:34s} {str(d['rule_kind']):10s} {str(d['downstream_effect']):20s} "
              f"{d['evaluator_status']:10s} {d['blocked_by']}")

    ready = [d for d in rep["derived"] if d["evaluator_status"] == "READY"]
    print(f"\n  READY {len(ready)}건 " + (f"→ {[d['id'] for d in ready]}" if ready else ""))
    print("  ⛔ 이 숫자는 Rule Inventory 가 아니다 — pilot 6칸의 파생 결과일 뿐이다")

    print(f"\nsemantic unresolved gate : "
          f"{'PASS' if rep['semantic_gate_pass'] else 'FAIL'} "
          f"({len(rep['semantic_unresolved'])}건)")
    for x in rep["semantic_unresolved"]:
        print(f"  ✗ {x}")

    if rep["violations"] or not rep["semantic_gate_pass"]:
        print(f"\n★ 불변식 위반 {len(rep['violations'])}건")
        for x in rep["violations"]:
            print(f"  ✗ {x}")
        sys.exit(1)
    print("\n✅ 불변식 위반 0건")


if __name__ == "__main__":
    main()
