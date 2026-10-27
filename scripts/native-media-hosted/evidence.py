#!/usr/bin/env python3
"""Candidate evidence only. No publication, device execution or runtime-security verdict."""
import base64
import binascii
import gzip
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import posixpath
import re
import shlex
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


# The dependency-upgrade receipt is deliberately separate from the existing
# shipping/source profile.  The coordinator seals the profile and manifest
# literals after rebasing this recovery-only delta onto the next carrier.
NATIVE_UPGRADES_PROFILE = 'native-upgrades-openssl410-crypto-subtitles-bluray150-v6'
SHADERC_WRAPPER_VERSION = '2026.1.0'
SHADERC_SOURCE_SPECS = (
    ('libshaderc.zip', 124322785,
     'd213fa58de7886846fb8cb713ea5213b77654a3ad2ba3d89b1f2fe367d945ba5'),
    ('Libshaderc_combined.xcframework.zip', 61246203,
     '77a3d355993cf53843a394bf58262dab99cdd93f9e6fe31d5ca6e55b03866ce1'),
)
SHADERC_MATRIX = (
    ('ios-arm64', 'arm64', 'ios'),
    ('ios-arm64_x86_64-simulator', 'arm64', 'isimulator'),
    ('ios-arm64_x86_64-simulator', 'x86_64', 'isimulator'),
    ('tvos-arm64_arm64e', 'arm64', 'tvos'),
    ('tvos-arm64_arm64e', 'arm64e', 'tvos'),
    ('tvos-arm64_x86_64-simulator', 'arm64', 'tvsimulator'),
    ('tvos-arm64_x86_64-simulator', 'x86_64', 'tvsimulator'),
    ('macos-arm64_x86_64', 'arm64', 'macos'),
    ('macos-arm64_x86_64', 'x86_64', 'macos'),
)
SHADERC_MAX_ENTRIES = 100000
SHADERC_MAX_MEMBER = 512 * 1024**2
SHADERC_MAX_TOTAL = 4 * 1024**3
SHADERC_MAX_LINK_BODY = 4096
SHADERC_MAX_LINK_HOPS = 8
SHADERC_XC_MAC_FRAMEWORK = (
    'Libshaderc_combined.xcframework/macos-arm64_x86_64/'
    'Libshaderc_combined.framework')
SHADERC_XC_LINKS = {
    SHADERC_XC_MAC_FRAMEWORK + '/Resources': 'Versions/Current/Resources',
    SHADERC_XC_MAC_FRAMEWORK + '/Libshaderc_combined':
        'Versions/Current/Libshaderc_combined',
    SHADERC_XC_MAC_FRAMEWORK + '/Versions/Current': 'A',
}

OPENSSL_BOM_PROFILE = 'openssl-4.1.0-source-v1'
OPENSSL_SOURCE = {
    'repository': 'https://github.com/openssl/openssl',
    'tag': 'openssl-4.1.0',
    'commit': '2f4e3a5fec05394068cfa7379890ab552483a8a6',
    'version': '4.1.0',
}
OPENSSL_GITLINKS = {
    'cloudflare-quiche': '7ab6a55cfe471267d61e4d28ba43d41defcd87e0',
    'gost-engine': '74b1f4fddbc2d6de969815b1992ddc1ae7c643fe',
    'krb5': '784c38f50e70a739400cdd3f2620bac2e2788e6c',
    'oqs-provider': 'c5fb439e4ffd947f24c139232fb04fd71fe3c29e',
    'pkcs11-provider': 'eb157e48066fdb2b5735f6f327ed7ae65c51eb6f',
    'pyca-cryptography': 'e83bbe58e373536636baaa7c33a64a6e8f04c953',
    'python-ecdsa': '4096fa01715929e08b97e73f3173aee9d57f2a3f',
    'fuzz/corpora': '7e3fb43a75c55275759e8bdb5c32de4b50978eab',
    'tlsfuzzer': '61f45d9701294fd87ef92d2a7e3dfb076653a562',
    'tlslite-ng': '77ef321dde1a9e6bcf94d73c80f8789a770d8031',
    'wycheproof': '3fa63dd0344abb611f1fb1d77e119938603ea230',
}
OPENSSL_ROOT = 'openssl-4.1.0-2f4e3a5f'
OPENSSL_OUTPUT_ROOT = 'openssl'
OPENSSL_CONFIGURE_FLAGS = ('no-async', 'no-shared', 'no-dso', 'no-tests', 'no-apps', 'no-docs')
OPENSSL_PKG_LIBRARIES = ('libmpv', 'FFmpeg', 'libshaderc', 'vulkan', 'lcms2', 'libdovi',
                         'openssl', 'libunibreak', 'libfreetype', 'libfribidi', 'libharfbuzz',
                         'libass', 'libsmbclient', 'libplacebo', 'libdav1d', 'gmp', 'nettle',
                         'gnutls', 'libuchardet', 'libbluray', 'libluajit', 'libuavs3d')
OPENSSL_COMPLETED_STEPS = ('Configure', 'make build_sw', 'make install_sw')
OPENSSL_ENV_KEYS = ('CC', 'CXX', 'CFLAGS', 'CPPFLAGS', 'CXXFLAGS', 'ASMFLAGS',
                    'LDFLAGS', 'PKG_CONFIG_LIBDIR', 'PKG_CONFIG_PATH', 'PATH',
                    'CURRENT_ARCH', 'LC_CTYPE', 'AR', 'ARFLAGS', 'RANLIB',
                    'MAKEFLAGS', 'CMAKE_BUILD_PARALLEL_LEVEL', 'CARGO_BUILD_JOBS')
OPENSSL_EFFECTIVE_KEYS = ('target', 'CC', 'CFLAGS', 'CPPFLAGS', 'CXX', 'CXXFLAGS',
                           'LDFLAGS', 'AR', 'ARFLAGS', 'RANLIB', 'CROSS_COMPILE',
                           'cflags', 'cppflags', 'lflags', 'lib_cppflags')
OPENSSL_DEFAULT_PATH = '/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
OPENSSL_OUTPUTS = (
    ('libssl.a', 'lib/libssl.a', 512 * 1024**2),
    ('libcrypto.a', 'lib/libcrypto.a', 512 * 1024**2),
    ('opensslv.h', 'include/openssl/opensslv.h', 1024 * 1024),
)
OPENSSL_MATRIX = (
    ('ios', 'arm64', 'vortx-apple-ios-arm64', 'ios-arm64'),
    ('isimulator', 'arm64', 'vortx-apple-isimulator-arm64', 'ios-arm64_x86_64-simulator'),
    ('isimulator', 'x86_64', 'vortx-apple-isimulator-x86_64', 'ios-arm64_x86_64-simulator'),
    ('tvos', 'arm64', 'vortx-apple-tvos-arm64', 'tvos-arm64_arm64e'),
    ('tvos', 'arm64e', 'vortx-apple-tvos-arm64e', 'tvos-arm64_arm64e'),
    ('tvsimulator', 'arm64', 'vortx-apple-tvsimulator-arm64', 'tvos-arm64_x86_64-simulator'),
    ('tvsimulator', 'x86_64', 'vortx-apple-tvsimulator-x86_64', 'tvos-arm64_x86_64-simulator'),
    ('macos', 'arm64', 'vortx-apple-macos-arm64', 'macos-arm64_x86_64'),
    ('macos', 'x86_64', 'vortx-apple-macos-x86_64', 'macos-arm64_x86_64'),
)
OPENSSL_MAX_RECEIPT = 4 * 1024 * 1024
OPENSSL_MAX_TARGET_CONFIG = 1024 * 1024

CRYPTO_SOURCE_PROFILE = 'gmp630-nettle400-gnutls3813-release-sources-v1'
CRYPTO_CHAIN_PROFILE = 'native-upgrades-crypto-chain-v1'
CRYPTO_CHAIN_OUTPUT = 'apple-crypto-chain.json'
CRYPTO_SOURCE_OUTPUTS = (
    {
        'id': 'gmp', 'version': '6.3.0', 'archiveRoot': 'gmp-6.3.0',
        'filename': 'gmp-6.3.0.tar.xz', 'compression': 'xz', 'bytes': 2094196,
        'sha256': 'a3c2b80201b89e68616f4ad30bc66aee4927c3ce50e33929ca819d5c43538898',
        'sourceRoot': 'gmp-6.3.0-a3c2b802',
        'url': 'https://ftp.gnu.org/gnu/gmp/gmp-6.3.0.tar.xz',
        'output': 'apple-crypto-source-gmp.tar.xz',
    },
    {
        'id': 'nettle', 'version': '4.0', 'archiveRoot': 'nettle-4.0',
        'filename': 'nettle-4.0.tar.gz', 'compression': 'gz', 'bytes': None,
        'sha256': '3addbc00da01846b232fb3bc453538ea5468da43033f21bb345cb1e9073f5094',
        'sourceRoot': 'nettle-4.0-3addbc00',
        'url': 'https://ftp.gnu.org/gnu/nettle/nettle-4.0.tar.gz',
        'output': 'apple-crypto-source-nettle.tar.gz',
    },
    {
        'id': 'gnutls', 'version': '3.8.13', 'archiveRoot': 'gnutls-3.8.13',
        'filename': 'gnutls-3.8.13.tar.xz', 'compression': 'xz', 'bytes': 7275324,
        'sha256': 'ffed8ec1bf09c2426d4f14aae377de4753b53e537d685e604e99a8b16ca9c97e',
        'sourceRoot': 'gnutls-3.8.13-ffed8ec1',
        'url': 'https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.13.tar.xz',
        'output': 'apple-crypto-source-gnutls.tar.xz',
    },
)
CRYPTO_CAPTURE_TAR_OUTPUT = 'apple-crypto-release-sources.tar'
CRYPTO_CAPTURE_RECEIPT_OUTPUT = 'apple-crypto-release-sources.json'
CRYPTO_MAX_ARCHIVE = 8 * 1024 * 1024
CRYPTO_MAX_UNCOMPRESSED = 512 * 1024 * 1024
CRYPTO_MAX_MEMBERS = 100000
CRYPTO_MAX_MEMBER = 64 * 1024 * 1024
CRYPTO_MAX_RECEIPT = 64 * 1024 * 1024
CRYPTO_MAX_PATH = 4096
CRYPTO_MAX_MTIME = 4102444800
CRYPTO_MAX_MTIME_NS = CRYPTO_MAX_MTIME * 1000000000
CRYPTO_INSTALLED_HELPERS = {
    'Sources/BuildScripts/crypto-release-sources.py':
        '3c310499d446eed72eb16521b27c16f718f23e7ab1472dec1fad79cc40ed1537',
    'Sources/BuildScripts/crypto-chain-gate.py':
        'be5bc18e00b9b1cb4c4c5889b0935b4cd5d1538a98bcfb985ccbfcb208825a9b',
}

SUBTITLE_SOURCE_PROFILE = 'unibreak800-fribidi1017-harfbuzz1500-bluray150-release-sources-v3-pax-ns'
SUBTITLE_SOURCE_OUTPUTS = (
    {
        'id': 'libunibreak', 'version': '8.0', 'archiveRoot': 'libunibreak-8.0',
        'filename': 'libunibreak-8.0.tar.gz', 'compression': 'gz', 'bytes': None,
        'maximumBytes': 8 * 1024 * 1024, 'entryPoint': 'configure',
        'sha256': '9c4fad6e517338a098373acc9f35579ae2c325e6446666fb9ac2666ba15ceba4',
        'sourceRoot': 'libunibreak-8.0-9c4fad6e',
        'url': ('https://github.com/adah1972/libunibreak/releases/download/'
                'libunibreak_8_0/libunibreak-8.0.tar.gz'),
        'output': 'apple-subtitle-source-unibreak.tar.gz',
    },
    {
        'id': 'libfribidi', 'version': '1.0.17', 'archiveRoot': 'fribidi-1.0.17',
        'filename': 'fribidi-1.0.17.tar.xz', 'compression': 'xz', 'bytes': 1203284,
        'maximumBytes': 8 * 1024 * 1024, 'entryPoint': 'meson.build',
        'sha256': '6949dcde27d41cebad1fd741fcafc36d55a1020d2d872d4a6eb3914caabbada2',
        'sourceRoot': 'libfribidi-1.0.17-6949dcde',
        'url': 'https://github.com/fribidi/fribidi/releases/download/v1.0.17/fribidi-1.0.17.tar.xz',
        'output': 'apple-subtitle-source-fribidi.tar.xz',
    },
    {
        'id': 'libharfbuzz', 'version': '15.0.0', 'archiveRoot': 'harfbuzz-15.0.0',
        'filename': 'harfbuzz-15.0.0.tar.xz', 'compression': 'xz', 'bytes': 20396372,
        'maximumBytes': 32 * 1024 * 1024, 'entryPoint': 'meson.build',
        'sha256': '968536e9abd782f07f7954044c0c4020199fcdd39b41d221ec42d2a3be4a44df',
        'sourceRoot': 'libharfbuzz-15.0.0-968536e9',
        'url': 'https://github.com/harfbuzz/harfbuzz/releases/download/15.0.0/harfbuzz-15.0.0.tar.xz',
        'output': 'apple-subtitle-source-harfbuzz.tar.xz',
    },
    {
        'id': 'libbluray', 'version': '1.5.0', 'archiveRoot': 'libbluray-1.5.0',
        'filename': 'libbluray-1.5.0.tar.xz', 'compression': 'xz', 'bytes': 473072,
        'maximumBytes': 473072, 'entryPoint': 'meson.build',
        'sha256': 'f676408e91a5d321abf8b8d4dfdae36205c297dab5c54c3ec519639025f474a2',
        'sourceRoot': 'libbluray-1.5.0-f676408e',
        'url': 'https://download.videolan.org/pub/videolan/libbluray/1.5.0/libbluray-1.5.0.tar.xz',
        'sourceTransformPolicy': 'bluray150-retained-apple-mobile-v1',
        'output': 'apple-bluray-source.tar.xz',
    },
)
SUBTITLE_CAPTURE_TAR_OUTPUT = 'apple-subtitle-release-sources.tar'
SUBTITLE_CAPTURE_RECEIPT_OUTPUT = 'apple-subtitle-release-sources.json'
SUBTITLE_BUILD_OUTPUT = 'apple-subtitle-builds.json'
SUBTITLE_INSTALLED_HELPERS = {
    'Sources/BuildScripts/subtitle-release-sources.py':
        'f082ea41b939e8152a2571636f937e2bb9dcfed57ffd2f1dbe65ed68522800df',
}
SUBTITLE_MATRIX = (
    ('ios', 'arm64', 'ios-arm64'),
    ('isimulator', 'arm64', 'ios-arm64_x86_64-simulator'),
    ('isimulator', 'x86_64', 'ios-arm64_x86_64-simulator'),
    ('tvos', 'arm64', 'tvos-arm64_arm64e'),
    ('tvos', 'arm64e', 'tvos-arm64_arm64e'),
    ('tvsimulator', 'arm64', 'tvos-arm64_x86_64-simulator'),
    ('tvsimulator', 'x86_64', 'tvos-arm64_x86_64-simulator'),
    ('macos', 'arm64', 'macos-arm64_x86_64'),
    ('macos', 'x86_64', 'macos-arm64_x86_64'),
)
SUBTITLE_VERSIONS = {'libunibreak': '8.0', 'libfribidi': '1.0.17',
                     'libharfbuzz': '15.0.0', 'libbluray': '1.5.0'}
SUBTITLE_SELECTORS = {'libunibreak': '8.0-9c4fad6e',
                      'libfribidi': '1.0.17-6949dcde',
                      'libharfbuzz': '15.0.0-968536e9', 'libbluray': '1.5.0-f676408e'}
SUBTITLE_ROUTES = {'libunibreak': 'release-autoconf',
                   'libfribidi': 'release-meson',
                   'libharfbuzz': 'release-meson', 'libbluray': 'release-meson'}
SUBTITLE_ARGUMENTS = {
    'libbluray': ('--wrap-mode=nodownload', '--force-fallback-for=libudfread',
                   '-Denable_docs=false', '-Denable_tools=false', '-Denable_devtools=false',
                   '-Denable_examples=false', '-Dbdj_jar=disabled', '-Dembed_udfread=true',
                   '-Dfontconfig=disabled', '-Dfreetype=enabled', '-Dlibxml2=enabled'),
    'libunibreak': None,
    'libfribidi': ('--wrap-mode=nodownload', '-Dtests=false', '-Ddocs=false',
                   '-Dbin=false', '-Ddeprecated=true'),
    'libharfbuzz': ('--wrap-mode=nodownload', '-Dauto_features=disabled',
                    '-Dtests=disabled', '-Ddocs=disabled', '-Dutilities=disabled',
                    '-Dintrospection=disabled', '-Dfreetype=disabled',
                    '-Dcoretext=disabled', '-Dglib=disabled', '-Dgobject=disabled',
                    '-Dcairo=disabled', '-Dchafa=disabled', '-Dpng=disabled',
                    '-Dzlib=disabled', '-Dicu=disabled', '-Dgraphite2=disabled',
                    '-Dfontations=disabled', '-Dharfrust=disabled', '-Dkbts=disabled',
                    '-Dwasm=disabled', '-Dgpu_demo=disabled', '-Dbenchmark=disabled',
                    '-Draster=disabled', '-Dvector=disabled', '-Dgpu=disabled',
                    '-Dsubset=disabled'),
}
SUBTITLE_MAKE_ARGUMENTS = ('-j1', 'AUTOCONF=/usr/bin/false',
                           'AUTOHEADER=/usr/bin/false', 'AUTOMAKE=/usr/bin/false',
                           'ACLOCAL=/usr/bin/false')
SUBTITLE_GENERATED = {
    'libbluray': ('crossFile.meson', 'build.ninja', 'compile_commands.json',
                  'meson-info/intro-buildoptions.json', 'meson-info/intro-dependencies.json',
                  'config.h', 'contrib/libudfread/config.h'),
    'libunibreak': ('Makefile', 'config.status', 'config.log'),
    'libfribidi': ('crossFile.meson', 'build.ninja', 'compile_commands.json',
                   'meson-info/intro-buildoptions.json',
                   'meson-info/intro-dependencies.json'),
    'libharfbuzz': ('crossFile.meson', 'build.ninja', 'compile_commands.json',
                    'meson-info/intro-buildoptions.json',
                    'meson-info/intro-dependencies.json'),
}
SUBTITLE_INSTALLED = {
    'libbluray': ('include/libbluray/bluray-version.h', 'lib/pkgconfig/libbluray.pc'),
    'libunibreak': ('include/unibreakbase.h', 'lib/pkgconfig/libunibreak.pc'),
    'libfribidi': ('include/fribidi/fribidi-config.h', 'lib/pkgconfig/fribidi.pc'),
    'libharfbuzz': ('include/harfbuzz/hb-version.h', 'lib/pkgconfig/harfbuzz.pc'),
}
SUBTITLE_MAX_RECORD = 16 * 1024 * 1024
SUBTITLE_MAX_BODY = 8 * 1024 * 1024
SUBTITLE_MAX_RECORD_BODIES = 8 * 1024 * 1024
SUBTITLE_MAX_INLINE_RAW = 32 * 1024 * 1024
SUBTITLE_MAX_RECEIPT = 64 * 1024 * 1024


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


def _bluray_source_body(pin, name, body):
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



def _crypto_canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n').encode()


def _crypto_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode,
            info.st_mtime_ns, info.st_ctime_ns)


def _crypto_read_regular(path, maximum):
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('crypto source input must be one absolute path')
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            raise ValueError('crypto source input has a linked ancestor')
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size < 0 or before.st_size > maximum):
        raise ValueError('crypto source input is not one bounded regular file')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _crypto_identity(opened) != _crypto_identity(before):
            raise ValueError('crypto source input changed before reading')
        body = stream.read(maximum + 1)
        if len(body) > maximum or _crypto_identity(os.fstat(stream.fileno())) != _crypto_identity(opened):
            raise ValueError('crypto source input changed or exceeded its bound')
    if _crypto_identity(path.lstat()) != _crypto_identity(before) or len(body) != before.st_size:
        raise ValueError('crypto source input changed during reading')
    return body


def _crypto_member_path(value):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > CRYPTO_MAX_PATH
            or not value.isprintable() or value.startswith('/') or '\\' in value
            or ':' in value or '\x00' in value):
        raise ValueError('unsafe crypto source archive path')
    parts = value.rstrip('/').split('/')
    if any(part in ('', '.', '..') or part.casefold() == '.git' for part in parts):
        raise ValueError('unsafe crypto source archive path component')
    return '/'.join(parts)


def _crypto_link_path(parent, target):
    if (not isinstance(target, str) or not target or len(target.encode('utf-8')) > CRYPTO_MAX_PATH
            or not target.isprintable() or target.startswith('/') or '\\' in target
            or ':' in target or '\x00' in target):
        raise ValueError('unsafe crypto source symlink target')
    parts = list(parent)
    for part in target.split('/'):
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                raise ValueError('crypto source symlink escapes its release root')
            parts.pop()
        elif part.casefold() == '.git':
            raise ValueError('crypto source symlink points through Git metadata')
        else:
            parts.append(part)
    return '/'.join(parts)


def _crypto_mtime(value):
    if type(value) is not int or not 0 <= value <= CRYPTO_MAX_MTIME:
        raise ValueError('crypto source mtime must be bounded integral seconds')
    return value


def _crypto_archive_mtime_ns(member):
    """Recover exact archive nanoseconds without trusting TarInfo's float view."""
    if type(member.pax_headers) is not dict:
        raise ValueError('subtitle source PAX metadata must be a dictionary')
    if 'mtime' in member.pax_headers:
        raw = member.pax_headers['mtime']
        if (type(raw) is not str or len(raw) > 32
                or re.fullmatch(r'[0-9]{1,10}(?:\.[0-9]{1,9})?', raw) is None):
            raise ValueError('subtitle source PAX mtime must be bounded decimal text')
        seconds, separator, fraction = raw.partition('.')
        value = int(seconds) * 1000000000
        if separator:
            value += int(fraction.ljust(9, '0'))
    else:
        if type(member.mtime) is not int:
            raise ValueError('subtitle source float-only mtime is not admitted')
        value = member.mtime * 1000000000
    if type(value) is not int or not 0 <= value <= CRYPTO_MAX_MTIME_NS:
        raise ValueError('subtitle source mtimeNs must be bounded integral nanoseconds')
    return value


def _crypto_member_mtime(member, nanoseconds):
    return _crypto_archive_mtime_ns(member) if nanoseconds else _crypto_mtime(member.mtime)


class _CryptoLimitedReader:
    def __init__(self, stream, maximum):
        self.stream, self.maximum, self.count = stream, maximum, 0

    def read(self, size=-1):
        allowance = self.maximum - self.count
        if size < 0 or size > allowance + 1:
            size = allowance + 1
        block = self.stream.read(size)
        self.count += len(block)
        if self.count > self.maximum:
            raise ValueError('crypto source archive exceeds its expanded bound')
        return block


def _crypto_tar_stream(blob, compression):
    raw = io.BytesIO(blob)
    if compression == 'xz':
        decoded = lzma.LZMAFile(raw, 'rb')
    elif compression == 'gz':
        decoded = gzip.GzipFile(fileobj=raw, mode='rb')
    elif compression == 'none':
        decoded = raw
    else:
        raise ValueError('unknown fixed crypto source compression')
    return tarfile.open(fileobj=_CryptoLimitedReader(decoded, CRYPTO_MAX_UNCOMPRESSED), mode='r|')


def _crypto_file_row(member, stream, nanoseconds=False):
    if member.size < 0 or member.size > CRYPTO_MAX_MEMBER:
        raise ValueError('crypto source member exceeds its bound')
    hasher, count = hashlib.sha256(), 0
    while True:
        block = stream.read(min(1024 * 1024, member.size - count + 1))
        if not block:
            break
        count += len(block)
        if count > member.size:
            raise ValueError('crypto source member exceeds its declared length')
        hasher.update(block)
    if count != member.size:
        raise ValueError('truncated crypto source member')
    return {'kind': 'file', 'mode': 0o755 if member.mode & 0o111 else 0o644,
            ('mtimeNs' if nanoseconds else 'mtime'):
                _crypto_member_mtime(member, nanoseconds),
            'bytes': count, 'sha256': hasher.hexdigest()}


def _crypto_finish_index(entries, nanoseconds=False):
    time_key = 'mtimeNs' if nanoseconds else 'mtime'
    for name in list(entries):
        parts = name.split('/') if name else []
        for end in range(1, len(parts)):
            parent = '/'.join(parts[:end])
            row = entries.setdefault(parent, {'kind': 'directory', 'mode': 0o755,
                                               time_key: 0})
            if row['kind'] != 'directory':
                raise ValueError('crypto source non-directory member has children')
            if len(entries) > CRYPTO_MAX_MEMBERS:
                raise ValueError('crypto source archive has too many paths')
    folded = {}
    for name in entries:
        key = name.casefold()
        if key in folded and folded[key] != name:
            raise ValueError('crypto source archive has case-colliding members')
        folded[key] = name

    def resolve(name, visited=()):
        if len(visited) >= 32:
            raise ValueError('crypto source symlink chain exceeds its bound')
        parts = name.split('/') if name else []
        for end in range(1, len(parts) + 1):
            prefix = '/'.join(parts[:end])
            row = entries.get(prefix)
            if row is None:
                raise ValueError('dangling crypto source symlink')
            if row['kind'] == 'symlink':
                if prefix in visited:
                    raise ValueError('cyclic crypto source symlink')
                destination = _crypto_link_path(parts[:end - 1], row['target'])
                tail = parts[end:]
                following = '/'.join(([destination] if destination else []) + tail)
                return resolve(following, visited + (prefix,))
            if end < len(parts) and row['kind'] != 'directory':
                raise ValueError('crypto source symlink resolves through a non-directory')
        return name

    for name, row in entries.items():
        if row['kind'] == 'symlink':
            resolve(name)
    return [{'path': name, **entries[name]} for name in sorted(entries)]


def _crypto_index_archive(pin, blob, nanoseconds=False):
    maximum_archive = min(CRYPTO_MAX_UNCOMPRESSED,
                          pin.get('maximumBytes', CRYPTO_MAX_ARCHIVE))
    if (len(blob) > maximum_archive
            or (pin['bytes'] is not None and len(blob) != pin['bytes'])
            or hashlib.sha256(blob).hexdigest() != pin['sha256']):
        raise ValueError('crypto release source body differs from its fixed pin')
    time_key = 'mtimeNs' if nanoseconds else 'mtime'
    entries = {'': {'kind': 'directory', 'mode': 0o755, time_key: 0}}
    seen, total, transformed = set(), 0, set()
    with _crypto_tar_stream(blob, pin['compression']) as archive:
        for member in archive:
            if len(seen) >= CRYPTO_MAX_MEMBERS:
                raise ValueError('crypto source archive has too many members')
            full = _crypto_member_path(member.name)
            root, separator, name = full.partition('/')
            if root != pin['archiveRoot']:
                raise ValueError('crypto source archive has a foreign top-level root')
            if full.casefold() in seen:
                raise ValueError('duplicate or case-colliding crypto source member')
            seen.add(full.casefold())
            if getattr(member, 'sparse', None) or any('sparse' in key.casefold() for key in member.pax_headers):
                raise ValueError('sparse crypto source members are not admitted')
            if not separator:
                if not member.isdir():
                    raise ValueError('crypto source archive root is not a directory')
                entries[''][time_key] = _crypto_member_mtime(member, nanoseconds)
                continue
            if member.isdir():
                row = {'kind': 'directory', 'mode': 0o755,
                       time_key: _crypto_member_mtime(member, nanoseconds)}
            elif member.isreg():
                total += member.size
                if total > CRYPTO_MAX_UNCOMPRESSED:
                    raise ValueError('crypto source member total exceeds its bound')
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError('missing crypto source member body')
                with stream:
                    if pin['id'] == 'libbluray' and name in BLURAY_SOURCE_TRANSFORMS:
                        raw = stream.read(CRYPTO_MAX_MEMBER + 1)
                        if len(raw) != member.size or len(raw) > CRYPTO_MAX_MEMBER:
                            raise ValueError('libbluray raw source member length differs')
                        data = _bluray_source_body(pin, name, raw)
                        transformed.add(name)
                        row = {'kind': 'file', 'mode': 0o755 if member.mode & 0o111 else 0o644,
                               time_key: _crypto_member_mtime(member, nanoseconds),
                               'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                    else:
                        row = _crypto_file_row(member, stream, nanoseconds)
            elif member.issym():
                _crypto_link_path(name.split('/')[:-1], member.linkname)
                body = member.linkname.encode('utf-8')
                row = {'kind': 'symlink', 'mode': 0o777, 'bytes': len(body),
                       time_key: _crypto_member_mtime(member, nanoseconds),
                       'sha256': hashlib.sha256(body).hexdigest(), 'target': member.linkname}
            else:
                raise ValueError('crypto source hardlinks and special members are not admitted')
            if name in entries:
                raise ValueError('duplicate crypto source member')
            entries[name] = row
    if pin['id'] == 'libbluray' and transformed != set(BLURAY_SOURCE_TRANSFORMS):
        raise ValueError('libbluray fixed source transform members are incomplete')
    rows = _crypto_finish_index(entries, nanoseconds)
    entry_point = pin.get('entryPoint', 'configure')
    entry = next((row for row in rows if row['path'] == entry_point), None)
    if (entry is None or entry['kind'] != 'file'
            or (entry_point == 'configure' and entry['mode'] != 0o755)):
        raise ValueError('release archive lacks its fixed regular build entry point')
    return rows


def _crypto_expected_receipt(verified, profile=None,
                             identity_basis='fixed-sha256-corroborated-by-package-maintainer-metadata',
                             schema=1):
    if profile is None:
        profile = CRYPTO_SOURCE_PROFILE
    roots = []
    for pin, blob, rows in verified:
        roots.append({
            'id': pin['id'], 'version': pin['version'], 'url': pin['url'],
            'archive': 'archives/' + pin['filename'], 'archiveRoot': pin['archiveRoot'],
            'archiveBytes': len(blob), 'archiveSHA256': pin['sha256'],
            'sourceRoot': 'dist/' + pin['sourceRoot'],
            'membersSHA256': hashlib.sha256(_crypto_canonical(rows)).hexdigest(),
            'members': rows,
            **({'sourceTransformPolicy': pin.get('sourceTransformPolicy', 'none')} if schema == 3 else {}),
        })
    return {'schema': schema, 'profile': profile,
            'identityBasis': identity_basis,
            'upstreamSignatureVerified': False, 'roots': roots}


def _crypto_strict_json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate crypto source receipt key')
            result[key] = value
        return result
    return json.loads(data.decode('utf-8', errors='strict'), object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          ValueError('non-finite crypto source receipt number')))


def _crypto_exact_equal(left, right):
    """JSON equality that never aliases booleans with integers."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return (set(left) == set(right) and
                all(_crypto_exact_equal(left[key], right[key]) for key in left))
    if isinstance(left, list):
        return (len(left) == len(right) and
                all(_crypto_exact_equal(a, b) for a, b in zip(left, right)))
    return left == right


def _crypto_capture_correspondence(path, expected, member_limit=None,
                                   nanoseconds=False):
    if member_limit is None:
        member_limit = CRYPTO_MAX_MEMBERS * len(CRYPTO_SOURCE_OUTPUTS)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('captured crypto source tar must be one absolute regular file')
    before = path.lstat()
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size < 1 or before.st_size > CRYPTO_MAX_UNCOMPRESSED):
        raise ValueError('captured crypto source tar exceeds its bound')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    observed, count, folded = set(), 0, set()
    with os.fdopen(descriptor, 'rb') as stream:
        opened = os.fstat(stream.fileno())
        if _crypto_identity(opened) != _crypto_identity(before):
            raise ValueError('captured crypto source tar changed before reading')
        bounded = _CryptoLimitedReader(stream, CRYPTO_MAX_UNCOMPRESSED)
        with tarfile.open(fileobj=bounded, mode='r|') as archive:
            for member in archive:
                name = _crypto_member_path(member.name)
                count += 1
                if (count > member_limit
                        or name in observed or name.casefold() in folded or name not in expected):
                    raise ValueError('captured crypto source tar has an extra or duplicate member')
                observed.add(name)
                folded.add(name.casefold())
                wanted = expected[name]
                time_key = 'mtimeNs' if nanoseconds else 'mtime'
                if member.isdir():
                    actual = {'kind': 'directory', 'mode': member.mode,
                              time_key: _crypto_member_mtime(member, nanoseconds)}
                elif (member.isreg() and member.mode in (0o644, 0o755)
                      and not getattr(member, 'sparse', None)
                      and not any('sparse' in key.casefold() for key in member.pax_headers)):
                    body = archive.extractfile(member)
                    if body is None:
                        raise ValueError('captured crypto source member has no body')
                    with body:
                        actual = _crypto_file_row(member, body, nanoseconds)
                elif member.issym():
                    _crypto_link_path(name.split('/')[:-1], member.linkname)
                    body = member.linkname.encode('utf-8')
                    actual = {'kind': 'symlink', 'mode': 0o777, 'bytes': len(body),
                              time_key: _crypto_member_mtime(member, nanoseconds),
                              'sha256': hashlib.sha256(body).hexdigest(),
                              'target': member.linkname}
                else:
                    raise ValueError('captured crypto source tar has an unsupported member')
                if not _crypto_exact_equal(actual, wanted):
                    raise ValueError('captured crypto source member differs from its raw archive')
        if _crypto_identity(os.fstat(stream.fileno())) != _crypto_identity(opened):
            raise ValueError('captured crypto source tar changed during reading')
    if _crypto_identity(path.lstat()) != _crypto_identity(before) or observed != set(expected):
        raise ValueError('captured crypto source tar changed or omitted release members')


def crypto_source_outputs(output, *, pins=None, profile=None,
                          capture_tar=None, capture_receipt=None,
                          capture_tar_logical='crypto-release-sources.tar',
                          capture_receipt_logical='crypto-release-sources.json',
                          identity_basis='fixed-sha256-corroborated-by-package-maintainer-metadata',
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
                      pin.get('maximumBytes', CRYPTO_MAX_ARCHIVE))
        body = _crypto_read_regular(output / pin['output'], maximum)
        rows = _crypto_index_archive(pin, body, nanoseconds)
        verified.append((pin, body, rows))
        output_rows.append({'logicalPath': 'archives/' + pin['filename'],
                            'name': pin['output'], 'bytes': len(body),
                            'sha256': hashlib.sha256(body).hexdigest()})
    expected_receipt = _crypto_expected_receipt(
        verified, profile, identity_basis, receipt_schema)
    receipt_body = _crypto_read_regular(output / capture_receipt,
                                        CRYPTO_MAX_RECEIPT)
    if (not _crypto_exact_equal(_crypto_strict_json(receipt_body), expected_receipt)
            or receipt_body != _crypto_canonical(expected_receipt)):
        raise ValueError('captured crypto source receipt differs from raw source bodies')
    expected_members = {}
    for pin, _, rows in verified:
        for row in rows:
            name = pin['archiveRoot'] + (('/' + row['path']) if row['path'] else '')
            if name in expected_members:
                raise ValueError('overlapping captured crypto source roots')
            expected_members[name] = {key: value for key, value in row.items() if key != 'path'}
    capture_path = output / capture_tar
    _crypto_capture_correspondence(
        capture_path, expected_members, CRYPTO_MAX_MEMBERS * len(pins), nanoseconds)
    capture_body = _crypto_read_regular(capture_path, CRYPTO_MAX_UNCOMPRESSED)
    receipt_row = {'logicalPath': capture_receipt_logical,
                   'name': capture_receipt, 'bytes': len(receipt_body),
                   'sha256': hashlib.sha256(receipt_body).hexdigest()}
    capture_row = {'logicalPath': capture_tar_logical,
                   'name': capture_tar, 'bytes': len(capture_body),
                   'sha256': hashlib.sha256(capture_body).hexdigest()}
    roots = []
    for root, pin in zip(expected_receipt['roots'], pins, strict=True):
        roots.append({'id': root['id'], 'version': root['version'],
                      'archiveRoot': root['archiveRoot'], 'sourceRoot': root['sourceRoot'],
                      'archive': output_rows[len(roots)],
                      'membersSHA256': root['membersSHA256'],
                      **({'sourceTransformPolicy': root['sourceTransformPolicy']} if receipt_schema == 3 else {})})
    return {'profile': profile, 'roots': roots,
            'capture': {'tar': capture_row, 'receipt': receipt_row}}


def subtitle_source_outputs(output):
    return crypto_source_outputs(
        output, pins=SUBTITLE_SOURCE_OUTPUTS, profile=SUBTITLE_SOURCE_PROFILE,
        capture_tar=SUBTITLE_CAPTURE_TAR_OUTPUT,
        capture_receipt=SUBTITLE_CAPTURE_RECEIPT_OUTPUT,
        capture_tar_logical='subtitle-release-sources.tar',
        capture_receipt_logical='subtitle-release-sources.json',
        identity_basis='fixed-sha256-from-upstream-release-metadata-maintained-android-pin-or-official-sidecar; fixed-apple-source-transforms',
        receipt_schema=3, nanoseconds=True)


def _shaderc_regular(path, label):
    """Require one immutable regular input; never follow an input symlink."""
    if (not path.is_absolute() or path.is_symlink() or not path.is_file()):
        raise ValueError('shaderc ' + label + ' must be an absolute regular file')
    stat_result = path.stat()
    if not stat.S_ISREG(stat_result.st_mode) or stat_result.st_nlink != 1:
        raise ValueError('shaderc ' + label + ' is not an exact regular file')
    return stat_result


def _shaderc_body(path, expected_name, expected_bytes, expected_sha256):
    _shaderc_regular(path, expected_name)
    observed = {'bytes': 0, 'sha256': hashlib.sha256()}
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            observed['bytes'] += len(block)
            if observed['bytes'] > expected_bytes:
                raise ValueError('shaderc input exceeds its pinned byte count: ' + expected_name)
            observed['sha256'].update(block)
    digest_value = observed['sha256'].hexdigest()
    if observed['bytes'] != expected_bytes or digest_value != expected_sha256:
        raise ValueError('shaderc input bytes/hash differ: ' + expected_name)
    return {'name': expected_name, 'bytes': observed['bytes'], 'sha256': digest_value}


def _shaderc_file_sha(path, label):
    """Hash a bounded generated file without accepting a link or special file."""
    _shaderc_regular(path, label)
    size = path.stat().st_size
    if size > SHADERC_MAX_MEMBER:
        raise ValueError('shaderc generated file exceeds bound: ' + label)
    h = hashlib.sha256()
    count = 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            count += len(block)
            if count > SHADERC_MAX_MEMBER:
                raise ValueError('shaderc generated file exceeds bound: ' + label)
            h.update(block)
    if count != size:
        raise ValueError('shaderc generated file length changed: ' + label)
    return count, h.hexdigest()


def _shaderc_archive_name(name):
    try:
        encoded = name.encode('utf-8') if isinstance(name, str) else b''
    except UnicodeEncodeError as exc:
        raise ValueError('unsafe shaderc ZIP entry name') from exc
    if (not isinstance(name, str) or not name or len(encoded) > 4096
            or '\x00' in name or '\\' in name or name.startswith('/')
            or not name.isprintable()):
        raise ValueError('unsafe shaderc ZIP entry name')
    pieces = name.rstrip('/').split('/')
    if not pieces or any(piece in ('', '.', '..') for piece in pieces):
        raise ValueError('traversal shaderc ZIP entry name')
    return '/'.join(pieces)


def _shaderc_link_body(archive, info, name):
    """Read and authenticate one bounded ZIP link body; never create a link."""
    if info.file_size > SHADERC_MAX_LINK_BODY:
        raise ValueError('shaderc ZIP symlink body exceeds bound: ' + name)
    with archive.open(info) as stream:
        raw = stream.read(SHADERC_MAX_LINK_BODY + 1)
    if len(raw) != info.file_size or len(raw) > SHADERC_MAX_LINK_BODY:
        raise ValueError('shaderc ZIP symlink body length changed: ' + name)
    try:
        target = raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('shaderc ZIP symlink body is not UTF-8: ' + name) from exc
    try:
        canonical = _shaderc_archive_name(target)
    except ValueError as exc:
        raise ValueError('unsafe shaderc ZIP symlink target: ' + name) from exc
    if canonical != target:
        raise ValueError('non-canonical shaderc ZIP symlink target: ' + name)
    return target, len(raw), hashlib.sha256(raw).hexdigest()


def _shaderc_within_prefix(name, prefix):
    return name == prefix or name.startswith(prefix + '/')


def _shaderc_link_target(name, target, framework_prefix):
    """Resolve a relative archive link without touching the filesystem."""
    try:
        canonical = _shaderc_archive_name(target)
    except ValueError as exc:
        raise ValueError('unsafe shaderc ZIP symlink target: ' + name) from exc
    if canonical != target:
        raise ValueError('non-canonical shaderc ZIP symlink target: ' + name)
    parent = name.rsplit('/', 1)[0]
    candidate = posixpath.normpath(posixpath.join(parent, canonical))
    if (not _shaderc_within_prefix(candidate, framework_prefix)
            or candidate == framework_prefix):
        raise ValueError('shaderc ZIP symlink target escapes framework: ' + name)
    return candidate


def _shaderc_resolve_archive_path(rows, requested, framework_prefix,
                                  *, max_hops=SHADERC_MAX_LINK_HOPS):
    """Resolve a requested archive path through indexed links only.

    The resolver follows a known link component such as Versions/Current in
    the in-archive index.  It never calls Path.resolve/readlink and returns the
    final indexed file or directory row.
    """
    if type(rows) is not dict or type(max_hops) is not int or max_hops < 0:
        raise ValueError('invalid shaderc ZIP resolver inputs')
    try:
        framework_prefix = _shaderc_archive_name(framework_prefix)
        current = _shaderc_archive_name(requested)
    except ValueError as exc:
        raise ValueError('unsafe shaderc ZIP resolver path') from exc
    if not _shaderc_within_prefix(current, framework_prefix):
        raise ValueError('shaderc ZIP resolver path escapes framework')

    framework_parts = framework_prefix.split('/')
    seen = set()
    hops = 0
    while True:
        if current in seen:
            raise ValueError('shaderc ZIP symlink cycle')
        seen.add(current)
        row = rows.get(current)
        if row is not None:
            kind = row.get('kind') if isinstance(row, dict) else None
            if kind == 'symlink':
                if row.get('directory') is not False:
                    raise ValueError('shaderc ZIP symlink directory shape')
                hops += 1
                if hops > max_hops:
                    raise ValueError('shaderc ZIP symlink hop bound')
                current = _shaderc_link_target(current, row.get('target'),
                                               framework_prefix)
                continue
            if kind in ('file', 'directory'):
                return current, row
            raise ValueError('invalid shaderc ZIP resolver row: ' + current)

        # A requested target can be below a known link component.  Prefer the
        # longest such component, then retain the suffix inside this framework.
        parts = current.split('/')
        link_prefix = None
        for index in range(len(parts) - 1, len(framework_parts), -1):
            candidate = '/'.join(parts[:index])
            candidate_row = rows.get(candidate)
            if candidate_row is None:
                continue
            candidate_kind = (candidate_row.get('kind')
                              if isinstance(candidate_row, dict) else None)
            if candidate_kind == 'symlink':
                link_prefix = candidate
                break
            if candidate_kind != 'directory':
                raise ValueError('shaderc ZIP regular-file ancestor: ' + candidate)
        if link_prefix is None:
            raise ValueError('missing shaderc ZIP symlink target: ' + current)
        link_row = rows[link_prefix]
        hops += 1
        if hops > max_hops:
            raise ValueError('shaderc ZIP symlink hop bound')
        replacement = _shaderc_link_target(link_prefix,
                                            link_row.get('target'),
                                            framework_prefix)
        suffix = current[len(link_prefix):].lstrip('/')
        current = replacement + (('/' + suffix) if suffix else '')


def _shaderc_validate_xc_links(rows):
    """Require the authenticated three-link macOS framework closure."""
    links = {name: row for name, row in rows.items()
             if row.get('kind') == 'symlink'}
    if set(links) != set(SHADERC_XC_LINKS):
        raise ValueError('shaderc XCFramework symlink set differs')
    expected_final = {
        SHADERC_XC_MAC_FRAMEWORK + '/Resources':
            (SHADERC_XC_MAC_FRAMEWORK + '/Versions/A/Resources', 'directory'),
        SHADERC_XC_MAC_FRAMEWORK + '/Libshaderc_combined':
            (SHADERC_XC_MAC_FRAMEWORK + '/Versions/A/Libshaderc_combined', 'file'),
        SHADERC_XC_MAC_FRAMEWORK + '/Versions/Current':
            (SHADERC_XC_MAC_FRAMEWORK + '/Versions/A', 'directory'),
    }
    for name, target in SHADERC_XC_LINKS.items():
        row = links[name]
        if row.get('target') != target:
            raise ValueError('shaderc XCFramework symlink target differs: ' + name)
        resolved_name, resolved_row = _shaderc_resolve_archive_path(
            rows, name, SHADERC_XC_MAC_FRAMEWORK)
        expected_name, expected_kind = expected_final[name]
        if (resolved_name != expected_name
                or resolved_row.get('kind') != expected_kind):
            raise ValueError('shaderc XCFramework symlink target missing: ' + name)


def _shaderc_zip_index(path, *, role='all'):
    """Index a bounded ZIP; links are permitted only for the fixed XC role."""
    if role not in ('all', 'xcframework'):
        raise ValueError('invalid shaderc ZIP role')
    rows, folded, total = {}, set(), 0
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > SHADERC_MAX_ENTRIES:
            raise ValueError('shaderc ZIP entry bound')
        for info in infos:
            name = _shaderc_archive_name(info.filename)
            key = name.casefold()
            if key in folded:
                raise ValueError('duplicate/case-colliding shaderc ZIP entry')
            folded.add(key)
            mode = (info.external_attr >> 16) & 0xffff
            file_type = stat.S_IFMT(mode)
            directory = info.is_dir()
            symlink = file_type == stat.S_IFLNK
            if symlink:
                if role != 'xcframework' or name not in SHADERC_XC_LINKS:
                    raise ValueError('unlisted shaderc ZIP symlink')
                if directory:
                    raise ValueError('shaderc ZIP symlink directory shape')
            elif file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError('special/symlink shaderc ZIP entry')
            if file_type == stat.S_IFDIR and not directory:
                raise ValueError('shaderc ZIP directory shape')
            if directory and file_type not in (0, stat.S_IFDIR):
                raise ValueError('shaderc ZIP directory shape')
            if info.flag_bits & 1:
                raise ValueError('encrypted shaderc ZIP entry')
            total += info.file_size
            if info.file_size > SHADERC_MAX_MEMBER or total > SHADERC_MAX_TOTAL:
                raise ValueError('shaderc ZIP inflated byte bound')
            row = {'info': info, 'directory': directory,
                   'bytes': info.file_size, 'compressedBytes': info.compress_size,
                   'kind': 'symlink' if symlink else ('directory' if directory else 'file')}
            if symlink:
                target, raw_bytes, raw_sha256 = _shaderc_link_body(archive, info, name)
                if target != SHADERC_XC_LINKS[name]:
                    raise ValueError('shaderc XCFramework symlink target differs: ' + name)
                row.update(target=target, rawBytes=raw_bytes,
                           rawSHA256=raw_sha256)
            rows[name] = row

        # Reject case-ambiguous implicit prefixes as well as exact duplicates.
        prefixes = {}
        for name in rows:
            pieces = name.split('/')
            for index in range(1, len(pieces) + 1):
                prefix = '/'.join(pieces[:index])
                folded_prefix = prefix.casefold()
                prior = prefixes.get(folded_prefix)
                if prior is not None and prior != prefix:
                    raise ValueError('case-colliding shaderc ZIP path')
                prefixes[folded_prefix] = prefix

        # A regular file or link may not be an ancestor of any archive entry.
        for name, row in rows.items():
            parts = name.split('/')
            for index in range(1, len(parts)):
                ancestor = '/'.join(parts[:index])
                ancestor_row = rows.get(ancestor)
                if ancestor_row is not None and not ancestor_row['directory']:
                    raise ValueError('shaderc ZIP file/symlink ancestor')
    if role == 'xcframework':
        _shaderc_validate_xc_links(rows)
    return rows


def _shaderc_entry_sha(path, rows, name):
    row = rows.get(name)
    if row is None or row.get('kind') != 'file':
        raise ValueError('missing shaderc ZIP regular entry: ' + name)
    h = hashlib.sha256()
    count = 0
    with zipfile.ZipFile(path) as archive, archive.open(row['info']) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            count += len(block)
            if count > SHADERC_MAX_MEMBER:
                raise ValueError('shaderc ZIP member exceeds bound: ' + name)
            h.update(block)
    if count != row['bytes']:
        raise ValueError('shaderc ZIP member length changed: ' + name)
    return count, h.hexdigest()


def _shaderc_receipt_rows(path):
    """Return authenticated hashes for the nine retained all-ZIP members."""
    rows = _shaderc_zip_index(path, role='all')
    result = {}
    for _, arch, platform_name in SHADERC_MATRIX:
        name = f'lib/{platform_name}/thin/{arch}/lib/libshaderc_combined.a'
        count, checksum = _shaderc_entry_sha(path, rows, name)
        result[name] = {'kind': 'file', 'bytes': count, 'sha256': checksum}
    return result


def _shaderc_tool_sha(path, label):
    """Hash a stable SDK tool; host hardlinks do not waive archive-input rules."""
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('shaderc ' + label + ' must be an absolute regular SDK tool')

    def identity(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    before = path.lstat()
    maximum = 128 * 1024**2
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink < 1
            or not 0 < before.st_size <= maximum):
        raise ValueError('shaderc ' + label + ' is not a bounded regular SDK tool')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    checksum = hashlib.sha256()
    count = 0
    with os.fdopen(descriptor, 'rb') as source:
        if identity(os.fstat(source.fileno())) != identity(before):
            raise ValueError('shaderc SDK tool changed before open: ' + label)
        for block in iter(lambda: source.read(1024 * 1024), b''):
            count += len(block)
            if count > maximum or count > before.st_size:
                raise ValueError('shaderc SDK tool exceeded its bound: ' + label)
            checksum.update(block)
        if identity(os.fstat(source.fileno())) != identity(before):
            raise ValueError('shaderc SDK tool changed while hashing: ' + label)
    if count != before.st_size or identity(path.lstat()) != identity(before):
        raise ValueError('shaderc SDK tool identity changed: ' + label)
    return checksum.hexdigest()


def _shaderc_lipo_identity():
    requested = Path('/usr/bin/xcrun')
    finder_sha256 = _shaderc_tool_sha(requested, 'xcrun finder')
    resolved_text = run('/usr/bin/xcrun', '--find', 'lipo').strip()
    if _shaderc_tool_sha(requested, 'xcrun finder') != finder_sha256:
        raise ValueError('shaderc xcrun finder changed during lookup')
    if (not resolved_text or '\x00' in resolved_text or not resolved_text.startswith('/')
            or len(resolved_text) > 4096):
        raise ValueError('xcrun returned an invalid lipo path')
    resolved = Path(resolved_text).resolve(strict=True)
    reader_sha256 = _shaderc_tool_sha(resolved, 'resolved lipo reader')
    toolchain = Path('/usr/bin/xcodebuild')
    toolchain_sha256 = _shaderc_tool_sha(toolchain, 'xcodebuild identity reader')
    toolchain_version = run('/usr/bin/xcodebuild', '-version').strip()
    if not toolchain_version or '\x00' in toolchain_version or len(toolchain_version) > 4096:
        raise ValueError('xcodebuild toolchain identity is invalid')
    if _shaderc_tool_sha(toolchain, 'xcodebuild identity reader') != toolchain_sha256:
        raise ValueError('shaderc xcodebuild identity reader changed during inspection')
    return ({'mode': 'xcrun-resolved-lipo', 'requestedReader': str(requested),
             'resolvedReader': str(resolved), 'readerSHA256': reader_sha256,
             'toolchainReader': str(toolchain), 'toolchainSHA256': toolchain_sha256,
             'toolchainVersion': toolchain_version}, resolved)


def _shaderc_thin_xc(source, arch, thin, lipo, architecture_count):
    """Only a true single-arch ar can be copied; a fat wrapper must be thinned."""
    with source.open('rb') as stream:
        prefix = stream.read(8)
    if prefix == b'!<arch>\n':
        if architecture_count != 1:
            raise ValueError('shaderc multi-architecture XCFramework binary is not fat')
        shutil.copyfile(source, thin)
    elif prefix[:4] in (b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                        b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca'):
        run(str(lipo), str(source), '-thin', arch, '-output', str(thin))
    else:
        raise ValueError('shaderc XCFramework binary is not an ar or fat archive')


def _shaderc_matrix_from_archives(all_zip, xc_zip, work):
    all_rows = _shaderc_zip_index(all_zip, role='all')
    xc_rows = _shaderc_zip_index(xc_zip, role='xcframework')
    expected_all = {}
    expected_xc = {}
    for slice_name, arch, platform_name in SHADERC_MATRIX:
        all_name = f'lib/{platform_name}/thin/{arch}/lib/libshaderc_combined.a'
        xc_name = ('Libshaderc_combined.xcframework/' + slice_name +
                   '/Libshaderc_combined.framework/Libshaderc_combined')
        expected_all[(slice_name, arch)] = all_name
        expected_xc[(slice_name, arch)] = xc_name
    if set(expected_all.values()) - set(all_rows) or set(expected_xc.values()) - set(xc_rows):
        raise ValueError('shaderc ZIP lacks an exact nine-architecture input')
    reader, lipo = _shaderc_lipo_identity()
    slice_arches = {}
    for slice_name, arch, _ in SHADERC_MATRIX:
        slice_arches.setdefault(slice_name, set()).add(arch)
    rows = []
    with tempfile.TemporaryDirectory(prefix='.shaderc-lipo-', dir=work) as temporary:
        temporary = Path(temporary)
        sources = {}
        for source_index, slice_name in enumerate(slice_arches):
            xc_name = next(expected_xc[(candidate, candidate_arch)]
                           for candidate, candidate_arch, _ in SHADERC_MATRIX
                           if candidate == slice_name)
            framework_prefix = xc_name.rsplit('/Libshaderc_combined', 1)[0]
            resolved_xc_name, resolved_xc_row = _shaderc_resolve_archive_path(
                xc_rows, xc_name, framework_prefix)
            expected_xc_name = xc_name
            if slice_name == 'macos-arm64_x86_64':
                expected_xc_name = framework_prefix + '/Versions/A/Libshaderc_combined'
            if (resolved_xc_name != expected_xc_name
                    or resolved_xc_row.get('kind') != 'file'):
                raise ValueError('shaderc XCFramework binary did not resolve to a regular member: '
                                 + xc_name)
            source = temporary / ('source-' + str(source_index))
            with zipfile.ZipFile(xc_zip) as archive, archive.open(
                    resolved_xc_row['info']) as stream:
                with source.open('xb') as sink:
                    copied = 0
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        copied += len(block)
                        if copied > SHADERC_MAX_MEMBER:
                            raise ValueError('shaderc XCFramework binary exceeds bound')
                        sink.write(block)
            if copied != resolved_xc_row['bytes']:
                raise ValueError('shaderc XCFramework binary length changed')
            _shaderc_regular(source, 'temporary XCFramework binary')
            listed_arches = run(str(lipo), '-archs', str(source)).split()
            expected_arches = slice_arches[slice_name]
            if len(listed_arches) != len(set(listed_arches)) or set(listed_arches) != expected_arches:
                raise ValueError('shaderc XCFramework architecture set differs: ' + slice_name)
            sources[slice_name] = source
        for index, (slice_name, arch, platform_name) in enumerate(SHADERC_MATRIX):
            all_name = expected_all[(slice_name, arch)]
            all_bytes, all_sha256 = _shaderc_entry_sha(all_zip, all_rows, all_name)
            restored = (work / 'dist' / 'libshaderc' / platform_name / 'thin' / arch / 'lib'
                        / 'libshaderc_combined.a')
            if restored.parent.resolve() != restored.parent:
                raise ValueError('shaderc restored archive path contains a link')
            restored_bytes, restored_sha256 = _shaderc_file_sha(
                restored, 'restored ' + all_name)
            if (restored_bytes, restored_sha256) != (all_bytes, all_sha256):
                raise ValueError('shaderc restored thin archive differs: ' + all_name)
            source = sources[slice_name]
            thin = temporary / ('thin-' + str(index))
            _shaderc_thin_xc(source, arch, thin, lipo, len(slice_arches[slice_name]))
            xc_bytes, xc_sha256 = _shaderc_file_sha(thin, 'lipo output')
            if all_sha256 != xc_sha256:
                raise ValueError('shaderc all/XCFramework archive mismatch: ' + all_name)
            rows.append({'slice': slice_name, 'arch': arch, 'allZipEntry': all_name,
                         'allArchiveSHA256': all_sha256, 'xcArchiveSHA256': xc_sha256,
                         'equal': all_sha256 == xc_sha256})
    if _shaderc_tool_sha(lipo, 'resolved lipo reader') != reader['readerSHA256']:
        raise ValueError('shaderc lipo reader changed during matrix inspection')
    if (_shaderc_tool_sha(Path(reader['toolchainReader']), 'xcodebuild identity reader')
            != reader['toolchainSHA256']):
        raise ValueError('shaderc xcodebuild reader changed during matrix inspection')
    return reader, rows


def apple_shaderc_inputs(work):
    """Inspect fixed Shaderc inputs once and emit the producer coherence receipt."""
    work = work.absolute()
    if work.is_symlink() or not work.is_dir() or work.resolve() != work:
        raise ValueError('shaderc WORK must be a real absolute directory')
    input_dir = work / 'dist' / ('libshaderc-' + SHADERC_WRAPPER_VERSION)
    if input_dir.is_symlink() or not input_dir.is_dir() or input_dir.resolve() != input_dir:
        raise ValueError('shaderc input directory is not a real wrapper directory')
    all_zip, xc_zip = (input_dir / name for name, _, _ in SHADERC_SOURCE_SPECS)
    sources = [_shaderc_body(path, name, size, checksum)
               for path, (name, size, checksum) in zip((all_zip, xc_zip), SHADERC_SOURCE_SPECS, strict=True)]
    reader, matrix = _shaderc_matrix_from_archives(all_zip, xc_zip, work)
    receipt = {'schema': 1, 'profile': NATIVE_UPGRADES_PROFILE,
               'wrapperVersion': SHADERC_WRAPPER_VERSION,
               'validatorSHA256': digest(Path(__file__)), 'reader': reader,
               'sources': sources, 'matrix': matrix,
               'runtimeSecurityTested': False, 'promotionEligible': False}
    destination = input_dir / 'vortx-shaderc-inputs.json'
    if destination.exists() or destination.is_symlink():
        raise ValueError('refusing an existing Shaderc input receipt')
    save(destination, receipt)
    print('PASS apple-shaderc-inputs')


def apple_shaderc_receipt(input_dir):
    """Revalidate receipt/body identity only; never run lipo or native inspection."""
    input_dir = input_dir.absolute()
    if input_dir.is_symlink() or not input_dir.is_dir() or input_dir.resolve() != input_dir:
        raise ValueError('shaderc receipt directory is not a real absolute directory')
    receipt_path = input_dir / 'vortx-shaderc-inputs.json'
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise ValueError('missing Shaderc input receipt')
    _shaderc_regular(receipt_path, 'receipt')
    if receipt_path.stat().st_size > 1024 * 1024:
        raise ValueError('Shaderc receipt exceeds bound')
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate Shaderc receipt key')
            result[key] = value
        return result
    with receipt_path.open('rb') as stream:
        encoded = stream.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024:
        raise ValueError('Shaderc receipt exceeds bound')
    value = json.loads(encoded, object_pairs_hook=unique,
                       parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite receipt JSON')))
    for name, size, checksum in SHADERC_SOURCE_SPECS:
        _shaderc_body(input_dir / name, name, size, checksum)
    _shaderc_receipt_shape(value, {name: {'bytes': size, 'sha256': checksum}
                                   for name, size, checksum in SHADERC_SOURCE_SPECS},
                           digest(Path(__file__)),
                           _shaderc_receipt_rows(input_dir / SHADERC_SOURCE_SPECS[0][0]))
    print('PASS apple-shaderc-receipt')


def _shaderc_receipt_shape(value, source_specs, validator_sha256, retained_all_rows=None):
    """Strict receipt shape shared by the producer re-read and offline policy."""
    if retained_all_rows is not None and type(retained_all_rows) is not dict:
        raise ValueError('invalid retained Shaderc all-ZIP index')
    if type(value) is not dict or set(value) != {'schema', 'profile', 'wrapperVersion',
        'validatorSHA256', 'reader', 'sources', 'matrix', 'runtimeSecurityTested',
        'promotionEligible'}:
        raise ValueError('unexpected Shaderc receipt keys')
    if (type(value['schema']) is not int or value['schema'] != 1
            or value['profile'] != NATIVE_UPGRADES_PROFILE
            or value['wrapperVersion'] != SHADERC_WRAPPER_VERSION
            or value['validatorSHA256'] != validator_sha256
            or value['runtimeSecurityTested'] is not False
            or value['promotionEligible'] is not False):
        raise ValueError('invalid Shaderc receipt identity/boundary')
    reader = value['reader']
    if (type(reader) is not dict or set(reader) != {'mode', 'requestedReader', 'resolvedReader',
        'readerSHA256', 'toolchainReader', 'toolchainSHA256', 'toolchainVersion'}
            or reader['mode'] != 'xcrun-resolved-lipo'
            or reader['requestedReader'] != '/usr/bin/xcrun'
            or reader['toolchainReader'] != '/usr/bin/xcodebuild'
            or not isinstance(reader['toolchainSHA256'], str)
            or not re.fullmatch(r'[0-9a-f]{64}', reader['toolchainSHA256'])
            or not isinstance(reader['resolvedReader'], str)
            or not reader['resolvedReader'].startswith('/')
            or '\x00' in reader['resolvedReader']
            or len(reader['resolvedReader']) > 4096
            or not isinstance(reader['readerSHA256'], str)
            or not isinstance(reader['toolchainVersion'], str)
            or not reader['toolchainVersion'].strip()
            or '\x00' in reader['toolchainVersion']
            or len(reader['toolchainVersion']) > 4096
            or not re.fullmatch(r'[0-9a-f]{64}', reader['readerSHA256'])):
        raise ValueError('invalid Shaderc reader identity')
    sources = value['sources']
    if type(sources) is not list or len(sources) != len(SHADERC_SOURCE_SPECS):
        raise ValueError('invalid Shaderc source rows')
    seen = set()
    for row in sources:
        if type(row) is not dict or set(row) != {'name', 'bytes', 'sha256'}:
            raise ValueError('invalid Shaderc source row')
        name = row['name']
        if (not isinstance(name, str) or name in seen or name not in source_specs
                or type(row['bytes']) is not int or not isinstance(row['sha256'], str)):
            raise ValueError('unexpected Shaderc source name/bytes')
        if row != dict(name=name, bytes=source_specs[name]['bytes'], sha256=source_specs[name]['sha256']):
            raise ValueError('Shaderc source identity differs from pinned body')
        seen.add(name)
    if seen != set(source_specs):
        raise ValueError('incomplete Shaderc source set')
    expected = {(slice_name, arch, platform_name):
                ('lib/' + platform_name + '/thin/' + arch + '/lib/libshaderc_combined.a',
                 'Libshaderc_combined.xcframework/' + slice_name +
                 '/Libshaderc_combined.framework/Libshaderc_combined')
                for slice_name, arch, platform_name in SHADERC_MATRIX}
    matrix = value['matrix']
    if type(matrix) is not list or len(matrix) != len(expected):
        raise ValueError('invalid Shaderc matrix rows')
    observed = set()
    for row in matrix:
        if type(row) is not dict or set(row) != {'slice', 'arch', 'allZipEntry',
            'allArchiveSHA256', 'xcArchiveSHA256', 'equal'}:
            raise ValueError('invalid Shaderc matrix row')
        if (not isinstance(row['slice'], str) or not isinstance(row['arch'], str)
                or not isinstance(row['allZipEntry'], str)
                or not isinstance(row['allArchiveSHA256'], str)
                or not isinstance(row['xcArchiveSHA256'], str)):
            raise ValueError('invalid Shaderc matrix row types')
        identity = (row['slice'], row['arch'])
        key = next((candidate for candidate in expected if candidate[:2] == identity), None)
        if key is None or key in observed or row['allZipEntry'] != expected[key][0]:
            raise ValueError('unexpected Shaderc matrix path/identity')
        if (not re.fullmatch(r'[0-9a-f]{64}', row['allArchiveSHA256'])
                or not re.fullmatch(r'[0-9a-f]{64}', row['xcArchiveSHA256'])
                or row['equal'] is not True
                or row['allArchiveSHA256'] != row['xcArchiveSHA256']):
            raise ValueError('Shaderc matrix digest mismatch')
        if retained_all_rows is not None:
            retained = retained_all_rows.get(row['allZipEntry'])
            if (not isinstance(retained, dict) or retained.get('kind') != 'file'
                    or retained.get('sha256') != row['allArchiveSHA256']):
                raise ValueError('Shaderc matrix digest differs from retained all-ZIP member')
        if expected[key][1] != ('Libshaderc_combined.xcframework/' + identity[0] +
                                '/Libshaderc_combined.framework/Libshaderc_combined'):
            raise ValueError('Shaderc matrix XCFramework path mapping')
        observed.add(key)
    if observed != set(expected):
        raise ValueError('incomplete Shaderc matrix')
    return value


def _openssl_load(path, label):
    _shaderc_regular(path, label)
    if path.stat().st_size > OPENSSL_MAX_RECEIPT:
        raise ValueError('OpenSSL ' + label + ' exceeds its JSON bound')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate OpenSSL ' + label + ' key')
            result[key] = value
        return result

    with path.open('rb') as stream:
        encoded = stream.read(OPENSSL_MAX_RECEIPT + 1)
    if len(encoded) > OPENSSL_MAX_RECEIPT:
        raise ValueError('OpenSSL ' + label + ' exceeds its JSON bound')
    return json.loads(encoded, object_pairs_hook=unique,
                      parse_constant=lambda _: (_ for _ in ()).throw(
                          ValueError('nonfinite OpenSSL JSON')))


def _openssl_expected(platform, arch, work):
    for candidate_platform, candidate_arch, target, slice_name in OPENSSL_MATRIX:
        if (candidate_platform, candidate_arch) == (platform, arch):
            prefix = work / 'dist' / 'openssl' / platform / 'thin' / arch
            return target, slice_name, prefix
    raise ValueError('unknown OpenSSL platform/architecture')


def _openssl_effective_configuration(value, environment, target):
    """Validate the bounded configdata.pm whitelist emitted after install_sw."""
    if type(value) is not dict or set(value) != set(OPENSSL_EFFECTIVE_KEYS):
        raise ValueError('OpenSSL effective configuration keys differ from the fixed whitelist')
    for key in OPENSSL_EFFECTIVE_KEYS:
        item = value[key]
        if key == 'CROSS_COMPILE':
            if item is not None:
                raise ValueError('OpenSSL CROSS_COMPILE must be JSON null for this profile')
            continue
        if isinstance(item, str):
            if not item or len(item) > 65536 or '\x00' in item:
                raise ValueError('OpenSSL effective configuration string is unbounded')
        elif isinstance(item, list):
            if len(item) > 4096 or any(not isinstance(part, str) or not part
                                       or len(part) > 65536 or '\x00' in part
                                       for part in item):
                raise ValueError('OpenSSL effective configuration list is unbounded')
        else:
            raise ValueError('OpenSSL effective configuration value is not JSON text/list')
    if (value['target'] != target or value['CC'] != environment['CC']
            or value['CXX'] != environment['CXX'] or value['AR'] != environment['AR']
            or value['RANLIB'] != environment['RANLIB']
            or value['ARFLAGS'] != ['qc']):
        raise ValueError('OpenSSL effective compiler/tool configuration differs from the environment')
    if (value['CFLAGS'] != [environment['CFLAGS']]
            or value['CPPFLAGS'] != [environment['CPPFLAGS']]
            or value['CXXFLAGS'] != [environment['CXXFLAGS']]
            or value['LDFLAGS'] != [environment['LDFLAGS']]):
        raise ValueError('OpenSSL effective make flags differ from the fixed environment')
    if value['lib_cppflags'] != '-DL_ENDIAN':
        raise ValueError('OpenSSL effective library CPP flags differ from the target config')
    cflags = value['cflags'] if isinstance(value['cflags'], list) else [value['cflags']]
    if '-fno-common' not in cflags:
        raise ValueError('OpenSSL effective C flags omit the fixed target flag')
    return value


def _openssl_environment(value, platform, arch, sdk_root, source_directory, build_directory):
    if type(value) is not dict or set(value) != set(OPENSSL_ENV_KEYS):
        raise ValueError('OpenSSL environment keys differ from the fixed build map')
    if any(not isinstance(value[key], str) or len(value[key]) > 65536
           or '\x00' in value[key] for key in OPENSSL_ENV_KEYS):
        raise ValueError('OpenSSL environment value is not bounded text')
    if (value['CC'] != '/usr/bin/clang' or value['CXX'] != '/usr/bin/clang++'
            or value['CURRENT_ARCH'] != arch or value['LC_CTYPE'] != 'C'
            or value['PKG_CONFIG_PATH'] != '' or value['PATH'] != OPENSSL_DEFAULT_PATH
            or value['ARFLAGS'] != 'qc' or value['MAKEFLAGS'] != '-j1'
            or value['CMAKE_BUILD_PARALLEL_LEVEL'] != '1'
            or value['CARGO_BUILD_JOBS'] != '1'):
        raise ValueError('OpenSSL environment has an unexpected fixed value')
    for key, suffix in (('AR', 'ar'), ('RANLIB', 'ranlib')):
        path = value[key]
        if (not path.startswith('/') or '..' in Path(path).parts
                or len(path) > 4096 or Path(path).name != suffix
                or not re.fullmatch(r'/.+/Toolchains/[^/]+\.xctoolchain/usr/bin/' + suffix, path)):
            raise ValueError('OpenSSL ' + key + ' is not the selected absolute SDK tool')
    sdk = Path(sdk_root)
    if (not sdk_root.startswith('/') or '\x00' in sdk_root or '..' in sdk.parts):
        raise ValueError('OpenSSL SDK root is not a safe absolute path')
    sdk_suffix = {
        'ios': ('iPhoneOS', 'iPhoneOS.sdk'),
        'isimulator': ('iPhoneSimulator', 'iPhoneSimulator.sdk'),
        'tvos': ('AppleTVOS', 'AppleTVOS.sdk'),
        'tvsimulator': ('AppleTVSimulator', 'AppleTVSimulator.sdk'),
        'macos': ('MacOSX', 'MacOSX.sdk'),
    }[platform]
    sdk_pattern = (r'.*/' + re.escape(sdk_suffix[0]) +
                   r'\.platform/Developer/SDKs/' + re.escape(sdk_suffix[0]) +
                   r'(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3})?\.sdk')
    if not re.fullmatch(sdk_pattern, str(sdk)):
        raise ValueError('OpenSSL SDK root does not match the fixed platform')
    cpu = 'arm64' if arch in ('arm64', 'arm64e') else 'x86_64'
    minimum = {'ios': '15.0', 'isimulator': '15.0', 'tvos': '15.0',
               'tvsimulator': '15.0', 'macos': '12.0'}[platform]
    target = {
        'ios': cpu + '-apple-ios' + minimum,
        'isimulator': cpu + '-apple-ios' + minimum + '-simulator',
        'tvos': cpu + '-apple-tvos' + minimum,
        'tvsimulator': cpu + '-apple-tvos' + minimum + '-simulator',
        'macos': cpu + '-apple-macos' + minimum,
    }[platform]
    minimum_flag = {
        'ios': '-mios-version-min=' + minimum,
        'isimulator': '-mios-simulator-version-min=' + minimum,
        'tvos': '-mtvos-version-min=' + minimum,
        'tvsimulator': '-mtvos-simulator-version-min=' + minimum,
        'macos': '-mmacosx-version-min=' + minimum,
    }[platform]
    common = '-arch ' + arch + ' -isysroot ' + sdk_root + ' -target ' + target + ' ' + minimum_flag
    cflags = common + (' -DHAVE_FORK=0' if platform in ('tvos', 'tvsimulator') else '')
    if not (value['CFLAGS'] == value['CXXFLAGS'] == cflags + ' -O3'
            and value['CPPFLAGS'] == value['ASMFLAGS'] == cflags
            and value['LDFLAGS'] == '-lc++ ' + common):
        raise ValueError('OpenSSL compiler/linker flags differ from the fixed platform policy')
    source = Path(source_directory)
    build = Path(build_directory)
    if (not source_directory.startswith('/') or not build_directory.startswith('/')
            or '\x00' in source_directory or '\x00' in build_directory
            or source.name != OPENSSL_ROOT or source.parent.name != 'dist'
            or build != source.parent / OPENSSL_OUTPUT_ROOT / platform / 'scratch' / arch):
        raise ValueError('OpenSSL source/build directory identity differs from the fixed layout')
    expected_pkg = [str(source.parent / library / platform / 'thin' / arch / 'lib' / 'pkgconfig')
                    for library in OPENSSL_PKG_LIBRARIES]
    expected_pkg.append(str(build / 'vortx-sdk-pkgconfig'))
    if value['PKG_CONFIG_LIBDIR'] != ':'.join(expected_pkg):
        raise ValueError('OpenSSL pkg-config target roots differ from the fixed ordered map')
    return value


def _openssl_target_config(value, platform, arch, target):
    if (not isinstance(value, str) or not value or len(value.encode('utf-8')) > OPENSSL_MAX_TARGET_CONFIG
            or '\x00' in value or not value.endswith('\n')):
        raise ValueError('OpenSSL generated target config is not bounded text')
    expected = ('my %targets = (\n'
                '    "' + target + '" => {\n'
                '        inherit_from => [ "darwin-common" ],\n'
                '        CC => "/usr/bin/clang",\n'
                '        cflags => "-fno-common",\n'
                '        lib_cppflags => "-DL_ENDIAN",\n'
                '        bn_ops => "SIXTY_FOUR_BIT_LONG",\n'
                '        asm_arch => "' + ('aarch64' if arch in ('arm64', 'arm64e') else 'x86_64') + '",\n'
                '        perlasm_scheme => "' + ('ios64' if arch in ('arm64', 'arm64e') else 'macosx') + '",\n'
                '        sys_id => "' + ('MACOSX' if platform == 'macos' else 'iOS') + '",\n'
                '    },\n'
                ');\n\n')
    if value != expected:
        raise ValueError('OpenSSL generated target config differs from the fixed template')
    return value


def _openssl_validate_scratch(value, work, platform, arch, scratch):
    target, slice_name, prefix = _openssl_expected(platform, arch, work)
    expected_keys = {'schema', 'version', 'sourceSHA', 'sourceTag', 'platform', 'arch',
                     'configureTarget', 'configureArguments', 'sdkRoot', 'sourceDirectory',
                     'buildDirectory', 'prefix', 'environment', 'targetConfig',
                     'targetConfigSHA256', 'effectiveConfiguration', 'compiled', 'installed'}
    if type(value) is not dict or set(value) != expected_keys:
        raise ValueError('unexpected OpenSSL scratch record keys')
    if (type(value['schema']) is not int or value['schema'] != 1
            or value['version'] != OPENSSL_SOURCE['version']
            or value['sourceSHA'] != OPENSSL_SOURCE['commit']
            or value['sourceTag'] != OPENSSL_SOURCE['tag']
            or value['platform'] != platform or value['arch'] != arch
            or value['configureTarget'] != target or value['compiled'] is not True
            or value['installed'] is not True):
        raise ValueError('OpenSSL scratch source/platform/target/completion differs from the fixed profile')
    for field in ('sdkRoot', 'sourceDirectory', 'buildDirectory', 'prefix'):
        if (not isinstance(value[field], str) or not value[field].startswith('/')
                or '\x00' in value[field] or len(value[field]) > 4096):
            raise ValueError('OpenSSL scratch ' + field + ' is not a bounded absolute path')
    if Path(value['buildDirectory']).resolve() != scratch.resolve():
        raise ValueError('OpenSSL scratch build directory differs from its exact scratch path')
    if Path(value['sourceDirectory']).resolve() != (work / 'dist' / OPENSSL_ROOT).resolve():
        raise ValueError('OpenSSL scratch source directory differs from the pinned source root')
    if Path(value['prefix']).resolve() != prefix.resolve():
        raise ValueError('OpenSSL scratch prefix differs from the expected thin root')
    expected_args = ['--config=' + str(scratch / 'vortx-openssl-target.conf'),
                     '--prefix=' + value['prefix'], *OPENSSL_CONFIGURE_FLAGS, target]
    if value['configureArguments'] != expected_args:
        raise ValueError('OpenSSL Configure arguments differ from the fixed profile')
    _openssl_environment(value['environment'], platform, arch, value['sdkRoot'],
                          value['sourceDirectory'], value['buildDirectory'])
    _openssl_target_config(value['targetConfig'], platform, arch, target)
    if value['targetConfigSHA256'] != hashlib.sha256(value['targetConfig'].encode('utf-8')).hexdigest():
        raise ValueError('OpenSSL generated target config digest differs')
    _openssl_effective_configuration(value['effectiveConfiguration'], value['environment'], target)
    return target, slice_name, prefix


def _openssl_hash_outputs(prefix):
    result = []
    for name, relative, limit in OPENSSL_OUTPUTS:
        path = prefix / relative
        count, checksum = _shaderc_file_sha(path, 'installed ' + name)
        if count > limit:
            raise ValueError('OpenSSL installed output exceeds its bound: ' + name)
        result.append({'name': name, 'path': relative, 'bytes': count, 'sha256': checksum})
    return result


def _openssl_receipt_shape(value, validator_sha256):
    expected_keys = {'schema', 'profile', 'sourceProfile', 'version', 'sourceSHA', 'sourceTag',
                     'validatorSHA256', 'matrix', 'runtimeSecurityTested', 'promotionEligible'}
    if type(value) is not dict or set(value) != expected_keys:
        raise ValueError('unexpected OpenSSL receipt keys')
    if (type(value['schema']) is not int or value['schema'] != 1
            or value['profile'] != NATIVE_UPGRADES_PROFILE
            or value['sourceProfile'] != OPENSSL_BOM_PROFILE
            or value['version'] != OPENSSL_SOURCE['version']
            or value['sourceSHA'] != OPENSSL_SOURCE['commit']
            or value['sourceTag'] != OPENSSL_SOURCE['tag']
            or value['validatorSHA256'] != validator_sha256
            or value['runtimeSecurityTested'] is not False
            or value['promotionEligible'] is not False):
        raise ValueError('invalid OpenSSL receipt identity/boundary')
    matrix = value['matrix']
    if type(matrix) is not list or len(matrix) != len(OPENSSL_MATRIX):
        raise ValueError('OpenSSL receipt must contain exactly nine matrix rows')
    expected = {(platform, arch): (target, slice_name)
                for platform, arch, target, slice_name in OPENSSL_MATRIX}
    observed = set()
    header_hashes = set()
    for row in matrix:
        row_keys = {'platform', 'arch', 'slice', 'configureTarget', 'configureArguments',
                    'sdkRoot', 'sourceDirectory', 'buildDirectory', 'prefix', 'environment',
                    'targetConfig', 'targetConfigSHA256', 'effectiveConfiguration',
                    'compiled', 'installed', 'outputs'}
        if type(row) is not dict or set(row) != row_keys:
            raise ValueError('unexpected OpenSSL receipt row keys')
        identity = (row['platform'], row['arch'])
        if (identity not in expected or identity in observed
                or row['slice'] != expected[identity][1]
                or row['configureTarget'] != expected[identity][0]
                or row['compiled'] is not True or row['installed'] is not True):
            raise ValueError('unexpected OpenSSL receipt platform/architecture/completion')
        for field in ('sdkRoot', 'sourceDirectory', 'buildDirectory', 'prefix'):
            if (not isinstance(row[field], str) or not row[field].startswith('/')
                    or '\x00' in row[field] or len(row[field]) > 4096):
                raise ValueError('OpenSSL receipt ' + field + ' is not a bounded absolute path')
        source_root = Path(row['sourceDirectory'])
        if (source_root.name != OPENSSL_ROOT or source_root.parent.name != 'dist'
                or Path(row['buildDirectory']) != source_root.parent / OPENSSL_OUTPUT_ROOT / row['platform'] /
                'scratch' / row['arch']
                or Path(row['prefix']) != source_root.parent / OPENSSL_OUTPUT_ROOT / row['platform'] /
                'thin' / row['arch']):
            raise ValueError('OpenSSL receipt source/build/prefix layout differs from the fixed tree')
        if not row['prefix'].endswith('/dist/' + OPENSSL_OUTPUT_ROOT + '/' + row['platform'] + '/thin/' + row['arch']):
            raise ValueError('OpenSSL receipt prefix differs from the fixed thin layout')
        expected_args = ['--config=' + row['buildDirectory'] + '/vortx-openssl-target.conf',
                         '--prefix=' + row['prefix'], *OPENSSL_CONFIGURE_FLAGS,
                         expected[identity][0]]
        if row['configureArguments'] != expected_args:
            raise ValueError('OpenSSL receipt Configure arguments differ from the fixed profile')
        _openssl_environment(row['environment'], row['platform'], row['arch'], row['sdkRoot'],
                             row['sourceDirectory'], row['buildDirectory'])
        _openssl_target_config(row['targetConfig'], row['platform'], row['arch'],
                               expected[identity][0])
        if row['targetConfigSHA256'] != hashlib.sha256(row['targetConfig'].encode('utf-8')).hexdigest():
            raise ValueError('OpenSSL receipt target config digest differs')
        _openssl_effective_configuration(row['effectiveConfiguration'], row['environment'],
                                          expected[identity][0])
        outputs = row['outputs']
        if type(outputs) is not list or len(outputs) != len(OPENSSL_OUTPUTS):
            raise ValueError('OpenSSL receipt output count differs from the fixed set')
        seen_outputs = set()
        for output in outputs:
            if type(output) is not dict or set(output) != {'name', 'path', 'bytes', 'sha256'}:
                raise ValueError('unexpected OpenSSL receipt output keys')
            name = output['name']
            fixed = next((candidate for candidate in OPENSSL_OUTPUTS if candidate[0] == name), None)
            if fixed is None or name in seen_outputs or output['path'] != fixed[1]:
                raise ValueError('unexpected OpenSSL receipt output path/name')
            if (type(output['bytes']) is not int or output['bytes'] < 1
                    or output['bytes'] > fixed[2]
                    or not re.fullmatch(r'[0-9a-f]{64}', output['sha256'])):
                raise ValueError('invalid OpenSSL receipt output bytes/hash')
            if name == 'opensslv.h':
                header_hashes.add(output['sha256'])
            seen_outputs.add(name)
        if seen_outputs != {name for name, _, _ in OPENSSL_OUTPUTS}:
            raise ValueError('incomplete OpenSSL receipt output set')
        observed.add(identity)
    if observed != set(expected) or len(header_hashes) != 1:
        raise ValueError('incomplete OpenSSL receipt matrix')
    return value


def _openssl_receipt_rows_from_scratch(value, outputs):
    return {'platform': value['platform'], 'arch': value['arch'],
            'slice': next(slice_name for platform, arch, _, slice_name in OPENSSL_MATRIX
                          if (platform, arch) == (value['platform'], value['arch'])),
            'configureTarget': value['configureTarget'],
            'configureArguments': value['configureArguments'], 'sdkRoot': value['sdkRoot'],
            'sourceDirectory': value['sourceDirectory'], 'buildDirectory': value['buildDirectory'],
            'prefix': value['prefix'], 'environment': value['environment'],
            'targetConfig': value['targetConfig'],
            'targetConfigSHA256': value['targetConfigSHA256'],
            'effectiveConfiguration': value['effectiveConfiguration'],
            'compiled': value['compiled'], 'installed': value['installed'], 'outputs': outputs}


def apple_openssl_builds(work):
    """Hash successful OpenSSL thin installs and emit the fixed source BOM."""
    work = work.absolute()
    if work.is_symlink() or not work.is_dir() or work.resolve() != work:
        raise ValueError('OpenSSL WORK must be a real absolute directory')
    matrix = []
    for platform, arch, _, _ in OPENSSL_MATRIX:
        scratch = work / 'dist' / 'openssl' / platform / 'scratch' / arch / 'vortx-openssl-build.json'
        record = _openssl_load(scratch, 'scratch record')
        _openssl_validate_scratch(record, work, platform, arch, scratch.parent)
        matrix.append(_openssl_receipt_rows_from_scratch(record,
                                                         _openssl_hash_outputs(
                                                             _openssl_expected(platform, arch, work)[2])))
    destination_root = work / 'dist' / OPENSSL_OUTPUT_ROOT
    if (destination_root.is_symlink() or not destination_root.is_dir()
            or destination_root.resolve() != destination_root):
        raise ValueError('OpenSSL destination root is not a real build directory')
    destination = destination_root / 'vortx-openssl-builds.json'
    if destination.exists() or destination.is_symlink():
        raise ValueError('refusing an existing OpenSSL BOM receipt')
    save(destination, {'schema': 1, 'profile': NATIVE_UPGRADES_PROFILE,
                       'sourceProfile': OPENSSL_BOM_PROFILE, 'version': OPENSSL_SOURCE['version'],
                       'sourceSHA': OPENSSL_SOURCE['commit'], 'sourceTag': OPENSSL_SOURCE['tag'],
                       'validatorSHA256': digest(Path(__file__)), 'matrix': matrix,
                       'runtimeSecurityTested': False, 'promotionEligible': False})
    print('PASS openssl-profile ' + OPENSSL_BOM_PROFILE)
    print('PASS apple-openssl-builds')


def apple_openssl_receipt(receipt_path):
    """Revalidate one OpenSSL BOM file and its installed outputs without native tools."""
    receipt_path = receipt_path.absolute()
    if (not receipt_path.is_absolute() or receipt_path.is_symlink()
            or receipt_path.resolve() != receipt_path
            or receipt_path.name != 'vortx-openssl-builds.json'
            or receipt_path.parent.name != OPENSSL_OUTPUT_ROOT
            or receipt_path.parent.parent.name != 'dist'):
        raise ValueError('OpenSSL receipt must be the exact generated BOM file')
    work = receipt_path.parent.parent.parent
    if work.is_symlink() or not work.is_dir() or work.resolve() != work:
        raise ValueError('OpenSSL receipt work root is not a real absolute directory')
    value = _openssl_load(receipt_path, 'receipt')
    _openssl_receipt_shape(value, digest(Path(__file__)))
    expected_rows = {(row['platform'], row['arch']): row for row in value['matrix']}
    for platform, arch, _, _ in OPENSSL_MATRIX:
        row = expected_rows[(platform, arch)]
        actual = _openssl_hash_outputs(work / 'dist' / 'openssl' / platform / 'thin' / arch)
        if actual != row['outputs']:
            raise ValueError('OpenSSL installed files differ from the BOM: ' + platform + '/' + arch)
    print('PASS openssl-profile ' + OPENSSL_BOM_PROFILE)
    print('PASS apple-openssl-receipt')


def _subtitle_json(body, label):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('duplicate subtitle ' + label + ' key')
            value[key] = item
        return value

    try:
        return json.loads(body.decode('utf-8', errors='strict'), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              ValueError('non-finite subtitle ' + label + ' number')))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError('invalid subtitle ' + label + ' JSON') from error


def _subtitle_inline(row, label, budget, maximum=SUBTITLE_MAX_BODY):
    if type(row) is not dict or set(row) != {'bytes', 'sha256', 'bodyBase64'}:
        raise ValueError('unexpected subtitle inline-body keys: ' + label)
    count = row['bytes']
    encoded = row['bodyBase64']
    if (type(count) is not int or not 0 < count <= maximum
            or not isinstance(encoded, str)
            or len(encoded) > ((maximum + 2) // 3) * 4
            or not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])):
        raise ValueError('invalid subtitle inline-body bound: ' + label)
    try:
        body = base64.b64decode(encoded.encode('ascii'), validate=True)
    except (UnicodeEncodeError, binascii.Error) as error:
        raise ValueError('subtitle inline body is not canonical base64: ' + label) from error
    if (base64.b64encode(body).decode('ascii') != encoded or len(body) != count
            or hashlib.sha256(body).hexdigest() != row['sha256']):
        raise ValueError('subtitle inline raw-body identity differs: ' + label)
    budget[0] += len(body)
    if budget[0] > SUBTITLE_MAX_INLINE_RAW:
        raise ValueError('subtitle aggregate raw-body cap exceeded')
    return body


def _subtitle_text(body, label):
    try:
        text = body.decode('utf-8', errors='strict')
    except UnicodeDecodeError as error:
        raise ValueError('subtitle ' + label + ' is not UTF-8') from error
    if not text or '\x00' in text:
        raise ValueError('subtitle ' + label + ' is empty or contains NUL')
    return text


def _subtitle_platform(platform, arch):
    sdk_stem = {'ios': 'iPhoneOS', 'isimulator': 'iPhoneSimulator',
                'tvos': 'AppleTVOS', 'tvsimulator': 'AppleTVSimulator',
                'macos': 'MacOSX'}[platform]
    cpu = 'x86_64' if arch == 'x86_64' else 'arm64'
    target = {'ios': cpu + '-apple-ios15.0',
              'isimulator': cpu + '-apple-ios15.0-simulator',
              'tvos': cpu + '-apple-tvos15.0',
              'tvsimulator': cpu + '-apple-tvos15.0-simulator',
              'macos': cpu + '-apple-macos12.0'}[platform]
    minimum = {'ios': '-mios-version-min=15.0',
               'isimulator': '-mios-simulator-version-min=15.0',
               'tvos': '-mtvos-version-min=15.0',
               'tvsimulator': '-mtvos-simulator-version-min=15.0',
               'macos': '-mmacosx-version-min=12.0'}[platform]
    host_cpu = 'aarch64' if cpu == 'arm64' else cpu
    host = host_cpu + '-' + ({'ios': 'ios', 'isimulator': 'ios',
                         'tvos': 'tvos', 'tvsimulator': 'tvos',
                         'macos': 'apple'}[platform]) + '-darwin'
    subsystem = {'ios': 'ios', 'isimulator': 'ios-simulator',
                 'tvos': 'tvos', 'tvsimulator': 'tvos-simulator',
                 'macos': 'macos'}[platform]
    cpu_family = 'x86_64' if arch == 'x86_64' else 'aarch64'
    return sdk_stem, target, minimum, host, subsystem, cpu_family, cpu


def _subtitle_environment(value, work, library, platform, arch):
    keys = {'LC_CTYPE', 'MAKEFLAGS', 'CMAKE_BUILD_PARALLEL_LEVEL', 'CARGO_BUILD_JOBS',
            'CC', 'CXX', 'CURRENT_ARCH', 'CFLAGS', 'CPPFLAGS', 'CXXFLAGS',
            'ASMFLAGS', 'LDFLAGS', 'PKG_CONFIG_LIBDIR', 'PKG_CONFIG_PATH', 'PATH'}
    if type(value) is not dict or set(value) != keys:
        raise ValueError('subtitle environment keys differ from BaseBuild')
    if any(not isinstance(item, str) or len(item) > 65536 or '\x00' in item
           for item in value.values()):
        raise ValueError('subtitle environment contains unbounded text')
    sdk_stem, target, minimum, _, _, _, _ = _subtitle_platform(platform, arch)
    prefix = '-arch ' + arch + ' -isysroot '
    suffix = ' -target ' + target + ' ' + minimum
    fork = ' -DHAVE_FORK=0' if platform in ('tvos', 'tvsimulator') else ''
    freetype = work / 'dist/libfreetype' / platform / 'thin' / arch
    dependency_cflags = ' -I' + str(freetype / 'include') if library == 'libbluray' else ''
    cflags = value['CFLAGS']
    optimization = ' -O2' if library == 'libunibreak' else ''
    if optimization:
        if (not cflags.endswith(optimization)
                or [item for item in cflags.split() if item.startswith('-O')] != ['-O2']):
            raise ValueError('Unibreak shipping CFLAGS require exactly one trailing -O2')
        cflags = cflags[:-len(optimization)]
    if not cflags.startswith(prefix) or not cflags.endswith(suffix + fork + dependency_cflags):
        raise ValueError('subtitle C flags differ from the fixed platform policy')
    sdk_root = cflags[len(prefix):len(cflags) - len(suffix + fork + dependency_cflags)]
    sdk_pattern = (r'.*/' + re.escape(sdk_stem) +
                   r'\.platform/Developer/SDKs/' + re.escape(sdk_stem) +
                   r'(?:[0-9]{1,3}(?:\.[0-9]{1,3}){0,3})?\.sdk')
    if (not sdk_root.startswith('/') or '\x00' in sdk_root or '..' in Path(sdk_root).parts
            or re.fullmatch(sdk_pattern, sdk_root) is None):
        raise ValueError('subtitle SDK root differs from the fixed platform')
    build = work / 'dist' / library / platform / 'scratch' / arch
    pkg_roots = [str(work / 'dist' / name / platform / 'thin' / arch / 'lib/pkgconfig')
                 for name in OPENSSL_PKG_LIBRARIES]
    pkg_roots.append(str(build / 'vortx-sdk-pkgconfig'))
    expected = {
        'LC_CTYPE': 'C', 'MAKEFLAGS': '-j1', 'CMAKE_BUILD_PARALLEL_LEVEL': '1',
        'CARGO_BUILD_JOBS': '1', 'CC': '/usr/bin/clang', 'CXX': '/usr/bin/clang++',
        'CURRENT_ARCH': arch, 'CFLAGS': cflags + optimization, 'CPPFLAGS': cflags,
        'CXXFLAGS': cflags + optimization, 'ASMFLAGS': cflags,
        'LDFLAGS': '-lc++ ' + prefix + sdk_root + suffix +
                   ((' -L' + str(freetype / 'lib') + ' -lfreetype') if library == 'libbluray' else ''),
        'PKG_CONFIG_LIBDIR': ':'.join(pkg_roots), 'PKG_CONFIG_PATH': '',
        'PATH': OPENSSL_DEFAULT_PATH,
    }
    if not _crypto_exact_equal(value, expected):
        raise ValueError('subtitle environment differs from exact BaseBuild output')
    return sdk_root, cflags, expected['LDFLAGS']


def _subtitle_meson_cross(body, work, library, platform, arch, sdk_root, cflags, ldflags):
    text = _subtitle_text(body, 'Meson cross file')
    tools = {}
    for name in ('ar', 'strip'):
        matches = re.findall(r"^" + name + r" = '([^']+)'$", text, re.M)
        if (len(matches) != 1 or not matches[0].startswith('/')
                or '..' in Path(matches[0]).parts or '\x00' in matches[0]
                or Path(matches[0]).name != name or len(matches[0]) > 4096):
            raise ValueError('subtitle Meson cross file has an unsafe SDK tool: ' + name)
        tools[name] = matches[0]
    _, _, _, _, subsystem, cpu_family, cpu = _subtitle_platform(platform, arch)
    quoted_c = ', '.join("'" + item + "'" for item in cflags.split(' '))
    quoted_ld = ', '.join("'" + item + "'" for item in ldflags.split(' '))
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
        ar=tools['ar'], strip=tools['strip'], subsystem=subsystem,
        cpu_family=cpu_family, cpu=cpu,
        prefix=work / 'dist' / library / platform / 'thin' / arch,
        cflags=quoted_c, ldflags=quoted_ld)
    if text.rstrip('\n') != expected:
        raise ValueError('subtitle Meson cross file differs from BaseBuild')


def _subtitle_build_options(body, library, prefix):
    value = _subtitle_json(body, 'Meson build options')
    if type(value) is not list or len(value) > 10000:
        raise ValueError('subtitle Meson build-option set is unbounded')
    options = {}
    for row in value:
        if type(row) is not dict or not isinstance(row.get('name'), str) or row['name'] in options:
            raise ValueError('subtitle Meson build-option row is malformed or duplicate')
        options[row['name']] = row.get('value')
    expected = {'default_library': 'static', 'buildtype': 'release',
                'prefix': str(prefix)}
    for argument in SUBTITLE_ARGUMENTS[library]:
        if argument == '--wrap-mode=nodownload':
            expected['wrap_mode'] = 'nodownload'
            continue
        if argument == '--force-fallback-for=libudfread':
            expected['force_fallback_for'] = ['libudfread']
            continue
        name, raw = argument.removeprefix('-D').split('=', 1)
        expected[name] = {'true': True, 'false': False}.get(raw, raw)
    if any(name not in options or not _crypto_exact_equal(options[name], item)
           for name, item in expected.items()):
        raise ValueError('subtitle Meson effective options differ from the fixed arguments')


def _subtitle_dependencies(body):
    value = _subtitle_json(body, 'Meson dependencies')
    if type(value) is not list or len(value) > 10000:
        raise ValueError('subtitle Meson dependency set is unbounded')
    text = json.dumps(value, sort_keys=True, separators=(',', ':')).casefold()
    forbidden = ('freetype', 'glib', 'gobject', 'cairo', 'chafa', 'libpng',
                 'zlib', 'icu', 'graphite2', 'fontations', 'harfrust', 'kbts',
                 'wrapdb')
    if any(re.search(r'(?<![a-z0-9_])' + re.escape(name) +
                     r'(?![a-z0-9_])', text) for name in forbidden):
        raise ValueError('subtitle Meson dependency closure admits a disabled integration')


def _subtitle_compile_command(row, arch, sdk_root, target, minimum, fribidi_role=None):
    if (type(row) is not dict or not 0 < len(row) <= 16
            or any(not isinstance(name, str) or not name or len(name) > 128
                   for name in row)):
        raise ValueError('subtitle compile-command row is malformed or unbounded')
    payloads = []
    if 'arguments' in row:
        arguments = row['arguments']
        if (type(arguments) is not list or not 0 < len(arguments) <= 16384
                or any(not isinstance(item, str) or not item or len(item) > 65536
                       or '\x00' in item for item in arguments)
                or sum(len(item) for item in arguments) > 1024 * 1024):
            raise ValueError('subtitle compile-command arguments are malformed or unbounded')
        payloads.append(arguments)
    if 'command' in row:
        command = row['command']
        if (not isinstance(command, str) or not command or len(command) > 1024 * 1024
                or '\x00' in command):
            raise ValueError('subtitle compile-command text is malformed or unbounded')
        try:
            arguments = shlex.split(command, posix=True)
        except ValueError as error:
            raise ValueError('subtitle compile-command text is not shell-tokenizable') from error
        if (not arguments or len(arguments) > 16384
                or any(not item or len(item) > 65536 for item in arguments)):
            raise ValueError('subtitle compile-command token set is malformed or unbounded')
        payloads.append(arguments)
    if not payloads:
        raise ValueError('subtitle compile-command row has no command payload')
    for arguments in payloads:
        alternate = [item for item in arguments
                     if ((item.startswith('-target') and item != '-target')
                         or item.startswith('--target')
                         or (item.startswith('-isysroot') and item != '-isysroot')
                         or item.startswith('--sysroot'))]
        if alternate:
            raise ValueError('subtitle compile-command has an alternate target/sysroot spelling')

        def values(flag):
            indexes = [index for index, item in enumerate(arguments) if item == flag]
            if any(index + 1 >= len(arguments) for index in indexes):
                raise ValueError('subtitle compile-command has an unterminated target flag')
            return [arguments[index + 1] for index in indexes]

        deployment = [item for item in arguments if re.fullmatch(
            r'-(?:mios|mios-simulator|mtvos|mtvos-simulator|mmacosx)-version-min=.*',
            item)]
        arch_values = values('-arch')
        sdk_values = values('-isysroot')
        target_values = values('-target')
        if fribidi_role is not None:
            source, output, native = fribidi_role
            if (arguments[0] != ('cc' if native else '/usr/bin/clang')
                    or any((item.startswith('-o') and item != '-o')
                           or item.startswith('--output') or item.startswith('@')
                           or item == '--' for item in arguments)
                    or values('-c') != [source] or values('-o') != [output]
                    or [item for item in arguments
                        if not item.startswith('-') and item.endswith('.c')] != [source]):
                raise ValueError('FriBidi compile-command compiler/source/output differs from its fixed role')
            if native:
                toggles = [item for item in arguments if re.match(
                    r'^-[DU](?:HAVE_CONFIG_H|HAVE_STRINGIZE|DONT_HAVE_FRIBIDI_CONFIG_H)(?:=|$)', item)]
                if (arch_values or sdk_values or target_values or deployment
                        or any(item.startswith('-arch') for item in arguments)
                        or any(item in ('-D', '-U') or
                               (item.startswith(('-D', '-U')) and any(char.isspace() for char in item))
                               for item in arguments)
                        or any(item.startswith(('-Wp,', '-Xpreprocessor', '-Xclang'))
                               for item in arguments)
                        or sorted(toggles) != sorted([
                            '-UHAVE_CONFIG_H', '-DHAVE_STRINGIZE', '-DDONT_HAVE_FRIBIDI_CONFIG_H'])):
                    raise ValueError('FriBidi native generator has shipping target flags or changed configuration defines')
                continue
        if (not arch_values or any(item != arch for item in arch_values)
                or not sdk_values or any(item != sdk_root for item in sdk_values)
                or not target_values or any(item != target for item in target_values)
                or not deployment or any(item != minimum for item in deployment)):
            raise ValueError('subtitle compile-command target flags differ from fixed policy')


def _subtitle_fribidi_compile_commands(commands, build, arch, sdk_root, target, minimum):
    # Exact pinned 1.0.17 gen.tab/meson.build: one native executable plus
    # six native table generators, each with its own source and packtab.c.
    root = '../../../../libfribidi-1.0.17-6949dcde/'
    expected = {}
    for program in ('gen-unicode-version', 'gen-bidi-type-tab',
                    'gen-joining-type-tab', 'gen-arabic-shaping-tab',
                    'gen-mirroring-tab', 'gen-brackets-tab', 'gen-brackets-type-tab'):
        names = (program + '.c',) if program == 'gen-unicode-version' else (program + '.c', 'packtab.c')
        for name in names:
            expected[(root + 'gen.tab/' + name, 'gen.tab/' + program + '.p/' + name + '.o')] = True
    # Pinned lib/meson.build's complete shipping source list (deprecated=true).
    for name in ('fribidi.c', 'fribidi-arabic.c', 'fribidi-bidi.c', 'fribidi-bidi-types.c',
                 'fribidi-char-sets.c', 'fribidi-char-sets-cap-rtl.c',
                 'fribidi-char-sets-cp1255.c', 'fribidi-char-sets-cp1256.c',
                 'fribidi-char-sets-iso8859-6.c', 'fribidi-char-sets-iso8859-8.c',
                 'fribidi-char-sets-utf8.c', 'fribidi-deprecated.c', 'fribidi-joining.c',
                 'fribidi-joining-types.c', 'fribidi-mirroring.c', 'fribidi-brackets.c',
                 'fribidi-run.c', 'fribidi-shape.c'):
        expected[(root + 'lib/' + name, 'lib/libfribidi.a.p/' + name + '.o')] = False
    if type(commands) is not list or len(commands) != len(expected):
        raise ValueError('FriBidi compile-command set must contain exactly 13 native and 18 shipping rows')
    seen = set()
    for row in commands:
        if (type(row) is not dict
                or not {'directory', 'file', 'output'} <= set(row)
                or set(row) - {'directory', 'file', 'output', 'command', 'arguments'}
                or not isinstance(row['file'], str) or not isinstance(row['output'], str)
                or row['directory'] != str(build)):
            raise ValueError('FriBidi compile-command metadata differs from the fixed build directory/schema')
        identity = (row['file'], row['output'])
        if identity not in expected or identity in seen:
            raise ValueError('FriBidi compile-command has an unknown or duplicate source/output role')
        seen.add(identity)
        _subtitle_compile_command(row, arch, sdk_root, target, minimum,
                                  (identity[0], identity[1], expected[identity]))
    if seen != set(expected):
        raise ValueError('FriBidi compile-command native/shipping closure is incomplete')


def _subtitle_pc(body, library, prefix):
    text = _subtitle_text(body, 'pkg-config metadata')
    variables, fields = {}, {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line and (':' not in line or line.index('=') < line.index(':')):
            name, item = line.split('=', 1)
            target = variables
        elif ':' in line:
            name, item = line.split(':', 1)
            target = fields
        else:
            raise ValueError('subtitle pkg-config metadata has an unclassified line')
        name, item = name.strip(), item.strip()
        if not name or name in target or '\x00' in item:
            raise ValueError('subtitle pkg-config metadata has a duplicate field')
        target[name] = item
    if variables.get('prefix') != str(prefix) or fields.get('Version') != SUBTITLE_VERSIONS[library]:
        raise ValueError('subtitle pkg-config prefix/version differs from the fixed build')
    if library == 'libbluray':
        # Meson exposes static-library dependencies publicly; require that exact closure.
        if (re.fullmatch(r'freetype2,[ \t]+libxml-2\.0[ \t]+>=[ \t]+2\.6', fields.get('Requires', '')) is None
                or 'Requires.private' in fields
                or any(item in text for item in ('-ludfread', '/opt/homebrew', '/usr/local/', 'fontconfig'))):
            raise ValueError('libbluray pkg-config dependency closure differs')
    elif fields.get('Requires', '') or fields.get('Requires.private', ''):
        raise ValueError('subtitle pkg-config unexpectedly admits another package dependency')
    expected_lib = {'libunibreak': '-lunibreak', 'libfribidi': '-lfribidi',
                    'libharfbuzz': '-lharfbuzz', 'libbluray': '-lbluray'}[library]
    if expected_lib not in fields.get('Libs', '').split():
        raise ValueError('subtitle pkg-config library identity differs')


def _subtitle_header(body, library):
    text = _subtitle_text(body, 'installed version header')
    if library == 'libbluray':
        for name, value in (('BLURAY_VERSION_MAJOR', '1'), ('BLURAY_VERSION_MINOR', '5'), ('BLURAY_VERSION_MICRO', '0')):
            _bluray_define(text, name, value)
        _bluray_define(text, 'BLURAY_VERSION_STRING', '1.5.0', True)
    elif library == 'libunibreak':
        definitions = re.findall(r'^[ \t]*#[ \t]*define[ \t]+UNIBREAK_VERSION\b[^\r\n]*$',
                                 text, re.M)
        versions = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+UNIBREAK_VERSION[ \t]+'
            r'(0[xX][0-9a-fA-F]+)[ \t]*'
            r'(?:/\*(?:[^*\r\n]|\*(?!/)){0,4096}\*/[ \t]*)?$', text, re.M)
        symbols = re.findall(r'^\s*extern\s+const\s+int\s+unibreak_version\s*;\s*$',
                             text, re.M)
        if len(definitions) != 1 or versions != ['0x0800'] or len(symbols) != 1:
            raise ValueError('Unibreak installed header version differs')
    elif library == 'libfribidi':
        expected = {'FRIBIDI_MAJOR_VERSION': '1', 'FRIBIDI_MINOR_VERSION': '0',
                    'FRIBIDI_MICRO_VERSION': '17'}
        for name, version in expected.items():
            definitions = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' +
                                     re.escape(name) + r'\b[^\r\n]*$', text, re.M)
            values = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + re.escape(name) +
                                r'[ \t]+([0-9]+)[ \t]*$',
                                text, re.M)
            if len(definitions) != 1 or values != [version]:
                raise ValueError('FriBidi installed header version differs: ' + name)
        definitions = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+FRIBIDI_VERSION\b[^\r\n]*$', text, re.M)
        versions = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+FRIBIDI_VERSION[ \t]+"([^"]+)"[ \t]*$',
                              text, re.M)
        if len(definitions) != 1 or versions != ['1.0.17']:
            raise ValueError('FriBidi installed header version differs')
    elif library == 'libharfbuzz':
        expected = {'HB_VERSION_MAJOR': '15', 'HB_VERSION_MINOR': '0',
                    'HB_VERSION_MICRO': '0'}
        for name, version in expected.items():
            definitions = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' +
                                     re.escape(name) + r'\b[^\r\n]*$', text, re.M)
            values = re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + re.escape(name) +
                                r'[ \t]+([0-9]+)[ \t]*$', text, re.M)
            if len(definitions) != 1 or values != [version]:
                raise ValueError('HarfBuzz installed header version differs: ' + name)
        definitions = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+HB_VERSION_STRING\b[^\r\n]*$', text, re.M)
        strings = re.findall(
            r'^[ \t]*#[ \t]*define[ \t]+HB_VERSION_STRING[ \t]+"([^"]+)"[ \t]*$',
                             text, re.M)
        if len(definitions) != 1 or strings != ['15.0.0']:
            raise ValueError('HarfBuzz installed header string version differs')


def _bluray_fields(body, label):
    variables, fields = {}, {}
    for raw in _subtitle_text(body, label).splitlines():
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
    config = _subtitle_text(generated['config.h'], 'libbluray config.h')
    _bluray_define(config, 'VERSION', '1.5.0', True)
    for name in ('HAVE_FT2', 'HAVE_LIBXML2', 'HAVE_LIBUDFREAD'):
        _bluray_define(config, name, '1')
    if re.search(r'^[ \t]*#[ \t]*define[ \t]+HAVE_FONTCONFIG\b', config, re.M):
        raise ValueError('libbluray configured fontconfig despite disabled policy')
    _bluray_define(_subtitle_text(generated['contrib/libudfread/config.h'], 'embedded UDF config'), 'HAVE_UDFREAD_VERSION_H', '1')


def _bluray_dependencies(body, work, platform, arch, sdk_root):
    value = _crypto_strict_json(body)
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
            decoded[name] = _subtitle_inline({key: row[key] for key in ('bytes', 'sha256', 'bodyBase64')}, 'bluray dependency ' + name, budget, 4 * 1024 * 1024)
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
    ft_header = _subtitle_text(decoded['freetypeHeader'], 'retained Freetype header')
    for macro in ('FREETYPE_MAJOR', 'FREETYPE_MINOR', 'FREETYPE_PATCH'):
        if len(re.findall(r'^[ \t]*#[ \t]*define[ \t]+' + macro + r'[ \t]+[0-9]{1,3}[ \t]*$', ft_header, re.M)) != 1:
            raise ValueError('libbluray retained Freetype header lacks bounded version macros')
    for kind, macro, include, install_name in (
            ('Libxml', 'LIBXML_DOTTED_VERSION', 'usr/include/libxml2', '/usr/lib/libxml2.2.dylib'),
            ('Zlib', 'ZLIB_VERSION', '', '/usr/lib/libz.1.dylib')):
        header = _subtitle_text(decoded['sdk' + kind + 'Header'], 'SDK version header')
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
        stub = _subtitle_text(decoded['sdk' + kind + 'Stub'], 'SDK linker stub')
        escaped = re.escape(install_name)
        if not stub.startswith('--- !tapi-tbd') or len(re.findall(r'(?m)^install-name:[ \t]+(?:' + escaped + r"|'"+ escaped + r"'|\"" + escaped + r'\")[ \t]*$', stub)) != 1:
            raise ValueError('libbluray selected SDK linker identity differs')
    return decoded



def _subtitle_record_contract(record, work, library, platform, arch, budget):
    expected_keys = {'schema', 'library', 'selector', 'route', 'platform', 'arch',
                     'sourceDirectory', 'buildDirectory', 'prefix', 'arguments',
                     'environment', 'makeArguments', 'generatedConfiguration',
                     'installedIdentity', 'compiled', 'installed'}
    if type(record) is not dict or set(record) != expected_keys:
        raise ValueError('subtitle build record keys differ from the Swift producer')
    source = work / 'dist' / (library + '-' + SUBTITLE_SELECTORS[library])
    build = work / 'dist' / library / platform / 'scratch' / arch
    prefix = work / 'dist' / library / platform / 'thin' / arch
    _, _, _, host, _, _, _ = _subtitle_platform(platform, arch)
    arguments = (('--host=' + host, '--with-pic', '--enable-static', '--disable-shared')
                 if library == 'libunibreak' else SUBTITLE_ARGUMENTS[library])
    fixed = {'schema': 1, 'library': library, 'selector': SUBTITLE_SELECTORS[library],
             'route': SUBTITLE_ROUTES[library], 'platform': platform, 'arch': arch,
             'sourceDirectory': str(source), 'buildDirectory': str(build),
             'prefix': str(prefix), 'arguments': list(arguments),
             'makeArguments': (list(SUBTITLE_MAKE_ARGUMENTS)
                               if library == 'libunibreak' else []),
             'compiled': True, 'installed': True}
    if any(not _crypto_exact_equal(record[name], item) for name, item in fixed.items()):
        raise ValueError('subtitle build record identity/options differ from fixed policy')
    sdk_root, cflags, ldflags = _subtitle_environment(
        record['environment'], work, library, platform, arch)
    generated_names = set(SUBTITLE_GENERATED[library])
    if library == 'libunibreak' and 'config.h' in record['generatedConfiguration']:
        generated_names.add('config.h')
    if (type(record['generatedConfiguration']) is not dict
            or set(record['generatedConfiguration']) != generated_names
            or type(record['installedIdentity']) is not dict
            or set(record['installedIdentity']) != set(SUBTITLE_INSTALLED[library])):
        raise ValueError('subtitle generated/installed file set differs')
    before = budget[0]
    generated = {name: _subtitle_inline(record['generatedConfiguration'][name],
                                         library + '/' + name, budget)
                 for name in sorted(generated_names)}
    installed = {name: _subtitle_inline(record['installedIdentity'][name],
                                         library + '/' + name, budget)
                 for name in SUBTITLE_INSTALLED[library]}
    if budget[0] - before > SUBTITLE_MAX_RECORD_BODIES:
        raise ValueError('subtitle per-record nested raw-body cap exceeded')
    if library == 'libunibreak':
        makefile = _subtitle_text(generated['Makefile'], 'libunibreak Makefile')
        logs = (_subtitle_text(generated['config.status'], 'libunibreak config.status') + '\n' +
                _subtitle_text(generated['config.log'], 'libunibreak config.log'))
        assignments = {
            'CC': record['environment']['CC'], 'CFLAGS': record['environment']['CFLAGS'],
            'CPPFLAGS': cflags, 'LDFLAGS': ldflags,
            'PACKAGE_VERSION': '8.0', 'prefix': str(prefix),
        }
        for name, expected in assignments.items():
            modifier_lines = re.findall(
                r'^[ \t]*(?:(?:override|export|private|unexport)[ \t]+)+' +
                re.escape(name) + r'\b[^\r\n]*$', makefile, re.M)
            assignment_lines = re.findall(
                r'^[ \t]*' + re.escape(name) +
                r'[ \t]*(?:::=|::=|:=|\+=|\?=|!=|=)[^\r\n]*$',
                makefile, re.M)
            values = re.findall(r'^[ \t]*' + re.escape(name) +
                                r'[ \t]*=[ \t]*([^\r\n]*?)[ \t]*$',
                                makefile, re.M)
            if modifier_lines or len(assignment_lines) != 1 or values != [expected]:
                raise ValueError('libunibreak generated Makefile assignment differs: ' + name)
        if any(argument not in logs for argument in arguments):
            raise ValueError('libunibreak generated configuration differs from fixed release options')
        if 'config.h' in generated:
            _subtitle_text(generated['config.h'], 'libunibreak config.h')
    else:
        _subtitle_meson_cross(generated['crossFile.meson'], work, library, platform,
                              arch, sdk_root, cflags, ldflags)
        ninja = _subtitle_text(generated['build.ninja'], library + ' build.ninja')
        if ('ninja_required_version' not in ninja
                or re.search(r'(?i)(?:^|[\s=])(curl|wget)(?:\s|$)|git\s+clone|wrapdb', ninja)):
            raise ValueError('subtitle Ninja file is missing identity or contains a fetch route')
        commands = _subtitle_json(generated['compile_commands.json'], 'compile commands')
        if type(commands) is not list or not commands or len(commands) > 100000:
            raise ValueError('subtitle compile command set is empty or unbounded')
        _, target, minimum, _, _, _, _ = _subtitle_platform(platform, arch)
        if library == 'libfribidi':
            _subtitle_fribidi_compile_commands(commands, build, arch, sdk_root, target, minimum)
        else:
            for command in commands:
                _subtitle_compile_command(command, arch, sdk_root, target, minimum)
        _subtitle_build_options(generated['meson-info/intro-buildoptions.json'],
                                library, prefix)
        if library == 'libbluray':
            _bluray_config(generated)
            _bluray_dependencies(generated['meson-info/intro-dependencies.json'], work, platform, arch, sdk_root)
            source = str(work / 'dist/libbluray-1.5.0-f676408e') + '/'
            udf = False
            for command in commands:
                if (command.get('directory') != str(build) or not isinstance(command.get('file'), str)
                        or not isinstance(command.get('output'), str)):
                    raise ValueError('libbluray compile directory/source identity differs')
                name = os.path.normpath(os.path.join(str(build), command['file']))
                if not name.startswith(source) or not name.endswith('.c'):
                    raise ValueError('libbluray compile source escapes its admitted archive')
                if not os.path.normpath(os.path.join(str(build), command['output'])).startswith(str(build) + '/'):
                    raise ValueError('libbluray compile output escapes its admitted build')
                _subtitle_compile_command(command, arch, sdk_root, target, minimum,
                                          (command['file'], command['output'], False))
                udf |= '/contrib/libudfread/src/' in name
            if not udf:
                raise ValueError('libbluray compile evidence lacks embedded UDF sources')
        else:
            _subtitle_dependencies(generated['meson-info/intro-dependencies.json'])
    header_name, pc_name = SUBTITLE_INSTALLED[library]
    _subtitle_header(installed[header_name], library)
    _subtitle_pc(installed[pc_name], library, prefix)
    return record


def _subtitle_release_boundary(work):
    state = work / 'dist' / 'subtitle-sources-v3'
    archive_directory = state / 'archives'
    if (state.is_symlink() or not state.is_dir() or state.resolve() != state.absolute()
            or archive_directory.is_symlink() or not archive_directory.is_dir()
            or archive_directory.resolve() != archive_directory.absolute()
            or {path.name for path in state.iterdir()} != {'archives', 'receipt.json'}
            or {path.name for path in archive_directory.iterdir()} !=
            {pin['filename'] for pin in SUBTITLE_SOURCE_OUTPUTS}):
        raise ValueError('subtitle release-source state is not the exact helper output')
    verified = []
    for pin in SUBTITLE_SOURCE_OUTPUTS:
        body = _crypto_read_regular(archive_directory / pin['filename'], pin['maximumBytes'])
        verified.append((pin, body, _crypto_index_archive(pin, body, True)))
    expected = _crypto_expected_receipt(
        verified, SUBTITLE_SOURCE_PROFILE,
        'fixed-sha256-from-upstream-release-metadata-maintained-android-pin-or-official-sidecar; fixed-apple-source-transforms', 3)
    receipt_path = state / 'receipt.json'
    body = _crypto_read_regular(receipt_path, CRYPTO_MAX_RECEIPT)
    if (not _crypto_exact_equal(_crypto_strict_json(body), expected)
            or body != _crypto_canonical(expected)):
        raise ValueError('subtitle helper receipt differs from retained raw archives')
    return {'path': 'dist/subtitle-sources-v3/receipt.json', 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest()}


def _subtitle_record_row(work, library, platform, arch, budget):
    path = (work / 'dist' / library / platform / 'scratch' / arch /
            'vortx-subtitle-build.json')
    body = _crypto_read_regular(path, SUBTITLE_MAX_RECORD)
    budget[0] += len(body)
    if budget[0] > SUBTITLE_MAX_INLINE_RAW:
        raise ValueError('subtitle aggregate raw-body cap exceeded')
    record = _subtitle_json(body, 'build record')
    _subtitle_record_contract(record, work, library, platform, arch, budget)
    return {'path': path.relative_to(work).as_posix(), 'bytes': len(body),
            'sha256': hashlib.sha256(body).hexdigest(),
            'bodyBase64': base64.b64encode(body).decode('ascii')}


def _bluray_observe_inputs(work, platform, arch, budget):
    record_path = work / 'dist/libbluray' / platform / 'scratch' / arch / 'vortx-subtitle-build.json'
    record = _subtitle_json(_crypto_read_regular(record_path, SUBTITLE_MAX_RECORD), 'bluray dependency record')
    sdk_root, _, _ = _subtitle_environment(record['environment'], work, 'libbluray', platform, arch)
    result = {}
    for name, relative in _bluray_dependency_paths(work, platform, arch, sdk_root).items():
        path = Path(relative) if relative.startswith('/') else work / relative
        if name.startswith('sdk') and name not in ('sdkLibxmlPC', 'sdkZlibPC'):
            sdk = Path(sdk_root).resolve()
            path = path.resolve()
            if sdk not in path.parents:
                raise ValueError('libbluray SDK dependency escaped the selected SDK')
        body = _crypto_read_regular(path, 512 * 1024 * 1024 if name == 'freetypeArchive' else 4 * 1024 * 1024)
        result[name] = {'path': relative, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        if name != 'freetypeArchive':
            result[name]['bodyBase64'] = base64.b64encode(body).decode('ascii')
    _bluray_dependency_inputs(result, work, platform, arch, sdk_root, budget)
    return result


def _subtitle_receipt_shape(value, body, work):
    expected_keys = {'schemaVersion', 'profile', 'sourceProfile', 'versions', 'selectors',
                     'releaseSourceBoundary', 'rows', 'retainedLibassCompatibility',
                     'retainedBlurayCompatibility', 'retainedFreetypeAuthentication',
                     'runtimeSecurityTested', 'promotionEligible'}
    if type(value) is not dict or set(value) != expected_keys:
        raise ValueError('subtitle BOM keys differ from schema 1')
    fixed = {'schemaVersion': 1, 'profile': NATIVE_UPGRADES_PROFILE,
             'sourceProfile': SUBTITLE_SOURCE_PROFILE, 'versions': SUBTITLE_VERSIONS,
             'selectors': SUBTITLE_SELECTORS, 'retainedLibassCompatibility': 'HOLD',
             'retainedBlurayCompatibility': 'HOLD', 'retainedFreetypeAuthentication': 'HOLD',
             'runtimeSecurityTested': False, 'promotionEligible': False}
    if any(not _crypto_exact_equal(value[name], item) for name, item in fixed.items()):
        raise ValueError('subtitle BOM identity or open-gate status differs')
    if not _crypto_exact_equal(value['releaseSourceBoundary'],
                               _subtitle_release_boundary(work)):
        raise ValueError('subtitle BOM/source-helper raw receipt correspondence')
    if type(value['rows']) is not list or len(value['rows']) != len(SUBTITLE_MATRIX):
        raise ValueError('subtitle BOM must contain the exact nine-row matrix')
    budget = [0]
    observed = []
    for row, identity in zip(value['rows'], SUBTITLE_MATRIX, strict=True):
        if type(row) is not dict or set(row) != {'platform', 'arch', 'slice', 'builds'}:
            raise ValueError('subtitle BOM matrix row shape differs')
        platform, arch, slice_name = identity
        if not _crypto_exact_equal(
                {name: row[name] for name in ('platform', 'arch', 'slice')},
                {'platform': platform, 'arch': arch, 'slice': slice_name}):
            raise ValueError('subtitle BOM matrix order or identity differs')
        builds = row['builds']
        if type(builds) is not dict or set(builds) != set(SUBTITLE_VERSIONS):
            raise ValueError('subtitle BOM build family differs')
        for library in SUBTITLE_VERSIONS:
            build = builds[library]
            if type(build) is not dict or set(build) != ({'record', 'dependencyInputs'} if library == 'libbluray' else {'record'}):
                raise ValueError('subtitle BOM build row differs')
            record_row = build['record']
            if (type(record_row) is not dict
                    or set(record_row) != {'path', 'bytes', 'sha256', 'bodyBase64'}
                    or record_row['path'] != ('dist/' + library + '/' + platform +
                                              '/scratch/' + arch +
                                              '/vortx-subtitle-build.json')
                    or type(record_row['bytes']) is not int
                    or not 0 < record_row['bytes'] <= SUBTITLE_MAX_RECORD
                    or not re.fullmatch(r'[0-9a-f]{64}', record_row['sha256'])
                    or not isinstance(record_row['bodyBase64'], str)):
                raise ValueError('subtitle BOM record wrapper differs')
            record_body = _subtitle_inline(
                {name: record_row[name]
                 for name in ('bytes', 'sha256', 'bodyBase64')},
                'BOM build record', budget, SUBTITLE_MAX_RECORD)
            record = _subtitle_json(record_body, 'BOM build record')
            _subtitle_record_contract(record, work, library, platform, arch, budget)
            if library == 'libbluray':
                sdk_root, _, _ = _subtitle_environment(record['environment'], work, library, platform, arch)
                _bluray_dependency_inputs(build['dependencyInputs'], work, platform, arch, sdk_root, budget)
        observed.append(identity)
    if observed != list(SUBTITLE_MATRIX):
        raise ValueError('subtitle BOM matrix coverage differs')
    canonical = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if body != canonical or len(body) > SUBTITLE_MAX_RECEIPT:
        raise ValueError('subtitle BOM is not canonical or exceeds its final bound')
    return value


def apple_subtitle_builds(work):
    """Capture all 36 authenticated subtitle/bluray build records and raw bodies."""
    work = work.absolute()
    if work.is_symlink() or not work.is_dir() or work.resolve() != work:
        raise ValueError('subtitle WORK must be a real absolute directory')
    release_boundary = _subtitle_release_boundary(work)
    budget = [0]
    rows = []
    for platform, arch, slice_name in SUBTITLE_MATRIX:
        builds = {}
        for library in SUBTITLE_VERSIONS:
            builds[library] = {'record': _subtitle_record_row(
                work, library, platform, arch, budget)}
            if library == 'libbluray':
                builds[library]['dependencyInputs'] = _bluray_observe_inputs(work, platform, arch, budget)
        rows.append({'platform': platform, 'arch': arch, 'slice': slice_name,
                     'builds': builds})
    destination_root = work / 'dist' / 'subtitle-builds'
    if destination_root.exists() or destination_root.is_symlink():
        raise ValueError('subtitle BOM destination must be fresh')
    destination_root.mkdir(mode=0o755)
    receipt = {'schemaVersion': 1, 'profile': NATIVE_UPGRADES_PROFILE,
               'sourceProfile': SUBTITLE_SOURCE_PROFILE,
               'versions': SUBTITLE_VERSIONS, 'selectors': SUBTITLE_SELECTORS,
               'releaseSourceBoundary': release_boundary, 'rows': rows,
               'retainedLibassCompatibility': 'HOLD',
               'retainedBlurayCompatibility': 'HOLD', 'retainedFreetypeAuthentication': 'HOLD',
               'runtimeSecurityTested': False, 'promotionEligible': False}
    encoded = (json.dumps(receipt, indent=2, sort_keys=True) + '\n').encode('utf-8')
    if len(encoded) > SUBTITLE_MAX_RECEIPT:
        raise ValueError('subtitle BOM exceeds its final bound')
    destination = destination_root / 'vortx-subtitle-builds.json'
    destination.write_bytes(encoded)
    print('PASS subtitle-profile ' + SUBTITLE_SOURCE_PROFILE)
    print('PASS apple-subtitle-builds')


def apple_subtitle_receipt(receipt_path):
    """Revalidate the canonical subtitle BOM against all current raw records."""
    receipt_path = receipt_path.absolute()
    if (receipt_path.is_symlink() or receipt_path.resolve() != receipt_path
            or receipt_path.name != 'vortx-subtitle-builds.json'
            or receipt_path.parent.name != 'subtitle-builds'
            or receipt_path.parent.parent.name != 'dist'):
        raise ValueError('subtitle receipt must be the exact generated BOM file')
    work = receipt_path.parent.parent.parent
    body = _crypto_read_regular(receipt_path, SUBTITLE_MAX_RECEIPT)
    value = _subtitle_receipt_shape(_subtitle_json(body, 'BOM'), body, work)
    rows = {(row['platform'], row['arch']): row for row in value['rows']}
    budget = [0]
    for platform, arch, _ in SUBTITLE_MATRIX:
        for library in SUBTITLE_VERSIONS:
            observed = _subtitle_record_row(work, library, platform, arch, budget)
            expected = rows[(platform, arch)]['builds'][library]['record']
            if not _crypto_exact_equal(observed, expected):
                raise ValueError('subtitle build record changed after BOM capture')
            if library == 'libbluray' and not _crypto_exact_equal(
                    _bluray_observe_inputs(work, platform, arch, budget), rows[(platform, arch)]['builds'][library]['dependencyInputs']):
                raise ValueError('libbluray dependency inputs changed after BOM capture')
    print('PASS subtitle-profile ' + SUBTITLE_SOURCE_PROFILE)
    print('PASS apple-subtitle-receipt')


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
    'avpriv_vortx_reference_reader_close_v1', 'avpriv_vortx_reference_recipe_release_v1',
    'avpriv_vortx_reference_sparse_budget_create_v1',
    'avpriv_vortx_reference_sparse_budget_info_v1',
    'avpriv_vortx_reference_sparse_open_v1',
    'avpriv_vortx_reference_sparse_next_part_v1',
    'avpriv_vortx_reference_sparse_read_part_v1',
    'avpriv_vortx_reference_sparse_stats_v1',
    'avpriv_vortx_reference_sparse_close_v1',
    'avpriv_vortx_reference_sparse_budget_release_v1')
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
CURRENT_SOURCE_PROFILE = 'original-http-sparse-native-reference-reader-v2-dependencies-openssl410-crypto-subtitles-bluray150-candidate'
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
                       'libplacebo': '275f7dea27924cdd34ab2e6c20e448b6bc4fab65'}
# Full-file postcomposition hashes retain cancellation plus owned observation.
CURRENT_CANCEL_FILES = {
    'misc/thread_tools.c': '151102f6fc8584e119317e3526502bbae492353b1c312ae4a8f3aecaee8bdab9',
    'misc/thread_tools.h': '62c7ea467ef911d9665cc4d7ffe28b2b09ea03f152326964056f62dab24acc7f',
    'stream/stream_lavf.c': '650bf7b94771e70a6b934b21e362bc77f66ceb1d23ee684d2b169c6e694544a5',
    'stream/reference_source.h': '1f606a977b55f18fdb9f9fc7f5e4549b5774bef10a06c33b06ab945531909b29',
                     'stream/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
}
CURRENT_RESPONSE_FILES = {
    'libavformat/http.c': 'd1301474b4389256f9862565a399a8888366a68d2e3630338848289fe8a9c831',
    'libavformat/url.h': '14e3acc5a6715d48edb3dadb2f5248deaa38c36b30ff763e9be4b89edf2b1a0c',
    'libavformat/avio.c': '16bf38abc7a5b5586268c71723041df2b90b7b99a8fbaeb06c8b20926534fe7a',
    'libavformat/tcp.c': 'cc9c11534ff5017d5931bb06788f95f10e9809953e5ec1cc03114a3a877cdcce',
    'libavformat/tls.c': '0f07be92b62cefb53c6db5552951c658c756fac36ce22b03ccca8a044f1911f2',
    'libavformat/tls.h': '257c76932de48ed84bf849a44ca9ea6fe85adb93a26bb4f7112b19000084ff19',
    'libavformat/tls_securetransport.c': 'b04fb28df85f1ef3883b4927d0bfab4477f400128c122f794ff13b4b9c85bebc',
}
CURRENT_READER_FFMPEG_CREATED = {
    'libavformat/vortx_reference_reader.inc': 'a8c74f929942423550dba65f738fd4d2ad2d63d975665f64e6da1b00f5001645',
    'libavformat/vortx_reference_pin.h': 'e0ccea8466e28169d0882fd664081baa09d3a39626d5351a31804b6dc4bb4e94',
    'libavformat/vortx_reference_reader_stream.h': 'adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87',
    'libavformat/vortx_reference_sparse.h': 'fa5f8e163d9368b30db6a9c7f0c69ed36e91d6a23ca409b7ba468c7d2282751c',
    'libavformat/vortx_reference_sparse.inc': '0e3201c26657765a1e6e7a15fe9db1bcac5cf96a8cb7e9d691533e0f4bf3f15f',
}
CURRENT_READER_MPV_FILES = {
    'demux/demux.c': 'f5a4cd7bf4e29902ef965e43027755c19ded1423129a3fbe9808ad89e64c0e1c',
    'demux/demux.h': 'bee52b38477ce7c8efd9a50e778f720ea8c9eec577afab29998682c26ffe75e2',
    'demux/demux_lavf.c': 'd945ae1699d383c3d645d2ab963abb166fd0c19f10a4ab144505e7fbfb0408fb',
    'demux/reference_reader.c': '222ec8920d38971d386470d8172e45a89a52350eb352124ddf954be9409b792b',
    'demux/reference_reader.h': '59d669fb370bc12f01856031b8f9d908eb1d50a927d955ecb1fe0b4484c2407d',
    'demux/reference_reader_internal.h': 'ded5dc02785e98934a966de87df4337e4c6aed0275bce26da2017883917f4c8d',
    'demux/demux_mkv.c': '1b7cc4d40efa0620bd10669dc44a7d6b32d0236e2ae2be3510a3bac0829e39c2',
    'demux/reference_mkv.h': '38df7814efc3aa7434e5afe269406a989ab6a5fa22a200aa28149a3ab68e46d9',
    'demux/reference_mkv_reader.c': '3ac442e8ff489d11f4a5df6b86f8d964457753f66ce5f3eb530d7db3746b6f7c',
    'demux/reference_mkv_sparse.h': '4b013b6e794257a576bc01f6becb9d120cb878e0851b2e0bdd2225dcf7b18df2',
    'demux/reference_mkv_sparse_planner.c': 'e36c6a879138b26e125f647c9254b41674b22b338ab12838376fe8d38bdae056',
    'demux/reference_mkv_sparse_planner.h': 'c88597a42186ceb81f45f1f44b3734469cf06889ea3bf91e4853b861943e84fe',
    'demux/reference_mkv_sparse_worker.c': '5deae68bf3c680aa4898ca6c8e0c0e76aa4628bffa693d0ae2d6dd287d9a5e64',
    'stream/vortx_reference_sparse.h': 'fa5f8e163d9368b30db6a9c7f0c69ed36e91d6a23ca409b7ba468c7d2282751c',
    'stream/stream_lavf.c': CURRENT_CANCEL_FILES['stream/stream_lavf.c'],
    'stream/reference_source.h': CURRENT_CANCEL_FILES['stream/reference_source.h'],
    'stream/vortx_reference_reader_stream.h': 'adaeb9d58c20ba3305bce4784205c20d6276ba67a758a9ec7207c850c7ba8d87',
    'player/owned_subtitle.c': '014b93845488ef873a4d29c4c8dd4ada69d244626e7a7b38bac82809989c6b29',
    'player/owned_subtitle.h': '8d2f0ee5c972d1da69213cd80fbe249bcf3f19d9de8a2ef87e241d2f08f06284',
    'player/command.c': 'e7a19f392bb69e9d208382f157234ac3c908d22a0d4afee5f372ee8f9d33dade',
    'player/reference_owned.inc': '23ac96bfedefcab2234afa2a6f329a36fac6735effca6798b4043d777b060217',
    'sub/owned_text_slot.h': '63c498dd4bdc9fa9c6d128f1be8f37d7cdf377c853888342897f6639601662f6',
    'sub/osd.c': '9b57ccd08e5777e2bc644cbf8001df0301552edf5cf24e36185a54f4cd667195',
    'sub/dec_sub.c': '6e2137b90fe7f2915a528bdd83426448afbd099229f65342dbfd85d1ba1d347b',
    'sub/dec_sub.h': 'cb9b5af7c3f87a8dda584617c1f20a90a9331182d54a50c7e5fb8e64e74b384f',
}
# Both maintained source lanes retain SENT/PREFIX's seven Android-conditional
# roots; only the Apple source includes MPVKit's MoltenVK configuration block.
CURRENT_READER_MPV_MESON = {
    'apple': 'c2abd8f89a772717fdd4bcdbb4a2236d9fb28eca029acfe84bf12f7333df5a55',
    'android': '32c9a1321883f6a980652173061c9c01727ef83b7a0dfe6e8e3a22364b753d47',
}
CURRENT_MBEDTLS_FILES = {'apple': '77d72cb88fc662b984f051335b356abc3e76e97ce39302dde6961ecdbba7a332',
                         'android': '7099664b2c15a784db38a0db7956f718862201ef6c02d27779b2fc654b44aeac'}
CURRENT_SOURCE_ROOTS = {'android': {'_native-publisher': 'fcf6745703dc1265bca88f12fee8fc355ddf251e',
             'dav1d': '54706fc6bc0cdecab7e9593974a4039cc038fca7',
             'ffmpeg': '946fcce07b6dcd0331c8cc609192aeff5e1924f8',
             'fontconfig': '0f236723a3c26bd9745e32ba2b3ee21e5f809295',
             'freetype': '0a0221a1347e2f1e07c395263540026e9a0aa7c7',
             'fribidi': 'b93119f5fdc7ea47672cc304c1455ffa6dfe7536',
             'harfbuzz': '35f656a1a2bab548ea7f54a6dd3fba2b09680d14',
             'libass': '4a05d8127f525943ebf45fdc6497c9e665947f0d',
             'libplacebo': '275f7dea27924cdd34ab2e6c20e448b6bc4fab65',
             'libxml2': '96498992efa48d52b0e8b83058bd88dbdaf153c1',
             'mbedtls': '068ff080b369adfac81509f9b57b2afabaf82dc5',
             'mpv': '8c67647b50059406c5c0444903597281b81516cf'},
 'apple': {'FFmpeg-9.0.2-946fcce0': '946fcce07b6dcd0331c8cc609192aeff5e1924f8',
           '_native-mpvkit': '2103893078c5e339073b11737b86f7f22b9c4491',
           'libmpv-master-8c67647b': '8c67647b50059406c5c0444903597281b81516cf',
           'libplacebo-7.387.0-275f7dea': '275f7dea27924cdd34ab2e6c20e448b6bc4fab65',
           'lcms2-2.20rc1-13154341': '1315434113566bcf3eb46c2485fcc1c0a56ab806',
           'libdav1d-1.5.4-54706fc6': '54706fc6bc0cdecab7e9593974a4039cc038fca7',
           'openssl-4.1.0-2f4e3a5f': '2f4e3a5fec05394068cfa7379890ab552483a8a6'}}
CURRENT_UNTRACKED_FILES = {'android': {'_native-publisher': {'buildscripts/include/download-libunibreak.sh': '34ce1ee52187a287ff7da6e18efd7368e5671ac77cc7da5c5a8027a3c63a0418',
                                   'buildscripts/patches/ffmpeg/0006-finite-reference-reader.patch': 'a06f4b8b9eadc12eace93b1b340e0555116061188d0674f063698ea75d953abd',
                                   'buildscripts/patches/mpv/0014-finite-reference-reader.patch': '42c020f9f6550af5072a8eff1b5860249bb9df5f9418855cb4636cd15854f7f2',
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
                     'demux/reference_mkv.h': CURRENT_READER_MPV_FILES['demux/reference_mkv.h'],
                     'demux/reference_mkv_reader.c': CURRENT_READER_MPV_FILES['demux/reference_mkv_reader.c'],
                     'demux/reference_mkv_sparse.h': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse.h'],
                     'demux/reference_mkv_sparse_planner.c': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse_planner.c'],
                     'demux/reference_mkv_sparse_planner.h': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse_planner.h'],
                     'demux/reference_mkv_sparse_worker.c': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse_worker.c'],
                     'stream/vortx_reference_sparse.h': CURRENT_READER_MPV_FILES['stream/vortx_reference_sparse.h'],
                     'stream/reference_source.h': CURRENT_CANCEL_FILES['stream/reference_source.h'],
                     'stream/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
                     'sub/owned_text_slot.h': CURRENT_READER_MPV_FILES['sub/owned_text_slot.h']}},
 'apple': {'FFmpeg-9.0.2-946fcce0': {**CURRENT_READER_FFMPEG_CREATED,
                                  'libavformat/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9'},
           '_native-mpvkit': {**CRYPTO_INSTALLED_HELPERS, **SUBTITLE_INSTALLED_HELPERS,
                              'Sources/BuildScripts/patch/FFmpeg/0003-sent-request-evidence.patch': '085b66cb5623ed8087481d3217b65da587b2970cd345a45896e7e87823dec9c9',
                              'Sources/BuildScripts/patch/FFmpeg/0005-finite-reference-reader.patch': 'a06f4b8b9eadc12eace93b1b340e0555116061188d0674f063698ea75d953abd',
                              'Sources/BuildScripts/patch/libmpv/0019-finite-reference-reader.patch': '42c020f9f6550af5072a8eff1b5860249bb9df5f9418855cb4636cd15854f7f2',
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
                                      'demux/reference_mkv.h': CURRENT_READER_MPV_FILES['demux/reference_mkv.h'],
                                      'demux/reference_mkv_reader.c': CURRENT_READER_MPV_FILES['demux/reference_mkv_reader.c'],
                                      'demux/reference_mkv_sparse.h': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse.h'],
                                      'demux/reference_mkv_sparse_planner.c': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse_planner.c'],
                                      'demux/reference_mkv_sparse_planner.h': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse_planner.h'],
                                      'demux/reference_mkv_sparse_worker.c': CURRENT_READER_MPV_FILES['demux/reference_mkv_sparse_worker.c'],
                                      'stream/vortx_reference_sparse.h': CURRENT_READER_MPV_FILES['stream/vortx_reference_sparse.h'],
                                      'stream/reference_source.h': CURRENT_CANCEL_FILES['stream/reference_source.h'],
                     'stream/vortx_http_request.h': 'd7730cc3492f6eaf6f6f1134fa50331c13e251fb1ddab8e577e1b912b8679dc9',
                                      'sub/owned_text_slot.h': CURRENT_READER_MPV_FILES['sub/owned_text_slot.h'],
                                      'video/out/vulkan/context_moltenvk.m': '00d2842ddb0dca5070feaf890c0190a0fe62beef9b29ec36e94e329d29a3da5e'},
           'libplacebo-7.387.0-275f7dea': {},
           'lcms2-2.20rc1-13154341': {},
           'libdav1d-1.5.4-54706fc6': {},
           'openssl-4.1.0-2f4e3a5f': {}}}

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


def _openssl_gitlink_placeholder(child):
    """Admit only an absent or empty real directory for a no-fetch submodule."""
    if child.is_symlink():
        raise ValueError('OpenSSL gitlink checkout must not be a symlink')
    if not child.exists():
        return
    if (not child.is_dir() or child.resolve() != child.absolute()
            or any(True for _ in child.iterdir())):
        raise ValueError('OpenSSL gitlink checkout must be absent or empty')


def source_paths(root, allowed_untracked):
    """Only pinned public Git roots and exact recipe-created untracked paths."""
    paths, tracked, gitlinks, seen, identities = [], set(), [], set(), []
    openssl_root = (root.name == OPENSSL_ROOT and
                    source_git(root, 'rev-parse', 'HEAD').decode().strip() == OPENSSL_SOURCE['commit'])
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
                if openssl_root:
                    if relative not in OPENSSL_GITLINKS or OPENSSL_GITLINKS[relative] != oid:
                        raise ValueError('OpenSSL source Gitlink differs from the pinned root tree')
                    _openssl_gitlink_placeholder(child)
                    gitlinks.append({'path': relative, 'commit': oid})
                else:
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
    if openssl_root and {row['path']: row['commit'] for row in gitlinks} != OPENSSL_GITLINKS:
        raise ValueError('OpenSSL source Gitlink set is incomplete or has extras')
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
        if old['directory'] == OPENSSL_ROOT:
            observed_gitlinks = {row['path']: row['commit'] for row in gitlinks}
            if observed_gitlinks != OPENSSL_GITLINKS:
                raise ValueError('OpenSSL source Gitlink map differs from the pinned root tree')
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
    required_pins = dict(CURRENT_NATIVE_PINS)
    if lane == 'apple':
        required_pins['lcms2-prerelease-candidate'] = '1315434113566bcf3eb46c2485fcc1c0a56ab806'
        required_pins['openssl-4.1.0-source'] = '2f4e3a5fec05394068cfa7379890ab552483a8a6'
    for name, pin in required_pins.items():
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
                         'apple-artifact-gate.txt', 'apple-shaderc-original-all.zip',
                         'apple-shaderc-original-xcframework.zip',
                         'apple-shaderc-inputs.json', 'apple-openssl-builds.json',
                         'apple-crypto-source-gmp.tar.xz',
                         'apple-crypto-source-nettle.tar.gz',
                         'apple-crypto-source-gnutls.tar.xz',
                         'apple-crypto-release-sources.tar',
                         'apple-crypto-release-sources.json',
                         'apple-crypto-chain.json',
                         'apple-subtitle-source-unibreak.tar.gz',
                         'apple-subtitle-source-fribidi.tar.xz',
                         'apple-subtitle-source-harfbuzz.tar.xz', 'apple-bluray-source.tar.xz',
                         'apple-subtitle-release-sources.tar',
                         'apple-subtitle-release-sources.json',
                         'apple-subtitle-builds.json'],
               'android': ['libmpv-release-candidate.aar', 'android-artifact-evidence.json',
                           'android-corresponding-sources.tar.gz', 'android-build-configs.tar.gz']}[lane]
    archive_sources = crypto_source_outputs(out) if lane == 'apple' else None
    subtitle_archive_sources = subtitle_source_outputs(out) if lane == 'apple' else None
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
        'sources': groups, 'archiveSources': archive_sources,
        'subtitleArchiveSources': subtitle_archive_sources,
        'outputs': [record(name) for name in outputs]})


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
    elif sys.argv[1] == 'apple-shaderc-inputs':
        if len(sys.argv) != 3:
            raise SystemExit('apple-shaderc-inputs WORK')
        apple_shaderc_inputs(Path(sys.argv[2]))
    elif sys.argv[1] == 'apple-shaderc-receipt':
        if len(sys.argv) != 3:
            raise SystemExit('apple-shaderc-receipt INPUT_DIR')
        apple_shaderc_receipt(Path(sys.argv[2]))
    elif sys.argv[1] == 'apple-openssl-builds':
        if len(sys.argv) != 3:
            raise SystemExit('apple-openssl-builds WORK')
        apple_openssl_builds(Path(sys.argv[2]))
    elif sys.argv[1] == 'apple-openssl-receipt':
        if len(sys.argv) != 3:
            raise SystemExit('apple-openssl-receipt FILE')
        apple_openssl_receipt(Path(sys.argv[2]))
    elif sys.argv[1] == 'apple-subtitle-builds':
        if len(sys.argv) != 3:
            raise SystemExit('apple-subtitle-builds WORK')
        apple_subtitle_builds(Path(sys.argv[2]))
    elif sys.argv[1] == 'apple-subtitle-receipt':
        if len(sys.argv) != 3:
            raise SystemExit('apple-subtitle-receipt FILE')
        apple_subtitle_receipt(Path(sys.argv[2]))
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
