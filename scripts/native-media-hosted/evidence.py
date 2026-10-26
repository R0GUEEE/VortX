#!/usr/bin/env python3
"""Candidate evidence only. No publication, device execution or runtime-security verdict."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import posixpath
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True, timeout=60)


def save(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')


def relro_16k_safe(row, loads):
    # Android also permits an unaligned RELRO end when it occupies its entire LOAD,
    # with no other LOAD data inside the page-rounded protection range. Do not
    # mistake NDK/LLD's isolated RELRO layout for writable data sharing that page.
    # https://developer.android.com/guide/practices/page-sizes (Check the RELRO security flag)
    offset, start, file_size, memory_size = (int(row[i], 16) for i in (1, 2, 4, 5))
    end = start + memory_size
    if end % 16384 == 0:
        return True
    if memory_size <= 0:
        return False
    owners = [index for index, load in enumerate(loads)
              if tuple(int(load[i], 16) for i in (1, 2, 4, 5)) ==
              (offset, start, file_size, memory_size)]
    if len(owners) != 1:
        return False
    protected_start = start // 16384 * 16384
    protected_end = (end + 16383) // 16384 * 16384
    for index, load in enumerate(loads):
        if index == owners[0]:
            continue
        other_start, other_size = int(load[2], 16), int(load[5], 16)
        if other_size and other_start < protected_end and other_start + other_size > protected_start:
            return False
    return True


def android(out, upstream, sdk, *, readelf=None):
    inspection = None
    if readelf is None:
        # The original hosted producer toolchain remains unchanged.
        readelf = sdk / 'ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf'
    else:
        if not readelf.is_absolute():
            raise ValueError('explicit inspection reader must be an absolute path')
        requested_reader = readelf
        resolved_reader = readelf.resolve(strict=True)
        if not resolved_reader.is_file():
            raise ValueError('explicit inspection reader is not a regular executable file')
        # Keep the llvm-readelf invocation name: LLVM's llvm-readobj symlink target
        # selects a different output style when invoked directly by its real basename.
        version = run(str(readelf), '--version').strip()
        if not version or len(version) > 4096:
            raise ValueError('unexpected inspection reader version output')
        inspection = {'mode': 'explicit-read-only-inspector',
                      'requestedReader': str(requested_reader), 'resolvedReader': str(resolved_reader),
                      'readerSHA256': digest(resolved_reader), 'readerVersion': version,
                      'validatorSHA256': digest(Path(__file__))}
    aar = out / 'libmpv-release-candidate.aar'
    machines = {'arm64-v8a': ('ELF64', 'AArch64'), 'armeabi-v7a': ('ELF32', 'ARM'),
                'x86_64': ('ELF64', 'Advanced Micro Devices X86-64')}
    required = {'libmpv.so', 'libplayer.so', 'libc++_shared.so', 'libavcodec.so', 'libavformat.so',
                'libavutil.so', 'libavfilter.so', 'libavdevice.so', 'libswresample.so', 'libswscale.so'}
    source_header = (out / 'tls_mbedtls_vortx_distrust.h').read_text()
    keys = [bytes(int(x, 16) for x in re.findall(r'0x([0-9a-f]{2})', line))
            for line in source_header.splitlines() if '}, /* EM' in line]
    if len(keys) != 8 or any(len(key) != 32 for key in keys):
        raise ValueError('unexpected reviewed distrust table')
    records = []
    with zipfile.ZipFile(aar) as archive, tempfile.TemporaryDirectory(dir=out) as temporary:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('duplicate AAR entries')
        libraries = [name for name in names if name.startswith('jni/') and name.endswith('.so')]
        if {name.split('/')[1] for name in libraries} != set(machines):
            raise ValueError('AAR must contain exactly three admitted native ABIs')
        for abi, (elf_class, machine) in machines.items():
            present = {name.split('/')[-1] for name in libraries if name.startswith(f'jni/{abi}/')}
            if not required <= present:
                raise ValueError(f'missing packaged media library: {abi}: {sorted(required - present)}')
            for name in sorted(n for n in libraries if n.startswith(f'jni/{abi}/')):
                if len(name.split('/')) != 3 or archive.getinfo(name).file_size > 512 * 1024**2:
                    raise ValueError('unexpected native entry shape/size')
                data = archive.read(name)
                target = Path(temporary) / 'library.so'
                target.write_bytes(data)
                header = run(str(readelf), '-h', str(target))
                if (not re.search(r'Class:\s+' + elf_class + r'\b', header) or not re.search(
                        r'Machine:\s+' + re.escape(machine) + r'\s*$', header, re.M)
                        or not re.search(r'Type:\s+DYN\b', header) or 'little endian' not in header):
                    raise ValueError(f'wrong native ELF identity: {name}')
                segments = run(str(readelf), '-W', '-l', str(target)).splitlines()
                loads = [line.split() for line in segments if line.strip().startswith('LOAD ')]
                relro = [line.split() for line in segments if line.strip().startswith('GNU_RELRO ')]
                if not loads or (elf_class == 'ELF64' and (not relro or any(
                        int(row[-1], 16) < 16384 or (int(row[2], 16) - int(row[1], 16)) % 16384
                        for row in loads) or any(not relro_16k_safe(row, loads) for row in relro))):
                    raise ValueError(f'not 16 KiB compatible: {name}')
                if name.endswith('/libavformat.so'):
                    if any(flag not in data for flag in (b'--enable-mbedtls', b'--enable-gpl', b'--enable-version3')):
                        raise ValueError(f'expected TLS/GPL configuration missing: {name}')
                    if any(key not in data for key in keys):
                        raise ValueError(f'distrust-key constants missing: {name}')
                records.append({'entry': name, 'sha256': hashlib.sha256(data).hexdigest(),
                                'bytes': len(data), 'elfIdentityChecked': True,
                                'loadAlignments': [int(row[-1], 16) for row in loads],
                                'page16KiBChecked': elf_class == 'ELF64'})
    evidence = {
        'aarSHA256': digest(aar), 'libraries': records, 'runtimeSecurityTested': False,
        'appAndSeamBindingChanged': False, 'promotionEligible': False}
    if inspection is not None:
        # This describes only the inspection host/tool, never the original producer's NDK.
        evidence['inspection'] = inspection
    save(out / 'android-artifact-evidence.json', evidence)


# Exact source-evidence profile for FUTURE successful native candidates. This
# carries source bytes, including new/untracked patch outputs, rather than
# substituting tracked diffs for a complete corresponding-source snapshot.
CURRENT_SOURCE_PROFILE = 'original-http-cancel-lifetime-v1'
CURRENT_RECIPE_PATHS = {
    'scripts/assemble-mpvkit-dvfel.sh', 'scripts/build-mpvkit-dvfel.sh',
    'scripts/mpv-coreaudio-hotplug-lifecycle.patch', 'scripts/mpv-http-seek-interrupt.patch',
    'scripts/mpv-moltenvk-resize.patch', 'scripts/mpv-original-http-reference-recipe.patch',
    'scripts/mpv-original-http-cancel-lifetime.patch', 'scripts/mpv-original-local-file-witness.patch',
    'scripts/mpv-owned-subtitle-constructor.patch', 'scripts/mpv-owned-subtitle-publication.patch',
    'scripts/mpv-upstream-audio-layout-bounds.patch', 'scripts/mpv-upstream-edition-title-lifetime.patch',
    'scripts/mpv-upstream-gpu-next-seek-reset.patch', 'scripts/mpvkit-dvfel.patch',
    'scripts/native-media-hosted/android-assembly.patch',
    'scripts/native-media-hosted/android/buildscripts/include/depinfo.sh',
    'scripts/native-media-hosted/android/buildscripts/include/download-deps.sh',
    'scripts/native-media-hosted/android/buildscripts/include/download-lua.sh',
    'scripts/native-media-hosted/android/buildscripts/include/download-libunibreak.sh',
    'scripts/native-media-hosted/android/buildscripts/patch.sh',
    'scripts/native-media-hosted/android/buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch',
    'scripts/native-media-hosted/android/buildscripts/scripts/ffmpeg.sh',
    'scripts/native-media-hosted/build.sh', 'scripts/native-media-hosted/evidence.py',
    'scripts/verify-mpvkit-dvfel-apple-tls.sh', 'scripts/verify-mpvkit-dvfel-artifacts.sh',
}
CURRENT_NATIVE_PINS = {'mpv': '8c67647b50059406c5c0444903597281b81516cf',
                       'ffmpeg': '946fcce07b6dcd0331c8cc609192aeff5e1924f8',
                       'libplacebo': '4c426e466814536def653cb23f1d1c287ea7a7f5'}
CURRENT_CANCEL_FILES = {
    'misc/thread_tools.c': '151102f6fc8584e119317e3526502bbae492353b1c312ae4a8f3aecaee8bdab9',
    'misc/thread_tools.h': '62c7ea467ef911d9665cc4d7ffe28b2b09ea03f152326964056f62dab24acc7f',
    'stream/stream_lavf.c': 'e0c69d92332e29a53d3d4458d4afa582919a9456d0fd63f7703cf342064cd16a',
    'stream/reference_source.h': 'ce22ce201e6a41899c8c05b98fb9d9912f565090c5ffa0209492118a2a1a07f8',
}
CURRENT_SOURCE_ROOTS = {'android': {'_native-publisher': 'fcf6745703dc1265bca88f12fee8fc355ddf251e',
             'dav1d': '54706fc6bc0cdecab7e9593974a4039cc038fca7',
             'ffmpeg': '946fcce07b6dcd0331c8cc609192aeff5e1924f8',
             'fontconfig': '0f236723a3c26bd9745e32ba2b3ee21e5f809295',
             'freetype': '0a0221a1347e2f1e07c395263540026e9a0aa7c7',
             'fribidi': 'b93119f5fdc7ea47672cc304c1455ffa6dfe7536',
             'harfbuzz': 'c7a7457b7385f33178e8cf87615ca077a810bbe7',
             'libass': '4a05d8127f525943ebf45fdc6497c9e665947f0d',
             'libplacebo': '4c426e466814536def653cb23f1d1c287ea7a7f5',
             'libxml2': '96498992efa48d52b0e8b83058bd88dbdaf153c1',
             'mbedtls': '068ff080b369adfac81509f9b57b2afabaf82dc5',
             'mpv': '8c67647b50059406c5c0444903597281b81516cf'},
 'apple': {'FFmpeg-9.0.2-946fcce0': '946fcce07b6dcd0331c8cc609192aeff5e1924f8',
           '_native-mpvkit': '2103893078c5e339073b11737b86f7f22b9c4491',
           'libmpv-master-8c67647b': '8c67647b50059406c5c0444903597281b81516cf',
           'libplacebo-master-4c426e46': '4c426e466814536def653cb23f1d1c287ea7a7f5'}}
CURRENT_UNTRACKED_FILES = {'android': {'_native-publisher': {'buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch': 'b36837784dc5b8e80e98c0f606fa7baf6e05031c91b5ab4e90b608a0a2d19714',
                                   'buildscripts/patches/mpv/0001-http-seek-interrupt.patch': '3687df5112f6cdcc22fed61dec6a5e16dcff89d54a7174edcefefbddd3054c92',
                                   'buildscripts/patches/mpv/0002-upstream-audio-layout-bounds.patch': 'db5d356912fb0f2b3d83378c05d4bc20c0d68a752148484a4545c31dbbe444c6',
                                   'buildscripts/patches/mpv/0003-upstream-gpu-next-seek-reset.patch': '5b41fd8e830caf37a17c23877742b19df74b76dd7d8d649feaed0dca11940f65',
                                   'buildscripts/patches/mpv/0004-original-local-file-witness.patch': 'a91a8ef61c513ad41ef9501d04e03017094aa581ed8aa31d2668129ea500e480',
                                   'buildscripts/patches/mpv/0005-upstream-edition-title-lifetime.patch': 'c2b8e1524a384e9a9de92ffc061991590a7220bd80b3f951e52073af9f39cb7a',
                                   'buildscripts/patches/mpv/0006-owned-subtitle-constructor.patch': '21140def002aa67849aa1e502d38e0da0c560b1dd0e90bcf04181fe57bad750f',
                                   'buildscripts/patches/mpv/0007-owned-subtitle-publication.patch': '54b29e4101e4cf7ef95d450462725beb8ac44c2733f7a444961f66b41df78341',
                                   'buildscripts/patches/mpv/0008-original-http-reference-recipe.patch': '7d4eaa5236fa8b1b7fb2bd1500ff71aac48fa79fca9e790dfab0dee6d3f0023e',
                                   'buildscripts/patches/mpv/0009-original-http-cancel-lifetime.patch': '778b58c05129e397ed2f90408718f6b3a3feca84492485657d89e784bbee5722'},
             'dav1d': {},
             'ffmpeg': {'libavformat/tls_mbedtls_vortx_distrust.h': '376bd507b67b3b912443bda657465204b808518ea353d161e23e47e9b64c0bf4'},
             'fontconfig': {},
             'freetype': {},
             'fribidi': {},
             'harfbuzz': {},
             'libass': {},
             'libplacebo': {},
             'libxml2': {},
             'mbedtls': {},
             'mpv': {'player/owned_subtitle.c': '875d9f0192cd5f45acca2654765bdc667df5d12dd6eabf9aef5caec5cdec821d',
                     'player/owned_subtitle.h': '2e9e64d92938e41c14c3397c9fbe7d064c2fe8aa5d2dcd05db4b9a74f4bad22e',
                     'stream/reference_source.h': 'ce22ce201e6a41899c8c05b98fb9d9912f565090c5ffa0209492118a2a1a07f8',
                     'sub/owned_text_slot.h': 'b5f99151efb15ba937f630d1c2589c1e6634a4bebb744a161283cb70b3e250b6'}},
 'apple': {'FFmpeg-9.0.2-946fcce0': {},
           '_native-mpvkit': {'Sources/BuildScripts/patch/libmpv/0004-moltenvk-context-check-events-resize.patch': 'c88bd8e3db721953559138dbf3cc4814cff89e71527cf667ed7b6e8c1b3e1e7d',
                              'Sources/BuildScripts/patch/libmpv/0005-coreaudio-hotplug-lifecycle.patch': '01079c34bc8dba62af7e6f5a724063d288a96f2c4b5d2fb75b1993f6085a5ec5',
                              'Sources/BuildScripts/patch/libmpv/0006-http-seek-interrupt.patch': '3687df5112f6cdcc22fed61dec6a5e16dcff89d54a7174edcefefbddd3054c92',
                              'Sources/BuildScripts/patch/libmpv/0007-upstream-audio-layout-bounds.patch': 'db5d356912fb0f2b3d83378c05d4bc20c0d68a752148484a4545c31dbbe444c6',
                              'Sources/BuildScripts/patch/libmpv/0008-upstream-gpu-next-seek-reset.patch': '5b41fd8e830caf37a17c23877742b19df74b76dd7d8d649feaed0dca11940f65',
                              'Sources/BuildScripts/patch/libmpv/0009-original-local-file-witness.patch': 'a91a8ef61c513ad41ef9501d04e03017094aa581ed8aa31d2668129ea500e480',
                              'Sources/BuildScripts/patch/libmpv/0010-upstream-edition-title-lifetime.patch': 'c2b8e1524a384e9a9de92ffc061991590a7220bd80b3f951e52073af9f39cb7a',
                              'Sources/BuildScripts/patch/libmpv/0011-owned-subtitle-constructor.patch': '21140def002aa67849aa1e502d38e0da0c560b1dd0e90bcf04181fe57bad750f',
                              'Sources/BuildScripts/patch/libmpv/0012-owned-subtitle-publication.patch': '54b29e4101e4cf7ef95d450462725beb8ac44c2733f7a444961f66b41df78341',
                              'Sources/BuildScripts/patch/libmpv/0013-original-http-reference-recipe.patch': '7d4eaa5236fa8b1b7fb2bd1500ff71aac48fa79fca9e790dfab0dee6d3f0023e',
                              'Sources/BuildScripts/patch/libmpv/0014-original-http-cancel-lifetime.patch': '778b58c05129e397ed2f90408718f6b3a3feca84492485657d89e784bbee5722'},
           'libmpv-master-8c67647b': {'player/owned_subtitle.c': '875d9f0192cd5f45acca2654765bdc667df5d12dd6eabf9aef5caec5cdec821d',
                                      'player/owned_subtitle.h': '2e9e64d92938e41c14c3397c9fbe7d064c2fe8aa5d2dcd05db4b9a74f4bad22e',
                                      'stream/reference_source.h': 'ce22ce201e6a41899c8c05b98fb9d9912f565090c5ffa0209492118a2a1a07f8',
                                      'sub/owned_text_slot.h': 'b5f99151efb15ba937f630d1c2589c1e6634a4bebb744a161283cb70b3e250b6',
                                      'video/out/vulkan/context_moltenvk.m': '00d2842ddb0dca5070feaf890c0190a0fe62beef9b29ec36e94e329d29a3da5e'},
           'libplacebo-master-4c426e46': {}}}

SOURCE_MAX_FILE = 64 * 1024**2
SOURCE_MAX_TOTAL = 2 * 1024**3
SOURCE_MAX_ENTRIES = 100000


def source_name(name):
    if (not isinstance(name, str) or not name or len(name.encode('utf-8')) > 4096
            or name.startswith('/') or '\\' in name or ':' in name
            or not name.isprintable()
            or any(p in ('', '.', '..') or p.casefold() == '.git' for p in name.split('/'))):
        raise ValueError('unsafe source evidence path')
    return name


def source_git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_NOSYSTEM='1', GIT_NO_REPLACE_OBJECTS='1', GIT_NO_LAZY_FETCH='1', LC_ALL='C')
    return subprocess.check_output(['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                                    '-C', str(root), *args], env=env, timeout=60)


def source_paths(root, allowed_untracked):
    """Only pinned public Git roots and exact recipe-created untracked paths."""
    paths, tracked, gitlinks, seen, identities = [], set(), [], set(), []
    def visit(tree, prefix=''):
        if tree.is_symlink() or tree.resolve() != tree.absolute():
            raise ValueError('linked source checkout')
        if source_git(tree, 'diff', '--cached', '--name-only').strip():
            raise ValueError('staged native changes are not an admitted source snapshot')
        identities.append((prefix, source_git(tree, 'rev-parse', 'HEAD').decode().strip(),
                           hashlib.sha256(source_git(tree, 'ls-files', '--stage', '-z')).hexdigest(),
                           hashlib.sha256(source_git(tree, 'diff', '--no-ext-diff', '--no-textconv', '--binary')).hexdigest()))
        for entry in filter(None, source_git(tree, 'ls-files', '--stage', '-z').decode().split('\0')):
            identity, name = entry.split('\t', 1)
            mode, oid, stage = identity.split()
            relative = source_name(prefix + source_name(name))
            if stage != '0' or mode not in ('100644', '100755', '120000', '160000'):
                raise ValueError('unsupported source Git index entry')
            if mode == '160000':
                child = tree / name
                if source_git(child, 'rev-parse', 'HEAD').decode().strip() != oid:
                    raise ValueError('native gitlink checkout differs from its pin')
                gitlinks.append({'path': relative, 'commit': oid})
                visit(child, relative + '/')
            else:
                paths.append(relative)
                tracked.add(relative)
        for name in filter(None, source_git(tree, 'ls-files', '--others', '--exclude-standard', '-z').decode().split('\0')):
            relative = source_name(prefix + source_name(name))
            if relative not in allowed_untracked:
                raise ValueError('unapproved untracked native source path')
            paths.append(relative)
    visit(root)
    if set(paths) - tracked != set(allowed_untracked):
        raise ValueError('recipe-created native source set is incomplete')
    for name in paths:
        if name.casefold() in seen:
            raise ValueError('duplicate/case-colliding source path')
        seen.add(name.casefold())
    return sorted(paths), sorted(gitlinks, key=lambda row: row['path']), sorted(tracked), sorted(identities)


def source_root_fd(root):
    # Every ancestor is traversed without following links, including the root.
    if not root.is_absolute():
        raise ValueError('source root must be absolute')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in root.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd
    except BaseException:
        os.close(fd)
        raise


def source_parent_fd(rootfd, name):
    parts = source_name(name).split('/')
    fd = os.dup(rootfd)
    try:
        for part in parts[:-1]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
            os.close(fd)
            fd = following
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


class SourceHashReader:
    def __init__(self, stream):
        self.stream, self.hash, self.bytes = stream, hashlib.sha256(), 0

    def read(self, size=-1):
        block = self.stream.read(size)
        self.hash.update(block)
        self.bytes += len(block)
        return block


def source_tar_row(archive, rootfd, prefix, name, limits):
    parent, leaf = source_parent_fd(rootfd, name)
    try:
        before = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
        row = {'path': name}
        entry = tarfile.TarInfo(prefix + '/' + name)
        entry.uid = entry.gid = entry.mtime = 0
        entry.uname = entry.gname = ''
        if stat.S_ISLNK(before.st_mode):
            target = os.readlink(leaf, dir_fd=parent)
            # Validate complete virtual link closure before publishing the staged archive.
            if (not target or len(target.encode('utf-8')) > 4096 or target.startswith('/')
                    or '\\' in target or ':' in target or not target.isprintable()
                    or any(p.casefold() == '.git' for p in target.split('/'))):
                raise ValueError('unsafe native source symlink target')
            after = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if (after.st_dev, after.st_ino, after.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_mtime_ns):
                raise ValueError('source symlink changed during capture')
            data = target.encode()
            entry.type, entry.linkname, entry.mode = tarfile.SYMTYPE, target, 0o777
            row.update(kind='symlink', mode=0o777, bytes=len(data),
                       sha256=hashlib.sha256(data).hexdigest(), target=target)
            archive.addfile(entry)
        elif stat.S_ISREG(before.st_mode):
            fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or (opened.st_dev, opened.st_ino, opened.st_size) !=
                           (before.st_dev, before.st_ino, before.st_size)
                        or opened.st_size < 0 or opened.st_size > SOURCE_MAX_FILE):
                    raise ValueError('unsafe/replaced opened native source file')
                entry.size, entry.mode = opened.st_size, 0o755 if opened.st_mode & 0o111 else 0o644
                reader = SourceHashReader(stream)
                archive.addfile(entry, reader)
                after = os.fstat(stream.fileno())
            if (reader.bytes != opened.st_size or after.st_size != opened.st_size
                    or after.st_mtime_ns != opened.st_mtime_ns):
                raise ValueError('source changed during capture')
            row.update(kind='file', mode=entry.mode, bytes=reader.bytes, sha256=reader.hash.hexdigest())
            limits[1] += reader.bytes
        else:
            raise ValueError('unsupported source object')
        limits[0] += 1
        if limits[0] > SOURCE_MAX_ENTRIES or limits[1] > SOURCE_MAX_TOTAL:
            raise ValueError('source profile limit exceeded')
        return row
    finally:
        os.close(parent)


def source_archive(path, rows):
    limits, result = [0, 0], []
    with path.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
            for root, prefix, names in rows:
                fd = source_root_fd(root)
                try:
                    result.append([source_tar_row(archive, fd, prefix, name, limits) for name in names])
                finally:
                    os.close(fd)
    return result


def producer_archive(path, entries):
    rows = []
    with path.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
            for name, data, mode in entries:
                entry = tarfile.TarInfo('producer/' + name)
                entry.uid = entry.gid = entry.mtime = 0
                entry.uname = entry.gname = ''
                entry.size, entry.mode = len(data), mode
                archive.addfile(entry, io.BytesIO(data))
                rows.append({'path': name, 'kind': 'file', 'mode': mode,
                             'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
    return rows


def source_link_closure(files):
    links = {row['path'].casefold(): row['target'] for row in files if row['kind'] == 'symlink'}
    for name, target in links.items():
        done = name.split('/')[:-1]
        pending, expansions = target.split('/'), 0
        while pending:
            part = pending.pop(0)
            if part in ('', '.'):
                continue
            if part == '..':
                if not done:
                    raise ValueError('source symlink graph escapes component')
                done.pop()
                continue
            candidate = '/'.join(done + [part]).casefold()
            if candidate in links:
                expansions += 1
                if expansions > 40:
                    raise ValueError('source symlink graph cycle/depth exceeds profile')
                pending = links[candidate].split('/') + pending
            else:
                done.append(part)


def current_source_evidence(lane, out, upstream, roots, legacy):
    # Invalid/interrupted snapshots stay in an owned temporary directory and are
    # removed before the workflow's always-upload step can retain partial data.
    names = ('producer-inputs-v2.tar.gz', 'native-sources-v2.tar.gz', 'source-format-v2.json')
    if any((out / name).exists() or (out / name).is_symlink() for name in names):
        raise ValueError('refusing an existing v2 source receipt')
    with tempfile.TemporaryDirectory(prefix='.source-evidence-v2-', dir=out) as temporary:
        staged = Path(temporary)
        capture_current_source_evidence(lane, out, upstream, roots, legacy, staged)
        for name in names:
            os.replace(staged / name, out / name)


def capture_current_source_evidence(lane, out, upstream, roots, legacy, staged):
    app = Path(os.environ['GITHUB_WORKSPACE'])
    commit = legacy['appSourceCommit']
    if (not re.fullmatch(r'[0-9a-f]{40}', commit) or app.is_symlink()
            or app.resolve() != app.absolute()
            or source_git(app, 'rev-parse', 'HEAD').decode().strip() != commit):
        raise ValueError('source evidence app identity differs')
    if not isinstance(legacy['workflowRef'], str) or not re.fullmatch(
            r'VortXTV/VortX/\.github/workflows/[A-Za-z0-9_.-]+\.yml@refs/(?:heads|tags)/[A-Za-z0-9_./-]+',
            legacy['workflowRef']):
        raise ValueError('unexpected public producer workflow ref')
    expected_wrapper = {'apple': '2103893078c5e339073b11737b86f7f22b9c4491',
                        'android': 'fcf6745703dc1265bca88f12fee8fc355ddf251e'}[lane]
    if (upstream.resolve() != upstream.absolute() or not upstream.is_relative_to(app)
            or legacy['sources'][0]['commit'] != expected_wrapper):
        raise ValueError('source evidence wrapper identity differs')
    manifest = out / 'reviewed-inputs.sha256'
    inputs = {}
    for line in manifest.read_text().splitlines():
        value, name = line.split('  ', 1)
        if name in inputs or not re.fullmatch(r'[0-9a-f]{64}', value):
            raise ValueError('duplicate or malformed recipe hash')
        inputs[source_name(name)] = value
    if set(inputs) != CURRENT_RECIPE_PATHS:
        raise ValueError('not the exact 26-input cancellation source family')
    names = sorted(inputs | {'scripts/native-media-hosted/inputs.sha256': digest(manifest),
                             '.github/workflows/native-media-candidates.yml': None})
    entries = []
    appfd = source_root_fd(app)
    try:
        for name in names:
            tree = source_git(app, 'ls-tree', '-z', commit, '--', name).decode().rstrip('\0')
            identity, found = tree.split('\t', 1)
            mode, kind, oid = identity.split()
            if found != name or kind != 'blob' or mode not in ('100644', '100755'):
                raise ValueError('unexpected immutable producer blob identity')
            size = int(source_git(app, 'cat-file', '-s', oid))
            if size > SOURCE_MAX_FILE:
                raise ValueError('producer blob exceeds profile')
            data = source_git(app, 'cat-file', 'blob', oid)
            parent, leaf = source_parent_fd(appfd, name)
            try:
                fd = os.open(leaf, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(fd, 'rb') as stream:
                    live = os.fstat(stream.fileno())
                    if (not stat.S_ISREG(live.st_mode) or live.st_nlink != 1
                            or live.st_size != len(data)
                            or (0o755 if live.st_mode & 0o111 else 0o644) != int(mode[-3:], 8)
                            or stream.read(SOURCE_MAX_FILE + 1) != data):
                        raise ValueError('producer differs from immutable blob')
            finally:
                os.close(parent)
            if name in inputs and hashlib.sha256(data).hexdigest() != inputs[name]:
                raise ValueError('producer differs from reviewed input')
            if name == 'scripts/native-media-hosted/inputs.sha256' and data != manifest.read_bytes():
                raise ValueError('retained input manifest differs from producer')
            entries.append((name, data, int(mode[-3:], 8)))
    finally:
        os.close(appfd)
    producer_rows = producer_archive(staged / 'producer-inputs-v2.tar.gz', entries)
    expected = CURRENT_SOURCE_ROOTS[lane]
    if (len(legacy['sources']) != len(expected)
            or {row['directory']: row['commit'] for row in legacy['sources']} != expected):
        raise ValueError('unexpected native source root or revision')
    native_rows, groups, boundaries = [], [], []
    for index, (root, old) in enumerate(zip(roots, legacy['sources'], strict=True)):
        expected_path = upstream if index == 0 else upstream / ('dist' if lane == 'apple' else 'buildscripts/deps') / old['directory']
        if root != expected_path or root.resolve() != root.absolute():
            raise ValueError('native source root escapes its exact profile path')
        if (source_git(root, 'rev-parse', 'HEAD').decode().strip() != old['commit']
                or hashlib.sha256(source_git(root, 'diff', '--no-ext-diff', '--no-textconv', '--binary')).hexdigest() != old['trackedDiffSHA256']):
            raise ValueError('native source changed after legacy receipt')
        allowed = CURRENT_UNTRACKED_FILES[lane][old['directory']]
        names, gitlinks, tracked, nested_identities = source_paths(root, allowed)
        boundaries.append((root, old, allowed, (names, gitlinks, tracked, nested_identities)))
        groups.append({'receiptIndex': index, 'directory': old['directory'],
                       'commit': old['commit'], 'root': 'source-' + str(index), 'gitlinks': gitlinks})
        native_rows.append((root, groups[-1]['root'], names))
    inventories = source_archive(staged / 'native-sources-v2.tar.gz', native_rows)
    for (root, old, allowed, boundary), files in zip(boundaries, inventories, strict=True):
        if (source_git(root, 'rev-parse', 'HEAD').decode().strip() != old['commit']
                or hashlib.sha256(source_git(root, 'diff', '--no-ext-diff', '--no-textconv', '--binary')).hexdigest() != old['trackedDiffSHA256']
                or source_paths(root, allowed) != boundary):
            raise ValueError('native source identity/membership changed during capture')
        actual = {row['path']: row for row in files}
        for name, value in allowed.items():
            if actual.get(name, {}).get('kind') != 'file' or actual[name]['sha256'] != value:
                raise ValueError('recipe-created source bytes differ from exact profile')
        source_link_closure(files)
        for row in files:
            row['tracked'] = row['path'] in boundary[2]
    actual_roots = [upstream]
    for directory in (upstream / 'dist', upstream / 'buildscripts/deps'):
        if directory.is_dir():
            actual_roots.extend(path for path in directory.iterdir() if (path / '.git').exists())
    if set(actual_roots) != set(roots) or len(actual_roots) != len(roots):
        raise ValueError('native source root set changed during capture')
    for group, files in zip(groups, inventories, strict=True):
        group['files'] = files
    for name, pin in CURRENT_NATIVE_PINS.items():
        if len([row for row in groups if row['commit'] == pin]) != 1:
            raise ValueError('missing/ambiguous required native source pin: ' + name)
    mpv = next(row for row in groups if row['commit'] == CURRENT_NATIVE_PINS['mpv'])
    actual = {row['path']: row for row in mpv['files']}
    if any(actual.get(name, {}).get('kind') != 'file'
           or actual[name]['sha256'] != value for name, value in CURRENT_CANCEL_FILES.items()):
        raise ValueError('native source snapshot lacks exact cancellation afterimages')
    outputs = {'apple': ['apple-candidate.tar.gz', 'apple-original-library-zips.tar',
                         'apple-artifact-gate.txt'],
               'android': ['libmpv-release-candidate.aar', 'android-artifact-evidence.json',
                           'android-corresponding-sources.tar.gz', 'android-build-configs.tar.gz']}[lane]
    def record(name):
        path = (staged if name in ('producer-inputs-v2.tar.gz', 'native-sources-v2.tar.gz') else out) / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('required current producer output is missing/linked')
        return {'name': name, 'sha256': digest(path), 'bytes': path.stat().st_size}
    save(staged / 'source-format-v2.json', {
        'schema': 2, 'sourceProfile': CURRENT_SOURCE_PROFILE, 'lane': lane,
        **{k: legacy[k] for k in ('appSourceCommit', 'runId', 'attempt', 'workflowRef')},
        'recipeManifestSHA256': digest(manifest), 'legacyReceiptSHA256': digest(out / 'result.json'),
        'producerArchive': record('producer-inputs-v2.tar.gz'),
        'nativeArchive': record('native-sources-v2.tar.gz'), 'producerFiles': producer_rows,
        'sources': groups, 'outputs': [record(name) for name in outputs]})


def receipt(lane, out, upstream, exit_code):
    roots = [upstream]
    for directory in (upstream / 'dist', upstream / 'buildscripts/deps'):
        if directory.is_dir():
            roots.extend(path for path in directory.iterdir() if (path / '.git').exists())
    sources = []
    for index, path in enumerate(roots):
        patch = out / f'source-{index}.patch'
        patch.write_text(run('git', '-C', str(path), 'diff', '--binary'))
        sources.append({'directory': path.name, 'commit': run('git', '-C', str(path), 'rev-parse', 'HEAD').strip(),
                        'trackedDiffSHA256': digest(patch)})
    legacy = {'schema': 1, 'lane': lane, 'buildExit': exit_code,
         'appSourceCommit': os.environ['GITHUB_SHA'], 'workflowRef': os.environ.get('GITHUB_WORKFLOW_REF'),
         'runId': os.environ['GITHUB_RUN_ID'], 'attempt': os.environ['GITHUB_RUN_ATTEMPT'],
         'sources': sources, 'runtimeSecurityTested': False, 'promotionEligible': False}
    save(out / 'result.json', legacy)
    if exit_code == 0:
        current_source_evidence(lane, out, upstream, roots, legacy)
    # build.log is still owned by the workflow's tee process until this shell exits.
    files = {str(path.relative_to(out)): digest(path) for path in sorted(out.iterdir())
             if path.is_file() and path.suffix != '.log'}
    save(out / 'artifact-sha256.json', files)


if __name__ == '__main__':
    if sys.argv[1] == 'android':
        if len(sys.argv) not in (5, 7) or (len(sys.argv) == 7 and sys.argv[5] != '--readelf'):
            raise SystemExit('android OUT UPSTREAM SDK [--readelf /absolute/path/to/llvm-readelf]')
        android(*(Path(value) for value in sys.argv[2:5]),
                readelf=Path(sys.argv[6]) if len(sys.argv) == 7 else None)
    elif sys.argv[1] == 'receipt':
        receipt(sys.argv[2], Path(sys.argv[3]), Path(sys.argv[4]), int(sys.argv[5]))
    else:
        raise SystemExit('unknown evidence mode')
