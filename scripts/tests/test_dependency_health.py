import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("dependency_health", ROOT / "scripts/dependency-health.py")
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)


class DependencyHealthTests(unittest.TestCase):
    def fixture_inventory(self, files):
        temporary_root = ROOT / "_build"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix="dependency-fixture-") as folder:
            root = Path(folder)
            for relative, content in files.items():
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content)
            paths = b"\0".join(name.encode() for name in files) + b"\0"
            with patch.object(health.subprocess, "check_output", side_effect=["a"*40, paths]), \
                 patch.object(health.Path, "home", return_value=root):
                return health.inventory({"repositories":[{"path":folder,"label":"fixture"}]})

    def test_only_explicit_registry_specs_are_admitted(self):
        for value in ("^1.2.3", "~2", ">=1.0.0 <2", "1.0.0 - 2.0.0", "1 || 2", "*", "1.x", "next", "1.0.0-rc.1"):
            with self.subTest(value=value): self.assertTrue(health.npm_registry_spec(value))
        for value in ("owner/private-repo#branch", "gitlab:private/repo", "bitbucket:private/repo", "git@host:repo", "ssh://private/repo", "../private", "https://host/file", "npm:private@latest"):
            with self.subTest(value=value): self.assertFalse(health.npm_registry_spec(value))
        result = self.fixture_inventory({"package.json":json.dumps({"dependencies":{
            "secret":"owner/private-repo#branch", "alias":"npm:public@^1.0.0"}})})
        self.assertEqual([e["name"] for e in result["entries"]], ["public"])
        self.assertNotIn("private-repo", json.dumps(result))
        self.assertNotIn("secret", json.dumps(result))

    def test_registry_inline_comments_and_unknown_directives_fail_closed(self):
        for directive in ("registry=https://private.invalid/ # comment", "registry https://private.invalid/", "registry=${PRIVATE_REGISTRY}", 'registry="https://private.invalid/', "userconfig=/private/config"):
            with self.subTest(directive=directive):
                result = self.fixture_inventory({"package.json":'{"dependencies":{"example":"1.0.0"}}', ".npmrc":directive})
                self.assertEqual(result["entries"], [])
                self.assertTrue(result["coverage_gaps"])
                self.assertNotIn("private.invalid", json.dumps(result))
        result = self.fixture_inventory({"package.json":'{"dependencies":{"example":"1.0.0"}}',
                                         ".npmrc":'registry="https://registry.npmjs.org/" ; comment'})
        self.assertEqual([e["name"] for e in result["entries"]], ["example"])

    def test_registry_override_environment_does_not_fall_back_to_public(self):
        with patch.dict(health.os.environ, {"NPM_CONFIG_USERCONFIG":"/private/unknown"}):
            result = self.fixture_inventory({"package.json":'{"dependencies":{"example":"1.0.0"}}'})
        self.assertEqual(result["entries"], [])

    def test_nested_private_packages_are_discovered_before_all_references(self):
        result = self.fixture_inventory({
            "package.json":'{"dependencies":{"nested-private":"1.0.0","example":"1.0.0"}}',
            "zzz/nested/package.json":'{"name":"nested-private","private":true}',
            "package-lock.json":'{"packages":{"node_modules/nested-private":{"version":"1.0.0","resolved":"https://registry.npmjs.org/nested-private/-/nested-private-1.0.0.tgz"}}}'})
        self.assertEqual({e["name"] for e in result["entries"]}, {"example"})

    def test_cargo_manifest_requires_corroborated_public_origin(self):
        lock = 'version=3\n[[package]]\nname="example"\nversion="1.0.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n'
        manifest = '[dependencies]\nexample="1"\nprivate_name="1"\n'
        result = self.fixture_inventory({"Cargo.lock":lock,"Cargo.toml":manifest})
        self.assertEqual({e["name"] for e in result["entries"]}, {"example"})
        self.assertEqual(len(result["entries"]), 2)
        self.assertTrue(result["coverage_gaps"])
        for extra in ({".cargo/config.toml":'[source.crates-io]\nreplace-with="private"\n'},
                      {".cargo/config.toml":'unsupported invalid config'},):
            result = self.fixture_inventory({"Cargo.lock":lock,"Cargo.toml":manifest,**extra})
            self.assertEqual([e["kind"] for e in result["entries"]], ["resolved-transitive"])
        with patch.dict(health.os.environ, {"CARGO_REGISTRY_DEFAULT":"private"}):
            result = self.fixture_inventory({"Cargo.lock":lock,"Cargo.toml":manifest})
        self.assertEqual([e["kind"] for e in result["entries"]], ["resolved-transitive"])

    def test_cargo_identity_and_sparse_path_are_ecosystem_specific(self):
        for name in ("@scope/name", "name.with.dots", "path/name"):
            with self.subTest(name=name), patch.object(health, "get") as fetch:
                with self.assertRaises(ValueError): health.releases("cargo", name)
                fetch.assert_not_called()
        with patch.object(health,"get",return_value=b'{"vers":"1.0.0"}') as fetch:
            result = health.releases("cargo", "Example_Name")
        self.assertEqual(result["source"], "https://index.crates.io/ex/am/example_name")

    def test_stable_comparison_does_not_promote_prereleases(self):
        self.assertEqual(health.stable_key("v1.12.3"), (1, 12, 3))
        self.assertIsNone(health.stable_key("1.12.3-rc.1"))
        self.assertIsNone(health.stable_key("^1.12.3"))
        self.assertEqual(health.stable_key("2026.10.21"), (2026, 10, 21))

    def test_registry_latest_tag_cannot_label_prerelease_as_stable(self):
        raw = json.dumps({"versions":{"1.0.0":{},"2.0.0-rc.1":{}},"dist-tags":{"latest":"2.0.0-rc.1"}}).encode()
        with patch.object(health, "get", return_value=raw):
            result = health.releases("npm", "example")
        self.assertEqual(result["latest_stable"], "1.0.0")
        self.assertIn("2.0.0-rc.1", result["versions"])

    def test_private_origins_and_firstparty_names_are_not_queried_publicly(self):
        temporary_root = ROOT / "_build"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix="dependency-fixture-") as folder:
            root = Path(folder)
            (root / "package.json").write_text(json.dumps({"name":"private-local","private":True,"dependencies":{"private-local":"1.0.0","@private/pkg":"1.0.0","public":"1.0.0"}}))
            (root / ".npmrc").write_text("@private:registry=https://private.invalid/\n//private.invalid/:_authToken=DO_NOT_LEAK\n")
            (root / "package-lock.json").write_text(json.dumps({"packages":{
                "node_modules/private":{"version":"1.0.0","resolved":"https://private.invalid/private.tgz"},
                "node_modules/public":{"version":"1.0.0","resolved":"https://registry.npmjs.org/public/-/public-1.0.0.tgz"}}}))
            with patch.object(health.subprocess, "check_output", side_effect=["a"*40,b"package.json\0package-lock.json\0"]), patch.object(health.Path,"home",return_value=root):
                result = health.inventory({"repositories":[{"path":folder,"label":"fixture"}]})
            self.assertEqual({entry["name"] for entry in result["entries"]}, {"public"})
            self.assertNotIn("DO_NOT_LEAK", json.dumps(result))
            self.assertNotIn("private.invalid", json.dumps(result))

    def test_real_vendor_manifest_is_not_silently_excluded(self):
        temporary_root = ROOT / "_build"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix="dependency-fixture-") as folder:
            root = Path(folder); (root / "vendor").mkdir()
            (root / "vendor/Cargo.lock").write_text('version = 3\n[[package]]\nname="example"\nversion="1.0.0"\nsource="registry+https://github.com/rust-lang/crates.io-index"\n')
            with patch.object(health.subprocess, "check_output", side_effect=["a"*40,b"vendor/Cargo.lock\0"]):
                result = health.inventory({"repositories":[{"path":folder,"label":"fixture"}]})
            self.assertEqual(result["entries"][0]["name"], "example")

    def test_bom_managed_library_is_tracked_with_unresolved_current(self):
        temporary_root = ROOT / "_build"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix="dependency-fixture-") as folder:
            root = Path(folder)
            (root / "libs.versions.toml").write_text('[libraries]\nui={group="androidx.compose.ui",name="ui"}\n')
            with patch.object(health.subprocess, "check_output", side_effect=["a"*40,b"libs.versions.toml\0"]):
                result = health.inventory({"repositories":[{"path":folder,"label":"fixture"}],
                                           "public_maven_names":["androidx.compose.ui:ui"]})
            self.assertEqual(result["entries"][0]["name"], "androidx.compose.ui:ui")
            self.assertEqual(result["entries"][0]["current"], "BOM-managed/unresolved")
            self.assertTrue(result["coverage_gaps"])

    def test_unknown_maven_and_plugin_names_never_trigger_public_lookup(self):
        for value in ('{module="internal.company:secret",version="1.0"}',
                      '{module="internal.company:secret"}'):
            with self.subTest(value=value):
                result = self.fixture_inventory({"libs.versions.toml":
                    '[libraries]\nprivate=' + value + '\n'
                    '[plugins]\nprivate={id="internal.company.plugin",version="1.0"}\n'})
                self.assertEqual(result["entries"], [])
                self.assertNotIn("internal.company", json.dumps(result))
                self.assertTrue(result["coverage_gaps"])

    def test_multiple_new_versions_including_prerelease_are_all_reported(self):
        inventory = {"entries": [{"ecosystem":"npm", "name":"example", "current":"1.0.0"}], "coverage_gaps":[]}
        before = {"releases":{"npm:example":{"versions":["1.0.0"],"latest_stable":"1.0.0"}}}
        release = {"versions":["1.0.0","1.0.1","1.1.0","2.0.0-rc.1"],"latest_stable":"1.1.0","source":"https://registry.npmjs.org/example"}
        with patch.object(health, "releases", return_value=release):
            report = health.compare(inventory, before, 1)
        self.assertEqual(report["new_releases"][0]["new_versions"], ["1.0.1","1.1.0","2.0.0-rc.1"])
        self.assertEqual(report["entries"][0]["status"], "outdated")

    def test_registry_failure_is_unverified_and_retains_original_cursor(self):
        previous = {"versions":["1.0.0"],"latest_stable":"1.0.0"}
        inventory = {"entries":[{"ecosystem":"cargo","name":"example","current":"1.0.0"}],"coverage_gaps":[]}
        with patch.object(health, "releases", side_effect=ValueError("untrusted response")):
            report = health.compare(inventory, {"releases":{"cargo:example":previous}}, 1)
        self.assertEqual(report["releases"]["cargo:example"], previous)
        self.assertEqual(report["entries"][0]["status"], "unverified")
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["registry_errors"]), 1)

    def test_new_component_is_enrolled_without_fabricating_new_release(self):
        inventory = {"entries":[{"ecosystem":"npm","name":"new","current":"^1"}],"coverage_gaps":[]}
        with patch.object(health, "releases", return_value={"versions":["1.0.0"],"latest_stable":"1.0.0","source":"https://registry.npmjs.org/new"}):
            report = health.compare(inventory, {"releases":{"npm:old":{}}}, 1)
        self.assertEqual(report["new_releases"], [])
        self.assertEqual(report["newly_tracked"][0]["component"], "npm:new")
        self.assertEqual(report["entries"][0]["status"], "latest-comparison-needs-resolution")

    def test_manifest_and_transitive_lock_keep_different_versions(self):
        temporary_root = ROOT / "_build"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix="dependency-fixture-") as folder:
            root = Path(folder)
            (root / "package.json").write_text(json.dumps({"dependencies":{"example":"^2","local":"file:../local"}}))
            (root / "package-lock.json").write_text(json.dumps({"packages":{"node_modules/example":{"version":"2.0.0"},"node_modules/nested/node_modules/example":{"version":"1.9.0"}}}))
            with patch.object(health.subprocess, "check_output", side_effect=["a"*40, b"package.json\0package-lock.json\0"]):
                report = health.inventory({"repositories":[{"path":folder,"label":"fixture"}]})
            self.assertEqual({e["current"] for e in report["entries"]}, {"^2","2.0.0","1.9.0"})
            self.assertTrue(all(e["name"] == "example" for e in report["entries"]))

    def test_unsupported_format_and_broken_repository_never_count_as_complete(self):
        temporary_root = ROOT / "_build"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root, prefix="dependency-fixture-") as folder:
            (Path(folder) / "go.mod").write_text("module example\n")
            with patch.object(health.subprocess, "check_output", side_effect=["a"*40,b"go.mod\0"]):
                report = health.inventory({"repositories":[{"path":folder,"label":"fixture"}]})
            self.assertEqual(len(report["coverage_gaps"]), 1)
        with patch.object(health.subprocess, "check_output", side_effect=OSError("unavailable")):
            report = health.inventory({"repositories":[{"path":"/missing","label":"fixture"}]})
        self.assertEqual(len(report["coverage_gaps"]), 1)

    def test_unchanged_release_list_is_quiet_but_coverage_errors_are_not_hidden(self):
        metadata = {"versions":["1.0.0"],"latest_stable":"1.0.0","source":"https://registry.npmjs.org/example"}
        inventory = {"entries":[{"ecosystem":"npm","name":"example","current":"1.0.0"}],"coverage_gaps":[{"source":"native","reason":"unresolved"}]}
        with patch.object(health,"releases",return_value=metadata):
            report = health.compare(inventory,{"releases":{"npm:example":metadata}},1)
        self.assertEqual(report["new_releases"], [])
        self.assertEqual(report["entries"][0]["status"], "current-or-newer")
        self.assertFalse(report["complete"])

    def test_source_failure_retains_absent_cursor_and_later_intervening_release(self):
        previous = {"versions":["1.0.0"],"latest_stable":"1.0.0","source":"https://registry.npmjs.org/example"}
        missing = {"entries":[],"coverage_gaps":[{"source":"repo","reason":"Repository unavailable"}]}
        with patch.object(health, "releases") as fetch:
            failed = health.compare(missing, {"releases":{"npm:example":previous}}, 1)
        fetch.assert_not_called()
        self.assertEqual(failed["releases"]["npm:example"], previous)
        self.assertFalse(failed["complete"])
        restored = {"entries":[{"ecosystem":"npm","name":"example","current":"1.0.0","kind":"resolved-transitive"}],
                    "coverage_gaps":[]}
        current = {**previous,"versions":["1.0.0","1.1.0"],"latest_stable":"1.1.0"}
        with patch.object(health, "releases", return_value=current):
            recovered = health.compare(restored, failed, 1)
        self.assertEqual(recovered["newly_tracked"], [])
        self.assertEqual(recovered["new_releases"][0]["new_versions"], ["1.1.0"])

    def test_release_contraction_cannot_make_reappearing_old_version_new(self):
        previous = {"versions":["1.0.0","2.0.0-rc.1"],"latest_stable":"1.0.0",
                    "source":"https://registry.npmjs.org/example"}
        def inventory():
            return {"entries":[{"ecosystem":"npm","name":"example","current":"1.0.0","kind":"resolved-transitive"}],
                    "coverage_gaps":[]}
        with patch.object(health, "releases", return_value={**previous,"versions":["1.0.0"]}):
            contracted = health.compare(inventory(), {"releases":{"npm:example":previous}}, 1)
        self.assertEqual(contracted["releases"]["npm:example"]["versions"], previous["versions"])
        with patch.object(health, "releases", return_value=previous):
            reappeared = health.compare(inventory(), contracted, 1)
        self.assertEqual(reappeared["new_releases"], [])

    def test_cargo_declared_constraint_is_not_an_installed_version(self):
        inventory = {"entries":[
            {"ecosystem":"cargo","name":"example","current":"1","kind":"dependencies"},
            {"ecosystem":"cargo","name":"example","current":"1.9.0","kind":"workspace"},
            {"ecosystem":"cargo","name":"example","current":"1.9.0","kind":"resolved-transitive"},
        ],"coverage_gaps":[]}
        with patch.object(health, "releases", return_value={"versions":["1.9.0"],"latest_stable":"1.9.0",
                                                          "source":"https://index.crates.io/ex/am/example"}):
            report = health.compare(inventory, {}, 1)
        self.assertEqual([e["status"] for e in report["entries"]],
                         ["latest-comparison-needs-resolution","latest-comparison-needs-resolution","current-or-newer"])
        self.assertEqual(report["unresolved_entries"], 2)
        self.assertFalse(report["complete"])

    def test_npm_partial_declaration_stays_unresolved_while_exact_pin_is_compared(self):
        inventory = {"entries":[
            {"ecosystem":"npm","name":"example","current":"1.2","kind":"dependencies"},
            {"ecosystem":"npm","name":"example","current":"1.2.9","kind":"dependencies"},
            {"ecosystem":"npm","name":"example","current":"1.2.9","kind":"resolved-transitive"},
        ],"coverage_gaps":[]}
        with patch.object(health, "releases", return_value={"versions":["1.2.9"],"latest_stable":"1.2.9",
                                                          "source":"https://registry.npmjs.org/example"}):
            report = health.compare(inventory, {}, 1)
        self.assertEqual([e["status"] for e in report["entries"]],
                         ["latest-comparison-needs-resolution","current-or-newer","current-or-newer"])
        self.assertEqual(report["unresolved_entries"], 1)
        self.assertFalse(report["complete"])


if __name__ == "__main__":
    unittest.main()
