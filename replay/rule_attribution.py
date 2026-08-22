#!/usr/bin/env python3
"""Keep / Change / Kill recommendation per existing rule (deliverable 6),
grounded in aggregate counts from the actual ledgers -- not speculative.

★ CIO review round 2 fix (flaw 5): takes deduplicated Opportunity Episodes,
  not raw daily rows.
★ CIO review round 3 fix (flaw 4): DATA_FAILURE is no longer present in
  `miss_episodes` at all (excluded upstream) -- coverage-gap evidence is now
  taken from a real `coverage_gap` report parameter instead of a Counter
  lookup that would always read 0.
"""
from __future__ import annotations

from collections import Counter


def recommend(miss_episodes: list[dict], defense_episodes: list[dict], comparison: dict,
              coverage_gap: dict | None = None) -> list[dict]:
    miss_causes = Counter(m["root_cause"] for m in miss_episodes if m["root_cause"])
    coverage_gap = coverage_gap or {}
    recs = []

    recs.append({
        "rule": "decision/alpha_review.py: trade_proposal unconditionally None",
        "evidence": {
            "material_misses_blocked_by_this_rule": miss_causes.get("GATE_BLOCK", 0)
                                                     + miss_causes.get("ACTION_CONVERSION_FAILURE", 0),
            "action_conversion_rate_existing_pct": comparison["existing_ruleset"]["action_conversion_rate_pct"],
            "action_conversion_rate_proposed_pct": comparison["proposed_ruleset"]["action_conversion_rate_pct"],
        },
        "recommendation": "CHANGE",
        "rationale": (
            "The replay shows this rule structurally zeroes Action Conversion Rate regardless of "
            "trigger strength or confirmation count -- it is not selectively blocking weak signals, "
            "it blocks all of them by construction. The safety property worth KEEPing is "
            "'no unratified capital/order' (see below), not this specific null-forever shape. "
            "CIO doctrine's own Opportunity Capture Control Loop section 11 slice 4 already "
            "prescribes the fix: a Probe-specific P5 Rule Slice with a real loss budget, not "
            "removal of the gate."
        ),
    })

    recs.append({
        "rule": "decision/alpha_review.py: default_next_review_cadence_days = 30 (fixed, uniform)",
        "evidence": {
            "material_misses_attributable_to_latency": miss_causes.get("DECISION_LATENCY", 0),
        },
        "recommendation": "CHANGE",
        "rationale": (
            "A fixed 30-day cadence cannot re-evaluate a flow/price-reversal trigger before it "
            "decays. The Control Loop doc's own section 6 already specifies a dynamic cadence "
            "(next trading day / 24h for price-flow triggers, event-driven for catalysts, 30d only "
            "for long-horizon thesis). Keep the 30-day default for long-horizon thesis review; "
            "add a fast lane for the trigger types this replay actually observed firing."
        ),
    })

    recs.append({
        "rule": "P0/P5 authority invariant: Stage/Buy/Action/Order/Production/trading = false "
                "until explicit ratification",
        "evidence": {
            "total_ledger_entries_with_zero_authority_violation": "all (verified structurally, see "
                                                                    "test_pit_replay_end_to_end.py)",
        },
        "recommendation": "KEEP",
        "rationale": (
            "Nothing in this replay depended on relaxing this invariant to find real, gradeable "
            "signal in the committed evidence. The Opportunity Capture Control Loop's own design "
            "(section 10) keeps this invariant and adds a Probe-specific gate underneath it rather "
            "than around it -- this replay's proposed-ruleset simulation does the same."
        ),
    })

    recs.append({
        "rule": "Repo evidence retention: no committed evidence exists for any date before 2026-08-13",
        "evidence": {
            "unauditable_entries": coverage_gap.get("unauditable_entries", "N/A"),
            "auditable_coverage_pct": coverage_gap.get("auditable_coverage_pct", "N/A"),
            "unauditable_days": coverage_gap.get("unauditable_days", []),
        },
        "recommendation": "CHANGE",
        "rationale": (
            "This is not a decision rule but an operational gap this replay could not paper over: "
            "22 of the 32 audit-window days have zero committed evidence of any kind. Whatever "
            "Atlas actually saw or said on those dates is unrecoverable from this repository. "
            "Recommend persisting daily evidence + generated briefing output as committed, dated "
            "artifacts going forward so a future audit of this kind does not hit the same wall."
        ),
    })

    recs.append({
        "rule": "Position sizing: no Portfolio NAV / per-trade loss allowance / Probe loss budget / "
                "portfolio headroom data source exists anywhere in this repo",
        "evidence": {
            "condition_5_position_sizing_status": "NOT_EVALUATED (structural constant, every entry)",
        },
        "recommendation": "CHANGE",
        "rationale": (
            "Even a fully-ratified Probe P5 Rule (see recommendation 1) would still have nothing "
            "real to size against today. This replay deliberately reports stop_distance_pct (a real "
            "entry-to-invalidation calculation) separately from position sizing rather than letting "
            "one stand in for the other. Recommend a ratified Portfolio Constitution NAV/headroom "
            "feed as a prerequisite alongside the Probe P5 Rule Slice, not after it."
        ),
    })

    recs.append({
        "rule": "Crypto asset taxonomy ratification timing (config/crypto_breadth_exclusion_taxonomy.json)",
        "evidence": {
            "eligible_crypto_ratified_from": "2026-08-19 (3 assets) / 2026-08-22 (84 more assets)",
        },
        "recommendation": "CHANGE",
        "rationale": (
            "The only real, ratified PIT-eligible crypto universe this repo has was ratified in the "
            "last 1-4 days of the audit window -- meaning a genuinely PIT-honest Crypto Opportunity "
            "Capture Rate is NOT_COMPUTABLE for nearly the entire window, not merely data-sparse. "
            "This is a real operational gap (taxonomy ratification lagged the audit window itself), "
            "not something this replay's methodology can fix by picking a different population."
        ),
    })

    return recs
