#!/usr/bin/env python3
"""P3-12 upbit_universe_populate.py scheduled wiring regression.

This script had zero direct test coverage before this file: the only thing
that ever exercised it end-to-end was the real scheduled workflow, which is
exactly how a real-world crash (a BTC-quoted market -- "BTC-0G" -- reaching
identity proposal building and raising MARKET_CODE_INVALID, which aborted
classification for every market in the snapshot) went undetected by the
approved regression suite. universe/upbit_tradeable_universe.py now excludes
non-KRW-quoted markets at the source (see
test_non_krw_quoted_market_in_raw_market_all_is_excluded_not_crashed in
test_upbit_tradeable_universe.py); this file proves the full
rebuild()/populate() entry point stays crash-free end to end, both normally
and with the exact incident scenario reproduced.
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "upbit_universe_populate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "upbit-universe-capture.yml"
P4_WORKFLOW = ROOT / ".github" / "workflows" / "upbit-microstructure-capture.yml"
P9_WORKFLOW = ROOT / ".github" / "workflows" / "upbit-realtime-capture.yml"

SPEC = importlib.util.spec_from_file_location("upbit_universe_populate", SCRIPT)
POPULATE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(POPULATE)

UNI = POPULATE.UNI
CAP = UNI.UPBIT_CAPTURE


def _capture(raw_root: Path, markets: list[str]):
    from test_upbit_market_capture import build_fetcher  # local sibling test module

    contract = CAP.load_contract()
    fetcher = build_fetcher(contract, markets)
    clock = lambda: dt.datetime(2026, 8, 28, 0, 40, 0, tzinfo=dt.timezone.utc)
    return CAP.capture_snapshot(
        raw_root, snapshot_date=dt.date(2026, 8, 28), contract=contract, fetcher=fetcher,
        sleeper=lambda s: None, clock=clock,
    )


def _inject_non_krw_market(target: Path, contract: dict, market: str):
    raw = json.loads(gzip.open(target / contract["market_all_raw_file"], "rb").read())
    raw.append({"market": market, "korean_name": "테스트", "english_name": "Test"})
    new_raw_bytes = json.dumps(raw).encode()
    (target / contract["market_all_raw_file"]).write_bytes(gzip.compress(new_raw_bytes))
    manifest_path = target / "_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["checksums"][contract["market_all_raw_file"]] = hashlib.sha256(new_raw_bytes).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def _exact_release_successor(existing: dict) -> dict:
    """Build the expected same-raw exact-eight successor without config edits.

    The production path always uses ``rebuild``.  This helper is only a
    focused populate-boundary fixture: exact-chain PASS/FAIL is controlled
    separately and the eight content identities come from the real retained
    registry, never from an invented test list.
    """
    successor = copy.deepcopy(existing)
    mappings = UNI.load_identity_registry()["mappings"]
    released = sorted(mappings)
    if len(released) != 8:
        raise AssertionError("fixture requires the retained exact-eight registry")

    successor["ratification"]["effective_for_snapshot"] = True
    successor["ratification"]["identity_registry"]["mapping_count"] = 8
    successor["authority"]["observation_pool_population_only"] = False
    packet = successor["packet"]
    packet["policy_ratified"] = True
    packet["taxonomy_ratified"] = True
    for row in packet["markets"]:
        market = row["market"]
        if market in mappings:
            row["state"] = UNI.STATE_PAPER_ELIGIBLE
            row["reason"] = "PAPER_ELIGIBLE_ALL_GATES_PASSED"
            row["candidate_canonical_asset_id"] = mappings[market]
    packet["summary"] = {
        "market_count": len(packet["markets"]),
        "observation_pool_count": len(packet["markets"]) - 8,
        "tradeable_universe_count": 0,
        "paper_eligible_count": 8,
        "blocked_count": 0,
    }
    packet["payload_sha256"] = UNI.payload_sha256(
        {key: value for key, value in packet.items() if key != "payload_sha256"}
    )
    successor["payload_sha256"] = POPULATE.payload_sha256(
        {key: value for key, value in successor.items() if key != "payload_sha256"}
    )
    return successor


def _transition_projection(successor: dict) -> dict:
    manifest = {
        "schema_version": "upbit_universe_same_vintage_transition/1",
        "snapshot_date": successor["snapshot_date"],
        "authority": {"order_authorized": False},
    }
    manifest["payload_sha256"] = POPULATE.payload_sha256(manifest)
    return {
        "manifest": manifest,
        "successor_record": copy.deepcopy(successor),
        "successor_bytes": POPULATE.RELEASE.formatted_json_bytes(successor),
    }


class UpbitUniversePopulateTests(unittest.TestCase):
    def test_pinned_rebuild_preserves_historical_state_and_uses_current_config_pins(self):
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-09-01" / "packet.json").read_text(encoding="utf-8")
        )
        rebuilt = UNI.rebuild_same_vintage_population_record(
            source,
            evaluation_as_of="2026-09-01",
            repo_root=ROOT,
        )
        # The 2026-09-02 code approval is never backdated into this
        # 2026-09-01 decision: the complete classification and authority
        # state remain identical and fail closed.
        self.assertEqual(rebuilt["packet"], source["packet"])
        self.assertEqual(rebuilt["authority"], source["authority"])
        self.assertFalse(rebuilt["ratification"]["effective_for_snapshot"])
        # A rebuild records the exact current configuration bytes, while
        # the immutable retained source keeps its original pre-approval
        # pins and payload hash.
        self.assertNotEqual(rebuilt["payload_sha256"], source["payload_sha256"])
        self.assertEqual(
            rebuilt["ratification"]["identity_registry"]["file_sha256"],
            UNI._file_sha(ROOT / "config" / "upbit_asset_identity_registry.json"),
        )
        self.assertEqual(
            rebuilt["ratification"]["taxonomy"]["file_sha256"],
            UNI._file_sha(ROOT / "config" / "upbit_exclusion_taxonomy.json"),
        )

    def test_rebuild_normal_krw_only_snapshot_populates_cleanly(self):
        with tempfile.TemporaryDirectory() as raw:
            raw_root = Path(raw)
            _capture(raw_root, ["KRW-BTC", "KRW-ETH"])
            record = POPULATE.rebuild("2026-08-28", raw_root)
            self.assertEqual(record["identity_review"]["proposal_count"], 2)
            self.assertEqual(
                {row["market"] for row in record["packet"]["markets"]}, {"KRW-BTC", "KRW-ETH"}
            )
            self.assertTrue(record["authority"]["observation_pool_population_only"])
            self.assertTrue(all(
                value is False for key, value in record["authority"].items()
                if key != "observation_pool_population_only"
            ))

    def test_rebuild_survives_a_btc_quoted_market_in_the_raw_snapshot(self):
        # Exact real-incident reproduction: GET /v1/market/all legitimately
        # returns non-KRW-quoted pairs; before the fix this crashed the
        # entire scheduled run and no packet was ever committed.
        with tempfile.TemporaryDirectory() as raw:
            raw_root = Path(raw)
            target = _capture(raw_root, ["KRW-BTC"])
            contract = CAP.load_contract()
            _inject_non_krw_market(target, contract, "BTC-0G")

            record = POPULATE.rebuild("2026-08-28", raw_root)  # must not raise

            market_codes = {row["market"] for row in record["packet"]["markets"]}
            self.assertEqual(market_codes, {"KRW-BTC"})
            self.assertEqual(record["identity_review"]["proposal_count"], 1)
            self.assertNotIn("BTC-0G", market_codes)

    def test_populate_writes_and_is_idempotent_on_rerun(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            _capture(raw_root, ["KRW-BTC"])
            first = POPULATE.populate("2026-08-28", raw_root, data_root)
            self.assertEqual(first["outcome"], "populated")
            second = POPULATE.populate("2026-08-28", raw_root, data_root)
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_policy_true_taxonomy_false_gets_append_only_exact_release_transition(self):
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-09-01" / "packet.json").read_text(encoding="utf-8")
        )
        successor = _exact_release_successor(source)

        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            canonical = POPULATE.output_path("2026-09-01", data_root)
            canonical.parent.mkdir(parents=True)
            original_bytes = json.dumps(source, indent=2, sort_keys=True) + "\n"
            canonical.write_text(original_bytes, encoding="utf-8")

            with (
                mock.patch.object(POPULATE, "rebuild", return_value=successor),
                mock.patch.object(POPULATE, "ROOT", data_root),
                mock.patch.object(
                    POPULATE.RELEASE,
                    "build_same_vintage_transition_projection",
                    return_value=_transition_projection(successor),
                ) as build_transition,
            ):
                first = POPULATE.populate("2026-09-01", data_root=data_root)
                second = POPULATE.populate("2026-09-01", data_root=data_root)

            self.assertEqual(first["outcome"], "transition_populated")
            self.assertEqual(second["outcome"], "verified_existing_transition")
            self.assertEqual(
                first["reason"],
                "POLICY_RATIFIED_TAXONOMY_UNRATIFIED_TO_EXACT_RELEASE_SAME_RAW_VINTAGE",
            )
            self.assertEqual(canonical.read_text(encoding="utf-8"), original_bytes)
            self.assertNotEqual(Path(first["path"]), canonical)
            self.assertEqual(
                Path(first["path"]),
                POPULATE.transition_output_path(
                    "2026-09-01",
                    source_payload_sha256=source["payload_sha256"],
                    successor_payload_sha256=successor["payload_sha256"],
                    data_root=data_root,
                ),
            )
            self.assertEqual(
                json.loads(Path(first["path"]).read_text(encoding="utf-8")),
                successor,
            )
            self.assertEqual(build_transition.call_count, 2)
            self.assertEqual(
                build_transition.call_args.kwargs["evaluation_as_of"],
                "2026-09-01",
            )
            self.assertTrue(all(value is False for value in successor["authority"].values()))
            self.assertTrue(all(value is False for value in successor["packet"]["authority"].values()))
            self.assertTrue(all(
                value is False
                for row in successor["packet"]["markets"]
                for value in row["authority"].values()
            ))

    def test_transition_requires_both_registry_and_taxonomy_exact_release_pass(self):
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-09-01" / "packet.json").read_text(encoding="utf-8")
        )
        successor = _exact_release_successor(source)
        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            canonical = POPULATE.output_path("2026-09-01", data_root)
            canonical.parent.mkdir(parents=True)
            canonical.write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with (
                mock.patch.object(POPULATE, "rebuild", return_value=successor),
                mock.patch.object(POPULATE, "ROOT", data_root),
                mock.patch.object(
                    POPULATE.RELEASE,
                    "build_same_vintage_transition_projection",
                    side_effect=POPULATE.RELEASE.ReleaseProjectionError(
                        "TRANSITION_TAXONOMY_EXACT_RELEASE_FAILED"
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    POPULATE.PopulationError,
                    "EXISTING_PACKET_DRIFT_OR_TAMPER:2026-09-01",
                ):
                    POPULATE.populate("2026-09-01", data_root=data_root)
            self.assertEqual(json.loads(canonical.read_text(encoding="utf-8")), source)
            self.assertFalse((canonical.parent / POPULATE.TRANSITION_DIRECTORY).exists())

    def test_transition_writer_rejects_sibling_and_raw_byte_reformat_drift(self):
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-09-01" / "packet.json").read_text(encoding="utf-8")
        )
        successor = _exact_release_successor(source)

        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            canonical = POPULATE.output_path("2026-09-01", data_root)
            canonical.parent.mkdir(parents=True)
            canonical.write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            sibling = canonical.parent / POPULATE.TRANSITION_DIRECTORY / "different-successor"
            sibling.mkdir(parents=True)
            with (
                mock.patch.object(POPULATE, "rebuild", return_value=successor),
                mock.patch.object(POPULATE, "ROOT", data_root),
                mock.patch.object(
                    POPULATE.RELEASE,
                    "build_same_vintage_transition_projection",
                    return_value=_transition_projection(successor),
                ),
            ):
                with self.assertRaisesRegex(POPULATE.PopulationError, "SIBLING_TRANSITION_FORBIDDEN"):
                    POPULATE.populate("2026-09-01", data_root=data_root)

        for drift_target in ("packet.json", "transition.json"):
            with self.subTest(drift_target=drift_target), tempfile.TemporaryDirectory() as data:
                data_root = Path(data)
                canonical = POPULATE.output_path("2026-09-01", data_root)
                canonical.parent.mkdir(parents=True)
                canonical.write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                with (
                    mock.patch.object(POPULATE, "rebuild", return_value=successor),
                    mock.patch.object(POPULATE, "ROOT", data_root),
                    mock.patch.object(
                        POPULATE.RELEASE,
                        "build_same_vintage_transition_projection",
                        return_value=_transition_projection(successor),
                    ),
                ):
                    first = POPULATE.populate("2026-09-01", data_root=data_root)
                    drift_path = Path(first["path"]).with_name(drift_target)
                    same_object = json.loads(drift_path.read_text(encoding="utf-8"))
                    drift_path.write_text(
                        json.dumps(same_object, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(
                        POPULATE.PopulationError,
                        "EXISTING_TRANSITION_DRIFT_OR_TAMPER",
                    ):
                        POPULATE.populate("2026-09-01", data_root=data_root)

    def test_transition_rejects_every_old_ratification_shape_except_true_false(self):
        # The former in-place false/false reclassification and every other
        # source shape are intentionally retired: canonical bytes remain
        # immutable and the pinned release builder fails closed.
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-09-01" / "packet.json").read_text(encoding="utf-8")
        )
        successor = _exact_release_successor(source)
        for policy_ratified, taxonomy_ratified in ((False, False), (False, True), (True, True)):
            with self.subTest(policy=policy_ratified, taxonomy=taxonomy_ratified):
                old = copy.deepcopy(source)
                old["packet"]["policy_ratified"] = policy_ratified
                old["packet"]["taxonomy_ratified"] = taxonomy_ratified
                old["payload_sha256"] = POPULATE.payload_sha256(
                    {key: value for key, value in old.items() if key != "payload_sha256"}
                )
                with tempfile.TemporaryDirectory() as data:
                    data_root = Path(data)
                    canonical = POPULATE.output_path("2026-09-01", data_root)
                    canonical.parent.mkdir(parents=True)
                    canonical.write_text(json.dumps(old, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                    with (
                        mock.patch.object(POPULATE, "rebuild", return_value=successor),
                        mock.patch.object(POPULATE, "ROOT", data_root),
                        mock.patch.object(
                            POPULATE.RELEASE,
                            "build_same_vintage_transition_projection",
                            side_effect=POPULATE.RELEASE.ReleaseProjectionError(
                                "TRANSITION_SOURCE_STATE_NOT_POLICY_TRUE_TAXONOMY_FALSE"
                            ),
                        ),
                    ):
                        with self.assertRaises(POPULATE.PopulationError):
                            POPULATE.populate("2026-09-01", data_root=data_root)
                    self.assertEqual(json.loads(canonical.read_text(encoding="utf-8")), old)

    def test_known_frozen_true_true_packet_is_never_overwritten_or_transitioned(self):
        # The old _safe_frozen_exact_hash_transition() overwrite exception
        # is deliberately removed. Historical eight-market true/true bytes
        # remain audit evidence and cannot enter the new true/false-only
        # append-only transition path.
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-08-30" / "packet.json").read_text(encoding="utf-8")
        )
        self.assertTrue(source["packet"]["policy_ratified"])
        self.assertTrue(source["packet"]["taxonomy_ratified"])
        self.assertEqual(source["packet"]["summary"]["paper_eligible_count"], 8)
        successor = copy.deepcopy(source)
        successor["ratification"]["identity_registry"]["file_sha256"] = "0" * 64
        successor["payload_sha256"] = POPULATE.payload_sha256(
            {key: value for key, value in successor.items() if key != "payload_sha256"}
        )

        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            canonical = POPULATE.output_path("2026-08-30", data_root)
            canonical.parent.mkdir(parents=True)
            original_bytes = json.dumps(source, indent=2, sort_keys=True) + "\n"
            canonical.write_text(original_bytes, encoding="utf-8")
            with (
                mock.patch.object(POPULATE, "rebuild", return_value=successor),
                mock.patch.object(POPULATE, "ROOT", data_root),
                mock.patch.object(
                    POPULATE.RELEASE,
                    "build_same_vintage_transition_projection",
                    side_effect=POPULATE.RELEASE.ReleaseProjectionError(
                        "TRANSITION_SOURCE_STATE_NOT_POLICY_TRUE_TAXONOMY_FALSE"
                    ),
                ),
            ):
                with self.assertRaises(POPULATE.PopulationError):
                    POPULATE.populate("2026-08-30", data_root=data_root)
            self.assertEqual(canonical.read_text(encoding="utf-8"), original_bytes)
            self.assertFalse((canonical.parent / POPULATE.TRANSITION_DIRECTORY).exists())

    def test_transition_rejects_authority_or_exact_eight_content_drift(self):
        source = json.loads(
            (ROOT / "data" / "observations" / "upbit_tradeable_universe"
             / "2026-09-01" / "packet.json").read_text(encoding="utf-8")
        )
        for mutate in (
            lambda record: record["packet"]["authority"].__setitem__("order_authorized", True),
            lambda record: next(
                row for row in record["packet"]["markets"]
                if row["state"] == UNI.STATE_PAPER_ELIGIBLE
            ).__setitem__("state", UNI.STATE_OBSERVATION_POOL),
        ):
            successor = _exact_release_successor(source)
            mutate(successor)
            successor["packet"]["payload_sha256"] = UNI.payload_sha256(
                {key: value for key, value in successor["packet"].items() if key != "payload_sha256"}
            )
            successor["payload_sha256"] = POPULATE.payload_sha256(
                {key: value for key, value in successor.items() if key != "payload_sha256"}
            )
            with tempfile.TemporaryDirectory() as data:
                data_root = Path(data)
                canonical = POPULATE.output_path("2026-09-01", data_root)
                canonical.parent.mkdir(parents=True)
                canonical.write_text(json.dumps(source, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                with (
                    mock.patch.object(POPULATE, "rebuild", return_value=successor),
                    mock.patch.object(POPULATE, "ROOT", data_root),
                    mock.patch.object(
                        POPULATE.RELEASE,
                        "build_same_vintage_transition_projection",
                        side_effect=POPULATE.RELEASE.ReleaseProjectionError(
                            "TRANSITION_SUCCESSOR_AUTHORITY_OR_CONTENT_INVALID"
                        ),
                    ),
                ):
                    with self.assertRaises(POPULATE.PopulationError):
                        POPULATE.populate("2026-09-01", data_root=data_root)
                self.assertEqual(json.loads(canonical.read_text(encoding="utf-8")), source)

    def test_rebuild_never_falls_back_to_previous_day_or_validation_capture(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as data:
            raw_root, data_root = Path(raw), Path(data)
            _capture(raw_root, ["KRW-BTC"])
            # _capture writes 2026-08-28. A neighboring vintage and any
            # separately scoped validation evidence must never satisfy a
            # request for 2026-08-29.
            (raw_root / "validation_capture" / "2026-08-29").mkdir(parents=True)
            with self.assertRaisesRegex(
                POPULATE.PopulationError,
                "RAW_SNAPSHOT_MISSING:2026-08-29",
            ):
                POPULATE.populate("2026-08-29", raw_root=raw_root, data_root=data_root)
            self.assertFalse(POPULATE.output_path("2026-08-29", data_root).exists())


class WorkflowTransactionTests(unittest.TestCase):
    def test_raw_is_committed_before_derived_outputs_and_telemetry(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        raw_commit = text.index("Commit immutable raw snapshot before P3 classification")
        classification = text.index("P3-12 classification (reads raw snapshot only")
        telemetry = text.index("Record P3-12 Upbit universe scheduler telemetry")
        derived_commit = text.index("Commit P3-12 tradeable-universe classification")
        self.assertLess(raw_commit, classification)
        self.assertLess(classification, telemetry)
        self.assertLess(telemetry, derived_commit)

        raw_block = text[raw_commit:classification]
        self.assertIn('git add "evidence/crypto/upbit/raw/$SNAPSHOT_DATE"', raw_block)
        self.assertNotIn("data/observations/upbit_tradeable_universe", raw_block)
        self.assertNotIn("data/operations/upbit_universe_capture_runs", raw_block)

        derived_block = text[derived_commit:]
        self.assertIn("data/operations/upbit_universe_capture_runs", derived_block)
        self.assertIn("data/observations/upbit_tradeable_universe", derived_block)
        self.assertIn("data/observations/upbit_identity_review", derived_block)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', derived_block)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', derived_block)

    def test_p4_and_p9_select_through_pinned_transition_consumer(self):
        for workflow in (P4_WORKFLOW, P9_WORKFLOW):
            with self.subTest(workflow=workflow.name):
                text = workflow.read_text(encoding="utf-8")
                self.assertIn(
                    "python3 .github/scripts/resolve_upbit_p3_p4_lineage.py",
                    text,
                )
                self.assertIn(
                    "--latest-data-root data/observations/upbit_tradeable_universe",
                    text,
                )
                self.assertNotIn(
                    "LATEST=$(ls -1 data/observations/upbit_tradeable_universe",
                    text,
                )
        self.assertIn("--select-only", P9_WORKFLOW.read_text(encoding="utf-8"))
        p4_text = P4_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("--transition-manifest", p4_text)
        self.assertIn("ATLAS_UNIVERSE_TRANSITION_MANIFEST_FILE_SHA256", p4_text)
        self.assertIn("ATLAS_UNIVERSE_TRANSITION_SOURCE_PAYLOAD_SHA256", p4_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
