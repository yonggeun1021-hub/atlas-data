"""B1 분해 어휘 — 폐쇄 집합. 새 토큰을 여기서 만들지 않는다.

각 토큰은 출처를 갖는다. 출처 없는 토큰은 추가하지 않는다.
결정할 수 없는 자리는 전부 `UNRESOLVED` 다 — 빈칸으로 두거나 그럴듯한 값을
채우면 "결정하지 않았다"와 "이렇게 결정했다"를 구분할 수 없게 된다.
"""

UNRESOLVED = "UNRESOLVED"          # CIO 지시 2026-08-15 — 결정 불가는 이 값으로 보고

# ── 정본 §21-14(2) — 상태를 3필드로 분리한다 ────────────────────────────
DEFINITION_STATUS = {"DEFINED", "UNDEFINED", UNRESOLVED}
DATA_STATUS = {"AVAILABLE", "MISSING", "UNDETERMINED", UNRESOLVED}
EVALUATOR_STATUS = {"READY", "BLOCKED", UNRESOLVED}     # ★ 파생값 — 직접 입력 금지

# ── E2E inactive draft — capability / source qualification 축 ───────────
DATA_CAPABILITY = {"SUPPORTED", "NOT_IMPLEMENTED", "PERMANENTLY_UNAVAILABLE", UNRESOLVED}
#   ★ SOURCE_RESOLVED — CIO 판정 2026-08-15 (P3) 로 승인돼 `RULE-0003·0007·0008` 에
#     실제로 실렸으나 **어휘 집합에 등록되지 않은 채로 통과했다.** 값 자체는 유효하므로
#     되돌리지 않고 여기 정식 등록한다. 그 누락이 가능했던 이유는 아래 참조.
SOURCE_QUALIFICATION = {"SOURCE_UNRESOLVED", "SOURCE_UNVERIFIED", "SOURCE_RESOLVED",
                        UNRESOLVED, None}

# ── 정본 §21-12(4) · §21-13 — downstream effect ────────────────────────
#    §21-13 표가 실제로 쓴 표기를 그대로 옮긴다.
DOWNSTREAM_EFFECT = {
    "daily_eligibility",     # §21-12(4)
    "execution_reference",   # §21-12(4)
    "monitoring",            # §21-13 (NVIDIA 8/26 실적 → monitoring)
    "강등 검토",              # §21-13. ⛔ CIO 판정 2026-08-15 #2 — Rule Evaluator 의 최종
                             #    downstream effect 로 승인하지 않는다. migration 중
                             #    legacy/provisional 표현으로만 보존하고 executable
                             #    semantics 로 쓰지 않는다. Rule 은 조건 충족 사실까지만
                             #    산출하고 Stage 변경은 Decision Layer 소관이다.
    UNRESOLVED,
    None,
}

# ── 소실된 B1 draft 의 rule_id 에서 관측된 어휘 ─────────────────────────
#    ⚠ 이 세 토큰은 draft 가 **사용**했을 뿐 정본이 **정의**한 적이 없다.
#       TSM-FAL-01 · TSM-ENT-01 · NVDA-ENT-01 · 298040-FAL-02 · 298040-FAL-03
#       · TSM-MON-01 · SNDK-MON-01 에서 관측. 정의 신설은 CIO 판정 사항.
#    ★ CIO 판정 2026-08-15 #1 — 이번 B1 migration vocabulary 로 승인.
#      의미는 최소한으로만 고정한다. 새 투자 규칙 신설이 아니라 draft 토큰의 복구·정규화다.
#        FAL = 탈락/무효화 계열 조건 후보
#        ENT = 진입 eligibility 계열 조건 후보
#        MON = 관측 이벤트
RULE_KIND = {"FAL", "ENT", "MON", UNRESOLVED, None}

# ── 객체 역할 ──────────────────────────────────────────────────────────
#    rule_candidate / non_rule_evidence : CIO 판정 2026-08-15 문언
#    execution_reference                : 정본 §21-12(4)
OBJECT_ROLE = {"rule_candidate", "execution_reference", "non_rule_evidence", UNRESOLVED}

# ── 원문 문자 정책 — builder 와 validator 가 **같은 정의를 공유한다** ──────
#    CIO 검수 2026-08-15 ③ — 두 모듈이 서로 다른 delimiter 정책을 가지면
#    builder 가 보존하기로 한 결합 표기를 validator 가 놓친다.
#    따라서 여기 한 곳에서만 정의하고 양쪽이 import 한다.

# 결합 표기 — 조건이 어떻게 결합되는지를 담는다. **의미 보존 대상**이다.
#   'FQ4 미달 또는 ASP 하락' 과 'FQ4 미달 그리고 ASP 하락' 은 완전히 다른 규칙이다.
#   ⛔ 이 집합의 원소는 커버리지에서 무의미 문자로 취급하면 안 된다.
CONNECTIVES = {"또는", "+", "→", "·", "/"}

# 문장 부호 — 결합 의미를 담지 않는다. 커버리지에서 무시해도 되는 유일한 문자들.
#   ⛔ 여기에 결합 표기를 옮기면 조건 구조가 조용히 사라진다.
PUNCTUATION = {".", "—"}

assert not (CONNECTIVES & PUNCTUATION), "결합 표기와 문장 부호는 겹칠 수 없다"

# 커버리지에서 무시하는 문자 = 공백 + 문장 부호. 그 외는 전부 의미 있는 원문이다.
IGNORABLE_CHARS = " \t\n" + "".join(PUNCTUATION)


VOCAB = {
    "object_role": OBJECT_ROLE,
    "rule_kind": RULE_KIND,
    "downstream_effect": DOWNSTREAM_EFFECT,
    "definition_status": DEFINITION_STATUS,
    "data_status": DATA_STATUS,
    "data_capability": DATA_CAPABILITY,
    "source_qualification": SOURCE_QUALIFICATION,
    "evaluator_status": EVALUATOR_STATUS,
}

# ★ 정본에 정의가 없어 이번 pilot 이 UNRESOLVED 로 올리는 어휘 공백
VOCABULARY_GAPS_RESOLVED = [
    "① FAL/ENT/MON — B1 migration vocabulary 로 승인 (최소 의미 고정)",
    "② 강등 검토 — Rule Evaluator 최종 effect 로 승인하지 않음. legacy/provisional 보존만",
    "③ execution_reference — Rule Inventory 집계 대상에서 제외. 별도 객체로 보존",
    "④ monitoring — Inventory 포함 · Evaluator Population 제외. 상태 억지로 채우지 않음",
]
VOCABULARY_GAPS = []          # 4건 전부 CIO 판정 2026-08-15 로 해소


# ══════════════════════════════════════════════════════════════════════
# 결함 C — Vocabulary Enforcement (CIO 판정 2026-08-15)
# ══════════════════════════════════════════════════════════════════════
#   발견 경위: `SOURCE_RESOLVED` 가 P3 승격에 실렸는데 회귀 16/16 · Actions PASS 로
#   통과했다. 원인은 어휘 검사가 **분해 단계(fragment)에만** 걸려 있었고
#   `config/rules.json` · `rules/rule_inventory.json` 같은 **하류 authoritative 산출물**
#   에는 걸려 있지 않았기 때문이다. 승격 단계에서 새로 만들어진 값은 어떤 검사도 통과할
#   필요가 없었다.
#
#   ⛔ 이 함수는 새 어휘를 만들지 않는다. 위에 선언된 폐쇄 집합을 **강제**할 뿐이다.
#   ★ evaluator_status 는 파생값이지만 **결과값도 어휘 안에 있어야** 하므로 검사한다.
#     (입력으로 적었는지 여부는 별도 검사가 담당한다.)
CLOSED_VOCAB_FIELDS = ("definition_status", "data_status", "data_capability",
                       "source_qualification", "evaluator_status",
                       "rule_kind", "downstream_effect")


def vocab_violations(record: dict, fields=CLOSED_VOCAB_FIELDS, tag: str = "") -> list:
    """폐쇄 어휘 밖 값을 전부 돌려준다. 하나라도 있으면 산출물을 내면 안 된다."""
    out = []
    for f in fields:
        if f not in record:
            continue
        allowed = VOCAB.get(f)
        if allowed is None:
            out.append(f"{tag}{f}: 폐쇄 어휘 선언이 없다 — 검사 대상으로 지정됐는데 집합이 없다")
            continue
        if record[f] not in allowed:
            out.append(f"{tag}{f}={record[f]!r} 는 허용 어휘 밖이다 "
                       f"(허용 {sorted(str(x) for x in allowed)})")
    return out


def derive_evaluator_status(definition_status: str, data_status: str) -> str:
    """★ 파생값이다. 직접 입력하는 필드로 두지 않는다.

    소실된 B1 draft 의 설계 결정 1건을 그대로 계승한다 —
    "evaluator_status 를 필드로 두지 않고 definition_status × data_status 에서
     파생시켰다." (§23-7 의 decision_input_status 파생 원칙과 같은 계열)

    ⛔ 둘 다 결손일 때 '어느 원인이 우선인가'를 여기서 정하지 않는다.
       B1-Q4 는 판정과 원인을 분리하면 사라지는 질문으로 resolved 됐다 —
       판정은 이진값이고, 원인은 blocked_by 로 복수 보존한다.
    """
    if definition_status == UNRESOLVED or data_status == UNRESOLVED:
        return UNRESOLVED
    if definition_status == "DEFINED" and data_status == "AVAILABLE":
        return "READY"
    return "BLOCKED"


def derive_blocked_by(definition_status: str, data_status: str,
                      source_qualification=None) -> list:
    """차단 원인은 **목록**이다 (§23-5 ①: 단수 필드는 그중 하나를 숨긴다)."""
    out = []
    if definition_status == "UNDEFINED":
        out.append("DEFINITION_UNDEFINED")
    if data_status == "MISSING":
        out.append("DATA_MISSING")
    if data_status == "UNDETERMINED":
        out.append("DATA_UNDETERMINED")
    if source_qualification in ("SOURCE_UNRESOLVED", "SOURCE_UNVERIFIED"):
        out.append(source_qualification)
    if definition_status == UNRESOLVED or data_status == UNRESOLVED:
        out.append("STATUS_UNRESOLVED")
    return out
