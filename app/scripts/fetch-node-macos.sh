#!/bin/sh
# Fetch a standalone Node.js binary for the macOS streaming server.
#
# StremioXMac runs Stremio's server.js (the torrent engine + /proxy + HLS) in a child
# process so TORRENT streams play on the Mac. iOS/tvOS embed nodejs-mobile (a node
# *library*), but nodejs-mobile has no macOS slice, so on macOS we ship the ordinary
# standalone `node` executable from nodejs.org and spawn it (see MacNodeServer.swift).
#
# INTEL PORT (this fork): the Mac target now builds a UNIVERSAL app (arm64 + x86_64), so the
# bundled interpreter must be universal too. This script downloads BOTH official per-arch
# tarballs, verifies each against its own pinned sha256, and `lipo -create`s them into a single
# two-slice Mach-O. The destination keeps its historical `node-darwin-arm64` name because that
# path is referenced by project.yml, MacNodeServer.swift and the packaging verifiers; the file
# it names is now a universal binary (asserted here at fetch time and again in CI).
#
# It is large (~200 MB universal) so it is .gitignored and produced on demand: this script is
# idempotent (skips the download when both slices are already present) and runs both as an
# Xcode pre-build phase and standalone before a build.
set -eu

NODE_VERSION="v20.18.1"          # pinned LTS; only system frameworks as deps (otool-verified)
PKG_ARM="node-${NODE_VERSION}-darwin-arm64"
PKG_X64="node-${NODE_VERSION}-darwin-x64"
BASE_URL="https://nodejs.org/dist/${NODE_VERSION}"
# Pinned sha256 values, from the official https://nodejs.org/dist/v20.18.1/SHASUMS256.txt.
# Each tarball is verified against its own digest before it is unpacked or its `node` is ever
# run, so a swapped/MITM'd download fails closed instead of shipping an unverified interpreter
# inside the .app. Bump these in lockstep with NODE_VERSION above (take the new lines from that
# release's SHASUMS256.txt).
NODE_SHA256_ARM64="9e92ce1032455a9cc419fe71e908b27ae477799371b45a0844eedb02279922a4"
NODE_SHA256_X86_64="c5497dd17c8875b53712edaf99052f961013cedc203964583fc0cfc0aaf93581"

# Resolve Resources/ relative to this script, so it works from any CWD (Xcode runs it
# from the project dir; a developer may run it from anywhere).
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
RES_DIR="${SCRIPT_DIR}/../Resources"
DEST="${RES_DIR}/node-darwin-arm64"

have_arch() { # <arch>
  lipo -archs "$DEST" 2>/dev/null | tr ' ' '\n' | grep -qx "$1"
}

# Idempotent: if a two-slice binary is already there, do nothing. Checked with lipo rather than by
# executing it: on an Apple Silicon host an x86_64-only binary would need Rosetta just to answer
# --version, and this script must not depend on Rosetta being installed.
if [ -x "${DEST}" ] && have_arch arm64 && have_arch x86_64; then
  echo "fetch-node-macos: ${DEST} already universal (arm64 + x86_64), skipping."
  exit 0
fi

TMP=$(mktemp -d)
trap 'rm -rf "${TMP}"' EXIT

fetch_slice() { # <pkg> <slice-name> <expected-sha256>
  pkg="$1"; slice="$2"; expected="$3"
  url="${BASE_URL}/${pkg}.tar.gz"
  echo "fetch-node-macos: downloading ${url}"
  curl -fsSL "${url}" -o "${TMP}/${pkg}.tar.gz"
  echo "fetch-node-macos: verifying sha256 (${expected})"
  printf '%s  %s\n' "${expected}" "${TMP}/${pkg}.tar.gz" > "${TMP}/${pkg}.sha256"
  if ! shasum -a 256 -c "${TMP}/${pkg}.sha256"; then
    echo "fetch-node-macos: sha256 mismatch for ${pkg}.tar.gz; refusing to unpack" >&2
    exit 1
  fi
  tar -xzf "${TMP}/${pkg}.tar.gz" -C "${TMP}"
  cp "${TMP}/${pkg}/bin/node" "${TMP}/node-${slice}"
  chmod +x "${TMP}/node-${slice}"
}

fetch_slice "${PKG_ARM}" arm64 "${NODE_SHA256_ARM64}"
fetch_slice "${PKG_X64}" x86_64 "${NODE_SHA256_X86_64}"

echo "fetch-node-macos: lipo -create (arm64 + x86_64)"
lipo -create "${TMP}/node-arm64" "${TMP}/node-x86_64" -output "${TMP}/node-universal"

# Fail closed if the merge did not actually produce both slices (e.g. a future tarball ships a
# universal binary itself, which lipo silently collapses into the first input).
for arch in arm64 x86_64; do
  lipo -archs "${TMP}/node-universal" | tr ' ' '\n' | grep -qx "$arch" \
    || { echo "fetch-node-macos: merged node is missing the ${arch} slice" >&2; exit 1; }
done

mkdir -p "${RES_DIR}"
cp "${TMP}/node-universal" "${DEST}"
chmod +x "${DEST}"

echo "fetch-node-macos: installed $(lipo -archs "${DEST}") node at ${DEST}"
