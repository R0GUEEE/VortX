#!/usr/bin/env bash
set -euo pipefail

# Hosted, manually requested candidate build only. Never installs into app/Vendor or publishes.
[[ ${GITHUB_ACTIONS:-} == true && ${GITHUB_EVENT_NAME:-} == workflow_dispatch ]] || exit 1
[[ ${GITHUB_REPOSITORY:-} == VortXTV/VortX ]] || exit 1
lane=${1:?apple or android required}
[[ $lane == apple || $lane == android ]] || exit 1
root=$(cd "$(dirname "$0")/../.." && pwd -P)
[[ $root == "$GITHUB_WORKSPACE" && $(git -C "$root" rev-parse HEAD) == "$GITHUB_SHA" ]] || exit 1
[[ -z $(git -C "$root" status --porcelain --untracked-files=no) ]] || exit 1
cd "$root"
shasum -a 256 -c scripts/native-media-hosted/inputs.sha256
out="$root/_build/native-media/$lane"
mkdir -p "$out"
[[ ! -e "$out/result.json" ]] || { echo 'Refusing an existing build receipt' >&2; exit 1; }
if [[ $lane == apple ]]; then
    upstream="$root/_native-mpvkit"
    expected=2103893078c5e339073b11737b86f7f22b9c4491
else
    upstream="$root/_native-publisher"
    expected=fcf6745703dc1265bca88f12fee8fc355ddf251e
fi
[[ $(git -C "$upstream" rev-parse HEAD) == "$expected" ]] || exit 1
[[ -z $(git -C "$upstream" status --porcelain --untracked-files=all) ]] || exit 1
# URLContext's private layout grew. A clean Git status alone does not exclude
# ignored native objects/archives with the old allocation size. Require a fresh
# candidate native closure; fail rather than deleting or reusing unknown caches.
if [[ $lane == apple ]]; then
    cold_paths=(dist)
else
    cold_paths=(buildscripts/deps buildscripts/prefix libmpv/src/main/jniLibs libmpv/build)
fi
for relative in "${cold_paths[@]}"; do
    [[ ! -e "$upstream/$relative" && ! -L "$upstream/$relative" ]] || {
        echo "A fresh native candidate workspace is required: $relative" >&2
        exit 1
    }
done
finish() {
    result=$?
    trap - EXIT
    set +e
    python3 "$root/scripts/native-media-hosted/evidence.py" receipt "$lane" "$out" "$upstream" "$result"
    receipt_result=$?
    [[ $result != 0 || $receipt_result == 0 ]] || result=$receipt_result
    exit "$result"
}
trap finish EXIT
cp scripts/native-media-hosted/inputs.sha256 "$out/reviewed-inputs.sha256"
git -C "$upstream" archive HEAD | gzip > "$out/original-upstream-source.tar.gz"

if [[ $lane == apple ]]; then
    {
        xcodebuild -version
        xcrun --sdk macosx --show-sdk-version
        xcrun --sdk iphoneos --show-sdk-version
        xcrun --sdk appletvos --show-sdk-version
        swift --version
        brew list --versions
    } > "$out/toolchain.txt" 2>&1
    MPVKIT_DVFEL_WORK="$upstream" MPVKIT_DVFEL_DEST="$out/MPVKit-DVFEL" \
        bash scripts/build-mpvkit-dvfel.sh
    # Existing builder already runs these; explicit retained log identifies candidate acceptance only.
    bash scripts/verify-mpvkit-dvfel-artifacts.sh "$out/MPVKit-DVFEL" > "$out/apple-artifact-gate.txt" 2>&1
    python3 scripts/native-media-hosted/evidence.py apple-sent-abi "$out/MPVKit-DVFEL" >> "$out/apple-artifact-gate.txt" 2>&1
    tar -czf "$out/apple-candidate.tar.gz" -C "$out" MPVKit-DVFEL
    # Keep the original per-library ZIP bytes used by assembly, separately from the assembled package.
    tar -cf "$out/apple-original-library-zips.tar" -C "$upstream/dist/release" \
        Libmpv.xcframework.zip Libavcodec.xcframework.zip Libavdevice.xcframework.zip \
        Libavfilter.xcframework.zip Libavformat.xcframework.zip Libavutil.xcframework.zip \
        Libswresample.xcframework.zip Libswscale.xcframework.zip Libplacebo.xcframework.zip
else
    # Downstream make/meson generators invoke python3 from PATH. Verify that SAME isolated
    # interpreter and its complete pinned third-party closure before downloading/building.
    unset PYTHONPATH PYTHONHOME
    export PYTHONNOUSERSITE=1
    export PYTHON="$(command -v python3)"
    "$PYTHON" -I - "${RUNNER_TEMP:?hosted temporary directory required}/vortx-native-media-tools" \
        > "$out/python-toolchain.json" <<'PYTHON_TOOLCHAIN'
import importlib
import importlib.metadata
import json
from pathlib import Path
import platform
import site
import sys

tools = Path(sys.argv[1]).resolve()
if (Path(sys.prefix).resolve() != tools or sys.prefix == sys.base_prefix
        or sys.version_info[:2] != (3, 12) or platform.system() != "Linux"
        or platform.machine() != "x86_64" or site.ENABLE_USER_SITE is not False):
    raise SystemExit("Unexpected native media Python interpreter/isolation")
config = dict(line.strip().split(" = ", 1) for line in (tools / "pyvenv.cfg").read_text().splitlines()
              if " = " in line)
if config.get("include-system-site-packages") != "false":
    raise SystemExit("System site packages are not permitted")
packages = {}
for distribution, module_name, expected_version in [
        ("meson", "mesonbuild", "1.12.1"), ("Jinja2", "jinja2", "3.1.6"),
        ("MarkupSafe", "markupsafe", "3.0.4"), ("jsonschema", "jsonschema", "4.26.0"),
        ("attrs", "attrs", "26.1.0"),
        ("jsonschema-specifications", "jsonschema_specifications", "2025.9.1"),
        ("referencing", "referencing", "0.37.0"), ("rpds-py", "rpds", "2026.9.1"),
        ("typing-extensions", "typing_extensions", "4.16.0")]:
    module = importlib.import_module(module_name)
    origin = Path(module.__file__).resolve()
    metadata = importlib.metadata.distribution(distribution)
    if (metadata.version != expected_version or not origin.is_relative_to(tools)
            or not Path(metadata.locate_file("")).resolve().is_relative_to(tools)):
        raise SystemExit("Unexpected native media Python package/version/origin")
    packages[distribution] = {"version": metadata.version, "module": str(origin)}
# Exercise the actual generator libraries' ordinary APIs, not only metadata imports.
jinja_module = importlib.import_module("jinja2")
schema_module = importlib.import_module("jsonschema")
if jinja_module.Environment().from_string("{{ value|e }}").render(value="<probe>") != "&lt;probe&gt;":
    raise SystemExit("Jinja2 rendering preflight failed")
schema_module.validate({"bounded": True}, {"type": "object",
                       "properties": {"bounded": {"type": "boolean"}},
                       "required": ["bounded"], "additionalProperties": False})
json.dump({"executable": sys.executable, "python": sys.version, "basePrefix": sys.base_prefix,
           "platform": platform.platform(), "systemSitePackages": False,
           "userSitePackages": False, "packages": packages}, sys.stdout, indent=2, sort_keys=True)
sys.stdout.write("\n")
PYTHON_TOOLCHAIN
    overlay="$root/scripts/native-media-hosted/android"
    # Exact public checkout plus reviewed overlay; never use an existing dirty dependency directory.
    for relative in buildscripts/include/depinfo.sh buildscripts/include/download-deps.sh \
        buildscripts/include/download-lua.sh buildscripts/include/download-libunibreak.sh \
        buildscripts/scripts/ffmpeg.sh buildscripts/patch.sh \
        buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch; do
        mkdir -p "$upstream/$(dirname "$relative")"
        cp "$overlay/$relative" "$upstream/$relative"
    done
    # Keep the selected producer's HTTP interruption fix and use the same new
    # upstream audio/seek backports as Apple. All patch bytes are input-hash bound.
    mkdir -p "$upstream/buildscripts/patches/mpv"
    cp "$root/scripts/mpv-http-seek-interrupt.patch" "$upstream/buildscripts/patches/mpv/0001-http-seek-interrupt.patch"
    cp "$root/scripts/mpv-upstream-audio-layout-bounds.patch" "$upstream/buildscripts/patches/mpv/0002-upstream-audio-layout-bounds.patch"
    cp "$root/scripts/mpv-upstream-gpu-next-seek-reset.patch" "$upstream/buildscripts/patches/mpv/0003-upstream-gpu-next-seek-reset.patch"
    # Same original-source witness prerequisite as Apple; no renderer/consumer activation.
    cp "$root/scripts/mpv-original-local-file-witness.patch" "$upstream/buildscripts/patches/mpv/0004-original-local-file-witness.patch"
    cp "$root/scripts/mpv-upstream-edition-title-lifetime.patch" "$upstream/buildscripts/patches/mpv/0005-upstream-edition-title-lifetime.patch"
    # Same bounded decoder foundation as Apple; no renderer/host activation.
    cp "$root/scripts/mpv-owned-subtitle-constructor.patch" "$upstream/buildscripts/patches/mpv/0006-owned-subtitle-constructor.patch"
    # Same first-drain publication lifecycle as Apple; app caller ownership remains separate.
    cp "$root/scripts/mpv-owned-subtitle-publication.patch" "$upstream/buildscripts/patches/mpv/0007-owned-subtitle-publication.patch"
    # Same immutable INITIAL_REQUEST_ONLY prerequisite; no remote reader/node/UI activation.
    cp "$root/scripts/mpv-original-http-reference-recipe.patch" "$upstream/buildscripts/patches/mpv/0008-original-http-reference-recipe.patch"
    # Same original-cancel observer lifetime prerequisite as Apple; no reader activation.
    cp "$root/scripts/mpv-original-http-cancel-lifetime.patch" "$upstream/buildscripts/patches/mpv/0009-original-http-cancel-lifetime.patch"
    # Owned observation only; INITIAL_REQUEST_ONLY and reader admission remain unchanged.
    cp "$root/scripts/mpv-completed-response-observation.patch" "$upstream/buildscripts/patches/mpv/0010-completed-response-observation.patch"
    cp "$root/scripts/mpv-sent-request-evidence.patch" "$upstream/buildscripts/patches/mpv/0011-sent-request-evidence.patch"
    # Private bounded request material only; no duplicate open or reader activation.
    cp "$root/scripts/mpv-bounded-prefix-request.patch" "$upstream/buildscripts/patches/mpv/0012-bounded-prefix-request.patch"
    # Original route/TLS policy observation only; hidden native getter is not a public ABI.
    cp "$root/scripts/mpv-original-transport-evidence.patch" "$upstream/buildscripts/patches/mpv/0013-original-transport-evidence.patch"
    # Coherent finite reader and native owned publication follow the route prerequisite.
    cp "$root/scripts/mpv-finite-reference-reader.patch" "$upstream/buildscripts/patches/mpv/0014-finite-reference-reader.patch"
    # FFmpeg's sorted sequence keeps the existing TLS distrust patch first.
    # The same source bytes as Apple add optional paired response metadata only.
    cp "$root/scripts/ffmpeg-response-witness.patch" "$upstream/buildscripts/patches/ffmpeg/0002-response-witness.patch"
    cp "$root/scripts/ffmpeg-response-generation.patch" "$upstream/buildscripts/patches/ffmpeg/0003-response-generation.patch"
    cp "$root/scripts/ffmpeg-sent-request-evidence.patch" "$upstream/buildscripts/patches/ffmpeg/0004-sent-request-evidence.patch"
    cp "$root/scripts/ffmpeg-original-transport-evidence.patch" "$upstream/buildscripts/patches/ffmpeg/0005-original-transport-evidence.patch"
    cp "$root/scripts/ffmpeg-finite-reference-reader.patch" "$upstream/buildscripts/patches/ffmpeg/0006-finite-reference-reader.patch"
    git -C "$upstream" apply --check "$root/scripts/native-media-hosted/android-assembly.patch"
    git -C "$upstream" apply "$root/scripts/native-media-hosted/android-assembly.patch"
    # Use the hosted SDK; bypass upstream download-sdk.sh's moving gas-preprocessor/master fetch.
    : "${ANDROID_HOME:?hosted Android SDK required}"
    mkdir -p "$upstream/buildscripts/sdk/bin"
    ln -s "$ANDROID_HOME" "$upstream/buildscripts/sdk/android-sdk-linux"
    sdkmanager="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"
    [[ -x $sdkmanager ]] || exit 1
    printf 'y\n' | "$sdkmanager" --sdk_root="$ANDROID_HOME" \
        'platforms;android-37.0' 'build-tools;37.0.0' 'ndk;30.0.16248370' 'cmake;4.1.2'
    gas="$upstream/buildscripts/sdk/bin/gas-preprocessor.pl"
    curl --fail --location --proto '=https' --proto-redir '=https' --max-time 120 \
        'https://raw.githubusercontent.com/FFmpeg/gas-preprocessor/ac1836309c2e77023c228b7184485597286289d3/gas-preprocessor.pl' -o "$gas"
    printf '%s  %s\n' 7124d70cdecba7c5612f9a71fbf3f28514dd9c2ca3022f58ad793f88bb925fcf "$gas" | shasum -a 256 -c -
    chmod +x "$gas"
    {
        java -version
        meson --version
        cmake --version
        ninja --version
        dpkg-query -W autoconf automake libtool pkgconf meson ninja-build nasm gperf python3-jinja2
        cat "$ANDROID_HOME/ndk/30.0.16248370/source.properties"
    } > "$out/toolchain.txt" 2>&1
    cd "$upstream/buildscripts"
    bash -e include/download-deps.sh
    bash patch.sh
    # Require the exact reviewed HTTP afterimage before any native compilation.
    [[ $(git -C deps/ffmpeg rev-parse HEAD) == 946fcce07b6dcd0331c8cc609192aeff5e1924f8 ]] || exit 1
    printf '%s  %s\n' d4ec29f40e9302f6bb1abedcbd0c5adf87c20b11236a8ad8aff42fea46616802 \
        deps/ffmpeg/libavformat/http.c | shasum -a 256 -c -
    # Layout and actual TLS producer changes must come from the same full source build.
    printf '%s  %s\n' \
        14e3acc5a6715d48edb3dadb2f5248deaa38c36b30ff763e9be4b89edf2b1a0c deps/ffmpeg/libavformat/url.h \
        16bf38abc7a5b5586268c71723041df2b90b7b99a8fbaeb06c8b20926534fe7a deps/ffmpeg/libavformat/avio.c \
        cc9c11534ff5017d5931bb06788f95f10e9809953e5ec1cc03114a3a877cdcce deps/ffmpeg/libavformat/tcp.c \
        0f07be92b62cefb53c6db5552951c658c756fac36ce22b03ccca8a044f1911f2 deps/ffmpeg/libavformat/tls.c \
        257c76932de48ed84bf849a44ca9ea6fe85adb93a26bb4f7112b19000084ff19 deps/ffmpeg/libavformat/tls.h \
        b04fb28df85f1ef3883b4927d0bfab4477f400128c122f794ff13b4b9c85bebc deps/ffmpeg/libavformat/tls_securetransport.c \
        7099664b2c15a784db38a0db7956f718862201ef6c02d27779b2fc654b44aeac deps/ffmpeg/libavformat/tls_mbedtls.c \
        a8c74f929942423550dba65f738fd4d2ad2d63d975665f64e6da1b00f5001645 deps/ffmpeg/libavformat/vortx_reference_reader.inc \
        1000225eb4660e936bdf5984b62c6cbd7ae2c747472e55aabb6dc620d4f386dc deps/ffmpeg/libavformat/vortx_reference_pin.h \
        adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87 deps/ffmpeg/libavformat/vortx_reference_reader_stream.h | shasum -a 256 -c -
    [[ $(git -C deps/mpv rev-parse HEAD) == 8c67647b50059406c5c0444903597281b81516cf ]] || exit 1
    printf '%s  %s\n' \
        e5f08305137ed3f7b28fa24ee431e19222153a6cfed10f12d109b92da55567a4 deps/mpv/demux/demux.c \
        bee52b38477ce7c8efd9a50e778f720ea8c9eec577afab29998682c26ffe75e2 deps/mpv/demux/demux.h \
        d945ae1699d383c3d645d2ab963abb166fd0c19f10a4ab144505e7fbfb0408fb deps/mpv/demux/demux_lavf.c \
        6947238ab25a71d7de74d75ce9c2e4590ad0d83e1e9c8ca898d561f74461df7e deps/mpv/demux/reference_reader.c \
        4894866fc49f8ab7af9f4d5b3a29bfc7d1ac4153483fd04354db3e89e55cad03 deps/mpv/demux/reference_reader.h \
        6b21e2b72c87c49d609fdfca0252c32635d50054abe4eaae33340fc5b7da1a9c deps/mpv/demux/reference_reader_internal.h \
        b8c78d8239af8c968c53ca83f601f91e521090d6d2d7522101fb68b332074940 deps/mpv/stream/stream_lavf.c \
        dacfde2ba2aacfb07bd959f23e7e6e367125dbde47d4a31b36c21c24eba0c38b deps/mpv/stream/reference_source.h \
        adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87 deps/mpv/stream/vortx_reference_reader_stream.h \
        316e9aca3c4960701eab42e62ca1005379e082e1a21b4788b7581811ca360147 deps/mpv/meson.build \
        242697e6be2bd2e10272679a3d618f07a07138b7a15b3b94cdbfdf521599bb25 deps/mpv/player/owned_subtitle.c \
        8d2f0ee5c972d1da69213cd80fbe249bcf3f19d9de8a2ef87e241d2f08f06284 deps/mpv/player/owned_subtitle.h \
        661388627775cf58dec688388d36aba9e457b0f3e97d349a91033f3f5f1f6e3b deps/mpv/player/command.c \
        4402bcd094a80a769479320ac852c329acb39781fa8fc478d0ed79af10ae19e2 deps/mpv/player/reference_owned.inc \
        63c498dd4bdc9fa9c6d128f1be8f37d7cdf377c853888342897f6639601662f6 deps/mpv/sub/owned_text_slot.h \
        9b57ccd08e5777e2bc644cbf8001df0301552edf5cf24e36185a54f4cd667195 deps/mpv/sub/osd.c \
        6e2137b90fe7f2915a528bdd83426448afbd099229f65342dbfd85d1ba1d347b deps/mpv/sub/dec_sub.c \
        cb9b5af7c3f87a8dda584617c1f20a90a9331182d54a50c7e5fb8e64e74b384f deps/mpv/sub/dec_sub.h | shasum -a 256 -c -
    for private_header in deps/ffmpeg/libavformat/vortx_http_request.h deps/mpv/stream/vortx_http_request.h; do
        [[ -f "$private_header" && ! -L "$private_header" ]] || exit 1
        printf '%s  %s\n' d7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9 "$private_header" | shasum -a 256 -c -
    done
    cmp -s deps/ffmpeg/libavformat/vortx_http_request.h deps/mpv/stream/vortx_http_request.h
    cmp -s deps/ffmpeg/libavformat/vortx_reference_reader_stream.h deps/mpv/stream/vortx_reference_reader_stream.h
    # Retain exactly the reviewed native source/patch state before any compiler runs.
    git -C deps/ffmpeg diff --binary > "$out/ffmpeg-applied.patch"
    cp deps/ffmpeg/libavformat/tls_mbedtls_vortx_distrust.h "$out/tls_mbedtls_vortx_distrust.h"
    # Includes non-Git Lua/libunibreak and submodule contents; captured before generated build files.
    tar -czf "$out/android-corresponding-sources.tar.gz" --exclude=.git -C "$upstream/buildscripts" deps
    export cores=2 CMAKE_BUILD_PARALLEL_LEVEL=2
    for arch in armv7l arm64 x86_64; do bash -e build.sh --arch "$arch" mpv; done
    cd "$upstream"
    # assemble only: no signing credentials, Maven publication, tests or upload to any package registry.
    ./gradlew --no-daemon --max-workers=2 :libmpv:assembleRelease
    cp libmpv/build/outputs/aar/libmpv-release.aar "$out/libmpv-release-candidate.aar"
    python3 "$root/scripts/native-media-hosted/evidence.py" android "$out" "$upstream" "$ANDROID_HOME"
    # Source manifests are not a substitute for corresponding-source packaging at public distribution.
    tar -czf "$out/android-build-configs.tar.gz" -C buildscripts/deps/ffmpeg \
        _build/ffbuild/config.mak _build/config.h \
        _build-arm64/ffbuild/config.mak _build-arm64/config.h \
        _build-x64/ffbuild/config.mak _build-x64/config.h
fi
