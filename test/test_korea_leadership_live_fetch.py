#!/usr/bin/env python3
"""Real KRX index -> korea_leadership.py live-fetch wiring regression."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlparse, parse_qs


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_leadership_live_fetch.py"
SPEC = importlib.util.spec_from_file_location("korea_leadership_live_fetch", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_policy_dict() -> dict:
    """Self-contained RATIFIED test policy, independent of the real
    committed config/korea_leadership_policy.json's own effective_from
    -- so this suite never silently breaks just because real time moves
    past whatever date the real file happens to be ratified from. Spans
    both markets (mirrors the real ratified shape: two benchmarks plus
    SECTOR members of each), since korea_leadership.build_transform()
    requires >=3 eligible (SECTOR/THEME) rows to rank anything -- but
    this suite doesn't call build_transform() with expectations of a
    full rank; it only proves the wiring reaches the real module
    correctly for whatever it decides."""
    def record(identity, role, benchmark):
        return {
            "series_identity": identity, "role": role, "benchmark_identity": benchmark,
            "effective_from": "2020-01-01", "effective_to": None, "reason": "test fixture",
        }

    return {
        "schema_version": 1,
        "policy_version": "korea_leadership/test-v1",
        "approval_status": "RATIFIED",
        "effective_from": "2020-01-01",
        "source_name": "KRX_OPEN_API_INDEX_LIVE",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "allowed_run_modes": ["FORWARD_SHADOW"],
        "session_calendar_source": "test_fixture_only",
        "publication_timing_source": "test_fixture_only",
        "earliest_usable_time": "18:00:00",
        "lookback_sessions": 1,
        "records": [
            record("KOSPI::코스피", "KOSPI_BENCHMARK", "KOSPI::코스피"),
            record("KOSDAQ::코스닥", "KOSDAQ_BENCHMARK", "KOSDAQ::코스닥"),
            record("KOSPI::화학", "SECTOR", "KOSPI::코스피"),
        ],
    }


def write_policy(directory: Path, policy: dict) -> Path:
    path = directory / "leadership-policy.json"
    path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    return path


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return 200


class FakeOpener:
    """Serves canned OutBlock_1 rows keyed by (market, basDd)."""

    def __init__(self, rows_by_market_date):
        self.rows_by_market_date = rows_by_market_date

    def __call__(self, request, timeout=30):
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        day = query["basDd"][0]
        market = "kospi" if "kospi" in parsed.path else "kosdaq"
        rows = self.rows_by_market_date[(market, day)]
        body = json.dumps({"OutBlock_1": rows}).encode("utf-8")
        return FakeResponse(body)


def row(day: str, name: str, close) -> dict:
    return {"BAS_DD": day, "IDX_NM": name, "CLSPRC_IDX": close}


def fetched(bas_dd: str, fetched_at_utc: str, names_to_close: dict) -> dict:
    return {"bas_dd": bas_dd, "fetched_at_utc": fetched_at_utc, "index_names_to_close": names_to_close}


class FetchIndexFamilyTest(unittest.TestCase):
    def test_extracts_only_name_and_close_ignores_other_fields(self):
        wide_row = row("20260818", "코스피", "3000.00") | {
            "IDX_CLSS": "x", "ACC_TRDVOL": "9999999999", "MKTCAP": "raw-value",
        }
        opener = FakeOpener({("kospi", "20260818"): [wide_row]})
        result = MODULE.fetch_index_family("KEY", "20260818", "kospi", opener=opener)
        self.assertEqual(result["index_names_to_close"], {"코스피": "3000.00"})
        self.assertEqual(len(result["response_sha256"]), 64)

    def test_empty_response_fails_closed(self):
        opener = FakeOpener({("kospi", "20260818"): []})
        with self.assertRaisesRegex(MODULE.LeadershipLiveFetchError, "KRX_RESPONSE_EMPTY"):
            MODULE.fetch_index_family("KEY", "20260818", "kospi", opener=opener)

    def test_rows_with_missing_name_or_close_are_skipped_not_guessed(self):
        rows = [
            row("20260818", "코스피", "3000.00"),
            {"BAS_DD": "20260818", "IDX_NM": "", "CLSPRC_IDX": "1.00"},
            {"BAS_DD": "20260818", "IDX_NM": "코스피 200", "CLSPRC_IDX": ""},
        ]
        opener = FakeOpener({("kospi", "20260818"): rows})
        result = MODULE.fetch_index_family("KEY", "20260818", "kospi", opener=opener)
        self.assertEqual(result["index_names_to_close"], {"코스피": "3000.00"})


class QualifiedIdentityTest(unittest.TestCase):
    def test_same_name_different_market_never_collides(self):
        self.assertNotEqual(
            MODULE.qualified_identity("kospi", "IT 서비스"),
            MODULE.qualified_identity("kosdaq", "IT 서비스"),
        )
        self.assertEqual(MODULE.qualified_identity("kospi", "코스피"), "KOSPI::코스피")


class BuildCombinedUpstreamPayloadTest(unittest.TestCase):
    def _per_market(self):
        return {
            "kospi": {
                "prior": fetched("20260818", "2026-08-18T09:00:00Z", {"코스피": "3000", "코스피 200": "400", "화학": "50"}),
                "current": fetched("20260820", "2026-08-20T09:00:00Z", {"코스피": "3050", "코스피 200": "410", "화학": "55"}),
            },
            "kosdaq": {
                "prior": fetched("20260818", "2026-08-18T09:05:00Z", {"코스닥": "900"}),
                "current": fetched("20260820", "2026-08-20T09:05:00Z", {"코스닥": "920"}),
            },
        }

    def test_combined_payload_spans_both_markets_ratified_series_only(self):
        payload, common_by_market = MODULE.build_combined_upstream_payload(
            self._per_market(), test_policy_dict()
        )
        identities = {row["series_identity"] for row in payload["series_rows"]}
        # 코스피 200 is real/common but NOT ratified -> excluded.
        self.assertEqual(identities, {"KOSPI::코스피", "KOSPI::화학", "KOSDAQ::코스닥"})
        self.assertEqual(common_by_market["kospi"], ["코스피", "코스피 200", "화학"])
        self.assertEqual(common_by_market["kosdaq"], ["코스닥"])
        self.assertEqual(payload["observation_date"], "2026-08-20")
        self.assertEqual(payload["expected_session_dates"], ["2026-08-18", "2026-08-20"])
        # fetched_at/available_at/decision_at must be KST (+09:00), not
        # bare UTC "Z" -- korea_leadership.py's own parse_timestamp()
        # requires exactly a +09:00 offset.
        self.assertTrue(payload["fetched_at"].endswith("+09:00"))

    def test_no_ratified_common_names_fails_closed(self):
        per_market = {
            "kospi": {
                "prior": fetched("20260818", "2026-08-18T09:00:00Z", {"코스피 200": "400"}),
                "current": fetched("20260820", "2026-08-20T09:00:00Z", {"코스피 200": "410"}),
            },
            "kosdaq": {
                "prior": fetched("20260818", "2026-08-18T09:05:00Z", {"코스닥 150": "100"}),
                "current": fetched("20260820", "2026-08-20T09:05:00Z", {"코스닥 150": "105"}),
            },
        }
        with self.assertRaisesRegex(
            MODULE.LeadershipLiveFetchError, "NO_RATIFIED_COMMON_INDEX_NAMES"
        ):
            MODULE.build_combined_upstream_payload(per_market, test_policy_dict())

    def test_mismatched_market_dates_fail_closed(self):
        per_market = self._per_market()
        per_market["kosdaq"]["current"]["bas_dd"] = "20260821"  # different date than kospi
        with self.assertRaisesRegex(MODULE.LeadershipLiveFetchError, "MARKET_DATE_MISMATCH"):
            MODULE.build_combined_upstream_payload(per_market, test_policy_dict())

    def test_ratified_but_not_yet_effective_is_excluded(self):
        policy = test_policy_dict()
        policy["records"][2]["effective_from"] = "2099-01-01"  # not yet effective
        payload, _ = MODULE.build_combined_upstream_payload(self._per_market(), policy)
        identities = {row["series_identity"] for row in payload["series_rows"]}
        self.assertEqual(identities, {"KOSPI::코스피", "KOSDAQ::코스닥"})

    def test_never_retains_a_raw_price_beyond_the_transform_input(self):
        payload, _ = MODULE.build_combined_upstream_payload(self._per_market(), test_policy_dict())
        by_identity = {row["series_identity"]: row for row in payload["series_rows"]}
        self.assertEqual(by_identity["KOSPI::화학"]["rows"][1]["close"], "55")


class AttemptLeadershipTransformTest(unittest.TestCase):
    def test_unratified_policy_blocks_not_fails(self):
        per_market = {
            "kospi": {
                "prior": fetched("20260818", "2026-08-18T09:00:00Z", {"코스피": "3000"}),
                "current": fetched("20260820", "2026-08-20T09:00:00Z", {"코스피": "3050"}),
            },
            "kosdaq": {
                "prior": fetched("20260818", "2026-08-18T09:05:00Z", {"코스닥": "900"}),
                "current": fetched("20260820", "2026-08-20T09:05:00Z", {"코스닥": "920"}),
            },
        }
        unratified = test_policy_dict() | {"approval_status": "UNRATIFIED"}
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), unratified)
            payload, _ = MODULE.build_combined_upstream_payload(per_market, unratified)
            result = MODULE.attempt_leadership_transform(payload, policy_path=policy_path)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("LEADERSHIP_POLICY_UNRATIFIED", result["reason"])
        self.assertIsNone(result["packet"])

    def test_ratified_policy_reaches_real_build_transform(self):
        per_market = {
            "kospi": {
                "prior": fetched("20260818", "2026-08-18T09:00:00Z", {"코스피": "3000"}),
                "current": fetched("20260820", "2026-08-20T09:00:00Z", {"코스피": "3050"}),
            },
            "kosdaq": {
                "prior": fetched("20260818", "2026-08-18T09:05:00Z", {"코스닥": "900"}),
                "current": fetched("20260820", "2026-08-20T09:05:00Z", {"코스닥": "920"}),
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), test_policy_dict())
            payload, _ = MODULE.build_combined_upstream_payload(per_market, test_policy_dict())
            result = MODULE.attempt_leadership_transform(payload, policy_path=policy_path)
        # Whatever the real, unmodified build_transform() decides (a
        # packet, or a different fail-closed reason such as insufficient
        # eligible SECTOR/THEME coverage) is its own logic -- this proves
        # the wiring reaches it with a schema-valid, KST-timestamped,
        # policy-filtered payload, not that a specific outcome is forced.
        self.assertIn(result["outcome"], ("populated", "blocked"))
        if result["outcome"] == "blocked":
            self.assertNotIn("LEADERSHIP_POLICY_UNRATIFIED", result["reason"])
            self.assertNotIn("TIMESTAMP_INVALID", result["reason"])


class PopulateTest(unittest.TestCase):
    def _fake_two_market_two_date_opener(self):
        data = {
            ("kospi", "20260818"): [row("20260818", "코스피", "3000"), row("20260818", "화학", "50")],
            ("kospi", "20260820"): [row("20260820", "코스피", "3050"), row("20260820", "화학", "55")],
            ("kosdaq", "20260818"): [row("20260818", "코스닥", "900")],
            ("kosdaq", "20260820"): [row("20260820", "코스닥", "920")],
        }
        return FakeOpener(data)

    def test_populate_writes_outcome_lineage_and_name_catalog_only(self):
        opener = self._fake_two_market_two_date_opener()
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), test_policy_dict())
            original_root, original_context_root, original_path_fn = (
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for
            )
            MODULE.ROOT = Path(tmp)
            MODULE.CONTEXT_ROOT = Path(tmp) / "data" / "observations" / "korea_leadership_context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            try:
                result = MODULE.populate(
                    "KEY", "20260818", "20260820", opener=opener, policy_path=policy_path
                )
                self.assertEqual(result["outcome"], "populated")
                on_disk = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
                self.assertIn(on_disk["outcome"], ("populated", "blocked"))
                self.assertEqual(on_disk["markets"]["KOSPI"]["ratified_series_count"], 2)
                self.assertEqual(on_disk["markets"]["KOSDAQ"]["ratified_series_count"], 1)
                self.assertEqual(
                    set(on_disk["markets"]["KOSPI"]["discovered_index_names"]),
                    {"코스피", "화학"},
                )
                # No raw close/price value survives anywhere as an actual
                # field VALUE in the committed file. Checked structurally
                # (walking real dict/list values), never as a raw-dump
                # substring search -- a short digit string can
                # coincidentally appear inside a sha256 hex digest, or
                # inside the \uXXXX JSON escape of a legitimate Korean
                # index name (e.g. "화학" escapes to "화학", which
                # itself contains "55") -- neither is a real leak.
                forbidden_values = {"3000", "3050", "900", "920", "50", "55"}

                def walk(value):
                    if isinstance(value, dict):
                        for item in value.values():
                            yield from walk(item)
                    elif isinstance(value, list):
                        for item in value:
                            yield from walk(item)
                    else:
                        yield value

                leaked = forbidden_values & {v for v in walk(on_disk) if isinstance(v, str)}
                self.assertEqual(leaked, set())
            finally:
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for = (
                    original_root, original_context_root, original_path_fn
                )

    def test_populate_is_idempotent_on_immediate_rerun(self):
        opener = self._fake_two_market_two_date_opener()
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), test_policy_dict())
            original_root, original_context_root, original_path_fn = (
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for
            )
            original_now = MODULE._now_utc_iso
            MODULE.ROOT = Path(tmp)
            MODULE.CONTEXT_ROOT = Path(tmp) / "data" / "observations" / "korea_leadership_context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            MODULE._now_utc_iso = lambda: "2026-08-20T09:00:00Z"
            try:
                first = MODULE.populate(
                    "KEY", "20260818", "20260820", opener=opener, policy_path=policy_path
                )
                second = MODULE.populate(
                    "KEY", "20260818", "20260820", opener=opener, policy_path=policy_path
                )
                self.assertEqual(second["outcome"], "verified_existing")
                self.assertEqual(first["payload_sha256"], second["payload_sha256"])
            finally:
                MODULE._now_utc_iso = original_now
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for = (
                    original_root, original_context_root, original_path_fn
                )

    def test_populate_fails_closed_on_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), test_policy_dict())
            original_root, original_context_root, original_path_fn = (
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for
            )
            MODULE.ROOT = Path(tmp)
            MODULE.CONTEXT_ROOT = Path(tmp) / "data" / "observations" / "korea_leadership_context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            try:
                path = MODULE.output_path_for("2026-08-20")
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({"schema_version": "tampered"}), encoding="utf-8")
                opener = self._fake_two_market_two_date_opener()
                with self.assertRaisesRegex(
                    MODULE.LeadershipLiveFetchError, "EXISTING_PACKET_DRIFT_OR_TAMPER"
                ):
                    MODULE.populate(
                        "KEY", "20260818", "20260820", opener=opener, policy_path=policy_path
                    )
            finally:
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for = (
                    original_root, original_context_root, original_path_fn
                )

    def test_verify_existing_observation_reuses_valid_packet_without_provider(self):
        opener = self._fake_two_market_two_date_opener()
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), test_policy_dict())
            original_context_root, original_path_fn, original_now = (
                MODULE.CONTEXT_ROOT, MODULE.output_path_for, MODULE._now_utc_iso
            )
            MODULE.CONTEXT_ROOT = Path(tmp) / "context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            MODULE._now_utc_iso = lambda: "2026-08-20T09:00:00Z"
            try:
                MODULE.populate(
                    "KEY", "20260818", "20260820", opener=opener, policy_path=policy_path
                )
                existing = MODULE.verify_existing_observation("20260818", "20260820")
                self.assertEqual(existing["observation_date"], "2026-08-20")
            finally:
                MODULE._now_utc_iso = original_now
                MODULE.CONTEXT_ROOT, MODULE.output_path_for = original_context_root, original_path_fn

    def test_verify_existing_observation_rejects_tamper_and_date_mismatch(self):
        opener = self._fake_two_market_two_date_opener()
        with tempfile.TemporaryDirectory() as tmp:
            policy_path = write_policy(Path(tmp), test_policy_dict())
            original_context_root, original_path_fn, original_now = (
                MODULE.CONTEXT_ROOT, MODULE.output_path_for, MODULE._now_utc_iso
            )
            MODULE.CONTEXT_ROOT = Path(tmp) / "context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            MODULE._now_utc_iso = lambda: "2026-08-20T09:00:00Z"
            try:
                MODULE.populate(
                    "KEY", "20260818", "20260820", opener=opener, policy_path=policy_path
                )
                with self.assertRaisesRegex(
                    MODULE.LeadershipLiveFetchError, "PRIOR_DATE_MISMATCH"
                ):
                    MODULE.verify_existing_observation("20260819", "20260820")
                path = MODULE.output_path_for("2026-08-20")
                packet = json.loads(path.read_text(encoding="utf-8"))
                packet["reason"] = "tampered"
                path.write_text(json.dumps(packet), encoding="utf-8")
                with self.assertRaisesRegex(
                    MODULE.LeadershipLiveFetchError, "HASH_MISMATCH"
                ):
                    MODULE.verify_existing_observation("20260818", "20260820")
            finally:
                MODULE._now_utc_iso = original_now
                MODULE.CONTEXT_ROOT, MODULE.output_path_for = original_context_root, original_path_fn


if __name__ == "__main__":
    unittest.main()
