#!/usr/bin/env python3
"""Offline exact-profile candidate packaging; no build, extraction, inspection or promotion."""
import argparse
import base64
import binascii
from collections import deque
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import plistlib
import re
import shlex
import stat
import subprocess
import tarfile
import unicodedata
import zipfile

CHUNK, MIB = 1024 * 1024, 1024 * 1024
GIT = Path("/usr/bin/git")  # Do not execute a producer-controlled/PATH-selected command.
PROFILE = "original-http-sparse-native-reference-reader-v2-dependencies-openssl410-crypto-subtitles-bluray150-candidate"
PROFILE_MANIFEST_SHA256 = "673870ca9ccd507d37862cc81e6dc5cd3b37f173c1d6c2822b2d8b9e77a2f15c"
NATIVE_UPGRADES_PROFILE = "native-upgrades-openssl410-crypto-subtitles-bluray150-v6"
SHADERC_WRAPPER_VERSION = "2026.1.0"
SHADERC_REMOTE_URL = "https://github.com/mpvkit/libshaderc-build/releases/download/2026.1.0/Libshaderc_combined.xcframework.zip"
SHADERC_SOURCE_SPECS = (
    ("libshaderc.zip", 124322785,
     "d213fa58de7886846fb8cb713ea5213b77654a3ad2ba3d89b1f2fe367d945ba5"),
    ("Libshaderc_combined.xcframework.zip", 61246203,
     "77a3d355993cf53843a394bf58262dab99cdd93f9e6fe31d5ca6e55b03866ce1"),
)
SHADERC_MATRIX = (
    ("ios-arm64", "arm64", "ios"),
    ("ios-arm64_x86_64-simulator", "arm64", "isimulator"),
    ("ios-arm64_x86_64-simulator", "x86_64", "isimulator"),
    ("tvos-arm64_arm64e", "arm64", "tvos"),
    ("tvos-arm64_arm64e", "arm64e", "tvos"),
    ("tvos-arm64_x86_64-simulator", "arm64", "tvsimulator"),
    ("tvos-arm64_x86_64-simulator", "x86_64", "tvsimulator"),
    ("macos-arm64_x86_64", "arm64", "macos"),
    ("macos-arm64_x86_64", "x86_64", "macos"),
)
OPENSSL_BOM_PROFILE = "openssl-4.1.0-source-v1"
OPENSSL_SOURCE_VERSION = "4.1.0"
OPENSSL_SOURCE_SHA = "2f4e3a5fec05394068cfa7379890ab552483a8a6"
OPENSSL_SOURCE_TAG = "openssl-4.1.0"
OPENSSL_ROOT = "openssl-4.1.0-2f4e3a5f"
OPENSSL_OUTPUT_ROOT = "openssl"
OPENSSL_GITLINKS = {
    "cloudflare-quiche": "7ab6a55cfe471267d61e4d28ba43d41defcd87e0",
    "gost-engine": "74b1f4fddbc2d6de969815b1992ddc1ae7c643fe",
    "krb5": "784c38f50e70a739400cdd3f2620bac2e2788e6c",
    "oqs-provider": "c5fb439e4ffd947f24c139232fb04fd71fe3c29e",
    "pkcs11-provider": "eb157e48066fdb2b5735f6f327ed7ae65c51eb6f",
    "pyca-cryptography": "e83bbe58e373536636baaa7c33a64a6e8f04c953",
    "python-ecdsa": "4096fa01715929e08b97e73f3173aee9d57f2a3f",
    "fuzz/corpora": "7e3fb43a75c55275759e8bdb5c32de4b50978eab",
    "tlsfuzzer": "61f45d9701294fd87ef92d2a7e3dfb076653a562",
    "tlslite-ng": "77ef321dde1a9e6bcf94d73c80f8789a770d8031",
    "wycheproof": "3fa63dd0344abb611f1fb1d77e119938603ea230",
}
OPENSSL_CONFIGURE_FLAGS = ("no-async", "no-shared", "no-dso", "no-tests", "no-apps", "no-docs")
OPENSSL_PKG_LIBRARIES = ("libmpv", "FFmpeg", "libshaderc", "vulkan", "lcms2", "libdovi",
                         "openssl", "libunibreak", "libfreetype", "libfribidi", "libharfbuzz",
                         "libass", "libsmbclient", "libplacebo", "libdav1d", "gmp", "nettle",
                         "gnutls", "libuchardet", "libbluray", "libluajit", "libuavs3d")
OPENSSL_MATRIX = (
    ("ios", "arm64", "vortx-apple-ios-arm64", "ios-arm64"),
    ("isimulator", "arm64", "vortx-apple-isimulator-arm64", "ios-arm64_x86_64-simulator"),
    ("isimulator", "x86_64", "vortx-apple-isimulator-x86_64", "ios-arm64_x86_64-simulator"),
    ("tvos", "arm64", "vortx-apple-tvos-arm64", "tvos-arm64_arm64e"),
    ("tvos", "arm64e", "vortx-apple-tvos-arm64e", "tvos-arm64_arm64e"),
    ("tvsimulator", "arm64", "vortx-apple-tvsimulator-arm64", "tvos-arm64_x86_64-simulator"),
    ("tvsimulator", "x86_64", "vortx-apple-tvsimulator-x86_64", "tvos-arm64_x86_64-simulator"),
    ("macos", "arm64", "vortx-apple-macos-arm64", "macos-arm64_x86_64"),
    ("macos", "x86_64", "vortx-apple-macos-x86_64", "macos-arm64_x86_64"),
)
OPENSSL_ENV_KEYS = ("CC", "CXX", "CFLAGS", "CPPFLAGS", "CXXFLAGS", "ASMFLAGS",
                    "LDFLAGS", "PKG_CONFIG_LIBDIR", "PKG_CONFIG_PATH", "PATH",
                    "CURRENT_ARCH", "LC_CTYPE", "AR", "ARFLAGS", "RANLIB",
                    "MAKEFLAGS", "CMAKE_BUILD_PARALLEL_LEVEL", "CARGO_BUILD_JOBS")
OPENSSL_EFFECTIVE_KEYS = ("target", "CC", "CFLAGS", "CPPFLAGS", "CXX", "CXXFLAGS",
                           "LDFLAGS", "AR", "ARFLAGS", "RANLIB", "CROSS_COMPILE",
                           "cflags", "cppflags", "lflags", "lib_cppflags")
OPENSSL_DEFAULT_PATH = "/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
OPENSSL_OUTPUTS = (("libssl.a", "lib/libssl.a", 512 * MIB),
                   ("libcrypto.a", "lib/libcrypto.a", 512 * MIB),
                   ("opensslv.h", "include/openssl/opensslv.h", MIB))
RECIPE_PATHS = frozenset("""
scripts/assemble-mpvkit-dvfel.sh scripts/build-mpvkit-dvfel.sh
scripts/ffmpeg-response-witness.patch scripts/ffmpeg-response-generation.patch
scripts/mpv-completed-response-observation.patch
scripts/ffmpeg-sent-request-evidence.patch scripts/mpv-sent-request-evidence.patch
scripts/mpv-bounded-prefix-request.patch
scripts/ffmpeg-original-transport-evidence.patch scripts/mpv-original-transport-evidence.patch
scripts/ffmpeg-finite-reference-reader.patch scripts/mpv-finite-reference-reader.patch
scripts/mpv-coreaudio-hotplug-lifecycle.patch scripts/mpv-http-seek-interrupt.patch
scripts/mpv-moltenvk-resize.patch scripts/mpv-original-http-cancel-lifetime.patch
scripts/mpv-original-http-reference-recipe.patch scripts/mpv-original-local-file-witness.patch
scripts/mpv-owned-subtitle-constructor.patch scripts/mpv-owned-subtitle-publication.patch
scripts/mpv-upstream-audio-layout-bounds.patch scripts/mpv-upstream-edition-title-lifetime.patch
scripts/mpv-upstream-gpu-next-seek-reset.patch scripts/mpvkit-dvfel.patch
scripts/native-media-hosted/android-assembly.patch
scripts/native-media-hosted/android/buildscripts/include/depinfo.sh
scripts/native-media-hosted/android/buildscripts/include/download-deps.sh
scripts/native-media-hosted/android/buildscripts/include/download-lua.sh
scripts/native-media-hosted/android/buildscripts/patch.sh
scripts/native-media-hosted/android/buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch
scripts/native-media-hosted/build.sh scripts/native-media-hosted/evidence.py
scripts/verify-mpvkit-dvfel-apple-tls.sh scripts/verify-mpvkit-dvfel-artifacts.sh
scripts/native-media-hosted/android/buildscripts/include/download-libunibreak.sh
scripts/native-media-hosted/android/buildscripts/scripts/ffmpeg.sh
""".split())
INPUT_MANIFEST = "scripts/native-media-hosted/inputs.sha256"
WORKFLOW = ".github/workflows/native-media-candidates.yml"
OUTPUTS = {"apple": {"apple-candidate.tar.gz", "apple-original-library-zips.tar", "apple-artifact-gate.txt",
                      "apple-shaderc-original-all.zip", "apple-shaderc-original-xcframework.zip",
                      "apple-shaderc-inputs.json", "apple-openssl-builds.json",
                      "apple-crypto-source-gmp.tar.xz", "apple-crypto-source-nettle.tar.gz",
                      "apple-crypto-source-gnutls.tar.xz", "apple-crypto-release-sources.tar",
                      "apple-crypto-release-sources.json", "apple-crypto-chain.json",
                      "apple-subtitle-source-unibreak.tar.gz",
                      "apple-subtitle-source-fribidi.tar.xz",
                      "apple-subtitle-source-harfbuzz.tar.xz", "apple-bluray-source.tar.xz",
                      "apple-subtitle-release-sources.tar",
                      "apple-subtitle-release-sources.json", "apple-subtitle-builds.json"},
           "android": {"libmpv-release-candidate.aar", "android-artifact-evidence.json",
                       "android-corresponding-sources.tar.gz", "android-build-configs.tar.gz"}}
PINS = {"mpv": "8c67647b50059406c5c0444903597281b81516cf",
        "ffmpeg": "946fcce07b6dcd0331c8cc609192aeff5e1924f8",
        "libplacebo": "275f7dea27924cdd34ab2e6c20e448b6bc4fab65"}
ANDROID_PINS = dict(PINS, mbedtls="068ff080b369adfac81509f9b57b2afabaf82dc5",
                    dav1d="54706fc6bc0cdecab7e9593974a4039cc038fca7",
                    freetype="0a0221a1347e2f1e07c395263540026e9a0aa7c7",
                    fribidi="b93119f5fdc7ea47672cc304c1455ffa6dfe7536",
                    harfbuzz="35f656a1a2bab548ea7f54a6dd3fba2b09680d14",
                    libxml2="96498992efa48d52b0e8b83058bd88dbdaf153c1",
                    fontconfig="0f236723a3c26bd9745e32ba2b3ee21e5f809295",
                    libass="4a05d8127f525943ebf45fdc6497c9e665947f0d")
WRAPPER = {"apple": "2103893078c5e339073b11737b86f7f22b9c4491",
           "android": "fcf6745703dc1265bca88f12fee8fc355ddf251e"}
# Full-file postcomposition hashes retain cancellation plus owned observation.
CANCEL = {"misc/thread_tools.c": "151102f6fc8584e119317e3526502bbae492353b1c312ae4a8f3aecaee8bdab9",
          "misc/thread_tools.h": "62c7ea467ef911d9665cc4d7ffe28b2b09ea03f152326964056f62dab24acc7f",
          "stream/stream_lavf.c": "650bf7b94771e70a6b934b21e362bc77f66ceb1d23ee684d2b169c6e694544a5",
          "stream/reference_source.h": "1f606a977b55f18fdb9f9fc7f5e4549b5774bef10a06c33b06ab945531909b29",
          "stream/vortx_http_request.h": "d7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9"}
RESPONSE = {
    "libavformat/http.c": "d1301474b4389256f9862565a399a8888366a68d2e3630338848289fe8a9c831",
    "libavformat/url.h": "14e3acc5a6715d48edb3dadb2f5248deaa38c36b30ff763e9be4b89edf2b1a0c",
    "libavformat/avio.c": "16bf38abc7a5b5586268c71723041df2b90b7b99a8fbaeb06c8b20926534fe7a",
    "libavformat/tcp.c": "cc9c11534ff5017d5931bb06788f95f10e9809953e5ec1cc03114a3a877cdcce",
    "libavformat/tls.c": "0f07be92b62cefb53c6db5552951c658c756fac36ce22b03ccca8a044f1911f2",
    "libavformat/tls.h": "257c76932de48ed84bf849a44ca9ea6fe85adb93a26bb4f7112b19000084ff19",
    "libavformat/tls_securetransport.c": "b04fb28df85f1ef3883b4927d0bfab4477f400128c122f794ff13b4b9c85bebc",
}
READER_FFMPEG_CREATED = {
    "libavformat/vortx_reference_reader.inc": "a8c74f929942423550dba65f738fd4d2ad2d63d975665f64e6da1b00f5001645",
    "libavformat/vortx_reference_pin.h": "e0ccea8466e28169d0882fd664081baa09d3a39626d5351a31804b6dc4bb4e94",
    "libavformat/vortx_reference_reader_stream.h": "adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87",
    "libavformat/vortx_reference_sparse.h": "fa5f8e163d9368b30db6a9c7f0c69ed36e91d6a23ca409b7ba468c7d2282751c",
    "libavformat/vortx_reference_sparse.inc": "0e3201c26657765a1e6e7a15fe9db1bcac5cf96a8cb7e9d691533e0f4bf3f15f",
}
READER_MPV = {
    "demux/demux.c": "f5a4cd7bf4e29902ef965e43027755c19ded1423129a3fbe9808ad89e64c0e1c",
    "demux/demux.h": "bee52b38477ce7c8efd9a50e778f720ea8c9eec577afab29998682c26ffe75e2",
    "demux/demux_lavf.c": "d945ae1699d383c3d645d2ab963abb166fd0c19f10a4ab144505e7fbfb0408fb",
    "demux/reference_reader.c": "222ec8920d38971d386470d8172e45a89a52350eb352124ddf954be9409b792b",
    "demux/reference_reader.h": "59d669fb370bc12f01856031b8f9d908eb1d50a927d955ecb1fe0b4484c2407d",
    "demux/reference_reader_internal.h": "ded5dc02785e98934a966de87df4337e4c6aed0275bce26da2017883917f4c8d",
    "demux/demux_mkv.c": "1b7cc4d40efa0620bd10669dc44a7d6b32d0236e2ae2be3510a3bac0829e39c2",
    "demux/reference_mkv.h": "38df7814efc3aa7434e5afe269406a989ab6a5fa22a200aa28149a3ab68e46d9",
    "demux/reference_mkv_reader.c": "3ac442e8ff489d11f4a5df6b86f8d964457753f66ce5f3eb530d7db3746b6f7c",
    "demux/reference_mkv_sparse.h": "4b013b6e794257a576bc01f6becb9d120cb878e0851b2e0bdd2225dcf7b18df2",
    "demux/reference_mkv_sparse_planner.c": "e36c6a879138b26e125f647c9254b41674b22b338ab12838376fe8d38bdae056",
    "demux/reference_mkv_sparse_planner.h": "c88597a42186ceb81f45f1f44b3734469cf06889ea3bf91e4853b861943e84fe",
    "demux/reference_mkv_sparse_worker.c": "5deae68bf3c680aa4898ca6c8e0c0e76aa4628bffa693d0ae2d6dd287d9a5e64",
    "stream/vortx_reference_sparse.h": "fa5f8e163d9368b30db6a9c7f0c69ed36e91d6a23ca409b7ba468c7d2282751c",
    "stream/stream_lavf.c": CANCEL["stream/stream_lavf.c"],
    "stream/reference_source.h": CANCEL["stream/reference_source.h"],
    "stream/vortx_reference_reader_stream.h": "adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87",
    "player/owned_subtitle.c": "014b93845488ef873a4d29c4c8dd4ada69d244626e7a7b38bac82809989c6b29",
    "player/owned_subtitle.h": "8d2f0ee5c972d1da69213cd80fbe249bcf3f19d9de8a2ef87e241d2f08f06284",
    "player/command.c": "e7a19f392bb69e9d208382f157234ac3c908d22a0d4afee5f372ee8f9d33dade",
    "player/reference_owned.inc": "23ac96bfedefcab2234afa2a6f329a36fac6735effca6798b4043d777b060217",
    "sub/owned_text_slot.h": "63c498dd4bdc9fa9c6d128f1be8f37d7cdf377c853888342897f6639601662f6",
    "sub/osd.c": "9b57ccd08e5777e2bc644cbf8001df0301552edf5cf24e36185a54f4cd667195",
    "sub/dec_sub.c": "6e2137b90fe7f2915a528bdd83426448afbd099229f65342dbfd85d1ba1d347b",
    "sub/dec_sub.h": "cb9b5af7c3f87a8dda584617c1f20a90a9331182d54a50c7e5fb8e64e74b384f",
}
READER_MPV_MESON = {
    "apple": "c2abd8f89a772717fdd4bcdbb4a2236d9fb28eca029acfe84bf12f7333df5a55",
    "android": "32c9a1321883f6a980652173061c9c01727ef83b7a0dfe6e8e3a22364b753d47",
}
MBEDTLS = {"apple": "77d72cb88fc662b984f051335b356abc3e76e97ce39302dde6961ecdbba7a332",
           "android": "7099664b2c15a784db38a0db7956f718862201ef6c02d27779b2fc654b44aeac"}
GITLINKS = {"3rdparty/Vulkan-Headers": "74d8a6cb930c68ef617b202c3ff3c59d919e086b",
            "3rdparty/fast_float": "97b54ca9e75f5303507699d27c6b4f4efe4641a1",
            "3rdparty/glad": "73db193f853e2ee079bf3ca8a64aa2eaf6459043",
            "3rdparty/jinja": "15206881c006c79667fe5154fe80c01c65410679",
            "3rdparty/markupsafe": "297fc8e356e6836a62087949245d09a28e9f1b13",
            "demos/3rdparty/nuklear": "242f35efa067a46c595645eeda7b1771ea1f83b1"}
LIBRARIES = "Libmpv Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale Libplacebo lcms2 Libdav1d Libssl Libcrypto gmp nettle hogweed gnutls Libsmbclient Libunibreak Libfribidi Libharfbuzz Libbluray".split()
UNSUFFIXED_LIBRARIES = frozenset(("Libplacebo", "lcms2", "Libdav1d", "Libssl", "Libcrypto",
                                  "gmp", "nettle", "hogweed", "gnutls", "Libsmbclient",
                                  "Libunibreak", "Libfribidi", "Libharfbuzz", "Libbluray"))

CRYPTO_SOURCE_PROFILE = "gmp630-nettle400-gnutls3813-release-sources-v1"
CRYPTO_CHAIN_PROFILE = "native-upgrades-crypto-chain-v1"
CRYPTO_PACKAGE_POLICY = "assembled-mpvkit-relative-artifact-closure-v1"
CRYPTO_SOURCE_OUTPUTS = (
    {"id": "gmp", "version": "6.3.0", "archiveRoot": "gmp-6.3.0",
     "filename": "gmp-6.3.0.tar.xz", "compression": "xz", "bytes": 2094196,
     "sha256": "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898",
     "sourceRoot": "gmp-6.3.0-a3c2b802", "url": "https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz",
     "output": "apple-crypto-source-gmp.tar.xz"},
    {"id": "nettle", "version": "4.0", "archiveRoot": "nettle-4.0",
     "filename": "nettle-4.0.tar.gz", "compression": "gz", "bytes": None,
     "sha256": "3addbc00da01846b232fb3bc453538ea5468da43033f21bb345cb1e9073f5094",
     "sourceRoot": "nettle-4.0-3addbc00", "url": "https://ftp.gnu.org/gnu/nettle/nettle-4.0.tar.gz",
     "output": "apple-crypto-source-nettle.tar.gz"},
    {"id": "gnutls", "version": "3.8.13", "archiveRoot": "gnutls-3.8.13",
     "filename": "gnutls-3.8.13.tar.xz", "compression": "xz", "bytes": 7275324,
     "sha256": "ffed8ec1bf09c2426d4f14aae377de4753b53e537d685e604e99a8b16ca9c97e",
     "sourceRoot": "gnutls-3.8.13-ffed8ec1",
     "url": "https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.13.tar.xz",
     "output": "apple-crypto-source-gnutls.tar.xz"},
)
CRYPTO_CAPTURE_TAR_OUTPUT = "apple-crypto-release-sources.tar"
CRYPTO_CAPTURE_RECEIPT_OUTPUT = "apple-crypto-release-sources.json"
CRYPTO_CHAIN_OUTPUT = "apple-crypto-chain.json"
CRYPTO_MAX_ARCHIVE = 8 * MIB
CRYPTO_MAX_UNCOMPRESSED = 512 * MIB
CRYPTO_MAX_MEMBERS = 100000
CRYPTO_MAX_MEMBER = 64 * MIB
CRYPTO_MAX_RECEIPT = 64 * MIB
CRYPTO_MAX_PATH = 4096
CRYPTO_MAX_MTIME = 4102444800
CRYPTO_MAX_MTIME_NS = CRYPTO_MAX_MTIME * 1000000000
CRYPTO_MAX_INLINE_RAW = 16 * MIB
CRYPTO_MAX_READER_JSON = 32 * MIB
CRYPTO_MAX_METADATA_JSON = 8 * MIB
CRYPTO_INLINE_BODY_POLICY = {
    "encoding": "canonical-rfc4648-base64", "recordMaxRawBytes": 4 * MIB,
    "configurationMaxRawBytes": 16 * MIB, "sdkMaxRawBytes": 4 * MIB,
    "aggregateMaxRawBytes": 16 * MIB, "readerMaxJSONBytes": 32 * MIB,
    "metadataMaxJSONBytes": 8 * MIB, "receiptMaxBytes": 64 * MIB,
}
CRYPTO_ACCEPTANCE = "ARCHIVE_AND_PUBLIC_SYMBOL_CORRESPONDENCE_ONLY"
CRYPTO_OPEN_GATES = (
    "mandatory companion release-source authentication/capture must be integrated by the profile caller",
    "actual execution receipts for the captured configured build commands",
    "consumer header/ABI compile and final application link",
    "explicit static gnutls_global_init consumer behavior",
    "physical SMB authentication, signing, encryption and IO behavior",
    "signing, package publication and release acceptance",
)
CRYPTO_INSTALLED_HELPERS = {
    "Sources/BuildScripts/crypto-release-sources.py":
        "3c310499d446eed72eb16521b27c16f718f23e7ab1472dec1fad79cc40ed1537",
    "Sources/BuildScripts/crypto-chain-gate.py":
        "be5bc18e00b9b1cb4c4c5889b0935b4cd5d1538a98bcfb985ccbfcb208825a9b",
}
CRYPTO_MATRIX = (
    ("ios", "arm64", "ios-arm64"),
    ("isimulator", "arm64", "ios-arm64_x86_64-simulator"),
    ("isimulator", "x86_64", "ios-arm64_x86_64-simulator"),
    ("tvos", "arm64", "tvos-arm64_arm64e"),
    ("tvos", "arm64e", "tvos-arm64_arm64e"),
    ("tvsimulator", "arm64", "tvos-arm64_x86_64-simulator"),
    ("tvsimulator", "x86_64", "tvos-arm64_x86_64-simulator"),
    ("macos", "arm64", "macos-arm64_x86_64"),
    ("macos", "x86_64", "macos-arm64_x86_64"),
)
CRYPTO_LIBRARIES = (
    ("gmp", "gmp", "libgmp.a", "gmp"),
    ("nettle", "nettle", "libnettle.a", "nettle"),
    ("hogweed", "nettle", "libhogweed.a", "hogweed"),
    ("gnutls", "gnutls", "libgnutls.a", "gnutls"),
    ("Libsmbclient", "libsmbclient", "libsmbclient.a", "Libsmbclient"),
)
CRYPTO_VERSIONS = {"gmp": "6.3.0", "nettle": "4.0", "hogweed": "4.0",
                   "gnutls": "3.8.13", "Libsmbclient": "4.15.13-2512"}
CRYPTO_SOURCE_PINS = {
    "gmp": {"archive": "gmp-6.3.0.tar.xz",
            "sha256": "a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898"},
    "gnutls": {"tag": "3.8.13", "commit": "b390d80208ed60f1b33ad899475951a8efb40ccd",
               "archive": "gnutls-3.8.13.tar.xz",
               "sha256": "ffed8ec1bf09c2426d4f14aae377de4753b53e537d685e604e99a8b16ca9c97e"},
    "nettle": {"tag": "nettle_4.0_release_20260205",
               "commit": "bf13ee9e5024014cfa6d2fc097923afe087e63ba",
               "archive": "nettle-4.0.tar.gz",
               "sha256": "3addbc00da01846b232fb3bc453538ea5468da43033f21bb345cb1e9073f5094"},
}
CRYPTO_SELECTORS = {"gmp": "6.3.0-a3c2b802", "nettle": "4.0-3addbc00",
                    "gnutls": "3.8.13-ffed8ec1"}
CRYPTO_PKG_LIBRARIES = OPENSSL_PKG_LIBRARIES
CRYPTO_DEFAULT_PATH = OPENSSL_DEFAULT_PATH
CRYPTO_PLATFORMS = {
    "ios": ("iphoneos", "ios15.0", "-mios-version-min=15.0", "ios"),
    "isimulator": ("iphonesimulator", "ios15.0-simulator", "-mios-simulator-version-min=15.0", "ios"),
    "tvos": ("appletvos", "tvos15.0", "-mtvos-version-min=15.0", "tvos"),
    "tvsimulator": ("appletvsimulator", "tvos15.0-simulator", "-mtvos-simulator-version-min=15.0", "tvos"),
    "macos": ("macosx", "macos12.0", "-mmacosx-version-min=12.0", "apple"),
}
CRYPTO_CONFIGURE_FLAGS = {
    "gmp": ("--disable-assembly", "--disable-cxx", "--with-pic", "--enable-static",
            "--disable-shared", "--disable-fast-install", "--disable-maintainer-mode"),
    "nettle": ("--disable-assembler", "--disable-openssl", "--disable-gcov", "--disable-documentation",
               "--disable-mini-gmp", "--enable-public-key", "--disable-fat", "--enable-pic",
               "--enable-static", "--disable-shared", "--disable-dependency-tracking"),
    "gnutls": ("--with-included-libtasn1", "--with-included-unistring", "--without-brotli",
               "--without-idn", "--without-p11-kit", "--without-zlib", "--without-zstd",
               "--without-tpm", "--without-tpm2", "--without-leancrypto", "--disable-cxx", "--disable-libdane",
               "--enable-hardware-acceleration", "--disable-openssl-compatibility", "--disable-code-coverage",
               "--disable-doc", "--disable-maintainer-mode", "--disable-manpages", "--disable-nls", "--disable-rpath",
               "--disable-tests", "--disable-tools", "--disable-full-test-suite", "--with-pic", "--enable-static",
               "--disable-shared", "--disable-fast-install", "--disable-dependency-tracking"),
}
CRYPTO_GENERATED = ("Makefile", "config.status", "config.log", "config.h")
CRYPTO_EXTRA_GENERATED = {
    "gmp": ("gmp.h",),
    "nettle": ("config.make", "config.m4", "version.h", "nettle.pc", "hogweed.pc"),
    "gnutls": ("lib/gnutls.pc", "lib/includes/gnutls/gnutls.h"),
}
CRYPTO_INSTALLED_CONFIGURATION = {
    "gmp": {"gmp.h": "include/gmp.h"},
    "nettle": {"version.h": "include/nettle/version.h",
               "nettle.pc": "lib/pkgconfig/nettle.pc", "hogweed.pc": "lib/pkgconfig/hogweed.pc"},
    "gnutls": {"lib/gnutls.pc": "lib/pkgconfig/gnutls.pc",
               "lib/includes/gnutls/gnutls.h": "include/gnutls/gnutls.h"},
}
CRYPTO_MAKE_ARGUMENTS = ("-j1", "AUTOCONF=/usr/bin/false", "AUTOHEADER=/usr/bin/false",
                         "AUTOMAKE=/usr/bin/false", "ACLOCAL=/usr/bin/false")
CRYPTO_REQUIRED_SYMBOLS = {
    "gmp": {"__gmpz_cmp", "__gmpz_init"},
    "nettle": {"nettle_sha256_init", "nettle_version_major"},
    "hogweed": {"nettle_rsa_private_key_init"},
    "gnutls": {"gnutls_check_version", "gnutls_global_init"},
    "Libsmbclient": {"smbc_new_context", "smbc_init_context"},
}
CRYPTO_SMB_PINS = {
    "ios/arm64": (31955240, "1c378176da85ff2386f5c81c903860f8ab258a4e846ddba5bb0febc284f01cd0"),
    "isimulator/arm64": (31948848, "bcb902da9a24ebbd3571bd1d04eeea88b3782e10ad98eee9a2be90e15ddeb795"),
    "isimulator/x86_64": (26473896, "0602a2a2eb8c987677b38a150cdf8b03d0f88f13eb7dd7877cacb4a41d7a5254"),
    "tvos/arm64": (31955264, "4921e4c649589e920e139078219b1a3f01a1fea6cd5bc8b71875d15c09aad252"),
    "tvos/arm64e": (32054336, "0267dfcd4ca93acf1e62bb75bc7509f6640c2367c63da99a3c983ec9ad9d8206"),
    "tvsimulator/arm64": (31948880, "6960f069a31801eb0f0f70eb0efd67239502d5c7081163a9ca38faca226a29fd"),
    "tvsimulator/x86_64": (26474992, "4295cab93d2e40c1234f81a8032d28a0da970643d07cb3c01aaf9b63cd103ef1"),
    "macos/arm64": (31933296, "4f6a36df06ad0e5f36c3106c7c61a1978dbc642a7d27c4f9aceb19540998424f"),
    "macos/x86_64": (27602360, "0c9c03db4279b730b86b505b09c2fedc11b879ee7914ef818c940a38e867ae81"),
}
CRYPTO_SMB_HEADERS = {
    "libsmbclient.h": (125287, "9e0fd2184591f449c79b6bc5b5468443920523ea943440534df04294b940ce1c"),
    "samba/version.h": (421, "d601401634a789612aab95d50295c69713af8fb4ac5addf39dffcc402e978554"),
}

SUBTITLE_SOURCE_PROFILE = "unibreak800-fribidi1017-harfbuzz1500-bluray150-release-sources-v3-pax-ns"
SUBTITLE_SOURCE_OUTPUTS = (
    {"id": "libunibreak", "version": "8.0", "archiveRoot": "libunibreak-8.0",
     "filename": "libunibreak-8.0.tar.gz", "compression": "gz", "bytes": None,
     "maximumBytes": 8 * MIB, "entryPoint": "configure",
     "sha256": "9c4fad6e517338a098373acc9f35579ae2c325e6446666fb9ac2666ba15ceba4",
     "sourceRoot": "libunibreak-8.0-9c4fad6e",
     "url": ("https://github.com/adah1972/libunibreak/releases/download/"
             "libunibreak_8_0/libunibreak-8.0.tar.gz"),
     "output": "apple-subtitle-source-unibreak.tar.gz"},
    {"id": "libfribidi", "version": "1.0.17", "archiveRoot": "fribidi-1.0.17",
     "filename": "fribidi-1.0.17.tar.xz", "compression": "xz", "bytes": 1203284,
     "maximumBytes": 8 * MIB, "entryPoint": "meson.build",
     "sha256": "6949dcde27d41cebad1fd741fcafc36d55a1020d2d872d4a6eb3914caabbada2",
     "sourceRoot": "libfribidi-1.0.17-6949dcde",
     "url": "https://github.com/fribidi/fribidi/releases/download/v1.0.17/fribidi-1.0.17.tar.xz",
     "output": "apple-subtitle-source-fribidi.tar.xz"},
    {"id": "libharfbuzz", "version": "15.0.0", "archiveRoot": "harfbuzz-15.0.0",
     "filename": "harfbuzz-15.0.0.tar.xz", "compression": "xz", "bytes": 20396372,
     "maximumBytes": 32 * MIB, "entryPoint": "meson.build",
     "sha256": "968536e9abd782f07f7954044c0c4020199fcdd39b41d221ec42d2a3be4a44df",
     "sourceRoot": "libharfbuzz-15.0.0-968536e9",
     "url": "https://github.com/harfbuzz/harfbuzz/releases/download/15.0.0/harfbuzz-15.0.0.tar.xz",
     "output": "apple-subtitle-source-harfbuzz.tar.xz"},
    {"id": "libbluray", "version": "1.5.0", "archiveRoot": "libbluray-1.5.0",
     "filename": "libbluray-1.5.0.tar.xz", "compression": "xz", "bytes": 473072,
     "maximumBytes": 473072, "entryPoint": "meson.build",
     "sha256": "f676408e91a5d321abf8b8d4dfdae36205c297dab5c54c3ec519639025f474a2",
     "sourceRoot": "libbluray-1.5.0-f676408e",
     "url": "https://download.videolan.org/pub/videolan/libbluray/1.5.0/libbluray-1.5.0.tar.xz",
     "sourceTransformPolicy": "bluray150-retained-apple-mobile-v1",
     "output": "apple-bluray-source.tar.xz"},
)
SUBTITLE_CAPTURE_TAR_OUTPUT = "apple-subtitle-release-sources.tar"
SUBTITLE_CAPTURE_RECEIPT_OUTPUT = "apple-subtitle-release-sources.json"
SUBTITLE_BUILD_OUTPUT = "apple-subtitle-builds.json"
SUBTITLE_INSTALLED_HELPERS = {
    "Sources/BuildScripts/subtitle-release-sources.py":
        "f082ea41b939e8152a2571636f937e2bb9dcfed57ffd2f1dbe65ed68522800df",
}
SUBTITLE_MATRIX = CRYPTO_MATRIX
SUBTITLE_VERSIONS = {"libunibreak": "8.0", "libfribidi": "1.0.17",
                     "libharfbuzz": "15.0.0", "libbluray": "1.5.0"}
SUBTITLE_SELECTORS = {"libunibreak": "8.0-9c4fad6e",
                      "libfribidi": "1.0.17-6949dcde",
                      "libharfbuzz": "15.0.0-968536e9", "libbluray": "1.5.0-f676408e"}
SUBTITLE_ROUTES = {"libunibreak": "release-autoconf",
                   "libfribidi": "release-meson", "libharfbuzz": "release-meson", "libbluray": "release-meson"}
SUBTITLE_ARGUMENTS = {
    "libbluray": ("--wrap-mode=nodownload", "--force-fallback-for=libudfread",
                  "-Denable_docs=false", "-Denable_tools=false", "-Denable_devtools=false",
                  "-Denable_examples=false", "-Dbdj_jar=disabled", "-Dembed_udfread=true",
                  "-Dfontconfig=disabled", "-Dfreetype=enabled", "-Dlibxml2=enabled"),
    "libunibreak": None,
    "libfribidi": ("--wrap-mode=nodownload", "-Dtests=false", "-Ddocs=false",
                    "-Dbin=false", "-Ddeprecated=true"),
    "libharfbuzz": ("--wrap-mode=nodownload", "-Dauto_features=disabled",
                     "-Dtests=disabled", "-Ddocs=disabled", "-Dutilities=disabled",
                     "-Dintrospection=disabled", "-Dfreetype=disabled",
                     "-Dcoretext=disabled", "-Dglib=disabled", "-Dgobject=disabled",
                     "-Dcairo=disabled", "-Dchafa=disabled", "-Dpng=disabled",
                     "-Dzlib=disabled", "-Dicu=disabled", "-Dgraphite2=disabled",
                     "-Dfontations=disabled", "-Dharfrust=disabled", "-Dkbts=disabled",
                     "-Dwasm=disabled", "-Dgpu_demo=disabled", "-Dbenchmark=disabled",
                     "-Draster=disabled", "-Dvector=disabled", "-Dgpu=disabled",
                     "-Dsubset=disabled"),
}
SUBTITLE_MAKE_ARGUMENTS = ("-j1", "AUTOCONF=/usr/bin/false",
                           "AUTOHEADER=/usr/bin/false", "AUTOMAKE=/usr/bin/false",
                           "ACLOCAL=/usr/bin/false")
SUBTITLE_GENERATED = {
    "libbluray": ("crossFile.meson", "build.ninja", "compile_commands.json",
                  "meson-info/intro-buildoptions.json", "meson-info/intro-dependencies.json",
                  "config.h", "contrib/libudfread/config.h"),
    "libunibreak": ("Makefile", "config.status", "config.log"),
    "libfribidi": ("crossFile.meson", "build.ninja", "compile_commands.json",
                    "meson-info/intro-buildoptions.json",
                    "meson-info/intro-dependencies.json"),
    "libharfbuzz": ("crossFile.meson", "build.ninja", "compile_commands.json",
                     "meson-info/intro-buildoptions.json",
                     "meson-info/intro-dependencies.json"),
}
SUBTITLE_INSTALLED = {
    "libbluray": ("include/libbluray/bluray-version.h", "lib/pkgconfig/libbluray.pc"),
    "libunibreak": ("include/unibreakbase.h", "lib/pkgconfig/libunibreak.pc"),
    "libfribidi": ("include/fribidi/fribidi-config.h", "lib/pkgconfig/fribidi.pc"),
    "libharfbuzz": ("include/harfbuzz/hb-version.h", "lib/pkgconfig/harfbuzz.pc"),
}
SUBTITLE_TARGETS = {"libunibreak": "Libunibreak", "libfribidi": "Libfribidi",
                    "libharfbuzz": "Libharfbuzz", "libbluray": "Libbluray"}
SUBTITLE_LIBRARIES = (
    ("Libunibreak", "libunibreak", "libunibreak.a", "Libunibreak"),
    ("Libfribidi", "libfribidi", "libfribidi.a", "Libfribidi"),
    ("Libharfbuzz", "libharfbuzz", "libharfbuzz.a", "Libharfbuzz"),
    ("Libbluray", "libbluray", "libbluray.a", "Libbluray"),
)
SUBTITLE_HEADERS = {"libunibreak": "unibreakbase.h",
                    "libfribidi": "fribidi/fribidi-config.h",
                    "libharfbuzz": "harfbuzz/hb-version.h", "libbluray": "libbluray/bluray-version.h"}
SUBTITLE_MAX_RECORD = 16 * MIB
SUBTITLE_MAX_BODY = 8 * MIB
SUBTITLE_MAX_RECORD_BODIES = 8 * MIB
SUBTITLE_MAX_INLINE_RAW = 32 * MIB
SUBTITLE_MAX_RECEIPT = 64 * MIB
BLURAY_SOURCE_TRANSFORMS = {
    'meson.build': {
        'beforeSHA256': '5f707fc923e4d2b9c845adff896b7c1734ea257e4a5fe77c67ddc856caaf73a0',
        'afterSHA256': '97eab258bb691ded280ebeff128c5caf4f1807b3a78f76721bb30034d1c715cd',
        'replacements': [{'before': "    extra_dependencies += dependency('appleframeworks',\n        modules: ['CoreFoundation', 'DiskArbitration'])",
                          'after': "    extra_dependencies += dependency('appleframeworks', modules: ['CoreFoundation'])\n    if cc.get_define('TARGET_OS_OSX', prefix: '#include <TargetConditionals.h>') == '1'\n        extra_dependencies += dependency('appleframeworks', modules: ['DiskArbitration'])\n    endif"}]},
    'src/file/mount_darwin.c': {
        'beforeSHA256': '055d80dbbe5f4b64837347fce756d9538f6254ddbb7d5608cc26f1bb7caee4df',
        'afterSHA256': 'f439c23dd34529ad613909114e6dedd0f339550ba90b3ea243350333ab33c483',
        'replacements': [
            {'before': '#include <DiskArbitration/DADisk.h>', 'after': '#include <TargetConditionals.h>\n#if TARGET_OS_OSX\n#include <DiskArbitration/DADisk.h>\n#endif'},
            {'before': '    DASessionRef session = DASessionCreate(kCFAllocatorDefault);', 'after': '#if TARGET_OS_OSX\n    DASessionRef session = DASessionCreate(kCFAllocatorDefault);'},
            {'before': '    return result;\n}', 'after': '#endif\n    return result;\n}'}]},
    'src/libbluray/bdj/bdj.c': {
        'beforeSHA256': '5fbe6d6bde9ac48a89399553d1cd1bcbfd7cffbcbc6f88aa6f010e91f1307e1f',
        'afterSHA256': 'a5111e63b6b557820f395cbcd140d070c5181187b07ddf9fe5085fd0f33f78aa',
        'replacements': [
            {'before': '#include <unistd.h>\n#endif', 'after': '#include <unistd.h>\n#include <TargetConditionals.h>\n#endif'},
            {'before': 'static char *_java_home_macos()\n{', 'after': 'static char *_java_home_macos()\n{\n#if TARGET_OS_OSX'},
            {'before': '    return result;\n}\n#undef MACOS_JAVA_HOME', 'after': '    return result;\n#else\n    // Match the retained Apple wrapper: mobile targets cannot launch java_home.\n    return NULL;\n#endif\n}\n#undef MACOS_JAVA_HOME'}]},
}


def bluray_source_body(pin, name, body):
    if pin['id'] != 'libbluray' or name not in BLURAY_SOURCE_TRANSFORMS:
        return body
    if pin.get('sourceTransformPolicy') != 'bluray150-retained-apple-mobile-v1':
        raise ValueError('libbluray source transform policy differs')
    policy = BLURAY_SOURCE_TRANSFORMS[name]
    if hashlib.sha256(body).hexdigest() != policy['beforeSHA256']:
        raise ValueError('libbluray source transform preimage differs')
    for edit in policy['replacements']:
        before, after = edit['before'].encode(), edit['after'].encode()
        if body.count(before) != 1:
            raise ValueError('libbluray source transform anchor is not unique')
        body = body.replace(before, after)
    if hashlib.sha256(body).hexdigest() != policy['afterSHA256']:
        raise ValueError('libbluray source transform afterimage differs')
    return body



CREATED_MPV = {"player/owned_subtitle.c": READER_MPV["player/owned_subtitle.c"],
               "player/owned_subtitle.h": READER_MPV["player/owned_subtitle.h"],
               "player/reference_owned.inc": READER_MPV["player/reference_owned.inc"],
               "demux/reference_reader.c": READER_MPV["demux/reference_reader.c"],
               "demux/reference_reader.h": READER_MPV["demux/reference_reader.h"],
               "demux/reference_reader_internal.h": READER_MPV["demux/reference_reader_internal.h"],
               "stream/vortx_reference_reader_stream.h": READER_MPV["stream/vortx_reference_reader_stream.h"],
               "demux/reference_mkv.h": READER_MPV["demux/reference_mkv.h"],
               "demux/reference_mkv_reader.c": READER_MPV["demux/reference_mkv_reader.c"],
               "demux/reference_mkv_sparse.h": READER_MPV["demux/reference_mkv_sparse.h"],
               "demux/reference_mkv_sparse_planner.c": READER_MPV["demux/reference_mkv_sparse_planner.c"],
               "demux/reference_mkv_sparse_planner.h": READER_MPV["demux/reference_mkv_sparse_planner.h"],
               "demux/reference_mkv_sparse_worker.c": READER_MPV["demux/reference_mkv_sparse_worker.c"],
               "stream/vortx_reference_sparse.h": READER_MPV["stream/vortx_reference_sparse.h"],
               "sub/owned_text_slot.h": READER_MPV["sub/owned_text_slot.h"],
               "stream/reference_source.h": CANCEL["stream/reference_source.h"],
               "stream/vortx_http_request.h": "d7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9"}
PATCH_COPIES = [
    ("0004-moltenvk-context-check-events-resize.patch", None, "mpv-moltenvk-resize.patch"),
    ("0005-coreaudio-hotplug-lifecycle.patch", None, "mpv-coreaudio-hotplug-lifecycle.patch"),
    ("0006-http-seek-interrupt.patch", "0001-http-seek-interrupt.patch", "mpv-http-seek-interrupt.patch"),
    ("0007-upstream-audio-layout-bounds.patch", "0002-upstream-audio-layout-bounds.patch", "mpv-upstream-audio-layout-bounds.patch"),
    ("0008-upstream-gpu-next-seek-reset.patch", "0003-upstream-gpu-next-seek-reset.patch", "mpv-upstream-gpu-next-seek-reset.patch"),
    ("0009-original-local-file-witness.patch", "0004-original-local-file-witness.patch", "mpv-original-local-file-witness.patch"),
    ("0010-upstream-edition-title-lifetime.patch", "0005-upstream-edition-title-lifetime.patch", "mpv-upstream-edition-title-lifetime.patch"),
    ("0011-owned-subtitle-constructor.patch", "0006-owned-subtitle-constructor.patch", "mpv-owned-subtitle-constructor.patch"),
    ("0012-owned-subtitle-publication.patch", "0007-owned-subtitle-publication.patch", "mpv-owned-subtitle-publication.patch"),
    ("0013-original-http-reference-recipe.patch", "0008-original-http-reference-recipe.patch", "mpv-original-http-reference-recipe.patch"),
    ("0014-original-http-cancel-lifetime.patch", "0009-original-http-cancel-lifetime.patch", "mpv-original-http-cancel-lifetime.patch"),
    ("0015-completed-response-observation.patch", "0010-completed-response-observation.patch", "mpv-completed-response-observation.patch"),
    ("0016-sent-request-evidence.patch", "0011-sent-request-evidence.patch", "mpv-sent-request-evidence.patch"),
    ("0017-bounded-prefix-request.patch", "0012-bounded-prefix-request.patch", "mpv-bounded-prefix-request.patch"),
    ("0018-original-transport-evidence.patch", "0013-original-transport-evidence.patch", "mpv-original-transport-evidence.patch"),
    ("0019-finite-reference-reader.patch", "0014-finite-reference-reader.patch", "mpv-finite-reference-reader.patch")]


def untracked(lane, row, recipe):
    if row["commit"] == WRAPPER[lane]:
        prefix = "Sources/BuildScripts/patch/libmpv/" if lane == "apple" else "buildscripts/patches/mpv/"
        result = {prefix + (apple if lane == "apple" else android): recipe["scripts/" + source]
                  for apple, android, source in PATCH_COPIES if lane == "apple" or android is not None}
        response_path = ("Sources/BuildScripts/patch/FFmpeg/0001-response-witness.patch" if lane == "apple"
                         else "buildscripts/patches/ffmpeg/0002-response-witness.patch")
        result[response_path] = recipe["scripts/ffmpeg-response-witness.patch"]
        generation_path = ("Sources/BuildScripts/patch/FFmpeg/0002-response-generation.patch" if lane == "apple"
                           else "buildscripts/patches/ffmpeg/0003-response-generation.patch")
        result[generation_path] = recipe["scripts/ffmpeg-response-generation.patch"]
        sent_path = ("Sources/BuildScripts/patch/FFmpeg/0003-sent-request-evidence.patch" if lane == "apple"
                     else "buildscripts/patches/ffmpeg/0004-sent-request-evidence.patch")
        result[sent_path] = recipe["scripts/ffmpeg-sent-request-evidence.patch"]
        transport_path = ("Sources/BuildScripts/patch/FFmpeg/0004-original-transport-evidence.patch" if lane == "apple"
                          else "buildscripts/patches/ffmpeg/0005-original-transport-evidence.patch")
        result[transport_path] = recipe["scripts/ffmpeg-original-transport-evidence.patch"]
        reader_path = ("Sources/BuildScripts/patch/FFmpeg/0005-finite-reference-reader.patch" if lane == "apple"
                       else "buildscripts/patches/ffmpeg/0006-finite-reference-reader.patch")
        result[reader_path] = recipe["scripts/ffmpeg-finite-reference-reader.patch"]
        if lane == "apple":
            result.update(CRYPTO_INSTALLED_HELPERS)
            result.update(SUBTITLE_INSTALLED_HELPERS)
        if lane == "android":
            result["buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch"] = recipe["scripts/native-media-hosted/android/buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch"]
            for helper in ("download-lua.sh", "download-libunibreak.sh"):
                name = "buildscripts/include/" + helper
                result[name] = recipe["scripts/native-media-hosted/android/" + name]
        return result
    if row["commit"] == PINS["mpv"]:
        result = dict(CREATED_MPV)
        if lane == "apple":
            result["video/out/vulkan/context_moltenvk.m"] = "00d2842ddb0dca5070feaf890c0190a0fe62beef9b29ec36e94e329d29a3da5e"
        return result
    if row["commit"] == PINS["ffmpeg"]:
        result = dict(READER_FFMPEG_CREATED, **{"libavformat/vortx_http_request.h":
                      "d7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9"})
        if lane == "android":
            result["libavformat/tls_mbedtls_vortx_distrust.h"] = "376bd507b67b3b912443bda657465204b808518ea353d161e23e47e9b64c0bf4"
        return result
    return {}


def require_response_source(files, lane):
    # Verified native tar bytes must correspond to the exact tracked HTTP composition.
    need(lane in MBEDTLS, "Known transport-evidence source lane")
    expected = dict(RESPONSE, **{"libavformat/tls_mbedtls.c": MBEDTLS[lane]})
    for name, checksum in expected.items():
        row = files.get(name, {})
        need(row.get("kind") == "file" and row.get("tracked") is True and row.get("sha256") == checksum,
             "Response-witness afterimage: " + name)
    for name, checksum in READER_FFMPEG_CREATED.items():
        row = files.get(name, {})
        need(row.get("kind") == "file" and row.get("tracked") is False and row.get("sha256") == checksum,
             "Finite-reader private source: " + name)


def need(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def digest(value):
    need(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value), "Invalid SHA256")
    return value


def integer(value, limit):
    need(type(value) is int and 0 <= value <= limit, "Invalid integer/bound")
    return value


def keys(value, expected):
    need(type(value) is dict and set(value) == set(expected.split()), "Unexpected object keys")


def exact_equal(left, right):
    """JSON equality that never aliases booleans with integers."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (set(left) == set(right) and
                all(exact_equal(left[key], right[key]) for key in left))
    if isinstance(left, list):
        return (len(left) == len(right) and
                all(exact_equal(a, b) for a, b in zip(left, right)))
    return left == right


def name_key(value):
    return unicodedata.normalize("NFC", value).casefold()


def safe_text(value):
    need(isinstance(value, str) and value.isprintable() and 0 < len(value.encode("utf-8")) <= 4096,
         "Invalid printable UTF-8 path/target byte bound")
    need(not any(c in "\\:\u2044\u2215\u29f8\u29f5\u2216" or (c != "/" and any(
         separator in unicodedata.normalize("NFKC", c) for separator in "/\\:")) for c in value), "Ambiguous path separator")


def path(value):
    safe_text(value)
    need(not value.startswith("/") and not any(unicodedata.normalize("NFKC", p).casefold()
         in {"", ".", "..", ".git"} for p in value.split("/")), "Unsafe archive path")
    return value


def link(name, target):
    safe_text(target)
    need(not target.startswith("/"), "Unsafe symlink target")
    parts = name.split("/")[:-1]
    for piece in target.split("/"):
        if piece == "..":
            need(len(parts) > 1, "Symlink escapes its source/package root")
            parts.pop()
        elif piece not in {"", "."}:
            need(unicodedata.normalize("NFKC", piece).casefold() not in {".", "..", ".git"}, "Ambiguous symlink component/Git metadata")
            parts.append(piece)


def measure(stream, limit, sink=None):
    h, count = hashlib.sha256(), 0
    for block in iter(lambda: stream.read(CHUNK), b""):
        count += len(block)
        need(count <= limit, "Inflated byte bound")
        h.update(block)
        if sink is not None:
            sink.write(block)
    return {"bytes": count, "sha256": h.hexdigest()}


def load(data):
    need(len(data) <= 32 * MIB, "JSON byte bound")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=unique, parse_constant=lambda _: need(False, "Nonfinite JSON"))


def read(archive, name, limit=32 * MIB):
    try:
        info = archive.getinfo(name)
    except KeyError as error:
        raise ValueError("Required ZIP member missing: " + name) from error
    need(info.file_size <= limit, "Member read bound: " + name)
    with archive.open(name) as stream:
        data = stream.read(limit + 1)
    need(len(data) <= limit, "Member read bound")
    return data


def zip_open(stream, count=100000):
    # Bound the central directory BEFORE ZipFile allocates/decodes its entries.
    stream.seek(0, 2)
    end = stream.tell()
    stream.seek(max(0, end - 65557))
    tail = stream.read(65557)
    offset = tail.rfind(b"PK\x05\x06")
    need(offset >= 0 and len(tail) - offset >= 22, "ZIP end record")
    record, position = tail[offset:], end - len(tail) + offset
    number = lambda data, a, b: int.from_bytes(data[a:b], "little")
    need(len(record) == 22 + number(record, 20, 22) and not any(record[4:8]), "ZIP comment/multidisk")
    entries, size, start, directory_end = number(record, 10, 12), number(record, 12, 16), number(record, 16, 20), position
    need(number(record, 8, 10) == entries, "ZIP split entry count")
    if entries == 65535 or size == 0xffffffff or start == 0xffffffff:
        stream.seek(position - 20)
        locator = stream.read(20)
        need(locator[:4] == b"PK\x06\x07" and not any(locator[4:8]) and number(locator, 16, 20) == 1, "ZIP64 locator")
        directory_end = number(locator, 8, 16)
        need(directory_end == position - 76, "ZIP64 contiguous end record")
        stream.seek(directory_end)
        record64 = stream.read(56)
        need(len(record64) == 56 and record64[:4] == b"PK\x06\x06" and number(record64, 4, 12) == 44 and not any(record64[16:24]), "ZIP64 end record")
        entries, size, start = number(record64, 32, 40), number(record64, 40, 48), number(record64, 48, 56)
        need(number(record64, 24, 32) == entries, "ZIP64 split entry count")
    need(entries <= count and size <= min(64 * MIB, count * 8192 + 22) and start + size == directory_end, "ZIP central-directory bound")
    stream.seek(0)
    result = zipfile.ZipFile(stream)
    if len(result.infolist()) != entries:
        result.close()
        raise ValueError("ZIP central-directory count")
    return result


def zip_index(archive, count=100000, total=4 * 1024**3, flat=False):
    rows, names, directories, size = {}, set(), set(), 0
    need(len(archive.infolist()) <= count, "ZIP entry bound")
    for info in archive.infolist():
        name = path(info.filename[:-1] if info.is_dir() else info.filename)
        need(name_key(name) not in names and not info.flag_bits & 1, "ZIP collision/encryption")
        names.add(name_key(name))
        size += info.file_size
        need(size <= total and info.file_size <= (4 * 1024**3 if flat else 512 * MIB)
             and (not flat or not info.is_dir()), "ZIP size/type bound")
        mode = info.external_attr >> 16
        need(not mode or stat.S_IFMT(mode) in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}, "ZIP special file")
        need(stat.S_IFMT(mode) != stat.S_IFDIR or info.is_dir(), "ZIP directory shape")
        need(not flat or stat.S_IFMT(mode) in {0, stat.S_IFREG}, "CI ZIP external file type")
        if info.is_dir():
            directories.add(name)
        if not info.is_dir():
            kind = "symlink" if stat.S_ISLNK(mode) else "file"
            need(not flat or ("/" not in name and kind == "file"), "CI ZIP must contain flat files")
            with archive.open(info) as stream:
                row = measure(stream, 4 * 1024**3 if flat else 512 * MIB)
            row.update(kind=kind, mode=stat.S_IMODE(mode) or 0o644)
            if kind == "symlink":
                row["target"] = read(archive, info.filename, 4096).decode("utf-8")
                link(name, row["target"])
            rows[name] = row
    safe_ancestors(rows, directories)
    return rows


def safe_ancestors(rows, directories=()):
    folded = {name_key(name): row for name, row in rows.items()}
    for name in set(rows) | set(directories):
        pieces = name.split("/")
        need(not any(name_key("/".join(pieces[:i])) in folded for i in range(1, len(pieces))), "File/symlink ancestor")
    for name, row in rows.items():
        if row["kind"] != "symlink":
            continue
        todo, resolved, followed = deque(name.split("/")), [], 0
        while todo:
            piece = todo.popleft()
            if piece in {"", "."}:
                continue
            if piece == "..":
                need(len(resolved) > 1, "Symlink graph escapes component root")
                resolved.pop()
                continue
            resolved.append(piece)
            candidate = folded.get(name_key("/".join(resolved)))
            if candidate and candidate["kind"] == "symlink":
                followed += 1
                need(followed <= 40, "Cyclic/excessive source symlink graph")
                resolved.pop()
                todo.extendleft(reversed(candidate["target"].split("/")))


class BoundedReader:
    def __init__(self, stream, limit):
        self.stream, self.left = stream, limit

    def read(self, size=-1):
        data = self.stream.read(min(size if size >= 0 else self.left + 1, self.left + 1))
        self.left -= len(data)
        need(self.left >= 0, "Inflated tar byte bound")
        return data


class BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, source):
        if self.type in {tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK}:
            need(self.size <= 16384, "Tar extended-header byte bound")
        need(self.type != tarfile.GNUTYPE_SPARSE, "Sparse tar member")
        return super()._proc_member(source)


def tar_index(archive, name, *, compressed=True, file_limit=64 * MIB, total=2 * 1024**3, capture=(), allow_dirs=False):
    rows, seen, directories, captured, size = {}, set(), set(), {}, 0
    with archive.open(name) as incoming:
        with gzip.GzipFile(fileobj=incoming) if compressed else incoming as decoded:
            limited = BoundedReader(decoded, total + 128 * MIB)
            with tarfile.open(fileobj=limited, mode="r|", bufsize=512, tarinfo=BoundedTarInfo) as source:
                for n, member in enumerate(source, 1):
                    item = path(member.name[:-1] if member.isdir() and member.name.endswith("/") else member.name)
                    need(n <= 100000 and name_key(item) not in seen, "Tar entry bound/collision")
                    seen.add(name_key(item))
                    need(member.mode in {0o644, 0o755, 0o777} and member.sparse is None and
                         set(member.pax_headers) <= {"path", "linkpath", "mtime", "atime", "ctime"}, "Tar mode/PAX metadata")
                    need(member.isfile() or member.issym() or member.isdir(), "Tar special/hardlink entry")
                    if member.isdir():
                        need(allow_dirs and member.size == 0, "Unrecorded source directory")
                        directories.add(item)
                        continue
                    if member.issym():
                        link(item, member.linkname)
                        data = member.linkname.encode("utf-8")
                        need(member.size == 0, "Symlink has payload")
                        row = {"bytes": len(data), "sha256": sha(data), "target": member.linkname, "kind": "symlink"}
                    else:
                        size += member.size
                        need(member.size <= file_limit and size <= total, "Tar regular byte bound")
                        sink = io.BytesIO() if item in capture else None
                        with source.extractfile(member) as stream:
                            row = measure(stream, file_limit, sink)
                        need(row["bytes"] == member.size, "Truncated tar file")
                        if sink is not None:
                            captured[item] = sink.getvalue()
                        row["kind"] = "file"
                    row["mode"] = member.mode
                    rows[item] = row
            # Consume the complete decoded stream; only zero tar padding is admitted.
            for block in iter(lambda: limited.read(CHUNK), b""):
                need(not block.strip(b"\0"), "Nonzero trailing tar data")
    safe_ancestors(rows, directories)
    return rows, captured


def crypto_canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def crypto_member_path(value):
    need(isinstance(value, str) and value and len(value.encode("utf-8")) <= CRYPTO_MAX_PATH and
         value.isprintable() and not value.startswith("/") and "\\" not in value and
         ":" not in value and "\x00" not in value,
         "Unsafe crypto source archive path")
    parts = value.rstrip("/").split("/")
    need(all(part not in ("", ".", "..") and part.casefold() != ".git" for part in parts),
         "Unsafe crypto source archive path component")
    return "/".join(parts)


def crypto_link_path(parent, target):
    need(isinstance(target, str) and target and len(target.encode("utf-8")) <= CRYPTO_MAX_PATH and
         target.isprintable() and not target.startswith("/") and "\\" not in target and
         ":" not in target and "\x00" not in target,
         "Unsafe crypto source symlink target")
    parts = list(parent)
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            need(parts, "Crypto source symlink escapes its release root")
            parts.pop()
        elif part.casefold() == ".git":
            need(False, "Crypto source symlink points through Git metadata")
        else:
            parts.append(part)
    return "/".join(parts)


def crypto_mtime(value):
    need(type(value) is int and 0 <= value <= CRYPTO_MAX_MTIME,
         "Crypto source mtime must be bounded integral seconds")
    return value


def crypto_archive_mtime_ns(member):
    """Recover exact archive nanoseconds without trusting TarInfo's float view."""
    need(type(member.pax_headers) is dict,
         "Subtitle source PAX metadata must be a dictionary")
    if "mtime" in member.pax_headers:
        raw = member.pax_headers["mtime"]
        need(type(raw) is str and len(raw) <= 32 and
             re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,9})?", raw) is not None,
             "Subtitle source PAX mtime must be bounded decimal text")
        seconds, separator, fraction = raw.partition(".")
        value = int(seconds) * 1000000000
        if separator:
            value += int(fraction.ljust(9, "0"))
    else:
        need(type(member.mtime) is int,
             "Subtitle source float-only mtime is not admitted")
        value = member.mtime * 1000000000
    need(type(value) is int and 0 <= value <= CRYPTO_MAX_MTIME_NS,
         "Subtitle source mtimeNs must be bounded integral nanoseconds")
    return value


def crypto_member_mtime(member, nanoseconds):
    return crypto_archive_mtime_ns(member) if nanoseconds else crypto_mtime(member.mtime)


def crypto_tar_stream(blob, compression):
    raw = io.BytesIO(blob)
    if compression == "xz":
        decoded = lzma.LZMAFile(raw, "rb")
    elif compression == "gz":
        decoded = gzip.GzipFile(fileobj=raw, mode="rb")
    elif compression == "none":
        decoded = raw
    else:
        need(False, "Unknown fixed crypto source compression")
    return tarfile.open(fileobj=BoundedReader(decoded, CRYPTO_MAX_UNCOMPRESSED), mode="r|")


def crypto_file_row(member, stream, nanoseconds=False):
    need(0 <= member.size <= CRYPTO_MAX_MEMBER, "Crypto source member exceeds its bound")
    hasher, count = hashlib.sha256(), 0
    while True:
        block = stream.read(min(CHUNK, member.size - count + 1))
        if not block:
            break
        count += len(block)
        need(count <= member.size, "Crypto source member exceeds its declared length")
        hasher.update(block)
    need(count == member.size, "Truncated crypto source member")
    return {"kind": "file", "mode": 0o755 if member.mode & 0o111 else 0o644,
            ("mtimeNs" if nanoseconds else "mtime"):
                crypto_member_mtime(member, nanoseconds),
            "bytes": count, "sha256": hasher.hexdigest()}


def crypto_finish_index(entries, nanoseconds=False):
    time_key = "mtimeNs" if nanoseconds else "mtime"
    for name in list(entries):
        parts = name.split("/") if name else []
        for end in range(1, len(parts)):
            parent = "/".join(parts[:end])
            row = entries.setdefault(parent, {"kind": "directory", "mode": 0o755,
                                               time_key: 0})
            need(row["kind"] == "directory", "Crypto source non-directory member has children")
            need(len(entries) <= CRYPTO_MAX_MEMBERS, "Crypto source archive has too many paths")
    folded = {}
    for name in entries:
        key = name.casefold()
        need(key not in folded or folded[key] == name,
             "Crypto source archive has case-colliding members")
        folded[key] = name

    def resolve(name, visited=()):
        need(len(visited) < 32, "Crypto source symlink chain exceeds its bound")
        parts = name.split("/") if name else []
        for end in range(1, len(parts) + 1):
            prefix = "/".join(parts[:end])
            row = entries.get(prefix)
            need(row is not None, "Dangling crypto source symlink")
            if row["kind"] == "symlink":
                need(prefix not in visited, "Cyclic crypto source symlink")
                destination = crypto_link_path(parts[:end - 1], row["target"])
                following = "/".join(([destination] if destination else []) + parts[end:])
                return resolve(following, visited + (prefix,))
            need(end == len(parts) or row["kind"] == "directory",
                 "Crypto source symlink resolves through a non-directory")
        return name

    for name, row in entries.items():
        if row["kind"] == "symlink":
            resolve(name)
    return [{"path": name, **entries[name]} for name in sorted(entries)]


def crypto_index_archive(pin, blob, nanoseconds=False):
    maximum_archive = min(CRYPTO_MAX_UNCOMPRESSED,
                          pin.get("maximumBytes", CRYPTO_MAX_ARCHIVE))
    need(len(blob) <= maximum_archive and
         (pin["bytes"] is None or len(blob) == pin["bytes"]) and
         sha(blob) == pin["sha256"],
         "Crypto release source body differs from its fixed pin")
    time_key = "mtimeNs" if nanoseconds else "mtime"
    entries = {"": {"kind": "directory", "mode": 0o755, time_key: 0}}
    seen, total, transformed = set(), 0, set()
    with crypto_tar_stream(blob, pin["compression"]) as archive:
        for member in archive:
            need(len(seen) < CRYPTO_MAX_MEMBERS, "Crypto source archive has too many members")
            full = crypto_member_path(member.name)
            root, separator, name = full.partition("/")
            need(root == pin["archiveRoot"], "Crypto source archive has a foreign top-level root")
            need(full.casefold() not in seen, "Duplicate or case-colliding crypto source member")
            seen.add(full.casefold())
            need(not getattr(member, "sparse", None) and
                 not any("sparse" in key.casefold() for key in member.pax_headers),
                 "Sparse crypto source members are not admitted")
            if not separator:
                need(member.isdir(), "Crypto source archive root is not a directory")
                entries[""][time_key] = crypto_member_mtime(member, nanoseconds)
                continue
            if member.isdir():
                row = {"kind": "directory", "mode": 0o755,
                       time_key: crypto_member_mtime(member, nanoseconds)}
            elif member.isreg():
                total += member.size
                need(total <= CRYPTO_MAX_UNCOMPRESSED, "Crypto source member total exceeds its bound")
                body = archive.extractfile(member)
                need(body is not None, "Missing crypto source member body")
                with body:
                    if pin['id'] == 'libbluray' and name in BLURAY_SOURCE_TRANSFORMS:
                        raw = body.read(CRYPTO_MAX_MEMBER + 1)
                        need(len(raw) == member.size and len(raw) <= CRYPTO_MAX_MEMBER,
                             'Libbluray raw source member length differs')
                        data = bluray_source_body(pin, name, raw)
                        transformed.add(name)
                        row = {'kind': 'file', 'mode': 0o755 if member.mode & 0o111 else 0o644,
                               time_key: crypto_member_mtime(member, nanoseconds),
                               'bytes': len(data), 'sha256': sha(data)}
                    else:
                        row = crypto_file_row(member, body, nanoseconds)
            elif member.issym():
                crypto_link_path(name.split("/")[:-1], member.linkname)
                body = member.linkname.encode("utf-8")
                row = {"kind": "symlink", "mode": 0o777, "bytes": len(body),
                       time_key: crypto_member_mtime(member, nanoseconds), "sha256": sha(body),
                       "target": member.linkname}
            else:
                need(False, "Crypto source hardlinks and special members are not admitted")
            need(name not in entries, "Duplicate crypto source member")
            entries[name] = row
    need(pin['id'] != 'libbluray' or transformed == set(BLURAY_SOURCE_TRANSFORMS),
         'Libbluray fixed source transform members are incomplete')
    rows = crypto_finish_index(entries, nanoseconds)
    entry_point = pin.get("entryPoint", "configure")
    entry = next((row for row in rows if row["path"] == entry_point), None)
    need(entry is not None and entry["kind"] == "file" and
         (entry_point != "configure" or entry["mode"] == 0o755),
         "Release archive lacks its fixed regular build entry point")
    return rows


def crypto_expected_receipt(verified, profile=None,
                            identity_basis="fixed-sha256-corroborated-by-package-maintainer-metadata",
                            schema=1):
    if profile is None:
        profile = CRYPTO_SOURCE_PROFILE
    roots = []
    for pin, blob, rows in verified:
        roots.append({"id": pin["id"], "version": pin["version"], "url": pin["url"],
                      "archive": "archives/" + pin["filename"],
                      "archiveRoot": pin["archiveRoot"], "archiveBytes": len(blob),
                      "archiveSHA256": pin["sha256"],
                      "sourceRoot": "dist/" + pin["sourceRoot"],
                      "membersSHA256": sha(crypto_canonical(rows)), "members": rows,
                      **({'sourceTransformPolicy': pin.get('sourceTransformPolicy', 'none')} if schema == 3 else {})})
    return {"schema": schema, "profile": profile,
            "identityBasis": identity_basis,
            "upstreamSignatureVerified": False, "roots": roots}


def crypto_load(data):
    need(len(data) <= CRYPTO_MAX_RECEIPT, "Crypto JSON byte bound")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            need(key not in result, "Duplicate crypto JSON key")
            result[key] = value
        return result
    return json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=unique,
                      parse_constant=lambda _: need(False, "Nonfinite crypto JSON"))


def crypto_capture_correspondence(blob, expected, member_limit=None,
                                  nanoseconds=False):
    if member_limit is None:
        member_limit = CRYPTO_MAX_MEMBERS * len(CRYPTO_SOURCE_OUTPUTS)
    need(0 < len(blob) <= CRYPTO_MAX_UNCOMPRESSED,
         "Captured crypto source tar exceeds its bound")
    observed, folded, count = set(), set(), 0
    with tarfile.open(fileobj=BoundedReader(io.BytesIO(blob), CRYPTO_MAX_UNCOMPRESSED), mode="r|") as archive:
        for member in archive:
            name = crypto_member_path(member.name)
            count += 1
            need(count <= member_limit and
                 name not in observed and name.casefold() not in folded and name in expected,
                 "Captured crypto source tar has an extra or duplicate member")
            observed.add(name)
            folded.add(name.casefold())
            wanted = expected[name]
            time_key = "mtimeNs" if nanoseconds else "mtime"
            if member.isdir():
                actual = {"kind": "directory", "mode": member.mode,
                          time_key: crypto_member_mtime(member, nanoseconds)}
            elif (member.isreg() and member.mode in (0o644, 0o755)
                  and not getattr(member, "sparse", None)
                  and not any("sparse" in key.casefold() for key in member.pax_headers)):
                body = archive.extractfile(member)
                need(body is not None, "Captured crypto source member has no body")
                with body:
                    actual = crypto_file_row(member, body, nanoseconds)
            elif member.issym():
                crypto_link_path(name.split("/")[:-1], member.linkname)
                body = member.linkname.encode("utf-8")
                actual = {"kind": "symlink", "mode": 0o777, "bytes": len(body),
                          time_key: crypto_member_mtime(member, nanoseconds), "sha256": sha(body),
                          "target": member.linkname}
            else:
                need(False, "Captured crypto source tar has an unsupported member")
            need(exact_equal(actual, wanted),
                 "Captured crypto source member differs from its raw archive")
    need(observed == set(expected), "Captured crypto source tar omitted release members")


def crypto_source_outputs(archive, *, pins=None, profile=None,
                          capture_tar=None, capture_receipt=None,
                          capture_tar_logical="crypto-release-sources.tar",
                          capture_receipt_logical="crypto-release-sources.json",
                          identity_basis="fixed-sha256-corroborated-by-package-maintainer-metadata",
                          receipt_schema=1, nanoseconds=False):
    if pins is None:
        pins = CRYPTO_SOURCE_OUTPUTS
    if profile is None:
        profile = CRYPTO_SOURCE_PROFILE
    if capture_tar is None:
        capture_tar = CRYPTO_CAPTURE_TAR_OUTPUT
    if capture_receipt is None:
        capture_receipt = CRYPTO_CAPTURE_RECEIPT_OUTPUT
    verified, output_rows = [], []
    for pin in pins:
        maximum = min(CRYPTO_MAX_UNCOMPRESSED,
                      pin.get("maximumBytes", CRYPTO_MAX_ARCHIVE))
        body = read(archive, pin["output"], maximum)
        rows = crypto_index_archive(pin, body, nanoseconds)
        verified.append((pin, body, rows))
        output_rows.append({"logicalPath": "archives/" + pin["filename"],
                            "name": pin["output"], "bytes": len(body), "sha256": sha(body)})
    expected_receipt = crypto_expected_receipt(
        verified, profile, identity_basis, receipt_schema)
    receipt_body = read(archive, capture_receipt, CRYPTO_MAX_RECEIPT)
    need(exact_equal(crypto_load(receipt_body), expected_receipt) and
         receipt_body == crypto_canonical(expected_receipt),
         "Captured crypto source receipt differs from raw source bodies")
    expected_members = {}
    for pin, _, rows in verified:
        for row in rows:
            name = pin["archiveRoot"] + (("/" + row["path"]) if row["path"] else "")
            need(name not in expected_members, "Overlapping captured crypto source roots")
            expected_members[name] = {key: value for key, value in row.items() if key != "path"}
    capture_body = read(archive, capture_tar, CRYPTO_MAX_UNCOMPRESSED)
    crypto_capture_correspondence(
        capture_body, expected_members, CRYPTO_MAX_MEMBERS * len(pins), nanoseconds)
    roots = []
    for root, output_row in zip(expected_receipt["roots"], output_rows, strict=True):
        roots.append({"id": root["id"], "version": root["version"],
                      "archiveRoot": root["archiveRoot"], "sourceRoot": root["sourceRoot"],
                      "archive": output_row, "membersSHA256": root["membersSHA256"],
                      **({'sourceTransformPolicy': root['sourceTransformPolicy']} if receipt_schema == 3 else {})})
    return {"profile": profile, "roots": roots,
            "capture": {
                "tar": {"logicalPath": capture_tar_logical,
                        "name": capture_tar, "bytes": len(capture_body),
                        "sha256": sha(capture_body)},
                "receipt": {"logicalPath": capture_receipt_logical,
                            "name": capture_receipt, "bytes": len(receipt_body),
                            "sha256": sha(receipt_body)}}}


def subtitle_source_outputs(archive):
    return crypto_source_outputs(
        archive, pins=SUBTITLE_SOURCE_OUTPUTS, profile=SUBTITLE_SOURCE_PROFILE,
        capture_tar=SUBTITLE_CAPTURE_TAR_OUTPUT,
        capture_receipt=SUBTITLE_CAPTURE_RECEIPT_OUTPUT,
        capture_tar_logical="subtitle-release-sources.tar",
        capture_receipt_logical="subtitle-release-sources.json",
        identity_basis="fixed-sha256-from-upstream-release-metadata-maintained-android-pin-or-official-sidecar; fixed-apple-source-transforms",
        receipt_schema=3, nanoseconds=True)


def declared(rows, root, native=False):
    need(type(rows) is list and len(rows) <= 100000, "File inventory bound")
    result = {}
    for row in rows:
        keys(row, "path kind mode bytes sha256" + (" target" if row.get("kind") == "symlink" else "") + (" tracked" if native else ""))
        need(not native or type(row["tracked"]) is bool, "Native tracked classification")
        name = root + "/" + path(row["path"])
        need(name not in result and row["kind"] in {"file", "symlink"}, "File inventory identity")
        need(type(row["mode"]) is int and row["mode"] in ({511} if row["kind"] == "symlink" else {420, 493}), "File mode")
        integer(row["bytes"], 64 * MIB)
        digest(row["sha256"])
        result[name] = {key: value for key, value in row.items() if key not in {"path", "tracked"}}
    return result


def git_blob(repo, commit, name):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_NOSYSTEM="1",
               GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    def run(*args):
        value = subprocess.run([str(GIT), "-C", str(repo), *args], env=env, capture_output=True, timeout=20)
        need(value.returncode == 0 and len(value.stdout) <= 4 * MIB, "Pinned local Git read failed")
        return value.stdout
    entry = run("ls-tree", "-z", commit, "--", name).decode().rstrip("\0")
    need(entry.count("\t") == 1, "Ambiguous pinned producer path")
    identity, found = entry.split("\t")
    mode, kind, oid = identity.split()
    need(found == name and kind == "blob" and mode in {"100644", "100755"}, "Pinned producer identity")
    size = run("cat-file", "-s", oid).strip()
    need(size.isdigit() and int(size) <= 4 * MIB, "Pinned Git blob byte bound")
    data = run("cat-file", "blob", oid)
    need(len(data) == int(size), "Pinned Git blob size")
    return {"bytes": len(data), "sha256": sha(data), "kind": "file", "mode": int(mode[-3:], 8)}


SENT_FFMPEG_FUNCTIONS = frozenset(("avpriv_vortx_http_copy_sent_request_v2", "avpriv_vortx_http_sent_request_clear_v2"))
REFERENCE_FFMPEG_FUNCTIONS = frozenset((
    "avpriv_vortx_reference_capture_v1", "avpriv_vortx_reference_revalidate_v1",
    "avpriv_vortx_reference_recipe_current", "avpriv_vortx_reference_open_prefix_v1",
    "avpriv_vortx_reference_read_v1", "avpriv_vortx_reference_stats_v1",
    "avpriv_vortx_reference_reader_close_v1", "avpriv_vortx_reference_recipe_release_v1",
    "avpriv_vortx_reference_sparse_budget_create_v1",
    "avpriv_vortx_reference_sparse_budget_info_v1",
    "avpriv_vortx_reference_sparse_open_v1",
    "avpriv_vortx_reference_sparse_next_part_v1",
    "avpriv_vortx_reference_sparse_read_part_v1",
    "avpriv_vortx_reference_sparse_stats_v1",
    "avpriv_vortx_reference_sparse_close_v1",
    "avpriv_vortx_reference_sparse_budget_release_v1"))
PRIVATE_MPV_FUNCTIONS = frozenset(("stream_reference_sent_request_copy_transport",))


def dynamic_bridge_abi(value, records, validator_sha256, functions, schema, retired_v1):
    """Validate producer inspection metadata against already byte-bound JNI rows.

    This offline consumer does not execute an inspector or independently certify ELF.
    """
    keys(value, "schema reader libraries" + (" retiredV1Absent" if retired_v1 else ""))
    need(type(value["schema"]) is int and value["schema"] == schema and
         (not retired_v1 or value["retiredV1Absent"] is True), "Exact dynamic bridge ABI schema")
    reader = value["reader"]
    keys(reader, "mode requestedReader resolvedReader readerSHA256 readerVersion validatorSHA256")
    need(reader["mode"] in {"hosted-ndk-reader", "explicit-read-only-inspector"}, "Sent-request reader mode")
    for key in ("requestedReader", "resolvedReader"):
        name = reader[key]
        need(type(name) is str and 0 < len(name) <= 4096 and "\0" not in name and
             name.startswith("/") and os.path.normpath(name) == name, "Sent-request reader path")
    digest(reader["readerSHA256"])
    need(digest(reader["validatorSHA256"]) == validator_sha256, "Sent-request validator source")
    version = reader["readerVersion"]
    need(type(version) is str and 0 < len(version) <= 4096 and "\0" not in version, "Sent-request reader version")
    expected = {(abi, library): role for abi in ("arm64-v8a", "armeabi-v7a", "x86_64")
                for library, role in (("libavformat.so", "defined-provider"), ("libmpv.so", "undefined-import"))}
    need(type(value["libraries"]) is list and len(value["libraries"]) == len(expected), "Sent-request ABI matrix")
    seen = set()
    for row in value["libraries"]:
        keys(row, "abi library role binarySHA256 dynsymTextSHA256 symbols")
        identity = (row["abi"], row["library"])
        need(identity in expected and identity not in seen and row["role"] == expected[identity], "Sent-request ABI/library role")
        seen.add(identity)
        entry = "jni/" + row["abi"] + "/" + row["library"]
        need(entry in records and digest(row["binarySHA256"]) == records[entry]["sha256"], "Sent-request inspected JNI bytes")
        digest(row["dynsymTextSHA256"])
        need(type(row["symbols"]) is list and len(row["symbols"]) == len(functions), "Dynamic bridge function count")
        names = []
        for symbol in row["symbols"]:
            keys(symbol, "name version defaultVersion type binding visibility section")
            name, version = symbol["name"], symbol["version"]
            need(name in functions and name not in names and symbol["type"] == "FUNC" and
                 symbol["binding"] == "GLOBAL" and symbol["visibility"] == "DEFAULT", "Sent-request exact function identity")
            names.append(name)
            need(type(symbol["defaultVersion"]) is bool and (version is None or
                 type(version) is str and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", version)) and
                 (not symbol["defaultVersion"] or version is not None), "Sent-request versioned symbol identity")
            section = symbol["section"]
            need(type(section) is str and (section == "UND" if row["role"] == "undefined-import" else
                 re.fullmatch(r"[0-9]+", section) is not None and int(section) > 0), "Sent-request function definition/import role")
        need(names == sorted(functions), "Dynamic bridge canonical function order")
    need(seen == set(expected), "Sent-request complete ABI/library coverage")


def sent_request_abi(value, records, validator_sha256):
    dynamic_bridge_abi(value, records, validator_sha256, SENT_FFMPEG_FUNCTIONS, 2, True)


def reference_reader_abi(value, records, validator_sha256, expected_reader):
    dynamic_bridge_abi(value, records, validator_sha256, REFERENCE_FFMPEG_FUNCTIONS, 1, False)
    need(value["reader"] == expected_reader, "Finite-reader and sent-request inspector identity")


def private_owner_abi(value, records, expected_reader):
    """Validate source-bound private-code inspection metadata, without an inspector."""
    keys(value, "schema reader libraries")
    need(type(value["schema"]) is int and value["schema"] == 1 and value["reader"] == expected_reader,
         "Private owner ABI schema/reader correspondence")
    expected = {(abi, "libmpv.so") for abi in ("arm64-v8a", "armeabi-v7a", "x86_64")}
    need(type(value["libraries"]) is list and len(value["libraries"]) == 3, "Private owner ABI matrix")
    seen = set()
    for row in value["libraries"]:
        keys(row, "abi library binarySHA256 symtabTextSHA256 symbols")
        identity = (row["abi"], row["library"])
        need(identity in expected and identity not in seen, "Private owner library identity")
        seen.add(identity)
        entry = "jni/" + row["abi"] + "/libmpv.so"
        need(entry in records and digest(row["binarySHA256"]) == records[entry]["sha256"],
             "Private owner inspected packaged bytes")
        digest(row["symtabTextSHA256"])
        need(type(row["symbols"]) is list and len(row["symbols"]) == len(PRIVATE_MPV_FUNCTIONS),
             "Private owner function count")
        names = []
        for symbol in row["symbols"]:
            keys(symbol, "name size type binding visibility section")
            name = symbol["name"]
            need(name in PRIVATE_MPV_FUNCTIONS and name not in names and symbol["type"] == "FUNC" and
                 (symbol["binding"], symbol["visibility"]) in {("LOCAL", "DEFAULT"), ("LOCAL", "HIDDEN"),
                                                              ("GLOBAL", "HIDDEN")},
                 "Private owner nonexported exact code identity")
            names.append(name)
            need(integer(symbol["size"], 512 * MIB) > 0 and type(symbol["section"]) is str and
                 re.fullmatch(r"[0-9]+", symbol["section"]) and int(symbol["section"]) > 0,
                 "Private owner nonzero defined code")
        need(names == sorted(PRIVATE_MPV_FUNCTIONS), "Private owner canonical function order")
    need(seen == expected, "Private owner complete three-ABI coverage")


def android(archive, inputs, recipe):
    evidence = load(read(archive, "android-artifact-evidence.json"))
    keys(evidence, "aarSHA256 libraries runtimeSecurityTested appAndSeamBindingChanged promotionEligible sentRequestABI privateOwnerABI referenceReaderABI" +
         (" inspection" if "inspection" in evidence else ""))
    need(evidence["aarSHA256"] == inputs["libmpv-release-candidate.aar"]["sha256"] and
         all(evidence[key] is False for key in ("runtimeSecurityTested", "appAndSeamBindingChanged", "promotionEligible")), "Android receipt boundary")
    with archive.open("libmpv-release-candidate.aar") as stream, zip_open(stream) as aar:
        files = zip_index(aar)
    jni = {name: row for name, row in files.items() if name.startswith("jni/")}
    admitted, records = {"arm64-v8a", "armeabi-v7a", "x86_64"}, {}
    need(type(evidence["libraries"]) is list and len(evidence["libraries"]) <= 256, "Android library rows")
    for row in evidence["libraries"]:
        keys(row, "entry sha256 bytes elfIdentityChecked loadAlignments page16KiBChecked")
        integer(row["bytes"], 512 * MIB)
        digest(row["sha256"])
        name = path(row["entry"])
        need(name in jni and name not in records and len(name.split("/")) == 3 and name.endswith(".so"), "Android JNI identity")
        need(row["elfIdentityChecked"] is True and row["page16KiBChecked"] is (name.split("/")[1] != "armeabi-v7a") and
             type(row["loadAlignments"]) is list and row["loadAlignments"] and
             all(type(n) is int and n > 0 and (name.split("/")[1] == "armeabi-v7a" or n >= 16384) for n in row["loadAlignments"]), "Android inspection flags")
        need(jni[name]["kind"] == "file" and {key: jni[name][key] for key in ("bytes", "sha256")} ==
             {"bytes": row["bytes"], "sha256": row["sha256"]}, "Android JNI byte correspondence")
        records[name] = row
    required = {"libmpv.so", "libplayer.so", "libc++_shared.so", "libavcodec.so", "libavformat.so", "libavutil.so", "libavfilter.so", "libavdevice.so", "libswresample.so", "libswscale.so"}
    need(set(records) == set(jni) and {name.split("/")[1] for name in jni} == admitted and all(
         required <= {name.split("/")[-1] for name in jni if name.startswith("jni/" + abi + "/")} for abi in admitted), "Android ABI/library coverage")
    sent_request_abi(evidence["sentRequestABI"], records, recipe["scripts/native-media-hosted/evidence.py"])
    reference_reader_abi(evidence["referenceReaderABI"], records,
                         recipe["scripts/native-media-hosted/evidence.py"], evidence["sentRequestABI"]["reader"])
    private_owner_abi(evidence["privateOwnerABI"], records, evidence["sentRequestABI"]["reader"])
    if "inspection" in evidence:
        need(evidence["inspection"] == evidence["sentRequestABI"]["reader"] and
             evidence["inspection"]["mode"] == "explicit-read-only-inspector", "Separate explicit inspector identity")
    else:
        need(evidence["sentRequestABI"]["reader"]["mode"] == "hosted-ndk-reader", "Default hosted reader identity")


def shaderc_remote_target(package):
    """Authenticate Shaderc's published remote target before local rewrites."""
    need(isinstance(package, str), "Apple package source text")
    need(len(re.findall(r'\bname:\s*"Libshaderc_combined"', package)) == 1,
         "Unique Shaderc Package.swift target")
    pattern = re.compile(
        r'\.binaryTarget\(\s*name:\s*"Libshaderc_combined",\s*'
        r'url:\s*"([^"]+)",\s*checksum:\s*"([0-9a-f]{64})"\s*\)',
        re.DOTALL)
    matches = pattern.findall(package)
    need(len(matches) == 1 and matches[0] == (SHADERC_REMOTE_URL, SHADERC_SOURCE_SPECS[1][2]),
         "Exact published Shaderc remote target")


def shaderc_receipt(value, source_rows, validator_sha256, retained_all_rows):
    """Validate the producer receipt without importing or executing evidence.py."""
    need(type(retained_all_rows) is dict, "Authenticated retained Shaderc all-ZIP index")
    keys(value, "schema profile wrapperVersion validatorSHA256 reader sources matrix runtimeSecurityTested promotionEligible")
    need(type(value["schema"]) is int and value["schema"] == 1 and
         value["profile"] == NATIVE_UPGRADES_PROFILE and
         value["wrapperVersion"] == SHADERC_WRAPPER_VERSION and
         value["runtimeSecurityTested"] is False and value["promotionEligible"] is False,
         "Shaderc receipt schema/profile boundary")
    digest(validator_sha256)
    need(value["validatorSHA256"] == validator_sha256, "Shaderc receipt validator identity")
    reader = value["reader"]
    keys(reader, "mode requestedReader resolvedReader readerSHA256 toolchainReader toolchainSHA256 toolchainVersion")
    need(reader["mode"] == "xcrun-resolved-lipo" and reader["requestedReader"] == "/usr/bin/xcrun" and
         reader["toolchainReader"] == "/usr/bin/xcodebuild" and
         isinstance(reader["resolvedReader"], str) and reader["resolvedReader"].startswith("/") and
         "\x00" not in reader["resolvedReader"] and isinstance(reader["toolchainVersion"], str) and
         len(reader["resolvedReader"]) <= 4096 and
         reader["toolchainVersion"].strip() and "\x00" not in reader["toolchainVersion"] and
         len(reader["toolchainVersion"]) <= 4096,
         "Shaderc reader identity")
    digest(reader["readerSHA256"])
    digest(reader["toolchainSHA256"])
    expected_sources = [dict(name=name, bytes=bytes_, sha256=checksum)
                        for name, bytes_, checksum in SHADERC_SOURCE_SPECS]
    need(type(source_rows) is list and source_rows == expected_sources,
         "Pinned Shaderc source bodies")
    sources = value["sources"]
    need(type(sources) is list and len(sources) == len(expected_sources), "Shaderc receipt source rows")
    for row in sources:
        keys(row, "name bytes sha256")
        integer(row["bytes"], 8 * 1024**3)
        digest(row["sha256"])
    need(sources == expected_sources, "Shaderc receipt source identity")
    expected = {(slice_name, arch):
                "lib/" + platform_name + "/thin/" + arch + "/lib/libshaderc_combined.a"
                for slice_name, arch, platform_name in SHADERC_MATRIX}
    matrix = value["matrix"]
    need(type(matrix) is list and len(matrix) == len(expected), "Shaderc exact matrix row count")
    observed = set()
    for row in matrix:
        keys(row, "slice arch allZipEntry allArchiveSHA256 xcArchiveSHA256 equal")
        need(isinstance(row["slice"], str) and isinstance(row["arch"], str),
             "Shaderc matrix identity types")
        identity = (row["slice"], row["arch"])
        need(identity in expected and identity not in observed and
             row["allZipEntry"] == expected[identity], "Shaderc matrix entry path")
        digest(row["allArchiveSHA256"])
        digest(row["xcArchiveSHA256"])
        need(row["equal"] is True and row["allArchiveSHA256"] == row["xcArchiveSHA256"],
             "Shaderc per-architecture digest equality")
        retained = retained_all_rows.get(row["allZipEntry"])
        need(isinstance(retained, dict) and retained.get("kind") == "file" and
             retained.get("sha256") == row["allArchiveSHA256"],
             "Shaderc matrix digest differs from retained all-ZIP member")
        observed.add(identity)
    need(observed == set(expected), "Shaderc exact nine-architecture matrix")


def openssl_effective_configuration(value, environment, target):
    """Validate the exact configdata.pm whitelist without executing its producer."""
    need(type(value) is dict and set(value) == set(OPENSSL_EFFECTIVE_KEYS),
         "OpenSSL effective configuration keys differ from the fixed whitelist")
    for key in OPENSSL_EFFECTIVE_KEYS:
        item = value[key]
        if key == "CROSS_COMPILE":
            need(item is None, "OpenSSL CROSS_COMPILE must be JSON null for this profile")
            continue
        if isinstance(item, str):
            need(bool(item) and len(item) <= 65536 and "\x00" not in item,
                 "OpenSSL effective configuration string bound")
        else:
            need(type(item) is list and len(item) <= 4096 and
                 all(isinstance(part, str) and part and len(part) <= 65536 and
                     "\x00" not in part for part in item),
                 "OpenSSL effective configuration list bound")
    need(value["target"] == target and value["CC"] == environment["CC"] and
         value["CXX"] == environment["CXX"] and value["AR"] == environment["AR"] and
         value["RANLIB"] == environment["RANLIB"] and value["ARFLAGS"] == ["qc"],
         "OpenSSL effective compiler/tool configuration differs from the environment")
    need(value["CFLAGS"] == [environment["CFLAGS"]] and
         value["CPPFLAGS"] == [environment["CPPFLAGS"]] and
         value["CXXFLAGS"] == [environment["CXXFLAGS"]] and
         value["LDFLAGS"] == [environment["LDFLAGS"]],
         "OpenSSL effective make flags differ from the fixed environment")
    need(value["lib_cppflags"] == "-DL_ENDIAN", "OpenSSL effective library CPP flags differ")
    cflags = value["cflags"] if type(value["cflags"]) is list else [value["cflags"]]
    need("-fno-common" in cflags, "OpenSSL effective C flags omit the fixed target flag")


def openssl_environment(value, platform, arch, sdk_root, source_directory, build_directory):
    need(type(value) is dict and set(value) == set(OPENSSL_ENV_KEYS),
         "OpenSSL environment keys differ from the fixed build map")
    need(all(isinstance(value[key], str) and len(value[key]) <= 65536 and "\x00" not in value[key]
             for key in OPENSSL_ENV_KEYS),
         "OpenSSL environment value bound")
    need(value["CC"] == "/usr/bin/clang" and value["CXX"] == "/usr/bin/clang++" and
         value["CURRENT_ARCH"] == arch and value["LC_CTYPE"] == "C" and
         value["PKG_CONFIG_PATH"] == "" and value["PATH"] == OPENSSL_DEFAULT_PATH and
         value["ARFLAGS"] == "qc" and value["MAKEFLAGS"] == "-j1" and
         value["CMAKE_BUILD_PARALLEL_LEVEL"] == "1" and value["CARGO_BUILD_JOBS"] == "1",
         "OpenSSL fixed environment identity")
    for key, suffix in (("AR", "ar"), ("RANLIB", "ranlib")):
        path = value[key]
        need(path.startswith("/") and ".." not in Path(path).parts and len(path) <= 4096 and
             Path(path).name == suffix and
             re.fullmatch(r"/.+/Toolchains/[^/]+\.xctoolchain/usr/bin/" + suffix, path),
             "OpenSSL selected absolute SDK tool: " + key)
    sdk = Path(sdk_root)
    need(sdk_root.startswith("/") and "\x00" not in sdk_root and ".." not in sdk.parts,
         "OpenSSL SDK root is not a safe absolute path")
    sdk_suffix = {"ios": ("iPhoneOS", "iPhoneOS.sdk"),
                  "isimulator": ("iPhoneSimulator", "iPhoneSimulator.sdk"),
                  "tvos": ("AppleTVOS", "AppleTVOS.sdk"),
                  "tvsimulator": ("AppleTVSimulator", "AppleTVSimulator.sdk"),
                  "macos": ("MacOSX", "MacOSX.sdk")}[platform]
    sdk_pattern = (r".*/" + re.escape(sdk_suffix[0]) +
                   r"\.platform/Developer/SDKs/" + re.escape(sdk_suffix[0]) +
                   r"(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3})?\.sdk")
    need(re.fullmatch(sdk_pattern, str(sdk)),
         "OpenSSL SDK root does not match the fixed platform")
    cpu = "arm64" if arch in ("arm64", "arm64e") else "x86_64"
    minimum = {"ios": "15.0", "isimulator": "15.0", "tvos": "15.0",
               "tvsimulator": "15.0", "macos": "12.0"}[platform]
    target = {"ios": cpu + "-apple-ios" + minimum,
              "isimulator": cpu + "-apple-ios" + minimum + "-simulator",
              "tvos": cpu + "-apple-tvos" + minimum,
              "tvsimulator": cpu + "-apple-tvos" + minimum + "-simulator",
              "macos": cpu + "-apple-macos" + minimum}[platform]
    minimum_flag = {"ios": "-mios-version-min=" + minimum,
                    "isimulator": "-mios-simulator-version-min=" + minimum,
                    "tvos": "-mtvos-version-min=" + minimum,
                    "tvsimulator": "-mtvos-simulator-version-min=" + minimum,
                    "macos": "-mmacosx-version-min=" + minimum}[platform]
    common = "-arch " + arch + " -isysroot " + sdk_root + " -target " + target + " " + minimum_flag
    cflags = common + (" -DHAVE_FORK=0" if platform in ("tvos", "tvsimulator") else "")
    need(value["CFLAGS"] == value["CXXFLAGS"] == cflags + " -O3" and
         value["CPPFLAGS"] == value["ASMFLAGS"] == cflags and
         value["LDFLAGS"] == "-lc++ " + common,
         "OpenSSL compiler/linker flags differ from the fixed platform policy")
    source = Path(source_directory)
    build = Path(build_directory)
    need(source_directory.startswith("/") and build_directory.startswith("/") and
         "\x00" not in source_directory and "\x00" not in build_directory and
         source.name == OPENSSL_ROOT and source.parent.name == "dist" and
         build == source.parent / OPENSSL_OUTPUT_ROOT / platform / "scratch" / arch,
         "OpenSSL source/build directory identity differs from the fixed layout")
    expected_pkg = [str(source.parent / library / platform / "thin" / arch / "lib" / "pkgconfig")
                    for library in OPENSSL_PKG_LIBRARIES]
    expected_pkg.append(str(build / "vortx-sdk-pkgconfig"))
    need(value["PKG_CONFIG_LIBDIR"] == ":".join(expected_pkg),
         "OpenSSL pkg-config target roots differ from the fixed ordered map")


def openssl_target_config(value, platform, arch, target):
    need(isinstance(value, str) and value and len(value.encode("utf-8")) <= MIB and
         "\x00" not in value and value.endswith("\n"),
         "OpenSSL generated target config is bounded text")
    expected = ("my %targets = (\n"
                "    \"" + target + "\" => {\n"
                "        inherit_from => [ \"darwin-common\" ],\n"
                "        CC => \"/usr/bin/clang\",\n"
                "        cflags => \"-fno-common\",\n"
                "        lib_cppflags => \"-DL_ENDIAN\",\n"
                "        bn_ops => \"SIXTY_FOUR_BIT_LONG\",\n"
                "        asm_arch => \"" + ("aarch64" if arch in ("arm64", "arm64e") else "x86_64") + "\",\n"
                "        perlasm_scheme => \"" + ("ios64" if arch in ("arm64", "arm64e") else "macosx") + "\",\n"
                "        sys_id => \"" + ("MACOSX" if platform == "macos" else "iOS") + "\",\n"
                "    },\n"
                ");\n\n")
    need(value == expected, "OpenSSL generated target config differs from the fixed template")


def validate_native_gitlinks(row, files):
    """Authenticate normal recursive links and OpenSSL's bounded no-fetch map."""
    need(type(row) is dict and isinstance(row.get("root"), str) and
         isinstance(row.get("commit"), str) and type(row.get("gitlinks")) is list,
         "Native Gitlink row shape")
    links = {}
    for item in row["gitlinks"]:
        keys(item, "path commit")
        name = path(item["path"])
        need(name not in links and re.fullmatch(r"[0-9a-f]{40}", item["commit"]),
             "Gitlink source preservation")
        if row["commit"] != OPENSSL_SOURCE_SHA:
            need(any(name0.startswith(row["root"] + "/" + name + "/") for name0 in files),
                 "Gitlink source preservation")
        links[name] = item["commit"]
    if row["commit"] == OPENSSL_SOURCE_SHA:
        need(links == OPENSSL_GITLINKS and
             not any(any(name0 == row["root"] + "/" + name or
                         name0.startswith(row["root"] + "/" + name + "/")
                        for name in OPENSSL_GITLINKS) for name0 in files),
             "Pinned OpenSSL no-fetch Gitlinks")
    return links


def openssl_receipt(value, validator_sha256):
    """Validate the source-build BOM without importing or executing build code."""
    keys(value, "schema profile sourceProfile version sourceSHA sourceTag validatorSHA256 matrix runtimeSecurityTested promotionEligible")
    need(type(value["schema"]) is int and value["schema"] == 1 and
         value["profile"] == NATIVE_UPGRADES_PROFILE and
         value["sourceProfile"] == OPENSSL_BOM_PROFILE and
         value["version"] == OPENSSL_SOURCE_VERSION and
         value["sourceSHA"] == OPENSSL_SOURCE_SHA and
         value["sourceTag"] == OPENSSL_SOURCE_TAG and
         value["runtimeSecurityTested"] is False and value["promotionEligible"] is False,
         "OpenSSL receipt source/profile boundary")
    digest(validator_sha256)
    need(value["validatorSHA256"] == validator_sha256, "OpenSSL receipt validator identity")
    need(type(value["matrix"]) is list and len(value["matrix"]) == len(OPENSSL_MATRIX),
         "OpenSSL exact nine-row matrix")
    expected = {(platform, arch): (target, slice_name)
                for platform, arch, target, slice_name in OPENSSL_MATRIX}
    observed = set()
    header_hashes = set()
    for row in value["matrix"]:
        keys(row, "platform arch slice configureTarget configureArguments sdkRoot sourceDirectory buildDirectory prefix environment targetConfig targetConfigSHA256 effectiveConfiguration compiled installed outputs")
        identity = (row["platform"], row["arch"])
        need(identity in expected and identity not in observed and
             row["slice"] == expected[identity][1] and
             row["configureTarget"] == expected[identity][0] and
             row["compiled"] is True and row["installed"] is True,
             "OpenSSL row platform/architecture/completion")
        for field in ("sdkRoot", "sourceDirectory", "buildDirectory", "prefix"):
            need(isinstance(row[field], str) and row[field].startswith("/") and
                 "\x00" not in row[field] and len(row[field]) <= 4096,
                 "OpenSSL row bounded absolute path: " + field)
        source_root = Path(row["sourceDirectory"])
        need(source_root.name == OPENSSL_ROOT and source_root.parent.name == "dist",
             "OpenSSL row source root")
        need(Path(row["buildDirectory"]) == source_root.parent / OPENSSL_OUTPUT_ROOT / row["platform"] /
             "scratch" / row["arch"], "OpenSSL row scratch directory")
        need(Path(row["prefix"]) == source_root.parent / OPENSSL_OUTPUT_ROOT / row["platform"] /
             "thin" / row["arch"], "OpenSSL row installed thin directory")
        need(row["prefix"].endswith("/dist/" + OPENSSL_OUTPUT_ROOT + "/" + row["platform"] + "/thin/" + row["arch"]),
             "OpenSSL row thin prefix")
        expected_args = ["--config=" + row["buildDirectory"] + "/vortx-openssl-target.conf",
                         "--prefix=" + row["prefix"], *OPENSSL_CONFIGURE_FLAGS, expected[identity][0]]
        need(row["configureArguments"] == expected_args, "OpenSSL Configure argument identity")
        openssl_environment(row["environment"], row["platform"], row["arch"], row["sdkRoot"],
                            row["sourceDirectory"], row["buildDirectory"])
        openssl_target_config(row["targetConfig"], row["platform"], row["arch"],
                              expected[identity][0])
        need(row["targetConfigSHA256"] == sha(row["targetConfig"].encode("utf-8")),
             "OpenSSL target config digest")
        openssl_effective_configuration(row["effectiveConfiguration"], row["environment"],
                                        expected[identity][0])
        outputs = row["outputs"]
        need(type(outputs) is list and len(outputs) == len(OPENSSL_OUTPUTS), "OpenSSL output count")
        seen = set()
        for output in outputs:
            keys(output, "name path bytes sha256")
            fixed = next((candidate for candidate in OPENSSL_OUTPUTS if candidate[0] == output["name"]), None)
            need(fixed is not None and output["name"] not in seen and output["path"] == fixed[1],
                 "OpenSSL output path/name")
            integer(output["bytes"], fixed[2])
            need(output["bytes"] > 0, "OpenSSL output must not be empty")
            digest(output["sha256"])
            if output["name"] == "opensslv.h":
                header_hashes.add(output["sha256"])
            seen.add(output["name"])
        need(seen == {name for name, _, _ in OPENSSL_OUTPUTS}, "OpenSSL output set")
        observed.add(identity)
    need(observed == set(expected) and len(header_hashes) == 1, "OpenSSL complete matrix/header identity")
    return value


def _zip_resolve(rows, name):
    seen = set()
    for _ in range(16):
        need(name not in seen, "ZIP link cycle")
        seen.add(name)
        row = rows.get(name)
        need(isinstance(row, dict), "ZIP member missing: " + name)
        if row.get("kind") == "file":
            return name
        need(row.get("kind") == "symlink" and isinstance(row.get("target"), str),
             "ZIP member is not a regular file: " + name)
        target = row["target"]
        need(target and len(target.encode("utf-8")) <= 4096 and
             not target.startswith("/") and "\\" not in target and "\x00" not in target and
             target.isprintable(), "unsafe ZIP link target")
        base = name.split("/")[:-1]
        for piece in target.split("/"):
            if piece in ("", "."):
                continue
            if piece == "..":
                need(base, "ZIP link escapes archive root")
                base.pop()
            else:
                base.append(piece)
        name = "/".join(base)
    raise ValueError("ZIP link hop bound")


def _macho_arch_hashes(data):
    """Return bounded universal-binary member hashes; thin archives are one-arch."""
    need(isinstance(data, bytes) and data, "OpenSSL framework binary is empty")
    big_magic = int.from_bytes(data[:4], "big") if len(data) >= 4 else 0
    little_magic = int.from_bytes(data[:4], "little") if len(data) >= 4 else 0
    fat_magic = big_magic if big_magic in (0xCAFEBABE, 0xCAFEBABF) else None
    if fat_magic is None and little_magic in (0xCAFEBABE, 0xCAFEBABF):
        fat_magic = little_magic
    if fat_magic is None:
        return None
    endian = "big" if big_magic in (0xCAFEBABE, 0xCAFEBABF) else "little"
    width = 8 if (big_magic == 0xCAFEBABF or little_magic == 0xCAFEBABF) else 4
    count = int.from_bytes(data[4:8], endian)
    need(0 < count <= 16, "OpenSSL universal-binary slice count")
    entry_size = 32 if width == 8 else 20
    need(8 + count * entry_size <= len(data), "OpenSSL universal-binary header bound")
    result = {}
    ranges = []
    for index in range(count):
        offset = 8 + index * entry_size
        cputype = int.from_bytes(data[offset:offset + 4], endian)
        subtype = int.from_bytes(data[offset + 4:offset + 8], endian)
        if width == 8:
            start = int.from_bytes(data[offset + 8:offset + 16], endian)
            size = int.from_bytes(data[offset + 16:offset + 24], endian)
        else:
            start = int.from_bytes(data[offset + 8:offset + 12], endian)
            size = int.from_bytes(data[offset + 12:offset + 16], endian)
        need(size > 0 and start >= 8 + count * entry_size and start + size <= len(data),
             "OpenSSL universal-binary slice bounds")
        arch = "arm64" if cputype == 0x0100000C and subtype & 0xff != 2 else (
            "arm64e" if cputype == 0x0100000C else "x86_64" if cputype == 0x01000007 else None)
        need(arch is not None and arch not in result, "OpenSSL universal-binary architecture identity")
        result[arch] = (size, sha(data[start:start + size]))
        ranges.append((start, start + size))
    ranges.sort()
    need(all(previous_end <= start for (_, previous_end), (start, _) in zip(ranges, ranges[1:])),
         "OpenSSL universal-binary slices overlap")
    return result


def _openssl_archive_member(archive, rows, name, limit=512 * MIB):
    resolved = _zip_resolve(rows, name)
    data = read(archive, resolved, limit)
    return resolved, data


def openssl_archive_binding(value, zip_bodies):
    """Bind nine BOM rows to the retained Libssl/Libcrypto ZIP members."""
    need(set(zip_bodies) == {"Libssl", "Libcrypto"}, "Retained OpenSSL ZIP bodies")
    archives = {}
    for base, body in zip_bodies.items():
        with io.BytesIO(body) as stream, zip_open(stream, 256) as archive:
            archives[base] = (body, archive.infolist(), zip_index(archive, count=256, total=8 * 1024**3))
    expected_arches = {}
    for platform, arch, _, slice_name in OPENSSL_MATRIX:
        expected_arches.setdefault(slice_name, set()).add(arch)
    rows = {(row["platform"], row["arch"]): row for row in value["matrix"]}
    header_hashes = set()
    for platform, arch, _, slice_name in OPENSSL_MATRIX:
        row = rows[(platform, arch)]
        outputs = {output["name"]: output for output in row["outputs"]}
        for base in ("Libssl", "Libcrypto"):
            body, _, indexed = archives[base]
            with io.BytesIO(body) as stream, zip_open(stream, 256) as archive:
                binary_name = base + ".xcframework/" + slice_name + "/" + base + ".framework/" + base
                _, binary = _openssl_archive_member(archive, indexed, binary_name)
                segments = _macho_arch_hashes(binary)
                if segments is None:
                    need(len(expected_arches[slice_name]) == 1,
                         "OpenSSL thin framework binary used for a multi-arch slice")
                    segments = {next(iter(expected_arches[slice_name])): (len(binary), sha(binary))}
                need(set(segments) == expected_arches[slice_name], "OpenSSL exact framework architecture set")
                expected_output = outputs["libssl.a" if base == "Libssl" else "libcrypto.a"]
                need(segments[arch] == (expected_output["bytes"], expected_output["sha256"]),
                     "OpenSSL framework/static-library byte correspondence")
                header_name = base + ".xcframework/" + slice_name + "/" + base + ".framework/Headers/openssl/opensslv.h"
                need(header_name in indexed, "Missing OpenSSL version header")
                _, header = _openssl_archive_member(archive, indexed, header_name, MIB)
                header_sha = sha(header)
                need(len(header) == outputs["opensslv.h"]["bytes"] and
                     header_sha == outputs["opensslv.h"]["sha256"],
                     "OpenSSL version-header byte correspondence")
                header_hashes.add(header_sha)
    need(len(header_hashes) == 1, "OpenSSL retained version-header correspondence")


def crypto_inline_file(row, maximum, expected_path=None, budget=None):
    keys(row, "path bytes sha256 bodyBase64")
    name = path(row["path"])
    need(expected_path is None or name == expected_path, "Crypto inline file path")
    integer(row["bytes"], maximum)
    need(row["bytes"] > 0 and isinstance(row["bodyBase64"], str) and
         len(row["bodyBase64"]) <= ((maximum + 2) // 3) * 4,
         "Crypto inline file body bound")
    try:
        body = base64.b64decode(row["bodyBase64"].encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as error:
        raise ValueError("Crypto inline file is not canonical base64") from error
    need(base64.b64encode(body).decode("ascii") == row["bodyBase64"] and
         len(body) == row["bytes"] and sha(body) == row["sha256"],
         "Crypto inline file raw-body identity")
    if budget is not None:
        budget[0] += len(body)
        need(budget[0] <= CRYPTO_MAX_INLINE_RAW, "Crypto aggregate inline raw-body cap")
    return body


def crypto_recorded_text(row, label):
    keys(row, "bytes sha256 text")
    need(isinstance(row["text"], str), "Crypto " + label + " text type")
    body = row["text"].encode("utf-8")
    integer(row["bytes"], 16 * MIB)
    need(len(body) == row["bytes"] and sha(body) == row["sha256"],
         "Crypto " + label + " text identity")
    return row["text"]


def crypto_tool_record(row, label):
    keys(row, "resolvedPath path bytes sha256")
    resolved = row["resolvedPath"]
    need(isinstance(resolved, str) and resolved.startswith("/") and "\x00" not in resolved and
         ".." not in Path(resolved).parts and len(resolved) <= 4096 and
         row["path"] == Path(resolved).name,
         "Crypto " + label + " resolved tool path")
    integer(row["bytes"], 512 * MIB)
    need(row["bytes"] > 0, "Crypto " + label + " empty tool")
    digest(row["sha256"])
    return row


def crypto_tools(value):
    keys(value, "xcrun xcodebuild lipo nm toolchainVersion platforms")
    resolved_aliases = {
        "xcrun": {"xcrun"}, "xcodebuild": {"xcodebuild"},
        "lipo": {"lipo"}, "nm": {"nm", "llvm-nm"},
        "ar": {"ar"}, "ranlib": {"ranlib", "libtool"},
    }
    for name in ("xcrun", "xcodebuild", "lipo", "nm"):
        crypto_tool_record(value[name], name)
        need(value[name]["path"] in resolved_aliases[name],
             "Crypto selected tool resolved basename: " + name)
    need(value["xcrun"]["resolvedPath"] == "/usr/bin/xcrun" and
         value["xcodebuild"]["resolvedPath"] == "/usr/bin/xcodebuild",
         "Crypto fixed Apple tool entry points")
    crypto_recorded_text(value["toolchainVersion"], "toolchain version")
    need(type(value["platforms"]) is dict and set(value["platforms"]) == set(CRYPTO_PLATFORMS),
         "Crypto selected SDK platform set")
    sdk_stems = {"ios": "iPhoneOS", "isimulator": "iPhoneSimulator",
                 "tvos": "AppleTVOS", "tvsimulator": "AppleTVSimulator",
                 "macos": "MacOSX"}
    for platform, row in value["platforms"].items():
        keys(row, "sdkRoot resolvedSDKRoot ar ranlib")
        stem = sdk_stems[platform]
        sdk_pattern = (r".*/" + re.escape(stem) +
                       r"\.platform/Developer/SDKs/" + re.escape(stem) +
                       r"(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3})?\.sdk")
        for field in ("sdkRoot", "resolvedSDKRoot"):
            sdk = row[field]
            need(isinstance(sdk, str) and sdk.startswith("/") and "\x00" not in sdk and
                 ".." not in Path(sdk).parts and len(sdk) <= 4096 and
                 re.fullmatch(sdk_pattern, sdk) is not None,
                 "Crypto selected SDK root for " + platform)
        for name in ("ar", "ranlib"):
            tool = row[name]
            keys(tool, "selectedPath resolvedPath path bytes sha256")
            selected = tool["selectedPath"]
            need(isinstance(selected, str) and selected.startswith("/") and
                 Path(selected).name == name and len(selected) <= 4096 and
                 "\x00" not in selected and ".." not in Path(selected).parts,
                 "Crypto selected target archive tool")
            crypto_tool_record({key: tool[key] for key in
                                ("resolvedPath", "path", "bytes", "sha256")}, name)
            need(tool["path"] in resolved_aliases[name],
                 "Crypto selected SDK tool resolved basename: " + name)
    return value


def crypto_recipe_contract(work, library, platform, arch, sdk_tools):
    dist = work / "dist"
    prefix = dist / library / platform / "thin" / arch
    scratch = dist / library / platform / "scratch" / arch
    _, target_os, minimum, host_os = CRYPTO_PLATFORMS[platform]
    target_cpu = "x86_64" if arch == "x86_64" else "arm64"
    host = target_cpu + "-" + host_os + "-darwin"
    cflags = ["-arch", arch, "-isysroot", sdk_tools["sdkRoot"],
              "-target", target_cpu + "-apple-" + target_os, minimum]
    if platform in ("tvos", "tvsimulator"):
        cflags.append("-DHAVE_FORK=0")
    dependencies = () if library == "gmp" else ("gmp",) if library == "nettle" else ("gmp", "nettle")
    for dependency in dependencies:
        cflags.append("-I" + str(dist / dependency / platform / "thin" / arch / "include"))
    ldflags = ["-lc++", "-arch", arch, "-isysroot", sdk_tools["sdkRoot"],
               "-target", target_cpu + "-apple-" + target_os, minimum]
    for dependency in dependencies:
        ldflags.append("-L" + str(dist / dependency / platform / "thin" / arch / "lib"))
        if library != "gnutls":
            ldflags.append("-l" + dependency)
    flags = " ".join(cflags)
    environment = {
        "LC_CTYPE": "C", "CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++",
        "CURRENT_ARCH": arch, "CFLAGS": flags + ' -O2', "CPPFLAGS": flags,
        "CXXFLAGS": flags + ' -O2', "ASMFLAGS": flags, "LDFLAGS": " ".join(ldflags),
        "PKG_CONFIG_LIBDIR": ":".join(str(dist / name / platform / "thin" / arch /
                                              "lib/pkgconfig") for name in CRYPTO_PKG_LIBRARIES) +
                                ":" + str(scratch / "vortx-sdk-pkgconfig"),
        "PKG_CONFIG_PATH": "", "PATH": CRYPTO_DEFAULT_PATH,
        "AR": sdk_tools["ar"]["selectedPath"],
        "RANLIB": sdk_tools["ranlib"]["selectedPath"],
        "CC_FOR_BUILD": "/usr/bin/clang", "MAKEFLAGS": "-j1",
        "CMAKE_BUILD_PARALLEL_LEVEL": "1", "CARGO_BUILD_JOBS": "1",
    }
    if library == "gmp":
        environment["ABI"] = "64"
    if library == "gnutls":
        gmp = str(dist / "gmp" / platform / "thin" / arch)
        nettle = str(dist / "nettle" / platform / "thin" / arch)
        environment.update(
            GMP_CFLAGS="-I" + gmp + "/include", GMP_LIBS="-L" + gmp + "/lib -lgmp",
            NETTLE_CFLAGS="-I" + nettle + "/include",
            NETTLE_LIBS="-L" + nettle + "/lib -lnettle",
            HOGWEED_CFLAGS="-I" + nettle + "/include",
            HOGWEED_LIBS="-L" + nettle + "/lib -lhogweed -lnettle -L" + gmp + "/lib -lgmp")
    return {
        "schema": 1, "library": library, "selector": CRYPTO_SELECTORS[library],
        "sourceDirectory": str(dist / (library + "-" + CRYPTO_SELECTORS[library])),
        "platform": platform, "arch": arch,
        "configureArguments": ["--prefix=" + str(prefix), *CRYPTO_CONFIGURE_FLAGS[library],
                               "--host=" + host],
        "makeArguments": list(CRYPTO_MAKE_ARGUMENTS), "environment": environment,
        "buildDirectory": str(scratch), "prefix": str(prefix),
        "compiled": True, "installed": True,
    }


def crypto_macro(text, name):
    values = re.findall(r"^\s*#\s*define\s+" + re.escape(name) +
                        r"\s+([0-9]+)\s*(?:/\*.*\*/)?\s*$", text, re.M)
    need(len(values) == 1, "Crypto expected one numeric macro " + name)
    return int(values[0])


def crypto_public_header(target, body):
    text = body.decode("utf-8", errors="strict")
    if target == "gmp":
        need(tuple(crypto_macro(text, key) for key in
                   ("__GNU_MP_VERSION", "__GNU_MP_VERSION_MINOR", "__GNU_MP_VERSION_PATCHLEVEL")) ==
             (6, 3, 0) and (crypto_macro(text, "GMP_LIMB_BITS"),
                            crypto_macro(text, "GMP_NAIL_BITS")) == (64, 0),
             "Mixed GMP public header version/ABI")
    elif target in ("nettle", "hogweed"):
        need(tuple(crypto_macro(text, key) for key in
                   ("NETTLE_VERSION_MAJOR", "NETTLE_VERSION_MINOR", "NETTLE_USE_MINI_GMP")) ==
             (4, 0, 0), "Mixed Nettle version or mini-GMP backend")
    elif target == "gnutls":
        need(tuple(crypto_macro(text, key) for key in
                   ("GNUTLS_VERSION_MAJOR", "GNUTLS_VERSION_MINOR", "GNUTLS_VERSION_PATCH")) ==
             (3, 8, 13) and
             len(re.findall(r'^\s*#\s*define\s+GNUTLS_VERSION\s+"3\.8\.13"\s*$', text, re.M)) == 1,
             "Mixed GnuTLS public header version")
    return body


def crypto_build_evidence(value, work, library, platform, arch, sdk_tools, budget):
    keys(value, "record generatedConfiguration installedConfiguration sdkMetadata")
    expected = crypto_recipe_contract(work, library, platform, arch, sdk_tools)
    scratch = Path(expected["buildDirectory"])
    record_path = (scratch / "vortx-crypto-build.json").relative_to(work).as_posix()
    record_body = crypto_inline_file(value["record"], 4 * MIB, record_path, budget)
    record = crypto_load(record_body)
    need(type(record) is dict and set(record) == set(expected) | {"generatedConfiguration"} and
         exact_equal({key: record[key] for key in expected}, expected),
         "Crypto configured build recipe differs from fixed policy")
    names = CRYPTO_GENERATED + CRYPTO_EXTRA_GENERATED[library]
    need(type(record["generatedConfiguration"]) is dict and
         set(record["generatedConfiguration"]) == set(names) and
         type(value["generatedConfiguration"]) is dict and
         set(value["generatedConfiguration"]) == set(names),
         "Crypto generated configuration set")
    generated = {}
    for name in names:
        expected_path = (scratch / name).relative_to(work).as_posix()
        body = crypto_inline_file(value["generatedConfiguration"][name], 16 * MIB,
                                  expected_path, budget)
        claimed = record["generatedConfiguration"][name]
        keys(claimed, "bytes sha256")
        need(exact_equal(claimed, {"bytes": len(body), "sha256": sha(body)}),
             "Crypto build record/generated raw bytes")
        generated[name] = body
    installed_spec = CRYPTO_INSTALLED_CONFIGURATION[library]
    need(type(value["installedConfiguration"]) is dict and
         set(value["installedConfiguration"]) == set(installed_spec.values()),
         "Crypto installed configuration set")
    installed = {}
    for generated_name, relative in installed_spec.items():
        expected_path = (Path(expected["prefix"]) / relative).relative_to(work).as_posix()
        body = crypto_inline_file(value["installedConfiguration"][relative], 16 * MIB,
                                  expected_path, budget)
        need(body == generated[generated_name],
             "Crypto installed configuration differs from generated raw bytes")
        installed[relative] = body
    need(type(value["sdkMetadata"]) is dict and
         set(value["sdkMetadata"]) == {"zlib.pc", "libxml-2.0.pc"},
         "Crypto SDK metadata set")
    for name in ("zlib.pc", "libxml-2.0.pc"):
        crypto_inline_file(value["sdkMetadata"][name], 4 * MIB,
                           (scratch / "vortx-sdk-pkgconfig" / name).relative_to(work).as_posix(),
                           budget)
    if library == "gmp":
        crypto_public_header("gmp", installed["include/gmp.h"])
    elif library == "nettle":
        crypto_public_header("nettle", installed["include/nettle/version.h"])
    else:
        crypto_public_header("gnutls", installed["include/gnutls/gnutls.h"])
    return installed, value["installedConfiguration"]


def crypto_normalize_symbols(text):
    symbols = set()
    for line in text.splitlines():
        value = line.strip()
        if not value or value.endswith(":"):
            continue
        need(re.fullmatch(r"[A-Za-z_.$][A-Za-z0-9_.$]*", value) is not None,
             "Unrecognized crypto nm row")
        symbols.add(value[1:] if value.startswith("_") else value)
    return symbols


def crypto_validate_symbols(observations):
    need(set(observations) == {item[0] for item in CRYPTO_LIBRARIES},
         "Crypto symbol library set")
    for target, required in CRYPTO_REQUIRED_SYMBOLS.items():
        need(required <= observations[target]["defined"],
             "Required defined crypto symbol missing from " + target)
    consumer = observations["Libsmbclient"]
    def helper(name):
        return name.startswith(("nettle_", "_nettle_", "hogweed_", "__gmp",
                                "gmp_", "mpz_", "mpn_", "mpq_", "mpf_"))
    need(not any(helper(name) for name in consumer["undefined"]),
         "Retained Samba directly imports a crypto-helper ABI")
    need(not any(name.startswith("_gnutls_") for name in consumer["undefined"]),
         "Retained Samba imports a private GnuTLS ABI")
    need(not any(name.startswith(("gnutls_", "_gnutls_")) or helper(name)
                 for name in consumer["defined"]),
         "Retained Samba embeds crypto provider symbols")
    imports = {name for name in consumer["undefined"] if name.startswith("gnutls_")} - consumer["defined"]
    need(imports, "Retained Samba has no observed public GnuTLS imports")
    need(not imports - observations["gnutls"]["defined"],
         "Selected GnuTLS does not define retained Samba imports")
    edges = {}
    for target in ("nettle", "hogweed", "gnutls"):
        required = {name for name in observations[target]["undefined"] if helper(name)} - observations[target]["defined"]
        providers = set().union(*(observations[name]["defined"]
                                  for name in ("gmp", "nettle", "hogweed") if name != target))
        need(not required - providers, target + " crypto-helper imports are unresolved")
        edges[target] = sorted(required)
    need(edges["gnutls"], "Selected GnuTLS has no observed crypto-helper linkage")
    return {"sambaGnuTLSImports": sorted(imports), "helperImports": edges}


def crypto_normalized_gmp_header(body):
    text = body.decode("utf-8", errors="strict")
    for name in ("__GMP_CC", "__GMP_CFLAGS"):
        definitions = re.findall(r"(?m)^[ \t]*#[ \t]*define[ \t]+" + name + r"\b[^\r\n]*$", text)
        pattern = (r"(?m)^[ \t]*#[ \t]*define[ \t]+" + name +
                   r'[ \t]+"(?:[^"\\\r\n]|\\[^\r\n])*"[ \t]*$')
        need(len(definitions) == 1 and len(re.findall(pattern, text)) == 1,
             "GMP diagnostic macro has an unapproved shape: " + name)
        text = re.sub(pattern, "#define " + name + ' "<generated-diagnostic>"', text)
    return text.encode("utf-8")


def crypto_header_correspondence(target, packaged, thin_evidence):
    if target != "gmp":
        need(all(packaged == body for _, body, _ in thin_evidence),
             "Packaged crypto header differs from selected thin build bytes")
        return {"kind": "exact-checked-header-bytes"}
    normalized = crypto_normalized_gmp_header(packaged)
    matches = []
    for identity, body, row in thin_evidence:
        if crypto_normalized_gmp_header(body) == normalized:
            if body == packaged:
                matches.append({"platform": identity[0], "arch": identity[1], "header": row})
        else:
            need(False, "GMP thin headers differ beyond generated compiler diagnostics")
    need(matches, "Packaged GMP header is not one exact selected thin header")
    return {"kind": "gmp-two-generated-diagnostic-strings-only",
            "normalizedSHA256": sha(normalized), "packagedHeaderFrom": matches}


def crypto_zip_file(catalog, target, name, limit=512 * MIB):
    body, rows = catalog[target]
    with io.BytesIO(body) as stream, zip_open(stream, 100000) as archive:
        _, data = _openssl_archive_member(archive, rows, name, limit)
    return data


def crypto_zip_catalog(zip_bodies, libraries=None):
    if libraries is None:
        libraries = CRYPTO_LIBRARIES
    expected_targets = {row[0] for row in libraries}
    need(set(zip_bodies) == expected_targets, "Retained crypto ZIP body set")
    expected_slices = {slice_name for _, _, slice_name in CRYPTO_MATRIX}
    expected_arches = {slice_name: sorted({arch for _, arch, candidate in CRYPTO_MATRIX
                                          if candidate == slice_name})
                       for slice_name in expected_slices}
    catalog = {}
    for target, _, _, binary_name in libraries:
        body = zip_bodies[target]
        with io.BytesIO(body) as stream, zip_open(stream, 100000) as archive:
            rows = zip_index(archive, count=100000, total=4 * 1024**3)
            root = target + ".xcframework"
            need(rows and all(name.startswith(root + "/") for name in rows),
                 "Crypto ZIP root")
            need(all((name.removeprefix(root + "/") == "Info.plist" or
                      name.removeprefix(root + "/").split("/", 1)[0] in expected_slices)
                     for name in rows),
                 "Crypto ZIP has an undeclared top-level slice")
            info_name = root + "/Info.plist"
            _, info_body = _openssl_archive_member(archive, rows, info_name, 4 * MIB)
            metadata = plistlib.loads(info_body)
        need(isinstance(metadata, dict) and metadata.get("CFBundlePackageType") == "XFWK" and
             metadata.get("XCFrameworkFormatVersion") == "1.0",
             "Crypto XCFramework metadata identity")
        available = metadata.get("AvailableLibraries")
        need(isinstance(available, list) and len(available) == len(expected_slices),
             "Crypto XCFramework slice declarations")
        declarations = set()
        for item in available:
            need(isinstance(item, dict), "Crypto XCFramework declaration shape")
            identifier = item.get("LibraryIdentifier")
            need(isinstance(identifier, str) and identifier in expected_slices and
                 identifier not in declarations, "Crypto XCFramework declaration identity")
            platform = "macos" if identifier.startswith("macos-") else (
                "tvos" if identifier.startswith("tvos-") else "ios")
            expected = {"LibraryIdentifier": identifier,
                        "LibraryPath": binary_name + ".framework",
                        "SupportedArchitectures": expected_arches[identifier],
                        "SupportedPlatform": platform}
            if identifier.endswith("-simulator"):
                expected["SupportedPlatformVariant"] = "simulator"
            actual = dict(item)
            need(isinstance(actual.get("SupportedArchitectures"), list) and
                 all(isinstance(value, str) for value in actual["SupportedArchitectures"]),
                 "Crypto XCFramework architecture list")
            actual["SupportedArchitectures"] = sorted(actual["SupportedArchitectures"])
            need(exact_equal(actual, expected), "Crypto XCFramework declaration differs")
            declarations.add(identifier)
        need(declarations == expected_slices, "Crypto XCFramework declaration coverage")
        expected_links = {}
        framework = root + "/macos-arm64_x86_64/" + binary_name + ".framework"
        for name, destination in (("Versions/Current", "A"),
                                  (binary_name, "Versions/Current/" + binary_name),
                                  ("Resources", "Versions/Current/Resources")):
            expected_links[framework + "/" + name] = destination
        actual_links = {name: row["target"] for name, row in rows.items()
                        if row["kind"] == "symlink"}
        need(actual_links == expected_links, "Crypto exact macOS framework link set")
        catalog[target] = (body, rows)
    return catalog


def crypto_plain_row(row, expected_path=None, maximum=512 * MIB):
    keys(row, "path bytes sha256")
    name = path(row["path"])
    need(expected_path is None or name == expected_path, "Crypto recorded file path")
    integer(row["bytes"], maximum)
    need(row["bytes"] > 0, "Crypto recorded file is empty")
    digest(row["sha256"])
    return row


def crypto_zip_row(catalog, target, package_path):
    prefix = "artifacts/" + target + ".xcframework/"
    need(package_path.startswith(prefix), "Crypto packaged path root")
    archive_name = target + ".xcframework/" + package_path.removeprefix(prefix)
    row = catalog[target][1].get(archive_name)
    need(isinstance(row, dict) and row.get("kind") == "file",
         "Crypto packaged regular file missing from original ZIP")
    return {"path": package_path, "bytes": row["bytes"], "sha256": row["sha256"]}, archive_name


def crypto_framework_layout(target, binary_name, slice_name, catalog):
    if not slice_name.startswith("macos-"):
        return {"kind": "shallow"}
    package_framework = ("artifacts/" + target + ".xcframework/" + slice_name + "/" +
                         binary_name + ".framework")
    archive_framework = (target + ".xcframework/" + slice_name + "/" +
                         binary_name + ".framework")
    links = []
    for name, destination in (("Versions/Current", "A"),
                              (binary_name, "Versions/Current/" + binary_name),
                              ("Resources", "Versions/Current/Resources")):
        row = catalog[target][1].get(archive_framework + "/" + name)
        body = destination.encode("utf-8")
        need(isinstance(row, dict) and row.get("kind") == "symlink" and
             row.get("target") == destination and row.get("bytes") == len(body) and
             row.get("sha256") == sha(body), "Crypto macOS framework link bytes")
        links.append({"path": package_framework + "/" + name, "target": destination,
                      "bytes": len(body), "sha256": sha(body)})
    info_path = package_framework + "/Versions/A/Resources/Info.plist"
    info, _ = crypto_zip_row(catalog, target, info_path)
    return {"kind": "fixed-macos-versions-A", "links": links, "info": info}


def crypto_reader_caps(value):
    def reader_bytes(item):
        if isinstance(item, dict):
            if "text" in item:
                crypto_recorded_text(item, "native reader")
                return len(json.dumps(item, sort_keys=True).encode("utf-8"))
            return sum(reader_bytes(child) for child in item.values())
        if isinstance(item, list):
            return sum(reader_bytes(child) for child in item)
        return 0
    def metadata(item):
        if isinstance(item, dict):
            return {key: metadata(child) for key, child in item.items()
                    if key not in ("bodyBase64", "text")}
        if isinstance(item, list):
            return [metadata(child) for child in item]
        return item
    need(reader_bytes(value) <= CRYPTO_MAX_READER_JSON, "Crypto aggregate reader JSON cap")
    need(len(json.dumps(metadata(value), indent=2, sort_keys=True).encode("utf-8")) <=
         CRYPTO_MAX_METADATA_JSON, "Crypto receipt metadata JSON cap")


def crypto_chain_receipt(body, zip_bodies, archive_sources,
                         candidate_package_row, candidate_package_body):
    """Validate the frozen gate receipt against retained raw artifact bytes."""
    need(isinstance(body, bytes) and 0 < len(body) <= CRYPTO_MAX_RECEIPT,
         "Crypto receipt raw byte bound")
    value = crypto_load(body)
    need(type(value) is dict, "Crypto receipt object")
    keys(value, "schemaVersion profile work packagePolicy inlineBodyPolicy versions sourcePins "
                "tools packageManifest xcframeworkInfo releaseSourceBoundary rows acceptance openGates")
    expected_identity = {
        "schemaVersion": 2, "profile": CRYPTO_CHAIN_PROFILE,
        "packagePolicy": CRYPTO_PACKAGE_POLICY,
        "inlineBodyPolicy": CRYPTO_INLINE_BODY_POLICY,
        "versions": CRYPTO_VERSIONS, "sourcePins": CRYPTO_SOURCE_PINS,
        "acceptance": CRYPTO_ACCEPTANCE, "openGates": list(CRYPTO_OPEN_GATES),
    }
    need(all(exact_equal(value[key], expected) for key, expected in expected_identity.items()),
         "Crypto receipt fixed identity")
    work_text = value["work"]
    need(type(work_text) is str and 0 < len(work_text) <= 4096 and
         work_text.startswith("/") and "\x00" not in work_text,
         "Crypto receipt WORK path")
    work = Path(work_text)
    need(work.is_absolute() and str(work) == work_text and ".." not in work.parts,
         "Crypto receipt normalized WORK path")

    tools = crypto_tools(value["tools"])
    catalog = crypto_zip_catalog(zip_bodies)

    need(type(candidate_package_row) is dict and
         candidate_package_row.get("kind") == "file" and
         candidate_package_row.get("bytes") == len(candidate_package_body) and
         candidate_package_row.get("sha256") == sha(candidate_package_body),
         "Crypto candidate Package.swift raw bytes")
    package_manifest = {"path": "Package.swift", "bytes": len(candidate_package_body),
                        "sha256": sha(candidate_package_body)}
    crypto_plain_row(value["packageManifest"], "Package.swift", 4 * MIB)
    need(exact_equal(value["packageManifest"], package_manifest),
         "Crypto package manifest/raw candidate correspondence")

    need(type(value["xcframeworkInfo"]) is dict and
         set(value["xcframeworkInfo"]) == {item[0] for item in CRYPTO_LIBRARIES},
         "Crypto XCFramework metadata target set")
    for target, _, _, _ in CRYPTO_LIBRARIES:
        expected, _ = crypto_zip_row(
            catalog, target, "artifacts/" + target + ".xcframework/Info.plist")
        crypto_plain_row(value["xcframeworkInfo"][target], expected["path"], 4 * MIB)
        need(exact_equal(value["xcframeworkInfo"][target], expected),
             "Crypto XCFramework metadata/raw ZIP correspondence")

    need(type(archive_sources) is dict and
         type(archive_sources.get("capture")) is dict and
         type(archive_sources["capture"].get("receipt")) is dict,
         "Crypto release-source boundary metadata")
    source_receipt = archive_sources["capture"]["receipt"]
    release_boundary = {
        "path": "dist/crypto-sources/receipt.json",
        "bytes": source_receipt["bytes"], "sha256": source_receipt["sha256"],
    }
    crypto_plain_row(value["releaseSourceBoundary"], release_boundary["path"],
                     CRYPTO_MAX_RECEIPT)
    need(exact_equal(value["releaseSourceBoundary"], release_boundary),
         "Crypto gate/source-helper receipt correspondence")

    rows = value["rows"]
    need(type(rows) is list and len(rows) == len(CRYPTO_MATRIX),
         "Crypto exact nine-row matrix")
    expected_matrix = list(CRYPTO_MATRIX)
    observed_matrix = []
    build_evidence = {}
    inline_budget = [0]
    for row in rows:
        keys(row, "platform arch slice libraries builds sambaGnuTLSImports helperImports")
        identity = (row["platform"], row["arch"], row["slice"])
        observed_matrix.append(identity)
        need(identity in CRYPTO_MATRIX and identity not in build_evidence,
             "Crypto matrix row identity")
        need(type(row["builds"]) is dict and
             set(row["builds"]) == {"gmp", "nettle", "gnutls"},
             "Crypto build family set")
        platform, arch, _ = identity
        sdk_tools = tools["platforms"][platform]
        build_evidence[identity] = {}
        for library in ("gmp", "nettle", "gnutls"):
            bodies, recorded_rows = crypto_build_evidence(
                row["builds"][library], work, library, platform, arch,
                sdk_tools, inline_budget)
            build_evidence[identity][library] = {
                "bodies": bodies, "rows": recorded_rows,
            }
    need(observed_matrix == expected_matrix,
         "Crypto matrix order/coverage differs from frozen gate")

    expected_slice_arches = {
        slice_name: sorted({arch for _, arch, candidate in CRYPTO_MATRIX
                            if candidate == slice_name})
        for _, _, slice_name in CRYPTO_MATRIX
    }
    library_map = {target: (directory, archive_name, binary_name)
                   for target, directory, archive_name, binary_name in CRYPTO_LIBRARIES}

    def plain_inline(row):
        keys(row, "path bytes sha256 bodyBase64")
        return {key: row[key] for key in ("path", "bytes", "sha256")}

    def thin_header_material(identity, target):
        platform, arch, _ = identity
        if target == "Libsmbclient":
            rows = [
                {"path": ("dist/libsmbclient/" + platform + "/thin/" + arch +
                          "/include/samba-4.0/" + name),
                 "bytes": size, "sha256": checksum}
                for name, (size, checksum) in CRYPTO_SMB_HEADERS.items()
            ]
            return rows, []
        source = "nettle" if target == "hogweed" else target
        relative = {"gmp": "include/gmp.h",
                    "nettle": "include/nettle/version.h",
                    "hogweed": "include/nettle/version.h",
                    "gnutls": "include/gnutls/gnutls.h"}[target]
        evidence = build_evidence[identity][source]
        return ([plain_inline(evidence["rows"][relative])],
                [evidence["bodies"][relative]])

    for row in rows:
        platform, arch, slice_name = row["platform"], row["arch"], row["slice"]
        identity = (platform, arch, slice_name)
        libraries = row["libraries"]
        need(type(libraries) is dict and set(libraries) == set(library_map),
             "Crypto row library set")
        observations = {}
        for target, library in libraries.items():
            keys(library, "version thin thinHeaders packaged packagedHeaders correspondence "
                          "frameworkLayout headerCorrespondence architectures defined undefined")
            need(type(library["version"]) is str and
                 library["version"] == CRYPTO_VERSIONS[target],
                 "Crypto library version: " + target)
            directory, archive_name, binary_name = library_map[target]
            thin_path = ("dist/" + directory + "/" + platform + "/thin/" + arch +
                         "/lib/" + archive_name)
            thin = crypto_plain_row(library["thin"], thin_path)
            if target == "Libsmbclient":
                need((thin["bytes"], thin["sha256"]) ==
                     CRYPTO_SMB_PINS[platform + "/" + arch],
                     "Retained Samba thin archive identity")

            framework = ("artifacts/" + target + ".xcframework/" + slice_name + "/" +
                         binary_name + ".framework")
            packaged_path = (framework + "/Versions/A/" + binary_name
                             if slice_name.startswith("macos-") else
                             framework + "/" + binary_name)
            packaged_expected, packaged_archive_name = crypto_zip_row(
                catalog, target, packaged_path)
            packaged = crypto_plain_row(library["packaged"], packaged_path)
            need(exact_equal(packaged, packaged_expected),
                 "Crypto packaged binary/raw ZIP correspondence")
            packaged_body = crypto_zip_file(catalog, target, packaged_archive_name)
            need((len(packaged_body), sha(packaged_body)) ==
                 (packaged["bytes"], packaged["sha256"]),
                 "Crypto packaged binary body identity")
            segments = _macho_arch_hashes(packaged_body)
            slice_arches = expected_slice_arches[slice_name]
            if segments is None:
                need(packaged_body.startswith(b"!<arch>\n") and len(slice_arches) == 1,
                     "Crypto one-arch package must be an ar body or a parsed FAT container")
                segments = {slice_arches[0]: (len(packaged_body), sha(packaged_body))}
            need(set(segments) == set(slice_arches),
                 "Crypto exact packaged architecture set")
            segment = {"bytes": segments[arch][0], "sha256": segments[arch][1]}
            keys(library["correspondence"], "bytes sha256")
            need(exact_equal(library["correspondence"], segment) and
                 exact_equal(segment, {"bytes": thin["bytes"], "sha256": thin["sha256"]}),
                 "Crypto packaged/thin archive segment correspondence")

            architectures = crypto_recorded_text(library["architectures"], "architecture")
            need(sorted(architectures.split()) == slice_arches,
                 "Crypto recorded architecture set")
            defined = crypto_normalize_symbols(
                crypto_recorded_text(library["defined"], "defined symbols"))
            undefined = crypto_normalize_symbols(
                crypto_recorded_text(library["undefined"], "undefined symbols"))
            observations[target] = {"defined": defined, "undefined": undefined}

            expected_thin_headers, thin_bodies = thin_header_material(identity, target)
            need(type(library["thinHeaders"]) is list and
                 len(library["thinHeaders"]) == len(expected_thin_headers),
                 "Crypto thin header count")
            for reported, expected in zip(library["thinHeaders"],
                                          expected_thin_headers, strict=True):
                crypto_plain_row(reported, expected["path"], 4 * MIB)
                need(exact_equal(reported, expected),
                     "Crypto thin header/build evidence correspondence")

            header_names = ({"gmp": ("gmp.h",),
                             "nettle": ("version.h",),
                             "hogweed": ("nettle/version.h",),
                             "gnutls": ("gnutls.h",),
                             "Libsmbclient": tuple(CRYPTO_SMB_HEADERS)}[target])
            expected_packaged_headers, packaged_header_bodies = [], []
            for header_name in header_names:
                header_path = framework + "/Headers/" + (
                    "samba-4.0/" + header_name if target == "Libsmbclient" else header_name)
                expected_header, zip_name = crypto_zip_row(catalog, target, header_path)
                header_body = crypto_zip_file(catalog, target, zip_name, 4 * MIB)
                expected_packaged_headers.append(expected_header)
                packaged_header_bodies.append(header_body)
                if target == "Libsmbclient":
                    size, checksum = CRYPTO_SMB_HEADERS[header_name]
                    need((len(header_body), sha(header_body)) == (size, checksum),
                         "Retained Samba packaged public header identity")
                else:
                    crypto_public_header(target, header_body)
            need(type(library["packagedHeaders"]) is list and
                 len(library["packagedHeaders"]) == len(expected_packaged_headers),
                 "Crypto packaged header count")
            for reported, expected in zip(library["packagedHeaders"],
                                          expected_packaged_headers, strict=True):
                crypto_plain_row(reported, expected["path"], 4 * MIB)
                need(exact_equal(reported, expected),
                     "Crypto packaged header/raw ZIP correspondence")

            if target == "gmp":
                thin_evidence = []
                for candidate in CRYPTO_MATRIX:
                    if candidate[2] != slice_name:
                        continue
                    candidate_rows, candidate_bodies = thin_header_material(candidate, target)
                    thin_evidence.append(((candidate[0], candidate[1]),
                                          candidate_bodies[0], candidate_rows[0]))
                expected_headers = crypto_header_correspondence(
                    target, packaged_header_bodies[0], thin_evidence)
            elif target == "Libsmbclient":
                expected_headers = {"kind": "exact-checked-header-bytes"}
            else:
                expected_headers = crypto_header_correspondence(
                    target, packaged_header_bodies[0],
                    [(identity[:2], thin_bodies[0], expected_thin_headers[0])])
            need(exact_equal(library["headerCorrespondence"], expected_headers),
                 "Crypto public-header correspondence policy")
            expected_layout = crypto_framework_layout(
                target, binary_name, slice_name, catalog)
            need(exact_equal(library["frameworkLayout"], expected_layout),
                 "Crypto framework layout/raw ZIP correspondence")

        symbol_policy = crypto_validate_symbols(observations)
        need(exact_equal(row["sambaGnuTLSImports"],
                         symbol_policy["sambaGnuTLSImports"]) and
             exact_equal(row["helperImports"], symbol_policy["helperImports"]),
             "Crypto symbol-boundary receipt")

    need(inline_budget[0] <= CRYPTO_MAX_INLINE_RAW,
         "Crypto aggregate inline raw-body cap")
    crypto_reader_caps(value)
    canonical = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    need(body == canonical and len(canonical) <= CRYPTO_MAX_RECEIPT,
         "Crypto canonical final receipt bytes")
    return value


def subtitle_inline(row, label, budget, maximum=SUBTITLE_MAX_BODY):
    keys(row, "bytes sha256 bodyBase64")
    count, encoded = row["bytes"], row["bodyBase64"]
    need(type(count) is int and 0 < count <= maximum and
         isinstance(encoded, str) and
         len(encoded) <= ((maximum + 2) // 3) * 4,
         "Subtitle inline-body bound: " + label)
    digest(row["sha256"])
    try:
        body = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as error:
        raise ValueError("Subtitle inline body is not canonical base64: " + label) from error
    need(base64.b64encode(body).decode("ascii") == encoded and
         len(body) == count and sha(body) == row["sha256"],
         "Subtitle inline raw-body identity: " + label)
    budget[0] += len(body)
    need(budget[0] <= SUBTITLE_MAX_INLINE_RAW,
         "Subtitle aggregate raw-body cap")
    return body


def subtitle_text(body, label):
    text = body.decode("utf-8", errors="strict")
    need(text and "\x00" not in text, "Subtitle bounded text: " + label)
    return text


def subtitle_platform(platform, arch):
    sdk_stem = {"ios": "iPhoneOS", "isimulator": "iPhoneSimulator",
                "tvos": "AppleTVOS", "tvsimulator": "AppleTVSimulator",
                "macos": "MacOSX"}[platform]
    cpu = "x86_64" if arch == "x86_64" else "arm64"
    target = {"ios": cpu + "-apple-ios15.0",
              "isimulator": cpu + "-apple-ios15.0-simulator",
              "tvos": cpu + "-apple-tvos15.0",
              "tvsimulator": cpu + "-apple-tvos15.0-simulator",
              "macos": cpu + "-apple-macos12.0"}[platform]
    minimum = {"ios": "-mios-version-min=15.0",
               "isimulator": "-mios-simulator-version-min=15.0",
               "tvos": "-mtvos-version-min=15.0",
               "tvsimulator": "-mtvos-simulator-version-min=15.0",
               "macos": "-mmacosx-version-min=12.0"}[platform]
    host_cpu = "aarch64" if cpu == "arm64" else cpu
    host = host_cpu + "-" + {"ios": "ios", "isimulator": "ios",
                         "tvos": "tvos", "tvsimulator": "tvos",
                         "macos": "apple"}[platform] + "-darwin"
    subsystem = {"ios": "ios", "isimulator": "ios-simulator",
                 "tvos": "tvos", "tvsimulator": "tvos-simulator",
                 "macos": "macos"}[platform]
    cpu_family = "x86_64" if arch == "x86_64" else "aarch64"
    return sdk_stem, target, minimum, host, subsystem, cpu_family, cpu


def subtitle_environment(value, work, library, platform, arch):
    expected_keys = {"LC_CTYPE", "MAKEFLAGS", "CMAKE_BUILD_PARALLEL_LEVEL",
                     "CARGO_BUILD_JOBS", "CC", "CXX", "CURRENT_ARCH", "CFLAGS",
                     "CPPFLAGS", "CXXFLAGS", "ASMFLAGS", "LDFLAGS",
                     "PKG_CONFIG_LIBDIR", "PKG_CONFIG_PATH", "PATH"}
    need(type(value) is dict and set(value) == expected_keys and
         all(isinstance(item, str) and len(item) <= 65536 and "\x00" not in item
             for item in value.values()),
         "Subtitle environment keys/text differ from BaseBuild")
    sdk_stem, target, minimum, _, _, _, _ = subtitle_platform(platform, arch)
    prefix = "-arch " + arch + " -isysroot "
    suffix = " -target " + target + " " + minimum
    fork = " -DHAVE_FORK=0" if platform in ("tvos", "tvsimulator") else ""
    freetype = work / 'dist/libfreetype' / platform / 'thin' / arch
    dependency_cflags = ' -I' + str(freetype / 'include') if library == 'libbluray' else ''
    cflags = value["CFLAGS"]
    optimization = ' -O2' if library == 'libunibreak' else ''
    if optimization:
        need(cflags.endswith(optimization) and
             [item for item in cflags.split() if item.startswith('-O')] == ['-O2'],
             'Unibreak shipping CFLAGS require exactly one trailing -O2')
        cflags = cflags[:-len(optimization)]
    need(cflags.startswith(prefix) and cflags.endswith(suffix + fork + dependency_cflags),
         "Subtitle C flags differ from fixed platform policy")
    sdk_root = cflags[len(prefix):len(cflags) - len(suffix + fork + dependency_cflags)]
    sdk_pattern = (r".*/" + re.escape(sdk_stem) +
                   r"\.platform/Developer/SDKs/" + re.escape(sdk_stem) +
                   r"(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3})?\.sdk")
    need(sdk_root.startswith("/") and "\x00" not in sdk_root and
         ".." not in Path(sdk_root).parts and
         re.fullmatch(sdk_pattern, sdk_root) is not None,
         "Subtitle SDK root differs from fixed platform")
    build = work / "dist" / library / platform / "scratch" / arch
    pkg_roots = [str(work / "dist" / name / platform / "thin" / arch /
                     "lib/pkgconfig") for name in OPENSSL_PKG_LIBRARIES]
    pkg_roots.append(str(build / "vortx-sdk-pkgconfig"))
    expected = {"LC_CTYPE": "C", "MAKEFLAGS": "-j1",
                "CMAKE_BUILD_PARALLEL_LEVEL": "1", "CARGO_BUILD_JOBS": "1",
                "CC": "/usr/bin/clang", "CXX": "/usr/bin/clang++",
                "CURRENT_ARCH": arch, "CFLAGS": cflags + optimization, "CPPFLAGS": cflags,
                "CXXFLAGS": cflags + optimization, "ASMFLAGS": cflags,
                "LDFLAGS": "-lc++ " + prefix + sdk_root + suffix +
                           ((' -L' + str(freetype / 'lib') + ' -lfreetype') if library == 'libbluray' else ''),
                "PKG_CONFIG_LIBDIR": ":".join(pkg_roots), "PKG_CONFIG_PATH": "",
                "PATH": OPENSSL_DEFAULT_PATH}
    need(exact_equal(value, expected),
         "Subtitle environment differs from exact BaseBuild output")
    return sdk_root, cflags, expected["LDFLAGS"]


def subtitle_meson_cross(body, work, library, platform, arch,
                         sdk_root, cflags, ldflags):
    text = subtitle_text(body, "Meson cross file")
    tools = {}
    for name in ("ar", "strip"):
        matches = re.findall(r"^" + name + r" = '([^']+)'$", text, re.M)
        need(len(matches) == 1 and matches[0].startswith("/") and
             ".." not in Path(matches[0]).parts and "\x00" not in matches[0] and
             Path(matches[0]).name == name and len(matches[0]) <= 4096,
             "Subtitle Meson cross-file SDK tool: " + name)
        tools[name] = matches[0]
    _, _, _, _, subsystem, cpu_family, cpu = subtitle_platform(platform, arch)
    quoted_c = ", ".join("'" + item + "'" for item in cflags.split(" "))
    quoted_ld = ", ".join("'" + item + "'" for item in ldflags.split(" "))
    expected = """[binaries]
c = '/usr/bin/clang'
cpp = '/usr/bin/clang++'
objc = '/usr/bin/clang'
objcpp = '/usr/bin/clang++'
ar = '{ar}'
strip = '{strip}'
pkg-config = 'pkg-config'

[properties]
has_function_printf = true
has_function_hfkerhisadf = false

[host_machine]
system = 'darwin'
subsystem = '{subsystem}'
kernel = 'xnu'
cpu_family = '{cpu_family}'
cpu = '{cpu}'
endian = 'little'

[built-in options]
default_library = 'static'
buildtype = 'release'
prefix = '{prefix}'
c_args = [{cflags}]
cpp_args = [{cflags}]
objc_args = [{cflags}]
objcpp_args = [{cflags}]
c_link_args = [{ldflags}]
cpp_link_args = [{ldflags}]
objc_link_args = [{ldflags}]
objcpp_link_args = [{ldflags}]""".format(
        ar=tools["ar"], strip=tools["strip"], subsystem=subsystem,
        cpu_family=cpu_family, cpu=cpu,
        prefix=work / "dist" / library / platform / "thin" / arch,
        cflags=quoted_c, ldflags=quoted_ld)
    need(text.rstrip("\n") == expected,
         "Subtitle Meson cross file differs from BaseBuild")


def subtitle_build_options(body, library, prefix):
    value = crypto_load(body)
    need(type(value) is list and len(value) <= 10000,
         "Subtitle Meson build-option set is unbounded")
    options = {}
    for row in value:
        need(type(row) is dict and isinstance(row.get("name"), str) and
             row["name"] not in options,
             "Subtitle Meson build-option row is malformed or duplicate")
        options[row["name"]] = row.get("value")
    expected = {"default_library": "static", "buildtype": "release",
                "prefix": str(prefix)}
    for argument in SUBTITLE_ARGUMENTS[library]:
        if argument == "--wrap-mode=nodownload":
            expected["wrap_mode"] = "nodownload"
        elif argument == '--force-fallback-for=libudfread':
            expected['force_fallback_for'] = ['libudfread']
        else:
            name, raw = argument.removeprefix("-D").split("=", 1)
            expected[name] = {"true": True, "false": False}.get(raw, raw)
    need(all(name in options and exact_equal(options[name], item)
             for name, item in expected.items()),
         "Subtitle Meson effective options differ from fixed arguments")


def subtitle_dependencies(body):
    value = crypto_load(body)
    need(type(value) is list and len(value) <= 10000,
         "Subtitle Meson dependency set is unbounded")
    text = json.dumps(value, sort_keys=True, separators=(",", ":")).casefold()
    forbidden = ("freetype", "glib", "gobject", "cairo", "chafa", "libpng",
                 "zlib", "icu", "graphite2", "fontations", "harfrust", "kbts",
                 "wrapdb")
    need(not any(re.search(r"(?<![a-z0-9_])" + re.escape(name) +
                           r"(?![a-z0-9_])", text) for name in forbidden),
         "Subtitle Meson dependency closure admits a disabled integration")


def subtitle_compile_command(row, arch, sdk_root, target, minimum, fribidi_role=None):
    need(type(row) is dict and 0 < len(row) <= 16 and
         all(isinstance(name, str) and name and len(name) <= 128 for name in row),
         "Subtitle compile-command row is malformed or unbounded")
    payloads = []
    if "arguments" in row:
        arguments = row["arguments"]
        need(type(arguments) is list and 0 < len(arguments) <= 16384 and
             all(isinstance(item, str) and item and len(item) <= 65536 and
                 "\x00" not in item for item in arguments) and
             sum(len(item) for item in arguments) <= 1024 * 1024,
             "Subtitle compile-command arguments are malformed or unbounded")
        payloads.append(arguments)
    if "command" in row:
        command = row["command"]
        need(isinstance(command, str) and command and len(command) <= 1024 * 1024 and
             "\x00" not in command,
             "Subtitle compile-command text is malformed or unbounded")
        try:
            arguments = shlex.split(command, posix=True)
        except ValueError as error:
            raise ValueError("Subtitle compile-command text is not shell-tokenizable") from error
        need(arguments and len(arguments) <= 16384 and
             all(item and len(item) <= 65536 for item in arguments),
             "Subtitle compile-command token set is malformed or unbounded")
        payloads.append(arguments)
    need(payloads, "Subtitle compile-command row has no command payload")
    for arguments in payloads:
        alternate = [item for item in arguments
                     if ((item.startswith("-target") and item != "-target")
                         or item.startswith("--target")
                         or (item.startswith("-isysroot") and item != "-isysroot")
                         or item.startswith("--sysroot"))]
        need(not alternate,
             "Subtitle compile-command has an alternate target/sysroot spelling")

        def values(flag):
            indexes = [index for index, item in enumerate(arguments) if item == flag]
            need(all(index + 1 < len(arguments) for index in indexes),
                 "Subtitle compile-command has an unterminated target flag")
            return [arguments[index + 1] for index in indexes]

        deployment = [item for item in arguments if re.fullmatch(
            r"-(?:mios|mios-simulator|mtvos|mtvos-simulator|mmacosx)-version-min=.*",
            item)]
        arch_values = values("-arch")
        sdk_values = values("-isysroot")
        target_values = values("-target")
        if fribidi_role is not None:
            source, output, native = fribidi_role
            need(arguments[0] == ("cc" if native else "/usr/bin/clang") and
                 not any((item.startswith("-o") and item != "-o") or
                         item.startswith("--output") or item.startswith("@") or item == "--"
                         for item in arguments) and
                 values("-c") == [source] and values("-o") == [output] and
                 [item for item in arguments
                  if not item.startswith("-") and item.endswith(".c")] == [source],
                 "FriBidi compile-command compiler/source/output differs from its fixed role")
            if native:
                toggles = [item for item in arguments if re.match(
                    r"^-[DU](?:HAVE_CONFIG_H|HAVE_STRINGIZE|DONT_HAVE_FRIBIDI_CONFIG_H)(?:=|$)", item)]
                need(not arch_values and not sdk_values and not target_values and not deployment and
                     not any(item.startswith("-arch") for item in arguments) and
                     not any(item in ("-D", "-U") or
                             (item.startswith(("-D", "-U")) and any(char.isspace() for char in item))
                             for item in arguments) and
                     not any(item.startswith(("-Wp,", "-Xpreprocessor", "-Xclang"))
                             for item in arguments) and
                     sorted(toggles) == sorted([
                         "-UHAVE_CONFIG_H", "-DHAVE_STRINGIZE", "-DDONT_HAVE_FRIBIDI_CONFIG_H"]),
                     "FriBidi native generator has shipping target flags or changed configuration defines")
                continue
        need(arch_values and all(item == arch for item in arch_values) and
             sdk_values and all(item == sdk_root for item in sdk_values) and
             target_values and all(item == target for item in target_values) and
             deployment and all(item == minimum for item in deployment),
             "Subtitle compile-command target flags differ from fixed policy")


def subtitle_fribidi_compile_commands(commands, build, arch, sdk_root, target, minimum):
    # Exact pinned 1.0.17 gen.tab/meson.build: 1 + 6*2 native objects.
    root = "../../../../libfribidi-1.0.17-6949dcde/"
    expected = {}
    for program in ("gen-unicode-version", "gen-bidi-type-tab",
                    "gen-joining-type-tab", "gen-arabic-shaping-tab",
                    "gen-mirroring-tab", "gen-brackets-tab", "gen-brackets-type-tab"):
        names = (program + ".c",) if program == "gen-unicode-version" else (program + ".c", "packtab.c")
        for name in names:
            expected[(root + "gen.tab/" + name, "gen.tab/" + program + ".p/" + name + ".o")] = True
    # Pinned lib/meson.build's complete shipping source list (deprecated=true).
    for name in ("fribidi.c", "fribidi-arabic.c", "fribidi-bidi.c", "fribidi-bidi-types.c",
                 "fribidi-char-sets.c", "fribidi-char-sets-cap-rtl.c",
                 "fribidi-char-sets-cp1255.c", "fribidi-char-sets-cp1256.c",
                 "fribidi-char-sets-iso8859-6.c", "fribidi-char-sets-iso8859-8.c",
                 "fribidi-char-sets-utf8.c", "fribidi-deprecated.c", "fribidi-joining.c",
                 "fribidi-joining-types.c", "fribidi-mirroring.c", "fribidi-brackets.c",
                 "fribidi-run.c", "fribidi-shape.c"):
        expected[(root + "lib/" + name, "lib/libfribidi.a.p/" + name + ".o")] = False
    need(type(commands) is list and len(commands) == len(expected),
         "FriBidi compile-command set must contain exactly 13 native and 18 shipping rows")
    seen = set()
    for row in commands:
        need(type(row) is dict and {"directory", "file", "output"} <= set(row) and
             not set(row) - {"directory", "file", "output", "command", "arguments"} and
             isinstance(row["file"], str) and isinstance(row["output"], str) and
             row["directory"] == str(build),
             "FriBidi compile-command metadata differs from the fixed build directory/schema")
        identity = (row["file"], row["output"])
        need(identity in expected and identity not in seen,
             "FriBidi compile-command has an unknown or duplicate source/output role")
        seen.add(identity)
        subtitle_compile_command(row, arch, sdk_root, target, minimum,
                                 (identity[0], identity[1], expected[identity]))
    need(seen == set(expected), "FriBidi compile-command native/shipping closure is incomplete")


def subtitle_pc(body, library, prefix):
    text = subtitle_text(body, "pkg-config metadata")
    variables, fields = {}, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and (":" not in line or line.index("=") < line.index(":")):
            name, item = line.split("=", 1)
            target = variables
        elif ":" in line:
            name, item = line.split(":", 1)
            target = fields
        else:
            need(False, "Subtitle pkg-config metadata has an unclassified line")
        name, item = name.strip(), item.strip()
        need(name and name not in target and "\x00" not in item,
             "Subtitle pkg-config metadata has a duplicate field")
        target[name] = item
    need(variables.get("prefix") == str(prefix) and
         fields.get("Version") == SUBTITLE_VERSIONS[library],
         "Subtitle pkg-config prefix/version differs from fixed build")
    if library == 'libbluray':
        required = re.sub(r'\s+>=\s+[0-9]+(?:\.[0-9]+){0,3}', '', fields.get('Requires.private', ''))
        need(not fields.get('Requires', '') and sorted(required.replace(',', ' ').split()) == ['freetype2', 'libxml-2.0']
             and not any(item in text for item in ('-ludfread', '/opt/homebrew', '/usr/local/', 'fontconfig')),
             'Libbluray pkg-config dependency closure differs')
    else:
        need(not fields.get("Requires", "") and not fields.get("Requires.private", ""),
             "Subtitle pkg-config unexpectedly admits another package dependency")
    expected_lib = {"libunibreak": "-lunibreak", "libfribidi": "-lfribidi",
                    "libharfbuzz": "-lharfbuzz", 'libbluray': '-lbluray'}[library]
    need(expected_lib in fields.get("Libs", "").split(),
         "Subtitle pkg-config library identity differs")


def subtitle_header(body, library):
    text = subtitle_text(body, "installed version header")
    if library == 'libbluray':
        for name, value in (('BLURAY_VERSION_MAJOR', '1'), ('BLURAY_VERSION_MINOR', '5'), ('BLURAY_VERSION_MICRO', '0')):
            _bluray_define(text, name, value)
        _bluray_define(text, 'BLURAY_VERSION_STRING', '1.5.0', True)
    elif library == "libunibreak":
        definitions = re.findall(r"^[ \t]*#[ \t]*define[ \t]+UNIBREAK_VERSION\b[^\r\n]*$",
                                 text, re.M)
        versions = re.findall(
            r"^[ \t]*#[ \t]*define[ \t]+UNIBREAK_VERSION[ \t]+"
            r"(0[xX][0-9a-fA-F]+)[ \t]*"
            r"(?:/\*(?:[^*\r\n]|\*(?!/)){0,4096}\*/[ \t]*)?$", text, re.M)
        symbols = re.findall(r"^\s*extern\s+const\s+int\s+unibreak_version\s*;\s*$",
                             text, re.M)
        need(len(definitions) == 1 and versions == ["0x0800"] and len(symbols) == 1,
             "Unibreak installed header version")
    elif library == "libfribidi":
        expected = {"FRIBIDI_MAJOR_VERSION": "1", "FRIBIDI_MINOR_VERSION": "0",
                    "FRIBIDI_MICRO_VERSION": "17"}
        for name, version in expected.items():
            definitions = re.findall(r"^[ \t]*#[ \t]*define[ \t]+" +
                                     re.escape(name) + r"\b[^\r\n]*$", text, re.M)
            values = re.findall(r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(name) +
                                r"[ \t]+([0-9]+)[ \t]*$",
                                text, re.M)
            need(len(definitions) == 1 and values == [version],
                 "FriBidi installed header version: " + name)
        definitions = re.findall(
            r"^[ \t]*#[ \t]*define[ \t]+FRIBIDI_VERSION\b[^\r\n]*$", text, re.M)
        strings = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+FRIBIDI_VERSION[ \t]+"([^"]+)"[ \t]*$',
                             text, re.M)
        need(len(definitions) == 1 and strings == ["1.0.17"],
             "FriBidi installed header string version")
    elif library == "libharfbuzz":
        expected = {"HB_VERSION_MAJOR": "15", "HB_VERSION_MINOR": "0",
                    "HB_VERSION_MICRO": "0"}
        for name, version in expected.items():
            definitions = re.findall(r"^[ \t]*#[ \t]*define[ \t]+" +
                                     re.escape(name) + r"\b[^\r\n]*$", text, re.M)
            values = re.findall(r"^[ \t]*#[ \t]*define[ \t]+" + re.escape(name) +
                                r"[ \t]+([0-9]+)[ \t]*$",
                                text, re.M)
            need(len(definitions) == 1 and values == [version],
                 "HarfBuzz installed header version: " + name)
        definitions = re.findall(
            r"^[ \t]*#[ \t]*define[ \t]+HB_VERSION_STRING\b[^\r\n]*$", text, re.M)
        strings = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+HB_VERSION_STRING[ \t]+"([^"]+)"[ \t]*$',
                             text, re.M)
        need(len(definitions) == 1 and strings == ["15.0.0"],
             "HarfBuzz installed header string version")


def _bluray_fields(body, label):
    variables, fields = {}, {}
    for raw in subtitle_text(body, label).splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line and (':' not in line or line.index('=') < line.index(':')):
            name, value = line.split('=', 1)
            target = variables
        elif ':' in line:
            name, value = line.split(':', 1)
            target = fields
        else:
            raise ValueError('libbluray dependency metadata has an unknown line')
        name, value = name.strip(), value.strip()
        if not name or name in target:
            raise ValueError('libbluray dependency metadata has duplicate fields')
        target[name] = value
    return variables, fields


def _bluray_define(text, name, expected, quoted=False):
    lines = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + re.escape(name) + r'\b[^\r\n]*$', text, re.M)
    undefs = re.findall(r'^[ \t]*#[ \t]*undef[ \t]+' + re.escape(name) + r'\b[^\r\n]*$', text, re.M)
    value = ('"' + re.escape(expected) + '"') if quoted else re.escape(expected)
    if undefs or len(lines) != 1 or re.fullmatch(r'[ \t]*#[ \t]*define[ \t]+' + re.escape(name) + r'[ \t]+' + value + r'[ \t]*', lines[0]) is None:
        raise ValueError('libbluray configured macro differs: ' + name)


def _bluray_config(generated):
    config = subtitle_text(generated['config.h'], 'libbluray config.h')
    _bluray_define(config, 'VERSION', '1.5.0', True)
    for name in ('HAVE_FT2', 'HAVE_LIBXML2', 'HAVE_LIBUDFREAD'):
        _bluray_define(config, name, '1')
    if re.search(r'^[ \t]*#[ \t]*define[ \t]+HAVE_FONTCONFIG\b', config, re.M):
        raise ValueError('libbluray configured fontconfig despite disabled policy')
    _bluray_define(subtitle_text(generated['contrib/libudfread/config.h'], 'embedded UDF config'), 'HAVE_UDFREAD_VERSION_H', '1')


def _bluray_dependencies(body, work, platform, arch, sdk_root):
    value = crypto_load(body)
    if type(value) is not list or len(value) > 10000:
        raise ValueError('libbluray dependency set is unbounded')
    rows, frameworks = {}, set()
    allowed = {'freetype2', 'libxml-2.0', 'libudfread', 'threads', 'appleframeworks'}
    ft = str(work / 'dist/libfreetype' / platform / 'thin' / arch)
    for row in value:
        if (type(row) is not dict or not isinstance(row.get('name'), str) or row['name'] not in allowed
                or (row['name'] in rows and row['name'] != 'appleframeworks')):
            raise ValueError('libbluray dependency name is unknown or duplicate')
        name = row['name']
        rows[name] = row
        for key in ('compile_args', 'link_args'):
            if type(row.get(key)) is not list or any(not isinstance(item, str) or len(item) > 65536 or '\x00' in item for item in row[key]):
                raise ValueError('libbluray dependency arguments are malformed')
        all_args = row['compile_args'] + row['link_args']
        if any('/opt/homebrew' in item or '/usr/local/' in item or 'wrapdb' in item.casefold() for item in all_args):
            raise ValueError('libbluray admits a host/fetch dependency')
        if name == 'freetype2':
            if ('-I' + ft + '/include/freetype2' not in row['compile_args']
                    or not ({'-lfreetype', ft + '/lib/libfreetype.a'} & set(row['link_args']))
                    or row['compile_args'] != ['-I' + ft + '/include/freetype2']
                    or set(row['link_args']) - {'-L' + ft + '/lib', '-lfreetype', ft + '/lib/libfreetype.a',
                                                '-lz', sdk_root + '/usr/lib/libz.tbd', sdk_root + '/usr/lib/libz.1.tbd'}):
                raise ValueError('libbluray Freetype dependency is not the retained target provider')
        elif name == 'libxml-2.0':
            if ('-I' + sdk_root + '/usr/include/libxml2' not in row['compile_args']
                    or row['compile_args'] != ['-I' + sdk_root + '/usr/include/libxml2']
                    or not row['link_args']
                    or set(row['link_args']) - {'-lxml2', sdk_root + '/usr/lib/libxml2.tbd', sdk_root + '/usr/lib/libxml2.2.tbd'}):
                raise ValueError('libbluray XML dependency is not the selected SDK provider')
        elif name == 'libudfread':
            if (row.get('type') != 'internal' or row.get('version') != '1.2.0'
                    or any(item.startswith('-L') or item == '-ludfread' for item in row['link_args'])):
                raise ValueError('libbluray admits external/wrong-version UDF')
        elif name == 'threads':
            if any(item != '-pthread' for item in all_args):
                raise ValueError('libbluray thread dependency admits foreign flags')
        elif name == 'appleframeworks':
            args = row['link_args']
            admitted = {'CoreFoundation', 'DiskArbitration'} if platform == 'macos' else {'CoreFoundation'}
            if (row['compile_args'] or not args or len(args) % 2
                    or any(args[index] != '-framework' or args[index + 1] not in admitted
                           for index in range(0, len(args), 2))):
                raise ValueError('libbluray Apple framework selection differs')
            selected = set(args[1::2])
            if len(selected) != len(args) // 2 or selected & frameworks:
                raise ValueError('libbluray Apple framework dependency is duplicated')
            frameworks.update(selected)
    if not {'freetype2', 'libxml-2.0'} <= set(rows):
        raise ValueError('libbluray enabled dependencies are missing')


def _bluray_dependency_paths(work, platform, arch, sdk_root):
    ft = 'dist/libfreetype/' + platform + '/thin/' + arch
    metadata = 'dist/libbluray/' + platform + '/scratch/' + arch + '/vortx-sdk-pkgconfig/'
    return {
        'freetypeArchive': ft + '/lib/libfreetype.a',
        'freetypePC': ft + '/lib/pkgconfig/freetype2.pc',
        'freetypeHeader': ft + '/include/freetype2/freetype/freetype.h',
        'sdkLibxmlPC': metadata + 'libxml-2.0.pc',
        'sdkLibxmlHeader': sdk_root + '/usr/include/libxml2/libxml/xmlversion.h',
        'sdkLibxmlStub': sdk_root + '/usr/lib/libxml2.tbd',
        'sdkZlibPC': metadata + 'zlib.pc',
        'sdkZlibHeader': sdk_root + '/usr/include/zlib.h',
        'sdkZlibStub': sdk_root + '/usr/lib/libz.tbd',
    }


def _bluray_dependency_inputs(value, work, platform, arch, sdk_root, budget):
    expected = _bluray_dependency_paths(work, platform, arch, sdk_root)
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('libbluray dependency input family differs')
    decoded, before = {}, budget[0]
    for name, relative in expected.items():
        row = value[name]
        fields = {'path', 'bytes', 'sha256'} | (set() if name == 'freetypeArchive' else {'bodyBase64'})
        if (type(row) is not dict or set(row) != fields or row['path'] != relative
                or type(row['bytes']) is not int or not 0 < row['bytes'] <= (512 * 1024 * 1024 if name == 'freetypeArchive' else 4 * 1024 * 1024)
                or not isinstance(row['sha256'], str) or re.fullmatch(r'[0-9a-f]{64}', row['sha256']) is None):
            raise ValueError('libbluray dependency input row differs: ' + name)
        if name != 'freetypeArchive':
            decoded[name] = subtitle_inline({key: row[key] for key in ('bytes', 'sha256', 'bodyBase64')}, 'bluray dependency ' + name, budget, 4 * 1024 * 1024)
    if budget[0] - before > 8 * 1024 * 1024:
        raise ValueError('libbluray dependency raw-body family exceeds cap')
    ft = str(work / 'dist/libfreetype' / platform / 'thin' / arch)
    variables, fields = _bluray_fields(decoded['freetypePC'], 'retained Freetype pc')
    if (variables != {'prefix': ft, 'includedir': '${prefix}/include', 'libdir': '${prefix}/lib'}
            or fields.get('Requires') != 'zlib' or fields.get('Requires.private', '')
            or fields.get('Libs') != '-L${libdir} -lfreetype'
            or fields.get('Cflags') != '-I${includedir}/freetype2'
            or re.fullmatch(r'[0-9]{1,3}(?:\.[0-9]{1,3}){1,3}', fields.get('Version', '')) is None):
        raise ValueError('libbluray retained Freetype pkg-config identity differs')
    ft_header = subtitle_text(decoded['freetypeHeader'], 'retained Freetype header')
    for macro in ('FREETYPE_MAJOR', 'FREETYPE_MINOR', 'FREETYPE_PATCH'):
        if len(re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + macro + r'[ \t]+[0-9]{1,3}[ \t]*$', ft_header, re.M)) != 1:
            raise ValueError('libbluray retained Freetype header lacks bounded version macros')
    for kind, macro, include, install_name in (
            ('Libxml', 'LIBXML_DOTTED_VERSION', 'usr/include/libxml2', '/usr/lib/libxml2.2.dylib'),
            ('Zlib', 'ZLIB_VERSION', '', '/usr/lib/libz.1.dylib')):
        header = subtitle_text(decoded['sdk' + kind + 'Header'], 'SDK version header')
        definitions = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + macro + r'\b[^\r\n]*$', header, re.M)
        versions = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + macro + r'[ \t]+"([0-9]{1,3}(?:\.[0-9]{1,3}){1,3})"[ \t]*$', header, re.M)
        if len(definitions) != 1 or len(versions) != 1:
            raise ValueError('libbluray selected SDK version header differs')
        if kind == 'Libxml':
            include_path = (sdk_root + '/' + include).replace('\\', '\\\\').replace('"', '\\"')
            expected_pc = 'Name: libxml-2.0\nDescription: Target Apple SDK libxml2\nVersion: ' + versions[0] + '\nLibs: -lxml2\nCflags: -I"' + include_path + '"\n'
        else:
            expected_pc = 'Name: zlib\nDescription: Target Apple SDK zlib\nVersion: ' + versions[0] + '\nLibs: -lz\nCflags:\n'
        if decoded['sdk' + kind + 'PC'] != expected_pc.encode():
            raise ValueError('libbluray selected SDK pkg-config bytes differ')
        stub = subtitle_text(decoded['sdk' + kind + 'Stub'], 'SDK linker stub')
        escaped = re.escape(install_name)
        if not stub.startswith('--- !tapi-tbd') or len(re.findall(r'(?m)^install-name:[ \t]+(?:' + escaped + r"|'"+ escaped + r"'|\"" + escaped + r'\")[ \t]*$', stub)) != 1:
            raise ValueError('libbluray selected SDK linker identity differs')
    return decoded



def subtitle_record_contract(record, work, library, platform, arch, budget):
    expected_keys = {"schema", "library", "selector", "route", "platform", "arch",
                     "sourceDirectory", "buildDirectory", "prefix", "arguments",
                     "environment", "makeArguments", "generatedConfiguration",
                     "installedIdentity", "compiled", "installed"}
    need(type(record) is dict and set(record) == expected_keys,
         "Subtitle build record keys differ from Swift producer")
    source = work / "dist" / (library + "-" + SUBTITLE_SELECTORS[library])
    build = work / "dist" / library / platform / "scratch" / arch
    prefix = work / "dist" / library / platform / "thin" / arch
    _, _, _, host, _, _, _ = subtitle_platform(platform, arch)
    arguments = (("--host=" + host, "--with-pic", "--enable-static", "--disable-shared")
                 if library == "libunibreak" else SUBTITLE_ARGUMENTS[library])
    fixed = {"schema": 1, "library": library,
             "selector": SUBTITLE_SELECTORS[library],
             "route": SUBTITLE_ROUTES[library], "platform": platform, "arch": arch,
             "sourceDirectory": str(source), "buildDirectory": str(build),
             "prefix": str(prefix), "arguments": list(arguments),
             "makeArguments": (list(SUBTITLE_MAKE_ARGUMENTS)
                               if library == "libunibreak" else []),
             "compiled": True, "installed": True}
    need(all(exact_equal(record[name], item) for name, item in fixed.items()),
         "Subtitle build record identity/options differ from fixed policy")
    sdk_root, cflags, ldflags = subtitle_environment(
        record["environment"], work, library, platform, arch)
    generated_names = set(SUBTITLE_GENERATED[library])
    if library == "libunibreak" and "config.h" in record["generatedConfiguration"]:
        generated_names.add("config.h")
    need(type(record["generatedConfiguration"]) is dict and
         set(record["generatedConfiguration"]) == generated_names and
         type(record["installedIdentity"]) is dict and
         set(record["installedIdentity"]) == set(SUBTITLE_INSTALLED[library]),
         "Subtitle generated/installed file set differs")
    before = budget[0]
    generated = {name: subtitle_inline(record["generatedConfiguration"][name],
                                        library + "/" + name, budget)
                 for name in sorted(generated_names)}
    installed = {name: subtitle_inline(record["installedIdentity"][name],
                                        library + "/" + name, budget)
                 for name in SUBTITLE_INSTALLED[library]}
    need(budget[0] - before <= SUBTITLE_MAX_RECORD_BODIES,
         "Subtitle per-record nested raw-body cap")
    if library == "libunibreak":
        makefile = subtitle_text(generated["Makefile"], "libunibreak Makefile")
        logs = (subtitle_text(generated["config.status"], "libunibreak config.status") +
                "\n" + subtitle_text(generated["config.log"], "libunibreak config.log"))
        assignments = {"CC": record["environment"]["CC"], "CFLAGS": record["environment"]["CFLAGS"],
                       "CPPFLAGS": cflags, "LDFLAGS": ldflags,
                       "PACKAGE_VERSION": "8.0", "prefix": str(prefix)}
        for name, expected in assignments.items():
            modifier_lines = re.findall(
                r"^[ \t]*(?:(?:override|export|private|unexport)[ \t]+)+" +
                re.escape(name) + r"\b[^\r\n]*$", makefile, re.M)
            assignment_lines = re.findall(
                r"^[ \t]*" + re.escape(name) +
                r"[ \t]*(?:::=|::=|:=|\+=|\?=|!=|=)[^\r\n]*$",
                makefile, re.M)
            values = re.findall(r"^[ \t]*" + re.escape(name) +
                                r"[ \t]*=[ \t]*([^\r\n]*?)[ \t]*$",
                                makefile, re.M)
            need(not modifier_lines and len(assignment_lines) == 1 and
                 values == [expected],
                 "Libunibreak generated Makefile assignment differs: " + name)
        need(all(argument in logs for argument in arguments),
             "Libunibreak generated configuration differs from fixed release options")
        if "config.h" in generated:
            subtitle_text(generated["config.h"], "libunibreak config.h")
    else:
        subtitle_meson_cross(generated["crossFile.meson"], work, library, platform,
                             arch, sdk_root, cflags, ldflags)
        ninja = subtitle_text(generated["build.ninja"], library + " build.ninja")
        need("ninja_required_version" in ninja and
             re.search(r"(?i)(?:^|[\s=])(curl|wget)(?:\s|$)|git\s+clone|wrapdb",
                       ninja) is None,
             "Subtitle Ninja file lacks identity or admits a fetch route")
        commands = crypto_load(generated["compile_commands.json"])
        need(type(commands) is list and 0 < len(commands) <= 100000,
             "Subtitle compile-command set is empty or unbounded")
        _, target, minimum, _, _, _, _ = subtitle_platform(platform, arch)
        if library == "libfribidi":
            subtitle_fribidi_compile_commands(commands, build, arch, sdk_root, target, minimum)
        else:
            for command in commands:
                subtitle_compile_command(command, arch, sdk_root, target, minimum)
        subtitle_build_options(
            generated["meson-info/intro-buildoptions.json"], library, prefix)
        if library == 'libbluray':
            _bluray_config(generated)
            _bluray_dependencies(generated['meson-info/intro-dependencies.json'], work, platform, arch, sdk_root)
            source = str(work / 'dist/libbluray-1.5.0-f676408e') + '/'
            udf = False
            for command in commands:
                need(command.get('directory') == str(build) and isinstance(command.get('file'), str)
                     and isinstance(command.get('output'), str),
                     'Libbluray compile directory/source identity differs')
                name = os.path.normpath(os.path.join(str(build), command['file']))
                need(name.startswith(source) and name.endswith('.c'), 'Libbluray compile source escapes its admitted archive')
                need(os.path.normpath(os.path.join(str(build), command['output'])).startswith(str(build) + '/'),
                     'Libbluray compile output escapes its admitted build')
                subtitle_compile_command(command, arch, sdk_root, target, minimum,
                                         (command['file'], command['output'], False))
                udf |= '/contrib/libudfread/src/' in name
            need(udf, 'Libbluray compile evidence lacks embedded UDF sources')
        else:
            subtitle_dependencies(generated["meson-info/intro-dependencies.json"])
    header_name, pc_name = SUBTITLE_INSTALLED[library]
    subtitle_header(installed[header_name], library)
    subtitle_pc(installed[pc_name], library, prefix)
    return installed


def subtitle_record_body(row, expected_path, budget):
    keys(row, "path bytes sha256 bodyBase64")
    need(row["path"] == expected_path and path(row["path"]) == expected_path,
         "Subtitle BOM build-record path")
    inline = {name: row[name] for name in ("bytes", "sha256", "bodyBase64")}
    return subtitle_inline(inline, "build record " + expected_path, budget,
                           SUBTITLE_MAX_RECORD)


def subtitle_builds_receipt(body, zip_bodies, subtitle_archive_sources):
    """Independently bind 36 raw subtitle/bluray records to source and retained ZIPs."""
    need(isinstance(body, bytes) and 0 < len(body) <= SUBTITLE_MAX_RECEIPT,
         "Subtitle BOM raw-byte bound")
    value = crypto_load(body)
    keys(value, "schemaVersion profile sourceProfile versions selectors "
                "releaseSourceBoundary rows retainedLibassCompatibility "
                "retainedBlurayCompatibility retainedFreetypeAuthentication runtimeSecurityTested promotionEligible")
    fixed = {"schemaVersion": 1, "profile": NATIVE_UPGRADES_PROFILE,
             "sourceProfile": SUBTITLE_SOURCE_PROFILE,
             "versions": SUBTITLE_VERSIONS, "selectors": SUBTITLE_SELECTORS,
             "retainedLibassCompatibility": "HOLD",
             'retainedBlurayCompatibility': 'HOLD', 'retainedFreetypeAuthentication': 'HOLD',
             "runtimeSecurityTested": False, "promotionEligible": False}
    need(all(exact_equal(value[name], item) for name, item in fixed.items()),
         "Subtitle BOM identity/open-gate state")
    rows = value["rows"]
    need(type(rows) is list and len(rows) == len(SUBTITLE_MATRIX),
         "Subtitle BOM exact nine-row matrix")

    budget, records = [0], {}
    observed = []
    for row, identity in zip(rows, SUBTITLE_MATRIX, strict=True):
        keys(row, "platform arch slice builds")
        platform, arch, slice_name = identity
        need(exact_equal({name: row[name] for name in ("platform", "arch", "slice")},
                         {"platform": platform, "arch": arch, "slice": slice_name}),
             "Subtitle BOM matrix order/identity")
        builds = row["builds"]
        need(type(builds) is dict and set(builds) == set(SUBTITLE_VERSIONS),
             "Subtitle BOM build family")
        for library in SUBTITLE_VERSIONS:
            build = builds[library]
            keys(build, "record dependencyInputs" if library == 'libbluray' else "record")
            expected_path = ("dist/" + library + "/" + platform + "/scratch/" +
                             arch + "/vortx-subtitle-build.json")
            record_body = subtitle_record_body(build["record"], expected_path, budget)
            records[(platform, arch, library)] = crypto_load(record_body)
        observed.append(identity)
    need(observed == list(SUBTITLE_MATRIX), "Subtitle BOM matrix coverage")

    first = records[(SUBTITLE_MATRIX[0][0], SUBTITLE_MATRIX[0][1], "libunibreak")]
    source_name = first.get("sourceDirectory") if type(first) is dict else None
    need(isinstance(source_name, str) and source_name.startswith("/") and
         "\x00" not in source_name and ".." not in Path(source_name).parts,
         "Subtitle BOM absolute WORK identity")
    source = Path(source_name)
    need(source.parent.name == "dist" and
         source.name == "libunibreak-" + SUBTITLE_SELECTORS["libunibreak"],
         "Subtitle BOM source root layout")
    work = source.parent.parent

    keys(subtitle_archive_sources, "profile roots capture")
    need(subtitle_archive_sources["profile"] == SUBTITLE_SOURCE_PROFILE,
         "Subtitle archive-source profile")
    capture = subtitle_archive_sources["capture"]
    keys(capture, "tar receipt")
    source_receipt = capture["receipt"]
    keys(source_receipt, "logicalPath name bytes sha256")
    need(source_receipt["logicalPath"] == "subtitle-release-sources.json" and
         source_receipt["name"] == SUBTITLE_CAPTURE_RECEIPT_OUTPUT and
         type(source_receipt["bytes"]) is int and
         0 < source_receipt["bytes"] <= CRYPTO_MAX_RECEIPT,
         "Subtitle source receipt metadata")
    digest(source_receipt["sha256"])
    expected_boundary = {"path": "dist/subtitle-sources-v3/receipt.json",
                         "bytes": source_receipt["bytes"],
                         "sha256": source_receipt["sha256"]}
    keys(value["releaseSourceBoundary"], "path bytes sha256")
    need(exact_equal(value["releaseSourceBoundary"], expected_boundary),
         "Subtitle BOM/source-helper raw receipt correspondence")

    catalog = crypto_zip_catalog(zip_bodies, SUBTITLE_LIBRARIES)
    library_map = {target: binary_name
                   for target, _, _, binary_name in SUBTITLE_LIBRARIES}
    for platform, arch, slice_name in SUBTITLE_MATRIX:
        for library in SUBTITLE_VERSIONS:
            installed = subtitle_record_contract(
                records[(platform, arch, library)], work, library, platform, arch, budget)
            if library == 'libbluray':
                sdk_root, _, _ = subtitle_environment(records[(platform, arch, library)]['environment'], work, library, platform, arch)
                aggregate = value['rows'][list(SUBTITLE_MATRIX).index((platform, arch, slice_name))]['builds'][library]
                _bluray_dependency_inputs(aggregate['dependencyInputs'], work, platform, arch, sdk_root, budget)
            target = SUBTITLE_TARGETS[library]
            binary_name = library_map[target]
            framework = ("artifacts/" + target + ".xcframework/" + slice_name +
                         "/" + binary_name + ".framework/")
            # BaseBuild keeps Headers shallow on every platform; only the macOS
            # binary/resources are moved under Versions/A and resolved by the
            # existing bounded framework-layout validator below.
            header_path = framework + "Headers/" + SUBTITLE_HEADERS[library]
            zip_row, archive_name = crypto_zip_row(catalog, target, header_path)
            header_name = SUBTITLE_INSTALLED[library][0]
            recorded = records[(platform, arch, library)]["installedIdentity"][header_name]
            need(recorded["bytes"] == zip_row["bytes"] and
                 recorded["sha256"] == zip_row["sha256"] and
                 installed[header_name] ==
                 crypto_zip_file(catalog, target, archive_name, SUBTITLE_MAX_BODY),
                 "Subtitle installed header/raw ZIP correspondence")
            crypto_framework_layout(target, binary_name, slice_name, catalog)
    need(budget[0] <= SUBTITLE_MAX_INLINE_RAW,
         "Subtitle aggregate raw-body cap")
    canonical = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    need(body == canonical and len(canonical) <= SUBTITLE_MAX_RECEIPT,
         "Subtitle canonical final BOM bytes")
    return value


def apple(archive, native, captured, recipe, archive_sources,
          subtitle_archive_sources):
    gate = read(archive, "apple-artifact-gate.txt", MIB).decode("utf-8")
    need("TLS VERIFY PASSED: 5 Apple slices and 9 architectures use SecureTransport" in gate.splitlines() and
         "MPVKit-DVFEL ARTIFACT VERIFY PASSED: FFmpeg 9.0.2 (libavcodec/libavformat 63.1.102), dovi_split, and Apple TLS gates" in gate.splitlines() and
         "PASS dependency-profile " + NATIVE_UPGRADES_PROFILE in gate.splitlines() and
         "PASS openssl-profile " + OPENSSL_BOM_PROFILE in gate.splitlines() and
         "PASS apple-openssl-receipt" in gate.splitlines() and
         "PASS apple-shaderc-receipt" in gate.splitlines() and
         "PASS " + CRYPTO_SOURCE_PROFILE + " verify" in gate.splitlines() and
         "PASS crypto-chain archive/public-symbol correspondence; native SMB/runtime gates OPEN" in gate.splitlines() and
         "PASS " + SUBTITLE_SOURCE_PROFILE + " verify" in gate.splitlines() and
         "PASS subtitle-profile " + SUBTITLE_SOURCE_PROFILE in gate.splitlines() and
         "PASS apple-subtitle-receipt" in gate.splitlines() and
         "PASS apple-sent-abi" in gate.splitlines() and "PASS apple-reference-abi" in gate.splitlines() and
         "FAILED" not in gate, "Apple original, sent-request and finite-reader artifact gates")
    need("apple-shaderc-original-all.zip" in archive.namelist() and
         "apple-shaderc-original-xcframework.zip" in archive.namelist(),
         "Missing Shaderc original output")
    need("apple-shaderc-inputs.json" in archive.namelist(), "Missing Shaderc receipt output")
    need("apple-openssl-builds.json" in archive.namelist(), "Missing OpenSSL BOM output")
    need(all(name in archive.namelist() for name in
             {pin["output"] for pin in CRYPTO_SOURCE_OUTPUTS} |
             {CRYPTO_CAPTURE_TAR_OUTPUT, CRYPTO_CAPTURE_RECEIPT_OUTPUT, CRYPTO_CHAIN_OUTPUT}),
         "Missing crypto source/artifact receipt output")
    need(all(name in archive.namelist() for name in
             {pin["output"] for pin in SUBTITLE_SOURCE_OUTPUTS} |
             {SUBTITLE_CAPTURE_TAR_OUTPUT, SUBTITLE_CAPTURE_RECEIPT_OUTPUT,
              SUBTITLE_BUILD_OUTPUT}),
         "Missing subtitle source/build receipt output")
    shaderc_sources = []
    retained_all_body = None
    for output_name, (source_name, expected_bytes, expected_sha256) in zip(
            ("apple-shaderc-original-all.zip", "apple-shaderc-original-xcframework.zip"),
            SHADERC_SOURCE_SPECS, strict=True):
        data = read(archive, output_name, expected_bytes + 1)
        need(len(data) == expected_bytes and sha(data) == expected_sha256,
             "Exact published Shaderc body: " + output_name)
        shaderc_sources.append(dict(name=source_name, bytes=len(data), sha256=sha(data)))
        if source_name == SHADERC_SOURCE_SPECS[0][0]:
            retained_all_body = data
    need(retained_all_body is not None, "Missing retained Shaderc all-ZIP body")
    with io.BytesIO(retained_all_body) as retained_stream, zip_open(retained_stream) as retained_archive:
        retained_all_rows = zip_index(retained_archive)
    shaderc_receipt(load(read(archive, "apple-shaderc-inputs.json")), shaderc_sources,
                     recipe["scripts/native-media-hosted/evidence.py"], retained_all_rows)
    openssl_bom = load(read(archive, "apple-openssl-builds.json"))
    openssl_receipt(openssl_bom, recipe["scripts/native-media-hosted/evidence.py"])
    subtitle_bom_body = read(archive, SUBTITLE_BUILD_OUTPUT, SUBTITLE_MAX_RECEIPT)
    candidate, actual = tar_index(archive, "apple-candidate.tar.gz", file_limit=512 * MIB, total=4 * 1024**3, capture=("MPVKit-DVFEL/Package.swift",), allow_dirs=True)
    zips, _ = tar_index(archive, "apple-original-library-zips.tar", compressed=False, file_limit=512 * MIB, total=4 * 1024**3, allow_dirs=True)
    need(set(zips) == {name + ".xcframework.zip" for name in LIBRARIES} and all(row["kind"] == "file" for row in zips.values()), "Apple original ZIP set")
    expected = {}
    openssl_zip_bodies = {}
    crypto_zip_bodies = {}
    subtitle_zip_bodies = {}
    # Read each original ZIP through its authenticated tar member, without filesystem extraction.
    with archive.open("apple-original-library-zips.tar") as stream, tarfile.open(fileobj=stream, mode="r|") as libraries:
        for member in libraries:
            if not member.isfile():
                continue
            base = member.name.removesuffix(".xcframework.zip")
            with libraries.extractfile(member) as incoming, io.BytesIO(incoming.read(512 * MIB + 1)) as saved, zip_open(saved) as library:
                body = saved.getvalue()
                rows = zip_index(library)
            if base in ("Libssl", "Libcrypto"):
                openssl_zip_bodies[base] = body
            if base in ("gmp", "nettle", "hogweed", "gnutls", "Libsmbclient"):
                crypto_zip_bodies[base] = body
            if base in ("Libunibreak", "Libfribidi", "Libharfbuzz", "Libbluray"):
                subtitle_zip_bodies[base] = body
            target = base if base in UNSUFFIXED_LIBRARIES else base + "-GPL"
            for name, row in rows.items():
                need(name.startswith(base + ".xcframework/"), "Apple library root")
                expected["MPVKit-DVFEL/artifacts/" + target + ".xcframework/" + name.split("/", 1)[1]] = row
    openssl_archive_binding(openssl_bom, openssl_zip_bodies)
    crypto_chain_receipt(
        read(archive, CRYPTO_CHAIN_OUTPUT, CRYPTO_MAX_RECEIPT),
        crypto_zip_bodies, archive_sources,
        candidate["MPVKit-DVFEL/Package.swift"],
        actual["MPVKit-DVFEL/Package.swift"])
    subtitle_builds_receipt(
        subtitle_bom_body, subtitle_zip_bodies, subtitle_archive_sources)
    for name, row in native.items():
        if any(name.startswith("source-0/Sources/" + value + "/") for value in ("_MPVKit", "_MPVKit-GPL", "_FFmpeg", "_FFmpeg-GPL")):
            expected["MPVKit-DVFEL/" + name.removeprefix("source-0/")] = row
    package = captured["source-0/Package.swift"].decode("utf-8")
    shaderc_remote_target(package)
    for base in LIBRARIES:
        target = base if base in UNSUFFIXED_LIBRARIES else base + "-GPL"
        pattern = r'\.binaryTarget\(\s*name:\s*"%s",\s*url:\s*"[^"]*",\s*checksum:\s*"[^"]*"\s*\)' % re.escape(target)
        replacement = '.binaryTarget(\n            name: "%s",\n            path: "artifacts/%s.xcframework"\n        )' % (target, target)
        package, count = re.subn(pattern, lambda _: replacement, package)
        need(count == 1, "Apple package source rewrite")
    need(actual.get("MPVKit-DVFEL/Package.swift") == package.encode(), "Apple package/source correspondence")
    ignore = b"# Build OUTPUT (hundreds of MB), reproducible from the pinned refs via\n# scripts/build-mpvkit-dvfel.sh. Must never enter git history.\nartifacts/\n"
    for name, data in (("Package.swift", package.encode()), (".gitignore", ignore)):
        expected["MPVKit-DVFEL/" + name] = {"bytes": len(data), "sha256": sha(data), "kind": "file", "mode": 420}
    need(candidate == expected, "Apple assembled/original ZIP/native source byte correspondence")


def package(args):
    need(re.fullmatch(r"[0-9a-f]{40}", args.source_commit) and re.fullmatch(r"[1-9][0-9]*", args.run_id) and
         re.fullmatch(r"[1-9][0-9]*", args.attempt), "Expected producer identity")
    digest(args.artifact_sha256)
    integer(args.artifact_bytes, 8 * 1024**3)
    need(args.repo.is_dir() and args.repo.resolve() == args.repo.absolute(), "Real local immutable Git repository")
    need(args.artifact_zip.is_file() and not args.artifact_zip.is_symlink(), "Original ZIP input")
    with args.artifact_zip.open("rb") as original:
        need(measure(original, 8 * 1024**3) == {"bytes": args.artifact_bytes, "sha256": args.artifact_sha256}, "Independently pinned original ZIP")
        original.seek(0)
        with zip_open(original, 256) as archive:
            inputs = zip_index(archive, count=256, total=8 * 1024**3, flat=True)
            receipt = load(read(archive, "result.json"))
            keys(receipt, "schema lane buildExit appSourceCommit workflowRef runId attempt sources runtimeSecurityTested promotionEligible")
            need(type(receipt["schema"]) is int and receipt["schema"] == 1 and type(receipt["buildExit"]) is int and receipt["buildExit"] == 0 and
                 receipt["lane"] == args.lane and receipt["appSourceCommit"] == args.source_commit and receipt["runId"] == args.run_id and receipt["attempt"] == args.attempt and
                 receipt["promotionEligible"] is False and receipt["runtimeSecurityTested"] is False and isinstance(receipt["workflowRef"], str) and
                 any(receipt["workflowRef"].startswith("VortXTV/VortX/.github/workflows/" + name + "@refs/")
                     for name in ("native-media-candidates.yml", "source-contracts.yml")), "Successful unpromoted original producer identity")
            sources = receipt["sources"]
            need(type(sources) is list and 4 <= len(sources) <= 32, "Original source group bound")
            common = {"artifact-sha256.json", "build.log", "result.json", "reviewed-inputs.sha256", "original-upstream-source.tar.gz", "toolchain.txt", "source-format-v2.json", "producer-inputs-v2.tar.gz", "native-sources-v2.tar.gz"}
            expected = common | OUTPUTS[args.lane] | {f"source-{n}.patch" for n in range(len(sources))}
            if args.lane == "android":
                expected |= {"python-toolchain.json", "ffmpeg-applied.patch", "tls_mbedtls_vortx_distrust.h"}
            manifest = load(read(archive, "artifact-sha256.json"))
            need(set(inputs) == expected and type(manifest) is dict and set(manifest) == expected - {"artifact-sha256.json", "build.log"}, "Exact original artifact membership/coverage")
            need(all(digest(value) == inputs[name]["sha256"] for name, value in manifest.items()), "Original manifest content")
            meta = load(read(archive, "source-format-v2.json"))
            keys(meta, "schema sourceProfile lane appSourceCommit runId attempt workflowRef recipeManifestSHA256 legacyReceiptSHA256 producerArchive nativeArchive producerFiles sources archiveSources subtitleArchiveSources outputs")
            need(type(meta["schema"]) is int and meta["schema"] == 2 and meta["sourceProfile"] == PROFILE and
                 all(meta[key] == receipt[key] for key in ("lane", "appSourceCommit", "runId", "attempt", "workflowRef")) and
                 meta["legacyReceiptSHA256"] == manifest["result.json"], "Exact v2 metadata identity")
            reviewed = read(archive, "reviewed-inputs.sha256", 4 * MIB)
            need(sha(reviewed) == meta["recipeManifestSHA256"] == PROFILE_MANIFEST_SHA256, "Reviewed exact profile manifest")
            recipe = {}
            for line in reviewed.decode("ascii").splitlines():
                need(re.fullmatch(r"[0-9a-f]{64}  [^ ]+", line), "Reviewed manifest row")
                checksum, name = line.split("  ")
                need(name not in recipe, "Duplicate reviewed input")
                recipe[path(name)] = checksum
            need(set(recipe) == RECIPE_PATHS and len(recipe) == 36, "Exact 36 reviewed recipe identities")
            producer = declared(meta["producerFiles"], "producer")
            need(set(producer) == {"producer/" + name for name in RECIPE_PATHS | {INPUT_MANIFEST, WORKFLOW}}, "Exact 38 producer files")
            for name, row in producer.items():
                need(row == git_blob(args.repo, args.source_commit, name.removeprefix("producer/")) and row["kind"] == "file", "Immutable producer Git blob/mode")
                relative = name.removeprefix("producer/")
                need(relative not in recipe or row["sha256"] == recipe[relative], "Reviewed producer digest")
            need(producer["producer/" + INPUT_MANIFEST]["sha256"] == sha(reviewed), "Producer input manifest bytes")
            native, groups = {}, {}
            need(type(meta["sources"]) is list and len(meta["sources"]) == len(sources), "Native group count")
            for index, (row, legacy) in enumerate(zip(meta["sources"], sources)):
                keys(legacy, "directory commit trackedDiffSHA256")
                keys(row, "receiptIndex directory commit root files gitlinks")
                need(type(row["receiptIndex"]) is int and row["receiptIndex"] == index and row["root"] == f"source-{index}" and
                     row["directory"] == legacy["directory"] and row["commit"] == legacy["commit"] and re.fullmatch(r"[0-9a-f]{40}", row["commit"]) and
                     legacy["trackedDiffSHA256"] == manifest[f"source-{index}.patch"] and row["directory"] not in groups, "Native/legacy source identity")
                groups[row["directory"]] = row
                files = declared(row["files"], row["root"], native=True)
                created = {item["path"]: item["sha256"] for item in row["files"] if item["tracked"] is False}
                need(created == untracked(args.lane, row, recipe) and all(item["kind"] == "file" for item in row["files"] if item["tracked"] is False), "Exact recipe-created untracked file set/content")
                need(files and not set(native) & set(files), "Missing/overlapping native source group")
                native.update(files)
                need(len(row["gitlinks"]) <= 1000, "Gitlink count")
                links = validate_native_gitlinks(row, files)
                if row["commit"] == PINS["libplacebo"]:
                    need(all(links.get(name) == commit for name, commit in GITLINKS.items()), "Pinned libplacebo Gitlinks")
            known = {"libmpv-master-8c67647b": PINS["mpv"], "FFmpeg-9.0.2-946fcce0": PINS["ffmpeg"],
                     "libplacebo-7.387.0-275f7dea": PINS["libplacebo"],
                     "lcms2-2.20rc1-13154341": "1315434113566bcf3eb46c2485fcc1c0a56ab806",
                     "libdav1d-1.5.4-54706fc6": "54706fc6bc0cdecab7e9593974a4039cc038fca7",
                     OPENSSL_ROOT: OPENSSL_SOURCE_SHA} if args.lane == "apple" else dict(ANDROID_PINS)
            known["_native-mpvkit" if args.lane == "apple" else "_native-publisher"] = WRAPPER[args.lane]
            need(set(groups) == set(known) and sources[0]["commit"] == WRAPPER[args.lane] and
                 all(groups[name]["commit"] == commit for name, commit in known.items()), "Exact admitted public native roots/revisions")
            need(len(native) <= 100000 and sum(row["bytes"] for row in native.values() if row["kind"] == "file") <= 2 * 1024**3, "Combined native bounds")
            mpv = next(row for row in meta["sources"] if row["commit"] == PINS["mpv"])
            for name, checksum in (CANCEL | READER_MPV | {"meson.build": READER_MPV_MESON[args.lane]}).items():
                row = native.get(mpv["root"] + "/" + name)
                need(row and row["kind"] == "file" and row["sha256"] == checksum, "Cancellation/finite-reader afterimage: " + name)
            ffmpeg = next(row for row in meta["sources"] if row["commit"] == PINS["ffmpeg"])
            require_response_source({row["path"]: row for row in ffmpeg["files"]}, args.lane)
            expected_archive_sources = crypto_source_outputs(archive) if args.lane == "apple" else None
            need(exact_equal(meta["archiveSources"], expected_archive_sources),
                 "Archive-backed source metadata/raw-body/capture correspondence")
            expected_subtitle_archive_sources = (subtitle_source_outputs(archive)
                                                 if args.lane == "apple" else None)
            need(exact_equal(meta["subtitleArchiveSources"],
                             expected_subtitle_archive_sources),
                 "Subtitle archive-backed source metadata/raw-body/capture correspondence")
            for key, name, expected_rows in (("producerArchive", "producer-inputs-v2.tar.gz", producer), ("nativeArchive", "native-sources-v2.tar.gz", native)):
                keys(meta[key], "name sha256 bytes")
                need(meta[key] == dict(name=name, sha256=inputs[name]["sha256"], bytes=inputs[name]["bytes"]), "V2 archive identity")
                actual, captured = tar_index(archive, name, capture=("source-0/Package.swift",))
                need(actual == expected_rows, "Complete tar/inventory byte/mode correspondence")
            need(type(meta["outputs"]) is list and len(meta["outputs"]) == len(OUTPUTS[args.lane]), "Output count")
            bound = {}
            for row in meta["outputs"]:
                keys(row, "name sha256 bytes")
                need(row["name"] in OUTPUTS[args.lane] and row["name"] not in bound and row == dict(name=row["name"], sha256=inputs[row["name"]]["sha256"], bytes=inputs[row["name"]]["bytes"]), "Same-byte lane output binding")
                bound[row["name"]] = row
            android(archive, inputs, recipe) if args.lane == "android" else apple(
                archive, native, captured, recipe, expected_archive_sources,
                expected_subtitle_archive_sources)
            out = args.out.absolute()
            need(out.parent.is_dir() and out.parent.resolve() == out.parent and not out.exists() and not out.is_symlink(), "Fresh real output parent")
            out.mkdir(mode=0o700, exist_ok=False)
            binary = "apple-candidate.tar.gz" if args.lane == "apple" else "libmpv-release-candidate.aar"
            outputs, content = {}, {}
            original.seek(0)
            with (out / "original-ci.zip").open("xb") as sink:
                outputs["original-ci.zip"] = measure(original, 8 * 1024**3, sink)
            need(outputs["original-ci.zip"] == {"bytes": args.artifact_bytes, "sha256": args.artifact_sha256}, "Original ZIP copy changed")
            with archive.open(binary) as stream, (out / binary).open("xb") as sink:
                outputs[binary] = measure(stream, 4 * 1024**3, sink)
            need(outputs[binary] == {key: inputs[binary][key] for key in ("bytes", "sha256")}, "Binary copy changed")
            tool = Path(__file__).read_bytes()
            with GIT.open("rb") as stream:
                git_tool = measure(stream, 64 * MIB)
            excluded = {"apple-original-library-zips.tar"} if args.lane == "apple" else set()
            with (out / "candidate-source-kit.tar.gz").open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=1) as compressed, tarfile.open(fileobj=compressed, mode="w|") as target:
                for name in sorted(set(inputs) - {binary} - excluded):
                    member = tarfile.TarInfo("original-evidence/" + name)
                    member.size, member.mode, member.mtime = inputs[name]["bytes"], 0o644, 0
                    with archive.open(name) as stream:
                        target.addfile(member, stream)
                    content[member.name] = {key: inputs[name][key] for key in ("bytes", "sha256")}
                member = tarfile.TarInfo("packaging/package-native-current-component.py")
                member.size, member.mode, member.mtime = len(tool), 0o644, 0
                target.addfile(member, io.BytesIO(tool))
                content[member.name] = {"bytes": len(tool), "sha256": sha(tool)}
            with tarfile.open(out / "candidate-source-kit.tar.gz", "r|gz") as saved:
                seen = set()
                for member in saved:
                    need(member.name in content and member.name not in seen and member.isfile() and
                         member.mode == 0o644 and member.size == content[member.name]["bytes"], "Source-kit readback membership/mode")
                    seen.add(member.name)
                    with saved.extractfile(member) as stream:
                        need(measure(stream, 4 * 1024**3) == content[member.name], "Source-kit readback bytes")
                need(seen == set(content), "Source-kit readback coverage")
            with (out / "candidate-source-kit.tar.gz").open("rb") as stream:
                outputs["candidate-source-kit.tar.gz"] = measure(stream, 8 * 1024**3)
            provenance = {"schema": 1, "sourceProfile": PROFILE, "producer": receipt, "inputFiles": inputs,
                          "originalZIP": outputs["original-ci.zip"], "toolSHA256": sha(tool), "gitEntrypoint": git_tool, "sourceKitContent": content,
                          "sourceKitExcludedBinaryCarriers": {name: {key: inputs[name][key] for key in ("bytes", "sha256")} for name in sorted(excluded)},
                          "outputs": outputs, "nativeRebuilt": False, "independentBinaryInspection": False,
                          "sourceCaptureScope": ("seven strict Git roots plus seven independently reindexed archive-backed crypto and subtitle/bluray release roots"
                                                 if args.lane == "apple" else
                                                 "cached/nonignored native files and recursive Gitlinks; Android original full deps/config archives also retained"),
                          "archiveSourceRootCount": 7 if args.lane == "apple" else 0,
                          "unresolvedExternalSourceObligations": True,
                          "externalCIauthenticationRequired": True,
                          "originalCIReclassified": False,
                          "runtimeSecurityTested": False, "publicationApproved": False, "promotionEligible": False}
            encoded = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode()
            with (out / "packaging-provenance.json").open("xb") as sink:
                sink.write(encoded)
            outputs["packaging-provenance.json"] = {"bytes": len(encoded), "sha256": sha(encoded)}
            with (out / "SHA256SUMS").open("x") as sink:
                sink.write("".join(row["sha256"] + "  " + name + "\n" for name, row in sorted(outputs.items())))
            need({p.name for p in out.iterdir()} == set(outputs) | {"SHA256SUMS"}, "Output membership")
    print(json.dumps({"status": "unpromoted-candidate-packaged", "lane": args.lane, "promotionEligible": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("artifact-zip", "repo", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("artifact-sha256", "run-id", "attempt", "source-commit"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--artifact-bytes", type=int, required=True)
    parser.add_argument("--lane", choices=sorted(OUTPUTS), required=True)
    package(parser.parse_args())
