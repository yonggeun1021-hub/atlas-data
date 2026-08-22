#!/usr/bin/env python3
"""Comparison of PIT replay results under the existing ruleset vs. the
proposed changes (deliverable 7) -- both runs over the exact same real
evidence and the exact same detected triggers, so the only thing that
differs is the rule being applied.
"""
from __future__ import annotations


def compare(entries: list[dict]) -> dict:
    total = len(entries)
    with_data = [e for e in entries if e["data_available"]]
    with_trigger = [e for e in with_data if e["triggers"]]
    with_confirmation = [e for e in with_trigger if len(e["proposed_ruleset"]["trigger_types_present"]) >= 2]

    existing_convertible = [e for e in with_trigger if e["existing_ruleset"]["action_convertible"]]
    proposed_convertible = [e for e in with_trigger
                             if e["proposed_ruleset"]["recommended_action"]
                             in ("PROBE_REVIEW_CANDIDATE", "PROBE_REVIEW_CANDIDATE_TACTICAL")]
    proposed_qualified_pending_gate = [e for e in with_trigger
                                        if e["proposed_ruleset"]["conditions_1_to_6_all_pass"]]

    def rate(numerator, denominator):
        return (len(numerator) / len(denominator) * 100.0) if denominator else None

    return {
        "population": {
            "total_entries": total,
            "entries_with_pit_data": len(with_data),
            "entries_with_any_trigger": len(with_trigger),
            "entries_with_independent_confirmation": len(with_confirmation),
        },
        "existing_ruleset": {
            "action_conversion_rate_pct": rate(existing_convertible, with_trigger),
            "convertible_count": len(existing_convertible),
            "review_cadence_days": 30,
            "note": "trade_proposal is unconditionally None (decision/alpha_review.py) -- "
                    "action_conversion_rate is 0% by construction, regardless of trigger strength",
        },
        "proposed_ruleset": {
            "action_conversion_rate_pct": rate(proposed_convertible, with_trigger),
            "convertible_count": len(proposed_convertible),
            "review_cadence_days": "dynamic (next trading day for price/flow triggers, per Control Loop doc section 6)",
            "condition_7_gate_ratified_anywhere": any(
                e["proposed_ruleset"]["condition_7_gate_ratified"] == "PASS" for e in with_trigger
            ),
            "qualified_pending_gate_ratification_count": len(proposed_qualified_pending_gate),
            "qualified_pending_gate_ratification_rate_pct": rate(proposed_qualified_pending_gate, with_trigger),
            "note": "conditions 1-6 (hypothesis, independent confirmation, entry zone, invalidation, "
                    "position sizing, PIT integrity) are independently evaluated from real trigger/price "
                    "data; condition 7 (ratified Probe P5 Rule) is uniformly False today (see "
                    "action_conversion_gate.gate_available()), which is why the final action-conversion "
                    "rate still matches the existing ruleset's 0% -- the differentiating value of the "
                    "proposed ruleset is in `qualified_pending_gate_ratification_count`, not in the final "
                    "rate, until a Probe-specific P5 Rule is ratified.",
        },
        "delta": {
            "action_conversion_rate_pct_delta": (
                (rate(proposed_convertible, with_trigger) or 0.0) - (rate(existing_convertible, with_trigger) or 0.0)
            ),
            "qualified_pending_gate_ratification_vs_existing_pct": (
                (rate(proposed_qualified_pending_gate, with_trigger) or 0.0)
                - (rate(existing_convertible, with_trigger) or 0.0)
            ),
        },
    }
