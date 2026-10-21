"""Secretless producer contracts. Synthetic ELF text is not actual native acceptance."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_media_evidence", ROOT / "scripts/native-media-hosted/evidence.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class NativeMediaProducer(unittest.TestCase):
    def test_all_recipe_inputs_match(self):
        lines = (ROOT / "scripts/native-media-hosted/inputs.sha256").read_text().splitlines()
        names = set()
        for line in lines:
            expected, name = line.split("  ", 1)
            self.assertNotIn(name, names)
            names.add(name)
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(len(names), 18)
        self.assertIn('scripts/native-media-hosted/android/buildscripts/include/download-libunibreak.sh', names)
        self.assertIn('scripts/native-media-hosted/android/buildscripts/scripts/ffmpeg.sh', names)

    def test_android_python_closure_is_pinned_and_isolated(self):
        workflow = (ROOT / ".github/workflows/native-media-candidates.yml").read_text()
        android = workflow.split("\n  android:\n", 1)[1]
        tools = android.split("- name: Prepare hosted build tools", 1)[1].split(
            "- name: Build unpromoted Android candidate", 1)[0]
        self.assertIn('/usr/bin/python3.12 -I -m venv "$tools"', tools)
        self.assertNotIn("--system-site-packages", tools)
        self.assertIn('pip --isolated --disable-pip-version-check install --require-hashes --no-index --no-deps --only-binary=:all:', tools)
        import re
        pins = re.findall(r"https://files\.pythonhosted\.org/packages/[^'\s]+", tools)
        self.assertEqual(len(pins), 9)
        expected = {
            "meson-1.12.1-py3-none-any.whl": "930bc7542cbd9f57009e182fd014eba48cf1a0180a7b9006d2d32d8f168d8b02",
            "jinja2-3.1.6-py3-none-any.whl": "85ece4451f492d0c13c5dd7c13a64681a86afae63a5f347908daf103ce6d2f67",
            "markupsafe-3.0.4-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl": "8e124f974786f831d6043728e38296969d3579db8896fe004682f5758e613581",
            "jsonschema-4.26.0-py3-none-any.whl": "d489f15263b8d200f8387e64b4c3a75f06629559fb73deb8fdfb525f2dab50ce",
            "attrs-26.1.0-py3-none-any.whl": "c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309",
            "jsonschema_specifications-2025.9.1-py3-none-any.whl": "98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe",
            "referencing-0.37.0-py3-none-any.whl": "381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231",
            "rpds_py-2026.9.1-cp312-cp312-manylinux_2_17_x86_64.manylinux2014_x86_64.whl": "addeda51556dac7c1a2f14cda62db8b621cd12afba3091d03a96c72932387eab",
            "typing_extensions-4.16.0-py3-none-any.whl": "481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8",
        }
        self.assertEqual({url.rsplit("/", 1)[1].split("#sha256=")[0]:
                          url.rsplit("#sha256=", 1)[1] for url in pins}, expected)
        manifest_hash = hashlib.sha256((ROOT / "scripts/native-media-hosted/inputs.sha256").read_bytes()).hexdigest()
        self.assertEqual(workflow.count(manifest_hash), 2, "both lanes verify the exact reviewed input manifest")
        build = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        preflight = build.split("<<'PYTHON_TOOLCHAIN'\n", 1)[1].split("\nPYTHON_TOOLCHAIN", 1)[0]
        self.assertLess(build.index("<<'PYTHON_TOOLCHAIN'"), build.index("bash -e include/download-deps.sh"))
        self.assertIn("unset PYTHONPATH PYTHONHOME", build)
        self.assertIn("export PYTHONNOUSERSITE=1", build)
        self.assertIn('export PYTHON="$(command -v python3)"', build)
        self.assertIn('"$PYTHON" -I - ', build)
        for fence in ['Path(sys.prefix).resolve() != tools', 'sys.prefix == sys.base_prefix',
                      'sys.version_info[:2] != (3, 12)', 'site.ENABLE_USER_SITE is not False',
                      'config.get("include-system-site-packages") != "false"',
                      'metadata.version != expected_version', 'not origin.is_relative_to(tools)',
                      'not Path(metadata.locate_file("")).resolve().is_relative_to(tools)']:
            self.assertIn(fence, preflight)
        for version in ["1.12.1", "3.1.6", "3.0.4", "4.26.0", "26.1.0",
                        "2025.9.1", "0.37.0", "2026.9.1", "4.16.0"]:
            self.assertIn('"' + version + '"', preflight)
        self.assertIn('jinja_module.Environment().from_string', preflight)
        self.assertIn('schema_module.validate', preflight)
        self.assertIn('> "$out/python-toolchain.json"', build)

    def fixture(self, out, *, missing=None, bad_abi=False, bad_tls=False):
        native_patch = (ROOT / "scripts/native-media-hosted/android/buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch").read_text()
        header = "\n".join(line[1:] for line in native_patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        (out / "tls_mbedtls_vortx_distrust.h").write_text(header)
        import re
        keys = b"".join(bytes(int(x, 16) for x in re.findall(r"0x([0-9a-f]{2})", line))
                        for line in header.splitlines() if "}, /* EM" in line)
        required = {"libmpv.so", "libplayer.so", "libc++_shared.so", "libavcodec.so", "libavformat.so",
                    "libavutil.so", "libavfilter.so", "libavdevice.so", "libswresample.so", "libswscale.so"}
        with zipfile.ZipFile(out / "libmpv-release-candidate.aar", "w") as archive:
            for abi in ("armeabi-v7a", "arm64-v8a", "x86_64"):
                for name in sorted(required):
                    entry = f"jni/{abi}/{name}"
                    if entry == missing:
                        continue
                    data = b"fixture:" + abi.encode() + b"\0"
                    if name == "libavformat.so":
                        data += b"--enable-mbedtls --enable-gpl --enable-version3\0"
                        data += keys[:-1] if bad_tls else keys
                    archive.writestr(entry, data)
            if bad_abi:
                archive.writestr("jni/mips/libmpv.so", b"unadmitted")

    @staticmethod
    def readelf(*args):
        data = Path(args[-1]).read_bytes()
        elf_class, machine = ("ELF32", "ARM") if b"armeabi-v7a" in data else (
            ("ELF64", "AArch64") if b"arm64-v8a" in data else ("ELF64", "Advanced Micro Devices X86-64"))
        if "-h" in args:
            return f"Class: {elf_class}\nMachine: {machine}\nType: DYN\nData: little endian\n"
        return ("LOAD 0x0000 0x0000 0x0000 0x1000 0x1000 R E 0x4000\n"
                "GNU_RELRO 0x4000 0x4000 0x4000 0x4000 0x4000 R 0x1\n")

    def run_fixture(self, **options):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            self.fixture(out, **options)
            MODULE.android(out, out, out)
            import json
            result = json.loads((out / "android-artifact-evidence.json").read_text())
            self.assertEqual(len(result["libraries"]), 30)
            self.assertFalse(result["promotionEligible"])
            self.assertFalse(result["runtimeSecurityTested"])
            self.assertFalse(result["appAndSeamBindingChanged"])

    def test_three_abi_receipt_remains_unpromoted(self):
        with patch.object(MODULE, "run", side_effect=self.readelf):
            self.run_fixture()

    def test_missing_library_rejects(self):
        with patch.object(MODULE, "run", side_effect=self.readelf), self.assertRaises(ValueError):
            self.run_fixture(missing="jni/x86_64/libmpv.so")

    def test_extra_abi_rejects(self):
        with patch.object(MODULE, "run", side_effect=self.readelf), self.assertRaises(ValueError):
            self.run_fixture(bad_abi=True)

    def test_incomplete_distrust_payload_rejects(self):
        with patch.object(MODULE, "run", side_effect=self.readelf), self.assertRaises(ValueError):
            self.run_fixture(bad_tls=True)

    def test_wrong_elf_identity_rejects(self):
        def wrong(*args):
            return self.readelf(*args).replace("AArch64", "RISC-V")
        with patch.object(MODULE, "run", side_effect=wrong), self.assertRaises(ValueError):
            self.run_fixture()

    def test_64_bit_page_alignment_rejects(self):
        def unaligned(*args):
            return self.readelf(*args).replace("E 0x4000", "E 0x1000")
        with patch.object(MODULE, "run", side_effect=unaligned), self.assertRaises(ValueError):
            self.run_fixture()


if __name__ == "__main__":
    unittest.main()
