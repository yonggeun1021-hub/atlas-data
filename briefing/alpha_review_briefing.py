#!/usr/bin/env python3
"""P8-11 stage 2 — Alpha Review human-readable briefing renderer.

★ This module is a **pure rendering function**. It computes nothing and
  invents nothing -- every line of output is a direct rendering of a
  specific field already present in the four packets/records it is given
  (`forward_thesis`, `expectations_gap`, `price_reflection`, `alpha_review`,
  optionally `shadow_ledger_entry`). It does not call `validate_packet()` on
  any of them (the caller is expected to have already built/validated real
  packets); it only reads already-validated dict fields.

★ 확인된 사실 (§3, from `forward_thesis.observed_facts`) and 미래 가설
  (§4, from `forward_thesis.forward_inferences`) are rendered in two
  structurally separate sections, on purpose -- the user's explicit
  requirement is that a forward-looking guess must never be visually
  indistinguishable from a sourced fact. Every §3 line visibly carries its
  `source_class` so a NARRATIVE_SOURCED line can never look identical to an
  EXHIBIT_EXTRACTED one. Every §7 line visibly carries `data_source_scope`
  so an IEX-partial-market or UNKNOWN-scope read is never presented as if it
  had full-market authority.

★ §12 (현재 행동) is explicit about what this briefing is NOT: capital is
  always 0, human_approval_required is always True, and this is never a
  Stage/Candidate/Ready/Buy promotion -- see `alpha_review.py`'s and
  `alpha_shadow_ledger.py`'s own hard-coded-authority docstrings, which this
  module only quotes, never overrides.
"""
from __future__ import annotations


def _bullet_lines(items: list[str], empty_note: str) -> str:
    if not items:
        return f"- {empty_note}"
    return "\n".join(f"- {item}" for item in items)


def _section_1_subject(alpha: dict) -> str:
    return (
        f"## 1. 종목\n\n"
        f"- subject: `{alpha['subject']}`\n"
        f"- decision_date: `{alpha['decision_date']}`\n"
    )


def _section_2_why_now(alpha: dict) -> str:
    return "## 2. 왜 지금 보는가\n\n" + _bullet_lines(
        alpha.get("why_now", []), "why_now 없음 (alpha_review.why_now 비어 있음)."
    )


def _section_3_observed_facts(ft: dict) -> str:
    facts = ft.get("observed_facts", [])
    if not facts:
        body = "- 확인된 사실 없음 (forward_thesis.observed_facts 비어 있음)."
    else:
        lines = []
        for fact in facts:
            lines.append(
                f"- [{fact['source_class']}] {fact['statement']} "
                f"(source_ref=`{fact['source_ref']}`, as_of={fact['as_of']})"
            )
        body = "\n".join(lines)
    return "## 3. 확인된 사실 (source_class 명시)\n\n" + body


def _section_4_forward_inferences(ft: dict) -> str:
    inferences = ft.get("forward_inferences", [])
    if not inferences:
        body = "- Atlas의 미래 가설 없음 (forward_thesis.forward_inferences 비어 있음)."
    else:
        lines = []
        for inf in inferences:
            lines.append(
                f"- {inf['statement']} (basis: {inf['basis']}; confidence={inf['confidence']})"
            )
        body = "\n".join(lines)
    return (
        "## 4. Atlas의 미래 가설 (§3과 절대 혼동 금지 -- 확인된 사실이 아니라 가설이다)\n\n"
        + body
    )


def _section_5_what_market_missing(alpha: dict) -> str:
    return "## 5. 시장이 놓친 것으로 추정하는 부분\n\n" + _bullet_lines(
        alpha.get("what_market_may_be_missing", []),
        "없음 (alpha_review.what_market_may_be_missing 비어 있음).",
    )


def _section_6_earnings_conversion(ft: dict) -> str:
    ec = ft["earnings_conversion"]
    lines = [
        f"- status: `{ec['status']}`",
        f"- mechanism: {ec['mechanism']}",
        f"- expected_start_window: {ec['expected_start_window']}",
        f"- expected_duration: {ec['expected_duration']}",
        f"- confidence: `{ec['confidence']}`",
    ]
    if ec.get("blockers"):
        lines.append("- blockers:")
        lines.extend(f"  - {b}" for b in ec["blockers"])
    else:
        lines.append("- blockers: 없음")
    return "## 6. 실적 전환 예상 구간\n\n" + "\n".join(lines)


def _section_7_price_reflection(pr_packet: dict) -> str:
    pr = pr_packet["price_reflection"]
    lines = [
        f"- price_state: `{pr['price_state']}` (가격/모멘텀만 — reflection 판정 아님)",
        f"- reflection_status: `{pr['reflection_status']}` "
        "(reference point 없으면 항상 UNKNOWN — momentum만으로 reflection 확정 불가)",
        f"- confidence: `{pr['confidence']}`",
        f"- data_state: `{pr['data_state']}`",
        f"- threshold_basis: `{pr['threshold_basis']}` "
        "(⚠ PROVISIONAL = 미비준 임계값, 최종/확정 판정 아님)",
        f"- data_source_scope: `{pr['data_source_scope']}` "
        "(⚠ IEX_ONLY_PARTIAL_US_MARKET/UNKNOWN scope is never full-market authority)",
        f"- price_as_of: {pr['price_as_of']}",
    ]
    return "## 7. 현재 가격/반영 정도\n\n" + "\n".join(lines)


def _section_8_next_catalyst(ft: dict, alpha: dict) -> str:
    catalysts = alpha.get("catalyst_timing", {}).get("catalysts", ft.get("catalysts", []))
    if not catalysts:
        body = "- 다음 촉매 없음 (catalysts 비어 있음)."
    else:
        lines = []
        for c in catalysts:
            ref = f", source_ref=`{c['source_ref']}`" if c.get("source_ref") else ""
            lines.append(f"- {c['description']} (예상: {c['expected_date_or_window']}{ref})")
        body = "\n".join(lines)
    return "## 8. 다음 촉매\n\n" + body


def _section_9_entry_conditions(alpha: dict) -> str:
    return "## 9. 탐색 진입 조건\n\n" + _bullet_lines(
        alpha.get("entry_conditions", []), "진입 조건 없음."
    )


def _section_10_add_conditions(alpha: dict) -> str:
    return "## 10. 증액 조건\n\n" + _bullet_lines(
        alpha.get("add_conditions", []), "증액 조건 없음."
    )


def _section_11_invalidation_conditions(alpha: dict) -> str:
    return "## 11. 무효화 조건\n\n" + _bullet_lines(
        alpha.get("invalidation_conditions", []), "무효화 조건 없음."
    )


def _section_12_current_action(alpha: dict, shadow: dict | None) -> str:
    lines = [
        f"- opportunity_state: `{alpha['opportunity_state']}`",
        f"- p5_rule_status: `{alpha['p5_rule_status']}`",
        f"- portfolio_status: `{alpha['portfolio_status']}`",
        f"- trade_proposal: `{alpha['trade_proposal']}` (항상 null -- 이 MVP에 P5 PASS + "
        "Human Approval 경로 없음)",
    ]
    if shadow is not None:
        proposal = shadow["shadow_proposal"]
        lines.extend([
            f"- shadow_proposal.action: `{proposal['action']}`",
            f"- shadow_proposal.capital: `{proposal['capital']}` (항상 0)",
            f"- shadow_proposal.human_approval_required: `{proposal['human_approval_required']}` "
            "(항상 true)",
        ])
    lines.append(
        "- ⚠ 이것은 Stage/Candidate/Ready/Buy 승격이 아니다. 실거래 자본은 항상 0이며, "
        "인간 승인이 항상 필요하다."
    )
    return "## 12. 현재 행동\n\n" + "\n".join(lines)


def _appendix_confidence(ft: dict, eg_packet: dict, pr_packet: dict) -> str:
    ec_conf = ft["earnings_conversion"]["confidence"]
    eg_conf = eg_packet["expectations_gap"]["confidence"]
    pr_conf = pr_packet["price_reflection"]["confidence"]
    return (
        "## 확신도\n\n"
        f"- earnings_conversion.confidence: `{ec_conf}`\n"
        f"- expectations_gap.confidence: `{eg_conf}`\n"
        f"- price_reflection.confidence: `{pr_conf}`\n\n"
        "(세 값을 하나의 평균으로 합치지 않는다 -- 각자 다른 것을 측정한다.)"
    )


def _appendix_missing_data(ft: dict, eg_packet: dict, pr_packet: dict) -> str:
    missing = list(dict.fromkeys(
        ft.get("unknowns", [])
        + eg_packet["expectations_gap"].get("missing_inputs", [])
        + pr_packet["price_reflection"].get("missing_inputs", [])
    ))
    return "## 부족한 데이터\n\n" + _bullet_lines(missing, "없음.")


def render_briefing(
    *,
    alpha_review_packet: dict,
    forward_thesis_packet: dict,
    expectations_gap_packet: dict,
    price_reflection_packet: dict,
    shadow_ledger_entry: dict | None = None,
) -> str:
    """Render one subject's full Alpha Review evidence chain as a 12-section
    (+2 appended) Korean-language markdown briefing. Pure rendering -- every
    line traces to a specific already-validated packet field; see module
    docstring."""
    alpha = alpha_review_packet
    ft = forward_thesis_packet
    eg = expectations_gap_packet
    pr = price_reflection_packet

    sections = [
        f"# Alpha Review Briefing -- {alpha['subject']} ({alpha['decision_date']})",
        _section_1_subject(alpha),
        _section_2_why_now(alpha),
        _section_3_observed_facts(ft),
        _section_4_forward_inferences(ft),
        _section_5_what_market_missing(alpha),
        _section_6_earnings_conversion(ft),
        _section_7_price_reflection(pr),
        _section_8_next_catalyst(ft, alpha),
        _section_9_entry_conditions(alpha),
        _section_10_add_conditions(alpha),
        _section_11_invalidation_conditions(alpha),
        _section_12_current_action(alpha, shadow_ledger_entry),
        _appendix_confidence(ft, eg, pr),
        _appendix_missing_data(ft, eg, pr),
    ]
    return "\n\n".join(sections) + "\n"
