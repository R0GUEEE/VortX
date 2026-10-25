"""UNRUN focused pure fixtures for the exact Beta 7 operational verifier.

Root may execute in the one allocated low-priority lane; no production API,
build, app launch, signing, account, or package-tool invocation is performed.
"""
import base64
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

SPEC = importlib.util.spec_from_file_location("beta7_startup_recovery", Path(__file__).resolve().parents[1] / "verify-ci-beta7-startup-recovery.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)


class ExactStartupRecoveryContract(unittest.TestCase):
    W = "b" * 40  # Disposable workflow identity, never a production admission.
    P = {"runId": 55, "attempt": 1, "artifactId": 88}

    def setUp(self):
        self.pins = mock.patch.multiple(V, OP_WORKFLOW_COMMIT=self.W, OP_WORKFLOW_SHA256="c" * 64)
        self.pins.start()
        self.addCleanup(self.pins.stop)

    def dispatch(self):
        return {"schemaVersion": 1, "event": "workflow_dispatch", "ref": "refs/heads/" + V.RECOVERY_BRANCH,
                "sourceCommit": V.RECOVERY_SOURCE, "workflowCommit": self.W, "runId": 55, "attempt": 1,
                "inputs": {**V.APPLE_INPUTS, "recovery_source_commit": V.RECOVERY_SOURCE}}

    def repair(self, created=True):
        before = {"state": "absent", "bytes": None, "sha256": None, "mode": None} if created else {
            "state": "existing", "bytes": 300, "sha256": V.STANDARD_INITIALIZER_SHA256, "mode": 0o644}
        after = {"state": "regular", "bytes": 0, "sha256": V.EMPTY_SHA256, "mode": 0o600} if created else {
            **before, "state": "regular"}
        return {"schemaVersion": 1, "kind": "beta7-missing-optional-cargo-env", "sourceCommit": V.RECOVERY_SOURCE,
                "workflowCommit": self.W, "ref": "refs/heads/" + V.RECOVERY_BRANCH, "tag": "",
                "nativeRevision": V.NATIVE_BETA7_ENGINE, "runId": 55, "attempt": 1,
                "initializer": "rustup-optional-shell-env", "created": created, "before": before,
                "after": after, "afterRuntime": copy.deepcopy(after), "runtimeVerified": True}

    def job(self):
        required, skipped = V.recovery_job_contract()
        steps = [{"name": name, "number": index + 10, "status": "completed", "conclusion": "success",
                  "started_at": "2026-10-26T01:00:00Z", "completed_at": "2026-10-26T01:00:02Z"}
                 for index, name in enumerate(required)]
        numbers = {V.REPAIR_PREPARE: 1, "Verify complete native engine ABI": 20,
                   "Verify exact native app selection and linked inputs": 70,
                   V.REPAIR_VERIFY: 90, "Upload the immutable app handoff": 100}
        for step in steps:
            step["number"] = numbers.get(step["name"], step["number"])
        steps += [{"name": name, "number": 200 + index, "status": "completed", "conclusion": "skipped"}
                  for index, name in enumerate(skipped)]
        return {"id": 99, "name": "build-tvos", "run_id": 55, "run_attempt": 1, "head_sha": self.W,
                "status": "completed", "conclusion": "success", "steps": steps}

    def test_only_exact_release_source_build_is_admitted(self):
        V.recovery_identity(V.NATIVE_BETA7_TAG, V.RECOVERY_SOURCE, V.RECOVERY_RELEASE, "280")
        values = [("v0.5.6-beta.8", V.RECOVERY_SOURCE, V.RECOVERY_RELEASE, "280"),
                  (V.NATIVE_BETA7_TAG, self.W, V.RECOVERY_RELEASE, "280"),
                  (V.NATIVE_BETA7_TAG, V.RECOVERY_SOURCE, V.RECOVERY_RELEASE + 1, "280"),
                  (V.NATIVE_BETA7_TAG, V.RECOVERY_SOURCE, V.RECOVERY_RELEASE, "281")]
        for args in values:
            with self.subTest(args=args), self.assertRaises(ValueError): V.recovery_identity(*args)
        for name in ("OP_WORKFLOW_COMMIT", "OP_WORKFLOW_SHA256"):
            with mock.patch.object(V, name, ""), self.assertRaises(ValueError):
                V.recovery_identity(V.NATIVE_BETA7_TAG, V.RECOVERY_SOURCE, V.RECOVERY_RELEASE)

    def test_twelve_dispatch_fields_remain_typed_exact_and_unmodified(self):
        receipt = self.dispatch(); original = copy.deepcopy(receipt)
        V.validate_recovery_dispatch(receipt, self.P)
        self.assertEqual(len(receipt["inputs"]), 12)
        self.assertEqual(receipt, original)
        for key, value in receipt["inputs"].items():
            invalid = (None, 1, "true", not value) if type(value) is bool else (None, False, [], "wrong", " ")
            for changed_value in invalid:
                changed = copy.deepcopy(receipt); changed["inputs"][key] = changed_value
                with self.subTest(key=key, value=changed_value), self.assertRaises(ValueError):
                    V.validate_recovery_dispatch(changed, self.P)
            changed = copy.deepcopy(receipt); del changed["inputs"][key]
            with self.assertRaises(ValueError): V.validate_recovery_dispatch(changed, self.P)
        changed = copy.deepcopy(receipt); changed["inputs"]["diagnose_startup"] = False
        with self.assertRaises(ValueError): V.validate_recovery_dispatch(changed, self.P)
        for key, value in (("sourceCommit", self.W), ("workflowCommit", V.RECOVERY_SOURCE),
                           ("ref", "refs/tags/" + V.NATIVE_BETA7_TAG), ("attempt", 2), ("runId", 56),
                           ("schemaVersion", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                V.validate_recovery_dispatch({**receipt, key: value}, self.P)

    def test_original_native_provenance_records_two_sources_and_empty_tag(self):
        tools = {"scripts/verify-native-apple-package.py": "d" * 64}
        value = {"schemaVersion": 1, "sourceCommit": V.RECOVERY_SOURCE, "workflowCommit": self.W,
                 "verifierSha256": "d" * 64, "tag": "", "runId": 55, "attempt": 1}
        V.validate_recovery_workflow(value, self.P, tools)
        for key, changed in (("workflowCommit", V.RECOVERY_SOURCE), ("sourceCommit", self.W),
                             ("tag", V.NATIVE_BETA7_TAG), ("verifierSha256", "e" * 64), ("runId", 56), ("attempt", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                V.validate_recovery_workflow({**value, key: changed}, self.P, tools)
        with self.assertRaises(ValueError): V.validate_recovery_workflow({**value, "unknown": 1}, self.P, tools)

    def test_missing_and_preserved_initializer_receipts_have_exact_compact_hashes(self):
        for created in (True, False):
            receipt = self.repair(created); before = copy.deepcopy(receipt)
            initial, final = V.validate_environment_repair(receipt, self.P)
            self.assertEqual(receipt, before)
            self.assertEqual(initial, hashlib.sha256(V.repair_bytes({**receipt, "afterRuntime": None, "runtimeVerified": False})).hexdigest())
            self.assertEqual(final, hashlib.sha256(V.repair_bytes(receipt)).hexdigest())
            self.assertNotEqual(final, hashlib.sha256(V.canonical(receipt)).hexdigest())

    def test_environment_identity_unknown_missing_and_typed_mutations_fail(self):
        receipt = self.repair()
        for key in receipt:
            changed = copy.deepcopy(receipt); del changed[key]
            with self.subTest(missing=key), self.assertRaises((ValueError, KeyError)):
                V.validate_environment_repair(changed, self.P)
        for key, value in (("kind", "another-repair"), ("sourceCommit", self.W), ("workflowCommit", V.RECOVERY_SOURCE),
                           ("ref", "refs/heads/main"), ("tag", V.NATIVE_BETA7_TAG), ("nativeRevision", "e" * 40),
                           ("runId", 56), ("attempt", True), ("created", 1), ("runtimeVerified", False),
                           ("initializer", "another-file"), ("schemaVersion", True)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                V.validate_environment_repair({**receipt, key: value}, self.P)
        with self.assertRaises(ValueError): V.validate_environment_repair({**receipt, "unknown": None}, self.P)

    def test_initializer_absence_existing_content_mode_and_post_runtime_are_mandatory(self):
        for created in (True, False):
            receipt = self.repair(created)
            for field in ("before", "after", "afterRuntime"):
                for key, value in (("state", "symlink"), ("bytes", True), ("sha256", "e" * 64), ("mode", 0o7777)):
                    changed = copy.deepcopy(receipt); changed[field][key] = value
                    with self.subTest(created=created, field=field, key=key), self.assertRaises(ValueError):
                        V.validate_environment_repair(changed, self.P)
            changed = copy.deepcopy(receipt); changed["afterRuntime"] = None
            with self.assertRaises(ValueError): V.validate_environment_repair(changed, self.P)

    def test_every_original_success_and_skip_gate_survives_recovery(self):
        job = self.job(); V.successful_recovery_job([job], self.P)
        required, skipped = V.recovery_job_contract()
        original_required, original_skipped = V.job_contract("apple", V.NATIVE_BETA7_TAG)
        self.assertTrue(set(original_required) <= set(required))
        self.assertEqual(set(original_skipped) - set(skipped), set(V.RECOVERED_SOURCE_STEPS))
        for name in required + skipped:
            for mode in ("missing", "duplicate", "wrong-outcome"):
                changed = copy.deepcopy(job); step = next(s for s in changed["steps"] if s["name"] == name)
                if mode == "missing": changed["steps"].remove(step)
                elif mode == "duplicate": changed["steps"].append(copy.deepcopy(step))
                else: step["conclusion"] = "failure" if name in required else "success"
                with self.subTest(name=name, mode=mode), self.assertRaises(ValueError):
                    V.successful_recovery_job([changed], self.P)
        for key, value in (("head_sha", V.RECOVERY_SOURCE), ("run_attempt", 2), ("run_id", 56), ("conclusion", "failure")):
            with self.subTest(key=key), self.assertRaises(ValueError): V.successful_recovery_job([{**job, key: value}], self.P)

    def test_prepare_and_post_runtime_marker_hashes_have_unique_step_chronology(self):
        receipt = self.repair(); _, steps = V.successful_recovery_job([self.job()], self.P)
        first, final = V.validate_environment_repair(receipt, self.P)
        lines = ["2026-10-26T01:00:01Z " + V.REPAIR_PREPARED_MARKER + first,
                 "2026-10-26T01:00:01Z " + V.REPAIR_VERIFIED_MARKER + final]
        V.repair_log_proof(receipt, self.P, steps, lines)
        for invalid in (lines[:1], lines + lines[:1], [lines[0].replace(first, "f" * 64), lines[1]],
                        [line.replace("T01:", "T00:") for line in lines]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): V.repair_log_proof(receipt, self.P, steps, invalid)
        changed = self.job(); next(s for s in changed["steps"] if s["name"] == V.REPAIR_VERIFY)["number"] = 200
        with self.assertRaises(ValueError): V.successful_recovery_job([changed], self.P)

    def test_authenticated_split_keeps_original_upload_zip_and_repair_proofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary); archive = work / "fixture.zip"; receipt = self.repair()
            with zipfile.ZipFile(archive, "w") as z: z.writestr(V.REPAIR_RECEIPT, V.repair_bytes(receipt))
            raw = archive.read_bytes(); sha = hashlib.sha256(raw).hexdigest(); job = self.job()
            repository = {"id": 1261501126, "full_name": V.REPO}
            run = {"id": 55, "run_attempt": 1, "event": "workflow_dispatch", "head_sha": self.W,
                   "head_branch": V.RECOVERY_BRANCH, "path": ".github/workflows/release-tvos.yml", "workflow_id": 7,
                   "status": "completed", "conclusion": "success", "repository": repository, "head_repository": repository}
            artifact = {"id": 88, "name": "VortX-tvOS-ci", "expired": False, "size_in_bytes": len(raw),
                        "digest": "sha256:" + sha, "created_at": "2026-10-26T01:00:01Z",
                        "workflow_run": {"id": 55, "head_sha": self.W, "head_branch": V.RECOVERY_BRANCH,
                                         "repository_id": 1261501126, "head_repository_id": 1261501126}}
            metadata = {"actions/runs/55": run, "actions/workflows/release-tvos.yml": {"id": 7, "path": run["path"]},
                        "actions/runs/55/attempts/1/jobs?per_page=100&page=1": {"jobs": [job], "total_count": 1},
                        "actions/artifacts/88": artifact}
            first, final = V.validate_environment_repair(receipt, self.P)
            lines = ["2026-10-26T01:00:00.100Z SHA256 digest of uploaded artifact is " + sha,
                     "2026-10-26T01:00:01.100Z Artifact VortX-tvOS-ci successfully finalized. Artifact ID 88",
                     f"2026-10-26T01:00:02.100Z Artifact VortX-tvOS-ci has been successfully uploaded! Final size is {len(raw)} bytes. Artifact ID is 88",
                     "2026-10-26T01:00:01Z " + V.REPAIR_PREPARED_MARKER + first,
                     "2026-10-26T01:00:01Z " + V.REPAIR_VERIFIED_MARKER + final]
            downloads = {"actions/jobs/99/logs": ("\n".join(lines) + "\n").encode(), "actions/artifacts/88/zip": raw}
            def download(endpoint, target, limit):
                self.assertLessEqual(len(downloads[endpoint]), limit); target.write_bytes(downloads[endpoint])
            with mock.patch.object(V, "api", side_effect=metadata.__getitem__), mock.patch.object(V, "api_download", side_effect=download):
                output, public = V.authenticate_recovery_build(self.P, {V.REPAIR_RECEIPT}, work)
            self.assertEqual(public["sourceCommit"], V.RECOVERY_SOURCE)
            self.assertEqual(public["workflowCommit"], self.W)
            self.assertEqual(public["headBranch"], V.RECOVERY_BRANCH)
            self.assertEqual(public["environmentRepairReceiptSha256"], V.digest(output / V.REPAIR_RECEIPT))
            # Reauthenticate a valid ZIP/upload carrying semantically equal but rewritten
            # receipt bytes: the original raw producer/log binding must reject it.
            with zipfile.ZipFile(archive, "w") as z: z.writestr(V.REPAIR_RECEIPT, V.canonical(receipt))
            rewritten = archive.read_bytes(); rewritten_sha = hashlib.sha256(rewritten).hexdigest()
            rewritten_metadata = copy.deepcopy(metadata)
            rewritten_metadata["actions/artifacts/88"].update(size_in_bytes=len(rewritten), digest="sha256:" + rewritten_sha)
            rewritten_log = "\n".join(lines).replace(sha, rewritten_sha).replace(
                f"Final size is {len(raw)} bytes", f"Final size is {len(rewritten)} bytes") + "\n"
            rewritten_downloads = {"actions/jobs/99/logs": rewritten_log.encode(), "actions/artifacts/88/zip": rewritten}
            def download_rewritten(endpoint, target, limit):
                self.assertLessEqual(len(rewritten_downloads[endpoint]), limit)
                target.write_bytes(rewritten_downloads[endpoint])
            with tempfile.TemporaryDirectory() as other, \
                    mock.patch.object(V, "api", side_effect=rewritten_metadata.__getitem__), \
                    mock.patch.object(V, "api_download", side_effect=download_rewritten), \
                    self.assertRaisesRegex(ValueError, "receipt bytes differ"):
                V.authenticate_recovery_build(self.P, {V.REPAIR_RECEIPT}, Path(other))
            for item, key, invalid in ((run, "head_sha", V.RECOVERY_SOURCE), (run, "head_branch", V.NATIVE_BETA7_TAG),
                                        (run, "run_attempt", 2), (artifact["workflow_run"], "head_sha", V.RECOVERY_SOURCE),
                                        (artifact, "id", 89)):
                old = item[key]; item[key] = invalid
                try:
                    with tempfile.TemporaryDirectory() as other, mock.patch.object(V, "api", side_effect=metadata.__getitem__), \
                            mock.patch.object(V, "api_download", side_effect=download), self.assertRaises(ValueError):
                        V.authenticate_recovery_build(self.P, {V.REPAIR_RECEIPT}, Path(other))
                finally: item[key] = old

    def test_retained_nine_source_contract_and_package_guards_are_literal(self):
        root = Path(os.environ.get("CI_DIRECT_BETA_REPO_ROOT", Path(__file__).resolve().parents[2]))
        # The original frozen verifier remains a distinct source-contract input.
        frozen = (root / "scripts/verify-ci-direct-beta.py").read_text()
        derivative = Path(V.__file__).read_text()
        self.assertEqual(hashlib.sha256(frozen.encode()).hexdigest(), V.FROZEN_VERIFIER_SHA256)
        for first, second in (("def source_contract(", "def source_versions("),
                              ("def inspect_android(", "def stable_snapshot("),
                              ("def validate_asset_url(", "# Only this already-tagged")):
            left = frozen.split(first, 1)[1].split(second, 1)[0] if second in frozen else frozen.split(first, 1)[1].split("def main(", 1)[0]
            right = derivative.split(first, 1)[1].split(second, 1)[0]
            self.assertEqual(left.strip(), right.strip())
        self.assertEqual(len(V.TOOLS), 9)
        self.assertEqual(V.ANDROID_POINTER, {"runId": 40237231304, "attempt": 1, "artifactId": 12616346338})
        self.assertEqual(len(V.file_matrix(V.NATIVE_BETA7_TAG, "0.5.6")[0]), 4)


if __name__ == "__main__":
    unittest.main()
