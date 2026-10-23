#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
publication_source_ref=working-tree
publication_prepare_only=0
publication_benchmark=0
publication_swift_version=6
while (($#)); do
  case "$1" in
    --prepare-only) publication_prepare_only=1; shift ;;
    --benchmark) publication_benchmark=1; shift ;;
    --swift-version)
      [[ $# -ge 2 && ( "$2" == 5 || "$2" == 6 ) ]] || { printf 'Expected --swift-version 5 or 6\n' >&2; exit 2; }
      publication_swift_version=$2; shift 2 ;;
    --*) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    *) [[ "$publication_source_ref" == working-tree ]] || { printf 'Only one source ref is allowed\n' >&2; exit 2; }
       publication_source_ref=$1; shift ;;
  esac
done
mkdir -p app/build
publication_test_dir=$(mktemp -d "$PWD/app/build/addon-stream-publication.XXXXXX")
printf 'Source %s\nRetained output %s\nSwift version %s; benchmark %s\n' \
  "$publication_source_ref" "$publication_test_dir" "$publication_swift_version" "$publication_benchmark"
publication_compile_status=not-run
publication_runtime_status=not-run
publication_phase=extract
publication_supervisor="$PWD/test/mpv-bounded-child.mjs"
publication_receipt() {
  publication_exit=$?
  trap - EXIT
  set +e
  if [[ -f "$publication_test_dir/input-manifest.json" ]]; then
    node - "$publication_test_dir" "$publication_source_ref" > "$publication_test_dir/conservation.log" 2>&1 <<'NODE'
const fs = require('node:fs'), cp = require('node:child_process'), crypto = require('node:crypto');
const [out, ref] = process.argv.slice(2);
const manifest = JSON.parse(fs.readFileSync(`${out}/input-manifest.json`, 'utf8'));
for (const entry of manifest.inputs) {
  for (const bytes of [fs.readFileSync(`${out}/inputs/${entry.path}`), entry.origin === 'working-tree'
    ? fs.readFileSync(entry.path) : cp.execFileSync('git', ['show', `${ref}:${entry.path}`])]) {
    if (bytes.length !== entry.bytes || crypto.createHash('sha256').update(bytes).digest('hex') !== entry.sha256)
      throw new Error(`Input changed: ${entry.path}`);
  }
}
console.log(`Conserved ${manifest.inputs.length} input snapshots and original source inputs`);
NODE
    [[ $? -eq 0 ]] || publication_exit=3
  fi
  if [[ -f "$publication_test_dir/generated-before.sha256" ]]; then
    shasum -a 256 "$publication_test_dir/Collaborators.swift" \
      "$publication_test_dir/ProductionResourceValues.swift" "$publication_test_dir/ProductionPublication.swift" \
      > "$publication_test_dir/generated-after.sha256"
    [[ $? -eq 0 ]] || publication_exit=3
    cmp -s "$publication_test_dir/generated-before.sha256" "$publication_test_dir/generated-after.sha256" || publication_exit=3
  fi
  printf 'source=%s\nswift_version=%s\nbenchmark=%s\ncompile_status=%s\nruntime_status=%s\nexit=%s\n' \
    "$publication_source_ref" "$publication_swift_version" "$publication_benchmark" \
    "$publication_compile_status" "$publication_runtime_status" "$publication_exit" > "$publication_test_dir/status.txt"
  printf 'phase=%s\nscope=whole-models-extracted-publication-resource-values-RAM-not-native-session-or-app\n' \
    "$publication_phase" >> "$publication_test_dir/status.txt"
  shasum -a 256 "$publication_test_dir/status.txt"
  for publication_artifact in input-manifest.json Collaborators.swift ProductionResourceValues.swift ProductionPublication.swift compiler.log test.log addon-stream-publication; do
    if [[ -f "$publication_test_dir/$publication_artifact" ]]; then
      shasum -a 256 "$publication_test_dir/$publication_artifact"
    fi
  done
  printf 'Retained receipt: %s/status.txt\n' "$publication_test_dir"
  exit "$publication_exit"
}
trap publication_receipt EXIT
node - "$publication_test_dir" "$publication_source_ref" <<'NODE'
const fs = require('node:fs');
const cp = require('node:child_process');
const crypto = require('node:crypto');
const path = require('node:path');
const [out, ref] = process.argv.slice(2);
const names = ['VortxResourceBridge', 'VortxResourceProjection', 'DetailMetaRecoveryPolicy',
  'CatalogRowResolution', 'SubtitleReleaseFingerprint', 'UsenetStreamValidation',
  'DiagnosticPlaybackIntegrityPolicy', 'ProfileAddonPreferences'];
const inputs = ['app/SourcesShared/CoreBridge.swift', 'app/SourcesShared/CoreModels.swift',
  'app/Tests/BingeSourceMemoryRaceContractTests.swift', 'app/SourcesShared/VortxNativeRuntime.swift',
  'app/SourcesShared/ContinueWatchingDedupe.swift', ...names.map(name => `app/SourcesShared/${name}.swift`),
  'app/Tests/AddonStreamPublicationTests.swift', 'scripts/test-addon-stream-publication.sh',
  'test/mpv-bounded-child.mjs'];
// Operational harness bytes are always the ones executing now, even for a historical model ref.
const harness = new Set(['scripts/test-addon-stream-publication.sh', 'test/mpv-bounded-child.mjs']);
const origin = name => ref === 'working-tree' || harness.has(name) ? 'working-tree' : ref;
// Capture every input ONCE. Compiler and source checks use only conserved bytes.
const captured = new Map(inputs.map(name => [name, origin(name) === 'working-tree'
  ? fs.readFileSync(name) : cp.execFileSync('git', ['show', `${ref}:${name}`])]));
const manifest = [];
for (const [name, bytes] of captured) {
  const retained = path.join(out, 'inputs', name);
  fs.mkdirSync(path.dirname(retained), {recursive: true});
  fs.writeFileSync(retained, bytes);
  manifest.push({path: name, origin: origin(name), bytes: bytes.length,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex')});
}
fs.writeFileSync(`${out}/input-manifest.json`, JSON.stringify({source:ref, inputs:manifest}, null, 2) + '\n');
const read = name => captured.get(name).toString('utf8');
function block(source, marker) {
  const begin = source.indexOf(marker);
  if (begin < 0) throw new Error(`Missing declaration ${marker}`);
  const open = source.indexOf('{', begin);
  let depth = 1, end = open + 1;
  for (; depth && end < source.length; end++) {
    if (source[end] === '{') depth++;
    if (source[end] === '}') depth--;
  }
  if (depth) throw new Error(`Unbalanced declaration ${marker}`);
  return source.slice(begin, end);
}
const core = read('app/SourcesShared/CoreBridge.swift');
const models = read('app/SourcesShared/CoreModels.swift');
const stubs = read('app/Tests/BingeSourceMemoryRaceContractTests.swift');
const start = stubs.indexOf('enum DebridService:');
const end = stubs.indexOf('// MARK: - StreamRanking peripheral stubs');
if (start < 0 || end < start) throw new Error('Missing inert collaborator boundaries');
fs.writeFileSync(`${out}/Collaborators.swift`, 'import Foundation\n' + stubs.slice(start, end) + '\n'
  + block(stubs, 'enum ProfileStore {') + `
// This fixture-only immutable, fieldless collaborator can satisfy checked
// Sendable. No production formatter/model conformance is mocked or changed.
extension DebridKeys: Sendable {}
extension CommunityStreamGateway: Sendable {}
extension DebridPlaybackAvailability: Sendable {}
extension VortXSyncManager {
  static func orderedByApplied<T>(_ values: [T], url: (T) -> String) -> [T] { values }
}
`);
const declarations = [
  block(read('app/SourcesShared/VortxNativeRuntime.swift'), 'enum VortxNativeError:'),
  block(read('app/SourcesShared/ContinueWatchingDedupe.swift'), 'enum WatchedMembershipPolicy {'),
];
const bridge = read('app/SourcesShared/VortxResourceBridge.swift');
const resourceMarkers = ['indirect enum VortxJSON: Codable, Equatable, Sendable {',
  'struct VortxResourceRequest: Codable, Equatable, Sendable {',
  'struct VortxResourceAddon: Codable, Sendable {', 'struct VortxResourceGroup: Decodable, Sendable {',
  'struct VortxResourceSnapshot: Sendable {'];
// Exact production value declarations only. Neither VortxEngine import/C ABI nor
// transport/lease execution is compiled or mocked by this pure host fixture.
fs.writeFileSync(`${out}/ProductionResourceValues.swift`, 'import Foundation\n'
  + resourceMarkers.map(marker => block(bridge, marker)).join('\n'));
const hasLibraryMerge = core.includes('private static func libraryMetaDetailsUpdate(');
if (!hasLibraryMerge && ref === 'working-tree') throw new Error('Missing current library overlay merge');
const predicates = ['private static func metaDetailsNeedsRepublish(', 'private static func metaDetailsStreamsChanged(',
  'private static func currentReadyStreamCount('];
if (hasLibraryMerge) predicates.push('private static func libraryMetaDetailsUpdate(');
predicates.push(core.includes('private static func streamSetsEqual(')
  ? 'private static func streamSetsEqual(' : 'private static func streamSetSignature(');
declarations.push(`@MainActor final class AddonPublicationProbe {
  var metadataPublications = 0
  var metaDetails: CoreMetaDetails? { didSet { metadataPublications += 1 } }
  var streamsEpoch = 0
  var indexerStarts = 0
  var names: [String: String] = [:]
  func addonNamesByBase() -> [String: String] { names }
  func isTombstonedAddonBase(_ base: String, removed: Set<String>) -> Bool { false }
  func startNZBIndexerSearchIfNeeded(details: CoreMetaDetails) { indexerStarts += 1 }
  ${predicates.map(marker => block(core, marker)).join('\n')}
  ${block(core, 'private func assembleStreamGroups(')}
  func needs(_ next: CoreMetaDetails?) -> Bool { Self.metaDetailsNeedsRepublish(current: metaDetails, next: next) }
  func streamsChange(_ next: CoreMetaDetails?) -> Bool { Self.metaDetailsStreamsChanged(current: metaDetails, next: next) }
  func readyCount(_ group: [String: Any]) -> Int? { Self.currentReadyStreamCount(group) }
  // Actual pure merge helper; surrounding account/request fences are source-checked below,
  // not executed as a real CoreBridge receipt in this RAM fixture.
  ${hasLibraryMerge ? `func acceptLibrary(_ next: CoreMetaDetails?) {
    if let merged = Self.libraryMetaDetailsUpdate(current: metaDetails, next: next) { metaDetails = merged }
  }` : ''}
  func groups(streamID: String? = nil) -> [CoreStreamSourceGroup] {
    guard let metaDetails else { return [] }
    return assembleStreamGroups(metaDetails, streamId: streamID)
  }
  // Actual coalesced accepted branch only. Account/profile/request fences, the
  // raw error-sidecar cache are NOT modeled.
  func accept(_ details: CoreMetaDetails?) {
    ${block(core, 'if Self.metaDetailsNeedsRepublish(current: self.metaDetails, next: details) {')}
  }
}`);
fs.writeFileSync(`${out}/ProductionPublication.swift`, 'import Foundation\n' + declarations.join('\n'));
if (hasLibraryMerge) {
const libraryStart = core.indexOf('let requestGeneration = metaLoadTargetLock.withLock { nativeMetaRequestGeneration }');
if (libraryStart < 0) throw new Error('Missing library request generation capture');
const libraryClosure = block(core.slice(libraryStart), 'DispatchQueue.main.async { [weak self] in');
const libraryGuards = ['self.publicationStillCurrent(publicationToken)',
  'CredentialScopeRegistry.shared.isCurrent(credentialCapture)',
  'self.metaLoadTargetLock.withLock({ self.nativeMetaRequestGeneration == requestGeneration })',
  'Self.libraryMetaDetailsUpdate(current: self.metaDetails, next: details)', 'self.metaDetails = merged'];
function libraryCallerAccepts(source) {
  return libraryGuards.every(guard => source.includes(guard)) && !source.includes('self.metaDetails = details');
}
if (!libraryCallerAccepts(libraryClosure)) throw new Error('Library overlay caller contract failed');
for (const guard of libraryGuards) {
  if (libraryCallerAccepts(libraryClosure.replace(guard, 'REMOVED_GUARD'))) throw new Error('Library caller negative control accepted');
}
fs.writeFileSync(`${out}/library-caller-contract.txt`, 'Actual library overlay caller contract PASS; five guard-removal negatives rejected; not executor/native receipt proof\n');
} else {
  fs.writeFileSync(`${out}/library-caller-contract.txt`, 'Historical source predates library overlay merge; no new caller contract result claimed\n');
}
// Models and tests compile directly from input snapshots; no source declaration,
// date formatter, protocol annotation or test assertion is edited during prepare.
NODE
shasum -a 256 "$publication_test_dir/Collaborators.swift" \
  "$publication_test_dir/ProductionResourceValues.swift" "$publication_test_dir/ProductionPublication.swift" \
  > "$publication_test_dir/generated-before.sha256"
git -C "$PWD" rev-parse HEAD > "$publication_test_dir/source-head.txt"
if ((publication_prepare_only)); then
  publication_phase=prepared
  printf 'Prepared exact frozen source; no compiler or executable run\n'
  exit 0
fi
publication_phase=compile
mkdir "$publication_test_dir/module-cache"
set +e
nice -n 15 node "$publication_supervisor" run 60 "$publication_test_dir/compile-process.json" -- \
  /usr/bin/xcrun swiftc -parse-as-library -warnings-as-errors -whole-module-optimization \
  -swift-version "$publication_swift_version" -strict-concurrency=complete -j 1 -num-threads 1 \
  -module-cache-path "$publication_test_dir/module-cache" \
  -D VORTX_NATIVE_DATA_ENGINE \
  "$publication_test_dir/Collaborators.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/CoreModels.swift" \
  "$publication_test_dir/ProductionPublication.swift" \
  "$publication_test_dir/ProductionResourceValues.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/VortxResourceProjection.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/DetailMetaRecoveryPolicy.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/CatalogRowResolution.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/SubtitleReleaseFingerprint.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/UsenetStreamValidation.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/DiagnosticPlaybackIntegrityPolicy.swift" \
  "$publication_test_dir/inputs/app/SourcesShared/ProfileAddonPreferences.swift" \
  "$publication_test_dir/inputs/app/Tests/AddonStreamPublicationTests.swift" \
  -o "$publication_test_dir/addon-stream-publication" > "$publication_test_dir/compiler.log" 2>&1
publication_compile_status=$?
set -e
if [[ "$publication_compile_status" != 0 ]]; then cat "$publication_test_dir/compiler.log"; exit "$publication_compile_status"; fi
nice -n 15 node "$publication_supervisor" check "$publication_test_dir/compile-process.json" 0 normal-exit
publication_phase=runtime
set +e
nice -n 15 node "$publication_supervisor" run 30 "$publication_test_dir/runtime-process.json" -- \
  /usr/bin/env "VORTX_ADDON_PUBLICATION_BENCHMARK=$publication_benchmark" \
  "$publication_test_dir/addon-stream-publication" > "$publication_test_dir/test.log" 2>&1
publication_runtime_status=$?
set -e
cat "$publication_test_dir/test.log"
if [[ "$publication_runtime_status" != 0 ]]; then exit "$publication_runtime_status"; fi
nice -n 15 node "$publication_supervisor" check "$publication_test_dir/runtime-process.json" 0 normal-exit
publication_phase=complete
exit "$publication_runtime_status"
