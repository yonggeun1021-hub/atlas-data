#!/usr/bin/env python3
"""P0-06 external consumer and H-24 immutable binding regressions."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PUBLISHER = load(
    "p0_06_publisher_for_consumer_test",
    ROOT / ".github/scripts/publish_scheduled_briefing_authority.py",
)
CONSUMER = load(
    "p0_06_consumer",
    ROOT / ".github/scripts/consume_scheduled_briefing_authority.py",
)

DATE = "2026-08-26"
GENERATION = "6" * 64


def payload_sha256(value):
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ConsumerFixture:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        for relative in (
            "config/scheduled_briefing_retrieval_contract.json",
            "config/read_model_authority_contract.json",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT / relative).read_bytes())
        generation = {"generation_id": GENERATION, "generation_contract_version": 1}
        dump(self.root / "data/briefing/step0_status.json", {
            "schema_version": 2, "expected_kst_date": DATE, "generation": generation,
        })
        dump(self.root / "data/briefing_status.json", {
            "schema_version": 2, "expected_kst_date": DATE, "generation": generation,
        })
        dump(self.root / "data/briefing/krx/005930.json", {
            "source": {"collected_for_kst_date": DATE}, "generation": generation,
        })
        self._write_delivery()
        self.commit = self.commit_all("consumer-ready")
        self.envelope = PUBLISHER.build_envelope(
            self.root, self.commit, "morning", DATE
        )
        self.responses = {}
        self._install_envelope(1, self.envelope)
        self._install_commit_artifacts(self.envelope)

    def _write_delivery(self):
        base = self.root / f"evidence/daily_briefing/morning/{DATE}"
        index = base / "index.json"
        packet = base / "rev-001/packet.json"
        briefing = base / "rev-001/briefing.md"
        packet_value = {
            "schema_version": 1,
            "contract_version": "daily_orchestrator/3",
            "output_schema_version": "daily_briefing_packet/1",
            "slot": "morning",
            "decision_date": DATE,
            "generated_at": "2026-08-25T23:05:00Z",
            "capture_mode": "provider_free_aggregation_of_persisted_evidence_only",
            "component_status_counts": {
                "READY": 1, "PENDING": 0, "DATA_BLOCKED": 0,
                "POLICY_BLOCKED": 0, "DEGRADED": 0, "UNAVAILABLE": 0,
                "UNKNOWN": 0,
            },
            "components": [{
                "component_id": "TEST_COMPONENT", "status": "READY",
                "decision_eligible": False, "action_eligible": False,
                "order_eligible": False,
            }],
            "authority": {
                "aggregation_only": True,
                "component_build_authorized": True,
                "source_interpretation_authorized": False,
                "regime_score_authorized": False,
                "rotation_ranking_authorized": False,
                "discovery_promotion_authorized": False,
                "rule_pass_fail_authorized": False,
                "portfolio_sizing_authorized": False,
                "action_generation_authorized": False,
                "order_generation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
            "frozen_sources": {},
            "unresolved_boundaries": ["TEST_ONLY"],
        }
        packet_value["packet_sha256"] = payload_sha256(packet_value)
        dump(packet, packet_value)
        dump(index, {
            "schema_version": 1, "slot": "morning", "decision_date": DATE,
            "latest_revision": 1,
            "revisions": [{
                "revision": 1, "path": "rev-001",
                "packet_sha256": packet_value["packet_sha256"],
            }],
        })
        briefing.parent.mkdir(parents=True, exist_ok=True)
        briefing.write_text("# verified briefing\n", encoding="utf-8")
        rel = lambda path: path.relative_to(self.root).as_posix()
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        dump(self.root / "data/briefing/daily_briefing_sources.json", {
            "schema_version": "daily_briefing_delivery/1",
            "slot": "morning", "decision_date": DATE, "revision": 1,
            "index_path": rel(index), "index_sha256": sha(index),
            "packet_path": rel(packet), "packet_file_sha256": sha(packet),
            "packet_sha256": packet_value["packet_sha256"],
            "briefing_path": rel(briefing), "briefing_sha256": sha(briefing),
            "delivery_scope": [
                "INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW",
                "SHADOW_ENTRY_REVIEW",
            ],
            "authority": {
                "stage": False, "buy": False, "action": False,
                "order": False, "production": False, "trading": False,
            },
        })

    def commit_all(self, message):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", message], check=True)
        return subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

    @staticmethod
    def clean_url(url):
        parsed = urlsplit(url)
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))

    def _install_envelope(self, revision, envelope):
        url = envelope["bootstrap_url"]
        self.responses[url] = (200, (json.dumps(envelope, sort_keys=True) + "\n").encode())

    def _install_commit_artifacts(self, envelope):
        for record in envelope["required_artifacts"] + envelope["delivery_artifacts"]:
            raw = subprocess.check_output([
                "git", "-C", str(self.root), "show",
                f"{envelope['source_commit']}:{record['path']}",
            ])
            self.responses[record["immutable_url"]] = (200, raw)
        compact_url = envelope["compact_immutable_url_templates"]["krx"].format(symbol="005930")
        raw = subprocess.check_output([
            "git", "-C", str(self.root), "show",
            f"{envelope['source_commit']}:data/briefing/krx/005930.json",
        ])
        self.responses[compact_url] = (200, raw)

    def get(self, url):
        clean = self.clean_url(url)
        return self.responses.get(clean, (404, b""))

    def close(self):
        self.temp.cleanup()


class ScheduledBriefingAuthorityConsumerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ConsumerFixture()
        self.contract = CONSUMER._load_contract(self.fixture.root / PUBLISHER.CONTRACT_PATH)
    def tearDown(self):
        self.fixture.close()

    def consume(self):
        return CONSUMER.consume(
            DATE, "morning", {"krx": ["005930"]}, contract=self.contract,
            get=self.fixture.get, nonce_factory=lambda: "unique",
        )

    def test_latest_bootstrap_fetches_exact_commit_read_model_and_h24(self):
        raw, envelope = self.consume()
        self.assertEqual(envelope["source_commit"], self.fixture.commit)
        self.assertIn("data/briefing/daily_briefing_sources.json", raw)
        self.assertIn("evidence/daily_briefing/morning/2026-08-26/rev-001/briefing.md", raw)
        self.assertIn("data/briefing/krx/005930.json", raw)
        self.assertFalse(any(
            value for key, value in envelope["authority"].items()
            if key != "retrieval_pointer_only"
        ))

    def test_consumer_validation_does_not_rebuild_against_newer_local_state(self):
        with mock.patch.object(
            CONSUMER, "_validate_pinned_delivery_packet",
            wraps=CONSUMER._validate_pinned_delivery_packet,
        ) as validator:
            self.consume()
        self.assertEqual(validator.call_count, 1)

    def test_resigned_packet_authority_escalation_is_rejected(self):
        packet = json.loads((
            self.fixture.root
            / f"evidence/daily_briefing/morning/{DATE}/rev-001/packet.json"
        ).read_text())
        packet["authority"]["trading_authorized"] = True
        packet["packet_sha256"] = payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "DELIVERY_PACKET_AUTHORITY_INVALID"
        ):
            CONSUMER._validate_pinned_delivery_packet(packet, DATE, "morning")

    def test_resigned_component_eligibility_escalation_is_rejected(self):
        packet = json.loads((
            self.fixture.root
            / f"evidence/daily_briefing/morning/{DATE}/rev-001/packet.json"
        ).read_text())
        packet["components"][0]["action_eligible"] = True
        packet["packet_sha256"] = payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            CONSUMER.ScheduledConsumerError, "COMPONENT_AUTHORITY_INVALID"
        ):
            CONSUMER._validate_pinned_delivery_packet(packet, DATE, "morning")

    def test_first_revision_missing_is_fail_closed(self):
        self.fixture.responses.pop(self.fixture.envelope["bootstrap_url"])
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "RETRIEVAL_AUTHORITY_UNAVAILABLE"):
            self.consume()

    def test_non_404_after_valid_revision_does_not_use_older_revision(self):
        rev2 = self.fixture.envelope["bootstrap_url"].replace("rev-001", "rev-002")
        self.fixture.responses[rev2] = (503, b"")
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "HTTP_503"):
            self.consume()

    def test_floating_artifact_url_is_rejected_before_fetch(self):
        envelope = copy.deepcopy(self.fixture.envelope)
        envelope["required_artifacts"][0]["immutable_url"] = envelope["required_artifacts"][0]["immutable_url"].replace(
            f"/{self.fixture.commit}/", "/main/"
        )
        self.fixture._install_envelope(1, envelope)
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "FLOATING_OR_WRONG_COMMIT"):
            self.consume()

    def test_wrong_date_envelope_is_rejected(self):
        envelope = copy.deepcopy(self.fixture.envelope)
        envelope["expected_kst_date"] = "2026-08-25"
        self.fixture._install_envelope(1, envelope)
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "EXPECTED_IDENTITY_MISMATCH"):
            self.consume()

    def test_h24_briefing_byte_tamper_is_rejected(self):
        record = next(row for row in self.fixture.envelope["delivery_artifacts"] if row["path"].endswith("briefing.md"))
        self.fixture.responses[record["immutable_url"]] = (200, b"# tampered\n")
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "CONTENT_HASH_MISMATCH"):
            self.consume()

    def test_embedded_locator_and_fetched_locator_must_match(self):
        locator_record = self.fixture.envelope["delivery_artifacts"][0]
        locator = json.loads(self.fixture.responses[locator_record["immutable_url"]][1])
        locator["revision"] = 2
        raw = (json.dumps(locator, sort_keys=True) + "\n").encode()
        locator_record["content_sha256"] = hashlib.sha256(raw).hexdigest()
        locator_record["git_blob_sha1"] = CONSUMER._git_blob_sha1(raw)
        self.fixture.responses[locator_record["immutable_url"]] = (200, raw)
        self.fixture._install_envelope(1, self.fixture.envelope)
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "LOCATOR_ENVELOPE_MISMATCH"):
            self.consume()

    def test_resigned_index_cannot_redirect_latest_revision(self):
        envelope = self.fixture.envelope
        locator = envelope["delivery_locator"]
        index_record = next(
            row for row in envelope["delivery_artifacts"]
            if row["path"] == locator["index_path"]
        )
        index = json.loads(self.fixture.responses[index_record["immutable_url"]][1])
        index["revisions"][-1]["path"] = "rev-999"
        raw = (json.dumps(index, sort_keys=True) + "\n").encode()
        digest = hashlib.sha256(raw).hexdigest()
        locator["index_sha256"] = digest
        index_record["content_sha256"] = digest
        index_record["git_blob_sha1"] = CONSUMER._git_blob_sha1(raw)
        self.fixture.responses[index_record["immutable_url"]] = (200, raw)
        locator_record = envelope["delivery_artifacts"][0]
        locator_raw = (json.dumps(locator, sort_keys=True) + "\n").encode()
        locator_record["content_sha256"] = hashlib.sha256(locator_raw).hexdigest()
        locator_record["git_blob_sha1"] = CONSUMER._git_blob_sha1(locator_raw)
        self.fixture.responses[locator_record["immutable_url"]] = (200, locator_raw)
        self.fixture._install_envelope(1, envelope)
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "INDEX_OR_REVISION"):
            self.consume()

    def test_compact_from_wrong_generation_is_rejected(self):
        url = self.fixture.envelope["compact_immutable_url_templates"]["krx"].format(symbol="005930")
        value = json.loads(self.fixture.responses[url][1])
        value["generation"]["generation_id"] = "9" * 64
        self.fixture.responses[url] = (200, (json.dumps(value) + "\n").encode())
        with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "GENERATION_MISMATCH"):
            self.consume()

    def test_revision_two_is_selected_only_after_valid_revision_one(self):
        second = copy.deepcopy(self.fixture.envelope)
        second["revision"] = 2
        second["bootstrap_path"] = second["bootstrap_path"].replace("rev-001", "rev-002")
        second["bootstrap_url"] = second["bootstrap_url"].replace("rev-001", "rev-002")
        self.fixture._install_envelope(2, second)
        _, envelope = self.consume()
        self.assertEqual(envelope["revision"], 2)

    def test_output_persistence_is_atomic_and_refuses_overwrite(self):
        raw, envelope = self.consume()
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / "verified"
            CONSUMER.persist(target, raw, envelope)
            self.assertTrue((target / "scheduled_retrieval_authority.json").is_file())
            with self.assertRaisesRegex(CONSUMER.ScheduledConsumerError, "OUTPUT_ALREADY_EXISTS"):
                CONSUMER.persist(target, raw, envelope)


if __name__ == "__main__":
    unittest.main()
