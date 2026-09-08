"""Explicit-file CLI integration against the real validators and guarded apply.

Every scenario invokes the installed CLI as a subprocess with externally hashed
temporary files.  The production preview builder, preview validator and guarded
application are used unchanged and are never mocked into success.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CLI_PATH = ROOT / "universe" / "global_asset_master_theme_application_cli.py"
SPEC = importlib.util.spec_from_file_location(
    "gam_application_cli_fixture", ROOT / "test" / "test_global_asset_master.py"
)
F = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(F)

from universe import global_asset_master_theme_application_cli as CLI
from universe import global_asset_master_theme_ingestion as I

TIMEOUT = 600


class ThemeApplicationCliTests(unittest.TestCase):
    def setUp(self):
        self.master = F.binding_master_input()
        self.expected_row = copy.deepcopy(F.bound_membership(self.master))
        record = next(r for r in self.master["records"] if r["asset_id"] == "US:XNAS:TEST")
        record["memberships"].remove(F.bound_membership(self.master))
        self.graph = F.taxonomy_fixture()
        self.requests = [
            {**F.binding_reference(), "gam_source_identity": F.bound_theme_source()}
        ]
        self.workdir = self.tempdir()
        self.use_repo(self.authority_repo())

    # ---- fixtures -------------------------------------------------------

    def tempdir(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def authority_repo(self, **kwargs):
        return F.AuthorityRepo(self.tempdir(), self.graph, **kwargs)

    def use_repo(self, repo):
        self.repo = repo
        self.commit = repo.head()
        self.preview = self.build()
        self.write_inputs()

    def build(self, **overrides):
        args = dict(
            master_source=self.master, taxonomy_source=self.graph,
            requests=self.requests, trusted_commit=self.repo.head(),
            authority_registry_path=self.repo.registry_path,
        )
        args.update(overrides)
        return I.build_theme_ingestion_preview(**args)

    @staticmethod
    def external_sha256(path) -> str:
        """Hash the exact file bytes independently of the CLI under test."""
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    def publish_input(self, name, value):
        path = self.workdir / f"{name}.json"
        path.write_text(
            json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return path, self.external_sha256(path)

    def publish_raw(self, name, text):
        path = self.workdir / f"{name}.json"
        path.write_text(text, encoding="utf-8")
        return path, self.external_sha256(path)

    def write_inputs(self, **overrides):
        values = dict(
            preview=self.preview, master_source=self.master,
            taxonomy_source=self.graph, requests=self.requests,
        )
        values.update(overrides)
        self.files = {name: self.publish_input(name, value) for name, value in values.items()}
        self.files["authority_registry"] = (
            self.repo.registry_path, self.external_sha256(self.repo.registry_path)
        )

    def destination(self, packet=None):
        packet = F.GAM.build_master(self.master) if packet is None else packet
        path = self.tempdir() / "master.json"
        F.GAM.write_json_atomic(path, packet)
        return path, packet

    # ---- invocation -----------------------------------------------------

    def argv(self, **overrides):
        files = dict(self.files)
        files.update(overrides)
        argv = []
        for name, (path, digest) in sorted(files.items()):
            flag = "--" + name.replace("_", "-")
            argv += [flag, str(path), f"{flag}-sha256", digest]
        return argv + ["--trusted-commit", self.commit]

    def apply_argv(self, destination, expected, **overrides):
        return self.argv(**overrides) + [
            "--apply", "--destination", str(destination),
            "--expected-previous-master-sha256", expected,
        ]

    def invoke(self, argv):
        proc = subprocess.run(
            [sys.executable, str(CLI_PATH), *argv], capture_output=True, text=True,
            cwd=str(self.workdir), timeout=TIMEOUT,
        )
        # One compact line, so a caller parses exactly one outcome object.
        self.assertEqual(proc.stdout.count("\n"), 1, proc.stderr or proc.stdout)
        return proc.returncode, json.loads(proc.stdout), proc

    @staticmethod
    def tree(root) -> dict:
        return {
            str(path.relative_to(root)): path.read_bytes()
            for path in sorted(Path(root).rglob("*")) if path.is_file()
        }

    def assert_destination_preserved(self, path, before, *, lock_allowed):
        self.assertEqual(path.read_bytes(), before)
        extra = {name for name in self.tree(path.parent) if name != path.name}
        self.assertTrue(extra <= ({I._lock_path(path).name} if lock_allowed else set()), extra)

    def assert_no_raw_master_on_stdout(self, proc):
        for leaked in ("candidate_master", "records", "source_identity", "display_name",
                       "aliases", "memberships"):
            self.assertNotIn(leaked, proc.stdout)

    # ---- validation-only ------------------------------------------------

    def test_validation_only_reports_identity_and_touches_nothing(self):
        destination, packet = self.destination()
        before, workdir_before = destination.read_bytes(), self.tree(self.workdir)
        code, result, proc = self.invoke(self.argv())
        self.assertEqual(code, 0, proc.stderr)
        self.assertEqual(result["mode"], "VALIDATE_ONLY")
        self.assertEqual(result["status"], "PREVIEW_REVALIDATED")
        self.assertFalse(result["applied"])
        self.assertFalse(result["destination_checked"])
        self.assertIn("DESTINATION_APPLICABILITY_NOT_CHECKED", result["notes"])
        self.assertIn("NOT_AN_OPERATIONAL_ADMISSION", result["notes"])
        self.assertEqual(result["preview"]["payload_sha256"], self.preview["payload_sha256"])
        self.assertEqual(result["preview"]["status"], "STRUCTURAL_PREVIEW")
        self.assertEqual(result["preview"]["change"], "APPEND")
        self.assertEqual(result["preview"]["addition_count"], 1)
        self.assertEqual(result["preview"]["binding_status"], "THEME_SOURCE_BINDING_VERIFIED")
        self.assertEqual(result["preview"]["failure_reasons"], [])
        self.assertEqual(result["preview_input_digests"], self.preview["input_digests"])
        self.assertEqual(result["trusted_commit"], self.commit)
        self.assertEqual(
            result["input_file_sha256"],
            {name: digest for name, (_path, digest) in self.files.items()},
        )
        self.assert_no_raw_master_on_stdout(proc)
        self.assertEqual(proc.stderr, "")
        # No destination, parent, lock or output file exists in this mode.
        self.assertEqual(self.tree(self.workdir), workdir_before)
        self.assert_destination_preserved(destination, before, lock_allowed=False)
        self.assertEqual(json.loads(before), packet)

    def test_run_is_callable_in_process_and_matches_the_subprocess(self):
        stream, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(errors):
            code = CLI.run(self.argv())
        _, _, proc = self.invoke(self.argv())
        self.assertEqual(code, 0)
        self.assertEqual(stream.getvalue(), proc.stdout)
        self.assertEqual(errors.getvalue(), "")

    # ---- application ----------------------------------------------------

    def test_apply_appends_through_the_unchanged_guarded_application(self):
        destination, packet = self.destination()
        code, result, proc = self.invoke(
            self.apply_argv(destination, packet["payload_sha256"])
        )
        self.assertEqual(code, 0, proc.stderr)
        self.assertEqual(result["mode"], "APPLY")
        self.assertEqual(result["outcome"], "APPLIED_APPEND")
        self.assertTrue(result["applied"])
        self.assertTrue(result["published"])
        self.assertEqual(result["change"], "APPEND")
        self.assertEqual(result["addition_count"], 1)
        self.assertEqual(result["unchanged_count"], 0)
        self.assertEqual(result["destination_path"], str(destination.resolve(strict=True)))
        self.assertEqual(result["preview_payload_sha256"], self.preview["payload_sha256"])
        self.assertIn("APPROVAL_FLAG_IS_THIS_CALLER_ACTION_ONLY", result["notes"])
        self.assertIn("NO_AUTHORITY_OUTPUT_CHANGED", result["notes"])
        published = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual(published, self.preview["candidate_master"])
        self.assertEqual(F.bound_membership(published), self.expected_row)
        self.assertEqual(
            result["previous_master"], {k: packet[k] for k in I.MASTER_IDENTITY_FIELDS}
        )
        self.assertEqual(
            result["master"], {k: published[k] for k in I.MASTER_IDENTITY_FIELDS}
        )
        self.assertFalse(published["authority"]["production_authorized"])
        self.assert_no_raw_master_on_stdout(proc)

    def test_explicitly_rebuilt_preview_is_no_change_and_publishes_nothing(self):
        destination, packet = self.destination()
        code, _, proc = self.invoke(self.apply_argv(destination, packet["payload_sha256"]))
        self.assertEqual(code, 0, proc.stderr)
        published = json.loads(destination.read_text(encoding="utf-8"))
        before = destination.read_bytes()

        self.preview = self.build(master_source=published)
        self.assertEqual(self.preview["change"], "NO_CHANGE")
        self.write_inputs(master_source=published)
        code, result, proc = self.invoke(
            self.apply_argv(destination, published["payload_sha256"])
        )
        self.assertEqual(code, 0, proc.stderr)
        self.assertEqual(result["outcome"], "APPLIED_NO_CHANGE")
        self.assertEqual(result["change"], "NO_CHANGE")
        self.assertFalse(result["published"])
        self.assertEqual(result["unchanged_count"], 1)
        self.assertEqual(result["previous_master"], result["master"])
        self.assertEqual(destination.read_bytes(), before)

    def test_stale_preview_after_an_append_conflicts_without_mutation(self):
        destination, packet = self.destination()
        argv = self.apply_argv(destination, packet["payload_sha256"])
        code, _, proc = self.invoke(argv)
        self.assertEqual(code, 0, proc.stderr)
        before = destination.read_bytes()

        code, result, proc = self.invoke(argv)
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "FAILED")
        self.assertFalse(result["applied"])
        self.assertIn("APPLICATION_EXPECTED_PREVIOUS_MASTER_MISMATCH", result["error"])
        self.assertEqual(result["destination_state"], "REQUIRES_INSPECTION")
        self.assertIn("APPLICATION_EXCEPTION_IS_NOT_PROOF_OF_ROLLBACK", result["notes"])
        self.assertIn("NO_AUTOMATIC_RETRY_PERFORMED", result["notes"])
        self.assertIn("gam theme application failed", proc.stderr)
        self.assert_destination_preserved(destination, before, lock_allowed=True)

    def test_wrong_external_hash_is_rejected_before_the_destination_is_opened(self):
        destination, packet = self.destination()
        before, digest = destination.read_bytes(), packet["payload_sha256"]
        cases = [
            ("preview", "a" * 64, "INPUT_FILE_SHA256_MISMATCH:preview"),
            ("master_source", "b" * 64, "INPUT_FILE_SHA256_MISMATCH:master_source"),
            ("taxonomy_source", "c" * 64, "INPUT_FILE_SHA256_MISMATCH:taxonomy_source"),
            ("requests", "d" * 64, "INPUT_FILE_SHA256_MISMATCH:requests"),
            ("authority_registry", "e" * 64, "INPUT_FILE_SHA256_MISMATCH:authority_registry"),
            ("preview", "not-a-sha256", "INPUT_EXPECTED_SHA256_INVALID:preview"),
        ]
        for name, supplied, expected in cases:
            with self.subTest(name=name, supplied=supplied):
                path, _real = self.files[name]
                code, result, _proc = self.invoke(
                    self.apply_argv(destination, digest, **{name: (path, supplied)})
                )
                self.assertEqual(code, 1)
                self.assertTrue(result["error"].startswith(expected), result["error"])
                self.assertEqual(
                    result["destination_state"], "NOT_REACHED_NO_APPLICATION_ATTEMPTED"
                )
                # Not even the cooperative lock sidecar was created.
                self.assert_destination_preserved(destination, before, lock_allowed=False)

    def test_self_rehashed_preview_is_rejected_before_destination_mutation(self):
        destination, packet = self.destination()
        before = destination.read_bytes()
        reviewed = copy.deepcopy(self.preview)
        forgeries = {
            "authority": lambda p: p["authority"].update(master_population_authorized=True),
            "addition_count": lambda p: p.update(addition_count=99),
            "binding": lambda p: p["binding_report"]["bindings"][0].update(verified=False),
            "digests": lambda p: p["input_digests"].update(trusted_commit="a" * 40),
        }
        for name, mutate in forgeries.items():
            with self.subTest(forgery=name):
                forged = copy.deepcopy(reviewed)
                mutate(forged)
                # Rehashed so the document attests to itself and its external
                # digest is genuinely correct for the bytes on disk.
                self.preview = F.rehash(forged)
                self.write_inputs()
                self.assertEqual(
                    self.files["preview"][1], self.external_sha256(self.files["preview"][0])
                )
                code, result, _proc = self.invoke(
                    self.apply_argv(destination, packet["payload_sha256"])
                )
                self.assertEqual(code, 1)
                self.assertIn("INGESTION_PREVIEW_DERIVATION_MISMATCH", result["error"])
                self.assertFalse(result["applied"])
                self.assert_destination_preserved(destination, before, lock_allowed=True)

    def test_unauthorized_registry_blocks_validation_and_application(self):
        self.use_repo(self.authority_repo(status="PROPOSED"))
        self.assertEqual(self.preview["status"], "BLOCKED")
        destination, packet = self.destination()
        before = destination.read_bytes()

        code, result, _proc = self.invoke(self.argv())
        self.assertEqual(code, 1)
        self.assertEqual(result["status"], "PREVIEW_NOT_APPLICABLE")
        self.assertEqual(result["preview"]["status"], "BLOCKED")
        self.assertIsNone(result["preview"]["change"])
        self.assertTrue(
            any("TAXONOMY_SOURCE_NOT_AUTHORIZED" in reason
                for reason in result["preview"]["failure_reasons"]),
            result["preview"]["failure_reasons"],
        )

        code, result, _proc = self.invoke(
            self.apply_argv(destination, packet["payload_sha256"])
        )
        self.assertEqual(code, 1)
        self.assertIn("APPLICATION_PREVIEW_NOT_APPLICABLE", result["error"])
        self.assert_destination_preserved(destination, before, lock_allowed=True)

    # ---- input and argument shape ---------------------------------------

    def test_malformed_duplicate_key_and_nonfinite_input_fail_closed(self):
        destination, packet = self.destination()
        before = destination.read_bytes()
        cases = [
            ("{", "INPUT_JSON_MALFORMED:requests"),
            ('{"a": 1, "a": 2}', "INPUT_JSON_DUPLICATE_KEY:requests:a"),
            ('{"a": NaN}', "INPUT_JSON_NONFINITE_NUMBER:requests:NaN"),
            ('{"a": Infinity}', "INPUT_JSON_NONFINITE_NUMBER:requests:Infinity"),
            ('{"a": 1e400}', "INPUT_JSON_NONFINITE_NUMBER:requests:1e400"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                override = {"requests": self.publish_raw("requests-raw", text)}
                code, result, _proc = self.invoke(self.argv(**override))
                self.assertEqual(code, 1)
                self.assertTrue(result["error"].startswith(expected), result["error"])
                self.assertEqual(
                    result["destination_state"], "NOT_USED_IN_VALIDATION_ONLY_MODE"
                )
                code, result, _proc = self.invoke(
                    self.apply_argv(destination, packet["payload_sha256"], **override)
                )
                self.assertEqual(code, 1)
                self.assertEqual(
                    result["destination_state"], "NOT_REACHED_NO_APPLICATION_ATTEMPTED"
                )
                self.assert_destination_preserved(destination, before, lock_allowed=False)

    def test_argument_shape_and_destination_arguments_fail_closed(self):
        destination, packet = self.destination()
        before, digest = destination.read_bytes(), packet["payload_sha256"]
        cases = [
            (self.argv() + ["--destination", str(destination)],
             "DESTINATION_ARGUMENTS_REQUIRE_APPLY"),
            (self.argv() + ["--expected-previous-master-sha256", digest],
             "DESTINATION_ARGUMENTS_REQUIRE_APPLY"),
            (self.argv() + ["--apply"], "APPLY_DESTINATION_REQUIRED"),
            (self.argv() + ["--apply", "--destination", str(destination)],
             "APPLY_EXPECTED_PREVIOUS_MASTER_SHA256_REQUIRED"),
            (self.apply_argv(destination, digest)[:-1] + ["ABSENT"],
             "APPLY_EXPECTED_PREVIOUS_MASTER_SHA256_INVALID"),
            (self.argv()[:-1] + ["HEAD"], "TRUSTED_COMMIT_INVALID"),
            (self.argv()[:-1] + ["a" * 7], "TRUSTED_COMMIT_INVALID"),
            (self.argv()[:-2], "ARGUMENTS_INVALID"),
            (self.argv() + ["--operational-application-approved"], "ARGUMENTS_INVALID"),
        ]
        for argv, expected in cases:
            with self.subTest(expected=expected, argv=argv[-2:]):
                code, result, _proc = self.invoke(argv)
                self.assertEqual(code, 1)
                self.assertTrue(result["error"].startswith(expected), result["error"])
                self.assertFalse(result["applied"])
        # A missing destination is never created, defaulted or initialized.
        absent = destination.parent / "absent.json"
        code, result, _proc = self.invoke(self.apply_argv(absent, digest))
        self.assertEqual(code, 1)
        self.assertIn("APPLICATION_DESTINATION_NOT_AN_EXISTING_FILE", result["error"])
        self.assertFalse(absent.exists())
        self.assert_destination_preserved(destination, before, lock_allowed=False)

    def test_registry_external_digest_must_bind_the_committed_bytes_consumed(self):
        destination, packet = self.destination()
        before = destination.read_bytes()
        committed = self.repo.registry_path.read_bytes()
        # Valid JSON with different exact bytes: the external digest names A,
        # but the unchanged authority validator can accept only committed B.
        observed = committed + b" \n"
        self.repo.registry_path.write_bytes(observed)
        self.files["authority_registry"] = (
            self.repo.registry_path, hashlib.sha256(observed).hexdigest(),
        )
        actual_apply = I.apply_theme_ingestion_preview

        def replace_registry_then_apply(**kwargs):
            self.repo.registry_path.write_bytes(committed)
            return actual_apply(**kwargs)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(
            I, "apply_theme_ingestion_preview", side_effect=replace_registry_then_apply,
        ) as application:
            code = CLI.run(
                self.apply_argv(destination, packet["payload_sha256"]),
                stdout=out, stderr=err,
            )
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "AUTHORITY_REGISTRY_FILE_COMMIT_DIGEST_MISMATCH")
        self.assertIs(result["applied"], False)
        self.assertEqual(application.call_count, 0)
        self.assert_destination_preserved(destination, before, lock_allowed=False)

    def test_failure_after_real_publication_reports_unknown_and_never_retries(self):
        destination, packet = self.destination()
        actual_write = I.GAM.write_json_atomic

        def publish_then_raise(path, value):
            actual_write(path, value)
            raise OSError("synthetic failure after actual replacement")

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(I.GAM, "write_json_atomic", side_effect=publish_then_raise) as writer:
            code = CLI.run(
                self.apply_argv(destination, packet["payload_sha256"]),
                stdout=out, stderr=err,
            )
        result = json.loads(out.getvalue())
        self.assertEqual(code, 1)
        self.assertIsNone(result["applied"])
        self.assertEqual(result["destination_state"], "REQUIRES_INSPECTION")
        self.assertEqual(writer.call_count, 1)
        self.assertEqual(json.loads(destination.read_text()), self.preview["candidate_master"])
        self.assertIn("NO_AUTOMATIC_RETRY_PERFORMED", result["notes"])


if __name__ == "__main__":
    unittest.main()
