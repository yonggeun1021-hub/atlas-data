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

Honesty boundary: the Breadth side of the packet is 100% real committed
evidence (no re-fetch, no synthetic value). The Theme/Leadership price
side is NOT live yet -- no operational korea_leadership pipeline nor a
CIO-ratified korea_capital_rotation_policy exists today -- so this proof
reuses the SAME test-fixture-style price/policy construction already
ratified for use in test/test_korea_capital_rotation.py's own
make_bundle() helper, clearly labeled as such below and in every report
this tool's output feeds into. This is not a production cron: it is not
wired into any scheduled workflow, matching "no new cron".
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


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


def build_test_fixture_price_side(as_of_date: str):
    """Loads test/test_korea_capital_rotation.py's own make_bundle()/
    write_upstream_policy()/upstream_payload() helpers -- the SAME
    already-ratified test fixture used throughout that module's own test
    suite -- and rebuilds current_observation for as_of_date so the
    packet's own as_of_date matches the real Breadth lineage date being
    proven. Explicitly test-fixture price data; see module docstring."""
    sys.path.insert(0, str(ROOT / "test"))
    test_module = _load_module(
        "test_korea_capital_rotation_for_proof", "test/test_korea_capital_rotation.py"
    )
    value, policy = test_module.make_bundle()
    prior_values = {
        "11::KOSPI_반도체": "130", "12::KOSPI_바이오": "110", "13::KOSPI_방산": "90",
        "21::KOSDAQ_반도체": "105", "22::KOSDAQ_바이오": "120", "23::KOSDAQ_로봇": "80",
    }
    current_values = {
        "11::KOSPI_반도체": "110", "12::KOSPI_바이오": "140", "13::KOSPI_방산": "80",
        "21::KOSDAQ_반도체": "130", "22::KOSDAQ_바이오": "110", "23::KOSDAQ_로봇": "90",
    }
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        policy_path = test_module.write_upstream_policy(Path(raw) / "leadership-policy.json")
        prior_previous = "2026-08-14" if as_of_date == "2026-08-18" else "2026-08-19"
        prior_date = "2026-08-18" if as_of_date != "2026-08-18" else "2026-08-15"
        prior = test_module.KL.build_transform(
            test_module.upstream_payload(prior_date, prior_values), policy_path
        )
        current = test_module.KL.build_transform(
            test_module.upstream_payload(as_of_date, current_values), policy_path
        )
    value["as_of_date"] = as_of_date
    value["prior_observation"] = prior
    value["current_observation"] = current
    return value, policy


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


def run(as_of_date: str, ledger_out: Path, pointer_out: Path | None) -> dict:
    value, rotation_policy = build_test_fixture_price_side(as_of_date)
    source = WIRE.load_breadth_context_source(as_of_date)
    breadth, reason = WIRE.build_coverage_context_breadth(as_of_date, 3, source)
    value["coverage_context"]["breadth"] = breadth
    rotation_packet = KCR.build_packet(value, rotation_policy)
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
    parser.add_argument("--as-of-date", required=True)
    parser.add_argument("--ledger-out", required=True, type=Path)
    parser.add_argument(
        "--commit-pointer", action="store_true",
        help="Also write data/latest_korea_rotation.json (tracked, committed).",
    )
    args = parser.parse_args()
    pointer_out = WIRE.BRIEFING_POINTER_PATH if args.commit_pointer else None
    result = run(args.as_of_date, args.ledger_out, pointer_out)
    print(
        "korea capital rotation ledger proof: "
        f"rotation_status={result['rotation_packet']['status']} "
        f"breadth_status={result['rotation_packet']['coverage_context']['breadth']['status']} "
        f"ledger_revision={result['ledger']['ledger_revision']} "
        f"pointer_path={pointer_out or '(not written)'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
