#!/usr/bin/env python3
"""Observation Layer 층 ④ — Observation Store.

★ 이 층의 유일한 책임: **economic-period series 를 보존하는 것.**
   ⛔ 「최신값을 고르는 것」이 아니다 (CIO 판정 §9-B).
   Store 는 역사를 보존하고, 어느 관측이 authority 인지는 **판정하지 않는다.**

⛔ 이 층이 하지 않는 것 — 다른 층의 책임이다
   ⛔ 취득 · 행/열 지목 (층 ① ②)      ⛔ 표기법 해석 (층 ③)
   ⛔ pair 구성 · 비교가능성 (층 ⑤)   ⛔ 임계값 판정 (층 ⑥)
   ⛔ Git · workflow · commit — **모른다.** 순수 결정 모듈이다.
      파일 IO 는 `load_state` / `save_state` 경계에만 있고, 그 경계도 Git 을 모른다.

★ 핵심 계약
   key            subject + measurement_identity + economic_period_end  (세 축뿐)
   ⛔ key 금지    run date · accession · filing_date · source sha · frame
   입력          serialized/deserialized **완성** record. 첫 동작은 `validate_record()`.
   D-6 경계      economic_period_end < COMMERCIAL_RPO_SERIES_START → 거부
   동일 key      IDEMPOTENT / CONFLICT / REVISION 세 갈래로만 갈린다
   ⛔ 조용한 overwrite 없음 · ⛔ 기존 revision 삭제 없음 · ⛔ authority 자동 선택 없음
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from record import RecordInvariantError, validate_record             # noqa: E402

STORE_SCHEMA_VERSION = "observation_store/1"

# ── D-6 경계 (CIO 판정 2026-08-16) ────────────────────────────────────
#   ★ `Commercial remaining performance obligation` 행이 8-K 표에 **처음 등장한** 분기.
#     그 이전에는 행이 존재하지 않았고, D-6 은 그것을 관측 결과로 확정했다.
#   ⛔ 이 상수는 임의 조정 대상이 아니다 — 바꾸려면 D-6 재판정이 필요하다.
COMMERCIAL_RPO_SERIES_START = "2025-09-30"

# ── 결과 코드 ─────────────────────────────────────────────────────────
NEW = "NEW"
IDEMPOTENT = "IDEMPOTENT"
CONFLICT = "CONFLICT"
REVISION = "REVISION"
REJECTED_INVALID_RECORD = "REJECTED_INVALID_RECORD"
REJECTED_PRE_SERIES = "PRE_SERIES_BACKFILL_FORBIDDEN"
OUTCOMES = (NEW, IDEMPOTENT, CONFLICT, REVISION,
            REJECTED_INVALID_RECORD, REJECTED_PRE_SERIES)
ACCEPTED_OUTCOMES = (NEW, IDEMPOTENT, CONFLICT, REVISION)
REJECTED_OUTCOMES = (REJECTED_INVALID_RECORD, REJECTED_PRE_SERIES)

# ── series 소비 차단 사유 ─────────────────────────────────────────────
#   ★ 이후 층 ⑤ Pair Validation 이 `COMPARABILITY_UNRESOLVED` 의 reason code 로 쓴다.
#   ⛔ Store 가 runtime 상태를 만들지 않는다 — 차단 사유만 노출한다.
REVISION_AUTHORITY_UNRESOLVED = "REVISION_AUTHORITY_UNRESOLVED"
OBSERVATION_CONFLICT_UNRESOLVED = "OBSERVATION_CONFLICT_UNRESOLVED"

# ── material provenance — 「같은 출처인가」의 정의 ─────────────────────
#   ★ 모호하게 두지 않는다. 아래 축만 비교한다.
#   ⛔ 재실행마다 달라질 수 있는 operational metadata(관측 시각 · run id 등)는
#      절대 넣지 않는다 — 넣으면 재관측이 매번 REVISION 으로 오분류된다.
MATERIAL_PROVENANCE_FIELDS = (
    "accession",
    "filing_date",
    "exhibit_type",
    "exhibit_document",
    "source_sha256",
    "slice_sha256",           # 있으면 비교, 없으면 양쪽 다 없어야 같다
)
MATERIAL_RECORD_FIELDS = (
    "row_label_raw",
    "period_end_raw",
    "decision_column_identity",
)


class StoreError(ValueError):
    """store 계약 위반."""


# ══════════════════════════════════════════════════════════════════════
# key · provenance
# ══════════════════════════════════════════════════════════════════════
def observation_key(record: dict) -> tuple:
    """(subject, measurement_identity, economic_period_end) — 이 셋뿐이다.

    ⛔ accession · filing_date · source sha 는 key 가 아니라 provenance 다.
    ⛔ run date 를 넣지 않는다.
    """
    for k in ("subject", "measurement_identity", "economic_period_end"):
        v = record.get(k)
        if not v or not isinstance(v, str):
            raise StoreError(f"key 축이 없다: {k}")
    return (record["subject"], record["measurement_identity"],
            record["economic_period_end"])


def key_str(key: tuple) -> str:
    """직렬화용 key 문자열. ⛔ 구분자가 값에 나타나면 거부한다 (충돌 방지)."""
    if len(key) != 3:
        raise StoreError(f"key 축이 3개가 아니다: {len(key)}")
    for part in key:
        if "|" in part:
            raise StoreError(f"key 축에 구분자 `|` 가 들어 있다: {part!r}")
    return "|".join(key)


def material_provenance(record: dict) -> dict:
    """「같은 출처인가」를 판정하는 축만 뽑는다.

    ⛔ operational metadata 를 넣지 않는다 — 재관측이 REVISION 으로 오분류된다.
    """
    prov = record.get("provenance") or {}
    ident = prov.get("exhibit_identity") or {}
    dec = record.get("decision") or {}
    flat = {
        "accession": prov.get("accession"),
        "filing_date": prov.get("filing_date"),
        "exhibit_type": ident.get("type"),
        "exhibit_document": ident.get("document"),
        "source_sha256": prov.get("source_sha256"),
        "slice_sha256": prov.get("slice_sha256"),
        "row_label_raw": record.get("row_label_raw"),
        "period_end_raw": record.get("period_end_raw"),
        "decision_column_identity": dec.get("column_identity"),
    }
    return {k: flat[k] for k in MATERIAL_PROVENANCE_FIELDS + MATERIAL_RECORD_FIELDS}


def observation_content(record: dict) -> dict:
    """「같은 관측인가」를 판정하는 값 축. raw 와 normalized 를 모두 본다."""
    dec = record.get("decision") or {}
    return {
        "decision": {
            "column_key": dec.get("column_key"),
            "raw_value": dec.get("raw_value"),
            "numeric_value": dec.get("numeric_value"),
            "unit": dec.get("unit"),
            "sign_convention": dec.get("sign_convention"),
        },
        "evidence_columns": [
            {"column_key": e.get("column_key"), "raw_value": e.get("raw_value"),
             "numeric_value": e.get("numeric_value"), "unit": e.get("unit")}
            for e in (record.get("evidence_columns") or [])
        ],
    }


def canonical_json(obj) -> str:
    """deterministic serialization — 키 정렬 · 고정 구분자 · 비ASCII 보존."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ══════════════════════════════════════════════════════════════════════
# state
# ══════════════════════════════════════════════════════════════════════
def empty_state() -> dict:
    return {"schema_version": STORE_SCHEMA_VERSION, "series": {}, "rejections": []}


def _revision_sort_key(rev: dict) -> tuple:
    """정렬은 **표시 순서**일 뿐 authority 가 아니다.

    ★ 삽입 순서와 무관하게 같은 state 가 나오도록 고정한다 (determinism).
    ⛔ 이 순서로 「최신」을 고르지 않는다 — 자동 선택은 존재하지 않는다.
    """
    p = rev.get("material_provenance") or {}
    return (p.get("filing_date") or "", p.get("accession") or "",
            p.get("source_sha256") or "")


def series_blocked_by(entry: dict) -> list:
    """이 key 를 지금 소비할 수 없게 만드는 사유 목록.

    ⛔ `active_revision` 같은 필드를 만들지 않는다 — 자동 authority 선택 금지.
    ★ revision 이 2건 이상이면 authority 가 확정되기 전까지 소비 불가다.
    """
    out = []
    if entry.get("conflicts"):
        out.append(OBSERVATION_CONFLICT_UNRESOLVED)
    if len(entry.get("revisions") or []) > 1:
        out.append(REVISION_AUTHORITY_UNRESOLVED)
    return out


def series_consumable(entry: dict) -> bool:
    """소비 가능 여부 — 차단 사유가 하나도 없고 revision 이 정확히 1건일 때만."""
    return not series_blocked_by(entry) and len(entry.get("revisions") or []) == 1


def _entry_view(entry: dict) -> dict:
    """저장되는 series entry 를 파생 필드까지 채워 정규형으로 만든다."""
    e = dict(entry)
    e["revisions"] = sorted(entry.get("revisions") or [], key=_revision_sort_key)
    e["conflicts"] = list(entry.get("conflicts") or [])
    e["blocked_by"] = series_blocked_by(e)
    e["consumable"] = series_consumable(e)
    return e


# ══════════════════════════════════════════════════════════════════════
# apply — 순수 함수. `state` 를 변형하지 않는다.
# ══════════════════════════════════════════════════════════════════════
def apply_record(state: dict, record: dict) -> tuple:
    """(new_state, result). ⛔ 입력 state 를 절대 변형하지 않는다.

    result = {outcome, key, reason, evidence, blocked_by, consumable}
    """
    if not isinstance(state, dict) or state.get("schema_version") != STORE_SCHEMA_VERSION:
        raise StoreError(f"store state schema 가 다르다: {(state or {}).get('schema_version')!r}")

    # ── ① 첫 동작은 반드시 record 검증이다 ────────────────────────────
    #    ⛔ 유효하지 않은 record 는 저장 판단까지 들어가지 않는다.
    try:
        validate_record(record)
    except RecordInvariantError as e:
        return state, _reject(REJECTED_INVALID_RECORD, None,
                              f"{type(e).__name__}: {e}", {"record_digest": _safe_digest(record)})

    key = observation_key(record)
    ks = key_str(key)

    # ── ② D-6 경계 ───────────────────────────────────────────────────
    if record["economic_period_end"] < COMMERCIAL_RPO_SERIES_START:
        return state, _reject(
            REJECTED_PRE_SERIES, key,
            f"economic_period_end {record['economic_period_end']} < "
            f"series start {COMMERCIAL_RPO_SERIES_START} — D-6 경계 위반",
            {"attempted_key": ks,
             "economic_period_end": record["economic_period_end"],
             "series_start": COMMERCIAL_RPO_SERIES_START,
             "material_provenance": material_provenance(record),
             "record_digest": digest(record)})

    mp, content = material_provenance(record), observation_content(record)
    new_rev = {"record": copy.deepcopy(record),
               "material_provenance": mp,
               "material_provenance_digest": digest(mp),
               "content_digest": digest(content)}

    new_state = copy.deepcopy(state)
    entry = new_state["series"].get(ks)

    # ── ③ 신규 key ───────────────────────────────────────────────────
    if entry is None:
        entry = {"key": {"subject": key[0], "measurement_identity": key[1],
                         "economic_period_end": key[2]},
                 "revisions": [new_rev], "conflicts": []}
        new_state["series"][ks] = _entry_view(entry)
        return new_state, _accept(NEW, key, "신규 economic period", new_state["series"][ks])

    # ── ④ 동일 provenance 인 revision 이 있는가 ──────────────────────
    same_prov = [r for r in entry["revisions"]
                 if r["material_provenance_digest"] == new_rev["material_provenance_digest"]]
    if same_prov:
        if all(r["content_digest"] == new_rev["content_digest"] for r in same_prov):
            # IDEMPOTENT — series 변화 없음. ⛔ 재관측으로 revision 을 만들지 않는다.
            return state, _accept(IDEMPOTENT, key,
                                  "동일 provenance · 동일 관측 — series 변화 없음",
                                  _entry_view(entry))
        # CONFLICT — 같은 원문에서 다른 값이 나왔다. 파서/결정성 결함 의심.
        # ⛔ 기존값을 덮지 않는다. 두 값을 함께 보존한다.
        conflict = {
            "material_provenance_digest": new_rev["material_provenance_digest"],
            "material_provenance": mp,
            "existing": [{"content": observation_content(r["record"]),
                          "content_digest": r["content_digest"]} for r in same_prov],
            "incoming": {"content": content, "content_digest": new_rev["content_digest"]},
            "reason": "동일 provenance 인데 관측 내용이 다르다 — parser/determinism 결함 의심",
        }
        # ★ conflict evidence 도 idempotent 여야 한다 (CIO 판정 S3.1 ·
        #   `REPEATED_CONFLICT_NOT_IDEMPOTENT`).
        #   같은 key · 같은 material provenance · 같은 incoming content 가 이미
        #   evidence 에 있으면 **같은 미해소 충돌을 다시 관측한 것**이지 새 충돌이 아니다.
        #   ⛔ 재시도(네트워크 · workflow 재실행 · persistence retry)가 충돌 하나를
        #      N 개처럼 부풀리게 두지 않는다.
        #   ★ 같은 provenance 에서 **제3의 새 content** 가 오면 새 evidence 로 보존한다.
        if _conflict_seen(entry, conflict):
            return state, _accept(
                CONFLICT, key,
                conflict["reason"] + " (이미 기록된 동일 충돌 — state 변화 없음)",
                _entry_view(entry), new_conflict=False)
        entry = dict(entry)
        entry["conflicts"] = list(entry.get("conflicts") or []) + [conflict]
        new_state["series"][ks] = _entry_view(entry)
        return new_state, _accept(CONFLICT, key, conflict["reason"],
                                  new_state["series"][ks], new_conflict=True)

    # ── ⑤ REVISION — 같은 key, 다른 provenance ───────────────────────
    #    ⛔ 기존 revision 을 삭제하지 않는다 · ⛔ authority 를 자동 선택하지 않는다.
    entry = dict(entry)
    entry["revisions"] = list(entry["revisions"]) + [new_rev]
    new_state["series"][ks] = _entry_view(entry)
    return new_state, _accept(
        REVISION, key,
        "같은 economic period 인데 provenance 가 다르다 — revision chain 에 추가. "
        "⛔ authority 는 자동 선택되지 않는다",
        new_state["series"][ks])


def apply_many(state: dict, records: list) -> tuple:
    """여러 record 를 순서대로 적용한다. (new_state, results)."""
    results = []
    cur = state
    for r in records:
        cur, res = apply_record(cur, r)
        results.append(res)
    return cur, results


def _conflict_seen(entry: dict, conflict: dict) -> bool:
    """이미 기록된 동일 충돌인가.

    같음의 정의 = 같은 material provenance **그리고** 같은 incoming content.
    ⛔ provenance 만 같다고 같은 충돌로 보지 않는다 — 제3의 새 content 는 새 충돌이다.
    """
    for c in entry.get("conflicts") or []:
        if (c.get("material_provenance_digest") == conflict["material_provenance_digest"]
                and (c.get("incoming") or {}).get("content_digest")
                == conflict["incoming"]["content_digest"]):
            return True
    return False


def _accept(outcome: str, key, reason: str, entry: dict, new_conflict=None) -> dict:
    out = {"outcome": outcome, "accepted": True,
           "key": {"subject": key[0], "measurement_identity": key[1],
                   "economic_period_end": key[2]},
           "reason": reason,
           "revision_count": len(entry.get("revisions") or []),
           "conflict_count": len(entry.get("conflicts") or []),
           "blocked_by": entry.get("blocked_by", []),
           "consumable": entry.get("consumable", False)}
    if new_conflict is not None:
        # ★ 새 상태 어휘를 만들지 않는다 — outcome 은 그대로 CONFLICT 다.
        #   이 필드는 「새 충돌인가, 같은 충돌의 재관측인가」만 구별한다.
        out["new_conflict"] = new_conflict
    return out


def _reject(outcome: str, key, reason: str, evidence: dict) -> dict:
    """거부는 series 를 건드리지 않는다. ⛔ 조용히 무시하지도 않는다 — 증거를 낸다."""
    return {"outcome": outcome, "accepted": False,
            "key": ({"subject": key[0], "measurement_identity": key[1],
                     "economic_period_end": key[2]} if key else None),
            "reason": reason, "evidence": evidence,
            "revision_count": 0, "conflict_count": 0,
            "blocked_by": [outcome], "consumable": False}


def _safe_digest(obj) -> str:
    try:
        return digest(obj)
    except (TypeError, ValueError):
        return "UNSERIALIZABLE"


def record_rejection(state: dict, result: dict) -> dict:
    """거부 증거를 series 밖 로그에 남긴다. (new_state)

    ⛔ 거부된 record 를 정상 series 안에 넣지 않는다.
    ★ 무엇이 왜 거부됐는지 재현 가능해야 하므로 증거는 남긴다.
    """
    if result.get("accepted") is not False:
        raise StoreError("거부 결과가 아닌 것을 rejection 으로 기록하려 한다")
    new_state = copy.deepcopy(state)
    new_state["rejections"] = list(new_state.get("rejections") or []) + [
        {"outcome": result["outcome"], "key": result.get("key"),
         "reason": result["reason"], "evidence": result.get("evidence")}]
    return new_state


# ══════════════════════════════════════════════════════════════════════
# 조회 — ⛔ authority 를 고르지 않는다
# ══════════════════════════════════════════════════════════════════════
def get_entry(state: dict, key) -> dict | None:
    return state.get("series", {}).get(key_str(tuple(key)))


def consumable_keys(state: dict) -> list:
    """지금 소비 가능한 key 목록 — 정렬된 결정적 순서."""
    return sorted(k for k, e in state.get("series", {}).items() if e.get("consumable"))


def blocked_keys(state: dict) -> dict:
    """소비 불가 key → 사유. 층 ⑤ 가 이것을 보고 pair 를 막는다."""
    return {k: e.get("blocked_by", []) for k, e in sorted(state.get("series", {}).items())
            if not e.get("consumable")}


# ══════════════════════════════════════════════════════════════════════
# IO 경계 — ⛔ Git · workflow 를 모른다. 파일만 안다.
# ══════════════════════════════════════════════════════════════════════
def serialize(state: dict) -> str:
    """canonical JSON. 같은 state 는 항상 같은 바이트열이 된다."""
    if state.get("schema_version") != STORE_SCHEMA_VERSION:
        raise StoreError("직렬화 대상 schema 가 다르다")
    return canonical_json(state) + "\n"


def deserialize(text: str) -> dict:
    state = json.loads(text)
    if state.get("schema_version") != STORE_SCHEMA_VERSION:
        raise StoreError(f"역직렬화 schema 가 다르다: {state.get('schema_version')!r}")
    return state


def load_state(path: str) -> dict:
    """파일이 없으면 빈 state. ⛔ 손상된 파일을 조용히 빈 state 로 대체하지 않는다."""
    if not os.path.exists(path):
        return empty_state()
    with open(path, encoding="utf-8") as f:
        return deserialize(f.read())


def save_state(state: dict, path: str) -> str:
    """같은 디렉터리 임시 파일에 쓰고 `os.replace` 로 원자적 교체한다.

    ⛔ Git 을 모른다 · ⛔ workflow 를 모른다 — 파일 하나를 바꿀 뿐이다.
    ★ 부분 기록 상태가 남지 않아야 이전 records 가 보존된다.
    """
    payload = serialize(state)
    d = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".obsstore-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path
