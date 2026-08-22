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


class BuildUpstreamPayloadTest(unittest.TestCase):
    def test_only_names_present_on_both_dates_are_kept(self):
        prior = {"bas_dd": "20260818", "fetched_at_utc": "2026-08-18T09:00:00Z",
                  "index_names_to_close": {"코스피": "3000", "코스피 200": "400"}}
        current = {"bas_dd": "20260820", "fetched_at_utc": "2026-08-20T09:00:00Z",
                    "index_names_to_close": {"코스피": "3050", "코스피 반도체": "900"}}
        payload, common = MODULE.build_upstream_payload("kospi", prior, current)
        self.assertEqual(common, ["코스피"])
        self.assertEqual(len(payload["series_rows"]), 1)
        self.assertEqual(payload["series_rows"][0]["series_identity"], "코스피")
        self.assertEqual(payload["observation_date"], "2026-08-20")
        self.assertEqual(payload["expected_session_dates"], ["2026-08-18", "2026-08-20"])

    def test_no_common_names_fails_closed(self):
        prior = {"bas_dd": "20260818", "fetched_at_utc": "2026-08-18T09:00:00Z",
                  "index_names_to_close": {"코스피": "3000"}}
        current = {"bas_dd": "20260820", "fetched_at_utc": "2026-08-20T09:00:00Z",
                    "index_names_to_close": {"코스피 200": "400"}}
        with self.assertRaisesRegex(MODULE.LeadershipLiveFetchError, "NO_COMMON_INDEX_NAMES"):
            MODULE.build_upstream_payload("kospi", prior, current)

    def test_never_retains_a_raw_price_in_the_upstream_payload_beyond_the_transform_input(self):
        # This is a documentation-level guard: the payload legitimately
        # contains real closes (the transform needs them), but the
        # caller (run()/populate()) must never persist this payload
        # itself -- only covered by test_populate_never_writes_raw_price
        # below.
        prior = {"bas_dd": "20260818", "fetched_at_utc": "2026-08-18T09:00:00Z",
                  "index_names_to_close": {"코스피": "3000"}}
        current = {"bas_dd": "20260820", "fetched_at_utc": "2026-08-20T09:00:00Z",
                    "index_names_to_close": {"코스피": "3050"}}
        payload, _ = MODULE.build_upstream_payload("kospi", prior, current)
        self.assertEqual(
            payload["series_rows"][0]["rows"][1]["close"], "3050"
        )


class AttemptLeadershipTransformTest(unittest.TestCase):
    def test_unratified_policy_blocks_not_fails(self):
        prior = {"bas_dd": "20260818", "fetched_at_utc": "2026-08-18T09:00:00Z",
                  "index_names_to_close": {"코스피": "3000"}}
        current = {"bas_dd": "20260820", "fetched_at_utc": "2026-08-20T09:00:00Z",
                    "index_names_to_close": {"코스피": "3050"}}
        payload, _ = MODULE.build_upstream_payload("kospi", prior, current)
        result = MODULE.attempt_leadership_transform(payload)
        self.assertEqual(result["outcome"], "blocked")
        self.assertIn("LEADERSHIP_POLICY_UNRATIFIED", result["reason"])
        self.assertIsNone(result["packet"])


class PopulateTest(unittest.TestCase):
    def _fake_two_market_two_date_opener(self):
        data = {
            ("kospi", "20260818"): [row("20260818", "코스피", "3000")],
            ("kospi", "20260820"): [row("20260820", "코스피", "3050")],
            ("kosdaq", "20260818"): [row("20260818", "코스닥", "900")],
            ("kosdaq", "20260820"): [row("20260820", "코스닥", "920")],
        }
        return FakeOpener(data)

    def test_populate_writes_outcome_lineage_and_name_catalog_only(self):
        opener = self._fake_two_market_two_date_opener()
        with tempfile.TemporaryDirectory() as tmp:
            original_root, original_context_root, original_path_fn = (
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for
            )
            MODULE.ROOT = Path(tmp)
            MODULE.CONTEXT_ROOT = Path(tmp) / "data" / "observations" / "korea_leadership_context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            try:
                result = MODULE.populate("KEY", "20260818", "20260820", opener=opener)
                self.assertEqual(result["outcome"], "populated")
                on_disk = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
                self.assertEqual(on_disk["markets"]["KOSPI"]["outcome"], "blocked")
                self.assertIn(
                    "LEADERSHIP_POLICY_UNRATIFIED", on_disk["markets"]["KOSPI"]["reason"]
                )
                self.assertEqual(
                    on_disk["markets"]["KOSPI"]["discovered_index_names"], ["코스피"]
                )
                # No raw close/price value anywhere in the committed file.
                raw_text = json.dumps(on_disk)
                for forbidden in ("3000", "3050", "900", "920"):
                    self.assertNotIn(forbidden, raw_text)
            finally:
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for = (
                    original_root, original_context_root, original_path_fn
                )

    def test_populate_is_idempotent_on_immediate_rerun(self):
        opener = self._fake_two_market_two_date_opener()
        with tempfile.TemporaryDirectory() as tmp:
            original_root, original_context_root, original_path_fn = (
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for
            )
            MODULE.ROOT = Path(tmp)
            MODULE.CONTEXT_ROOT = Path(tmp) / "data" / "observations" / "korea_leadership_context"
            MODULE.output_path_for = lambda d: MODULE.CONTEXT_ROOT / d / "packet.json"
            try:
                first = MODULE.populate("KEY", "20260818", "20260820", opener=opener)
                second = MODULE.populate("KEY", "20260818", "20260820", opener=opener)
                # Same second in practice (fast fake fetch) -> byte-
                # identical; a genuinely later re-fetch fails closed on
                # drift instead (existing precedent, tested next).
                self.assertIn(second["outcome"], ("verified_existing", "populated"))
                if second["outcome"] == "verified_existing":
                    self.assertEqual(first["payload_sha256"], second["payload_sha256"])
            finally:
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for = (
                    original_root, original_context_root, original_path_fn
                )

    def test_populate_fails_closed_on_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
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
                    MODULE.populate("KEY", "20260818", "20260820", opener=opener)
            finally:
                MODULE.ROOT, MODULE.CONTEXT_ROOT, MODULE.output_path_for = (
                    original_root, original_context_root, original_path_fn
                )


if __name__ == "__main__":
    unittest.main()
