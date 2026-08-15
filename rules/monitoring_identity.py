"""Machine Rule Inventory — monitoring opaque identity assignment.

CIO 판정 2026-08-15 — §21-12 identity 선택지 ⓐ.
Inventory 에 포함되는 monitoring 객체에도 고유·안정 opaque identity 를 부여한다.

★ 이번 판정이 새로 도입한 것은 **prefix `MON` 하나뿐**이다.
  번호 폭 · 기존 occurrence 재사용 · 새 occurrence 에만 다음 번호 · 정렬 순서에 따른
  재발급 금지 · occurrence provenance 와 logical identity 분리는 전부
  `canonicalize.py` 의 기존 opaque identity 계약을 **그대로 호출**해 소비한다.

⛔ 이 파일이 하지 않는 것 — 전부 불변조건으로 강제한다
    MON identity ≠ evaluation_rule
    MON identity ≠ canonical evaluation rule (B3 의 semantic canonicalization 대상 아님)
    MON identity ≠ config/rules.json 편입
    MON identity ≠ Evaluator Population 편입
    MON identity ≠ evaluator eligibility
    MON identity ≠ executable

★ B3 scope 는 건드리지 않았다. `canonical_rules.json` 의
  「semantic canonicalization 은 FAL+ENT 대상」 계약은 그대로이며, 여기서 하는 것은
  Inventory 객체를 안정적으로 가리키기 위한 **주소 부여**뿐이다.

⛔ 과거 draft 의 `TSM-MON-01` · `SNDK-MON-01` 형식은 채택하지 않는다 —
   종목명과 kind 를 identity 에 encoding 하므로 현재 opaque-ID 계약과 맞지 않는다.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "rules"))
import canonicalize as K                                             # noqa: E402
import ssot_mapping as SM                                            # noqa: E402

MAPPING = os.path.join(ROOT, "rules", "ssot_mapping.json")
OUT = os.path.join(ROOT, "rules", "monitoring_identity.json")

ARTIFACT = "monitoring opaque identity (Machine Rule Inventory)"

# ★ CIO 판정 2026-08-15 — 이번에 새로 정해진 것은 이 문자열 하나다.
MONITORING_PREFIX = "MON"

# 이 identity 가 부여하지 않는 것. 값이 아니라 계약 문구로 남긴다.
NOT_GRANTED = [
    "evaluation_rule 편입",
    "canonical evaluation rule 생성 (B3 semantic canonicalization 대상 아님)",
    "config/rules.json 편입",
    "Evaluator Population 편입",
    "evaluator eligibility",
    "executable 자격",
]


def monitoring_occurrences(mapping: dict) -> list:
    """Inventory 계약상 monitoring 인 것만. 목적지 판정은 mapping 이 이미 했다."""
    return [m for m in mapping["mapping"]
            if m["destination"] == SM.DEST_INVENTORY_ONLY]


def assign(occurrences: list, existing: dict | None = None) -> tuple:
    """기존 ID 재사용 · 새 occurrence 만 다음 번호. 입력 순서와 무관하다.

    ★ 번호 부여는 `canonicalize._next_id` 를 그대로 호출한다 — 규칙을 복제하지 않는다.
    """
    prior = {}
    if existing:
        for r in existing.get("monitoring_identities", []):
            prior[r["occurrence_id"]] = r["monitoring_id"]

    used = set(prior.values())
    records, unchanged, newly = [], 0, 0
    for occ in occurrences:
        oid = occ["occurrence_id"]
        if oid in prior:
            mid = prior[oid]
            unchanged += 1
        else:
            mid = K._next_id(used, MONITORING_PREFIX)
            used.add(mid)
            newly += 1
        records.append({
            "monitoring_id": mid,
            "occurrence_id": oid,          # ★ provenance 주소. identity 와 분리한다.
            # 사람이 읽는 의미는 ID 밖에 둔다 — 전부 원본에서 기계적으로 온다.
            "subject": occ["candidate_id"].split("::", 1)[0],
            "rule_kind": occ["rule_kind"],
            "downstream_effect": occ["downstream_effect"],
            "raw_fragment": occ["raw_fragment"],
        })
    records.sort(key=lambda r: r["monitoring_id"])
    return records, {"unchanged": unchanged, "newly_assigned": newly}


def build(out_path=OUT, mapping_path=MAPPING):
    errs = []
    mapping = json.load(open(mapping_path, encoding="utf-8"))
    existing = None
    if os.path.exists(out_path):
        existing = json.load(open(out_path, encoding="utf-8"))

    occ = monitoring_occurrences(mapping)
    records, stat = assign(occ, existing)

    # ── 불변조건 ──────────────────────────────────────────────────────
    if len(records) != mapping["counts"]["rule_inventory_only"]:
        errs.append(f"monitoring identity {len(records)} ≠ mapping 이 집계한 "
                    f"{mapping['counts']['rule_inventory_only']}")

    ids = [r["monitoring_id"] for r in records]
    if len(set(ids)) != len(ids):
        errs.append("monitoring_id 가 중복됐다 — 1:1 이 아니다")
    occs = [r["occurrence_id"] for r in records]
    if len(set(occs)) != len(occs):
        errs.append("같은 occurrence 에 두 번 부여됐다")
    if set(occs) != {m["occurrence_id"] for m in occ}:
        errs.append("부여 대상이 monitoring 집합과 다르다")

    for r in records:
        if not r["monitoring_id"].startswith(MONITORING_PREFIX + "-"):
            errs.append(f"{r['monitoring_id']}: monitoring namespace 가 아니다")
        if r["monitoring_id"].startswith(K.EVALUATION_PREFIX + "-"):
            errs.append(f"{r['monitoring_id']}: evaluation namespace 와 충돌한다")
        if r["rule_kind"] != "MON":
            errs.append(f"{r['occurrence_id']}: MON 이 아닌 객체에 부여했다")
        if r["downstream_effect"] != "monitoring":
            errs.append(f"{r['occurrence_id']}: 효과가 monitoring 이 아니다")
        # ⛔ 의미를 ID 에 encoding 하지 않았는가 (draft 형식 재발 방지)
        if r["subject"] and r["subject"] in r["monitoring_id"]:
            errs.append(f"{r['monitoring_id']}: 종목명이 identity 에 들어갔다")
        for tok in ("FAL", "ENT"):
            if tok in r["monitoring_id"]:
                errs.append(f"{r['monitoring_id']}: kind 어휘가 identity 에 들어갔다")

    # 두 namespace 가 겹치지 않는가
    canon = json.load(open(K.OUT, encoding="utf-8"))
    rule_ids = {r["canonical_rule_id"] for r in canon["canonical_rules"]}
    if rule_ids & set(ids):
        errs.append(f"namespace 충돌: {sorted(rule_ids & set(ids))}")
    if canon["scope_note"] != ("evaluation_rules (FAL + ENT) 만 대상. MON · "
                              "execution_reference · non_rule_evidence · Portfolio "
                              "Operation 은 canonicalization 대상이 아니다."):
        errs.append("B3 scope_note 가 바뀌었다 — semantic canonicalization 대상을 "
                    "MON 으로 확대하지 않는다")

    # 안정성 — 순서를 뒤집어도 같은 결과여야 한다
    again, _ = assign(list(reversed(occ)), {"monitoring_identities": records})
    if {r["occurrence_id"]: r["monitoring_id"] for r in again} != \
            {r["occurrence_id"]: r["monitoring_id"] for r in records}:
        errs.append("정렬 순서에 따라 ID 가 재발급됐다")

    payload = {
        "artifact": ARTIFACT,
        "authority": False,
        "consumable_by_evaluator": False,
        "prefix": MONITORING_PREFIX,
        "id_format": f"{MONITORING_PREFIX}-{{n:0{K.ID_WIDTH}d}}",
        "id_policy": ("opaque stable ID. 의미를 ID 에 encoding 하지 않는다. 정렬 "
                      "순서로 재발급하지 않으며, 기존 occurrence 의 ID 를 재사용한다. "
                      "★ 번호 부여는 canonicalize 의 기존 계약을 그대로 호출한다."),
        "newly_introduced": f"prefix {MONITORING_PREFIX!r} 하나뿐",
        "purpose": "Machine Rule Inventory 객체의 식별과 추적",
        "not_granted": NOT_GRANTED,
        "scope_separation": ("B3 의 semantic canonicalization 대상은 FAL+ENT 그대로다. "
                             "이 파일은 Inventory identity 만 부여하며 두 scope 는 "
                             "별개다."),
        "no_merge_note": ("의미 유사성을 이유로 monitoring occurrence 를 병합하지 "
                          "않는다. occurrence 와 1:1 이다."),
        # ⛔ 실행 이력(`unchanged` / `newly_assigned`)은 payload 에 싣지 않는다 —
        #   CIO 판정 2026-08-15 ⓑ. canonical 의 `assignment` 와 같은 성격이며, 직전
        #   출력 파일의 존재 여부에 따라 값이 달라져 재빌드 byte-identity 를 깬다.
        #   ★ 진단용으로는 `build()` 의 세 번째 반환값으로 넘긴다.
        "counts": {"monitoring_identities": len(records)},
        "monitoring_identities": records,
    }

    if errs:
        return payload, errs, stat
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return payload, errs, stat


if __name__ == "__main__":
    p, e, stat = build()
    c = p["counts"]
    print(f"[monitoring identity] {OUT}")
    print(f"  {c['monitoring_identities']}건 · 형식 {p['id_format']}")
    print(f"  재사용 {stat['unchanged']} · 신규 {stat['newly_assigned']}"
          f"   ← 진단값 (산출물에 싣지 않는다)")
    print(f"  새로 도입한 것 — {p['newly_introduced']}")
    for x in e:
        print("  ⛔", x)
    print("  ✅ 위반 0" if not e else f"  ⛔ 위반 {len(e)}")
    sys.exit(1 if e else 0)
