#!/usr/bin/env python3
"""Official GitHub action runtime and immutable pin regression."""

import json
from pathlib import Path
import re
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
CONTRACT_PATH = ROOT / "config" / "github_actions_runtime_contract.json"
SHA = re.compile(r"^[0-9a-f]{40}$")


def strings_for_key(value, target):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == target and isinstance(item, str):
                yield item
            yield from strings_for_key(item, target)
    elif isinstance(value, list):
        for item in value:
            yield from strings_for_key(item, target)


def mappings_for_key(value, target):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == target and isinstance(item, dict):
                yield item
            yield from mappings_for_key(item, target)
    elif isinstance(value, list):
        for item in value:
            yield from mappings_for_key(item, target)


class GitHubActionsRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
        cls.documents = {}
        for path in sorted(WORKFLOWS.glob("*.yml")):
            cls.documents[path] = yaml.safe_load(path.read_text(encoding="utf-8"))

    def test_contract_pins_verified_official_node24_releases(self):
        self.assertEqual(self.contract["schema_version"], 1)
        self.assertEqual(
            self.contract["contract_version"], "github_actions_runtime/v1"
        )
        self.assertEqual(self.contract["pin_policy"], "FULL_COMMIT_SHA")
        self.assertFalse(self.contract["mutable_tag_authorized"])
        self.assertEqual(
            set(self.contract["actions"]),
            {
                "actions/checkout",
                "actions/setup-python",
                "actions/upload-artifact",
                "actions/download-artifact",
            },
        )
        for action, item in self.contract["actions"].items():
            self.assertRegex(item["commit_sha"], SHA)
            self.assertEqual(item["runtime"], "node24")
            self.assertEqual(
                item["release_url"],
                f"https://github.com/{action}/releases/tag/{item['version']}",
            )

    def test_every_checkout_and_setup_python_use_exact_immutable_pin(self):
        observed = {action: [] for action in self.contract["actions"]}
        for path, document in self.documents.items():
            for use in strings_for_key(document, "uses"):
                for action, item in self.contract["actions"].items():
                    if use.startswith(f"{action}@"):
                        observed[action].append((path.name, use))
                        self.assertEqual(use, f"{action}@{item['commit_sha']}")

        self.assertEqual(len(observed["actions/checkout"]), 27)
        self.assertEqual(len(observed["actions/setup-python"]), 25)
        self.assertEqual(len(observed["actions/upload-artifact"]), 13)
        self.assertEqual(len(observed["actions/download-artifact"]), 2)

    def test_no_mutable_or_retired_refs_remain_in_workflows(self):
        raw = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        for retired in (
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "actions/upload-artifact@v4",
            "93cb6efe18208431cddfb8368fd83d5badbf9bfd",
            "a26af69be951a213d495a4c3e4e4022e16d87065",
            "ea165f8d65b6e75b540449e92b4886f43607fa02",
        ):
            self.assertNotIn(retired, raw)

    def test_removed_setup_python_input_is_not_used(self):
        for path, document in self.documents.items():
            for step in mappings_for_key(document, "with"):
                self.assertNotIn(
                    "pip-install",
                    step,
                    f"removed v7 input in {path.name}",
                )


if __name__ == "__main__":
    unittest.main()
