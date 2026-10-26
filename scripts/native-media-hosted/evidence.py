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
import shutil
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


SENT_FFMPEG_FUNCTIONS = (
    'avpriv_vortx_http_copy_sent_request_v2', 'avpriv_vortx_http_sent_request_clear_v2')
REFERENCE_FFMPEG_FUNCTIONS = (
    'avpriv_vortx_reference_capture_v1', 'avpriv_vortx_reference_revalidate_v1',
    'avpriv_vortx_reference_recipe_current', 'avpriv_vortx_reference_open_prefix_v1',
    'avpriv_vortx_reference_read_v1', 'avpriv_vortx_reference_stats_v1',
    'avpriv_vortx_reference_reader_close_v1', 'avpriv_vortx_reference_recipe_release_v1')
RETIRED_SENT_FFMPEG_FUNCTIONS = (
    'avpriv_vortx_http_copy_sent_request', 'avpriv_vortx_http_sent_request_clear')
PRIVATE_MPV_FUNCTIONS = ('stream_reference_sent_request_copy_transport',)
SENT_MPV_FUNCTIONS = (
    'stream_reference_source_observe_sent_request',
    'stream_reference_source_revalidate_sent_request',
    'stream_reference_sent_request_cancelled',
    'stream_reference_sent_request_copy_values',
    'stream_reference_sent_request_values_clear', 'stream_reference_sent_request_release',
    'stream_reference_source_prepare_prefix_request',
    'stream_reference_sent_request_copy_transport')


def required_elf_functions(text, required, role, forbidden=()):
    """Require one real .dynsym GLOBAL DEFAULT FUNC per exact normalized name."""
    if role not in ('defined-provider', 'undefined-import') or not required or len(set(required)) != len(required):
        raise ValueError('invalid required ELF function contract')
    if not isinstance(text, str) or '\0' in text or len(text) > 16 * 1024**2:
        raise ValueError('invalid or oversized dynamic symbol text')
    lines = text.splitlines()
    if len(lines) > 200000 or any(len(line) > 16384 for line in lines):
        raise ValueError('oversized dynamic symbol table')
    header = [re.fullmatch(r"Symbol table '\.dynsym' contains ([0-9]+) entries:", line.strip())
              for line in lines if line.strip().startswith('Symbol table ')]
    if len(header) != 1 or header[0] is None:
        raise ValueError('expected exactly one complete .dynsym table')
    count = int(header[0].group(1))
    if count < 1 or count > 200000:
        raise ValueError('invalid dynamic symbol count')
    rows, found = set(), {}
    pattern = re.compile(r'\s*([0-9]+):\s+([0-9a-fA-F]+)\s+([0-9]+)\s+'
                         r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+)(?:\s+\([0-9]+\))?)?\s*')
    for line in lines:
        if not re.match(r'\s*[0-9]+:', line):
            continue
        row = pattern.fullmatch(line)
        if not row or len(row.group(2)) > 16:
            raise ValueError('malformed dynamic symbol row')
        index, value, size, kind, binding, visibility, section, raw = row.groups()
        index = int(index)
        if index >= count or index in rows:
            raise ValueError('duplicate or out-of-range dynamic symbol index')
        rows.add(index)
        if raw and raw.split('@', 1)[0] in forbidden:
            raise ValueError('retired ELF bridge symbol: ' + raw)
        if not raw or raw.split('@', 1)[0] not in required:
            continue
        name = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]*)(?:(@@?)([A-Za-z_][A-Za-z0-9_.-]{0,127}))?', raw)
        if not name or len(raw) > 384:
            raise ValueError('malformed required versioned ELF name')
        base, version_kind, version = name.groups()
        if base in found or kind != 'FUNC' or binding != 'GLOBAL' or visibility != 'DEFAULT':
            raise ValueError('duplicate, weak, non-function or hidden required ELF symbol: ' + base)
        defined = re.fullmatch(r'[0-9]+', section) is not None and int(section) > 0
        if (role == 'defined-provider' and not defined) or (role == 'undefined-import' and section != 'UND'):
            raise ValueError('wrong required ELF function role: ' + base)
        found[base] = {'name': base, 'version': version, 'defaultVersion': version_kind == '@@',
                       'type': kind, 'binding': binding, 'visibility': visibility, 'section': section}
    if rows != set(range(count)) or set(found) != set(required):
        raise ValueError('incomplete dynamic table or missing required ELF functions')
    return [found[name] for name in sorted(required)]


def required_private_elf_functions(text, required):
    """Require actual nonexported FUNC code in one complete .symtab.

    This is a private retention check, never a request to export a native helper.
    Candidate recipe must retain .symtab in packaged libmpv.so explicitly.
    """
    if not required or len(set(required)) != len(required):
        raise ValueError('invalid private ELF function contract')
    if not isinstance(text, str) or '\0' in text or len(text) > 32 * 1024**2:
        raise ValueError('invalid or oversized full symbol text')
    lines = text.splitlines()
    if len(lines) > 400000 or any(len(line) > 16384 for line in lines):
        raise ValueError('oversized full symbol table')
    headers = [(index, re.fullmatch(r"Symbol table '(\.dynsym|\.symtab)' contains ([0-9]+) entries:",
                                   line.strip()))
               for index, line in enumerate(lines) if line.strip().startswith('Symbol table ')]
    if (any(row is None for _, row in headers) or
            len({row.group(1) for _, row in headers}) != len(headers)):
        raise ValueError('malformed or duplicate full symbol-table header')
    starts = [index for index, row in headers if row.group(1) == '.symtab']
    if len(starts) != 1:
        raise ValueError('expected exactly one complete private .symtab table')
    start = starts[0]
    count = int(re.fullmatch(r"Symbol table '\.symtab' contains ([0-9]+) entries:",
                            lines[start].strip()).group(1))
    if not 1 <= count <= 400000:
        raise ValueError('invalid private symbol count')
    end = next((index for index in range(start + 1, len(lines))
                if lines[index].strip().startswith('Symbol table ')), len(lines))
    rows, found = set(), {}
    pattern = re.compile(r'\s*([0-9]+):\s+([0-9a-fA-F]+)\s+([0-9]+)\s+'
                         r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)(?:\s+(\S+))?\s*')
    for line in lines[start + 1:end]:
        if not re.match(r'\s*[0-9]+:', line):
            continue
        row = pattern.fullmatch(line)
        if not row or len(row.group(2)) > 16:
            raise ValueError('malformed private symbol row')
        index, value, size, kind, binding, visibility, section, raw = row.groups()
        index = int(index)
        if index >= count or index in rows:
            raise ValueError('duplicate or out-of-range private symbol index')
        rows.add(index)
        if raw not in required:
            continue
        if (raw in found or kind != 'FUNC' or int(size) <= 0 or
                not re.fullmatch(r'[0-9]+', section) or int(section) <= 0 or
                (binding, visibility) not in {('LOCAL', 'DEFAULT'), ('LOCAL', 'HIDDEN'),
                                               ('GLOBAL', 'HIDDEN')}):
            raise ValueError('private helper is missing unique nonexported code: ' + raw)
        found[raw] = {'name': raw, 'size': int(size), 'type': kind,
                      'binding': binding, 'visibility': visibility, 'section': section}
    if rows != set(range(count)) or set(found) != set(required):
        raise ValueError('incomplete private table or missing private code')
    return [found[name] for name in sorted(required)]


def reader_identity(reader, mode):
    requested = reader.absolute()
    resolved = requested.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError('selected inspection reader is not a regular file')
    # Preserve argv[0]: llvm-readelf can be a symlink to llvm-readobj, whose
    # resolved basename would select another output format if invoked directly.
    version = run(str(requested), '--version').strip()
    if not version or '\0' in version or len(version) > 4096:
        raise ValueError('unexpected inspection reader version output')
    return {'mode': mode, 'requestedReader': str(requested), 'resolvedReader': str(resolved),
            'readerSHA256': digest(resolved), 'readerVersion': version,
            'validatorSHA256': digest(Path(__file__))}


def required_macho_functions(text, required, role, forbidden=()):
    """The caller selects real nm -gU definitions or -gu undefined externals."""
    if role not in ('defined-code', 'undefined-import') or not required or len(set(required)) != len(required):
        raise ValueError('invalid required Mach-O function contract')
    if not isinstance(text, str) or '\0' in text or len(text) > 32 * 1024**2:
        raise ValueError('invalid or oversized nm text')
    found = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.endswith(':'):
            continue
        row = re.fullmatch(r'(?:[0-9a-fA-F]+\s+)?([A-Za-z?])\s+(\S+)', line)
        if not row:
            if any('_' + name in line for name in required):
                raise ValueError('malformed required nm row')
            continue
        kind, raw = row.groups()
        if raw.lstrip('_').split('@', 1)[0] in forbidden:
            raise ValueError('retired Mach-O bridge symbol')
        name = raw[1:] if raw.startswith('_') else raw
        if name not in required:
            if raw.lstrip('_').split('@', 1)[0] in required:
                raise ValueError('wrong required Mach-O symbol spelling')
            continue
        correct_type = kind in ('T', 't') if role == 'defined-code' else kind == 'U'
        if raw != '_' + name or name in found or not correct_type:
            raise ValueError('duplicate or wrong-type required Mach-O function: ' + name)
        found.add(name)
    if found != set(required):
        raise ValueError('missing required Mach-O functions')
    return sorted(found)


def apple_sent_abi(package, *, lipo=None, nm=None):
    slices = {'ios-arm64': {'arm64'}, 'ios-arm64_x86_64-simulator': {'arm64', 'x86_64'},
              'tvos-arm64_arm64e': {'arm64', 'arm64e'},
              'tvos-arm64_x86_64-simulator': {'arm64', 'x86_64'},
              'macos-arm64_x86_64': {'arm64', 'x86_64'}}
    if not package.is_dir():
        raise ValueError('expected a staged Apple package directory')
    package = package.resolve(strict=True)
    for tool in (lipo, nm):
        if tool is not None and not tool.is_absolute():
            raise ValueError('explicit Apple inspection tools must be absolute paths')
    lipo_command = (str(lipo),) if lipo is not None else ('xcrun', 'lipo')
    nm_command = (str(nm),) if nm is not None else ('xcrun', 'nm')
    with tempfile.TemporaryDirectory(prefix='.sent-request-abi-', dir=package.parent) as temporary:
        for library, functions in (('Libavformat', SENT_FFMPEG_FUNCTIONS + REFERENCE_FFMPEG_FUNCTIONS),
                                   ('Libmpv', SENT_MPV_FUNCTIONS)):
            xc = package / 'artifacts' / (library + '-GPL.xcframework')
            if not xc.is_dir():
                raise ValueError('missing sent-request XCFramework')
            if {p.name for p in xc.iterdir() if p.is_dir()} != set(slices):
                raise ValueError('wrong exact sent-request Apple slice set')
            for slice_name, expected in slices.items():
                framework = xc / slice_name / (library + '.framework')
                binary = framework / library
                if not framework.is_dir() or not binary.is_file():
                    raise ValueError('missing staged Apple archive')
                archs = run(*lipo_command, '-archs', str(binary)).split()
                if len(archs) != len(expected) or set(archs) != expected:
                    raise ValueError('wrong exact staged Apple architecture set')
                for arch in sorted(expected):
                    thin = Path(temporary) / (library + '-' + slice_name + '-' + arch + '.a')
                    if len(expected) == 1:
                        shutil.copyfile(binary, thin)
                    else:
                        run(*lipo_command, str(binary), '-thin', arch, '-output', str(thin))
                    required_macho_functions(run(*nm_command, '-gU', str(thin)), functions, 'defined-code',
                                             RETIRED_SENT_FFMPEG_FUNCTIONS)
                    if library == 'Libmpv':
                        required_macho_functions(run(*nm_command, '-gu', str(thin)),
                                                SENT_FFMPEG_FUNCTIONS + REFERENCE_FFMPEG_FUNCTIONS, 'undefined-import',
                                                RETIRED_SENT_FFMPEG_FUNCTIONS)
    print('PASS apple-sent-abi')
    print('PASS apple-reference-abi')


def android(out, upstream, sdk, *, readelf=None):
    explicit_reader = readelf is not None
    if explicit_reader and not readelf.is_absolute():
        raise ValueError('explicit inspection reader must be an absolute path')
    if readelf is None:
        readelf = sdk / 'ndk/30.0.16248370/toolchains/llvm/prebuilt/linux-x86_64/bin/llvm-readelf'
    readelf = readelf.absolute()
    reader = reader_identity(readelf, 'explicit-read-only-inspector' if explicit_reader else 'hosted-ndk-reader')
    inspection = reader if explicit_reader else None
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
    records, sent_records, private_records, reference_records = [], [], [], []
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
                library = name.split('/')[-1]
                if library in ('libavformat.so', 'libmpv.so'):
                    role = 'defined-provider' if library == 'libavformat.so' else 'undefined-import'
                    dynamic = run(str(readelf), '--dyn-syms', '--wide', str(target))
                    symbols = required_elf_functions(dynamic, SENT_FFMPEG_FUNCTIONS, role,
                                                     RETIRED_SENT_FFMPEG_FUNCTIONS +
                                                     (PRIVATE_MPV_FUNCTIONS if library == 'libmpv.so' else ()))
                    sent_records.append({'abi': abi, 'library': library, 'role': role,
                                         'binarySHA256': hashlib.sha256(data).hexdigest(),
                                         'dynsymTextSHA256': hashlib.sha256(dynamic.encode('utf-8')).hexdigest(),
                                         'symbols': symbols})
                    reference = required_elf_functions(dynamic, REFERENCE_FFMPEG_FUNCTIONS, role)
                    reference_records.append({'abi': abi, 'library': library, 'role': role,
                                              'binarySHA256': hashlib.sha256(data).hexdigest(),
                                              'dynsymTextSHA256': hashlib.sha256(dynamic.encode('utf-8')).hexdigest(),
                                              'symbols': reference})
                    if library == 'libmpv.so':
                        full = run(str(readelf), '--symbols', '--wide', str(target))
                        private = required_private_elf_functions(full, PRIVATE_MPV_FUNCTIONS)
                        private_records.append({'abi': abi, 'library': library,
                                                'binarySHA256': hashlib.sha256(data).hexdigest(),
                                                'symtabTextSHA256': hashlib.sha256(full.encode('utf-8')).hexdigest(),
                                                'symbols': private})
                records.append({'entry': name, 'sha256': hashlib.sha256(data).hexdigest(),
                                'bytes': len(data), 'elfIdentityChecked': True,
                                'loadAlignments': [int(row[-1], 16) for row in loads],
                                'page16KiBChecked': elf_class == 'ELF64'})
    if (str(readelf.resolve(strict=True)) != reader['resolvedReader'] or
            digest(Path(reader['resolvedReader'])) != reader['readerSHA256']):
        raise ValueError('selected inspection reader changed during ABI inspection')
    evidence = {
        'aarSHA256': digest(aar), 'libraries': records, 'runtimeSecurityTested': False,
        'appAndSeamBindingChanged': False, 'promotionEligible': False,
        'sentRequestABI': {'schema': 2, 'reader': reader, 'libraries': sent_records,
                           'retiredV1Absent': True},
        'referenceReaderABI': {'schema': 1, 'reader': reader, 'libraries': reference_records},
        'privateOwnerABI': {'schema': 1, 'reader': reader, 'libraries': private_records}}
    if inspection is not None:
        # This describes only the inspection host/tool, never the original producer's NDK.
        evidence['inspection'] = inspection
    save(out / 'android-artifact-evidence.json', evidence)


# Exact source-evidence profile for FUTURE successful native candidates. This
# carries source bytes, including new/untracked patch outputs, rather than
# substituting tracked diffs for a complete corresponding-source snapshot.
CURRENT_SOURCE_PROFILE = 'original-http-finite-reference-reader-v1'
CURRENT_RECIPE_PATHS = {
    'scripts/assemble-mpvkit-dvfel.sh', 'scripts/build-mpvkit-dvfel.sh',
    'scripts/ffmpeg-response-witness.patch', 'scripts/ffmpeg-response-generation.patch',
    'scripts/mpv-completed-response-observation.patch',
    'scripts/ffmpeg-sent-request-evidence.patch', 'scripts/mpv-sent-request-evidence.patch',
    'scripts/mpv-bounded-prefix-request.patch',
    'scripts/ffmpeg-original-transport-evidence.patch', 'scripts/mpv-original-transport-evidence.patch',
    'scripts/ffmpeg-finite-reference-reader.patch', 'scripts/mpv-finite-reference-reader.patch',
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
# Full-file postcomposition hashes retain cancellation plus owned observation.
CURRENT_CANCEL_FILES = {
    'misc/thread_tools.c': '151102f6fc8584e119317e3526502bbae492353b1c312ae4a8f3aecaee8bdab9',
    'misc/thread_tools.h': '62c7ea467ef911d9665cc4d7ffe28b2b09ea03f152326964056f62dab24acc7f',
    'stream/stream_lavf.c': 'b8c78d8239af8c968c53ca83f601f91e521090d6d2d7522101fb68b332074940',
    'stream/reference_source.h': 'dacfde2ba2aacfb07bd959f23e7e6e367125dbde47d4a31b36c21c24eba0c38b',
                     'stream/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
}
CURRENT_RESPONSE_FILES = {
    'libavformat/http.c': 'd4ec29f40e9302f6bb1abedcbd0c5adf87c20b11236a8ad8aff42fea46616802',
    'libavformat/url.h': '14e3acc5a6715d48edb3dadb2f5248deaa38c36b30ff763e9be4b89edf2b1a0c',
    'libavformat/avio.c': '16bf38abc7a5b5586268c71723041df2b90b7b99a8fbaeb06c8b20926534fe7a',
    'libavformat/tcp.c': 'cc9c11534ff5017d5931bb06788f95f10e9809953e5ec1cc03114a3a877cdcce',
    'libavformat/tls.c': '0f07be92b62cefb53c6db5552951c658c756fac36ce22b03ccca8a044f1911f2',
    'libavformat/tls.h': '257c76932de48ed84bf849a44ca9ea6fe85adb93a26bb4f7112b19000084ff19',
    'libavformat/tls_securetransport.c': 'b04fb28df85f1ef3883b4927d0bfab4477f400128c122f794ff13b4b9c85bebc',
}
CURRENT_READER_FFMPEG_CREATED = {
    'libavformat/vortx_reference_reader.inc': 'a8c74f929942423550dba65f738fd4d2ad2d63d975665f64e6da1b00f5001645',
    'libavformat/vortx_reference_pin.h': '1000225eb4660e936bdf5984b62c6cbd7ae2c747472e55aabb6dc620d4f386dc',
    'libavformat/vortx_reference_reader_stream.h': 'adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87',
}
CURRENT_READER_MPV_FILES = {
    'demux/demux.c': 'e5f08305137ed3f7b28fa24ee431e19222153a6cfed10f12d109b92da55567a4',
    'demux/demux.h': 'bee52b38477ce7c8efd9a50e778f720ea8c9eec577afab29998682c26ffe75e2',
    'demux/demux_lavf.c': 'd945ae1699d383c3d645d2ab963abb166fd0c19f10a4ab144505e7fbfb0408fb',
    'demux/reference_reader.c': '322fa3a90ad9a461c549b2aaebf50adba07b9862d11b8690355a6d789571c752',
    'demux/reference_reader.h': '4894866fc49f8ab7af9f4d5b3a29bfc7d1ac4153483fd04354db3e89e55cad03',
    'demux/reference_reader_internal.h': '6b21e2b72c87c49d609fdfca0252c32635d50054abe4eaae33340fc5b7da1a9c',
    'stream/stream_lavf.c': CURRENT_CANCEL_FILES['stream/stream_lavf.c'],
    'stream/reference_source.h': CURRENT_CANCEL_FILES['stream/reference_source.h'],
    'stream/vortx_reference_reader_stream.h': 'adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87',
    'player/owned_subtitle.c': '242697e6be2bd2e10272679a3d618f07a07138b7a15b3b94cdbfdf521599bb25',
    'player/owned_subtitle.h': '8d2f0ee5c972d1da69213cd80fbe249bcf3f19d9de8a2ef87e241d2f08f06284',
    'player/command.c': '661388627775cf58dec688388d36aba9e457b0f3e97d349a91033f3f5f1f6e3b',
    'player/reference_owned.inc': '4402bcd094a80a769479320ac852c329acb39781fa8fc478d0ed79af10ae19e2',
    'sub/owned_text_slot.h': '63c498dd4bdc9fa9c6d128f1be8f37d7cdf377c853888342897f6639601662f6',
    'sub/osd.c': '9b57ccd08e5777e2bc644cbf8001df0301552edf5cf24e36185a54f4cd667195',
    'sub/dec_sub.c': '6e2137b90fe7f2915a528bdd83426448afbd099229f65342dbfd85d1ba1d347b',
    'sub/dec_sub.h': 'cb9b5af7c3f87a8dda584617c1f20a90a9331182d54a50c7e5fb8e64e74b384f',
}
# Both maintained source lanes retain SENT/PREFIX's seven Android-conditional
# roots; only the Apple source includes MPVKit's MoltenVK configuration block.
CURRENT_READER_MPV_MESON = {
    'apple': '4c748249732131a2942b66087fa7142a2f9c239274cc272a35ec36221c010205',
    'android': '316e9aca3c4960701eab42e62ca1005379e082e1a21b4788b7581811ca360147',
}
CURRENT_MBEDTLS_FILES = {'apple': '77d72cb88fc662b984f051335b356abc3e76e97ce39302dde6961ecdbba7a332',
                         'android': '7099664b2c15a784db38a0db7956f718862201ef6c02d27779b2fc654b44aeac'}
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
CURRENT_UNTRACKED_FILES = {'android': {'_native-publisher': {'buildscripts/include/download-libunibreak.sh': '34ce1ee52187a287ff7da6e18efd7368e5671ac77cc7da5c5a8027a3c63a0418',
                                   'buildscripts/patches/ffmpeg/0006-finite-reference-reader.patch': '6927705a432e987d2e13e2d5922756cb6c395d5c060268191cf6c1d94063b2df',
                                   'buildscripts/patches/mpv/0014-finite-reference-reader.patch': 'c8752eb80824a5487f5d80addc473da09778d6dd8cb1adac899079aa2bc211f2',
                                   'buildscripts/patches/ffmpeg/0005-original-transport-evidence.patch': '480302686b9fff135cd0665e7af76d635fc21d83f54ee0096578ddd6bd6d83e5',
                                   'buildscripts/patches/mpv/0013-original-transport-evidence.patch': 'dad7f2d20ea14eb4661ef853978be78806e1b01fce67653e3ffec9b57d4a0e82',
                                   'buildscripts/include/download-lua.sh': 'bdfb58aa6fc85ec4dabd095bf11c760482fbbff86b94abbe835044ea49f06f93',
                                   'buildscripts/patches/ffmpeg/0004-sent-request-evidence.patch': '085b66cb5623ed8087481d3217b65da587b2970cd345a45896e7e87823dec9c9',
                                   'buildscripts/patches/mpv/0011-sent-request-evidence.patch': 'e3840084725be1f4948fb90c6fec87c35d55665f8310854713ebff496e1358bf',
                                   'buildscripts/patches/mpv/0012-bounded-prefix-request.patch': 'edcfdc7d9d8da7f8a44b19d326a809de844be968cc8061b80744229da801331e',
                                   'buildscripts/patches/ffmpeg/0003-response-generation.patch': 'ddfb68b5d7ed3779cfa69a75a28e9d4b17bd4f28529cccf26a005e790da03098',
                                   'buildscripts/patches/mpv/0010-completed-response-observation.patch': 'c038924be4c9b0ef187e1e9336d201b866dfa6f13457817c6e8c2d484e882e8e',
                                   'buildscripts/patches/ffmpeg/0002-response-witness.patch': '6a2bf6d0feff31280470ac46e9c56b2e0a4522513959784dcb04aaf196334b9b',
                                   'buildscripts/patches/ffmpeg/0001-emudhra-spki-distrust.patch': 'b36837784dc5b8e80e98c0f606fa7baf6e05031c91b5ab4e90b608a0a2d19714',
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
             'ffmpeg': {**CURRENT_READER_FFMPEG_CREATED,
                        'libavformat/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
                        'libavformat/tls_mbedtls_vortx_distrust.h': '376bd507b67b3b912443bda657465204b808518ea353d161e23e47e9b64c0bf4'},
             'fontconfig': {},
             'freetype': {},
             'fribidi': {},
             'harfbuzz': {},
             'libass': {},
             'libplacebo': {},
             'libxml2': {},
             'mbedtls': {},
             'mpv': {'player/owned_subtitle.c': CURRENT_READER_MPV_FILES['player/owned_subtitle.c'],
                     'player/owned_subtitle.h': CURRENT_READER_MPV_FILES['player/owned_subtitle.h'],
                     'player/reference_owned.inc': CURRENT_READER_MPV_FILES['player/reference_owned.inc'],
                     'demux/reference_reader.c': CURRENT_READER_MPV_FILES['demux/reference_reader.c'],
                     'demux/reference_reader.h': CURRENT_READER_MPV_FILES['demux/reference_reader.h'],
                     'demux/reference_reader_internal.h': CURRENT_READER_MPV_FILES['demux/reference_reader_internal.h'],
                     'stream/vortx_reference_reader_stream.h': CURRENT_READER_MPV_FILES['stream/vortx_reference_reader_stream.h'],
                     'stream/reference_source.h': CURRENT_CANCEL_FILES['stream/reference_source.h'],
                     'stream/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
                     'sub/owned_text_slot.h': CURRENT_READER_MPV_FILES['sub/owned_text_slot.h']}},
 'apple': {'FFmpeg-9.0.2-946fcce0': {**CURRENT_READER_FFMPEG_CREATED,
                                  'libavformat/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9'},
           '_native-mpvkit': {'Sources/BuildScripts/patch/FFmpeg/0003-sent-request-evidence.patch': '085b66cb5623ed8087481d3217b65da587b2970cd345a45896e7e87823dec9c9',
                              'Sources/BuildScripts/patch/FFmpeg/0005-finite-reference-reader.patch': '6927705a432e987d2e13e2d5922756cb6c395d5c060268191cf6c1d94063b2df',
                              'Sources/BuildScripts/patch/libmpv/0019-finite-reference-reader.patch': 'c8752eb80824a5487f5d80addc473da09778d6dd8cb1adac899079aa2bc211f2',
                              'Sources/BuildScripts/patch/FFmpeg/0004-original-transport-evidence.patch': '480302686b9fff135cd0665e7af76d635fc21d83f54ee0096578ddd6bd6d83e5',
                              'Sources/BuildScripts/patch/libmpv/0018-original-transport-evidence.patch': 'dad7f2d20ea14eb4661ef853978be78806e1b01fce67653e3ffec9b57d4a0e82',
                              'Sources/BuildScripts/patch/libmpv/0016-sent-request-evidence.patch': 'e3840084725be1f4948fb90c6fec87c35d55665f8310854713ebff496e1358bf',
                              'Sources/BuildScripts/patch/libmpv/0017-bounded-prefix-request.patch': 'edcfdc7d9d8da7f8a44b19d326a809de844be968cc8061b80744229da801331e',
                              'Sources/BuildScripts/patch/FFmpeg/0002-response-generation.patch': 'ddfb68b5d7ed3779cfa69a75a28e9d4b17bd4f28529cccf26a005e790da03098',
                              'Sources/BuildScripts/patch/libmpv/0015-completed-response-observation.patch': 'c038924be4c9b0ef187e1e9336d201b866dfa6f13457817c6e8c2d484e882e8e',
                              'Sources/BuildScripts/patch/FFmpeg/0001-response-witness.patch': '6a2bf6d0feff31280470ac46e9c56b2e0a4522513959784dcb04aaf196334b9b',
                              'Sources/BuildScripts/patch/libmpv/0004-moltenvk-context-check-events-resize.patch': 'c88bd8e3db721953559138dbf3cc4814cff89e71527cf667ed7b6e8c1b3e1e7d',
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
           'libmpv-master-8c67647b': {'player/owned_subtitle.c': CURRENT_READER_MPV_FILES['player/owned_subtitle.c'],
                                      'player/owned_subtitle.h': CURRENT_READER_MPV_FILES['player/owned_subtitle.h'],
                                      'player/reference_owned.inc': CURRENT_READER_MPV_FILES['player/reference_owned.inc'],
                                      'demux/reference_reader.c': CURRENT_READER_MPV_FILES['demux/reference_reader.c'],
                                      'demux/reference_reader.h': CURRENT_READER_MPV_FILES['demux/reference_reader.h'],
                                      'demux/reference_reader_internal.h': CURRENT_READER_MPV_FILES['demux/reference_reader_internal.h'],
                                      'stream/vortx_reference_reader_stream.h': CURRENT_READER_MPV_FILES['stream/vortx_reference_reader_stream.h'],
                                      'stream/reference_source.h': CURRENT_CANCEL_FILES['stream/reference_source.h'],
                     'stream/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
                                      'sub/owned_text_slot.h': CURRENT_READER_MPV_FILES['sub/owned_text_slot.h'],
                                      'video/out/vulkan/context_moltenvk.m': '00d2842ddb0dca5070feaf890c0190a0fe62beef9b29ec36e94e329d29a3da5e'},
           'libplacebo-master-4c426e46': {}}}

SOURCE_MAX_FILE = 64 * 1024**2
SOURCE_MAX_TOTAL = 2 * 1024**3
SOURCE_MAX_ENTRIES = 100000


def require_current_response_source(files, lane):
    # Source bytes only: no assertion about a live response, TLS, or a timing reader.
    if lane not in CURRENT_MBEDTLS_FILES:
        raise ValueError('unknown transport-evidence source lane')
    expected = dict(CURRENT_RESPONSE_FILES, **{
        'libavformat/tls_mbedtls.c': CURRENT_MBEDTLS_FILES[lane]})
    for name, checksum in expected.items():
        row = files.get(name, {})
        if row.get('kind') != 'file' or row.get('tracked') is not True or row.get('sha256') != checksum:
            raise ValueError('native source snapshot lacks exact response-witness afterimage: ' + name)
    for name, checksum in CURRENT_READER_FFMPEG_CREATED.items():
        row = files.get(name, {})
        if row.get('kind') != 'file' or row.get('tracked') is not False or row.get('sha256') != checksum:
            raise ValueError('native source snapshot lacks exact finite-reader private file: ' + name)


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
        raise ValueError('not the exact 36-input finite-reference-reader source family')
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
           or actual[name]['sha256'] != value
           for name, value in (CURRENT_CANCEL_FILES | CURRENT_READER_MPV_FILES |
                               {'meson.build': CURRENT_READER_MPV_MESON[lane]}).items()):
        raise ValueError('native source snapshot lacks exact cancellation/finite-reader afterimages')
    ffmpeg = next(row for row in groups if row['commit'] == CURRENT_NATIVE_PINS['ffmpeg'])
    require_current_response_source({row['path']: row for row in ffmpeg['files']}, lane)
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
    elif sys.argv[1] == 'apple-sent-abi':
        if len(sys.argv) < 3 or len(sys.argv[3:]) % 2:
            raise SystemExit('apple-sent-abi PACKAGE [--lipo /absolute/lipo] [--nm /absolute/nm]')
        tools = {}
        for flag, value in zip(sys.argv[3::2], sys.argv[4::2], strict=True):
            if flag not in ('--lipo', '--nm') or flag in tools:
                raise SystemExit('invalid or duplicate Apple inspection tool option')
            tools[flag] = Path(value)
        apple_sent_abi(Path(sys.argv[2]), lipo=tools.get('--lipo'), nm=tools.get('--nm'))
    else:
        raise SystemExit('unknown evidence mode')
