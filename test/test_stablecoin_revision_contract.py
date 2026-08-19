#!/usr/bin/env python3
"""P1-CR-02 endpoint-aware Stablecoin revision contract regression.

No live DefiLlama calls are made.  Synthetic snapshots are written only under
temporary directories; committed evidence is read-only regression material.
"""

import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "stablecoin_revision_contract.py"
CONTRACT_PATH = ROOT / "config" / "stablecoin_endpoint_contract.json"
WORKFLOW = ROOT / ".github" / "workflows" / "stablecoin-capture.yml"
EVIDENCE = ROOT / "evidence" / "stablecoin" / "raw"
SPEC = importlib.util.spec_from_file_location(
    "stablecoin_revision_contract",
    SCRIPT,
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract(CONTRACT_PATH)


def chart_rows():
    return [
        {
            "date": str(1700000000 + index * 172800),
            "totalCirculating": {"peggedUSD": 1000 + index},
            "totalCirculatingUSD": {"peggedUSD": 1000 + index},
        }
        for index in range(100)
    ]


def payloads():
    return {
        "stablecoincharts_all": chart_rows(),
        "stablecoincharts_Terra": chart_rows(),
        "stablecoinchains": [
            {
                "name": f"chain-{index:03d}",
                "totalCirculatingUSD": {"peggedUSD": index},
            }
            for index in range(100)
        ],
        "stablecoins_withprices": {
            "chains": [f"chain-{index:03d}" for index in range(100)],
            "peggedAssets": [
                {
                    "id": str(index),
                    "name": f"asset-{index:03d}",
                    "pegType": "peggedUSD",
                    "circulating": {"peggedUSD": index},
                }
                for index in range(100)
            ],
        },
    }


def write_snapshot(
    raw_root,
    date,
    data=None,
    manifest=True,
    collector_version="stablecoin-capture/v2",
):
    data = payloads() if data is None else data
    snapshot_dir = Path(raw_root) / date
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "_downloaded_at.txt").write_text(
        f"{date}T06:20:00Z\n",
        encoding="utf-8",
    )

    checksums = []
    for endpoint in CONTRACT["endpoints"]:
        raw = json.dumps(
            data[endpoint["name"]],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        raw_name = Path(endpoint["raw_file"]).name.removesuffix(".gz")
        checksums.append(f"{hashlib.sha256(raw).hexdigest()}  {raw_name}")
        with gzip.open(snapshot_dir / endpoint["raw_file"], "wb") as stream:
            stream.write(raw)
    (snapshot_dir / "_sha256.txt").write_text(
        "\n".join(checksums) + "\n",
        encoding="utf-8",
    )
    if manifest:
        MODULE.build_manifest(
            snapshot_dir,
            collector_version,
            contract=CONTRACT,
        )
    return snapshot_dir


class StablecoinRevisionContractTest(unittest.TestCase):
    def test_contract_assigns_explicit_endpoint_semantics(self):
        semantics = {
            endpoint["name"]: endpoint["semantics"]
            for endpoint in CONTRACT["endpoints"]
        }

        self.assertEqual(
            semantics,
            {
                "stablecoincharts_all": "historical_series",
                "stablecoincharts_Terra": "historical_series",
                "stablecoinchains": "live_snapshot",
                "stablecoins_withprices": "live_snapshot",
            },
        )
        self.assertEqual(CONTRACT["pit_coverage_start"], "2026-08-17")

    def test_committed_snapshots_validate_without_mutation(self):
        tracked_before = {
            path: path.read_bytes()
            for path in EVIDENCE.rglob("*")
            if path.is_file()
        }

        first = MODULE.validate_snapshot(EVIDENCE / "2026-08-17")
        second = MODULE.validate_snapshot(EVIDENCE / "2026-08-18")

        self.assertEqual(first["metadata_status"], "legacy_pre_manifest")
        self.assertEqual(second["metadata_status"], "legacy_pre_manifest")
        self.assertTrue(
            all(
                item["record_count"] >= 100
                for item in first["endpoints"] + second["endpoints"]
            )
        )
        self.assertEqual(
            tracked_before,
            {
                path: path.read_bytes()
                for path in EVIDENCE.rglob("*")
                if path.is_file()
            },
        )

    def test_committed_drift_is_classified_by_endpoint_type(self):
        report = MODULE.compare_snapshots(
            EVIDENCE / "2026-08-17",
            EVIDENCE / "2026-08-18",
        )
        all_chart = report["endpoints"]["stablecoincharts_all"]
        terra = report["endpoints"]["stablecoincharts_Terra"]

        self.assertEqual(all_chart["counts"]["revised"], 1)
        self.assertEqual(all_chart["counts"]["appended"], 1)
        self.assertEqual(terra["counts"]["removed"], 1)
        self.assertEqual(terra["counts"]["appended"], 1)
        for name in ("stablecoinchains", "stablecoins_withprices"):
            result = report["endpoints"][name]
            self.assertEqual(result["event"], "snapshot_changed")
            self.assertEqual(result["revision_inference"], "not_applicable")
            self.assertNotIn("revised", result["counts"])

    def test_manifest_carries_required_provenance_and_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp, "2026-08-20")
            manifest = json.loads(
                (snapshot / "_manifest.json").read_text(encoding="utf-8")
            )

            self.assertEqual(
                MODULE.validate_snapshot(snapshot)["metadata_status"],
                "complete",
            )
            for endpoint in manifest["endpoints"]:
                self.assertEqual(
                    set(endpoint),
                    {
                        "name",
                        "endpoint",
                        "semantics",
                        "raw_file",
                        "fetched_at_utc",
                        "response_sha256",
                        "byte_length",
                        "collector_version",
                    },
                )
            with self.assertRaisesRegex(
                MODULE.ContractError,
                "APPEND_ONLY_VIOLATION",
            ):
                MODULE.build_manifest(snapshot, "stablecoin-capture/v2")

    def test_manifest_is_required_from_effective_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp, "2026-08-20", manifest=False)

            with self.assertRaisesRegex(
                MODULE.ContractError,
                "MANIFEST_REQUIRED",
            ):
                MODULE.validate_snapshot(snapshot)

    def test_historical_events_split_revision_reindex_backfill_append(self):
        with tempfile.TemporaryDirectory() as tmp:
            before = payloads()
            after = payloads()
            rows = after["stablecoincharts_all"]
            rows[0]["totalCirculating"]["peggedUSD"] = 999999
            rows.pop(2)
            rows.append(
                {
                    "date": str(1700000000 + 86400),
                    "totalCirculating": {"peggedUSD": 77},
                    "totalCirculatingUSD": {"peggedUSD": 77},
                }
            )
            rows.append(
                {
                    "date": str(1700000000 + 100 * 172800),
                    "totalCirculating": {"peggedUSD": 88},
                    "totalCirculatingUSD": {"peggedUSD": 88},
                }
            )
            after["stablecoinchains"][0]["totalCirculatingUSD"] = {
                "peggedUSD": 555
            }
            old_dir = write_snapshot(tmp, "2026-08-20", before)
            new_dir = write_snapshot(tmp, "2026-08-21", after)

            report = MODULE.compare_snapshots(old_dir, new_dir)
            chart = report["endpoints"]["stablecoincharts_all"]

            self.assertEqual(
                chart["events"],
                [
                    "historical_revision",
                    "historical_reindex",
                    "historical_backfill",
                    "forward_append",
                ],
            )
            self.assertEqual(
                report["endpoints"]["stablecoinchains"][
                    "revision_inference"
                ],
                "not_applicable",
            )

    def test_missing_vintage_and_corrupt_bytes_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "2026-08-20"
            with self.assertRaisesRegex(
                MODULE.ContractError,
                "NO_VINTAGE_MECHANISM",
            ):
                MODULE.validate_snapshot(missing)

            snapshot = write_snapshot(tmp, "2026-08-21")
            target = snapshot / "stablecoinchains.json.gz"
            with gzip.open(target, "wb") as stream:
                stream.write(b"[]")
            with self.assertRaisesRegex(
                MODULE.ContractError,
                "CHECKSUM_MISMATCH",
            ):
                MODULE.validate_snapshot(snapshot)

    def test_workflow_builds_and_validates_manifest_before_commit(self):
        with WORKFLOW.open(encoding="utf-8") as stream:
            workflow = yaml.safe_load(stream)
        steps = workflow["jobs"]["capture"]["steps"]
        capture = next(
            step
            for step in steps
            if step.get("name") == "Capture raw snapshot (append-only)"
        )
        commit_index = next(
            index for index, step in enumerate(steps) if step.get("name") == "Commit"
        )
        capture_index = steps.index(capture)
        script = capture["run"]

        self.assertLess(capture_index, commit_index)
        self.assertIn("stablecoin_revision_contract.py\" manifest", script)
        self.assertIn("--collector-version \"stablecoin-capture/v2\"", script)
        self.assertIn("stablecoin_revision_contract.py\" validate", script)
        commit = steps[commit_index]
        self.assertEqual(commit.get("if"), "always()")
        self.assertIn(
            "data/operations/stablecoin_capture_runs",
            commit.get("run", ""),
        )


if __name__ == "__main__":
    unittest.main()
