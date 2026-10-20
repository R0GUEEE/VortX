#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/native-watched-swift-inputs.sh
mode="${1:?choose components or checkpoint}"
case "$mode" in
  components) entry=app/Tests/XperienceNativePersistenceTests.swift ;;
  checkpoint) entry=app/Tests/XperienceNativeCheckpointTests.swift ;;
  *) printf 'Unsupported test mode\n' >&2; exit 2 ;;
esac
header="${VORTX_FFI_HEADER:?point at the reviewed private header}"
library="${VORTX_FFI_LIBRARY:?point at the matching retained macOS test ABI}"
swift_mode="${VORTX_TEST_SWIFT_VERSION:-6}"
case "$swift_mode" in 5|6) ;; *) exit 2 ;; esac
library_hash=$(shasum -a 256 "$library" | awk '{print $1}')
header_hash=$(shasum -a 256 "$header" | awk '{print $1}')
mkdir -p app/build
storage_test_dir=$(mktemp -d "$PWD/app/build/xperience-storage.XXXXXX")
printf 'Retained test directory %s\n' "$storage_test_dir"
cp "$header" "$storage_test_dir/vortx_ffi.h"
printf 'module VortxEngine { header "vortx_ffi.h" export * }\n' > "$storage_test_dir/module.modulemap"
sed -n '1,/^\/\/\/ The profile roster and the active selection\./{ /^\/\/\/ The profile roster and the active selection\./!p; }' app/SourcesShared/Profiles.swift > "$storage_test_dir/UserProfile.swift"
{
  printf '%s\n' 'import Foundation'
  sed -n '/^struct ProfileDiscoveryPreferences: /,/^}$/p' app/SourcesShared/ProfileDiscoveryPreferences.swift
} > "$storage_test_dir/Discovery.swift"
inputs=(
  app/SourcesShared/VortxNativeRuntime.swift app/SourcesShared/VortxResourceBridge.swift
  app/SourcesShared/VortxResourceProjection.swift app/SourcesShared/VortxNativeBootstrapArchive.swift
  app/SourcesShared/VortxNativeHostPreferences.swift "${native_watched_swift_inputs[@]}"
  app/SourcesShared/VortxNativeSession.swift app/SourcesShared/VortxNativeCoreFacade.swift
  app/SourcesShared/VortxNativePreviouslyWatched.swift
  "$storage_test_dir/UserProfile.swift" "$storage_test_dir/Discovery.swift"
  app/SourcesShared/ProfileAddonPreferences.swift app/SourcesShared/VortxProfileOverlayWitness.swift
  app/SourcesShared/VortxNativeProfiles.swift app/SourcesShared/VortxNativeProviderCredentials.swift
  app/SourcesShared/VortxNativeProfileEditHost.swift
  app/SourcesShared/CredentialScope.swift app/SourcesShared/Keychain.swift app/SourcesShared/VortXSyncCrypto.swift
  app/SourcesShared/XperienceCollections.swift app/SourcesShared/XperienceAddonBinding.swift
  app/SourcesShared/XperienceImportPrivacy.swift app/SourcesShared/XperienceNativePersistence.swift
  "$entry"
)
# Hash actual production inputs, including the source of each narrowly extracted value type.
shasum -a 256 app/SourcesShared/Profiles.swift app/SourcesShared/ProfileDiscoveryPreferences.swift \
  scripts/native-watched-swift-inputs.sh "$0" "$header" "$library" > "$storage_test_dir/input-sha256.txt"
for input in "${inputs[@]}"; do
  [[ "$input" == -lz ]] || shasum -a 256 "$input" >> "$storage_test_dir/input-sha256.txt"
done
nice -n 15 xcrun swiftc -parse-as-library -swift-version "$swift_mode" \
  -strict-concurrency=complete -warnings-as-errors -j 1 -num-threads 1 -whole-module-optimization \
  -D VORTX_ENGINE_STATE_BRIDGE -D VORTX_ENGINE_RESOURCE_HOST -I "$storage_test_dir" \
  "${inputs[@]}" "$library" -o "$storage_test_dir/storage-tests"
DYLD_LIBRARY_PATH="$(dirname "$library"):$(dirname "$library")/deps" \
  nice -n 15 "$storage_test_dir/storage-tests" "$storage_test_dir/checkpoints"
shasum -a 256 --check "$storage_test_dir/input-sha256.txt"
test "$library_hash" = "$(shasum -a 256 "$library" | awk '{print $1}')"
test "$header_hash" = "$(shasum -a 256 "$header" | awk '{print $1}')"
printf 'PASS %s with Swift %s, unchanged actual inputs and retained test ABI\n' "$mode" "$swift_mode"
