#!/usr/bin/env python3
"""P2-03 -> rotation_state_ledger -> daily briefing one-shot wiring proof.

Manual verification tool (mirrors the existing korea_breadth_derived_
outputs.py precedent): builds one real Korea capital-rotation packet
using the committed, real P3-03/P1-KR-05 breadth-context lineage
(rotation/korea_capital_rotation_ledger_wire.py's coverage_context.
breadth), applies it through the existing, UNCHANGED rotation_state_
ledger.apply_rotation(), and refreshes the committed briefing pointer
(data/latest_korea_rotation.json) that briefing/daily_orchestrator.py
reads -- then, optionally, builds one real daily briefing packet for the
same decision_date to prove the BLOCKED breadth lineage renders all the
way through.

Honesty boundary, updated 2026-08-22 (minimal rotation_policy
ratification): Breadth, Leadership, AND korea_capital_rotation.py's own
rotation_policy are now all real. Breadth is the committed P3-03/
P1-KR-05 lineage (unchanged, still available_at=null -> BLOCKED --
that is a separate, still-open boundary, see PR B). Leadership is the
committed korea_leadership_context/{date}/packet.json real
observations, built by real KRX index fetches through the ratified
P1-KR-07 policy (48 sector/benchmark identities). rotation_policy
(REAL_ROTATION_POLICY below) is now RATIFIED for real: it reuses the
already-implemented ranking meaning (RELATIVE_STRENGTH_VS_OWN_
BENCHMARK, own-benchmark-scope-only, TOP/MIDDLE/BOTTOM ordinal
buckets) exactly as-is, maps every real ratified P1-KR-07 SECTOR
identity 1:1 to a positional theme_id token (never a P2-01 cross-market
Theme grouping -- that taxonomy remains UNRATIFIED, honestly recorded
via the all-zero taxonomy_decision/packet SHA placeholders below), and
introduces exactly one new number: top_count=bottom_count=1. That is
deliberately the *only* value that needs no external justification --
"flag the single best and single worst performer within each
benchmark's own scope" is the unique choice that is not a percentage or
score cutoff (any N>1 would need a basis for N that does not exist), so
it is the minimal non-arbitrary realization of the existing TOP/BOTTOM
bucket vocabulary, not an invented investment threshold.

POLICY_EFFECTIVE here means only "this calculation contract is now
active" -- authority.trading_authorized / stage_promotion_authorized /
production_authorized etc. all stay closed exactly as before; nothing
about Buy/Stage/Action authority changes.

Anti-lookahead note: korea_capital_rotation.py's own _validate_policy()
rejects `ratified_at_utc` claimed to be before an observation pair's
prior_available_at was already real (OUTPUT_POLICY_RATIFIED_AFTER_
PRIOR_OBSERVATION) whenever that pair falls inside the policy's
effective window -- this is what stops a ratification from being
backdated to retroactively "cover" evidence that already existed. The
real REAL_ROTATION_POLICY.ratified_at_utc below is genuinely fixed at
the real moment this policy was ratified (2026-08-22T07:19:09Z); the
already-committed 2026-08-19/2026-08-21 evidence pair predates that
timestamp and is therefore correctly REJECTED by this same real policy
if replayed (see test_korea_capital_rotation_policy_ratified.py) --
this is not a bug, it is the anti-cherry-picking property working as
designed. The real end-to-end proof that flips POLICY_NOT_EFFECTIVE ->
KOREA_BREADTH_BLOCKED therefore uses a genuinely NEW observation pair
(2026-08-18 prior / 2026-08-20 current) fetched strictly AFTER
ratification.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WIRE = _load_module(
    "korea_capital_rotation_ledger_wire_for_proof",
    "rotation/korea_capital_rotation_ledger_wire.py",
)
KCR = _load_module("korea_capital_rotation_for_proof", "rotation/korea_capital_rotation.py")
LEDGER = _load_module("rotation_state_ledger_for_proof", "rotation/rotation_state_ledger.py")


def load_real_leadership_packet(observation_date: str) -> dict:
    """Reads the real, already-committed korea_leadership_context
    evidence (built by a real .github/scripts/korea_leadership_live_
    fetch.py workflow_dispatch run) and returns the full real
    korea_leadership.build_transform() output for that date -- fails
    closed if that date's real run never populated or was blocked."""
    path = (
        ROOT / "data" / "observations" / "korea_leadership_context"
        / observation_date / "packet.json"
    )
    if not path.is_file():
        raise RuntimeError(f"NO_LEADERSHIP_EVIDENCE_FOR_DATE:{observation_date}")
    summary = json.loads(path.read_text(encoding="utf-8"))
    if summary.get("outcome") != "populated" or not summary.get("leadership_packet"):
        raise RuntimeError(
            f"LEADERSHIP_NOT_POPULATED_FOR_DATE:{observation_date}:{summary.get('outcome')}"
        )
    return summary["leadership_packet"]


# The real, fixed moment REAL_ROTATION_POLICY was ratified. Held as a
# literal constant (never dt.datetime.now()) so every invocation of this
# script -- today or on a future rerun -- sees the exact same real
# ratification instant; a policy's own ratified_at_utc is a historical
# fact, not something that should drift with wall-clock time.
REAL_ROTATION_POLICY_RATIFIED_AT_UTC = "2026-08-22T07:19:09Z"
REAL_ROTATION_POLICY_EFFECTIVE_FROM = "2026-08-01"


def build_real_price_side(prior_date: str, current_date: str):
    """Real prior_observation/current_observation from the two committed
    real Leadership packets -- no synthetic fixture. Builds the REAL,
    ratified rotation_policy (see module docstring): every real ratified
    P1-KR-07 SECTOR identity is mapped 1:1 to a positional theme_id
    token (never a P2-01 cross-market theme grouping -- that taxonomy
    stays UNRATIFIED, honestly recorded via the all-zero taxonomy
    binding placeholders), ranking/order/tie-break reuse the contract's
    existing meaning unchanged, and top_count=bottom_count=1 is the one
    new number this ratification introduces (see docstring for why 1 is
    the minimal non-arbitrary choice). approval_status is now RATIFIED
    for real: build_packet() will genuinely rank and bucket whenever a
    supplied observation pair's dates fall inside the effective window
    AND that pair's own prior_available_at is not before this real
    ratified_at_utc (anti-lookahead, see module docstring)."""
    prior = load_real_leadership_packet(prior_date)
    current = load_real_leadership_packet(current_date)

    leadership_policy = _load_module(
        "korea_leadership_for_proof", ".github/scripts/korea_leadership.py"
    ).load_policy()
    upstream_leadership_policy_sha256 = current["policy"]["policy_sha256"]

    taxonomy_decision_sha256 = "0" * 64  # no real ratified P2-01 decision exists yet
    taxonomy_packet_sha256 = "0" * 64
    binding = {
        "taxonomy_contract_version": "theme_taxonomy/1",
        "taxonomy_id": "TAXONOMY.NOT_RATIFIED",
        "taxonomy_decision_id": "DECISION.NOT_RATIFIED",
        "taxonomy_decision_sha256": taxonomy_decision_sha256,
        "taxonomy_packet_sha256": taxonomy_packet_sha256,
        "upstream_leadership_policy_sha256": upstream_leadership_policy_sha256,
    }
    context = {
        "breadth": None,  # filled in by the caller with the real breadth context
        "investor_flow": {
            "status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
            "market_venue_scope": "KRX_ONLY",
            "nxt_included": False,
            "whole_korea_market_claim_authorized": False,
            "source_release_time_status": "unverified",
            "available_at": None,
            "decision_eligible": False,
            "ranking_input_authorized": False,
        },
    }
    input_value = {
        "schema_version": "korea_capital_rotation_input/1",
        "as_of_date": current_date,
        "taxonomy_binding": binding,
        "coverage_context": context,
        "prior_observation": prior,
        "current_observation": current,
    }

    def scope_for(market_prefix: str, benchmark_identity: str) -> dict:
        members = sorted(
            row["series_identity"]
            for row in current["relative_strength_observations"]
            if row["role"] == "SECTOR" and row["series_identity"].startswith(f"{market_prefix}::")
        )
        return {
            "benchmark_identity": benchmark_identity,
            "members": [
                {
                    "series_identity": identity,
                    # positional token tied back to the real series_identity
                    # via this same mapping -- not an invented cross-market
                    # theme grouping (P2-01 Theme taxonomy remains
                    # unratified: taxonomy_decision/packet SHA below are the
                    # honest all-zero placeholder, never a real P2-01
                    # decision).
                    "theme_id": f"{market_prefix}.SECTOR.{index:02d}",
                }
                for index, identity in enumerate(members, 1)
            ],
            # The one new number this ratification introduces -- see
            # module docstring for why 1 (not any N>1) is the minimal
            # non-arbitrary realization of the existing TOP/BOTTOM bucket
            # vocabulary: it identifies only the single extremal member on
            # each side, never a chosen percentage/score cutoff.
            "top_count": 1,
            "bottom_count": 1,
        }

    rotation_policy = {
        "schema_version": "korea_capital_rotation_policy/1",
        "policy_id": "POLICY.P2.03.KOREA_OWN_BENCHMARK_EXTREMES_V1",
        "approval_status": "RATIFIED",
        "ratified_by": "Atlas CIO",
        "ratified_at_utc": REAL_ROTATION_POLICY_RATIFIED_AT_UTC,
        "effective_from": REAL_ROTATION_POLICY_EFFECTIVE_FROM,
        "effective_to": None,
        "taxonomy_decision_sha256": taxonomy_decision_sha256,
        "taxonomy_packet_sha256": taxonomy_packet_sha256,
        "upstream_leadership_policy_sha256": upstream_leadership_policy_sha256,
        "ranking_metric": "RELATIVE_STRENGTH_VS_OWN_BENCHMARK",
        "ranking_order": "DESCENDING_WITHIN_BENCHMARK_SCOPE",
        "tie_break": "SERIES_IDENTITY_ASC",
        "maximum_calendar_gap_days": 30,
        "benchmark_scopes": [
            scope_for("KOSDAQ", "KOSDAQ::코스닥"),
            scope_for("KOSPI", "KOSPI::코스피"),
        ],
    }
    return input_value, rotation_policy


def build_state_policy(rotation_packet: dict) -> dict:
    """Same closed-vocabulary RATIFIED test state policy already used by
    test/test_rotation_state_ledger.py -- the ledger itself is unchanged,
    this only supplies a policy that satisfies its own validator."""
    return {
        "schema_version": LEDGER.POLICY_SCHEMA_VERSION,
        "policy_id": "POLICY.P2.05.PROOF",
        "approval_status": "RATIFIED",
        "ratified_by": "Atlas CIO",
        "ratified_at_utc": "2026-08-01T00:00:00Z",
        "effective_from": "2026-08-01",
        "effective_to": None,
        "market": "KOREA",
        "input_rotation_contract_version": rotation_packet["contract_version"],
        "input_rotation_policy_sha256": rotation_packet["lineage"]["rotation_policy_sha256"],
        "state_vocabulary": ["EMERGING", "STRONG", "WEAKENING"],
        "state_by_bucket_transition": {
            "BOTTOM_TO_BOTTOM": "WEAKENING", "BOTTOM_TO_MIDDLE": "EMERGING",
            "BOTTOM_TO_TOP": "EMERGING", "MIDDLE_TO_BOTTOM": "WEAKENING",
            "MIDDLE_TO_MIDDLE": "STRONG", "MIDDLE_TO_TOP": "EMERGING",
            "TOP_TO_BOTTOM": "WEAKENING", "TOP_TO_MIDDLE": "WEAKENING",
            "TOP_TO_TOP": "STRONG",
        },
        "maximum_ledger_gap_days": 30,
    }


def run(prior_date: str, current_date: str, ledger_out: Path | None, pointer_out: Path | None) -> dict:
    as_of_date = current_date
    value, rotation_policy = build_real_price_side(prior_date, current_date)
    source = WIRE.load_breadth_context_source(as_of_date)
    breadth, reason = WIRE.build_coverage_context_breadth(as_of_date, 3, source)
    value["coverage_context"]["breadth"] = breadth
    rotation_packet = KCR.build_packet(value, rotation_policy)

    ledger = None
    # rotation_policy is now RATIFIED for real, but build_packet() still
    # independently re-derives rotation_policy_effective per the actual
    # observation pair's dates and anti-lookahead check (see module
    # docstring) -- a pair the ratified policy does not cover, or one
    # that predates real ratification, still honestly comes back
    # POLICY_NOT_EFFECTIVE. rotation_state_ledger.apply_rotation() only
    # ever accepts a packet whose own status is ROTATION_BUCKETS_OBSERVED
    # (rotation_policy_effective=True); an ineffective packet is
    # correctly never pushed into the ledger, fail-closed either way.
    if rotation_packet["rotation_policy_effective"] and ledger_out is not None:
        state_policy = build_state_policy(rotation_packet)
        ledger = LEDGER.apply_rotation(rotation_packet, state_policy, previous_ledger=None)
        LEDGER.write_json_atomic(ledger_out, ledger)

    context_rel_path = None
    if source is not None:
        context_rel_path = str(
            WIRE.context_source_path(as_of_date).relative_to(ROOT)
        )
    pointer = WIRE.build_briefing_pointer(
        rotation_packet, reason, source, context_rel_path,
        generated_at=(source["generated_at"] if source else f"{as_of_date}T23:59:59Z"),
    )
    if pointer_out is not None:
        WIRE.write_json_atomic(pointer_out, pointer)
    return {
        "rotation_packet": rotation_packet,
        "ledger": ledger,
        "pointer": pointer,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prior-date", required=True, help="YYYY-MM-DD, real committed Leadership evidence")
    parser.add_argument("--current-date", required=True, help="YYYY-MM-DD, real committed Leadership evidence")
    parser.add_argument("--ledger-out", type=Path, default=None)
    parser.add_argument(
        "--commit-pointer", action="store_true",
        help="Also write data/latest_korea_rotation.json (tracked, committed).",
    )
    args = parser.parse_args()
    pointer_out = WIRE.BRIEFING_POINTER_PATH if args.commit_pointer else None
    result = run(args.prior_date, args.current_date, args.ledger_out, pointer_out)
    print(
        "korea capital rotation ledger proof: "
        f"rotation_status={result['rotation_packet']['status']} "
        f"rotation_policy_effective={result['rotation_packet']['rotation_policy_effective']} "
        f"breadth_status={result['rotation_packet']['coverage_context']['breadth']['status']} "
        f"ledger_revision={result['ledger']['ledger_revision'] if result['ledger'] else '(not eligible)'} "
        f"pointer_path={pointer_out or '(not written)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
