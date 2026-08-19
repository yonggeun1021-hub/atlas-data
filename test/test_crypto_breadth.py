#!/usr/bin/env python3
"""P1-CR-06 Crypto breadth PIT universe and participation regression.

Every Kraken response and policy fixture lives under a temporary directory.
The tests make no live request and write no tracked breadth output.
"""

import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_breadth.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("crypto_breadth", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def text_number(value):
    return format(Decimal(str(value)), "f")


def candle(day, close):
    close = Decimal(str(close))
    return [
        int(
            dt.datetime.combine(
                day, dt.time(), tzinfo=dt.timezone.utc
            ).timestamp()
        ),
        text_number(close),
        text_number(close + Decimal(1)),
        text_number(close - Decimal(1)),
        text_number(close),
        text_number(close),
        "10.5",
        42,
    ]


def source_payload(result):
    return {"error": [], "result": result}


def ohlc_payload(vintage, base, previous, latest, current):
    vintage = dt.date.fromisoformat(vintage)
    pair = f"{base}/USD"
    rows = [
        candle(vintage - dt.timedelta(days=2), previous),
        candle(vintage - dt.timedelta(days=1), latest),
        candle(vintage, current),
    ]
    return source_payload({pair: rows, "last": rows[-2][0]})


def raw_bytes(payload):
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def write_gzip(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as output:
        with gzip.GzipFile(fileobj=output, mode="wb", mtime=0) as stream:
            stream.write(raw)


def write_policy(path, excluded=None, minimum=3):
    payload = {
        "schema_version": 1,
        "policy_version": "crypto_breadth_universe/test-v1",
        "approval_status": "RATIFIED",
        "source_name": "kraken_spot_market_data",
        "universe_kind": "breadth_source_coverage_not_investable",
        "effective_from": "2026-01-01",
        "quote_currency": "USD",
        "allowed_asset_statuses": ["enabled"],
        "allowed_pair_statuses": ["online"],
        "excluded_canonical_assets": sorted(excluded or []),
        "minimum_asset_count": minimum,
        "selection_rule": (
            "all_matching_pairs_minus_explicit_canonical_exclusions"
        ),
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return Path(path)


def write_identity(path, records):
    payload = {
        "schema_version": 1,
        "policy_version": "crypto_asset_identity_exceptions/v1",
        "source_name": "kraken_spot_market_data",
        "asset_version": 1,
        "records": records,
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return Path(path)


def identity_record(
    source,
    canonical,
    start="1970-01-01",
    end=None,
    aliases=None,
):
    return {
        "source_asset_id": source,
        "canonical_asset_id": canonical,
        "aliases": sorted(aliases or []),
        "effective_from": start,
        "effective_to": end,
        "reason": "test fixture identity",
    }


def write_snapshot(
    root,
    vintage="2026-08-20",
    prices=None,
    pair_bases=None,
    ohlc_bases=None,
    identity_path=MODULE.IDENTITY_EXCEPTIONS_PATH,
):
    prices = prices or {
        "BTC": (100, 110, 9999),
        "ETH": (10, 12, 9999),
        "SOL": (20, 18, 9999),
        "USDT": (5, 5, 9999),
    }
    pair_bases = list(prices) if pair_bases is None else list(pair_bases)
    ohlc_bases = pair_bases if ohlc_bases is None else list(ohlc_bases)
    snapshot = Path(root) / vintage
    snapshot.mkdir(parents=True)
    (snapshot / "_downloaded_at.txt").write_text(
        f"{vintage}T00:30:00Z\n",
        encoding="utf-8",
    )

    asset_ids = sorted(set(pair_bases) | {"USD"})
    assets = {
        asset: {
            "aclass": "currency",
            "altname": "XBT" if asset == "BTC" else asset,
            "status": "enabled",
            "decimals": 8,
        }
        for asset in asset_ids
    }
    pairs = {
        f"{base}/USD": {
            "altname": f"{base}USD",
            "wsname": f"{base}/USD",
            "aclass_base": "currency",
            "base": base,
            "aclass_quote": "currency",
            "quote": "USD",
            "status": "online",
        }
        for base in pair_bases
    }
    payloads = {
        CONTRACT["assets_raw_file"]: raw_bytes(source_payload(assets)),
        CONTRACT["asset_pairs_raw_file"]: raw_bytes(source_payload(pairs)),
    }
    for base in ohlc_bases:
        pair = f"{base}/USD"
        payloads[MODULE.ohlc_file_name(pair)] = raw_bytes(
            ohlc_payload(vintage, base, *prices[base])
        )

    checksum_lines = []
    for relative, raw in sorted(payloads.items()):
        write_gzip(snapshot / relative, raw)
        checksum_lines.append(
            f"{hashlib.sha256(raw).hexdigest()}  "
            f"{MODULE.raw_checksum_name(relative)}"
        )
    (snapshot / "_sha256.txt").write_text(
        "\n".join(checksum_lines) + "\n",
        encoding="utf-8",
    )
    MODULE.build_manifest(
        snapshot,
        "crypto-breadth-capture/v1",
        identity_exceptions_path=identity_path,
    )
    return snapshot


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class CryptoBreadthTest(unittest.TestCase):
    def test_contract_and_default_policy_keep_authority_closed(self):
        policy = MODULE.load_universe_policy()

        self.assertEqual(
            CONTRACT["historical_universe_policy"],
            "as_captured_append_only_no_current_state_backfill",
        )
        self.assertEqual(
            CONTRACT["current_candle_policy"],
            "exclude_last_row_always",
        )
        self.assertEqual(policy["approval_status"], "UNRATIFIED")
        self.assertIsNone(policy["effective_from"])
        self.assertIsNone(policy["minimum_asset_count"])

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            with self.assertRaisesRegex(
                MODULE.BreadthError, "UNIVERSE_POLICY_UNRATIFIED"
            ):
                MODULE.build_transform(snapshot)

    def test_exact_universe_builds_raw_btc_and_alt_participation(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", {"USDT"}, 3)
            snapshot = write_snapshot(Path(tmp) / "raw")
            result = MODULE.build_transform(
                snapshot, universe_policy_path=policy
            )

            self.assertEqual(result["as_of_date"], "2026-08-19")
            self.assertEqual(result["previous_date"], "2026-08-18")
            self.assertEqual(result["universe"]["asset_count"], 3)
            self.assertEqual(
                [
                    item["canonical_asset_id"]
                    for item in result["universe"]["members"]
                ],
                ["BTC", "ETH", "SOL"],
            )
            self.assertEqual(result["btc_reference"]["direction"], "ADVANCE")
            alt = result["alt_participation"]
            self.assertEqual(alt["asset_count"], 2)
            self.assertEqual(alt["advancing_count"], 1)
            self.assertEqual(alt["declining_count"], 1)
            self.assertEqual(alt["advance_fraction"], "0.5")
            self.assertEqual(alt["decline_fraction"], "0.5")
            self.assertEqual(alt["classification"], "UNDEFINED")
            self.assertFalse(result["breadth_classification_authorized"])
            self.assertFalse(result["threshold_authorized"])
            self.assertFalse(result["regime_score_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(result["trading_action_authorized"])

    def test_current_uncommitted_rows_cannot_change_participation(self):
        baseline = {
            "BTC": (100, 110, 2),
            "ETH": (10, 12, 2),
            "SOL": (20, 18, 2),
        }
        changed = {
            key: (values[0], values[1], 999999)
            for key, values in baseline.items()
        }
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            first = write_snapshot(Path(tmp) / "first", prices=baseline)
            second = write_snapshot(Path(tmp) / "second", prices=changed)
            low = MODULE.build_transform(first, universe_policy_path=policy)
            high = MODULE.build_transform(second, universe_policy_path=policy)

            self.assertEqual(low["btc_reference"], high["btc_reference"])
            self.assertEqual(
                low["alt_participation"], high["alt_participation"]
            )
            self.assertEqual(
                low["universe"]["members"], high["universe"]["members"]
            )
            self.assertNotEqual(
                low["lineage"]["manifest_sha256"],
                high["lineage"]["manifest_sha256"],
            )

    def test_missing_policy_member_fails_instead_of_partial_breadth(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json")
            snapshot = write_snapshot(
                Path(tmp) / "raw",
                pair_bases=["BTC", "ETH", "SOL"],
                ohlc_bases=["BTC", "ETH"],
            )
            with self.assertRaisesRegex(
                MODULE.BreadthError, "COVERAGE_INCOMPLETE.*SOL/USD"
            ):
                MODULE.build_transform(
                    snapshot, universe_policy_path=policy
                )

    def test_identity_collision_and_overlapping_reuse_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            identity = write_identity(
                Path(tmp) / "identity.json",
                [identity_record("SOL", "ETH")],
            )
            policy = write_policy(Path(tmp) / "policy.json")
            snapshot = write_snapshot(
                Path(tmp) / "raw",
                prices={
                    "BTC": (100, 110, 500),
                    "ETH": (10, 12, 500),
                    "SOL": (20, 18, 500),
                },
                identity_path=identity,
            )
            with self.assertRaisesRegex(
                MODULE.BreadthError, "CANONICAL_ASSET_DUPLICATE"
            ):
                MODULE.build_transform(
                    snapshot,
                    universe_policy_path=policy,
                    identity_exceptions_path=identity,
                )

        with tempfile.TemporaryDirectory() as tmp:
            overlap = write_identity(
                Path(tmp) / "overlap.json",
                [
                    identity_record(
                        "ABC", "OLD", "2026-01-01", "2026-06-30"
                    ),
                    identity_record(
                        "ABC", "NEW", "2026-06-30", None
                    ),
                ],
            )
            with self.assertRaisesRegex(
                MODULE.BreadthError, "IDENTITY_RANGE_OVERLAP"
            ):
                MODULE.load_identity_exceptions(overlap)

        with tempfile.TemporaryDirectory() as tmp:
            reuse = write_identity(
                Path(tmp) / "reuse.json",
                [
                    identity_record(
                        "ABC", "OLD", "2026-01-01", "2026-06-29"
                    ),
                    identity_record(
                        "ABC", "NEW", "2026-06-30", None
                    ),
                ],
            )
            reuse_payload = json.loads(reuse.read_text(encoding="utf-8"))
            reuse_payload["policy_version"] = (
                "crypto_asset_identity_exceptions/v2"
            )
            reuse.write_text(
                json.dumps(reuse_payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            loaded = MODULE.load_identity_exceptions(reuse)
            self.assertEqual(
                MODULE.canonical_identity(
                    "ABC", dt.date(2026, 6, 29), loaded
                ),
                "OLD",
            )
            self.assertEqual(
                MODULE.canonical_identity(
                    "ABC", dt.date(2026, 6, 30), loaded
                ),
                "NEW",
            )

    def test_replay_uses_each_dates_captured_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", minimum=2)
            root = Path(tmp) / "raw"
            write_snapshot(
                root,
                vintage="2026-08-19",
                prices={"BTC": (90, 100, 500), "ETH": (9, 10, 500)},
            )
            write_snapshot(
                root,
                vintage="2026-08-20",
                prices={
                    "BTC": (100, 110, 500),
                    "ETH": (10, 12, 500),
                    "SOL": (20, 18, 500),
                },
            )
            replay = MODULE.build_replay(
                root, universe_policy_path=policy
            )

            self.assertEqual(replay["point_count"], 2)
            self.assertEqual(replay["first_as_of_date"], "2026-08-18")
            self.assertEqual(replay["last_as_of_date"], "2026-08-19")
            self.assertEqual(
                [point["universe"]["asset_count"] for point in replay["points"]],
                [2, 3],
            )
            self.assertFalse(replay["current_catalog_backfill_authorized"])

    def test_checksum_manifest_and_append_only_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            with self.assertRaisesRegex(
                MODULE.BreadthError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.build_manifest(
                    snapshot, "crypto-breadth-capture/v1"
                )

            pair_file = snapshot / MODULE.ohlc_file_name("BTC/USD")
            tampered = raw_bytes(
                ohlc_payload("2026-08-20", "BTC", 100, 500, 999)
            )
            write_gzip(pair_file, tampered)
            with self.assertRaisesRegex(
                MODULE.BreadthError, "CHECKSUM_MISMATCH"
            ):
                MODULE.validate_snapshot(snapshot)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            path = snapshot / "_manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["historical_universe_policy"] = "use_current_catalog"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.BreadthError, "MANIFEST_MISMATCH"
            ):
                MODULE.validate_snapshot(snapshot)

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            write_gzip(
                snapshot / "unexpected.json.gz",
                raw_bytes(source_payload({"unexpected": {}})),
            )
            with self.assertRaisesRegex(
                MODULE.BreadthError, "RAW_FILE_INVENTORY_INVALID"
            ):
                MODULE.validate_snapshot(snapshot)

    def test_transform_is_deterministic_float_free_and_output_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", {"USDT"}, 3)
            snapshot = write_snapshot(Path(tmp) / "raw")
            first = MODULE.build_transform(
                snapshot, universe_policy_path=policy
            )
            second = MODULE.build_transform(
                snapshot, universe_policy_path=policy
            )
            output = Path(tmp) / "output" / "crypto_breadth.json"
            MODULE.write_output(first, output)

            self.assertEqual(first, second)
            self.assertFalse(has_float(first))
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), first
            )
            self.assertFalse(list(output.parent.glob(".*.tmp.*")))

    def test_no_live_capture_or_workflow_publication_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )

        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertNotIn("crypto_breadth.py", workflows)
        self.assertNotIn("/public/AssetPairs", workflows)
        self.assertNotIn("crypto_breadth", workflows)


if __name__ == "__main__":
    unittest.main()
