"""Secretless producer contracts. Synthetic ELF text is not actual native acceptance."""
import hashlib
import importlib.util
import io
import json
from contextlib import redirect_stdout
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("native_media_evidence", ROOT / "scripts/native-media-hosted/evidence.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
ACQUIRE_SPEC = importlib.util.spec_from_file_location("android_mpv_acquisition", ROOT / "scripts/acquire-android-mpv-component.py")
ACQUIRE = importlib.util.module_from_spec(ACQUIRE_SPEC)
ACQUIRE_SPEC.loader.exec_module(ACQUIRE)


class NativeMediaProducer(unittest.TestCase):
    def test_all_recipe_inputs_match(self):
        lines = (ROOT / "scripts/native-media-hosted/inputs.sha256").read_text().splitlines()
        names = set()
        for line in lines:
            expected, name = line.split("  ", 1)
            self.assertNotIn(name, names)
            names.add(name)
            self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(len(names), 36)
        self.assertEqual(names, {
            'scripts/assemble-mpvkit-dvfel.sh',
            'scripts/build-mpvkit-dvfel.sh',
            'scripts/ffmpeg-response-witness.patch',
            'scripts/ffmpeg-response-generation.patch',
            'scripts/mpv-completed-response-observation.patch',
            'scripts/ffmpeg-sent-request-evidence.patch',
            'scripts/mpv-sent-request-evidence.patch',
            'scripts/mpv-bounded-prefix-request.patch',
            'scripts/ffmpeg-original-transport-evidence.patch',
            'scripts/mpv-original-transport-evidence.patch',
            'scripts/ffmpeg-finite-reference-reader.patch',
            'scripts/mpv-finite-reference-reader.patch',
            'scripts/mpv-coreaudio-hotplug-lifecycle.patch',
            'scripts/mpv-http-seek-interrupt.patch',
            'scripts/mpv-moltenvk-resize.patch',
            'scripts/mpv-original-local-file-witness.patch',
            'scripts/mpv-original-http-reference-recipe.patch',
            'scripts/mpv-original-http-cancel-lifetime.patch',
            'scripts/mpv-owned-subtitle-constructor.patch',
            'scripts/mpv-owned-subtitle-publication.patch',
            'scripts/mpv-upstream-audio-layout-bounds.patch',
            'scripts/mpv-upstream-edition-title-lifetime.patch',
            'scripts/mpv-upstream-gpu-next-seek-reset.patch',
            'scripts/mpvkit-dvfel.patch',
            'scripts/native-media-hosted/android-assembly.patch',
            'scripts/native-media-hosted/android/buildscripts/include/depinfo.sh',
            'scripts/native-media-hosted/android/buildscripts/include/download-deps.sh',
            'scripts/native-media-hosted/android/buildscripts/include/download-lua.sh',
            'scripts/native-media-hosted/android/buildscripts/include/download-libunibreak.sh',
            'scripts/native-media-hosted/android/buildscripts/patch.sh',
            'scripts/native-media-hosted/android/buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch',
            'scripts/native-media-hosted/android/buildscripts/scripts/ffmpeg.sh',
            'scripts/native-media-hosted/build.sh',
            'scripts/native-media-hosted/evidence.py',
            'scripts/verify-mpvkit-dvfel-apple-tls.sh',
            'scripts/verify-mpvkit-dvfel-artifacts.sh',
        })
        self.assertIn('scripts/native-media-hosted/android/buildscripts/include/download-libunibreak.sh', names)
        self.assertIn('scripts/native-media-hosted/android/buildscripts/scripts/ffmpeg.sh', names)
        self.assertIn('scripts/mpv-upstream-audio-layout-bounds.patch', names)
        self.assertIn('scripts/mpv-upstream-gpu-next-seek-reset.patch', names)
        self.assertIn('scripts/mpv-original-local-file-witness.patch', names)
        self.assertIn('scripts/mpv-upstream-edition-title-lifetime.patch', names)
        self.assertIn('scripts/mpv-owned-subtitle-constructor.patch', names)
        self.assertIn('scripts/mpv-owned-subtitle-publication.patch', names)
        self.assertIn('scripts/mpv-original-http-reference-recipe.patch', names)
        self.assertIn('scripts/mpv-original-http-cancel-lifetime.patch', names)

    def test_android_preserves_selected_custom_source_family(self):
        import re
        apple = (ROOT / "scripts/mpvkit-dvfel.patch").read_text()
        android = (ROOT / "scripts/native-media-hosted/android/buildscripts/include/depinfo.sh").read_text()
        for android_key, apple_key in [("v_mpv_sha", "mpvPinnedSHA"),
                                       ("v_libplacebo_sha", "placeboPinnedSHA"),
                                       ("v_ffmpeg_sha", "ffmpegPinnedSHA")]:
            actual = re.search(r"^" + android_key + r"=([0-9a-f]{40})$", android, re.M).group(1)
            expected = re.search(r"static let " + apple_key + r' = "([0-9a-f]{40})"', apple).group(1)
            self.assertEqual(actual, expected, android_key)
        self.assertIn("v_ffmpeg=9.0.2\n", android)
        self.assertNotIn("v_mpv=0.41.0", android)
        self.assertNotIn("v_libplacebo=7.360.1", android)
        download = (ROOT / "scripts/native-media-hosted/android/buildscripts/include/download-deps.sh").read_text()
        self.assertIn('fetch_commit mpv https://github.com/mpv-player/mpv.git "$v_mpv_sha"', download)
        self.assertIn('fetch_commit libplacebo https://code.videolan.org/videolan/libplacebo.git "$v_libplacebo_sha"', download)
        self.assertIn('[ "$(git -C "$dir" rev-parse HEAD)" = "$sha" ]', download)

    def test_both_candidate_recipes_apply_the_same_backports(self):
        apple = (ROOT / "scripts/build-mpvkit-dvfel.sh").read_text()
        android = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        for source, apple_number, android_number in [
                ("mpv-http-seek-interrupt.patch", "0006-http-seek-interrupt.patch", "0001-http-seek-interrupt.patch"),
                ("mpv-upstream-audio-layout-bounds.patch", "0007-upstream-audio-layout-bounds.patch", "0002-upstream-audio-layout-bounds.patch"),
                ("mpv-upstream-gpu-next-seek-reset.patch", "0008-upstream-gpu-next-seek-reset.patch", "0003-upstream-gpu-next-seek-reset.patch"),
                ("mpv-original-local-file-witness.patch", "0009-original-local-file-witness.patch", "0004-original-local-file-witness.patch"),
                ("mpv-upstream-edition-title-lifetime.patch", "0010-upstream-edition-title-lifetime.patch", "0005-upstream-edition-title-lifetime.patch"),
                ("mpv-owned-subtitle-constructor.patch", "0011-owned-subtitle-constructor.patch", "0006-owned-subtitle-constructor.patch"),
                ("mpv-owned-subtitle-publication.patch", "0012-owned-subtitle-publication.patch", "0007-owned-subtitle-publication.patch"),
                ("mpv-original-http-reference-recipe.patch", "0013-original-http-reference-recipe.patch", "0008-original-http-reference-recipe.patch"),
                ("mpv-original-http-cancel-lifetime.patch", "0014-original-http-cancel-lifetime.patch", "0009-original-http-cancel-lifetime.patch"),
                ("mpv-completed-response-observation.patch", "0015-completed-response-observation.patch", "0010-completed-response-observation.patch"),
                ("mpv-sent-request-evidence.patch", "0016-sent-request-evidence.patch", "0011-sent-request-evidence.patch"),
                ("mpv-bounded-prefix-request.patch", "0017-bounded-prefix-request.patch", "0012-bounded-prefix-request.patch"),
                ("mpv-original-transport-evidence.patch", "0018-original-transport-evidence.patch", "0013-original-transport-evidence.patch"),
                ("mpv-finite-reference-reader.patch", "0019-finite-reference-reader.patch", "0014-finite-reference-reader.patch")]:
            if source == "mpv-sent-request-evidence.patch":
                self.assertIn('SENT_MPV_PATCH="$REPO/scripts/' + source + '"', apple)
                self.assertIn('copy_exact "$SENT_MPV_PATCH" "$GENERATED_SENT_MPV_PATCH"', apple)
            elif source == "mpv-bounded-prefix-request.patch":
                self.assertIn('PREFIX_PATCH="$REPO/scripts/' + source + '"', apple)
                self.assertIn('copy_exact "$PREFIX_PATCH" "$GENERATED_PREFIX_PATCH"', apple)
            elif source == "mpv-original-transport-evidence.patch":
                self.assertIn('TRANSPORT_MPV_PATCH="$REPO/scripts/' + source + '"', apple)
                self.assertIn('copy_exact "$TRANSPORT_MPV_PATCH" "$GENERATED_TRANSPORT_MPV_PATCH"', apple)
            elif source == "mpv-finite-reference-reader.patch":
                self.assertIn('READER_MPV_PATCH="$REPO/scripts/' + source + '"', apple)
                self.assertIn('copy_exact "$READER_MPV_PATCH" "$GENERATED_READER_MPV_PATCH"', apple)
            else:
                self.assertIn('copy_exact "$REPO/scripts/' + source + '"', apple)
            self.assertIn('"$MPVKIT_DIR/Sources/BuildScripts/patch/libmpv/' + apple_number + '"', apple)
            self.assertIn('cp "$root/scripts/' + source + '" "$upstream/buildscripts/patches/mpv/' + android_number + '"', android)
        self.assertLess(android.index("0006-owned-subtitle-constructor.patch"),
                        android.index("bash patch.sh"))
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-upstream-edition-title-lifetime.patch"'),
                        apple.index('copy_exact "$REPO/scripts/mpv-owned-subtitle-constructor.patch"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-upstream-edition-title-lifetime.patch"'),
                        android.index('cp "$root/scripts/mpv-owned-subtitle-constructor.patch"'))
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-owned-subtitle-constructor.patch"'),
                        apple.index('copy_exact "$REPO/scripts/mpv-owned-subtitle-publication.patch"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-owned-subtitle-constructor.patch"'),
                        android.index('cp "$root/scripts/mpv-owned-subtitle-publication.patch"'))
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-owned-subtitle-publication.patch"'),
                        apple.index('copy_exact "$REPO/scripts/mpv-original-http-reference-recipe.patch"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-owned-subtitle-publication.patch"'),
                        android.index('cp "$root/scripts/mpv-original-http-reference-recipe.patch"'))
        self.assertLess(android.index("0007-owned-subtitle-publication.patch"),
                        android.index("bash patch.sh"))
        self.assertLess(android.index("0008-original-http-reference-recipe.patch"),
                        android.index("bash patch.sh"))
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-original-http-reference-recipe.patch"'),
                        apple.index('copy_exact "$REPO/scripts/mpv-original-http-cancel-lifetime.patch"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-original-http-reference-recipe.patch"'),
                        android.index('cp "$root/scripts/mpv-original-http-cancel-lifetime.patch"'))
        self.assertLess(android.index("0009-original-http-cancel-lifetime.patch"),
                        android.index("bash patch.sh"))
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-original-http-cancel-lifetime.patch"'),
                        apple.index('copy_exact "$REPO/scripts/mpv-completed-response-observation.patch"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-original-http-cancel-lifetime.patch"'),
                        android.index('cp "$root/scripts/mpv-completed-response-observation.patch"'))
        self.assertLess(android.index("0010-completed-response-observation.patch"),
                        android.index("bash patch.sh"))
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-completed-response-observation.patch"'),
                        apple.index('copy_exact "$SENT_MPV_PATCH" "$GENERATED_SENT_MPV_PATCH"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-completed-response-observation.patch"'),
                        android.index('cp "$root/scripts/mpv-sent-request-evidence.patch"'))
        self.assertLess(android.index("0011-sent-request-evidence.patch"), android.index("bash patch.sh"))

    def test_response_witness_is_the_same_exact_ffmpeg_producer_on_both_lanes(self):
        relative = "scripts/ffmpeg-response-witness.patch"
        patch_bytes = (ROOT / relative).read_bytes()
        self.assertEqual(hashlib.sha256(patch_bytes).hexdigest(),
                         "6a2bf6d0feff31280470ac46e9c56b2e0a4522513959784dcb04aaf196334b9b")
        patch_text = patch_bytes.decode()
        self.assertEqual([line for line in patch_text.splitlines() if line.startswith("--- ")],
                         ["--- a/libavformat/http.c"])
        self.assertEqual([line for line in patch_text.splitlines() if line.startswith("+++ ")],
                         ["+++ b/libavformat/http.c"])
        # Completed read-only metadata only, not a worker/current-response reader.
        for option in ("response_etag", "response_location"):
            self.assertIn('AV_OPT_FLAG_EXPORT | AV_OPT_FLAG_READONLY', next(
                line for line in patch_text.splitlines()
                if line.startswith('+    { "' + option + '"')))
        apple = (ROOT / "scripts/build-mpvkit-dvfel.sh").read_text()
        android = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        self.assertIn('RESPONSE_PATCH="$REPO/scripts/ffmpeg-response-witness.patch"', apple)
        self.assertIn('copy_exact "$RESPONSE_PATCH" "$GENERATED_RESPONSE_PATCH"', apple)
        self.assertIn('/patch/FFmpeg/0001-response-witness.patch"', apple)
        self.assertLess(apple.index('copy_exact "$RESPONSE_PATCH" "$GENERATED_RESPONSE_PATCH"'),
                        apple.index('swift run --build-path'))
        self.assertLess(apple.index('build enable-gpl platform=ios,tvos,macos'),
                        apple.index('FFMPEG_RESPONSE_SOURCE='))
        copy = 'cp "$root/scripts/ffmpeg-response-witness.patch" "$upstream/buildscripts/patches/ffmpeg/0002-response-witness.patch"'
        self.assertIn(copy, android)
        self.assertLess(android.index('buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch; do'),
                        android.index(copy))
        self.assertLess(android.index(copy), android.index("bash patch.sh"))
        source_check = android.index("deps/ffmpeg/libavformat/http.c | shasum")
        self.assertLess(android.index("bash patch.sh"), source_check)
        self.assertLess(source_check, android.index('for arch in armv7l arm64 x86_64;'))
        for recipe in (apple, android):
            self.assertIn("946fcce07b6dcd0331c8cc609192aeff5e1924f8", recipe)
            self.assertIn("d4ec29f40e9302f6bb1abedcbd0c5adf87c20b11236a8ad8aff42fea46616802", recipe)
        # The preexisting lexical patcher remains the dependency sequence authority.
        patcher = (ROOT / "scripts/native-media-hosted/android/buildscripts/patch.sh").read_text()
        self.assertIn('PATCHES=(patches/*)', patcher)
        self.assertIn('patches=("$dep_path"/*)', patcher)
        self.assertIn('git apply --reverse --check', patcher)

    def test_completed_response_observation_preserves_source_and_sequence_fences(self):
        generation = (ROOT / "scripts/ffmpeg-response-generation.patch").read_bytes()
        observation = (ROOT / "scripts/mpv-completed-response-observation.patch").read_bytes()
        self.assertEqual(hashlib.sha256(generation).hexdigest(),
                         "ddfb68b5d7ed3779cfa69a75a28e9d4b17bd4f28529cccf26a005e790da03098")
        self.assertEqual(hashlib.sha256(observation).hexdigest(),
                         "c038924be4c9b0ef187e1e9336d201b866dfa6f13457817c6e8c2d484e882e8e")
        generation_text, observation_text = generation.decode(), observation.decode()
        self.assertEqual([line for line in generation_text.splitlines() if line.startswith("+++ ")],
                         ["+++ b/libavformat/http.c"])
        self.assertEqual([line for line in observation_text.splitlines() if line.startswith("+++ ")],
                         ["+++ b/stream/reference_source.h", "+++ b/stream/stream_lavf.c"])
        self.assertIn('AV_OPT_TYPE_INT64', next(line for line in generation_text.splitlines()
                                              if line.startswith('+    { "response_generation"')))
        self.assertIn('AV_OPT_FLAG_EXPORT | AV_OPT_FLAG_READONLY', next(
            line for line in generation_text.splitlines() if line.startswith('+    { "response_generation"')))
        self.assertIn('#if HAVE_PTHREADS || HAVE_W32THREADS || HAVE_OS2THREADS', generation_text)
        self.assertIn('static AVMutex response_generation_lock = AV_MUTEX_INITIALIZER;', generation_text)
        self.assertIn('last_response_generation < INT64_MAX', generation_text)
        self.assertIn('s->response_generation = 0;', generation_text)
        self.assertIn('INITIAL_REQUEST_ONLY', observation_text)
        for name in ('stream_reference_source_observe_response',
                     'stream_reference_source_revalidate_observation',
                     'stream_reference_observation_cancelled',
                     'stream_reference_observation_release'):
            self.assertIn(name, observation_text)
        apple = (ROOT / "scripts/build-mpvkit-dvfel.sh").read_text()
        android = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        self.assertIn('copy_exact "$RESPONSE_GENERATION_PATCH" "$GENERATED_RESPONSE_GENERATION_PATCH"', apple)
        self.assertIn('/patch/FFmpeg/0002-response-generation.patch"', apple)
        self.assertLess(apple.index('copy_exact "$RESPONSE_PATCH" "$GENERATED_RESPONSE_PATCH"'),
                        apple.index('copy_exact "$RESPONSE_GENERATION_PATCH" "$GENERATED_RESPONSE_GENERATION_PATCH"'))
        self.assertLess(apple.index('copy_exact "$RESPONSE_GENERATION_PATCH" "$GENERATED_RESPONSE_GENERATION_PATCH"'),
                        apple.index('swift run --build-path'))
        copy = 'cp "$root/scripts/ffmpeg-response-generation.patch" "$upstream/buildscripts/patches/ffmpeg/0003-response-generation.patch"'
        self.assertIn(copy, android)
        self.assertLess(android.index('cp "$root/scripts/ffmpeg-response-witness.patch"'), android.index(copy))
        self.assertLess(android.index(copy), android.index("bash patch.sh"))
        for recipe in (apple, android):
            for afterimage in ("d4ec29f40e9302f6bb1abedcbd0c5adf87c20b11236a8ad8aff42fea46616802",
                               "b8c78d8239af8c968c53ca83f601f91e521090d6d2d7522101fb68b332074940",
                               "dacfde2ba2aacfb07bd959f23e7e6e367125dbde47d4a31b36c21c24eba0c38b"):
                self.assertIn(afterimage, recipe)
        # Source contracts only. This does not exercise an HTTP owner or reader.

    def test_sent_request_private_header_and_native_only_link_contract(self):
        ffmpeg = (ROOT / "scripts/ffmpeg-sent-request-evidence.patch").read_bytes()
        mpv = (ROOT / "scripts/mpv-sent-request-evidence.patch").read_bytes()
        self.assertEqual(hashlib.sha256(ffmpeg).hexdigest(), "085b66cb5623ed8087481d3217b65da587b2970cd345a45896e7e87823dec9c9")
        self.assertEqual(hashlib.sha256(mpv).hexdigest(), "e3840084725be1f4948fb90c6fec87c35d55665f8310854713ebff496e1358bf")
        self.assertEqual([line for line in ffmpeg.decode().splitlines() if line.startswith("+++ ")],
                         ["+++ b/libavformat/http.c", "+++ b/libavformat/vortx_http_request.h"])
        self.assertEqual([line for line in mpv.decode().splitlines() if line.startswith("+++ ")],
                         ["+++ b/stream/reference_source.h", "+++ b/stream/stream_lavf.c",
                          "+++ b/stream/vortx_http_request.h", "+++ b/meson.build"])
        self.assertNotIn('+            if (!av_isalnum', ffmpeg.decode())
        self.assertIn("*c >= 'A' && *c <= 'Z'", ffmpeg.decode())
        self.assertIn("vortx_sent_request_link_roots = []", mpv.decode())
        self.assertIn("if host_machine.system() == 'android'", mpv.decode())
        for symbol in ("stream_reference_source_observe_sent_request",
                       "stream_reference_source_revalidate_sent_request",
                       "stream_reference_sent_request_cancelled", "stream_reference_sent_request_release",
                       "stream_reference_sent_request_values_clear", "stream_reference_sent_request_copy_values"):
            self.assertIn("'-Wl,--undefined=" + symbol + "'", mpv.decode())
        # Internal functions remain hidden. These link roots do not export APIs or mount a reader.
        self.assertNotIn("--export-dynamic", mpv.decode())
        self.assertNotIn("--dynamic-list", mpv.decode())
        apple = (ROOT / "scripts/build-mpvkit-dvfel.sh").read_text()
        android = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        copy = 'cp "$root/scripts/ffmpeg-sent-request-evidence.patch" "$upstream/buildscripts/patches/ffmpeg/0004-sent-request-evidence.patch"'
        self.assertIn(copy, android)
        self.assertLess(android.index('cp "$root/scripts/ffmpeg-response-generation.patch"'), android.index(copy))
        self.assertLess(android.index(copy), android.index("bash patch.sh"))
        self.assertIn('copy_exact "$SENT_FFMPEG_PATCH" "$GENERATED_SENT_FFMPEG_PATCH"', apple)
        self.assertIn('copy_exact "$SENT_MPV_PATCH" "$GENERATED_SENT_MPV_PATCH"', apple)
        self.assertIn('Sources/BuildScripts/patch/libmpv/0016-sent-request-evidence.patch', apple)
        self.assertLess(apple.index('copy_exact "$REPO/scripts/mpv-completed-response-observation.patch"'),
                        apple.index('copy_exact "$SENT_MPV_PATCH" "$GENERATED_SENT_MPV_PATCH"'))
        for lane in ("apple", "android"):
            roots = MODULE.CURRENT_SOURCE_ROOTS[lane]
            ffroot = next(name for name, pin in roots.items() if pin == MODULE.CURRENT_NATIVE_PINS["ffmpeg"])
            mproot = next(name for name, pin in roots.items() if pin == MODULE.CURRENT_NATIVE_PINS["mpv"])
            ffheader = MODULE.CURRENT_UNTRACKED_FILES[lane][ffroot]["libavformat/vortx_http_request.h"]
            mpheader = MODULE.CURRENT_UNTRACKED_FILES[lane][mproot]["stream/vortx_http_request.h"]
            self.assertEqual(ffheader, "d7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9")
            self.assertEqual(ffheader, mpheader)
        self.assertIn('native-media-hosted/evidence.py" apple-sent-abi "$STAGED_DEST"', apple)
        self.assertIn('evidence.py apple-sent-abi "$out/MPVKit-DVFEL"', android)

    def test_bounded_prefix_material_carrier_is_exact_private_and_ordered(self):
        material = (ROOT / "scripts/mpv-bounded-prefix-request.patch").read_bytes()
        self.assertEqual(hashlib.sha256(material).hexdigest(),
                         "edcfdc7d9d8da7f8a44b19d326a809de844be968cc8061b80744229da801331e")
        text = material.decode()
        self.assertEqual([line for line in text.splitlines() if line.startswith("+++ ")],
                         ["+++ b/stream/reference_source.h", "+++ b/stream/stream_lavf.c",
                          "+++ b/meson.build"])
        symbol = "stream_reference_source_prepare_prefix_request"
        self.assertIn(symbol, MODULE.SENT_MPV_FUNCTIONS)
        self.assertEqual(text.count("+        '-Wl,--undefined=" + symbol + "',"), 1)
        self.assertNotIn("--export-dynamic", text)
        self.assertNotIn("--dynamic-list", text)
        self.assertNotIn("avio_open", text)
        self.assertNotIn("avformat_open_input", text)
        apple = (ROOT / "scripts/build-mpvkit-dvfel.sh").read_text()
        android = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        self.assertLess(apple.index('copy_exact "$SENT_MPV_PATCH" "$GENERATED_SENT_MPV_PATCH"'),
                        apple.index('copy_exact "$PREFIX_PATCH" "$GENERATED_PREFIX_PATCH"'))
        copy = 'cp "$root/scripts/mpv-bounded-prefix-request.patch" "$upstream/buildscripts/patches/mpv/0012-bounded-prefix-request.patch"'
        self.assertLess(android.index('cp "$root/scripts/mpv-sent-request-evidence.patch"'), android.index(copy))
        self.assertLess(android.index(copy), android.index("bash patch.sh"))
        for lane, groups in MODULE.CURRENT_SOURCE_ROOTS.items():
            native = next(name for name, pin in groups.items() if pin == MODULE.CURRENT_NATIVE_PINS["mpv"])
            self.assertEqual(MODULE.CURRENT_UNTRACKED_FILES[lane][native]["stream/reference_source.h"],
                             "dacfde2ba2aacfb07bd959f23e7e6e367125dbde47d4a31b36c21c24eba0c38b")

    def test_transport_carriers_are_exact_ordered_v2_and_keep_private_retention(self):
        ffmpeg = (ROOT / "scripts/ffmpeg-original-transport-evidence.patch").read_bytes()
        mpv = (ROOT / "scripts/mpv-original-transport-evidence.patch").read_bytes()
        self.assertEqual(hashlib.sha256(ffmpeg).hexdigest(),
                         "480302686b9fff135cd0665e7af76d635fc21d83f54ee0096578ddd6bd6d83e5")
        self.assertEqual(hashlib.sha256(mpv).hexdigest(),
                         "dad7f2d20ea14eb4661ef853978be78806e1b01fce67653e3ffec9b57d4a0e82")
        self.assertEqual([line for line in ffmpeg.decode().splitlines() if line.startswith("+++ ")],
                         ["+++ b/libavformat/" + name for name in
                          ("http.c", "url.h", "tls.c", "tls.h", "tls_securetransport.c",
                           "tls_mbedtls.c", "vortx_http_request.h")])
        self.assertEqual([line for line in mpv.decode().splitlines() if line.startswith("+++ ")],
                         ["+++ b/stream/" + name for name in
                          ("reference_source.h", "stream_lavf.c", "vortx_http_request.h")])
        self.assertIn('+    if (!shr->listen && !shr->numerichost)', ffmpeg.decode())
        for patch_text in (ffmpeg.decode(), mpv.decode()):
            for symbol in MODULE.SENT_FFMPEG_FUNCTIONS:
                self.assertIn(symbol, patch_text)
            self.assertNotIn('--export-dynamic', patch_text)
            self.assertNotIn('--dynamic-list', patch_text)
        self.assertIn('stream_reference_sent_request_copy_transport', mpv.decode())
        apple = (ROOT / "scripts/build-mpvkit-dvfel.sh").read_text()
        android = (ROOT / "scripts/native-media-hosted/build.sh").read_text()
        for source, apple_number, android_number in (
                ("ffmpeg-original-transport-evidence.patch", "0004-original-transport-evidence.patch",
                 "0005-original-transport-evidence.patch"),
                ("mpv-original-transport-evidence.patch", "0018-original-transport-evidence.patch",
                 "0013-original-transport-evidence.patch")):
            kind = "FFMPEG" if source.startswith("ffmpeg") else "MPV"
            folder = "ffmpeg" if kind == "FFMPEG" else "mpv"
            copy = 'copy_exact "$TRANSPORT_' + kind + '_PATCH" "$GENERATED_TRANSPORT_' + kind + '_PATCH"'
            self.assertIn(copy, apple)
            self.assertIn(apple_number, apple)
            self.assertLess(apple.index(copy), apple.index('swift run --build-path'))
            android_copy = ('cp "$root/scripts/' + source + '" "$upstream/buildscripts/patches/' +
                            folder + '/' + android_number + '"')
            self.assertIn(android_copy, android)
            self.assertLess(android.index(android_copy), android.index('bash patch.sh'))
        self.assertLess(apple.index('copy_exact "$PREFIX_PATCH" "$GENERATED_PREFIX_PATCH"'),
                        apple.index('copy_exact "$TRANSPORT_MPV_PATCH" "$GENERATED_TRANSPORT_MPV_PATCH"'))
        self.assertLess(android.index('cp "$root/scripts/mpv-bounded-prefix-request.patch"'),
                        android.index('cp "$root/scripts/mpv-original-transport-evidence.patch"'))
        self.assertIn('cold_paths=(dist)', android)
        self.assertIn('cold_paths=(buildscripts/deps buildscripts/prefix libmpv/src/main/jniLibs libmpv/build)', android)
        self.assertIn('[[ ! -e "$upstream/$relative" && ! -L "$upstream/$relative" ]]', android)
        assembly = (ROOT / 'scripts/native-media-hosted/android-assembly.patch').read_text()
        self.assertIn('-Wl,-u,stream_reference_sent_request_copy_transport', assembly)
        self.assertIn('-Dstrip=false', assembly)
        self.assertIn('keepDebugSymbols', assembly)
        self.assertIn('**/libmpv.so', assembly)
        self.assertNotIn('--export-dynamic', assembly)
        self.assertNotIn('--dynamic-list', assembly)

    def test_finite_reference_reader_carriers_preserve_full_source_and_patch_order(self):
        ffmpeg = (ROOT / 'scripts/ffmpeg-finite-reference-reader.patch').read_bytes()
        mpv = (ROOT / 'scripts/mpv-finite-reference-reader.patch').read_bytes()
        self.assertEqual(hashlib.sha256(ffmpeg).hexdigest(),
                         '6927705a432e987d2e13e2d5922756cb6c395d5c060268191cf6c1d94063b2df')
        self.assertEqual(hashlib.sha256(mpv).hexdigest(),
                         'd902f2637c535c92ed135e0cdda659355bcf08f492b1586cdbe537f01f8cb991')
        self.assertEqual([line for line in ffmpeg.decode().splitlines() if line.startswith('+++ ')],
                         ['+++ b/libavformat/' + name for name in
                          ('http.c', 'url.h', 'avio.c', 'tcp.c', 'tls.c',
                           'vortx_reference_reader.inc', 'vortx_reference_pin.h',
                           'vortx_reference_reader_stream.h')])
        self.assertEqual([line for line in mpv.decode().splitlines() if line.startswith('+++ ')],
                         ['+++ b/' + name for name in
                          ('demux/demux.c', 'demux/demux.h', 'demux/demux_lavf.c',
                           'demux/reference_reader.c', 'demux/reference_reader.h',
                           'demux/reference_reader_internal.h', 'stream/stream_lavf.c',
                           'stream/reference_source.h', 'stream/vortx_reference_reader_stream.h',
                           'meson.build', 'player/owned_subtitle.c', 'player/owned_subtitle.h',
                           'player/command.c', 'player/reference_owned.inc', 'sub/owned_text_slot.h',
                           'sub/osd.c', 'sub/dec_sub.c', 'sub/dec_sub.h')])
        for text in (ffmpeg.decode(), mpv.decode()):
            self.assertNotIn('--export-dynamic', text)
            self.assertNotIn('--dynamic-list', text)
        apple = (ROOT / 'scripts/build-mpvkit-dvfel.sh').read_text()
        android = (ROOT / 'scripts/native-media-hosted/build.sh').read_text()
        for kind, folder, apple_number, android_number in (
                ('FFMPEG', 'ffmpeg', '0005', '0006'), ('MPV', 'mpv', '0019', '0014')):
            source = folder + '-finite-reference-reader.patch'
            copy = 'copy_exact "$READER_' + kind + '_PATCH" "$GENERATED_READER_' + kind + '_PATCH"'
            previous = 'copy_exact "$TRANSPORT_' + kind + '_PATCH" "$GENERATED_TRANSPORT_' + kind + '_PATCH"'
            self.assertIn('READER_' + kind + '_PATCH="$REPO/scripts/' + source + '"', apple)
            self.assertIn(apple_number + '-finite-reference-reader.patch', apple)
            self.assertLess(apple.index(previous), apple.index(copy))
            self.assertLess(apple.index(copy), apple.index('swift run --build-path'))
            android_copy = ('cp "$root/scripts/' + source + '" "$upstream/buildscripts/patches/' +
                            folder + '/' + android_number + '-finite-reference-reader.patch"')
            self.assertLess(android.index('cp "$root/scripts/' + folder + '-original-transport-evidence.patch"'),
                            android.index(android_copy))
            self.assertLess(android.index(android_copy), android.index('bash patch.sh'))
        # Source-carrier pairing only; these assertions execute no native/network reader.
        for lane, groups in MODULE.CURRENT_SOURCE_ROOTS.items():
            ffroot = next(name for name, pin in groups.items() if pin == MODULE.CURRENT_NATIVE_PINS['ffmpeg'])
            mproot = next(name for name, pin in groups.items() if pin == MODULE.CURRENT_NATIVE_PINS['mpv'])
            self.assertEqual(MODULE.CURRENT_UNTRACKED_FILES[lane][ffroot]['libavformat/vortx_reference_reader_stream.h'],
                             'adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87')
            self.assertEqual(MODULE.CURRENT_UNTRACKED_FILES[lane][ffroot]['libavformat/vortx_reference_reader_stream.h'],
                             MODULE.CURRENT_UNTRACKED_FILES[lane][mproot]['stream/vortx_reference_reader_stream.h'])

    def test_response_witness_source_evidence_profile_preserves_exact_capture_and_consumer(self):
        spec = importlib.util.spec_from_file_location(
            "current_native_response_packager", ROOT / "scripts/package-native-current-component.py")
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        self.assertEqual(MODULE.CURRENT_SOURCE_PROFILE, consumer.PROFILE)
        self.assertEqual(consumer.PROFILE, "original-http-finite-reference-reader-v1")
        self.assertEqual(MODULE.CURRENT_RECIPE_PATHS, consumer.RECIPE_PATHS)
        self.assertEqual(len(consumer.RECIPE_PATHS), 36)
        manifest = ROOT / "scripts/native-media-hosted/inputs.sha256"
        self.assertEqual(hashlib.sha256(manifest.read_bytes()).hexdigest(), consumer.PROFILE_MANIFEST_SHA256)
        recipe = dict((name, checksum) for checksum, name in
                      (line.split("  ", 1) for line in manifest.read_text().splitlines()))
        self.assertEqual(MODULE.CURRENT_RESPONSE_FILES, consumer.RESPONSE)
        self.assertEqual(MODULE.CURRENT_READER_FFMPEG_CREATED, consumer.READER_FFMPEG_CREATED)
        self.assertEqual(MODULE.CURRENT_READER_MPV_FILES, consumer.READER_MPV)
        self.assertEqual(len(consumer.READER_FFMPEG_CREATED), 3)
        self.assertEqual(len(consumer.READER_MPV), 17)
        self.assertNotIn("meson.build", consumer.READER_MPV)
        meson = {
            "apple": "4c748249732131a2942b66087fa7142a2f9c239274cc272a35ec36221c010205",
            "android": "316e9aca3c4960701eab42e62ca1005379e082e1a21b4788b7581811ca360147"}
        self.assertEqual(MODULE.CURRENT_READER_MPV_MESON, meson)
        self.assertEqual(consumer.READER_MPV_MESON, meson)
        self.assertEqual(MODULE.CURRENT_MBEDTLS_FILES, consumer.MBEDTLS)
        self.assertEqual(consumer.MBEDTLS, {
            "apple": "77d72cb88fc662b984f051335b356abc3e76e97ce39302dde6961ecdbba7a332",
            "android": "7099664b2c15a784db38a0db7956f718862201ef6c02d27779b2fc654b44aeac"})
        for lane, groups in MODULE.CURRENT_SOURCE_ROOTS.items():
            self.assertEqual(len(consumer.READER_MPV | {"meson.build": consumer.READER_MPV_MESON[lane]}), 18)
            for directory, commit in groups.items():
                self.assertEqual(consumer.untracked(lane, {"commit": commit}, recipe),
                                 MODULE.CURRENT_UNTRACKED_FILES[lane][directory])
        for helper in ("buildscripts/include/download-lua.sh", "buildscripts/include/download-libunibreak.sh"):
            self.assertEqual(MODULE.CURRENT_UNTRACKED_FILES["android"]["_native-publisher"][helper],
                             recipe["scripts/native-media-hosted/android/" + helper])
        # These are synthetic source observations, not native tar/binary or HTTP execution.
        old_sources = ("6a34addba8bc8ce1a24b28a3faac3e6fc8b1c5d464be88946e4b0694eaf98a22",
                       "3c9becd663989d20b09805aeb99b525252908398e29b172e92e64f1c769831aa",
                       "db1b96a39966a51edbb10975b1cc67a69a6c744fa4101931470717cd63d44da0",
                       "b4261b2728bba10b77e99b43d2ee937a25392f9e53c15785f188566a8f166c1f",
                       "205a083b57dd105078e3f71a1d0fad1fb6342d023d9fae32a60cbf6227218e74",
                       "faebc27e86d3bf4375ef9c4461526f58caeaf3a6974d776f1a52159e29cdedd2")
        for lane in ("apple", "android"):
            expected = dict(consumer.RESPONSE, **{"libavformat/tls_mbedtls.c": consumer.MBEDTLS[lane]})
            valid = {name: {"kind": "file", "tracked": True, "sha256": checksum}
                     for name, checksum in expected.items()}
            valid.update({name: {"kind": "file", "tracked": False, "sha256": checksum}
                          for name, checksum in consumer.READER_FFMPEG_CREATED.items()})
            self.assertEqual(len(expected), 8)
            self.assertEqual(len(valid), 11)
            for guard in (MODULE.require_current_response_source, consumer.require_response_source):
                guard(valid, lane)
                for path in expected:
                    for field, value in (("tracked", False), ("kind", "symlink"), ("sha256", "0" * 64)):
                        observed = deepcopy(valid)
                        observed[path][field] = value
                        with self.subTest(lane=lane, path=path, field=field), self.assertRaises(ValueError):
                            guard(observed, lane)
                    observed = deepcopy(valid)
                    del observed[path]
                    with self.assertRaises(ValueError):
                        guard(observed, lane)
                for old in old_sources:
                    observed = deepcopy(valid)
                    observed["libavformat/http.c"]["sha256"] = old
                    with self.assertRaises(ValueError):
                        guard(observed, lane)
                for path in consumer.READER_FFMPEG_CREATED:
                    for field, value in (("tracked", True), ("kind", "symlink"), ("sha256", "0" * 64)):
                        observed = deepcopy(valid)
                        observed[path][field] = value
                        with self.subTest(lane=lane, path=path, field=field), self.assertRaises(ValueError):
                            guard(observed, lane)
                    observed = deepcopy(valid)
                    del observed[path]
                    with self.assertRaises(ValueError):
                        guard(observed, lane)
                with self.assertRaises(ValueError):
                    guard(valid, "unknown")
                other = "android" if lane == "apple" else "apple"
                with self.assertRaises(ValueError):
                    guard(valid, other)

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
        reader = out / 'ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf'
        reader.parent.mkdir(parents=True)
        reader.write_bytes(b'synthetic reader identity only; subprocess is mocked')
        required = {"libmpv.so", "libplayer.so", "libc++_shared.so", "libavcodec.so", "libavformat.so",
                    "libavutil.so", "libavfilter.so", "libavdevice.so", "libswresample.so", "libswscale.so"}
        with zipfile.ZipFile(out / "libmpv-release-candidate.aar", "w") as archive:
            for abi in ("armeabi-v7a", "arm64-v8a", "x86_64"):
                for name in sorted(required):
                    entry = f"jni/{abi}/{name}"
                    if entry == missing:
                        continue
                    data = b"fixture:" + abi.encode() + b"/" + name.encode() + b"\0"
                    if name == "libavformat.so":
                        data += b"--enable-mbedtls --enable-gpl --enable-version3\0"
                        data += keys[:-1] if bad_tls else keys
                    archive.writestr(entry, data)
            if bad_abi:
                archive.writestr("jni/mips/libmpv.so", b"unadmitted")

    @staticmethod
    def readelf(*args):
        if '--version' in args:
            return 'LLVM synthetic inspection fixture'
        data = Path(args[-1]).read_bytes()
        if '--dyn-syms' in args:
            return SentRequestABISynthetic.dynsym(
                'defined-provider' if b'libavformat.so' in data else 'undefined-import',
                functions=MODULE.SENT_FFMPEG_FUNCTIONS + MODULE.REFERENCE_FFMPEG_FUNCTIONS)
        if '--symbols' in args:
            return SentRequestABISynthetic.symtab()
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

    def test_android_producer_rejects_missing_finite_reader_provider_or_import(self):
        symbol = MODULE.REFERENCE_FFMPEG_FUNCTIONS[-1]
        for library in (b'libavformat.so', b'libmpv.so'):
            def missing(*args):
                result = self.readelf(*args)
                if '--dyn-syms' in args and library in Path(args[-1]).read_bytes():
                    return result.replace(symbol + '@', symbol + '_missing@')
                return result
            with self.subTest(library=library), patch.object(MODULE, 'run', side_effect=missing), self.assertRaises(ValueError):
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

    # Actual arm64 libavcodec program-header geometry from artifact 12338601965.
    # These are metadata fixtures, not execution of that library or a device-security test.
    @staticmethod
    def isolated_relro_headers():
        return [
            'LOAD 0x000000 0x000000 0x000000 0xb3c840 0xb3c840 R E 0x4000',
            'LOAD 0xb3c840 0xb40840 0xb40840 0x045228 0x0457c0 RW 0x4000',
            'LOAD 0xb81a68 0xb89a68 0xb89a68 0x001020 0x5ae498 RW 0x4000',
            'GNU_RELRO 0xb3c840 0xb40840 0xb40840 0x045228 0x0457c0 R 0x1',
        ]

    def test_isolated_relro_layout_reaches_existing_full_artifact_gate(self):
        def isolated(*args):
            if any(flag in args for flag in ('-h', '--version', '--dyn-syms', '--symbols')):
                return self.readelf(*args)
            return '\n'.join(self.isolated_relro_headers()) + '\n'
        with patch.object(MODULE, 'run', side_effect=isolated):
            self.run_fixture()

    def test_relro_shared_writable_tail_is_not_exempt(self):
        rows = [line.split() for line in self.isolated_relro_headers()]
        rows[1][5] = '0x0467c0'  # containing LOAD extends past RELRO into mutable data
        self.assertFalse(MODULE.relro_16k_safe(rows[-1], rows[:-1]))

    def test_relro_next_load_overlapping_rounded_padding_rejects(self):
        rows = [line.split() for line in self.isolated_relro_headers()]
        rows[2][2] = '0xb87000'  # after RELRO end, but inside rounded protection end 0xb88000
        self.assertFalse(MODULE.relro_16k_safe(rows[-1], rows[:-1]))

    def test_relro_previous_load_overlapping_rounded_start_rejects(self):
        rows = [line.split() for line in self.isolated_relro_headers()]
        rows[0][5] = '0xb40500'  # before RELRO start, but inside rounded protection start 0xb40000
        self.assertFalse(MODULE.relro_16k_safe(rows[-1], rows[:-1]))

    def test_relro_next_load_at_exact_next_page_is_safe(self):
        rows = [line.split() for line in self.isolated_relro_headers()]
        rows[2][2] = '0xb88000'
        self.assertTrue(MODULE.relro_16k_safe(rows[-1], rows[:-1]))

    def test_relro_ambiguous_or_wrong_file_mapping_rejects(self):
        rows = [line.split() for line in self.isolated_relro_headers()]
        self.assertFalse(MODULE.relro_16k_safe(rows[-1], rows[:-1] + [rows[1]]))
        rows[1][1] = '0xb3c841'
        self.assertFalse(MODULE.relro_16k_safe(rows[-1], rows[:-1]))

    def test_isolated_relro_does_not_excuse_bad_load_alignment(self):
        def unaligned(*args):
            if any(flag in args for flag in ('-h', '--version', '--dyn-syms', '--symbols')):
                return self.readelf(*args)
            return '\n'.join(self.isolated_relro_headers()).replace('E 0x4000', 'E 0x1000')
        with patch.object(MODULE, 'run', side_effect=unaligned), self.assertRaises(ValueError):
            self.run_fixture()

    def test_explicit_inspector_is_used_and_separately_identified(self):
        def reader_output(*args):
            return 'LLVM synthetic inspection fixture' if '--version' in args else self.readelf(*args)
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            self.fixture(out)
            binary = out / 'llvm-readobj'
            binary.write_bytes(b'fixture only, not executable LLVM')
            reader = out / 'llvm-readelf'
            reader.symlink_to(binary.name)
            with patch.object(MODULE, 'run', side_effect=reader_output) as calls:
                MODULE.android(out, out, out, readelf=reader)
            import json
            result = json.loads((out / 'android-artifact-evidence.json').read_text())
            self.assertEqual(len(result['libraries']), 30)
            self.assertEqual(result['inspection']['resolvedReader'], str(reader.resolve()))
            self.assertEqual(result['inspection']['readerSHA256'], hashlib.sha256(reader.read_bytes()).hexdigest())
            self.assertEqual(result['inspection']['validatorSHA256'], hashlib.sha256(Path(MODULE.__file__).read_bytes()).hexdigest())
            self.assertTrue(all(call.args[0] == str(reader) for call in calls.call_args_list))
            self.assertFalse(result['promotionEligible'])
            self.assertFalse(result['runtimeSecurityTested'])
            self.assertFalse(result['appAndSeamBindingChanged'])

    def test_relative_inspector_path_is_rejected(self):
        with self.assertRaises(ValueError):
            MODULE.android(Path('.'), Path('.'), Path('.'), readelf=Path('llvm-readelf'))


class AndroidMpvComponentBinding(unittest.TestCase):
    # Synthetic ZIP contents only: this checks acquisition policy, never native compatibility.
    URL = "https://github.com/VortXTV/VortX/releases/download/vendor-fixture/libmpv.aar"

    @staticmethod
    def fixture(path, *, min_sdk="26", missing=None, extra_abi=False, duplicate=False, manifest=None):
        entries = {
            "classes.jar": b"synthetic classes, not executable",
            "AndroidManifest.xml": manifest if manifest is not None else (
                '<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                'package="dev.jdtech.mpv"><uses-sdk android:minSdkVersion="' + min_sdk + '"/></manifest>'
            ).encode(),
        }
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            for name in ("libmpv.so", "libplayer.so", "libc++_shared.so", "libavcodec.so",
                         "libavformat.so", "libavutil.so", "libavfilter.so", "libavdevice.so",
                         "libswresample.so", "libswscale.so"):
                if f"jni/{abi}/{name}" != missing:
                    entries[f"jni/{abi}/{name}"] = b"synthetic, not an ELF"
        if extra_abi:
            entries["jni/mips/libmpv.so"] = b"unadmitted"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
            if duplicate:
                archive.writestr("classes.jar", b"duplicate")
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_empty_selection_has_no_network_and_both_consumers_share_full_only_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "android").mkdir()
            (root / "android/mpv-component.properties").write_text("url=\nsha256=\n")
            with patch.object(ACQUIRE, "build_opener", side_effect=AssertionError("unexpected network")), patch("builtins.print"):
                self.assertIsNone(ACQUIRE.acquire(root))
            self.assertFalse((root / "android/.gradle").exists())
        # Source contracts only; real Gradle resolution/package-content gates remain separate.
        app = (ROOT / "android/app/build.gradle.kts").read_text()
        seam = (ROOT / "android/mpv-seam/build.gradle.kts").read_text()
        root_build = (ROOT / "android/build.gradle.kts").read_text()
        self.assertEqual(app.count('"fullImplementation"(checkNotNull(rootProject.extra["vortxMpvAarDependency"]))'), 1)
        self.assertIn('"fullImplementation"(project(":mpv-seam"))', app)
        self.assertEqual(seam.count('mpvLinkAar(checkNotNull(rootProject.extra["vortxMpvAarDependency"]))'), 1)
        self.assertIn("dependsOn(mpvLinkAar)", seam)
        self.assertIn('extra["vortxMpvAarDependency"] = files(aar).builtBy(verifyMpvComponent)', root_build)
        self.assertIn('extra["vortxMpvAarDependency"] = libs.mpv.libmpv', root_build)
        self.assertNotIn('"playImplementation"(rootProject.extra["vortxMpvAarDependency"])', app)
        self.assertNotIn('"playImplementation"(project(":mpv-seam"))', app)

    def test_selection_and_redirects_refuse_unapproved_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            descriptor = Path(directory) / "component.properties"
            for text in (
                f"url={self.URL}\nsha256=\n", "url=\nsha256=" + "a" * 64 + "\n",
                "url=https://example.com/private.aar\nsha256=" + "a" * 64 + "\n",
                f"url={self.URL}?token=not-a-credential\nsha256=" + "a" * 64 + "\n",
                f"url={self.URL}\nsha256=" + "a" * 64 + "\nurl=\n",
            ):
                with self.subTest(descriptor=text):
                    descriptor.write_text(text)
                    with self.assertRaises(ValueError):
                        ACQUIRE.selection(descriptor)
        handler = ACQUIRE.ReleaseRedirects()
        request = ACQUIRE.Request(self.URL)
        for url in ("http://github.com/payload", "https://example.com/payload",
                    "https://user@github.com/payload", "https://github.com:444/payload"):
            with self.subTest(redirect=url), self.assertRaises(ValueError):
                handler.redirect_request(request, None, 302, "Found", {}, url)
        allowed = handler.redirect_request(request, None, 302, "Found", {},
                                           "https://release-assets.githubusercontent.com/payload?signature=synthetic")
        self.assertEqual(allowed.host, "release-assets.githubusercontent.com")

    def test_cached_bytes_are_reused_and_bad_or_linked_cache_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "android").mkdir()
            source = root / "fixture.aar"
            checksum = self.fixture(source)
            (root / "android/mpv-component.properties").write_text(f"url={self.URL}\nsha256={checksum}\n")
            target = root / "android/.gradle/vortx-mpv-components" / checksum / "libmpv.aar"
            target.parent.mkdir(parents=True)
            original = source.read_bytes()
            target.write_bytes(original)
            inode = target.stat().st_ino
            with patch.object(ACQUIRE, "build_opener", side_effect=AssertionError("unexpected network")), patch("builtins.print"):
                ACQUIRE.acquire(root)
                self.assertEqual(target.read_bytes(), original)
                self.assertEqual(target.stat().st_ino, inode)
                target.write_bytes(b"corrupt existing cache")
                with self.assertRaisesRegex(ValueError, "digest mismatch"):
                    ACQUIRE.acquire(root)
                self.assertEqual(target.read_bytes(), b"corrupt existing cache")
                target.unlink()  # fixture-owned path only; production never removes a bad cache
                target.symlink_to(source)
                with self.assertRaisesRegex(ValueError, "linked"):
                    ACQUIRE.acquire(root)
                self.assertTrue(target.is_symlink())
                self.assertEqual(source.read_bytes(), original)

    def test_actual_aar_content_gate_accepts_exact_shape_and_rejects_wrong_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixture.aar"
            checksum = self.fixture(target)
            ACQUIRE.verify_aar(target, checksum)
            for options in ({"min_sdk": "25"}, {"extra_abi": True},
                            {"missing": "jni/arm64-v8a/libavcodec.so"}):
                with self.subTest(options=options):
                    checksum = self.fixture(target, **options)
                    with self.assertRaises(ValueError):
                        ACQUIRE.verify_aar(target, checksum)
            with self.assertWarns(UserWarning):
                checksum = self.fixture(target, duplicate=True)
            with self.assertRaisesRegex(ValueError, "membership"):
                ACQUIRE.verify_aar(target, checksum)

    def test_manifest_encoding_and_character_references_keep_the_same_identity(self):
        document = ('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                    'package="dev.jdtech.mpv"><uses-sdk android:minSdkVersion="2&#54;"/></manifest>')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixture.aar"
            for encoding in ("utf-8", "utf-16"):
                with self.subTest(encoding=encoding):
                    checksum = self.fixture(target, manifest=document.encode(encoding))
                    ACQUIRE.verify_aar(target, checksum)

    def test_aar_manifest_rejects_dtd_entities_in_every_supported_encoding(self):
        manifest = ('<manifest xmlns:android="http://schemas.android.com/apk/res/android" '
                    'package="dev.jdtech.mpv"><uses-sdk android:minSdkVersion="26"/></manifest>')
        declarations = (
            '<!DOCTYPE manifest>',
            '<!DOCTYPE manifest [<!ENTITY value "26">]>',
            '<!DOCTYPE manifest [<!ENTITY value SYSTEM "file:///unread-fixture">]>',
            '<!DOCTYPE manifest [<!ENTITY % value "ignored">%value;]>',
            '<!DOCTYPE manifest SYSTEM "https://unread.invalid/fixture.dtd">',
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "fixture.aar"
            for declaration in declarations:
                for encoding in ("utf-8", "utf-16"):
                    with self.subTest(declaration=declaration, encoding=encoding):
                        checksum = self.fixture(target, manifest=(declaration + manifest).encode(encoding))
                        with self.assertRaisesRegex(ValueError, "declarations"):
                            ACQUIRE.verify_aar(target, checksum)

    def test_manifest_structure_and_malformed_xml_fail_closed(self):
        for raw in (b"<manifest>", b"<manifest>" + b"<node>" * 32 + b"</node>" * 32 + b"</manifest>",
                    b"<manifest>" + b"<node/>" * 4096 + b"</manifest>", b" " * 65537):
            with self.subTest(prefix=raw[:40]), self.assertRaises(ValueError):
                ACQUIRE.manifest_identity(raw)


class SentRequestABISynthetic(unittest.TestCase):
    @staticmethod
    def dynsym(role, *, change=None, duplicate=False, missing=False, functions=None):
        rows = ['0: 0000000000000000 0 NOTYPE LOCAL DEFAULT UND']
        functions = MODULE.SENT_FFMPEG_FUNCTIONS if functions is None else functions
        names = functions[:1] if missing else functions
        for index, name in enumerate(names, 1):
            section = '12' if role == 'defined-provider' else 'UND'
            suffix = '@@LIBAVFORMAT_63' if role == 'defined-provider' else '@LIBAVFORMAT_63 (2)'
            row = f'{index}: 0000000000000000 16 FUNC GLOBAL DEFAULT {section} {name}{suffix}'
            rows.append(change(row) if change and index == 1 else row)
        if duplicate:
            rows.append(f'{len(rows)}: 0000000000000000 16 FUNC GLOBAL DEFAULT 12 '
                        f'{functions[0]}@OTHER_VERSION')
        return f"Symbol table '.dynsym' contains {len(rows)} entries:\n" + '\n'.join(rows) + '\n'

    @staticmethod
    def nm_text(names, kind='T'):
        return 'archive.a(object.o):\n' + ''.join(
            f'0000000000000000 {kind} _{name}\n' if kind != 'U' else f'                 U _{name}\n'
            for name in names)

    @staticmethod
    def symtab(binding='LOCAL', visibility='DEFAULT', *, change=None, missing=False):
        rows = ['0: 0000000000000000 0 NOTYPE LOCAL DEFAULT UND']
        if not missing:
            row = ('1: 0000000000000010 16 FUNC ' + binding + ' ' + visibility +
                   ' 12 ' + MODULE.PRIVATE_MPV_FUNCTIONS[0])
            rows.append(change(row) if change else row)
        return f"Symbol table '.symtab' contains {len(rows)} entries:\n" + '\n'.join(rows) + '\n'

    def test_exact_elf_provider_and_import_roles_and_version_normalization(self):
        for role in ('defined-provider', 'undefined-import'):
            rows = MODULE.required_elf_functions(self.dynsym(role), MODULE.SENT_FFMPEG_FUNCTIONS, role)
            self.assertEqual({row['name'] for row in rows}, set(MODULE.SENT_FFMPEG_FUNCTIONS))
            self.assertTrue(all(row['version'] == 'LIBAVFORMAT_63' for row in rows))
            self.assertTrue(all(row['type'] == 'FUNC' and row['binding'] == 'GLOBAL' and
                                row['visibility'] == 'DEFAULT' for row in rows))
            self.assertEqual({row['section'] for row in rows}, {'12'} if role == 'defined-provider' else {'UND'})
        self.assertEqual(MODULE.required_macho_functions(self.nm_text(MODULE.SENT_MPV_FUNCTIONS),
            MODULE.SENT_MPV_FUNCTIONS, 'defined-code'), sorted(MODULE.SENT_MPV_FUNCTIONS))

    def test_required_elf_and_macho_adversaries_are_not_plain_name_matches(self):
        bad = [self.dynsym('defined-provider', missing=True), self.dynsym('defined-provider', duplicate=True)]
        for old, new in [('FUNC', 'OBJECT'), ('GLOBAL', 'WEAK'), ('DEFAULT', 'HIDDEN'),
                         (' 12 ', ' UND '), ('@@LIBAVFORMAT_63', '@'),
                         ('@@LIBAVFORMAT_63', '@' + 'A' * 129)]:
            bad.append(self.dynsym('defined-provider', change=lambda row, old=old, new=new: row.replace(old, new)))
        bad.append(self.dynsym('defined-provider').replace("'.dynsym'", "'.symtab'"))
        for text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError):
                MODULE.required_elf_functions(text, MODULE.SENT_FFMPEG_FUNCTIONS, 'defined-provider')
        with self.assertRaises(ValueError):
            MODULE.required_elf_functions(self.dynsym('defined-provider'), MODULE.SENT_FFMPEG_FUNCTIONS, 'undefined-import')
        for text in (self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS, 'D'),
                     self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS, 'W'),
                     self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS, 'U'),
                     self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS) + self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS[:1]),
                     self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS).replace('_avpriv_', '__avpriv_', 1)):
            with self.subTest(text=text), self.assertRaises(ValueError):
                MODULE.required_macho_functions(text, MODULE.SENT_FFMPEG_FUNCTIONS, 'defined-code')

    def test_finite_reader_elf_and_macho_require_every_function_and_correct_role(self):
        functions = MODULE.REFERENCE_FFMPEG_FUNCTIONS
        self.assertEqual(functions, (
            'avpriv_vortx_reference_capture_v1', 'avpriv_vortx_reference_revalidate_v1',
            'avpriv_vortx_reference_recipe_current', 'avpriv_vortx_reference_open_prefix_v1',
            'avpriv_vortx_reference_read_v1', 'avpriv_vortx_reference_stats_v1',
            'avpriv_vortx_reference_reader_close_v1', 'avpriv_vortx_reference_recipe_release_v1'))
        for role, kind in (('defined-provider', 'T'), ('undefined-import', 'U')):
            dynamic = self.dynsym(role, functions=functions)
            rows = MODULE.required_elf_functions(dynamic, functions, role)
            self.assertEqual([row['name'] for row in rows], sorted(functions))
            macho_role = 'defined-code' if role == 'defined-provider' else 'undefined-import'
            self.assertEqual(MODULE.required_macho_functions(self.nm_text(functions, kind),
                                                           functions, macho_role), sorted(functions))
            opposite_role = 'undefined-import' if role == 'defined-provider' else 'defined-provider'
            with self.assertRaises(ValueError):
                MODULE.required_elf_functions(dynamic, functions, opposite_role)
            for missing in functions:
                remaining = tuple(name for name in functions if name != missing)
                with self.subTest(role=role, missing=missing), self.assertRaises(ValueError):
                    MODULE.required_elf_functions(self.dynsym(role, functions=remaining), functions, role)
                with self.subTest(role=role, missing=missing), self.assertRaises(ValueError):
                    MODULE.required_macho_functions(self.nm_text(remaining, kind), functions, macho_role)
        for old, new in ((' FUNC ', ' OBJECT '), (' GLOBAL ', ' WEAK '),
                         (' DEFAULT ', ' HIDDEN '), (' 12 ', ' UND ')):
            text = self.dynsym('defined-provider', functions=functions,
                               change=lambda row, old=old, new=new: row.replace(old, new))
            with self.subTest(change=(old, new)), self.assertRaises(ValueError):
                MODULE.required_elf_functions(text, functions, 'defined-provider')

    def test_apple_material_abi_rejects_old_component_missing_private_helper(self):
        required = MODULE.SENT_MPV_FUNCTIONS
        symbol = "stream_reference_source_prepare_prefix_request"
        self.assertIn(symbol, required)
        old = tuple(name for name in required if name != symbol)
        with self.assertRaisesRegex(ValueError, "missing required"):
            MODULE.required_macho_functions(self.nm_text(old), required, 'defined-code')
        for kind in ('U', 'D', 'W'):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                MODULE.required_macho_functions(self.nm_text(old) + self.nm_text((symbol,), kind),
                                                required, 'defined-code')

    def test_private_transport_code_requires_complete_nonexported_symtab(self):
        for binding, visibility in (('LOCAL', 'DEFAULT'), ('LOCAL', 'HIDDEN'), ('GLOBAL', 'HIDDEN')):
            text = self.symtab(binding, visibility)
            for observed in (text, self.dynsym('undefined-import') + text):
                with self.subTest(binding=binding, visibility=visibility):
                    rows = MODULE.required_private_elf_functions(observed, MODULE.PRIVATE_MPV_FUNCTIONS)
                    self.assertEqual(rows, [{'name': MODULE.PRIVATE_MPV_FUNCTIONS[0], 'size': 16,
                                            'type': 'FUNC', 'binding': binding, 'visibility': visibility,
                                            'section': '12'}])
        valid = self.symtab()
        bad = [self.symtab(missing=True), valid + valid,
               valid.replace("'.symtab'", "'.unknown'"),
               valid.replace('contains 2 entries:', 'contains two entries:'),
               valid.replace('1: 0000000000000010', '2: 0000000000000010'),
               valid.replace('contains 2 entries:', 'contains 3 entries:') + valid.splitlines()[-1] + '\n']
        for old, new in ((' 16 FUNC ', ' 0 FUNC '), (' FUNC ', ' OBJECT '),
                         (' LOCAL ', ' WEAK '), (' 12 ', ' UND '), (' 12 ', ' ABS '),
                         (' LOCAL DEFAULT 12 ', ' GLOBAL DEFAULT 12 ')):
            bad.append(self.symtab(change=lambda row, old=old, new=new: row.replace(old, new)))
        for observed in bad:
            with self.subTest(text=observed), self.assertRaises(ValueError):
                MODULE.required_private_elf_functions(observed, MODULE.PRIVATE_MPV_FUNCTIONS)

    def test_dynamic_private_export_or_retired_provider_cannot_satisfy_v2(self):
        self.assertEqual(MODULE.SENT_FFMPEG_FUNCTIONS,
                         ('avpriv_vortx_http_copy_sent_request_v2',
                          'avpriv_vortx_http_sent_request_clear_v2'))
        for forbidden in MODULE.RETIRED_SENT_FFMPEG_FUNCTIONS + MODULE.PRIVATE_MPV_FUNCTIONS:
            dynamic = self.dynsym('undefined-import').replace('contains 3 entries:', 'contains 4 entries:')
            dynamic += f'3: 0000000000000010 16 FUNC GLOBAL DEFAULT 12 {forbidden}\n'
            with self.subTest(name=forbidden), self.assertRaises(ValueError):
                MODULE.required_elf_functions(dynamic, MODULE.SENT_FFMPEG_FUNCTIONS, 'undefined-import',
                                              MODULE.RETIRED_SENT_FFMPEG_FUNCTIONS + MODULE.PRIVATE_MPV_FUNCTIONS)
        for retired in MODULE.RETIRED_SENT_FFMPEG_FUNCTIONS:
            with self.assertRaises(ValueError):
                MODULE.required_macho_functions(self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS) +
                                                self.nm_text((retired,)), MODULE.SENT_FFMPEG_FUNCTIONS,
                                                'defined-code', MODULE.RETIRED_SENT_FFMPEG_FUNCTIONS)

    def test_real_android_producer_records_default_reader_and_six_symbol_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reader = root / 'ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf'
            reader.parent.mkdir(parents=True)
            reader.write_bytes(b'synthetic reader identity only; subprocess is mocked')
            keys = [bytes([index]) * 32 for index in range(1, 9)]
            (root / 'tls_mbedtls_vortx_distrust.h').write_text('\n'.join(
                '{' + ','.join(f'0x{value:02x}' for value in key) + '}, /* EM fixture */' for key in keys))
            libraries = ('libmpv.so', 'libplayer.so', 'libc++_shared.so', 'libavcodec.so', 'libavformat.so',
                         'libavutil.so', 'libavfilter.so', 'libavdevice.so', 'libswresample.so', 'libswscale.so')
            with zipfile.ZipFile(root / 'libmpv-release-candidate.aar', 'w') as archive:
                for abi in ('arm64-v8a', 'armeabi-v7a', 'x86_64'):
                    for library in libraries:
                        data = f'{abi}/{library}\0'.encode()
                        if library == 'libavformat.so':
                            data += b'--enable-mbedtls --enable-gpl --enable-version3\0' + b''.join(keys)
                        archive.writestr(f'jni/{abi}/{library}', data)
            def readelf(*args):
                if '--version' in args:
                    return 'LLVM synthetic ABI fixture\n'
                data = Path(args[-1]).read_bytes()
                if '--dyn-syms' in args:
                    self.assertIn('--wide', args)
                    return self.dynsym('defined-provider' if b'libavformat.so' in data else 'undefined-import',
                                       functions=MODULE.SENT_FFMPEG_FUNCTIONS + MODULE.REFERENCE_FFMPEG_FUNCTIONS)
                if '--symbols' in args:
                    self.assertIn('--wide', args)
                    self.assertIn(b'libmpv.so', data)
                    binding, visibility = ('LOCAL', 'DEFAULT') if b'armeabi-v7a' in data else (
                        ('LOCAL', 'HIDDEN') if b'arm64-v8a' in data else ('GLOBAL', 'HIDDEN'))
                    return self.symtab(binding, visibility)
                if '-h' in args:
                    elf, machine = ('ELF32', 'ARM') if b'armeabi-v7a' in data else (
                        ('ELF64', 'AArch64') if b'arm64-v8a' in data else ('ELF64', 'Advanced Micro Devices X86-64'))
                    return f'Class: {elf}\nMachine: {machine}\nType: DYN\nData: little endian\n'
                return ('LOAD 0x0000 0x0000 0x0000 0x1000 0x1000 R E 0x4000\n'
                        'GNU_RELRO 0x4000 0x4000 0x4000 0x4000 0x4000 R 0x1\n')
            with patch.object(MODULE, 'run', side_effect=readelf) as calls:
                MODULE.android(root, root, root)
            evidence = json.loads((root / 'android-artifact-evidence.json').read_text())
            abi = evidence['sentRequestABI']
            self.assertEqual(abi['schema'], 2)
            self.assertIs(abi['retiredV1Absent'], True)
            self.assertEqual(len(abi['libraries']), 6)
            self.assertEqual({row['role'] for row in abi['libraries']}, {'defined-provider', 'undefined-import'})
            self.assertEqual(abi['reader']['requestedReader'], str(reader))
            self.assertEqual(abi['reader']['resolvedReader'], str(reader.resolve()))
            self.assertEqual(abi['reader']['readerSHA256'], hashlib.sha256(reader.read_bytes()).hexdigest())
            self.assertEqual(abi['reader']['validatorSHA256'], hashlib.sha256(Path(MODULE.__file__).read_bytes()).hexdigest())
            self.assertEqual(abi['reader']['mode'], 'hosted-ndk-reader')
            self.assertEqual(len([c for c in calls.call_args_list if '--dyn-syms' in c.args]), 6)
            self.assertEqual(len([c for c in calls.call_args_list if '--symbols' in c.args]), 3)
            private = evidence['privateOwnerABI']
            self.assertEqual(private['schema'], 1)
            self.assertEqual(private['reader'], abi['reader'])
            self.assertEqual(len(private['libraries']), 3)
            self.assertEqual({row['abi'] for row in private['libraries']}, {'armeabi-v7a', 'arm64-v8a', 'x86_64'})
            reference = evidence['referenceReaderABI']
            self.assertEqual(reference['schema'], 1)
            self.assertEqual(reference['reader'], abi['reader'])
            self.assertEqual(len(reference['libraries']), 6)
            self.assertEqual({(row['abi'], row['library'], row['role']) for row in reference['libraries']},
                             {(native_abi, library, role) for native_abi in ('armeabi-v7a', 'arm64-v8a', 'x86_64')
                              for library, role in (('libavformat.so', 'defined-provider'),
                                                    ('libmpv.so', 'undefined-import'))})
            with zipfile.ZipFile(root / 'libmpv-release-candidate.aar') as archive:
                for row in reference['libraries']:
                    dynamic = self.dynsym(row['role'], functions=(MODULE.SENT_FFMPEG_FUNCTIONS +
                                                                 MODULE.REFERENCE_FFMPEG_FUNCTIONS))
                    self.assertEqual(row['binarySHA256'], hashlib.sha256(
                        archive.read('jni/' + row['abi'] + '/' + row['library'])).hexdigest())
                    self.assertEqual(row['dynsymTextSHA256'], hashlib.sha256(dynamic.encode()).hexdigest())
                    self.assertEqual(row['symbols'], MODULE.required_elf_functions(
                        dynamic, MODULE.REFERENCE_FFMPEG_FUNCTIONS, row['role']))
                for row in private['libraries']:
                    self.assertEqual(row['library'], 'libmpv.so')
                    self.assertEqual(row['binarySHA256'], hashlib.sha256(
                        archive.read('jni/' + row['abi'] + '/libmpv.so')).hexdigest())
                    binding, visibility = ('LOCAL', 'DEFAULT') if row['abi'] == 'armeabi-v7a' else (
                        ('LOCAL', 'HIDDEN') if row['abi'] == 'arm64-v8a' else ('GLOBAL', 'HIDDEN'))
                    text = self.symtab(binding, visibility)
                    self.assertEqual(row['symtabTextSHA256'], hashlib.sha256(text.encode()).hexdigest())
                    self.assertEqual(row['symbols'], MODULE.required_private_elf_functions(
                        text, MODULE.PRIVATE_MPV_FUNCTIONS))
            self.assertNotIn('inspection', evidence)
            self.assertFalse(evidence['promotionEligible'])
            self.assertFalse(evidence['runtimeSecurityTested'])
            for row in abi['libraries']:
                self.assertEqual(len(row['binarySHA256']), 64)
                self.assertEqual(len(row['dynsymTextSHA256']), 64)

    def test_apple_complete_nine_arch_gate_and_fixed_marker_with_cleanup(self):
        slices = {'ios-arm64': ('arm64',), 'ios-arm64_x86_64-simulator': ('arm64', 'x86_64'),
                  'tvos-arm64_arm64e': ('arm64', 'arm64e'),
                  'tvos-arm64_x86_64-simulator': ('arm64', 'x86_64'),
                  'macos-arm64_x86_64': ('arm64', 'x86_64')}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'package'
            for library in ('Libavformat', 'Libmpv'):
                for slice_name in slices:
                    binary = package / 'artifacts' / (library + '-GPL.xcframework') / slice_name / (library + '.framework') / library
                    binary.parent.mkdir(parents=True)
                    binary.write_bytes(f'{library}|{slice_name}'.encode())
            def inspect(*args):
                if '-archs' in args:
                    slice_name = Path(args[-1]).read_text().split('|')[1]
                    return ' '.join(reversed(slices[slice_name])) + '\n'
                if '-thin' in args:
                    Path(args[-1]).write_bytes(Path(args[2]).read_bytes())
                    return ''
                library = Path(args[-1]).read_text().split('|')[0]
                if '-gu' in args:
                    return self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS + MODULE.REFERENCE_FFMPEG_FUNCTIONS, 'U')
                return self.nm_text(MODULE.SENT_FFMPEG_FUNCTIONS + MODULE.REFERENCE_FFMPEG_FUNCTIONS
                                    if library == 'Libavformat' else MODULE.SENT_MPV_FUNCTIONS)
            output = io.StringIO()
            with patch.object(MODULE, 'run', side_effect=inspect) as calls, redirect_stdout(output):
                MODULE.apple_sent_abi(package)
            self.assertEqual(output.getvalue(), 'PASS apple-sent-abi\nPASS apple-reference-abi\n')
            self.assertEqual(len([c for c in calls.call_args_list if '-gU' in c.args]), 18)
            self.assertEqual(len([c for c in calls.call_args_list if '-gu' in c.args]), 9)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['package'])
            def wrong_archs(*args):
                return 'arm64 x86_64 arm64e\n' if '-archs' in args else inspect(*args)
            with patch.object(MODULE, 'run', side_effect=wrong_archs), self.assertRaises(ValueError):
                MODULE.apple_sent_abi(package)
            self.assertEqual(sorted(path.name for path in root.iterdir()), ['package'])


class SentRequestOfflineEvidence(unittest.TestCase):
    @staticmethod
    def fixture():
        spec = importlib.util.spec_from_file_location(
            "sent_request_offline_consumer", ROOT / "scripts/package-native-current-component.py")
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        validator = hashlib.sha256(Path(MODULE.__file__).read_bytes()).hexdigest()
        value = {"schema": 2, "retiredV1Absent": True,
                 "reader": {"mode": "hosted-ndk-reader", "requestedReader": "/sdk/llvm-readelf",
                 "resolvedReader": "/sdk/llvm-readobj", "readerSHA256": "a" * 64,
                 "readerVersion": "synthetic LLVM", "validatorSHA256": validator}, "libraries": []}
        records = {}
        for abi in ("arm64-v8a", "armeabi-v7a", "x86_64"):
            for library, role in (("libavformat.so", "defined-provider"), ("libmpv.so", "undefined-import")):
                checksum = hashlib.sha256((abi + library).encode()).hexdigest()
                records["jni/" + abi + "/" + library] = {"sha256": checksum}
                value["libraries"].append({"abi": abi, "library": library, "role": role,
                    "binarySHA256": checksum, "dynsymTextSHA256": "b" * 64,
                    "symbols": MODULE.required_elf_functions(SentRequestABISynthetic.dynsym(role),
                                                              MODULE.SENT_FFMPEG_FUNCTIONS, role)})
        return consumer, value, records, validator

    def test_six_roles_are_bound_to_verified_jni_and_validator_source(self):
        consumer, value, records, validator = self.fixture()
        consumer.sent_request_abi(value, records, validator)
        explicit = deepcopy(value)
        explicit["reader"]["mode"] = "explicit-read-only-inspector"
        consumer.sent_request_abi(explicit, records, validator)
        self.assertIn('"PASS apple-sent-abi" in gate.splitlines()',
                      (ROOT / "scripts/package-native-current-component.py").read_text())

    def test_stale_or_forged_normalized_evidence_is_rejected(self):
        consumer, original, records, validator = self.fixture()
        for change in (lambda value: value.update(schema=1),
                       lambda value: value.update(retiredV1Absent=False),
                       lambda value: value["libraries"].pop(),
                       lambda value: value["libraries"].append(deepcopy(value["libraries"][0])),
                       lambda value: value["libraries"][0].update(role="undefined-import"),
                       lambda value: value["libraries"][0].update(binarySHA256="c" * 64),
                       lambda value: value["reader"].update(validatorSHA256="c" * 64),
                       lambda value: value["reader"].update(mode="unknown"),
                       lambda value: value["reader"].update(requestedReader="relative-reader"),
                       lambda value: value["libraries"][0]["symbols"][0].update(type="OBJECT"),
                       lambda value: value["libraries"][0]["symbols"][0].update(binding="WEAK"),
                       lambda value: value["libraries"][0]["symbols"][0].update(visibility="HIDDEN"),
                       lambda value: value["libraries"][0]["symbols"][0].update(section="UND"),
                       lambda value: value["libraries"][1]["symbols"][0].update(section="12"),
                       lambda value: value["libraries"][0]["symbols"][0].update(version="bad@version"),
                       lambda value: value["libraries"][0]["symbols"][0].update(
                           name="avpriv_vortx_http_copy_sent_request"),
                       lambda value: value.update(ignoredUnknownField=True)):
            value = deepcopy(original)
            change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                consumer.sent_request_abi(value, records, validator)
        with self.assertRaises(ValueError):
            consumer.sent_request_abi({}, records, validator)

    @staticmethod
    def reference_fixture():
        consumer, sent, records, validator = SentRequestOfflineEvidence.fixture()
        value = {'schema': 1, 'reader': deepcopy(sent['reader']), 'libraries': []}
        for row in sent['libraries']:
            reference = deepcopy(row)
            reference['symbols'] = MODULE.required_elf_functions(
                SentRequestABISynthetic.dynsym(row['role'], functions=MODULE.REFERENCE_FFMPEG_FUNCTIONS),
                MODULE.REFERENCE_FFMPEG_FUNCTIONS, row['role'])
            value['libraries'].append(reference)
        return consumer, value, sent, records, validator

    def test_reference_reader_evidence_binds_all_eight_functions_and_same_inspector(self):
        consumer, value, sent, records, validator = self.reference_fixture()
        consumer.sent_request_abi(sent, records, validator)
        consumer.reference_reader_abi(value, records, validator, sent['reader'])
        self.assertEqual(set(consumer.REFERENCE_FFMPEG_FUNCTIONS), set(MODULE.REFERENCE_FFMPEG_FUNCTIONS))
        self.assertEqual(len(value['libraries']), 6)
        self.assertTrue(all(len(row['symbols']) == 8 for row in value['libraries']))
        explicit = deepcopy(value)
        expected_reader = deepcopy(sent['reader'])
        expected_reader['mode'] = 'explicit-read-only-inspector'
        explicit['reader'] = deepcopy(expected_reader)
        consumer.reference_reader_abi(explicit, records, validator, expected_reader)
        self.assertIn('"PASS apple-reference-abi" in gate.splitlines()',
                      (ROOT / 'scripts/package-native-current-component.py').read_text())

    def test_reference_reader_evidence_rejects_missing_functions_wrong_roles_and_bytes(self):
        consumer, original, sent, records, validator = self.reference_fixture()
        for index, row in enumerate(original['libraries']):
            for symbol_index, symbol in enumerate(row['symbols']):
                value = deepcopy(original)
                value['libraries'][index]['symbols'].pop(symbol_index)
                with self.subTest(abi=row['abi'], library=row['library'], missing=symbol['name']), self.assertRaises(ValueError):
                    consumer.reference_reader_abi(value, records, validator, sent['reader'])
        for change in (lambda value: value.update(schema=2),
                       lambda value: value.update(schema=True),
                       lambda value: value['libraries'].pop(),
                       lambda value: value['libraries'].append(deepcopy(value['libraries'][0])),
                       lambda value: value['libraries'][0].update(role='undefined-import'),
                       lambda value: value['libraries'][1].update(role='defined-provider'),
                       lambda value: value['libraries'][0].update(binarySHA256='c' * 64),
                       lambda value: value['libraries'][0].update(dynsymTextSHA256='not-a-digest'),
                       lambda value: value['libraries'][0]['symbols'][0].update(type='OBJECT'),
                       lambda value: value['libraries'][0]['symbols'][0].update(binding='WEAK'),
                       lambda value: value['libraries'][0]['symbols'][0].update(visibility='HIDDEN'),
                       lambda value: value['libraries'][0]['symbols'][0].update(section='UND'),
                       lambda value: value['libraries'][1]['symbols'][0].update(section='12'),
                       lambda value: value['libraries'][0]['symbols'][0].update(
                           name='avpriv_vortx_http_copy_sent_request_v2'),
                       lambda value: value['libraries'][0]['symbols'].append(
                           deepcopy(value['libraries'][0]['symbols'][0])),
                       lambda value: value.update(unrecognized=True)):
            value = deepcopy(original)
            change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                consumer.reference_reader_abi(value, records, validator, sent['reader'])
        with self.assertRaises(ValueError):
            consumer.reference_reader_abi({}, records, validator, sent['reader'])
        missing_record = dict(records)
        del missing_record['jni/arm64-v8a/libavformat.so']
        with self.assertRaises(ValueError):
            consumer.reference_reader_abi(original, missing_record, validator, sent['reader'])

    def test_reference_reader_evidence_rejects_distinct_valid_inspector_identity(self):
        consumer, original, sent, records, validator = self.reference_fixture()
        # Each altered reader remains individually well formed and validator-bound;
        # rejection must come from equality with the original sent-request reader.
        for field, replacement in (('mode', 'explicit-read-only-inspector'),
                                   ('requestedReader', '/another/llvm-readelf'),
                                   ('resolvedReader', '/another/llvm-readobj'),
                                   ('readerSHA256', 'd' * 64),
                                   ('readerVersion', 'another synthetic LLVM')):
            value = deepcopy(original)
            value['reader'][field] = replacement
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'inspector identity'):
                consumer.reference_reader_abi(value, records, validator, sent['reader'])
        forged_validator = deepcopy(original)
        forged_validator['reader']['validatorSHA256'] = 'd' * 64
        with self.assertRaises(ValueError):
            consumer.reference_reader_abi(forged_validator, records, validator, sent['reader'])

    @staticmethod
    def private_fixture():
        consumer, sent, records, validator = SentRequestOfflineEvidence.fixture()
        private = {"schema": 1, "reader": deepcopy(sent["reader"]), "libraries": []}
        for abi, binding, visibility in (("armeabi-v7a", "LOCAL", "DEFAULT"),
                                         ("arm64-v8a", "LOCAL", "HIDDEN"),
                                         ("x86_64", "GLOBAL", "HIDDEN")):
            private["libraries"].append({"abi": abi, "library": "libmpv.so",
                "binarySHA256": records["jni/" + abi + "/libmpv.so"]["sha256"],
                "symtabTextSHA256": "c" * 64,
                "symbols": MODULE.required_private_elf_functions(
                    SentRequestABISynthetic.symtab(binding, visibility), MODULE.PRIVATE_MPV_FUNCTIONS)})
        return consumer, private, sent, records, validator

    def test_private_code_evidence_is_bound_to_same_reader_and_three_packaged_libraries(self):
        consumer, private, sent, records, validator = self.private_fixture()
        consumer.sent_request_abi(sent, records, validator)
        consumer.private_owner_abi(private, records, sent["reader"])
        explicit = deepcopy(sent["reader"])
        explicit["mode"] = "explicit-read-only-inspector"
        private["reader"] = deepcopy(explicit)
        consumer.private_owner_abi(private, records, explicit)

    def test_private_code_evidence_rejects_stale_reader_bytes_and_noncode_rows(self):
        consumer, original, sent, records, _ = self.private_fixture()
        for change in (lambda value: value.update(schema=2),
                       lambda value: value.update(schema=True),
                       lambda value: value["libraries"].pop(),
                       lambda value: value["libraries"].append(deepcopy(value["libraries"][0])),
                       lambda value: value["libraries"][0].update(abi="mips"),
                       lambda value: value["libraries"][0].update(library="libavformat.so"),
                       lambda value: value["reader"].update(readerSHA256="d" * 64),
                       lambda value: value["libraries"][0].update(binarySHA256="d" * 64),
                       lambda value: value["libraries"][0].update(symtabTextSHA256="not-a-digest"),
                       lambda value: value["libraries"][0]["symbols"].clear(),
                       lambda value: value["libraries"][0]["symbols"].append(
                           deepcopy(value["libraries"][0]["symbols"][0])),
                       lambda value: value["libraries"][0]["symbols"][0].update(name="other_helper"),
                       lambda value: value["libraries"][0]["symbols"][0].update(size=0),
                       lambda value: value["libraries"][0]["symbols"][0].update(size=True),
                       lambda value: value["libraries"][0]["symbols"][0].update(type="OBJECT"),
                       lambda value: value["libraries"][0]["symbols"][0].update(binding="WEAK"),
                       lambda value: value["libraries"][0]["symbols"][0].update(binding="GLOBAL"),
                       lambda value: value["libraries"][0]["symbols"][0].update(section="UND"),
                       lambda value: value["libraries"][0]["symbols"][0].update(section="ABS"),
                       lambda value: value["libraries"][0]["symbols"][0].update(section="0"),
                       lambda value: value.update(unrecognized=True)):
            value = deepcopy(original)
            change(value)
            with self.subTest(value=value), self.assertRaises(ValueError):
                consumer.private_owner_abi(value, records, sent["reader"])
        with self.assertRaises(ValueError):
            consumer.private_owner_abi({}, records, sent["reader"])


if __name__ == "__main__":
    unittest.main()
