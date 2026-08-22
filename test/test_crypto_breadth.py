#!/usr/bin/env python3
"""P1-CR-06 Crypto breadth PIT universe and participation regression.

Every Kraken response and policy fixture lives under a temporary directory.
The collector test injects a fake fetcher; tests make no live request and write
no tracked breadth output.
"""

import datetime as dt
import base64
from decimal import Decimal
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import urllib.parse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_breadth.py"
CAPTURE_SCRIPT = ROOT / ".github" / "scripts" / "crypto_breadth_capture.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("crypto_breadth", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CAPTURE_SPEC = importlib.util.spec_from_file_location(
    "crypto_breadth_capture", CAPTURE_SCRIPT
)
CAPTURE_MODULE = importlib.util.module_from_spec(CAPTURE_SPEC)
CAPTURE_SPEC.loader.exec_module(CAPTURE_MODULE)
CONTRACT = MODULE.load_contract()


def text_number(value):
    return format(Decimal(str(value)), "f")


def candle(day, close, volume="10.5"):
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
        text_number(volume),
        42,
    ]


def no_trade_candle(day, close):
    value = text_number(close)
    return [
        int(
            dt.datetime.combine(
                day, dt.time(), tzinfo=dt.timezone.utc
            ).timestamp()
        ),
        value,
        value,
        value,
        value,
        "0.0000",
        "0.00000000",
        0,
    ]


def source_payload(result):
    return {"error": [], "result": result}


def ohlc_payload(
    vintage,
    base,
    previous,
    latest,
    current,
    volume="10.5",
    omit_latest=False,
):
    vintage = dt.date.fromisoformat(vintage)
    pair = f"{base}/USD"
    rows = [
        candle(vintage - dt.timedelta(days=offset), previous, volume)
        for offset in range(31, 2, -1)
    ] + [
        candle(vintage - dt.timedelta(days=2), previous, volume),
    ]
    if not omit_latest:
        rows.append(
            candle(vintage - dt.timedelta(days=1), latest, volume)
        )
    rows.append(candle(vintage, current, volume))
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


def write_policy(path, target=3, coverage_bps=9000):
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
        "ranking_lookback_finalized_days": 30,
        "ranking_metric": "sum_daily_vwap_times_base_volume",
        "ranking_end_policy": "previous_day_before_observation_as_of",
        "target_asset_count": target,
        "minimum_observation_coverage_bps": coverage_bps,
        "btc_policy": "reference_only_excluded_from_alt_participation",
        "unknown_taxonomy_policy": "fail_closed_unknown",
        "selection_rule": (
            "trailing_30d_usd_turnover_top_n_after_explicit_taxonomy"
        ),
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return Path(path)


def write_taxonomy(path, categories, approval="RATIFIED"):
    records = [
        {
            "canonical_asset_id": asset,
            "category": category,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "reason": "test fixture classification",
        }
        for asset, category in sorted(categories.items())
    ]
    payload = {
        "schema_version": 1,
        "policy_version": "crypto_breadth_exclusion_taxonomy/v2",
        "approval_status": approval,
        "source_name": "kraken_spot_market_data",
        "effective_from": "2026-01-01",
        "eligible_category": "eligible_crypto",
        "excluded_categories": [
            "commodity_linked",
            "fiat",
            "stablecoin",
            "staked",
            "unverified_identity",
            "wrapped",
        ],
        "unknown_asset_policy": "fail_closed_unknown",
        "records": records,
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
    omit_latest_bases=None,
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
    omit_latest_bases = set(omit_latest_bases or [])
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
    ohlc_records = []
    for base in sorted(ohlc_bases):
        pair = f"{base}/USD"
        raw = raw_bytes(
            ohlc_payload(
                vintage,
                base,
                *prices[base],
                omit_latest=base in omit_latest_bases,
            )
        )
        ohlc_records.append(
            {
                "pair_id": pair,
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "body_b64": base64.b64encode(raw).decode("ascii"),
            }
        )
    payloads[CONTRACT["ohlc_bundle_raw_file"]] = b"".join(
        json.dumps(
            record,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
        for record in ohlc_records
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
        "crypto-breadth-capture/v2",
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
    def test_collector_is_complete_paced_atomic_and_append_only(self):
        vintage = "2026-08-20"
        bases = ["BTC", "ETH", "SOL"]
        assets = {
            asset: {
                "aclass": "currency",
                "altname": asset,
                "status": "enabled",
                "decimals": 8,
            }
            for asset in bases + ["USD"]
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
            for base in bases
        }
        responses = {
            "Assets": raw_bytes(source_payload(assets)),
            "AssetPairs": raw_bytes(source_payload(pairs)),
        }
        for index, base in enumerate(bases):
            responses[base] = raw_bytes(
                ohlc_payload(vintage, base, 100 - index, 101 - index, 999)
            )

        def fetcher(url, timeout):
            self.assertEqual(timeout, 17)
            if "/Assets?" in url:
                return responses["Assets"]
            if "/AssetPairs?" in url:
                return responses["AssetPairs"]
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            pair = query["pair"][0]
            return responses[pair.split("/")[0]]

        sleeps = []
        clock = lambda: dt.datetime(
            2026, 8, 20, 0, 40, tzinfo=dt.timezone.utc
        )
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = CAPTURE_MODULE.capture_snapshot(
                Path(tmp),
                snapshot_date=dt.date(2026, 8, 20),
                request_interval_seconds=1.0,
                timeout_seconds=17,
                fetcher=fetcher,
                sleeper=sleeps.append,
                clock=clock,
            )
            validated = MODULE.validate_snapshot(snapshot)
            self.assertEqual(validated["ohlc_pair_count"], 3)
            manifest = MODULE.read_json(
                snapshot / "_manifest.json", "MANIFEST_INVALID"
            )
            self.assertEqual(manifest["source"]["minimum_response_rows"], 1)
            self.assertEqual(
                manifest["source"]["no_trade_candle_policy"],
                "zero_vwap_zero_volume_zero_trades_flat_ohlc",
            )
            self.assertEqual(sleeps, [1.0, 1.0])
            with self.assertRaisesRegex(
                CAPTURE_MODULE.CaptureError, "APPEND_ONLY_VIOLATION"
            ):
                CAPTURE_MODULE.capture_snapshot(
                    Path(tmp),
                    snapshot_date=dt.date(2026, 8, 20),
                    request_interval_seconds=1.0,
                    timeout_seconds=17,
                    fetcher=fetcher,
                    sleeper=sleeps.append,
                    clock=clock,
                )

    def test_contract_and_default_policy_keep_authority_closed(self):
        policy = MODULE.load_universe_policy()
        taxonomy = MODULE.load_exclusion_taxonomy()

        self.assertEqual(
            CONTRACT["historical_universe_policy"],
            "as_captured_append_only_no_current_state_backfill",
        )
        self.assertEqual(
            CONTRACT["current_candle_policy"],
            "exclude_last_row_always",
        )
        self.assertEqual(
            CONTRACT["no_trade_candle_policy"],
            "zero_vwap_zero_volume_zero_trades_flat_ohlc",
        )
        self.assertEqual(CONTRACT["minimum_response_rows"], 1)
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(policy["target_asset_count"], 100)
        self.assertEqual(policy["minimum_observation_coverage_bps"], 9000)
        self.assertEqual(taxonomy["approval_status"], "RATIFIED")

        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            result = MODULE.build_transform(snapshot)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(
                result["unknown_reason"],
                "RANK_ELIGIBLE_UNIVERSE_BELOW_TARGET",
            )
            self.assertFalse(result["production_wiring_authorized"])

    def test_zero_vwap_is_only_the_exact_no_trade_sentinel(self):
        day = dt.date(2026, 8, 20)
        normalized = MODULE.normalize_candle(
            no_trade_candle(day, "0.0867"), 40, "1INCH/USD"
        )
        self.assertEqual(normalized["date"], day)
        self.assertEqual(normalized["vwap"], Decimal(0))
        self.assertEqual(normalized["volume"], Decimal(0))
        self.assertEqual(normalized["trade_count"], 0)

        zero_vwap_with_activity = candle(day, "10.0867")
        zero_vwap_with_activity[5] = "0.0000"
        with self.assertRaisesRegex(
            MODULE.BreadthError, "CANDLE_TRADE_ACTIVITY_INVALID"
        ):
            MODULE.normalize_candle(
                zero_vwap_with_activity, 40, "1INCH/USD"
            )

        nonflat_no_trade = no_trade_candle(day, "0.0867")
        nonflat_no_trade[2] = "0.0868"
        with self.assertRaisesRegex(
            MODULE.BreadthError, "CANDLE_NO_TRADE_SENTINEL_INVALID"
        ):
            MODULE.normalize_candle(nonflat_no_trade, 40, "1INCH/USD")

    def test_new_listing_history_is_captured_but_rank_ineligible(self):
        vintage = dt.date(2026, 8, 20)
        pair_id = "USDGO/USD"
        one_finalized_and_current = source_payload(
            {
                pair_id: [
                    candle(vintage - dt.timedelta(days=1), "10.0000"),
                    no_trade_candle(vintage, "0.9997"),
                ],
                "last": int(
                    dt.datetime.combine(
                        vintage, dt.time(), tzinfo=dt.timezone.utc
                    ).timestamp()
                ),
            }
        )
        normalized = MODULE.normalize_ohlc(
            one_finalized_and_current,
            vintage,
            CONTRACT,
            MODULE.ohlc_file_name(pair_id),
        )
        self.assertEqual(normalized["response_rows"], 2)
        self.assertEqual(normalized["finalized_rows"], 1)
        self.assertFalse(normalized["ranking_history_complete"])
        self.assertIsNone(normalized["previous_finalized_day"])
        self.assertEqual(normalized["latest_finalized_day"], "2026-08-19")
        self.assertIsNone(normalized["trailing_usd_turnover"])

        empty = source_payload({pair_id: [], "last": 0})
        with self.assertRaisesRegex(
            MODULE.BreadthError, "OHLC_HISTORY_INVALID"
        ):
            MODULE.normalize_ohlc(
                empty,
                vintage,
                CONTRACT,
                MODULE.ohlc_file_name(pair_id),
            )

    def test_exact_universe_builds_raw_btc_and_alt_participation(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", target=3)
            snapshot = write_snapshot(Path(tmp) / "raw")
            result = MODULE.build_transform(
                snapshot, universe_policy_path=policy
            )

            self.assertEqual(result["as_of_date"], "2026-08-19")
            self.assertEqual(result["previous_date"], "2026-08-18")
            self.assertEqual(result["universe"]["selected_asset_count"], 3)
            self.assertEqual(result["universe"]["observed_asset_count"], 3)
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
                MODULE.BreadthError,
                "CAPTURE_COVERAGE_INCOMPLETE.*SOL/USD",
            ):
                MODULE.build_transform(
                    snapshot, universe_policy_path=policy
                )

    def test_unknown_taxonomy_member_stops_before_universe_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", target=3)
            snapshot = write_snapshot(
                Path(tmp) / "raw",
                prices={
                    "BTC": (100, 110, 500),
                    "ETH": (10, 12, 500),
                    "DOGE": (200, 210, 500),
                },
            )
            result = MODULE.build_transform(
                snapshot, universe_policy_path=policy
            )

            self.assertEqual(result["status"], "UNKNOWN")
            self.assertEqual(
                result["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN"
            )
            self.assertEqual(
                [
                    item["canonical_asset_id"]
                    for item in result["universe"][
                        "taxonomy_unknown_before_cutoff"
                    ]
                ],
                ["DOGE"],
            )
            self.assertIsNone(result["alt_participation"])

    def test_unverified_identity_is_excluded_not_unknown(self):
        # unverified_identity (policy_version v2, 2026-08-22): an
        # explicitly-classified excluded category, structurally distinct
        # from an unclassified (None) asset -- the gate skips past it and
        # keeps ranking toward target, it does not block the whole gate
        # UNKNOWN the way a genuinely unclassified asset does. NIGHT is
        # ranked 3rd (by descending turnover) among 6 candidates so the
        # target=4 selection loop genuinely passes through it before
        # completing -- proving skip-and-continue, not merely "never
        # reached".
        bases = ["BTC", "ETH", "NIGHT", "SOL", "DOGE", "XRP"]
        prices = {
            base: (100 - index, 101 - index, 999)
            for index, base in enumerate(bases)
        }
        categories = {
            "BTC": "eligible_crypto", "ETH": "eligible_crypto",
            "SOL": "eligible_crypto", "DOGE": "eligible_crypto",
            "XRP": "eligible_crypto", "NIGHT": "unverified_identity",
        }
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(Path(tmp) / "policy.json", target=4)
            taxonomy = write_taxonomy(Path(tmp) / "taxonomy.json", categories)
            snapshot = write_snapshot(
                Path(tmp) / "raw", prices=prices,
            )
            result = MODULE.build_transform(
                snapshot,
                universe_policy_path=policy,
                exclusion_taxonomy_path=taxonomy,
            )
            self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(result["universe"]["taxonomy_unknown_before_cutoff"], [])
            excluded_ids = [
                item["canonical_asset_id"]
                for item in result["universe"]["taxonomy_excluded_before_cutoff"]
            ]
            self.assertEqual(excluded_ids, ["NIGHT"])
            selected_ids = {
                item["canonical_asset_id"] for item in result["universe"]["members"]
            }
            self.assertEqual(selected_ids, {"BTC", "ETH", "SOL", "DOGE"})
            self.assertNotIn("NIGHT", selected_ids)
            # rank_before_taxonomy is preserved even for a skipped
            # candidate -- original ticker/rank/source are never lost.
            self.assertEqual(
                result["universe"]["taxonomy_excluded_before_cutoff"][0][
                    "rank_before_taxonomy"
                ],
                3,
            )

    def test_invalid_excluded_categories_list_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            taxonomy_path = Path(tmp) / "taxonomy.json"
            payload = json.loads(write_taxonomy(taxonomy_path, {}).read_text())
            # Typo/omission in the required category list -- not the
            # same as an unratified policy, must still fail closed.
            payload["excluded_categories"] = [
                "commodity_linked", "fiat", "stablecoin", "staked", "wrapped",
            ]  # missing unverified_identity
            taxonomy_path.write_text(json.dumps(payload, sort_keys=True))
            with self.assertRaisesRegex(MODULE.BreadthError, "TAXONOMY_POLICY_INVALID"):
                MODULE.load_exclusion_taxonomy(taxonomy_path)

    def test_unverified_identity_effective_date_gates_replay(self):
        # A record's own effective_from governs whether it is recognized
        # as of a given as_of date, independent of when the whole policy
        # document was ratified -- replay before vs. after the record's
        # effective_from must differ.
        record = {
            "canonical_asset_id": "NIGHT",
            "category": "unverified_identity",
            "effective_from": "2026-08-15",
            "effective_to": None,
            "reason": "test fixture: identity unresolved",
        }
        with tempfile.TemporaryDirectory() as tmp:
            taxonomy_path = Path(tmp) / "taxonomy.json"
            payload = json.loads(write_taxonomy(taxonomy_path, {}).read_text())
            payload["records"] = [record]
            taxonomy_path.write_text(json.dumps(payload, sort_keys=True))
            policy = MODULE.load_exclusion_taxonomy(taxonomy_path)
            self.assertIsNone(
                MODULE.taxonomy_category("NIGHT", dt.date(2026, 8, 14), policy)
            )
            self.assertEqual(
                MODULE.taxonomy_category("NIGHT", dt.date(2026, 8, 15), policy),
                "unverified_identity",
            )
            self.assertEqual(
                MODULE.taxonomy_category("NIGHT", dt.date(2026, 8, 22), policy),
                "unverified_identity",
            )

    def test_observation_coverage_gate_is_exactly_ninety_percent(self):
        bases = ["BTC"] + [f"A{i}" for i in range(1, 10)]
        prices = {
            base: (100 - index, 101 - index, 999)
            for index, base in enumerate(bases)
        }
        categories = {base: "eligible_crypto" for base in bases}
        with tempfile.TemporaryDirectory() as tmp:
            policy = write_policy(
                Path(tmp) / "policy.json", target=10, coverage_bps=9000
            )
            taxonomy = write_taxonomy(
                Path(tmp) / "taxonomy.json", categories
            )
            exact = write_snapshot(
                Path(tmp) / "exact",
                prices=prices,
                omit_latest_bases={"A9"},
            )
            observed = MODULE.build_transform(
                exact,
                universe_policy_path=policy,
                exclusion_taxonomy_path=taxonomy,
            )
            self.assertEqual(observed["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(
                observed["universe"]["observation_coverage_bps"], 9000
            )
            self.assertFalse(observed["universe"]["coverage_complete"])

            below = write_snapshot(
                Path(tmp) / "below",
                prices=prices,
                omit_latest_bases={"A8", "A9"},
            )
            unknown = MODULE.build_transform(
                below,
                universe_policy_path=policy,
                exclusion_taxonomy_path=taxonomy,
            )
            self.assertEqual(unknown["status"], "UNKNOWN")
            self.assertEqual(
                unknown["unknown_reason"],
                "OBSERVATION_COVERAGE_BELOW_90_PERCENT",
            )
            self.assertIsNone(unknown["btc_reference"])
            self.assertIsNone(unknown["alt_participation"])

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
            policy = write_policy(Path(tmp) / "policy.json", target=2)
            taxonomy = write_taxonomy(
                Path(tmp) / "taxonomy.json",
                {
                    "BTC": "eligible_crypto",
                    "ETH": "eligible_crypto",
                    "SOL": "eligible_crypto",
                },
            )
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
                root,
                universe_policy_path=policy,
                exclusion_taxonomy_path=taxonomy,
            )

            self.assertEqual(replay["point_count"], 2)
            self.assertEqual(replay["first_as_of_date"], "2026-08-18")
            self.assertEqual(replay["last_as_of_date"], "2026-08-19")
            self.assertEqual(
                [
                    point["universe"]["selected_asset_count"]
                    for point in replay["points"]
                ],
                [2, 2],
            )
            self.assertFalse(replay["current_catalog_backfill_authorized"])

    def test_checksum_manifest_and_append_only_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = write_snapshot(tmp)
            with self.assertRaisesRegex(
                MODULE.BreadthError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.build_manifest(
                    snapshot, "crypto-breadth-capture/v2"
                )

            pair_file = snapshot / CONTRACT["ohlc_bundle_raw_file"]
            with gzip.open(pair_file, "rb") as stream:
                tampered = stream.read() + b"\n"
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
            policy = write_policy(Path(tmp) / "policy.json", target=3)
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

    def test_live_capture_is_free_paced_append_only_and_factor_untracked(self):
        script = SCRIPT.read_text(encoding="utf-8")
        capture_script = (
            ROOT / ".github" / "scripts" / "crypto_breadth_capture.py"
        ).read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )

        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertIn("urllib.request", capture_script)
        self.assertIn("request_interval_seconds < 1", capture_script)
        self.assertIn("APPEND_ONLY_VIOLATION", capture_script)
        self.assertIn("crypto_breadth_capture.py", workflows)
        self.assertIn("--request-interval-seconds 1.05", workflows)
        self.assertIn("evidence/crypto/breadth/raw", workflows)
        self.assertNotIn("data/factors/crypto_breadth", workflows)


if __name__ == "__main__":
    unittest.main()
