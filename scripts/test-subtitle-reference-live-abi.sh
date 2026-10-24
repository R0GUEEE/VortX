#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# Explicit synthetic integration only. Supply the reviewed genuine header and
# same-build Mac dylib; this neither installs a library nor launches a player.
header="${VORTX_FFI_HEADER:?point at the exact reviewed private header}"
library="${VORTX_FFI_LIBRARY:?point at the matching macOS libvortx_ffi.dylib}"
[[ -f "$header" && -f "$library" ]]
mkdir -p app/build
query_dir=$(mktemp -d "$PWD/app/build/subtitle-reference-live-abi.XXXXXX")
inputs=(
    "$0" "$header" "$library"
    app/SourcesShared/VortxNativeRuntime.swift
    app/SourcesShared/SubtitleReferenceNativeQuery.swift
    app/Tests/SubtitleReferenceNativeDefaultCAdapterTests.swift
)
shasum -a 256 "${inputs[@]}" > "$query_dir/inputs.sha256"
cp "$header" "$query_dir/vortx_ffi.h"
printf 'module VortxEngine { header "vortx_ffi.h" export * }\n' > "$query_dir/module.modulemap"
cmp "$header" "$query_dir/vortx_ffi.h"

nice -n 15 xcrun swiftc -parse-as-library -swift-version 6 \
    -strict-concurrency=complete -warnings-as-errors -j 1 -num-threads 1 \
    -whole-module-optimization -sdk "$(xcrun --sdk macosx --show-sdk-path)" \
    -target "$(uname -m)-apple-macosx14.0" \
    -module-cache-path "$query_dir/module-cache" -I "$query_dir" \
    app/SourcesShared/VortxNativeRuntime.swift \
    app/SourcesShared/SubtitleReferenceNativeQuery.swift \
    app/Tests/SubtitleReferenceNativeDefaultCAdapterTests.swift \
    "$library" -o "$query_dir/default-c-query"
DYLD_LIBRARY_PATH="$(dirname "$library"):$(dirname "$library")/deps" \
    nice -n 15 "$query_dir/default-c-query"
shasum -a 256 --check "$query_dir/inputs.sha256"
cmp "$header" "$query_dir/vortx_ffi.h"
shasum -a 256 "$query_dir/default-c-query" "$query_dir/module.modulemap" \
    "$query_dir/vortx_ffi.h" > "$query_dir/artifacts.sha256"
printf 'Verified genuine default-C query inputs and artifacts: %s\n' "$query_dir"
