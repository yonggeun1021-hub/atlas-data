"""P4-07 expected governance WAIT must not become a failed workflow run."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BRIDGE = _load("p407_wait_bridge", "microstructure/upbit_p3_p4_bridge.py")
RESOLVER = _load("p407_wait_resolver", ".github/scripts/resolve_upbit_p3_p4_lineage.py")
TELEMETRY = _load("p407_wait_telemetry", ".github/scripts/record_upbit_microstructure_run.py")
WORKFLOW = ROOT / ".github/workflows/upbit-microstructure-capture.yml"


class ExpectedWaitTests(unittest.TestCase):
    def _packet(self, root: Path) -> Path:
        path = root / "packet.json"
        path.write_text(
            json.dumps({"snapshot_date": "2026-09-01", "payload_sha256": "a" * 64}),
            encoding="utf-8",
        )
        return path

    def test_only_unratified_latest_record_becomes_wait(self):
        def expected_wait(*args, **kwargs):
            raise RESOLVER.BridgeError(RESOLVER.EXPECTED_WAIT_REASON)

        with tempfile.TemporaryDirectory() as tmp:
            result = RESOLVER.resolve_lineage(self._packet(Path(tmp)), consumer=expected_wait)
        self.assertEqual(result["ready"], "false")
        self.assertEqual(result["wait_reason"], "UNIVERSE_RATIFICATION_NOT_EFFECTIVE")
        self.assertEqual(result["packet_date"], "2026-09-01")

    def test_integrity_bridge_error_remains_hard_failure(self):
        def integrity_error(*args, **kwargs):
            raise RESOLVER.BridgeError("UNIVERSE_RECORD_EXACT_HASH_MISMATCH")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(RESOLVER.BridgeError, "EXACT_HASH_MISMATCH"):
                RESOLVER.resolve_lineage(self._packet(Path(tmp)), consumer=integrity_error)

    def test_wait_capture_is_normalized_as_waiting_telemetry(self):
        observation = TELEMETRY.capture_observation("success", "waiting_universe_ratification")
        self.assertEqual(observation["result"], "waiting")
        self.assertEqual(observation["reason"], "universe_ratification_not_effective")
        self.assertFalse(observation["raw_publication_eligible"])

    def test_workflow_skips_provider_capture_and_records_wait(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("steps.universe.outputs.ready", text)
        self.assertIn("waiting_universe_ratification", text)
        self.assertIn("ATLAS_UNIVERSE_WAIT_REASON", text)
        resolve_at = text.index("resolve_upbit_p3_p4_lineage.py")
        provider_at = text.index("python3 .github/scripts/upbit_microstructure_capture.py")
        self.assertLess(resolve_at, provider_at)


if __name__ == "__main__":
    unittest.main()
