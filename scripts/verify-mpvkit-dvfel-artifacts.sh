#!/usr/bin/env bash
# Verify the shipped MPVKit-DVFEL package by inspecting its framework contents.
#
# This gate intentionally does not trust archive freshness, release prose, or a checksum alone:
# it proves the actual local binary targets contain the FFmpeg 9 ABI and Dolby Vision splitter that
# the package manifest claims to provide, then delegates the existing per-slice TLS proof.
set -euo pipefail

PKG="${1:?usage: verify-mpvkit-dvfel-artifacts.sh <MPVKit-DVFEL-package-dir>}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARTIFACTS="$PKG/artifacts"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "MPVKit-DVFEL ARTIFACT VERIFY FAILED: $*" >&2
  exit 1
}

# The default contract stays compatible with the currently selected shipping package.
# A candidate must select this exact dependency profile; unknown arguments fail closed.
DEPENDENCY_PROFILE=shipping
SHADERC_INPUTS=
OPENSSL_BUILDS=
CRYPTO_WORK=
if [ "$#" -eq 3 ] && [ "$2" = --dependency-profile ] &&
   [ "$3" = libplacebo-7.387.0-lcms2.20rc1-source-v1 ]; then
  DEPENDENCY_PROFILE="$3"
elif [ "$#" -eq 5 ] && [ "$2" = --dependency-profile ] &&
     [ "$3" = native-upgrades-dav1d154-shaderc20261-hb150-v2 ] &&
     [ "$4" = --shaderc-inputs ]; then
  DEPENDENCY_PROFILE="$3"
  SHADERC_INPUTS="$5"
  [ -n "$SHADERC_INPUTS" ] || fail "Shaderc original inputs are required"
elif [ "$#" -eq 7 ] && [ "$2" = --dependency-profile ] &&
     [ "$3" = native-upgrades-openssl410-v3 ] && [ "$4" = --shaderc-inputs ] &&
     [ "$6" = --openssl-builds ]; then
  DEPENDENCY_PROFILE="$3"
  SHADERC_INPUTS="$5"
  OPENSSL_BUILDS="$7"
  [ -n "$SHADERC_INPUTS" ] && [ -n "$OPENSSL_BUILDS" ] || fail "Dependency input receipts are required"
elif [ "$#" -eq 9 ] && [ "$2" = --dependency-profile ] &&
     [ "$3" = native-upgrades-openssl410-crypto-v4 ] && [ "$4" = --shaderc-inputs ] &&
     [ "$6" = --openssl-builds ] && [ "$8" = --crypto-work ]; then
  DEPENDENCY_PROFILE="$3"
  SHADERC_INPUTS="$5"
  OPENSSL_BUILDS="$7"
  CRYPTO_WORK="$9"
  [ -n "$SHADERC_INPUTS" ] && [ -n "$OPENSSL_BUILDS" ] && [ -n "$CRYPTO_WORK" ] ||
    fail "Shaderc, OpenSSL and crypto-chain inputs are required"
elif [ "$#" -ne 1 ]; then
  fail "usage: verify-mpvkit-dvfel-artifacts.sh PACKAGE [--dependency-profile FIXED_PROFILE [--shaderc-inputs DIRECTORY] [--openssl-builds FILE] [--crypto-work WORK]]"
fi

[ -f "$PKG/Package.swift" ] || fail "missing package manifest: $PKG/Package.swift"
[ -d "$ARTIFACTS" ] || fail "missing artifact directory: $ARTIFACTS"

# Local targets are the only rebuilt pieces. Requiring each path prevents a package that happens
# to contain one valid framework from silently falling back to the old remote target set.
local_targets=(
  Libmpv-GPL Libavcodec-GPL Libavdevice-GPL Libavfilter-GPL Libavformat-GPL
  Libavutil-GPL Libswresample-GPL Libswscale-GPL Libplacebo
)
if [ "$DEPENDENCY_PROFILE" = libplacebo-7.387.0-lcms2.20rc1-source-v1 ]; then
  local_targets+=(lcms2)
elif [ "$DEPENDENCY_PROFILE" = native-upgrades-dav1d154-shaderc20261-hb150-v2 ]; then
  local_targets+=(lcms2 Libdav1d)
elif [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-v3 ]; then
  local_targets+=(lcms2 Libdav1d Libssl Libcrypto)
elif [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-crypto-v4 ]; then
  local_targets+=(lcms2 Libdav1d Libssl Libcrypto gmp nettle hogweed gnutls Libsmbclient)
fi
for target in "${local_targets[@]}"; do
  grep -Fq "name: \"$target\"" "$PKG/Package.swift" || fail "manifest is missing local target $target"
  grep -Fq "path: \"artifacts/$target.xcframework\"" "$PKG/Package.swift" ||
    fail "manifest does not bind $target to its local artifact"
  [ -d "$ARTIFACTS/$target.xcframework" ] || fail "missing local framework $target.xcframework"
done

SLICES=(
  ios-arm64
  ios-arm64_x86_64-simulator
  tvos-arm64_arm64e
  tvos-arm64_x86_64-simulator
  macos-arm64_x86_64
)

framework_binary() {
  case "$1" in
    Libmpv-GPL) echo Libmpv ;;
    Libavcodec-GPL) echo Libavcodec ;;
    Libavdevice-GPL) echo Libavdevice ;;
    Libavfilter-GPL) echo Libavfilter ;;
    Libavformat-GPL) echo Libavformat ;;
    Libavutil-GPL) echo Libavutil ;;
    Libswresample-GPL) echo Libswresample ;;
    Libswscale-GPL) echo Libswscale ;;
    Libplacebo) echo Libplacebo ;;
    *) fail "unknown local framework $1" ;;
  esac
}

version_major_pattern() {
  case "$1" in
    Libavcodec-GPL) echo '^[[:space:]]*#define[[:space:]]+LIBAVCODEC_VERSION_MAJOR[[:space:]]+63([[:space:]]|$)' ;;
    Libavformat-GPL) echo '^[[:space:]]*#define[[:space:]]+LIBAVFORMAT_VERSION_MAJOR[[:space:]]+63([[:space:]]|$)' ;;
    Libmpv-GPL) echo '' ;;
    *) fail "unknown FFmpeg version target $1" ;;
  esac
}

# 9.0.2 keeps major 63; a major-only check would accept the superseded 63.1.100 build.
verify_ffmpeg_maintenance() {
  local target="$1" headers="$2" slice="$3" prefix candidate found
  case "$target" in
    Libavcodec-GPL) prefix=LIBAVCODEC ;;
    Libavformat-GPL) prefix=LIBAVFORMAT ;;
    *) fail "unknown maintenance target $target" ;;
  esac
  found=0
  while IFS= read -r candidate; do
    if grep -Eq "^[[:space:]]*#define[[:space:]]+${prefix}_VERSION_MINOR[[:space:]]+1([[:space:]]|$)" "$candidate" &&
       grep -Eq "^[[:space:]]*#define[[:space:]]+${prefix}_VERSION_MICRO[[:space:]]+102([[:space:]]|$)" "$candidate"; then
      found=1
      break
    fi
  done < <(find "$headers" -type f -name 'version.h' | LC_ALL=C sort)
  [ "$found" = 1 ] || fail "$target/$slice does not declare required FFmpeg 9.0.2 ABI 63.1.102"
}

expected_arches() {
  case "$1" in
    ios-arm64) echo 'arm64' ;;
    ios-arm64_x86_64-simulator) echo 'arm64 x86_64' ;;
    tvos-arm64_arm64e) echo 'arm64 arm64e' ;;
    tvos-arm64_x86_64-simulator) echo 'arm64 x86_64' ;;
    macos-arm64_x86_64) echo 'arm64 x86_64' ;;
    *) fail "unknown XCFramework slice $1" ;;
  esac
}

normalize_arches() {
  # lipo's order is not a package contract; the set is. Sort both sides before comparing so a
  # slice with an extra architecture cannot pass merely because it has at least one expected arch.
  printf '%s\n' $1 | LC_ALL=C sort | paste -sd' ' -
}

verify_arches() {
  local binary="$1" slice="$2" actual expected
  actual="$(lipo -archs "$binary")" || fail "cannot enumerate architectures in $binary"
  [ -n "$actual" ] || fail "no architectures found in $binary"
  expected="$(expected_arches "$slice")"
  [ "$(normalize_arches "$actual")" = "$(normalize_arches "$expected")" ] ||
    fail "$binary has architectures [$actual], expected exactly [$expected]"
}

if command -v xcrun >/dev/null 2>&1; then
  NM=(xcrun nm)
else
  NM=(nm)
fi
command -v lipo >/dev/null 2>&1 || fail "lipo is required to inspect every framework architecture"

for target in Libavcodec-GPL Libavformat-GPL Libmpv-GPL; do
  binary_name="$(framework_binary "$target")"
  pattern="$(version_major_pattern "$target")"
  framework="$ARTIFACTS/$target.xcframework"
  for slice in "${SLICES[@]}"; do
    slice_root="$framework/$slice/${binary_name}.framework"
    binary="$slice_root/$binary_name"
    headers="$slice_root/Headers"
    [ -f "$binary" ] || fail "missing $target binary in slice $slice"
    [ -d "$headers" ] || fail "missing $target headers in slice $slice"
    verify_arches "$binary" "$slice"

    if [ -n "$pattern" ]; then
      version_header=""
      while IFS= read -r candidate; do
        if grep -Eq "$pattern" "$candidate"; then
          version_header="$candidate"
          break
        fi
      done < <(find "$headers" -type f \( -name 'version_major.h' -o -name 'version.h' \) | LC_ALL=C sort)
      [ -n "$version_header" ] || fail "$target/$slice does not declare required FFmpeg 9 major 63"
      verify_ffmpeg_maintenance "$target" "$headers" "$slice"
    fi

    # dovi_split is an externally defined libavcodec capability. Keep the check per exact
    # slice/architecture; libavformat still gets its own FFmpeg 9 major/version and exact-arch
    # checks, while the Libmpv pass above prevents a CoreAudio player artifact from disappearing.
    if [ "$target" = Libavcodec-GPL ]; then
      arch_list="$(lipo -archs "$binary")"
      read -r -a arches <<<"$arch_list"
      for arch in "${arches[@]}"; do
        thin="$TMP/$target-$slice-$arch"
        if [ "${#arches[@]}" -eq 1 ]; then
          cp "$binary" "$thin"
        else
          lipo "$binary" -thin "$arch" -output "$thin"
        fi
        symbols="$("${NM[@]}" -gU "$thin" 2>/dev/null)" ||
          fail "cannot inspect defined symbols in $target/$slice [$arch]"
        # nm -gU reports only defined external symbols; the symbol must therefore be real code/data,
        # not an undefined reference or a string/comment mentioning dovi_split.
        grep -Eq '(^|[[:space:]])_?(ff_)?dovi_split(_bsf)?$' <<<"$symbols" ||
          fail "$target/$slice [$arch] lacks the defined dovi_split bitstream filter symbol"
      done
    fi
  done
done

if [ "$DEPENDENCY_PROFILE" != shipping ]; then
  # Dependency candidate identity is checked per slice and architecture. The RC header's
  # numeric 2200 value alone cannot establish stable status or source provenance.
  for dependency in Libplacebo lcms2; do
    for slice in "${SLICES[@]}"; do
      framework="$ARTIFACTS/$dependency.xcframework/$slice/$dependency.framework"
      binary="$framework/$dependency"
      headers="$framework/Headers"
      [ -f "$binary" ] && [ -d "$headers" ] || fail "missing $dependency candidate slice $slice"
      verify_arches "$binary" "$slice"
      if [ "$dependency" = Libplacebo ]; then
        version_pattern='^[[:space:]]*#define[[:space:]]+PL_API_VER[[:space:]]+387([[:space:]]|$)'
        header_name=config.h
        code_symbol=pl_renderer_reset
      else
        version_pattern='^[[:space:]]*#define[[:space:]]+LCMS_VERSION[[:space:]]+2200([[:space:]]|$)'
        header_name=lcms2.h
        code_symbol=cmsGetTagOffsetAndSize
      fi
      found=0
      while IFS= read -r header; do
        if grep -Eq "$version_pattern" "$header"; then
          if [ "$dependency" = lcms2 ]; then
            grep -Fq 'Version 2.20rc1' "$header" || fail "LittleCMS prerelease label lost in $slice"
          fi
          found=1
        fi
      done < <(find "$headers" -type f -name "$header_name" | LC_ALL=C sort)
      [ "$found" = 1 ] || fail "$dependency candidate header version differs in $slice"
      read -r -a arches <<<"$(lipo -archs "$binary")"
      for arch in "${arches[@]}"; do
        thin="$TMP/$dependency-$slice-$arch"
        if [ "${#arches[@]}" -eq 1 ]; then cp "$binary" "$thin"
        else lipo "$binary" -thin "$arch" -output "$thin"; fi
        symbols="$("${NM[@]}" -gU "$thin" 2>/dev/null)" || fail "cannot inspect $dependency/$slice [$arch]"
        grep -Eq "(^|[[:space:]])_?${code_symbol}$" <<<"$symbols" ||
          fail "$dependency/$slice [$arch] lacks the selected dependency code symbol"
      done
    done
  done
fi

if [ "$DEPENDENCY_PROFILE" = native-upgrades-dav1d154-shaderc20261-hb150-v2 ] ||
   [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-v3 ] ||
   [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-crypto-v4 ]; then
  # dav1d's public API stays 7.0.0 across releases. Bind exact header bytes, actual
  # defined code, and the release-tag/commit descriptor in every thin archive.
  dav1d_pin=54706fc6bc0cdecab7e9593974a4039cc038fca7
  for slice in "${SLICES[@]}"; do
    framework="$ARTIFACTS/Libdav1d.xcframework/$slice/Libdav1d.framework"
    binary="$framework/Libdav1d"
    headers="$framework/Headers"
    [ -f "$binary" ] && [ -d "$headers" ] || fail "missing dav1d candidate slice $slice"
    verify_arches "$binary" "$slice"
    found=0
    while IFS= read -r header; do
      actual="$(shasum -a 256 "$header")"
      [ "${actual%% *}" = ee63fe5d8bba3a84ed9e57360ea593dbd81c7e9b731e2e02ca2724f813b89fdd ] && found=1
    done < <(find "$headers" -type f -name version.h | LC_ALL=C sort)
    [ "$found" = 1 ] || fail "dav1d 1.5.4 API header differs in $slice"
    read -r -a arches <<<"$(lipo -archs "$binary")"
    for arch in "${arches[@]}"; do
      thin="$TMP/Libdav1d-$slice-$arch"
      if [ "${#arches[@]}" -eq 1 ]; then cp "$binary" "$thin"
      else lipo "$binary" -thin "$arch" -output "$thin"; fi
      symbols="$("${NM[@]}" -gU "$thin" 2>/dev/null)" || fail "cannot inspect dav1d/$slice [$arch]"
      for code_symbol in dav1d_version dav1d_version_api dav1d_open; do
        grep -Eq "(^|[[:space:]])_?${code_symbol}$" <<<"$symbols" ||
          fail "dav1d/$slice [$arch] lacks $code_symbol"
      done
      found=0
      while IFS= read -r descriptor; do
        short="${descriptor#1.5.4-0-g}"
        if [[ "$descriptor" == 1.5.4-0-g* && "$short" =~ ^[0-9a-f]{7,40}$ && "$dav1d_pin" == "$short"* ]]; then
          found=1
        fi
      done < <(/usr/bin/strings "$thin")
      [ "$found" = 1 ] || fail "dav1d/$slice [$arch] release/commit descriptor differs"
    done
  done
  # Revalidate both published archive identities and the fixed matrix receipt. The
  # original comparison ran once against trusted xcrun/lipo in the source builder.
  python3 "$REPO/scripts/native-media-hosted/evidence.py" apple-shaderc-receipt "$SHADERC_INPUTS"
fi

if [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-v3 ] ||
   [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-crypto-v4 ]; then
  for dependency in Libcrypto Libssl; do
    for slice in "${SLICES[@]}"; do
      framework="$ARTIFACTS/$dependency.xcframework/$slice/$dependency.framework"
      binary="$framework/$dependency"
      headers="$framework/Headers"
      [ -f "$binary" ] && [ -d "$headers" ] || fail "missing $dependency source slice $slice"
      verify_arches "$binary" "$slice"
      found=0
      while IFS= read -r header; do
        if grep -Eq '^#[[:space:]]*define[[:space:]]+OPENSSL_VERSION_MAJOR[[:space:]]+4([[:space:]]|$)' "$header" &&
           grep -Eq '^#[[:space:]]*define[[:space:]]+OPENSSL_VERSION_MINOR[[:space:]]+1([[:space:]]|$)' "$header" &&
           grep -Eq '^#[[:space:]]*define[[:space:]]+OPENSSL_VERSION_PATCH[[:space:]]+0([[:space:]]|$)' "$header" &&
           grep -Fq 'OpenSSL 4.1.0 23 Oct 2026' "$header"; then found=1; fi
      done < <(find "$headers" -type f -name opensslv.h | LC_ALL=C sort)
      [ "$found" = 1 ] || fail "$dependency/$slice does not declare OpenSSL 4.1.0"
      read -r -a arches <<<"$(lipo -archs "$binary")"
      for arch in "${arches[@]}"; do
        thin="$TMP/$dependency-$slice-$arch"
        if [ "${#arches[@]}" -eq 1 ]; then cp "$binary" "$thin"
        else lipo "$binary" -thin "$arch" -output "$thin"; fi
        symbols="$("${NM[@]}" -gU "$thin" 2>/dev/null)" || fail "cannot inspect $dependency/$slice [$arch]"
        if [ "$dependency" = Libcrypto ]; then
          code_symbols=(OpenSSL_version OpenSSL_version_num)
          strings_result="$(/usr/bin/strings "$thin")"
          grep -Fxq 'OpenSSL 4.1.0 23 Oct 2026' <<<"$strings_result" || fail "libcrypto release string differs"
        else code_symbols=(SSL_new SSL_get1_peer_certificate); fi
        for code_symbol in "${code_symbols[@]}"; do
          grep -Eq "(^|[[:space:]])_?${code_symbol}$" <<<"$symbols" || fail "$dependency lacks $code_symbol"
        done
      done
    done
  done
  python3 "$REPO/scripts/native-media-hosted/evidence.py" apple-openssl-receipt "$OPENSSL_BUILDS"
fi

if [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-crypto-v4 ]; then
  CRYPTO_SOURCE_HELPER="$CRYPTO_WORK/Sources/BuildScripts/crypto-release-sources.py"
  CRYPTO_GATE_HELPER="$CRYPTO_WORK/Sources/BuildScripts/crypto-chain-gate.py"
  CRYPTO_SOURCE_HELPER_SHA256="3c310499d446eed72eb16521b27c16f718f23e7ab1472dec1fad79cc40ed1537"
  CRYPTO_GATE_HELPER_SHA256="041dd13e48e1821f5d7bf049bb8eb097d3772d6ea867c15a3b7646779a3c015d"
  [ -f "$CRYPTO_SOURCE_HELPER" ] && [ ! -L "$CRYPTO_SOURCE_HELPER" ] || fail "crypto source helper is missing"
  [ -f "$CRYPTO_GATE_HELPER" ] && [ ! -L "$CRYPTO_GATE_HELPER" ] || fail "crypto gate helper is missing"
  [ "$(shasum -a 256 "$CRYPTO_SOURCE_HELPER" | awk '{print $1}')" = "$CRYPTO_SOURCE_HELPER_SHA256" ] ||
    fail "crypto source helper hash is not the resealed parent pin"
  [ "$(shasum -a 256 "$CRYPTO_GATE_HELPER" | awk '{print $1}')" = "$CRYPTO_GATE_HELPER_SHA256" ] ||
    fail "crypto gate helper hash is not the resealed parent pin"
  # Re-read the authenticated release closure immediately before the content gate. The gate then
  # recomputes packaged/public-symbol correspondence and binds its own receipt to current bytes.
  python3 "$CRYPTO_SOURCE_HELPER" verify "$CRYPTO_WORK"
  python3 "$CRYPTO_GATE_HELPER" verify "$CRYPTO_WORK" "$PKG" \
    --dependency-profile native-upgrades-crypto-chain-v1
fi

# The existing TLS gate checks every Libavformat architecture, config macros, SecureTransport
# imports, and absence of GnuTLS references. Keep it in this content gate so release fetches cannot
# stop after a checksum/unzip or accidentally skip the security backend proof.
"$REPO/scripts/verify-mpvkit-dvfel-apple-tls.sh" "$PKG"

echo "MPVKit-DVFEL ARTIFACT VERIFY PASSED: FFmpeg 9.0.2 (libavcodec/libavformat 63.1.102), dovi_split, and Apple TLS gates"
if [ "$DEPENDENCY_PROFILE" = libplacebo-7.387.0-lcms2.20rc1-source-v1 ]; then
  echo "PASS dependency-profile libplacebo-7.387.0-lcms2.20rc1-source-v1"
elif [ "$DEPENDENCY_PROFILE" = native-upgrades-dav1d154-shaderc20261-hb150-v2 ]; then
  echo "PASS dependency-profile native-upgrades-dav1d154-shaderc20261-hb150-v2"
elif [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-v3 ]; then
  echo "PASS openssl-profile openssl-4.1.0-source-v1"
  echo "PASS dependency-profile native-upgrades-openssl410-v3"
elif [ "$DEPENDENCY_PROFILE" = native-upgrades-openssl410-crypto-v4 ]; then
  echo "PASS openssl-profile openssl-4.1.0-source-v1"
  echo "PASS crypto-profile gmp-6.3.0-nettle-4.0-gnutls-3.8.13-samba-4.15.13-2512-source-v1"
  echo "PASS dependency-profile native-upgrades-openssl410-crypto-v4"
fi
