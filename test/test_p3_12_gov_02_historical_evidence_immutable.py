#!/usr/bin/env python3
"""P3-12-GOV-02/GOV-02B: historical Upbit realtime capture evidence
(run_006/007/008, both 2026-08-29 and 2026-08-30, and their
realtime_validation counterparts) must stay byte-for-byte untouched by the
revert-and-quarantine work in this PR. These files predate, and are
unrelated to, the invalid P3-12 ratification (PR #465) and its revert --
this test locks in their exact bytes so neither this PR nor a future one
accidentally rewrites, deletes, or "cleans up" real historical evidence
while doing unrelated governance work.
"""
import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_SHA256 = {
    "evidence/crypto/upbit/realtime/2026-08-29/run_006.json": "6fd4072b6b259402af2a87c213c7407842dbb13ec9f415f81cee2fd1070f76e4",
    "evidence/crypto/upbit/realtime/2026-08-29/run_007.json": "7e1515db1afb47b71d5812ff2cd1acd97be354d28606472fdf7cf76cbc1234af",
    "evidence/crypto/upbit/realtime/2026-08-29/run_008.json": "c99f18460436a377cca0f72ed2e7e1d9ee6253010eaf2c330475b4195762828e",
    "evidence/crypto/upbit/realtime/2026-08-30/run_006.json": "82fc8e0a82baa5dd004072e798432dc29253562b510ffb8f8a891ecf74e9dbf6",
    "evidence/crypto/upbit/realtime/2026-08-30/run_007.json": "b891df4ff097edaadafe53e88cd432973f5828fee0b6fc28b050d0dd9c75c640",
    "evidence/crypto/upbit/realtime/2026-08-30/run_008.json": "9afc7b85ec2766bf3914ebcc5100445d9d5a9f407e542af76a581e5d471a3b8b",
    "evidence/crypto/upbit/realtime_validation/2026-08-29/run_006.json": "f10b900f4bf356001ec8454c6674f3b04d052bc207d94e8f0b77fcae926fe5d2",
    "evidence/crypto/upbit/realtime_validation/2026-08-29/run_007.json": "ab5feb04dc14d48303c019cc283aaa82f73cbc1535a49dc916ea4f2e401d3c42",
    "evidence/crypto/upbit/realtime_validation/2026-08-29/run_008.json": "17a26ebd6f4b17b45cee7d71d94fa1f59fd3eec869d839d7bdd22edca364fa65",
    "evidence/crypto/upbit/realtime_validation/2026-08-30/run_006.json": "59a482e160295626eec724abf52042c0b0637005408f1febd27ef87a9a72f6de",
    "evidence/crypto/upbit/realtime_validation/2026-08-30/run_007.json": "59e5996588ab98bbb4657259b9b4d0866a8a765e5af33e6049bd5eea81798975",
    "evidence/crypto/upbit/realtime_validation/2026-08-30/run_008.json": "3c940c17253ab5c9e78b159bea1992a51af3d213c55b127f3a88923f4f2f743d",
}


class HistoricalRealtimeEvidenceImmutableTests(unittest.TestCase):
    def test_run_006_007_008_files_all_present(self):
        for relative in EXPECTED_SHA256:
            self.assertTrue((ROOT / relative).is_file(), relative)

    def test_run_006_007_008_bytes_unchanged(self):
        for relative, expected in EXPECTED_SHA256.items():
            actual = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, f"{relative} bytes changed -- historical evidence must never be rewritten")


if __name__ == "__main__":
    unittest.main()
