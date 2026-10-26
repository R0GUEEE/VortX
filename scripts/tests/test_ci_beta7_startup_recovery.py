"""UNRUN focused pure fixtures for the exact Beta 7 operational verifier.

Root may execute in the one allocated low-priority lane; no production API,
build, app launch, signing, account, or package-tool invocation is performed.
"""
import base64
import copy
from datetime import datetime, timedelta, timezone
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

    def fixture(self):
        return {"schemaVersion": 1, "kind": "beta7-live-abi-fixture-compat", "sourceCommit": V.RECOVERY_SOURCE,
                "workflowCommit": self.W, "ref": "refs/heads/" + V.RECOVERY_BRANCH, "tag": "",
                "nativeRevision": V.NATIVE_BETA7_ENGINE, "runId": 55, "attempt": 1, "testPath": V.FIXTURE_PATH,
                "beforeSha256": V.FIXTURE_BEFORE, "fixtureSha256": V.FIXTURE_AFTER,
                "runnerSha256": V.FIXTURE_RUNNER_SHA256, "fixtureOnlyDiff": True, "runtimeOutcome": "success",
                "restoredSha256": V.FIXTURE_BEFORE, "restoredClean": True}

    @staticmethod
    def log(step, body):
        return step["started_at"] + " " + body

    def extra_logs(self, job):
        steps = {step["name"]: step for step in job["steps"]}
        first, final = V.validate_fixture_repair(self.fixture(), self.P)
        return [self.log(steps[V.FIXTURE_PREPARE], V.FIXTURE_PREPARED_MARKER + first),
                self.log(steps[V.FIXTURE_RESTORE], V.FIXTURE_RESTORED_MARKER + final),
                self.log(steps[V.FIXTURE_RUNTIME], V.FIXTURE_PATH + ": OK"),
                self.log(steps[V.FIXTURE_RUNTIME], "Verified unchanged library " + "a" * 64),
                self.log(steps[V.FIXTURE_RUNTIME], "Verified unchanged header " + "d" * 64),
                self.log(steps[V.SDK_CACHE_RESTORE], "Cache not found for input keys: " + self.cache_key())]

    @staticmethod
    def cache_key():
        return "vortx-ffi-resource-host-v1-macOS-Xcode-26.6-Build-version-17F113--" + V.NATIVE_BETA7_ENGINE + "-" + "a" * 64

    def job(self):
        required, skipped = V.recovery_job_contract()
        steps = [{"name": name, "number": index + 10, "status": "completed", "conclusion": "success",
                  "started_at": "2026-10-26T01:00:00Z", "completed_at": "2026-10-26T01:00:02Z"}
                 for index, name in enumerate(required)]
        steps += [{"name": name, "number": 200 + index, "status": "completed", "conclusion": "skipped"}
                  for index, name in enumerate(skipped)]
        steps += [{"name": name, "status": "completed", "conclusion": "success"}
                  for name in (V.SDK_BUILD, V.SDK_EARLY_SAVE)]
        numbers = {V.REPAIR_PREPARE: 10, V.SDK_CACHE_RESTORE: 15, V.SDK_BUILD: 16,
                   "Verify complete native engine ABI": 20,
                   "Build native macOS streaming server from the same pin": 21,
                   "Verify Apple engine artifacts (fail closed)": 22, V.SDK_EARLY_SAVE: 23,
                   V.FIXTURE_PREPARE: 30, V.FIXTURE_RUNTIME: 40, V.FIXTURE_RESTORE: 50,
                   "Fetch the MPVKit-DVFEL artifacts (pinned, sha256-verified)": 55,
                   "Generate the Xcode project": 60,
                   "Capture exact native SDK and player inputs before app compilation": 61,
                   "Check shared server settings at the iOS 16 deployment target": 62,
                   "Smoke-test tvOS launch in a simulator (fail closed)": 65,
                   "Build tvOS (Full)": 70, "Build tvOS (Lite)": 71, "Build iOS": 72,
                   "Gate iOS-simulator link + dSYM (fail closed)": 73, "Build macOS": 74,
                   "Verify exact native app selection and linked inputs": 80,
                   V.REPAIR_VERIFY: 90, "Upload the immutable app handoff": 100}
        base = datetime(2026, 10, 26, 1, tzinfo=timezone.utc)
        for index, step in enumerate(steps):
            number = numbers.get(step["name"], index + 10)
            step.update(number=number, started_at=(base + timedelta(seconds=number * 3)).isoformat().replace("+00:00", "Z"),
                        completed_at=(base + timedelta(seconds=number * 3 + 1)).isoformat().replace("+00:00", "Z"))
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
        lines = [self.log(steps[V.REPAIR_PREPARE], V.REPAIR_PREPARED_MARKER + first),
                 self.log(steps[V.REPAIR_VERIFY], V.REPAIR_VERIFIED_MARKER + final)]
        V.repair_log_proof(receipt, self.P, steps, lines)
        for invalid in (lines[:1], lines + lines[:1], [lines[0].replace(first, "f" * 64), lines[1]],
                        [line.replace("T01:", "T00:") for line in lines]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): V.repair_log_proof(receipt, self.P, steps, invalid)
        changed = self.job(); next(s for s in changed["steps"] if s["name"] == V.REPAIR_VERIFY)["number"] = 200
        with self.assertRaises(ValueError): V.successful_recovery_job([changed], self.P)

    def test_authenticated_split_keeps_original_upload_zip_and_repair_proofs(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary); archive = work / "fixture.zip"; receipt = self.repair()
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(V.REPAIR_RECEIPT, V.repair_bytes(receipt))
                z.writestr(V.FIXTURE_RECEIPT, V.repair_bytes(self.fixture()))
            raw = archive.read_bytes(); sha = hashlib.sha256(raw).hexdigest(); job = self.job()
            steps = {step["name"]: step for step in job["steps"]}
            upload_time = steps["Upload the immutable app handoff"]["started_at"]
            repository = {"id": 1261501126, "full_name": V.REPO}
            run = {"id": 55, "run_attempt": 1, "event": "workflow_dispatch", "head_sha": self.W,
                   "head_branch": V.RECOVERY_BRANCH, "path": ".github/workflows/release-tvos.yml", "workflow_id": 7,
                   "status": "completed", "conclusion": "success", "repository": repository, "head_repository": repository}
            artifact = {"id": 88, "name": "VortX-tvOS-ci", "expired": False, "size_in_bytes": len(raw),
                        "digest": "sha256:" + sha, "created_at": upload_time,
                        "workflow_run": {"id": 55, "head_sha": self.W, "head_branch": V.RECOVERY_BRANCH,
                                         "repository_id": 1261501126, "head_repository_id": 1261501126}}
            metadata = {"actions/runs/55": run, "actions/workflows/release-tvos.yml": {"id": 7, "path": run["path"]},
                        "actions/runs/55/attempts/1/jobs?per_page=100&page=1": {"jobs": [job], "total_count": 1},
                        "actions/artifacts/88": artifact}
            first, final = V.validate_environment_repair(receipt, self.P)
            lines = [upload_time + " SHA256 digest of uploaded artifact is " + sha,
                     upload_time + " Artifact VortX-tvOS-ci successfully finalized. Artifact ID 88",
                     upload_time + f" Artifact VortX-tvOS-ci has been successfully uploaded! Final size is {len(raw)} bytes. Artifact ID is 88",
                     self.log(steps[V.REPAIR_PREPARE], V.REPAIR_PREPARED_MARKER + first),
                     self.log(steps[V.REPAIR_VERIFY], V.REPAIR_VERIFIED_MARKER + final)] + self.extra_logs(job)
            downloads = {"actions/jobs/99/logs": ("\n".join(lines) + "\n").encode(), "actions/artifacts/88/zip": raw}
            def download(endpoint, target, limit):
                self.assertLessEqual(len(downloads[endpoint]), limit); target.write_bytes(downloads[endpoint])
            with mock.patch.object(V, "api", side_effect=metadata.__getitem__), mock.patch.object(V, "api_download", side_effect=download):
                output, public = V.authenticate_recovery_build(self.P, {V.REPAIR_RECEIPT, V.FIXTURE_RECEIPT}, work)
            self.assertEqual(public["sourceCommit"], V.RECOVERY_SOURCE)
            self.assertEqual(public["workflowCommit"], self.W)
            self.assertEqual(public["headBranch"], V.RECOVERY_BRANCH)
            self.assertEqual(public["environmentRepairReceiptSha256"], V.digest(output / V.REPAIR_RECEIPT))
            # Reauthenticate a valid ZIP/upload carrying semantically equal but rewritten
            # receipt bytes: the original raw producer/log binding must reject it.
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(V.REPAIR_RECEIPT, V.canonical(receipt))
                z.writestr(V.FIXTURE_RECEIPT, V.repair_bytes(self.fixture()))
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
                V.authenticate_recovery_build(self.P, {V.REPAIR_RECEIPT, V.FIXTURE_RECEIPT}, Path(other))
            for item, key, invalid in ((run, "head_sha", V.RECOVERY_SOURCE), (run, "head_branch", V.NATIVE_BETA7_TAG),
                                        (run, "run_attempt", 2), (artifact["workflow_run"], "head_sha", V.RECOVERY_SOURCE),
                                        (artifact, "id", 89)):
                old = item[key]; item[key] = invalid
                try:
                    with tempfile.TemporaryDirectory() as other, mock.patch.object(V, "api", side_effect=metadata.__getitem__), \
                            mock.patch.object(V, "api_download", side_effect=download), self.assertRaises(ValueError):
                        V.authenticate_recovery_build(self.P, {V.REPAIR_RECEIPT, V.FIXTURE_RECEIPT}, Path(other))
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

class LiveABIFixtureRecoveryContract(unittest.TestCase):
    W, P = ExactStartupRecoveryContract.W, ExactStartupRecoveryContract.P
    setUp = ExactStartupRecoveryContract.setUp
    fixture = ExactStartupRecoveryContract.fixture
    repair = ExactStartupRecoveryContract.repair
    job = ExactStartupRecoveryContract.job
    extra_logs = ExactStartupRecoveryContract.extra_logs
    log = staticmethod(ExactStartupRecoveryContract.log)
    cache_key = staticmethod(ExactStartupRecoveryContract.cache_key)

    def test_exact_fixture_receipt_preserves_original_corrected_and_restored_hashes(self):
        receipt = self.fixture(); original = copy.deepcopy(receipt)
        before, after = V.validate_fixture_repair(receipt, self.P)
        self.assertEqual(receipt, original)
        self.assertEqual(before, hashlib.sha256(V.repair_bytes({**receipt, "runtimeOutcome": None,
                                                               "restoredSha256": None, "restoredClean": False})).hexdigest())
        self.assertEqual(after, hashlib.sha256(V.repair_bytes(receipt)).hexdigest())
        self.assertEqual(receipt["beforeSha256"], receipt["restoredSha256"])
        self.assertNotEqual(receipt["fixtureSha256"], receipt["restoredSha256"])

    def test_fixture_missing_extra_type_identity_outcome_and_restoration_mutations_reject(self):
        receipt = self.fixture()
        for key in receipt:
            changed = copy.deepcopy(receipt); del changed[key]
            with self.subTest(missing=key), self.assertRaises(ValueError): V.validate_fixture_repair(changed, self.P)
        changes = [("schemaVersion", True), ("kind", "different"), ("sourceCommit", self.W),
                   ("workflowCommit", V.RECOVERY_SOURCE), ("ref", "refs/heads/main"), ("tag", V.NATIVE_BETA7_TAG),
                   ("nativeRevision", "a" * 40), ("runId", 56), ("attempt", 2), ("testPath", "app/Other.swift"),
                   ("beforeSha256", V.FIXTURE_AFTER), ("fixtureSha256", V.FIXTURE_BEFORE),
                   ("runnerSha256", "a" * 64), ("fixtureOnlyDiff", 1), ("restoredSha256", V.FIXTURE_AFTER),
                   ("restoredClean", False), ("restoredClean", 1)]
        changes += [("runtimeOutcome", value) for value in (None, "failure", "cancelled", "skipped", "Success", True)]
        for key, value in changes:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                V.validate_fixture_repair({**receipt, key: value}, self.P)
        with self.assertRaises(ValueError): V.validate_fixture_repair({**receipt, "unknown": None}, self.P)

    def test_full_runtime_and_both_fixture_markers_are_required_in_their_original_steps(self):
        job = self.job(); _, steps = V.successful_recovery_job([job], self.P); lines = self.extra_logs(job)
        proof = V.live_abi_log_proof(self.fixture(), self.P, steps, lines)
        self.assertEqual(proof["runtimeHeaderSha256"], "d" * 64)
        self.assertEqual(proof["runtimeLibrarySha256"], "a" * 64)
        for index in range(5):
            for mode in ("missing", "duplicate", "wrong-step", "wrong-value"):
                changed = list(lines)
                if mode == "missing": del changed[index]
                elif mode == "duplicate": changed.append(changed[index])
                elif mode == "wrong-step": changed[index] = self.log(steps[V.SDK_CACHE_RESTORE], changed[index].partition(" ")[2])
                else: changed[index] += "-wrong"
                with self.subTest(index=index, mode=mode), self.assertRaises(ValueError):
                    V.live_abi_log_proof(self.fixture(), self.P, steps, changed)

    def test_restoration_must_precede_every_app_compile_and_follow_full_runtime(self):
        job = self.job(); _, steps = V.successful_recovery_job([job], self.P); lines = self.extra_logs(job)
        names = ("Fetch the MPVKit-DVFEL artifacts (pinned, sha256-verified)", "Generate the Xcode project",
                 "Capture exact native SDK and player inputs before app compilation",
                 "Check shared server settings at the iOS 16 deployment target",
                 "Smoke-test tvOS launch in a simulator (fail closed)", "Build tvOS (Full)",
                 "Build tvOS (Lite)", "Build iOS", "Gate iOS-simulator link + dSYM (fail closed)", "Build macOS")
        for name in names:
            for key, value in (("number", steps[V.FIXTURE_RESTORE]["number"] - 1),
                               ("started_at", steps[V.FIXTURE_PREPARE]["started_at"])):
                changed = copy.deepcopy(steps); changed[name][key] = value
                with self.subTest(name=name, key=key), self.assertRaises(ValueError):
                    V.live_abi_log_proof(self.fixture(), self.P, changed, lines)
        for name, neighbor in ((V.FIXTURE_PREPARE, V.FIXTURE_RUNTIME), (V.FIXTURE_RUNTIME, V.FIXTURE_RESTORE)):
            changed = copy.deepcopy(steps); changed[name]["completed_at"] = steps[neighbor]["completed_at"]
            with self.subTest(name=name), self.assertRaises(ValueError):
                V.live_abi_log_proof(self.fixture(), self.P, changed, lines)

        invalid_duration = copy.deepcopy(steps[V.FIXTURE_RUNTIME])
        raw_start = datetime.fromisoformat(invalid_duration["started_at"].replace("Z", "+00:00"))
        invalid_duration["completed_at"] = (raw_start - timedelta(milliseconds=500)).isoformat().replace("+00:00", "Z")
        with self.assertRaisesRegex(ValueError, "negative step duration"):
            V.step_log_bodies(lines, invalid_duration)

    def test_sdk_cache_miss_and_exact_hit_preserve_all_runtime_gates(self):
        job = self.job(); _, steps = V.successful_recovery_job([job], self.P); lines = self.extra_logs(job)
        self.assertEqual(V.sdk_cache_proof(steps, lines)["restore"], "miss-built-and-save-step-succeeded")
        hit_steps = copy.deepcopy(steps)
        hit_steps[V.SDK_BUILD]["conclusion"] = hit_steps[V.SDK_EARLY_SAVE]["conclusion"] = "skipped"
        hit_lines = [line for line in lines if "Cache not found for input keys:" not in line] + [
            self.log(steps[V.SDK_CACHE_RESTORE], "Cache hit for: " + self.cache_key()),
            self.log(steps[V.SDK_CACHE_RESTORE], "Cache restored from key: " + self.cache_key())]
        self.assertEqual(V.sdk_cache_proof(hit_steps, hit_lines)["restore"], "exact-hit")
        # Even a legitimate cache hit does not permit the full live runtime to be skipped.
        changed_job = copy.deepcopy(job)
        for step in changed_job["steps"]:
            if step["name"] in (V.SDK_BUILD, V.SDK_EARLY_SAVE): step["conclusion"] = "skipped"
            if step["name"] == V.FIXTURE_RUNTIME: step["conclusion"] = "skipped"
        with self.assertRaises(ValueError): V.successful_recovery_job([changed_job], self.P)
        invalid = [(steps, hit_lines), (hit_steps, lines), (hit_steps, hit_lines[:-1]),
                   (hit_steps, hit_lines + [hit_lines[-1]]),
                   (hit_steps, [line.replace(V.NATIVE_BETA7_ENGINE, "a" * 40) for line in hit_lines])]
        for selected, observed in invalid:
            with self.subTest(observed=observed), self.assertRaises(ValueError): V.sdk_cache_proof(selected, observed)

    def test_early_component_save_cannot_move_ahead_of_native_artifact_proof(self):
        job = self.job(); _, steps = V.successful_recovery_job([job], self.P); lines = self.extra_logs(job)
        changed = copy.deepcopy(steps)
        changed[V.SDK_EARLY_SAVE]["number"] = steps["Verify Apple engine artifacts (fail closed)"]["number"] - 1
        with self.assertRaises(ValueError): V.sdk_cache_proof(changed, lines)
        changed = copy.deepcopy(steps); changed[V.SDK_EARLY_SAVE]["number"] = steps[V.FIXTURE_PREPARE]["number"] + 1
        with self.assertRaises(ValueError): V.sdk_cache_proof(changed, lines)

class RawLogTimestampBOMContract(unittest.TestCase):
    W, P = ExactStartupRecoveryContract.W, ExactStartupRecoveryContract.P
    setUp = ExactStartupRecoveryContract.setUp
    fixture = ExactStartupRecoveryContract.fixture
    job = ExactStartupRecoveryContract.job
    extra_logs = ExactStartupRecoveryContract.extra_logs
    log = staticmethod(ExactStartupRecoveryContract.log)
    cache_key = staticmethod(ExactStartupRecoveryContract.cache_key)

    @staticmethod
    def step():
        return {"started_at": "2026-10-26T00:01:36Z", "completed_at": "2026-10-26T00:01:37Z"}

    def test_multichunk_timestamp_prefix_bom_conserves_every_raw_line_and_body_byte(self):
        lines = ["\ufeff2026-10-26T00:01:36.1786820Z first chunk",
                 "2026-10-26T00:01:36.2000000Z ordinary body",
                 "\ufeff2026-10-26T00:01:36.2581980Z second chunk",
                 "2026-10-26T00:01:36.3000000Z \ufeffbody stays literal",
                 "\ufeff2026-10-26T00:01:36.4006630Z third chunk"]
        original = list(lines); raw = ("\n".join(lines) + "\n").encode("utf-8")
        result = V.step_log_bodies(lines, self.step())
        self.assertEqual(result, ["first chunk", "ordinary body", "second chunk", "\ufeffbody stays literal", "third chunk"])
        self.assertEqual(lines, original)
        self.assertEqual(("\n".join(lines) + "\n").encode("utf-8"), raw)
        self.assertEqual(hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).digest(), hashlib.sha256(raw).digest())

    def test_internal_double_trailing_bom_and_malformed_raw_timestamps_still_fail(self):
        valid = "2026-10-26T00:01:36.1786820Z"
        invalid = ["\ufeff\ufeff" + valid, valid.replace("T", "\ufeff", 1),
                   valid.replace(":01:", ":\ufeff01:", 1), valid + "\ufeff", "not-a-timestamp",
                   "\ufeffnot-a-timestamp", "\ufeff"]
        for stamp in invalid:
            lines = [stamp + " unchanged body"]
            original = list(lines)
            with self.subTest(stamp=stamp), self.assertRaises(ValueError): V.step_log_bodies(lines, self.step())
            self.assertEqual(lines, original)

    def test_api_step_timestamps_never_use_the_raw_chunk_bom_exception(self):
        for field in ("started_at", "completed_at"):
            value = self.step()[field]
            for invalid in ("\ufeff" + value, "\ufeff\ufeff" + value, value.replace("T", "\ufeff", 1),
                            value + "\ufeff", "not-a-timestamp"):
                step = {**self.step(), field: invalid}
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    V.step_log_bodies([], step)

    def test_full_marker_proofs_allow_timestamp_bom_but_never_normalize_marker_bodies(self):
        job = self.job(); _, steps = V.successful_recovery_job([job], self.P)
        lines = self.extra_logs(job)
        chunked = ["\ufeff" + line if index in (0, 2, 4) else line for index, line in enumerate(lines)]
        original = list(chunked); receipt = self.fixture(); raw_receipt = V.repair_bytes(receipt)
        raw_log = ("\n".join(chunked) + "\n").encode("utf-8")
        V.live_abi_log_proof(receipt, self.P, steps, chunked)
        V.sdk_cache_proof(steps, chunked)
        self.assertEqual(chunked, original)
        self.assertEqual(("\n".join(chunked) + "\n").encode("utf-8"), raw_log)
        self.assertEqual(V.repair_bytes(receipt), raw_receipt)
        for index in (0, 1):
            stamp, _, body = lines[index].partition(" ")
            for changed_body in ("\ufeff" + body, "\ufeff\ufeff" + body, body.replace("FIXTURE", "FI\ufeffXTURE", 1)):
                changed = list(chunked); changed[index] = stamp + " " + changed_body
                before = list(changed)
                self.assertIn(changed_body, V.step_log_bodies(changed, steps[V.FIXTURE_PREPARE if index == 0 else V.FIXTURE_RESTORE]))
                with self.subTest(index=index, changed_body=changed_body), self.assertRaises(ValueError):
                    V.live_abi_log_proof(receipt, self.P, steps, changed)
                self.assertEqual(changed, before)
                self.assertEqual(V.repair_bytes(receipt), raw_receipt)

class UntimestampedJSONContinuationContract(unittest.TestCase):
    W, P = ExactStartupRecoveryContract.W, ExactStartupRecoveryContract.P
    setUp = ExactStartupRecoveryContract.setUp
    fixture = ExactStartupRecoveryContract.fixture
    job = ExactStartupRecoveryContract.job
    extra_logs = ExactStartupRecoveryContract.extra_logs
    log = staticmethod(ExactStartupRecoveryContract.log)
    cache_key = staticmethod(ExactStartupRecoveryContract.cache_key)
    step = staticmethod(RawLogTimestampBOMContract.step)

    @staticmethod
    def field_lines():
        # Actual retained GitHub env-display shape; these are never evidence.
        return ['  "publish_existing_055": false,', '  "native_only": true,',
                '  "tvos_test_only": false,', '  "publish_release": false,',
                '  "apple_only": false,', '  "recovery_source_commit": "' + V.RECOVERY_SOURCE + '"']

    def block(self, step, name="DISPATCH_INPUTS_JSON"):
        return [self.log(step, "  " + name + ": {")] + self.field_lines() + ["}"]

    def test_two_approved_blocks_are_non_evidence_without_inherited_time_or_byte_changes(self):
        lines = ["2026-10-26T00:01:35Z   RECOVERY_INPUTS_JSON: {"] + self.field_lines() + ["}"] + [
            "2026-10-26T00:01:36.1Z actual marker",
            "\ufeff2026-10-26T00:01:36.2Z   DISPATCH_INPUTS_JSON: {"] + self.field_lines() + ["}"] + [
            "2026-10-26T00:01:36.3Z after block"]
        original = list(lines); raw = ("\n".join(lines) + "\n").encode("utf-8")
        self.assertEqual(V.step_log_bodies(lines, self.step()),
                         ["actual marker", "  DISPATCH_INPUTS_JSON: {", "after block"])
        self.assertEqual(lines, original)
        self.assertEqual(("\n".join(lines) + "\n").encode("utf-8"), raw)
        self.assertEqual(hashlib.sha256(raw).digest(), hashlib.sha256(("\n".join(lines) + "\n").encode("utf-8")).digest())

    def test_unanchored_unknown_malformed_and_forged_indented_records_fail(self):
        anchor = "2026-10-26T00:01:36Z   DISPATCH_INPUTS_JSON: {"
        field = self.field_lines()[1]
        invalid = [[field], ["2026-10-26T00:01:36Z ordinary body", field],
                   [anchor, "  VORTX_LIVE_ABI_FIXTURE_PREPARED_SHA256=" + "a" * 64],
                   [anchor, '  "native_only": false,'], [anchor, '  "unknown": true,'],
                   [anchor, '  "native_only": "VORTX_LIVE_ABI_FIXTURE_PREPARED_SHA256=forged",'],
                   [anchor, ' \t"native_only": true,'], [anchor, '\t"native_only": true,'],
                   [anchor, "  2026-10-26Tbad malformed body"], [anchor, "\ufeff" + field],
                   [anchor, field.replace("native_only", "native_\ufeffonly")]]
        for lines in invalid:
            original = list(lines)
            with self.subTest(lines=lines), self.assertRaises(ValueError): V.step_log_bodies(lines, self.step())
            self.assertEqual(lines, original)

    def test_supported_continuations_cannot_supply_any_required_marker_or_runtime_proof(self):
        job = self.job(); _, steps = V.successful_recovery_job([job], self.P)
        lines = self.extra_logs(job)
        healthy = self.block(steps[V.FIXTURE_PREPARE], "RECOVERY_INPUTS_JSON") + lines + self.block(steps[V.FIXTURE_RESTORE])
        original = list(healthy); receipt = self.fixture(); raw_receipt = V.repair_bytes(receipt)
        V.live_abi_log_proof(receipt, self.P, steps, healthy)
        V.sdk_cache_proof(steps, healthy)
        self.assertEqual(healthy, original)
        for missing in range(5):
            observed = self.block(steps[V.FIXTURE_PREPARE]) + [line for index, line in enumerate(lines) if index != missing]
            before = list(observed)
            with self.subTest(missing=missing), self.assertRaises(ValueError):
                V.live_abi_log_proof(receipt, self.P, steps, observed)
            self.assertEqual(observed, before)
            self.assertEqual(V.repair_bytes(receipt), raw_receipt)

    def test_old_no_space_records_stay_non_evidence_but_malformed_timestamp_tokens_fail(self):
        anchor = "2026-10-26T00:01:36Z   RECOVERY_INPUTS_JSON: {"
        for record in ("not-a-time body", "} body", " bogus body"):
            with self.subTest(record=record), self.assertRaises(ValueError):
                V.step_log_bodies([anchor, record], self.step())
        for record in ("", "\n", "}", "garbage", "2026-10-26T00:01:36Z", "~/.cargo/git",
                       "app/Vendor/engine-prelocalize/VortxEngine", "out/native-*.json",
                       V.FIXTURE_PREPARED_MARKER + "a" * 64):
            lines = [record]; original = list(lines)
            self.assertEqual(V.step_log_bodies(lines, self.step()), [])
            self.assertEqual(lines, original)
            # A no-space record terminates the JSON continuation context, never transfers it.
            with self.assertRaises(ValueError): V.step_log_bodies([anchor, record, self.field_lines()[0]], self.step())


if __name__ == "__main__":
    unittest.main()
