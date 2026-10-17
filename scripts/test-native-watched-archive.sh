#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/native-watched-swift-inputs.sh
mkdir -p app/build
watched_archive_dir=$(mktemp -d "$PWD/app/build/native-watched-archive.XXXXXX")
trap 'rm -rf "$watched_archive_dir"' EXIT
sed -n '1,/^\/\/\/ The profile roster and the active selection\./{ /^\/\/\/ The profile roster and the active selection\./!p; }' app/SourcesShared/Profiles.swift > "$watched_archive_dir/UserProfile.swift"
{ printf '%s\n' 'import Foundation'; sed -n '/^struct ProfileDiscoveryPreferences: /,/^}$/p' app/SourcesShared/ProfileDiscoveryPreferences.swift; } > "$watched_archive_dir/Discovery.swift"
xcrun swiftc -O -swift-version 6 -parse-as-library -strict-concurrency=complete -warnings-as-errors \
    app/SourcesShared/VortxNativeRuntime.swift app/SourcesShared/VortxResourceBridge.swift \
    app/SourcesShared/VortxResourceProjection.swift app/SourcesShared/VortxNativeBootstrapArchive.swift \
    app/SourcesShared/VortxNativeHostPreferences.swift "${native_watched_swift_inputs[@]}" \
    app/SourcesShared/VortxNativeSession.swift app/SourcesShared/ProfileAddonPreferences.swift \
    app/SourcesShared/VortxProfileOverlayWitness.swift app/SourcesShared/VortxNativeProfileEditHost.swift \
    app/SourcesShared/SettingsDirtyKeys.swift app/SourcesShared/HomeRailPreferences.swift \
    "$watched_archive_dir/UserProfile.swift" "$watched_archive_dir/Discovery.swift" \
    app/Tests/VortxNativeWatchedArchiveTests.swift -o "$watched_archive_dir/watched-archive"
"$watched_archive_dir/watched-archive"
