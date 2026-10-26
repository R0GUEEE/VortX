#!/usr/bin/env python3
"""Offline exact-profile candidate packaging; no build, extraction, inspection or promotion."""
import argparse
from collections import deque
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile
import unicodedata
import zipfile

CHUNK, MIB = 1024 * 1024, 1024 * 1024
GIT = Path("/usr/bin/git")  # Do not execute a producer-controlled/PATH-selected command.
PROFILE = "original-http-cancel-lifetime-v1"
PROFILE_MANIFEST_SHA256 = "af353a37bb7aeb1a54c5569cd6f87dba818d3bc11ff6e1faf4fdb7e140e4cf15"  # Filled only with the reviewed producer profile.
RECIPE_PATHS = frozenset("""
scripts/assemble-mpvkit-dvfel.sh scripts/build-mpvkit-dvfel.sh
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
OUTPUTS = {"apple": {"apple-candidate.tar.gz", "apple-original-library-zips.tar", "apple-artifact-gate.txt"},
           "android": {"libmpv-release-candidate.aar", "android-artifact-evidence.json",
                       "android-corresponding-sources.tar.gz", "android-build-configs.tar.gz"}}
PINS = {"mpv": "8c67647b50059406c5c0444903597281b81516cf",
        "ffmpeg": "946fcce07b6dcd0331c8cc609192aeff5e1924f8",
        "libplacebo": "4c426e466814536def653cb23f1d1c287ea7a7f5"}
ANDROID_PINS = dict(PINS, mbedtls="068ff080b369adfac81509f9b57b2afabaf82dc5",
                    dav1d="54706fc6bc0cdecab7e9593974a4039cc038fca7",
                    freetype="0a0221a1347e2f1e07c395263540026e9a0aa7c7",
                    fribidi="b93119f5fdc7ea47672cc304c1455ffa6dfe7536",
                    harfbuzz="c7a7457b7385f33178e8cf87615ca077a810bbe7",
                    libxml2="96498992efa48d52b0e8b83058bd88dbdaf153c1",
                    fontconfig="0f236723a3c26bd9745e32ba2b3ee21e5f809295",
                    libass="4a05d8127f525943ebf45fdc6497c9e665947f0d")
WRAPPER = {"apple": "2103893078c5e339073b11737b86f7f22b9c4491",
           "android": "fcf6745703dc1265bca88f12fee8fc355ddf251e"}
CANCEL = {"misc/thread_tools.c": "151102f6fc8584e119317e3526502bbae492353b1c312ae4a8f3aecaee8bdab9",
          "misc/thread_tools.h": "62c7ea467ef911d9665cc4d7ffe28b2b09ea03f152326964056f62dab24acc7f",
          "stream/stream_lavf.c": "e0c69d92332e29a53d3d4458d4afa582919a9456d0fd63f7703cf342064cd16a",
          "stream/reference_source.h": "ce22ce201e6a41899c8c05b98fb9d9912f565090c5ffa0209492118a2a1a07f8"}
GITLINKS = {"3rdparty/Vulkan-Headers": "74d8a6cb930c68ef617b202c3ff3c59d919e086b",
            "3rdparty/fast_float": "97b54ca9e75f5303507699d27c6b4f4efe4641a1",
            "3rdparty/glad": "73db193f853e2ee079bf3ca8a64aa2eaf6459043",
            "3rdparty/jinja": "15206881c006c79667fe5154fe80c01c65410679",
            "3rdparty/markupsafe": "297fc8e356e6836a62087949245d09a28e9f1b13",
            "demos/3rdparty/nuklear": "242f35efa067a46c595645eeda7b1771ea1f83b1"}
LIBRARIES = "Libmpv Libavcodec Libavdevice Libavfilter Libavformat Libavutil Libswresample Libswscale Libplacebo".split()
CREATED_MPV = {"player/owned_subtitle.c": "875d9f0192cd5f45acca2654765bdc667df5d12dd6eabf9aef5caec5cdec821d",
               "player/owned_subtitle.h": "2e9e64d92938e41c14c3397c9fbe7d064c2fe8aa5d2dcd05db4b9a74f4bad22e",
               "sub/owned_text_slot.h": "b5f99151efb15ba937f630d1c2589c1e6634a4bebb744a161283cb70b3e250b6",
               "stream/reference_source.h": CANCEL["stream/reference_source.h"]}
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
    ("0014-original-http-cancel-lifetime.patch", "0009-original-http-cancel-lifetime.patch", "mpv-original-http-cancel-lifetime.patch")]


def untracked(lane, row, recipe):
    if row["commit"] == WRAPPER[lane]:
        prefix = "Sources/BuildScripts/patch/libmpv/" if lane == "apple" else "buildscripts/patches/mpv/"
        result = {prefix + (apple if lane == "apple" else android): recipe["scripts/" + source]
                  for apple, android, source in PATCH_COPIES if lane == "apple" or android is not None}
        if lane == "android":
            result["buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch"] = recipe["scripts/native-media-hosted/android/buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch"]
        return result
    if row["commit"] == PINS["mpv"]:
        result = dict(CREATED_MPV)
        if lane == "apple":
            result["video/out/vulkan/context_moltenvk.m"] = "00d2842ddb0dca5070feaf890c0190a0fe62beef9b29ec36e94e329d29a3da5e"
        return result
    return {"libavformat/tls_mbedtls_vortx_distrust.h": "376bd507b67b3b912443bda657465204b808518ea353d161e23e47e9b64c0bf4"} if lane == "android" and row["commit"] == PINS["ffmpeg"] else {}


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
    need(archive.getinfo(name).file_size <= limit, "Member read bound: " + name)
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


def android(archive, inputs):
    evidence = load(read(archive, "android-artifact-evidence.json"))
    keys(evidence, "aarSHA256 libraries runtimeSecurityTested appAndSeamBindingChanged promotionEligible")
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


def apple(archive, native, captured):
    gate = read(archive, "apple-artifact-gate.txt", MIB).decode("utf-8")
    need("TLS VERIFY PASSED: 5 Apple slices and 9 architectures use SecureTransport" in gate.splitlines() and
         "MPVKit-DVFEL ARTIFACT VERIFY PASSED: FFmpeg 9.0.2 (libavcodec/libavformat 63.1.102), dovi_split, and Apple TLS gates" in gate.splitlines() and "FAILED" not in gate, "Apple original artifact gate")
    candidate, actual = tar_index(archive, "apple-candidate.tar.gz", file_limit=512 * MIB, total=4 * 1024**3, capture=("MPVKit-DVFEL/Package.swift",), allow_dirs=True)
    zips, _ = tar_index(archive, "apple-original-library-zips.tar", compressed=False, file_limit=512 * MIB, total=4 * 1024**3, allow_dirs=True)
    need(set(zips) == {name + ".xcframework.zip" for name in LIBRARIES} and all(row["kind"] == "file" for row in zips.values()), "Apple original ZIP set")
    expected = {}
    # Read each original ZIP through its authenticated tar member, without filesystem extraction.
    with archive.open("apple-original-library-zips.tar") as stream, tarfile.open(fileobj=stream, mode="r|") as libraries:
        for member in libraries:
            if not member.isfile():
                continue
            base = member.name.removesuffix(".xcframework.zip")
            with libraries.extractfile(member) as incoming, io.BytesIO(incoming.read(512 * MIB + 1)) as saved, zip_open(saved) as library:
                rows = zip_index(library)
            target = base if base == "Libplacebo" else base + "-GPL"
            for name, row in rows.items():
                need(name.startswith(base + ".xcframework/"), "Apple library root")
                expected["MPVKit-DVFEL/artifacts/" + target + ".xcframework/" + name.split("/", 1)[1]] = row
    for name, row in native.items():
        if any(name.startswith("source-0/Sources/" + value + "/") for value in ("_MPVKit", "_MPVKit-GPL", "_FFmpeg", "_FFmpeg-GPL")):
            expected["MPVKit-DVFEL/" + name.removeprefix("source-0/")] = row
    package = captured["source-0/Package.swift"].decode("utf-8")
    for base in LIBRARIES:
        target = base if base == "Libplacebo" else base + "-GPL"
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
            keys(meta, "schema sourceProfile lane appSourceCommit runId attempt workflowRef recipeManifestSHA256 legacyReceiptSHA256 producerArchive nativeArchive producerFiles sources outputs")
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
            need(set(recipe) == RECIPE_PATHS and len(recipe) == 26, "Exact 26 reviewed recipe identities")
            producer = declared(meta["producerFiles"], "producer")
            need(set(producer) == {"producer/" + name for name in RECIPE_PATHS | {INPUT_MANIFEST, WORKFLOW}}, "Exact 28 producer files")
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
                links = {}
                need(type(row["gitlinks"]) is list and len(row["gitlinks"]) <= 1000, "Gitlink count")
                for item in row["gitlinks"]:
                    keys(item, "path commit")
                    name = path(item["path"])
                    need(name not in links and re.fullmatch(r"[0-9a-f]{40}", item["commit"]) and any(name0.startswith(row["root"] + "/" + name + "/") for name0 in files), "Gitlink source preservation")
                    links[name] = item["commit"]
                if row["commit"] == PINS["libplacebo"]:
                    need(all(links.get(name) == commit for name, commit in GITLINKS.items()), "Pinned libplacebo Gitlinks")
            known = {"libmpv-master-8c67647b": PINS["mpv"], "FFmpeg-9.0.2-946fcce0": PINS["ffmpeg"],
                     "libplacebo-master-4c426e46": PINS["libplacebo"]} if args.lane == "apple" else dict(ANDROID_PINS)
            known["_native-mpvkit" if args.lane == "apple" else "_native-publisher"] = WRAPPER[args.lane]
            need(set(groups) == set(known) and sources[0]["commit"] == WRAPPER[args.lane] and
                 all(groups[name]["commit"] == commit for name, commit in known.items()), "Exact admitted public native roots/revisions")
            need(len(native) <= 100000 and sum(row["bytes"] for row in native.values() if row["kind"] == "file") <= 2 * 1024**3, "Combined native bounds")
            mpv = next(row for row in meta["sources"] if row["commit"] == PINS["mpv"])
            for name, checksum in CANCEL.items():
                row = native.get(mpv["root"] + "/" + name)
                need(row and row["kind"] == "file" and row["sha256"] == checksum, "Cancellation afterimage: " + name)
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
            android(archive, inputs) if args.lane == "android" else apple(archive, native, captured)
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
                          "sourceCaptureScope": "cached/nonignored native files and recursive Gitlinks; Android original full deps/config archives also retained",
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
