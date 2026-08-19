#!/usr/bin/env python3
"""Audit KOFIA unit evidence without assigning a transport unit.

The official data.go.kr guide supplies response samples but does not declare a
unit.  For the same historical dates, the current API reproduces the investor
deposit sample exactly while the credit-financing sample matches only after a
one-million divisor and half-up rounding.  FreeSIS displays both series in
million KRW, but that UI label is not an API transport contract.

This audit makes the primary-evidence conflict reproducible and keeps every
conversion and downstream authority fail-closed.
"""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import gzip
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
import kofia_first_seen as capture  # noqa: E402
import kofia_liquidity as liquidity  # noqa: E402


CONTRACT_PATH = ROOT / "config" / "kofia_unit_evidence_contract.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
RELATIONS = frozenset({"exact", "round_half_up_after_division"})
EXPECTED_OFFICIAL_GUIDE = {
    "page_url": "https://www.data.go.kr/data/15094809/openapi.do",
    "download_url": (
        "https://www.data.go.kr/cmm/cmm/fileDownload.do?"
        "atchFileId=FILE_000000002624267&fileDetailSn=1"
    ),
    "attachment_id": "FILE_000000002624267",
    "attachment_sequence": "1",
    "sha256": "49c2d261468fe36ae2684aba407033c5383b5c352c4a1785b7b21bbe664a87b4",
    "retrieved_date": "2026-08-19",
    "sample_semantics": "official_response_message_spec_sample_data",
    "unit_declared": False,
}
EXPECTED_LIVE_EVIDENCE = {
    "capture_path": (
        "evidence/kofia/full_coverage/2026-08-19/"
        "run-32262446592-attempt-1"
    ),
    "commit": "246fc40",
    "run_id": "32262446592",
    "run_attempt": "1",
    "collector_version": "kofia-first-seen-capture/v2",
    "source_contract_version": "kofia_liquidity_source/v3",
}
EXPECTED_COMPARISONS = [
    {
        "operation": "credit_financing",
        "observation_date": "2022-09-29",
        "field": "crdTrFingWhl",
        "guide_sample_raw": "17461184",
        "relation": "round_half_up_after_division",
        "divisor": "1000000",
    },
    {
        "operation": "investor_deposits",
        "observation_date": "2022-09-28",
        "field": "invrDpsgAmt",
        "guide_sample_raw": "52567764085909",
        "relation": "exact",
        "divisor": None,
    },
]
EXPECTED_QUALIFICATION = {
    "api_field_unit_status": "conflicting_primary_evidence",
    "api_raw_unit": None,
    "normalization_factor": None,
    "conversion_authorized": False,
    "decision_eligible": False,
    "regime_score_authorized": False,
    "production_wiring_authorized": False,
    "trading_action_authorized": False,
}


class UnitEvidenceError(RuntimeError):
    """Fail-closed KOFIA unit-evidence contract violation."""


def fail(code: str, detail: str) -> None:
    raise UnitEvidenceError(f"{code}: {detail}")


def decimal_text(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or liquidity.DECIMAL_TEXT.fullmatch(value) is None:
        fail("UNIT_VALUE_INVALID", label)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        fail("UNIT_VALUE_INVALID", label)
    if not parsed.is_finite() or parsed < 0:
        fail("UNIT_VALUE_INVALID", label)
    return parsed


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("UNIT_CONTRACT_INVALID", str(exc))
    if set(contract) != {
        "schema_version",
        "contract_version",
        "official_guide",
        "freesis_display",
        "live_evidence",
        "comparisons",
        "qualification",
    } or contract.get("schema_version") != 1:
        fail("UNIT_CONTRACT_INVALID", "schema or fields")
    if contract.get("contract_version") != "kofia_unit_evidence/v1":
        fail("UNIT_CONTRACT_INVALID", "contract_version")

    guide = contract.get("official_guide")
    if not isinstance(guide, dict) or set(guide) != {
        "page_url",
        "download_url",
        "attachment_id",
        "attachment_sequence",
        "sha256",
        "retrieved_date",
        "sample_semantics",
        "unit_declared",
    }:
        fail("UNIT_CONTRACT_INVALID", "official guide")
    if (
        guide != EXPECTED_OFFICIAL_GUIDE
        or SHA256.fullmatch(str(guide.get("sha256", ""))) is None
    ):
        fail("UNIT_CONTRACT_INVALID", "official guide identity")

    freesis = contract.get("freesis_display")
    if freesis != {
        "url": "https://freesis.kofia.or.kr/stat/main.do",
        "retrieved_date": "2026-08-19",
        "display_unit": "million_krw",
        "scope": "dashboard_display_only_not_api_transport_contract",
    }:
        fail("UNIT_CONTRACT_INVALID", "FreeSIS display boundary")

    live = contract.get("live_evidence")
    if not isinstance(live, dict) or set(live) != {
        "capture_path",
        "commit",
        "run_id",
        "run_attempt",
        "collector_version",
        "source_contract_version",
    }:
        fail("UNIT_CONTRACT_INVALID", "live evidence")
    capture_path = Path(str(live.get("capture_path", "")))
    if (
        live != EXPECTED_LIVE_EVIDENCE
        or capture_path.is_absolute()
        or ".." in capture_path.parts
        or capture_path.parts[:3] != ("evidence", "kofia", "full_coverage")
        or COMMIT.fullmatch(str(live.get("commit", ""))) is None
        or capture.RUN_ID.fullmatch(str(live.get("run_id", ""))) is None
        or capture.RUN_ID.fullmatch(str(live.get("run_attempt", ""))) is None
        or live.get("collector_version") != "kofia-first-seen-capture/v2"
        or live.get("source_contract_version") != "kofia_liquidity_source/v3"
    ):
        fail("UNIT_CONTRACT_INVALID", "live evidence identity")

    comparisons = contract.get("comparisons")
    operations = capture.operation_map(liquidity.load_contract())
    if not isinstance(comparisons, list) or len(comparisons) != 2:
        fail("UNIT_CONTRACT_INVALID", "comparisons")
    identities = set()
    for item in comparisons:
        if not isinstance(item, dict) or set(item) != {
            "operation",
            "observation_date",
            "field",
            "guide_sample_raw",
            "relation",
            "divisor",
        }:
            fail("UNIT_CONTRACT_INVALID", "comparison schema")
        name = item.get("operation")
        if name not in operations:
            fail("UNIT_CONTRACT_INVALID", "comparison operation")
        field = item.get("field")
        if field != operations[name]["primary_value_field"]:
            fail("UNIT_CONTRACT_INVALID", "comparison primary field")
        try:
            liquidity.parse_observation_date(
                str(item.get("observation_date", "")).replace("-", ""), name
            )
        except liquidity.KofiaContractError:
            fail("UNIT_CONTRACT_INVALID", "comparison date")
        decimal_text(item.get("guide_sample_raw"), f"{name} guide sample")
        relation = item.get("relation")
        if relation not in RELATIONS:
            fail("UNIT_CONTRACT_INVALID", "comparison relation")
        if relation == "exact" and item.get("divisor") is not None:
            fail("UNIT_CONTRACT_INVALID", "exact divisor")
        if relation == "round_half_up_after_division":
            divisor = decimal_text(item.get("divisor"), f"{name} divisor")
            if divisor <= 1 or divisor != divisor.to_integral_value():
                fail("UNIT_CONTRACT_INVALID", "comparison divisor")
        identity = (name, item["observation_date"], field)
        if identity in identities:
            fail("UNIT_CONTRACT_INVALID", "duplicate comparison")
        identities.add(identity)

    if {item["relation"] for item in comparisons} != {
        "exact",
        "round_half_up_after_division",
    }:
        fail("UNIT_CONTRACT_INVALID", "conflict pair missing")
    if comparisons != EXPECTED_COMPARISONS:
        fail("UNIT_CONTRACT_INVALID", "comparison identity")
    if contract.get("qualification") != EXPECTED_QUALIFICATION:
        fail("UNIT_CONTRACT_INVALID", "qualification boundary")
    return contract


def load_live_rows(capture_dir: Path, contract: dict) -> dict[tuple[str, str], dict]:
    capture_dir = Path(capture_dir)
    expected = (ROOT / contract["live_evidence"]["capture_path"]).resolve()
    if capture_dir.resolve() != expected:
        fail("LIVE_EVIDENCE_PATH_MISMATCH", str(capture_dir))
    try:
        observation = capture.validate_capture(
            capture_dir,
            ROOT / "evidence" / "kofia" / "first_seen",
        )
        manifest = json.loads(
            (capture_dir / "_manifest.json").read_text(encoding="utf-8")
        )
    except (capture.CaptureError, OSError, json.JSONDecodeError) as exc:
        fail("LIVE_EVIDENCE_INVALID", str(exc))
    if observation.get("mode") != "full_coverage":
        fail("LIVE_EVIDENCE_INVALID", "mode")
    live = contract["live_evidence"]
    if (
        manifest.get("github")
        != {"run_id": live["run_id"], "run_attempt": live["run_attempt"]}
        or manifest.get("collector_version") != live["collector_version"]
        or manifest.get("source_contract_version")
        != live["source_contract_version"]
    ):
        fail("LIVE_EVIDENCE_IDENTITY_MISMATCH", capture_dir.name)

    wanted = {
        (item["operation"], item["observation_date"])
        for item in contract["comparisons"]
    }
    operations = capture.operation_map(liquidity.load_contract())
    found = {}
    for entry in manifest["raw_responses"]:
        name = entry["operation"]
        raw_path = capture_dir / entry["raw_file"]
        try:
            with gzip.open(raw_path, "rb") as stream:
                raw = stream.read()
        except (OSError, EOFError) as exc:
            fail("LIVE_EVIDENCE_INVALID", str(exc))
        page = capture.parse_page(raw, operations[name], entry["page_no"])
        for row in page["rows"]:
            identity = (name, row["observation_date"])
            if identity in wanted:
                if identity in found:
                    fail("LIVE_EVIDENCE_DUPLICATE", str(identity))
                found[identity] = row
    if set(found) != wanted:
        fail("LIVE_EVIDENCE_ROW_MISSING", str(sorted(wanted - set(found))))
    return found


def compare(contract: dict, live_rows: dict[tuple[str, str], dict]) -> list[dict]:
    results = []
    for item in contract["comparisons"]:
        identity = (item["operation"], item["observation_date"])
        row = live_rows.get(identity)
        if not isinstance(row, dict):
            fail("LIVE_EVIDENCE_ROW_MISSING", str(identity))
        try:
            live_raw = row["values"][item["field"]]
        except (KeyError, TypeError):
            fail("LIVE_EVIDENCE_FIELD_MISSING", str(identity))
        live_value = decimal_text(live_raw, f"{identity} live")
        guide_value = decimal_text(item["guide_sample_raw"], f"{identity} guide")
        relation = item["relation"]
        if relation == "exact":
            derived = live_value
        else:
            divisor = decimal_text(item["divisor"], f"{identity} divisor")
            derived = (live_value / divisor).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        if derived != guide_value:
            fail("EVIDENCE_RELATION_MISMATCH", str(identity))
        results.append(
            {
                "operation": item["operation"],
                "observation_date": item["observation_date"],
                "field": item["field"],
                "guide_sample_raw": item["guide_sample_raw"],
                "live_api_raw": live_raw,
                "relation": relation,
                "divisor": item["divisor"],
                "relation_verified": True,
            }
        )
    return results


def build_audit(capture_dir: Path, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    live_rows = load_live_rows(capture_dir, contract)
    comparisons = compare(contract, live_rows)
    relations = {item["relation"] for item in comparisons}
    if relations != {"exact", "round_half_up_after_division"}:
        fail("PRIMARY_EVIDENCE_CONFLICT_NOT_REPRODUCED", str(sorted(relations)))
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "official_guide": contract["official_guide"],
        "freesis_display": contract["freesis_display"],
        "live_evidence": contract["live_evidence"],
        "comparisons": comparisons,
        "conclusion": {
            **contract["qualification"],
            "reason_codes": [
                "OFFICIAL_GUIDE_CROSS_OPERATION_SAMPLE_SCALE_CONFLICT",
                "FREESIS_DISPLAY_UNIT_IS_NOT_API_TRANSPORT_CONTRACT",
            ],
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_audit(args.capture_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
