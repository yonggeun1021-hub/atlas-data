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


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "upbit_universe_populate.py"

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


class UpbitUniversePopulateTests(unittest.TestCase):
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

    def test_effective_date_allows_one_atomic_fail_closed_ratification_transition(self):
        current = POPULATE.rebuild("2026-08-30")
        historical = copy.deepcopy(current)
        historical.pop("ratification")
        historical["authority"]["observation_pool_population_only"] = True
        packet = historical["packet"]
        packet["policy_ratified"] = False
        packet["taxonomy_ratified"] = False
        for row in packet["markets"]:
            row["state"] = UNI.STATE_OBSERVATION_POOL
            row["reason"] = "IDENTITY_UNRATIFIED"
            row["candidate_canonical_asset_id"] = None
        packet["summary"] = {
            "market_count": len(packet["markets"]),
            "observation_pool_count": len(packet["markets"]),
            "tradeable_universe_count": 0,
            "paper_eligible_count": 0,
            "blocked_count": 0,
        }
        packet["payload_sha256"] = UNI.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        historical["payload_sha256"] = POPULATE.payload_sha256(
            {key: value for key, value in historical.items() if key != "payload_sha256"}
        )

        with tempfile.TemporaryDirectory() as data:
            data_root = Path(data)
            target = POPULATE.output_path("2026-08-30", data_root)
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(historical, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            result = POPULATE.populate("2026-08-30", data_root=data_root)
            self.assertEqual(result["outcome"], "ratified_reclassification")
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), current)


if __name__ == "__main__":
    unittest.main(verbosity=2)
