"""PAPER entry opportunity ledger (RULE.ENTRY.PAPER_BASELINE_B.V1) regression."""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OL = load_module("rotation_opportunity_ledger_under_test", ROOT / "rotation" / "rotation_opportunity_ledger.py")
RC = OL.RC
CONFIG = OL.load_config()
POLICY = RC.load_policy()
US = POLICY["markets"]["US"]["entities"]
ENTRY_SHA = "b2a905c4eaf23d44749d3e5bcd59b2efe34ff0b0ab5c955a8ce1e0870163154f"


def copy_contracts(root: Path) -> None:
    for relative in (
        RC.POLICY_RELATIVE_PATH, OL.CONFIG_RELATIVE_PATH, RC.STATE_MAPPING_RELATIVE_PATH,
        POLICY["ratification_record"]["repo_path"], CONFIG["entry_rule"]["repo_path"],
        POLICY["markets"]["KR"]["source"]["sector_policy_path"],
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def us_manifest(root: Path, capture: str, session: str, ranking: list) -> None:
    order = list(ranking) + [s for s in US if s not in ranking]
    etfs = [
        {"symbol": s, "as_of_session_date": session, "available_session_count": 60,
         "relative_to_spy_pct": {"20_session_pct": str(20 - i)}}
        for i, s in enumerate(order)
    ]
    write_json(root / "evidence/free_market_data/derived" / capture / "manifest.json",
               {"us_market_reference": {"sector_etfs": etfs, "payload_sha256": "0" * 64}})


def paper_reference(root: Path, folder: str, generated_at: str, rows: list) -> None:
    write_json(root / "evidence/regime/paper_reference" / folder / generated_at.replace(":", "") / "packet.json", {
        "generated_at": generated_at, "generation_id": generated_at, "payload_sha256": "1" * 64,
        "markets": [{"market": m, "as_of_date": d, "paper_reference": {"candidate_regime": r}, "runtime_regime": "UNKNOWN"}
                    for m, d, r in rows],
    })


def iex_bars(root: Path, capture: str, symbol_sessions: dict) -> None:
    responses = {}
    for symbol, sessions in symbol_sessions.items():
        bars = []
        for index, session in enumerate(sessions):
            close = 100 + index
            bars.append({"t": f"{session}T04:00:00Z", "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 1})
        responses[symbol] = {"symbol": symbol, "bars": bars}
    path = root / "evidence/free_market_data/raw" / capture / "alpaca_iex_daily_bars.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"responses": responses}, handle)


def sessions_until(end_day: int, count: int) -> list:
    import datetime as dt
    end = dt.date(2026, 9, end_day)
    return [(end - dt.timedelta(days=count - 1 - i)).isoformat() for i in range(count)]


class ConfigTests(unittest.TestCase):
    def test_bound_to_entry_baseline_b_record(self):
        entry = CONFIG["entry_rule"]
        self.assertEqual((entry["rule_id"], entry["sha256"]), ("RULE.ENTRY.PAPER_BASELINE_B.V1", ENTRY_SHA))
        self.assertEqual(RC.file_sha256(ROOT / entry["repo_path"]), ENTRY_SHA)
        self.assertFalse(CONFIG["authority"]["entry_edge_claimed"])
        self.assertEqual(CONFIG["t2"]["status"], "PENDING")

    def test_tamper_fails_closed(self):
        for mutate, code in (
            (lambda c: c["t2"].update(status="PASS"), "T2_STATUS_REQUIRES_REAL_T2_OUTPUT"),
            (lambda c: c["market_state"]["new_buys_by_market_state"].update(RISK_OFF="PERMIT"), "ALLOCATION_V2_NEW_BUY_TABLE_MISMATCH"),
            (lambda c: c["authority"].update(entry_edge_claimed=True), "ENTRY_EDGE_CLAIM_FORBIDDEN"),
            (lambda c: c["entry_rule"].update(sha256="0" * 64), "ENTRY_RATIFICATION_RECORD_SHA_MISMATCH"),
        ):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                copy_contracts(root)
                value = json.loads((root / OL.CONFIG_RELATIVE_PATH).read_text(encoding="utf-8"))
                mutate(value)
                write_json(root / OL.CONFIG_RELATIVE_PATH, value)
                with self.assertRaisesRegex(OL.OpportunityLedgerError, code):
                    OL.load_config(root)


class SyntheticLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        copy_contracts(self.root)
        us_manifest(self.root, "2026-09-01", "2026-09-01", ["XLK", "XLE", "XLV"])
        us_manifest(self.root, "2026-09-02", "2026-09-02", ["XLK", "XLE", "XLF"])
        us_manifest(self.root, "2026-09-03", "2026-09-03", ["XLK", "XLE", "XLF"])
        history = sessions_until(3, 30)
        iex_bars(self.root, "2026-09-02", {s: [d for d in history if d <= "2026-09-02"] for s in US})
        iex_bars(self.root, "2026-09-03", {s: history for s in US})

    def tearDown(self):
        self.tmp.cleanup()

    def build(self):
        return OL.build_market_days("US", self.root)

    def test_day_is_written_only_when_market_state_is_final(self):
        self.assertEqual(self.build(), [])
        paper_reference(self.root, "2026-09-02", "2026-09-02T22:00:00Z", [("US", "2026-09-02", "NEUTRAL")])
        days = self.build()
        self.assertEqual([d["as_of_date"] for d in days], ["2026-09-01", "2026-09-02"])
        self.assertEqual(days[0]["market_state"]["status"], "UNKNOWN")  # later as-of exists -> finalized UNKNOWN
        self.assertEqual(days[0]["market_state"]["new_buy_verdict"], "DENY")
        day = days[1]
        self.assertEqual(day["market_state"]["candidate_regime"], "NEUTRAL")
        self.assertEqual([o["entity_id"] for o in day["opportunities"]], ["XLK", "XLE"])
        for row in day["opportunities"]:
            self.assertEqual((row["eligibility"], row["market_state_new_buy"], row["t2"]["status"]),
                             ("ELIGIBLE_PENDING_T2", "PERMIT_SELECTIVE", "PENDING"))
            self.assertEqual(set(row["forward_tracking"]["forward_returns"].values()), {None})
            self.assertIsNone(row["forward_tracking"]["entry_reference_price"])
            self.assertIn(("RULE.ENTRY.PAPER_BASELINE_B.V1", "APPLIED"), [(r["rule_id"], r["role"]) for r in row["rule_refs"]])
            self.assertIn(("RULE.ROTATION.US.V1P", "APPLIED"), [(r["rule_id"], r["role"]) for r in row["rule_refs"]])
        # a later regeneration for the same as-of does not rewrite the first known state
        paper_reference(self.root, "2026-09-03", "2026-09-03T09:00:00Z", [("US", "2026-09-02", "RISK_OFF")])
        again = self.build()
        self.assertEqual(RC.render_json(again[1]), RC.render_json(day))

    def test_market_state_deny_is_recorded_as_blocked_opportunity(self):
        paper_reference(self.root, "2026-09-02", "2026-09-02T22:00:00Z", [("US", "2026-09-02", "RISK_OFF")])
        day = self.build()[1]
        self.assertEqual({o["eligibility"] for o in day["opportunities"]}, {"BLOCKED_BY_MARKET_STATE"})
        self.assertIn(("RULE.ENTRY.PAPER_BASELINE_B.V1", "BLOCKED_BY"),
                      [(r["rule_id"], r["role"]) for r in day["opportunities"][0]["rule_refs"]])

    def test_record_only_features_use_bars_up_to_session_only(self):
        paper_reference(self.root, "2026-09-03", "2026-09-03T22:00:00Z", [("US", "2026-09-03", "RISK_ON")])
        days = {d["as_of_date"]: d for d in self.build()}
        features = days["2026-09-02"]["opportunities"][0]["record_only_features"]
        self.assertEqual(features["status"], "OBSERVED")
        self.assertEqual(features["source"]["path"], "evidence/free_market_data/raw/2026-09-02/alpaca_iex_daily_bars.json.gz")
        self.assertEqual(features["session"], "2026-09-02")
        self.assertEqual(features["bars_used"], 29)
        self.assertIsNone(features["rise_since_signal_pct"])
        bars = OL.USBars(OL.load_config(self.root), self.root)
        later_capture_only = bars._compute(
            [b for b in bars._load(self.root / "evidence/free_market_data/raw/2026-09-03/alpaca_iex_daily_bars.json.gz")["XLK"]["bars"]
             if b["t"][:10] <= "2026-09-02"],
            "XLK", "2026-09-02", self.root / "evidence/free_market_data/raw/2026-09-03/alpaca_iex_daily_bars.json.gz",
        )
        for key in ("close", "ema20", "atr14", "breakout_20", "prior_session_change_pct"):
            self.assertEqual(later_capture_only[key], features[key])  # future bars never enter the computation

    def test_second_same_day_capture_does_not_rebind_an_earlier_session(self):
        """A replaced compatibility file must not silently re-pin a recorded observation.

        The 2026-09-18 capture day was captured twice (01:41Z and 23:32Z). The
        second run replaced evidence/free_market_data/raw/2026-09-18/... in
        place, so a builder that reads only that path re-pinned the already
        committed 2026-09-17 packet to the newer bytes and failed its own
        determinism gate. The replaced response stays addressable in the
        append-only content-addressed store, so the earlier session keeps the
        observation it was recorded with while the later one is still reached.
        """
        capture, early, late = "2026-09-03", "2026-09-02", "2026-09-03"
        history = sessions_until(3, 30)
        shutil.rmtree(self.root / "evidence/free_market_data/raw" / early)
        iex_bars(self.root, capture, {s: [d for d in history if d <= early] for s in US})
        compat = self.root / "evidence/free_market_data/raw" / capture / "alpaca_iex_daily_bars.json.gz"
        first = compat.read_bytes()

        before = OL.USBars(OL.load_config(self.root), self.root).features("XLK", early)
        self.assertEqual(before["status"], "OBSERVED")
        self.assertEqual(before["source"]["path"], f"evidence/free_market_data/raw/{capture}/alpaca_iex_daily_bars.json.gz")
        self.assertEqual(before["source"]["sha256"], RC.file_sha256(compat))

        # A second capture on the same UTC day replaces the compatibility file
        # with bars through the next session, preserves the replaced response
        # by content address, and records both observations as revisions.
        iex_bars(self.root, capture, {s: history for s in US})
        second = compat.read_bytes()
        self.assertNotEqual(first, second)
        for index, (observed, payload) in enumerate(((f"{capture}T01:41:42Z", first), (f"{capture}T23:32:57Z", second))):
            digest = hashlib.sha256(gzip.decompress(payload)).hexdigest()
            store = self.root / "evidence/free_market_data/raw/alpaca/daily_bars" / digest
            store.mkdir(parents=True, exist_ok=True)
            (store / "alpaca_iex_daily_bars.json.gz").write_bytes(payload)
            write_json(self.root / "evidence/free_market_data/derived" / capture / f"rev{index}" / "manifest.json", {
                "observed_at_utc": observed,
                "alpaca": {"daily_raw_evidence": {
                    "kind": "daily_bars",
                    "raw_path": f"evidence/free_market_data/raw/alpaca/daily_bars/{digest}/alpaca_iex_daily_bars.json.gz",
                }},
            })

        rebound = OL.USBars(OL.load_config(self.root), self.root)
        self.assertEqual(RC.render_json(rebound.features("XLK", early)), RC.render_json(before))
        # ...while the session only the later capture observed is still reached.
        self.assertEqual(rebound.features("XLK", late)["source"]["sha256"], RC.file_sha256(compat))

    def test_append_only_writer(self):
        paper_reference(self.root, "2026-09-03", "2026-09-03T22:00:00Z", [("US", "2026-09-03", "NEUTRAL")])
        days = self.build()
        OL.write_market_days("US", days, self.root)
        self.assertEqual(OL.verify_market_days("US", days, self.root), [])
        changed = copy.deepcopy(days)
        changed[0]["counts"]["opportunities"] = 99
        with self.assertRaisesRegex(OL.OpportunityLedgerError, "APPEND_ONLY_EVIDENCE_CONFLICT"):
            OL.write_market_days("US", changed, self.root)


class RetainedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days = {m: OL.build_market_days(m, ROOT, CONFIG, POLICY) for m in RC.MARKETS}

    def test_rebuild_is_deterministic_and_committed_days_match(self):
        for market in RC.MARKETS:
            again = OL.build_market_days(market, ROOT, CONFIG, POLICY)
            self.assertEqual([RC.render_json(d) for d in again], [RC.render_json(d) for d in self.days[market]])
            built = {d["as_of_date"]: d for d in self.days[market]}
            for path in sorted((ROOT / OL.EVIDENCE_RELATIVE_ROOT / market).glob("*/packet.json")):
                self.assertIn(path.parent.name, built)
                self.assertEqual(path.read_bytes(), RC.render_json(built[path.parent.name]), path)

    def test_only_strong_confirmed_or_held_entities_become_opportunities(self):
        for market in RC.MARKETS:
            confirmation = {p["as_of_date"]: p for p in RC.build_market(market, ROOT, POLICY)}
            for day in self.days[market]:
                strong = [(g["scope_id"], g["entity_id"]) for g in confirmation[day["as_of_date"]]["entry_gate_view"] if g["decision_eligible"]]
                self.assertEqual([(o["scope_id"], o["entity_id"]) for o in day["opportunities"]], strong)
                for row in day["opportunities"]:
                    self.assertIn(row["sector_state"], RC.STRONG_STATES)
                    expected = "BLOCKED_BY_MARKET_STATE" if day["market_state"]["new_buy_verdict"] == "DENY" else "ELIGIBLE_PENDING_T2"
                    self.assertEqual(row["eligibility"], expected)

    def test_retained_us_features_are_observed(self):
        day = next(d for d in self.days["US"] if d["as_of_date"] == "2026-09-11")
        self.assertEqual([o["entity_id"] for o in day["opportunities"]], ["XLE", "XLC", "XLK"])
        self.assertTrue(all(o["record_only_features"]["status"] == "OBSERVED" for o in day["opportunities"]))


class WorkflowTests(unittest.TestCase):
    def test_workflow_is_chained_without_cron_or_secrets(self):
        path = ROOT / ".github/workflows/rotation-confirmation.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        triggers = workflow.get("on", workflow.get(True))
        self.assertNotIn("schedule", triggers)
        self.assertEqual(sorted(triggers["workflow_run"]["workflows"]), sorted([
            "Atlas Free Market Data Evidence", "P1-CR-06 Crypto Breadth Daily Capture",
            "Korea Leadership Live Proof", "PAPER Market Risk Reference",
        ]))
        self.assertNotIn("secrets.", text)
        self.assertEqual(workflow["permissions"], {"contents": "write"})
        run = "\n".join(step.get("run", "") for step in workflow["jobs"]["build"]["steps"])
        self.assertIn("rotation/rotation_confirmation.py verify", run)
        self.assertIn("rotation/rotation_opportunity_ledger.py verify", run)

    def test_sha_pinned_source_workflows_are_not_modified_by_this_layer(self):
        for name in ("free-market-data.yml", "crypto-breadth-capture.yml", "korea-leadership-live-proof.yml"):
            self.assertNotIn("rotation_confirmation", (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
