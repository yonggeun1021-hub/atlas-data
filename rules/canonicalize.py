"""B3 — Canonical Identity 부여. **identity 부여만 한다.**

⛔ dedup / merge 금지 · duplicate 제거 금지 · threshold 수정 금지 ·
   UNDEFINED 정의 보완 금지 · source qualification 수정 금지 ·
   READY/BLOCKED 수정 금지 · downstream effect 정리 금지 ·
   MON / execution_reference canonicalization 금지 · Portfolio 객체 생성 금지.

★ 입력 SSOT
    rules/decompose_full.json           (B2 adjudicated)
    + population boundary  = object_role == "rule_candidate" AND rule_kind ∈ {FAL, ENT}
  ⛔ `populations.json` 은 비권위 view 이므로 입력으로 쓰지 않는다.
     특히 그 안의 duplicate 탐색 결과를 canonicalization 입력으로 쓰지 않는다.

★ 두 ID 를 분리한다
    occurrence_id      현재 provenance 위치       예) `MU::탈락 조건#3`   — 기존 그대로 보존
    canonical_rule_id  Rule 의 논리적 identity    예) `RULE-0001`        — opaque · stable

★ canonical record 는 source occurrence 를 복사·대체하지 않는다.
  방향은 한쪽뿐이다:  canonical rule → source_occurrences[]
  decomposition 원본에 canonical field 를 주입하지 않는다. 별도 artifact 를 만든다.

★ opaque ID 인 이유
  `TSM-FAL-398-BREAK` 처럼 현재 의미를 ID 에 박으면 threshold·문구·분류가 정정될 때
  identity 까지 바뀌어 provenance 가 깨진다. 영구 주소와 그 주소가 설명하는 속성을 분리한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))

DECOMP = os.path.join(ROOT, "rules", "decompose_full.json")
OUT = os.path.join(ROOT, "rules", "canonical_rules.json")

EVALUATION_KINDS = {"FAL", "ENT"}          # B2-1 에서 확정한 population boundary

UNRESOLVED = "UNRESOLVED"


def evaluation_occurrences(decomp: dict) -> list:
    """B2-1 population boundary 를 원본에서 직접 다시 계산한다."""
    out = []
    for c in decomp["cells"]:
        for f in c["fragments"]:
            if f.get("object_role") != "rule_candidate":
                continue
            if f.get("rule_kind") not in EVALUATION_KINDS:
                continue
            out.append({
                "occurrence_id": f"{c['candidate_id']}#{f['split_index']}",
                "candidate_id": c["candidate_id"],
                "source_cell": c["source_cell"],
                "split_index": f["split_index"],
                "rule_kind": f["rule_kind"],
                "raw_fragment": f["raw_fragment"],
            })
    return out


# ★ opaque identity 번호 부여 계약 — 이 함수가 그 계약의 단일 구현이다.
#   바깥에서 고르는 것은 prefix 하나뿐이다. 다른 namespace(예: Machine Rule Inventory
#   의 monitoring identity)도 같은 계약을 쓰되 **이 함수를 그대로 호출**한다.
#   ⛔ 번호 규칙을 복제하지 않는다 — 복제하면 두 갈래가 조용히 어긋난다.
#   ★ prefix 를 나눈다는 것은 namespace 를 나눈다는 뜻이지, B3 의 semantic
#     canonicalization 대상(FAL+ENT)이 넓어진다는 뜻이 아니다.
ID_WIDTH = 4
EVALUATION_PREFIX = "RULE"


def _next_id(used: set, prefix: str = EVALUATION_PREFIX) -> str:
    n = 1
    while f"{prefix}-{n:0{ID_WIDTH}d}" in used:
        n += 1
    return f"{prefix}-{n:0{ID_WIDTH}d}"


def assign(occurrences: list, existing: dict | None = None) -> tuple:
    """★ ID 는 정렬 순서에 의존해 재발급하지 않는다.

    기존 mapping 을 읽어 알려진 occurrence 의 ID 를 **재사용**하고,
    새 occurrence 만 다음 번호를 받는다. 입력 순서가 바뀌어도 기존 ID 는 불변이다.
    """
    prior = {}
    if existing:
        for r in existing.get("canonical_rules", []):
            for occ in r["source_occurrences"]:
                prior[occ] = r["canonical_rule_id"]

    used = set(prior.values())
    records, unchanged, newly = [], 0, 0
    for occ in occurrences:
        oid = occ["occurrence_id"]
        if oid in prior:
            cid = prior[oid]
            unchanged += 1
        else:
            cid = _next_id(used)
            used.add(cid)
            newly += 1
        records.append({
            "canonical_rule_id": cid,

            # ── 사람이 읽는 의미는 ID 밖에 둔다 ──
            #    subject / rule_kind 는 원본에서 기계적으로 파생된다(판단 없음).
            "subject": occ["candidate_id"].split("::", 1)[0],
            "rule_kind": occ["rule_kind"],
            "condition_text": occ["raw_fragment"],          # ★ 원문 그대로. 재작성 없음.
            "condition_text_sha256": hashlib.sha256(
                occ["raw_fragment"].encode("utf-8")).hexdigest(),

            # ── ⛔ B3 에서 채우지 않는다 ──
            #   CIO 가 제시한 identity tuple 은
            #     subject + rule_kind + condition_semantics + scope
            #   인데 아래 둘은 채우려면 정본에 없는 어휘를 만들거나,
            #   "두 조건이 같은가" 라는 B4 의 equivalence 판정을 미리 해야 한다.
            #   B3 에서 허용된 것은 identity 부여뿐이므로 UNRESOLVED 로 남긴다.
            "condition_semantics": UNRESOLVED,
            "scope": UNRESOLVED,

            # ── canonical → source 단방향 참조 ──
            "source_occurrences": [oid],
        })
    return records, {"reused": unchanged, "new": newly}


def build(decomp_path: str = DECOMP, out_path: str = OUT) -> tuple:
    """returns (payload, stat). ★ stat 은 진단값이며 산출물에 실리지 않는다."""
    with open(decomp_path, encoding="utf-8") as f:
        decomp = json.load(f)
    occ = evaluation_occurrences(decomp)

    existing = None
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            existing = json.load(f)

    records, stat = assign(occ, existing)

    payload = {
        "artifact": "B3 Canonical Identity",
        "status": "inactive preparation",
        "authority": False,
        "consumable_by_evaluator": False,
        "scope_note": ("evaluation_rules (FAL + ENT) 만 대상. "
                       "MON · execution_reference · non_rule_evidence · "
                       "Portfolio Operation 은 canonicalization 대상이 아니다."),
        "dedup_status": ("미착수 — canonicalization ≠ deduplication. "
                         "25 occurrence → 25 canonical record 로 시작한다. "
                         "문자열이 같아도 같은 Rule 로 확정하지 않는다 "
                         "(예: MU / 000660.KS 의 'DRAM ASP 하락 전환'). "
                         "equivalence 기준은 B4 에서 CIO 가 판정한다."),
        "id_policy": ("opaque stable ID. 의미를 ID 에 encoding 하지 않는다. "
                      "정렬 순서로 재발급하지 않으며, 기존 mapping 의 ID 를 재사용한다."),
        "input_ssot": {
            "decomposition": "rules/decompose_full.json (B2 adjudicated)",
            "population_boundary": "object_role == rule_candidate AND rule_kind in {FAL, ENT}",
            "excluded_input": ("rules/populations.json — 비권위 view 이므로 입력으로 쓰지 않는다. "
                               "duplicate 탐색 결과도 canonicalization 입력이 아니다."),
        },
        # ⛔ 실행 이력(`assignment`)은 여기 넣지 않는다 — CIO 판정 2026-08-15 ⓑ.
        #   ID 를 새로 발급했는지 재사용했는지는 **직전 출력 파일이 있었는가**에 따라
        #   달라진다. 그것을 authoritative payload 에 실으면 같은 입력인데도 바이트가
        #   달라져 clean-checkout 재빌드 byte-identity 가 성립하지 않는다.
        #   ★ 진단용으로는 `build()` 의 두 번째 반환값으로 그대로 넘긴다.
        "canonical_rules": records,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, stat


def main() -> None:
    p, stat = build()
    print(f"[B3] {OUT}")
    print(f"  canonical records : {len(p['canonical_rules'])}")
    print(f"  ID 재사용 {stat['reused']} · 신규 {stat['new']}   ← 진단값 (산출물에 싣지 않는다)")
    print(f"  dedup            : 미착수")


if __name__ == "__main__":
    main()
