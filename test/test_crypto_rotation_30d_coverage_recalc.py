"""RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1 regression (user ratification P1, 2026-09-15).

Covers: record binding, eligible days only (>= 2026-08-19), confirmed classifications
only, the write-once guard with idempotent verify, no price look-ahead, the rotation
30d strength read path with '재계산' mark propagation into packets / entry gate /
opportunity ledger rows, committed packets preferred, and the crypto regime
LEADERSHIP axis plus the natural leadership packets left byte-identical.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import importlib.util
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


R = load_module("crypto_rotation_30d_coverage_recalc_under_test", ROOT / "rotation" / "crypto_rotation_30d_coverage_recalc.py")
OL = load_module("rotation_opportunity_ledger_for_recalc_test", ROOT / "rotation" / "rotation_opportunity_ledger.py")
RC = OL.RC
CL = load_module("crypto_leadership_for_recalc_test", ROOT / ".github" / "scripts" / "crypto_leadership.py")
BREADTH_FIXTURE = load_module("crypto_breadth_fixture_for_recalc_test", ROOT / "test" / "test_crypto_breadth.py")

RECORD_SHA = "2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd"
RECORD_PATH = "evidence/authority/USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json"
NOW = dt.datetime(2026, 9, 15, 1, 0, tzinfo=dt.timezone.utc)
FIRST_AS_OF = dt.date(2026, 8, 18)   # one day before the eligible start
LAST_AS_OF = dt.date(2026, 9, 19)
NEW_EFFECTIVE = "2026-08-24"         # NEW is unclassified (point in time) on as_of 08-18..08-23


def write_json(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def price(asset: str, k: int) -> str:
    base = {"BTC": Decimal(100) + k, "ETH": Decimal(200) + k, "SOL": Decimal(300) + k,
            "NEW": (Decimal(150) * Decimal("1.03") ** k).quantize(Decimal("0.0001"))}
    return format(base[asset], "f")


def exclusion_taxonomy(new_effective=NEW_EFFECTIVE) -> dict:
    records = [
        {"canonical_asset_id": a, "category": "eligible_crypto", "effective_from": "2026-01-01",
         "effective_to": None, "reason": "fixture"}
        for a in ("BTC", "ETH", "SOL")
    ] + [{"canonical_asset_id": "NEW", "category": "eligible_crypto", "effective_from": new_effective,
          "effective_to": None, "reason": "fixture later-confirmed classification"}]
    return {
        "schema_version": 1, "policy_version": "crypto_breadth_exclusion_taxonomy/v2", "approval_status": "RATIFIED",
        "source_name": "kraken_spot_market_data", "effective_from": "2026-01-01", "eligible_category": "eligible_crypto",
        "excluded_categories": ["commodity_linked", "fiat", "stablecoin", "staked", "unverified_identity", "wrapped"],
        "unknown_asset_policy": "fail_closed_unknown",
        "records": sorted(records, key=lambda r: (r["canonical_asset_id"], r["effective_from"])),
    }


def confirmed_all(record):
    """Base records confirmed long before every snapshot; NEW confirmed 2026-08-24 00:00Z (not backdated)."""
    return confirmed_new_at("2026-08-24T00:00:00Z")(record)


def confirmed_new_at(new_committed: str):
    def lookup(record):
        committed = new_committed if record["canonical_asset_id"] == "NEW" else "2025-12-31T00:00:00Z"
        return {"commit": "f" * 40, "committed_at_utc": committed, "history_shallow": False}
    return lookup


PINNED_POINT_SHA256 = {
    "2026-08-21": "5a1bdfa8e381006cabbc32936d0296c5ab5931026d24632e885e5916d373e29a",
    "2026-08-22": "2c78e4ce2b7c143a7fedfa3f1e2facafb41360dac8956ce2021d3e12de422475",
    "2026-08-23": "ed19682888e606b4f46c8f3ffd67f7d48e5a0c5569a6bbc99b67dfa620baf2ca",
    "2026-08-24": "c59ef1abb9285648e25ec5f2315252b91488417c6f3fb592761a3a9941229719",
    "2026-08-25": "e116b7e3b424c4a147c001af5b695cacd4aad66a7109a15e7de8a24370ba53a2",
    "2026-08-26": "4960421461f6f7b0b34faf466c73cf5c2816efb7b53e8b8b68bffde310269f8d",
    "2026-08-28": "c0b48385dbe616ab60e9b15089063828fc35306e85eb051b7a22a06f031685a3",
    "2026-08-31": "d4a09ec4d7969a0de0622c4ec80f1ddad888a8def9e318e041e5eb62ebbbe41e",
    "2026-09-01": "870a1fb8d13dfd18d18fb29e975cae3a97684b5b735724737e92a829bd43d48e",
    "2026-09-02": "7c7e5becfbd72e0d90a42c4097bf06b0b4394d7c5ab2d4ab58a79e3a1f105900",
    "2026-09-03": "2f99340d0527470ce174d50a8a93dc4669dd1d60edd578e2d173a6dcbacb46d9",
    "2026-09-04": "477eb68a9a2ad34ccb4a01749cbf16f382b8424fca212a83825782d577adb4a5",
    "2026-09-05": "d044438c9efe3e0287a5b497e1327ae7de345c657f76333bd9ede3d7d8cf7a82",
    "2026-09-06": "d1646c9aade9c99ea15e30e6ef76a92532191a7fc2ab07b0dd91e52d48915441",
    "2026-09-07": "4e5535a36eb5fd2df0284f116f40cbf46dba3d6f92660072dfa5736f6ed223b2",
}


def append_taxonomy_record(root: Path, asset: str, effective_from: str) -> None:
    path = root / R.CONFIG_PATHS["exclusion_taxonomy"]
    value = json.loads(path.read_text(encoding="utf-8"))
    value["records"].append({"canonical_asset_id": asset, "category": "eligible_crypto", "effective_from": effective_from,
                             "effective_to": None, "reason": "unrelated later addition"})
    value["records"].sort(key=lambda r: (r["canonical_asset_id"], r["effective_from"]))
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def natural_packet(root: Path, end: str) -> dict:
    return CL.build_transform(
        root / R.RAW_RELATIVE_ROOT,
        contract_path=root / R.CONFIG_PATHS["leadership_contract"],
        universe_policy_path=root / R.CONFIG_PATHS["universe"],
        exclusion_taxonomy_path=root / R.CONFIG_PATHS["exclusion_taxonomy"],
        leadership_policy_path=root / R.CONFIG_PATHS["leadership_policy"],
        taxonomy_path=root / R.CONFIG_PATHS["sector_taxonomy"],
        identity_exceptions_path=root / R.CONFIG_PATHS["identity_exceptions"],
        end_date=end,
    )


def build_fixture(root: Path, first=FIRST_AS_OF, last=LAST_AS_OF, packets_from="2026-09-16") -> None:
    policy = RC.load_policy()
    config = OL.load_config()
    for relative in {
        R.CONFIG_RELATIVE_PATH, RECORD_PATH, RC.POLICY_RELATIVE_PATH, RC.STATE_MAPPING_RELATIVE_PATH,
        policy["ratification_record"]["repo_path"], policy["markets"]["KR"]["source"]["sector_policy_path"],
        OL.CONFIG_RELATIVE_PATH, config["entry_rule"]["repo_path"],
        R.CONFIG_PATHS["leadership_contract"], R.CONFIG_PATHS["identity_exceptions"],
        R.CONFIG_PATHS["leadership_policy"], R.CONFIG_PATHS["sector_taxonomy"],
    }:
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)
    BREADTH_FIXTURE.write_policy(root / R.CONFIG_PATHS["universe"], target=4)
    # Classification history: base records committed 2025-12-31, NEW committed 2026-08-24 00:00Z.
    base = exclusion_taxonomy()
    base["records"] = [r for r in base["records"] if r["canonical_asset_id"] != "NEW"]
    write_json(root / R.CONFIG_PATHS["exclusion_taxonomy"], base)
    git(root, "init", "-q")
    commit_taxonomy(root, "base classifications", "2025-12-31T00:00:00Z")
    write_json(root / R.CONFIG_PATHS["exclusion_taxonomy"], exclusion_taxonomy())
    commit_taxonomy(root, "NEW classification", "2026-08-24T00:00:00Z")
    raw = root / R.RAW_RELATIVE_ROOT
    raw.mkdir(parents=True, exist_ok=True)
    day, k = first, 1
    while day <= last:
        BREADTH_FIXTURE.write_snapshot(
            raw, vintage=(day + dt.timedelta(days=1)).isoformat(),
            prices={a: (price(a, k - 1), price(a, k), "9999") for a in ("BTC", "ETH", "SOL", "NEW")},
        )
        day += dt.timedelta(days=1)
        k += 1
    day = dt.date.fromisoformat(packets_from)
    while day <= last:
        target = root / "data/observations/crypto_leadership" / day.isoformat() / "packet.json"
        CL.write_output(natural_packet(root, day.isoformat()), target)
        day += dt.timedelta(days=1)


def git(root: Path, *args, date: str = "2026-09-20T00:00:00Z") -> None:
    import os
    import subprocess

    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
               GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    subprocess.run(["git", "-C", str(root), *args], check=True, env=env, capture_output=True)


def commit_taxonomy(root: Path, message: str, date: str) -> None:
    git(root, "add", R.CONFIG_PATHS["exclusion_taxonomy"], date=date)
    git(root, "commit", "-q", "-m", message, date=date)


def edit_taxonomy(root: Path, mutate, date: str = "2026-09-20T00:00:00Z") -> None:
    path = root / R.CONFIG_PATHS["exclusion_taxonomy"]
    value = json.loads(path.read_text(encoding="utf-8"))
    mutate(value["records"])
    value["records"].sort(key=lambda r: (r["canonical_asset_id"], r["effective_from"]))
    write_json(path, value)
    commit_taxonomy(root, "later edit", date)


def reset_caches() -> None:
    for module in (R, RC._coverage_recalc_module()):
        module._TRANSFORM_CACHE.clear()
        module._HISTORIES.clear()


def notices(root: Path) -> dict:
    """The notice document of the module instance the rotation layer actually uses."""
    return RC._coverage_recalc_module().notice_document(root)


def crypto_packets(root: Path) -> dict:
    return {p["as_of_date"]: p for p in RC.build_market("CRYPTO", root)}


def crypto_entities(packet: dict) -> dict:
    return {e["entity_id"]: e for scope in packet["scopes"] for e in scope["entities"]}


class FixtureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.template = Path(cls._tmp.name) / "template"
        build_fixture(cls.template)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "root"
        shutil.copytree(self.template, self.root)
        reset_caches()

    def tearDown(self):
        self.tmp.cleanup()

    def recalc(self, root=None, **kwargs):
        kwargs.setdefault("now", NOW)
        kwargs.setdefault("confirmed", confirmed_all)
        return R.recalculate(self.root if root is None else root, write=True, **kwargs)


class RecordBindingTests(unittest.TestCase):
    def test_record_is_byte_copied_and_config_is_bound(self):
        self.assertEqual(R.file_sha256(ROOT / RECORD_PATH), RECORD_SHA)
        config = R.load_config(ROOT)
        self.assertEqual(config["ratification_record"]["sha256"], RECORD_SHA)
        self.assertEqual(config["rule_id"], "RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1")
        self.assertIn("CRYPTO_MARKET_REGIME_LEADERSHIP_AXIS", config["not_applied_to"])
        self.assertEqual(config["eligible_observation_from"], "2026-08-19")
        self.assertFalse(config["current_catalog_backfill_authorized_changed"])

    def test_tampered_config_or_record_fails_closed(self):
        for mutate, code in (
            (lambda c: c["ratification_record"].update(sha256="0" * 64), "RECALC_RATIFICATION_RECORD_SHA_MISMATCH"),
            (lambda c: c.update(eligible_observation_from="2026-08-01"), "RECALC_ELIGIBLE_FROM_MISMATCH"),
            (lambda c: c["not_applied_to"].remove("CRYPTO_MARKET_REGIME_LEADERSHIP_AXIS"), "RECALC_SCOPE_MISMATCH"),
            (lambda c: c.update(write_once=False), "RECALC_WRITE_ONCE_OR_BACKFILL_FLAG_MISMATCH"),
            (lambda c: c["mark"].update(label_ko="recalc"), "RECALC_MARK_MISMATCH"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                for relative in (R.CONFIG_RELATIVE_PATH, RECORD_PATH):
                    (root / relative).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / relative, root / relative)
                value = json.loads((root / R.CONFIG_RELATIVE_PATH).read_text(encoding="utf-8"))
                mutate(value)
                write_json(root / R.CONFIG_RELATIVE_PATH, value)
                with self.assertRaisesRegex(R.CoverageRecalcError, code):
                    R.load_config(root)

    def test_global_backfill_flag_and_source_transforms_untouched(self):
        for relative in (".github/scripts/crypto_leadership.py", ".github/scripts/crypto_breadth.py",
                         "regime/crypto_paper_runtime.py", "regime/crypto_paper_runtime_publication.py"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("coverage_recalc", text, relative)
        self.assertNotIn('"current_catalog_backfill_authorized": True', (ROOT / ".github/scripts/crypto_leadership.py").read_text(encoding="utf-8"))
        universe = json.loads((ROOT / "config/crypto_global_universe_contract.json").read_text(encoding="utf-8"))
        self.assertIn('"current_catalog_backfill_authorized": false', json.dumps(universe))


class RecalculationTests(FixtureCase):
    def test_only_eligible_days_on_or_after_2026_08_19_are_recalculated(self):
        report = self.recalc()
        statuses = {d["as_of_date"]: d["status"] for d in report["days"]}
        self.assertEqual(statuses["2026-08-18"], "IGNORED_BEFORE_ELIGIBLE_FROM")
        for day in ("2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"):
            self.assertEqual(statuses[day], "RECALCULATED", day)
        self.assertEqual(statuses["2026-08-24"], "NOT_ELIGIBLE_POINT_IN_TIME")
        config = R.load_config(self.root)
        written = sorted(p.parent.name for p in (self.root / config["evidence_root"]).glob("*/point.json"))
        self.assertEqual(written, ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"])
        self.assertFalse(R.point_path(self.root, config, "2026-08-18").exists())
        point = R.load_point(R.point_path(self.root, config, "2026-08-19"))
        self.assertIs(point["recalculated"], True)
        self.assertEqual(point["mark_ko"], "재계산")
        self.assertEqual(point["ratification_record"]["sha256"], RECORD_SHA)
        self.assertEqual(point["point_in_time"]["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        self.assertEqual([r["canonical_asset_id"] for r in point["resolving_records"]], ["NEW"])
        self.assertEqual(point["resolving_records"][0]["effective_from"], NEW_EFFECTIVE)
        self.assertEqual(point["resolving_records"][0]["kind"], "EFFECTIVE_AFTER_DAY")
        self.assertEqual({r["canonical_asset_id"] for r in point["point_in_time_records"]}, {"BTC", "ETH", "SOL"})
        self.assertEqual(point["classification_source"]["sha256"], R.file_sha256(self.root / R.CONFIG_PATHS["exclusion_taxonomy"]))
        self.assertEqual(point["recalculation"]["recalculated_at_utc"], "2026-09-15T01:00:00Z")
        self.assertEqual(point["classification_confirmations"][0]["committed_at_utc"], "2026-08-24T00:00:00Z")
        self.assertEqual(point["recalculated_source_point"]["status"], "OBSERVED_UNCLASSIFIED")
        self.assertIn("NEW", {m["canonical_asset_id"] for m in point["recalculated_source_point"]["universe"]["members"]})

    def test_unconfirmed_or_later_confirmed_classification_is_not_used(self):
        report = self.recalc(confirmed=lambda record: None)
        self.assertNotIn("RECALCULATED", {d["status"] for d in report["days"]})
        self.assertEqual({d["status"] for d in report["days"] if "2026-08-19" <= d["as_of_date"] < NEW_EFFECTIVE},
                         {"STILL_UNKNOWN_AFTER_CONFIRMED_CLASSIFICATIONS"})
        late = lambda record: {"commit": "e" * 40, "committed_at_utc": "2026-09-16T00:00:00Z", "history_shallow": False}
        report = self.recalc(confirmed=late)
        self.assertNotIn("RECALCULATED", {d["status"] for d in report["days"]})
        config = R.load_config(self.root)
        self.assertEqual(list((self.root / config["evidence_root"]).glob("*/point.json")) if (self.root / config["evidence_root"]).exists() else [], [])

    def test_one_time_guard_refuses_second_run_and_verify_is_idempotent(self):
        self.recalc()
        config = R.load_config(self.root)
        path = R.point_path(self.root, config, "2026-08-21")
        before = path.read_bytes()
        again = self.recalc(now=NOW + dt.timedelta(days=1))
        self.assertEqual(again["refused_already_recalculated"], ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"])
        self.assertEqual(path.read_bytes(), before)
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = R.run(["recalc", "--root", str(self.root), "--as-of", "2026-08-21", "--write"])
        self.assertEqual(code, 4)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(R.verify(self.root), [])
        self.assertEqual(R.verify(self.root), [])
        tampered = json.loads(before)
        tampered["recalculated_source_point"]["universe"]["members"][0]["latest_close"] = "1"
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = R.payload_sha256(tampered)
        path.write_bytes(R.render_json(tampered))
        self.assertIn("BODY_MISMATCH:2026-08-21", R.verify(self.root))

    def test_backdated_record_committed_after_snapshot_is_later_confirmed(self):
        # NEW effective 2026-08-19 (backdated) but committed 2026-09-01 -> not point in time before that.
        write_json(self.root / R.CONFIG_PATHS["exclusion_taxonomy"], exclusion_taxonomy("2026-08-19"))
        report = self.recalc(confirmed=confirmed_new_at("2026-09-01T00:00:00Z"))
        statuses = {d["as_of_date"]: d for d in report["days"]}
        self.assertEqual(statuses["2026-08-25"]["status"], "RECALCULATED")
        self.assertEqual(statuses["2026-08-25"]["backdated_assets"], ["NEW"])
        self.assertEqual(statuses["2026-08-30"]["status"], "RECALCULATED")   # vintage 08-31 00:30Z < commit
        self.assertEqual(statuses["2026-08-31"]["status"], "NOT_ELIGIBLE_POINT_IN_TIME")  # vintage 09-01 00:30Z > commit
        point = R.load_point(R.point_path(self.root, R.load_config(self.root), "2026-08-25"))
        self.assertEqual(point["resolving_records"][0]["kind"], "BACKDATED_COMMITTED_AFTER_SNAPSHOT")
        self.assertEqual(point["classification_confirmations"][0]["committed_at_utc"], "2026-09-01T00:00:00Z")
        self.assertNotIn("NEW", {r["canonical_asset_id"] for r in point["point_in_time_records"]})
        self.assertEqual(R.verify(self.root), [])

    def test_unrelated_later_classification_addition_keeps_verify_and_packets(self):
        self.recalc()
        before = {d: RC.render_json(p) for d, p in crypto_packets(self.root).items()}
        append_taxonomy_record(self.root, "ZZZ", "2026-12-01")
        reset_caches()
        self.assertEqual(R.verify(self.root), [])
        after = {d: RC.render_json(p) for d, p in crypto_packets(self.root).items()}
        self.assertEqual(after, before)

    def test_shallow_or_missing_history_is_refused(self):
        shutil.rmtree(self.root / ".git")
        with self.assertRaisesRegex(R.CoverageRecalcError, "CLASSIFICATION_HISTORY_UNAVAILABLE"):
            R.recalculate(self.root, write=True, now=NOW)
        import subprocess
        origin = Path(self.tmp.name) / "origin"
        (origin / "config").mkdir(parents=True)
        shutil.copyfile(self.root / R.CONFIG_PATHS["exclusion_taxonomy"], origin / R.CONFIG_PATHS["exclusion_taxonomy"])
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
               "PATH": __import__("os").environ.get("PATH", "")}
        for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "a"]):
            subprocess.run(["git", "-C", str(origin), *args], check=True, env=env, capture_output=True)
        (origin / "x.txt").write_text("x", encoding="utf-8")
        for args in (["add", "."], ["commit", "-q", "-m", "b"]):
            subprocess.run(["git", "-C", str(origin), *args], check=True, env=env, capture_output=True)
        shallow = Path(self.tmp.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1", origin.as_uri(), str(shallow)], check=True, env=env, capture_output=True)
        with self.assertRaisesRegex(R.CoverageRecalcError, "REFUSED_SHALLOW_HISTORY"):
            R.git_confirmations(shallow)
        full = R.git_confirmations(origin)
        info = full({"canonical_asset_id": "NEW", "category": "eligible_crypto", "effective_from": NEW_EFFECTIVE, "effective_to": None})
        self.assertIs(info["history_shallow"], False)

    def test_no_price_lookahead(self):
        self.recalc()
        config = R.load_config(self.root)
        # Same body when every snapshot after the day's own vintage is absent.
        truncated = Path(self.tmp.name) / "truncated"
        shutil.copytree(self.root, truncated)
        shutil.rmtree(truncated / config["evidence_root"])
        for snapshot in (truncated / R.RAW_RELATIVE_ROOT).iterdir():
            if snapshot.name > "2026-08-22":
                shutil.rmtree(snapshot)
        report = self.recalc(root=truncated)
        self.assertEqual([d["as_of_date"] for d in report["days"] if d["status"] == "RECALCULATED"],
                         ["2026-08-19", "2026-08-20", "2026-08-21"])
        full = R.load_point(R.point_path(self.root, config, "2026-08-21"))
        cut = R.load_point(R.point_path(truncated, config, "2026-08-21"))
        self.assertEqual(full["body_sha256"], cut["body_sha256"])
        # Prices are the as-captured closes of vintage d+1 and never after d.
        self.assertEqual(full["snapshot"]["vintage_date"], "2026-08-22")
        self.assertLessEqual(full["prices_point_in_time"]["latest_finalized_day_max"], "2026-08-21")
        k = (dt.date(2026, 8, 21) - FIRST_AS_OF).days + 1
        closes = {m["canonical_asset_id"]: (m["previous_close"], m["latest_close"]) for m in full["recalculated_source_point"]["universe"]["members"]}
        self.assertEqual(Decimal(closes["NEW"][1]), Decimal(price("NEW", k)))
        self.assertEqual(Decimal(closes["BTC"][0]), Decimal(price("BTC", k - 1)))


class RotationReadPathTests(FixtureCase):
    def test_without_points_the_natural_unknown_stands(self):
        packets = crypto_packets(self.root)
        for day in ("2026-09-16", "2026-09-17", "2026-09-18", "2026-09-19"):
            self.assertEqual(packets[day]["observation"]["status"], "UNKNOWN", day)
            self.assertNotIn("coverage_recalculation", packets[day]["observation"])

    def test_recalculated_30d_strength_and_mark_propagation(self):
        self.recalc()
        packets = crypto_packets(self.root)
        # Window starting before 2026-08-19 keeps its natural blockers (no mark).
        self.assertEqual(packets["2026-09-16"]["observation"]["status"], "UNKNOWN")
        self.assertNotIn("coverage_recalculation", packets["2026-09-16"]["observation"])
        first = packets["2026-09-17"]["observation"]
        self.assertEqual(first["status"], "OBSERVED")
        mark = first["coverage_recalculation"]
        self.assertEqual((mark["recalculated"], mark["mark_ko"], mark["ratification_record_sha256"]), (True, "재계산", RECORD_SHA))
        self.assertEqual([d["as_of_date"] for d in mark["recalculated_days"]],
                         ["2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"])
        self.assertTrue(all(d["recalculated_at_utc"] == "2026-09-15T01:00:00Z" for d in mark["recalculated_days"]))
        self.assertTrue(any(s["path"].endswith("2026-08-19/point.json") for s in first["sources"]))
        bindings = mark["rebuild_bindings"]
        self.assertEqual(bindings["identity_exceptions_sha256"], R.file_sha256(self.root / R.CONFIG_PATHS["identity_exceptions"]))
        self.assertEqual(set(bindings), {"classification_view", "visited_classifications_sha256", "identity_exceptions_sha256"})
        # The recalculated window equals the unmodified CR-07 window with NEW classified all along.
        reference_root = Path(self.tmp.name) / "reference"
        shutil.copytree(self.root, reference_root)
        write_json(reference_root / R.CONFIG_PATHS["exclusion_taxonomy"], exclusion_taxonomy("2026-01-01"))
        reference = next(w for w in natural_packet(reference_root, "2026-09-17")["windows"] if w["window_id"] == "primary_30d")
        self.assertEqual(reference["status"], "OBSERVED_UNCLASSIFIED")
        strengths = {e["entity_id"]: e["strength"] for e in crypto_entities(packets["2026-09-17"]).values()}
        self.assertEqual(strengths, {row["group_id"]: row["relative_strength_vs_btc"] for row in reference["group_relative_strength"]["bucket"]})

        top = next(e for e in crypto_entities(packets["2026-09-18"]).values() if e["rank"] == 1)
        self.assertEqual(top["state"], "STRONG_CONFIRMED")
        self.assertEqual(top["coverage_recalculation"]["depends_on_recalculated_observation_dates"], ["2026-09-17", "2026-09-18"])
        self.assertEqual(top["coverage_recalculation"]["mark_ko"], "재계산")
        gate = next(g for g in packets["2026-09-18"]["entry_gate_view"] if g["entity_id"] == top["entity_id"])
        self.assertEqual(gate["coverage_recalculation"], top["coverage_recalculation"])
        self.assertIn(("RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1", RECORD_SHA),
                      {(r["rule_id"], r["source_record_sha256"]) for r in gate["rule_refs"]})
        held = crypto_entities(packets["2026-09-19"])[top["entity_id"]]
        self.assertEqual(held["state"], "STRONG_HELD")
        self.assertEqual(held["coverage_recalculation"]["depends_on_recalculated_observation_dates"],
                         ["2026-09-17", "2026-09-18", "2026-09-19"])
        # Recalculated daily points never feed the lagging warning.
        self.assertEqual({e["lagging"]["status"] for e in crypto_entities(packets["2026-09-19"]).values()} - {"EXCLUDED_BENCHMARK"}, {"UNKNOWN"})
        for packet in packets.values():
            RC.validate_packet(packet)

    def test_marks_follow_state_machine_semantics(self):
        policy = RC.load_policy()
        mapping = RC.load_state_mapping()
        mark = {"rule_id": R.RULE_ID, "ratification_record_sha256": RECORD_SHA}

        def obs(day, ranking, recalculated):
            order = list(ranking)
            item = {"as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [],
                    "scopes": {"BTC_RELATIVE_BUCKETS": [
                        {"entity_id": e, "source_identity": e, "strength": str(3 - i)} for i, e in enumerate(order)]},
                    "aux": {}}
            if recalculated:
                item["coverage_recalculation"] = dict(mark)
            return item

        observations = [
            obs("2026-09-17", ["ALT", "ETH", "BTC"], True),
            obs("2026-09-18", ["ALT", "ETH", "BTC"], False),
            obs("2026-09-19", ["ALT", "ETH", "BTC"], False),
            obs("2026-09-20", ["ETH", "BTC", "ALT"], False),
            obs("2026-09-21", ["ETH", "ALT", "BTC"], False),
            obs("2026-09-22", ["ETH", "ALT", "BTC"], False),
        ]
        packets = {p["as_of_date"]: p for p in RC.build_market_packets(policy, "CRYPTO", observations, mapping)}
        alt = lambda day: crypto_entities(packets[day])["ALT"]
        eth = lambda day: crypto_entities(packets[day])["ETH"]
        self.assertEqual(alt("2026-09-18")["state"], "STRONG_CONFIRMED")
        self.assertEqual(alt("2026-09-18")["coverage_recalculation"]["depends_on_recalculated_observation_dates"], ["2026-09-17"])
        self.assertEqual(alt("2026-09-19")["coverage_recalculation"]["depends_on_recalculated_observation_dates"], ["2026-09-17"])
        self.assertEqual(alt("2026-09-20")["state"], "STRONG_RELEASED")  # bottom once
        self.assertEqual(alt("2026-09-20")["coverage_recalculation"]["depends_on_recalculated_observation_dates"], ["2026-09-17"])
        self.assertNotIn("coverage_recalculation", alt("2026-09-21"))
        self.assertEqual(eth("2026-09-21")["state"], "STRONG_CONFIRMED")  # natural observations only
        self.assertNotIn("coverage_recalculation", eth("2026-09-21"))
        self.assertNotIn("coverage_recalculation", packets["2026-09-18"]["observation"])
        self.assertIn("coverage_recalculation", packets["2026-09-17"]["observation"])

    def test_opportunity_ledger_rows_carry_the_mark(self):
        self.recalc()
        write_json(self.root / "evidence/regime/paper_reference/2026-09-20/2026-09-20T220000Z/packet.json", {
            "generated_at": "2026-09-20T22:00:00Z", "generation_id": "g", "payload_sha256": "1" * 64,
            "markets": [{"market": "CRYPTO", "as_of_date": d, "paper_reference": {"candidate_regime": "RISK_ON"}, "runtime_regime": "RISK_ON"}
                        for d in ("2026-09-18", "2026-09-19", "2026-09-20")],
        })
        days = {d["as_of_date"]: d for d in OL.build_market_days("CRYPTO", self.root)}
        rows = days["2026-09-18"]["opportunities"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["coverage_recalculation"]["mark_ko"], "재계산")
        self.assertIn("RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1", {r["rule_id"] for r in rows[0]["rule_refs"]})
        self.assertEqual(days["2026-09-18"]["confirmation"]["coverage_recalculation"]["mark_ko"], "재계산")
        self.assertNotIn("coverage_recalculation", days["2026-09-16"]["confirmation"])

    def test_committed_packets_without_mark_are_preferred(self):
        packets = RC.build_market("CRYPTO", self.root)
        with redirect_stdout(io.StringIO()):
            RC.write_market("CRYPTO", [p for p in packets if p["as_of_date"] <= "2026-09-17"], self.root)
        committed = RC.evidence_path(self.root, "CRYPTO", "2026-09-17").read_bytes()
        self.recalc()
        rebuilt = {p["as_of_date"]: p for p in RC.build_market("CRYPTO", self.root)}
        self.assertEqual(RC.render_json(rebuilt["2026-09-17"]), committed)
        with redirect_stdout(io.StringIO()):
            RC.write_market("CRYPTO", list(rebuilt.values()), self.root)  # no append-only conflict
        self.assertEqual(RC.evidence_path(self.root, "CRYPTO", "2026-09-17").read_bytes(), committed)
        self.assertEqual(rebuilt["2026-09-18"]["observation"]["status"], "OBSERVED")
        self.assertIn("coverage_recalculation", rebuilt["2026-09-18"]["observation"])

    def test_natural_leadership_packets_and_regime_leadership_inputs_unchanged(self):
        before = {day: R.render_json(natural_packet(self.root, day)) for day in ("2026-09-17", "2026-09-19")}
        committed = {p: p.read_bytes() for p in (self.root / "data/observations/crypto_leadership").glob("*/packet.json")}
        self.recalc()
        RC.build_market("CRYPTO", self.root)
        for day, data in before.items():
            self.assertEqual(R.render_json(natural_packet(self.root, day)), data)
        self.assertEqual({p: p.read_bytes() for p in committed}, committed)


class FrozenReplayTests(FixtureCase):
    def commit_all_crypto_packets(self):
        packets = RC.build_market("CRYPTO", self.root)
        with redirect_stdout(io.StringIO()):
            RC.write_market("CRYPTO", packets, self.root)
        return {p.parent.name: p.read_bytes() for p in (self.root / RC.EVIDENCE_RELATIVE_ROOT / "CRYPTO").glob("*/packet.json")}

    def test_later_taxonomy_edits_keep_replay_and_verify_green(self):
        self.recalc()
        committed = self.commit_all_crypto_packets()
        self.assertIn("coverage_recalculation", json.loads(committed["2026-09-18"])["observation"])
        fresh_before = {d: RC.render_json(p) for d, p in crypto_packets(Path(shutil.copytree(self.root, Path(self.tmp.name) / "fresh0",
                        ignore=shutil.ignore_patterns("confirmation")))).items()}
        self.assertIn("coverage_recalculation", json.loads(fresh_before["2026-09-18"])["observation"])
        edits = (
            ("backdated unrelated addition", lambda records: records.append({
                "canonical_asset_id": "ZZZ", "category": "stablecoin", "effective_from": "2026-08-01",
                "effective_to": None, "reason": "backdated unrelated"})),
            ("future closure", lambda records: next(r for r in records if r["canonical_asset_id"] == "SOL").update(effective_to="2027-12-31")),
            ("reason-only edit", lambda records: next(r for r in records if r["canonical_asset_id"] == "BTC").update(reason="edited reason")),
            ("NEW future closure", lambda records: next(r for r in records if r["canonical_asset_id"] == "NEW").update(effective_to="2027-06-30")),
        )
        for label, mutate in edits:
            with self.subTest(label):
                edit_taxonomy(self.root, mutate)
                reset_caches()
                rebuilt = RC.build_market("CRYPTO", self.root)
                with redirect_stdout(io.StringIO()):
                    RC.write_market("CRYPTO", rebuilt, self.root)  # no APPEND_ONLY_EVIDENCE_CONFLICT
                self.assertEqual({p["as_of_date"]: RC.render_json(p) for p in rebuilt}, committed)
                self.assertEqual(R.verify(self.root), [])
                self.assertEqual(R.classification_drift(self.root), [])
                document = notices(self.root)
                self.assertEqual(document["notices"], [], label)
                # A fresh replay without committed packets is frozen too (history before each snapshot).
                fresh = Path(self.tmp.name) / f"fresh-{len(label)}"
                shutil.copytree(self.root, fresh, ignore=shutil.ignore_patterns("confirmation"))
                reset_caches()
                self.assertEqual({d: RC.render_json(p) for d, p in crypto_packets(fresh).items()}, fresh_before)

    def test_real_classification_drift_is_a_notice_not_a_failure(self):
        self.recalc()
        committed = self.commit_all_crypto_packets()
        edit_taxonomy(self.root, lambda records: next(r for r in records if r["canonical_asset_id"] == "NEW").update(category="stablecoin"))
        reset_caches()
        rebuilt = {p["as_of_date"]: RC.render_json(p) for p in RC.build_market("CRYPTO", self.root)}
        self.assertEqual(rebuilt, committed)
        self.assertEqual(R.verify(self.root), [])
        drift = R.classification_drift(self.root)
        self.assertEqual({row["as_of_date"] for row in drift}, {"2026-08-19", "2026-08-20", "2026-08-21", "2026-08-22", "2026-08-23"})
        self.assertIn("RECORDED_CLASSIFICATION_CHANGED_LATER", {n["code"] for n in notices(self.root)["notices"]})

    def test_altered_committed_strengths_are_never_reused_silently(self):
        self.recalc()
        committed = self.commit_all_crypto_packets()
        path = RC.evidence_path(self.root, "CRYPTO", "2026-09-18")
        packet = json.loads(committed["2026-09-18"])
        self.assertTrue(packet["observation"]["coverage_recalculation"]["entity_strengths_sha256"])
        # Tamper: change an entity strength and recompute the payload sha.
        packet["scopes"][0]["entities"][0]["strength"] = "9.999999999999"
        packet.pop("payload_sha256")
        packet["payload_sha256"] = RC.payload_sha256(packet)
        path.write_bytes(RC.render_json(packet))
        reset_caches()
        rebuilt = {p["as_of_date"]: p for p in RC.build_market("CRYPTO", self.root)}
        self.assertNotEqual(RC.render_json(rebuilt["2026-09-18"]), path.read_bytes())  # rebuilt live, not reused
        codes = {n["code"] for n in notices(self.root)["notices"]}
        self.assertIn("COMMITTED_RECALCULATED_PACKET_INCONSISTENT", codes)
        with redirect_stdout(io.StringIO()), self.assertRaisesRegex(RC.RotationConfirmationError, "APPEND_ONLY_EVIDENCE_CONFLICT"):
            RC.write_market("CRYPTO", list(rebuilt.values()), self.root)

    def test_live_strength_difference_on_reuse_writes_notice(self):
        self.recalc()
        self.commit_all_crypto_packets()
        # A consistent committed packet whose live rebuild now differs (a snapshot-level change) -> notice only.
        path = RC.evidence_path(self.root, "CRYPTO", "2026-09-18")
        packet = json.loads(path.read_text(encoding="utf-8"))
        packet["scopes"][0]["entities"][0]["strength"] = "9.999999999999"
        mark = packet["observation"]["coverage_recalculation"]
        mark["entity_strengths_sha256"] = RC.payload_sha256(R.committed_entity_strengths(packet))
        packet.pop("payload_sha256")
        packet["payload_sha256"] = RC.payload_sha256(packet)
        path.write_bytes(RC.render_json(packet))
        reset_caches()
        RC.build_market("CRYPTO", self.root)
        codes = {n["code"] for n in notices(self.root)["notices"]}
        self.assertIn("COMMITTED_RECALCULATED_PACKET_STRENGTH_DRIFT", codes)

    def test_history_unavailable_is_explicit_crypto_unknown_and_committed_packets_stay(self):
        self.recalc()
        committed = self.commit_all_crypto_packets()
        shutil.rmtree(self.root / ".git")
        reset_caches()
        rebuilt = {p["as_of_date"]: RC.render_json(p) for p in RC.build_market("CRYPTO", self.root)}
        self.assertEqual(rebuilt, committed)
        fresh = Path(self.tmp.name) / "nohistory"
        shutil.copytree(self.root, fresh, ignore=shutil.ignore_patterns("confirmation"))
        reset_caches()
        packets = crypto_packets(fresh)
        self.assertEqual(packets["2026-09-17"]["observation"]["unknown_reason"], R.UNAVAILABLE_REASON)
        self.assertIn(R.UNAVAILABLE_REASON, {n["code"] for n in notices(fresh)["notices"]})

    def test_crypto_drift_cannot_fail_kr_us_builds(self):
        policy = RC.load_policy()
        us = policy["markets"]["US"]["entities"]
        for day in ("2026-09-16", "2026-09-17", "2026-09-18"):
            etfs = [{"symbol": s, "as_of_session_date": day, "available_session_count": 60,
                     "relative_to_spy_pct": {"20_session_pct": str(20 - i)}} for i, s in enumerate(us)]
            write_json(self.root / "evidence/free_market_data/derived" / day / "manifest.json",
                       {"us_market_reference": {"sector_etfs": etfs, "payload_sha256": "0" * 64}})
        self.recalc()
        # Crypto-side trouble: corrupt a recalculated point and drop the classification history.
        point = R.point_path(self.root, R.load_config(self.root), "2026-08-21")
        point.write_text(point.read_text(encoding="utf-8").replace('"mark_ko": "재계산"', '"mark_ko": "x"', 1), encoding="utf-8")
        shutil.rmtree(self.root / ".git")
        reset_caches()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = RC.run(["build", "--market", "US", "--market", "KR", "--market", "CRYPTO", "--write", "--no-portal", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(len(list((self.root / RC.EVIDENCE_RELATIVE_ROOT / "US").glob("*/packet.json"))), 3)
        notice = json.loads((self.root / RC.CRYPTO_COVERAGE_RECALC_NOTICE_RELATIVE_PATH).read_text(encoding="utf-8"))
        self.assertEqual(notice["market"], "CRYPTO")
        self.assertTrue(notice["notices"])
        for market in ("US", "KR", "CRYPTO"):
            RC.build_market(market, self.root)  # the shared replay does not raise


class RepositoryEvidenceTests(unittest.TestCase):
    """Committed evidence: points reproduce and the regime LEADERSHIP axis inputs are untouched."""

    def test_committed_points_verify(self):
        config = R.load_config(ROOT)
        points = R.committed_points(ROOT, config)
        self.assertTrue(all(day >= "2026-08-19" for day in points))
        # 2026-08-19/20 stay UNKNOWN: TLM, TRU, NANO, ADI have no confirmed classification.
        self.assertEqual(sorted(points), [
            "2026-08-21", "2026-08-22", "2026-08-23", "2026-08-24", "2026-08-25", "2026-08-26", "2026-08-28",
            "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06", "2026-09-07",
        ])
        self.assertTrue(all(p["point"]["recalculated"] is True and p["point"]["mark_ko"] == "재계산" for p in points.values()))
        self.assertEqual(R.verify(ROOT), [])

    def test_committed_point_bytes_are_pinned_append_only(self):
        config = R.load_config(ROOT)
        actual = {p.parent.name: R.file_sha256(p) for p in (ROOT / config["evidence_root"]).glob("*/point.json")}
        self.assertEqual(actual, PINNED_POINT_SHA256)
        for day in ("2026-09-06", "2026-09-07"):
            point = R.load_point(R.point_path(ROOT, config, day))
            self.assertEqual(sorted(r["canonical_asset_id"] for r in point["resolving_records"] if r["kind"] == "BACKDATED_COMMITTED_AFTER_SNAPSHOT"),
                             ["CHIP", "QUID", "SN8"])
            self.assertTrue(all(c["history_shallow"] is False for c in point["classification_confirmations"]))

    def test_regime_leadership_axis_is_byte_identical_without_recalc_evidence(self):
        from regime import crypto_paper_runtime_publication as PUB

        raw = ROOT / R.RAW_RELATIVE_ROOT
        if not raw.is_dir():
            self.skipTest("breadth raw snapshots not checked out")
        latest = max(p.name for p in raw.iterdir() if p.is_dir())
        decision = dt.date.fromisoformat(latest)
        with tempfile.TemporaryDirectory() as tmp:
            bare = Path(tmp)
            (bare / R.RAW_RELATIVE_ROOT).parent.mkdir(parents=True)
            (bare / R.RAW_RELATIVE_ROOT).symlink_to(raw, target_is_directory=True)
            with_points = json.dumps(PUB._leadership(ROOT, decision), sort_keys=True)
            without_points = json.dumps(PUB._leadership(bare, decision), sort_keys=True)
            self.assertEqual(with_points, without_points)
            self.assertEqual(json.dumps(PUB._breadth(ROOT, decision), sort_keys=True),
                             json.dumps(PUB._breadth(bare, decision), sort_keys=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
