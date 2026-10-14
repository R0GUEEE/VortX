#!/usr/bin/env bash
# Network shares (WebDAV and web folders): address rules, credential scoping, listing parsers, and the
# client against a local fixture server (never the network).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p app/build
dir=$(mktemp -d app/build/network-shares.XXXXXX)
server_pid=""
cleanup() { [[ -n "$server_pid" ]] && kill "$server_pid" 2>/dev/null; rm -rf "$dir"; }
trap cleanup EXIT
xcrun swiftc -parse-as-library -warnings-as-errors app/SourcesShared/NetworkShares.swift \
  app/Tests/NetworkSharesTests.swift -o "$dir/shares"
"$dir/shares"
xcrun swiftc -parse-as-library -warnings-as-errors app/SourcesShared/NetworkShares.swift app/SourcesShared/NetworkShareClient.swift \
  app/Tests/NetworkShareClientLiveTests.swift -o "$dir/client"
python3 -I test/fixtures/network-share-server.py > "$dir/port.txt" &
server_pid=$!
for _ in $(seq 1 50); do [[ -s "$dir/port.txt" ]] && break; sleep 0.1; done
"$dir/client" "$(head -1 "$dir/port.txt")"
