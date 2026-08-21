#!/usr/bin/env python3
"""P6-02 explicit hedge eligibility registry regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "hedge_instrument_eligibility.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("hedge_instrument_eligibility", SOURCE)
CONTRACT = MODULE.load_contract()


def observation(metric, marker):
    return {
        "metric": metric,
        "value": 12.5,
        "unit": "BPS",
        "as_of_date": "2026-08-20",
        "available_at": "2026-08-20T22:00:00Z",
        "source_ref": f"test://evidence/{metric}",
        "source_sha256": marker * 64,
    }


def record(
    instrument_id="US:ARCX:TESTHEDGE",
    market="US",
    scope="INDEX",
    exposure="US:BROAD_MARKET",
    eligible=True,
    start="2026-08-20",
    end=None,
    marker="a",
):
    return {
        "instrument_id": instrument_id,
        "market": market,
        "venue": "ARCX" if market == "US" else "KRX",
        "symbol": instrument_id.rsplit(":", 1)[-1],
        "currency": "USD" if market == "US" else "KRW",
        "instrument_type": "ETF",
        "hedge_scope": scope,
        "hedged_exposure_id": exposure,
        "eligible": eligible,
        "valid_from": start,
        "valid_to": end,
        "cost_evidence": observation("TOTAL_COST", marker),
        "tracking_error_evidence": observation("TRACKING_ERROR", "b"),
        "decision_reasons": ["CIO_EXPLICIT_DECISION"],
        "decision_basis_ref": f"test://decision/{instrument_id}/{start}",
        "decision_basis_sha256": "c" * 64,
        "restrictions": [] if eligible else ["NOT_ELIGIBLE"],
    }


def registry(records=None):
    value = {
        "schema_version": "hedge_instrument_registry/1",
        "contract_version": "hedge_instrument_eligibility/1",
        "registry_id": "TEST-HEDGE-REGISTRY-2026-08-20",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "valid_from": "2026-08-20",
        "valid_to": None,
        "records": [record()] if records is None else records,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["records"] = sorted(
        normalized["records"],
        key=lambda row: (row["instrument_id"], row["valid_from"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class HedgeInstrumentEligibilityTests(unittest.TestCase):
    def test_contract_has_no_default_selection_sizing_or_order_authority(self):
        self.assertEqual(CONTRACT["approval_mode"], "EXPLICIT_CIO_RATIFIED_ONLY")
        self.assertEqual(
            CONTRACT["output_schema_version"],
            "hedge_instrument_eligibility_packet/2",
        )
        self.assertEqual(
            CONTRACT["repository_default_status"],
            "BLOCKED_UNTIL_EXTERNAL_REGISTRY_RATIFIED",
        )
        self.assertTrue(CONTRACT["authority"]["eligibility_registry_validation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "eligibility_registry_validation_only":
                self.assertFalse(value, key)

    def test_ratified_index_and_sector_records_are_reproduced_not_selected(self):
        rows = [
            record(),
            record(
                instrument_id="KOREA:KRX:TESTSECTOR",
                market="KOREA",
                scope="SECTOR",
                exposure="KOREA:SEMICONDUCTORS",
                eligible=False,
                marker="d",
            ),
        ]
        packet = MODULE.build_packet(registry(rows), "2026-08-21", CONTRACT)
        self.assertEqual(packet["status"], "ELIGIBILITY_REGISTRY_VALIDATED")
        self.assertEqual(packet["summary"], {
            "active_count": 2,
            "eligible_count": 1,
            "ineligible_count": 1,
            "by_scope": {"INDEX": 1, "SECTOR": 1},
        })
        self.assertEqual(packet["eligible_instruments"], ["US:ARCX:TESTHEDGE"])
        self.assertIsNone(packet["selected_instrument"])
        self.assertIsNone(packet["hedge_size"])
        self.assertEqual(packet["order_intents"], [])

    def test_effective_history_selects_exact_date_without_rewriting_history(self):
        rows = [
            record(eligible=False, end="2026-08-21"),
            record(eligible=True, start="2026-08-21", marker="d"),
        ]
        value = registry(rows)
        packet = MODULE.build_packet(value, "2026-08-21", CONTRACT)
        self.assertEqual(packet["summary"]["active_count"], 1)
        self.assertTrue(packet["active_records"][0]["eligible"])

        permuted = copy.deepcopy(value)
        permuted["records"].reverse()
        self.assertEqual(
            MODULE.canonical_json(packet),
            MODULE.canonical_json(MODULE.build_packet(permuted, "2026-08-21", CONTRACT)),
        )

    def test_overlapping_or_identity_drifting_records_fail_closed(self):
        overlap = [
            record(end="2026-08-22"),
            record(start="2026-08-21", marker="d"),
        ]
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "INSTRUMENT_INTERVAL_OVERLAP",
        ):
            MODULE.build_packet(registry(overlap), "2026-08-21", CONTRACT)

        drift = [record(end="2026-08-21"), record(start="2026-08-21")]
        drift[1]["venue"] = "XNAS"
        value = registry(drift)
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "INSTRUMENT_IDENTITY_DRIFT",
        ):
            MODULE.build_packet(value, "2026-08-21", CONTRACT)

    def test_cost_tracking_error_and_decision_lineage_are_mandatory(self):
        cases = []
        missing_cost = record()
        missing_cost.pop("cost_evidence")
        cases.append((missing_cost, "INSTRUMENT_FIELDS_MISMATCH"))
        bad_tracking = record()
        bad_tracking["tracking_error_evidence"]["source_sha256"] = "bad"
        cases.append((bad_tracking, "EVIDENCE_SHA_INVALID"))
        bad_basis = record()
        bad_basis["decision_basis_sha256"] = None
        cases.append((bad_basis, "DECISION_BASIS_SHA_INVALID"))
        for row, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.HedgeEligibilityError, error
            ):
                MODULE.build_packet(registry([row]), "2026-08-21", CONTRACT)

    def test_invalid_scope_market_approval_or_authority_fails_closed(self):
        cases = []
        bad_scope = record()
        bad_scope["hedge_scope"] = "SINGLE_NAME"
        cases.append((registry([bad_scope]), "HEDGE_SCOPE_INVALID"))
        bad_market = record()
        bad_market["market"] = "CRYPTO"
        cases.append((registry([bad_market]), "MARKET_INVALID"))
        approval = registry()
        approval["ratified_by"] = "SYSTEM"
        cases.append((approval, "REGISTRY_IDENTITY_INVALID"))
        authority = registry()
        authority["authority"]["order_authorized"] = True
        cases.append((authority, "REGISTRY_IDENTITY_INVALID"))
        for value, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.HedgeEligibilityError, error
            ):
                MODULE.build_packet(value, "2026-08-21", CONTRACT)

    def test_registry_packet_is_hash_bound_and_output_is_deterministic(self):
        value = registry()
        first = MODULE.build_packet(value, "2026-08-21", CONTRACT)
        second = MODULE.build_packet(value, "2026-08-21", CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(
            first["lineage"]["registry_packet_sha256"],
            value["packet_sha256"],
        )
        self.assertEqual(first["source_packets"]["REGISTRY"], value)
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

        tampered = registry()
        tampered["records"][0]["eligible"] = False
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "REGISTRY_PACKET_SHA_MISMATCH",
        ):
            MODULE.build_packet(tampered, "2026-08-21", CONTRACT)

    def test_self_rehashed_output_semantic_tamper_fails_closed(self):
        packet = MODULE.build_packet(registry(), "2026-08-21", CONTRACT)
        packet["summary"]["active_count"] += 1
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_unratified_instrument_cannot_be_injected_into_self_rehashed_output(self):
        packet = MODULE.build_packet(registry(), "2026-08-21", CONTRACT)
        fake = record(instrument_id="US:ARCX:FAKEHEDGE", marker="f")
        packet["active_records"] = sorted(
            packet["active_records"] + [fake],
            key=lambda row: (row["instrument_id"], row["valid_from"]),
        )
        packet["eligible_instruments"] = sorted(
            row["instrument_id"] for row in packet["active_records"] if row["eligible"]
        )
        packet["summary"] = {
            "active_count": 2,
            "eligible_count": 2,
            "ineligible_count": 0,
            "by_scope": {"INDEX": 2, "SECTOR": 0},
        }
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_embedded_registry_is_fully_revalidated(self):
        packet = MODULE.build_packet(registry(), "2026-08-21", CONTRACT)
        packet["source_packets"]["REGISTRY"]["status"] = "DRAFT"
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "REGISTRY_IDENTITY_INVALID",
        ):
            MODULE.validate_packet(packet, CONTRACT)

        packet = MODULE.build_packet(registry(), "2026-08-21", CONTRACT)
        packet["source_packets"]["REGISTRY"]["records"][0]["eligible"] = False
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.HedgeEligibilityError,
            "REGISTRY_PACKET_SHA_MISMATCH",
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source = write_json(tmp / "registry.json", registry())
            output = tmp / "nested" / "eligibility.json"
            self.assertEqual(MODULE.run(source, "2026-08-21", output), 0)
            self.assertEqual(
                json.loads(output.read_text())["eligible_instruments"],
                ["US:ARCX:TESTHEDGE"],
            )
            forbidden = ROOT / "data" / "hedge_eligibility_test.json"
            self.assertEqual(MODULE.run(source, "2026-08-21", forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
