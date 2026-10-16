#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p app/build
archive_test_dir=$(mktemp -d app/build/native-bootstrap-archive.XXXXXX)
trap 'rm -rf "$archive_test_dir"' EXIT
if command -v xcrun >/dev/null 2>&1; then
    compiler=(xcrun swiftc)
else
    compiler=(swiftc)
fi
"${compiler[@]}" -swift-version 6 -strict-concurrency=complete -warnings-as-errors -parse-as-library \
    -module-cache-path "$archive_test_dir/module-cache" \
    app/SourcesShared/VortxNativeBootstrapArchive.swift app/Tests/VortxNativeBootstrapArchiveTests.swift \
    -o "$archive_test_dir/bootstrap-archive"
"$archive_test_dir/bootstrap-archive"
