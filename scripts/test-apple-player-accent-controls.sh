#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p app/build
accent_test_dir=$(mktemp -d app/build/player-accent-controls-tests.XXXXXX)
node scripts/prepare-player-accent-controls.cjs "$accent_test_dir"
xcrun swiftc -Onone -j 2 -parse-as-library "$accent_test_dir/accent.swift" -o "$accent_test_dir/accent-controls" 2>&1 | tee "$accent_test_dir/compiler.log"
"$accent_test_dir/accent-controls" 2>&1 | tee "$accent_test_dir/runtime.log"
